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
        self.assertEqual(self.game.try_move(self.red, "翻 1 1")[0], [])
        self.assertEqual(self.game.player_side[1], "red")
        self.assertEqual(self.game.player_side[2], "black")
        shown = "\n".join(self.game.show(self.red))
        self.assertIn("+将", shown)
        self.assertNotIn("-卒", shown)

    def test_board_tokens_are_fixed_width(self):
        self.game.face_up = {0}
        self.game.board = [0] + [None] + list(range(2, 32))
        self.game.pieces[0] = {"side": "red", "rank": 1, "label": "G"}
        row = next(
            line
            for line in self.game.show(self.red)
            if line.lstrip().startswith("1 ") and "+将" in line
        )
        from games import _darkchess_disp_width, _DARKCHESS_CELL_W

        body = row[3:]  # after " 1 "
        self.assertEqual(_darkchess_disp_width(body), _DARKCHESS_CELL_W * 8)
        self.assertIn("+将", body)
        self.assertIn(".", body)
        self.assertIn("?", body)

    def test_last_move_summary_describes_flip_and_capture(self):
        self.game.pieces = [
            {"side": "red", "rank": 4, "label": "R"},
            {"side": "black", "rank": 7, "label": "S"},
        ] + [{"side": "red", "rank": 7, "label": "S"}] * 30
        self.game.board = list(range(32))
        self.assertEqual(self.game.try_move(self.red, "翻 1 1")[0], [])
        shown = "\n".join(self.game.show(self.red))
        self.assertRegex(shown, r"上一步：red 翻开 \+车 于 \(1,1\)")
        self.assertIn("!+车", shown.replace(" ", ""))

        # Force sides and a capture setup after first flip assigned red to player1.
        self.game.player_side = {1: "red", 2: "black"}
        self.game.face_up = {0, 1}
        self.game.board = [0, 1] + [None] * 30
        self.game.turn = 1
        err, bcast, _ = self.game.try_move(self.red, "走 1 1 1 2")
        self.assertEqual(err, [])
        self.assertTrue(any("吃掉" in line and "-卒" in line for line in bcast))
        shown = "\n".join(self.game.show(self.red))
        self.assertIn("上一步：red 用 +车 从 (1,1) 吃掉 -卒 至 (1,2)", shown)

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
        self.assertEqual(self.game.try_move(self.red, "走 1 1 1 2")[0], [])
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

        rows = [
            line
            for line in self.game.show(self.red)
            if line.lstrip()[:1].isdigit() and ("?" in line or "+" in line or "-" in line or "." in line)
        ]

        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0].split(), ["1", "+将", "?", "?", "?", "?", "?", "?", "?"])
        from games import _darkchess_disp_width

        # Chinese piece names are one Unicode char but wider than ASCII; compare
        # display width (terminal columns), not Python string length.
        widths = {_darkchess_disp_width(row[3:]) for row in rows}
        self.assertEqual(len(widths), 1)


if __name__ == "__main__":
    unittest.main()
