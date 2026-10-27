"""One-shot 5-minute guest chat links for users without an SSH key.

The first real page open burns the URL and starts the timer. Refresh is a
new GET, so it fails immediately. A WebSocket ticket lives only in that
first page, so a dropped socket on the same tab can reconnect until time
runs out; a reload cannot.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

CHAT_TTL_SEC = 5 * 60
UNUSED_TTL_SEC = 30 * 60
MAX_ACTIVE_PER_CREATOR = 5
MAX_MSG_CHARS = 500
# `~` is not allowed in Linux usernames (admin-add-user.sh), so this can never
# collide with a pubkey account. The rest is random — guests do not pick a nick.
_NICK_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"


def random_guest_nick() -> str:
    body = "".join(secrets.choice(_NICK_ALPHABET) for _ in range(6))
    return "~g" + body


def is_guest_nick(name: str) -> bool:
    return (name or "").startswith("~g")


@dataclass
class GuestLink:
    token: str
    creator: str
    room: str
    created_at: float
    unused_expires_at: float
    opened_at: float = 0.0
    chat_expires_at: float = 0.0
    ws_ticket: str = ""
    nick: str = ""
    locale: str = "en"
    closed: bool = False
    conn: object = None
    kick_started: bool = False


@dataclass(eq=False)
class GuestConn:
    """Duck-typed socket: chat bytes become WebSocket JSON text frames.

    eq=False keeps identity hashing so it can live in server.clients / rooms.
    """

    sock: object = None
    closed: bool = False
    _timeout: Optional[float] = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def attach_sock(self, sock) -> None:
        with self.lock:
            self.sock = sock

    def gettimeout(self):
        return self._timeout

    def settimeout(self, value) -> None:
        self._timeout = value

    def sendall(self, data: bytes) -> None:
        if not data:
            return
        text = data.decode("utf-8", errors="replace")
        if not self.send_json({"t": "line", "s": text}):
            raise OSError("guest disconnected")

    def send(self, data: bytes) -> None:
        self.sendall(data)

    def send_json(self, obj: dict) -> bool:
        if self.closed:
            return False
        try:
            import json

            import piano_ws
        except Exception:
            return False
        raw = piano_ws.encode_frame(
            json.dumps(obj, ensure_ascii=False).encode("utf-8")
        )
        try:
            with self.lock:
                if self.closed or self.sock is None:
                    return False
                self.sock.sendall(raw)
            return True
        except OSError:
            self.closed = True
            return False

    def close(self) -> None:
        with self.lock:
            if self.closed:
                sock = None
            else:
                self.closed = True
                sock = self.sock
                self.sock = None
        if sock is None:
            return
        try:
            import struct

            import piano_ws

            sock.sendall(
                piano_ws.encode_frame(
                    struct.pack("!H", 1000), opcode=piano_ws.OP_CLOSE
                )
            )
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass


class GuestStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.links: dict[str, GuestLink] = {}

    def create(self, creator: str, room: str) -> Optional[GuestLink]:
        now = time.time()
        with self._lock:
            self._purge_locked(now)
            mine = [
                link
                for link in self.links.values()
                if link.creator == creator and not link.closed
            ]
            for link in mine:
                if not link.opened_at:
                    link.closed = True
            active = [
                link
                for link in self.links.values()
                if link.creator == creator and not link.closed
            ]
            if len(active) >= MAX_ACTIVE_PER_CREATOR:
                return None
            token = secrets.token_urlsafe(24)
            link = GuestLink(
                token=token,
                creator=creator,
                room=room,
                created_at=now,
                unused_expires_at=now + UNUSED_TTL_SEC,
            )
            self.links[token] = link
            return link

    def get(self, token: str) -> Optional[GuestLink]:
        with self._lock:
            self._purge_locked(time.time())
            link = self.links.get(token)
            if link is None or link.closed:
                return None
            return link

    def open_once(self, token: str) -> tuple[Optional[GuestLink], str]:
        """First join burns the URL and starts the 5-minute window.

        A later join or a page refresh (GET after this) is ``used``.
        """
        now = time.time()
        with self._lock:
            self._purge_locked(now)
            link = self.links.get(token)
            if link is None or link.closed:
                return None, "gone"
            if link.opened_at:
                return None, "used"
            if now >= link.unused_expires_at:
                link.closed = True
                self.links.pop(token, None)
                return None, "expired"
            link.opened_at = now
            link.chat_expires_at = now + CHAT_TTL_SEC
            link.ws_ticket = secrets.token_urlsafe(16)
            link.nick = random_guest_nick()
            return link, ""

    def ws_ok(self, token: str, ticket: str) -> tuple[Optional[GuestLink], str]:
        now = time.time()
        with self._lock:
            self._purge_locked(now)
            link = self.links.get(token)
            if link is None or link.closed:
                return None, "gone"
            if not link.opened_at or not link.ws_ticket:
                return None, "gone"
            try:
                same = secrets.compare_digest(ticket or "", link.ws_ticket)
            except (TypeError, ValueError):
                same = False
            if not same:
                return None, "used"
            if now >= link.chat_expires_at:
                link.closed = True
                return None, "expired"
            return link, ""

    def remaining(self, link: GuestLink, now: Optional[float] = None) -> float:
        now = time.time() if now is None else now
        if not link.opened_at:
            return max(0.0, link.unused_expires_at - now)
        return max(0.0, link.chat_expires_at - now)

    def close_for_creator(self, creator: str, room: str) -> list[GuestLink]:
        closed: list[GuestLink] = []
        with self._lock:
            for link in self.links.values():
                if (
                    link.creator == creator
                    and link.room == room
                    and not link.closed
                ):
                    link.closed = True
                    closed.append(link)
        return closed

    def close_token(self, token: str) -> Optional[GuestLink]:
        with self._lock:
            link = self.links.get(token)
            if link is None or link.closed:
                return None
            link.closed = True
            return link

    def cleanup_expired(self) -> None:
        with self._lock:
            self._purge_locked(time.time())

    def _purge_locked(self, now: float) -> None:
        dead = []
        for token, link in self.links.items():
            if link.closed:
                dead.append(token)
                continue
            if not link.opened_at and now >= link.unused_expires_at:
                link.closed = True
                dead.append(token)
            elif link.opened_at and now >= link.chat_expires_at:
                link.closed = True
                dead.append(token)
        for token in dead:
            self.links.pop(token, None)


guest_store = GuestStore()
