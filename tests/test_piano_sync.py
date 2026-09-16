#!/usr/bin/env python3
"""Piano batch push, debounced save, long-poll, and held-key snapshot."""

from __future__ import annotations

import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import piano_sharing as ps  # noqa: E402


def _fresh_store() -> ps.PianoStore:
    tmp = tempfile.mkdtemp(prefix="sshchat-piano-")
    return ps.PianoStore(
        store_path=str(Path(tmp) / "sessions.json"),
        recordings_path=str(Path(tmp) / "recordings.json"),
    )


def test_batch_push_and_held() -> None:
    store = _fresh_store()
    session = store.create_session("Alice", ["Bob"], room="lobby")
    tok = session.tokens["Alice"]
    key = session.keys["Alice"]
    _sess, _p, ticket, err = store.issue_access_ticket(tok, key)
    assert ticket and not err

    batch, err = store.push_notes(
        tok,
        ticket,
        [
            {"note": "C4", "action": "on", "ts": time.time()},
            {"note": "E4", "action": "on", "ts": time.time()},
            {"note": "C4", "action": "off", "ts": time.time()},
        ],
        held=["E4"],
    )
    assert err == ""
    assert batch is not None
    assert len(batch["events"]) == 3
    assert batch["held"]["Alice"] == ["E4"]

    sync, err = store.sync_since(tok, ticket, 0, wait_ms=0)
    assert err == ""
    assert sync is not None
    assert len(sync["events"]) == 3
    assert sync["held"]["Alice"] == ["E4"]


def test_long_poll_wakes_on_note() -> None:
    store = _fresh_store()
    session = store.create_session("Alice", ["Bob"], room="r")
    tok_a = session.tokens["Alice"]
    tok_b = session.tokens["Bob"]
    _, _, ticket_a, _ = store.issue_access_ticket(tok_a, session.keys["Alice"])
    _, _, ticket_b, _ = store.issue_access_ticket(tok_b, session.keys["Bob"])
    assert ticket_a and ticket_b

    result: dict = {}

    def waiter() -> None:
        payload, err = store.sync_since(tok_b, ticket_b, 0, wait_ms=3000)
        result["err"] = err
        result["payload"] = payload

    t = threading.Thread(target=waiter, daemon=True)
    t.start()
    time.sleep(0.05)
    store.push_note(tok_a, ticket_a, note="G4", action="on")
    t.join(timeout=4)
    assert not t.is_alive()
    assert result.get("err") == ""
    events = (result.get("payload") or {}).get("events") or []
    assert len(events) == 1
    assert events[0]["note"] == "G4"


def test_note_does_not_fsync_immediately() -> None:
    store = _fresh_store()
    session = store.create_session("Alice", [], room="x")
    tok = session.tokens["Alice"]
    _, _, ticket, _ = store.issue_access_ticket(tok, session.keys["Alice"])
    path = Path(store.store_path)
    mtime_before = path.stat().st_mtime_ns
    store.push_note(tok, ticket, note="C4", action="on")
    # Debounced: file should not rewrite in the first few dozen ms.
    time.sleep(0.05)
    mtime_after = path.stat().st_mtime_ns
    assert mtime_after == mtime_before
    # Force flush for cleanup.
    store._save_now()


if __name__ == "__main__":
    test_batch_push_and_held()
    test_long_poll_wakes_on_note()
    test_note_does_not_fsync_immediately()
    print("✅ piano sync ok")
