from __future__ import annotations

import argparse
import base64
import os
import pickle
import re
import secrets
import signal
import socket
import ssl
import textwrap
import threading
import time
import traceback
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse
from collections import OrderedDict, defaultdict
from html import unescape
from pathlib import Path
from typing import Optional, Tuple

import canvas_sharing
import dict_lookup
import federation
import games
import i18n
import library
import file_sharing
import file_http_server
import piano_sharing
from locale_store import LocaleStore
from offline_messages import OfflineMessageStore
from ratings import GAME_CONFIGS, GameRatingStore, is_rated_game, localize_level
from session_store import DisconnectedSeat, FederatedSeat, GameSessionStore

DEFAULT_ROOM = "default"
PORT = int(os.environ.get("SSHCHAT_PORT", "12345"))


def _rating_store_path() -> str:
    raw = os.environ.get("SSHCHAT_RATING_STORE", "").strip()
    if raw:
        return raw
    return os.path.join(os.path.dirname(__file__), "game_ratings.json")


def _library_bookmarks_path() -> str:
    raw = os.environ.get("SSHCHAT_LIBRARY_BOOKMARKS", "").strip()
    if raw:
        return raw
    return os.path.join(os.path.dirname(__file__), "library_bookmarks.json")


def _session_store_path() -> str:
    raw = os.environ.get("SSHCHAT_SESSION_STORE", "").strip()
    if raw:
        return raw
    return os.path.join(os.path.dirname(__file__), "game_sessions.json")


def _offline_messages_path() -> str:
    raw = os.environ.get("SSHCHAT_OFFLINE_MSG_STORE", "").strip()
    if raw:
        return raw
    return os.path.join(os.path.dirname(__file__), "offline_messages.json")


def _locale_store_path() -> str:
    raw = os.environ.get("SSHCHAT_LOCALE_STORE", "").strip()
    if raw:
        return raw
    return os.path.join(os.path.dirname(__file__), "user_locales.json")


rating_store = GameRatingStore(_rating_store_path())


def _federation_sync_ratings(rows: list[dict]) -> None:
    hub = federation.get_hub()
    if hub is None or not hub.enabled or not rows:
        return
    try:
        hub.sync_ratings(rows)
    except Exception as e:
        print(f"federation: sync_ratings failed: {e!r}")


def _on_local_rating_change(game: str, changed: list[tuple[str, dict]]) -> None:
    """Push host-settled rating rows so federated same-nick views match."""
    rows: list[dict] = []
    for user, entry in changed:
        if not isinstance(entry, dict):
            continue
        row = dict(entry)
        row["game"] = game
        row["user"] = str(entry.get("display_name") or user)
        rows.append(row)
    _federation_sync_ratings(rows)


rating_store.on_change = _on_local_rating_change
library_bookmarks = library.LibraryBookmarkStore(_library_bookmarks_path())
session_store = GameSessionStore(_session_store_path())
offline_messages = OfflineMessageStore(_offline_messages_path())
locale_store = LocaleStore(_locale_store_path())
file_http = None  # HTTP server for file transfers, initialized in main()

# conn -> {"name", "rooms", "current_room"}
clients = {}
# room -> set of conn
rooms = defaultdict(set)
# room -> conn of owner (first joiner; default room = first TCP client in #default)
room_owners: dict[str, object] = {}
# room -> announcement text (shown to everyone entering the room)
room_announcements: dict[str, str] = {}
# room -> active game session (e.g. games.ChessGame); at most one per room
room_games: dict[str, object] = {}
# room -> parked (inactive) game kept across federation merge/partition
room_games_parked: dict[str, object] = {}
# room -> node_id that owns authoritative game state (federation)
room_game_authority: dict[str, str] = {}
# room -> random hex token used to break dual-authority conflicts deterministically
room_game_tokens: dict[str, str] = {}
# ended session id -> room (offline peers get this receipt on reconnect)
room_game_ended_ids: OrderedDict[str, str] = OrderedDict()
_ENDED_GAME_IDS_MAX = 64
# rooms where we claimed hostship only because the real host was unreachable
room_game_provisional: set[str] = set()
# room -> monotonic deadline: we sent greq and should accept the next gsync/gend
_greq_until: dict[str, float] = {}
# room -> set of canonical game ids enabled for /game list and /game new
room_enabled_games: dict[str, set[str]] = {}
# The room catalog predates several games. Keep this separate from the
# persisted session version so adding a game does not silently re-enable it
# after the owner explicitly turns it off on a current server.
ROOM_GAME_CATALOG_VERSION = 3
ROOM_GAME_CATALOG_MIGRATION_IDS = frozenset(
    {"doushou", "reversi", "darkchess", "battleship", "junqi", "drawguess"}
)
# lower nickname -> last known rooms/current room for reconnect resume
disconnected_sessions: dict[str, dict[str, object]] = {}
# conn -> {"path": str, "page": int (0-based)}  (remote: also origin/name/title/total_pages)
library_reading: dict[object, dict[str, object]] = {}
# req_id -> {"event": Event, "payload": dict|None, "error": str}
_library_page_waiters: dict[str, dict[str, object]] = {}
_library_page_waiters_lock = threading.Lock()
# req_id -> {"event": Event, "payload": dict|None} for federation file-host proxy
_file_host_waiters: dict[str, dict[str, object]] = {}
_file_host_waiters_lock = threading.Lock()
# resolved path -> (mtime_ns, BookDocument)
library_doc_cache: dict[str, tuple[int, library.BookDocument]] = {}
# per-book load locks so concurrent federated page requests share one parse
_library_load_locks: dict[str, threading.Lock] = {}
_library_load_locks_guard = threading.Lock()
# Cap federated page bodies so lpage_ok stays well under the 1 MiB line budget.
_FED_LIBRARY_PAGE_MAX_CHARS = max(500, int(library.LIBRARY_PAGE_CHARS) * 2)
lock = threading.Lock()
_MISSING = object()  # sentinel for "attribute not present"
_persist_dirty = False
_persist_timer: Optional[threading.Timer] = None
_shutting_down = False
_shutdown_requested = False
_listen_socket: Optional[socket.socket] = None
_fed_hub: Optional[federation.FederationHub] = None
_library_watch_thread: Optional[threading.Thread] = None
_library_watch_stop = threading.Event()
_library_last_state: Optional[tuple[set[str], float]] = None
PERSIST_DEBOUNCE_SECONDS = float(
    os.environ.get("SSHCHAT_SESSION_PERSIST_SECONDS", "2")
)

ROOM_RE = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")
MAX_ANNOUNCE_LEN = 400
_DISCONNECT_ERRNOS = {32, 54, 57, 104}
SESSION_RESUME_TTL_SECONDS = int(
    # 0 = never expire. Default 30d so "last room" survives typical reconnects.
    os.environ.get("SSHCHAT_SESSION_RESUME_TTL_SECONDS", "2592000")
)

# VT100: clear display + cursor home; trailing \n so line-oriented clients flush it.
_CLEAR_SCREEN = "\x1b[2J\x1b[H\n"
_SCREEN_CLEARED_ACK = "[*] Screen cleared.\n"

NEWS_CACHE_TTL = int(os.environ.get("SSHCHAT_NEWS_CACHE_SECONDS", "600"))
NEWS_FETCH_TIMEOUT = float(os.environ.get("SSHCHAT_NEWS_TIMEOUT", "4"))
NEWS_TLS_FALLBACK = os.environ.get("SSHCHAT_NEWS_TLS_FALLBACK", "1") != "0"
NEWS_PROXY_FALLBACK_DIRECT = (
    os.environ.get("SSHCHAT_NEWS_PROXY_FALLBACK_DIRECT", "1") != "0"
)
NEWS_BODY_MAX_CHARS = int(os.environ.get("SSHCHAT_NEWS_BODY_CHARS", "900"))
NEWS_DETAIL_MAX_CHARS = int(os.environ.get("SSHCHAT_NEWS_DETAIL_CHARS", "4000"))
NEWS_WRAP_WIDTH = int(os.environ.get("SSHCHAT_NEWS_WRAP", "88"))
NEWS_BODY_FETCH_CAP = 8000
# RSS 默认走本机 HTTP 代理（常见 Clash / sing-box mixed 端口）；不需要设 SSHCHAT_NEWS_NO_PROXY=1
NEWS_PROXY_LOCAL_DEFAULT = (
    os.environ.get("SSHCHAT_NEWS_PROXY_DEFAULT", "http://127.0.0.1:7897").strip()
)
NEWS_DEFAULT_ALL_LIMIT = 3
NEWS_DEFAULT_CATEGORY_LIMIT = 8
NEWS_MAX_LIMIT = 15
NEWS_PAGE_TIMEOUT = float(os.environ.get("SSHCHAT_NEWS_PAGE_TIMEOUT", "15"))
NEWS_PAGE_MAX_BYTES = int(os.environ.get("SSHCHAT_NEWS_PAGE_MAX_BYTES", "900000"))
NEWS_PAGE_TEXT_MAX = int(os.environ.get("SSHCHAT_NEWS_PAGE_TEXT_CHARS", "16000"))
NEWS_PAGE_FETCH_CACHE_TTL = int(
    os.environ.get("SSHCHAT_NEWS_PAGE_CACHE_SECONDS", "1800")
)

NEWS_CATEGORIES = {
    "cn": {
        "label": "中文新闻",
        "feeds": (
            ("BBC 中文", "https://feeds.bbci.co.uk/zhongwen/simp/rss.xml"),
            ("纽约时报中文网", "https://cn.nytimes.com/rss/"),
            ("美国之音中文", "https://m.voachinese.com/api/zm_yql-vomx-tpeybti"),
            ("RFI 华语", "https://www.rfi.fr/cn/rss"),
            ("德国之声中文", "https://rss.dw.com/rdf/rss-chi-all"),
        ),
    },
    "world": {
        "label": "国际新闻",
        "feeds": (
            ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
            ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
            ("NPR News", "https://feeds.npr.org/1001/rss.xml"),
            ("The Guardian World", "https://www.theguardian.com/world/rss"),
        ),
    },
    "tech": {
        "label": "科技新闻",
        "feeds": (
            ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index"),
            ("The Verge", "https://www.theverge.com/rss/index.xml"),
            ("Wired", "https://www.wired.com/feed/rss"),
            ("Hacker News", "https://hnrss.org/frontpage"),
        ),
    },
}

NEWS_ALIASES = {
    "cn": "cn",
    "zh": "cn",
    "中文": "cn",
    "chinese": "cn",
    "world": "world",
    "intl": "world",
    "international": "world",
    "国际": "world",
    "tech": "tech",
    "科技": "tech",
}
news_cache: dict[str, tuple[float, list[dict[str, str]]]] = {}
news_cache_lock = threading.Lock()
news_fetching: set[str] = set()  # categories currently being fetched
article_fetch_cache: dict[str, tuple[float, str]] = {}
article_fetch_lock = threading.Lock()
_ARTICLE_CACHE_MAX = 200

# /help text lives in locales/{en,zh}.py (i18n.help_lines).


def _parse_handshake_line(raw: str) -> str:
    """First line: nickname only (optional tab suffix from old clients is ignored)."""
    line = raw.strip()
    if not line:
        return "Unknown"
    return line.split("\t", 1)[0].strip() or "Unknown"


def conn_locale(conn) -> str:
    info = clients.get(conn)
    if info:
        loc = info.get("locale")
        if loc:
            return i18n.normalize_locale(str(loc))
        name = (info.get("name") or "").strip()
        if name:
            return locale_store.get(name)
    return i18n.default_locale()


def nick_locale(nickname: str) -> str:
    return locale_store.get(nickname)


def _ts(conn, key: str, **kwargs) -> str:
    return i18n.t(f"server.{key}", conn_locale(conn), **kwargs)


def set_conn_locale(conn, locale: str) -> str:
    loc = i18n.normalize_locale(locale)
    with lock:
        info = clients.get(conn)
        if info:
            info["locale"] = loc
            name = (info.get("name") or "").strip()
        else:
            name = ""
    if name:
        locale_store.set(name, loc)
    return loc


def normalize_room(name: str) -> Optional[str]:
    name = name.strip()
    if not name or not ROOM_RE.match(name):
        return None
    return name


def _reassign_room_owner_locked(room: str, departed: object) -> None:
    """Must hold lock. departed left this room or disconnected."""
    if room_owners.get(room) != departed:
        return
    rem = rooms.get(room, ())
    if rem:
        room_owners[room] = next(iter(rem))
    else:
        room_owners.pop(room, None)


def send_room_announcement_preview(conn, room: str) -> None:
    """If the room has an announcement, show it to this client (after join/switch)."""
    with lock:
        text = (room_announcements.get(room) or "").strip()
    if not text:
        return
    send_line(conn, _ts(conn, "announce_preview", room=room, text=text))


def _format_game_lines(room: str, lines) -> bytes:
    """Wrap each game-line with the standard [#room] [*] prefix as one byte blob."""
    return "".join(f"[#{room}] [*] {ln}\n" for ln in lines).encode("utf-8")


def _should_skip_game_localize(room: str) -> bool:
    game = room_games.get(room)
    return getattr(game, "name", "") == "sanguo"


def send_game_private(conn, room: str, lines) -> None:
    if not lines:
        return
    if _should_skip_game_localize(room):
        out = list(lines)
    else:
        out = i18n.localize_game_lines(list(lines), conn_locale(conn))
    send_line(conn, _format_game_lines(room, out).decode("utf-8"))


# [#room] [*] <body> — game system lines from broadcast_game / send_game_private
_GAME_BROADCAST_LINE_RE = re.compile(
    r"^\[#([a-zA-Z0-9_-]{1,32})\] \[\*\] (.*)$"
)


def _parse_game_broadcast_msg(msg: bytes) -> Optional[tuple[str, list[str]]]:
    """If msg is only [#room] [*] lines for one room, return (room, bodies)."""
    try:
        text = msg.decode("utf-8")
    except Exception:
        return None
    lines = text.splitlines()
    if not lines:
        return None
    room: Optional[str] = None
    bodies: list[str] = []
    for ln in lines:
        m = _GAME_BROADCAST_LINE_RE.match(ln)
        if not m:
            return None
        r, body = m.group(1), m.group(2)
        if room is None:
            room = r
        elif r != room:
            return None
        bodies.append(body)
    if room is None:
        return None
    return room, bodies


def _deliver_game_lines_localized(room: str, lines: list[str]) -> None:
    """Send game lines to each local client in that client's UI locale."""
    if not lines:
        return
    skip = _should_skip_game_localize(room)
    with lock:
        targets = [c for c in list(rooms.get(room, ())) if c in clients]
    for conn in targets:
        out = list(lines) if skip else i18n.localize_game_lines(list(lines), conn_locale(conn))
        send_line(conn, _format_game_lines(room, out).decode("utf-8"))


def broadcast_game(room: str, lines, *, locale: str | None = None) -> None:
    """Broadcast game system text, localized per recipient (not one room language).

    ``locale`` is kept for call-site compatibility but ignored: each connection
    uses ``conn_locale(conn)``. Federation forwards the source (usually Chinese)
    lines so peer nodes can localize for their own clients.
    """
    if not lines:
        return
    _ = locale  # retained for API compatibility; delivery is always per-conn
    raw = list(lines)
    _deliver_game_lines_localized(room, raw)
    hub = federation.get_hub()
    if hub is not None and hub.enabled:
        hub.broadcast_room(room, _format_game_lines(room, raw))


def _viewer_name_for_conn(conn) -> str | None:
    info = clients.get(conn)
    if not info:
        return None
    name = (info.get("name") or "").strip()
    return name or None


def _game_show_for_conn(game, conn) -> list[str]:
    """Per-connection board view; chess/xiangqi flip by seated side, not conn identity."""
    viewer_name = _viewer_name_for_conn(conn)
    if viewer_name and getattr(game, "name", "") in {"chess", "xiangqi"}:
        try:
            return game.show(conn, viewer_name=viewer_name)
        except TypeError:
            pass
    try:
        return game.show(conn)
    except TypeError:
        return game.show()


def send_oriented_boards(room: str, game) -> None:
    """Send full board view; chess/xiangqi second seat sees flipped board (己方在下)."""
    with lock:
        targets = [c for c in list(rooms.get(room, ())) if c in clients]
    for conn in targets:
        lines = _game_show_for_conn(game, conn)
        if lines:
            send_game_private(conn, room, lines)


def send_sanguo_hand_views(room: str, game) -> None:
    """三国杀：轮到出牌/需响应时私信手牌与装备。"""
    if getattr(game, "name", "") != "sanguo":
        return
    push = getattr(game, "push_hand_views", None)
    if not push:
        return
    try:
        pairs = push()
    except Exception as e:
        print(f"send_sanguo_hand_views error: {e!r}")
        return
    for conn, lines in pairs:
        send_game_private(conn, room, lines)


def _rating_profile_line(
    game_name: str,
    profile: dict[str, object],
    rank: int | None = None,
    *,
    locale: str | None = None,
) -> str:
    prefix = f"#{rank} " if rank is not None else ""
    loc = locale or i18n.default_locale()
    level = localize_level(str(profile["level"]), loc)
    return i18n.tr(
        en=(
            f"{prefix}{profile['name']}: rating={profile['rating']} "
            f"level={level} "
            f"W/L/D={profile['wins']}/{profile['losses']}/{profile['draws']} "
            f"games={profile['games']}"
        ),
        zh=(
            f"{prefix}{profile['name']}: 积分={profile['rating']} 等级={profile['level']} "
            f"战绩={profile['wins']}/{profile['losses']}/{profile['draws']} "
            f"局数={profile['games']}"
        ),
        locale=loc,
    )


def _rating_summary_lines(
    target_name: str,
    game_name: Optional[str] = None,
    *,
    locale: str | None = None,
) -> list[str]:
    loc = locale or i18n.default_locale()
    if game_name:
        profile = rating_store.profile(game_name, target_name)
        lines = [
            i18n.tr(
                en=f"{game_name} rating ({profile['scheme']})",
                zh=f"{game_name} 积分（{profile['scheme']}）",
                locale=loc,
            )
        ]
        lines.append(_rating_profile_line(game_name, profile, locale=loc))
        top = rating_store.top(game_name, limit=5)
        if top:
            lines.append(
                i18n.tr(en="Leaderboard Top 5:", zh="榜单 Top 5：", locale=loc)
            )
            lines.extend(
                _rating_profile_line(game_name, item, idx, locale=loc)
                for idx, item in enumerate(top, start=1)
            )
        return lines
    lines = [
        i18n.tr(
            en=f"{target_name} board-game ratings overview (shared across rooms)",
            zh=f"{target_name} 的棋类积分总览（跨房间共享）",
            locale=loc,
        )
    ]
    for rated_game in sorted(GAME_CONFIGS):
        profile = rating_store.profile(rated_game, target_name)
        level = localize_level(str(profile["level"]), loc)
        lines.append(
            i18n.tr(
                en=(
                    f"{rated_game}: rating={profile['rating']} level={level} "
                    f"W/L/D={profile['wins']}/{profile['losses']}/{profile['draws']} "
                    f"games={profile['games']} scheme={profile['scheme']}"
                ),
                zh=(
                    f"{rated_game}: 积分={profile['rating']} 等级={profile['level']} "
                    f"战绩={profile['wins']}/{profile['losses']}/{profile['draws']} "
                    f"局数={profile['games']} 体系={profile['scheme']}"
                ),
                locale=loc,
            )
        )
    return lines


def _default_enabled_games() -> set[str]:
    return set(games.GAMES)


def _enabled_games_for_room_locked(room: str) -> set[str]:
    """Per-room online games; new rooms default to all registered games."""
    enabled = room_enabled_games.get(room)
    if enabled is None:
        enabled = _default_enabled_games()
        room_enabled_games[room] = enabled
    return enabled


def _drop_game_if_room_empty_locked(room: str) -> None:
    """Caller holds lock; drop ended/inactive games when the room has no clients."""
    game = room_games.get(room)
    game_active = game is not None and getattr(game, "state", "ended") != "ended"
    if not rooms.get(room) and not game_active:
        room_games.pop(room, None)


def _nick_key(name: str) -> str:
    return (name or "").strip().lower()


def _remember_session_locked(name: str, joined_rooms: list[str], current_room: str) -> None:
    """Keep enough account state for app restart reconnect resume."""
    key = _nick_key(name)
    if not key:
        return
    room_set = {r for r in joined_rooms if r}
    room_set.add(DEFAULT_ROOM)
    active = current_room if current_room in room_set else DEFAULT_ROOM
    disconnected_sessions[key] = {
        "name": name,
        "rooms": set(room_set),
        "current_room": active,
        "ts": time.time(),
    }


def _sync_live_session_locked(conn) -> None:
    """Persist last active room while still online (so reconnect hits the last room)."""
    info = clients.get(conn)
    if not info:
        return
    _remember_session_locked(
        info["name"],
        list(info.get("rooms") or ()),
        info.get("current_room", DEFAULT_ROOM),
    )
    _mark_sessions_dirty()


def _load_recent_session_locked(name: str) -> dict[str, object] | None:
    key = _nick_key(name)
    if not key:
        return None
    session = disconnected_sessions.get(key)
    if not session:
        return None
    ts = float(session.get("ts") or 0)
    if SESSION_RESUME_TTL_SECONDS > 0 and time.time() - ts > SESSION_RESUME_TTL_SECONDS:
        disconnected_sessions.pop(key, None)
        return None
    return session


def _same_name_peer_in_room_locked(room: str, name: str, exclude_conn=None):
    """Find another online connection in room that has the same nickname."""
    key = _nick_key(name)
    if not key:
        return None
    for peer in list(rooms.get(room, ())):
        if peer is exclude_conn:
            continue
        info = clients.get(peer)
        if not info:
            continue
        if info["name"].strip().lower() == key:
            return peer
    return None


def _is_conn_seated_in_game(game, conn) -> bool:
    is_seated = getattr(game, "is_seated", None)
    if callable(is_seated):
        try:
            return bool(is_seated(conn))
        except Exception:
            return False
    players = getattr(game, "players", None)
    if isinstance(players, list):
        return any(isinstance(item, tuple) and item and item[0] is conn for item in players)
    for attr in ("white_conn", "black_conn", "red_conn"):
        if getattr(game, attr, None) is conn:
            return True
    return False


def _replace_conn_refs(value, old_conn, new_conn):
    """Deep-replace old_conn -> new_conn inside game state objects."""
    if value is old_conn:
        return new_conn, True

    changed = False
    if isinstance(value, list):
        for i, item in enumerate(list(value)):
            new_item, item_changed = _replace_conn_refs(item, old_conn, new_conn)
            if item_changed:
                value[i] = new_item
                changed = True
        return value, changed

    if isinstance(value, tuple):
        items = []
        for item in value:
            new_item, item_changed = _replace_conn_refs(item, old_conn, new_conn)
            if item_changed:
                changed = True
            items.append(new_item)
        return (tuple(items) if changed else value), changed

    if isinstance(value, dict):
        updates = []
        removals = []
        for k, v in list(value.items()):
            new_k, k_changed = _replace_conn_refs(k, old_conn, new_conn)
            new_v, v_changed = _replace_conn_refs(v, old_conn, new_conn)
            if k_changed or v_changed:
                removals.append(k)
                updates.append((new_k, new_v))
                changed = True
        for k in removals:
            value.pop(k, None)
        for k, v in updates:
            value[k] = v
        return value, changed

    if isinstance(value, set):
        to_remove = []
        to_add = []
        for item in list(value):
            new_item, item_changed = _replace_conn_refs(item, old_conn, new_conn)
            if item_changed:
                to_remove.append(item)
                to_add.append(new_item)
                changed = True
        for item in to_remove:
            value.discard(item)
        for item in to_add:
            value.add(item)
        return value, changed

    if hasattr(value, "__dict__"):
        for attr, cur in vars(value).items():
            new_cur, cur_changed = _replace_conn_refs(cur, old_conn, new_conn)
            if cur_changed:
                setattr(value, attr, new_cur)
                changed = True
        return value, changed

    slots = getattr(value, "__slots__", None)
    if slots:
        for attr in slots if isinstance(slots, (list, tuple)) else (slots,):
            if not hasattr(value, attr):
                continue
            cur = getattr(value, attr)
            new_cur, cur_changed = _replace_conn_refs(cur, old_conn, new_conn)
            if cur_changed:
                setattr(value, attr, new_cur)
                changed = True
        return value, changed

    return value, False


def _game_seat_conn_by_name(game, nickname: str):
    """Best-effort: find seated connection by nickname in heterogeneous game classes."""
    key = _nick_key(nickname)
    if not key:
        return None

    players = getattr(game, "players", None)
    if isinstance(players, list):
        for item in players:
            if isinstance(item, tuple) and len(item) >= 2:
                conn, name = item[0], item[1]
            elif hasattr(item, "conn") and hasattr(item, "name"):
                conn, name = item.conn, item.name
            else:
                continue
            if isinstance(name, str) and name.strip().lower() == key:
                return conn

    for conn_attr, name_attr in (
        ("first_conn", "first_name"),
        ("second_conn", "second_name"),
        ("white_conn", "white_name"),
        ("black_conn", "black_name"),
        ("red_conn", "red_name"),
    ):
        conn_val = getattr(game, conn_attr, None)
        name_val = getattr(game, name_attr, None)
        if isinstance(name_val, str) and name_val.strip().lower() == key:
            return conn_val

    return None


def _resume_same_account_seat_locked(
    room: str,
    game,
    new_conn,
    nickname: str,
    *,
    old_conn_hint=None,
) -> bool:
    """Transfer a seated role from another same-name connection to new_conn."""
    if game is None:
        return False
    if getattr(game, "state", "ended") == "ended":
        return False
    is_seated = getattr(game, "is_seated", None)
    if callable(is_seated):
        if is_seated(new_conn):
            return False
    else:
        if _game_seat_conn_by_name(game, nickname) is new_conn:
            return False

    old_conn = old_conn_hint
    if old_conn is None:
        seat_conn = _game_seat_conn_by_name(game, nickname)
        if seat_conn is not None and seat_conn is not new_conn:
            old_conn = seat_conn
        else:
            for peer in list(rooms.get(room, ())):
                if peer is new_conn:
                    continue
                info = clients.get(peer)
                if not info:
                    continue
                if info["name"].strip().lower() != nickname.strip().lower():
                    continue
                if callable(is_seated):
                    if is_seated(peer):
                        old_conn = peer
                        break
                elif seat_conn is peer:
                    old_conn = peer
                    break
    if old_conn is None or old_conn is new_conn:
        return False
    if callable(is_seated) and not is_seated(old_conn):
        return False

    _updated, changed = _replace_conn_refs(game, old_conn, new_conn)
    if changed and room_owners.get(room) is old_conn:
        room_owners[room] = new_conn
    return changed


def _iter_game_conn_seats(game):
    """Yield (conn, display_name) for seated human/placeholder roles."""
    seen: set[int] = set()
    players = getattr(game, "players", None)
    if isinstance(players, list):
        for item in players:
            if isinstance(item, tuple) and len(item) >= 2:
                conn, name = item[0], item[1]
                if conn is not None and isinstance(name, str) and id(conn) not in seen:
                    seen.add(id(conn))
                    yield conn, name
            elif hasattr(item, "conn") and hasattr(item, "name"):
                conn, name = item.conn, item.name
                if conn is not None and isinstance(name, str) and id(conn) not in seen:
                    seen.add(id(conn))
                    yield conn, name
    for conn_attr, name_attr in (
        ("first_conn", "first_name"),
        ("second_conn", "second_name"),
        ("white_conn", "white_name"),
        ("black_conn", "black_name"),
        ("red_conn", "red_name"),
    ):
        conn = getattr(game, conn_attr, None)
        name = getattr(game, name_attr, None)
        if (
            conn is not None
            and isinstance(name, str)
            and name
            and id(conn) not in seen
        ):
            seen.add(id(conn))
            yield conn, name


def _conn_needs_seat_swap(conn) -> bool:
    if conn is None or isinstance(conn, (DisconnectedSeat, FederatedSeat)):
        return False
    if conn in clients:
        return True
    return hasattr(conn, "send") or hasattr(conn, "recv")


def _pickle_game_for_storage(game) -> bytes:
    swaps: list[tuple[DisconnectedSeat, object]] = []
    for conn, name in _iter_game_conn_seats(game):
        if not _conn_needs_seat_swap(conn):
            continue
        seat = DisconnectedSeat(name)
        _replace_conn_refs(game, conn, seat)
        swaps.append((seat, conn))
    # rating_store contains an RLock and cannot be pickled; it is rebound after
    # loading via _rebind_game_services, so strip it before serialising.
    saved_rs = getattr(game, "rating_store", _MISSING)
    if saved_rs is not _MISSING:
        game.rating_store = None
    try:
        return pickle.dumps(game, protocol=pickle.HIGHEST_PROTOCOL)
    finally:
        if saved_rs is not _MISSING:
            game.rating_store = saved_rs
        for seat, conn in swaps:
            _replace_conn_refs(game, seat, conn)


def _rebind_game_services(game) -> None:
    if hasattr(game, "rating_store"):
        game.rating_store = rating_store
    if getattr(game, "name", "") == "xiangqi":
        log = getattr(game, "_xq_ply_log", None)
        if not isinstance(log, list) or any(not isinstance(item, dict) for item in log):
            game._xq_ply_log = []
        hist = getattr(game, "_history", None)
        if isinstance(hist, list) and len(game._xq_ply_log) > len(hist):
            game._xq_ply_log = game._xq_ply_log[: len(hist)]
    ensure = getattr(game, "_ensure_compat_state", None)
    if callable(ensure):
        ensure()


def _ensure_game_runtime_compat(game) -> None:
    """Repair active game objects created by older code before dispatching actions."""
    if game is None:
        return
    _rebind_game_services(game)


def _nudge_game_bots_locked(game) -> list[str]:
    """Advance bot turns. Must not run while holding the server lock — AI search blocks all commands."""
    if game is None or getattr(game, "state", "ended") == "ended":
        return []
    nudge = getattr(game, "nudge_bots", None)
    if not callable(nudge):
        return []
    try:
        return list(nudge() or [])
    except Exception as e:
        print(f"nudge_bots failed for {getattr(game, 'name', game)!r}: {e!r}")
        return []


