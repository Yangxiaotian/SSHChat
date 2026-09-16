"""WebSocket hub for shared canvas (stdlib framing via piano_ws).

Used by FileHTTP: upgrade GET /canvas/<token>/ws, then broadcast scene/clear
to other participants in the same canvas session.
"""

from __future__ import annotations

import json
import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

import piano_ws

# Match canvas_sharing.MAX_SCENE_BYTES (+ JSON overhead).
try:
    from canvas_sharing import MAX_SCENE_BYTES as _MAX_SCENE

    MAX_CANVAS_WS_PAYLOAD = int(_MAX_SCENE) + 65536
except Exception:
    MAX_CANVAS_WS_PAYLOAD = 12 * 1024 * 1024 + 65536


@dataclass
class CanvasWsClient:
    conn_id: str
    session_id: str
    participant: str
    token: str
    sock: socket.socket
    lock: threading.Lock = field(default_factory=threading.Lock)
    closed: bool = False

    def send_json(self, obj: dict) -> bool:
        if self.closed:
            return False
        raw = piano_ws.encode_frame(
            json.dumps(obj, ensure_ascii=False).encode("utf-8")
        )
        try:
            with self.lock:
                if self.closed:
                    return False
                self.sock.sendall(raw)
            return True
        except OSError:
            self.closed = True
            return False

    def send_pong(self, payload: bytes = b"") -> None:
        try:
            with self.lock:
                if self.closed:
                    return
                self.sock.sendall(
                    piano_ws.encode_frame(payload, opcode=piano_ws.OP_PONG)
                )
        except OSError:
            self.closed = True

    def close(self, code: int = 1000) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            body = struct.pack("!H", code)
            with self.lock:
                self.sock.sendall(
                    piano_ws.encode_frame(body, opcode=piano_ws.OP_CLOSE)
                )
        except OSError:
            pass
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


class CanvasWsHub:
    """Track live canvas WebSocket clients and broadcast scene updates."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_session: Dict[str, Dict[str, CanvasWsClient]] = {}

    def register(self, client: CanvasWsClient) -> None:
        with self._lock:
            bucket = self._by_session.setdefault(client.session_id, {})
            bucket[client.conn_id] = client

    def unregister(self, client: CanvasWsClient) -> None:
        with self._lock:
            bucket = self._by_session.get(client.session_id)
            if not bucket:
                return
            bucket.pop(client.conn_id, None)
            if not bucket:
                self._by_session.pop(client.session_id, None)

    def broadcast(
        self,
        session_id: str,
        message: dict,
        *,
        exclude_conn_id: Optional[str] = None,
    ) -> int:
        with self._lock:
            clients = list((self._by_session.get(session_id) or {}).values())
        sent = 0
        dead: list[CanvasWsClient] = []
        for client in clients:
            if exclude_conn_id and client.conn_id == exclude_conn_id:
                continue
            if client.send_json(message):
                sent += 1
            else:
                dead.append(client)
        for client in dead:
            self.unregister(client)
            client.close()
        return sent

    def session_client_count(self, session_id: str) -> int:
        with self._lock:
            return len(self._by_session.get(session_id) or {})


canvas_ws_hub = CanvasWsHub()


def run_canvas_ws_session(
    client: CanvasWsClient,
    *,
    on_scene: Callable[
        [CanvasWsClient, Any, Any], Optional[dict]
    ],
    on_clear: Callable[[CanvasWsClient], Optional[dict]],
    on_close: Optional[Callable[[CanvasWsClient], None]] = None,
) -> None:
    """Blocking read loop for one canvas WebSocket connection."""
    canvas_ws_hub.register(client)
    try:
        client.send_json(
            {
                "type": "hello",
                "participant": client.participant,
                "server_ts": time.time(),
            }
        )
        while not client.closed:
            try:
                opcode, payload = piano_ws.decode_frame(
                    client.sock, max_payload=MAX_CANVAS_WS_PAYLOAD
                )
            except (ConnectionError, OSError, ValueError, struct.error):
                break
            if opcode == piano_ws.OP_CLOSE:
                break
            if opcode == piano_ws.OP_PING:
                client.send_pong(payload)
                continue
            if opcode == piano_ws.OP_PONG:
                continue
            if opcode not in (piano_ws.OP_TEXT, piano_ws.OP_BINARY):
                continue
            try:
                msg = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(msg, dict):
                continue
            mtype = str(msg.get("type") or "").strip().lower()
            if mtype in ("ping", "heartbeat"):
                client.send_json({"type": "pong", "t": time.time()})
                continue
            if mtype == "clear":
                result = on_clear(client)
                if result is None:
                    client.send_json({"type": "error", "error": "clear failed"})
                    continue
                client.send_json(
                    {
                        "type": "ack",
                        "kind": "clear",
                        "rev": result.get("rev", 0),
                    }
                )
                continue
            if mtype != "scene":
                continue
            elements = msg.get("elements")
            files = msg.get("files")
            if elements is not None and not isinstance(elements, list):
                continue
            if files is not None and not isinstance(files, dict):
                continue
            result = on_scene(
                client,
                elements if isinstance(elements, list) else [],
                files if isinstance(files, dict) else None,
            )
            if result is None:
                client.send_json({"type": "error", "error": "scene failed"})
                continue
            client.send_json(
                {
                    "type": "ack",
                    "kind": "scene",
                    "rev": result.get("rev", 0),
                }
            )
    finally:
        canvas_ws_hub.unregister(client)
        if on_close:
            try:
                on_close(client)
            except Exception:
                pass
        client.close()
