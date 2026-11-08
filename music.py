"""Server-side music drop folder: list audio files under a directory."""
from __future__ import annotations

import os
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import quote, urlsplit, urlunsplit

MUSIC_EXTENSIONS = {".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".wav"}
MUSIC_TTL_SECONDS = int(os.environ.get("SSHCHAT_MUSIC_TTL_SECONDS", str(8 * 3600)))

_MIME = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".flac": "audio/flac",
    ".wav": "audio/wav",
}


def default_music_dir() -> Path:
    raw = os.environ.get("SSHCHAT_MUSIC_DIR", "").strip()
    if raw:
        return Path(raw)
    return Path("/opt/sshchat/music")


def format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def mime_for(name: str) -> str:
    return _MIME.get(Path(name).suffix.lower(), "application/octet-stream")


@dataclass
class SongEntry:
    index: int
    name: str
    ext: str
    size_bytes: int
    path: Path


@dataclass(frozen=True)
class CatalogItem:
    """Unified playlist row for local + federated catalogs."""

    index: int
    name: str
    ext: str
    size_bytes: int
    origin: str  # "" = local; otherwise owning peer node_id
    path: Optional[Path] = None

    @property
    def is_remote(self) -> bool:
        return bool(self.origin)

    def display_origin(self) -> str:
        return f" @{self.origin}" if self.origin else ""


def song_entry_to_meta(entry: SongEntry) -> dict[str, Any]:
    return {
        "name": entry.name,
        "ext": entry.ext,
        "size_bytes": int(entry.size_bytes),
    }


def merge_federated_catalog(
    local: list[SongEntry],
    remote_by_node: dict[str, list[dict[str, Any]]],
) -> list[CatalogItem]:
    """Union local songs with remote catalogs; stable sort by name then origin."""
    items: list[CatalogItem] = []
    for entry in local:
        items.append(
            CatalogItem(
                index=0,
                name=entry.name,
                ext=entry.ext,
                size_bytes=entry.size_bytes,
                origin="",
                path=entry.path,
            )
        )
    allowed_exts = {ext.lstrip(".") for ext in MUSIC_EXTENSIONS}
    for node_id, rows in (remote_by_node or {}).items():
        node_id = str(node_id or "").strip()
        if not node_id:
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip()
            if not name or Path(name).name != name:
                continue
            ext = str(row.get("ext") or Path(name).suffix.lstrip(".")).lower()
            if ext not in allowed_exts:
                continue
            try:
                size = int(row.get("size_bytes") or 0)
            except (TypeError, ValueError):
                size = 0
            items.append(
                CatalogItem(
                    index=0,
                    name=name,
                    ext=ext,
                    size_bytes=size,
                    origin=node_id,
                    path=None,
                )
            )
    items.sort(key=lambda it: (it.name.lower(), it.origin.lower()))
    return [
        CatalogItem(
            index=i + 1,
            name=it.name,
            ext=it.ext,
            size_bytes=it.size_bytes,
            origin=it.origin,
            path=it.path,
        )
        for i, it in enumerate(items)
    ]


def search_catalog_items(
    catalog: list[CatalogItem], query: str
) -> list[CatalogItem]:
    query = (query or "").strip().lower()
    if not query:
        return list(catalog)
    terms = query.split()
    results: list[CatalogItem] = []
    for entry in catalog:
        haystack = f"{entry.name} {entry.ext} {entry.origin}".lower()
        if all(term in haystack for term in terms):
            results.append(entry)
    return results


def resolve_catalog_item(
    token: str, catalog: list[CatalogItem]
) -> Optional[CatalogItem]:
    token = (token or "").strip()
    if not token:
        return None
    if token.isdigit():
        idx = int(token)
        for entry in catalog:
            if entry.index == idx:
                return entry
        return None
    origin = ""
    name = token
    if "@" in token and not token.startswith("@"):
        name, origin = token.rsplit("@", 1)
        name = name.strip()
        origin = origin.strip()
    safe_name = Path(name).name
    if safe_name != name or ".." in name or "/" in name or "\\" in name:
        return None
    matches = [
        entry
        for entry in catalog
        if entry.name == safe_name and (not origin or entry.origin == origin)
    ]
    if len(matches) == 1:
        return matches[0]
    if not origin:
        local = [entry for entry in matches if not entry.origin]
        if len(local) == 1:
            return local[0]
    return None


