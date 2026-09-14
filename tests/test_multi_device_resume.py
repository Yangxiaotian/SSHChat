import unittest
from unittest import mock

from games import GomokuGame, SanguoshaGame
import server
from session_store import DisconnectedSeat


class DummyConn:
    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self.closed = False

    def send(self, data: bytes) -> None:
        self.sent.append(data)

    def close(self) -> None:
        self.closed = True


class DummyGame:
    def __init__(self, old_conn, seat_name: str) -> None:
        self.state = "playing"
        self.players = [(old_conn, seat_name), (DummyConn(), "R1")]
        self.leave_calls = 0

    def is_seated(self, conn) -> bool:
        return any(c is conn for c, _ in self.players)

    def on_player_leave(self, conn, name: str):
        self.leave_calls += 1
        return ([], [f"{name} left"], False)


class MultiDeviceResumeTests(unittest.TestCase):
    def setUp(self) -> None:
        server.clients.clear()
        server.rooms.clear()
        server.room_owners.clear()
        server.room_announcements.clear()
        server.room_polls.clear()
        server.room_capsules.clear()
        server.room_games.clear()
        server.room_enabled_games.clear()
        server.disconnected_sessions.clear()

    def test_resume_helper_transfers_same_account_seat(self) -> None:
        room = "default"
        old_conn = DummyConn()
        new_conn = DummyConn()
        game = DummyGame(old_conn, "zouyu")

        server.clients[old_conn] = {"name": "zouyu", "rooms": {room}, "current_room": room}
        server.clients[new_conn] = {"name": "zouyu", "rooms": {room}, "current_room": room}
        server.rooms[room].add(old_conn)
        server.rooms[room].add(new_conn)

        moved = server._resume_same_account_seat_locked(room, game, new_conn, "zouyu")
        self.assertTrue(moved)
        self.assertIs(game.players[0][0], new_conn)

    def test_disconnect_migrates_seat_when_same_account_peer_online(self) -> None:
        room = "default"
        old_conn = DummyConn()
        new_conn = DummyConn()
        game = DummyGame(old_conn, "zouyu")

        server.clients[old_conn] = {"name": "zouyu", "rooms": {room}, "current_room": room}
        server.clients[new_conn] = {"name": "zouyu", "rooms": {room}, "current_room": room}
        server.rooms[room].add(old_conn)
        server.rooms[room].add(new_conn)
        server.room_owners[room] = old_conn
        server.room_games[room] = game

        server.remove_client(old_conn)

        self.assertNotIn(old_conn, server.clients)
        self.assertIn(new_conn, server.clients)
        self.assertIs(game.players[0][0], new_conn)
        self.assertEqual(game.leave_calls, 0)
        self.assertIs(server.room_owners[room], new_conn)

    def test_disconnect_without_peer_preserves_active_game_for_later_reconnect(self) -> None:
        room = "default"
        old_conn = DummyConn()
        game = DummyGame(old_conn, "zouyu")

        server.clients[old_conn] = {"name": "zouyu", "rooms": {room}, "current_room": room}
        server.rooms[room].add(old_conn)
        server.room_owners[room] = old_conn
        server.room_games[room] = game

        server.remove_client(old_conn)

        self.assertNotIn(old_conn, server.clients)
        self.assertIn(room, server.room_games)
        self.assertEqual(game.leave_calls, 0)
        self.assertIsInstance(game.players[0][0], DisconnectedSeat)
        self.assertEqual(game.players[0][0].nickname, "zouyu")
        self.assertIn("zouyu", server.disconnected_sessions)

        new_conn = DummyConn()
        session = server._load_recent_session_locked("zouyu")
        self.assertIsNotNone(session)
        restored_rooms = set(session["rooms"])
        server.clients[new_conn] = {
            "name": "zouyu",
            "rooms": restored_rooms,
            "current_room": session["current_room"],
        }
        for restored_room in restored_rooms:
            server.rooms[restored_room].add(new_conn)

        moved = server._resume_same_account_seat_locked(room, game, new_conn, "zouyu")

        self.assertTrue(moved)
        self.assertIs(game.players[0][0], new_conn)

    def test_game_show_does_not_run_bot_nudge(self) -> None:
        room = "default"
        conn = DummyConn()
        game = DummyGame(conn, "zouyu")
        game.name = "gomoku"
        game.show_calls = 0
        game.nudge_calls = 0

        def show(_conn=None, **_kwargs):
            game.show_calls += 1
            return ["board"]

        def nudge_bots():
            game.nudge_calls += 1
            return ["ai moved"]

        game.show = show  # type: ignore[method-assign]
        game.nudge_bots = nudge_bots  # type: ignore[attr-defined]
        server.clients[conn] = {
            "name": "zouyu",
            "rooms": {room},
            "current_room": room,
        }
        server.rooms[room].add(conn)
        server.room_games[room] = game
        with mock.patch.object(server.federation, "get_hub", return_value=None):
            server._handle_game(conn, "zouyu", room, "/game show")
        self.assertEqual(game.show_calls, 1)
        self.assertEqual(game.nudge_calls, 0)
        self.assertTrue(any(b"board" in chunk for chunk in conn.sent))

    def test_reconnected_gomoku_player_can_continue_turn(self) -> None:
        room = "default"
        old_black = DummyConn()
        white = DummyConn()
        new_black = DummyConn()
        game = GomokuGame(old_black, "zouyu")
        err_join, _bcast_join, _ = game.try_join(white, "yxt")
        self.assertEqual(err_join, [])

        server.clients[old_black] = {"name": "zouyu", "rooms": {room}, "current_room": room}
        server.clients[white] = {"name": "yxt", "rooms": {room}, "current_room": room}
        server.rooms[room].update({old_black, white})
        server.room_owners[room] = old_black
        server.room_games[room] = game

        server.remove_client(old_black)
        server.clients[new_black] = {"name": "zouyu", "rooms": {room}, "current_room": room}
        server.rooms[room].add(new_black)

        moved = server._resume_same_account_seat_locked(room, game, new_black, "zouyu")
        priv, _bcast, _done = game.try_move(new_black, "8 8")

        self.assertTrue(moved)
        self.assertEqual(priv, [])
        self.assertEqual(game.grid[7][7], 1)

    def test_sanguo_seat_conn_by_name_finds_sgs_player(self) -> None:
        host = DummyConn()
        game = SanguoshaGame(host, "yxt")
        game.try_join(DummyConn(), "bob")
        self.assertIs(server._game_seat_conn_by_name(game, "yxt"), host)
        self.assertIs(server._game_seat_conn_by_name(game, "bob"), game.players[1].conn)

    def test_sanguo_terminal_resumes_host_seat_by_nickname(self) -> None:
        room = "default"
        phone = DummyConn()
        terminal = DummyConn()
        game = SanguoshaGame(phone, "yxt")
        game.try_join(DummyConn(), "bob")

        server.clients[phone] = {"name": "yxt", "rooms": {room}, "current_room": room}
        server.clients[terminal] = {
            "name": "yxt",
            "rooms": {room},
            "current_room": room,
        }
        server.rooms[room].update({phone, terminal})

        moved = server._resume_same_account_seat_locked(room, game, terminal, "yxt")
        priv, _bcast, _ = game.try_move(terminal, "start")

        self.assertTrue(moved)
        self.assertIs(game.players[0].conn, terminal)
        self.assertNotIn("你不是玩家", "\n".join(priv))

    def test_sanguo_terminal_resumes_after_phone_disconnect(self) -> None:
        room = "default"
        phone = DummyConn()
        terminal = DummyConn()
        game = SanguoshaGame(phone, "yxt")
        game.try_join(DummyConn(), "bob")

        server.clients[phone] = {"name": "yxt", "rooms": {room}, "current_room": room}
        server.rooms[room].add(phone)
        server.room_games[room] = game

        server.remove_client(phone)

        server.clients[terminal] = {
            "name": "yxt",
            "rooms": {room},
            "current_room": room,
        }
        server.rooms[room].add(terminal)

        moved = server._resume_same_account_seat_locked(room, game, terminal, "yxt")
        priv, _bcast, _ = game.try_move(terminal, "start")

        self.assertTrue(moved)
        self.assertIs(game.players[0].conn, terminal)
        self.assertNotIn("你不是玩家", "\n".join(priv))


if __name__ == "__main__":
    unittest.main()
