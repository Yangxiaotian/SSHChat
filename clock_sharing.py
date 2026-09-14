"""Shared chess-clock sessions for /clock.

The page ticks in the browser. This store only remembers the last
reported times so a reload can resume.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional

CLOCK_TTL_SEC = 12 * 3600
MIN_BASE_MS = 30 * 1000
MAX_BASE_MS = 180 * 60 * 1000
MAX_INC_MS = 60 * 1000
DEFAULT_BASE_MS = 10 * 60 * 1000
SIDES = ("top", "bottom")


def _token() -> str:
    return secrets.token_urlsafe(24)


def parse_time_spec(raw: str) -> Optional[tuple[int, int]]:
    """Parse '10', '10+5', '10m', '1h' into (base_ms, inc_ms)."""
    text = (raw or "").strip().lower().replace(" ", "")
    if not text:
        return None
    inc_sec = 0
    if "+" in text:
        left, right = text.split("+", 1)
        text = left
        if not right.isdigit():
            return None
        inc_sec = int(right)
    if text.endswith("h"):
        body = text[:-1]
        if not body.isdigit():
            return None
        minutes = int(body) * 60
    elif text.endswith("m"):
        body = text[:-1]
        if not body.isdigit():
            return None
        minutes = int(body)
    elif text.isdigit():
        minutes = int(text)
    else:
        return None
    if minutes <= 0 or inc_sec < 0:
        return None
    base_ms = minutes * 60 * 1000
    inc_ms = inc_sec * 1000
    if base_ms < MIN_BASE_MS or base_ms > MAX_BASE_MS or inc_ms > MAX_INC_MS:
        return None
    return base_ms, inc_ms


def format_clock(ms: int) -> str:
    ms = max(0, int(ms))
    total = ms // 1000
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


@dataclass
class ClockSession:
    session_id: str
    token: str
    creator: str
    room: Optional[str]
    created_at: float
    base_ms: int
    inc_ms: int
    top_ms: int
    bottom_ms: int
    running: Optional[str]
    running_since: float
    flagged: Optional[str]
    lang: str
    closed: bool = False

    def other(self, side: str) -> str:
        return "bottom" if side == "top" else "top"

    def remaining_ms(self, side: str, now: Optional[float] = None) -> int:
        now = time.time() if now is None else now
        stored = self.top_ms if side == "top" else self.bottom_ms
        if self.running != side or self.flagged is not None:
            return max(0, stored)
        elapsed = int((now - self.running_since) * 1000)
        if elapsed < 0:
            elapsed = 0
        return max(0, stored - elapsed)

    def settle(self, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        if self.running not in SIDES:
            return
        side = self.running
        left = self.remaining_ms(side, now)
        if side == "top":
            self.top_ms = left
        else:
            self.bottom_ms = left
        if left <= 0:
            self.flagged = side
            self.running = None
            self.running_since = 0.0
            return
        self.running_since = now

    def snapshot(self, now: Optional[float] = None) -> dict:
        now = time.time() if now is None else now
        self.settle(now)
        return {
            "top_ms": self.top_ms,
            "bottom_ms": self.bottom_ms,
            "running": self.running,
            "flagged": self.flagged,
            "base_ms": self.base_ms,
            "inc_ms": self.inc_ms,
            "paused": self.running is None and self.flagged is None,
        }

    def start(self, side: str, now: Optional[float] = None) -> str:
        now = time.time() if now is None else now
        if side not in SIDES:
            return "bad side"
        self.settle(now)
        if self.flagged:
            return "flagged"
        if self.remaining_ms(side, now) <= 0:
            self.flagged = side
            return "flagged"
        self.running = side
        self.running_since = now
        return ""

    def hit(self, side: str, now: Optional[float] = None) -> str:
        """Side finished a move: stop them, add increment, start the other side.

        If the clock is paused, tapping a side starts that side instead.
        """
        now = time.time() if now is None else now
        if side not in SIDES:
            return "bad side"
        self.settle(now)
        if self.flagged:
            return "flagged"
        if self.running is None:
            return self.start(side, now)
        if self.running != side:
            return "not your turn"
        stored = self.top_ms if side == "top" else self.bottom_ms
        if stored <= 0:
            self.flagged = side
            self.running = None
            return "flagged"
        stored += self.inc_ms
        if side == "top":
            self.top_ms = stored
        else:
            self.bottom_ms = stored
        other = self.other(side)
        if self.remaining_ms(other, now) <= 0:
            self.flagged = other
            self.running = None
            self.running_since = 0.0
            return "flagged"
        self.running = other
        self.running_since = now
        return ""

    def pause(self, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        self.settle(now)
        self.running = None
        self.running_since = 0.0

    def reset(self, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        self.top_ms = self.base_ms
        self.bottom_ms = self.base_ms
        self.running = None
        self.running_since = 0.0
        self.flagged = None

    def apply_report(
        self,
        *,
        top_ms: int,
        bottom_ms: int,
        running: Optional[str],
        flagged: Optional[str],
        now: Optional[float] = None,
    ) -> None:
        now = time.time() if now is None else now
        self.top_ms = max(0, int(top_ms))
        self.bottom_ms = max(0, int(bottom_ms))
        if flagged in SIDES:
            self.flagged = flagged
            self.running = None
            self.running_since = 0.0
            return
        self.flagged = None
        if running in SIDES:
            self.running = running
            self.running_since = now
        else:
            self.running = None
            self.running_since = 0.0

    def set_times(self, base_ms: int, inc_ms: int, now: Optional[float] = None) -> str:
        now = time.time() if now is None else now
        self.settle(now)
        if self.running is not None:
            return "running"
        if base_ms < MIN_BASE_MS or base_ms > MAX_BASE_MS or inc_ms < 0 or inc_ms > MAX_INC_MS:
            return "bad time"
        self.base_ms = base_ms
        self.inc_ms = inc_ms
        self.reset(now)
        return ""


class ClockStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.sessions: Dict[str, ClockSession] = {}
        self.token_to_id: Dict[str, str] = {}

    def create_session(
        self,
        *,
        creator: str,
        room: Optional[str],
        base_ms: int = DEFAULT_BASE_MS,
        inc_ms: int = 0,
        lang: str = "zh",
    ) -> ClockSession:
        if base_ms < MIN_BASE_MS or base_ms > MAX_BASE_MS or inc_ms < 0 or inc_ms > MAX_INC_MS:
            raise ValueError("bad time")
        session = ClockSession(
            session_id=_token(),
            token=_token(),
            creator=creator,
            room=room,
            created_at=time.time(),
            base_ms=base_ms,
            inc_ms=inc_ms,
            top_ms=base_ms,
            bottom_ms=base_ms,
            running=None,
            running_since=0.0,
            flagged=None,
            lang="en" if str(lang).lower().startswith("en") else "zh",
        )
        with self._lock:
            self.sessions[session.session_id] = session
            self.token_to_id[session.token] = session.session_id
        return session

    def get_by_token(self, token: str) -> Optional[ClockSession]:
        with self._lock:
            sid = self.token_to_id.get(token)
            if not sid:
                return None
            session = self.sessions.get(sid)
            if session is None or session.closed:
                return None
            if time.time() - session.created_at > CLOCK_TTL_SEC:
                session.closed = True
                return None
            return session

    def find_open_for_room(self, room: str) -> Optional[ClockSession]:
        key = (room or "").strip()
        if not key:
            return None
        with self._lock:
            found: Optional[ClockSession] = None
            for session in self.sessions.values():
                if session.closed or session.room != key:
                    continue
                if time.time() - session.created_at > CLOCK_TTL_SEC:
                    session.closed = True
                    continue
                if found is None or session.created_at > found.created_at:
                    found = session
            return found

    def close_session(self, session_id: str, requester: str) -> tuple[bool, str]:
        with self._lock:
            session = self.sessions.get(session_id)
            if session is None or session.closed:
                return False, "not found"
            if requester and session.creator.lower() != requester.lower():
                return False, "not creator"
            session.closed = True
            self.token_to_id.pop(session.token, None)
            return True, ""

    def cleanup_expired(self) -> None:
        now = time.time()
        with self._lock:
            dead = [
                sid
                for sid, session in self.sessions.items()
                if session.closed or now - session.created_at > CLOCK_TTL_SEC
            ]
            for sid in dead:
                session = self.sessions.pop(sid, None)
                if session is not None:
                    self.token_to_id.pop(session.token, None)


clock_store = ClockStore()