def _build_session_payload_locked() -> dict[str, object]:
    games_blob: dict[str, str] = {}
    for room, game in room_games.items():
        if game is None or getattr(game, "state", "ended") == "ended":
            continue
        try:
            raw = _pickle_game_for_storage(game)
        except Exception as e:
            print(f"skip persisting room {room!r} game: {e!r}")
            continue
        games_blob[room] = base64.b64encode(raw).decode("ascii")
    parked_blob: dict[str, str] = {}
    for room, game in room_games_parked.items():
        if game is None or getattr(game, "state", "ended") == "ended":
            continue
        try:
            raw = _pickle_game_for_storage(game)
        except Exception as e:
            print(f"skip persisting parked room {room!r} game: {e!r}")
            continue
        parked_blob[room] = base64.b64encode(raw).decode("ascii")
    sessions: dict[str, dict[str, object]] = {}
    for key, sess in disconnected_sessions.items():
        rooms_set = sess.get("rooms") or set()
        if isinstance(rooms_set, set):
            room_list = sorted(rooms_set)
        else:
            room_list = sorted(str(r) for r in rooms_set)
        sessions[key] = {
            "name": sess.get("name"),
            "rooms": room_list,
            "current_room": sess.get("current_room"),
            "ts": sess.get("ts"),
        }
    return {
        "room_games": games_blob,
        "room_games_parked": parked_blob,
        "disconnected_sessions": sessions,
        "room_enabled_games": {
            room: sorted(enabled)
            for room, enabled in room_enabled_games.items()
        },
        "room_enabled_games_version": ROOM_GAME_CATALOG_VERSION,
        "room_announcements": dict(room_announcements),
        "room_game_authority": {
            room: auth
            for room, auth in room_game_authority.items()
            if isinstance(room, str) and isinstance(auth, str) and auth.strip()
        },
        "room_game_tokens": {
            room: tok
            for room, tok in room_game_tokens.items()
            if isinstance(room, str) and isinstance(tok, str) and tok.strip()
        },
        "room_game_ended_ids": {
            tok: room
            for tok, room in room_game_ended_ids.items()
            if isinstance(tok, str) and tok.strip() and isinstance(room, str)
        },
    }


def _apply_session_payload_locked(payload: dict[str, object]) -> bool:
    catalog_migrated = False
    games_blob = payload.get("room_games")
    if isinstance(games_blob, dict):
        for room, encoded in games_blob.items():
            if not isinstance(room, str) or not isinstance(encoded, str):
                continue
            try:
                game = pickle.loads(base64.b64decode(encoded))
                _rebind_game_services(game)
            except Exception as e:
                print(f"skip restoring room {room!r} game: {e!r}")
                continue
            room_games[room] = game
    parked_blob = payload.get("room_games_parked")
    if isinstance(parked_blob, dict):
        for room, encoded in parked_blob.items():
            if not isinstance(room, str) or not isinstance(encoded, str):
                continue
            try:
                game = pickle.loads(base64.b64decode(encoded))
                _rebind_game_services(game)
            except Exception as e:
                print(f"skip restoring parked room {room!r} game: {e!r}")
                continue
            room_games_parked[room] = game
    sessions = payload.get("disconnected_sessions")
    if isinstance(sessions, dict):
        for key, sess in sessions.items():
            if not isinstance(sess, dict):
                continue
            rooms_raw = sess.get("rooms")
            if isinstance(rooms_raw, list):
                room_set = {str(r) for r in rooms_raw if r}
            else:
                room_set = set()
            room_set.add(DEFAULT_ROOM)
            current = sess.get("current_room")
            if not isinstance(current, str) or not current:
                current = DEFAULT_ROOM
            if current not in room_set:
                room_set.add(current)
            disconnected_sessions[str(key)] = {
                "name": sess.get("name") or key,
                "rooms": room_set,
                "current_room": current,
                "ts": float(sess.get("ts") or time.time()),
            }
    try:
        catalog_version = int(payload.get("room_enabled_games_version") or 0)
    except (TypeError, ValueError):
        catalog_version = 0
    enabled = payload.get("room_enabled_games")
    if isinstance(enabled, dict):
        for room, names in enabled.items():
            if isinstance(room, str) and isinstance(names, list):
                room_enabled_games[room] = {str(n) for n in names}
                if catalog_version < ROOM_GAME_CATALOG_VERSION:
                    room_enabled_games[room].update(ROOM_GAME_CATALOG_MIGRATION_IDS)
                    catalog_migrated = True
    announcements = payload.get("room_announcements")
    if isinstance(announcements, dict):
        for room, text in announcements.items():
            if isinstance(room, str) and isinstance(text, str):
                room_announcements[room] = text
    authority = payload.get("room_game_authority")
    if isinstance(authority, dict):
        for room, auth in authority.items():
            # Keep auth even with no active game (ended-tombstone) so greq can
            # answer gend and gsync cannot revive a stale peer board after restart.
            if isinstance(room, str) and isinstance(auth, str) and auth.strip():
                room_game_authority[room] = auth.strip()
    tokens = payload.get("room_game_tokens")
    if isinstance(tokens, dict):
        for room, tok in tokens.items():
            if (
                isinstance(room, str)
                and room in room_games
                and isinstance(tok, str)
                and tok.strip()
            ):
                room_game_tokens[room] = tok.strip()
    ended_blob = payload.get("room_game_ended_ids")
    if isinstance(ended_blob, dict):
        for tok, room in ended_blob.items():
            if (
                isinstance(tok, str)
                and tok.strip()
                and isinstance(room, str)
                and room.strip()
            ):
                _remember_ended_game_locked(room.strip(), tok.strip())
    return catalog_migrated


def _remember_ended_game_locked(room: str, token: str) -> None:
    token = (token or "").strip()
    room = (room or "").strip()
    if not token or not room:
        return
    room_game_ended_ids.pop(token, None)
    room_game_ended_ids[token] = room
    while len(room_game_ended_ids) > _ENDED_GAME_IDS_MAX:
        room_game_ended_ids.popitem(last=False)


def _ended_token_for_room_locked(room: str) -> str:
    room = (room or "").strip()
    if not room:
        return ""
    for tok, r in reversed(list(room_game_ended_ids.items())):
        if r == room:
            return tok
    return ""


def _game_id_is_ended_locked(token: str) -> bool:
    token = (token or "").strip()
    return bool(token) and token in room_game_ended_ids


def _mark_sessions_dirty() -> None:
    global _persist_dirty, _persist_timer
    _persist_dirty = True
    if _persist_timer is not None:
        return
    timer = threading.Timer(PERSIST_DEBOUNCE_SECONDS, _flush_sessions_if_dirty)
    timer.daemon = True
    _persist_timer = timer
    timer.start()


def _flush_sessions_if_dirty() -> None:
    global _persist_dirty, _persist_timer
    _persist_timer = None
    if not _persist_dirty:
        return
    _persist_dirty = False
    try:
        with lock:
            payload = _build_session_payload_locked()
        session_store.save(payload)
    except Exception as e:
        print(f"session persist failed: {e!r}")
        _mark_sessions_dirty()


def _persist_sessions_now() -> None:
    global _persist_dirty, _persist_timer
    _persist_dirty = False
    if _persist_timer is not None:
        _persist_timer.cancel()
        _persist_timer = None
    with lock:
        payload = _build_session_payload_locked()
    session_store.save(payload)


def _safe_persist_sessions_now() -> None:
    """Persist session state; never raise to callers (avoid disconnecting clients)."""
    try:
        _persist_sessions_now()
    except Exception as e:
        print(f"session persist failed: {e!r} (path={session_store.path!r})")
        _mark_sessions_dirty()


def _persist_after_game_change() -> None:
    """Write in-progress games to disk immediately after state changes."""
    _safe_persist_sessions_now()


def _load_persisted_sessions() -> None:
    payload = session_store.load()
    if not payload:
        return
    with lock:
        catalog_migrated = _apply_session_payload_locked(payload)
        parked_back = _restore_idle_parked_games_locked()
        active = sum(
            1
            for game in room_games.values()
            if game is not None and getattr(game, "state", "ended") != "ended"
        )
        sessions = len(disconnected_sessions)
    if parked_back:
        print(
            "promoted parked game(s) to active: "
            + ", ".join(f"#{room}" for room, _ in parked_back)
        )
    if parked_back or catalog_migrated:
        _persist_after_game_change()
    if active or sessions:
        print(
            f"restored {active} active room game(s) and "
            f"{sessions} reconnect session(s) from {session_store.path}"
        )


def send_line(conn, text: str) -> None:
    try:
        conn.send(text.encode("utf-8"))
    except Exception as e:
        print(f"send_line error: {e!r}")
        remove_client(conn)


def _format_offline_ts(ts: float) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ts)))
    except (OverflowError, OSError, ValueError, TypeError):
        return "?"


def _format_file_size_kb(size: int | float) -> str:
    try:
        return f"{float(size) / 1024:.1f} KB"
    except (TypeError, ValueError):
        return "? KB"


def _format_file_leave_summary(filename: str, file_size: int | float) -> str:
    name = (filename or "").strip() or "file"
    return f"[文件] {name} ({_format_file_size_kb(file_size)})"


def _build_file_ready_message(
    *,
    sender: str,
    filename: str,
    file_size: int | float,
    download_url: str,
    key: str,
    room: str | None = None,
) -> str:
    message_lines = [
        "[*] ========== 收到新文件 ==========\n",
        f"[*] 发件人: {sender}\n",
        f"[*] 文件名: {filename}\n",
        f"[*] 大小: {_format_file_size_kb(file_size)}\n",
    ]
    if room:
        message_lines.append(f"[*] 来自房间: #{room}\n")
    message_lines.extend([
        "[*]\n",
        "[*] 下载网址:\n",
        f"[*] {download_url}\n",
        "[*]\n",
        f"[*] 下载密钥: {key}\n",
        "[*]\n",
        "[*] 说明:\n",
        "[*] 1. 打开下载网址，在页面里输入上面的密钥\n",
        "[*] 2. 图片、视频、PDF 等会直接预览，确认后再点按钮保存\n",
        "[*] 3. 文件只能下载一次，存好之前别关页面\n",
        "[*] 4. 每个接收者的网址和密钥都不同\n",
        "[*] 5. 图形客户端会折叠成按钮，可一键打开\n",
        "[*] ================================\n",
        f"[*] gui-open download {download_url} {key}\n",
    ])
    return "".join(message_lines)


def _file_ready_message_from_leave(item: dict) -> str | None:
    """Rebuild a full download notice from an offline file leave-message."""
    meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
    token = str(meta.get("download_token") or "").strip()
    key = str(meta.get("download_key") or "").strip()
    filename = str(meta.get("filename") or item.get("text") or "file").strip() or "file"
    try:
        file_size = int(meta.get("file_size") or 0)
    except (TypeError, ValueError):
        file_size = 0
    room = meta.get("room")
    if isinstance(room, str):
        room = room.strip() or None
    else:
        room = None
    # Federated leaves carry an absolute URL hosted on the origin node.
    download_url = str(meta.get("download_url") or "").strip()
    if not download_url:
        if file_http is None or not token:
            return None
        download_url = f"{file_http.get_base_url()}/download/{token}"
    if not key:
        return None
    return _build_file_ready_message(
        sender=str(item.get("from") or "?"),
        filename=filename,
        file_size=file_size,
        download_url=download_url,
        key=key,
        room=room,
    )


def _revoke_recalled_file(removed: dict, recipient: str) -> None:
    """Drop download access when a pending offline file leave-message is recalled."""
    if (removed.get("kind") or "pm") != "file":
        return
    meta = removed.get("meta") if isinstance(removed.get("meta"), dict) else {}
    transfer_id = str(meta.get("transfer_id") or "").strip()
    if not transfer_id:
        return
    try:
        file_sharing.file_transfer_store.revoke_recipient(transfer_id, recipient)
    except Exception as e:
        print(f"[FileTransfer] Failed to revoke recalled file for {recipient}: {e}")
    _federation_clear_file_leave(recipient, transfer_id)


def _federation_clear_file_leave(recipient: str, transfer_id: str) -> None:
    hub = federation.get_hub()
    if hub is None or not hub.enabled:
        return
    try:
        print(f"[FileTransfer] Broadcasting fleave_clear for {recipient} "
              f"(transfer_id={transfer_id})")
        hub.clear_file_leave(recipient, transfer_id)
    except Exception as e:
        print(f"federation: clear_file_leave failed: {e!r}")


def _federation_clear_offline_pm(recipient: str, leave_id: str) -> None:
    hub = federation.get_hub()
    if hub is None or not hub.enabled:
        return
    try:
        hub.clear_offline_pm(recipient, leave_id)
    except Exception as e:
        print(f"federation: clear_offline_pm failed: {e!r}")


def _federation_seed_file_leave(
    recipient: str, sender: str, notice: dict
) -> None:
    hub = federation.get_hub()
    if hub is None or not hub.enabled:
        return
    try:
        hub.broadcast_file_leave(recipient, sender, notice)
    except Exception as e:
        print(f"federation: broadcast_file_leave failed: {e!r}")


def _federation_push_all_offline_clears() -> None:
    """Re-broadcast tombstones so a partitioned peer drops recalled/delivered leaves."""
    hub = federation.get_hub()
    if hub is None or not hub.enabled:
        return
    try:
        cleared = offline_messages.snapshot_cleared()
    except Exception as e:
        print(f"federation: snapshot offline clears failed: {e!r}")
        return
    pushed_pm = 0
    pushed_file = 0
    for item in cleared:
        to_name = str(item.get("to") or "").strip()
        if not to_name:
            continue
        kind = str(item.get("kind") or "pm")
        try:
            if kind == "file":
                tid = str(item.get("transfer_id") or "").strip()
                if tid and hub.clear_file_leave(to_name, tid):
                    pushed_file += 1
            else:
                leave_id = str(item.get("id") or "").strip()
                if leave_id and hub.clear_offline_pm(to_name, leave_id):
                    pushed_pm += 1
        except Exception as e:
            print(f"federation: catch-up clear failed: {e!r}")
    if pushed_pm or pushed_file:
        print(
            f"federation: catch-up offline clears "
            f"pm={pushed_pm} file={pushed_file}"
        )


def _federation_push_all_offline_leaves() -> None:
    """Re-seed every pending leave so a newly linked peer shares the mailbox.

    Same-nick users logging into either node (and /leave on either node) need the
    full unread set, including leaves created while the peer was offline.
    """
    hub = federation.get_hub()
    if hub is None or not hub.enabled:
        return
    try:
        removed = offline_messages.compact_duplicates()
        if removed:
            print(f"federation: compacted {removed} duplicate offline leave(s)")
        pending = offline_messages.snapshot_pending()
    except Exception as e:
        print(f"federation: snapshot offline leaves failed: {e!r}")
        return
    pushed_pm = 0
    pushed_file = 0
    for item in pending:
        to_name = str(item.get("to") or "").strip()
        from_name = str(item.get("from") or "").strip() or "?"
        text = str(item.get("text") or "")
        if not to_name or not text:
            continue
        kind = str(item.get("kind") or "pm")
        try:
            ts = float(item.get("ts") or 0)
        except (TypeError, ValueError):
            ts = 0.0
        if kind == "file":
            meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
            tid = str(meta.get("transfer_id") or "").strip()
            download_url = str(meta.get("download_url") or "").strip()
            download_key = str(meta.get("download_key") or "").strip()
            if not tid or not download_url or not download_key:
                continue
            notice = {
                "filename": str(meta.get("filename") or "").strip() or "file",
                "file_size": meta.get("file_size") or 0,
                "download_url": download_url,
                "download_key": download_key,
                "download_token": str(meta.get("download_token") or "").strip(),
                "room": meta.get("room"),
                "transfer_id": tid,
                "leave_ts": ts,
            }
            try:
                if hub.broadcast_file_leave(to_name, from_name, notice):
                    pushed_file += 1
            except Exception as e:
                print(f"federation: catch-up fleave failed: {e!r}")
            continue
        leave_id = str(item.get("id") or "").strip()
        # Never re-seed without a stable id — empty ids mint a new one per hop
        # and explode into duplicate /leave rows across federation reconnects.
        if not leave_id:
            continue
        try:
            if hub.broadcast_offline_pm(
                to_name,
                from_name,
                text,
                leave_id=leave_id,
                ts=ts or None,
            ):
                pushed_pm += 1
        except Exception as e:
            print(f"federation: catch-up pleave failed: {e!r}")
    if pushed_pm or pushed_file:
        print(
            f"federation: catch-up offline leaves "
            f"pm={pushed_pm} file={pushed_file}"
        )

def deliver_offline_messages(conn, recipient_name: str) -> int:
    """Flush stored leave-messages to this connection. Returns how many were sent."""
    pending = offline_messages.take_all(recipient_name)
    if not pending:
        return 0
    n = len(pending)
    send_line(conn, _ts(conn, "offline_header", n=n))
    for item in pending:
        when = _format_offline_ts(item.get("ts", 0))
        sender = item.get("from") or "?"
        if (item.get("kind") or "pm") == "file":
            notice = _file_ready_message_from_leave(item)
            if notice:
                send_line(conn, _ts(conn, "offline_file_meta", when=when, sender=sender))
                send_line(conn, notice)
            else:
                text = item.get("text") or i18n.tr(en="[file]", zh="[文件]", locale=conn_locale(conn))
                send_line(
                    conn,
                    _ts(conn, "offline_file_pm", sender=sender, when=when, text=text),
                )
            meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
            tid = str(meta.get("transfer_id") or "").strip()
            if tid:
                _federation_clear_file_leave(recipient_name, tid)
            else:
                # Log missing transfer_id for debugging
                print(f"[WARNING] deliver_offline_messages: file leave without transfer_id "
                      f"(from={sender}, to={recipient_name})")
            continue
        text = item.get("text") or ""
        send_line(conn, _ts(conn, "offline_pm", sender=sender, when=when, text=text))
        lid = str(item.get("id") or "").strip()
        if lid:
            _federation_clear_offline_pm(recipient_name, lid)
    return n


def _send_leave_list(conn, sender_name: str, recipient: str | None = None) -> None:
    try:
        offline_messages.compact_duplicates()
    except Exception as e:
        print(f"offline leave compact failed: {e!r}")
    items = offline_messages.list_sent_unread(sender_name, recipient)
    if not items:
        if recipient:
            send_line(
                conn,
                i18n.tr(
                    en=(
                        f"[*] No unread leave-messages or files awaiting {recipient!r}.\n"
                    ),
                    zh=(
                        f"[*] 没有发给 {recipient!r}、对方尚未阅读的留言或文件。\n"
                    ),
                    locale=conn_locale(conn),
                ),
            )
        else:
            send_line(conn, _ts(conn, "leave_none"))
        return
    if recipient:
        send_line(
            conn,
            i18n.tr(
                en=(
                    f"[*] Unread leave-messages/files to {recipient!r} "
                    f"({len(items)} total):\n"
                ),
                zh=(
                    f"[*] 发给 {recipient!r}、对方尚未阅读的留言/文件"
                    f"（共 {len(items)} 条）：\n"
                ),
                locale=conn_locale(conn),
            ),
        )
        for item in items:
            when = _format_offline_ts(item.get("ts", 0))
            send_line(
                conn,
                _ts(conn, "leave_item", index=item["index"], when=when, text=item["text"]),
            )
        send_line(conn, _ts(conn, "leave_recall_hint", recipient=recipient))
        return
    send_line(conn, _ts(conn, "leave_list_header", n=len(items)))
    current_to = None
    for item in items:
        to_name = item.get("to") or "?"
        if to_name != current_to:
            current_to = to_name
            send_line(conn, _ts(conn, "leave_group", name=to_name))
        when = _format_offline_ts(item.get("ts", 0))
        send_line(
            conn,
            _ts(conn, "leave_item", index=item["index"], when=when, text=item["text"]),
        )
    send_line(
        conn,
        i18n.tr(
            en="[*] Recall: /leave <nick> <n>\n",
            zh="[*] 撤回：/leave <昵称> <编号>\n",
            locale=conn_locale(conn),
        ),
    )


def handle_leave_command(conn, name: str, parts: list[str]) -> None:
    """List or recall unread leave-messages sent by this user."""
    # handle_command uses split(None, 1), so parts[1] is the whole remainder.
    # /leave | /leave <nick> | /leave <nick> <n>
    # /leave recall <nick> <n> | /leave 撤回 <nick> <n>
    rest = parts[1] if len(parts) > 1 else ""
    args = rest.split()
    if not args:
        _send_leave_list(conn, name)
        return
    if args[0].lower() in ("recall", "unmsg", "撤回", "撤销"):
        if len(args) < 3:
            send_line(conn, _ts(conn, "leave_usage"))
            return
        target = args[1].strip()
        num_raw = args[2].strip()
    elif len(args) >= 2 and args[1].strip().isdigit():
        target = args[0].strip()
        num_raw = args[1].strip()
    elif len(args) == 1:
        _send_leave_list(conn, name, args[0].strip())
        return
    else:
        send_line(conn, _ts(conn, "leave_usage"))
        return
    try:
        index = int(num_raw)
    except ValueError:
        send_line(conn, _ts(conn, "leave_bad_index"))
        return
    removed = offline_messages.recall(name, target, index)
    if removed is None:
        send_line(
            conn,
            _ts(conn, "leave_recall_fail", recipient=target, index=index),
        )
        return
    _revoke_recalled_file(removed, target)
    lid = str(removed.get("id") or "").strip()
    if lid and (removed.get("kind") or "pm") != "file":
        _federation_clear_offline_pm(target, lid)
    when = _format_offline_ts(removed.get("ts", 0))
    kind = i18n.tr(
        en="file" if (removed.get("kind") or "pm") == "file" else "leave-message",
        zh="文件" if (removed.get("kind") or "pm") == "file" else "留言",
        locale=conn_locale(conn),
    )
    send_line(
        conn,
        _ts(
            conn,
            "leave_recalled",
            kind=kind,
            index=index,
            recipient=target,
            when=when,
            text=removed.get("text"),
        ),
    )


def send_private_messages(conn, sender_name: str, target_nick: str, text: str) -> None:
    """Deliver a private message to all matching nicks; leave a message if offline."""
    targets = find_clients_by_nickname(target_nick)
    hub = federation.get_hub()
    remote_sent = False
    if hub is not None and hub.enabled and hub.has_remote_user(target_nick):
        remote_sent = hub.send_pm(target_nick, sender_name, text)
    if not targets and not remote_sent:
        stored = offline_messages.leave(target_nick, sender_name, text)
        if stored is None:
            send_line(
                conn,
                f"[*] No one online named {target_nick!r} (match is case-insensitive)\n",
            )
            return
        if hub is not None and hub.enabled:
            try:
                hub.broadcast_offline_pm(
                    target_nick,
                    sender_name,
                    text,
                    leave_id=str(stored.get("id") or ""),
                    ts=float(stored.get("ts") or 0) or None,
                )
            except Exception as e:
                print(f"federation: broadcast_offline_pm failed: {e!r}")
        send_line(
            conn,
            f"[*] {target_nick!r} 当前不在线，已留言；对方下次上线时会收到。\n",
        )
        return
    for peer_conn, peer_name in targets:
        send_line(peer_conn, f"[PM from {sender_name}] {text}\n")
    # Echo the private message to the sender as a PM, not as room chat.
    # Avoid duplicating it when the sender targets their own connection.
    if not any(peer_conn is conn for peer_conn, _ in targets):
        send_line(conn, f"[PM from {sender_name}] {text}\n")
    if remote_sent and not targets:
        send_line(conn, f"[*] PM → {target_nick} (federated)\n")
    elif len(targets) == 1 and not remote_sent:
        only = targets[0][1]
        send_line(conn, f"[*] PM → {only}\n")
    elif targets or remote_sent:
        n = len(targets)
        extra = " + federated peers" if remote_sent else ""
        send_line(
            conn,
            f"[*] PM sent to {n} user(s) matching {target_nick!r}{extra}\n",
        )


def find_clients_by_nickname(nick: str, *, local_only: bool = False) -> list[tuple]:
    """Return [(conn, display_name), ...] for online users matching nick (case-insensitive)."""
    key = nick.strip().lower()
    if not key:
        return []
    with lock:
        return [
            (c, clients[c]["name"])
            for c in list(clients)
            if c in clients and clients[c]["name"].lower() == key
        ]


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _clean_feed_text(text: Optional[str]) -> str:
    if not text:
        return ""
    text = unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())


def _entry_title(entry) -> str:
    for child in list(entry):
        if _local_name(child.tag) == "title":
            return _clean_feed_text("".join(child.itertext()))
    return ""


def _entry_body(entry) -> str:
    """RSS description / Atom summary+content / content:encoded — plain text, best-effort."""
    best = ""
    for child in list(entry):
        ln = _local_name(child.tag)
        if ln not in ("description", "summary", "content", "encoded"):
            continue
        raw = "".join(child.itertext())
        if not raw.strip():
            continue
        t = _clean_feed_text(raw)
        if len(t) > len(best):
            best = t
    if len(best) > NEWS_BODY_FETCH_CAP:
        best = best[:NEWS_BODY_FETCH_CAP].rstrip() + "…"
    return best


def _entry_link(entry) -> str:
    """RSS <link> or Atom <link href>; fallback http(s) in <guid>."""
    best = ""
    for child in list(entry):
        if _local_name(child.tag) != "link":
            continue
        href = (child.attrib.get("href") or "").strip()
        rel = (child.attrib.get("rel") or "").lower()
        if href and ("alternate" in rel or rel in ("", "self")):
            if "alternate" in rel:
                return href
            if not best:
                best = href
        if not href and child.text:
            t = _clean_feed_text(child.text)
            if t.startswith(("http://", "https://")):
                best = t or best
    if best:
        return best
    for child in list(entry):
        if _local_name(child.tag) != "guid":
            continue
        t = _clean_feed_text("".join(child.itertext()))
        if t.startswith(("http://", "https://")):
            return t
    return ""


def _news_proxy_url() -> str:
    """HTTPS/HTTP proxy for RSS fetches (runs on the host that executes server.py).

    Priority: SSHCHAT_NEWS_* / standard proxy env, then local default 127.0.0.1:7897.
    Set SSHCHAT_NEWS_NO_PROXY=1 to disable proxy entirely (direct fetch).
    """
    if os.environ.get("SSHCHAT_NEWS_NO_PROXY", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return ""
    for key in (
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
    return NEWS_PROXY_LOCAL_DEFAULT or ""


def _news_transport_retryable(exc: BaseException) -> bool:
    """SSL/代理/握手类错误：可换 TLS 策略或改走直连重试。"""
    if isinstance(exc, ssl.SSLError):
        return True
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, ssl.SSLError):
            return True
        if isinstance(reason, (TimeoutError, socket.timeout, ConnectionResetError)):
            return True
        if isinstance(reason, OSError) and reason.errno in (54, 104):
            return True
    msg = str(exc)
    return any(
        token in msg
        for token in (
            "CERTIFICATE_VERIFY_FAILED",
            "SSLEOFError",
            "SSL: ",
            "UNEXPECTED_EOF",
            "EOF occurred",
            "handshake operation timed out",
            "Connection reset",
        )
    )


def _news_urlopen_strategies() -> list[tuple[Optional[ssl.SSLContext], bool]]:
    """(ssl_context, use_proxy) 尝试顺序：代理→TLS 放宽→（可选）代理失败后直连。"""
    proxy = _news_proxy_url()
    strategies: list[tuple[Optional[ssl.SSLContext], bool]] = []
    if proxy:
        strategies.append((None, True))
        if NEWS_TLS_FALLBACK:
            strategies.append((ssl._create_unverified_context(), True))
        if NEWS_PROXY_FALLBACK_DIRECT:
            strategies.append((None, False))
            if NEWS_TLS_FALLBACK:
                strategies.append((ssl._create_unverified_context(), False))
    else:
        strategies.append((None, False))
        if NEWS_TLS_FALLBACK:
            strategies.append((ssl._create_unverified_context(), False))
    return strategies


def _news_build_opener(
    ssl_context: Optional[ssl.SSLContext] = None, *, use_proxy: bool = True
):
    """Return a custom opener, or None to use urllib.request.urlopen defaults."""
    handlers: list = []
    proxy = _news_proxy_url() if use_proxy else ""
    if proxy:
        handlers.append(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
    if ssl_context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=ssl_context))
    if not handlers:
        return None
    return urllib.request.build_opener(*handlers)


def _read_stream_limit(resp, max_bytes: int) -> bytes:
    out = bytearray()
    while len(out) < max_bytes:
        chunk = resp.read(min(65536, max_bytes - len(out)))
        if not chunk:
            break
        out.extend(chunk)
    return bytes(out)


def _charset_from_headers(resp) -> Optional[str]:
    ctype = resp.headers.get("Content-Type") or ""
    m = re.search(r"charset=([\w-]+)", ctype, re.I)
    if m:
        return m.group(1).strip().strip('"').strip("'")
    return None


def _news_urlopen_read_limited(
    req: urllib.request.Request, timeout: float, max_bytes: int
) -> tuple[bytes, Optional[str]]:
    """GET up to max_bytes; returns (data, charset from Content-Type if any)."""

    def _once(
        ssl_ctx: Optional[ssl.SSLContext], use_proxy: bool
    ) -> tuple[bytes, Optional[str]]:
        opener = _news_build_opener(ssl_ctx, use_proxy=use_proxy)
        if opener is not None:
            resp = opener.open(req, timeout=timeout)
        else:
            if ssl_ctx is not None:
                resp = urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx)
            else:
                resp = urllib.request.urlopen(req, timeout=timeout)
        try:
            return _read_stream_limit(resp, max_bytes), _charset_from_headers(resp)
        finally:
            resp.close()

    last_exc: Optional[BaseException] = None
    for ssl_ctx, use_proxy in _news_urlopen_strategies():
        try:
            return _once(ssl_ctx, use_proxy)
        except Exception as e:
            if not _news_transport_retryable(e):
                raise
            last_exc = e
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("news urlopen: no strategies")


def _news_http_get(req: urllib.request.Request) -> bytes:
    """GET response body; optional proxy; TLS verify fallback for broken CA bundles."""
    data, _charset = _news_urlopen_read_limited(req, NEWS_FETCH_TIMEOUT, 512 * 1024)
    return data


def _safe_http_url(raw: str) -> Optional[str]:
    u = (raw or "").strip()
    if not u:
        return None
    p = urlparse(u)
    if p.scheme not in ("http", "https") or not p.netloc:
        return None
    return u


def _decode_html_bytes(data: bytes, charset: Optional[str]) -> str:
    # Try to extract charset from HTML meta tags
    meta_charset = None
    try:
        # Try to decode first 2KB as ASCII-compatible to find meta charset
        head_sample = data[:2048].decode("ascii", errors="ignore").lower()
        # Look for <meta charset="...">
        m = re.search(r'<meta\s+charset=["\']?([\w-]+)', head_sample, re.I)
        if m:
            meta_charset = m.group(1)
        else:
            # Look for <meta http-equiv="Content-Type" content="...charset=...">
            m = re.search(
                r'<meta\s+http-equiv=["\']?content-type["\']?\s+content=["\']?[^"\']*charset=([\w-]+)',
                head_sample,
                re.I,
            )
            if not m:
                m = re.search(
                    r'<meta\s+content=["\']?[^"\']*charset=([\w-]+)[^"\']*["\']?\s+http-equiv=["\']?content-type',
                    head_sample,
                    re.I,
                )
            if m:
                meta_charset = m.group(1)
    except Exception:
        pass
    
    # Try charsets in order: HTTP header, HTML meta, common Chinese encodings
    for enc in (charset, meta_charset, "utf-8", "gb18030", "gbk", "gb2312", "latin-1"):
        if not enc:
            continue
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def _html_pick_main_fragment(html: str) -> str:
    for tag in ("article", "main"):
        m = re.search(rf"(?is)<{tag}\b[^>]*>(.*)</{tag}>", html)
        if m and len(m.group(1).strip()) > 200:
            return m.group(1)
    m = re.search(r"(?is)<body\b[^>]*>(.*)</body>", html)
    if m:
        return m.group(1)
    return html


