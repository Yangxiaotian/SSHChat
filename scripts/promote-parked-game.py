#!/usr/bin/env python3
"""Promote a parked room game to active when it is the newer/shorter session.

Used after a federation partition left a stale long fork active and the
/game new session parked. Backs up game_sessions.json before writing.
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


def _plies(game: object) -> int:
    hist = getattr(game, "_history", None)
    if isinstance(hist, list):
        return len(hist)
    ply = getattr(game, "_xq_ply_log", None)
    if isinstance(ply, list):
        return len(ply)
    return 0


def _decode(blob: str) -> object:
    return pickle.loads(base64.b64decode(blob))


def _encode(game: object) -> str:
    return base64.b64encode(pickle.dumps(game)).decode("ascii")


def _aliases(room: str) -> list[str]:
    room = room.strip().lstrip("#")
    return list(dict.fromkeys([room, f"#{room}"]))


def _pick(d: dict, room: str) -> tuple[str | None, str | None]:
    for key in _aliases(room):
        if key in d:
            return key, d[key]
    return None, None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prefix",
        default="/opt/sshchat",
        help="SSHChat install prefix (default: /opt/sshchat)",
    )
    parser.add_argument(
        "--room",
        default="default",
        help="Room name without # (default: default)",
    )
    parser.add_argument(
        "--authority",
        default="",
        help="Set room_game_authority after promote (default: hostname)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions without writing",
    )
    args = parser.parse_args()

    path = Path(args.prefix) / "game_sessions.json"
    if not path.is_file():
        print(f"error: missing {path}", file=sys.stderr)
        return 1

    sys.path.insert(0, str(args.prefix))
    data = json.loads(path.read_text(encoding="utf-8"))
    room_games: dict = dict(data.get("room_games") or {})
    parked_games: dict = dict(data.get("room_games_parked") or {})
    authority: dict = dict(data.get("room_game_authority") or {})
    tokens: dict = dict(data.get("room_game_tokens") or {})

    active_key, active_blob = _pick(room_games, args.room)
    parked_key, parked_blob = _pick(parked_games, args.room)
    if not active_blob or not parked_blob:
        print(
            f"error: need both active and parked games for room {args.room!r} "
            f"(active={active_key!r}, parked={parked_key!r})",
            file=sys.stderr,
        )
        return 1

    active = _decode(active_blob)
    parked = _decode(parked_blob)
    active_plies = _plies(active)
    parked_plies = _plies(parked)
    print(
        f"room {args.room!r}: active {active_plies} plies ({active_key!r}), "
        f"parked {parked_plies} plies ({parked_key!r})"
    )

    if parked_plies >= active_plies:
        print(
            "error: parked game is not shorter — refusing to promote "
            "(use manual edit if this is intentional)",
            file=sys.stderr,
        )
        return 1

    canon = args.room.strip().lstrip("#")
    for alias in _aliases(canon):
        room_games.pop(alias, None)
        parked_games.pop(alias, None)
        authority.pop(alias, None)
        tokens.pop(alias, None)

    room_games[canon] = _encode(parked)
    parked_games[canon] = _encode(active)
    auth = (args.authority or socket.gethostname()).strip()
    authority[canon] = auth
    tokens[canon] = secrets.token_hex(16)

    print(
        f"promote: active -> {parked_plies} plies, "
        f"parked -> {active_plies} plies, authority={auth!r}"
    )

    if args.dry_run:
        print("dry-run: no changes written")
        return 0

    backup = path.with_suffix(f".json.bak-promote-{int(time.time())}")
    shutil.copy2(path, backup)
    data["room_games"] = room_games
    data["room_games_parked"] = parked_games
    data["room_game_authority"] = authority
    data["room_game_tokens"] = tokens
    path.write_text(json.dumps(data), encoding="utf-8")
    print(f"wrote {path} (backup {backup})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
