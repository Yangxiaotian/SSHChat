import unittest

from games import create_game
from tests.test_new_games_common import DummyConn


class ReversiGameTests(unittest.TestCase):
    def setUp(self):
        self.black = DummyConn("black")
        self.white = DummyConn("white")
        self.game = create_game("reversi", self.black, "black")
        self.game.try_join(self.white, "white")

    def test_opening_flips_bracketed_piece_and_changes_turn(self):
        self.assertEqual(self.game.try_move(self.black, "3 4")[0], [])
        self.assertEqual(self.game.board[2][3], 1)
        self.assertEqual(self.game.board[3][3], 1)
        self.assertEqual(self.game.turn, 2)

    def test_occupied_or_non_flipping_move_is_rejected_without_mutation(self):
        before = [row[:] for row in self.game.board]
        error, _, _ = self.game.try_move(self.black, "4 4")
        self.assertTrue(error)
        self.assertEqual(self.game.board, before)

    def test_two_consecutive_passes_end_as_score_result(self):
        self.game.board = [[1] * 8 for _ in range(8)]
        self.game.turn = 1
        _, _, first_done = self.game.try_move(self.black, "pass")
        self.assertFalse(first_done)
        _, _, second_done = self.game.try_move(self.white, "pass")
        self.assertTrue(second_done)

    def test_show_marks_only_the_opponents_last_action(self):
        self.assertEqual(self.game.try_move(self.black, "3 4")[0], [])
        black_view = "\n".join(self.game.show(self.black))
        white_view = "\n".join(self.game.show(self.white))
        self.assertNotIn("!#", black_view)
        self.assertIn("!#", white_view)


if __name__ == "__main__":
    unittest.main()
