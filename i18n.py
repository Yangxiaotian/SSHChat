"""Lightweight zh/en message catalogs for SSHChat server text.

Default locale is English. Users switch with ``/lang zh`` (persisted per nick).
Game line localization for non-sanguo titles uses phrase maps in ``locales``.
"""

from __future__ import annotations

import os
import re
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Mapping, Optional

Locale = str  # "en" | "zh"

DEFAULT_LOCALE: Locale = "en"
SUPPORTED_LOCALES = ("en", "zh")

_locale_ctx: ContextVar[Locale] = ContextVar("sshchat_locale", default=DEFAULT_LOCALE)

_CATALOGS: dict[Locale, dict[str, Any]] = {}
_GAME_EXACT: dict[str, str] = {}  # zh -> en
_GAME_PATTERNS: list[tuple[re.Pattern[str], str]] = []
_loaded = False
_load_lock = threading.Lock()


def normalize_locale(raw: Optional[str]) -> Locale:
    if not raw:
        return default_locale()
    key = raw.strip().lower().replace("_", "-")
    if key in ("zh", "zh-cn", "zh-hans", "cn", "chinese", "中文"):
        return "zh"
    if key in ("en", "en-us", "en-gb", "english", "英文"):
        return "en"
    return default_locale()


def default_locale() -> Locale:
    env = os.environ.get("SSHCHAT_DEFAULT_LOCALE", "").strip()
    if env:
        key = env.lower().replace("_", "-")
        if key in ("zh", "zh-cn", "zh-hans", "cn"):
            return "zh"
        if key in ("en", "en-us", "en-gb"):
            return "en"
    return DEFAULT_LOCALE


def current_locale() -> Locale:
    return _locale_ctx.get()


def set_current_locale(locale: Locale) -> None:
    _locale_ctx.set(normalize_locale(locale))


@contextmanager
def use_locale(locale: Locale) -> Iterator[Locale]:
    token = _locale_ctx.set(normalize_locale(locale))
    try:
        yield _locale_ctx.get()
    finally:
        _locale_ctx.reset(token)


def _deep_get(tree: Mapping[str, Any], path: str) -> Optional[str]:
    cur: Any = tree
    for part in path.split("."):
        if not isinstance(cur, Mapping) or part not in cur:
            return None
        cur = cur[part]
    return cur if isinstance(cur, str) else None


def _ensure_loaded() -> None:
    global _loaded, _GAME_EXACT, _GAME_PATTERNS
    if _loaded:
        return
    with _load_lock:
        if _loaded:
            return
        from locales import en as en_mod
        from locales import zh as zh_mod
        from locales import game_phrases

        _CATALOGS["en"] = en_mod.MESSAGES
        _CATALOGS["zh"] = zh_mod.MESSAGES
        _GAME_EXACT = dict(game_phrases.EXACT_ZH_TO_EN)
        patterns: list[tuple[re.Pattern[str], str]] = []
        for zh_pat, en_repl in game_phrases.PATTERNS_ZH_TO_EN:
            patterns.append((re.compile(zh_pat), en_repl))
        _GAME_PATTERNS = patterns
        _loaded = True


def t(key: str, locale: Optional[Locale] = None, **vars: Any) -> str:
    """Lookup dotted key in catalogs; fall back to English then key."""
    _ensure_loaded()
    loc = normalize_locale(locale if locale is not None else current_locale())
    text = _deep_get(_CATALOGS.get(loc) or {}, key)
    if text is None and loc != "en":
        text = _deep_get(_CATALOGS.get("en") or {}, key)
    if text is None:
        text = key
    if vars:
        try:
            return text.format(**vars)
        except (KeyError, ValueError, IndexError):
            return text
    return text


def help_lines(locale: Optional[Locale] = None) -> list[str]:
    _ensure_loaded()
    loc = normalize_locale(locale if locale is not None else current_locale())
    lines = (_CATALOGS.get(loc) or {}).get("help_lines") or (_CATALOGS.get("en") or {}).get(
        "help_lines"
    )
    if isinstance(lines, (list, tuple)):
        return list(lines)
    return []


def game_help_lines(locale: Optional[Locale] = None) -> list[str]:
    _ensure_loaded()
    loc = normalize_locale(locale if locale is not None else current_locale())
    lines = (_CATALOGS.get(loc) or {}).get("game_help_lines") or (
        _CATALOGS.get("en") or {}
    ).get("game_help_lines")
    if isinstance(lines, (list, tuple)):
        return list(lines)
    return []


def localize_game_line(line: str, locale: Optional[Locale] = None) -> str:
    """Translate a fully formatted game status/help line zh→en when needed.

    Leaves board glyphs and unknown phrases unchanged. Sanguosha content is
    intentionally not covered by the phrase maps.
    """
    _ensure_loaded()
    loc = normalize_locale(locale if locale is not None else current_locale())
    if loc != "en" or not line:
        return line
    if line in _GAME_EXACT:
        return _GAME_EXACT[line]
    for pat, repl in _GAME_PATTERNS:
        m = pat.match(line)
        if m:
            try:
                return m.expand(repl) if "\\" in repl else repl.format(**m.groupdict())
            except (KeyError, ValueError, IndexError, re.error):
                try:
                    return repl.format(**m.groupdict())
                except Exception:
                    return line
    return line


def localize_game_lines(lines: list[str], locale: Optional[Locale] = None) -> list[str]:
    loc = normalize_locale(locale if locale is not None else current_locale())
    if loc != "en":
        return list(lines)
    return [localize_game_line(ln, loc) for ln in lines]


def tr(*, en: str, zh: str, locale: Optional[Locale] = None, **vars: Any) -> str:
    """Inline bilingual pick without a catalog key."""
    loc = normalize_locale(locale if locale is not None else current_locale())
    text = en if loc == "en" else zh
    if vars:
        try:
            return text.format(**vars)
        except (KeyError, ValueError, IndexError):
            return text
    return text
