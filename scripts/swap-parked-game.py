#!/usr/bin/env python3
"""Swap active ↔ parked game for a room in game_sessions.json.

Use when federation parked a darkchess under an active doushou (or similar)
and /game restore refuses because the room is busy.

  sudo python3 scripts/swap-parked-game.py --prefix /opt/sshchat --room default
  sudo systemctl restart sshchat   # Linux
  # macOS: sudo launchctl kickstart -k system/com.sshchat.server
"""
from __future__ import annotations

import argparse
import base64
import json
import pickle
import secrets
import shutil
import socket
import sys
import time
from pathlib import Path


def _aliases(room: str) -> list[str]:
    room = room.strip().lstrip("#")
    return list(dict.fromkeys([room, f"#{room}"]))


def _pick(d: dict, room: str) -> tuple[str | None, object | None]:
    for key in _aliases(room):
        if key in d:
            return key, d[key]
    return None, None


def _decode(blob: str):
    return pickle.loads(base64.b64decode(blob))


def _encode(obj) -> str:
    return base64.b64encode(pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)).decode(
        "ascii"
    )


def _parked_list(parked) -> list:
    if parked is None:
        return []
    if isinstance(parked, list):
        return [g for g in parked if g is not None]
    return [parked]


def _best(parked):
    active = [g for g in _parked_list(parked) if getattr(g, "state", "ended") != "ended"]
    if not active:
        return None
    def score(g):
        face = getattr(g, "face_up", None)
        n = len(face) if isinstance(face, (set, list, tuple)) else 0
        hist = getattr(g, "_history", None)
        if isinstance(hist, list):
            n = max(n, len(hist))
        return (n, float(getattr(g, "session_updated_at", 0) or 0))
    return max(active, key=score)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", default="/opt/sshchat")
    parser.add_argument("--room", default="default")
    parser.add_argument("--prefer", default="", help="Prefer parked game name (e.g. darkchess)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    path = Path(args.prefix) / "game_sessions.json"
    if not path.is_file():
        print(f"error: missing {path}", file=sys.stderr)
        return 1

    data = json.loads(path.read_text(encoding="utf-8"))
    room_games = dict(data.get("room_games") or {})
    parked_games = dict(data.get("room_games_parked") or {})
    authority = dict(data.get("room_game_authority") or {})
    tokens = dict(data.get("room_game_tokens") or {})

    active_key, active_blob = _pick(room_games, args.room)
    parked_key, parked_blob = _pick(parked_games, args.room)
    if not parked_blob:
        print(f"error: no parked game for room {args.room!r}", file=sys.stderr)
        return 1

    active = _decode(active_blob) if active_blob else None
    parked_obj = _decode(parked_blob)
    prefer = (args.prefer or "").strip()
    if prefer:
        candidates = [
            g
            for g in _parked_list(parked_obj)
            if getattr(g, "name", None) == prefer
            and getattr(g, "state", "ended") != "ended"
        ]
        parked = candidates[0] if candidates else _best(parked_obj)
    else:
        parked = _best(parked_obj)
    if parked is None:
        print("error: parked slot has no active game", file=sys.stderr)
        return 1

    print(
        f"room {args.room!r}: active="
        f"{getattr(active, 'name', None)}/{getattr(active, 'state', None)} "
        f"↔ parked={getattr(parked, 'name', None)}/{getattr(parked, 'state', None)} "
        f"face_up={len(getattr(parked, 'face_up', set()) or set())}"
    )
    if args.dry_run:
        return 0

    bak = path.with_name(
        f"{path.name}.bak-swap-{time.strftime('%Y%m%d%H%M%S')}"
    )
    shutil.copy2(path, bak)
    print(f"backup {bak}")

    remaining = [g for g in _parked_list(parked_obj) if g is not parked]
    if active is not None and getattr(active, "state", "ended") != "ended":
        remaining.append(active)

    canon = args.room.strip().lstrip("#")
    for alias in _aliases(canon):
        room_games.pop(alias, None)
        parked_games.pop(alias, None)
        authority.pop(alias, None)
        tokens.pop(alias, None)

    room_games[canon] = _encode(parked)
    if remaining:
        parked_games[canon] = _encode(remaining if len(remaining) > 1 else remaining[0])
    authority[canon] = socket.gethostname()
    tokens[canon] = secrets.token_hex(16)

    data["room_games"] = room_games
    data["room_games_parked"] = parked_games
    data["room_game_authority"] = authority
    data["room_game_tokens"] = tokens
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"wrote {path}: active now {getattr(parked, 'name', '?')}; "
        f"restart sshchat to load"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
