from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from typing import Any, Callable, Optional


def _normalize_user(name: str) -> str:
    return (name or "").strip().lower()


def _chess_level(rating: int) -> str:
    if rating >= 2500:
        return "GM线"
    if rating >= 2400:
        return "IM线"
    if rating >= 2300:
        return "FM线"
    if rating >= 2200:
        return "CM线"
    if rating >= 2000:
        return "Expert"
    if rating >= 1800:
        return "Class A"
    if rating >= 1600:
        return "Class B"
    if rating >= 1400:
        return "Class C"
    if rating >= 1200:
        return "Class D"
    return "Beginner"


_LEVEL_LABEL_EN: dict[str, str] = {
    "GM线": "GM",
    "IM线": "IM",
    "FM线": "FM",
    "CM线": "CM",
    "业余7段": "Amateur 7 dan",
    "业余6段": "Amateur 6 dan",
    "业余5段": "Amateur 5 dan",
    "业余4段": "Amateur 4 dan",
    "业余3段": "Amateur 3 dan",
    "业余2段": "Amateur 2 dan",
    "业余1段": "Amateur 1 dan",
    "1级": "1 kyu",
    "2级": "2 kyu",
    "4级": "4 kyu",
    "6级": "6 kyu",
    "8级": "8 kyu",
    "10级": "10 kyu",
    "12级": "12 kyu",
    "道祖": "Dao Ancestor",
    "大罗大圆满": "Daluo (Peak)",
    "大罗后期": "Daluo (Late)",
    "大罗中期": "Daluo (Mid)",
    "大罗初期": "Daluo (Early)",
    "太乙大圆满": "Taiyi (Peak)",
    "太乙后期": "Taiyi (Late)",
    "太乙中期": "Taiyi (Mid)",
    "太乙初期": "Taiyi (Early)",
    "金仙大圆满": "Golden Immortal (Peak)",
    "金仙后期": "Golden Immortal (Late)",
    "金仙中期": "Golden Immortal (Mid)",
    "金仙初期": "Golden Immortal (Early)",
    "真仙大圆满": "True Immortal (Peak)",
    "真仙后期": "True Immortal (Late)",
    "真仙中期": "True Immortal (Mid)",
    "真仙初期": "True Immortal (Early)",
    "渡劫大圆满": "Tribulation (Peak)",
    "渡劫后期": "Tribulation (Late)",
    "渡劫中期": "Tribulation (Mid)",
    "渡劫初期": "Tribulation (Early)",
    "大乘大圆满": "Mahayana (Peak)",
    "大乘后期": "Mahayana (Late)",
    "大乘中期": "Mahayana (Mid)",
    "大乘初期": "Mahayana (Early)",
    "合体大圆满": "Body Integration (Peak)",
    "合体后期": "Body Integration (Late)",
    "合体中期": "Body Integration (Mid)",
    "合体初期": "Body Integration (Early)",
    "炼虚大圆满": "Void Refining (Peak)",
    "炼虚后期": "Void Refining (Late)",
    "炼虚中期": "Void Refining (Mid)",
    "炼虚初期": "Void Refining (Early)",
    "化神大圆满": "Spirit Transformation (Peak)",
    "化神后期": "Spirit Transformation (Late)",
    "化神中期": "Spirit Transformation (Mid)",
    "化神初期": "Spirit Transformation (Early)",
    "元婴大圆满": "Nascent Soul (Peak)",
    "元婴后期": "Nascent Soul (Late)",
    "元婴中期": "Nascent Soul (Mid)",
    "元婴初期": "Nascent Soul (Early)",
    "结丹大圆满": "Core Formation (Peak)",
    "结丹后期": "Core Formation (Late)",
    "结丹中期": "Core Formation (Mid)",
    "结丹初期": "Core Formation (Early)",
    "筑基大圆满": "Foundation (Peak)",
    "筑基后期": "Foundation (Late)",
    "筑基中期": "Foundation (Mid)",
    "筑基初期": "Foundation (Early)",
    "炼气大圆满": "Qi Refining (Peak)",
    "炼气后期": "Qi Refining (Late)",
    "炼气中期": "Qi Refining (Mid)",
    "炼气初期": "Qi Refining (Early)",
    "练气初期": "Qi Refining (Early)",
}


