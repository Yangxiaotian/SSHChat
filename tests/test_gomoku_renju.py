"""Renju forbidden-move rules for gomoku."""

import unittest

from games import (
    GOMOKU_SIZE,
    GomokuGame,
    _gomoku_axis_has_four,
    _gomoku_axis_open_three,
    _gomoku_renju_forbidden,
    _gomoku_winner_at,
)


class TestGomokuRenjuAxisPatterns(unittest.TestCase):
    def test_open_three_straight(self) -> None:
        self.assertTrue(_gomoku_axis_open_three("..XXX...."))

    def test_open_three_jump(self) -> None:
        self.assertTrue(_gomoku_axis_open_three("..X.XX..."))

    def test_open_three_jump_xx_x(self) -> None:
        self.assertTrue(_gomoku_axis_open_three(".XX.X...."))

    def test_open_three_jump_x_xx(self) -> None:
        self.assertTrue(_gomoku_axis_open_three(".X.XX...."))

    def test_four_rush(self) -> None:
        self.assertTrue(_gomoku_axis_has_four(".XXXX...."))

    def test_four_rush_right_of_center(self) -> None:
        self.assertTrue(_gomoku_axis_has_four("##..XXXXO"))

    def test_four_open(self) -> None:
        self.assertTrue(_gomoku_axis_has_four("..XXXX..."))

    def test_dead_four_not_counted(self) -> None:
        self.assertFalse(_gomoku_axis_has_four("OXXXXO..."))


class TestGomokuRenjuForbidden(unittest.TestCase):
    def _empty_grid(self) -> list[list[int]]:
        return [[0] * GOMOKU_SIZE for _ in range(GOMOKU_SIZE)]

    def test_overline_forbidden(self) -> None:
        g = self._empty_grid()
        for c in range(6):
            g[7][c] = 1
        g[7][6] = 1  # six in a row on row 8
        self.assertIn("长连", _gomoku_renju_forbidden(g, 7, 6))

    def test_black_exactly_five_wins(self) -> None:
        g = self._empty_grid()
        for c in range(4):
            g[7][c] = 1
        g[7][4] = 1
        self.assertTrue(_gomoku_winner_at(g, 7, 4, 1))

    def test_black_six_not_win(self) -> None:
        g = self._empty_grid()
        for c in range(6):
            g[7][c] = 1
        self.assertFalse(_gomoku_winner_at(g, 7, 5, 1))

    def test_white_five_or_more_wins(self) -> None:
        g = self._empty_grid()
        for c in range(6):
            g[7][c] = 2
        self.assertTrue(_gomoku_winner_at(g, 7, 5, 2))


class TestGomokuRenjuReportedPosition(unittest.TestCase):
    """Regression: diagonal XXXX through last move must count toward 四四."""

    def test_double_four_on_user_diagonal(self) -> None:
        g = [[0] * GOMOKU_SIZE for _ in range(GOMOKU_SIZE)]
        rows = [
            "...............",
            "........o......",
            "........##oo...",
            ".......#o###o..",
            "....o##.#o##...",
            ".....#oooo##...",
            "....o.#o##oo...",
            "....#.#oo##oo..",
            ".....o#ooo###o.",
            "....#ooo#o#....",
            "....o##o.......",
            "....#oooo#.....",
            "......##o#.....",
            "...............",
            "...............",
        ]
        for r, row in enumerate(rows):
            for c, ch in enumerate(row):
                if ch == "#":
                    g[r][c] = 1
                elif ch == "o":
                    g[r][c] = 2
        last = (2, 8)
        g[last[0]][last[1]] = 1
        self.assertIn("四四", _gomoku_renju_forbidden(g, last[0], last[1]))

    def test_double_open_three_on_user_position(self) -> None:
        """Regression: (10,5) is forbidden — two four-threats on vertical and diagonal."""
        g = [[0] * GOMOKU_SIZE for _ in range(GOMOKU_SIZE)]
        rows = [
            "...............",
            "...............",
            "...............",
            "....#.#........",
            ".#ooo.#........",
            ".o.o.#.o#......",
            "..oo###oooo#...",
            ".o#o.oo#..o....",
            "#..##o#.o#.....",
            "....##..#......",
            "...............",
            "...............",
            "...............",
            "...............",
            "...............",
        ]
        for r, row in enumerate(rows):
            for c, ch in enumerate(row):
                if ch == "#":
                    g[r][c] = 1
                elif ch == "o":
                    g[r][c] = 2
        last = (9, 4)  # (10, 5)
        self.assertIn("四四", _gomoku_renju_forbidden(g, last[0], last[1]))

    def test_jump_to_five_gap_not_counted_as_four(self) -> None:
        """Inner gap-fill X . X X X → five is not a 四 on that axis."""
        line = "X.XXX...."
        self.assertTrue(_gomoku_axis_has_four(line))  # extend at 5 → XXXX
        # isolated vertical — only one axis, not 四四
        g = [[0] * GOMOKU_SIZE for _ in range(GOMOKU_SIZE)]
        for r in (6, 8, 9):
            g[r][4] = 1
        last = (10, 4)
        g[last[0]][last[1]] = 1
        self.assertEqual(_gomoku_renju_forbidden(g, last[0], last[1]), [])

    def test_user_position_5_11_not_forbidden(self) -> None:
        """Regression: (5,11) is only one rush-four; split diagonal must not count."""
        g = [[0] * GOMOKU_SIZE for _ in range(GOMOKU_SIZE)]
        rows = [
            "...............",
            "......o..#.....",
            "..oo.#..o......",
            "...#.o.#o......",
            "....#..#o#.....",
            ".....#oooo##...",
            "......#o#o.#...",
            ".......o#o###...",
            ".......#ooo#o..",
            ".........#.(o)..",
            "...............",
            "...............",
            "...............",
            "...............",
            "...............",
        ]
        for r, row in enumerate(rows):
            for c, ch in enumerate(row):
                if ch == "#":
                    g[r][c] = 1
                elif ch == "o":
                    g[r][c] = 2
        g[9][11] = 2  # white last at (10, 12)
        last = (4, 10)  # (5, 11)
        self.assertEqual(_gomoku_renju_forbidden(g, last[0], last[1]), [])

        c1, c2 = object(), object()
        game = GomokuGame(c1, "zouyu")
        game.try_join(c2, "yxt")
        game.grid = [row[:] for row in g]
        game.state = "playing"
        game._turn = 1
        game._last = (9, 11)
        priv, _, _ = game.try_move(c1, "5 11")
        self.assertFalse(any("禁手" in line for line in priv))
        self.assertEqual(game.grid[last[0]][last[1]], 1)


class TestGomokuRenjuTryMove(unittest.TestCase):
    def test_rejects_black_overline(self) -> None:
        c1, c2 = object(), object()
        game = GomokuGame(c1, "Black")
        game.try_join(c2, "White")
        # Artificial: five black on row 8 without ending the game object state.
        for c in range(5):
            game.grid[7][c] = 1
        game.state = "playing"
        game._turn = 1
        priv, _, _ = game.try_move(c1, "8 6")
        self.assertTrue(any("禁手" in line for line in priv))
        self.assertEqual(game.grid[7][5], 0)

    def test_white_move_unaffected_by_forbidden(self) -> None:
        c1, c2 = object(), object()
        game = GomokuGame(c1, "Black")
        game.try_join(c2, "White")
        game.try_move(c1, "8 8")
        priv, bcast, _ = game.try_move(c2, "7 7")
        self.assertFalse(any("禁手" in line for line in priv + bcast))


if __name__ == "__main__":
    unittest.main()
