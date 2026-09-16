#!/usr/bin/env python3
"""Piano WebSocket framing + hub broadcast."""

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

import piano_sharing as ps  # noqa: E402
import piano_ws as ws  # noqa: E402


def test_ws_accept_key() -> None:
    # RFC6455 example
    key = "dGhlIHNhbXBsZSBub25jZQ=="
    assert ws.ws_accept_key(key) == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="


def test_frame_roundtrip() -> None:
    payload = b'{"type":"ping"}'
    frame = ws.encode_frame(payload, mask=True)
    a, b = socket.socketpair()
    try:
        a.sendall(frame)
        opcode, got = ws.decode_frame(b)
        assert opcode == ws.OP_TEXT
        assert got == payload
    finally:
        a.close()
        b.close()


def test_hub_broadcast_and_push() -> None:
    tmp = tempfile.mkdtemp(prefix="sshchat-piano-ws-")
    store = ps.PianoStore(
        store_path=str(Path(tmp) / "sessions.json"),
        recordings_path=str(Path(tmp) / "recordings.json"),
    )
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
                opcode, payload = ws.decode_frame(sb)
                if opcode == ws.OP_TEXT:
                    received.append(json.loads(payload.decode("utf-8")))
                if opcode == ws.OP_CLOSE:
                    break
        except Exception:
            pass

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    client = ws.PianoWsClient(
        conn_id="c1",
        session_id=session.session_id,
        participant="Bob",
        token=tok_b,
        sock=sa,
    )
    ws.piano_ws_hub.register(client)
    try:
        batch, err = store.push_notes(
            tok_a,
            ticket_a,
            [{"note": "C4", "action": "on", "ts": time.time()}],
        )
        assert err == ""
        assert batch is not None
        ws.piano_ws_hub.broadcast(
            session.session_id,
            {
                "type": "events",
                "rev": batch["rev"],
                "events": batch["events"],
                "held": batch["held"],
            },
        )
        deadline = time.time() + 2
        while time.time() < deadline and not any(
            m.get("type") == "events" for m in received
        ):
            time.sleep(0.02)
        assert any(m.get("type") == "events" for m in received)
        ev = next(m for m in received if m.get("type") == "events")
        assert ev["events"][0]["note"] == "C4"
    finally:
        ws.piano_ws_hub.unregister(client)
        client.close()
        sb.close()


if __name__ == "__main__":
    test_ws_accept_key()
    test_frame_roundtrip()
    test_hub_broadcast_and_push()
    print("✅ piano websocket ok")