def localize_level(level: str, locale: str = "en") -> str:
    """Translate stored level labels for English UI; Chinese kept as-is."""
    if (locale or "en").lower().startswith("zh"):
        return level
    return _LEVEL_LABEL_EN.get(level, level)


def localize_levels_in_text(text: str, locale: str = "en") -> str:
    """Replace known Chinese level tokens inside a longer English/mixed line."""
    if (locale or "en").lower().startswith("zh") or not text:
        return text
    # Longer names first so 大罗大圆满 wins over 大罗.
    for zh, en in sorted(_LEVEL_LABEL_EN.items(), key=lambda kv: len(kv[0]), reverse=True):
        if zh in text:
            text = text.replace(zh, en)
    return text


def _dan_level(rating: int) -> str:
    if rating >= 2400:
        return "业余7段"
    if rating >= 2300:
        return "业余6段"
    if rating >= 2200:
        return "业余5段"
    if rating >= 2100:
        return "业余4段"
    if rating >= 2000:
        return "业余3段"
    if rating >= 1900:
        return "业余2段"
    if rating >= 1800:
        return "业余1段"
    if rating >= 1700:
        return "1级"
    if rating >= 1600:
        return "2级"
    if rating >= 1500:
        return "4级"
    if rating >= 1400:
        return "6级"
    if rating >= 1300:
        return "8级"
    if rating >= 1200:
        return "10级"
    return "12级"


_GOMOKU_CULTIVATION_LEVELS: tuple[tuple[int, str], ...] = (
    (4200, "道祖"),
    (4000, "大罗大圆满"),
    (3880, "大罗后期"),
    (3760, "大罗中期"),
    (3640, "大罗初期"),
    (3520, "太乙大圆满"),
    (3400, "太乙后期"),
    (3280, "太乙中期"),
    (3160, "太乙初期"),
    (3040, "金仙大圆满"),
    (2920, "金仙后期"),
    (2800, "金仙中期"),
    (2680, "金仙初期"),
    (2560, "真仙大圆满"),
    (2480, "真仙后期"),
    (2400, "真仙中期"),
    (2320, "真仙初期"),
    (2240, "渡劫大圆满"),
    (2160, "渡劫后期"),
    (2080, "渡劫中期"),
    (2000, "渡劫初期"),
    (1920, "大乘大圆满"),
    (1860, "大乘后期"),
    (1800, "大乘中期"),
    (1740, "大乘初期"),
    (1680, "合体大圆满"),
    (1620, "合体后期"),
    (1560, "合体中期"),
    (1500, "合体初期"),
    (1440, "炼虚大圆满"),
    (1380, "炼虚后期"),
    (1320, "炼虚中期"),
    (1260, "炼虚初期"),
    (1200, "化神大圆满"),
    (1160, "化神后期"),
    (1120, "化神中期"),
    (1080, "化神初期"),
    (1040, "元婴大圆满"),
    (1000, "元婴后期"),
    (960, "元婴中期"),
    (920, "元婴初期"),
    (880, "结丹大圆满"),
    (840, "结丹后期"),
    (800, "结丹中期"),
    (760, "结丹初期"),
    (720, "筑基大圆满"),
    (680, "筑基后期"),
    (640, "筑基中期"),
    (600, "筑基初期"),
    (560, "炼气大圆满"),
    (520, "炼气后期"),
    (480, "炼气中期"),
    (0, "炼气初期"),
)


def _gomoku_cultivation_level(rating: int) -> str:
    for threshold, level in _GOMOKU_CULTIVATION_LEVELS:
        if rating >= threshold:
            return level
    return "练气初期"


