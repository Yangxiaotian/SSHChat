"""Tests for offline leave-message (mailbox) feature."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

import server
from offline_messages import OfflineMessageStore


class DummyConn:
    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self.closed = False

    def send(self, data: bytes) -> None:
        self.sent.append(data)

    def close(self) -> None:
        self.closed = True


class OfflineMessageStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(self.path)
        self.store = OfflineMessageStore(self.path, max_per_user=3, max_text_len=20)

    def tearDown(self) -> None:
        if os.path.exists(self.path):
            os.unlink(self.path)

    def test_leave_and_take_all(self) -> None:
        self.assertIsNotNone(self.store.leave("Alice", "Bob", "hello"))
        self.assertEqual(self.store.count("alice"), 1)
        pending = self.store.take_all("ALICE")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["from"], "Bob")
        self.assertEqual(pending[0]["text"], "hello")
        self.assertEqual(self.store.count("alice"), 0)
        self.assertEqual(self.store.take_all("alice"), [])

    def test_truncate_and_cap(self) -> None:
        self.store.leave("u", "a", "x" * 100)
        self.store.leave("u", "b", "one")
        self.store.leave("u", "c", "two")
        self.store.leave("u", "d", "three")
        self.store.leave("u", "e", "four")
        pending = self.store.take_all("u")
        self.assertEqual(len(pending), 3)
        self.assertEqual([p["text"] for p in pending], ["two", "three", "four"])
        self.store.leave("v", "a", "0123456789012345678901234")
        got = self.store.take_all("v")
        self.assertEqual(got[0]["text"], "01234567890123456789")

    def test_persist_across_instances(self) -> None:
        self.store.leave("zoe", "ann", "ping")
        other = OfflineMessageStore(self.path)
        self.assertEqual(other.count("zoe"), 1)


class OfflineMessageServerTests(unittest.TestCase):
    def setUp(self) -> None:
        server.clients.clear()
        server.rooms.clear()
        server.room_owners.clear()
        server.room_announcements.clear()
        server.room_games.clear()
        server.room_enabled_games.clear()
        server.disconnected_sessions.clear()
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(self.path)
        self._prev_store = server.offline_messages
        server.offline_messages = OfflineMessageStore(self.path)

    def tearDown(self) -> None:
        server.offline_messages = self._prev_store
        if os.path.exists(self.path):
            os.unlink(self.path)

    def test_msg_offline_leaves_mailbox(self) -> None:
        sender = DummyConn()
        server.clients[sender] = {
            "name": "bob",
            "rooms": {"default"},
            "current_room": "default",
        }
        server.rooms["default"].add(sender)
        with patch.object(server.federation, "get_hub", return_value=None):
            server.send_private_messages(sender, "bob", "alice", "see you later")
        self.assertEqual(server.offline_messages.count("alice"), 1)
        joined = b"".join(sender.sent).decode("utf-8")
        self.assertIn("已留言", joined)

    def test_msg_online_does_not_leave(self) -> None:
        sender = DummyConn()
        recipient = DummyConn()
        server.clients[sender] = {
            "name": "bob",
            "rooms": {"default"},
            "current_room": "default",
        }
        server.clients[recipient] = {
            "name": "alice",
            "rooms": {"default"},
            "current_room": "default",
        }
        with patch.object(server.federation, "get_hub", return_value=None):
            server.send_private_messages(sender, "bob", "alice", "hi now")
        self.assertEqual(server.offline_messages.count("alice"), 0)
        self.assertTrue(
            any(b"[PM from bob] hi now" in chunk for chunk in recipient.sent)
        )

    def test_deliver_on_login_clears_mailbox(self) -> None:
        server.offline_messages.leave("alice", "bob", "missed you")
        server.offline_messages.leave("alice", "carol", "call me")
        conn = DummyConn()
        n = server.deliver_offline_messages(conn, "Alice")
        self.assertEqual(n, 2)
        self.assertEqual(server.offline_messages.count("alice"), 0)
        text = b"".join(conn.sent).decode("utf-8")
        self.assertIn("你有 2 条留言", text)
        self.assertIn("[PM from bob] (留言 ", text)
        self.assertIn("missed you", text)
        self.assertIn("[PM from carol]", text)


if __name__ == "__main__":
    unittest.main()
