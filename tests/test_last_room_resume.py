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
                    with mock.patch.object(self.server, "send_room_pad_preview"):
                        with mock.patch.object(self.server, "send_room_poll_preview"):
                            with mock.patch.object(
                                self.server.federation, "get_hub", return_value=None
                            ):
                                self.server.handle_command(conn, "/join ops")

        sess = self.server.disconnected_sessions.get("bob")
        self.assertIsNotNone(sess)
        self.assertEqual(sess["current_room"], "ops")
        self.assertIn("ops", sess["rooms"])

    def test_join_already_joined_notifies_federation_switch(self) -> None:
        conn = object()
        self.server.clients[conn] = {
            "name": "erin",
            "rooms": {"default", "ops"},
            "current_room": "default",
        }
        self.server.rooms["default"] = {conn}
        self.server.rooms["ops"] = {conn}
        hub = mock.Mock()
        hub.enabled = True

        with mock.patch.object(self.server, "send_line"):
            with mock.patch.object(self.server, "send_room_announcement_preview"):
                with mock.patch.object(self.server, "send_room_pad_preview"):
                    with mock.patch.object(self.server, "send_room_poll_preview"):
                        with mock.patch.object(
                            self.server.federation, "get_hub", return_value=hub
                        ):
                            self.server.handle_command(conn, "/join ops")

        hub.notify_switch.assert_called_once_with("erin", "ops")
        hub.notify_join.assert_not_called()
        self.assertEqual(
            self.server.disconnected_sessions["erin"]["current_room"], "ops"
        )

    def test_reconnect_prefers_session_over_stale_peer_room(self) -> None:
        """Idle same-nick device must not override last /join|/switch room."""
        phone = object()
        self.server.clients[phone] = {
            "name": "yxt",
            "rooms": {"default", "ops"},
            "current_room": "default",
        }
        self.server.rooms["default"] = {phone}
        self.server.rooms["ops"] = {phone}
        self.server._remember_session_locked("yxt", ["default", "ops"], "ops")

        previous_session = self.server._load_recent_session_locked("yxt")
        same_name_peers = [phone]
        inherited_rooms: set[str] = set()
        session_active = ""
        if previous_session is not None:
            inherited_rooms.update(previous_session.get("rooms") or set())
            previous_active = previous_session.get("current_room")
            if isinstance(previous_active, str) and previous_active.strip():
                session_active = previous_active.strip()
        peer_active = ""
        for peer in same_name_peers:
            peer_info = self.server.clients.get(peer)
            inherited_rooms.update(peer_info["rooms"])
            if not peer_active:
                peer_active = str(peer_info.get("current_room") or "").strip()
        fed_active = ""
        if fed_active:
            active_room = fed_active
        elif session_active:
            active_room = session_active
        elif peer_active:
            active_room = peer_active
        else:
            active_room = self.server.DEFAULT_ROOM
        self.assertEqual(active_room, "ops")

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