def _fide_k(entry: dict[str, Any]) -> int:
    games = int(entry.get("games", 0))
    rating = int(entry.get("rating", 1200))
    if games < 30 and rating < 2300:
        return 40
    if rating < 2400:
        return 20
    return 10


def _elo_k(entry: dict[str, Any]) -> int:
    games = int(entry.get("games", 0))
    return 32 if games < 30 else 24


GAME_CONFIGS: dict[str, dict[str, Any]] = {
    "chess": {
        "scheme": "FIDE Elo",
        "initial": 1200,
        "floor": 1000,
        "level_of": _chess_level,
        "k_factor": _fide_k,
    },
    "gomoku": {
        "scheme": "Cultivation Elo",
        "initial": 1200,
        "floor": 1000,
        "level_of": _gomoku_cultivation_level,
        "k_factor": _elo_k,
    },
    "go": {
        "scheme": "Elo",
        "initial": 1200,
        "floor": 1000,
        "level_of": _dan_level,
        "k_factor": _elo_k,
    },
    "xiangqi": {
        "scheme": "Elo",
        "initial": 1200,
        "floor": 1000,
        "level_of": _dan_level,
        "k_factor": _elo_k,
    },
    "doushou": {
        "scheme": "Elo",
        "initial": 1200,
        "floor": 1000,
        "level_of": _dan_level,
        "k_factor": _elo_k,
    },
    "reversi": {
        "scheme": "Elo",
        "initial": 1200,
        "floor": 1000,
        "level_of": _dan_level,
        "k_factor": _elo_k,
    },
    "darkchess": {
        "scheme": "Elo",
        "initial": 1200,
        "floor": 1000,
        "level_of": _dan_level,
        "k_factor": _elo_k,
    },
    "battleship": {
        "scheme": "Elo",
        "initial": 1200,
        "floor": 1000,
        "level_of": _dan_level,
        "k_factor": _elo_k,
    },
    "junqi": {
        "scheme": "Elo",
        "initial": 1200,
        "floor": 1000,
        "level_of": _dan_level,
        "k_factor": _elo_k,
    },
}

RATED_GAMES = tuple(GAME_CONFIGS)


def is_rated_game(game: str) -> bool:
    return game in GAME_CONFIGS


def game_scheme_label(game: str) -> str:
    cfg = GAME_CONFIGS.get(game)
    return cfg["scheme"] if cfg else "Elo"


