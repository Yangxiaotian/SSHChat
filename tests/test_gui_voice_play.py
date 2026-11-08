#!/usr/bin/env python3
"""Tk voice auto-play helpers (align with mobile MediaMime / autoOpen)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import sshchat_gui as gui  # noqa: E402


def test_is_audio_media() -> None:
    assert gui._is_audio_media("voice-1.m4a", "audio/mp4")
    assert gui._is_audio_media("note.mp3", "")
    assert gui._is_audio_media("voice-9.webm", "video/webm")
    assert gui._is_audio_media("voice-9.webm", "")
    assert not gui._is_audio_media("clip.webm", "video/webm")
    assert not gui._is_audio_media("photo.png", "image/png")


def test_guess_media_mime() -> None:
    assert gui._guess_media_mime("a.m4a", "audio/x-m4a") == "audio/mp4"
    assert gui._guess_media_mime("voice-1.webm", "video/webm") == "audio/webm"


if __name__ == "__main__":
    test_is_audio_media()
    test_guess_media_mime()
    print("✅ gui voice play helpers ok")
