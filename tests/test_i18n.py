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
