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


if __name__ == "__main__":
    unittest.main()
