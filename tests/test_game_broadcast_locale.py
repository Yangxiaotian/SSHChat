"""Per-recipient locale for game broadcast lines."""

from __future__ import annotations

import tempfile
import unittest

import federation
import server
from locale_store import LocaleStore


class DummyConn:
    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def send(self, data: bytes) -> None:
        self.sent.append(data)


class GameBroadcastLocaleTests(unittest.TestCase):
    def setUp(self) -> None:
        server.clients.clear()
        server.rooms.clear()
        server.room_games.clear()
        federation._hub = None
        server._fed_hub = None
        self._tmpdir = tempfile.TemporaryDirectory()
        server.locale_store = LocaleStore(f"{self._tmpdir.name}/user_locales.json")

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _join_room(self, conn: DummyConn, name: str, room: str, locale: str) -> None:
        server.clients[conn] = {
            "name": name,
            "rooms": {room},
            "current_room": room,
            "locale": locale,
        }
        server.rooms[room].add(conn)

    def test_broadcast_game_localizes_per_connection(self) -> None:
        room = "default"
        en_conn = DummyConn()
        zh_conn = DummyConn()
        self._join_room(en_conn, "alice", room, "en")
        self._join_room(zh_conn, "bob", room, "zh")

        server.broadcast_game(room, ["不是你的回合。"])

        self.assertEqual(len(en_conn.sent), 1)
        self.assertEqual(len(zh_conn.sent), 1)
        self.assertIn(b"Not your turn.", en_conn.sent[0])
        self.assertIn("不是你的回合。".encode(), zh_conn.sent[0])

    def test_fed_on_room_msg_relocalizes_game_broadcast(self) -> None:
        room = "default"
        en_conn = DummyConn()
        self._join_room(en_conn, "alice", room, "en")

        raw = server._format_game_lines(room, ["不是你的回合。"])
        server._fed_on_room_msg(room, raw, "remote-node")

        self.assertEqual(len(en_conn.sent), 1)
        self.assertIn(b"Not your turn.", en_conn.sent[0])

    def test_parse_game_broadcast_msg(self) -> None:
        room = "chess"
        raw = server._format_game_lines(room, ["line one", "line two"])
        parsed = server._parse_game_broadcast_msg(raw)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed[0], room)
        self.assertEqual(parsed[1], ["line one", "line two"])


if __name__ == "__main__":
    unittest.main()
