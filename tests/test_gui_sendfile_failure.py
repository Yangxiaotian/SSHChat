#!/usr/bin/env python3
"""GUI clients must clear pending /sendfile when the server rejects early."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import sshchat_gui as gui  # noqa: E402


def _matches(body: str) -> bool:
    return bool(gui._SENDFILE_FAIL_RE.search(body.strip()))


def test_no_other_users_in_room() -> None:
    assert _matches("房间 #default 中没有其他用户。")


def test_file_transfer_disabled() -> None:
    assert _matches("文件传输功能未启用。")


def test_invalid_room() -> None:
    assert _matches("无效的房间名。")
    assert _matches("你不在房间 #dev 中。")
    assert _matches("房间 #nosuch 不存在。")


def test_success_invite_not_failure() -> None:
    assert not _matches("gui-open upload https://x/upload/tok ABCDEF")


if __name__ == "__main__":
    test_no_other_users_in_room()
    test_file_transfer_disabled()
    test_invalid_room()
    test_success_invite_not_failure()
    print("✅ gui sendfile failure patterns ok")
