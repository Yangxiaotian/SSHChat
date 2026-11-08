#!/usr/bin/env python3
"""Tk /guest completions + auto-copy guest URL (mirrors mobile SecureInvite)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import sshchat_gui as gui  # noqa: E402
from sshchat_gui import (
    _command_completions,
    _is_secure_invite_noise,
    _parse_guest_url,
)


class TestGuestCompletions(unittest.TestCase):
    def test_top_level_guest_and_tmplink(self) -> None:
        tops = _command_completions("/")
        self.assertIn("/guest", tops)
        self.assertIn("/tmplink", tops)
        self.assertEqual(_command_completions("/gue"), ["/guest"])

    def test_guest_minute_suggestions(self) -> None:
        items = _command_completions("/guest ")
        self.assertEqual(
            items,
            [
                "/guest help",
                "/guest close",
                "/guest 5",
                "/guest 10",
                "/guest 15",
                "/guest 30",
                "/guest 60",
            ],
        )
        self.assertEqual(
            _command_completions("/guest 1"),
            ["/guest 10", "/guest 15"],
        )


class TestGuestUrlParseAndNoise(unittest.TestCase):
    def test_parse_guest_url(self) -> None:
        url = "https://abc.trycloudflare.com/guest/Tok_en-1"
        self.assertEqual(_parse_guest_url(url), url)
        self.assertEqual(_parse_guest_url(url + "/"), url)
        self.assertIsNone(_parse_guest_url(f"{url}/join"))
        self.assertIsNone(
            _parse_guest_url("https://abc.trycloudflare.com/clock/Tok_en-1")
        )

    def test_guest_url_not_invite_noise(self) -> None:
        guest = "https://abc.trycloudflare.com/guest/Tok_en-1"
        self.assertFalse(_is_secure_invite_noise(guest))
        self.assertTrue(
            _is_secure_invite_noise("https://abc.trycloudflare.com/download/Tok_en-1")
        )
        self.assertTrue(
            _is_secure_invite_noise("https://abc.trycloudflare.com/clock/Tok_en-1")
        )


class TestGuestClipboardCopy(unittest.TestCase):
    def setUp(self) -> None:
        cfg = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        cfg.write(b"{}")
        cfg.close()
        self._cfg = Path(cfg.name)
        self.gui = gui.SSHChatGUI(self._cfg, force_full_ui=True)
        self.gui.root.geometry("+5000+5000")
        try:
            self.gui.root.deiconify()
        except Exception:
            pass
        self.gui.root.update()

    def tearDown(self) -> None:
        try:
            self.gui.root.destroy()
        except Exception:
            pass
        self._cfg.unlink(missing_ok=True)

    def test_append_guest_url_copies_clipboard(self) -> None:
        url = "https://abc.trycloudflare.com/guest/Tok_en-1"
        copied: list[str] = []

        def fake_copy(text: str) -> bool:
            copied.append(text)
            return True

        with mock.patch.object(self.gui, "_copy_text_to_clipboard", side_effect=fake_copy):
            self.gui._append_chat_line(f"[*] {url}")
        self.assertEqual(copied, [url])
        self.assertEqual(self.gui.var_status.get(), "临时链接已复制，可粘贴发给对方")
        # URL must remain visible in history (not collapsed as invite noise).
        hist = self.gui._room_history.get(self.gui._active_room, [])
        joined = "".join(
            (e[0] if isinstance(e, tuple) else str(e)) for e in hist
        )
        self.assertIn(url, joined)


if __name__ == "__main__":
    unittest.main()
