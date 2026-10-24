"""Server-side music drop folder: list audio files under a directory."""
from __future__ import annotations

import os
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

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

    def mint(self, start_index: int, nick: str) -> MusicSession:
        now = time.time()
        session = MusicSession(
            token=_generate_token(),
            key=_generate_key(),
            start_index=max(1, int(start_index or 1)),
            nick=(nick or "").strip(),
            created=now,
        )
        with self._lock:
            self._purge_locked(now)
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
