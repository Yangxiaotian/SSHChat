"""
Shared drawing canvas with URL + separate key (same security shape as /sendfile).

Flow:
1. Chat creates a session; each participant gets a unique /canvas/<token> URL
   and a 6-character key delivered on a separate line (never in the URL).
2. Opening the page and posting the key mints a short-lived access ticket.
3. Excalidraw scene sync uses that ticket in a header — not the key, not the URL.

Unlike file download tickets, canvas access tickets are multi-use for the TTL
so collaborators can keep drawing and polling.
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


CANVAS_TTL_SECONDS = int(os.environ.get("SSHCHAT_CANVAS_TTL_SECONDS", str(4 * 3600)))
ACCESS_TICKET_TTL_SECONDS = int(
    os.environ.get("SSHCHAT_CANVAS_TICKET_TTL_SECONDS", "1800")
)
# Excalidraw scene limits (element-id merge, not freehand strokes).
MAX_ELEMENTS = int(os.environ.get("SSHCHAT_CANVAS_MAX_ELEMENTS", "5000"))
MAX_SCENE_BYTES = int(os.environ.get("SSHCHAT_CANVAS_MAX_SCENE_BYTES", str(2 * 1024 * 1024)))
MAX_FILES_BYTES = int(os.environ.get("SSHCHAT_CANVAS_MAX_FILES_BYTES", str(1 * 1024 * 1024)))
# Legacy stroke constants — kept so old clients get a clear error path.
MAX_STROKES = int(os.environ.get("SSHCHAT_CANVAS_MAX_STROKES", "5000"))
MAX_POINTS_PER_STROKE = int(os.environ.get("SSHCHAT_CANVAS_MAX_POINTS", "800"))
LOGICAL_WIDTH = 1200
LOGICAL_HEIGHT = 800


def _generate_token() -> str:
    return secrets.token_urlsafe(32)


def _generate_key() -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return "".join(secrets.choice(alphabet) for _ in range(6))


@dataclass
class CanvasAccessTicket:
    ticket: str
    session_id: str
    participant: str
    expires: float


@dataclass
class CanvasSession:
    session_id: str
    creator: str
    room: Optional[str]
    tokens: Dict[str, str]  # participant -> token
    keys: Dict[str, str]  # participant -> key
    # Excalidraw scene (elements + optional binary files) + monotonic rev for poll.
    elements: List[dict] = field(default_factory=list)
    files: Dict[str, dict] = field(default_factory=dict)
    rev: int = 0
    # Legacy freehand log (ignored by new UI; kept for disk compat).
    strokes: List[dict] = field(default_factory=list)
    next_seq: int = 1
    created_at: float = 0.0
    expires: float = 0.0
    closed: bool = False
    title: str = ""
    # When set, scene lives on host_node; this node only mirrors invites/lookup.
    host_node: Optional[str] = None
    host_base_url: Optional[str] = None


class CanvasStore:
    """In-memory (+ optional disk) store for shared canvas sessions."""

    def __init__(self, store_path: str = "canvas_sessions.json"):
        self.store_path = store_path
        self.sessions: Dict[str, CanvasSession] = {}
        self.token_to_session: Dict[str, str] = {}
        self.tickets: Dict[str, CanvasAccessTicket] = {}
        self.lock = threading.RLock()
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.store_path):
            return
        try:
            with open(self.store_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for sid, raw in data.get("sessions", {}).items():
                session = CanvasSession(
                    session_id=raw["session_id"],
                    creator=raw["creator"],
                    room=raw.get("room"),
                    tokens=dict(raw.get("tokens") or {}),
                    keys=dict(raw.get("keys") or {}),
                    elements=list(raw.get("elements") or []),
                    files=dict(raw.get("files") or {}),
                    rev=int(raw.get("rev") or 0),
                    strokes=list(raw.get("strokes") or []),
                    next_seq=int(raw.get("next_seq") or 1),
                    created_at=float(raw.get("created_at") or 0),
                    expires=float(raw.get("expires") or 0),
                    closed=bool(raw.get("closed")),
                    title=str(raw.get("title") or ""),
                    host_node=raw.get("host_node") or None,
                    host_base_url=raw.get("host_base_url") or None,
                )
                self.sessions[sid] = session
                for token in session.tokens.values():
                    self.token_to_session[token] = sid
            for ticket, raw in data.get("tickets", {}).items():
                self.tickets[ticket] = CanvasAccessTicket(**raw)
        except Exception as e:
            print(f"[Canvas] Failed to load: {e}")

    def _save(self) -> None:
        try:
            data = {
                "sessions": {
                    sid: asdict(session) for sid, session in self.sessions.items()
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
            os.replace(tmp, self.store_path)
        except Exception as e:
            print(f"[Canvas] Failed to save: {e}")

    def create_session(
        self,
        creator: str,
        participants: List[str],
        room: Optional[str] = None,
        title: str = "",
        ttl_seconds: int = CANVAS_TTL_SECONDS,
    ) -> CanvasSession:
        """Create a canvas; every participant (including creator) gets URL+key."""
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

        session = CanvasSession(
            session_id=session_id,
            creator=creator,
            room=room,
            tokens=tokens,
            keys=keys,
            elements=[],
            files={},
            rev=0,
            strokes=[],
            next_seq=1,
            created_at=now,
            expires=now + max(60, int(ttl_seconds)),
            closed=False,
            title=(title or "").strip()[:80],
        )
        with self.lock:
            self.sessions[session_id] = session
            for token in tokens.values():
                self.token_to_session[token] = session_id
            self._save()
        return session

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
    ) -> CanvasSession:
        """Mirror a canvas hosted on a federation peer (no local stroke storage)."""
        now = time.time()
        session = CanvasSession(
            session_id=session_id,
            creator=creator,
            room=room,
            tokens=dict(tokens),
            keys=dict(keys),
            elements=[],
            files={},
            rev=0,
            strokes=[],
            next_seq=1,
            created_at=now,
            expires=float(expires) if expires > 0 else now + CANVAS_TTL_SECONDS,
            closed=False,
            title=(title or "").strip()[:80],
            host_node=str(host_node or "").strip() or None,
            host_base_url=str(host_base_url or "").strip().rstrip("/") or None,
        )
        with self.lock:
            # Drop any prior open canvas for the same room on this node.
            if room:
                for sid, existing in list(self.sessions.items()):
                    if (
                        existing.room == room
                        and not existing.closed
                        and sid != session_id
                    ):
                        existing.closed = True
            self.sessions[session_id] = session
            # Do NOT index tokens here: stroke/auth must hit host_base_url.
            # Local token→session maps would mint empty local boards by mistake.
            self._save()
        return session

    def get_by_token(self, token: str) -> Optional[CanvasSession]:
        with self.lock:
            sid = self.token_to_session.get(token)
            if not sid:
                return None
            return self.sessions.get(sid)

    def participant_for_token(self, session: CanvasSession, token: str) -> Optional[str]:
        for name, t in session.tokens.items():
            if t == token:
                return name
        return None

    def _alive(self, session: CanvasSession) -> Tuple[bool, str]:
        if session.closed:
            return False, "画布已关闭"
        if time.time() > session.expires:
            return False, "画布已过期"
        return True, ""

    def issue_access_ticket(
        self, token: str, key: str
    ) -> Tuple[Optional[CanvasSession], Optional[str], Optional[str], str]:
        """Validate key for a token; return (session, participant, ticket, error)."""
        key = (key or "").strip().upper()
        if len(key) != 6:
            return None, None, None, "请输入6位密钥"
        with self.lock:
            session = self.get_by_token(token)
            if session is None:
                return None, None, None, "画布链接无效"
            ok, err = self._alive(session)
            if not ok:
                return None, None, None, err
            participant = self.participant_for_token(session, token)
            if participant is None:
                return None, None, None, "画布链接无效"
            expected = session.keys.get(participant, "")
            if not expected or key != expected.upper():
                return None, None, None, "密钥错误"
            # Keep a few concurrent tickets so re-auth / multi-tab / GUI+web
            # does not instantly invalidate an in-flight sync poller.
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
            self.tickets[ticket] = CanvasAccessTicket(
                ticket=ticket,
                session_id=session.session_id,
                participant=participant,
                expires=time.time() + ACCESS_TICKET_TTL_SECONDS,
            )
            self._save()
            return session, participant, ticket, ""

    def resolve_ticket(
        self, token: str, ticket: str
    ) -> Tuple[Optional[CanvasSession], Optional[str], str]:
        ticket = (ticket or "").strip()
        if not ticket:
            return None, None, "缺少访问凭据"
        with self.lock:
            session = self.get_by_token(token)
            if session is None:
                return None, None, "画布链接无效"
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
            # Sliding expiry while actively drawing/syncing.
            entry.expires = time.time() + ACCESS_TICKET_TTL_SECONDS
            return session, participant, ""

    @staticmethod
    def _element_rank(el: dict) -> Tuple[int, int]:
        try:
            version = int(el.get("version") or 0)
        except (TypeError, ValueError):
            version = 0
        try:
            nonce = int(el.get("versionNonce") or 0)
        except (TypeError, ValueError):
            nonce = 0
        return version, nonce

    def _sanitize_elements(self, elements) -> Optional[List[dict]]:
        if not isinstance(elements, list):
            return None
        out: List[dict] = []
        for el in elements:
            if not isinstance(el, dict):
                continue
            eid = el.get("id")
            if not isinstance(eid, str) or not eid or len(eid) > 128:
                continue
            # Drop huge unexpected blobs early.
            try:
                raw = json.dumps(el, ensure_ascii=False, separators=(",", ":"))
            except (TypeError, ValueError):
                continue
            if len(raw.encode("utf-8")) > 256 * 1024:
                continue
            out.append(el)
            if len(out) >= MAX_ELEMENTS:
                break
        return out

    def _sanitize_files(self, files) -> Dict[str, dict]:
        if not isinstance(files, dict):
            return {}
        out: Dict[str, dict] = {}
        total = 0
        for fid, meta in files.items():
            if not isinstance(fid, str) or len(fid) > 128:
                continue
            if not isinstance(meta, dict):
                continue
            try:
                raw = json.dumps(meta, ensure_ascii=False, separators=(",", ":"))
            except (TypeError, ValueError):
                continue
            size = len(raw.encode("utf-8"))
            if size > 512 * 1024:
                continue
            if total + size > MAX_FILES_BYTES:
                break
            out[fid] = meta
            total += size
        return out

    def _merge_elements(
        self, existing: List[dict], incoming: List[dict]
    ) -> List[dict]:
        by_id: Dict[str, dict] = {}
        for el in existing:
            eid = el.get("id") if isinstance(el, dict) else None
            if isinstance(eid, str) and eid:
                by_id[eid] = el
        for el in incoming:
            eid = el.get("id")
            if not isinstance(eid, str) or not eid:
                continue
            old = by_id.get(eid)
            if old is None or self._element_rank(el) >= self._element_rank(old):
                by_id[eid] = el
        # Keep deleted markers so peers can tombstone; cap list size.
        merged = list(by_id.values())
        if len(merged) > MAX_ELEMENTS:
            # Prefer non-deleted, then higher version.
            merged.sort(
                key=lambda e: (
                    1 if e.get("isDeleted") else 0,
                    -self._element_rank(e)[0],
                )
            )
            merged = merged[:MAX_ELEMENTS]
        return merged

    def apply_scene(
        self,
        token: str,
        ticket: str,
        *,
        elements,
        files=None,
    ) -> Tuple[Optional[dict], str]:
        """Merge Excalidraw elements by id/version and bump rev."""
        session, participant, err = self.resolve_ticket(token, ticket)
        if session is None or participant is None:
            return None, err
        cleaned = self._sanitize_elements(elements)
        if cleaned is None:
            return None, "场景数据无效"
        file_patch = self._sanitize_files(files) if files is not None else None
        try:
            probe = {"elements": cleaned, "files": file_patch or {}}
            if (
                len(json.dumps(probe, ensure_ascii=False).encode("utf-8"))
                > MAX_SCENE_BYTES
            ):
                return None, "场景过大"
        except (TypeError, ValueError):
            return None, "场景数据无效"

        with self.lock:
            session = self.get_by_token(token)
            if session is None:
                return None, "画布链接无效"
            ok, alive_err = self._alive(session)
            if not ok:
                return None, alive_err
            merged = self._merge_elements(session.elements, cleaned)
            session.elements = merged
            if file_patch is not None:
                # Shallow merge file ids; incoming wins per id.
                session.files.update(file_patch)
                # Drop files no longer referenced? skip — cheap keep.
            session.rev += 1
            # Keep legacy next_seq in lockstep for any old poller.
            session.next_seq = session.rev + 1
            self._save()
            return {
                "rev": session.rev,
                "elements": session.elements,
                "files": session.files,
                "author": participant,
            }, ""

    def add_stroke(
        self,
        token: str,
        ticket: str,
        *,
        color: str,
        width: float,
        points,
    ) -> Tuple[Optional[dict], str]:
        # Old freehand clients — point them at the Excalidraw web UI.
        return None, "请使用网页画板（Excalidraw）"

    def clear_board(
        self, token: str, ticket: str
    ) -> Tuple[Optional[dict], str]:
        session, participant, err = self.resolve_ticket(token, ticket)
        if session is None or participant is None:
            return None, err
        with self.lock:
            session = self.get_by_token(token)
            if session is None:
                return None, "画布链接无效"
            ok, alive_err = self._alive(session)
            if not ok:
                return None, alive_err
            session.elements = []
            session.files = {}
            session.strokes = []
            session.rev += 1
            session.next_seq = session.rev + 1
            self._save()
            return {
                "rev": session.rev,
                "kind": "clear",
                "author": participant,
                "elements": [],
                "files": {},
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
            changed = session.rev > since_i
            return {
                "rev": session.rev,
                "changed": changed,
                "elements": list(session.elements) if changed else [],
                "files": dict(session.files) if changed else {},
                # Legacy field for old tests/clients.
                "events": [],
                "next_seq": session.rev + 1,
                "participant": participant,
                "creator": session.creator,
                "room": session.room,
                "title": session.title,
                "expires": session.expires,
                "width": LOGICAL_WIDTH,
                "height": LOGICAL_HEIGHT,
            }, ""

    def close_session(self, session_id: str, by_user: str) -> Tuple[bool, str]:
        with self.lock:
            session = self.sessions.get(session_id)
            if session is None:
                return False, "画布不存在"
            if session.creator.lower() != by_user.lower():
                return False, "只有发起人可以关闭画布"
            session.closed = True
            dead_tickets = [
                t
                for t, entry in self.tickets.items()
                if entry.session_id == session_id
            ]
            for t in dead_tickets:
                self.tickets.pop(t, None)
            self._save()
            return True, ""

    def find_open_for_room(self, room: str) -> Optional[CanvasSession]:
        now = time.time()
        with self.lock:
            for session in self.sessions.values():
                if (
                    session.room
                    and session.room == room
                    and not session.closed
                    and session.expires > now
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
                if s.closed or s.expires <= now
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
                # also drop tickets whose session vanished
            orphan = [
                t
                for t, entry in self.tickets.items()
                if entry.session_id not in self.sessions
            ]
            for t in orphan:
                self.tickets.pop(t, None)
            if removed or dead_tickets or orphan:
                self._save()
        return removed


_store_path = os.environ.get(
    "SSHCHAT_CANVAS_STORE",
    os.path.join(
        os.environ.get("SSHCHAT_FILE_STORAGE_DIR", "/tmp/sshchat_files"),
        "canvas_sessions.json",
    ),
)
canvas_store = CanvasStore(store_path=_store_path)
