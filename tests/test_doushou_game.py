import unittest

from games import DoushouGame, resolve_game_name


class DoushouGameTests(unittest.TestCase):
    def _started(self):
        red = object()
        black = object()
        game = DoushouGame(red, "Red")
        priv, bcast, ended = game.try_join(black, "Black")
        self.assertEqual(priv, [])
        self.assertFalse(ended)
        self.assertTrue(any("斗兽棋开始" in line for line in bcast))
        return game, red, black

    def test_alias_and_join(self):
        self.assertEqual(resolve_game_name("斗兽棋"), "doushou")
        game, _red, _black = self._started()
        self.assertEqual(game.state, "playing")
        self.assertTrue(any("红方" in line for line in game.seats()))

    def test_basic_move_and_turn(self):
        game, red, black = self._started()
        priv, bcast, ended = game.try_move(red, "7 7 6 7")
        self.assertEqual(priv, [])
        self.assertFalse(ended)
        self.assertTrue(any("红方 Red 走 鼠" in line for line in bcast))
        self.assertTrue(any("轮到 黑方 Black" in line for line in bcast))
        priv, _bcast, _ended = game.try_move(red, "6 7 5 7")
        self.assertTrue(any("不是你的回合" in line for line in priv))
        priv, bcast, ended = game.try_move(black, "3 1 4 1")
        self.assertEqual(priv, [])
        self.assertFalse(ended)
        self.assertTrue(any("黑方 Black 走 鼠" in line for line in bcast))

    def test_piece_name_move_command(self):
        game, red, _black = self._started()
        priv, bcast, ended = game.try_move(red, "鼠 6 7")
        self.assertEqual(priv, [])
        self.assertFalse(ended)
        self.assertTrue(any("红方 Red 走 鼠" in line for line in bcast))

    def test_invalid_own_den_and_water(self):
        game, red, _black = self._started()
        # Red tiger cannot enter own den.
        game.board = [[None for _ in range(7)] for _ in range(9)]
        game.board[7][3] = {"side": "red", "kind": "tiger"}
        priv, _bcast, _ended = game.try_move(red, "8 4 9 4")
        self.assertTrue(any("自己的兽穴" in line for line in priv))
        # A dog cannot enter river.
        game.board[7][3] = None
        game.board[2][1] = {"side": "red", "kind": "dog"}
        priv, _bcast, _ended = game.try_move(red, "3 2 4 2")
        self.assertTrue(any("只有鼠可以进入河流" in line for line in priv))

    def test_rat_eats_elephant_but_elephant_cannot_eat_rat(self):
        game, red, black = self._started()
        game.board = [[None for _ in range(7)] for _ in range(9)]
        game.board[4][3] = {"side": "red", "kind": "rat"}
        game.board[3][3] = {"side": "black", "kind": "elephant"}
        game.board[0][0] = {"side": "black", "kind": "lion"}
        priv, bcast, ended = game.try_move(red, "5 4 4 4")
        self.assertEqual(priv, [])
        self.assertFalse(ended)
        self.assertTrue(any("吃掉黑方象" in line for line in bcast))
        game._turn = "black"
        game.board = [[None for _ in range(7)] for _ in range(9)]
        game.board[3][3] = {"side": "black", "kind": "elephant"}
        game.board[4][3] = {"side": "red", "kind": "rat"}
        game.board[8][6] = {"side": "red", "kind": "lion"}
        priv, _bcast, _ended = game.try_move(black, "4 4 5 4")
        self.assertTrue(any("象不能吃鼠" in line for line in priv))

    def test_trap_allows_any_piece_to_capture(self):
        game, red, _black = self._started()
        game.board = [[None for _ in range(7)] for _ in range(9)]
        game.board[7][2] = {"side": "red", "kind": "rat"}
        game.board[7][3] = {"side": "black", "kind": "elephant"}
        game.board[0][0] = {"side": "black", "kind": "lion"}
        priv, bcast, ended = game.try_move(red, "8 3 8 4")
        self.assertEqual(priv, [])
        self.assertFalse(ended)
        self.assertTrue(any("吃掉黑方象" in line for line in bcast))

    def test_lion_tiger_jump_blocked_by_rat(self):
        game, red, _black = self._started()
        game.board = [[None for _ in range(7)] for _ in range(9)]
        game.board[6][1] = {"side": "red", "kind": "tiger"}
        game.board[0][0] = {"side": "black", "kind": "lion"}
        priv, bcast, ended = game.try_move(red, "7 2 3 2")
        self.assertEqual(priv, [])
        self.assertFalse(ended)
        self.assertTrue(any("走 虎" in line for line in bcast))

        game, red, _black = self._started()
        game.board = [[None for _ in range(7)] for _ in range(9)]
        game.board[6][1] = {"side": "red", "kind": "tiger"}
        game.board[4][1] = {"side": "black", "kind": "rat"}
        priv, _bcast, _ended = game.try_move(red, "7 2 3 2")
        self.assertTrue(any("河中不能有鼠" in line for line in priv))

    def test_enter_enemy_den_wins(self):
        game, red, _black = self._started()
        game.board = [[None for _ in range(7)] for _ in range(9)]
        game.board[1][3] = {"side": "red", "kind": "cat"}
        priv, bcast, ended = game.try_move(red, "2 4 1 4")
        self.assertEqual(priv, [])
        self.assertTrue(ended)
        self.assertEqual(game.state, "ended")
        self.assertTrue(any("攻入对方兽穴获胜" in line for line in bcast))

    def test_undo_restores_capture(self):
        game, red, black = self._started()
        game.board = [[None for _ in range(7)] for _ in range(9)]
        game.board[4][3] = {"side": "red", "kind": "rat"}
        game.board[3][3] = {"side": "black", "kind": "elephant"}
        game.board[0][0] = {"side": "black", "kind": "lion"}
        game.try_move(red, "5 4 4 4")
        priv, _bcast, _ended = game.request_undo(red)
        self.assertTrue(any("已向" in line for line in priv))
        priv, bcast, ended = game.accept_undo(black)
        self.assertFalse(ended)
        self.assertTrue(any("已撤销" in line for line in bcast))
        self.assertEqual(game.board[4][3], {"side": "red", "kind": "rat"})
        self.assertEqual(game.board[3][3], {"side": "black", "kind": "elephant"})


if __name__ == "__main__":
    unittest.main()
