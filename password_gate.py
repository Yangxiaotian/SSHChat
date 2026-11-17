"""Shared command password check with iPhone-style progressive lockout.

After consecutive failures: 6→1m, 7→5m, 8→15m, 9→60m, 10+→6h (per user+scope).
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from pathlib import Path

_LOCKOUT_SECONDS = {6: 60, 7: 300, 8: 900, 9: 3600}
_LOCKOUT_MAX_SECONDS = 6 * 3600
_lock = threading.Lock()
# (scope, user) -> {fails, locked_until}
_state: dict[tuple[str, str], dict[str, float | int]] = {}


def _lockout_seconds_for_fails(fails: int) -> int:
    if fails >= 10:
        return _LOCKOUT_MAX_SECONDS
    return int(_LOCKOUT_SECONDS.get(fails, 0))


def load_password_from_config(
    *,
    env_key: str,
    path: Path,
    json_key: str = "password",
) -> str:
    env = (os.environ.get(env_key) or "").strip()
    if env:
        return env
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get(json_key) or "").strip()


def check_password(
    *,
    scope: str,
    user: str,
    password: str,
    expected: str,
) -> tuple[str, dict]:
    """Returns (code, kwargs): ok | unset | locked(seconds=) | bad(fails=, lock_after=)."""
    if not expected:
        return "unset", {}
    key = ((scope or "?").strip().lower(), (user or "").strip().lower() or "?")
    now = time.time()
    with _lock:
        st = _state.setdefault(key, {"fails": 0, "locked_until": 0.0})
        locked_until = float(st.get("locked_until") or 0.0)
        if locked_until > now:
            return "locked", {"seconds": max(1, int(locked_until - now))}
        got = password or ""
        if len(got) == len(expected) and secrets.compare_digest(got, expected):
            st["fails"] = 0
            st["locked_until"] = 0.0
            return "ok", {}
        fails = int(st.get("fails") or 0) + 1
        st["fails"] = fails
        delay = _lockout_seconds_for_fails(fails)
        if delay:
            st["locked_until"] = now + delay
            return "locked", {"seconds": delay}
        return "bad", {"fails": fails, "lock_after": 6}


def reset_attempts(*, scope: str = "", user: str = "") -> None:
    """Test helper."""
    with _lock:
        if not scope and not user:
            _state.clear()
            return
        scope_l = (scope or "").strip().lower()
        user_l = (user or "").strip().lower()
        for k in list(_state):
            if scope_l and k[0] != scope_l:
                continue
            if user_l and k[1] != user_l:
                continue
            _state.pop(k, None)
