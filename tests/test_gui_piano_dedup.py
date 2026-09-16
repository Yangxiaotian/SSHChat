#!/usr/bin/env python3
"""Piano URL token parsing and invite reopen dedup helpers for Tk client."""

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


def test_invite_url_host_changed() -> None:
    same = "https://abc.trycloudflare.com/piano/tok"
    assert not gui._invite_url_host_changed(same, same + "/")
    assert not gui._invite_url_host_changed(same, same + "#k=ABC")
    assert gui._invite_url_host_changed(
        same, "https://xyz.trycloudflare.com/piano/tok"
    )


if __name__ == "__main__":
    test_piano_token_from_url()
    test_invite_url_host_changed()
    print("✅ gui piano dedup ok")