def _html_to_plain_text(html: str) -> str:
    frag = _html_pick_main_fragment(html)
    frag = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", frag)
    frag = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", frag)
    frag = re.sub(r"(?is)<noscript[^>]*>.*?</noscript>", " ", frag)
    frag = re.sub(r"(?is)<!--.*?-->", " ", frag)
    frag = re.sub(
        r"(?is)<(header|footer|nav|aside)\b[^>]*>.*?</\1>", " ", frag
    )
    frag = re.sub(r"<[^>]+>", " ", frag)
    text = unescape(frag)
    return " ".join(text.split())


def _fetch_article_plain(url: str) -> str:
    cached = None
    with article_fetch_lock:
        ent = article_fetch_cache.get(url)
        if ent and time.monotonic() - ent[0] < NEWS_PAGE_FETCH_CACHE_TTL:
            cached = ent[1]
    if cached is not None:
        return cached

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            # 经 HTTP 代理时避免长连接/压缩流被中途掐断导致 SSLEOFError
            "Connection": "close",
            "Accept-Encoding": "identity",
        },
    )
    data, charset = _news_urlopen_read_limited(
        req, NEWS_PAGE_TIMEOUT, NEWS_PAGE_MAX_BYTES
    )
    html = _decode_html_bytes(data, charset)
    text = _html_to_plain_text(html)
    with article_fetch_lock:
        article_fetch_cache[url] = (time.monotonic(), text)
        if len(article_fetch_cache) > _ARTICLE_CACHE_MAX:
            oldest_key = min(article_fetch_cache, key=lambda k: article_fetch_cache[k][0])
            del article_fetch_cache[oldest_key]
    return text


def _send_news_item_fetched(conn, category: str, index_1based: int) -> None:
    label = NEWS_CATEGORIES[category]["label"]
    items = _get_news_items(category)
    if not items:
        send_line(conn, "[*] 暂时没有取到新闻；稍后再试。\n")
        return
    if index_1based < 1 or index_1based > len(items):
        send_line(
            conn,
            f"[*] 无效序号：「{label}」当前共 {len(items)} 条，请用 1～{len(items)}。\n",
        )
        return
    item = items[index_1based - 1]
    link = _safe_http_url(item.get("link") or "")
    send_line(conn, f"[*] --- {label} 第 {index_1based} 条（网页正文）---\n")
    send_line(conn, f"[*] [{item['source']}] {item['title']}\n")
    if not link:
        send_line(conn, "[*] RSS 未提供可用的 http(s) 链接，无法抓取正文。\n")
        return
    try:
        plain = _fetch_article_plain(link)
    except Exception as e:
        send_line(conn, f"[*] 抓取失败：{e!r}\n")
        send_line(
            conn,
            "[*] 常见原因：付费墙、需登录、反爬、仅 JS 渲染页面、超时，"
            "或代理 HTTPS 异常（SSLEOF/握手失败）。\n",
        )
        send_line(
            conn,
            "[*] 可试：加大 SSHCHAT_NEWS_PAGE_TIMEOUT；确认代理端口；"
            "或 SSHCHAT_NEWS_NO_PROXY=1 直连（境外源在境内机房常需代理）。\n",
        )
        return
    if not plain.strip():
        send_line(conn, "[*] 页面无可见正文（可能被脚本独占或结构特殊）。\n")
        return
    send_line(conn, "[*] （以下为抽取的正文，可能不完整）\n")
    _send_wrapped_news_body(conn, plain, NEWS_PAGE_TEXT_MAX)
    send_line(
        conn,
        "[*] 说明：纯 HTML 粗提取，非官方 API；超长已截断，完整排版请用浏览器打开原站。\n",
    )


def _fetch_feed(source: str, url: str, per_feed_limit: int = 3) -> list[dict[str, str]]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "SSHChat/1.0 (+https://github.com/) RSS reader",
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml",
        },
    )
    data = _news_http_get(req)
    root = ET.fromstring(data)

    entries = [e for e in root.iter() if _local_name(e.tag) in {"item", "entry"}]
    items: list[dict[str, str]] = []
    for entry in entries:
        title = _entry_title(entry)
        if not title:
            continue
        body = _entry_body(entry)
        link = _entry_link(entry)
        items.append({"source": source, "title": title, "body": body, "link": link})
        if len(items) >= per_feed_limit:
            break
    return items


def _get_news_items(category: str) -> list[dict[str, str]]:
    now = time.monotonic()
    with news_cache_lock:
        cached = news_cache.get(category)
        if cached and now - cached[0] < NEWS_CACHE_TTL:
            return list(cached[1])
        if category in news_fetching:
            # Another thread is already fetching this category; wait and return cached
            pass
        else:
            news_fetching.add(category)

    # If we're not the fetcher, wait briefly and return whatever is cached
    if category not in news_fetching:
        time.sleep(0.5)
        with news_cache_lock:
            cached = news_cache.get(category)
            if cached:
                return list(cached[1])
        return []

    feeds = NEWS_CATEGORIES[category]["feeds"]
    try:
        by_source: dict[str, list[dict[str, str]]] = {}
        with ThreadPoolExecutor(max_workers=min(5, len(feeds))) as executor:
            futures = {
                executor.submit(_fetch_feed, source, url): source
                for source, url in feeds
            }
            for future in as_completed(futures):
                try:
                    by_source[futures[future]] = future.result()
                except Exception as e:
                    print(f"news feed error ({futures[future]}): {e!r}")

        fetched: list[dict[str, str]] = []
        for source, _url in feeds:
            fetched.extend(by_source.get(source, ()))

        seen_titles: set[str] = set()
        deduped: list[dict[str, str]] = []
        for item in fetched:
            key = item["title"].lower()
            if key in seen_titles:
                continue
            seen_titles.add(key)
            deduped.append(item)

        with news_cache_lock:
            news_cache[category] = (time.monotonic(), list(deduped))
        return deduped
    finally:
        with news_cache_lock:
            news_fetching.discard(category)


def _parse_news_limit(raw: str, default: int) -> int:
    if not raw:
        return default
    try:
        return max(1, min(NEWS_MAX_LIMIT, int(raw)))
    except ValueError:
        return default


def _send_news_usage(conn) -> None:
    send_line(conn, "[*] Usage: /news [中文|国际|科技|all] [条数]\n")
    send_line(conn, "[*]        /news detail <分类> <序号>  （别名：详情）RSS 更长提要\n")
    send_line(conn, "[*]        /news fetch <分类> <序号>  （别名：全文）按链接抓取网页正文\n")
    send_line(conn, "[*] Aliases: cn/zh, world/intl, tech. Examples: /news 中文, /news fetch cn 2\n")


def _resolve_news_category(cat_token: str) -> Optional[str]:
    return NEWS_ALIASES.get(cat_token.lower()) or NEWS_ALIASES.get(cat_token)


def _send_wrapped_news_body(conn, raw_body: str, max_chars: int) -> None:
    raw_body = (raw_body or "").strip()
    if not raw_body:
        send_line(conn, "[*]    （提要暂缺）\n")
        return
    if len(raw_body) > max_chars:
        raw_body = raw_body[: max_chars - 1].rstrip() + "…"
    for wrap_ln in textwrap.wrap(
        raw_body,
        width=max(40, NEWS_WRAP_WIDTH),
        break_long_words=True,
        break_on_hyphens=False,
    ):
        send_line(conn, f"[*]    {wrap_ln}\n")


def _send_news_item_detail(conn, category: str, index_1based: int) -> None:
    label = NEWS_CATEGORIES[category]["label"]
    items = _get_news_items(category)
    if not items:
        send_line(conn, "[*] 暂时没有取到新闻；稍后再试。\n")
        return
    if index_1based < 1 or index_1based > len(items):
        send_line(
            conn,
            f"[*] 无效序号：「{label}」当前共 {len(items)} 条，请用 1～{len(items)}（与列表中的编号一致）。\n",
        )
        return
    item = items[index_1based - 1]
    send_line(conn, f"[*] --- {label} 第 {index_1based} 条 ---\n")
    send_line(conn, f"[*] [{item['source']}] {item['title']}\n")
    _send_wrapped_news_body(conn, item.get("body") or "", NEWS_DETAIL_MAX_CHARS)


def _send_news_section(conn, category: str, limit: int) -> None:
    label = NEWS_CATEGORIES[category]["label"]
    send_line(conn, f"[*] --- {label} ---\n")
    items = _get_news_items(category)[:limit]
    if not items:
        send_line(conn, "[*] 暂时没有取到新闻；稍后再试。\n")
        return
    for idx, item in enumerate(items, start=1):
        send_line(conn, f"[*] {idx}. [{item['source']}] {item['title']}\n")
        _send_wrapped_news_body(conn, item.get("body") or "", NEWS_BODY_MAX_CHARS)


def _handle_news(conn, payload: str) -> None:
    raw = payload[len("/news") :].strip()
    if not raw:
        for category in ("cn", "world", "tech"):
            _send_news_section(conn, category, NEWS_DEFAULT_ALL_LIMIT)
        send_line(conn, "[*] 可用：/news 中文、/news 国际、/news 科技、/news all 5\n")
        send_line(conn, "[*] 单条更长提要：/news detail 中文 2（序号与上表一致；别名：详情）\n")
        send_line(conn, "[*] 抓取网页正文：/news fetch 中文 2（别名：全文；较慢，可能失败）\n")
        return

    parts = raw.split()
    head = parts[0]
    if head.lower() in ("detail", "详情"):
        if len(parts) < 3:
            send_line(conn, "[*] Usage: /news detail <中文|国际|科技|cn|…> <序号>\n")
            send_line(conn, "[*] 例：/news 中文  看列表编号，再  /news detail 中文 2\n")
            return
        category = _resolve_news_category(parts[1])
        if not category:
            _send_news_usage(conn)
            return
        try:
            n = int(parts[2])
        except ValueError:
            send_line(conn, "[*] 序号须为整数，例如：/news detail 国际 1\n")
            return
        _send_news_item_detail(conn, category, n)
        send_line(
            conn,
            "[*] 说明：详情仍是 RSS 里的提要文字；若仍很短，说明该源未给更长摘要（非网页全文）。\n",
        )
        return

    if head.lower() in ("fetch", "全文"):
        if len(parts) < 3:
            send_line(conn, "[*] Usage: /news fetch <中文|国际|科技|cn|…> <序号>\n")
            send_line(conn, "[*] 例：/news 中文  看编号后  /news fetch 中文 2  或  /news 全文 cn 1\n")
            return
        category = _resolve_news_category(parts[1])
        if not category:
            _send_news_usage(conn)
            return
        try:
            n = int(parts[2])
        except ValueError:
            send_line(conn, "[*] 序号须为整数，例如：/news fetch 国际 1\n")
            return
        _send_news_item_fetched(conn, category, n)
        return

    topic = head.lower()
    if topic in {"help", "?"}:
        _send_news_usage(conn)
        return

    if topic in {"all", "全部"}:
        limit = _parse_news_limit(parts[1] if len(parts) > 1 else "", NEWS_DEFAULT_ALL_LIMIT)
        for category in ("cn", "world", "tech"):
            _send_news_section(conn, category, limit)
        return

    category = _resolve_news_category(topic)
    if not category:
        _send_news_usage(conn)
        return
    limit = _parse_news_limit(parts[1] if len(parts) > 1 else "", NEWS_DEFAULT_CATEGORY_LIMIT)
    _send_news_section(conn, category, limit)


def _library_dir() -> Path:
    return library.default_library_dir()


def _client_name(conn) -> str:
    with lock:
        info = clients.get(conn)
    return str(info["name"]) if info else "Unknown"


def _library_bookmark_key(origin: str, name: str) -> str:
    """Bookmark id is the bare filename so local and federated opens share progress."""
    _ = origin  # origin only matters for catalog display / page fetch
    return library.bookmark_bare_name(name)


def _fed_local_library_snapshot() -> list[dict]:
    lib_dir = _library_dir()
    if not lib_dir.is_dir():
        return []
    return [library.book_entry_to_meta(entry) for entry in library.list_books(lib_dir)]


def _union_library_catalog() -> list[library.CatalogItem]:
    lib_dir = _library_dir()
    local = library.list_books(lib_dir) if lib_dir.is_dir() else []
    remote: dict[str, list[dict]] = {}
    hub = federation.get_hub()
    if hub is not None and hub.enabled:
        remote = hub.remote_library_catalogs()
    return library.merge_federated_catalog(local, remote)


def _federation_sync_library_catalog() -> None:
    hub = federation.get_hub()
    if hub is None or not hub.enabled:
        return
    try:
        hub.sync_library_catalog(_fed_local_library_snapshot())
    except Exception as e:
        print(f"federation: library catalog sync failed: {e!r}")


def _get_library_state() -> tuple[set[str], float]:
    """Get current library directory state (file names + mtime)."""
    lib_dir = _library_dir()
    if not lib_dir.is_dir():
        return (set(), 0.0)
    try:
        dir_mtime = lib_dir.stat().st_mtime
        files = {
            path.name
            for path in lib_dir.iterdir()
            if path.is_file() and path.suffix.lower() in library.LIBRARY_EXTENSIONS
        }
        return (files, dir_mtime)
    except OSError:
        return (set(), 0.0)


def _library_watch_loop() -> None:
    """Background thread that monitors library directory for changes."""
    global _library_last_state
    
    # Get initial state
    _library_last_state = _get_library_state()
    
    # Check interval in seconds
    watch_interval = float(os.environ.get("SSHCHAT_LIBRARY_WATCH_SECONDS", "5"))
    watch_interval = max(1.0, watch_interval)
    
    print(f"federation: library watch started (interval={watch_interval}s)")
    
    while not _library_watch_stop.is_set():
        time.sleep(watch_interval)
        
        if _library_watch_stop.is_set():
            break
        
        hub = federation.get_hub()
        if hub is None or not hub.enabled:
            continue
        
        try:
            current_state = _get_library_state()
            
            if _library_last_state is None:
                _library_last_state = current_state
                continue
            
            prev_files, prev_mtime = _library_last_state
            curr_files, curr_mtime = current_state
            
            # Check if there are changes (new/removed files or directory mtime changed)
            if prev_files != curr_files or abs(curr_mtime - prev_mtime) > 0.01:
                added = curr_files - prev_files
                removed = prev_files - curr_files
                
                if added or removed:
                    print(
                        f"federation: library changed "
                        f"(+{len(added)} -{len(removed)}), syncing catalog"
                    )
                elif curr_mtime != prev_mtime:
                    print("federation: library directory modified, syncing catalog")
                
                # Sync catalog to federation peers
                _federation_sync_library_catalog()
                _library_last_state = current_state
                
        except Exception as e:
            print(f"federation: library watch error: {e!r}")
    
    print("federation: library watch stopped")


def _fed_local_library_bookmarks_snapshot() -> list[dict]:
    """Bookmarks for locally connected nicks (for peer catch-up)."""
    with lock:
        names = {str(info.get("name") or "").strip() for info in clients.values()}
    rows: list[dict] = []
    for name in sorted(names, key=lambda n: n.lower()):
        if not name:
            continue
        books = library_bookmarks.export_user(name)
        if books:
            rows.append({"name": name, "books": books})
    return rows


def _federation_sync_library_bookmarks(
    user: str, entries: Optional[dict] = None
) -> None:
    hub = federation.get_hub()
    if hub is None or not hub.enabled:
        return
    if entries is None:
        entries = library_bookmarks.export_user(user)
    if not entries:
        return
    try:
        hub.sync_library_bookmarks(user, entries)
    except Exception as e:
        print(f"federation: library bookmark sync failed: {e!r}")


def _fed_on_library_bookmarks(_origin: str, nick: str, books: dict) -> None:
    if not isinstance(books, dict) or not nick:
        return
    try:
        library_bookmarks.merge_from_remote(nick, books)
    except Exception as e:
        print(f"federation: merge library bookmarks failed: {e!r}")


def _fed_on_library_bookmark_clear(nick: str, book_name: str) -> None:
    """Owner-node handler: clear a nick's bookmark for a local book."""
    cleared = library_bookmarks.clear_book(nick, book_name)
    if cleared:
        _federation_sync_library_bookmarks(nick, cleared)


def _fed_on_library_page_request(
    _owner: str,
    req_id: str,
    book_name: str,
    page: int,
    requester: str,
    nick: str = "",
    flags: str = "",
    query: str = "",
) -> None:
    """Serve one page from this node's library; bookmarks live on the owner node."""
    hub = federation.get_hub()
    if hub is None:
        return
    book_name = Path(str(book_name or "")).name
    nick = str(nick or "").strip()
    flags = str(flags or "").lower()
    resume = "r" in flags
    save = "s" in flags
    find = "f" in flags
    payload: dict = {"ok": False, "error": "not found", "req_id": req_id}
    try:
        lib_dir = _library_dir()
        catalog = library.list_books(lib_dir) if lib_dir.is_dir() else []
        entry = next((e for e in catalog if e.name == book_name), None)
        if entry is None:
            payload["error"] = f"book not found: {book_name}"
        else:
            doc = _get_cached_book(entry.path)
            total = doc.total_pages
            if find:
                q = str(query or "").strip()[:200]
                hits = library.search_book(doc, q) if q else []
                payload = {
                    "ok": True,
                    "req_id": req_id,
                    "name": entry.name,
                    "title": doc.title,
                    "total_pages": total,
                    "query": q,
                    "results": [{"page": p, "snippet": s} for p, s in hits],
                }
            else:
                bookmark_page = (
                    library_bookmarks.get_page(nick, book_name) if nick else None
                )
                had_bookmark = bookmark_page is not None
                if resume:
                    page = bookmark_page if had_bookmark else 0
                page = max(0, min(int(page), total - 1))
                if nick and save:
                    entries = library_bookmarks.set_page(nick, book_name, page)
                    _federation_sync_library_bookmarks(nick, entries)
                    bookmark_page = page
                text = str(doc.pages[page] or "")
                if len(text) > _FED_LIBRARY_PAGE_MAX_CHARS:
                    text = (
                        text[:_FED_LIBRARY_PAGE_MAX_CHARS]
                        + "\n…（本页过长，联邦传输已截断）"
                    )
                payload = {
                    "ok": True,
                    "req_id": req_id,
                    "name": entry.name,
                    "title": doc.title,
                    "page": page,
                    "total_pages": total,
                    "text": text,
                    "ext": entry.ext,
                    "size_bytes": entry.size_bytes,
                    "bookmark_page": (
                        bookmark_page if bookmark_page is not None else page
                    ),
                    "resumed": bool(resume and had_bookmark),
                }
    except Exception as e:
        payload = {"ok": False, "error": str(e), "req_id": req_id}
    try:
        hub.reply_library_page(requester, req_id, payload)
    except Exception as e:
        print(f"federation: reply_library_page failed: {e!r}")


def _fed_local_file_public() -> str:
    """Public file base URL advertised to federation peers (empty if LAN-only)."""
    if file_http is None:
        return ""
    try:
        base = file_http.get_base_url()
    except Exception:
        return ""
    if not file_http_server.is_externally_reachable_url(base):
        return ""
    return base.rstrip("/")


def _fed_on_file_host_request(
    requester: str, req_id: str, payload: dict
) -> None:
    """Peer asked us to host a /sendfile or /canvas session on our public URL."""
    hub = federation.get_hub()
    if hub is None:
        return
    mode = str(payload.get("mode") or "file").strip().lower()
    if mode == "canvas":
        reply: dict = {
            "ok": False,
            "error": "canvas unavailable",
            "req_id": req_id,
            "mode": "canvas",
        }
        try:
            if file_http is None:
                reply["error"] = "canvas disabled on host"
            elif not file_http_server.is_externally_reachable_url(
                file_http.get_base_url()
            ):
                reply["error"] = "host has no public file URL"
            else:
                creator = str(payload.get("creator") or "").strip()
                participants = payload.get("participants") or []
                if not isinstance(participants, list):
                    participants = []
                participants = [
                    str(p).strip() for p in participants if str(p).strip()
                ]
                room = payload.get("room")
                room_name = str(room).strip() if room else None
                title = str(payload.get("title") or "").strip()
                if not creator or not participants:
                    reply["error"] = "invalid creator/participants"
                else:
                    session = canvas_sharing.canvas_store.create_session(
                        creator=creator,
                        participants=participants,
                        room=room_name,
                        title=title,
                    )
                    base_url = file_http.get_base_url().rstrip("/")
                    reply = {
                        "ok": True,
                        "req_id": req_id,
                        "mode": "canvas",
                        "host_node": hub.node_id,
                        "base_url": base_url,
                        "session_id": session.session_id,
                        "creator": session.creator,
                        "room": room_name,
                        "tokens": dict(session.tokens),
                        "keys": dict(session.keys),
                        "title": session.title,
                        "expires": session.expires,
                        "conflict_token": session.conflict_token,
                        "rev": session.rev,
                    }
                    print(
                        f"[Canvas] Hosted federated canvas for {requester}: "
                        f"creator={creator} participants={len(participants)} "
                        f"via {base_url}"
                    )
        except Exception as e:
            print(f"[Canvas] federated host error: {e!r}")
            traceback.print_exc()
            reply = {"ok": False, "error": str(e), "req_id": req_id, "mode": "canvas"}
        try:
            hub.reply_file_host(requester, req_id, reply)
        except Exception as e:
            print(f"federation: reply_file_host (canvas) failed: {e!r}")
        return
    if mode == "canvas_close":
        reply = {
            "ok": False,
            "error": "canvas unavailable",
            "req_id": req_id,
            "mode": "canvas_close",
        }
        try:
            session_id = str(payload.get("session_id") or "").strip()
            by_user = str(payload.get("by_user") or "").strip()
            if not session_id or not by_user:
                reply["error"] = "invalid session_id/by_user"
            else:
                ok, err = canvas_sharing.canvas_store.close_session(
                    session_id, by_user
                )
                reply = {
                    "ok": ok,
                    "req_id": req_id,
                    "mode": "canvas_close",
                    "error": err if not ok else "",
                }
        except Exception as e:
            print(f"[Canvas] federated close error: {e!r}")
            traceback.print_exc()
            reply = {
                "ok": False,
                "error": str(e),
                "req_id": req_id,
                "mode": "canvas_close",
            }
        try:
            hub.reply_file_host(requester, req_id, reply)
        except Exception as e:
            print(f"federation: reply_file_host (canvas_close) failed: {e!r}")
        return
    if mode == "canvas_join":
        reply = {
            "ok": False,
            "error": "canvas unavailable",
            "req_id": req_id,
            "mode": "canvas_join",
        }
        try:
            session_id = str(payload.get("session_id") or "").strip()
            nick = str(payload.get("nick") or "").strip()
            if not session_id or not nick:
                reply["error"] = "invalid session_id/nick"
            else:
                canvas_sharing.canvas_store.rotate_keys_if_due(session_id)
                token, key, err = canvas_sharing.canvas_store.add_participant(
                    session_id, nick
                )
                if err or not token:
                    reply["error"] = err or "join failed"
                else:
                    reply = {
                        "ok": True,
                        "req_id": req_id,
                        "mode": "canvas_join",
                        "token": token,
                        "key": key,
                        "session_id": session_id,
                        "nick": nick,
                    }
        except Exception as e:
            print(f"[Canvas] federated join error: {e!r}")
            traceback.print_exc()
            reply = {
                "ok": False,
                "error": str(e),
                "req_id": req_id,
                "mode": "canvas_join",
            }
        try:
            hub.reply_file_host(requester, req_id, reply)
        except Exception as e:
            print(f"federation: reply_file_host (canvas_join) failed: {e!r}")
        return
    if mode == "canvas_query":
        reply = {
            "ok": True,
            "found": False,
            "req_id": req_id,
            "mode": "canvas_query",
        }
        try:
            room_name = str(payload.get("room") or "").strip()
            session = (
                canvas_sharing.canvas_store.find_open_for_room(room_name)
                if room_name
                else None
            )
            if session is not None:
                ann = canvas_sharing.canvas_store.announce_dict(session)
                if not ann.get("host_node"):
                    ann["host_node"] = hub.node_id
                if not ann.get("base_url"):
                    if file_http is not None:
                        ann["base_url"] = file_http.get_base_url().rstrip("/")
                if ann.get("base_url"):
                    reply = {
                        "ok": True,
                        "found": True,
                        "req_id": req_id,
                        "mode": "canvas_query",
                        **ann,
                    }
        except Exception as e:
            print(f"[Canvas] federated query error: {e!r}")
            traceback.print_exc()
            reply = {
                "ok": False,
                "found": False,
                "error": str(e),
                "req_id": req_id,
                "mode": "canvas_query",
            }
        try:
            hub.reply_file_host(requester, req_id, reply)
        except Exception as e:
            print(f"federation: reply_file_host (canvas_query) failed: {e!r}")
        return

    reply: dict = {"ok": False, "error": "file transfer unavailable", "req_id": req_id}
    try:
        if file_http is None:
            reply["error"] = "file transfer disabled on host"
        elif not file_http_server.is_externally_reachable_url(file_http.get_base_url()):
            reply["error"] = "host has no public file URL"
        else:
            sender = str(payload.get("sender") or "").strip()
            recipients = payload.get("recipients") or []
            if not isinstance(recipients, list):
                recipients = []
            recipients = [str(r).strip() for r in recipients if str(r).strip()]
            room = payload.get("room")
            room_name = str(room).strip() if room else None
            if not sender or not recipients:
                reply["error"] = "invalid sender/recipients"
            else:
                store = file_sharing.file_transfer_store
                transfer = store.create_upload_session(
                    sender=sender,
                    recipients=recipients,
                    room=room_name,
                )
                base_url = file_http.get_base_url().rstrip("/")
                reply = {
                    "ok": True,
                    "req_id": req_id,
                    "host_node": hub.node_id,
                    "base_url": base_url,
                    "transfer_id": transfer.transfer_id,
                    "upload_token": transfer.upload_token,
                    "upload_key": transfer.upload_key,
                    "upload_url": f"{base_url}/upload/{transfer.upload_token}",
                    "download_tokens": dict(transfer.download_tokens),
                    "download_keys": dict(transfer.download_keys),
                    "room": room_name,
                    "sender": sender,
                    "recipients": recipients,
                }
                print(
                    f"[FileTransfer] Hosted federated upload for {requester}: "
                    f"sender={sender} recipients={len(recipients)} via {base_url}"
                )
    except Exception as e:
        print(f"[FileTransfer] federated host error: {e!r}")
        traceback.print_exc()
        reply = {"ok": False, "error": str(e), "req_id": req_id}
    try:
        hub.reply_file_host(requester, req_id, reply)
    except Exception as e:
        print(f"federation: reply_file_host failed: {e!r}")


def _fed_on_file_host_result(_from_peer: str, req_id: str, payload: dict) -> None:
    with _file_host_waiters_lock:
        waiter = _file_host_waiters.get(req_id)
        if waiter is None:
            return
        waiter["payload"] = payload
        event = waiter.get("event")
        if isinstance(event, threading.Event):
            event.set()


def _federation_request_canvas_host(
    host_node: str,
    creator: str,
    participants: list[str],
    room: Optional[str],
    *,
    title: str = "",
    timeout: float | None = None,
) -> dict:
    return _federation_request_file_host(
        host_node,
        creator,
        participants,
        room,
        timeout=timeout,
        mode="canvas",
        title=title,
    )


def _federation_request_canvas_close(
    host_node: str,
    session_id: str,
    by_user: str,
    *,
    timeout: float | None = None,
) -> dict:
    return _federation_request_file_host(
        host_node,
        "",
        [],
        None,
        timeout=timeout,
        mode="canvas_close",
        session_id=session_id,
        by_user=by_user,
    )


def _federation_request_canvas_join(
    host_node: str,
    session_id: str,
    nick: str,
    *,
    timeout: float | None = None,
) -> dict:
    return _federation_request_file_host(
        host_node,
        "",
        [],
        None,
        timeout=timeout,
        mode="canvas_join",
        session_id=session_id,
        nick=nick,
    )


def _federation_request_file_host(
    host_node: str,
    sender: str,
    recipients: list[str],
    room: Optional[str],
    *,
    timeout: float | None = None,
    mode: str = "file",
    title: str = "",
    session_id: str = "",
    by_user: str = "",
    nick: str = "",
) -> dict:
    hub = federation.get_hub()
    if hub is None or not hub.enabled:
        return {"ok": False, "error": "federation disabled"}
    if timeout is None:
        try:
            timeout = float(os.environ.get("SSHCHAT_FILE_FED_PROXY_TIMEOUT", "30") or "30")
        except ValueError:
            timeout = 30.0
        timeout = max(5.0, timeout)
    req_id = secrets.token_hex(8)
    event = threading.Event()
    with _file_host_waiters_lock:
        _file_host_waiters[req_id] = {"event": event, "payload": None}
    payload: dict = {}
    if mode == "canvas":
        payload = {
            "mode": "canvas",
            "creator": sender,
            "participants": recipients,
            "room": room,
            "title": title,
        }
    elif mode == "canvas_close":
        payload = {
            "mode": "canvas_close",
            "session_id": session_id,
            "by_user": by_user,
        }
    elif mode == "canvas_join":
        payload = {
            "mode": "canvas_join",
            "session_id": session_id,
            "nick": nick,
        }
    elif mode == "canvas_query":
        payload = {
            "mode": "canvas_query",
            "room": room,
        }
    else:
        payload = {
            "mode": "file",
            "sender": sender,
            "recipients": recipients,
            "room": room,
        }
    try:
        if not hub.request_file_host(host_node, req_id, payload):
            return {"ok": False, "error": f"无法联系文件代理节点 {host_node}"}
        if not event.wait(timeout):
            return {
                "ok": False,
                "error": f"等待文件代理节点超时（{timeout:.0f}s）",
            }
        with _file_host_waiters_lock:
            payload = _file_host_waiters.get(req_id, {}).get("payload")
        if not isinstance(payload, dict):
            return {"ok": False, "error": "对端返回无效"}
        return payload
    finally:
        with _file_host_waiters_lock:
            _file_host_waiters.pop(req_id, None)


def _federation_sync_file_public() -> None:
    hub = federation.get_hub()
    if hub is None or not hub.enabled:
        return
    try:
        hub.sync_file_public(_fed_local_file_public())
    except Exception as e:
        print(f"federation: file public sync failed: {e!r}")


def _fed_on_library_page_result(_from_peer: str, req_id: str, payload: dict) -> None:
    with _library_page_waiters_lock:
        waiter = _library_page_waiters.get(req_id)
        if not waiter:
            return
        waiter["payload"] = payload
        event = waiter.get("event")
        if isinstance(event, threading.Event):
            event.set()


def _fail_library_page_waiters(error: str) -> None:
    """Unblock pending remote /library opens when a peer link drops."""
    with _library_page_waiters_lock:
        waiters = list(_library_page_waiters.values())
    for waiter in waiters:
        if waiter.get("payload") is not None:
            continue
        waiter["payload"] = {"ok": False, "error": error}
        event = waiter.get("event")
        if isinstance(event, threading.Event):
            event.set()


def _fail_file_host_waiters(error: str) -> None:
    """Unblock pending federated /sendfile host requests when a peer drops."""
    with _file_host_waiters_lock:
        waiters = list(_file_host_waiters.values())
    for waiter in waiters:
        if waiter.get("payload") is not None:
            continue
        waiter["payload"] = {"ok": False, "error": error}
        event = waiter.get("event")
        if isinstance(event, threading.Event):
            event.set()


