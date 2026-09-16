"""Minimal RFC6455 WebSocket helpers for room piano (stdlib only).

Used by FileHTTP ThreadingHTTPServer: upgrade GET /piano/<token>/ws, then
broadcast note events to other participants in the same piano session.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Set

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
OP_CONT = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA


def ws_accept_key(sec_key: str) -> str:
    digest = hashlib.sha1((sec_key.strip() + WS_GUID).encode("utf-8")).digest()
    return base64.b64encode(digest).decode("ascii")


def encode_frame(payload: bytes, *, opcode: int = OP_TEXT, mask: bool = False) -> bytes:
    fin_opcode = 0x80 | (opcode & 0x0F)
    length = len(payload)
    header = bytearray([fin_opcode])
    mask_bit = 0x80 if mask else 0x00
    if length < 126:
        header.append(mask_bit | length)
    elif length < (1 << 16):
        header.append(mask_bit | 126)
        header.extend(struct.pack("!H", length))
    else:
        header.append(mask_bit | 127)
        header.extend(struct.pack("!Q", length))
    if mask:
        masking_key = secrets.token_bytes(4)
        header.extend(masking_key)
        masked = bytes(b ^ masking_key[i % 4] for i, b in enumerate(payload))
        return bytes(header) + masked
    return bytes(header) + payload


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("websocket closed")
        buf.extend(chunk)
    return bytes(buf)


def decode_frame(
    sock: socket.socket, *, max_payload: int = 1_000_000
) -> tuple[int, bytes]:
    """Read one WebSocket data frame. Returns (opcode, payload)."""
    hdr = _recv_exact(sock, 2)
    b0, b1 = hdr[0], hdr[1]
    opcode = b0 & 0x0F
    masked = bool(b1 & 0x80)
    length = b1 & 0x7F
    if length == 126:
        length = struct.unpack("!H", _recv_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", _recv_exact(sock, 8))[0]
    if length > max_payload:
        raise ValueError("websocket frame too large")
    mask_key = _recv_exact(sock, 4) if masked else b""
    payload = _recv_exact(sock, length) if length else b""
    if masked:
        payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    return opcode, payload


@dataclass
class PianoWsClient:
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
        raw = encode_frame(json.dumps(obj, ensure_ascii=False).encode("utf-8"))
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
                self.sock.sendall(encode_frame(payload, opcode=OP_PONG))
        except OSError:
            self.closed = True

    def close(self, code: int = 1000) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            body = struct.pack("!H", code)
            with self.lock:
                self.sock.sendall(encode_frame(body, opcode=OP_CLOSE))
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


class PianoWsHub:
    """Track live piano WebSocket clients and broadcast note events."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_session: Dict[str, Dict[str, PianoWsClient]] = {}

    def register(self, client: PianoWsClient) -> None:
        with self._lock:
            bucket = self._by_session.setdefault(client.session_id, {})
            bucket[client.conn_id] = client

    def unregister(self, client: PianoWsClient) -> None:
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
        only_participant: Optional[str] = None,
    ) -> int:
        with self._lock:
            clients = list((self._by_session.get(session_id) or {}).values())
        sent = 0
        dead: list[PianoWsClient] = []
        only_key = (only_participant or "").strip().lower()
        for client in clients:
            if exclude_conn_id and client.conn_id == exclude_conn_id:
                continue
            if only_key and client.participant.lower() != only_key:
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


piano_ws_hub = PianoWsHub()


def try_enable_tcp_nodelay(sock: socket.socket) -> None:
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        pass


def run_piano_ws_session(
    client: PianoWsClient,
    *,
    on_notes: Callable[[PianoWsClient, list, Optional[list]], Optional[dict]],
    on_close: Optional[Callable[[PianoWsClient], None]] = None,
) -> None:
    """Blocking read loop for one piano WebSocket connection."""
    piano_ws_hub.register(client)
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
                opcode, payload = decode_frame(client.sock)
            except (ConnectionError, OSError, ValueError, struct.error):
                break
            if opcode == OP_CLOSE:
                break
            if opcode == OP_PING:
                client.send_pong(payload)
                continue
            if opcode == OP_PONG:
                continue
            if opcode not in (OP_TEXT, OP_BINARY):
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
            if mtype != "notes":
                continue
            events = msg.get("events")
            held = msg.get("held")
            if events is not None and not isinstance(events, list):
                continue
            if held is not None and not isinstance(held, list):
                continue
            result = on_notes(
                client,
                events if isinstance(events, list) else [],
                held if isinstance(held, list) else None,
            )
            if result is None:
                client.send_json({"type": "error", "error": "push failed"})
                continue
            # Ack to sender (seq numbers); peers get the live broadcast separately.
            client.send_json(
                {
                    "type": "ack",
                    "rev": result.get("rev", 0),
                    "events": result.get("events") or [],
                    "held": result.get("held") or {},
                }
            )
    finally:
        piano_ws_hub.unregister(client)
        if on_close:
            try:
                on_close(client)
            except Exception:
                pass
        client.close()
