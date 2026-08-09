import argparse
import base64
import os
import pickle
import re
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
from collections import defaultdict
from html import unescape
from pathlib import Path
from typing import Optional

import dict_lookup
import federation
import games
import library
import file_sharing
import file_http_server
from offline_messages import OfflineMessageStore
from ratings import GAME_CONFIGS, GameRatingStore, is_rated_game
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


rating_store = GameRatingStore(_rating_store_path())
library_bookmarks = library.LibraryBookmarkStore(_library_bookmarks_path())
session_store = GameSessionStore(_session_store_path())
offline_messages = OfflineMessageStore(_offline_messages_path())
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
# room -> node_id that owns authoritative game state (federation)
room_game_authority: dict[str, str] = {}
# room -> set of canonical game ids enabled for /game list and /game new
room_enabled_games: dict[str, set[str]] = {}
# lower nickname -> last known rooms/current room for reconnect resume
disconnected_sessions: dict[str, dict[str, object]] = {}
# conn -> {"path": str, "page": int (0-based)}
library_reading: dict[object, dict[str, object]] = {}
# resolved path -> (mtime_ns, BookDocument)
library_doc_cache: dict[str, tuple[int, library.BookDocument]] = {}
lock = threading.Lock()
_MISSING = object()  # sentinel for "attribute not present"
_persist_dirty = False
_persist_timer: Optional[threading.Timer] = None
_shutting_down = False
_shutdown_requested = False
_listen_socket: Optional[socket.socket] = None
_fed_hub: Optional[federation.FederationHub] = None
PERSIST_DEBOUNCE_SECONDS = float(
    os.environ.get("SSHCHAT_SESSION_PERSIST_SECONDS", "2")
)