def list_songs(music_dir: Path) -> list[SongEntry]:
    if not music_dir.is_dir():
        return []
    entries: list[SongEntry] = []
    for path in sorted(music_dir.iterdir(), key=lambda p: p.name.lower()):
        if not path.is_file():
            continue
        ext = path.suffix.lower()
        if ext not in MUSIC_EXTENSIONS:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        entries.append(
            SongEntry(
                index=len(entries) + 1,
                name=path.name,
                ext=ext.lstrip("."),
                size_bytes=size,
                path=path,
            )
        )
    return entries


def search_catalog(catalog: list[SongEntry], query: str) -> list[SongEntry]:
    query = (query or "").strip().lower()
    if not query:
        return list(catalog)
    terms = query.split()
    results: list[SongEntry] = []
    for entry in catalog:
        haystack = f"{entry.name} {entry.ext}".lower()
        if all(term in haystack for term in terms):
            results.append(entry)
    return results


def resolve_song(token: str, catalog: list[SongEntry]) -> Optional[SongEntry]:
    token = (token or "").strip()
    if not token:
        return None
    if token.isdigit():
        idx = int(token)
        for entry in catalog:
            if entry.index == idx:
                return entry
        return None
    safe = Path(token).name
    if safe != token or ".." in token or "/" in token or "\\" in token:
        return None
    matches = [entry for entry in catalog if entry.name == safe]
    if len(matches) == 1:
        return matches[0]
    lowered = [entry for entry in catalog if entry.name.lower() == safe.lower()]
    if len(lowered) == 1:
        return lowered[0]
    return None


def catalog_item_to_player_row(item: CatalogItem) -> dict[str, Any]:
    return {
        "name": item.name,
        "ext": item.ext,
        "size": format_size(item.size_bytes),
        "bytes": int(item.size_bytes),
        "origin": item.origin or "",
        "mime": mime_for(item.name),
    }


def song_entry_to_player_row(entry: SongEntry, origin: str = "") -> dict[str, Any]:
    return {
        "name": entry.name,
        "ext": entry.ext,
        "size": format_size(entry.size_bytes),
        "bytes": int(entry.size_bytes),
        "origin": origin or "",
        "mime": mime_for(entry.name),
    }


_player_catalog_hook: Optional[Callable[[], list]] = None
_remote_file_hook: Optional[Callable[[str, str, str], Optional[str]]] = None


def set_player_catalog_hook(fn: Optional[Callable[[], list]]) -> None:
    global _player_catalog_hook
    _player_catalog_hook = fn


def set_remote_file_hook(
    fn: Optional[Callable[[str, str, str], Optional[str]]],
) -> None:
    global _remote_file_hook
    _remote_file_hook = fn


def _coerce_player_row(item: object) -> Optional[dict[str, Any]]:
    if isinstance(item, CatalogItem):
        return catalog_item_to_player_row(item)
    if isinstance(item, SongEntry):
        return song_entry_to_player_row(item)
    if not isinstance(item, dict):
        return None
    name = str(item.get("name") or "").strip()
    if not name or Path(name).name != name:
        return None
    ext = str(item.get("ext") or Path(name).suffix.lstrip(".")).lower()
    try:
        size_bytes = int(item.get("bytes") or item.get("size_bytes") or 0)
    except (TypeError, ValueError):
        size_bytes = 0
    origin = str(item.get("origin") or "").strip()
    return {
        "name": name,
        "ext": ext,
        "size": str(item.get("size") or format_size(size_bytes)),
        "bytes": size_bytes,
        "origin": origin,
        "mime": str(item.get("mime") or mime_for(name)),
    }


def player_rows() -> list[dict[str, Any]]:
    """Federated playlist when the server hook is set; else local drop folder."""
    items: list = []
    hook = _player_catalog_hook
    if hook is not None:
        try:
            items = list(hook() or [])
        except Exception:
            items = []
    if not items:
        items = list_songs(default_music_dir())
    rows: list[dict[str, Any]] = []
    for item in items:
        row = _coerce_player_row(item)
        if row:
            rows.append(row)
    return rows


