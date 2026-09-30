#!/usr/bin/env python3
"""Canvas WebSocket framing + hub broadcast."""

from __future__ import annotations

import json
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import canvas_sharing as cs  # noqa: E402
import canvas_ws as ws  # noqa: E402
import piano_ws as pws  # noqa: E402


def _el(eid: str, version: int = 1, **extra):
    base = {
        "id": eid,
        "type": "rectangle",
        "x": 0,
        "y": 0,
        "width": 10,
        "height": 10,
        "angle": 0,
        "strokeColor": "#000",
        "backgroundColor": "transparent",
        "fillStyle": "solid",
        "strokeWidth": 1,
        "strokeStyle": "solid",
        "roughness": 0,
        "opacity": 100,
        "groupIds": [],
        "frameId": None,
        "roundness": None,
        "seed": 1,
        "version": version,
        "versionNonce": version,
        "isDeleted": False,
        "boundElements": None,
        "updated": 1,
        "link": None,
        "locked": False,
    }
    base.update(extra)
    return base


def test_hub_broadcast_and_scene() -> None:
    tmp = tempfile.mkdtemp(prefix="sshchat-canvas-ws-")
    store = cs.CanvasStore(store_path=str(Path(tmp) / "canvas.json"))
    session = store.create_session("Alice", ["Bob"], room="lobby")
    tok_a = session.tokens["Alice"]
    tok_b = session.tokens["Bob"]
    _, _, ticket_a, _ = store.issue_access_ticket(tok_a, session.keys["Alice"])
    _, _, ticket_b, _ = store.issue_access_ticket(tok_b, session.keys["Bob"])
    assert ticket_a and ticket_b

    sa, sb = socket.socketpair()
    received: list[dict] = []

    def reader() -> None:
        try:
            while True:
                opcode, payload = pws.decode_frame(sb, max_payload=ws.MAX_CANVAS_WS_PAYLOAD)
                if opcode == pws.OP_TEXT:
                    received.append(json.loads(payload.decode("utf-8")))
                if opcode == pws.OP_CLOSE:
                    break
        except Exception:
            pass

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    client = ws.CanvasWsClient(
        conn_id="c1",
        session_id=session.session_id,
        participant="Bob",
        token=tok_b,
        sock=sa,
    )
    ws.canvas_ws_hub.register(client)
    try:
        result, err = store.apply_scene(
            tok_a, ticket_a, elements=[_el("box1", 1)]
        )
        assert err == ""
        assert result is not None
        assert result.get("session_id") == session.session_id
        ws.canvas_ws_hub.broadcast(
            session.session_id,
            {
                "type": "scene",
                "rev": result["rev"],
                "elements": result["elements"],
                "files": result.get("files") or {},
                "author": result.get("author") or "",
            },
        )
        deadline = time.time() + 2
        while time.time() < deadline and not any(
            m.get("type") == "scene" for m in received
        ):
            time.sleep(0.02)
        assert any(m.get("type") == "scene" for m in received)
        ev = next(m for m in received if m.get("type") == "scene")
        assert ev["elements"][0]["id"] == "box1"
    finally:
        ws.canvas_ws_hub.unregister(client)
        client.close()
        sb.close()


def test_run_session_scene_ack() -> None:
    tmp = tempfile.mkdtemp(prefix="sshchat-canvas-ws2-")
    store = cs.CanvasStore(store_path=str(Path(tmp) / "canvas.json"))
    session = store.create_session("Alice", [], room="r1")
    tok = session.tokens["Alice"]
    _, _, ticket, _ = store.issue_access_ticket(tok, session.keys["Alice"])
    assert ticket

    sa, sb = socket.socketpair()
    replies: list[dict] = []

    def reader() -> None:
        try:
            while True:
                opcode, payload = pws.decode_frame(sb, max_payload=ws.MAX_CANVAS_WS_PAYLOAD)
                if opcode == pws.OP_TEXT:
                    replies.append(json.loads(payload.decode("utf-8")))
                if opcode == pws.OP_CLOSE:
                    break
        except Exception:
            pass

    t = threading.Thread(target=reader, daemon=True)
    t.start()

    client = ws.CanvasWsClient(
        conn_id="c2",
        session_id=session.session_id,
        participant="Alice",
        token=tok,
        sock=sa,
    )

    def on_scene(c, elements, files, scene_gen=None):
        result, err = store.apply_scene(
            c.token, ticket, elements=elements, files=files, scene_gen=scene_gen
        )
        assert err == ""
        return result

    def on_clear(c):
        result, err = store.clear_board(c.token, ticket)
        assert err == ""
        return result

    loop = threading.Thread(
        target=lambda: ws.run_canvas_ws_session(
            client, on_scene=on_scene, on_clear=on_clear
        ),
        daemon=True,
    )
    loop.start()

    deadline = time.time() + 2
    while time.time() < deadline and not any(m.get("type") == "hello" for m in replies):
        time.sleep(0.02)
    assert any(m.get("type") == "hello" for m in replies)

    frame = pws.encode_frame(
        json.dumps(
            {"type": "scene", "elements": [_el("s1", 1)], "files": {}}
        ).encode("utf-8"),
        mask=True,
    )
    sb.sendall(frame)

    deadline = time.time() + 2
    while time.time() < deadline and not any(m.get("type") == "ack" for m in replies):
        time.sleep(0.02)
    ack = next(m for m in replies if m.get("type") == "ack")
    assert ack.get("kind") == "scene"
    assert int(ack.get("rev") or 0) >= 1

    client.close()
    try:
        sb.close()
    except OSError:
        pass


if __name__ == "__main__":
    test_hub_broadcast_and_scene()
    test_run_session_scene_ack()
    print("✅ canvas websocket ok")
