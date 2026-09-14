import unittest

from games import create_game
from tests.test_new_games_common import DummyConn


PIECES = (
    "flag", "commander", "army", "division", "division", "brigade", "brigade",
    "regiment", "regiment", "battalion", "battalion", "company", "company", "company",
    "platoon", "platoon", "platoon", "engineer", "engineer", "engineer", "mine", "mine",
    "mine", "bomb", "bomb",
)


def place_side(game, conn, rows, flag_col):
    rows = tuple(rows)
    flag = (rows[0] if rows[0] == 1 else rows[-1], flag_col)
    available = [
        (row, col)
        for row in rows
        for col in range(1, 6)
        if (row, col) != flag
    ]
    mine_rows = {4, 5} if rows[0] == 1 else {8, 9}
    bomb_rows = set(rows) - ({rows[0]} if rows[0] == 1 else {rows[-1]})
    mine_positions = [position for position in available if position[0] in mine_rows][:3]
    for position in mine_positions:
        available.remove(position)
    bomb_positions = [position for position in available if position[0] in bomb_rows][:2]
    for position in bomb_positions:
        available.remove(position)
    positions = [flag]
    for piece in PIECES[1:]:
        position = (mine_positions if piece == "mine" else bomb_positions if piece == "bomb" else available).pop(0)
        positions.append(position)
    for piece, position in zip(PIECES, positions):
        error, _, _ = game.try_move(conn, f"setup {piece} {position[0]} {position[1]}")
        assert error == [], (piece, position, error)


class JunqiGameTests(unittest.TestCase):
    def setUp(self):
        self.red = DummyConn("red")
        self.black = DummyConn("black")
        self.game = create_game("junqi", self.red, "red")
        self.game.try_join(self.black, "black")

    def test_flag_must_be_in_headquarters_and_piece_counts_are_validated(self):
        error, _, _ = self.game.try_move(self.red, "setup flag 1 1")
        self.assertTrue(error)
        self.assertEqual(self.game.try_move(self.red, "setup flag 1 2")[0], [])
        error, _, _ = self.game.try_move(self.red, "setup flag 1 2")
        self.assertTrue(error)

    def test_both_players_ready_starts_play_and_hidden_view_is_private(self):
        place_side(self.game, self.red, range(1, 6), 2)
        place_side(self.game, self.black, range(8, 13), 2)
        self.assertEqual(self.game.try_move(self.red, "ready")[0], [])
        self.assertEqual(self.game.try_move(self.black, "ready")[0], [])
        self.assertEqual(self.game.state, "playing")
        red_view = "\n".join(self.game.show(self.red))
        black_view = "\n".join(self.game.show(self.black))
        self.assertIn("+F", red_view)
        self.assertIn("-F", black_view)
        self.assertIn("?", red_view)
        self.assertIn("?", black_view)

    def test_engineer_can_clear_mine_but_normal_piece_cannot(self):
        self.game.state = "playing"
        self.game.board = [[None] * 5 for _ in range(12)]
        self.game.board[4][0] = {"side": 1, "kind": "engineer"}
        self.game.board[5][0] = {"side": -1, "kind": "mine"}
        self.game.turn = 1
        self.assertEqual(self.game.try_move(self.red, "move 5 1 6 1")[0], [])

        self.game.board[4][0] = {"side": 1, "kind": "company"}
        self.game.board[5][0] = {"side": -1, "kind": "mine"}
        self.game.turn = 1
        error, _, _ = self.game.try_move(self.red, "move 5 1 6 1")
        self.assertTrue(error)

    def test_bomb_capture_removes_both_and_railway_move_can_cross_empty_cells(self):
        self.game.state = "playing"
        self.game.board = [[None] * 5 for _ in range(12)]
        self.game.board[4][0] = {"side": 1, "kind": "bomb"}
        self.game.board[4][4] = {"side": -1, "kind": "company"}
        self.game.turn = 1
        self.assertEqual(self.game.try_move(self.red, "move 5 1 5 5")[0], [])
        self.assertIsNone(self.game.board[4][0])
        self.assertIsNone(self.game.board[4][4])

    def test_capture_reveals_both_surviving_pieces_to_both_players(self):
        self.game.state = "playing"
        self.game.board = [[None] * 5 for _ in range(12)]
        self.game.board[4][0] = {"side": 1, "kind": "commander", "revealed": False}
        self.game.board[5][0] = {"side": -1, "kind": "company", "revealed": False}
        self.game.turn = 1
        self.assertEqual(self.game.try_move(self.red, "move 5 1 6 1")[0], [])
        red_view = "\n".join(self.game.show(self.red))
        black_view = "\n".join(self.game.show(self.black))
        self.assertIn("+C", red_view)
        self.assertIn("+C", black_view)

    def test_show_marks_only_the_opponents_last_action(self):
        self.game.state = "playing"
        self.game._last = ((0, 0), (0, 1))
        self.game._last_player = 2
        red_view = "\n".join(self.game.show(self.red))
        black_view = "\n".join(self.game.show(self.black))
        self.assertIn("!.", red_view)
        self.assertNotIn("!.", black_view)

    def test_show_explains_player_zones_for_client_and_terminal(self):
        shown = "\n".join(self.game.show(self.red))
        self.assertIn("Red setup rows 1-5", shown)
        self.assertIn("neutral rows 6-7", shown)
        self.assertIn("Blue setup rows 8-12", shown)


if __name__ == "__main__":
    unittest.main()