def _fetch_remote_library_page(
    owner: str,
    book_name: str,
    page: int,
    *,
    nick: str = "",
    flags: str = "",
    query: str = "",
    timeout: float = 90.0,
) -> dict:
    hub = federation.get_hub()
    if hub is None or not hub.enabled:
        return {"ok": False, "error": "federation disabled"}
    req_id = secrets.token_hex(8)
    event = threading.Event()
    with _library_page_waiters_lock:
        _library_page_waiters[req_id] = {"event": event, "payload": None}
    try:
        if not hub.request_library_page(
            owner, req_id, book_name, page, nick=nick, flags=flags, query=query
        ):
            return {"ok": False, "error": f"无法联系图书所在节点 {owner}"}
        if not event.wait(timeout):
            return {"ok": False, "error": "等待对端图书页超时"}
        with _library_page_waiters_lock:
            payload = _library_page_waiters.get(req_id, {}).get("payload")
        if not isinstance(payload, dict):
            return {"ok": False, "error": "对端返回无效"}
        return payload
    finally:
        with _library_page_waiters_lock:
            _library_page_waiters.pop(req_id, None)


def _parse_fed_search_hits(raw) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        try:
            hits.append((int(item["page"]), str(item.get("snippet") or "")))
        except (KeyError, TypeError, ValueError):
            continue
    return hits


def _emit_library_search_hits(
    conn, title: str, query: str, results: list[tuple[int, str]]
) -> None:
    if not results:
        send_line(conn, f"[*] 在《{title}》中未找到「{query}」。\n")
        return
    send_line(
        conn,
        f"[*] 在《{title}》中搜索「{query}」，找到 {len(results)} 处：\n",
    )
    for page_idx, snippet in results:
        send_line(conn, f"[*]   第 {page_idx + 1} 页：{snippet}\n")
    if len(results) != 1:
        send_line(conn, "[*] 用 /library page <页码> 跳转到对应页。\n")


def _set_library_page(conn, user: str, path: Path, page: int) -> None:
    page = max(0, int(page))
    with lock:
        library_reading[conn] = {"path": str(path.resolve()), "page": page, "origin": ""}
    book_key = library.bookmark_bare_name(path.name)
    if page <= 0 and library_bookmarks.get_page(user, book_key) is None:
        return
    entries = library_bookmarks.set_page(user, book_key, page)
    _federation_sync_library_bookmarks(user, entries)


def _set_library_session(
    conn,
    user: str,
    *,
    origin: str,
    name: str,
    page: int,
    path: Optional[Path] = None,
    title: str = "",
    total_pages: int = 0,
    persist_bookmark: bool = True,
) -> None:
    page = max(0, int(page))
    origin = (origin or "").strip()
    name = Path(str(name or "")).name
    with lock:
        library_reading[conn] = {
            "path": str(path.resolve()) if path is not None else "",
            "page": page,
            "origin": origin,
            "name": name,
            "title": title,
            "total_pages": int(total_pages or 0),
        }
    if not persist_bookmark:
        return
    book_key = _library_bookmark_key(origin, name)
    if origin:
        # Remote books: authoritative bookmark is on the owner node (via lpage).
        # Keep a local mirror for /library list only — do not fan out lmarks.
        if page <= 0 and library_bookmarks.get_page(user, book_key) is None:
            return
        library_bookmarks.set_page(user, book_key, page)
        return
    if page <= 0 and library_bookmarks.get_page(user, book_key) is None:
        return
    entries = library_bookmarks.set_page(user, book_key, page)
    _federation_sync_library_bookmarks(user, entries)


def _send_library_page_payload(
    conn, title: str, page_idx: int, total: int, text: str
) -> None:
    total = max(1, int(total))
    page_idx = max(0, min(int(page_idx), total - 1))
    send_line(conn, f"[*] --- 《{title}》 第 {page_idx + 1}/{total} 页 ---\n")
    for ln in library.wrap_page_lines(text or ""):
        send_line(conn, f"[*]    {ln}\n")
    send_line(
        conn,
        "[*] 翻页：/library next | prev | page <页码> | search <关键词> | show | info | close\n",
    )


def _get_cached_book(path: Path) -> library.BookDocument:
    key = str(path.resolve())
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError as exc:
        raise FileNotFoundError(str(path)) from exc
    cached = library_doc_cache.get(key)
    if cached and cached[0] == mtime_ns:
        return cached[1]
    with _library_load_locks_guard:
        load_lock = _library_load_locks.setdefault(key, threading.Lock())
    with load_lock:
        cached = library_doc_cache.get(key)
        if cached and cached[0] == mtime_ns:
            return cached[1]
        doc = library.load_book_isolated(path)
        library_doc_cache[key] = (mtime_ns, doc)
        return doc


def _send_library_page(conn, doc: library.BookDocument, page_idx: int) -> None:
    total = doc.total_pages
    page_idx = max(0, min(page_idx, total - 1))
    _send_library_page_payload(conn, doc.title, page_idx, total, doc.pages[page_idx])


def _library_read_session_page(
    session: dict, *, user: str = "", save_bookmark: bool = False
) -> tuple[str, int, int, str]:
    """Return (title, page, total, text) for a library_reading session."""
    origin = str(session.get("origin") or "").strip()
    page = int(session.get("page") or 0)
    if origin:
        name = str(session.get("name") or "").strip()
        flags = "s" if (save_bookmark and user) else ""
        payload = _fetch_remote_library_page(
            origin, name, page, nick=user, flags=flags
        )
        if not payload.get("ok"):
            raise RuntimeError(str(payload.get("error") or "remote page failed"))
        title = str(payload.get("title") or name)
        total = int(payload.get("total_pages") or 1)
        page = int(payload.get("page") or page)
        text = str(payload.get("text") or "")
        session["title"] = title
        session["total_pages"] = total
        session["page"] = page
        return title, page, total, text
    path = Path(str(session.get("path") or ""))
    doc = _get_cached_book(path)
    page = max(0, min(page, doc.total_pages - 1))
    return doc.title, page, doc.total_pages, doc.pages[page]


def _send_library_catalog(conn, user: str, query: str = "") -> None:
    lib_dir = _library_dir()
    send_line(conn, "[*] --- 图书馆 ---\n")
    catalog = _union_library_catalog()
    local_count = sum(1 for e in catalog if not e.origin)
    remote_count = len(catalog) - local_count
    if not catalog:
        if not lib_dir.is_dir():
            send_line(conn, f"[*] 本机图书馆目录不存在：{lib_dir}\n")
        else:
            send_line(conn, f"[*] 本机目录为空：{lib_dir}\n")
        if remote_count == 0:
            send_line(conn, "[*] 联邦暂无共享图书；支持格式：.epub、.txt、.md、.pdf\n")
        return
    query = (query or "").strip()
    books = library.search_catalog_items(catalog, query) if query else catalog
    if query:
        send_line(
            conn,
            f"[*] 查找「{query}」，共 {len(books)} 本（联邦并集 {len(catalog)} 本："
            f"本机 {local_count}，对端 {remote_count}）。\n",
        )
        if not books:
            send_line(conn, "[*] 未找到匹配的图书，请换关键词重试。\n")
            send_line(conn, "[*] 用 /library 查看全部书目。\n")
            return
    else:
        send_line(
            conn,
            f"[*] 联邦并集共 {len(catalog)} 本（本机 {local_count}，对端 {remote_count}）。\n",
        )
    user_marks = library_bookmarks.list_for_user(user)
    for entry in books:
        mark = user_marks.get(_library_bookmark_key(entry.origin, entry.name))
        mark_suffix = f" · 书签第 {mark + 1} 页" if mark is not None else ""
        send_line(
            conn,
            f"[*] {entry.index}. [{entry.ext.upper()}] {entry.name} "
            f"({library.format_size(entry.size_bytes)}){entry.display_origin()}{mark_suffix}\n",
        )
    send_line(conn, "[*] 打开：/library open <序号>  或  /library open <文件名[@节点]>\n")
    if len(catalog) > 10:
        send_line(conn, "[*] 查找：/library find <关键词>\n")
    send_line(conn, "[*] 我的书签：/library bookmarks\n")


def _send_library_bookmarks(conn, user: str) -> None:
    marks = library_bookmarks.list_for_user(user)
    send_line(conn, "[*] --- 我的书签 ---\n")
    if not marks:
        send_line(conn, "[*] 暂无书签；打开图书并翻页后会自动保存。\n")
        return
    catalog = _union_library_catalog()
    by_key = {
        _library_bookmark_key(entry.origin, entry.name): entry for entry in catalog
    }
    for book_key in sorted(marks, key=lambda n: n.lower()):
        page = marks[book_key]
        entry = by_key.get(book_key)
        if entry:
            label = (
                f"{entry.index}. [{entry.ext.upper()}] {entry.name}"
                f"{entry.display_origin()}"
            )
        else:
            label = book_key
        send_line(conn, f"[*] {label} · 第 {page + 1} 页\n")
    send_line(conn, "[*] 打开对应图书会自动从书签继续。\n")


def _handle_library(conn, payload: str) -> None:
    if payload.startswith("/library"):
        raw = payload[len("/library") :].strip()
    elif payload.startswith("/lib"):
        raw = payload[len("/lib") :].strip()
    else:
        raw = payload.strip()
    lib_dir = _library_dir()
    user = _client_name(conn)

    if not raw:
        _send_library_catalog(conn, user)
        with lock:
            session = dict(library_reading.get(conn) or {})
        if session:
            try:
                title, page, total, _text = _library_read_session_page(
                    session, user=user
                )
                origin = str(session.get("origin") or "")
                where = f" @{origin}" if origin else ""
                send_line(
                    conn,
                    f"[*] 当前在读：《{title}》第 {page + 1}/{total} 页{where}\n",
                )
            except Exception:
                library_reading.pop(conn, None)
        return

    parts = raw.split()
    head = parts[0].lower()

    if head in {"help", "?", "帮助"}:
        loc = conn_locale(conn)
        for line in (
            i18n.tr(en="[*] /library usage:\n", zh="[*] /library 用法：\n", locale=loc),
            i18n.tr(
                en="[*]   /library | /lib                 Federated catalog (with bookmark progress)\n",
                zh="[*]   /library | /lib                 联邦并集书目（含书签进度）\n",
                locale=loc,
            ),
            i18n.tr(
                en="[*]   /library find <keyword>           Find by title (when no book is open)\n",
                zh="[*]   /library find <关键词> | 查找      按书名查找（未打开书时）\n",
                locale=loc,
            ),
            i18n.tr(
                en="[*]   /library open <index|file[@node]>  Open (resume from bookmark if any)\n",
                zh="[*]   /library open <序号|文件名[@节点]>  打开（有书签则从书签继续）\n",
                locale=loc,
            ),
            i18n.tr(
                en="[*]   /library show                     Show current page\n",
                zh="[*]   /library show | 显示               显示当前页内容\n",
                locale=loc,
            ),
            i18n.tr(
                en="[*]   /library next | prev | page <n>   Turn pages (auto-save bookmark)\n",
                zh="[*]   /library next|prev|page <页码>     翻页（自动存书签）\n",
                locale=loc,
            ),
            i18n.tr(
                en="[*]   /library search <keyword>         Search inside the current book (local or federated)\n",
                zh="[*]   /library search <关键词> | 搜索    当前书内检索（本机与联邦书均可）\n",
                locale=loc,
            ),
            i18n.tr(
                en="[*]   /library bookmarks | reset | info | close\n",
                zh="[*]   /library bookmarks | reset | info | close（书签/清除/状态/关闭）\n",
                locale=loc,
            ),
            i18n.tr(
                en=f"[*] Local directory: {lib_dir} (remote books stream page-by-page)\n",
                zh=f"[*] 本机目录：{lib_dir}（对端图书按页拉取，不复制整本）\n",
                locale=loc,
            ),
        ):
            send_line(conn, line)
        return

    if head in {"bookmarks", "bookmark", "书签"}:
        _send_library_bookmarks(conn, user)
        return

    if head in {"reset", "清除"}:
        if len(parts) < 2:
            send_line(conn, "[*] Usage: /library reset <序号|文件名[@节点]>\n")
            return
        token = raw.split(None, 1)[1].strip()
        catalog = _union_library_catalog()
        entry = library.resolve_catalog_item(token, catalog)
        remote_cleared = False
        if entry:
            book_key = _library_bookmark_key(entry.origin, entry.name)
            label = f"{entry.name}{entry.display_origin()}"
            if entry.is_remote:
                hub = federation.get_hub()
                if hub is not None and hub.enabled:
                    try:
                        remote_cleared = hub.clear_remote_library_bookmark(
                            entry.origin, user, entry.name
                        )
                    except Exception as e:
                        print(f"federation: remote bookmark clear failed: {e!r}")
        else:
            book_key = library.bookmark_book_key(token) or Path(token).name
            label = book_key
        cleared = library_bookmarks.clear_book(user, book_key)
        if cleared and not (entry and entry.is_remote):
            _federation_sync_library_bookmarks(user, cleared)
        if cleared or remote_cleared:
            send_line(conn, f"[*] 已清除《{label}》的书签。\n")
        else:
            send_line(conn, f"[*] 《{label}》没有保存的书签。\n")
        return

    if head in {"close", "关闭"}:
        with lock:
            library_reading.pop(conn, None)
        send_line(conn, "[*] 已关闭当前图书（书签已保留）。\n")
        return

    if head in {"info", "状态"}:
        with lock:
            session = dict(library_reading.get(conn) or {})
        if not session:
            send_line(conn, "[*] 当前没有在阅读的图书。\n")
            return
        try:
            title, page, total, _text = _library_read_session_page(session, user=user)
        except Exception as exc:
            with lock:
                library_reading.pop(conn, None)
            send_line(conn, f"[*] 无法读取当前图书：{exc}\n")
            return
        origin = str(session.get("origin") or "")
        name = str(session.get("name") or Path(str(session.get("path") or "")).name)
        where = f" @{origin}" if origin else ""
        send_line(
            conn,
            f"[*] 在读：《{title}》第 {page + 1}/{total} 页（{name}{where}）\n",
        )
        return

    if head in {"show", "显示"}:
        with lock:
            session = dict(library_reading.get(conn) or {})
        if not session:
            send_line(conn, "[*] 请先用 /library open <序号> 打开图书。\n")
            return
        try:
            title, page, total, text = _library_read_session_page(session, user=user)
        except Exception as exc:
            with lock:
                library_reading.pop(conn, None)
            send_line(conn, f"[*] 无法读取图书：{exc}\n")
            return
        with lock:
            if conn in library_reading:
                library_reading[conn]["page"] = page
                library_reading[conn]["title"] = title
                library_reading[conn]["total_pages"] = total
        _send_library_page_payload(conn, title, page, total, text)
        return

    if head in {"next", "n", "下一页"}:
        with lock:
            session = dict(library_reading.get(conn) or {})
        if not session:
            send_line(conn, "[*] 请先用 /library open <序号> 打开图书。\n")
            return
        requested = int(session.get("page") or 0) + 1
        session["page"] = requested
        try:
            title, page, total, text = _library_read_session_page(
                session, user=user, save_bookmark=True
            )
        except Exception as exc:
            with lock:
                library_reading.pop(conn, None)
            send_line(conn, f"[*] 无法读取图书：{exc}\n")
            return
        if requested > page:
            send_line(conn, "[*] 已是最后一页。\n")
        origin = str(session.get("origin") or "")
        name = str(session.get("name") or Path(str(session.get("path") or "")).name)
        path = Path(str(session["path"])) if session.get("path") else None
        _set_library_session(
            conn,
            user,
            origin=origin,
            name=name,
            page=page,
            path=path,
            title=title,
            total_pages=total,
        )
        _send_library_page_payload(conn, title, page, total, text)
        return

    if head in {"prev", "p", "上一页"}:
        with lock:
            session = dict(library_reading.get(conn) or {})
        if not session:
            send_line(conn, "[*] 请先用 /library open <序号> 打开图书。\n")
            return
        old_page = int(session.get("page") or 0)
        page = max(0, old_page - 1)
        if page == old_page:
            send_line(conn, "[*] 已是第一页。\n")
        session["page"] = page
        try:
            title, page, total, text = _library_read_session_page(
                session, user=user, save_bookmark=True
            )
        except Exception as exc:
            with lock:
                library_reading.pop(conn, None)
            send_line(conn, f"[*] 无法读取图书：{exc}\n")
            return
        origin = str(session.get("origin") or "")
        name = str(session.get("name") or Path(str(session.get("path") or "")).name)
        path = Path(str(session["path"])) if session.get("path") else None
        _set_library_session(
            conn,
            user,
            origin=origin,
            name=name,
            page=page,
            path=path,
            title=title,
            total_pages=total,
        )
        _send_library_page_payload(conn, title, page, total, text)
        return

    if head in {"page", "页"}:
        if len(parts) < 2:
            send_line(conn, "[*] Usage: /library page <页码>\n")
            return
        try:
            page_1based = int(parts[1])
        except ValueError:
            send_line(conn, "[*] 页码须为整数，例如：/library page 3\n")
            return
        with lock:
            session = dict(library_reading.get(conn) or {})
        if not session:
            send_line(conn, "[*] 请先用 /library open <序号> 打开图书。\n")
            return
        session["page"] = max(0, page_1based - 1)
        try:
            title, page, total, text = _library_read_session_page(
                session, user=user, save_bookmark=True
            )
        except Exception as exc:
            with lock:
                library_reading.pop(conn, None)
            send_line(conn, f"[*] 无法读取图书：{exc}\n")
            return
        if page_1based < 1 or page_1based > total:
            send_line(
                conn,
                f"[*] 无效页码：共 {total} 页，请用 1～{total}。\n",
            )
            return
        origin = str(session.get("origin") or "")
        name = str(session.get("name") or Path(str(session.get("path") or "")).name)
        path = Path(str(session["path"])) if session.get("path") else None
        _set_library_session(
            conn,
            user,
            origin=origin,
            name=name,
            page=page,
            path=path,
            title=title,
            total_pages=total,
        )
        _send_library_page_payload(conn, title, page, total, text)
        return

    if head in {"search", "find", "搜索", "查找", "检索"}:
        query = raw.split(None, 1)[1].strip() if len(parts) >= 2 else ""
        if not query:
            send_line(conn, "[*] 用法：/library find <关键词>（查找书目）或打开书后 search <关键词>（书内检索）\n")
            return
        with lock:
            session = dict(library_reading.get(conn) or {})
        if not session:
            _send_library_catalog(conn, user, query)
            return
        origin = str(session.get("origin") or "").strip()
        if origin:
            name = str(session.get("name") or "").strip()
            send_line(conn, f"[*] 正在节点 {origin} 检索「{query}」…\n")
            payload = _fetch_remote_library_page(
                origin, name, 0, nick=user, flags="f", query=query
            )
            if not payload.get("ok"):
                send_line(
                    conn,
                    f"[*] 检索失败：{payload.get('error') or '对端无响应'}\n",
                )
                return
            if "results" not in payload:
                send_line(conn, "[*] 对端节点不支持书内检索。\n")
                return
            title = str(payload.get("title") or name)
            results = _parse_fed_search_hits(payload.get("results"))
            _emit_library_search_hits(conn, title, query, results)
            if len(results) != 1:
                return
            page_idx = results[0][0]
            send_line(conn, f"[*] 已自动跳转到第 {page_idx + 1} 页。\n")
            jump = _fetch_remote_library_page(
                origin, name, page_idx, nick=user, flags="s"
            )
            if not jump.get("ok"):
                send_line(conn, f"[*] 跳转失败：{jump.get('error') or '对端无响应'}\n")
                return
            title = str(jump.get("title") or title)
            total = int(jump.get("total_pages") or 1)
            page = int(jump.get("page") or page_idx)
            text = str(jump.get("text") or "")
            _set_library_session(
                conn,
                user,
                origin=origin,
                name=name,
                page=page,
                title=title,
                total_pages=total,
            )
            _send_library_page_payload(conn, title, page, total, text)
            return
        try:
            doc = _get_cached_book(Path(str(session["path"])))
        except Exception as exc:
            with lock:
                library_reading.pop(conn, None)
            send_line(conn, f"[*] 无法读取图书：{exc}\n")
            return
        results = library.search_book(doc, query)
        _emit_library_search_hits(conn, doc.title, query, results)
        if len(results) != 1:
            return
        page_idx = results[0][0]
        _set_library_session(
            conn,
            user,
            origin="",
            name=Path(str(session["path"])).name,
            page=page_idx,
            path=Path(str(session["path"])),
            title=doc.title,
            total_pages=doc.total_pages,
        )
        send_line(conn, f"[*] 已自动跳转到第 {page_idx + 1} 页。\n")
        _send_library_page(conn, doc, page_idx)
        return

    if head in {"open", "read", "读", "打开"}:
        if len(parts) < 2:
            send_line(conn, "[*] Usage: /library open <序号|文件名[@节点]>\n")
            return
        token = raw.split(None, 1)[1].strip()
        catalog = _union_library_catalog()
        entry = library.resolve_catalog_item(token, catalog)
        if not entry:
            send_line(conn, f"[*] 未找到图书：{token}\n")
            send_line(conn, "[*] 用 /library 查看可用序号与文件名[@节点]。\n")
            return
        bookmark_key = _library_bookmark_key(entry.origin, entry.name)
        if entry.is_remote:
            send_line(
                conn,
                f"[*] 正在从节点 {entry.origin} 拉取 "
                f"[{entry.ext.upper()}] {entry.name}…（请稍候）\n",
            )
            try:
                # Resume from the book-owner node's bookmark for this nick.
                payload = _fetch_remote_library_page(
                    entry.origin, entry.name, 0, nick=user, flags="r"
                )
            except Exception as exc:
                send_line(conn, f"[*] 打开失败：{exc}\n")
                return
            if not payload.get("ok"):
                send_line(
                    conn,
                    f"[*] 打开失败：{payload.get('error') or '对端无响应'}\n",
                )
                return
            title = str(payload.get("title") or entry.name)
            total = max(1, int(payload.get("total_pages") or 1))
            page = max(0, min(int(payload.get("page") or 0), total - 1))
            text = str(payload.get("text") or "")
            resumed = bool(payload.get("resumed"))
            _set_library_session(
                conn,
                user,
                origin=entry.origin,
                name=entry.name,
                page=page,
                title=title,
                total_pages=total,
            )
            if resumed and page > 0:
                send_line(
                    conn,
                    f"[*] 已打开 [{entry.ext.upper()}] {entry.name} @{entry.origin}，"
                    f"从书签第 {page + 1}/{total} 页继续。\n",
                )
            else:
                send_line(
                    conn,
                    f"[*] 已打开 [{entry.ext.upper()}] {entry.name} @{entry.origin}，"
                    f"共 {total} 页。\n",
                )
            _send_library_page_payload(conn, title, page, total, text)
            return
        saved = library_bookmarks.get_page(user, bookmark_key)
        page = saved if saved is not None else 0
        if entry.path is None:
            send_line(conn, f"[*] 本机图书缺少路径：{entry.name}\n")
            return
        if entry.ext == "pdf" or entry.size_bytes >= 2 * 1024 * 1024:
            send_line(
                conn,
                f"[*] 正在加载 [{entry.ext.upper()}] {entry.name}，"
                "首次打开可能需要十几秒，请稍候…\n",
            )
        try:
            doc = _get_cached_book(entry.path)
        except Exception as exc:
            send_line(conn, f"[*] 打开失败：{exc}\n")
            return
        page = min(page, doc.total_pages - 1)
        _set_library_session(
            conn,
            user,
            origin="",
            name=entry.name,
            page=page,
            path=entry.path,
            title=doc.title,
            total_pages=doc.total_pages,
        )
        if saved is not None and saved > 0:
            send_line(
                conn,
                f"[*] 已打开 [{entry.ext.upper()}] {entry.name}，"
                f"从书签第 {page + 1}/{doc.total_pages} 页继续。\n",
            )
        else:
            send_line(
                conn,
                f"[*] 已打开 [{entry.ext.upper()}] {entry.name}，共 {doc.total_pages} 页。\n",
            )
        _send_library_page(conn, doc, page)
        return

    send_line(conn, "[*] 未知子命令。试试 /library help\n")


def _send_dict_help(conn) -> None:
    send_line(conn, "[*] /dict 用法：\n")
    send_line(conn, "[*]   /dict en <英文>     英→中（中英文词典）\n")
    send_line(conn, "[*]   /dict cn <中文>     中→英（中英文词典）\n")
    send_line(conn, "[*]   /dict hh <词语>     汉语词典（汉→汉释义）\n")
    send_line(conn, "[*]   /dict <词语>        自动：英文查英→中，中文查中→英+汉语\n")
    send_line(conn, "[*] 别名：英/en、中/cn/中英、汉/hh/汉语\n")


def _handle_dict(conn, payload: str) -> None:
    raw = payload[len("/dict") :].strip()
    if not raw or raw.lower() in ("help", "?", "帮助"):
        _send_dict_help(conn)
        return

    parts = raw.split(None, 1)
    explicit_mode = dict_lookup.normalize_mode(parts[0])
    if explicit_mode and len(parts) > 1:
        word = parts[1].strip()
        modes = [explicit_mode]
    elif explicit_mode:
        send_line(conn, "[*] 请提供要查询的词语，例如：/dict en hello\n")
        return
    else:
        word = raw
        if dict_lookup.detect_mode(word) == "cn":
            modes = ["cn", "hh"]
        else:
            modes = ["en"]

    if not word:
        send_line(conn, "[*] 请提供要查询的词语。\n")
        return

    try:
        for idx, mode in enumerate(modes):
            if idx:
                send_line(conn, "[*]\n")
            for line in dict_lookup.lookup_lines(mode, word):
                send_line(conn, f"[*] {line}\n")
    except ValueError as e:
        send_line(conn, f"[*] {e}\n")
    except urllib.error.URLError as e:
        print(f"/dict network error: {e!r}")
        send_line(conn, "[*] 词典查询失败：网络不可用或超时，请稍后重试。\n")
    except Exception as e:
        print(f"/dict error: {e!r}")
        traceback.print_exc()
        send_line(conn, "[*] 词典查询失败，请稍后重试（详情见服务端日志）。\n")


def _notify_file_ready(transfer: file_sharing.FileTransfer) -> None:
    """Notify recipients that a file is ready for download.

    Online users get the notice immediately. Offline recipients get a leave-message
    (kind=file) so they see it on next login, and the sender can list/recall it
    via /leave just like a text leave-message.
    """
    if file_http is None:
        return

    base_url = file_http.get_base_url()

    for recipient, token in transfer.download_tokens.items():
        key = transfer.download_keys[recipient]
        download_url = f"{base_url}/download/{token}"
        message = _build_file_ready_message(
            sender=transfer.sender,
            filename=transfer.filename,
            file_size=transfer.file_size,
            download_url=download_url,
            key=key,
            room=transfer.room,
        )

        recipient_lower = recipient.lower()
        delivered = False
        with lock:
            for conn, info in clients.items():
                if info["name"].lower() != recipient_lower:
                    continue
                try:
                    send_line(conn, message)
                    delivered = True
                except Exception as e:
                    print(f"[FileTransfer] Failed to notify {recipient}: {e}")

        # Also fan out to the same nick on peer nodes (Cloudflare/public file URL).
        remote_sent = False
        hub = federation.get_hub()
        if hub is not None and hub.enabled and hub.has_remote_user(recipient):
            remote_sent = hub.send_file_notice(
                recipient,
                transfer.sender,
                {
                    "filename": transfer.filename,
                    "file_size": transfer.file_size,
                    "download_url": download_url,
                    "download_key": key,
                    "download_token": token,
                    "room": transfer.room,
                    "transfer_id": transfer.transfer_id,
                },
            )

        if delivered or remote_sent:
            continue

        # Offline: leave a mailbox entry the sender can also see/recall via /leave.
        # Also seed every federation peer so login on another node still delivers.
        summary = _format_file_leave_summary(transfer.filename, transfer.file_size)
        meta = {
            "transfer_id": transfer.transfer_id,
            "filename": transfer.filename,
            "file_size": transfer.file_size,
            "download_token": token,
            "download_key": key,
            "download_url": download_url,
            "room": transfer.room,
        }
        stored = offline_messages.leave(
            recipient,
            transfer.sender,
            summary,
            kind="file",
            meta=meta,
        )
        leave_ts = float((stored or {}).get("ts") or time.time())
        _federation_seed_file_leave(
            recipient,
            transfer.sender,
            {
                "filename": transfer.filename,
                "file_size": transfer.file_size,
                "download_url": download_url,
                "download_key": key,
                "download_token": token,
                "room": transfer.room,
                "transfer_id": transfer.transfer_id,
                "leave_ts": leave_ts,
            },
        )
        with lock:
            sender_lower = transfer.sender.lower()
            for conn, info in clients.items():
                if info["name"].lower() != sender_lower:
                    continue
                try:
                    send_line(
                        conn,
                        f"[*] {recipient!r} 当前不在线，文件已留言；"
                        f"对方下次上线时会收到。\n",
                    )
                except Exception as e:
                    print(f"[FileTransfer] Failed to notify sender about offline leave: {e}")


def _canvas_invite_message(
    *,
    creator: str,
    url: str,
    key: str,
    room: Optional[str],
    title: str = "",
) -> str:
    where = f"房间 #{room}" if room else "私密画布"
    title_line = f"[*] 标题: {title}\n" if title else ""
    return (
        f"[*] ========== 共享画布 ==========\n"
        f"[*] 发起人: {creator}\n"
        f"[*] 范围: {where}\n"
        f"{title_line}"
        f"[*]\n"
        f"[*] 画布网址:\n"
        f"[*] {url}\n"
        f"[*]\n"
        f"[*] 访问密钥: {key}\n"
        f"[*]\n"
        f"[*] 说明:\n"
        f"[*] 1. 打开网址，在页面里输入上面的密钥\n"
        f"[*] 2. 密钥不在网址里；每人的网址和密钥都不同\n"
        f"[*] 3. 解锁后可共同绘画，笔画会自动同步\n"
        f"[*] 4. 图形客户端会折叠成按钮，可一键打开\n"
        f"[*] =====================================\n"
        f"[*] gui-open canvas {url} {key}\n"
    )


def _deliver_canvas_invites(
    session: canvas_sharing.CanvasSession,
    *,
    only: Optional[str] = None,
) -> None:
    """Privately deliver each participant their canvas URL + key.

    If *only* is set, deliver solely to that nick (case-insensitive).
    Rotates keys when due so re-entry uses fresh keys; open tickets stay valid.
    """
    # Host-owned rotation; no-op for federated mirrors / disabled interval.
    if canvas_sharing.canvas_store.rotate_keys_if_due(session.session_id):
        with canvas_sharing.canvas_store.lock:
            live = canvas_sharing.canvas_store.sessions.get(session.session_id)
            if live is not None:
                session.keys = dict(live.keys)
                session.keys_rotated_at = live.keys_rotated_at
    base_url = (session.host_base_url or "").strip()
    if not base_url:
        if file_http is None:
            return
        base_url = file_http.get_base_url()
    base_url = base_url.rstrip("/")
    hub = federation.get_hub()
    only_key = (only or "").strip().lower()
    for participant, token in session.tokens.items():
        if only_key and participant.lower() != only_key:
            continue
        key = session.keys.get(participant) or ""
        url = f"{base_url}/canvas/{token}"
        message = _canvas_invite_message(
            creator=session.creator,
            url=url,
            key=key,
            room=session.room,
            title=session.title,
        )
        recipient_lower = participant.lower()
        delivered = False
        with lock:
            for c, info in clients.items():
                if info["name"].lower() != recipient_lower:
                    continue
                try:
                    send_line(c, message)
                    delivered = True
                except Exception as e:
                    print(f"[Canvas] Failed to notify {participant}: {e}")
        if (
            not delivered
            and hub is not None
            and hub.enabled
            and hub.has_remote_user(participant)
        ):
            try:
                hub.send_pm(participant, session.creator, message)
            except Exception as e:
                print(f"[Canvas] Federated invite failed for {participant}: {e}")


