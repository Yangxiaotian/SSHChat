import tempfile
import unittest

from games import GoGame, create_game, resolve_game_name
from ratings import GameRatingStore


class GoGameTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = GameRatingStore(f"{self.tmp.name}/ratings.json")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _started(self, black_name: str = "Alice", white_name: str = "Bob") -> tuple[GoGame, object, object]:
        black = object()
        white = object()
        game = GoGame(black, black_name, rating_store=self.store)
        err, _bcast, _ = game.try_join(white, white_name)
        self.assertEqual(err, [])
        return game, black, white

    def test_create_and_alias(self) -> None:
        self.assertEqual(resolve_game_name("围棋"), "go")
        self.assertEqual(resolve_game_name("weiqi"), "go")
        game = create_game("go", object(), "Alice", rating_store=self.store)
        self.assertIsInstance(game, GoGame)

    def test_capture_single_stone(self) -> None:
        game, black, white = self._started()
        sequence = [
            (black, "2 1"),
            (white, "2 2"),
            (black, "1 2"),
            (white, "10 10"),
            (black, "2 3"),
            (white, "11 10"),
            (black, "3 2"),
        ]
        last_bcast = []
        for conn, move in sequence:
            err, last_bcast, _ = game.try_move(conn, move)
            self.assertEqual(err, [])
        self.assertEqual(game.grid[1][1], 0)
        self.assertEqual(game._captures[1], 1)
        self.assertTrue(any("提子 1" in line for line in last_bcast))

    def test_suicide_is_rejected(self) -> None:
        game, black, _white = self._started()
        game.grid[0][1] = 2
        game.grid[1][0] = 2
        game.grid[1][2] = 2
        game.grid[2][1] = 2
        err, _bcast, done = game.try_move(black, "2 2")
        self.assertFalse(done)
        self.assertTrue(any("自杀" in line for line in err))
        self.assertEqual(game.grid[1][1], 0)

    def test_true_ko_retake_is_rejected(self) -> None:
        game, black, white = self._started()
        game.grid = [[0 for _ in range(19)] for _ in range(19)]
        # A real simple-ko shape: black at 4,4 captures one white stone at 4,3,
        # and the new black stone has exactly one liberty, so immediate retake is illegal.
        for row, col, stone in [
            (3, 3, 1),
            (3, 4, 2),
            (4, 2, 1),
            (4, 3, 2),
            (4, 5, 2),
            (5, 3, 1),
            (5, 4, 2),
        ]:
            game.grid[row - 1][col - 1] = stone
        game._turn = 1

        err_black, _bcast_black, _ = game.try_move(black, "4 4")
        err_white, _bcast_white, _ = game.try_move(white, "4 3")

        self.assertEqual(err_black, [])
        self.assertTrue(any("劫点" in line for line in err_white))
        self.assertEqual(game.grid[3][2], 0)
        self.assertEqual(game.grid[3][3], 1)

    def test_non_ko_single_capture_allows_next_move_on_captured_point(self) -> None:
        game, black, white = self._started()
        game.grid = [[0 for _ in range(19)] for _ in range(19)]
        # Single capture, but the newly placed black stone has more than one liberty.
        # This is not a ko; white may legally play the captured point next.
        for row, col, stone in [
            (3, 4, 2),
            (4, 2, 1),
            (4, 3, 2),
            (4, 4, 1),
            (4, 5, 2),
            (5, 3, 1),
            (5, 4, 2),
        ]:
            game.grid[row - 1][col - 1] = stone
        game._turn = 1

        err_black, _bcast_black, _ = game.try_move(black, "3 3")
        err_white, _bcast_white, _ = game.try_move(white, "4 3")

        self.assertEqual(err_black, [])
        self.assertEqual(err_white, [])
        self.assertEqual(game.grid[3][2], 2)

    def test_two_passes_end_and_record_rating(self) -> None:
        game, black, white = self._started()
        err1, b1, done1 = game.try_move(black, "pass")
        self.assertEqual(err1, [])
        self.assertFalse(done1)
        self.assertTrue(any("停一手" in line for line in b1))

        err2, b2, done2 = game.try_move(white, "停一手")
        self.assertEqual(err2, [])
        self.assertTrue(done2)
        self.assertEqual(game.state, "ended")
        self.assertTrue(any("开始数子" in line for line in b2))
        self.assertEqual(self.store.profile("go", "Alice")["games"], 1)
        self.assertEqual(self.store.profile("go", "Bob")["games"], 1)

    def test_undo_restores_captures_and_turn(self) -> None:
        game, black, white = self._started()
        game.try_move(black, "4 4")
        game.try_move(white, "16 16")
        _priv, _bcast, _ = game.request_undo(white)
        err, bcast, done = game.accept_undo(black)
        self.assertEqual(err, ["悔棋成功，已撤销你的上一步。"])
        self.assertFalse(done)
        self.assertEqual(game.grid[15][15], 0)
        self.assertEqual(game._turn, 2)
        self.assertTrue(any("轮到 白方 Bob 落子" in line for line in bcast))

    def test_show_exposes_current_ko_point(self) -> None:
        game, _black, _white = self._started()
        game._ko_point = (4, 5)

        lines = game.show()

        self.assertTrue(any("劫点：第 5 行，第 6 列" in line for line in lines))

    def test_show_hides_katago_move_history_from_regular_users(self) -> None:
        game, black, white = self._started()
        self.assertEqual(game.try_move(black, "4 4")[0], [])
        self.assertEqual(game.try_move(white, "16 16")[0], [])
        self.assertEqual(game.try_move(black, "pass")[0], [])

        no_conn_lines = game.show()
        black_lines = game.show(black)
        white_lines = game.show(white)

        self.assertFalse(any("KataGo手顺" in line for line in no_conn_lines))
        self.assertFalse(any("KataGo手顺" in line for line in black_lines))
        self.assertFalse(any("KataGo手顺" in line for line in white_lines))

    def test_show_exposes_katago_move_history_only_to_zouyu(self) -> None:
        game, black, white = self._started(black_name="zouyu", white_name="Bob")
        spectator = object()
        self.assertEqual(game.try_move(black, "4 4")[0], [])
        self.assertEqual(game.try_move(white, "16 16")[0], [])
        self.assertEqual(game.try_move(black, "pass")[0], [])

        zouyu_lines = game.show(black)
        white_lines = game.show(white)
        spectator_lines = game.show(spectator)

        self.assertTrue(
            any("KataGo手顺：B D16; W Q4; B pass" in line for line in zouyu_lines)
        )
        self.assertFalse(any("KataGo手顺" in line for line in white_lines))
        self.assertFalse(any("KataGo手顺" in line for line in spectator_lines))

    def test_undo_rewinds_katago_move_history(self) -> None:
        game, black, white = self._started(black_name="zouyu", white_name="Bob")
        self.assertEqual(game.try_move(black, "4 4")[0], [])
        self.assertEqual(game.try_move(white, "16 16")[0], [])

        game.request_undo(white)
        game.accept_undo(black)
        lines = game.show(black)

        self.assertTrue(any("KataGo手顺：B D16" in line for line in lines))
        self.assertFalse(any("W Q4" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
