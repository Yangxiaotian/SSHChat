import tempfile
import unittest

from games import GomokuGame
import server
from session_store import DisconnectedSeat, GameSessionStore


class DummyConn:
    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def send(self, data: bytes) -> None:
        self.sent.append(data)


class ServerRestartResumeTests(unittest.TestCase):
    def setUp(self) -> None:
        server.clients.clear()
        server.rooms.clear()
        server.room_owners.clear()
        server.room_announcements.clear()
        server.room_games.clear()
        server.room_enabled_games.clear()
        server.disconnected_sessions.clear()
        self._tmpdir = tempfile.TemporaryDirectory()
        self._store_path = f"{self._tmpdir.name}/game_sessions.json"
        server.session_store = GameSessionStore(self._store_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_active_gomoku_survives_restart_and_reconnect(self) -> None:
        room = "default"
        black = DummyConn()
        white = DummyConn()
        game = GomokuGame(black, "zouyu")
        err, _bcast, _ = game.try_join(white, "yxt")
        self.assertEqual(err, [])
        priv, _move_bcast, _ = game.try_move(black, "8 8")
        self.assertEqual(priv, [])

        server.clients[black] = {"name": "zouyu", "rooms": {room}, "current_room": room}
        server.clients[white] = {"name": "yxt", "rooms": {room}, "current_room": room}
        server.rooms[room].update({black, white})
        server.room_owners[room] = black
        server.room_games[room] = game
        server._remember_session_locked("zouyu", [room], room)
        server._remember_session_locked("yxt", [room], room)

        with server.lock:
            payload = server._build_session_payload_locked()
        server.session_store.save(payload)

        server.clients.clear()
        server.rooms.clear()
        server.room_owners.clear()
        server.room_games.clear()
        server.disconnected_sessions.clear()

        server._load_persisted_sessions()

        self.assertIn(room, server.room_games)
        restored = server.room_games[room]
        self.assertEqual(restored.grid[7][7], 1)
        self.assertIsInstance(restored.black_conn, DisconnectedSeat)
        self.assertIsInstance(restored.white_conn, DisconnectedSeat)

        new_white = DummyConn()
        server.clients[new_white] = {
            "name": "yxt",
            "rooms": {room},
            "current_room": room,
        }
        server.rooms[room].add(new_white)

        moved = server._resume_same_account_seat_locked(
            room, restored, new_white, "yxt"
        )
        priv, _bcast, _ = restored.try_move(new_white, "8 9")

        self.assertTrue(moved)
        self.assertIs(restored.white_conn, new_white)
        self.assertEqual(priv, [])
        self.assertEqual(restored.grid[7][8], 2)

    def test_ended_games_are_not_persisted(self) -> None:
        room = "default"
        black = DummyConn()
        game = GomokuGame(black, "zouyu")
        game.state = "ended"
        server.room_games[room] = game

        with server.lock:
            payload = server._build_session_payload_locked()
        self.assertEqual(payload["room_games"], {})


if __name__ == "__main__":
    unittest.main()