def _ensure_canvas_participant(
    session: canvas_sharing.CanvasSession, nick: str
) -> Tuple[bool, str]:
    """Mint URL+key for *nick* on *session* (local or federated host)."""
    nick = (nick or "").strip()
    if not nick:
        return False, "无效昵称"
    # Already present?
    for existing in session.tokens:
        if existing.lower() == nick.lower():
            return True, ""
    if session.host_node:
        hosted = _federation_request_canvas_join(
            session.host_node, session.session_id, nick
        )
        if not hosted.get("ok"):
            return False, str(hosted.get("error") or "联邦加入失败")
        token = str(hosted.get("token") or "").strip()
        key = str(hosted.get("key") or "").strip()
        if not token or not key:
            return False, "联邦加入返回无效"
        # Update local mirror so invites use the new credentials.
        with canvas_sharing.canvas_store.lock:
            live = canvas_sharing.canvas_store.sessions.get(session.session_id)
            if live is None:
                return False, "画布不存在"
            live.tokens[nick] = token
            live.keys[nick] = key
            canvas_sharing.canvas_store._save()
            session.tokens[nick] = token
            session.keys[nick] = key
        return True, ""
    token, key, err = canvas_sharing.canvas_store.add_participant(
        session.session_id, nick
    )
    if err or not token:
        return False, err or "加入失败"
    session.tokens[nick] = token
    session.keys[nick] = key or ""
    return True, ""


def _canvas_host_reachable(host: str) -> bool:
    hub = federation.get_hub()
    if hub is None or not hub.enabled:
        return False
    host = (host or "").strip()
    if not host:
        return False
    if host == hub.node_id:
        return True
    return host in hub.known_peer_ids()


def _federation_adopt_remote_canvas(announce: dict) -> Optional[canvas_sharing.CanvasSession]:
    """Install a remote room board mirror from query/csync metadata."""
    session_id = str(announce.get("session_id") or "").strip()
    room = str(announce.get("room") or "").strip() or None
    host_node = str(announce.get("host_node") or "").strip()
    base_url = str(announce.get("base_url") or "").strip().rstrip("/")
    if not session_id or not room or not host_node or not base_url:
        return None
    tokens = announce.get("tokens") or {}
    keys = announce.get("keys") or {}
    if not isinstance(tokens, dict):
        tokens = {}
    if not isinstance(keys, dict):
        keys = {}
    try:
        expires = float(announce.get("expires") or 0)
    except (TypeError, ValueError):
        expires = 0.0
    try:
        rev = int(announce.get("rev") or 0)
    except (TypeError, ValueError):
        rev = 0
    return canvas_sharing.canvas_store.register_remote_session(
        session_id=session_id,
        creator=str(announce.get("creator") or "").strip() or "remote",
        participants=list(tokens.keys()),
        room=room,
        tokens={str(k): str(v) for k, v in tokens.items()},
        keys={str(k): str(v) for k, v in keys.items()},
        host_node=host_node,
        host_base_url=base_url,
        title=str(announce.get("title") or ""),
        expires=expires,
        conflict_token=str(announce.get("conflict_token") or ""),
        rev=rev,
    )


def _federation_query_room_canvas(room: str) -> Optional[dict]:
    """Ask peers whether an open board already exists for *room*."""
    hub = federation.get_hub()
    if hub is None or not hub.enabled:
        return None
    room = (room or "").strip()
    if not room:
        return None
    for peer in hub.known_peer_ids():
        try:
            reply = _federation_request_file_host(
                peer,
                "",
                [],
                room,
                mode="canvas_query",
                timeout=8.0,
            )
        except Exception as e:
            print(f"[Canvas] query {peer} failed: {e!r}")
            continue
        if reply.get("ok") and reply.get("found") and reply.get("session_id"):
            if not reply.get("host_node"):
                reply["host_node"] = peer
            return reply
    return None


def _federation_push_canvas_announce(
    session: canvas_sharing.CanvasSession,
) -> None:
    hub = federation.get_hub()
    if hub is None or not hub.enabled:
        return
    if not session.room or session.closed or session.parked or session.host_node:
        return
    ann = canvas_sharing.canvas_store.announce_dict(session)
    ann["host_node"] = hub.node_id
    if file_http is not None:
        ann["base_url"] = file_http.get_base_url().rstrip("/")
    if not ann.get("base_url"):
        return
    try:
        hub.sync_canvas_announce(ann)
    except Exception as e:
        print(f"[Canvas] csync push failed: {e!r}")


def _federation_push_all_canvas_announces() -> None:
    hub = federation.get_hub()
    if hub is None or not hub.enabled:
        return
    for ann in canvas_sharing.canvas_store.list_open_room_announces(
        local_node_id=hub.node_id
    ):
        if not ann.get("base_url") and file_http is not None:
            ann["base_url"] = file_http.get_base_url().rstrip("/")
        if not ann.get("base_url"):
            continue
        try:
            hub.sync_canvas_announce(ann)
        except Exception as e:
            print(f"[Canvas] csync fanout failed: {e!r}")


def _fed_on_canvas_sync(origin: str, announce: dict) -> None:
    """Merge peer room-canvas ads; park loser like federated games."""
    if not isinstance(announce, dict):
        return
    room = str(announce.get("room") or "").strip()
    sid = str(announce.get("session_id") or "").strip()
    remote_host = str(announce.get("host_node") or origin or "").strip()
    base_url = str(announce.get("base_url") or "").strip().rstrip("/")
    remote_tok = str(announce.get("conflict_token") or sid).strip()
    if not room or not sid or not remote_host or not base_url:
        return
    hub = federation.get_hub()
    local_id = hub.node_id if hub is not None else _local_node_id()
    if remote_host == local_id:
        return

    local = canvas_sharing.canvas_store.find_open_for_room(room)
    if local is None:
        _federation_adopt_remote_canvas(announce)
        return
    if local.session_id == sid:
        return

    local_auth = (local.host_node or local_id).strip()
    local_tok = (local.conflict_token or local.session_id).strip()
    win_auth, _win_tok = _game_conflict_winner(
        local_auth, local_tok, remote_host, remote_tok
    )
    if win_auth == local_auth:
        if not local.host_node:
            _federation_push_canvas_announce(local)
        return

    # Remote wins: park local fork (keep scene), adopt remote board.
    loser = local_auth
    if not local.host_node:
        canvas_sharing.canvas_store.park_session(local.session_id)
    else:
        with canvas_sharing.canvas_store.lock:
            live = canvas_sharing.canvas_store.sessions.get(local.session_id)
            if live is not None:
                live.closed = True
                canvas_sharing.canvas_store._save()
    adopted = _federation_adopt_remote_canvas(announce)
    if adopted is None:
        return
    notice = (
        f"[*] 联邦画板冲突：#{room} 同时存在多块画板，已启用节点 "
        f"{remote_host} 的画板；节点 {loser} 上的画板已暂存。"
        f"联邦断开后可恢复暂存画板；请重新 /canvas 获取最新密钥。\n"
    )
    broadcast_room(room, notice.encode("utf-8"))


def _fed_handle_unreachable_canvas_authority(down_peer: str = "") -> None:
    """When a canvas host drops, promote any parked local room board."""
    down_peer = (down_peer or "").strip()
    hub = federation.get_hub()
    local_id = hub.node_id if hub is not None else _local_node_id()
    restored: list[tuple[str, canvas_sharing.CanvasSession]] = []
    with canvas_sharing.canvas_store.lock:
        rooms = {
            s.room
            for s in canvas_sharing.canvas_store.sessions.values()
            if s.room and not s.closed
        }
    for room in rooms:
        active = canvas_sharing.canvas_store.find_open_for_room(room)
        if active is None:
            # Idle room with only a parked local fork.
            parked = canvas_sharing.canvas_store.find_parked_for_room(room)
            if parked is None:
                continue
            promoted = canvas_sharing.canvas_store.promote_parked_for_room(room)
            if promoted is not None:
                restored.append((room, promoted))
            continue
        host = (active.host_node or "").strip()
        if not host or host == local_id:
            continue
        if down_peer and host != down_peer:
            continue
        if _canvas_host_reachable(host):
            continue
        promoted = canvas_sharing.canvas_store.promote_parked_for_room(room)
        if promoted is None:
            continue
        restored.append((room, promoted))
    for room, session in restored:
        notice = (
            f"[*] 联邦画板宿主不可达，已恢复本节点暂存的 #{room} 画板。"
            f"请重新 /canvas 获取密钥。\n"
        )
        broadcast_room(room, notice.encode("utf-8"))
        _federation_push_canvas_announce(session)


def _create_canvas_via_federation_proxy(
    conn,
    sender: str,
    recipients: list[str],
    room_name: Optional[str],
) -> Optional[canvas_sharing.CanvasSession]:
    """Host canvas on a Cloudflare-capable peer when local file URL is LAN-only."""
    if file_http is None:
        return None
    hub = federation.get_hub()
    if hub is None or not hub.enabled:
        return None
    if not file_http_server.needs_federation_file_proxy(file_http.get_base_url()):
        return None
    if not hub.has_remote_file_public():
        try:
            _federation_sync_file_public()
        except Exception:
            pass
    proxy_peer = hub.pick_file_public_peer()
    if proxy_peer is None:
        send_line(
            conn,
            "[*] 联邦中暂无已广告的 Cloudflare/公网文件节点；"
            "将使用本机地址（对端可能打不开）。\n",
        )
        return None
    host_node, peer_url = proxy_peer
    send_line(
        conn,
        f"[*] 正在向联邦节点 {host_node} 申请公网画布"
        f"（{peer_url}）…\n",
    )
    hosted = _federation_request_canvas_host(
        host_node, sender, recipients, room_name
    )
    if not hosted.get("ok"):
        err = hosted.get("error") or "unknown"
        send_line(
            conn,
            f"[*] 联邦公网画布代理（{host_node}）失败：{err}；"
            f"改用本机地址。\n",
        )
        return None
    session_id = str(hosted.get("session_id") or "").strip()
    base_url = str(hosted.get("base_url") or "").strip().rstrip("/")
    tokens = hosted.get("tokens") or {}
    keys = hosted.get("keys") or {}
    if not session_id or not base_url or not isinstance(tokens, dict) or not isinstance(keys, dict):
        send_line(conn, "[*] 联邦公网画布代理返回无效；改用本机地址。\n")
        return None
    try:
        expires = float(hosted.get("expires") or 0)
    except (TypeError, ValueError):
        expires = 0.0
    session = canvas_sharing.canvas_store.register_remote_session(
        session_id=session_id,
        creator=str(hosted.get("creator") or sender).strip() or sender,
        participants=recipients,
        room=room_name,
        tokens={str(k): str(v) for k, v in tokens.items()},
        keys={str(k): str(v) for k, v in keys.items()},
        host_node=host_node,
        host_base_url=base_url,
        title=str(hosted.get("title") or ""),
        expires=expires,
        conflict_token=str(hosted.get("conflict_token") or ""),
        rev=int(hosted.get("rev") or 0) if isinstance(hosted.get("rev"), (int, float)) else 0,
    )
    send_line(
        conn,
        f"[*] 画布已托管在联邦节点 {host_node} 的公网通道"
        f"（本机无 Cloudflare）。\n",
    )
    return session


def _close_canvas_session(
    session: canvas_sharing.CanvasSession, by_user: str
) -> Tuple[bool, str]:
    if session.host_node:
        hosted = _federation_request_canvas_close(
            session.host_node, session.session_id, by_user
        )
        if not hosted.get("ok"):
            err = str(hosted.get("error") or "关闭失败")
            return False, err
    ok, err = canvas_sharing.canvas_store.close_session(
        session.session_id, by_user
    )
    return ok, err


def _handle_canvas(conn, sender: str, payload: str) -> None:
    """Handle /canvas — shared web drawing board (URL + separate key)."""
    if file_http is None:
        send_line(conn, "[*] 画布功能依赖文件网页服务，当前未启用。\n")
        return

    raw = payload[len("/canvas") :].strip()
    if payload.lower().startswith("/board"):
        raw = payload[len("/board") :].strip()

    if raw.lower() in ("help", "?", "帮助"):
        loc = conn_locale(conn)
        if loc == "en":
            send_line(conn, "[*] Usage:\n")
            send_line(conn, "[*]   /canvas            - shared drawing board for the current room\n")
            send_line(conn, "[*]   /canvas #<room>    - board for a specific room\n")
            send_line(conn, "[*]   /canvas <nick>     - private board with an online user\n")
            send_line(conn, "[*]   /canvas close      - creator closes the current room board\n")
            send_line(conn, "[*]   /canvas new        - force a new board even if the room already has one\n")
            send_line(
                conn,
                "[*] Alias: /board. Each person gets a unique URL + key (key is not in the URL).\n",
            )
            send_line(
                conn,
                "[*] If the room already has a board, /canvas joins it and re-sends your invite.\n",
            )
            send_line(
                conn,
                "[*] Room board content persists until /canvas close; keys rotate periodically "
                "(open sessions keep working; re-entry needs the latest key).\n",
            )
            send_line(
                conn,
                "[*] Open the URL in a browser, enter the key, then draw; strokes sync live.\n",
            )
        else:
            send_line(conn, "[*] 用法：\n")
            send_line(conn, "[*]   /canvas            - 当前房间共享画板\n")
            send_line(conn, "[*]   /canvas #<房间>    - 指定房间共享画板\n")
            send_line(conn, "[*]   /canvas <昵称>     - 与某人私密画板\n")
            send_line(conn, "[*]   /canvas close      - 发起人关闭当前房间画板\n")
            send_line(conn, "[*]   /canvas new        - 强制新开一局（即使房间已有）\n")
            send_line(
                conn,
                "[*] 别名 /board。每人收到独立网址和密钥（密钥不在网址里）；解锁后共同绘画。\n",
            )
            send_line(
                conn,
                "[*] 房间已有画板时，再发 /canvas（或点画板）会加入已有画板并给你发邀请。\n",
            )
            send_line(
                conn,
                "[*] 房间画板内容会一直保留（直到 /canvas close）；密钥会定期更换，"
                "已打开的画板不受影响，重新进入需用最新密钥。\n",
            )
            send_line(
                conn,
                "[*] 终端请把网址复制到浏览器；图形客户端会自动打开画板。\n",
            )
        return

    parts = raw.split()
    target = parts[0].strip() if parts else ""
    force_new = False
    if target.lower() in ("new", "新建"):
        force_new = True
        target = parts[1].strip() if len(parts) > 1 else ""

    if target.lower() in ("close", "关闭", "end"):
        with lock:
            info = clients.get(conn)
            room_name = (info or {}).get("current_room") or DEFAULT_ROOM
        session = canvas_sharing.canvas_store.find_open_for_room(room_name)
        if session is None:
            send_line(conn, f"[*] 房间 #{room_name} 当前没有进行中的画布。\n")
            return
        ok, err = _close_canvas_session(session, sender)
        if not ok:
            send_line(conn, f"[*] {err}\n")
            return
        send_line(conn, f"[*] 已关闭房间 #{room_name} 的共享画布。\n")
        broadcast_room(
            room_name,
            f"[*] {sender} 关闭了共享画布。\n".encode("utf-8"),
        )
        return

    recipients: list[str] = []
    room_name: Optional[str] = None

    if not target:
        with lock:
            info = clients.get(conn)
            room_name = (info or {}).get("current_room") or DEFAULT_ROOM
    elif target.startswith("#"):
        room_name = normalize_room(target[1:])
        if not room_name:
            send_line(conn, "[*] 无效的房间名。\n")
            return

    if room_name is not None:
        with lock:
            if room_name not in rooms:
                send_line(conn, f"[*] 房间 #{room_name} 不存在。\n")
                return
            if conn not in rooms[room_name]:
                send_line(conn, f"[*] 你不在房间 #{room_name} 中。\n")
                return
            for c in rooms[room_name]:
                if c in clients:
                    recipients.append(clients[c]["name"])

        hub = federation.get_hub()
        if hub is not None and hub.enabled:
            seen = {n.lower() for n in recipients}
            for remote_name in hub.names_in_room(room_name):
                rk = remote_name.lower()
                if rk not in seen:
                    recipients.append(remote_name)
                    seen.add(rk)

        if len({n.lower() for n in recipients}) < 1:
            send_line(conn, f"[*] 房间 #{room_name} 里没有人。\n")
            return

        if not force_new:
            existing = canvas_sharing.canvas_store.find_open_for_room(room_name)
            if existing is not None:
                ok, err = _ensure_canvas_participant(existing, sender)
                if not ok:
                    send_line(conn, f"[*] 加入已有画布失败：{err}\n")
                    return
                send_line(
                    conn,
                    f"[*] 房间 #{room_name} 已有共享画布；正在把你加入并发送邀请。"
                    f"（强制新开用 /canvas new）\n",
                )
                _deliver_canvas_invites(existing, only=sender)
                return
            # Federation already linked: join peer's board instead of forking.
            remote = _federation_query_room_canvas(room_name)
            if remote is not None:
                adopted = _federation_adopt_remote_canvas(remote)
                if adopted is not None:
                    ok, err = _ensure_canvas_participant(adopted, sender)
                    if not ok:
                        send_line(conn, f"[*] 加入联邦画布失败：{err}\n")
                        return
                    send_line(
                        conn,
                        f"[*] 已加入联邦节点上的房间 #{room_name} 画板并发送邀请。\n",
                    )
                    _deliver_canvas_invites(adopted, only=sender)
                    return
            parked = canvas_sharing.canvas_store.find_parked_for_room(room_name)
            if parked is not None:
                promoted = canvas_sharing.canvas_store.promote_parked_for_room(
                    room_name
                )
                if promoted is not None:
                    ok, err = _ensure_canvas_participant(promoted, sender)
                    if not ok:
                        send_line(conn, f"[*] 恢复暂存画布失败：{err}\n")
                        return
                    send_line(
                        conn,
                        f"[*] 已恢复本节点暂存的房间 #{room_name} 画板并发送邀请。\n",
                    )
                    _deliver_canvas_invites(promoted, only=sender)
                    _federation_push_canvas_announce(promoted)
                    return
        else:
            # Replace room board; keep disk history only if previously closed.
            old = canvas_sharing.canvas_store.find_open_for_room(room_name)
            if old is not None:
                canvas_sharing.canvas_store.close_session(old.session_id, old.creator)
    else:
        target_lower = target.lower()
        with lock:
            online = [
                info["name"]
                for info in clients.values()
                if info["name"].lower() == target_lower
            ]
        if not online:
            hub = federation.get_hub()
            if hub is not None and hub.enabled and hub.has_remote_user(target):
                online = [target]
        if not online:
            send_line(conn, f"[*] 用户 {target} 不在线。\n")
            return
        if target_lower == sender.lower():
            send_line(conn, "[*] 私密画布请指定另一位在线用户。\n")
            return
        recipients = [sender, online[0]]

    session = _create_canvas_via_federation_proxy(
        conn, sender, recipients, room_name
    )
    if session is None:
        try:
            session = canvas_sharing.canvas_store.create_session(
                creator=sender,
                participants=recipients,
                room=room_name,
            )
        except Exception as e:
            print(f"[Canvas] create failed: {e}")
            traceback.print_exc()
            send_line(conn, "[*] 创建画布失败，请稍后重试。\n")
            return

    if room_name:
        broadcast_room(
            room_name,
            f"[*] {sender} 开启了共享画布（请查看私信中的网址与密钥）。\n".encode(
                "utf-8"
            ),
        )
    else:
        send_line(conn, f"[*] 已与 {recipients[-1]} 创建私密画布。\n")

    _deliver_canvas_invites(session)
    if room_name and session is not None and not session.host_node:
        _federation_push_canvas_announce(session)


def _piano_invite_message(
    *,
    creator: str,
    url: str,
    key: str,
    room: Optional[str],
    title: str = "",
) -> str:
    where = f"房间 #{room}" if room else "私密钢琴"
    title_line = f"[*] 标题: {title}\n" if title else ""
    return (
        f"[*] ========== 房间钢琴 ==========\n"
        f"[*] 发起人: {creator}\n"
        f"[*] 范围: {where}\n"
        f"{title_line}"
        f"[*]\n"
        f"[*] 钢琴网址:\n"
        f"[*] {url}\n"
        f"[*]\n"
        f"[*] 访问密钥: {key}\n"
        f"[*]\n"
        f"[*] 说明:\n"
        f"[*] 1. 打开网址，在页面里输入上面的密钥\n"
        f"[*] 2. 密钥不在网址里；每人的网址和密钥都不同\n"
        f"[*] 3. 解锁后可用键盘演奏，同房间其他人会听到\n"
        f"[*] 4. 电脑白键 Z→M、A→'、1→= 连续排列；黑键 Q→P 等，琴键上标按键。手机三行分段。\n"
        f"[*] 5. 图形客户端会折叠成按钮，可一键打开\n"
        f"[*] =====================================\n"
        f"[*] gui-open piano {url} {key}\n"
    )


def _deliver_piano_invites(
    session: piano_sharing.PianoSession,
    *,
    only: Optional[str] = None,
) -> None:
    if file_http is None:
        return
    base_url = file_http.get_base_url().rstrip("/")
    hub = federation.get_hub()
    only_key = (only or "").strip().lower()
    for participant, token in session.tokens.items():
        if only_key and participant.lower() != only_key:
            continue
        key = session.keys.get(participant) or ""
        url = f"{base_url}/piano/{token}"
        message = _piano_invite_message(
            creator=session.creator,
            url=url,
            key=key,
            room=session.room,
            title=session.title,
        )
        recipient_lower = participant.lower()
        delivered = False
        with lock:
            for c, info in clients.items():
                if info["name"].lower() != recipient_lower:
                    continue
                try:
                    send_line(c, message)
                    delivered = True
                except Exception as e:
                    print(f"[Piano] Failed to notify {participant}: {e}")
        if (
            not delivered
            and hub is not None
            and hub.enabled
            and hub.has_remote_user(participant)
        ):
            try:
                hub.send_pm(participant, session.creator, message)
            except Exception as e:
                print(f"[Piano] Federated invite failed for {participant}: {e}")


def _ensure_piano_participant(
    session: piano_sharing.PianoSession, nick: str
) -> Tuple[bool, str]:
    nick = (nick or "").strip()
    if not nick:
        return False, "无效昵称"
    for existing in session.tokens:
        if existing.lower() == nick.lower():
            return True, ""
    token, key, err = piano_sharing.piano_store.add_participant(
        session.session_id, nick
    )
    if err or not token:
        return False, err or "加入失败"
    session.tokens[nick] = token
    session.keys[nick] = key or ""
    return True, ""


def _handle_piano(conn, sender: str, payload: str) -> None:
    """Handle /piano — shared room piano (URL + separate key)."""
    if file_http is None:
        send_line(conn, "[*] 钢琴功能依赖文件网页服务，当前未启用。\n")
        return

    raw = payload[len("/piano") :].strip()

    if raw.lower() in ("help", "?", "帮助"):
        loc = conn_locale(conn)
        if loc == "en":
            send_line(conn, "[*] Usage:\n")
            send_line(conn, "[*]   /piano            - room piano for the current room\n")
            send_line(conn, "[*]   /piano #<room>    - piano for a specific room\n")
            send_line(conn, "[*]   /piano <nick>     - private piano with an online user\n")
            send_line(conn, "[*]   /piano close      - creator closes the current room piano\n")
            send_line(conn, "[*]   /piano new        - force a new piano even if the room already has one\n")
            send_line(
                conn,
                "[*] Desktop: consecutive whites Z→M, A→', 1→=; blacks Q→P/`/numpad. Phone: 3 rows.\n",
            )
            send_line(
                conn,
                "[*] If the room already has a piano, /piano joins it and re-sends your invite.\n",
            )
        else:
            send_line(conn, "[*] 用法：\n")
            send_line(conn, "[*]   /piano            - 当前房间共享钢琴\n")
            send_line(conn, "[*]   /piano #<房间>    - 指定房间共享钢琴\n")
            send_line(conn, "[*]   /piano <昵称>     - 与某人私密钢琴\n")
            send_line(conn, "[*]   /piano close      - 发起人关闭当前房间钢琴\n")
            send_line(conn, "[*]   /piano new        - 强制新开（即使房间已有）\n")
            send_line(
                conn,
                "[*] 电脑白键 Z→M、A→'、1→= 连续排列；黑键 Q→P 等。手机三行分段。\n",
            )
            send_line(
                conn,
                "[*] 房间已有钢琴时，再发 /piano 会加入已有钢琴并给你发邀请。\n",
            )
        return

    parts = raw.split()
    target = parts[0].strip() if parts else ""
    force_new = False
    if target.lower() in ("new", "新建"):
        force_new = True
        target = parts[1].strip() if len(parts) > 1 else ""

    if target.lower() in ("close", "关闭", "end"):
        with lock:
            info = clients.get(conn)
            room_name = (info or {}).get("current_room") or DEFAULT_ROOM
        session = piano_sharing.piano_store.find_open_for_room(room_name)
        if session is None:
            send_line(conn, f"[*] 房间 #{room_name} 当前没有进行中的钢琴。\n")
            return
        ok, err = piano_sharing.piano_store.close_session(session.session_id, sender)
        if not ok:
            send_line(conn, f"[*] {err}\n")
            return
        send_line(conn, f"[*] 已关闭房间 #{room_name} 的共享钢琴。\n")
        broadcast_room(
            room_name,
            f"[*] {sender} 关闭了共享钢琴。\n".encode("utf-8"),
        )
        return

    recipients: list[str] = []
    room_name: Optional[str] = None

    if not target:
        with lock:
            info = clients.get(conn)
            room_name = (info or {}).get("current_room") or DEFAULT_ROOM
    elif target.startswith("#"):
        room_name = normalize_room(target[1:])
        if not room_name:
            send_line(conn, "[*] 无效的房间名。\n")
            return

    if room_name is not None:
        with lock:
            if room_name not in rooms:
                send_line(conn, f"[*] 房间 #{room_name} 不存在。\n")
                return
            if conn not in rooms[room_name]:
                send_line(conn, f"[*] 你不在房间 #{room_name} 中。\n")
                return
            for c in rooms[room_name]:
                if c in clients:
                    recipients.append(clients[c]["name"])

        hub = federation.get_hub()
        if hub is not None and hub.enabled:
            seen = {n.lower() for n in recipients}
            for remote_name in hub.names_in_room(room_name):
                rk = remote_name.lower()
                if rk not in seen:
                    recipients.append(remote_name)
                    seen.add(rk)

        if len({n.lower() for n in recipients}) < 1:
            send_line(conn, f"[*] 房间 #{room_name} 里没有人。\n")
            return

        if not force_new:
            existing = piano_sharing.piano_store.find_open_for_room(room_name)
            if existing is not None:
                ok, err = _ensure_piano_participant(existing, sender)
                if not ok:
                    send_line(conn, f"[*] 加入已有钢琴失败：{err}\n")
                    return
                send_line(
                    conn,
                    f"[*] 房间 #{room_name} 已有共享钢琴；正在把你加入并发送邀请。"
                    f"（强制新开用 /piano new）\n",
                )
                _deliver_piano_invites(existing, only=sender)
                return
        else:
            old = piano_sharing.piano_store.find_open_for_room(room_name)
            if old is not None:
                piano_sharing.piano_store.close_session(old.session_id, old.creator)
    else:
        target_lower = target.lower()
        with lock:
            online = [
                info["name"]
                for info in clients.values()
                if info["name"].lower() == target_lower
            ]
        if not online:
            hub = federation.get_hub()
            if hub is not None and hub.enabled and hub.has_remote_user(target):
                online = [target]
        if not online:
            send_line(conn, f"[*] 用户 {target} 不在线。\n")
            return
        if target_lower == sender.lower():
            send_line(conn, "[*] 私密钢琴请指定另一位在线用户。\n")
            return
        recipients = [sender, online[0]]

    try:
        session = piano_sharing.piano_store.create_session(
            creator=sender,
            participants=recipients,
            room=room_name,
        )
    except Exception as e:
        print(f"[Piano] create failed: {e}")
        traceback.print_exc()
        send_line(conn, "[*] 创建钢琴失败，请稍后重试。\n")
        return

    if room_name:
        broadcast_room(
            room_name,
            f"[*] {sender} 开启了共享钢琴（请查看私信中的网址与密钥）。\n".encode(
                "utf-8"
            ),
        )
    else:
        send_line(conn, f"[*] 已与 {recipients[-1]} 创建私密钢琴。\n")

    _deliver_piano_invites(session)


