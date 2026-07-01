import tempfile
import unittest

import server
from games import (
    ChessGame,
    GomokuGame,
    XiangqiGame,
    _xq_adjudicate_repetition,
    _xq_copy,
    _xq_initial_board,
    _xq_position_key,
    _xq_repetition_verdict,
    chess_available,
)
from ratings import GameRatingStore
from session_store import FederatedSeat


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

        self.assertEqual(gomoku_profile["level"], "化神大圆满")
        self.assertEqual(go_profile["level"], "10级")
        self.assertEqual(xiangqi_profile["level"], "10级")

    def test_xiangqi_ai_practice_game_responds(self) -> None:
        player_conn = object()
        game = XiangqiGame(player_conn, "Alice", rating_store=self.store, ai_level="easy")

        err, bcast, done = game.try_move(player_conn, "炮二平五")

        self.assertEqual(err, [])
        self.assertFalse(done)
        self.assertTrue(any("AI-" in line for line in bcast))
        profile = self.store.profile("xiangqi", "Alice")
        self.assertEqual(profile["games"], 0)

    def test_xiangqi_absolute_coord_moves_support_client_clicks(self) -> None:
        red_conn = object()
        black_conn = object()
        game = XiangqiGame(red_conn, "Alice", rating_store=self.store)
        err_join, _bcast_join, _ = game.try_join(black_conn, "Bob")
        self.assertEqual(err_join, [])

        err_red, _bcast_red, _ = game.try_move(red_conn, "coord 10 1 9 1")
        self.assertEqual(err_red, [])
        self.assertEqual(game.board[8][0], 5)
        self.assertEqual(game.board[9][0], 0)

        err_black, _bcast_black, _ = game.try_move(black_conn, "coord 1 1 2 1")
        self.assertEqual(err_black, [])
        self.assertEqual(game.board[1][0], -5)
        self.assertEqual(game.board[0][0], 0)

    def test_xiangqi_flying_general_is_reported_as_check(self) -> None:
        red_conn = object()
        black_conn = object()
        game = XiangqiGame(red_conn, "Alice", rating_store=self.store)
        err_join, _bcast_join, _ = game.try_join(black_conn, "Bob")
        self.assertEqual(err_join, [])
        game.board = [[0 for _ in range(9)] for _ in range(10)]
        game.board[0][4] = -1
        game.board[9][4] = 1
        game._turn = 1

        lines = game.show(red_conn)

        self.assertTrue(any("被将军" in line for line in lines))

    def test_xiangqi_board_flip_follows_viewer_name_not_conn_identity(self) -> None:
        red_conn = object()
        federated_black = FederatedSeat("node-b", "Bob")
        game = XiangqiGame(red_conn, "Alice")
        err_join, _bcast_join, _ = game.try_join(federated_black, "Bob")
        self.assertEqual(err_join, [])

        local_black = object()
        black_view = "\n".join(game.show(local_black, viewer_name="Bob"))
        red_view = "\n".join(game.show(local_black, viewer_name="Alice"))

        self.assertIn("己方在下方", black_view)
        self.assertNotIn("己方在下方", red_view)

    def test_chess_board_flip_follows_viewer_name_not_conn_identity(self) -> None:
        if not chess_available():
            self.skipTest("python-chess is not installed")
        white_conn = object()
        federated_black = FederatedSeat("node-b", "Bob")
        game = ChessGame(white_conn, "Alice")
        err_join, _bcast_join, _ = game.try_join(federated_black, "Bob")
        self.assertEqual(err_join, [])

        local_black = object()
        black_view = "\n".join(game.show(local_black, viewer_name="Bob"))
        white_view = "\n".join(game.show(local_black, viewer_name="Alice"))

        self.assertIn("己方在下方", black_view)
        self.assertNotIn("己方在下方", white_view)

    def test_xiangqi_perpetual_check_adjudication(self) -> None:
        board = _xq_initial_board()
        key = _xq_position_key(board, -1)
        bb = _xq_copy(board)
        red_check = {
            "key": key,
            "mover": 1,
            "check": True,
            "chase_targets": frozenset(),
            "board_before": bb,
        }
        black_idle = {
            "key": key,
            "mover": -1,
            "check": False,
            "chase_targets": frozenset(),
            "board_before": bb,
        }
        ply_log = [red_check, black_idle, red_check, black_idle, red_check]
        msg, score = _xq_adjudicate_repetition(ply_log, 0, 4)
        self.assertIn("长将", msg)
        self.assertEqual(score, 0.0)

    def test_xiangqi_idle_repetition_is_draw(self) -> None:
        board = _xq_initial_board()
        key = _xq_position_key(board, -1)
        bb = _xq_copy(board)
        idle = {
            "key": key,
            "mover": 1,
            "check": False,
            "chase_targets": frozenset(),
            "board_before": bb,
        }
        idle_b = {**idle, "mover": -1}
        ply_log = [idle, idle_b, idle, idle_b, idle]
        msg, score = _xq_adjudicate_repetition(ply_log, 0, 4)
        self.assertIn("和棋", msg)
        self.assertEqual(score, 0.5)

    def test_xiangqi_repetition_verdict_on_third_identical_position(self) -> None:
        board = _xq_initial_board()
        key = _xq_position_key(board, -1)
        bb = _xq_copy(board)
        ply_log: list[dict] = []
        for mover, check in ((1, True), (-1, False)) * 2 + ((1, True),):
            ply_log.append(
                {
                    "key": key,
                    "mover": mover,
                    "check": check,
                    "chase_targets": frozenset(),
                    "board_before": bb,
                }
            )
        verdict = _xq_repetition_verdict(ply_log)
        self.assertIsNotNone(verdict)
        msg, score = verdict
        self.assertIn("长将", msg)
        self.assertEqual(score, 0.0)

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
