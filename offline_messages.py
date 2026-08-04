"""Offline leave-messages (mailbox) persisted as JSON on disk."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from typing import Any


def _normalize_user(name: str) -> str:
    return (name or "").strip().lower()


# Cap abuse / disk growth; oldest messages are dropped when exceeded.
DEFAULT_MAX_PER_USER = 50
DEFAULT_MAX_TEXT_LEN = 500


class OfflineMessageStore:
    """Per-recipient offline PMs, delivered once when the user next comes online."""

    def __init__(
        self,
        path: str,
        *,
        max_per_user: int = DEFAULT_MAX_PER_USER,
        max_text_len: int = DEFAULT_MAX_TEXT_LEN,
    ) -> None:
        self.path = path
        self.max_per_user = max(1, int(max_per_user))
        self.max_text_len = max(1, int(max_text_len))
        self._lock = threading.RLock()
        self._cache: dict[str, Any] | None = None

    def _empty_data(self) -> dict[str, Any]:
        return {"version": 1, "mailboxes": {}}

    def _ensure_loaded_locked(self) -> None:
        if self._cache is not None:
            return
        if not os.path.exists(self.path):
            self._cache = self._empty_data()
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = self._empty_data()
        mailboxes = data.get("mailboxes")
        if not isinstance(mailboxes, dict):
            data = self._empty_data()
        self._cache = data

    def _save_locked(self) -> None:
        assert self._cache is not None
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            prefix=".offline-messages-",
            suffix=".json",
            dir=directory,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, ensure_ascii=False, indent=2, sort_keys=True)
                f.write("\n")
            os.replace(tmp_path, self.path)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def count(self, recipient: str) -> int:
        key = _normalize_user(recipient)
        if not key:
            return 0
        with self._lock:
            self._ensure_loaded_locked()
            assert self._cache is not None
            box = self._cache["mailboxes"].get(key)
            if not isinstance(box, list):
                return 0
            return len(box)

    def leave(self, recipient: str, sender: str, text: str) -> dict[str, Any] | None:
        """Enqueue a leave-message. Returns the stored entry, or None if invalid."""
        key = _normalize_user(recipient)
        sender_name = (sender or "").strip() or "?"
        body = (text or "").strip()
        if not key or not body:
            return None
        if len(body) > self.max_text_len:
            body = body[: self.max_text_len]
        entry = {
            "from": sender_name,
            "text": body,
            "ts": time.time(),
        }
        with self._lock:
            self._ensure_loaded_locked()
            assert self._cache is not None
            mailboxes = self._cache["mailboxes"]
            box = mailboxes.get(key)
            if not isinstance(box, list):
                box = []
            box.append(entry)
            if len(box) > self.max_per_user:
                box = box[-self.max_per_user :]
            mailboxes[key] = box
            self._save_locked()
            return dict(entry)

    def take_all(self, recipient: str) -> list[dict[str, Any]]:
        """Pop and return all pending messages for recipient (clears mailbox)."""
        key = _normalize_user(recipient)
        if not key:
            return []
        with self._lock:
            self._ensure_loaded_locked()
            assert self._cache is not None
            mailboxes = self._cache["mailboxes"]
            box = mailboxes.pop(key, None)
            if not isinstance(box, list) or not box:
                return []
            self._save_locked()
            out: list[dict[str, Any]] = []
            for item in box:
                if not isinstance(item, dict):
                    continue
                sender = str(item.get("from") or "?").strip() or "?"
                text = str(item.get("text") or "").strip()
                if not text:
                    continue
                try:
                    ts = float(item.get("ts", 0))
                except (TypeError, ValueError):
                    ts = 0.0
                out.append({"from": sender, "text": text, "ts": ts})
            return out
