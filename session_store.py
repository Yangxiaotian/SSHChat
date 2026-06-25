"""Persist in-progress room games and reconnect sessions across server restarts."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from typing import Any


PERSIST_VERSION = 1


class DisconnectedSeat:
    """Stand-in for a player TCP connection while offline or on disk."""

    __slots__ = ("nickname",)

    def __init__(self, nickname: str) -> None:
        self.nickname = (nickname or "").strip() or "?"

    def __repr__(self) -> str:
        return f"DisconnectedSeat({self.nickname!r})"


class FederatedSeat:
    """Player seated on a peer SSHChat node (federated game)."""

    __slots__ = ("node_id", "nickname")

    def __init__(self, node_id: str, nickname: str) -> None:
        self.node_id = (node_id or "").strip() or "?"
        self.nickname = (nickname or "").strip() or "?"

    def __repr__(self) -> str:
        return f"FederatedSeat({self.node_id!r}, {self.nickname!r})"


class GameSessionStore:
    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.RLock()

    def save(self, payload: dict[str, Any]) -> None:
        data = {"version": PERSIST_VERSION, **payload}
        with self._lock:
            directory = os.path.dirname(self.path) or "."
            os.makedirs(directory, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                prefix=".game-sessions-",
                suffix=".json",
                dir=directory,
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
                    f.write("\n")
                os.replace(tmp_path, self.path)
            finally:
                if os.path.exists(tmp_path):
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass

    def load(self) -> dict[str, Any] | None:
        with self._lock:
            if not os.path.exists(self.path):
                return None
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                return None
            if not isinstance(data, dict):
                return None
            if int(data.get("version") or 0) != PERSIST_VERSION:
                return None
            return data

    def reset(self) -> None:
        with self._lock:
            if os.path.exists(self.path):
                try:
                    os.unlink(self.path)
                except OSError:
                    pass
