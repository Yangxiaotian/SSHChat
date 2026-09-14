"""Room /poll command."""

from __future__ import annotations

import unittest

import server


class DummyConn:
    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def send(self, data: bytes) -> None:
        self.sent.append(data)


class PollTests(unittest.TestCase):
    def setUp(self) -> None:
        server.clients.clear()
        server.rooms.clear()
        server.room_owners.clear()
        server.room_announcements.clear()
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

    def test_new_vote_close(self) -> None:
        server.handle_command(self.alice, "/poll new dinner? | pizza | sushi | tacos")
        self.assertIn("lobby", server.room_polls)
        self.assertIn("started a poll", self._out(self.bob))

        self.alice.sent.clear()
        self.bob.sent.clear()
        server.handle_command(self.bob, "/poll 2")
        self.assertIn("Voted for 2. sushi", self._out(self.bob))
        self.assertEqual(server.room_polls["lobby"]["votes"]["bob"], 1)

        server.handle_command(self.bob, "/poll 1")
        self.assertEqual(server.room_polls["lobby"]["votes"]["bob"], 0)

        self.alice.sent.clear()
        self.bob.sent.clear()
        server.handle_command(self.alice, "/poll close")
        self.assertNotIn("lobby", server.room_polls)
        closed = self._out(self.bob)
        self.assertIn("poll closed", closed)
        self.assertIn("pizza", closed)

    def test_close_denied_for_non_owner(self) -> None:
        server.handle_command(self.alice, "/poll new Q | A | B")
        self.bob.sent.clear()
        server.handle_command(self.bob, "/poll close")
        self.assertIn("Only the creator or room owner", self._out(self.bob))
        self.assertIn("lobby", server.room_polls)

    def test_rejects_bad_new(self) -> None:
        server.handle_command(self.alice, "/poll new only-question")
        self.assertNotIn("lobby", server.room_polls)
        self.assertIn("at least 2 options", self._out(self.alice))


if __name__ == "__main__":
    unittest.main()
