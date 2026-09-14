"""Personal /later time capsule."""

from __future__ import annotations

import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

import server
from session_store import GameSessionStore


class DummyConn:
    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self._timeout = None

    def gettimeout(self):
        return self._timeout

    def settimeout(self, value) -> None:
        self._timeout = value

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

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
        server.disconnected_sessions.clear()
        self._tmpdir = tempfile.TemporaryDirectory()
        self._store_path = f"{self._tmpdir.name}/game_sessions.json"
        server.session_store = GameSessionStore(self._store_path)
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

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

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
        with patch.object(server, "_safe_persist_sessions_now"):
            server.handle_command(self.alice, "/later 30m bring umbrella")
        self.assertEqual(len(server.room_capsules), 1)
        self.assertIn("Reminder set for", self._out(self.alice))
        self.assertEqual(self._out(self.bob), "")

        self.alice.sent.clear()
        server.handle_command(self.alice, "/later list")
        self.assertIn("Your pending time capsules", self._out(self.alice))

        server.room_capsules[0]["deliver_at"] = time.time() - 1
        self.alice.sent.clear()
        self.bob.sent.clear()
        with patch.object(server, "_safe_persist_sessions_now"):
            with patch.object(server.federation, "get_hub", return_value=None):
                server._deliver_due_capsules()
        self.assertEqual(server.room_capsules, [])
        self.assertIn("Time capsule: bring umbrella", self._out(self.alice))
        self.assertEqual(self._out(self.bob), "")

    def test_cancel_private(self) -> None:
        with patch.object(server, "_safe_persist_sessions_now"):
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

    def test_survives_restart(self) -> None:
        with patch.object(
            server, "_safe_persist_sessions_now", wraps=server._safe_persist_sessions_now
        ):
            server.handle_command(self.alice, "/later 30m after reboot")
        self.assertEqual(len(server.room_capsules), 1)
        with server.lock:
            payload = server._build_session_payload_locked()
        self.assertEqual(len(payload.get("room_capsules") or []), 1)
        server.session_store.save(payload)

        server.room_capsules.clear()
        server._capsule_next_id = 1
        server.clients.clear()
        server._load_persisted_sessions()
        self.assertEqual(len(server.room_capsules), 1)
        self.assertEqual(server.room_capsules[0]["text"], "after reboot")
        self.assertEqual(server.room_capsules[0]["creator"], "Alice")

    def test_federation_same_name_gets_notify(self) -> None:
        hub = MagicMock()
        hub.enabled = True
        hub.has_remote_user.return_value = True
        hub.send_pm.return_value = True
        hub.node_id = "node-a"
        server.clients.pop(self.alice, None)
        server.rooms["lobby"] = {self.bob}
        with patch.object(server, "_safe_persist_sessions_now"):
            with patch.object(server.federation, "get_hub", return_value=hub):
                with patch.object(server, "_federation_sync_capsules") as sync:
                    server.room_capsules.append(
                        {
                            "id": 9,
                            "room": "lobby",
                            "creator": "Alice",
                            "text": "fed ping",
                            "deliver_at": time.time() - 1,
                            "origin": "node-a",
                        }
                    )
                    server._deliver_due_capsules()
        hub.send_pm.assert_called_once_with("Alice", "later", "fed ping")
        self.assertEqual(server.room_capsules, [])
        self.assertEqual(self._out(self.bob), "")
        sync.assert_called_once_with("Alice")

    def test_fed_on_pm_formats_later(self) -> None:
        server._fed_on_pm("Alice", "later", "wake up")
        self.assertIn("Time capsule: wake up", self._out(self.alice))
        self.assertNotIn("[PM from later]", self._out(self.alice))

    def test_federation_sync_shows_on_peer_list(self) -> None:
        with patch.object(server, "_safe_persist_sessions_now"):
            server._fed_on_capsules(
                "node-b",
                "Alice",
                [
                    {
                        "id": 3,
                        "room": "lobby",
                        "creator": "Alice",
                        "text": "from peer",
                        "deliver_at": time.time() + 3600,
                    }
                ],
            )
        self.alice.sent.clear()
        server.handle_command(self.alice, "/later list")
        out = self._out(self.alice)
        self.assertIn("from peer", out)
        self.assertIn("Your pending time capsules", out)

    def test_federation_cancel_remote_requests_origin(self) -> None:
        hub = MagicMock()
        hub.enabled = True
        hub.node_id = "node-a"
        with patch.object(server, "_safe_persist_sessions_now"):
            server.room_capsules.append(
                {
                    "id": 7,
                    "room": "lobby",
                    "creator": "Alice",
                    "text": "remote one",
                    "deliver_at": time.time() + 3600,
                    "origin": "node-b",
                }
            )
            with patch.object(server.federation, "get_hub", return_value=hub):
                server.handle_command(self.alice, "/later cancel 1")
        hub.request_capsule_cancel.assert_called_once_with("node-b", "Alice", 7)
        self.assertEqual(server.room_capsules, [])
        self.assertIn("Cancelled time capsule #1", self._out(self.alice))

    def test_remote_replica_does_not_deliver_locally(self) -> None:
        with patch.object(server, "_safe_persist_sessions_now"):
            with patch.object(server.federation, "get_hub", return_value=None):
                server.room_capsules.append(
                    {
                        "id": 4,
                        "room": "lobby",
                        "creator": "Alice",
                        "text": "stay replica",
                        "deliver_at": time.time() - 1,
                        "origin": "other-node",
                    }
                )
                server._deliver_due_capsules()
        self.assertEqual(len(server.room_capsules), 1)
        self.assertEqual(self._out(self.alice), "")

    def test_fed_on_capsule_cancel_removes_and_resyncs(self) -> None:
        hub = MagicMock()
        hub.enabled = True
        hub.node_id = "node-a"
        with patch.object(server, "_safe_persist_sessions_now"):
            server.room_capsules.append(
                {
                    "id": 2,
                    "room": "lobby",
                    "creator": "Alice",
                    "text": "bye",
                    "deliver_at": time.time() + 3600,
                    "origin": "node-a",
                }
            )
            with patch.object(server.federation, "get_hub", return_value=hub):
                server._fed_on_capsule_cancel("Alice", 2)
        self.assertEqual(server.room_capsules, [])
        hub.sync_capsules.assert_called_once_with("Alice", [])

    def test_schedule_syncs_to_federation(self) -> None:
        hub = MagicMock()
        hub.enabled = True
        hub.node_id = "node-a"
        with patch.object(server, "_safe_persist_sessions_now"):
            with patch.object(server.federation, "get_hub", return_value=hub):
                server.handle_command(self.alice, "/later 30m sync me")
        hub.sync_capsules.assert_called_once()
        args = hub.sync_capsules.call_args[0]
        self.assertEqual(args[0], "Alice")
        self.assertEqual(args[1][0]["text"], "sync me")
        self.assertEqual(server.room_capsules[0]["origin"], "node-a")


if __name__ == "__main__":
    unittest.main()