def pick_player_row(
    rows: list[dict[str, Any]], name: str, origin: str = ""
) -> Optional[dict[str, Any]]:
    name = Path(str(name or "")).name
    origin = str(origin or "").strip()
    if not name:
        return None
    if origin:
        for row in rows:
            if row.get("name") == name and str(row.get("origin") or "") == origin:
                return row
        return None
    local = [
        row
        for row in rows
        if row.get("name") == name and not row.get("origin")
    ]
    if len(local) == 1:
        return local[0]
    if local:
        return local[0]
    remote = [
        row for row in rows if row.get("name") == name and row.get("origin")
    ]
    if len(remote) == 1:
        return remote[0]
    return remote[0] if remote else None


def origin_file_url(page_url: str, song_name: str) -> str:
    """Map an origin player page URL to its audio file URL."""
    raw = str(page_url or "").strip()
    song_name = Path(str(song_name or "")).name
    parts = urlsplit(raw)
    path = str(parts.path or "").rstrip("/")
    file_path = f"{path}/file/{quote(song_name, safe='')}"
    return urlunsplit((parts.scheme, parts.netloc, file_path, "", ""))


def resolve_song_file(music_dir: Path, name: str) -> Optional[Path]:
    """Map a playlist filename to a file inside *music_dir* (no path escape)."""
    raw = (name or "").replace("\\", "/")
    safe = Path(raw).name
    if not safe or raw != safe or ".." in safe:
        return None
    root = music_dir.resolve()
    path = (root / safe).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    if path.suffix.lower() not in MUSIC_EXTENSIONS or not path.is_file():
        return None
    return path


def _generate_token() -> str:
    return secrets.token_urlsafe(32)


def _generate_key() -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return "".join(secrets.choice(alphabet) for _ in range(6))


@dataclass
class MusicSession:
    token: str
    key: str
    start_index: int
    nick: str
    created: float


class MusicStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, MusicSession] = {}
        # origin node_id -> player page URL (token reused for every song on that node)
        self._origin_pages: dict[str, str] = {}

    def remote_file_url(self, origin: str, song_name: str, nick: str) -> Optional[str]:
        origin = str(origin or "").strip()
        song_name = Path(str(song_name or "")).name
        if not origin or not song_name:
            return None
        with self._lock:
            cached = str(self._origin_pages.get(origin) or "").strip()
        if cached:
            return origin_file_url(cached, song_name)
        hook = _remote_file_hook
        if hook is None:
            return None
        try:
            page_url = hook(origin, song_name, nick)
        except Exception:
            return None
        page_url = str(page_url or "").strip()
        if not page_url:
            return None
        with self._lock:
            self._origin_pages[origin] = page_url
        return origin_file_url(page_url, song_name)

    def mint(self, start_index: int, nick: str) -> MusicSession:
        now = time.time()
        nick = (nick or "").strip()
        start_index = max(1, int(start_index or 1))
        with self._lock:
            self._purge_locked(now)
            # Reopen must keep token+key: a new mint each /music play made the
            # phone auth the latest key against an older still-open page.
            if nick:
                live = [s for s in self._sessions.values() if s.nick == nick]
                if live:
                    session = max(live, key=lambda s: s.created)
                    session.start_index = start_index
                    session.created = now
                    return session
            session = MusicSession(
                token=_generate_token(),
                key=_generate_key(),
                start_index=start_index,
                nick=nick,
                created=now,
            )
            self._sessions[session.token] = session
            return session

    def get(self, token: str) -> Optional[MusicSession]:
        token = (token or "").strip()
        if not token:
            return None
        now = time.time()
        with self._lock:
            self._purge_locked(now)
            return self._sessions.get(token)

    def _purge_locked(self, now: float) -> None:
        expired = [
            tok
            for tok, sess in self._sessions.items()
            if now - sess.created > MUSIC_TTL_SECONDS
        ]
        for tok in expired:
            self._sessions.pop(tok, None)


music_store = MusicStore()
