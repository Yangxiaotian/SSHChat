import tempfile
import unittest

import server
from games import ChessGame, GomokuGame, XiangqiGame, chess_available
from ratings import GameRatingStore


class BoardAiAndRatingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.store = GameRatingStore(f"{self._tmpdir.name}/ratings.json")
        self._old_server_store = server.rating_store

    def tearDown(self) -> None:
        server.rating_store = self._old_server_store
        self._tmpdir.cleanup()

    def test_chess_ai_practice_game_does_not_persist_rating(self) -> None:
        if not chess_available():
            self.skipTest("python-chess is not installed")
        player_conn = object()
        game = ChessGame(player_conn, "Alice", rating_store=self.store, ai_level="easy")

        err, bcast, done = game.try_move(player_conn, "e4")

        self.assertEqual(err, [])
        self.assertFalse(done)
        self.assertTrue(any("AI-" in line for line in bcast))
        profile = self.store.profile("chess", "Alice")
        self.assertEqual(profile["rating"], 1200)
        self.assertEqual(profile["games"], 0)
        self.assertTrue(game.send_view_on_move)

    def test_human_chess_result_persists_rating(self) -> None:
        if not chess_available():
            self.skipTest("python-chess is not installed")
        white_conn = object()
        black_conn = object()
        game = ChessGame(white_conn, "Alice", rating_store=self.store)

        err_join, _bcast_join, _ = game.try_join(black_conn, "Bob")
        self.assertEqual(err_join, [])

        err_resign, bcast_resign, done = game.resign(black_conn, "Bob")

        self.assertEqual(err_resign, [])
        self.assertTrue(done)
        self.assertTrue(any("积分结算" in line for line in bcast_resign))
        alice = self.store.profile("chess", "Alice")
        bob = self.store.profile("chess", "Bob")
        self.assertEqual(alice["games"], 1)
        self.assertEqual(bob["games"], 1)
        self.assertGreater(alice["rating"], 1200)
        self.assertLess(bob["rating"], 1200)

    def test_gomoku_ai_practice_game_responds(self) -> None:
        player_conn = object()
        game = GomokuGame(player_conn, "Alice", rating_store=self.store, ai_level="easy")

        err, bcast, done = game.try_move(player_conn, "8 8")

        self.assertEqual(err, [])
        self.assertFalse(done)
        self.assertTrue(any("AI-" in line for line in bcast))
        profile = self.store.profile("gomoku", "Alice")
        self.assertEqual(profile["games"], 0)
        self.assertTrue(game.send_view_on_move)

    def test_gomoku_ai_nudge_after_reconnect_when_ai_turn(self) -> None:
        player_conn = object()
        game = GomokuGame(player_conn, "Alice", ai_level="easy")
        game._turn = 2
        lines = game.nudge_bots()
        self.assertTrue(lines)
        self.assertEqual(game._turn, 1)

    def test_gomoku_uses_cultivation_levels_without_affecting_other_board_games(self) -> None:
        gomoku_profile = self.store.profile("gomoku", "Alice")
        go_profile = self.store.profile("go", "Alice")
        xiangqi_profile = self.store.profile("xiangqi", "Alice")

        self.assertEqual(gomoku_profile["level"], "?????")
        self.assertEqual(go_profile["level"], "10?")
        self.assertEqual(xiangqi_profile["level"], "10?")

    def test_xiangqi_ai_practice_game_responds(self) -> None:
        player_conn = object()
        game = XiangqiGame(player_conn, "Alice", rating_store=self.store, ai_level="easy")

        err, bcast, done = game.try_move(player_conn, "炮二平五")

        self.assertEqual(err, [])
        self.assertFalse(done)
        self.assertTrue(any("AI-" in line for line in bcast))
        profile = self.store.profile("xiangqi", "Alice")
        self.assertEqual(profile["games"], 0)

    def test_server_reset_rating_flags_reset_store(self) -> None:
        server.rating_store = self.store
        self.store.record_result("gomoku", "Alice", "Bob", 1.0)

        rc = server.main(["--reset-ratings-user-game", "Alice", "gomoku"])
        self.assertEqual(rc, 0)
        alice = self.store.profile("gomoku", "Alice")
        bob = self.store.profile("gomoku", "Bob")
        self.assertEqual(alice["rating"], 1200)
        self.assertEqual(alice["games"], 0)
        self.assertEqual(bob["games"], 1)


if __name__ == "__main__":
    unittest.main()
