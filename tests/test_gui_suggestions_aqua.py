"""Regression tests for Tk suggestion UI / Aqua click handling."""

from __future__ import annotations

import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path

from sshchat_gui import SSHChatGUI, _StatusTip, _command_completions


class GuiSuggestionTests(unittest.TestCase):
    def setUp(self) -> None:
        cfg = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        cfg.write(b"{}")
        cfg.close()
        self._cfg = Path(cfg.name)
        self.gui = SSHChatGUI(self._cfg, force_full_ui=True)
        self.gui.root.geometry("+5000+5000")
        try:
            self.gui.root.deiconify()
        except tk.TclError:
            pass
        self.gui.root.update()

    def tearDown(self) -> None:
        try:
            self.gui.root.destroy()
        except Exception:
            pass
        self._cfg.unlink(missing_ok=True)

    def test_slash_shows_persistent_suggestions(self) -> None:
        self.gui.var_input.set("/")
        self.gui.root.update()
        self.gui._flush_suggestion_ui()
        self.gui.root.update()
        self.assertIsNotNone(self.gui._suggest_win)
        self.assertGreater(self.gui._suggest_list.size(), 0)
        lst_id = id(self.gui._suggest_list)
        self.gui._hide_suggestions()
        self.gui._flush_suggestion_ui()
        self.gui.root.update()
        self.assertIsNone(self.gui._suggest_win)
        self.assertEqual(id(self.gui._suggest_list), lst_id)

    def test_toolbar_button_not_treated_as_suggestion_child(self) -> None:
        self.gui._show_suggestions(_command_completions("/")[:4])
        self.gui._flush_suggestion_ui()
        self.gui.root.update()
        self.assertFalse(self.gui._widget_in_suggestions(self.gui.btn_send))
        self.assertTrue(self.gui._widget_in_suggestions(self.gui._suggest_list))

    def test_status_tip_does_not_create_toplevel(self) -> None:
        before = set(self.gui.root.winfo_children())
        tip = _StatusTip(self.gui.btn_send, "发送", self.gui.var_status_tip)
        tip._show()
        self.gui.root.update()
        self.assertEqual(self.gui.var_status_tip.get(), "发送")
        self.assertEqual(set(self.gui.root.winfo_children()), before)

    def test_dismiss_runs_after_button_release(self) -> None:
        self.gui._show_suggestions(["/help", "/lang"])
        self.gui._flush_suggestion_ui()
        self.gui.root.update()

        class _E:
            pass

        ev = _E()
        ev.widget = self.gui.btn_send
        self.gui._on_any_button_release(ev)
        self.assertIsNotNone(self.gui._suggest_win)
        self.assertIsNotNone(self.gui._suggest_dismiss_job)
        for _ in range(15):
            self.gui.root.update()
            time.sleep(0.01)
        self.gui._flush_suggestion_ui()
        self.gui.root.update()
        self.assertIsNone(self.gui._suggest_win)


if __name__ == "__main__":
    unittest.main()
