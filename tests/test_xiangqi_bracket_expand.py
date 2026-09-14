#!/usr/bin/env python3
"""Xiangqi color expansion must not rewrite drawguess / sanguo brackets."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import client  # noqa: E402


def test_drawguess_word_keeps_brackets() -> None:
    raw = "你是画家。本回合词：【相机】"
    out = client._expand_xiangqi_color(raw)
    assert "【相机】" in out
    assert "+相" not in out
    assert "+相机" not in out


def test_single_xiangqi_piece_bracket_still_converts() -> None:
    # Legacy SSH-safe markers for one piece cell.
    out = client._expand_xiangqi_color("棋盘 【相】 与 〔象〕")
    assert "+相" in out
    assert "-象" in out
    assert "【相】" not in out


def test_sanguo_card_brackets_untouched() -> None:
    raw = "alice 使用【杀】；bob 打出【闪】"
    out = client._expand_xiangqi_color(raw)
    assert "【杀】" in out
    assert "【闪】" in out
    assert "+杀" not in out


if __name__ == "__main__":
    test_drawguess_word_keeps_brackets()
    test_single_xiangqi_piece_bracket_still_converts()
    test_sanguo_card_brackets_untouched()
    print("✅ xiangqi bracket expand ok")
