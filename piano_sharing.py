"""
Shared room piano — URL + separate key (same security shape as /canvas).

Flow:
1. Chat creates a session; each participant gets /piano/<token> + 6-char key.
2. Opening the page and posting the key mints a short-lived access ticket.
3. Note events sync via ticket-gated HTTP poll/push.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PIANO_TTL_SECONDS = int(os.environ.get("SSHCHAT_PIANO_TTL_SECONDS", str(4 * 3600)))
ACCESS_TICKET_TTL_SECONDS = int(
    os.environ.get("SSHCHAT_PIANO_TICKET_TTL_SECONDS", "1800")
)
MAX_EVENTS = int(os.environ.get("SSHCHAT_PIANO_MAX_EVENTS", "2000"))


def _generate_token() -> str:
    return secrets.token_urlsafe(32)


def _generate_key() -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return "".join(secrets.choice(alphabet) for _ in range(6))


@dataclass
class PianoAccessTicket:
    ticket: str
    session_id: str
    participant: str
    expires: float


@dataclass
class PianoNoteEvent:
    seq: int
    note: str
    action: str  # "on" | "off"
    author: str
    ts: float


@dataclass
class PianoSession:
    session_id: str
    creator: str
    room: Optional[str]
    tokens: Dict[str, str]
    keys: Dict[str, str]
    events: List[PianoNoteEvent] = field(default_factory=list)
    rev: int = 0
    next_seq: int = 1
    created_at: float = 0.0
    expires: float = 0.0
    closed: bool = False
    title: str = ""


class PianoStore:
    """In-memory (+ optional disk) store for shared piano sessions."""

    def __init__(self, store_path: str = "piano_sessions.json"):
        self.store_path = store_path
        self.sessions: Dict[str, PianoSession] = {}
        self.token_to_session: Dict[str, str] = {}
        self.tickets: Dict[str, PianoAccessTicket] = {}
        self.lock = threading.RLock()
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.store_path):
            return
        try:
            with open(self.store_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            now = time.time()
            for sid, raw in data.get("sessions", {}).items():
                events = [
                    PianoNoteEvent(**e) for e in (raw.get("events") or [])
                ]
                room = raw.get("room") or None
                expires = float(raw.get("expires") or 0)
                if room:
                    expires = 0.0
                self.sessions[sid] = PianoSession(
                    session_id=raw["session_id"],
                    creator=raw["creator"],
                    room=room,
                    tokens=dict(raw.get("tokens") or {}),
                    keys=dict(raw.get("keys") or {}),
                    events=events[-MAX_EVENTS:],
                    rev=int(raw.get("rev") or 0),
                    next_seq=int(raw.get("next_seq") or 1),
                    created_at=float(raw.get("created_at") or 0),
                    expires=expires,
                    closed=bool(raw.get("closed")),
                    title=str(raw.get("title") or ""),
                )
                if not self.sessions[sid].closed:
                    for token in self.sessions[sid].tokens.values():
                        self.token_to_session[token] = sid
            for ticket, raw in data.get("tickets", {}).items():
                try:
                    entry = PianoAccessTicket(**raw)
                except (TypeError, ValueError):
                    continue
                if entry.expires <= now or entry.session_id not in self.sessions:
                    continue
                self.tickets[ticket] = entry
        except Exception as e:
            print(f"[Piano] Failed to load: {e}")

    def _save(self) -> None:
        try:
            data = {
                "sessions": {
                    sid: {
                        **asdict(session),
                        "events": [asdict(e) for e in session.events[-MAX_EVENTS:]],
                    }
                    for sid, session in self.sessions.items()
                },
                "tickets": {
                    ticket: asdict(entry) for ticket, entry in self.tickets.items()
                },
            }
            path = Path(self.store_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = f"{self.store_path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
            os.replace(tmp, self.store_path)
        except Exception as e:
            print(f"[Piano] Failed to save: {e}")

    def create_session(
        self,
        creator: str,
        participants: List[str],
        room: Optional[str] = None,
        title: str = "",
        ttl_seconds: int = PIANO_TTL_SECONDS,
    ) -> PianoSession:
        now = time.time()
        names: List[str] = []
        seen = set()
        for name in [creator, *participants]:
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            names.append(name)

        session_id = _generate_token()
        tokens: Dict[str, str] = {}
        keys: Dict[str, str] = {}
        for name in names:
            tokens[name] = _generate_token()
            keys[name] = _generate_key()

        expires = 0.0 if room else now + max(60, int(ttl_seconds))
        session = PianoSession(
            session_id=session_id,
            creator=creator,
            room=room,
            tokens=tokens,
            keys=keys,
            events=[],
            rev=0,
            next_seq=1,
            created_at=now,
            expires=expires,
            closed=False,
            title=(title or "").strip()[:80],
        )
        with self.lock:
            self.sessions[session_id] = session
            for token in tokens.values():
                self.token_to_session[token] = session_id
            self._save()
        return session

    def add_participant(
        self, session_id: str, name: str
    ) -> Tuple[Optional[str], Optional[str], str]:
        name = (name or "").strip()
        if not name:
            return None, None, "无效昵称"
        with self.lock:
            session = self.sessions.get(session_id)
            if session is None:
                return None, None, "钢琴不存在"
            ok, err = self._alive(session)
            if not ok:
                return None, None, err
            for existing, tok in session.tokens.items():
                if existing.lower() == name.lower():
                    return tok, session.keys.get(existing, ""), ""
            token = _generate_token()
            key = _generate_key()
            session.tokens[name] = token
            session.keys[name] = key
            self.token_to_session[token] = session_id
            self._save()
            return token, key, ""

    def get_by_token(self, token: str) -> Optional[PianoSession]:
        with self.lock:
            sid = self.token_to_session.get(token)
            if not sid:
                return None
            session = self.sessions.get(sid)
            if session is None or session.closed:
                return None
            return session

    def participant_for_token(self, session: PianoSession, token: str) -> Optional[str]:
        for name, t in session.tokens.items():
            if t == token:
                return name
        return None

    def _alive(self, session: PianoSession) -> Tuple[bool, str]:
        if session.closed:
            return False, "钢琴已关闭"
        if session.room:
            return True, ""
        if session.expires > 0 and time.time() > session.expires:
            return False, "钢琴已过期"
        return True, ""

    def issue_access_ticket(
        self, token: str, key: str
    ) -> Tuple[Optional[PianoSession], Optional[str], Optional[str], str]:
        key = (key or "").strip().upper()
        if len(key) != 6:
            return None, None, None, "请输入6位密钥"
        with self.lock:
            session = self.get_by_token(token)
            if session is None:
                return None, None, None, "钢琴链接无效"
            ok, err = self._alive(session)
            if not ok:
                return None, None, None, err
            participant = self.participant_for_token(session, token)
            if participant is None:
                return None, None, None, "钢琴链接无效"
            expected = session.keys.get(participant, "")
            if not expected or key != expected.upper():
                return None, None, None, "密钥错误"
            stale = [
                t
                for t, entry in self.tickets.items()
                if entry.session_id == session.session_id
                and entry.participant.lower() == participant.lower()
            ]
            stale.sort(
                key=lambda t: self.tickets[t].expires if t in self.tickets else 0.0
            )
            for t in stale[:-4]:
                self.tickets.pop(t, None)
            ticket = _generate_token()
            self.tickets[ticket] = PianoAccessTicket(
                ticket=ticket,
                session_id=session.session_id,
                participant=participant,
                expires=time.time() + ACCESS_TICKET_TTL_SECONDS,
            )
            self._save()
            return session, participant, ticket, ""

    def resolve_ticket(
        self, token: str, ticket: str
    ) -> Tuple[Optional[PianoSession], Optional[str], str]:
        ticket = (ticket or "").strip()
        if not ticket:
            return None, None, "缺少访问凭据"
        with self.lock:
            session = self.get_by_token(token)
            if session is None:
                return None, None, "钢琴链接无效"
            ok, err = self._alive(session)
            if not ok:
                return None, None, err
            entry = self.tickets.get(ticket)
            if entry is None:
                return None, None, "访问凭据无效，请重新输入密钥"
            if entry.session_id != session.session_id:
                return None, None, "访问凭据无效，请重新输入密钥"
            if time.time() > entry.expires:
                self.tickets.pop(ticket, None)
                self._save()
                return None, None, "访问凭据已过期，请重新输入密钥"
            participant = self.participant_for_token(session, token)
            if (
                participant is None
                or participant.lower() != entry.participant.lower()
            ):
                return None, None, "访问凭据与链接不匹配"
            entry.expires = time.time() + ACCESS_TICKET_TTL_SECONDS
            return session, participant, ""

    def push_note(
        self,
        token: str,
        ticket: str,
        *,
        note: str,
        action: str,
    ) -> Tuple[Optional[dict], str]:
        note = (note or "").strip()
        action = (action or "on").strip().lower()
        if action not in ("on", "off"):
            return None, "无效动作"
        # Accept C4, C#4, etc.
        if not note or len(note) > 8:
            return None, "无效音符"
        session, participant, err = self.resolve_ticket(token, ticket)
        if session is None or participant is None:
            return None, err
        with self.lock:
            session = self.get_by_token(token)
            if session is None:
                return None, "钢琴链接无效"
            ok, alive_err = self._alive(session)
            if not ok:
                return None, alive_err
            evt = PianoNoteEvent(
                seq=session.next_seq,
                note=note,
                action=action,
                author=participant,
                ts=time.time(),
            )
            session.next_seq += 1
            session.events.append(evt)
            if len(session.events) > MAX_EVENTS:
                session.events = session.events[-MAX_EVENTS:]
            session.rev += 1
            self._save()
            return {
                "rev": session.rev,
                "event": asdict(evt),
            }, ""

    def sync_since(
        self, token: str, ticket: str, since: int
    ) -> Tuple[Optional[dict], str]:
        session, participant, err = self.resolve_ticket(token, ticket)
        if session is None or participant is None:
            return None, err
        try:
            since_i = max(0, int(since))
        except (TypeError, ValueError):
            since_i = 0
        with self.lock:
            events = [
                asdict(e)
                for e in session.events
                if e.seq > since_i
            ]
            return {
                "rev": session.rev,
                "events": events,
                "participant": participant,
                "creator": session.creator,
                "room": session.room,
                "title": session.title,
                "expires": session.expires,
            }, ""

    def close_session(self, session_id: str, by_user: str) -> Tuple[bool, str]:
        with self.lock:
            session = self.sessions.get(session_id)
            if session is None:
                return False, "钢琴不存在"
            if session.creator.lower() != by_user.lower():
                return False, "只有发起人可以关闭钢琴"
            session.closed = True
            for token in session.tokens.values():
                self.token_to_session.pop(token, None)
            dead = [
                t
                for t, entry in self.tickets.items()
                if entry.session_id == session_id
            ]
            for t in dead:
                self.tickets.pop(t, None)
            self._save()
            return True, ""

    def find_open_for_room(self, room: str) -> Optional[PianoSession]:
        with self.lock:
            for session in self.sessions.values():
                if (
                    session.room
                    and session.room == room
                    and not session.closed
                ):
                    return session
        return None

    def cleanup_expired(self) -> int:
        now = time.time()
        removed = 0
        with self.lock:
            dead_ids = [
                sid
                for sid, s in self.sessions.items()
                if s.closed
                or (not s.room and s.expires > 0 and s.expires <= now)
            ]
            for sid in dead_ids:
                session = self.sessions.pop(sid, None)
                if session is None:
                    continue
                removed += 1
                for token in session.tokens.values():
                    self.token_to_session.pop(token, None)
            dead_tickets = [
                t for t, entry in self.tickets.items() if entry.expires <= now
            ]
            for t in dead_tickets:
                self.tickets.pop(t, None)
            if removed or dead_tickets:
                self._save()
        return removed


_store_path = os.environ.get(
    "SSHCHAT_PIANO_STORE",
    os.path.join(
        os.environ.get("SSHCHAT_FILE_STORAGE_DIR", "/tmp/sshchat_files"),
        "piano_sessions.json",
    ),
)
piano_store = PianoStore(store_path=_store_path)