def _handle_sendfile(conn, sender: str, payload: str) -> None:
    """Handle /sendfile command for secure file sharing."""
    global file_http
    
    if file_http is None:
        send_line(conn, "[*] 文件传输功能未启用。\n")
        return
    
    raw = payload[len("/sendfile"):].strip()
    if payload.startswith("/file"):
        raw = payload[len("/file"):].strip()
    
    if raw.lower() in ("help", "?", "帮助"):
        send_line(conn, "[*] 用法：\n")
        send_line(conn, "[*]   /sendfile          - 发送到当前房间\n")
        send_line(conn, "[*]   /sendfile <昵称>   - 发送给某个用户\n")
        send_line(conn, "[*]   /sendfile #<房间>  - 发送到指定房间\n")
        send_line(conn, "[*] 文件名不用写，以你上传时选的文件为准。\n")
        return
    
    parts = raw.split()
    target = parts[0].strip() if parts else ""
    extra_args = len(parts) > 1
    
    # Determine if sending to room or user
    recipients = []
    room_name = None
    
    if not target:
        # No target: default to the room the sender is currently in
        with lock:
            info = clients.get(conn)
            room_name = (info or {}).get("current_room") or DEFAULT_ROOM
    elif target.startswith("#"):
        room_name = normalize_room(target[1:])
        if not room_name:
            send_line(conn, "[*] 无效的房间名。\n")
            return
    
    is_room = room_name is not None
    
    if is_room:
        with lock:
            if room_name not in rooms:
                send_line(conn, f"[*] 房间 #{room_name} 不存在。\n")
                return
            
            if conn not in rooms[room_name]:
                send_line(conn, f"[*] 你不在房间 #{room_name} 中。\n")
                return
            
            # Everyone in the room except the sender (by nick, not just this SSH
            # session — same user may be logged in on phone + desktop).
            sender_lower = sender.lower()
            seen_names = {sender_lower}
            for c in rooms[room_name]:
                if c == conn or c not in clients:
                    continue
                nick = clients[c]["name"]
                key = nick.lower()
                if key in seen_names:
                    continue
                recipients.append(nick)
                seen_names.add(key)
        
        # Federated peers in the same room (presence) also get a download slot.
        hub = federation.get_hub()
        if hub is not None and hub.enabled:
            seen = {n.lower() for n in recipients}
            seen.add(sender.lower())
            for remote_name in hub.names_in_room(room_name):
                rk = remote_name.lower()
                if rk not in seen:
                    recipients.append(remote_name)
                    seen.add(rk)
        
        if not recipients:
            send_line(conn, f"[*] 房间 #{room_name} 中没有其他用户。\n")
            return
    else:
        # Sending to specific user(s) — local online, federated online, or offline leave.
        target_lower = target.lower()
        with lock:
            online_recipients = [
                info["name"] for info in clients.values()
                if info["name"].lower() == target_lower
            ]
        
        if online_recipients:
            recipients = online_recipients
        else:
            # Offline locally or only present on a peer — open a session either way.
            recipients = [target]
    # Create transfer session (prefer a Cloudflare-capable federation peer when
    # this node only has a LAN / non-public file URL — e.g. iSH --no-cloudflare).
    try:
        hub = federation.get_hub()
        used_proxy = False
        if (
            hub is not None
            and hub.enabled
            and file_http_server.needs_federation_file_proxy(file_http.get_base_url())
        ):
            # Peer may have connected before fpub arrived (catalog-first push on
            # older builds). Nudge a local re-advertise from us; peers push on
            # link-up — if pubs are still empty, tell the user clearly.
            if not hub.has_remote_file_public():
                try:
                    _federation_sync_file_public()
                except Exception:
                    pass
            proxy_peer = hub.pick_file_public_peer()
            if proxy_peer is None:
                send_line(
                    conn,
                    "[*] 联邦中暂无已广告的 Cloudflare/公网文件节点；"
                    "使用本机地址（对端可能打不开）。\n",
                )
            else:
                host_node, peer_url = proxy_peer
                send_line(
                    conn,
                    f"[*] 正在向联邦节点 {host_node} 申请公网通道"
                    f"（{peer_url}）…\n",
                )
                hosted = _federation_request_file_host(
                    host_node, sender, recipients, room_name
                )
                if hosted.get("ok"):
                    upload_url = str(hosted.get("upload_url") or "").strip()
                    upload_key = str(hosted.get("upload_key") or "").strip()
                    if upload_url and upload_key:
                        used_proxy = True
                        if extra_args:
                            send_line(
                                conn,
                                "[*] 提示: 现在不用再写文件名，直接 /sendfile 即可。\n",
                            )
                        send_line(conn, "[*] ========== 文件上传信息 ==========\n")
                        if is_room:
                            send_line(
                                conn,
                                f"[*] 接收者: 房间 #{room_name} ({len(recipients)} 人)\n",
                            )
                        else:
                            send_line(
                                conn, f"[*] 接收者: {', '.join(recipients)}\n"
                            )
                        send_line(
                            conn,
                            f"[*] 经联邦节点 {host_node} 的公网通道"
                            f"（本机无 Cloudflare）\n",
                        )
                        send_line(conn, "[*]\n")
                        send_line(conn, "[*] 上传网址:\n")
                        send_line(conn, f"[*] {upload_url}\n")
                        send_line(conn, "[*]\n")
                        send_line(conn, f"[*] 上传密钥: {upload_key}\n")
                        send_line(conn, "[*]\n")
                        send_line(conn, "[*] 说明:\n")
                        send_line(
                            conn, "[*] 1. 打开上传网址，在页面里输入上面的密钥\n"
                        )
                        send_line(
                            conn,
                            "[*] 2. 选择要发的文件并上传，文件名以所选文件为准\n",
                        )
                        send_line(
                            conn, "[*] 3. 上传成功即完成，此网址随后作废\n"
                        )
                        send_line(
                            conn,
                            "[*] 4. 接收者将收到各自的下载网址和密钥，"
                            "各自只能下载一次\n",
                        )
                        send_line(
                            conn, "[*] 5. 图形客户端会折叠成按钮，可一键打开\n"
                        )
                        send_line(
                            conn, "[*] =====================================\n"
                        )
                        send_line(
                            conn,
                            f"[*] gui-open upload {upload_url} {upload_key}\n",
                        )
                    else:
                        send_line(
                            conn,
                            "[*] 联邦公网文件代理返回无效；改用本机地址。\n",
                        )
                else:
                    err = hosted.get("error") or "unknown"
                    send_line(
                        conn,
                        f"[*] 联邦公网文件代理（{host_node}）失败：{err}；"
                        f"改用本机地址。\n",
                    )

        if used_proxy:
            return

        store = file_sharing.file_transfer_store
        transfer = store.create_upload_session(
            sender=sender,
            recipients=recipients,
            room=room_name
        )

        # Send upload URL and key to sender
        base_url = file_http.get_base_url()
        upload_url = f"{base_url}/upload/{transfer.upload_token}"

        if extra_args:
            send_line(conn, "[*] 提示: 现在不用再写文件名，直接 /sendfile 即可。\n")

        send_line(conn, "[*] ========== 文件上传信息 ==========\n")
        if is_room:
            send_line(conn, f"[*] 接收者: 房间 #{room_name} ({len(recipients)} 人)\n")
        else:
            send_line(conn, f"[*] 接收者: {', '.join(recipients)}\n")
        send_line(conn, "[*]\n")
        send_line(conn, "[*] 上传网址:\n")
        send_line(conn, f"[*] {upload_url}\n")
        send_line(conn, "[*]\n")
        send_line(conn, f"[*] 上传密钥: {transfer.upload_key}\n")
        send_line(conn, "[*]\n")
        send_line(conn, "[*] 说明:\n")
        send_line(conn, "[*] 1. 打开上传网址，在页面里输入上面的密钥\n")
        send_line(conn, "[*] 2. 选择要发的文件并上传，文件名以所选文件为准\n")
        send_line(conn, "[*] 3. 上传成功即完成，此网址随后作废\n")
        send_line(conn, "[*] 4. 接收者将收到各自的下载网址和密钥，各自只能下载一次\n")
        send_line(conn, "[*] 5. 图形客户端会折叠成按钮，可一键打开\n")
        send_line(conn, "[*] =====================================\n")
        send_line(
            conn,
            f"[*] gui-open upload {upload_url} {transfer.upload_key}\n",
        )

    except Exception as e:
        print(f"[FileTransfer] Error creating transfer: {e}")
        traceback.print_exc()
        send_line(conn, "[*] 创建文件传输失败，请稍后重试。\n")


def _fed_snapshot_clients() -> list[dict[str, object]]:
    with lock:
        return [
            {
                "name": info["name"],
                "rooms": sorted(info["rooms"]),
                "current_room": info.get("current_room", DEFAULT_ROOM),
            }
            for info in clients.values()
        ]


def _local_node_id() -> str:
    hub = federation.get_hub()
    if hub is not None:
        return hub.node_id
    return os.environ.get("SSHCHAT_NODE_ID", "").strip() or socket.gethostname()


def _local_conn_for_name_in_room_locked(name: str, room: str):
    key = _nick_key(name)
    if not key:
        return None
    for c in rooms.get(room, ()):
        info = clients.get(c)
        if info and info["name"].strip().lower() == key:
            return c
    return None


def _remap_local_game_seats_locked(room: str, game) -> None:
    local_node = _local_node_id()
    for old_conn, seat_name in list(_iter_game_conn_seats(game)):
        if isinstance(old_conn, FederatedSeat):
            if old_conn.node_id == local_node:
                local = _local_conn_for_name_in_room_locked(seat_name, room)
                if local is not None:
                    _replace_conn_refs(game, old_conn, local)
            continue
        if old_conn in clients and clients[old_conn]["name"] == seat_name:
            continue
        local = _local_conn_for_name_in_room_locked(seat_name, room)
        if local is not None and local is not old_conn:
            _replace_conn_refs(game, old_conn, local)


def _route_game_private(room: str, conn, lines) -> None:
    if not lines:
        return
    if conn in clients:
        send_game_private(conn, room, lines)
        return
    if isinstance(conn, FederatedSeat):
        hub = federation.get_hub()
        if hub is not None and hub.enabled:
            hub.send_game_private_to(conn.node_id, room, conn.nickname, lines)


def _note_greq(room: str, ttl: float = 5.0) -> None:
    """Mark that we asked peers for this room; the reply may replace our board."""
    room = (room or "").strip()
    if room:
        _greq_until[room] = time.monotonic() + max(0.5, float(ttl))


def _greq_outstanding(room: str) -> bool:
    until = _greq_until.get(room) or 0.0
    return time.monotonic() < until


def _clear_greq(room: str) -> None:
    _greq_until.pop(room, None)


def _federation_ask_peers_for_game(room: str) -> None:
    hub = federation.get_hub()
    if hub is None:
        return
    _note_greq(room)
    hub.request_game(room)


def _federation_sync_game(room: str) -> None:
    hub = federation.get_hub()
    if hub is None or not hub.enabled:
        return
    with lock:
        game = room_games.get(room)
        if game is None or getattr(game, "state", "ended") == "ended":
            return
        auth = room_game_authority.get(room) or hub.node_id
        if auth != hub.node_id:
            return
        room_game_authority[room] = hub.node_id
        token = room_game_tokens.get(room) or secrets.token_hex(16)
        room_game_tokens[room] = token
        raw = _pickle_game_for_storage(game)
    hub.sync_game(room, hub.node_id, base64.b64encode(raw).decode("ascii"), token)


def _federation_push_game_snapshot(room: str) -> None:
    """Re-fanout a room's game (owned or replica) so late-joining peers catch up."""
    hub = federation.get_hub()
    if hub is None or not hub.enabled:
        return
    with lock:
        game = room_games.get(room)
        if game is None or getattr(game, "state", "ended") == "ended":
            return
        auth = room_game_authority.get(room) or hub.node_id
        token = room_game_tokens.get(room) or secrets.token_hex(16)
        room_game_tokens[room] = token
        raw = _pickle_game_for_storage(game)
    hub.sync_game(room, auth, base64.b64encode(raw).decode("ascii"), token)


def _federation_push_all_game_snapshots() -> None:
    """After a peer connects, push every active game this node is authority for."""
    hub = federation.get_hub()
    if hub is None or not hub.enabled:
        return
    local = hub.node_id
    with lock:
        rooms = [
            room
            for room, game in room_games.items()
            if game is not None
            and getattr(game, "state", "ended") != "ended"
            # Strict: empty authority after restart must NOT claim host and push
            # a possibly stale replica that races the real host.
            and (room_game_authority.get(room) or "").strip() == local
        ]
    for room in rooms:
        try:
            _federation_push_game_snapshot(room)
        except Exception as e:
            print(f"federation: catch-up gsync failed for room {room!r}: {e!r}")


def _federation_reconcile_restored_games() -> None:
    """After restart, pull newer boards for games whose authority is unknown/remote.

    Session restore used to omit authority, so both nodes treated empty auth as
    local and kept a one-ply-stale replica forever.

    Restores a parked local fork if the authority peer is already gone.
    In-progress replica boards stay visible across restart.
    """
    hub = federation.get_hub()
    if hub is None or not hub.enabled:
        return
    if hub.peer_count < 1:
        # Restart always has a zero-peer window. Parking here hid in-progress
        # boards until (or unless) the host re-pushed. Keep them; peer-up greq
        # will refresh, and a real partition still restores parked local forks.
        return
    local = hub.node_id
    with lock:
        rooms = [
            room
            for room, game in list(room_games.items())
            if game is not None and getattr(game, "state", "ended") != "ended"
        ]
    for room in rooms:
        with lock:
            auth = (room_game_authority.get(room) or "").strip()
            before = _game_progress_score(room_games.get(room))
        if auth == local:
            # Partitioned nodes often still believe they are authority with a stale
            # board. Pull from peers before pushing, or we fan-out an old gsync on
            # link-up and can overwrite the real host (e.g. WSL reconnect).
            try:
                _federation_ask_peers_for_game(room)
            except Exception as e:
                print(f"federation: reconcile greq failed room={room!r}: {e!r}")
            _federation_wait_after_greq(
                room, min_progress=before + 1, prior_auth=auth, timeout=2.0
            )
            with lock:
                auth_now = (room_game_authority.get(room) or "").strip()
                after = _game_progress_score(room_games.get(room))
            if auth_now != local:
                if after > before:
                    print(
                        f"federation: reconciled #{room} progress {before}->{after} "
                        f"auth={auth_now or '?'}"
                    )
                continue
            if after > before:
                print(
                    f"federation: reconciled #{room} progress {before}->{after} "
                    f"auth={local}"
                )
            with lock:
                game_now = room_games.get(room)
                still_active = (
                    game_now is not None
                    and getattr(game_now, "state", "ended") != "ended"
                )
            if still_active:
                try:
                    _federation_push_game_snapshot(room)
                except Exception as e:
                    print(f"federation: reconcile push failed room={room!r}: {e!r}")
            continue
        # Unknown or remote authority: ask peers and keep the newer board.
        try:
            _federation_ask_peers_for_game(room)
        except Exception as e:
            print(f"federation: reconcile greq failed room={room!r}: {e!r}")
            continue
        _federation_wait_after_greq(
            room, min_progress=before + 1, prior_auth=auth, timeout=2.0
        )
        with lock:
            after = _game_progress_score(room_games.get(room))
            auth_now = (room_game_authority.get(room) or "").strip()
        if after > before:
            print(
                f"federation: reconciled #{room} progress {before}->{after} "
                f"auth={auth_now or '?'}"
            )
        elif not auth_now:
            # No peer answered with a newer board — claim local hostship so
            # subsequent moves/syncs have a deterministic authority.
            with lock:
                game_now = room_games.get(room)
                still_active = (
                    game_now is not None
                    and getattr(game_now, "state", "ended") != "ended"
                )
                if still_active and not (room_game_authority.get(room) or "").strip():
                    room_game_authority[room] = local
                    if not (room_game_tokens.get(room) or "").strip():
                        room_game_tokens[room] = secrets.token_hex(16)
            if still_active:
                try:
                    _federation_push_game_snapshot(room)
                except Exception as e:
                    print(
                        f"federation: reconcile claim push failed room={room!r}: {e!r}"
                    )
    # greq may have timed out while the real host is still partitioned away.
    _fed_handle_unreachable_game_authority()


def _fed_on_game_request(_from_peer: str, room: str) -> None:
    """Peer is missing this room's game; only the authority may re-push.

    Replicas answering greq with a stale board can regress peers that already
    received a newer authority snapshot.
    """
    room = (room or "").strip()
    if not room:
        return
    hub = federation.get_hub()
    local = hub.node_id if hub is not None else _local_node_id()
    with lock:
        game = room_games.get(room)
        auth = (room_game_authority.get(room) or "").strip()
        if game is None or getattr(game, "state", "ended") == "ended":
            # Authority with no active game (ended while partitioned) must gend
            # so stale replicas clear instead of fan-out after a silent greq.
            if auth == local:
                hub.end_game(room, local, _ended_token_for_room_locked(room))
            return
        # Empty auth after restart must not answer — a stale replica would claim
        # hostship via push_game_snapshot's `auth or node_id` fallback.
        if auth != local:
            return
    _federation_push_game_snapshot(room)


def _federation_request_game_and_wait(room: str, timeout: float = 1.5) -> bool:
    """Ask peers for a missing room game and wait briefly for gsync."""
    hub = federation.get_hub()
    if hub is None or not hub.enabled or hub.peer_count < 1:
        return False
    with lock:
        if room_games.get(room) is not None:
            return True
        # Local authority with no board = ended tombstone. greq would only
        # fetch a stale peer replica (WSL) and revive the finished game on
        # /game show / join.
        auth = (room_game_authority.get(room) or "").strip()
        local = hub.node_id
        if auth == local:
            try:
                hub.end_game(room, local, _ended_token_for_room_locked(room))
            except Exception as e:
                print(f"federation: tombstone gend failed room={room!r}: {e!r}")
            return False
    try:
        _federation_ask_peers_for_game(room)
    except Exception as e:
        print(f"federation: greq failed for room {room!r}: {e!r}")
        return False
    deadline = time.time() + max(0.2, float(timeout))
    while time.time() < deadline:
        with lock:
            game = room_games.get(room)
            if game is not None and getattr(game, "state", "ended") != "ended":
                return True
        time.sleep(0.05)
    return False


def _federation_broadcast_ended_tombstones() -> None:
    """Tell peers to drop boards for rooms we already ended (auth-only)."""
    hub = federation.get_hub()
    if hub is None or not hub.enabled or hub.peer_count < 1:
        return
    local = hub.node_id
    with lock:
        rooms = [
            (room, _ended_token_for_room_locked(room))
            for room, auth in list(room_game_authority.items())
            if (auth or "").strip() == local
            and (
                room not in room_games
                or getattr(room_games.get(room), "state", "ended") == "ended"
            )
        ]
    for room, token in rooms:
        try:
            hub.end_game(room, local, token)
        except Exception as e:
            print(f"federation: tombstone gend failed room={room!r}: {e!r}")


def _federation_wait_game_progress(
    room: str, min_progress: int, timeout: float = 1.5
) -> bool:
    """Poll until local replica progress reaches min_progress (no greq)."""
    deadline = time.time() + max(0.2, float(timeout))
    target = int(min_progress)
    while time.time() < deadline:
        with lock:
            if _game_progress_score(room_games.get(room)) >= target:
                return True
        time.sleep(0.05)
    with lock:
        return _game_progress_score(room_games.get(room)) >= target


def _federation_wait_after_greq(
    room: str,
    *,
    min_progress: int,
    prior_auth: str = "",
    timeout: float = 2.0,
) -> bool:
    """Wait for a greq reply: newer ply, gend clear, or authority change."""
    deadline = time.time() + max(0.2, float(timeout))
    target = int(min_progress)
    prior_auth = (prior_auth or "").strip()

    def _settled() -> bool:
        game = room_games.get(room)
        auth = (room_game_authority.get(room) or "").strip()
        if game is None:
            return True
        if getattr(game, "state", "ended") == "ended":
            return True
        if prior_auth and auth != prior_auth:
            return True
        return _game_progress_score(game) >= target

    while time.time() < deadline:
        with lock:
            if _settled():
                return True
        time.sleep(0.05)
    with lock:
        return _settled()


def _federation_refresh_replica_and_wait(
    room: str,
    *,
    min_progress: int | None = None,
    timeout: float = 2.0,
) -> bool:
    """Ask authority for a fresh gsync and wait until local progress catches up.

    Used before /game show on a non-authority node so a delayed/lost gsync after a
    forwarded move does not leave /game show on the previous ply.
    """
    hub = federation.get_hub()
    if hub is None or not hub.enabled or hub.peer_count < 1:
        return False
    local = hub.node_id
    with lock:
        auth = (room_game_authority.get(room) or "").strip()
        game = room_games.get(room)
        if auth and auth == local:
            return True
        before = _game_progress_score(game)
    try:
        _federation_ask_peers_for_game(room)
    except Exception as e:
        print(f"federation: refresh greq failed for room {room!r}: {e!r}")
        return False
    if min_progress is None:
        # Prefer a strictly newer board when one is available; otherwise time out
        # and show whatever we already have.
        target = before + 1
    else:
        target = max(before + 1, int(min_progress))
    return _federation_wait_game_progress(room, target, timeout=timeout)

def _federation_notify_game_end(room: str) -> None:
    hub = federation.get_hub()
    if hub is None or not hub.enabled:
        return
    with lock:
        auth = (room_game_authority.get(room) or "").strip() or hub.node_id
        token = (room_game_tokens.pop(room, None) or "").strip()
        if token:
            _remember_ended_game_locked(room, token)
        # Keep authority so greq can answer gend after partition (WSL missed gend).
        room_game_authority[room] = auth
        room_game_provisional.discard(room)
        room_games_parked.pop(room, None)
    _persist_after_game_change()
    if auth == hub.node_id:
        hub.end_game(room, hub.node_id, token)


def _game_progress_score(game) -> int:
    """Best-effort ply/move count for waiting until a replica catches a move."""
    if game is None:
        return 0
    hist = getattr(game, "_history", None)
    if isinstance(hist, list):
        return len(hist)
    ply = getattr(game, "_xq_ply_log", None)
    if isinstance(ply, list):
        return len(ply)
    board = getattr(game, "board", None)
    if board is not None:
        stack = getattr(board, "move_stack", None)
        if stack is not None:
            try:
                return len(stack)
            except Exception:
                pass
    return 0


def _game_conflict_winner(
    auth_a: str, token_a: str, auth_b: str, token_b: str
) -> tuple[str, str]:
    """Pick one of two conflicting games. Tokens are random; compare is deterministic."""
    a_auth = (auth_a or "").strip()
    b_auth = (auth_b or "").strip()
    a_tok = (token_a or "").strip() or a_auth
    b_tok = (token_b or "").strip() or b_auth
    if a_tok > b_tok:
        return a_auth, a_tok
    if b_tok > a_tok:
        return b_auth, b_tok
    if a_auth >= b_auth:
        return a_auth, a_tok
    return b_auth, b_tok


def _game_sync_should_keep_local(
    *,
    local_game,
    remote_game,
    local_auth: str,
    authority: str,
    local_id: str,
    local_token: str,
    conflict_token: str,
    greq: bool,
) -> bool | None:
    """Return True/False to pick a side, or None to fall through to token tiebreak."""
    local_active = (
        local_game is not None
        and getattr(local_game, "state", "ended") != "ended"
    )
    remote_active = getattr(remote_game, "state", "ended") != "ended"
    if not local_active or not remote_active:
        return None
    we_host = local_auth == local_id
    if we_host and not greq:
        return True
    local_ts = games.game_session_updated_at(local_game)
    remote_ts = games.game_session_updated_at(remote_game)
    if local_ts > 0 or remote_ts > 0:
        if remote_ts > local_ts:
            return False
        if local_ts > remote_ts:
            return True
    return None


def _park_room_game_locked(room: str, game) -> None:
    """Stash a displaced game so a later partition can restore it."""
    if game is None or getattr(game, "state", "ended") == "ended":
        return
    room_games_parked[room] = game


def _promote_parked_game_locked(room: str):
    """Move a parked in-progress game into an idle room. Caller holds lock."""
    parked = room_games_parked.get(room)
    if parked is None or getattr(parked, "state", "ended") == "ended":
        return None
    active = room_games.get(room)
    if active is not None and getattr(active, "state", "ended") != "ended":
        return None
    room_games_parked.pop(room, None)
    _remap_local_game_seats_locked(room, parked)
    _rebind_game_services(parked)
    room_games[room] = parked
    hub = federation.get_hub()
    local = hub.node_id if hub is not None else _local_node_id()
    room_game_authority[room] = local
    if not (room_game_tokens.get(room) or "").strip():
        room_game_tokens[room] = secrets.token_hex(16)
    room_game_provisional.add(room)
    return parked


def _restore_idle_parked_games_locked() -> list[tuple[str, object]]:
    restored: list[tuple[str, object]] = []
    for room in list(room_games_parked):
        game = _promote_parked_game_locked(room)
        if game is not None:
            restored.append((room, game))
    return restored


def _game_authority_reachable(auth: str) -> bool:
    auth = (auth or "").strip()
    hub = federation.get_hub()
    local = hub.node_id if hub is not None else _local_node_id()
    if not auth or auth == local:
        return True
    if hub is None or not hub.enabled:
        return False
    return hub._link_toward(auth) is not None


def _fed_handle_unreachable_game_authority(_down_peer: str = "") -> None:
    """When a game host is unreachable: restore a parked local fork if any.

    In-progress replica boards stay in the room. Parking them on restart or
    peer-down made half-played games vanish until the host re-pushed.
    """
    hub = federation.get_hub()
    local = hub.node_id if hub is not None else _local_node_id()
    restored: list[tuple[str, object]] = []
    with lock:
        rooms = set(room_games) | set(room_games_parked)
        for room in list(rooms):
            auth = (room_game_authority.get(room) or "").strip()
            game = room_games.get(room)
            active = game is not None and getattr(game, "state", "ended") != "ended"
            if not active or not auth or auth == local:
                continue
            if _game_authority_reachable(auth):
                continue
            parked = room_games_parked.get(room)
            parked_ok = (
                parked is not None and getattr(parked, "state", "ended") != "ended"
            )
            if not parked_ok:
                continue
            room_games_parked.pop(room, None)
            _remap_local_game_seats_locked(room, parked)
            _rebind_game_services(parked)
            room_games[room] = parked
            room_game_authority[room] = local
            room_game_tokens[room] = secrets.token_hex(16)
            room_game_provisional.add(room)
            restored.append((room, parked))

    for room, game in restored:
        notice = (
            f"[*] 联邦断开：#{room} 权威节点不可达，已恢复本节点暂存对局"
            f"（{getattr(game, 'name', '?')}）。请用 /game show 查看。\n"
        )
        broadcast_room(room, notice.encode("utf-8"))
        if getattr(game, "state", "ended") != "ended":
            send_oriented_boards(room, game)
            send_sanguo_hand_views(room, game)
    if restored:
        _persist_after_game_change()


def _fed_on_game_sync(
    _from_peer: str,
    room: str,
    authority: str,
    b64: str,
    conflict_token: str = "",
) -> None:
    hub = federation.get_hub()
    local_id = hub.node_id if hub is not None else _local_node_id()
    authority = (authority or "").strip()
    conflict_token = (conflict_token or "").strip() or authority
    if authority == local_id:
        # Echo of our own authority snapshot (or stale self-claim).
        return
    try:
        game = pickle.loads(base64.b64decode(b64.encode("ascii")))
    except Exception as e:
        print(f"federation game sync skip room {room!r}: {e!r}")
        return
    _rebind_game_services(game)

    lost_local = False
    loser_auth = ""
    winner_auth = authority
    keep_local = False
    greq = _greq_outstanding(room)
    with lock:
        local_game = room_games.get(room)
        local_auth = (room_game_authority.get(room) or "").strip()
        local_token = (room_game_tokens.get(room) or "").strip() or local_auth
        local_active = (
            local_game is not None
            and getattr(local_game, "state", "ended") != "ended"
        )
        remote_active = getattr(game, "state", "ended") != "ended"
        we_host = local_auth == local_id
        if _game_id_is_ended_locked(conflict_token):
            # Offline replica of a finished session; never revive by ply count.
            return
        if not local_active and remote_active and we_host:
            # Ended tombstone: ignore even if we greq'd (stale replica answering).
            return
        if (
            local_active
            and remote_active
            and local_auth == authority
            and not greq
        ):
            local_ts = games.game_session_updated_at(local_game)
            remote_ts = games.game_session_updated_at(game)
            if local_ts > 0 or remote_ts > 0:
                if remote_ts <= local_ts:
                    return
            # Same host: their snapshot is source of truth. Do not compare plies.
        if local_active and remote_active and local_auth and local_auth != authority:
            pick = _game_sync_should_keep_local(
                local_game=local_game,
                remote_game=game,
                local_auth=local_auth,
                authority=authority,
                local_id=local_id,
                local_token=local_token,
                conflict_token=conflict_token,
                greq=greq,
            )
            if pick is True:
                room_game_tokens[room] = local_token
                keep_local = True
            elif pick is False:
                winner_auth = authority
            else:
                win_auth, win_tok = _game_conflict_winner(
                    local_auth, local_token, authority, conflict_token
                )
                winner_auth = win_auth
                if win_auth == local_auth:
                    room_game_tokens[room] = win_tok or local_token
                    keep_local = True
                else:
                    lost_local = True
                    loser_auth = local_auth

        if not keep_local:
            if (
                local_active
                and local_auth
                and local_auth != authority
            ):
                _park_room_game_locked(room, local_game)
                lost_local = True
                loser_auth = loser_auth or local_auth
            _remap_local_game_seats_locked(room, game)
            room_games[room] = game
            room_game_authority[room] = authority
            room_game_tokens[room] = conflict_token

    if keep_local:
        try:
            _federation_push_game_snapshot(room)
        except Exception as e:
            print(f"federation: re-push after winning conflict: {e!r}")
        return
    _clear_greq(room)

    # Immediate persist: debounce alone left replicas one ply behind after restart
    # when the host had already flushed the newer move.
    _persist_after_game_change()
    if lost_local:
        notice = (
            f"[*] 联邦对局冲突：#{room} 同时存在多局，已保留节点 "
            f"{winner_auth} 的对局；节点 {loser_auth} 上的对局已暂存。"
            f"联邦断开后可恢复暂存局；请用 /game show 查看当前局面。\n"
        )
        broadcast_room(room, notice.encode("utf-8"))
    if getattr(game, "state", "ended") != "ended":
        send_oriented_boards(room, game)
        send_sanguo_hand_views(room, game)


def _fed_on_game_end(room: str, authority: str, token: str = "") -> None:
    hub = federation.get_hub()
    local = hub.node_id if hub is not None else _local_node_id()
    if authority == local:
        return
    greq = _greq_outstanding(room)
    token = (token or "").strip()
    with lock:
        if token:
            _remember_ended_game_locked(room, token)
        local_auth = (room_game_authority.get(room) or "").strip()
        local_token = (room_game_tokens.get(room) or "").strip()
        game = room_games.get(room)
        local_active = (
            game is not None and getattr(game, "state", "ended") != "ended"
        )
        same_game = bool(token) and local_token == token
        provisional = room in room_game_provisional
        # A replica must not gend-wipe a live game we host. Honor the real
        # host's receipt for this session id, a provisional claim, or greq.
        if (
            local_auth == local
            and local_active
            and not greq
            and not same_game
            and not provisional
        ):
            return
        room_games.pop(room, None)
        room_game_authority.pop(room, None)
        room_game_tokens.pop(room, None)
        room_games_parked.pop(room, None)
        room_game_provisional.discard(room)
    _clear_greq(room)
    _persist_after_game_change()


def _fed_on_game_priv(room: str, to_name: str, lines: list[str]) -> None:
    targets = find_clients_by_nickname(to_name, local_only=True)
    for conn, _ in targets:
        send_game_private(conn, room, lines)


def _fed_resolve_actor(room: str, game, player_node: str, name: str, sub: str):
    local = _local_node_id()
    if player_node == local:
        conn = _local_conn_for_name_in_room_locked(name, room)
        if conn is not None and sub != "join":
            _resume_same_account_seat_locked(room, game, conn, name)
        return conn
    # Remote player: always act as FederatedSeat for *this* peer node.
    # Returning a stale DisconnectedSeat / old-node FederatedSeat / local socket
    # skips resume and drops private replies (not a FederatedSeat → no gpriv).
    seat = FederatedSeat(player_node, name)
    _resume_same_account_seat_locked(room, game, seat, name)
    return seat


def _fed_send_player_notice(
    player_node: str, room: str, name: str, lines: list[str]
) -> None:
    """Best-effort private notice to a federation player (or local conn)."""
    if not lines:
        return
    local = _local_node_id()
    if player_node == local:
        conn = _local_conn_for_name_in_room_locked(name, room)
        if conn is not None:
            send_game_private(conn, room, lines)
        return
    hub = federation.get_hub()
    if hub is not None and hub.enabled:
        hub.send_game_private_to(player_node, room, name, lines)


