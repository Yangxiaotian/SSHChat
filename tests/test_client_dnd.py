import unittest

import client as client_mod


class ClientDndTest(unittest.TestCase):
    def setUp(self) -> None:
        self._prev_dnd = client_mod._DND_ENABLED
        client_mod._DND_ENABLED = True

    def tearDown(self) -> None:
        client_mod._DND_ENABLED = self._prev_dnd

    def test_reading_content_not_suppressed(self) -> None:
        self.assertTrue(client_mod._is_reading_content_line("--- 中文 ---"))
        self.assertTrue(client_mod._is_reading_content_line("1. [BBC] Example headline"))
        self.assertTrue(client_mod._is_reading_content_line("《三体》"))
        self.assertIsNone(client_mod._dnd_system_action("--- 中文 ---", "alice"))

    def test_game_board_suppressed(self) -> None:
        self.assertTrue(client_mod._is_game_flood_line("底池=120，当前注=10"))
        self.assertEqual(client_mod._dnd_system_action("底池=120，当前注=10", "alice"), "")

    def test_gomoku_show_header_suppressed(self) -> None:
        header = "gomoku 对局（playing）  黑：alice   白：bob"
        rating = "积分体系：五子棋 Elo；积分跨房间共享。"
        col_hdr = "    1  2  3  4  5  6  7  8  9 10 11 12 13 14 15 "
        self.assertEqual(client_mod._dnd_system_action(header, "alice"), "")
        self.assertEqual(client_mod._dnd_system_action(rating, "alice"), "")
        self.assertEqual(client_mod._dnd_system_action(col_hdr, "alice"), "")

    def test_chess_turn_with_move_number(self) -> None:
        line = "轮到 白方 alice（第 5 手）（将军）"
        self.assertTrue(client_mod._is_my_turn_line(line, "alice"))
        self.assertEqual(client_mod._dnd_system_action(line, "alice"), "turn_hint")

    def test_my_turn_shows_hint(self) -> None:
        self.assertTrue(client_mod._is_my_turn_line("轮到 黑方 alice 落子", "alice"))
        self.assertEqual(
            client_mod._dnd_system_action("轮到 黑方 alice 落子", "alice"),
            "turn_hint",
        )

    def test_other_turn_suppressed(self) -> None:
        self.assertFalse(client_mod._is_my_turn_line("轮到 白方 bob 落子", "alice"))
        self.assertEqual(client_mod._dnd_system_action("轮到 白方 bob 落子", "alice"), "")

    def test_holdem_turn_colon(self) -> None:
        self.assertTrue(client_mod._is_my_turn_line("轮到：alice", "alice"))
        self.assertEqual(client_mod._dnd_system_action("轮到：alice", "alice"), "turn_hint")

    def test_xiangqi_show_lines_suppressed(self) -> None:
        header = "xiangqi 对局（playing）  红：alice   黑：bob"
        legend = "图例：+红  -黑  !上一步  ·空  （请用等宽字体）"
        col_hdr = "   九  八  七  六  五  四  三  二  一  ← 红方纵线 九…一（右为一）"
        board_row = "+車-+馬+象-士-将-士-象-馬-車"
        river = "楚河汉界"
        move = "黑方 bob 走 马2进3"
        turn = "轮到 红方 alice 走子（被将军）"
        for line in (header, legend, col_hdr, board_row, river, move):
            self.assertEqual(client_mod._dnd_system_action(line, "alice"), "", msg=line)
        self.assertEqual(client_mod._dnd_system_action(turn, "alice"), "turn_hint")

    def test_chess_show_lines_suppressed(self) -> None:
        header = "chess 对局（playing）  白：alice   黑：bob"
        files = "   a  b  c  d  e  f  g  h"
        board = " 8 (♜)(♞)(♝)(♛)(♚)(♝)(♞)(♜)"
        move = "黑方 bob 走 e5"
        turn = "轮到 白方 alice（第 2 手）"
        for line in (header, files, board, move):
            self.assertEqual(client_mod._dnd_system_action(line, "alice"), "", msg=line)
        self.assertEqual(client_mod._dnd_system_action(turn, "alice"), "turn_hint")

    def test_go_show_lines_suppressed(self) -> None:
        header = "go 对局（playing）  黑：alice   白：bob"
        komi = "贴目：白 6.5；提子：黑 0，白 0"
        legend = "  图例：# 黑棋  o 白棋  . 空点；连续两次停一手自动数子。"
        move = "黑方 bob 落子 (4, 4)"
        turn = "轮到 白方 alice 落子"
        for line in (header, komi, legend, move):
            self.assertEqual(client_mod._dnd_system_action(line, "alice"), "", msg=line)
        self.assertEqual(client_mod._dnd_system_action(turn, "alice"), "turn_hint")

    def test_doushou_turn_without_fang(self) -> None:
        turn = "轮到 红 alice 行棋"
        self.assertTrue(client_mod._is_my_turn_line(turn, "alice"))
        self.assertEqual(client_mod._dnd_system_action(turn, "alice"), "turn_hint")


if __name__ == "__main__":
    unittest.main()
