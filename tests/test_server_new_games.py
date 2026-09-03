import unittest

import games
import server
from tests.test_battleship_game import place_standard
from tests.test_junqi_game import place_side


class Conn:
    def __init__(self, name):
        self.name = name
        self.out = []

    def send(self, payload):
        self.out.append(payload.decode("utf-8"))


class ServerNewGamesTests(unittest.TestCase):
    def setUp(self):
        self.room = "__new_games_server_smoke__"
        self.first = Conn("first")
        self.second = Conn("second")
        self.saved = {
            "clients": dict(server.clients),
            "rooms": {key: set(value) for key, value in server.rooms.items()},
            "owners": dict(server.room_owners),
            "games": dict(server.room_games),
            "enabled": {key: set(value) for key, value in server.room_enabled_games.items()},
        }
        server.clients.clear()
        server.rooms.clear()
        server.room_owners.clear()
        server.room_games.clear()
        server.room_enabled_games.clear()
        for conn, name in ((self.first, "first"), (self.second, "second")):
            server.clients[conn] = {
                "name": name,
                "rooms": {self.room},
                "current_room": self.room,
                "locale": "en",
            }
        server.rooms[self.room] = {self.first, self.second}
        server.room_owners[self.room] = self.first
        self.saved_hooks = {
            "persist": server._persist_after_game_change,
            "sync": server._federation_sync_game,
            "end": server._federation_notify_game_end,
            "nudge": server._nudge_game_bots_locked,
            "touch": games.touch_session,
        }
        server._persist_after_game_change = lambda: None
        server._federation_sync_game = lambda _room: None
        server._federation_notify_game_end = lambda _room: None
        server._nudge_game_bots_locked = lambda _game: []
        games.touch_session = lambda _game: None

    def tearDown(self):
        server._persist_after_game_change = self.saved_hooks["persist"]
        server._federation_sync_game = self.saved_hooks["sync"]
        server._federation_notify_game_end = self.saved_hooks["end"]
        server._nudge_game_bots_locked = self.saved_hooks["nudge"]
        games.touch_session = self.saved_hooks["touch"]
        server.clients.clear()
        server.clients.update(self.saved["clients"])
        server.rooms.clear()
        for key, value in self.saved["rooms"].items():
            server.rooms[key] = value
        server.room_owners.clear()
        server.room_owners.update(self.saved["owners"])
        server.room_games.clear()
        server.room_games.update(self.saved["games"])
        server.room_enabled_games.clear()
        for key, value in self.saved["enabled"].items():
            server.room_enabled_games[key] = value

    def _command(self, conn, name, payload):
        conn.out.clear()
        server._handle_game(conn, name, self.room, payload)
        return "\n".join(conn.out)

    def _start_and_join(self, name):
        self._command(self.first, "first", f"/game new {name}")
        output = self._command(self.second, "second", "/game join")
        self.assertIn(name, output.lower())
        self.assertIn("Terminal:", "\n".join(self.first.out + self.second.out))

    def test_same_game_card_joins_existing_waiting_game(self):
        self._command(self.first, "first", "/game new darkchess")
        output = self._command(self.second, "second", "/game new darkchess")
        self.assertNotIn("already active", output.lower())
        self.assertIs(server.room_games[self.room].second_conn, self.second)
        self.assertIn("joined", output.lower())

    def test_reversi_move_uses_server_command_pipeline(self):
        self._start_and_join("reversi")
        output = self._command(self.first, "first", "/game move 3 4")
        self.assertNotIn("command failed", output.lower())
        self.assertEqual(server.room_games[self.room].turn, 2)

    def test_darkchess_flip_does_not_become_generic_failure(self):
        self._start_and_join("darkchess")
        output = self._command(self.first, "first", "/game move flip 1 1")
        self.assertNotIn("command failed", output.lower())
        self.assertIn("flips", output.lower())
        self.assertEqual(server.room_games[self.room].turn, 2)

    def test_battleship_ready_and_fire_use_server_command_pipeline(self):
        self._start_and_join("battleship")
        game = server.room_games[self.room]
        place_standard(game, self.first)
        place_standard(game, self.second)
        self._command(self.first, "first", "/game move ready")
        self._command(self.second, "second", "/game move ready")
        output = self._command(self.first, "first", "/game move fire 10 10")
        self.assertNotIn("command failed", output.lower())
        self.assertEqual(game.turn, 2)

    def test_junqi_ready_and_move_use_server_command_pipeline(self):
        self._start_and_join("junqi")
        game = server.room_games[self.room]
        place_side(game, self.first, range(1, 6), 2)
        place_side(game, self.second, range(8, 13), 2)
        self._command(self.first, "first", "/game move ready")
        self._command(self.second, "second", "/game move ready")
        game.board[4][0] = {"side": 1, "kind": "commander", "revealed": False}
        game.board[5][0] = None
        game.turn = 1
        output = self._command(self.first, "first", "/game move move 5 1 6 1")
        self.assertNotIn("command failed", output.lower())
        self.assertEqual(game.turn, 2)

    def test_post_move_sync_failure_does_not_hide_a_successful_move(self):
        self._start_and_join("darkchess")
        server._federation_sync_game = lambda _room: (_ for _ in ()).throw(RuntimeError("sync down"))
        output = self._command(self.first, "first", "/game move flip 1 1")
        self.assertNotIn("command failed", output.lower())
        self.assertEqual(server.room_games[self.room].turn, 2)


if __name__ == "__main__":
    unittest.main()