def _apply_game_canvas_actions(room: str, game) -> None:
    """Honor optional canvas orchestration hooks from social games (e.g. drawguess)."""
    drain = getattr(game, "drain_canvas_actions", None)
    if not callable(drain):
        return
    try:
        actions = list(drain() or [])
    except Exception:
        print(f"game canvas drain failed: room={room!r}")
        traceback.print_exc()
        return
    for action in actions:
        if action != "clear":
            continue
        try:
            cleared = canvas_sharing.canvas_store.clear_open_room_board(room)
        except Exception:
            print(f"game canvas clear failed: room={room!r}")
            traceback.print_exc()
            continue
        if cleared:
            broadcast_game(room, ["房间画板已清空，请画家重新作画。"])


def _finish_game_action(
    room: str,
    game,
    actor_conn,
    priv,
    bcast,
    ended: bool,
    *,
    send_boards: bool = True,
) -> None:
    if bcast and hasattr(game, "finalize_broadcast"):
        bcast = game.finalize_broadcast(bcast)
    _route_game_private(room, actor_conn, priv)
    drain = getattr(game, "drain_extra_privates", None)
    if drain:
        for peer_conn, extra in drain():
            _route_game_private(room, peer_conn, extra)
    if bcast:
        broadcast_game(room, bcast)
    try:
        _apply_game_canvas_actions(room, game)
    except Exception:
        print(f"game canvas actions failed: room={room!r}")
        traceback.print_exc()
    # The move is committed before this function runs. Rendering, persistence,
    # and federation are follow-up services and must not turn it into a generic
    # command failure when one of them is temporarily unavailable.
    try:
        if send_boards and (ended or getattr(game, "send_view_on_move", True)):
            send_oriented_boards(room, game)
    except Exception:
        print(f"game board refresh failed: room={room!r} game={getattr(game, 'name', '?')!r}")
        traceback.print_exc()
        _route_game_private(room, actor_conn, ["Move accepted; board refresh failed. Use /game show to retry."])
    try:
        send_sanguo_hand_views(room, game)
    except Exception:
        print(f"game private view failed: room={room!r} game={getattr(game, 'name', '?')!r}")
        traceback.print_exc()
    if not ended:
        try:
            games.touch_session(game)
        except Exception:
            print(f"game session timestamp failed: room={room!r} game={getattr(game, 'name', '?')!r}")
            traceback.print_exc()
    try:
        _persist_after_game_change()
    except Exception:
        print(f"game persistence failed: room={room!r} game={getattr(game, 'name', '?')!r}")
        traceback.print_exc()
    if ended:
        with lock:
            room_games.pop(room, None)
        try:
            _federation_notify_game_end(room)
        except Exception:
            print(f"game federation end failed: room={room!r} game={getattr(game, 'name', '?')!r}")
            traceback.print_exc()
    else:
        try:
            _federation_sync_game(room)
        except Exception:
            print(f"game federation sync failed: room={room!r} game={getattr(game, 'name', '?')!r}")
            traceback.print_exc()


def _fed_execute_game_cmd(
    _from_peer: str,
    room: str,
    player_node: str,
    name: str,
    sub: str,
    rest: str,
) -> None:
    local = _local_node_id()
    sub = sub.lower()
    with lock:
        if room_game_authority.get(room, local) != local:
            return

    if sub == "join":
        with lock:
            game = room_games.get(room)
            if game is None:
                _fed_send_player_notice(
                    player_node,
                    room,
                    name,
                    ["本房没有进行中的对局；用 /game new … 开局。"],
                )
                return
            actor = _fed_resolve_actor(room, game, player_node, name, sub)
            if isinstance(actor, FederatedSeat):
                # Resume may have already seated this FederatedSeat (same nick).
                if getattr(game, "is_seated", lambda _c: False)(actor):
                    priv = ["检测到你在其他终端已有席位，已自动续玩接管。"]
                    bcast = [f"{name} 从另一终端接管了本局操作。"]
                else:
                    priv, bcast, _ = game.try_join(actor, name)
            else:
                resumed = _resume_same_account_seat_locked(room, game, actor, name)
                if resumed:
                    priv = ["检测到你在其他终端已有席位，已自动续玩接管。"]
                    bcast = [f"{name} 从另一终端接管了本局操作。"]
                else:
                    priv, bcast, _ = game.try_join(actor, name)
        _finish_game_action(room, game, actor, priv, bcast, False)
        return

    with lock:
        game = room_games.get(room)
        actor = _fed_resolve_actor(room, game, player_node, name, sub) if game else None

    if sub == "move":
        if game is None or actor is None:
            _fed_send_player_notice(
                player_node,
                room,
                name,
                ["本房没有进行中的对局，或席位无法续玩；可试 /game show。"],
            )
            return
        try:
            with lock:
                # Resolve already attempted resume; call again is cheap/no-op if seated.
                resumed = _resume_same_account_seat_locked(room, game, actor, name)
                _ensure_game_runtime_compat(game)
                priv, bcast, ended = game.try_move(actor, rest)
        except Exception as e:
            print(f"fed /game move failed: {e!r}")
            _fed_send_player_notice(
                player_node,
                room,
                name,
                [f"/game move 执行失败：{e}"],
            )
            return
        if not ended:
            extra = _nudge_game_bots_locked(game)
            if extra:
                bcast = list(bcast) + extra
                ended = getattr(game, "state", "ended") == "ended"
        if resumed:
            priv = ["你已从其他终端续玩接管，以下是本次操作结果："] + list(priv)
        _finish_game_action(room, game, actor, priv, bcast, ended)
        return

    if sub == "resign":
        if game is None or actor is None:
            _fed_send_player_notice(
                player_node,
                room,
                name,
                ["本房没有进行中的对局，或席位无法续玩。"],
            )
            return
        with lock:
            resumed = _resume_same_account_seat_locked(room, game, actor, name)
            priv, bcast, ended = game.resign(actor, name)
        if not ended:
            extra = _nudge_game_bots_locked(game)
            if extra:
                bcast = list(bcast) + extra
                ended = getattr(game, "state", "ended") == "ended"
        if resumed:
            priv = ["你已从其他终端续玩接管，以下是本次操作结果："] + list(priv)
        _finish_game_action(room, game, actor, priv, bcast, ended)
        return

    if sub == "undo":
        if game is None or actor is None or not hasattr(game, "request_undo"):
            return
        undo_action, undo_err = games.parse_undo_action(rest)
        if undo_err:
            _route_game_private(room, actor, [undo_err])
            return
        with lock:
            if undo_action == "accept":
                priv, bcast, _ = game.accept_undo(actor)
            elif undo_action == "reject":
                priv, bcast, _ = game.reject_undo(actor)
            elif undo_action == "cancel":
                priv, bcast, _ = game.cancel_undo(actor)
            else:
                priv, bcast, _ = game.request_undo(actor)
        _finish_game_action(room, game, actor, priv, bcast, False, send_boards=bool(bcast))
        return

    if sub == "abort":
        if game is None or actor is None:
            return
        with lock:
            resumed = _resume_same_account_seat_locked(room, game, actor, name)
            priv, bcast, _ = game.abort(actor, name)
        if resumed:
            priv = ["你已从其他终端续玩接管，以下是本次操作结果："] + list(priv)
        _finish_game_action(room, game, actor, priv, bcast, False, send_boards=False)
        return

    if sub == "end":
        if game is None:
            return
        with lock:
            owner_conn = room_owners.get(room)
            owner_name = clients[owner_conn]["name"] if owner_conn in clients else ""
            if owner_name.strip().lower() != name.strip().lower():
                return
        with lock:
            room_games.pop(room, None)
        broadcast_game(room, [f"{name}（房主）结束了本房的对局。"])
        _federation_notify_game_end(room)
        _persist_after_game_change()


def _game_all_seat_nicks_present_locally_locked(
    room: str, game, local_node: str
) -> bool:
    """True only when every seat owner is currently online in this room.

    DisconnectedSeat / missing opponents must NOT look "local-only", or a peer
    that only has the resuming player will wrongly reclaim authority and push a
    divergent board back to the real host.
    """
    has_seat = False
    for conn, name in _iter_game_conn_seats(game):
        has_seat = True
        if isinstance(conn, FederatedSeat) and conn.node_id != local_node:
            return False
        if _local_conn_for_name_in_room_locked(name, room) is None:
            return False
    return has_seat


def _reclaim_game_authority_for_local_seats(room: str) -> bool:
    """Take authority when a remote claim remains but every seat nick is here.

    Common after gsync: both players reconnect to the same SSHChat host while
    ``room_game_authority`` still points at a peer. Forwarding ``/game move`` then
    black-holes when the federation link is flaky. Returns True when authority is
    local afterwards (including already-local / empty-then-claimed).
    """
    hub = federation.get_hub()
    local = hub.node_id if hub is not None else _local_node_id()
    push = False
    with lock:
        auth = (room_game_authority.get(room) or "").strip()
        game = room_games.get(room)
        if game is None or getattr(game, "state", "ended") == "ended":
            return (not auth) or auth == local
        if not auth or auth == local:
            if not auth:
                room_game_authority[room] = local
                if not (room_game_tokens.get(room) or "").strip():
                    room_game_tokens[room] = secrets.token_hex(16)
            return True
        if not _game_all_seat_nicks_present_locally_locked(room, game, local):
            return False
        room_game_authority[room] = local
        room_game_tokens[room] = secrets.token_hex(16)
        push = True
    if push:
        print(
            f"federation: reclaimed game authority for #{room} "
            f"(all seat nicks local on {local})"
        )
        try:
            _federation_sync_game(room)
        except Exception as e:
            print(f"federation: reclaim sync failed room={room!r}: {e!r}")
    return True


def _should_forward_game(room: str, sub: str) -> bool:
    hub = federation.get_hub()
    if hub is None or not hub.enabled:
        return False
    if sub not in (
        "join",
        "move",
        "resign",
        "undo",
        "abort",
        "end",
    ):
        return False
    auth = (room_game_authority.get(room) or "").strip()
    if not auth or auth == hub.node_id:
        return False
    # Stale remote authority with only local players → play here, don't black-hole.
    if _reclaim_game_authority_for_local_seats(room):
        return False
    return True


def _fed_on_room_msg(room: str, msg: bytes, from_peer: str) -> None:
    # Hub already fanouts the original msg line; only deliver locally.
    # Join/leave presence uses dedicated join/leave frames — ignore chat-shaped
    # duplicates from older peers that still federated those via msg.
    if _is_presence_chat_notice(msg):
        return
    parsed = _parse_game_broadcast_msg(msg)
    if parsed is not None:
        parsed_room, bodies = parsed
        deliver_room = room or parsed_room
        if not _should_skip_game_localize(deliver_room):
            _deliver_game_lines_localized(deliver_room, bodies)
            return
    broadcast_room(room, msg, via_federation_from=from_peer, skip_federation=True)


def _is_presence_chat_notice(msg: bytes) -> bool:
    """True for `[+] nick joined #room` / `[!] nick left #room` system lines."""
    try:
        text = msg.decode("utf-8", errors="replace").strip()
    except Exception:
        return False
    if text.startswith("[+] ") and " joined #" in text:
        return True
    if text.startswith("[!] ") and " left #" in text:
        return True
    return False


def _fed_on_join_notice(room: str, msg: bytes) -> None:
    broadcast_room(room, msg, skip_federation=True)


def _fed_on_peer_event(event: str, peer_node: str, reporter: str) -> None:
    """Tell all local chat users when a federation node comes or goes."""
    hub = federation.get_hub()
    local_id = hub.node_id if hub is not None else _local_node_id()
    peer_node = (peer_node or "").strip() or "?"
    reporter = (reporter or "").strip() or "?"
    if event == "up":
        if reporter == local_id:
            text = f"[*] 联邦节点 {peer_node} 已加入（与本机已连通）\n"
            # Pull before push on link-up so a partitioned replica (common on
            # WSL) does not fan-out a stale board before seeing the real host.
            try:
                _federation_reconcile_restored_games()
            except Exception as e:
                print(f"federation: peer-up game reconcile error: {e!r}")
            try:
                _federation_broadcast_ended_tombstones()
            except Exception as e:
                print(f"federation: peer-up ended-tombstone gend error: {e!r}")
            try:
                _federation_push_all_game_snapshots()
            except Exception as e:
                print(f"federation: peer-up game catch-up error: {e!r}")
            try:
                _federation_sync_library_catalog()
            except Exception as e:
                print(f"federation: peer-up library catalog sync error: {e!r}")
            try:
                _federation_sync_file_public()
            except Exception as e:
                print(f"federation: peer-up file public sync error: {e!r}")
            try:
                _federation_push_all_offline_clears()
            except Exception as e:
                print(f"federation: peer-up offline clear sync error: {e!r}")
            try:
                _federation_push_all_offline_leaves()
            except Exception as e:
                print(f"federation: peer-up offline leave sync error: {e!r}")
            try:
                _federation_sync_ratings(rating_store.export_entries())
            except Exception as e:
                print(f"federation: peer-up ratings sync error: {e!r}")
            try:
                _federation_push_all_canvas_announces()
            except Exception as e:
                print(f"federation: peer-up canvas catch-up error: {e!r}")
        else:
            text = f"[*] 联邦节点 {peer_node} 已加入（由 {reporter} 通报）\n"
    elif event == "down":
        if reporter == local_id:
            text = f"[*] 联邦节点 {peer_node} 已退出（与本机断开）\n"
        else:
            text = f"[*] 联邦节点 {peer_node} 已退出（由 {reporter} 通报）\n"
        _fail_library_page_waiters(f"联邦节点 {peer_node} 已断开，图书页拉取中断")
        _fail_file_host_waiters(f"联邦节点 {peer_node} 已断开，文件代理中断")
        try:
            _fed_handle_unreachable_game_authority(peer_node)
        except Exception as e:
            print(f"federation: peer-down game park/restore error: {e!r}")
        try:
            _fed_handle_unreachable_canvas_authority(peer_node)
        except Exception as e:
            print(f"federation: peer-down canvas park/restore error: {e!r}")
    else:
        return
    broadcast_local_notice(text)


def broadcast_local_notice(text: str) -> None:
    """Send a system line to every locally connected client (no federation fan-out)."""
    with lock:
        targets = list(clients.keys())
    dead = []
    for c in targets:
        try:
            send_line(c, text)
        except Exception:
            dead.append(c)
    for c in dead:
        remove_client(c)


def _fed_on_pm(to_name: str, from_name: str, text: str) -> None:
    targets = find_clients_by_nickname(to_name, local_only=True)
    # Canvas invites are system blocks (gui-open canvas); keep them unwrapped
    # so GUI clients can auto-open. Regular PMs still get the PM prefix.
    canvas_invite = "gui-open canvas " in (text or "")
    for peer_conn, _ in targets:
        if canvas_invite:
            payload = text if text.endswith("\n") else f"{text}\n"
            send_line(peer_conn, payload)
        else:
            send_line(peer_conn, f"[PM from {from_name}] {text}\n")


def _fed_on_file_notice(to_name: str, from_name: str, notice: dict) -> None:
    """Deliver a federated /sendfile download notice to a local user (or leave offline)."""
    if not isinstance(notice, dict):
        return
    download_url = str(notice.get("download_url") or "").strip()
    key = str(notice.get("download_key") or "").strip()
    if not download_url or not key:
        return
    filename = str(notice.get("filename") or "").strip() or "file"
    try:
        file_size = int(notice.get("file_size") or 0)
    except (TypeError, ValueError):
        file_size = 0
    room = notice.get("room")
    room_name = str(room).strip() if room else None
    message = _build_file_ready_message(
        sender=from_name,
        filename=filename,
        file_size=file_size,
        download_url=download_url,
        key=key,
        room=room_name or None,
    )
    targets = find_clients_by_nickname(to_name, local_only=True)
    if targets:
        for peer_conn, _ in targets:
            try:
                send_line(peer_conn, message)
            except Exception as e:
                print(f"[FileTransfer] Federated notify failed for {to_name}: {e}")
        # Live delivery — drop any seeded offline copies (including origin).
        tid = str(notice.get("transfer_id") or "").strip()
        if tid:
            offline_messages.remove_file_by_transfer(to_name, tid)
            _federation_clear_file_leave(to_name, tid)
        return

    # Recipient is offline on this node — store absolute origin URL for later login.
    summary = _format_file_leave_summary(filename, file_size)
    try:
        leave_ts = float(notice.get("leave_ts") or 0) or None
    except (TypeError, ValueError):
        leave_ts = None
    offline_messages.leave(
        to_name,
        from_name,
        summary,
        kind="file",
        meta={
            "transfer_id": str(notice.get("transfer_id") or "").strip(),
            "filename": filename,
            "file_size": file_size,
            "download_token": str(notice.get("download_token") or "").strip(),
            "download_key": key,
            "download_url": download_url,
            "room": room_name,
            "federated": True,
        },
        ts=leave_ts,
    )


def _fed_on_file_leave_clear(to_name: str, transfer_id: str) -> None:
    removed = offline_messages.remove_file_by_transfer(to_name, transfer_id)
    if removed:
        print(f"[FileTransfer] Federation clear: removed {len(removed)} file leave(s) "
              f"for {to_name} (transfer_id={transfer_id})")
    else:
        print(f"[FileTransfer] Federation clear: no file leave found for {to_name} "
              f"(transfer_id={transfer_id})")


def _fed_on_offline_pm(
    to_name: str, from_name: str, text: str, leave_id: str = "", leave_ts: float = 0.0
) -> None:
    """Seed or deliver an offline text leave that originated on another node."""
    targets = find_clients_by_nickname(to_name, local_only=True)
    if targets:
        for peer_conn, _ in targets:
            send_line(peer_conn, f"[PM from {from_name}] {text}\n")
        if leave_id:
            offline_messages.remove_by_id(to_name, leave_id)
            _federation_clear_offline_pm(to_name, leave_id)
        return
    try:
        ts = float(leave_ts) if leave_ts else None
    except (TypeError, ValueError):
        ts = None
    offline_messages.leave(
        to_name, from_name, text, leave_id=leave_id or None, ts=ts
    )


def _fed_on_offline_pm_clear(to_name: str, leave_id: str) -> None:
    offline_messages.remove_by_id(to_name, leave_id)


def _fed_on_ratings(origin: str, rows: list) -> None:
    """Merge peer rating ledger; newer host settlements win for same nick."""
    if not isinstance(rows, list):
        return
    applied = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        game = str(row.get("game") or "").strip()
        user = str(row.get("user") or row.get("display_name") or "").strip()
        if not game or not user or not is_rated_game(game):
            continue
        if rating_store.apply_remote_entry(game, user, row, source_node=origin):
            applied += 1
    if applied:
        print(f"federation: applied {applied} rating row(s) from {origin}")


def _ensure_federation_hub() -> None:
    global _fed_hub, _library_watch_thread
    if _fed_hub is not None:
        return
    _fed_hub = federation.init_hub(
        PORT,
        lock,
        _fed_on_room_msg,
        _fed_on_join_notice,
        _fed_on_pm,
        _fed_snapshot_clients,
        _fed_on_game_sync,
        _fed_on_game_end,
        _fed_execute_game_cmd,
        _fed_on_game_priv,
        _fed_on_file_notice,
        _fed_on_peer_event,
        _fed_on_game_request,
        _fed_local_library_snapshot,
        _fed_on_library_page_request,
        _fed_on_library_page_result,
        _fed_local_library_bookmarks_snapshot,
        _fed_on_library_bookmarks,
        _fed_on_file_host_request,
        _fed_on_file_host_result,
        _fed_local_file_public,
    )
    _fed_hub.start()
    _fed_hub.on_library_bookmark_clear = _fed_on_library_bookmark_clear
    _fed_hub.on_file_leave_clear = _fed_on_file_leave_clear
    _fed_hub.on_offline_pm = _fed_on_offline_pm
    _fed_hub.on_canvas_sync = _fed_on_canvas_sync
    _fed_hub.on_offline_pm_clear = _fed_on_offline_pm_clear
    _fed_hub.on_ratings = _fed_on_ratings
    _fed_hub.get_local_ratings = rating_store.export_entries
    _federation_sync_library_catalog()
    # Push bookmarks for currently connected users once hub is up.
    for row in _fed_local_library_bookmarks_snapshot():
        _federation_sync_library_bookmarks(row["name"], row.get("books") or {})
    try:
        _federation_push_all_offline_clears()
    except Exception as e:
        print(f"federation: initial offline clear sync error: {e!r}")
    try:
        _federation_push_all_offline_leaves()
    except Exception as e:
        print(f"federation: initial offline leave sync error: {e!r}")
    try:
        _federation_sync_ratings(rating_store.export_entries())
    except Exception as e:
        print(f"federation: initial ratings sync error: {e!r}")
    try:
        # Peers may already be up; otherwise peer-up handler reconciles again.
        _federation_reconcile_restored_games()
    except Exception as e:
        print(f"federation: initial game reconcile error: {e!r}")
    try:
        _federation_broadcast_ended_tombstones()
    except Exception as e:
        print(f"federation: initial ended-tombstone gend error: {e!r}")

    # Start library directory watch thread
    if _library_watch_thread is None:
        _library_watch_stop.clear()
        _library_watch_thread = threading.Thread(
            target=_library_watch_loop,
            name="library-watch",
            daemon=True
        )
        _library_watch_thread.start()


def broadcast_room(
    room: str,
    msg: bytes,
    exclude_conn=None,
    *,
    skip_federation: bool = False,
    via_federation_from: Optional[str] = None,
) -> None:
    with lock:
        targets = [
            c
            for c in list(rooms.get(room, ()))
            if c is not exclude_conn and c in clients
        ]
    dead = []
    for c in targets:
        try:
            c.send(msg)
        except Exception as e:
            print(f"broadcast send error: {e!r}")
            dead.append(c)
    for c in dead:
        remove_client(c)
    if not skip_federation:
        hub = federation.get_hub()
        if hub is not None and hub.enabled:
            hub.broadcast_room(room, msg, exclude_node=via_federation_from)


def remove_client(conn) -> None:
    flush_now = False
    with lock:
        info = clients.pop(conn, None)
        if not info:
            try:
                conn.close()
            except Exception:
                pass
            return
        name = info["name"]
        joined_rooms = list(info["rooms"])
        current_room = info.get("current_room", DEFAULT_ROOM)
        library_reading.pop(conn, None)
        _remember_session_locked(name, joined_rooms, current_room)
        game_notices: list[tuple[str, list[str]]] = []
        for room in joined_rooms:
            same_name_peer = _same_name_peer_in_room_locked(room, name, exclude_conn=conn)
            game = room_games.get(room)
            preserve_disconnected_seat = (
                not _shutting_down
                and game is not None
                and getattr(game, "state", "ended") != "ended"
                and _is_conn_seated_in_game(game, conn)
            )
            rooms[room].discard(conn)
            if room_owners.get(room) is conn:
                if same_name_peer is not None:
                    room_owners[room] = same_name_peer
                elif not preserve_disconnected_seat and not _shutting_down:
                    _reassign_room_owner_locked(room, conn)
            if game is not None:
                if _shutting_down:
                    if _is_conn_seated_in_game(game, conn):
                        seat = DisconnectedSeat(name)
                        _replace_conn_refs(game, conn, seat)
                        flush_now = True
                elif _is_conn_seated_in_game(game, conn):
                    if same_name_peer is not None:
                        resumed = _resume_same_account_seat_locked(
                            room,
                            game,
                            same_name_peer,
                            name,
                            old_conn_hint=conn,
                        )
                        if resumed:
                            game_notices.append(
                                (
                                    room,
                                    [f"{name} 在另一终端续玩，当前对局席位已自动迁移。"],
                                )
                            )
                        elif _game_seat_conn_by_name(game, name) is conn:
                            seat = DisconnectedSeat(name)
                            _replace_conn_refs(game, conn, seat)
                            if room_owners.get(room) is conn:
                                room_owners.pop(room, None)
                            flush_now = True
                            game_notices.append(
                                (
                                    room,
                                    [
                                        f"{name} 暂时离线，席位已保留；"
                                        "重新连接后可继续本局。"
                                    ],
                                )
                            )
                    else:
                        seat = DisconnectedSeat(name)
                        _replace_conn_refs(game, conn, seat)
                        if room_owners.get(room) is conn:
                            room_owners.pop(room, None)
                        flush_now = True
                        game_notices.append(
                            (
                                room,
                                [
                                    f"{name} 暂时离线，席位已保留；"
                                    "重新连接后可继续本局。"
                                ],
                            )
                        )
                elif same_name_peer is not None:
                    resumed = _resume_same_account_seat_locked(
                        room,
                        game,
                        same_name_peer,
                        name,
                        old_conn_hint=conn,
                    )
                    if resumed:
                        game_notices.append(
                            (
                                room,
                                [f"{name} 在另一终端续玩，当前对局席位已自动迁移。"],
                            )
                        )
            _drop_game_if_room_empty_locked(room)
    hub = federation.get_hub()
    for room in joined_rooms:
        same_local = _same_name_peer_in_room_locked(room, name, exclude_conn=conn)
        remote_same = (
            hub is not None
            and hub.enabled
            and hub.same_name_in_room(room, name, bool(same_local))
        )
        if same_local or remote_same:
            continue
        leave_msg = f"[!] {name} left #{room}\n".encode("utf-8")
        # Local delivery only: federation uses notify_leave (presence), not msg,
        # otherwise peers see the leave line twice.
        broadcast_room(room, leave_msg, skip_federation=True)
        if hub is not None and hub.enabled:
            hub.notify_leave(name, room)
    for room, lines in game_notices:
        broadcast_game(room, lines)
    try:
        conn.close()
    except Exception:
        pass
    if flush_now:
        _safe_persist_sessions_now()
    else:
        _mark_sessions_dirty()


def handle_command(conn, payload: str) -> None:
    with lock:
        info = clients.get(conn)
        if not info:
            return
        name = info["name"]
        current_room = info["current_room"]

    parts = payload.split(None, 1)
    cmd = parts[0].lower() if parts else ""

    if cmd == "/join":
        if len(parts) < 2 or not parts[1].strip():
            send_line(conn, "[*] Usage: /join <room>\n")
            return
        new_room = normalize_room(parts[1])
        if not new_room:
            send_line(
                conn,
                "[*] Invalid room name (1–32 chars: letters, digits, _ -)\n",
            )
            return

        newly_joined = False
        with lock:
            if conn not in clients:
                return
            joined = clients[conn]["rooms"]
            prev_room = clients[conn]["current_room"]
            if new_room not in joined:
                was_empty = len(rooms[new_room]) == 0
                joined.add(new_room)
                rooms[new_room].add(conn)
                if was_empty:
                    room_owners[new_room] = conn
                newly_joined = True
            clients[conn]["current_room"] = new_room
            _sync_live_session_locked(conn)

        if newly_joined:
            broadcast_room(
                new_room,
                f"[+] {name} joined #{new_room}\n".encode("utf-8"),
                exclude_conn=conn,
                skip_federation=True,
            )
            hub = federation.get_hub()
            if hub is not None and hub.enabled:
                hub.notify_join(name, new_room)
            send_line(
                conn,
                f"[*] Joined #{new_room} and switched from #{prev_room} to #{new_room}\n",
            )
            send_room_announcement_preview(conn, new_room)
            with lock:
                active_game = room_games.get(new_room)
                if active_game is not None:
                    game_label = getattr(active_game, "name", "游戏")
                    seats_info = active_game.seats() if hasattr(active_game, "seats") else []
                    send_line(conn, f"[*] 本房正在进行一局 {game_label}，用 /game show 查看。\n")
                    if seats_info:
                        send_line(conn, "\n".join(seats_info) + "\n")
        elif new_room == current_room:
            send_line(conn, f"[*] Already active in #{new_room}\n")
        else:
            send_line(conn, f"[*] Switched from #{current_room} to #{new_room}\n")
            send_room_announcement_preview(conn, new_room)
        return

    if cmd == "/switch":
        if len(parts) < 2 or not parts[1].strip():
            send_line(conn, "[*] Usage: /switch <room>\n")
            return
        target_room = normalize_room(parts[1])
        if not target_room:
            send_line(
                conn,
                "[*] Invalid room name (1–32 chars: letters, digits, _ -)\n",
            )
            return
        with lock:
            if conn not in clients:
                return
            joined = clients[conn]["rooms"]
            active = clients[conn]["current_room"]
            if target_room not in joined:
                send_line(conn, f"[*] You are not in #{target_room}. Use /join first.\n")
                return
            if target_room == active:
                send_line(conn, f"[*] Already active in #{target_room}\n")
                return
            clients[conn]["current_room"] = target_room
            _sync_live_session_locked(conn)
        hub = federation.get_hub()
        if hub is not None and hub.enabled:
            hub.notify_switch(name, target_room)
        send_line(conn, f"[*] Switched from #{active} to #{target_room}\n")
        send_room_announcement_preview(conn, target_room)
        return

    if cmd == "/msg":
        parts3 = payload.split(None, 2)
        if len(parts3) < 3 or not parts3[1].strip() or not parts3[2].strip():
            send_line(
                conn,
                "[*] Usage: /msg #<room> <text>  |  /msg <nick> <text>\n"
                "[*] (Room only if target starts with #; otherwise nick — same as irssi.)\n",
            )
            return
        target = parts3[1].strip()
        text = parts3[2].strip()
        if target.startswith("#"):
            target_room = normalize_room(target[1:])
            if not target_room:
                send_line(
                    conn,
                    "[*] Invalid room name (1–32 chars: letters, digits, _ -)\n",
                )
                return
            with lock:
                if conn not in clients:
                    return
                joined = clients[conn]["rooms"]
                if target_room not in joined:
                    send_line(
                        conn,
                        f"[*] You are not in #{target_room}. Use /join first.\n",
                    )
                    return
            line_out = f"[#{target_room}] [{name}] {text}\n".encode("utf-8")
            broadcast_room(target_room, line_out)
            return
        send_private_messages(conn, name, target, text)
        return

    if cmd in ("/leave", "/留言", "/unmsg"):
        handle_leave_command(conn, name, parts)
        return

    if cmd == "/part":
        if len(parts) < 2 or not parts[1].strip():
            send_line(conn, "[*] Usage: /part <room>\n")
            return
        target_room = normalize_room(parts[1])
        if not target_room:
            send_line(
                conn,
                "[*] Invalid room name (1–32 chars: letters, digits, _ -)\n",
            )
            return
        switched_to = None
        with lock:
            if conn not in clients:
                return
            joined = clients[conn]["rooms"]
            active = clients[conn]["current_room"]
            if target_room not in joined:
                send_line(conn, f"[*] You are not in #{target_room}\n")
                return
            if len(joined) == 1:
                send_line(conn, "[*] Cannot leave your last room\n")
                return
            joined.remove(target_room)
            rooms[target_room].discard(conn)
            same_name_peer = _same_name_peer_in_room_locked(
                target_room,
                name,
                exclude_conn=conn,
            )
            if room_owners.get(target_room) is conn and same_name_peer is not None:
                room_owners[target_room] = same_name_peer
            else:
                _reassign_room_owner_locked(target_room, conn)
            game_bcast: list[str] = []
            game = room_games.get(target_room)
            if game is not None:
                resumed = _resume_same_account_seat_locked(
                    target_room,
                    game,
                    same_name_peer,
                    name,
                    old_conn_hint=conn,
                ) if same_name_peer is not None else False
                if resumed:
                    game_bcast = [f"{name} 在另一终端续玩，席位已自动迁移。"]
                else:
                    _, game_bcast, _ended = game.on_player_leave(conn, name)
            _drop_game_if_room_empty_locked(target_room)
            if active == target_room:
                switched_to = sorted(joined)[0]
                clients[conn]["current_room"] = switched_to
            _sync_live_session_locked(conn)
        if game_bcast:
            broadcast_game(target_room, game_bcast)
        hub = federation.get_hub()
        remote_same = (
            hub is not None
            and hub.enabled
            and hub.same_name_in_room(target_room, name, bool(same_name_peer))
        )
        if not same_name_peer and not remote_same:
            broadcast_room(
                target_room,
                f"[!] {name} left #{target_room}\n".encode("utf-8"),
                skip_federation=True,
            )
            if hub is not None and hub.enabled:
                hub.notify_leave(name, target_room)
        if switched_to:
            send_line(conn, f"[*] Left #{target_room}, switched to #{switched_to}\n")
        else:
            send_line(conn, f"[*] Left #{target_room}\n")
        return

    if cmd == "/rooms":
        with lock:
            if conn not in clients:
                return
            active = clients[conn]["current_room"]
            joined = sorted(clients[conn]["rooms"])
        labels = [f"*#{r}" if r == active else f"#{r}" for r in joined]
        send_line(conn, f"[*] Rooms: {', '.join(labels)}\n")
        return

    if cmd in ("/names", "/users"):
        with lock:
            r = clients[conn]["current_room"]
            members = sorted(
                clients[c]["name"] for c in rooms.get(r, ()) if c in clients
            )
        hub = federation.get_hub()
        if hub is not None and hub.enabled:
            remote = hub.names_in_room(r)
            members = sorted(set(members) | set(remote))
        send_line(
            conn,
            f"[*] #{r} ({len(members)}): {', '.join(members) if members else '(empty)'}\n",
        )
        return

    if cmd in ("/clear", "/cls"):
        send_line(conn, _CLEAR_SCREEN)
        send_line(conn, _SCREEN_CLEARED_ACK)
        return

    if cmd == "/help":
        for hline in i18n.help_lines(conn_locale(conn)):
            for part in library.wrap_output_lines(hline):
                send_line(conn, part)
        return

    if cmd in ("/lang", "/language", "/locale"):
        rest = payload.split(None, 1)
        if len(rest) < 2 or not rest[1].strip():
            send_line(conn, _ts(conn, "lang_current", lang=conn_locale(conn)))
            send_line(conn, _ts(conn, "lang_usage"))
            return
        arg = rest[1].strip().split()[0]
        if arg.lower() not in (
            "en",
            "zh",
            "cn",
            "english",
            "chinese",
            "中文",
            "英文",
        ):
            send_line(conn, _ts(conn, "lang_usage"))
            return
        loc = set_conn_locale(conn, arg)
        send_line(conn, _ts(conn, "lang_set", lang=loc))
        return

    if cmd == "/announce":
        tail = payload[len("/announce") :].strip()
        with lock:
            if conn not in clients:
                return
            room = clients[conn]["current_room"]
            owner_conn = room_owners.get(room)
            same_owner_account = (
                owner_conn in clients
                and clients[owner_conn]["name"].strip().lower() == name.strip().lower()
            )
            is_owner = owner_conn is conn or same_owner_account
            if same_owner_account and owner_conn is not conn:
                room_owners[room] = conn
        if not tail:
            with lock:
                cur = (room_announcements.get(room) or "").strip()
            if cur:
                send_line(conn, _ts(conn, "announce_current", room=room, text=cur))
            else:
                send_line(conn, _ts(conn, "announce_none", room=room))
            return
        if not is_owner:
            send_line(conn, _ts(conn, "announce_owner_only"))
            return
        if tail.lower() == "clear":
            with lock:
                room_announcements.pop(room, None)
            broadcast_room(
                room,
                _ts(conn, "announce_cleared_bcast", room=room).encode("utf-8"),
            )
            send_line(conn, _ts(conn, "announce_cleared", room=room))
            return
        one_line = " ".join(tail.split())
        if len(one_line) > MAX_ANNOUNCE_LEN:
            send_line(
                conn,
                _ts(conn, "announce_too_long", max_len=MAX_ANNOUNCE_LEN),
            )
            return
        with lock:
            room_announcements[room] = one_line
        broadcast_room(
            room,
            _ts(conn, "announce_set_bcast", room=room, text=one_line).encode("utf-8"),
        )
        send_line(conn, _ts(conn, "announce_updated", room=room))
        return

    if cmd == "/game":
        try:
            _handle_game(conn, name, current_room, payload)
        except Exception as e:
            print(f"/game error: room={current_room} user={name} payload={payload!r} err={e!r}")
            traceback.print_exc()
            send_line(conn, _ts(conn, "game_cmd_fail"))
        return

    if cmd == "/news":
        try:
            _handle_news(conn, payload)
        except Exception as e:
            print(f"/news error: {e!r}")
            traceback.print_exc()
            send_line(conn, _ts(conn, "news_cmd_fail"))
        return

    if cmd in {"/library", "/lib"}:
        try:
            _handle_library(conn, payload)
        except Exception as e:
            print(f"/library error: {e!r}")
            traceback.print_exc()
            send_line(conn, _ts(conn, "library_cmd_fail"))
        return

    if cmd == "/dict":
        _handle_dict(conn, payload)
        return

    if cmd == "/sendfile" or cmd == "/file":
        _handle_sendfile(conn, name, payload)
        return

    if cmd == "/canvas" or cmd == "/board":
        _handle_canvas(conn, name, payload)
        return

    if cmd == "/piano":
        _handle_piano(conn, name, payload)
        return

    send_line(conn, "[*] Unknown command. Try /help\n")


