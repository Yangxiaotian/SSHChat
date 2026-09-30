"""
Shared room piano — URL + separate key (same security shape as /canvas).

Flow:
1. Chat creates a session; each participant gets /piano/<token> + 6-char key.
2. Opening the page and posting the key mints a short-lived access ticket.
3. Note events sync via ticket-gated HTTP: batched POST /notes, long-poll GET /sync,
   plus pressed-key snapshots for drift correction. Disk persist is debounced.
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
HANDOFF_TTL_SECONDS = int(os.environ.get("SSHCHAT_PIANO_HANDOFF_TTL_SECONDS", "120"))
MAX_EVENTS = int(os.environ.get("SSHCHAT_PIANO_MAX_EVENTS", "2000"))
RECORDING_TTL_SECONDS = int(
    os.environ.get("SSHCHAT_PIANO_RECORDING_TTL_SECONDS", str(7 * 24 * 3600))
)
MAX_RECORDING_EVENTS = int(os.environ.get("SSHCHAT_PIANO_MAX_RECORDING_EVENTS", "5000"))
# Debounce disk writes for note spam (structural changes still save immediately).
SAVE_DEBOUNCE_SECONDS = float(os.environ.get("SSHCHAT_PIANO_SAVE_DEBOUNCE", "1.5"))
# Cap for one POST /notes body and long-poll wait.
MAX_BATCH_EVENTS = int(os.environ.get("SSHCHAT_PIANO_MAX_BATCH", "64"))
MAX_SYNC_WAIT_MS = int(os.environ.get("SSHCHAT_PIANO_SYNC_WAIT_MS", "20000"))


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
class PianoHandoff:
    token: str
    ticket: str
    participant: str
    room: Optional[str]
    title: str
    expires: float
    handoff_expires: float


@dataclass
class PianoNoteEvent:
    seq: int
    note: str
    action: str  # "on" | "off"
    author: str
    ts: float


@dataclass
class PianoRecording:
    recording_id: str
    author: str
    title: str
    events: List[dict]
    duration: float
    created_at: float
    expires: float


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
    parked: bool = False
    conflict_token: str = ""
    title: str = ""
    # When set, notes live on host_node; this node only mirrors invites/lookup.
    host_node: Optional[str] = None
    host_base_url: Optional[str] = None


class PianoStore:
    """In-memory (+ optional disk) store for shared piano sessions."""

    def __init__(
        self,
        store_path: str = "piano_sessions.json",
        recordings_path: Optional[str] = None,
    ):
        self.store_path = store_path
        self.recordings_path = recordings_path or str(
            Path(store_path).with_name("piano_recordings.json")
        )
        self.sessions: Dict[str, PianoSession] = {}
        self.token_to_session: Dict[str, str] = {}
        self.tickets: Dict[str, PianoAccessTicket] = {}
        self.handoffs: Dict[str, PianoHandoff] = {}
        self.recordings: Dict[str, PianoRecording] = {}
        # Ephemeral: session_id -> author -> currently held notes (not persisted).
        self.held: Dict[str, Dict[str, set]] = {}
        self.lock = threading.RLock()
        self._event_cond = threading.Condition(self.lock)
        self._save_timer: Optional[threading.Timer] = None
        self._load()
        self._load_recordings()

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
                    parked=bool(raw.get("parked")),
                    conflict_token=str(raw.get("conflict_token") or ""),
                    title=str(raw.get("title") or ""),
                    host_node=raw.get("host_node") or None,
                    host_base_url=raw.get("host_base_url") or None,
                )
                if self.sessions[sid].room and not self.sessions[sid].conflict_token:
                    self.sessions[sid].conflict_token = secrets.token_hex(16)
                if (
                    not self.sessions[sid].closed
                    and not self.sessions[sid].parked
                    and not self.sessions[sid].host_node
                ):
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

    def _load_recordings(self) -> None:
        if not os.path.exists(self.recordings_path):
            return
        try:
            with open(self.recordings_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            now = time.time()
            for rid, raw in (data.get("recordings") or {}).items():
                try:
                    rec = PianoRecording(
                        recording_id=str(raw["recording_id"]),
                        author=str(raw.get("author") or ""),
                        title=str(raw.get("title") or "")[:80],
                        events=list(raw.get("events") or [])[-MAX_RECORDING_EVENTS:],
                        duration=float(raw.get("duration") or 0),
                        created_at=float(raw.get("created_at") or 0),
                        expires=float(raw.get("expires") or 0),
                    )
                except (KeyError, TypeError, ValueError):
                    continue
                if rec.expires > 0 and rec.expires <= now:
                    continue
                self.recordings[rid] = rec
        except Exception as e:
            print(f"[Piano] Failed to load recordings: {e}")

    def _save_recordings(self) -> None:
        try:
            data = {
                "recordings": {
                    rid: asdict(rec) for rid, rec in self.recordings.items()
                }
            }
            path = Path(self.recordings_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = f"{self.recordings_path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
            os.replace(tmp, self.recordings_path)
        except Exception as e:
            print(f"[Piano] Failed to save recordings: {e}")

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

    def _cancel_save_timer_locked(self) -> None:
        if self._save_timer is not None:
            try:
                self._save_timer.cancel()
            except Exception:
                pass
            self._save_timer = None

    def _save_now(self) -> None:
        """Immediate persist (create/close/auth). Cancels a pending debounced save."""
        with self.lock:
            self._cancel_save_timer_locked()
            self._save()

    def _schedule_save(self) -> None:
        """Debounce disk writes under note spam. Caller may hold the store lock."""
        def _fire() -> None:
            with self.lock:
                self._save_timer = None
                self._save()

        with self.lock:
            self._cancel_save_timer_locked()
            timer = threading.Timer(max(0.2, SAVE_DEBOUNCE_SECONDS), _fire)
            timer.daemon = True
            self._save_timer = timer
            timer.start()

    def _held_snapshot_locked(self, session_id: str) -> Dict[str, List[str]]:
        by_author = self.held.get(session_id) or {}
        return {
            author: sorted(notes)
            for author, notes in by_author.items()
            if notes
        }

    def _apply_held_event_locked(
        self, session_id: str, author: str, note: str, action: str
    ) -> None:
        by_author = self.held.setdefault(session_id, {})
        held = by_author.setdefault(author, set())
        if action == "on":
            held.add(note)
        else:
            held.discard(note)
        if not held:
            by_author.pop(author, None)
        if not by_author:
            self.held.pop(session_id, None)

    def _set_held_locked(
        self, session_id: str, author: str, notes: Optional[List[str]]
    ) -> None:
        """Replace author's held set (client authoritative snapshot for correction)."""
        if notes is None:
            return
        cleaned: set = set()
        for raw in notes:
            note = str(raw or "").strip()
            if note and len(note) <= 8:
                cleaned.add(note)
        by_author = self.held.setdefault(session_id, {})
        if cleaned:
            by_author[author] = cleaned
        else:
            by_author.pop(author, None)
        if not by_author:
            self.held.pop(session_id, None)

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
            parked=False,
            conflict_token=secrets.token_hex(16) if room else "",
            title=(title or "").strip()[:80],
        )
        with self.lock:
            self.sessions[session_id] = session
            for token in tokens.values():
                self.token_to_session[token] = session_id
            self._save_now()
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
            if not session.host_node:
                self.token_to_session[token] = session_id
            self._save_now()
            return token, key, ""

    def get_by_token(self, token: str) -> Optional[PianoSession]:
        with self.lock:
            sid = self.token_to_session.get(token)
            if not sid:
                return None
            session = self.sessions.get(sid)
            if session is None or session.closed or session.parked:
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
        if session.parked:
            return False, "钢琴已暂存（联邦合并中）"
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
            self._save_now()
            return session, participant, ticket, ""

    def create_handoff(self, token: str, key: str) -> Tuple[Optional[str], str]:
        session, participant, ticket, err = self.issue_access_ticket(token, key)
        if session is None or not ticket or participant is None:
            return None, err or "密钥错误"
        code = secrets.token_urlsafe(24)
        now = time.time()
        with self.lock:
            self.handoffs[code] = PianoHandoff(
                token=token,
                ticket=ticket,
                participant=participant,
                room=session.room,
                title=session.title,
                expires=session.expires,
                handoff_expires=now + HANDOFF_TTL_SECONDS,
            )
            self._purge_handoffs_locked(now)
        return code, ""

    def consume_handoff(
        self, token: str, code: str
    ) -> Tuple[Optional[dict], str]:
        token = (token or "").strip()
        code = (code or "").strip()
        if not token or not code:
            return None, "链接无效"
        with self.lock:
            now = time.time()
            self._purge_handoffs_locked(now)
            entry = self.handoffs.pop(code, None)
            if entry is None:
                return None, "链接无效或已使用"
            if entry.token != token:
                return None, "链接无效"
            if entry.handoff_expires <= now:
                return None, "链接已过期，请重新打开"
            session = self.get_by_token(token)
            if session is None:
                return None, "钢琴链接无效"
            ok, err = self._alive(session)
            if not ok:
                return None, err
            return (
                {
                    "ticket": entry.ticket,
                    "participant": entry.participant,
                    "room": entry.room or "",
                    "title": entry.title or "",
                    "expires": entry.expires,
                },
                "",
            )

    def _purge_handoffs_locked(self, now: float) -> None:
        dead = [c for c, h in self.handoffs.items() if h.handoff_expires <= now]
        for code in dead:
            self.handoffs.pop(code, None)

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
                self._save_now()
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
        client_ts: Optional[float] = None,
    ) -> Tuple[Optional[dict], str]:
        batch, err = self.push_notes(
            token,
            ticket,
            [{"note": note, "action": action, "ts": client_ts}],
        )
        if batch is None:
            return None, err
        events = batch.get("events") or []
        return {
            "rev": batch.get("rev", 0),
            "event": events[0] if events else None,
            "held": batch.get("held") or {},
            "session_id": batch.get("session_id") or "",
        }, ""

    def push_notes(
        self,
        token: str,
        ticket: str,
        events: Optional[List[dict]] = None,
        *,
        held: Optional[List[str]] = None,
    ) -> Tuple[Optional[dict], str]:
        """Append a batch of note on/off events; optionally set author's held keys."""
        raw = list(events or [])
        if not raw and held is None:
            return None, "无事件"
        if len(raw) > MAX_BATCH_EVENTS:
            return None, f"事件过多（最多 {MAX_BATCH_EVENTS}）"
        session, participant, err = self.resolve_ticket(token, ticket)
        if session is None or participant is None:
            return None, err
        with self._event_cond:
            session = self.get_by_token(token)
            if session is None:
                return None, "钢琴链接无效"
            ok, alive_err = self._alive(session)
            if not ok:
                return None, alive_err
            now = time.time()
            out_events: List[dict] = []
            for item in raw:
                if not isinstance(item, dict):
                    continue
                note = str(item.get("note") or "").strip()
                action = str(item.get("action") or "on").strip().lower()
                if action not in ("on", "off") or not note or len(note) > 8:
                    continue
                ts = now
                client_ts = item.get("ts")
                if client_ts is not None:
                    try:
                        ct = float(client_ts)
                        if abs(ct - now) < 60:
                            ts = ct
                    except (TypeError, ValueError):
                        pass
                evt = PianoNoteEvent(
                    seq=session.next_seq,
                    note=note,
                    action=action,
                    author=participant,
                    ts=ts,
                )
                session.next_seq += 1
                session.events.append(evt)
                self._apply_held_event_locked(
                    session.session_id, participant, note, action
                )
                out_events.append(asdict(evt))
            if held is not None:
                self._set_held_locked(session.session_id, participant, held)
            if not out_events and held is None:
                return None, "无效音符"
            if len(session.events) > MAX_EVENTS:
                session.events = session.events[-MAX_EVENTS:]
            if out_events or held is not None:
                session.rev += 1
            self._schedule_save()
            self._event_cond.notify_all()
            return {
                "rev": session.rev,
                "events": out_events,
                "held": self._held_snapshot_locked(session.session_id),
                "session_id": session.session_id,
            }, ""

    def sync_since(
        self,
        token: str,
        ticket: str,
        since: int,
        *,
        wait_ms: int = 0,
    ) -> Tuple[Optional[dict], str]:
        session, participant, err = self.resolve_ticket(token, ticket)
        if session is None or participant is None:
            return None, err
        try:
            since_i = max(0, int(since))
        except (TypeError, ValueError):
            since_i = 0
        try:
            wait_i = max(0, min(int(wait_ms), MAX_SYNC_WAIT_MS))
        except (TypeError, ValueError):
            wait_i = 0
        deadline = time.time() + (wait_i / 1000.0) if wait_i else 0.0

        with self._event_cond:
            # Re-check under the same lock used for waits.
            while True:
                live = self.get_by_token(token)
                if live is None:
                    return None, "钢琴链接无效"
                session = live
                events = [asdict(e) for e in session.events if e.seq > since_i]
                payload = {
                    "rev": session.rev,
                    "events": events,
                    "held": self._held_snapshot_locked(session.session_id),
                    "participant": participant,
                    "creator": session.creator,
                    "room": session.room,
                    "title": session.title,
                    "expires": session.expires,
                }
                if events or wait_i <= 0:
                    return payload, ""
                remaining = deadline - time.time()
                if remaining <= 0:
                    return payload, ""
                rev_before = session.rev
                self._event_cond.wait(timeout=remaining)
                # Held-only updates bump rev with no new seqs — still return so
                # peers can apply the pressed-key snapshot immediately.
                if session.rev != rev_before:
                    events = [asdict(e) for e in session.events if e.seq > since_i]
                    return {
                        "rev": session.rev,
                        "events": events,
                        "held": self._held_snapshot_locked(session.session_id),
                        "participant": participant,
                        "creator": session.creator,
                        "room": session.room,
                        "title": session.title,
                        "expires": session.expires,
                    }, ""

    def close_session(self, session_id: str, by_user: str) -> Tuple[bool, str]:
        with self._event_cond:
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
            self.held.pop(session_id, None)
            self._cancel_save_timer_locked()
            self._save()
            self._event_cond.notify_all()
            return True, ""

    def find_open_for_room(self, room: str) -> Optional[PianoSession]:
        with self.lock:
            for session in self.sessions.values():
                if (
                    session.room
                    and session.room == room
                    and not session.closed
                    and not session.parked
                ):
                    return session
        return None

    def find_parked_for_room(self, room: str) -> Optional[PianoSession]:
        room = (room or "").strip()
        if not room:
            return None
        with self.lock:
            for session in self.sessions.values():
                if (
                    session.room == room
                    and session.parked
                    and not session.closed
                    and not session.host_node
                ):
                    return session
        return None

    def register_remote_session(
        self,
        *,
        session_id: str,
        creator: str,
        participants: List[str],
        room: Optional[str],
        tokens: Dict[str, str],
        keys: Dict[str, str],
        host_node: str,
        host_base_url: str,
        title: str = "",
        expires: float = 0.0,
        conflict_token: str = "",
        rev: int = 0,
    ) -> PianoSession:
        """Mirror a piano hosted on a federation peer (no local note storage)."""
        now = time.time()
        session = PianoSession(
            session_id=session_id,
            creator=creator,
            room=room,
            tokens=dict(tokens),
            keys=dict(keys),
            events=[],
            rev=max(0, int(rev or 0)),
            next_seq=1,
            created_at=now,
            expires=float(expires) if expires > 0 else (0.0 if room else now + PIANO_TTL_SECONDS),
            closed=False,
            parked=False,
            conflict_token=str(conflict_token or "").strip() or secrets.token_hex(16),
            title=(title or "").strip()[:80],
            host_node=str(host_node or "").strip() or None,
            host_base_url=str(host_base_url or "").strip().rstrip("/") or None,
        )
        with self.lock:
            if room:
                for sid, existing in list(self.sessions.items()):
                    if (
                        existing.room == room
                        and not existing.closed
                        and not existing.parked
                        and sid != session_id
                    ):
                        if existing.host_node:
                            existing.closed = True
                        else:
                            existing.parked = True
                            for token in existing.tokens.values():
                                if self.token_to_session.get(token) == sid:
                                    self.token_to_session.pop(token, None)
            self.sessions[session_id] = session
            self._save_now()
        return session

    def announce_dict(self, session: PianoSession) -> dict:
        host = (session.host_node or "").strip()
        base = (session.host_base_url or "").strip().rstrip("/")
        return {
            "session_id": session.session_id,
            "room": session.room,
            "creator": session.creator,
            "host_node": host,
            "base_url": base,
            "conflict_token": session.conflict_token or session.session_id,
            "rev": int(session.rev or 0),
            "title": session.title,
            "tokens": dict(session.tokens),
            "keys": dict(session.keys),
            "expires": float(session.expires or 0),
        }

    def list_open_room_announces(self, *, local_node_id: str = "") -> List[dict]:
        out: List[dict] = []
        with self.lock:
            for session in self.sessions.values():
                if (
                    not session.room
                    or session.closed
                    or session.parked
                    or session.host_node
                ):
                    continue
                ann = self.announce_dict(session)
                if local_node_id and not ann["host_node"]:
                    ann["host_node"] = local_node_id
                out.append(ann)
        return out

    def park_session(self, session_id: str) -> bool:
        with self.lock:
            session = self.sessions.get(session_id)
            if session is None or session.closed or session.host_node:
                return False
            if session.parked:
                return True
            session.parked = True
            for token in session.tokens.values():
                if self.token_to_session.get(token) == session_id:
                    self.token_to_session.pop(token, None)
            dead = [
                t
                for t, entry in self.tickets.items()
                if entry.session_id == session_id
            ]
            for t in dead:
                self.tickets.pop(t, None)
            self._save_now()
            return True

    def promote_parked_for_room(self, room: str) -> Optional[PianoSession]:
        room = (room or "").strip()
        if not room:
            return None
        with self.lock:
            parked = self.find_parked_for_room(room)
            if parked is None:
                return None
            # find_parked holds no lock guarantee when called nested — re-fetch
            parked = None
            for session in self.sessions.values():
                if (
                    session.room == room
                    and session.parked
                    and not session.closed
                    and not session.host_node
                ):
                    parked = session
                    break
            if parked is None:
                return None
            for session in list(self.sessions.values()):
                if (
                    session.session_id == parked.session_id
                    or session.room != room
                    or session.closed
                ):
                    continue
                if session.host_node and not session.parked:
                    session.closed = True
                elif not session.host_node and not session.parked:
                    return None
            parked.parked = False
            for token in parked.tokens.values():
                self.token_to_session[token] = parked.session_id
            self._save_now()
            return parked

    def claim_remote_as_local(
        self, session_id: str, *, base_url: str = ""
    ) -> Optional[PianoSession]:
        session_id = (session_id or "").strip()
        if not session_id:
            return None
        base = (base_url or "").strip().rstrip("/") or None
        with self.lock:
            session = self.sessions.get(session_id)
            if session is None or session.closed or session.parked:
                return None
            if not (session.host_node or "").strip():
                return None
            session.host_node = None
            session.host_base_url = base
            for token in session.tokens.values():
                tok = (token or "").strip()
                if tok:
                    self.token_to_session[tok] = session_id
            dead = [
                t
                for t, entry in self.tickets.items()
                if entry.session_id == session_id
            ]
            for t in dead:
                self.tickets.pop(t, None)
            self._save_now()
            return session

    def refresh_host_base_url(self, host_node: str, base_url: str) -> int:
        host_node = (host_node or "").strip()
        base = (base_url or "").strip().rstrip("/")
        if not host_node or not base or base == "-":
            return 0
        changed = 0
        with self.lock:
            for session in self.sessions.values():
                if session.closed or session.parked:
                    continue
                if (session.host_node or "").strip() != host_node:
                    continue
                prev = (session.host_base_url or "").strip().rstrip("/")
                if prev == base:
                    continue
                session.host_base_url = base
                changed += 1
            if changed:
                self._save_now()
        return changed

    def apply_remote_announce_refresh(self, announce: dict) -> bool:
        session_id = str(announce.get("session_id") or "").strip()
        if not session_id:
            return False
        base = str(announce.get("base_url") or "").strip().rstrip("/")
        host_node = str(announce.get("host_node") or "").strip()
        tokens = announce.get("tokens") or {}
        keys = announce.get("keys") or {}
        if not isinstance(tokens, dict):
            tokens = {}
        if not isinstance(keys, dict):
            keys = {}
        with self.lock:
            session = self.sessions.get(session_id)
            if session is None or session.closed or not session.host_node:
                return False
            dirty = False
            if host_node and (session.host_node or "") != host_node:
                session.host_node = host_node
                dirty = True
            if base and (session.host_base_url or "").rstrip("/") != base:
                session.host_base_url = base
                dirty = True
            new_tokens = {str(k): str(v) for k, v in tokens.items() if str(k) and str(v)}
            new_keys = {str(k): str(v) for k, v in keys.items() if str(k)}
            if new_tokens and new_tokens != dict(session.tokens):
                session.tokens = new_tokens
                dirty = True
            if new_keys and new_keys != dict(session.keys):
                session.keys = new_keys
                dirty = True
            if dirty:
                self._save_now()
            return dirty

    def save_recording(
        self,
        token: str,
        ticket: str,
        *,
        title: str = "",
        events: Optional[List[dict]] = None,
        duration: float = 0,
    ) -> Tuple[Optional[str], str]:
        session, participant, err = self.resolve_ticket(token, ticket)
        if session is None or participant is None:
            return None, err
        raw_events = list(events or [])
        if not raw_events:
            return None, "录制为空"
        if len(raw_events) > MAX_RECORDING_EVENTS:
            return None, f"录制事件过多（最多 {MAX_RECORDING_EVENTS}）"
        cleaned: List[dict] = []
        for evt in raw_events:
            if not isinstance(evt, dict):
                continue
            note = str(evt.get("note") or "").strip()
            action = str(evt.get("action") or "on").strip().lower()
            if action not in ("on", "off") or not note or len(note) > 8:
                continue
            try:
                t = max(0.0, float(evt.get("t", 0)))
            except (TypeError, ValueError):
                t = 0.0
            cleaned.append({"t": t, "note": note, "action": action})
        if not cleaned:
            return None, "录制无效"
        try:
            dur = max(0.0, float(duration))
        except (TypeError, ValueError):
            dur = 0.0
        if dur <= 0:
            dur = cleaned[-1]["t"]
        now = time.time()
        recording_id = secrets.token_urlsafe(12)
        rec = PianoRecording(
            recording_id=recording_id,
            author=participant,
            title=(title or "").strip()[:80],
            events=cleaned,
            duration=dur,
            created_at=now,
            expires=now + max(3600, RECORDING_TTL_SECONDS),
        )
        with self.lock:
            self.recordings[recording_id] = rec
            self._save_recordings()
        return recording_id, ""

    def get_recording(self, recording_id: str) -> Optional[PianoRecording]:
        recording_id = (recording_id or "").strip()
        if not recording_id:
            return None
        with self.lock:
            rec = self.recordings.get(recording_id)
            if rec is None:
                return None
            if rec.expires > 0 and time.time() > rec.expires:
                self.recordings.pop(recording_id, None)
                self._save_recordings()
                return None
            return rec

    def cleanup_expired(self) -> int:
        now = time.time()
        removed = 0
        with self._event_cond:
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
                self.held.pop(sid, None)
            dead_tickets = [
                t for t, entry in self.tickets.items() if entry.expires <= now
            ]
            for t in dead_tickets:
                self.tickets.pop(t, None)
            dead_handoffs = [
                c for c, h in self.handoffs.items() if h.handoff_expires <= now
            ]
            for code in dead_handoffs:
                self.handoffs.pop(code, None)
            dead_recordings = [
                rid
                for rid, rec in self.recordings.items()
                if rec.expires > 0 and rec.expires <= now
            ]
            for rid in dead_recordings:
                self.recordings.pop(rid, None)
            if removed or dead_tickets or dead_recordings:
                if removed or dead_tickets:
                    self._cancel_save_timer_locked()
                    self._save()
                if dead_recordings:
                    self._save_recordings()
            if removed:
                self._event_cond.notify_all()
        return removed


_store_path = os.environ.get(
    "SSHCHAT_PIANO_STORE",
    os.path.join(
        os.environ.get("SSHCHAT_FILE_STORAGE_DIR", "/tmp/sshchat_files"),
        "piano_sessions.json",
    ),
)
piano_store = PianoStore(store_path=_store_path)
