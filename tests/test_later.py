"""Personal /later time capsule."""

from __future__ import annotations

import time
import unittest
from unittest.mock import patch

import server


class DummyConn:
    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def send(self, data: bytes) -> None:
        self.sent.append(data)


class LaterTests(unittest.TestCase):
    def setUp(self) -> None:
        server.clients.clear()
        server.rooms.clear()
        server.room_owners.clear()
        server.room_announcements.clear()
        server.room_polls.clear()
        server.room_capsules.clear()
        server._capsule_next_id = 1
        self.alice = DummyConn()
        self.bob = DummyConn()
        server.clients[self.alice] = {
            "name": "Alice",
            "rooms": {"lobby"},
            "current_room": "lobby",
            "locale": "en",
        }
        server.clients[self.bob] = {
            "name": "Bob",
            "rooms": {"lobby"},
            "current_room": "lobby",
            "locale": "en",
        }
        server.rooms["lobby"] = {self.alice, self.bob}
        server.room_owners["lobby"] = self.alice

    def _out(self, conn: DummyConn) -> str:
        return b"".join(conn.sent).decode("utf-8")

    def test_parse_relative_and_tomorrow(self) -> None:
        got = server._parse_later_when("30m hello world")
        self.assertIsNotNone(got)
        assert got is not None
        when, text = got
        self.assertEqual(text, "hello world")
        self.assertGreater(when, time.time() + 29 * 60)

        got2 = server._parse_later_when("tomorrow 09:00 standup")
        self.assertIsNotNone(got2)
        assert got2 is not None
        self.assertEqual(got2[1], "standup")

    def test_schedule_is_private_and_delivers_only_to_self(self) -> None:
        with patch.object(server, "_mark_sessions_dirty"):
            server.handle_command(self.alice, "/later 30m bring umbrella")
        self.assertEqual(len(server.room_capsules), 1)
        self.assertIn("only you will see it", self._out(self.alice))
        self.assertEqual(self._out(self.bob), "")

        self.alice.sent.clear()
        server.handle_command(self.alice, "/later list")
        self.assertIn("Your pending time capsules", self._out(self.alice))

        server.room_capsules[0]["deliver_at"] = time.time() - 1
        self.alice.sent.clear()
        self.bob.sent.clear()
        with patch.object(server, "_mark_sessions_dirty"):
            server._deliver_due_capsules()
        self.assertEqual(server.room_capsules, [])
        self.assertIn("Time capsule: bring umbrella", self._out(self.alice))
        self.assertEqual(self._out(self.bob), "")

    def test_cancel_private(self) -> None:
        with patch.object(server, "_mark_sessions_dirty"):
            server.handle_command(self.alice, "/later 1h remember")
            self.bob.sent.clear()
            self.alice.sent.clear()
            server.handle_command(self.alice, "/later cancel 1")
        self.assertEqual(server.room_capsules, [])
        self.assertIn("Cancelled time capsule #1", self._out(self.alice))
        self.assertEqual(self._out(self.bob), "")

    def test_rejects_too_soon(self) -> None:
        server.handle_command(self.alice, "/later 5s nope")
        self.assertEqual(server.room_capsules, [])
        self.assertIn("too soon", self._out(self.alice))


if __name__ == "__main__":
    unittest.main()
