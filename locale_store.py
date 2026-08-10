"""Per-nickname UI locale persistence."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from typing import Optional

from i18n import DEFAULT_LOCALE, Locale, default_locale, normalize_locale


class LocaleStore:
    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._data: dict[str, str] = {}
        self._load()

    def _normalize_user(self, name: str) -> str:
        return (name or "").strip().lower()

    def _load(self) -> None:
        with self._lock:
            if not os.path.exists(self.path):
                self._data = {}
                return
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
            except Exception:
                self._data = {}
                return
            users = raw.get("users") if isinstance(raw, dict) else None
            if not isinstance(users, dict):
                self._data = {}
                return
            out: dict[str, str] = {}
            for k, v in users.items():
                if isinstance(k, str) and isinstance(v, str):
                    out[self._normalize_user(k)] = normalize_locale(v)
            self._data = out

    def _save(self) -> None:
        with self._lock:
            directory = os.path.dirname(self.path) or "."
            os.makedirs(directory, exist_ok=True)
            payload = {
                "version": 1,
                "users": dict(sorted(self._data.items())),
            }
            fd, tmp_path = tempfile.mkstemp(
                prefix=".user-locales-",
                suffix=".json",
                dir=directory,
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
                    f.write("\n")
                os.replace(tmp_path, self.path)
            finally:
                if os.path.exists(tmp_path):
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass

    def get(self, nickname: str) -> Locale:
        key = self._normalize_user(nickname)
        with self._lock:
            return self._data.get(key) or default_locale()

    def set(self, nickname: str, locale: Locale) -> Locale:
        key = self._normalize_user(nickname)
        loc = normalize_locale(locale)
        with self._lock:
            self._data[key] = loc
            self._save()
        return loc
