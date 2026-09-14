"""Shared helpers for SSHChat local clients (CLI launcher + GUI)."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

# /join|/switch|/part take a bare room name.
_ROOM_ARG_CMDS = frozenset({"/join", "/switch", "/part"})
# /msg|/sendfile|/file take a nick or #room.
_USER_OR_ROOM_ARG_CMDS = frozenset({"/msg", "/sendfile", "/file"})
# /leave|/unmsg take a nick (leave-message / recall).
_USER_ARG_CMDS = frozenset({"/leave", "/unmsg"})
_ROOMS_LIST_RE = re.compile(r"Rooms:\s*(.*)$", re.I)
_ROOM_TOKEN_RE = re.compile(r"\*?#([a-zA-Z0-9_-]{1,32})")
_NAMES_LINE_RE = re.compile(r"^\[\*]\s+#([^\s(]+)\s+\(\d+\):\s*(.*)$", re.I)
_ROOM_CHAT_RE = re.compile(r"^\[#([^\]]+)\]\s+\[([^\]]+)\]")
_PM_FROM_RE = re.compile(r"^\[PM from ([^\]]+)\]", re.I)
_SYSTEM_SENDERS = frozenset({"+", "-", "*", "!", "OK", "ERROR", "INFO", "WARN", "WARNING", "DEBUG", "HINT"})


def name_arg_completions(
    text: str,
    *,
    rooms: Sequence[str] = (),
    users: Sequence[str] = (),
) -> list[str]:
    """Full replacement strings for room/nick args on /join|/msg|/sendfile|/leave …"""
    if not text.startswith("/"):
        return []
    trailing_space = text.endswith(" ")
    parts = text.split()
    if not parts:
        return []
    cmd = parts[0].lower()
    room_names = _uniq_keep_order(
        r.strip().lstrip("#") for r in rooms if isinstance(r, str) and r.strip()
    )
    user_names = _uniq_keep_order(
        u.strip() for u in users if isinstance(u, str) and u.strip()
    )

    if cmd in _ROOM_ARG_CMDS:
        cands = room_names
    elif cmd in _USER_OR_ROOM_ARG_CMDS:
        cands = list(user_names) + [f"#{r}" for r in room_names]
    elif cmd in _USER_ARG_CMDS:
        cands = user_names
    else:
        return []

    if trailing_space and len(parts) == 1:
        return [f"{parts[0]} {c}" for c in cands]
    if len(parts) >= 2 and not trailing_space:
        prefix = parts[1]
        pl = prefix.lower()
        bare = pl.lstrip("#")
        matched: list[str] = []
        for c in cands:
            cl = c.lower()
            if pl == "#":
                if c.startswith("#"):
                    matched.append(c)
                continue
            if cl.startswith(pl):
                matched.append(c)
            elif c.startswith("#") and c[1:].lower().startswith(bare):
                matched.append(c)
            elif not c.startswith("#") and cl.startswith(bare) and prefix.startswith("#"):
                # `/join #def` → still offer bare room name `default`
                matched.append(c)
        return [f"{parts[0]} {c}" for c in matched]
    return []


def extract_rooms_from_text(text: str) -> list[str]:
    """Rooms from a `Rooms: #a, *#b` system body (or full line)."""
    t = text.strip()
    m = _ROOMS_LIST_RE.search(t)
    if not m:
        return []
    return _uniq_keep_order(_ROOM_TOKEN_RE.findall(m.group(1)))


def extract_names_members(text: str) -> tuple[str, list[str]] | None:
    """Parse `[*] #room (n): a, b` → (room, members)."""
    m = _NAMES_LINE_RE.match(text.strip())
    if not m:
        return None
    room = m.group(1).strip()
    tail = m.group(2).strip()
    if not tail or tail.lower() == "(empty)":
        return room, []
    members = [x.strip() for x in tail.split(",") if x.strip()]
    return room, members


def extract_completion_hints(text: str) -> tuple[list[str], list[str]]:
    """Best-effort rooms/users from one wire/display line for completion caches."""
    rooms: list[str] = []
    users: list[str] = []
    t = text.strip()
    rooms.extend(extract_rooms_from_text(t))
    names = extract_names_members(t)
    if names:
        room, members = names
        if room:
            rooms.append(room)
        users.extend(members)
    m = _ROOM_CHAT_RE.match(t)
    if m:
        rooms.append(m.group(1))
        sender = m.group(2)
        if sender and sender not in _SYSTEM_SENDERS:
            users.append(sender)
    m = _PM_FROM_RE.match(t)
    if m:
        users.append(m.group(1).strip())
    return _uniq_keep_order(rooms), _uniq_keep_order(users)


def _uniq_keep_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in items:
        key = raw.strip()
        if not key:
            continue
        low = key.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(key)
    return out

# PyInstaller sets sys.frozen to True for frozen apps.
def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def default_client_config_path() -> Path:
    env = (os.environ.get("SSHCHAT_CLIENT_CONFIG") or "").strip()
    if env:
        return Path(env).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / "SSHChat" / "client.json"
    return Path.home() / ".config" / "sshchat" / "client.json"


def load_client_config(path: Path) -> dict[str, Any] | None:
    path = path.expanduser().resolve()
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def save_client_config(path: Path, data: dict[str, Any]) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def bundled_site_search_paths() -> list[Path]:
    """Paths checked in order for deploy-time client-bundle.json (embedded or sidecar)."""
    paths: list[Path] = []
    env = (os.environ.get("SSHCHAT_BUNDLE_FILE") or "").strip()
    if env:
        paths.append(Path(env).expanduser())
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        paths.append(Path(meipass) / "client-bundle.json")
    if _is_frozen():
        paths.append(Path(sys.executable).resolve().parent / "client-bundle.json")
    here = Path(__file__).resolve().parent
    paths.append(here / "client-bundle.json")
    return paths


def load_bundled_site_config() -> dict[str, Any] | None:
    """
    End-user SSH target only: hostname/IP + sshd port for "ssh user@host -p port".
    Not SSHCHAT_SERVER / chat TCP (often 127.0.0.1 on the server). When present, GUI
    installers hide these fields and only ask for the Linux username (key must match
    authorized_keys).
    """
    for p in bundled_site_search_paths():
        cfg = load_client_config(p)
        if not cfg:
            continue
        host = cfg.get("host")
        if not isinstance(host, str) or not host.strip():
            continue
        port = cfg.get("ssh_port", 22)
        try:
            port_n = int(port)
        except (TypeError, ValueError):
            port_n = 22
        out = dict(cfg)
        out["host"] = host.strip()
        out["ssh_port"] = port_n
        return out
    return None
