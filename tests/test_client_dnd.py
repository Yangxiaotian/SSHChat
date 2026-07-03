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


if __name__ == "__main__":
    unittest.main()
