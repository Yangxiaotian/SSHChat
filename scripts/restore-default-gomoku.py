#!/usr/bin/env python3
"""Restore default-room gomoku from parked slot or a backup game_sessions.json."""
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


def _pick_gomoku(data: dict, slot: str, room: str) -> tuple[str | None, str | None]:
    games = data.get(slot) or {}
    for key in (room, f"#{room}"):
        blob = games.get(key)
        if not blob:
            continue
        try:
            game = _decode(blob)
        except Exception:
            continue
        if getattr(game, "name", "") == "gomoku":
            return key, blob
    return None, None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", default="/opt/sshchat")
    parser.add_argument("--room", default="default")
    parser.add_argument(
        "--from-backup",
        default="",
        help="Optional backup game_sessions.json (uses active gomoku if more plies)",
    )
    parser.add_argument(
        "--prefer",
        choices=("parked", "backup-active", "most-plies"),
        default="most-plies",
        help="Which source wins when multiple exist (default: most-plies)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    path = Path(args.prefix) / "game_sessions.json"
    if not path.is_file():
        print(f"error: missing {path}", file=sys.stderr)
        return 1

    sys.path.insert(0, str(Path(args.prefix).resolve()))

    data = json.loads(path.read_text(encoding="utf-8"))
    room = args.room.strip().lstrip("#")

    sources: list[tuple[str, str, int]] = []
    _, parked_blob = _pick_gomoku(data, "room_games_parked", room)
    if parked_blob:
        sources.append(("parked", parked_blob, _plies(_decode(parked_blob))))

    active_key, active_blob = _pick_gomoku(data, "room_games", room)
    if active_blob:
        sources.append(("active", active_blob, _plies(_decode(active_blob))))

    if args.from_backup:
        bak_path = Path(args.from_backup)
        if not bak_path.is_file():
            print(f"error: missing backup {bak_path}", file=sys.stderr)
            return 1
        bak = json.loads(bak_path.read_text(encoding="utf-8"))
        _, bak_active = _pick_gomoku(bak, "room_games", room)
        if bak_active:
            sources.append(
                ("backup-active", bak_active, _plies(_decode(bak_active)))
            )
        _, bak_parked = _pick_gomoku(bak, "room_games_parked", room)
        if bak_parked:
            sources.append(
                ("backup-parked", bak_parked, _plies(_decode(bak_parked)))
            )

    if not sources:
        print(f"error: no gomoku found for room {room!r}", file=sys.stderr)
        return 1

    print("candidates:")
    for label, _blob, pl in sources:
        print(f"  {label}: {pl} plies")

    if args.prefer == "parked":
        chosen = next(s for s in sources if s[0] == "parked")
    elif args.prefer == "backup-active":
        chosen = next(
            (s for s in sources if s[0] == "backup-active"),
            max(sources, key=lambda s: s[2]),
        )
    else:
        chosen = max(sources, key=lambda s: s[2])

    label, blob, plies = chosen
    game = _decode(blob)
    last = getattr(game, "_history", [None])[-1]
    print(f"selected: {label} ({plies} plies, last move {last})")

    if args.dry_run:
        print("dry-run: no changes written")
        return 0

    room_games = dict(data.get("room_games") or {})
    parked_games = dict(data.get("room_games_parked") or {})
    authority = dict(data.get("room_game_authority") or {})
    tokens = dict(data.get("room_game_tokens") or {})

    # Keep previous active as parked when promoting something else.
    if active_blob and blob != active_blob:
        parked_games[room] = active_blob
    if label == "parked":
        parked_games.pop(room, None)
        parked_games.pop(f"#{room}", None)

    room_games[room] = _encode(game)
    room_games.pop(f"#{room}", None)
    auth = (
        authority.get(room)
        or authority.get(f"#{room}")
        or socket.gethostname()
    )
    authority[room] = str(auth).strip()
    authority.pop(f"#{room}", None)
    tokens[room] = secrets.token_hex(16)
    tokens.pop(f"#{room}", None)

    stamp = int(time.time())
    backup = path.with_suffix(f".json.bak-restore-{stamp}")
    shutil.copy2(path, backup)

    data["room_games"] = room_games
    data["room_games_parked"] = parked_games
    data["room_game_authority"] = authority
    data["room_game_tokens"] = tokens
    path.write_text(json.dumps(data), encoding="utf-8")
    print(f"wrote {path} (backup {backup})")
    print("restart sshchat server, then /game show in #default")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
