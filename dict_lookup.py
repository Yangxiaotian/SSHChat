"""Server-side dictionary lookup via Youdao JSON API (中英 / 汉语)."""
from __future__ import annotations

import json
import os
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Optional

DICT_TIMEOUT = float(os.environ.get("SSHCHAT_DICT_TIMEOUT", "8"))
DICT_MAX_WORD_LEN = int(os.environ.get("SSHCHAT_DICT_MAX_WORD_LEN", "64"))
DICT_CACHE_TTL = int(os.environ.get("SSHCHAT_DICT_CACHE_TTL", "3600"))
DICT_TLS_FALLBACK = os.environ.get("SSHCHAT_DICT_TLS_FALLBACK", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
DICT_USER_AGENT = "Mozilla/5.0 (compatible; SSHChat/1.0)"

YOUDAO_API = "https://dict.youdao.com/jsonapi"

_MODE_ALIASES: dict[str, str] = {
    "en": "en",
    "eng": "en",
    "英": "en",
    "ce": "cn",
    "cn": "cn",
    "中": "cn",
    "中英": "cn",
    "zh": "hh",
    "hh": "hh",
    "汉": "hh",
    "汉语": "hh",
}

_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_cache_lock = threading.Lock()


def normalize_mode(token: str) -> Optional[str]:
    key = (token or "").strip().lower()
    if not key:
        return None
    return _MODE_ALIASES.get(key)


def detect_mode(word: str) -> str:
    """Guess lookup mode from query text."""
    if _is_mostly_english(word):
        return "en"
    if _has_cjk(word):
        return "cn"
    return "en"


def _is_mostly_english(word: str) -> bool:
    letters = sum(1 for c in word if c.isascii() and c.isalpha())
    cjk = sum(1 for c in word if "\u4e00" <= c <= "\u9fff")
    return letters > 0 and cjk == 0


def _has_cjk(word: str) -> bool:
    return any("\u4e00" <= c <= "\u9fff" for c in word)


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _flatten_i_field(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("#text") or item.get("i") or ""))
        return "".join(parts).strip()
    if isinstance(value, dict):
        inner = value.get("i")
        if inner is not None:
            return _flatten_i_field(inner)
        return str(value.get("#text") or value.get("#tran") or "").strip()
    return ""


def _dict_proxy_url() -> str:
    for key in (
        "SSHCHAT_DICT_PROXY",
        "SSHCHAT_NEWS_PROXY",
        "SSHCHAT_HTTPS_PROXY",
        "SSHCHAT_HTTP_PROXY",
        "https_proxy",
        "http_proxy",
        "ALL_PROXY",
        "all_proxy",
    ):
        v = (os.environ.get(key) or "").strip()
        if v and v.lower() not in ("0", "none", "no", "off", "false"):
            return v
    return ""


def _dict_transport_retryable(exc: BaseException) -> bool:
    if isinstance(exc, ssl.SSLError):
        return True
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, ssl.SSLError):
            return True
        if isinstance(reason, (TimeoutError, socket.timeout, ConnectionResetError)):
            return True
    msg = str(exc)
    return "CERTIFICATE_VERIFY_FAILED" in msg or "SSL:" in msg


def _dict_urlopen(req: urllib.request.Request) -> bytes:
    proxy = _dict_proxy_url()
    strategies: list[Optional[ssl.SSLContext]] = [None]
    if DICT_TLS_FALLBACK:
        strategies.append(ssl._create_unverified_context())

    last_exc: Optional[BaseException] = None
    for ssl_ctx in strategies:
        handlers: list = []
        if proxy:
            handlers.append(
                urllib.request.ProxyHandler({"http": proxy, "https": proxy})
            )
        if ssl_ctx is not None:
            handlers.append(urllib.request.HTTPSHandler(context=ssl_ctx))
        opener = urllib.request.build_opener(*handlers) if handlers else None
        try:
            if opener is not None:
                resp = opener.open(req, timeout=DICT_TIMEOUT)
            elif ssl_ctx is not None:
                resp = urllib.request.urlopen(req, timeout=DICT_TIMEOUT, context=ssl_ctx)
            else:
                resp = urllib.request.urlopen(req, timeout=DICT_TIMEOUT)
            try:
                return resp.read()
            finally:
                resp.close()
        except Exception as exc:
            if not _dict_transport_retryable(exc):
                raise
            last_exc = exc
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("dict urlopen: no strategies")


def _fetch_youdao(word: str) -> dict[str, Any]:
    word = (word or "").strip()
    if not word:
        raise ValueError("empty query")
    if len(word) > DICT_MAX_WORD_LEN:
        raise ValueError(f"query too long (max {DICT_MAX_WORD_LEN} chars)")

    cache_key = word.lower()
    now = time.time()
    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached and now - cached[0] < DICT_CACHE_TTL:
            return cached[1]

    url = f"{YOUDAO_API}?{urllib.parse.urlencode({'q': word})}"
    req = urllib.request.Request(url, headers={"User-Agent": DICT_USER_AGENT})
    raw = _dict_urlopen(req)
    data = json.loads(raw.decode("utf-8", errors="replace"))
    if not isinstance(data, dict):
        raise RuntimeError("invalid dictionary response")

    with _cache_lock:
        _cache[cache_key] = (now, data)
    return data


