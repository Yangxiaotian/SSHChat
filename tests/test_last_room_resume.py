"""Last-active room is restored on reconnect."""

from __future__ import annotations

import time
import unittest
from unittest import mock


class LastRoomResumeTests(unittest.TestCase):
    def setUp(self) -> None:
        import server

        self.server = server
        self.server.clients.clear()
        self.server.rooms.clear()
        self.server.room_owners.clear()
        self.server.disconnected_sessions.clear()
        self.server._persist_dirty = False

    def test_switch_updates_remembered_session_while_online(self) -> None:
        conn = object()
        self.server.clients[conn] = {
            "name": "alice",
            "rooms": {"default", "wq"},
            "current_room": "default",
        }
        self.server.rooms["default"] = {conn}
        self.server.rooms["wq"] = {conn}

        with mock.patch.object(self.server, "send_line"):
            with mock.patch.object(self.server, "send_room_announcement_preview"):
                self.server.handle_command(conn, "/switch wq")

        sess = self.server.disconnected_sessions.get("alice")
        self.assertIsNotNone(sess)
        self.assertEqual(sess["current_room"], "wq")
        self.assertIn("wq", sess["rooms"])

    def test_join_updates_remembered_session(self) -> None:
        conn = object()
        self.server.clients[conn] = {
            "name": "bob",
            "rooms": {"default"},
            "current_room": "default",
        }
        self.server.rooms["default"] = {conn}

        with mock.patch.object(self.server, "send_line"):
            with mock.patch.object(self.server, "broadcast_room"):
                with mock.patch.object(self.server, "send_room_announcement_preview"):
                    with mock.patch.object(self.server.federation, "get_hub", return_value=None):
                        self.server.handle_command(conn, "/join ops")

        sess = self.server.disconnected_sessions.get("bob")
        self.assertIsNotNone(sess)
        self.assertEqual(sess["current_room"], "ops")
        self.assertIn("ops", sess["rooms"])

    def test_load_recent_session_returns_last_room(self) -> None:
        self.server._remember_session_locked("carol", ["default", "lab"], "lab")
        sess = self.server._load_recent_session_locked("carol")
        self.assertIsNotNone(sess)
        self.assertEqual(sess["current_room"], "lab")

    def test_expired_session_dropped_when_ttl_positive(self) -> None:
        self.server._remember_session_locked("dave", ["default", "old"], "old")
        self.server.disconnected_sessions["dave"]["ts"] = time.time() - 10
        old_ttl = self.server.SESSION_RESUME_TTL_SECONDS
        try:
            self.server.SESSION_RESUME_TTL_SECONDS = 1
            self.assertIsNone(self.server._load_recent_session_locked("dave"))
        finally:
            self.server.SESSION_RESUME_TTL_SECONDS = old_ttl


if __name__ == "__main__":
    unittest.main()
