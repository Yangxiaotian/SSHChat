#!/usr/bin/env python3
"""Piano URL token parsing for Tk client dedup."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import sshchat_gui as gui  # noqa: E402


def test_piano_token_from_url() -> None:
    url = "https://example.com/piano/abc123XYZ/open/foo"
    assert gui._piano_token_from_url(url) == "abc123XYZ"
    assert gui._piano_token_from_url("https://host/piano/tok") == "tok"
    assert gui._piano_token_from_url("https://host/canvas/tok") == ""


if __name__ == "__main__":
    test_piano_token_from_url()
    print("✅ gui piano dedup ok")
