import unittest

from games import create_game
from tests.test_new_games_common import DummyConn


class DarkchessGameTests(unittest.TestCase):
    def setUp(self):
        self.red = DummyConn("red")
        self.black = DummyConn("black")
        self.game = create_game("darkchess", self.red, "red")
        self.game.try_join(self.black, "black")

    def test_first_flip_assigns_side_and_reveals_only_that_piece(self):
        self.game.pieces = [
            {"side": "red", "rank": 1, "label": "G"},
        ] + [
            {"side": "black", "rank": 7, "label": "S"},
        ] * 31
        self.game.board = list(range(32))
        self.assertEqual(self.game.try_move(self.red, "flip 1 1")[0], [])
        self.assertEqual(self.game.player_side[1], "red")
        self.assertEqual(self.game.player_side[2], "black")
        self.assertIn("+G", "\n".join(self.game.show(self.red)))
        self.assertNotIn("-S", "\n".join(self.game.show(self.red)))

    def test_rank_capture_and_cannon_screen_are_enforced(self):
        self.game.player_side = {1: "red", 2: "black"}
        self.game.face_up = {0, 1, 2, 3, 4}
        self.game.board = [None] * 32
        self.game.board[0] = 0
        self.game.board[1] = 1
        self.game.board[2] = 2
        self.game.board[3] = 3
        self.game.board[4] = 4
        self.game.pieces = [
            {"side": "red", "rank": 4, "label": "R"},
            {"side": "black", "rank": 7, "label": "S"},
            {"side": "red", "rank": 6, "label": "C"},
            {"side": "black", "rank": 7, "label": "S"},
            {"side": "black", "rank": 1, "label": "G"},
        ] + [{"side": "red", "rank": 7, "label": "S"}] * 27
        self.game.turn = 1
        self.assertEqual(self.game.try_move(self.red, "move 1 1 1 2")[0], [])
        self.game.turn = 1
        self.game.board[3] = None
        self.assertTrue(self.game.try_move(self.red, "move 1 3 1 5")[0])
        self.game.turn = 1
        self.game.board[3] = 3
        self.assertEqual(self.game.try_move(self.red, "move 1 3 1 5")[0], [])

    def test_out_of_turn_does_not_change_board(self):
        before = list(self.game.board)
        error, _, _ = self.game.try_move(self.black, "flip 1 1")
        self.assertTrue(error)
        self.assertEqual(self.game.board, before)

    def test_show_keeps_columns_aligned_after_a_piece_is_revealed(self):
        self.game.pieces = [
            {"side": "red", "rank": 1, "label": "G"},
        ] + [
            {"side": "black", "rank": 7, "label": "S"},
        ] * 31
        self.game.board = list(range(32))
        self.game.face_up = {0}

        rows = [line for line in self.game.show(self.red) if line.lstrip()[:1].isdigit()]

        self.assertEqual(len(rows), 4)
        self.assertTrue(all(len(row) == len(rows[0]) for row in rows))
        self.assertEqual(rows[0].split(), ["1", "+G", "?", "?", "?", "?", "?", "?", "?"])

    def test_show_marks_only_the_opponents_last_action(self):
        self.game.pieces = [
            {"side": "red", "rank": 1, "label": "G"},
        ] + [
            {"side": "black", "rank": 7, "label": "S"},
        ] * 31
        self.game.board = list(range(32))
        self.assertEqual(self.game.try_move(self.red, "flip 1 1")[0], [])

        red_view = "\n".join(self.game.show(self.red))
        black_view = "\n".join(self.game.show(self.black))
        self.assertNotIn("!+G", red_view)
        self.assertIn("!+G", black_view)


if __name__ == "__main__":
    unittest.main()