ROOM_RE = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")
MAX_ANNOUNCE_LEN = 400
_DISCONNECT_ERRNOS = {32, 54, 57, 104}
SESSION_RESUME_TTL_SECONDS = int(
    os.environ.get("SSHCHAT_SESSION_RESUME_TTL_SECONDS", "86400")
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

HELP_LINES = (
    "[*] ---------- SSHChat 命令说明 ----------\n",
    "[*] 普通文字（不以 / 开头）发到「当前活跃房间」，房内在线用户都会收到。\n",
    "[*]\n",
    "[*] /join <房间>     加入房间并立刻切到该房；若已在房内则只切换当前房。\n",
    "[*]              房间名：1～32 字符，仅字母、数字、下划线、连字符。\n",
    "[*] /switch <房间>  只在已加入的房间之间切换；未加入会提示先用 /join。\n",
    "[*] /part <房间>    退出某房间；至少保留一间，不能退出最后一个。\n",
    "[*] /rooms         列出你已加入的房间；前面带 * 的是当前活跃房间。\n",
    "[*] /names 或 /users  列出当前活跃房间内的昵称（二者相同）。\n",
    "[*]\n",
    "[*] /msg #<房间> <文字>   不切换当前房，把一句话发到指定房间（# 开头表示房间）。\n",
    "[*] /msg <昵称> <文字>   私聊：对方在线则即时送达；不在线则留言，对方下次上线时收到。\n",
    "[*]              昵称大小写不敏感；同昵称多人在线会全部收到；发件人会收到汇总提示。\n",
    "[*] /leave [昵称]     查看你发出、对方尚未阅读的留言/文件（按昵称分组编号）。\n",
    "[*] /leave <昵称> <编号>  撤回发给该昵称的第 N 条未读留言或离线文件（别名：/留言、/unmsg）。\n",
    "[*]\n",
    "[*] /clear 或 /cls  清屏（终端会清空显示；图形客户端会清空当前房间记录）。\n",
    "[*] /announce      查看当前房间公告；房主可用 /announce <文字> 设置，/announce clear 清除。\n",
    "[*]              房主：#default 为第一个进服用户；其它房间为第一个 /join 该房的用户。\n",
    "[*]\n",
    "[*] /game ...      房间小游戏（chess、gomoku、xiangqi、sanguo）。/game list /new /join …；房主 /game on|off 上下线。\n",
    "[*]              详细用法用 /game help 查看。\n",
    "[*] /news [中文|国际|科技|all] [条数]  从 RSS 查看标题与提要正文；默认每类 3 条。\n",
    "[*] /news detail <分类> <序号>  更长提要（RSS 内；别名：详情）。\n",
    "[*] /news fetch <分类> <序号>  按 RSS 链接抓取网页正文（别名：全文；非 JS 站、可能截断）。\n",
    "[*] /library       列出图书馆书目（epub / txt / md / pdf；每人自带书签，翻页自动保存）。\n",
    "[*] /lib             /library 的简写。\n",
    "[*] /library open <序号|文件名>  打开图书（有书签则从书签继续）；next|prev|page 翻页。\n",
    "[*] /library find <关键词>        按书名查找书目；阅读中则在当前书中检索（别名：search / 搜索 / 查找）。\n",
    "[*] /dict en|cn|hh <词>  词典：英→中、中→英、汉语释义；/dict <词> 自动识别。\n",
    "[*]\n",
    "[*] /sendfile      发送文件到当前房间，你将收到上传网址，密钥另行单独给出。\n",
    "[*] /sendfile <昵称>    发送文件给指定用户（对方离线则留言，上线后收到；可用 /leave 查看或撤回）。\n",
    "[*] /sendfile #<房间>   发送文件到指定房间，成员各自收到不同的下载网址+密钥。\n",
    "[*]              文件名以你实际上传的文件为准，不必在指令里写。\n",
    "[*]              密钥不在网址里，打开网页后另行输入；支持图片、视频、PDF等在线预览。\n",
    "[*]              上传和下载都只能用一次，用过即作废，链接被别人截获也没用。\n",
    "[*] /help          显示本说明。\n",
)


def _parse_handshake_line(raw: str) -> str:
    """First line: nickname only (optional tab suffix from old clients is ignored)."""
    line = raw.strip()
    if not line:
        return "Unknown"
    return line.split("\t", 1)[0].strip() or "Unknown"


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
    send_line(conn, f"[#{room}] [*] 公告：{text}\n")


def _format_game_lines(room: str, lines) -> bytes:
    """Wrap each game-line with the standard [#room] [*] prefix as one byte blob."""
    return "".join(f"[#{room}] [*] {ln}\n" for ln in lines).encode("utf-8")


def send_game_private(conn, room: str, lines) -> None:
    if not lines:
        return
    send_line(conn, _format_game_lines(room, lines).decode("utf-8"))


def broadcast_game(room: str, lines) -> None:
    if not lines:
        return
    broadcast_room(room, _format_game_lines(room, lines))


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


def _rating_profile_line(game_name: str, profile: dict[str, object], rank: int | None = None) -> str:
    prefix = f"#{rank} " if rank is not None else ""
    return (
        f"{prefix}{profile['name']}: 积分={profile['rating']} 等级={profile['level']} "
        f"战绩={profile['wins']}/{profile['losses']}/{profile['draws']} "
        f"局数={profile['games']}"
    )


def _rating_summary_lines(target_name: str, game_name: Optional[str] = None) -> list[str]:
    if game_name:
        profile = rating_store.profile(game_name, target_name)
        lines = [f"{game_name} 积分（{profile['scheme']}）"]
        lines.append(_rating_profile_line(game_name, profile))
        top = rating_store.top(game_name, limit=5)
        if top:
            lines.append("榜单 Top 5：")
            lines.extend(_rating_profile_line(game_name, item, idx) for idx, item in enumerate(top, start=1))
        return lines
    lines = [f"{target_name} 的棋类积分总览（跨房间共享）"]
    for rated_game in sorted(GAME_CONFIGS):
        profile = rating_store.profile(rated_game, target_name)
        lines.append(
            f"{rated_game}: 积分={profile['rating']} 等级={profile['level']} "
            f"战绩={profile['wins']}/{profile['losses']}/{profile['draws']} "
            f"局数={profile['games']} 体系={profile['scheme']}"
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

    return value, False


def _game_seat_conn_by_name(game, nickname: str):
    """Best-effort: find seated connection by nickname in heterogeneous game classes."""
    key = _nick_key(nickname)
    if not key:
        return None

    players = getattr(game, "players", None)
    if isinstance(players, list):
        for item in players:
            if not isinstance(item, tuple) or len(item) < 2:
                continue
            conn, name = item[0], item[1]
            if isinstance(name, str) and name.strip().lower() == key:
                return conn

    for conn_attr, name_attr in (
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
    """Advance bot turns when humans are idle; caller holds lock."""
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
        raw = _pickle_game_for_storage(game)
        games_blob[room] = base64.b64encode(raw).decode("ascii")
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
        "disconnected_sessions": sessions,
        "room_enabled_games": {
            room: sorted(enabled)
            for room, enabled in room_enabled_games.items()
        },
        "room_announcements": dict(room_announcements),
    }


def _apply_session_payload_locked(payload: dict[str, object]) -> None:
    games_blob = payload.get("room_games")
    if isinstance(games_blob, dict):
        for room, encoded in games_blob.items():
            if not isinstance(room, str) or not isinstance(encoded, str):
                continue
            try:
                game = pickle.loads(base64.b64decode(encoded))
            except Exception as e:
                print(f"skip restoring room {room!r} game: {e!r}")
                continue
            _rebind_game_services(game)
            room_games[room] = game
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
    enabled = payload.get("room_enabled_games")
    if isinstance(enabled, dict):
        for room, names in enabled.items():
            if isinstance(room, str) and isinstance(names, list):
                room_enabled_games[room] = {str(n) for n in names}
    announcements = payload.get("room_announcements")
    if isinstance(announcements, dict):
        for room, text in announcements.items():
            if isinstance(room, str) and isinstance(text, str):
                room_announcements[room] = text


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
        _apply_session_payload_locked(payload)
        for game in room_games.values():
            _nudge_game_bots_locked(game)
        active = sum(
            1
            for game in room_games.values()
            if game is not None and getattr(game, "state", "ended") != "ended"
        )
        sessions = len(disconnected_sessions)
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
        "[*] ================================\n",
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


def deliver_offline_messages(conn, recipient_name: str) -> int:
    """Flush stored leave-messages to this connection. Returns how many were sent."""
    pending = offline_messages.take_all(recipient_name)
    if not pending:
        return 0
    n = len(pending)
    send_line(conn, f"[*] 你有 {n} 条留言（离线期间收到，按时间顺序）：\n")
    for item in pending:
        when = _format_offline_ts(item.get("ts", 0))
        sender = item.get("from") or "?"
        if (item.get("kind") or "pm") == "file":
            notice = _file_ready_message_from_leave(item)
            if notice:
                send_line(conn, f"[*] （离线文件 {when}，来自 {sender}）\n")
                send_line(conn, notice)
            else:
                text = item.get("text") or "[文件]"
                send_line(conn, f"[PM from {sender}] (离线文件 {when}) {text}\n")
            continue
        text = item.get("text") or ""
        send_line(conn, f"[PM from {sender}] (留言 {when}) {text}\n")
    return n


def _send_leave_list(conn, sender_name: str, recipient: str | None = None) -> None:
    items = offline_messages.list_sent_unread(sender_name, recipient)
    if not items:
        if recipient:
            send_line(
                conn,
                f"[*] 没有发给 {recipient!r}、对方尚未阅读的留言或文件。\n",
            )
        else:
            send_line(conn, "[*] 你目前没有对方尚未阅读的留言或文件。\n")
        return
    if recipient:
        send_line(
            conn,
            f"[*] 发给 {recipient!r}、对方尚未阅读的留言/文件（共 {len(items)} 条）：\n",
        )
        for item in items:
            when = _format_offline_ts(item.get("ts", 0))
            send_line(
                conn,
                f"[*]   {item['index']}. ({when}) {item['text']}\n",
            )
        send_line(conn, f"[*] 撤回：/leave {recipient} <编号>\n")
        return
    send_line(conn, f"[*] 你发出的未读留言/文件（共 {len(items)} 条）：\n")
    current_to = None
    for item in items:
        to_name = item.get("to") or "?"
        if to_name != current_to:
            current_to = to_name
            send_line(conn, f"[*] → {to_name}:\n")
        when = _format_offline_ts(item.get("ts", 0))
        send_line(
            conn,
            f"[*]   {item['index']}. ({when}) {item['text']}\n",
        )
    send_line(conn, "[*] 撤回：/leave <昵称> <编号>\n")


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
            send_line(
                conn,
                "[*] Usage: /leave <nick> <n>  |  /leave recall <nick> <n>\n",
            )
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
        send_line(
            conn,
            "[*] Usage: /leave [nick]  |  /leave <nick> <n>\n"
            "[*] （列出或撤回你发出、对方尚未阅读的留言或离线文件）\n",
        )
        return
    try:
        index = int(num_raw)
    except ValueError:
        send_line(conn, "[*] 编号须为正整数。\n")
        return
    removed = offline_messages.recall(name, target, index)
    if removed is None:
        send_line(
            conn,
            f"[*] 撤回失败：没有发给 {target!r} 的第 {index} 条未读留言/文件"
            f"（可用 /leave {target} 查看）。\n",
        )
        return
    _revoke_recalled_file(removed, target)
    when = _format_offline_ts(removed.get("ts", 0))
    label = "文件" if (removed.get("kind") or "pm") == "file" else "留言"
    send_line(
        conn,
        f"[*] 已撤回发给 {target!r} 的第 {index} 条{label}"
        f"（{when}）：{removed.get('text')}\n",
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
        send_line(
            conn,
            f"[*] {target_nick!r} 当前不在线，已留言；对方下次上线时会收到。\n",
        )
        return
    for peer_conn, peer_name in targets:
        send_line(peer_conn, f"[PM from {sender_name}] {text}\n")
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


def _set_library_page(conn, user: str, path: Path, page: int) -> None:
    page = max(0, int(page))
    with lock:
        library_reading[conn] = {"path": str(path.resolve()), "page": page}
    library_bookmarks.set_page(user, path.name, page)


def _get_cached_book(path: Path) -> library.BookDocument:
    key = str(path.resolve())
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError as exc:
        raise FileNotFoundError(str(path)) from exc
    cached = library_doc_cache.get(key)
    if cached and cached[0] == mtime_ns:
        return cached[1]
    doc = library.load_book(path)
    library_doc_cache[key] = (mtime_ns, doc)
    return doc


def _send_library_page(conn, doc: library.BookDocument, page_idx: int) -> None:
    total = doc.total_pages
    page_idx = max(0, min(page_idx, total - 1))
    send_line(conn, f"[*] --- 《{doc.title}》 第 {page_idx + 1}/{total} 页 ---\n")
    for ln in library.wrap_page_lines(doc.pages[page_idx]):
        send_line(conn, f"[*]    {ln}\n")
    send_line(
        conn,
        "[*] 翻页：/library next | prev | page <页码> | search <关键词> | show | info | close\n",
    )


def _send_library_catalog(conn, user: str, query: str = "") -> None:
    lib_dir = _library_dir()
    send_line(conn, "[*] --- 图书馆 ---\n")
    if not lib_dir.is_dir():
        send_line(conn, f"[*] 图书馆目录不存在：{lib_dir}\n")
        send_line(conn, "[*] 请将 epub / txt / md / pdf 放入该目录后重试。\n")
        return
    catalog = library.list_books(lib_dir)
    if not catalog:
        send_line(conn, f"[*] 目录为空：{lib_dir}\n")
        send_line(conn, "[*] 支持格式：.epub、.txt、.md、.pdf\n")
        return
    query = (query or "").strip()
    books = library.search_catalog(catalog, query) if query else catalog
    if query:
        send_line(
            conn,
            f"[*] 查找「{query}」，共 {len(books)} 本（全库 {len(catalog)} 本）。\n",
        )
        if not books:
            send_line(conn, "[*] 未找到匹配的图书，请换关键词重试。\n")
            send_line(conn, "[*] 用 /library 查看全部书目。\n")
            return
    user_marks = library_bookmarks.list_for_user(user)
    for entry in books:
        mark = user_marks.get(entry.name)
        mark_suffix = f" · 书签第 {mark + 1} 页" if mark is not None else ""
        send_line(
            conn,
            f"[*] {entry.index}. [{entry.ext.upper()}] {entry.name} ({library.format_size(entry.size_bytes)}){mark_suffix}\n",
        )
    send_line(conn, "[*] 打开：/library open <序号>  或  /library open <文件名>\n")
    if len(catalog) > 10:
        send_line(conn, "[*] 查找：/library find <关键词>\n")
    send_line(conn, "[*] 我的书签：/library bookmarks\n")


def _send_library_bookmarks(conn, user: str) -> None:
    lib_dir = _library_dir()
    marks = library_bookmarks.list_for_user(user)
    send_line(conn, "[*] --- 我的书签 ---\n")
    if not marks:
        send_line(conn, "[*] 暂无书签；打开图书并翻页后会自动保存。\n")
        return
    catalog = library.list_books(lib_dir) if lib_dir.is_dir() else []
    name_by_file = {entry.name: entry for entry in catalog}
    for book_name in sorted(marks, key=lambda n: n.lower()):
        page = marks[book_name]
        entry = name_by_file.get(book_name)
        if entry:
            label = f"{entry.index}. [{entry.ext.upper()}] {book_name}"
        else:
            label = book_name
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
            session = library_reading.get(conn)
        if session:
            path = session.get("path")
            page = int(session.get("page") or 0)
            if path:
                try:
                    doc = _get_cached_book(Path(str(path)))
                    send_line(
                        conn,
                        f"[*] 当前在读：《{doc.title}》第 {page + 1}/{doc.total_pages} 页\n",
                    )
                except Exception:
                    library_reading.pop(conn, None)
        return

    parts = raw.split()
    head = parts[0].lower()

    if head in {"help", "?", "帮助"}:
        send_line(conn, "[*] /library 用法：\n")
        send_line(conn, "[*]   /library | /lib                 书目列表（含你的书签进度）\n")
        send_line(conn, "[*]   /library find <关键词> | 查找      按书名查找（未打开书时）\n")
        send_line(conn, "[*]   /library open <序号|文件名>        打开（有书签则从书签继续）\n")
        send_line(conn, "[*]   /library show | 显示               显示当前页内容\n")
        send_line(conn, "[*]   /library next | 下一页             下一页（自动存书签）\n")
        send_line(conn, "[*]   /library prev | 上一页             上一页（自动存书签）\n")
        send_line(conn, "[*]   /library page <页码> | 页 N        跳到指定页（自动存书签）\n")
        send_line(conn, "[*]   /library search <关键词> | 搜索    在当前书中关键词检索并跳转\n")
        send_line(conn, "[*]   /library bookmarks | 书签           列出我的全部书签\n")
        send_line(conn, "[*]   /library reset <序号|文件名>       清除某本书的书签\n")
        send_line(conn, "[*]   /library info | 状态               当前阅读进度信息\n")
        send_line(conn, "[*]   /library close | 关闭              结束阅读（保留书签）\n")
        send_line(conn, f"[*] 目录：{lib_dir}\n")
        return

    if head in {"bookmarks", "bookmark", "书签"}:
        _send_library_bookmarks(conn, user)
        return

    if head in {"reset", "清除"}:
        if len(parts) < 2:
            send_line(conn, "[*] Usage: /library reset <序号|文件名>\n")
            return
        token = raw.split(None, 1)[1].strip()
        if not lib_dir.is_dir():
            send_line(conn, f"[*] 图书馆目录不存在：{lib_dir}\n")
            return
        catalog = library.list_books(lib_dir)
        entry = library.resolve_book(lib_dir, token, catalog)
        book_name = entry.name if entry else Path(token).name
        if library_bookmarks.clear_book(user, book_name):
            send_line(conn, f"[*] 已清除《{book_name}》的书签。\n")
        else:
            send_line(conn, f"[*] 《{book_name}》没有保存的书签。\n")
        return

    if head in {"close", "关闭"}:
        with lock:
            library_reading.pop(conn, None)
        send_line(conn, "[*] 已关闭当前图书（书签已保留）。\n")
        return

    if head in {"info", "状态"}:
        with lock:
            session = library_reading.get(conn)
        if not session:
            send_line(conn, "[*] 当前没有在阅读的图书。\n")
            return
        try:
            doc = _get_cached_book(Path(str(session["path"])))
        except Exception as exc:
            with lock:
                library_reading.pop(conn, None)
            send_line(conn, f"[*] 无法读取当前图书：{exc}\n")
            return
        page = int(session.get("page") or 0)
        send_line(
            conn,
            f"[*] 在读：《{doc.title}》第 {page + 1}/{doc.total_pages} 页（{doc.source_path.name}）\n",
        )
        return

    if head in {"show", "显示"}:
        with lock:
            session = library_reading.get(conn)
        if not session:
            send_line(conn, "[*] 请先用 /library open <序号> 打开图书。\n")
            return
        try:
            doc = _get_cached_book(Path(str(session["path"])))
        except Exception as exc:
            with lock:
                library_reading.pop(conn, None)
            send_line(conn, f"[*] 无法读取图书：{exc}\n")
            return
        page = int(session.get("page") or 0)
        _send_library_page(conn, doc, page)
        return

    if head in {"next", "n", "下一页"}:
        with lock:
            session = library_reading.get(conn)
        if not session:
            send_line(conn, "[*] 请先用 /library open <序号> 打开图书。\n")
            return
        try:
            doc = _get_cached_book(Path(str(session["path"])))
        except Exception as exc:
            with lock:
                library_reading.pop(conn, None)
            send_line(conn, f"[*] 无法读取图书：{exc}\n")
            return
        page = int(session.get("page") or 0) + 1
        if page >= doc.total_pages:
            send_line(conn, "[*] 已是最后一页。\n")
            page = doc.total_pages - 1
        _set_library_page(conn, user, Path(str(session["path"])), page)
        _send_library_page(conn, doc, page)
        return

    if head in {"prev", "p", "上一页"}:
        with lock:
            session = library_reading.get(conn)
        if not session:
            send_line(conn, "[*] 请先用 /library open <序号> 打开图书。\n")
            return
        try:
            doc = _get_cached_book(Path(str(session["path"])))
        except Exception as exc:
            with lock:
                library_reading.pop(conn, None)
            send_line(conn, f"[*] 无法读取图书：{exc}\n")
            return
        page = max(0, int(session.get("page") or 0) - 1)
        if page == int(session.get("page") or 0):
            send_line(conn, "[*] 已是第一页。\n")
        _set_library_page(conn, user, Path(str(session["path"])), page)
        _send_library_page(conn, doc, page)
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
            session = library_reading.get(conn)
        if not session:
            send_line(conn, "[*] 请先用 /library open <序号> 打开图书。\n")
            return
        try:
            doc = _get_cached_book(Path(str(session["path"])))
        except Exception as exc:
            with lock:
                library_reading.pop(conn, None)
            send_line(conn, f"[*] 无法读取图书：{exc}\n")
            return
        if page_1based < 1 or page_1based > doc.total_pages:
            send_line(
                conn,
                f"[*] 无效页码：共 {doc.total_pages} 页，请用 1～{doc.total_pages}。\n",
            )
            return
        page = page_1based - 1
        _set_library_page(conn, user, Path(str(session["path"])), page)
        _send_library_page(conn, doc, page)
        return

    if head in {"search", "find", "搜索", "查找", "检索"}:
        query = raw.split(None, 1)[1].strip() if len(parts) >= 2 else ""
        if not query:
            send_line(conn, "[*] 用法：/library find <关键词>（查找书目）或打开书后 search <关键词>（书内检索）\n")
            return
        with lock:
            session = library_reading.get(conn)
        if not session:
            _send_library_catalog(conn, user, query)
            return
        try:
            doc = _get_cached_book(Path(str(session["path"])))
        except Exception as exc:
            with lock:
                library_reading.pop(conn, None)
            send_line(conn, f"[*] 无法读取图书：{exc}\n")
            return
        results = library.search_book(doc, query)
        if not results:
            send_line(conn, f"[*] 在《{doc.title}》中未找到「{query}」。\n")
            return
        send_line(
            conn,
            f"[*] 在《{doc.title}》中搜索「{query}」，找到 {len(results)} 处：\n",
        )
        for page_idx, snippet in results:
            send_line(conn, f"[*]   第 {page_idx + 1} 页：{snippet}\n")
        if len(results) == 1:
            page_idx = results[0][0]
            _set_library_page(conn, user, Path(str(session["path"])), page_idx)
            send_line(conn, f"[*] 已自动跳转到第 {page_idx + 1} 页。\n")
            _send_library_page(conn, doc, page_idx)
        else:
            send_line(conn, "[*] 用 /library page <页码> 跳转到对应页。\n")
        return

    if head in {"open", "read", "读", "打开"}:
        if len(parts) < 2:
            send_line(conn, "[*] Usage: /library open <序号|文件名>\n")
            return
        token = raw.split(None, 1)[1].strip()
        if not lib_dir.is_dir():
            send_line(conn, f"[*] 图书馆目录不存在：{lib_dir}\n")
            return
        catalog = library.list_books(lib_dir)
        entry = library.resolve_book(lib_dir, token, catalog)
        if not entry:
            send_line(conn, f"[*] 未找到图书：{token}\n")
            send_line(conn, "[*] 用 /library 查看可用序号与文件名。\n")
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
        saved = library_bookmarks.get_page(user, entry.name)
        page = saved if saved is not None else 0
        page = min(page, doc.total_pages - 1)
        _set_library_page(conn, user, entry.path, page)
        if saved is not None and saved > 0:
            send_line(
                conn,
                f"[*] 已打开 [{entry.ext.upper()}] {entry.name}，从书签第 {page + 1}/{doc.total_pages} 页继续。\n",
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
        summary = _format_file_leave_summary(transfer.filename, transfer.file_size)
        offline_messages.leave(
            recipient,
            transfer.sender,
            summary,
            kind="file",
            meta={
                "transfer_id": transfer.transfer_id,
                "filename": transfer.filename,
                "file_size": transfer.file_size,
                "download_token": token,
                "download_key": key,
                "download_url": download_url,
                "room": transfer.room,
            },
        )


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
            
            # Get all users in room except sender
            for c in rooms[room_name]:
                if c != conn and c in clients:
                    recipients.append(clients[c]["name"])
        
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
    # Create transfer session
    try:
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
        send_line(conn, "[*] =====================================\n")
        
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
        raw = _pickle_game_for_storage(game)
    hub.sync_game(room, hub.node_id, base64.b64encode(raw).decode("ascii"))


def _federation_notify_game_end(room: str) -> None:
    hub = federation.get_hub()
    if hub is None or not hub.enabled:
        return
    auth = room_game_authority.pop(room, None) or hub.node_id
    if auth == hub.node_id:
        hub.end_game(room, hub.node_id)


def _fed_on_game_sync(_from_peer: str, room: str, authority: str, b64: str) -> None:
    hub = federation.get_hub()
    if hub is not None and authority == hub.node_id:
        return
    try:
        game = pickle.loads(base64.b64decode(b64.encode("ascii")))
    except Exception as e:
        print(f"federation game sync skip room {room!r}: {e!r}")
        return
    _rebind_game_services(game)
    with lock:
        _remap_local_game_seats_locked(room, game)
        room_games[room] = game
        room_game_authority[room] = authority
    _mark_sessions_dirty()
    if getattr(game, "state", "ended") != "ended":
        send_oriented_boards(room, game)
        send_sanguo_hand_views(room, game)


def _fed_on_game_end(room: str, authority: str) -> None:
    hub = federation.get_hub()
    if hub is not None and authority == hub.node_id:
        return
    with lock:
        room_games.pop(room, None)
        room_game_authority.pop(room, None)
    _mark_sessions_dirty()


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
    if sub == "join":
        return FederatedSeat(player_node, name)
    seat = _game_seat_conn_by_name(game, name)
    if isinstance(seat, FederatedSeat) and seat.node_id == player_node:
        return seat
    return seat


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
    if send_boards and (ended or getattr(game, "send_view_on_move", True)):
        send_oriented_boards(room, game)
    send_sanguo_hand_views(room, game)
    _persist_after_game_change()
    if ended:
        with lock:
            room_games.pop(room, None)
        _federation_notify_game_end(room)
    else:
        _federation_sync_game(room)


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
                return
            actor = _fed_resolve_actor(room, game, player_node, name, sub)
            if isinstance(actor, FederatedSeat):
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
            return
        try:
            with lock:
                resumed = _resume_same_account_seat_locked(room, game, actor, name)
                _ensure_game_runtime_compat(game)
                priv, bcast, ended = game.try_move(actor, rest)
                if not ended:
                    extra = _nudge_game_bots_locked(game)
                    if extra:
                        bcast = list(bcast) + extra
                        ended = getattr(game, "state", "ended") == "ended"
        except Exception as e:
            print(f"fed /game move failed: {e!r}")
            return
        if resumed:
            priv = ["你已从其他终端续玩接管，以下是本次操作结果："] + list(priv)
        _finish_game_action(room, game, actor, priv, bcast, ended)
        return

    if sub == "resign":
        if game is None or actor is None:
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


def _should_forward_game(room: str, sub: str) -> bool:
    hub = federation.get_hub()
    if hub is None or not hub.enabled:
        return False
    auth = room_game_authority.get(room)
    if not auth:
        return False
    return auth != hub.node_id and sub in (
        "join",
        "move",
        "resign",
        "undo",
        "abort",
        "end",
    )


def _fed_on_room_msg(room: str, msg: bytes, from_peer: str) -> None:
    broadcast_room(room, msg, via_federation_from=from_peer)


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
        else:
            text = f"[*] 联邦节点 {peer_node} 已加入（由 {reporter} 通报）\n"
    elif event == "down":
        if reporter == local_id:
            text = f"[*] 联邦节点 {peer_node} 已退出（与本机断开）\n"
        else:
            text = f"[*] 联邦节点 {peer_node} 已退出（由 {reporter} 通报）\n"
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
    for peer_conn, _ in targets:
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
        return

    # Recipient is offline on this node — store absolute origin URL for later login.
    summary = _format_file_leave_summary(filename, file_size)
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
    )


def _ensure_federation_hub() -> None:
    global _fed_hub
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
    )
    _fed_hub.start()


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
        broadcast_room(room, leave_msg)
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

        if newly_joined:
            broadcast_room(
                new_room,
                f"[+] {name} joined #{new_room}\n".encode("utf-8"),
                exclude_conn=conn,
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
        for hline in HELP_LINES:
            send_line(conn, hline)
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
                send_line(conn, f"[*] #{room} 当前公告：{cur}\n")
            else:
                send_line(conn, f"[*] #{room} 暂无公告。\n")
            return
        if not is_owner:
            send_line(conn, "[*] 只有房主可以修改公告（查看无需权限）。\n")
            return
        if tail.lower() == "clear":
            with lock:
                room_announcements.pop(room, None)
            broadcast_room(
                room,
                f"[#{room}] [*] 公告已清除。\n".encode("utf-8"),
            )
            send_line(conn, f"[*] 已清除 #{room} 的公告。\n")
            return
        one_line = " ".join(tail.split())
        if len(one_line) > MAX_ANNOUNCE_LEN:
            send_line(
                conn,
                f"[*] 公告过长（最多 {MAX_ANNOUNCE_LEN} 字符）。\n",
            )
            return
        with lock:
            room_announcements[room] = one_line
        broadcast_room(
            room,
            f"[#{room}] [*] 公告：{one_line}\n".encode("utf-8"),
        )
        send_line(conn, f"[*] 已更新 #{room} 的公告。\n")
        return

    if cmd == "/game":
        try:
            _handle_game(conn, name, current_room, payload)
        except Exception as e:
            print(f"/game error: room={current_room} user={name} payload={payload!r} err={e!r}")
            traceback.print_exc()
            send_line(conn, "[*] /game 命令执行失败，请稍后重试（详情见服务端日志）。\n")
        return

    if cmd == "/news":
        try:
            _handle_news(conn, payload)
        except Exception as e:
            print(f"/news error: {e!r}")
            traceback.print_exc()
            send_line(conn, "[*] 新闻命令处理失败，请稍后重试（详情见服务端日志）。\n")
        return

    if cmd in {"/library", "/lib"}:
        try:
            _handle_library(conn, payload)
        except Exception as e:
            print(f"/library error: {e!r}")
            traceback.print_exc()
            send_line(conn, "[*] 图书馆命令处理失败，请稍后重试（详情见服务端日志）。\n")
        return

    if cmd == "/dict":
        _handle_dict(conn, payload)
        return

    if cmd == "/sendfile" or cmd == "/file":
        _handle_sendfile(conn, name, payload)
        return

    send_line(conn, "[*] Unknown command. Try /help\n")


def _handle_game(conn, name: str, room: str, payload: str) -> None:
    """All /game subcommands. Mutates room_games under the global lock."""
    raw = payload[len("/game") :].strip()
    if not raw or raw.lower() == "help":
        send_line(conn, "[*] /game 用法：\n")
        for ln in games.HELP_LINES:
            send_line(conn, ln + "\n")
        with lock:
            enabled = _enabled_games_for_room_locked(room)
        send_line(
            conn,
            "[*] 本房可玩：" + ", ".join(games.list_game_names(enabled)) + "\n",
        )
        return

    sub, _, rest = raw.partition(" ")
    sub = sub.lower()
    rest = rest.strip()

    if _should_forward_game(room, sub):
        hub = federation.get_hub()
        auth = room_game_authority.get(room, "")
        if hub and auth and hub.forward_game_cmd(auth, room, hub.node_id, name, sub, rest):
            return
        send_line(conn, "[*] 无法连接对局所在节点，请稍后重试。\n")
        return

    if sub == "list":
        with lock:
            enabled = _enabled_games_for_room_locked(room)
            names = games.list_game_names(enabled)
        if names:
            line = "[*] 可玩游戏：" + ", ".join(names)
            line += "（xiangqi 别名 cchess；sanguo 别名 sgs/三国杀）\n"
        else:
            line = (
                "[*] 本房暂无已上线游戏；房主可用 /game on <名称> 上线。\n"
            )
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
            send_line(conn, f"[*] {game_name} 当前没有持久化棋类积分。\n")
            return
        send_game_private(conn, room, _rating_summary_lines(target_name, game_name))
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
        send_oriented_boards(room, new_game)
        _persist_after_game_change()
        _federation_sync_game(room)
        return

    if sub == "join":
        with lock:
            game = room_games.get(room)
            if game is None:
                send_line(conn, "[*] 本房没有进行中的对局；用 /game new chess 开局。\n")
                return
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
            lines = game.seats() if game else ["本房没有进行中的对局。"]
        send_game_private(conn, room, lines)
        return

    if sub == "show":
        bot_lines: list[str] = []
        with lock:
            game = room_games.get(room)
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
                nudge = getattr(game, "nudge_bots", None)
                if callable(nudge):
                    bot_lines = nudge()
        send_game_private(conn, room, lines)
        if bot_lines:
            broadcast_game(room, bot_lines)
        return

    if sub == "move":
        try:
            with lock:
                game = room_games.get(room)
                if game is None:
                    send_line(conn, "[*] 本房没有进行中的对局。\n")
                    return
                resumed = _resume_same_account_seat_locked(room, game, conn, name)
                _ensure_game_runtime_compat(game)
                priv, bcast, ended = game.try_move(conn, rest)
                if not ended:
                    extra = _nudge_game_bots_locked(game)
                    if extra:
                        bcast = list(bcast) + extra
                        ended = getattr(game, "state", "ended") == "ended"
        except Exception as e:
            print(f"/game move failed: room={room} user={name} cmd={rest!r} err={e!r}")
            send_line(conn, f"[*] /game move 执行失败：{e}\n")
            return
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
                    active_game_lines = ["已自动续玩接管旧终端席位。"] + active_game_lines
            room_labels = [
                f"*#{r}" if r == active_room else f"#{r}"
                for r in sorted(inherited_rooms)
            ]

        print(f"{name} joined #{active_room} (tcp_peer={addr[0]!r}:{addr[1]})")

        join_msg = f"[+] {name} joined #{active_room}\n".encode("utf-8")
        broadcast_room(active_room, join_msg, exclude_conn=conn)
        if hub is not None and hub.enabled:
            for room in inherited_rooms:
                hub.notify_join(name, room)
        send_line(
            conn,
            f"[*] Active room #{active_room}. "
            f"/names /rooms /join /switch /msg /sendfile /leave /part /announce /game /news /dict /clear /help\n",
        )
        send_line(conn, f"[*] Rooms: {', '.join(room_labels)}\n")
        if hub is not None and hub.enabled and hub.peer_count > 0:
            send_line(
                conn,
                f"[*] 联邦网络已连接 {hub.peer_count} 个节点（同名用户/房间跨服合并）。\n",
            )
        if same_name_peers:
            send_line(conn, "[*] 检测到同账号其他终端在线，已同步房间并支持直接续玩。\n")
        elif restored_from_session:
            send_line(conn, "[*] 已恢复上次客户端会话，回到原房间；如有未结束对局可继续操作。\n")
        if resumed_game_rooms:
            send_line(conn, "[*] 已接管旧连接保留的游戏席位。\n")
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
                    nudge = getattr(g, "nudge_bots", None) if g is not None else None
                    bot_lines = nudge() if callable(nudge) else []
                if bot_lines:
                    broadcast_game(active_room, bot_lines)

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
            
            # Start cleanup task for expired transfers
            def _cleanup_task():
                while not _shutdown_requested:
                    time.sleep(3600)  # Run every hour
                    if not _shutdown_requested:
                        try:
                            file_sharing.file_transfer_store.cleanup_expired()
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
