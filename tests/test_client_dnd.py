import unittest

import client as client_mod


class ClientDndTest(unittest.TestCase):
    def setUp(self) -> None:
        self._prev_dnd = client_mod._DND_ENABLED
        self._prev_bypass_until = client_mod._GAME_BYPASS_UNTIL
        self._prev_persist_dnd = client_mod._persist_dnd
        client_mod._persist_dnd = lambda _enabled: None
        client_mod._DND_ENABLED = True

    def tearDown(self) -> None:
        client_mod._DND_ENABLED = self._prev_dnd
        client_mod._GAME_BYPASS_UNTIL = self._prev_bypass_until
        client_mod._persist_dnd = self._prev_persist_dnd

    def test_reenabling_dnd_clears_game_bypass(self) -> None:
        client_mod._DND_ENABLED = False
        client_mod._note_game_command()
        client_mod._set_dnd(True)
        self.assertEqual(client_mod._dnd_system_action("底池=120，当前注=10", "alice"), "")

    def test_game_move_does_not_bypass_while_dnd_on(self) -> None:
        client_mod._GAME_BYPASS_UNTIL = 0.0
        client_mod._prepare_outgoing("/game move 8 8")
        self.assertFalse(client_mod._game_bypass_active())
        self.assertEqual(
            client_mod._dnd_system_action("gomoku 对局（playing）  黑：alice   白：bob", "alice"),
            "",
        )

    def test_game_show_bypasses_while_dnd_on(self) -> None:
        client_mod._GAME_BYPASS_UNTIL = 0.0
        client_mod._prepare_outgoing("/game show")
        self.assertTrue(client_mod._game_bypass_active())

    def test_show_peek_clears_when_opponent_turn_line_arrives(self) -> None:
        """Black peeks with /game show while red to move; show ends with opponent turn."""
        client_mod._GAME_BYPASS_UNTIL = 0.0
        client_mod._prepare_outgoing("/game show")
        self.assertTrue(client_mod._game_bypass_active())
        # Oriented board body still visible during peek.
        self.assertIsNone(
            client_mod._dnd_system_action(
                "xiangqi 对局（playing）  红：alice   黑：bob", "bob"
            )
        )
        # Trailing「轮到 红方 …」must end the peek so waiting is quiet again.
        self.assertEqual(
            client_mod._dnd_system_action("轮到 红方 alice 走子", "bob"),
            "",
        )
        self.assertFalse(client_mod._game_bypass_active())
        self.assertEqual(
            client_mod._dnd_system_action("楚河汉界", "bob"),
            "",
        )

    def test_my_turn_reopens_board_after_opponent_move(self) -> None:
        """After peek bypass is gone, my-turn must reopen so oriented board displays."""
        client_mod._GAME_BYPASS_UNTIL = 0.0
        self.assertEqual(
            client_mod._dnd_system_action("轮到 黑方 bob 走子", "bob"),
            "turn_hint",
        )
        self.assertTrue(client_mod._game_bypass_active())
        self.assertIsNone(
            client_mod._dnd_system_action(
                "xiangqi 对局（playing）  红：alice   黑：bob", "bob"
            )
        )
        self.assertIsNone(
            client_mod._dnd_system_action("  （己方在下方）", "bob")
        )

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

    def test_game_error_not_suppressed_in_dnd(self) -> None:
        self.assertIsNone(client_mod._dnd_system_action("不是你的回合。", "alice"))
        self.assertIsNone(client_mod._dnd_system_action("无法识别走法 'x9'。", "alice"))

    def test_game_join_and_new_feedback_not_suppressed(self) -> None:
        lines = (
            "本房已有进行中的对局（gomoku/playing）；/game end 由房主结束或先等当前局结束。",
            "白方席位已被 bob 占。",
            "你已经是黑方。",
            "本房没有进行中的对局；用 /game new chess 开局。",
            "alice 加入为白方，对局开始！",
            "bob 开了一局 gomoku（黑方），等另一位玩家用 /game join 加入。",
        )
        for line in lines:
            self.assertIsNone(client_mod._dnd_system_action(line, "alice"), msg=line)

    def test_dnd_game_session_command_detection(self) -> None:
        self.assertTrue(client_mod._is_dnd_game_session_command("/game join"))
        self.assertTrue(client_mod._is_dnd_game_session_command("/game new gomoku"))
        self.assertFalse(client_mod._is_dnd_game_session_command("/game move 8 8"))

    def test_dnd_game_action_command_detection(self) -> None:
        self.assertTrue(client_mod._is_dnd_game_action_command("/game move 8 8"))
        self.assertTrue(client_mod._is_dnd_game_action_command("/game fold"))
        self.assertFalse(client_mod._is_dnd_game_action_command("/game show"))

    def test_clear_command_is_local(self) -> None:
        calls: list[str] = []

        def _fake_clear() -> None:
            calls.append("clear")

        original = client_mod._terminal_hard_clear
        client_mod._terminal_hard_clear = _fake_clear
        try:
            self.assertFalse(client_mod._prepare_outgoing("/cls"))
            self.assertFalse(client_mod._prepare_outgoing("/clear"))
        finally:
            client_mod._terminal_hard_clear = original
        self.assertEqual(calls, ["clear", "clear"])

    def test_write_real_clear_csi_uses_underlying_stdout(self) -> None:
        import io

        raw = io.BytesIO()
        real = io.TextIOWrapper(raw, encoding="ascii", newline="\n")
        real.isatty = lambda: True  # type: ignore[attr-defined]
        original = client_mod._get_real_stdout
        client_mod._get_real_stdout = lambda: real
        try:
            client_mod._write_real_clear_csi()
        finally:
            client_mod._get_real_stdout = original
        self.assertEqual(raw.getvalue(), client_mod._CLEAR_CSI)

    def test_dnd_subcommand_completion(self) -> None:
        from prompt_toolkit.document import Document

        comp = client_mod.SSHChatCommandCompleter()
        after_cmd = list(comp.get_completions(Document("/dnd "), None))
        self.assertTrue(any(c.text.startswith("on") for c in after_cmd))
        self.assertTrue(any(c.text.startswith("off") for c in after_cmd))
        partial = list(comp.get_completions(Document("/dnd o"), None))
        self.assertTrue(any(c.text.startswith("on") for c in partial))
        self.assertTrue(any(c.text.startswith("off") for c in partial))


if __name__ == "__main__":
    unittest.main()
