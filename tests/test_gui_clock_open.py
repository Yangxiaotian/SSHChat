#!/usr/bin/env python3
"""Tk chess-clock open must not block on webbrowser.open."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import sshchat_gui as gui  # noqa: E402


def test_gui_open_clock_re() -> None:
    url = "https://abc.trycloudflare.com/clock/tok123"
    m = gui._GUI_OPEN_CLOCK_RE.match(f"gui-open clock {url}")
    assert m is not None
    assert m.group(1) == url
    assert gui._GUI_OPEN_CLOCK_RE.match(f"gui-open clock {url} EXTRA") is None


def test_open_browser_tab_does_not_call_webbrowser() -> None:
    calls: list[list[str]] = []

    def fake_popen(args, **_kwargs):
        calls.append(list(args))

        class P:
            pass

        return P()

    with mock.patch.object(gui.subprocess, "Popen", side_effect=fake_popen):
        with mock.patch.object(
            gui, "_chromium_app_binaries", return_value=["/usr/bin/google-chrome"]
        ):
            with mock.patch.object(gui.os.path, "isfile", return_value=True):
                assert gui._open_browser_tab("https://example.com/clock/x")
    assert calls and calls[0][0] == "/usr/bin/google-chrome"
    assert "https://example.com/clock/x" in calls[0]


def test_open_url_fallback_uses_xdg_on_linux() -> None:
    if sys.platform != "linux":
        return
    calls: list[list[str]] = []

    def fake_popen(args, **_kwargs):
        calls.append(list(args))

        class P:
            pass

        return P()

    with mock.patch.object(gui.subprocess, "Popen", side_effect=fake_popen):
        with mock.patch.object(gui.shutil, "which", return_value="/usr/bin/xdg-open"):
            assert gui._open_url_fallback("https://example.com/clock/x")
    assert calls == [["/usr/bin/xdg-open", "https://example.com/clock/x"]]


if __name__ == "__main__":
    test_gui_open_clock_re()
    test_open_browser_tab_does_not_call_webbrowser()
    test_open_url_fallback_uses_xdg_on_linux()
    print("✅ gui clock open ok")