def _phonetic_lines(data: dict[str, Any]) -> list[str]:
    simple = data.get("simple") or {}
    words = simple.get("word") or []
    lines: list[str] = []
    for w in words:
        if not isinstance(w, dict):
            continue
        usphone = w.get("usphone")
        ukphone = w.get("ukphone")
        phone = w.get("phone")
        if usphone or ukphone:
            parts: list[str] = []
            if ukphone:
                parts.append(f"英 [{ukphone}]")
            if usphone:
                parts.append(f"美 [{usphone}]")
            lines.append("  ".join(parts))
        elif phone:
            lines.append(f"[{phone}]")
    return lines


def _format_en_zh(data: dict[str, Any], word: str) -> list[str]:
    lines = [f"--- 英→中：{word} ---"]
    lines.extend(_phonetic_lines(data))

    ec = data.get("ec") or {}
    ec_words = ec.get("word") or []
    found = False
    for w in ec_words:
        if not isinstance(w, dict):
            continue
        for tr_group in w.get("trs") or []:
            for tr in (tr_group.get("tr") or []):
                l = tr.get("l") or {}
                text = _flatten_i_field(l.get("i"))
                if text:
                    lines.append(f"  · {text}")
                    found = True
        for wf in w.get("wfs") or []:
            wf_data = wf.get("wf") or {}
            name = wf_data.get("name", "")
            value = wf_data.get("value", "")
            if name and value:
                lines.append(f"  {name}: {value}")
                found = True

    if not found:
        lines.append("  （未找到释义，可换词重试）")
    return lines


def _format_zh_en(data: dict[str, Any], word: str) -> list[str]:
    lines = [f"--- 中→英：{word} ---"]
    lines.extend(_phonetic_lines(data))

    ce = data.get("ce") or {}
    ce_words = ce.get("word") or []
    found = False
    for w in ce_words:
        if not isinstance(w, dict):
            continue
        for tr_group in w.get("trs") or []:
            for tr in (tr_group.get("tr") or []):
                l = tr.get("l") or {}
                en = _flatten_i_field(l.get("i"))
                zh = str(l.get("#tran") or "").strip()
                if en and zh:
                    lines.append(f"  · {en}  —  {zh}")
                    found = True
                elif en:
                    lines.append(f"  · {en}")
                    found = True
                elif zh:
                    lines.append(f"  · {zh}")
                    found = True

    if not found:
        web = data.get("web_trans") or {}
        for item in (web.get("web-translation") or [])[:3]:
            if not isinstance(item, dict):
                continue
            key = item.get("key", "")
            trans = item.get("trans") or []
            values = [
                t.get("value", "")
                for t in trans[:3]
                if isinstance(t, dict) and t.get("value")
            ]
            if key and values:
                lines.append(f"  · {key}: {', '.join(values)}")
                found = True

    if not found:
        lines.append("  （未找到释义，可换词重试）")
    return lines


def _format_zh_zh(data: dict[str, Any], word: str) -> list[str]:
    hh = data.get("newhh") or {}
    source = (hh.get("source") or {}).get("name") or "现代汉语词典"
    lines = [f"--- 汉语：{word}（{source}）---"]
    entries = hh.get("dataList") or []
    if not entries:
        lines.append("  （未找到汉语释义，可换词重试）")
        return lines

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_word = entry.get("word") or word
        pinyin = entry.get("pinyin") or ""
        head = f"{entry_word}  [{pinyin}]" if pinyin else str(entry_word)
        lines.append(head)

        note = entry.get("note")
        if note:
            lines.append(f"  注：{note}")
        variant = entry.get("variant") or []
        if variant:
            lines.append(f"  异体：{'、'.join(str(v) for v in variant)}")

        for sense in entry.get("sense") or []:
            if not isinstance(sense, dict):
                continue
            cat = sense.get("cat") or ""
            defs = sense.get("def") or []
            def_text = "；".join(str(d) for d in defs if d)
            prefix = f"  [{cat}] " if cat else "  "
            if def_text:
                lines.append(f"{prefix}{def_text}")
            for ex in sense.get("examples") or []:
                lines.append(f"    例：{_strip_tags(str(ex))}")
    return lines


def lookup_lines(mode: str, word: str, fetch: Callable[[str], dict[str, Any]] = _fetch_youdao) -> list[str]:
    word = (word or "").strip()
    if not word:
        raise ValueError("missing word")

    data = fetch(word)
    if mode == "en":
        return _format_en_zh(data, word)
    if mode == "cn":
        return _format_zh_en(data, word)
    if mode == "hh":
        return _format_zh_zh(data, word)
    raise ValueError(f"unknown mode: {mode}")
