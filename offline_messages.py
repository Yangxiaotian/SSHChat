"""Offline leave-messages (mailbox) persisted as JSON on disk."""

from __future__ import annotations

import json
import os
import secrets
import tempfile
import threading
import time
from typing import Any


def _normalize_user(name: str) -> str:
    return (name or "").strip().lower()


# Cap abuse / disk growth; oldest messages are dropped when exceeded.
DEFAULT_MAX_PER_USER = 50
DEFAULT_MAX_TEXT_LEN = 500
# After /leave recall or delivery, peers may catch-up re-seed; refuse for a while.
DEFAULT_CLEARED_TTL_SEC = 7 * 24 * 3600


class OfflineMessageStore:
    """Per-recipient offline PMs, delivered once when the user next comes online."""

    def __init__(
        self,
        path: str,
        *,
        max_per_user: int = DEFAULT_MAX_PER_USER,
        max_text_len: int = DEFAULT_MAX_TEXT_LEN,
        cleared_ttl_sec: int = DEFAULT_CLEARED_TTL_SEC,
    ) -> None:
        self.path = path
        self.max_per_user = max(1, int(max_per_user))
        self.max_text_len = max(1, int(max_text_len))
        self.cleared_ttl_sec = max(60, int(cleared_ttl_sec))
        self._lock = threading.RLock()
        self._cache: dict[str, Any] | None = None

    def _empty_data(self) -> dict[str, Any]:
        return {"version": 1, "mailboxes": {}, "cleared": {"pm": {}, "file": {}}}

    def _cleared_maps_locked(self) -> tuple[dict[str, float], dict[str, float]]:
        assert self._cache is not None
        raw = self._cache.get("cleared")
        if not isinstance(raw, dict):
            raw = {"pm": {}, "file": {}}
            self._cache["cleared"] = raw
        pm = raw.get("pm")
        fl = raw.get("file")
        if not isinstance(pm, dict):
            pm = {}
            raw["pm"] = pm
        if not isinstance(fl, dict):
            fl = {}
            raw["file"] = fl
        return pm, fl  # type: ignore[return-value]

    def _prune_cleared_locked(self) -> bool:
        """Drop expired tombstones. Returns True if anything changed."""
        now = time.time()
        pm, fl = self._cleared_maps_locked()
        dirty = False
        for bucket in (pm, fl):
            for key, exp in list(bucket.items()):
                try:
                    expiry = float(exp)
                except (TypeError, ValueError):
                    bucket.pop(key, None)
                    dirty = True
                    continue
                if expiry <= now:
                    bucket.pop(key, None)
                    dirty = True
        return dirty

    @staticmethod
    def _cleared_key(recipient: str, item_id: str) -> str:
        return f"{_normalize_user(recipient)}\t{str(item_id or '').strip()}"

    def _mark_cleared_pm_locked(self, recipient: str, leave_id: str) -> bool:
        lid = str(leave_id or "").strip()
        if not _normalize_user(recipient) or not lid:
            return False
        pm, _ = self._cleared_maps_locked()
        pm[self._cleared_key(recipient, lid)] = time.time() + self.cleared_ttl_sec
        return True

    def _mark_cleared_file_locked(self, recipient: str, transfer_id: str) -> bool:
        tid = str(transfer_id or "").strip()
        if not _normalize_user(recipient) or not tid:
            return False
        _, fl = self._cleared_maps_locked()
        fl[self._cleared_key(recipient, tid)] = time.time() + self.cleared_ttl_sec
        return True

    def _is_cleared_pm_locked(self, recipient: str, leave_id: str) -> bool:
        lid = str(leave_id or "").strip()
        if not lid:
            return False
        self._prune_cleared_locked()
        pm, _ = self._cleared_maps_locked()
        return self._cleared_key(recipient, lid) in pm

    def _is_cleared_file_locked(self, recipient: str, transfer_id: str) -> bool:
        tid = str(transfer_id or "").strip()
        if not tid:
            return False
        self._prune_cleared_locked()
        _, fl = self._cleared_maps_locked()
        return self._cleared_key(recipient, tid) in fl

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
        if "cleared" not in data or not isinstance(data.get("cleared"), dict):
            data["cleared"] = {"pm": {}, "file": {}}
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

    @staticmethod
    def _parse_entry(item: Any) -> dict[str, Any] | None:
        if not isinstance(item, dict):
            return None
        sender = str(item.get("from") or "?").strip() or "?"
        text = str(item.get("text") or "").strip()
        if not text:
            return None
        try:
            ts = float(item.get("ts", 0))
        except (TypeError, ValueError):
            ts = 0.0
        kind = str(item.get("kind") or "pm").strip() or "pm"
        out: dict[str, Any] = {"from": sender, "text": text, "ts": ts, "kind": kind}
        leave_id = str(item.get("id") or "").strip()
        if leave_id:
            out["id"] = leave_id
        meta = item.get("meta")
        if isinstance(meta, dict):
            out["meta"] = dict(meta)
        return out

    @staticmethod
    def _content_dedupe_key(entry: dict[str, Any]) -> tuple[str, str, str, str]:
        """Identity for federated re-seeds that may arrive with different leave ids."""
        kind = str(entry.get("kind") or "pm").strip() or "pm"
        sender = _normalize_user(str(entry.get("from") or ""))
        text = str(entry.get("text") or "").strip()
        tid = ""
        if kind == "file":
            meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
            tid = str(meta.get("transfer_id") or "").strip()
        return (kind, sender, text, tid)

    def leave(
        self,
        recipient: str,
        sender: str,
        text: str,
        *,
        kind: str = "pm",
        meta: dict[str, Any] | None = None,
        leave_id: str | None = None,
        ts: float | None = None,
    ) -> dict[str, Any] | None:
        """Enqueue a leave-message. Returns the stored entry, or None if invalid.

        kind=\"file\" stores an offline file notice (see meta for transfer details).
        File summaries are not truncated by max_text_len so listing stays readable.
        ``ts`` preserves the origin timestamp when federation re-seeds a leave.
        """
        key = _normalize_user(recipient)
        sender_name = (sender or "").strip() or "?"
        body = (text or "").strip()
        if not key or not body:
            return None
        msg_kind = (kind or "pm").strip() or "pm"
        if msg_kind != "file" and len(body) > self.max_text_len:
            body = body[: self.max_text_len]
        lid = str(leave_id or "").strip() or secrets.token_hex(8)
        try:
            entry_ts = float(ts) if ts is not None else time.time()
        except (TypeError, ValueError):
            entry_ts = time.time()
        entry: dict[str, Any] = {
            "id": lid,
            "from": sender_name,
            "to_display": (recipient or "").strip() or key,
            "text": body,
            "ts": entry_ts,
            "kind": msg_kind,
        }
        if isinstance(meta, dict) and meta:
            entry["meta"] = dict(meta)
        content_key = self._content_dedupe_key(entry)
        with self._lock:
            self._ensure_loaded_locked()
            assert self._cache is not None
            # Reject federation catch-up after /leave recall or delivery.
            if self._is_cleared_pm_locked(key, lid):
                return None
            if msg_kind == "file" and isinstance(meta, dict):
                tid = str(meta.get("transfer_id") or "").strip()
                if tid and self._is_cleared_file_locked(key, tid):
                    return None
            mailboxes = self._cache["mailboxes"]
            box = mailboxes.get(key)
            if not isinstance(box, list):
                box = []
            # File leaves are keyed by transfer_id so federated seeding is idempotent.
            if msg_kind == "file" and isinstance(meta, dict):
                tid = str(meta.get("transfer_id") or "").strip()
                if tid:
                    for existing in box:
                        if not isinstance(existing, dict):
                            continue
                        if str(existing.get("kind") or "") != "file":
                            continue
                        em = existing.get("meta")
                        if (
                            isinstance(em, dict)
                            and str(em.get("transfer_id") or "").strip() == tid
                        ):
                            return dict(existing)
            for existing in box:
                if not isinstance(existing, dict):
                    continue
                # Same federated id → idempotent.
                if str(existing.get("id") or "").strip() == lid:
                    return dict(existing)
                # Catch-up / dual-node seeds often mint different ids for one leave.
                parsed = self._parse_entry(existing)
                if parsed is None:
                    continue
                if self._content_dedupe_key(parsed) == content_key:
                    return dict(existing)
            box.append(entry)
            if len(box) > self.max_per_user:
                box = box[-self.max_per_user :]
            mailboxes[key] = box
            self._save_locked()
            return dict(entry)

    def compact_duplicates(self) -> int:
        """Drop duplicate pending leaves (by id and by content). Returns removed count."""
        removed = 0
        with self._lock:
            self._ensure_loaded_locked()
            assert self._cache is not None
            mailboxes = self._cache["mailboxes"]
            dirty = False
            for to_key, box in list(mailboxes.items()):
                if not isinstance(box, list) or not box:
                    continue
                seen_ids: set[str] = set()
                seen_content: set[tuple[str, str, str, str]] = set()
                kept: list[Any] = []
                for item in box:
                    parsed = self._parse_entry(item)
                    if parsed is None:
                        removed += 1
                        dirty = True
                        continue
                    if not isinstance(item, dict):
                        item = dict(parsed)
                        dirty = True
                    lid = str(item.get("id") or parsed.get("id") or "").strip()
                    if not lid:
                        lid = secrets.token_hex(8)
                        item = dict(item)
                        item["id"] = lid
                        dirty = True
                    elif "id" not in item:
                        item = dict(item)
                        item["id"] = lid
                        dirty = True
                    content_key = self._content_dedupe_key(parsed)
                    if lid in seen_ids or content_key in seen_content:
                        removed += 1
                        dirty = True
                        continue
                    seen_ids.add(lid)
                    seen_content.add(content_key)
                    kept.append(item)
                if kept:
                    mailboxes[to_key] = kept
                else:
                    mailboxes.pop(to_key, None)
                    dirty = True
            if dirty:
                self._save_locked()
        return removed

    def snapshot_pending(self) -> list[dict[str, Any]]:
        """Copy every pending leave for federation catch-up (idempotent re-seed)."""
        self.compact_duplicates()
        with self._lock:
            self._ensure_loaded_locked()
            assert self._cache is not None
            out: list[dict[str, Any]] = []
            mailboxes = self._cache["mailboxes"]
            for to_key in sorted(k for k in mailboxes if isinstance(k, str)):
                box = mailboxes.get(to_key)
                if not isinstance(box, list):
                    continue
                for item in box:
                    parsed = self._parse_entry(item)
                    if parsed is None:
                        continue
                    display_to = to_key
                    if isinstance(item, dict):
                        raw_to = str(item.get("to_display") or "").strip()
                        if raw_to:
                            display_to = raw_to
                    row = {
                        "to": display_to,
                        "from": parsed["from"],
                        "text": parsed["text"],
                        "ts": parsed["ts"],
                        "kind": parsed.get("kind") or "pm",
                    }
                    lid = str(parsed.get("id") or "").strip()
                    if not lid and isinstance(item, dict):
                        lid = str(item.get("id") or "").strip()
                    if lid:
                        row["id"] = lid
                    if "meta" in parsed:
                        row["meta"] = parsed["meta"]
                    out.append(row)
            return out

    def remove_file_by_transfer(
        self, recipient: str, transfer_id: str
    ) -> list[dict[str, Any]]:
        """Remove pending file leave(s) for recipient matching transfer_id.

        Always tombstones the transfer id so a late fleave catch-up cannot restore it.
        """
        key = _normalize_user(recipient)
        tid = str(transfer_id or "").strip()
        if not key or not tid:
            return []
        with self._lock:
            self._ensure_loaded_locked()
            assert self._cache is not None
            self._mark_cleared_file_locked(key, tid)
            mailboxes = self._cache["mailboxes"]
            box = mailboxes.get(key)
            if not isinstance(box, list) or not box:
                self._save_locked()
                return []
            kept: list[Any] = []
            removed: list[dict[str, Any]] = []
            for item in box:
                parsed = self._parse_entry(item)
                if parsed is None:
                    continue
                if (parsed.get("kind") or "pm") != "file":
                    kept.append(item)
                    continue
                meta = parsed.get("meta") if isinstance(parsed.get("meta"), dict) else {}
                if str(meta.get("transfer_id") or "").strip() == tid:
                    lid = str(parsed.get("id") or "").strip()
                    if lid:
                        self._mark_cleared_pm_locked(key, lid)
                    removed.append(parsed)
                else:
                    kept.append(item)
            if kept:
                mailboxes[key] = kept
            else:
                mailboxes.pop(key, None)
            self._save_locked()
            return removed

    def list_sent_unread(
        self,
        sender: str,
        recipient: str | None = None,
    ) -> list[dict[str, Any]]:
        """Unread leave-messages from sender, optionally only to one recipient.

        Each item: {to, from, text, ts, index} where index is 1-based among
        messages from this sender to that recipient (for /leave recall).
        """
        sender_key = _normalize_user(sender)
        if not sender_key:
            return []
        only_to = _normalize_user(recipient) if recipient else ""
        with self._lock:
            self._ensure_loaded_locked()
            assert self._cache is not None
            mailboxes = self._cache["mailboxes"]
            out: list[dict[str, Any]] = []
            keys = (
                [only_to]
                if only_to
                else sorted(k for k in mailboxes if isinstance(k, str))
            )
            for to_key in keys:
                if not to_key:
                    continue
                box = mailboxes.get(to_key)
                if not isinstance(box, list):
                    continue
                idx = 0
                for item in box:
                    parsed = self._parse_entry(item)
                    if parsed is None:
                        continue
                    if _normalize_user(parsed["from"]) != sender_key:
                        continue
                    idx += 1
                    display_to = to_key
                    if isinstance(item, dict):
                        raw_to = str(item.get("to_display") or "").strip()
                        if raw_to:
                            display_to = raw_to
                    row = {
                        "to": display_to,
                        "from": parsed["from"],
                        "text": parsed["text"],
                        "ts": parsed["ts"],
                        "index": idx,
                        "kind": parsed.get("kind") or "pm",
                    }
                    if "meta" in parsed:
                        row["meta"] = parsed["meta"]
                    out.append(row)
            return out

    def recall(
        self,
        sender: str,
        recipient: str,
        index: int,
    ) -> dict[str, Any] | None:
        """Remove the index-th (1-based) unread message from sender to recipient."""
        sender_key = _normalize_user(sender)
        to_key = _normalize_user(recipient)
        try:
            want = int(index)
        except (TypeError, ValueError):
            return None
        if not sender_key or not to_key or want < 1:
            return None
        with self._lock:
            self._ensure_loaded_locked()
            assert self._cache is not None
            mailboxes = self._cache["mailboxes"]
            box = mailboxes.get(to_key)
            if not isinstance(box, list) or not box:
                return None
            seen = 0
            remove_at = -1
            removed: dict[str, Any] | None = None
            for i, item in enumerate(box):
                parsed = self._parse_entry(item)
                if parsed is None:
                    continue
                if _normalize_user(parsed["from"]) != sender_key:
                    continue
                seen += 1
                if seen == want:
                    remove_at = i
                    removed = {
                        "to": to_key,
                        "from": parsed["from"],
                        "text": parsed["text"],
                        "ts": parsed["ts"],
                        "index": want,
                        "kind": parsed.get("kind") or "pm",
                    }
                    if parsed.get("id"):
                        removed["id"] = parsed["id"]
                    if "meta" in parsed:
                        removed["meta"] = parsed["meta"]
                    break
            if remove_at < 0 or removed is None:
                return None
            del box[remove_at]
            if box:
                mailboxes[to_key] = box
            else:
                mailboxes.pop(to_key, None)
            lid = str(removed.get("id") or "").strip()
            if lid:
                self._mark_cleared_pm_locked(to_key, lid)
            if (removed.get("kind") or "pm") == "file":
                meta = removed.get("meta") if isinstance(removed.get("meta"), dict) else {}
                tid = str(meta.get("transfer_id") or "").strip()
                if tid:
                    self._mark_cleared_file_locked(to_key, tid)
            self._save_locked()
            return removed

    def remove_by_id(self, recipient: str, leave_id: str) -> dict[str, Any] | None:
        """Remove one pending leave by federated id.

        Always tombstones the id so a late pleave catch-up cannot restore it.
        """
        key = _normalize_user(recipient)
        lid = str(leave_id or "").strip()
        if not key or not lid:
            return None
        with self._lock:
            self._ensure_loaded_locked()
            assert self._cache is not None
            self._mark_cleared_pm_locked(key, lid)
            mailboxes = self._cache["mailboxes"]
            box = mailboxes.get(key)
            if not isinstance(box, list) or not box:
                self._save_locked()
                return None
            for i, item in enumerate(box):
                if not isinstance(item, dict):
                    continue
                if str(item.get("id") or "").strip() != lid:
                    continue
                parsed = self._parse_entry(item)
                del box[i]
                if box:
                    mailboxes[key] = box
                else:
                    mailboxes.pop(key, None)
                if parsed and (parsed.get("kind") or "pm") == "file":
                    meta = (
                        parsed.get("meta")
                        if isinstance(parsed.get("meta"), dict)
                        else {}
                    )
                    tid = str(meta.get("transfer_id") or "").strip()
                    if tid:
                        self._mark_cleared_file_locked(key, tid)
                self._save_locked()
                return parsed
            self._save_locked()
            return None

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
            out: list[dict[str, Any]] = []
            for item in box:
                parsed = self._parse_entry(item)
                if parsed is None:
                    continue
                out.append(parsed)
                lid = str(parsed.get("id") or "").strip()
                if lid:
                    self._mark_cleared_pm_locked(key, lid)
                if (parsed.get("kind") or "pm") == "file":
                    meta = (
                        parsed.get("meta")
                        if isinstance(parsed.get("meta"), dict)
                        else {}
                    )
                    tid = str(meta.get("transfer_id") or "").strip()
                    if tid:
                        self._mark_cleared_file_locked(key, tid)
            self._save_locked()
            return out
