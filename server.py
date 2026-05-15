import os
import re
import socket
import ssl
import textwrap
import threading
import time
import traceback
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse
from collections import defaultdict
from html import unescape
from typing import Optional

import games

DEFAULT_ROOM = "default"
PORT = int(os.environ.get("SSHCHAT_PORT", "12345"))

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
lock = threading.Lock()

ROOM_RE = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")
MAX_ANNOUNCE_LEN = 400
_DISCONNECT_ERRNOS = {32, 54, 57, 104}

# VT100: clear display + cursor home; trailing \n so line-oriented clients flush it.
_CLEAR_SCREEN = "\x1b[2J\x1b[H\n"
_SCREEN_CLEARED_ACK = "[*] Screen cleared.\n"

NEWS_CACHE_TTL = int(os.environ.get("SSHCHAT_NEWS_CACHE_SECONDS", "600"))
NEWS_FETCH_TIMEOUT = float(os.environ.get("SSHCHAT_NEWS_TIMEOUT", "4"))
NEWS_TLS_FALLBACK = os.environ.get("SSHCHAT_NEWS_TLS_FALLBACK", "1") != "0"
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
article_fetch_cache: dict[str, tuple[float, str]] = {}
article_fetch_lock = threading.Lock()

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
    "[*] /msg <昵称> <文字>   私聊：发给该昵称的在线用户（大小写不敏感）。\n",
    "[*]              若有多人同昵称，会全部收到；发件人会收到汇总提示。\n",
    "[*]\n",
    "[*] /clear 或 /cls  清屏（终端会清空显示；图形客户端会清空当前房间记录）。\n",
    "[*] /announce      查看当前房间公告；房主可用 /announce <文字> 设置，/announce clear 清除。\n",
    "[*]              房主：#default 为第一个进服用户；其它房间为第一个 /join 该房的用户。\n",
    "[*]\n",
    "[*] /game ...      房间小游戏（chess、gomoku、xiangqi）。/game list /new /join /seats /show /move /pgn /resign /abort /end。\n",
    "[*]              详细用法用 /game help 查看。\n",
    "[*] /news [中文|国际|科技|all] [条数]  从 RSS 查看标题与提要正文；默认每类 3 条。\n",
    "[*] /news detail <分类> <序号>  更长提要（RSS 内；别名：详情）。\n",
    "[*] /news fetch <分类> <序号>  按 RSS 链接抓取网页正文（别名：全文；非 JS 站、可能截断）。\n",
    "[*] /help          显示本说明。\n",
    "[*]\n",
    "[*] 发 /file … 会提示不支持：本项目不在 SSH 会话里做文件传输。\n",
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


def send_oriented_boards(room: str, game) -> None:
    """Send full board view; second seat sees flipped board (己方在下)."""
    with lock:
        targets = [c for c in list(rooms.get(room, ())) if c in clients]
    for conn in targets:
        try:
            lines = game.show(conn)
        except TypeError:
            lines = game.show()
        if lines:
            send_game_private(conn, room, lines)


def _drop_game_if_room_empty_locked(room: str) -> None:
    """Caller holds lock; drop the game session when the room has no clients."""
    if not rooms.get(room):
        room_games.pop(room, None)


def send_line(conn, text: str) -> None:
    try:
        conn.send(text.encode("utf-8"))
    except Exception as e:
        print(f"send_line error: {e!r}")
        remove_client(conn)


def send_private_messages(conn, sender_name: str, target_nick: str, text: str) -> None:
    """Deliver a private message to all matching nicks; echo status to sender."""
    targets = find_clients_by_nickname(target_nick)
    if not targets:
        send_line(
            conn,
            f"[*] No one online named {target_nick!r} (match is case-insensitive)\n",
        )
        return
    for peer_conn, peer_name in targets:
        send_line(peer_conn, f"[PM from {sender_name}] {text}\n")
    if len(targets) == 1:
        only = targets[0][1]
        send_line(conn, f"[*] PM → {only}: {text}\n")
    else:
        n = len(targets)
        send_line(
            conn,
            f"[*] PM sent to {n} users matching {target_nick!r}: {text}\n",
        )


def find_clients_by_nickname(nick: str) -> list[tuple]:
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


