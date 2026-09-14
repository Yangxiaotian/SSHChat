"""Room /pad shared clipboard."""

from __future__ import annotations

import unittest

import server


class DummyConn:
    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def send(self, data: bytes) -> None:
        self.sent.append(data)


class PadTests(unittest.TestCase):
    def setUp(self) -> None:
        server.clients.clear()
        server.rooms.clear()
        server.room_owners.clear()
        server.room_announcements.clear()
        server.room_pads.clear()
        server.room_pad_revs.clear()
        server.room_polls.clear()
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

    def test_set_show_clear(self) -> None:
        server.handle_command(self.alice, "/pad meet at 21:00")
        self.assertEqual(server.room_pads["lobby"], "meet at 21:00")
        self.assertIn("updated the pad", self._out(self.bob).lower())

        self.alice.sent.clear()
        server.handle_command(self.alice, "/pad")
        self.assertIn("meet at 21:00", self._out(self.alice))

        self.bob.sent.clear()
        server.handle_command(self.bob, "/pad clear")
        self.assertNotIn("lobby", server.room_pads)
        self.assertIn("cleared the pad", self._out(self.bob).lower())

    def test_anyone_can_overwrite(self) -> None:
        server.handle_command(self.alice, "/pad first")
        server.handle_command(self.bob, "/pad second draft")
        self.assertEqual(server.room_pads["lobby"], "second draft")

    def test_rejects_too_long(self) -> None:
        server.handle_command(self.alice, "/pad " + ("x" * (server.MAX_PAD_LEN + 1)))
        self.assertNotIn("lobby", server.room_pads)
        self.assertIn("too long", self._out(self.alice).lower())

    def test_clear_phrase_is_content_not_command(self) -> None:
        server.handle_command(self.alice, "/pad clear wifi password later")
        self.assertEqual(server.room_pads["lobby"], "clear wifi password later")

    def test_load_preserves_newlines(self) -> None:
        import base64

        text = "line1\nline2\nline3"
        b64 = base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")
        server.handle_command(self.alice, f"/pad load {b64}")
        self.assertEqual(server.room_pads["lobby"], text)
        self.alice.sent.clear()
        server.handle_command(self.alice, "/pad")
        out = self._out(self.alice)
        self.assertIn("3 lines", out.lower())
        self.assertIn("line2", out)

    def test_dump_roundtrip(self) -> None:
        import base64

        server.handle_command(self.alice, "/pad hello")
        self.alice.sent.clear()
        server.handle_command(self.alice, "/pad dump")
        out = self._out(self.alice)
        self.assertTrue(out.startswith(server.PAD_DUMP_PREFIX))
        blob = out[len(server.PAD_DUMP_PREFIX) :].strip()
        self.assertEqual(base64.urlsafe_b64decode(blob).decode("utf-8"), "hello")

    def test_edit_hint_for_non_terminal(self) -> None:
        server.handle_command(self.bob, "/pad edit")
        self.assertIn("terminal", self._out(self.bob).lower())

    def test_fed_pad_lww_and_clear(self) -> None:
        server.room_pads["lobby"] = "old"
        server.room_pad_revs["lobby"] = 100
        server._fed_on_pad_sync("peer-a", "lobby", "new\nline", 200)
        self.assertEqual(server.room_pads["lobby"], "new\nline")
        self.assertEqual(server.room_pad_revs["lobby"], 200)
        # Stale revision ignored.
        server._fed_on_pad_sync("peer-b", "lobby", "stale", 150)
        self.assertEqual(server.room_pads["lobby"], "new\nline")
        # Clear via empty body with newer rev.
        server._fed_on_pad_sync("peer-a", "lobby", "", 300)
        self.assertNotIn("lobby", server.room_pads)
        self.assertEqual(server.room_pad_revs["lobby"], 300)

    def test_set_bumps_rev_and_clear_keeps_rev(self) -> None:
        server.handle_command(self.alice, "/pad hello fed")
        self.assertGreater(server.room_pad_revs.get("lobby", 0), 0)
        rev = server.room_pad_revs["lobby"]
        server.handle_command(self.bob, "/pad clear")
        self.assertNotIn("lobby", server.room_pads)
        self.assertGreater(server.room_pad_revs["lobby"], rev)


if __name__ == "__main__":
    unittest.main()