def _handle_game(conn, name: str, room: str, payload: str) -> None:
    """All /game subcommands. Mutates room_games under the global lock."""
    raw = payload[len("/game") :].strip()
    if not raw or raw.lower() == "help":
        send_line(conn, _ts(conn, "game_usage_header"))
        for ln in i18n.game_help_lines(conn_locale(conn)):
            for part in library.wrap_output_lines(ln + "\n"):
                send_line(conn, part)
        with lock:
            enabled = _enabled_games_for_room_locked(room)
        send_line(
            conn,
            _ts(
                conn,
                "game_room_playable",
                games=", ".join(games.list_game_names(enabled)),
            ),
        )
        return

    sub, _, rest = raw.partition(" ")
    sub = sub.lower()
    rest = rest.strip()

    if _should_forward_game(room, sub):
        hub = federation.get_hub()
        auth = room_game_authority.get(room, "")
        with lock:
            progress_before = _game_progress_score(room_games.get(room))
        if hub and auth and hub.forward_game_cmd(auth, room, hub.node_id, name, sub, rest):
            # Authority applies then gsyncs; wait so replica board catches up.
            if sub == "move":
                _federation_wait_game_progress(
                    room, progress_before + 1, timeout=1.5
                )
            else:
                _federation_refresh_replica_and_wait(room, timeout=1.5)
            return
        # Peer unreachable: if seats are all local, reclaim and handle here.
        if _reclaim_game_authority_for_local_seats(room):
            pass
        else:
            send_line(conn, _ts(conn, "game_forward_fail"))
            return

    if sub == "list":
        with lock:
            enabled = _enabled_games_for_room_locked(room)
            names = games.list_game_names(enabled)
        if names:
            line = _ts(conn, "game_list_line", games=", ".join(names))
        else:
            line = _ts(conn, "game_list_empty")
        send_line(conn, line)
        return

    if sub in ("rating", "ratings", "score", "scores"):
        parts = rest.split()
        game_name: Optional[str] = None
        target_name = name
        if parts:
            maybe_game = games.resolve_game_name(parts[0].lower())
            if is_rated_game(maybe_game):
                game_name = maybe_game
                if len(parts) >= 2:
                    target_name = parts[1]
            else:
                target_name = parts[0]
                if len(parts) >= 2:
                    maybe_game = games.resolve_game_name(parts[1].lower())
                    if is_rated_game(maybe_game):
                        game_name = maybe_game
        if game_name is not None and not is_rated_game(game_name):
            send_line(conn, _ts(conn, "rating_no_persist", game=game_name))
            return
        send_game_private(
            conn,
            room,
            _rating_summary_lines(target_name, game_name, locale=conn_locale(conn)),
        )
        return

    if sub in ("on", "off", "上线", "下线"):
        enable = sub in ("on", "上线")
        if not rest:
            send_line(conn, f"[*] 用法：/game {'on' if enable else 'off'} <名称>\n")
            return
        game_name = games.resolve_game_name(rest.split()[0].lower())
        if game_name not in games.GAMES:
            send_line(
                conn,
                f"[*] 未知游戏 {game_name!r}；可用："
                + ", ".join(games.all_game_names())
                + "\n",
            )
            return
        with lock:
            owner_conn = room_owners.get(room)
            same_owner_account = (
                owner_conn in clients
                and clients[owner_conn]["name"].strip().lower() == name.strip().lower()
            )
            is_owner = owner_conn is conn or same_owner_account
            if not is_owner:
                send_line(conn, "[*] 只有房主可以上下线游戏。\n")
                return
            if same_owner_account and owner_conn is not conn:
                room_owners[room] = conn
            enabled = _enabled_games_for_room_locked(room)
            if enable:
                if game_name in enabled:
                    send_line(conn, f"[*] {game_name} 已在本房上线。\n")
                else:
                    enabled.add(game_name)
                    send_line(conn, f"[*] 已上线 {game_name}，/game list 可见。\n")
                _mark_sessions_dirty()
                return
            if game_name not in enabled:
                send_line(conn, f"[*] {game_name} 已在本房下线。\n")
                return
            game = room_games.get(room)
            if (
                game is not None
                and getattr(game, "name", "") == game_name
                and getattr(game, "state", "ended") != "ended"
            ):
                send_line(
                    conn,
                    f"[*] 本房仍有进行中的 {game_name} 对局；"
                    "请先 /game end 或等对局结束再下线。\n",
                )
                return
            enabled.discard(game_name)
        send_line(conn, f"[*] 已下线 {game_name}，/game list 不再显示。\n")
        _mark_sessions_dirty()
        return

    if sub == "new":
        parts = rest.strip().split()
        game_arg = parts[0] if parts else "chess"
        game_name = games.resolve_game_name(game_arg.lower())
        cls = games.GAMES.get(game_name)
        if cls is None:
            send_line(
                conn,
                f"[*] 未知游戏 {game_name!r}；/game list 查看可用。\n",
            )
            return
        with lock:
            enabled = _enabled_games_for_room_locked(room)
        if game_name not in enabled:
            send_line(
                conn,
                f"[*] 游戏 {game_name} 在本房未上线；/game list 查看可玩。\n",
            )
            return
        with lock:
            existing = room_games.get(room)
            auto_join_existing = (
                existing is not None
                and existing.state != "ended"
                and getattr(existing, "name", "") == game_name
                and getattr(existing, "state", "") in {"waiting", "setup"}
                and len(parts) == 1
            )
        if auto_join_existing:
            # A game card is an entry point: join an open same-game seat when
            # the room already has a waiting/setup game.
            _handle_game(conn, name, room, "/game join")
            return
        with lock:
            existing = room_games.get(room)
            if existing is not None and existing.state != "ended":
                send_line(
                    conn,
                    f"[*] 本房已有进行中的对局（{existing.name}/"
                    f"{existing.state}）；/game end 由房主结束或先等当前局结束。\n",
                )
                return
            try:
                new_game = games.create_game(
                    game_name,
                    conn,
                    name,
                    options=parts[1:],
                    rating_store=rating_store,
                )
            except RuntimeError as e:
                send_line(conn, f"[*] 无法开局：{e}\n")
                return
            room_games[room] = new_game
            hub = federation.get_hub()
            if hub is not None:
                room_game_authority[room] = hub.node_id
                room_game_tokens[room] = secrets.token_hex(16)
                room_game_provisional.discard(room)
            else:
                room_game_authority.pop(room, None)
                room_game_tokens.pop(room, None)
        seat = getattr(new_game, "first_seat_desc", "第一席")
        join_hint = getattr(
            new_game,
            "join_blurb",
            "等另一位玩家用 /game join 加入。",
        )
        broadcast_game(
            room,
            [f"{name} 开了一局 {game_name}（{seat}），{join_hint}"],
        )
        terminal_hint = games.terminal_hint(game_name)
        if terminal_hint:
            broadcast_game(room, [terminal_hint])
        send_oriented_boards(room, new_game)
        _persist_after_game_change()
        _federation_sync_game(room)
        return

    if sub == "join":
        with lock:
            game = room_games.get(room)
        if game is None:
            _federation_request_game_and_wait(room)
            with lock:
                game = room_games.get(room)
        if game is None:
            send_line(conn, "[*] 本房没有进行中的对局；用 /game new chess 开局。\n")
            return
        with lock:
            resumed = _resume_same_account_seat_locked(room, game, conn, name)
            if resumed:
                priv = ["检测到你在其他终端已有席位，已自动续玩接管。"]
                bcast = [f"{name} 从另一终端接管了本局操作。"]
            else:
                priv, bcast, _ = game.try_join(conn, name)
        _finish_game_action(room, game, conn, priv, bcast, False)
        return

    if sub == "seats":
        with lock:
            game = room_games.get(room)
        if game is None:
            _federation_request_game_and_wait(room)
            with lock:
                game = room_games.get(room)
        with lock:
            lines = game.seats() if game else ["本房没有进行中的对局。"]
        send_game_private(conn, room, lines)
        return

    if sub == "show":
        with lock:
            game = room_games.get(room)
            auth = (room_game_authority.get(room) or "").strip()
        hub = federation.get_hub()
        if game is None:
            _federation_request_game_and_wait(room)
        elif (
            hub is not None
            and hub.enabled
            and (not auth or auth != hub.node_id)
        ):
            # Replica or unknown authority after restart: pull before showing.
            _federation_refresh_replica_and_wait(room, timeout=2.0)
        with lock:
            game = room_games.get(room)
        with lock:
            if game is None:
                lines = ["本房没有进行中的对局。"]
            else:
                resumed = _resume_same_account_seat_locked(room, game, conn, name)
                rest_show = rest.strip().lower()
                full_help = rest_show in ("help", "帮助", "?", "h")
                if getattr(game, "name", "") == "sanguo":
                    lines = game.show(conn, full=full_help)
                else:
                    lines = _game_show_for_conn(game, conn)
                if resumed:
                    lines = ["已检测到同账号旧终端席位，已自动续玩接管。"] + lines
        send_game_private(conn, room, lines)
        return

    if sub == "move":
        try:
            with lock:
                game = room_games.get(room)
            if game is None:
                _federation_request_game_and_wait(room)
                with lock:
                    game = room_games.get(room)
            with lock:
                if game is None:
                    send_line(conn, "[*] 本房没有进行中的对局。\n")
                    return
                resumed = _resume_same_account_seat_locked(room, game, conn, name)
                _ensure_game_runtime_compat(game)
                priv, bcast, ended = game.try_move(conn, rest)
        except Exception as e:
            print(f"/game move failed: room={room} user={name} cmd={rest!r} err={e!r}")
            send_line(conn, f"[*] /game move 执行失败：{e}\n")
            return
        if not ended:
            extra = _nudge_game_bots_locked(game)
            if extra:
                bcast = list(bcast) + extra
                ended = getattr(game, "state", "ended") == "ended"
        if resumed:
            priv = ["你已从其他终端续玩接管，以下是本次操作结果："] + priv
        _finish_game_action(room, game, conn, priv, bcast, ended)
        return

    if sub == "resign":
        with lock:
            game = room_games.get(room)
            if game is None:
                send_line(conn, "[*] 本房没有进行中的对局。\n")
                return
            resumed = _resume_same_account_seat_locked(room, game, conn, name)
            priv, bcast, ended = game.resign(conn, name)
        if not ended:
            extra = _nudge_game_bots_locked(game)
            if extra:
                bcast = list(bcast) + extra
                ended = getattr(game, "state", "ended") == "ended"
        if resumed:
            priv = ["你已从其他终端续玩接管，以下是本次操作结果："] + priv
        _finish_game_action(room, game, conn, priv, bcast, ended)
        return

    if sub == "undo":
        with lock:
            game = room_games.get(room)
            if game is None:
                send_line(conn, "[*] 本房没有进行中的对局。\n")
                return
            if not hasattr(game, "request_undo"):
                send_line(
                    conn,
                    "[*] 当前对局不支持悔棋（仅 chess、gomoku、xiangqi）。\n",
                )
                return
            undo_action, undo_err = games.parse_undo_action(rest)
            if undo_err:
                send_line(conn, f"[*] {undo_err}\n")
                return
            if undo_action == "accept":
                priv, bcast, _ = game.accept_undo(conn)
            elif undo_action == "reject":
                priv, bcast, _ = game.reject_undo(conn)
            elif undo_action == "cancel":
                priv, bcast, _ = game.cancel_undo(conn)
            else:
                priv, bcast, _ = game.request_undo(conn)
        _finish_game_action(room, game, conn, priv, bcast, False, send_boards=bool(bcast))
        return

    if sub == "abort":
        with lock:
            game = room_games.get(room)
            if game is None:
                send_line(conn, "[*] 本房没有进行中的对局。\n")
                return
            resumed = _resume_same_account_seat_locked(room, game, conn, name)
            priv, bcast, _ = game.abort(conn, name)
        if resumed:
            priv = ["你已从其他终端续玩接管，以下是本次操作结果："] + priv
        _finish_game_action(room, game, conn, priv, bcast, False, send_boards=False)
        return

    if sub == "pgn":
        with lock:
            game = room_games.get(room)
            if game is None or not hasattr(game, "pgn_export"):
                lines = ["本房没有可导出 PGN 的对局（仅 chess 支持）。"]
            else:
                lines = game.pgn_export()
        send_game_private(conn, room, lines)
        return

    if sub == "end":
        with lock:
            game = room_games.get(room)
            owner_conn = room_owners.get(room)
            same_owner_account = (
                owner_conn in clients
                and clients[owner_conn]["name"].strip().lower() == name.strip().lower()
            )
            is_owner = owner_conn is conn or same_owner_account
            if game is None:
                send_line(conn, "[*] 本房没有进行中的对局。\n")
                return
            if not is_owner:
                send_line(conn, "[*] 只有房主可以 /game end。\n")
                return
            if same_owner_account and owner_conn is not conn:
                room_owners[room] = conn
            room_games.pop(room, None)
        broadcast_game(room, [f"{name}（房主）结束了本房的对局。"])
        _federation_notify_game_end(room)
        _persist_after_game_change()
        return

    if sub in {"restore", "恢复"}:
        with lock:
            restored = _restore_idle_parked_games_locked()
            busy = [
                r
                for r, g in room_games_parked.items()
                if g is not None and getattr(g, "state", "ended") != "ended"
            ]
        if not restored:
            if busy:
                send_line(
                    conn,
                    "[*] 暂存对局所在房间仍有进行中的棋局，未覆盖。"
                    "可先 /game end 再 /game restore。\n",
                )
            else:
                send_line(conn, "[*] 没有可恢复的暂存对局。\n")
            return
        for room_name, game in restored:
            broadcast_game(
                room_name,
                [
                    f"{name} 恢复了暂存对局（{getattr(game, 'name', '?')}）。"
                    "请用 /game show 查看。"
                ],
            )
            send_oriented_boards(room_name, game)
            send_sanguo_hand_views(room_name, game)
            try:
                _federation_push_game_snapshot(room_name)
            except Exception as e:
                print(f"federation: restore push failed room={room_name!r}: {e!r}")
        _persist_after_game_change()
        msg = "[*] 已恢复暂存对局：" + "、".join(f"#{r}" for r, _ in restored) + "。"
        if busy:
            msg += (
                " 这些房间仍有进行中的对局，暂存未动："
                + "、".join(f"#{r}" for r in busy)
                + "。"
            )
        send_line(conn, msg + "\n")
        return

    send_line(conn, f"[*] 未知子命令 /game {sub}；用 /game help 查看。\n")


def process_client_line(conn, raw_line: bytes) -> None:
    text = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
    if not text:
        return
    if text.startswith("[") and "] " in text:
        _, _, payload = text.partition("] ")
    else:
        payload = text
    if not payload:
        return

    with lock:
        info = clients.get(conn)
        if not info:
            return
        room = info["current_room"]
        name = info["name"]

    if payload.startswith("/file "):
        send_line(conn, "[*] File transfer is not supported.\n")
        return

    if payload.startswith("/"):
        handle_command(conn, payload)
        return

    line_out = f"[#{room}] [{name}] {payload}\n".encode("utf-8")
    broadcast_room(room, line_out)


def handle_client(conn, addr) -> None:
    buffer = b""
    try:
        while b"\n" not in buffer:
            try:
                chunk = conn.recv(1024)
            except OSError as e:
                if getattr(e, "errno", None) in _DISCONNECT_ERRNOS:
                    return
                raise
            if not chunk:
                return
            buffer += chunk
            if len(buffer) > 65536:
                return
        first, buffer = buffer.split(b"\n", 1)
        name = _parse_handshake_line(first.decode("utf-8", errors="replace"))

        restored_from_session = False
        resumed_game_rooms: list[str] = []
        active_game_lines: list[str] = []
        hub = federation.get_hub()
        with lock:
            same_name_peers = [
                c
                for c, info in clients.items()
                if info["name"].strip().lower() == name.strip().lower()
            ]
            previous_session = _load_recent_session_locked(name)
            inherited_rooms: set[str] = set()
            active_room = DEFAULT_ROOM
            if same_name_peers:
                for peer in same_name_peers:
                    peer_info = clients.get(peer)
                    if not peer_info:
                        continue
                    inherited_rooms.update(peer_info["rooms"])
                    if active_room == DEFAULT_ROOM:
                        active_room = peer_info["current_room"]
            elif previous_session is not None:
                inherited_rooms.update(previous_session.get("rooms") or set())
                previous_active = previous_session.get("current_room")
                if isinstance(previous_active, str) and previous_active:
                    active_room = previous_active
                restored_from_session = True
            if hub is not None and hub.enabled:
                inherited_rooms.update(hub.rooms_for_name(name))
                fed_active = hub.active_room_for_name(name)
                if fed_active and active_room == DEFAULT_ROOM:
                    active_room = fed_active
            inherited_rooms.add(DEFAULT_ROOM)
            if active_room not in inherited_rooms:
                inherited_rooms.add(active_room)

            clients[conn] = {
                "name": name,
                "rooms": set(inherited_rooms),
                "current_room": active_room,
                "locale": locale_store.get(name),
            }
            for room in inherited_rooms:
                was_empty = len(rooms[room]) == 0
                rooms[room].add(conn)
                if was_empty:
                    room_owners[room] = conn
            for room in inherited_rooms:
                game = room_games.get(room)
                if _resume_same_account_seat_locked(room, game, conn, name):
                    resumed_game_rooms.append(room)
            active_game = room_games.get(active_room)
            if active_game is not None and getattr(active_game, "state", "ended") != "ended":
                active_game_lines = _game_show_for_conn(active_game, conn)
                if active_room in resumed_game_rooms:
                    active_game_lines = [
                        _ts(conn, "game_resume_takeover")
                    ] + active_game_lines
            room_labels = [
                f"*#{r}" if r == active_room else f"#{r}"
                for r in sorted(inherited_rooms)
            ]

        print(f"{name} joined #{active_room} (tcp_peer={addr[0]!r}:{addr[1]})")

        join_msg = f"[+] {name} joined #{active_room}\n".encode("utf-8")
        broadcast_room(active_room, join_msg, exclude_conn=conn, skip_federation=True)
        if hub is not None and hub.enabled:
            for room in inherited_rooms:
                hub.notify_join(name, room)
            _federation_sync_library_bookmarks(name)
        send_line(
            conn,
            f"[*] Active room #{active_room}. "
            f"/names /rooms /join /switch /msg /sendfile /canvas /piano /leave /part /announce /game /news /dict /clear /lang /help\n",
        )
        send_line(conn, f"[*] Rooms: {', '.join(room_labels)}\n")
        if hub is not None and hub.enabled and hub.peer_count > 0:
            send_line(
                conn,
                _ts(conn, "fed_connected", n=hub.peer_count),
            )
        if same_name_peers:
            send_line(conn, _ts(conn, "multi_terminal"))
        elif restored_from_session:
            send_line(conn, _ts(conn, "session_restored"))
        if resumed_game_rooms:
            send_line(conn, _ts(conn, "game_seat_resumed"))
        # Deliver leave-messages only on the first local session for this nick,
        # so multi-device reconnect does not drain the mailbox twice.
        if not same_name_peers:
            deliver_offline_messages(conn, name)
        _mark_sessions_dirty()
        send_room_announcement_preview(conn, active_room)
        if active_game_lines:
            send_game_private(conn, active_room, active_game_lines)
            if resumed_game_rooms:
                with lock:
                    g = room_games.get(active_room)
                extra = _nudge_game_bots_locked(g) if g is not None else []
                if extra:
                    broadcast_game(active_room, extra)
                    _persist_after_game_change()
                    try:
                        _federation_sync_game(active_room)
                    except Exception as e:
                        print(f"federation: reconnect bot sync failed: {e!r}")

        while True:
            if not buffer:
                try:
                    chunk = conn.recv(4096)
                except OSError as e:
                    if getattr(e, "errno", None) in _DISCONNECT_ERRNOS:
                        break
                    raise
                if not chunk:
                    break
                buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if line:
                    process_client_line(conn, line)
            if len(buffer) > 65536:
                send_line(conn, "[*] 消息过长。\n")
                buffer = b""

    except Exception as e:
        print("connection error:", e)
        traceback.print_exc()
    finally:
        remove_client(conn)


def run_server() -> int:
    global _listen_socket, _shutdown_requested, _shutting_down, file_http
    _load_persisted_sessions()
    _ensure_federation_hub()
    
    # Start file HTTP server
    file_enabled = os.environ.get("SSHCHAT_FILE_TRANSFER_ENABLED", "1") != "0"
    if file_enabled:
        try:
            # Set up upload complete callback
            file_sharing.file_transfer_store.upload_complete_callback = _notify_file_ready
            
            file_http = file_http_server.create_file_server()
            file_http.start()
            print(f"[FileTransfer] HTTP server started at {file_http.get_base_url()}")
            _federation_sync_file_public()

            # Quick Tunnel hostname can change while this process stays up.
            # Re-advertise to federation (iSH / LAN peers) whenever the live
            # public_url latch moves — do not wait for peer-up or restart.
            def _file_public_watch_task():
                try:
                    last = _fed_local_file_public()
                except Exception:
                    last = ""
                while not _shutdown_requested:
                    time.sleep(30)
                    if _shutdown_requested:
                        break
                    try:
                        cur = _fed_local_file_public()
                        if cur != last:
                            last = cur
                            _federation_sync_file_public()
                            if cur:
                                print(f"[FileTransfer] federation file public -> {cur}")
                            else:
                                print("[FileTransfer] federation file public cleared (no live CF URL)")
                    except Exception as e:
                        print(f"[FileTransfer] file public watch error: {e}")

            threading.Thread(
                target=_file_public_watch_task, daemon=True, name="file-public-watch"
            ).start()
            
            # Start cleanup task for expired transfers
            def _cleanup_task():
                while not _shutdown_requested:
                    time.sleep(3600)  # Run every hour
                    if not _shutdown_requested:
                        try:
                            file_sharing.file_transfer_store.cleanup_expired()
                            canvas_sharing.canvas_store.cleanup_expired()
                            piano_sharing.piano_store.cleanup_expired()
                        except Exception as e:
                            print(f"[FileTransfer] Cleanup error: {e}")
            
            cleanup_thread = threading.Thread(target=_cleanup_task, daemon=True)
            cleanup_thread.start()
            
        except Exception as e:
            print(f"[FileTransfer] Failed to start file server: {e}")
            traceback.print_exc()
            file_http = None
    else:
        print("[FileTransfer] File transfer disabled (SSHCHAT_FILE_TRANSFER_ENABLED=0)")

    def _handle_shutdown_signal(signum, _frame) -> None:
        global _shutting_down, _shutdown_requested
        if _shutting_down:
            return
        _shutting_down = True
        print(
            f"shutdown signal {signum}, saving game sessions to {session_store.path}..."
        )
        _safe_persist_sessions_now()
        _shutdown_requested = True
        
        # Stop library watch thread
        _library_watch_stop.set()
        
        if file_http is not None:
            try:
                file_http.stop()
            except Exception:
                pass
        sock = _listen_socket
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    signal.signal(signal.SIGINT, _handle_shutdown_signal)

    def _handle_federation_reload(signum, _frame) -> None:
        hub = federation.get_hub()
        if hub is None or not hub.enabled:
            print(f"federation reload signal {signum}: hub not active")
            return
        try:
            started = hub.reload_peers()
            print(f"federation reload signal {signum}: {started} new outbound loop(s)")
        except Exception as e:
            print(f"federation reload signal {signum} failed: {e!r}")

    if hasattr(signal, "SIGHUP"):
        try:
            signal.signal(signal.SIGHUP, _handle_federation_reload)
        except (ValueError, OSError) as e:
            print(f"warning: could not install SIGHUP handler for federation reload: {e!r}")

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", PORT))
    s.listen()
    _listen_socket = s
    fed = federation.get_hub()
    fed_note = ""
    if fed is not None and fed.enabled:
        fed_note = f", federation port {fed.port}"
    print(f"chat server started on port {PORT} (default room #{DEFAULT_ROOM}){fed_note}")

    while not _shutdown_requested:
        try:
            conn, addr = s.accept()
        except OSError:
            break
        threading.Thread(
            target=handle_client,
            args=(conn, addr),
            daemon=True,
        ).start()
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="SSHChat server")
    parser.add_argument(
        "--reset-ratings-all",
        action="store_true",
        help="Reset all persisted board-game ratings and exit.",
    )
    parser.add_argument(
        "--reset-ratings-game",
        metavar="GAME",
        help="Reset one game's persisted ratings and exit.",
    )
    parser.add_argument(
        "--reset-ratings-user-game",
        nargs=2,
        metavar=("USER", "GAME"),
        help="Reset one user's persisted rating for one game and exit.",
    )
    args = parser.parse_args(argv)

    if args.reset_ratings_all:
        rating_store.reset_all()
        print(f"reset all ratings in {rating_store.path}")
        return 0
    if args.reset_ratings_game:
        game_name = games.resolve_game_name(args.reset_ratings_game.lower())
        if not is_rated_game(game_name):
            parser.error(f"{game_name!r} is not a rated board game")
        rating_store.reset_game(game_name)
        print(f"reset ratings for game {game_name} in {rating_store.path}")
        return 0
    if args.reset_ratings_user_game:
        user_name, raw_game = args.reset_ratings_user_game
        game_name = games.resolve_game_name(raw_game.lower())
        if not is_rated_game(game_name):
            parser.error(f"{game_name!r} is not a rated board game")
        removed = rating_store.reset_user_game(user_name, game_name)
        status = "removed" if removed else "not-found"
        print(
            f"reset rating for user {user_name} game {game_name}: {status} "
            f"({rating_store.path})"
        )
        return 0
    return run_server()


if __name__ == "__main__":
    raise SystemExit(main())