def _news_build_opener(ssl_context: Optional[ssl.SSLContext] = None):
    """Return a custom opener, or None to use urllib.request.urlopen defaults."""
    handlers: list = []
    proxy = _news_proxy_url()
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

    def _once(ssl_ctx: Optional[ssl.SSLContext]) -> tuple[bytes, Optional[str]]:
        opener = _news_build_opener(ssl_ctx)
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

    try:
        return _once(None)
    except Exception as e:
        if not NEWS_TLS_FALLBACK or "CERTIFICATE_VERIFY_FAILED" not in str(e):
            raise
        return _once(ssl._create_unverified_context())


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
    for enc in (charset, "utf-8", "gb18030", "gbk", "latin-1"):
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
        },
    )
    data, charset = _news_urlopen_read_limited(
        req, NEWS_PAGE_TIMEOUT, NEWS_PAGE_MAX_BYTES
    )
    html = _decode_html_bytes(data, charset)
    text = _html_to_plain_text(html)
    with article_fetch_lock:
        article_fetch_cache[url] = (time.monotonic(), text)
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
            "[*] 常见原因：付费墙、需登录、反爬、仅 JS 渲染页面、或超时。"
            "可加大 SSHCHAT_NEWS_PAGE_TIMEOUT / 检查代理。\n",
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

    feeds = NEWS_CATEGORIES[category]["feeds"]
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


def broadcast_room(room: str, msg: bytes, exclude_conn=None) -> None:
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


def remove_client(conn) -> None:
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
        game_notices: list[tuple[str, list[str]]] = []
        for room in joined_rooms:
            rooms[room].discard(conn)
            _reassign_room_owner_locked(room, conn)
            game = room_games.get(room)
            if game is not None:
                _, bcast, _ended = game.on_player_leave(conn, name)
                if bcast:
                    game_notices.append((room, bcast))
            _drop_game_if_room_empty_locked(room)
    for room in joined_rooms:
        leave_msg = f"[!] {name} left #{room}\n".encode("utf-8")
        broadcast_room(room, leave_msg)
    for room, lines in game_notices:
        broadcast_game(room, lines)
    try:
        conn.close()
    except Exception:
        pass


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
            send_line(
                conn,
                f"[*] Joined #{new_room} and switched from #{prev_room} to #{new_room}\n",
            )
            send_room_announcement_preview(conn, new_room)
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
            _reassign_room_owner_locked(target_room, conn)
            game_bcast: list[str] = []
            game = room_games.get(target_room)
            if game is not None:
                _, game_bcast, _ended = game.on_player_leave(conn, name)
            _drop_game_if_room_empty_locked(target_room)
            if active == target_room:
                switched_to = sorted(joined)[0]
                clients[conn]["current_room"] = switched_to
        if game_bcast:
            broadcast_game(target_room, game_bcast)
        broadcast_room(
            target_room,
            f"[!] {name} left #{target_room}\n".encode("utf-8"),
        )
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
            is_owner = room_owners.get(room) == conn
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
        _handle_game(conn, name, current_room, payload)
        return

    if cmd == "/news":
        try:
            _handle_news(conn, payload)
        except Exception as e:
            print(f"/news error: {e!r}")
            traceback.print_exc()
            send_line(conn, "[*] 新闻命令处理失败，请稍后重试（详情见服务端日志）。\n")
        return

    send_line(conn, "[*] Unknown command. Try /help\n")


