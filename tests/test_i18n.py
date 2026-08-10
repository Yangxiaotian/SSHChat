"""Tests for SSHChat bilingual i18n helpers."""

from __future__ import annotations

import os
import tempfile
import unittest

import i18n
from locale_store import LocaleStore


class I18nTests(unittest.TestCase):
    def test_default_locale_is_english(self) -> None:
        self.assertEqual(i18n.default_locale(), "en")

    def test_help_lines_bilingual(self) -> None:
        en = i18n.help_lines("en")
        zh = i18n.help_lines("zh")
        self.assertTrue(any("/lang" in line for line in en))
        self.assertTrue(any("/lang" in line for line in zh))
        self.assertIn("command help", "".join(en).lower())
        self.assertIn("命令", "".join(zh))

    def test_game_help_lines(self) -> None:
        en = i18n.game_help_lines("en")
        self.assertTrue(any("holdem" in line.lower() for line in en))
        self.assertTrue(any("sanguo" in line.lower() for line in en))

    def test_localize_game_line(self) -> None:
        self.assertEqual(
            i18n.localize_game_line("不是你的回合。", "en"),
            "Not your turn.",
        )
        self.assertEqual(
            i18n.localize_game_line("不是你的回合。", "zh"),
            "不是你的回合。",
        )

    def test_gomoku_rating_and_white_labels(self) -> None:
        self.assertEqual(
            i18n.localize_game_line(
                "#1 alice: 积分=1200 等级=炼气初期 战绩=1/2/0",
                "en",
            ),
            "#1 alice: rating=1200 level=Qi Refining (Early) W/L/D=1/2/0",
        )
        self.assertEqual(
            i18n.localize_game_line(
                "gomoku 对局（playing）  黑：alice   白：bob",
                "en",
            ),
            "gomoku (playing)  Black: alice   White: bob",
        )
        self.assertEqual(
            i18n.localize_game_line("轮到 白方 bob 落子", "en"),
            "White bob to move",
        )
        self.assertEqual(
            i18n.localize_game_line("  白方：bob", "en"),
            "  White: bob",
        )

    def test_other_games_common_labels(self) -> None:
        self.assertEqual(
            i18n.localize_game_line("轮到 黑方 Bob（第 12 手）", "en"),
            "Black Bob to move (move 12)",
        )
        self.assertEqual(
            i18n.localize_game_line(
                "黑方：64.5（含空 58）  白方：71（含贴目 6.5，空 64）",
                "en",
            ),
            "Black: 64.5 (territory 58)  White: 71 (komi 6.5, territory 64)",
        )
        self.assertEqual(
            i18n.localize_game_line("请等待 Bob", "en"),
            "Please wait for Bob",
        )
        self.assertEqual(
            i18n.localize_game_line("待响应弃牌：m1 -> Alice", "en"),
            "Pending discard: m1 -> Alice",
        )
        self.assertEqual(
            i18n.localize_game_line(
                "  上一步：(8, 6)  （行 列，1 起算，左上为 1,1）",
                "en",
            ),
            "  Last move: (8, 6)  (row col, 1-based, top-left is 1,1)",
        )

    def test_env_default_override(self) -> None:
        old = os.environ.get("SSHCHAT_DEFAULT_LOCALE")
        try:
            os.environ["SSHCHAT_DEFAULT_LOCALE"] = "zh"
            self.assertEqual(i18n.default_locale(), "zh")
            os.environ["SSHCHAT_DEFAULT_LOCALE"] = "en"
            self.assertEqual(i18n.default_locale(), "en")
        finally:
            if old is None:
                os.environ.pop("SSHCHAT_DEFAULT_LOCALE", None)
            else:
                os.environ["SSHCHAT_DEFAULT_LOCALE"] = old

    def test_locale_store_persist(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "user_locales.json")
            store = LocaleStore(path)
            self.assertEqual(store.get("Alice"), i18n.default_locale())
            store.set("Alice", "zh")
            store2 = LocaleStore(path)
            self.assertEqual(store2.get("alice"), "zh")


if __name__ == "__main__":
    unittest.main()