class GameRatingStore:
    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._cache: dict[str, Any] | None = None
        # Optional hook: after local record_result, for federation fan-out.
        self.on_change: Optional[
            Callable[[str, list[tuple[str, dict[str, Any]]]], None]
        ] = None

    def _empty_data(self) -> dict[str, Any]:
        return {
            "version": 1,
            "games": {game: {} for game in GAME_CONFIGS},
        }

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
        games = data.get("games")
        if not isinstance(games, dict):
            data = self._empty_data()
        else:
            for game in GAME_CONFIGS:
                if not isinstance(games.get(game), dict):
                    games[game] = {}
        self._cache = data

    def _save_locked(self) -> None:
        assert self._cache is not None
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            prefix=".ratings-",
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

    def _profile_from_entry(
        self,
        game: str,
        user: str,
        entry: dict[str, Any] | None,
    ) -> dict[str, Any]:
        cfg = GAME_CONFIGS[game]
        base = {
            "display_name": (user or "").strip() or "?",
            "rating": cfg["initial"],
            "wins": 0,
            "losses": 0,
            "draws": 0,
            "games": 0,
        }
        if isinstance(entry, dict):
            base.update(
                {
                    "display_name": entry.get("display_name") or base["display_name"],
                    "rating": int(entry.get("rating", base["rating"])),
                    "wins": int(entry.get("wins", 0)),
                    "losses": int(entry.get("losses", 0)),
                    "draws": int(entry.get("draws", 0)),
                    "games": int(entry.get("games", 0)),
                }
            )
        rating = int(base["rating"])
        return {
            "name": base["display_name"],
            "rating": rating,
            "wins": int(base["wins"]),
            "losses": int(base["losses"]),
            "draws": int(base["draws"]),
            "games": int(base["games"]),
            "level": cfg["level_of"](rating),
            "scheme": cfg["scheme"],
        }

    def profile(self, game: str, user: str) -> dict[str, Any]:
        if game not in GAME_CONFIGS:
            raise KeyError(game)
        key = _normalize_user(user)
        with self._lock:
            self._ensure_loaded_locked()
            assert self._cache is not None
            entry = self._cache["games"][game].get(key)
            return self._profile_from_entry(game, user, entry)

    def _ensure_entry_locked(self, game: str, user: str) -> dict[str, Any]:
        assert self._cache is not None
        key = _normalize_user(user)
        display_name = (user or "").strip() or "?"
        entries = self._cache["games"][game]
        entry = entries.get(key)
        if not isinstance(entry, dict):
            cfg = GAME_CONFIGS[game]
            entry = {
                "display_name": display_name,
                "rating": cfg["initial"],
                "wins": 0,
                "losses": 0,
                "draws": 0,
                "games": 0,
                "updated_at": 0.0,
            }
            entries[key] = entry
        else:
            entry["display_name"] = display_name
        return entry

    def record_result(
        self,
        game: str,
        player_a: str,
        player_b: str,
        score_a: float,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if game not in GAME_CONFIGS:
            raise KeyError(game)
        changed: list[tuple[str, dict[str, Any]]] = []
        with self._lock:
            self._ensure_loaded_locked()
            assert self._cache is not None
            cfg = GAME_CONFIGS[game]
            entry_a = self._ensure_entry_locked(game, player_a)
            entry_b = self._ensure_entry_locked(game, player_b)
            before_a = self._profile_from_entry(game, player_a, entry_a)
            before_b = self._profile_from_entry(game, player_b, entry_b)
            rating_a = before_a["rating"]
            rating_b = before_b["rating"]
            expected_a = 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400.0))
            expected_b = 1.0 - expected_a
            score_b = 1.0 - score_a
            k_a: Callable[[dict[str, Any]], int] = cfg["k_factor"]
            k_b: Callable[[dict[str, Any]], int] = cfg["k_factor"]
            new_a = max(
                cfg["floor"],
                round(rating_a + k_a(entry_a) * (score_a - expected_a)),
            )
            new_b = max(
                cfg["floor"],
                round(rating_b + k_b(entry_b) * (score_b - expected_b)),
            )
            entry_a["rating"] = int(new_a)
            entry_b["rating"] = int(new_b)
            entry_a["games"] = int(entry_a.get("games", 0)) + 1
            entry_b["games"] = int(entry_b.get("games", 0)) + 1
            if score_a == 0.5:
                entry_a["draws"] = int(entry_a.get("draws", 0)) + 1
                entry_b["draws"] = int(entry_b.get("draws", 0)) + 1
            elif score_a > 0.5:
                entry_a["wins"] = int(entry_a.get("wins", 0)) + 1
                entry_b["losses"] = int(entry_b.get("losses", 0)) + 1
            else:
                entry_a["losses"] = int(entry_a.get("losses", 0)) + 1
                entry_b["wins"] = int(entry_b.get("wins", 0)) + 1
            now = time.time()
            entry_a["updated_at"] = now
            entry_b["updated_at"] = now
            self._save_locked()
            after_a = self._profile_from_entry(game, player_a, entry_a)
            after_b = self._profile_from_entry(game, player_b, entry_b)
            changed = [
                (player_a, dict(entry_a)),
                (player_b, dict(entry_b)),
            ]
        hook = self.on_change
        if hook is not None:
            try:
                hook(game, changed)
            except Exception:
                pass
        return (after_a | {"delta": after_a["rating"] - before_a["rating"]}), (
            after_b | {"delta": after_b["rating"] - before_b["rating"]}
        )

    @staticmethod
    def _entry_rank(entry: dict[str, Any]) -> tuple[float, int, int]:
        """Higher wins when merging federated copies of the same nick."""
        try:
            updated = float(entry.get("updated_at") or 0.0)
        except (TypeError, ValueError):
            updated = 0.0
        try:
            games = int(entry.get("games") or 0)
        except (TypeError, ValueError):
            games = 0
        try:
            rating = int(entry.get("rating") or 0)
        except (TypeError, ValueError):
            rating = 0
        return (updated, games, rating)

    def apply_remote_entry(
        self,
        game: str,
        user: str,
        entry: dict[str, Any],
        *,
        source_node: str = "",
    ) -> bool:
        """Install a peer rating row when it is at least as fresh as ours.

        Settlement only runs on the game-host (authority) node; peers receive that
        node's rows so same-nick ``/game rating`` matches the host.
        """
        if game not in GAME_CONFIGS or not isinstance(entry, dict):
            return False
        key = _normalize_user(user)
        if not key:
            return False
        incoming = {
            "display_name": str(entry.get("display_name") or user).strip() or user,
            "rating": int(entry.get("rating") or GAME_CONFIGS[game]["initial"]),
            "wins": int(entry.get("wins") or 0),
            "losses": int(entry.get("losses") or 0),
            "draws": int(entry.get("draws") or 0),
            "games": int(entry.get("games") or 0),
            "updated_at": float(entry.get("updated_at") or 0.0),
        }
        if source_node:
            incoming["source_node"] = str(source_node).strip()
        with self._lock:
            self._ensure_loaded_locked()
            assert self._cache is not None
            current = self._cache["games"][game].get(key)
            if isinstance(current, dict) and self._entry_rank(current) > self._entry_rank(
                incoming
            ):
                return False
            self._cache["games"][game][key] = incoming
            self._save_locked()
            return True

    def export_entries(self) -> list[dict[str, Any]]:
        """All non-empty rating rows for federation catch-up."""
        out: list[dict[str, Any]] = []
        with self._lock:
            self._ensure_loaded_locked()
            assert self._cache is not None
            for game, users in self._cache["games"].items():
                if not isinstance(users, dict):
                    continue
                for key, entry in users.items():
                    if not isinstance(entry, dict):
                        continue
                    if int(entry.get("games") or 0) <= 0 and float(
                        entry.get("updated_at") or 0
                    ) <= 0:
                        continue
                    row = dict(entry)
                    row["game"] = game
                    row["user"] = str(entry.get("display_name") or key)
                    out.append(row)
        return out

    def top(self, game: str, limit: int = 10) -> list[dict[str, Any]]:
        if game not in GAME_CONFIGS:
            raise KeyError(game)
        with self._lock:
            self._ensure_loaded_locked()
            assert self._cache is not None
            profiles = [
                self._profile_from_entry(game, entry.get("display_name") or key, entry)
                for key, entry in self._cache["games"][game].items()
                if isinstance(entry, dict)
            ]
        profiles.sort(key=lambda item: (-item["rating"], -item["games"], item["name"].lower()))
        return profiles[:limit]

    def reset_all(self) -> None:
        with self._lock:
            self._cache = self._empty_data()
            self._save_locked()

    def reset_game(self, game: str) -> None:
        if game not in GAME_CONFIGS:
            raise KeyError(game)
        with self._lock:
            self._ensure_loaded_locked()
            assert self._cache is not None
            self._cache["games"][game] = {}
            self._save_locked()

    def reset_user_game(self, user: str, game: str) -> bool:
        if game not in GAME_CONFIGS:
            raise KeyError(game)
        with self._lock:
            self._ensure_loaded_locked()
            assert self._cache is not None
            key = _normalize_user(user)
            removed = self._cache["games"][game].pop(key, None) is not None
            self._save_locked()
            return removed
