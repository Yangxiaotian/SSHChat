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

    def test_file_kind_keeps_summary_and_meta(self) -> None:
        long_name = "x" * 40
        summary = f"[文件] {long_name}.pdf (1.0 KB)"
        self.assertGreater(len(summary), self.store.max_text_len)
        entry = self.store.leave(
            "alice",
            "bob",
            summary,
            kind="file",
            meta={"transfer_id": "t1", "filename": f"{long_name}.pdf"},
        )
        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry["kind"], "file")
        self.assertEqual(entry["text"], summary)
        pending = self.store.take_all("alice")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["kind"], "file")
        self.assertEqual(pending[0]["meta"]["transfer_id"], "t1")

    def test_file_leave_dedupes_by_transfer_id(self) -> None:
        a = self.store.leave(
            "alice",
            "bob",
            "[文件] a.pdf",
            kind="file",
            meta={"transfer_id": "same"},
        )
        b = self.store.leave(
            "alice",
            "bob",
            "[文件] a.pdf",
            kind="file",
            meta={"transfer_id": "same"},
        )
        self.assertEqual(self.store.count("alice"), 1)
        assert a is not None and b is not None
        self.assertEqual(a.get("id"), b.get("id"))
        removed = self.store.remove_file_by_transfer("alice", "same")
        self.assertEqual(len(removed), 1)
        self.assertEqual(self.store.count("alice"), 0)

    def test_list_and_recall_by_index(self) -> None:
        self.store.leave("alice", "bob", "one")
        self.store.leave("alice", "carol", "other")
        self.store.leave("alice", "bob", "two")
        self.store.leave("dave", "bob", "to-dave")
        listed = self.store.list_sent_unread("Bob")
        self.assertEqual(
            [(x["to"].lower(), x["index"], x["text"]) for x in listed],
            [("alice", 1, "one"), ("alice", 2, "two"), ("dave", 1, "to-dave")],
        )
        only_alice = self.store.list_sent_unread("bob", "Alice")
        self.assertEqual([x["text"] for x in only_alice], ["one", "two"])
        removed = self.store.recall("bob", "alice", 1)
        self.assertIsNotNone(removed)
        assert removed is not None
        self.assertEqual(removed["text"], "one")
        after = self.store.list_sent_unread("bob", "alice")
        self.assertEqual([x["index"] for x in after], [1])
        self.assertEqual(after[0]["text"], "two")
        self.assertIsNone(self.store.recall("bob", "alice", 9))
        # carol's message still pending for alice
        self.assertEqual(self.store.count("alice"), 2)


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

    def test_leave_command_list_and_recall(self) -> None:
        sender = DummyConn()
        server.clients[sender] = {
            "name": "bob",
            "rooms": {"default"},
            "current_room": "default",
        }
        server.offline_messages.leave("alice", "bob", "first")
        server.offline_messages.leave("alice", "bob", "second")
        # Mimic handle_command: split(None, 1) keeps remainder as one string.
        server.handle_leave_command(sender, "bob", ["/leave", "alice"])
        listed = b"".join(sender.sent).decode("utf-8")
        self.assertIn("1. (", listed)
        self.assertIn("first", listed)
        self.assertIn("2. (", listed)
        self.assertIn("second", listed)
        sender.sent.clear()
        server.handle_leave_command(sender, "bob", ["/leave", "alice 1"])
        ack = b"".join(sender.sent).decode("utf-8")
        self.assertIn("已撤回", ack)
        self.assertIn("first", ack)
        remaining = server.offline_messages.list_sent_unread("bob", "alice")
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["text"], "second")

    def test_leave_via_handle_command_split(self) -> None:
        sender = DummyConn()
        server.clients[sender] = {
            "name": "bob",
            "rooms": {"default"},
            "current_room": "default",
        }
        server.rooms["default"].add(sender)
        server.offline_messages.leave("alice", "bob", "keep")
        server.offline_messages.leave("alice", "bob", "drop-me")
        server.handle_command(sender, "/leave alice 2")
        text = b"".join(sender.sent).decode("utf-8")
        self.assertIn("已撤回", text)
        self.assertIn("drop-me", text)
        remaining = server.offline_messages.list_sent_unread("bob", "alice")
        self.assertEqual([x["text"] for x in remaining], ["keep"])

    def test_file_leave_deliver_and_recall(self) -> None:
        class FakeHTTP:
            def get_base_url(self):
                return "https://files.example:8443"

        prev_http = server.file_http
        server.file_http = FakeHTTP()
        try:
            server.offline_messages.leave(
                "alice",
                "bob",
                "[文件] notes.pdf (1.0 KB)",
                kind="file",
                meta={
                    "transfer_id": "tid-1",
                    "filename": "notes.pdf",
                    "file_size": 1024,
                    "download_token": "tok-alice",
                    "download_key": "ABCDEF",
                    "room": None,
                },
            )
            conn = DummyConn()
            n = server.deliver_offline_messages(conn, "alice")
            self.assertEqual(n, 1)
            text = b"".join(conn.sent).decode("utf-8")
            self.assertIn("收到新文件", text)
            self.assertIn("notes.pdf", text)
            self.assertIn("ABCDEF", text)
            self.assertIn("/download/tok-alice", text)
            self.assertEqual(server.offline_messages.count("alice"), 0)

            server.offline_messages.leave(
                "carol",
                "bob",
                "[文件] secret.bin (2.0 KB)",
                kind="file",
                meta={
                    "transfer_id": "tid-2",
                    "filename": "secret.bin",
                    "file_size": 2048,
                    "download_token": "tok-carol",
                    "download_key": "XYZ123",
                    "room": None,
                },
            )
            with patch.object(
                server.file_sharing.file_transfer_store,
                "revoke_recipient",
                return_value=True,
            ) as revoke:
                sender = DummyConn()
                server.clients[sender] = {
                    "name": "bob",
                    "rooms": {"default"},
                    "current_room": "default",
                }
                server.handle_leave_command(sender, "bob", ["/leave", "carol 1"])
                ack = b"".join(sender.sent).decode("utf-8")
                self.assertIn("已撤回", ack)
                self.assertIn("文件", ack)
                self.assertIn("secret.bin", ack)
                revoke.assert_called_once_with("tid-2", "carol")
            self.assertEqual(
                server.offline_messages.list_sent_unread("bob", "carol"),
                [],
            )
        finally:
            server.file_http = prev_http


if __name__ == "__main__":
    unittest.main()