def _handle_game(conn, name: str, room: str, payload: str) -> None:
    """All /game subcommands. Mutates room_games under the global lock."""
    raw = payload[len("/game") :].strip()
    if not raw or raw.lower() == "help":
        send_line(conn, "[*] /game 用法：\n")
        for ln in games.HELP_LINES:
            send_line(conn, ln + "\n")
        send_line(
            conn,
            "[*] 当前支持的游戏：" + ", ".join(games.list_game_names()) + "\n",
        )
        return

    sub, _, rest = raw.partition(" ")
    sub = sub.lower()
    rest = rest.strip()

    if sub == "list":
        send_line(
            conn,
            "[*] 可玩游戏：" + ", ".join(games.list_game_names()) + "（xiangqi 别名 cchess）\n",
        )
        return

    if sub == "new":
        game_name = games.resolve_game_name(rest.lower() or "chess")
        cls = games.GAMES.get(game_name)
        if cls is None:
            send_line(
                conn,
                f"[*] 未知游戏 {game_name!r}；/game list 查看可用。\n",
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
                new_game = cls(conn, name)
            except RuntimeError as e:
                send_line(conn, f"[*] 无法开局：{e}\n")
                return
            room_games[room] = new_game
        seat = getattr(new_game, "first_seat_desc", "第一席")
        broadcast_game(
            room,
            [
                f"{name} 开了一局 {game_name}（{seat}），"
                "等另一位玩家用 /game join 加入。",
            ],
        )
        send_oriented_boards(room, new_game)
        return

    if sub == "join":
        with lock:
            game = room_games.get(room)
            if game is None:
                send_line(conn, "[*] 本房没有进行中的对局；用 /game new chess 开局。\n")
                return
            priv, bcast, _ = game.try_join(conn, name)
        send_game_private(conn, room, priv)
        broadcast_game(room, bcast)
        send_oriented_boards(room, game)
        return

    if sub == "seats":
        with lock:
            game = room_games.get(room)
            lines = game.seats() if game else ["本房没有进行中的对局。"]
        send_game_private(conn, room, lines)
        return

    if sub == "show":
        with lock:
            game = room_games.get(room)
            if game is None:
                lines = ["本房没有进行中的对局。"]
            else:
                try:
                    lines = game.show(conn)
                except TypeError:
                    lines = game.show()
        send_game_private(conn, room, lines)
        return

    if sub == "move":
        with lock:
            game = room_games.get(room)
            if game is None:
                send_line(conn, "[*] 本房没有进行中的对局。\n")
                return
            priv, bcast, ended = game.try_move(conn, rest)
        send_game_private(conn, room, priv)
        broadcast_game(room, bcast)
        send_oriented_boards(room, game)
        return

    if sub == "resign":
        with lock:
            game = room_games.get(room)
            if game is None:
                send_line(conn, "[*] 本房没有进行中的对局。\n")
                return
            priv, bcast, _ = game.resign(conn, name)
        send_game_private(conn, room, priv)
        broadcast_game(room, bcast)
        return

    if sub == "abort":
        with lock:
            game = room_games.get(room)
            if game is None:
                send_line(conn, "[*] 本房没有进行中的对局。\n")
                return
            priv, bcast, _ = game.abort(conn, name)
        send_game_private(conn, room, priv)
        broadcast_game(room, bcast)
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
            is_owner = room_owners.get(room) is conn
            if game is None:
                send_line(conn, "[*] 本房没有进行中的对局。\n")
                return
            if not is_owner:
                send_line(conn, "[*] 只有房主可以 /game end。\n")
                return
            room_games.pop(room, None)
        broadcast_game(room, [f"{name}（房主）结束了本房的对局。"])
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
        first, buffer = buffer.split(b"\n", 1)
        name = _parse_handshake_line(first.decode("utf-8", errors="replace"))

        with lock:
            was_empty_default = len(rooms[DEFAULT_ROOM]) == 0
            clients[conn] = {
                "name": name,
                "rooms": {DEFAULT_ROOM},
                "current_room": DEFAULT_ROOM,
            }
            rooms[DEFAULT_ROOM].add(conn)
            if was_empty_default:
                room_owners[DEFAULT_ROOM] = conn

        print(f"{name} joined #{DEFAULT_ROOM} (tcp_peer={addr[0]!r}:{addr[1]})")

        join_msg = f"[+] {name} joined #{DEFAULT_ROOM}\n".encode("utf-8")
        broadcast_room(DEFAULT_ROOM, join_msg, exclude_conn=conn)
        send_line(
            conn,
            f"[*] Active room #{DEFAULT_ROOM}. "
            f"/names /rooms /join /switch /msg /part /announce /game /news /clear /help\n",
        )
        send_room_announcement_preview(conn, DEFAULT_ROOM)

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

    except Exception as e:
        print("connection error:", e)
        traceback.print_exc()
    finally:
        remove_client(conn)


def main():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", PORT))
    s.listen()
    print(f"chat server started on port {PORT} (default room #{DEFAULT_ROOM})")

    while True:
        conn, addr = s.accept()
        threading.Thread(
            target=handle_client,
            args=(conn, addr),
            daemon=True,
        ).start()


if __name__ == "__main__":
    main()
