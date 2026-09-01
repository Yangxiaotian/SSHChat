#!/usr/bin/env python3
"""
SSHChat 图形客户端：通过 SSH（与命令行相同）进入服务端强制命令聊天界面，
在窗口中显示远端输出并发送输入。需已安装 paramiko，见 requirements-gui.txt。
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import mimetypes
import os
import queue
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from collections import deque
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk
from typing import Any

try:
    import paramiko
except ImportError:
    print("请先安装图形客户端依赖: pip install -r requirements-gui.txt", file=sys.stderr)
    sys.exit(1)

from sshchat_client_util import (
    default_client_config_path,
    load_bundled_site_config,
    load_client_config,
    name_arg_completions,
    save_client_config,
)

# Strip common ANSI/OSC sequences so Tk Text stays readable (prompt_toolkit may emit CSI).
_CSI_RE = re.compile(r"\x1b\[[\d;?]*[A-Za-z]")
_OSC_RE = re.compile(r"\x1b\][^\x07]*(?:\x07|\x1b\\)")
_OTHER_ESC_RE = re.compile(r"\x1b[\][()#%][\d\"A-Za-z]*")
_PROMPT_PREFIX_RE = re.compile(r"(?:^|\s)>+\s*")
# Single space after `]` before body preserves user-leading spaces in chat.
_CHAT_LINE_RE = re.compile(r"^\[([^\]]+)\] (.*)$")
_ROOM_CHAT_LINE_RE = re.compile(r"^\[#([^\]]+)\]\s+\[([^\]]+)\] (.*)$")
_TIME_PREFIX_RE = re.compile(r"^\[\d{2}:\d{2}:\d{2}\]\s*")
_TIME_ANY_RE = re.compile(r"\[\d{2}:\d{2}:\d{2}\]\s*")
_CTRL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MANGLED_CSI_RE = re.compile(r"\?\[[\d;?]*[A-Za-z]")
_XQ_COLOR_SEG = re.compile(
    r"!([车马炮相仕帅将士象兵卒])"
    r"|\+([车马炮相仕帅将士象兵卒])"
    r"|-([车马炮相仕帅将士象兵卒])",
)
_LEADING_GARBAGE_RE = re.compile(r"^[\s\uFFFD\u25A1\uFEFF\u00A0]+")
_SPACE_RE = re.compile(r"\s+")
_GUI_OPEN_UPLOAD_RE = re.compile(
    r"^gui-open\s+upload\s+(https?://\S+)\s+([A-Z0-9]{6})\s*$",
    re.I,
)
_GUI_OPEN_CANVAS_RE = re.compile(
    r"^gui-open\s+canvas\s+(https?://\S+)\s+([A-Z0-9]{6})\s*$",
    re.I,
)
_GUI_OPEN_PIANO_RE = re.compile(
    r"^gui-open\s+piano\s+(https?://\S+)\s+([A-Z0-9]{6})\s*$",
    re.I,
)
_GUI_OPEN_DOWNLOAD_RE = re.compile(
    r"^gui-open\s+download\s+(https?://\S+)\s+([A-Z0-9]{6})\s*$",
    re.I,
)
_CANVAS_LOGICAL_W = 1200
_CANVAS_LOGICAL_H = 800
_SENDFILE_FAIL_RE = re.compile(
    r"没有其他用户|文件传输功能未启用|创建文件传输失败|"
    r"File transfer is disabled|no other users|"
    r"无效的房间名|你不在房间|房间\s+#\S+\s+不存在",
    re.I,
)
_NAMES_LINE_RE = re.compile(
    r"^\[\*]\s+#([^\s(]+)\s+\(\d+\):\s*(.*)$",
    re.I,
)


def _parse_names_line(line: str) -> tuple[str, list[str]] | None:
    t = line.strip()
    m = _NAMES_LINE_RE.match(t)
    if not m:
        return None
    room = m.group(1).strip()
    tail = m.group(2).strip()
    if not tail or tail.lower() == "(empty)":
        return room, []
    members = [x.strip() for x in tail.split(",") if x.strip()]
    return room, members
_SECURE_BANNER_START_RE = re.compile(
    r"^(=+\s*)?(共享画布|房间钢琴|文件上传信息|收到新文件|Shared\s+canvas|Room\s+piano|File\s+upload|New\s+file)",
    re.I,
)
_SECURE_BANNER_END_RE = re.compile(r"^=+")
_SECURE_URL_LABEL_RE = re.compile(
    r"(画布网址|钢琴网址|上传网址|下载网址|Canvas\s*URL|Piano\s*URL|Upload\s*URL|Download\s*URL|网址)\s*:?\s*$",
    re.I,
)
_SECURE_KEY_LINE_RE = re.compile(
    r"^(?:访问密钥|上传密钥|下载密钥|Access\s*key|Upload\s*key|Download\s*key|密钥)"
    r"\s*[:：]\s*[A-Z0-9]{6}\s*$",
    re.I,
)
_SECURE_HTTP_URL_RE = re.compile(r"^https?://\S+\s*$", re.I)
_SECURE_META_LINE_RE = re.compile(
    r"^(发起人|发件人|文件名|大小|范围|来自房间|标题|接收者|"
    r"From|Sender|Filename|Size|Room|Recipients?)\s*[:：]",
    re.I,
)
_IMAGE_MIME_RE = re.compile(r"^image/(jpeg|jpg|png|gif|webp|bmp)$", re.I)
_MAX_INLINE_IMAGE_PX = 360
_MAX_PREVIEW_SOURCE_PX = 4096
_PREVIEW_ZOOM_MIN = 0.1
_PREVIEW_ZOOM_MAX = 8.0
_MAX_ROOM_HISTORY = 4000
_DRAIN_BATCH_ITEMS = 200
_PASTE_UPLOAD_TIMEOUT_S = 45.0
_LAST_PIL_ERROR = ""

try:
    from PIL import Image

    _HAS_PIL = True
except ImportError:
    Image = None  # type: ignore[assignment, misc]
    _HAS_PIL = False


def _sniff_raster_image(data: bytes) -> bool:
    """True if bytes look like PNG/JPEG/GIF/WEBP/BMP (not HTML error pages)."""
    if not data or len(data) < 12:
        return False
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if data.startswith(b"\xff\xd8\xff"):
        return True
    if data.startswith((b"GIF87a", b"GIF89a")):
        return True
    if data.startswith(b"BM"):
        return True
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return True
    head = data.lstrip()[:64].lower()
    if head.startswith((b"<!", b"<html", b"<!doctype", b"{")):
        return False
    return False


def _path_looks_like_image(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return _sniff_raster_image(f.read(64))
    except OSError:
        return False
_TOP_COMMANDS = (
    "/help",
    "/lang",
    "/language",
    "/names",
    "/users",
    "/rooms",
    "/join",
    "/switch",
    "/part",
    "/msg",
    "/sendfile",
    "/file",
    "/canvas",
    "/board",
    "/piano",
    "/leave",
    "/unmsg",
    "/announce",
    "/game",
    "/news",
    "/library",
    "/lib",
    "/dict",
    "/clear",
    "/cls",
    "/dnd",
)

_SUBCOMMANDS_BY_CMD: dict[str, tuple[str, ...]] = {
    "/game": (
        "help",
        "list",
        "new",
        "join",
        "show",
        "move",
        "resign",
        "undo",
        "abort",
        "end",
        "on",
        "off",
        "seats",
        "rating",
        "pgn",
    ),
    "/news": ("中文", "国际", "科技", "all", "detail", "详情", "fetch", "全文"),
    "/library": (
        "open",
        "read",
        "next",
        "n",
        "prev",
        "p",
        "page",
        "find",
        "search",
        "bookmarks",
        "bookmark",
        "reset",
        "close",
        "info",
        "show",
        "help",
    ),
    "/lib": (
        "open",
        "read",
        "next",
        "n",
        "prev",
        "p",
        "page",
        "find",
        "search",
        "bookmarks",
        "bookmark",
        "reset",
        "close",
        "info",
        "show",
        "help",
    ),
    "/dict": ("en", "cn", "hh", "help", "英", "中", "汉"),
    "/dnd": ("on", "off"),
    "/lang": ("en", "zh", "english", "chinese", "中文", "英文"),
    "/language": ("en", "zh", "english", "chinese", "中文", "英文"),
}

_NESTED_SUBCOMMANDS: dict[tuple[str, str], tuple[str, ...]] = {
    ("/game", "undo"): ("accept", "reject", "cancel"),
}


_SUGGEST_UI_IDLE = object()


def _longest_common_prefix(values: list[str]) -> str:
    if not values:
        return ""
    prefix = values[0]
    for value in values[1:]:
        while not value.startswith(prefix):
            prefix = prefix[:-1]
            if not prefix:
                return ""
    return prefix


def _command_completions(
    text: str,
    *,
    rooms: list[str] | None = None,
    users: list[str] | None = None,
) -> list[str]:
    """Return full replacement strings for the current input prefix."""
    if not text.startswith("/"):
        return []
    if " " not in text:
        return [cmd for cmd in _TOP_COMMANDS if cmd.startswith(text)]

    parts = text.split()
    trailing_space = text.endswith(" ")
    cmd = parts[0].lower()

    if len(parts) == 1 and not trailing_space:
        return [c for c in _TOP_COMMANDS if c.startswith(parts[0])]

    if len(parts) >= 2:
        sub = parts[1].lower()
        nested = _NESTED_SUBCOMMANDS.get((cmd, sub), ())
        if nested:
            if trailing_space and len(parts) == 2:
                return [f"{parts[0]} {parts[1]} {item}" for item in nested]
            if len(parts) >= 3 and not trailing_space:
                prefix = parts[2]
                return [
                    f"{parts[0]} {parts[1]} {item}"
                    for item in nested
                    if item.startswith(prefix)
                ]
            if nested and len(parts) == 2 and not trailing_space:
                # still typing subcommand name, fall through
                pass
            elif nested:
                return []

    subs = _SUBCOMMANDS_BY_CMD.get(cmd, ())
    if subs:
        if trailing_space and len(parts) == 1:
            return [f"{parts[0]} {sub}" for sub in subs]
        if trailing_space and len(parts) == 2:
            return []
        if len(parts) >= 2 and not trailing_space:
            if len(parts) > 2:
                return []
            prefix = parts[1]
            return [f"{parts[0]} {sub}" for sub in subs if sub.startswith(prefix)]
        return []

    return name_arg_completions(text, rooms=rooms or (), users=users or ())


def _is_ssl_verify_error(exc: BaseException) -> bool:
    if isinstance(exc, ssl.SSLError):
        return True
    reason = getattr(exc, "reason", None)
    if isinstance(reason, ssl.SSLError):
        return True
    msg = str(exc)
    return "CERTIFICATE_VERIFY_FAILED" in msg or "SSL:" in msg


def _http_error_message(exc: urllib.error.HTTPError) -> str:
    """Prefer server JSON `error` over generic HTTPError text."""
    try:
        raw = exc.read().decode("utf-8", errors="replace")
    except Exception:
        raw = ""
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    if isinstance(payload, dict):
        msg = str(payload.get("error") or "").strip()
        if msg:
            return msg
    snippet = raw.strip()[:200]
    if snippet:
        return snippet
    return f"HTTP {exc.code}"


def _urlopen_read(req: urllib.request.Request, timeout: float = 120) -> bytes:
    """urlopen with default verify, then unverified fallback (frozen macOS CA gaps)."""
    strategies: list[ssl.SSLContext | None] = [None]
    if str(req.full_url).lower().startswith("https:"):
        strategies.append(ssl._create_unverified_context())
    last: BaseException | None = None
    for ctx in strategies:
        try:
            if ctx is None:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return resp.read()
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            raise RuntimeError(_http_error_message(e)) from e
        except Exception as e:
            last = e
            if ctx is None and _is_ssl_verify_error(e):
                continue
            raise
    assert last is not None
    raise last


def _upload_secure_file(url: str, key: str, path: Path) -> str:
    """POST multipart file with X-Upload-Key; return remote filename."""
    filename = path.name.replace("\\", "_").replace("/", "_")[:200] or "file"
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    data = path.read_bytes()
    boundary = f"----SSHChat{uuid.uuid4().hex}"
    body = (
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode("utf-8")
        + data
        + f"\r\n--{boundary}--\r\n".encode("utf-8")
    )
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "X-Upload-Key": key.upper(),
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    raw = _urlopen_read(req).decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    if isinstance(payload, dict) and payload.get("error"):
        raise RuntimeError(str(payload["error"]))
    if isinstance(payload, dict) and payload.get("filename"):
        return str(payload["filename"])
    return filename


def _parse_file_meta_field(body: str) -> tuple[str, str] | None:
    """Return (field, value) for file-invite meta lines."""
    t = body.strip()
    patterns = (
        (r"^(?:发起人|发件人|From|Sender)\s*[:：]\s*(.+)$", "sender"),
        (r"^(?:文件名|Filename)\s*[:：]\s*(.+)$", "filename"),
        (r"^(?:范围|来自房间|Room)\s*[:：]\s*(.+)$", "room"),
    )
    for pat, key in patterns:
        m = re.match(pat, t, re.I)
        if m:
            return key, m.group(1).strip()
    return None


def _is_secure_invite_noise(body: str) -> bool:
    """True for multi-line file/canvas invite lines that GUI clients collapse."""
    t = body.strip()
    if not t:
        return True
    if _SECURE_BANNER_START_RE.match(t) or _SECURE_BANNER_END_RE.match(t):
        return True
    if _SECURE_URL_LABEL_RE.search(t) or _SECURE_KEY_LINE_RE.match(t):
        return True
    if _SECURE_HTTP_URL_RE.match(t):
        return True
    if _GUI_OPEN_UPLOAD_RE.match(t) or _GUI_OPEN_CANVAS_RE.match(t) or _GUI_OPEN_PIANO_RE.match(t) or _GUI_OPEN_DOWNLOAD_RE.match(t):
        return True
    if re.match(r"^(说明|Instructions?)\s*:?\s*$", t, re.I):
        return True
    # File-invite instruction bullets only — do NOT match library rows like
    # "1. [EPUB] title.epub (1.2 MB)" or "2. [PDF] notes.pdf".
    if re.match(
        r"^\d+\.\s+("
        r"打开|选择|输入|上传|下载|文件只能|每个接收|图形客户端|"
        r"图片|视频|PDF|确认后再|"
        r"Enter|Open|Click|Choose|Select|Upload|Download|Preview|"
        r"This page|Each recipient|The key|Verify"
        r")",
        t,
        re.I,
    ):
        return True
    if _SECURE_META_LINE_RE.match(t):
        return True
    if t.startswith("经联邦节点"):
        return True
    if "图形客户端会折叠" in t:
        return True
    if "只能下载一次" in t or "存好之前别关" in t:
        return True
    if "网址和密钥都不同" in t or "此网址随后作废" in t:
        return True
    return False


def _download_token_from_url(url: str) -> tuple[str, str]:
    parsed = urllib.parse.urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2 or parts[0] != "download":
        raise ValueError("invalid download url")
    base = f"{parsed.scheme}://{parsed.netloc}"
    return base, parts[1]


def _fetch_secure_download(url: str, key: str) -> dict[str, Any]:
    """Exchange download key for a ticket, pull bytes locally, return media dict."""
    base, token = _download_token_from_url(url)
    tickets = _http_json(
        f"{base}/download/{token}/ticket",
        method="POST",
        headers={"Content-Type": "application/json"},
        body=json.dumps({"key": key.upper()}).encode("utf-8"),
    )
    filename = str(tickets.get("filename") or "file").strip() or "file"
    mime = str(
        tickets.get("mime")
        or mimetypes.guess_type(filename)[0]
        or "application/octet-stream"
    )
    download_rel = tickets.get("download")
    if not download_rel:
        raise RuntimeError("no download ticket")
    download_url = urllib.parse.urljoin(base + "/", str(download_rel).lstrip("/"))
    data = _urlopen_read(urllib.request.Request(download_url), timeout=180)
    suffix = Path(filename).suffix or mimetypes.guess_extension(mime) or ".bin"
    fd, tmp_name = tempfile.mkstemp(prefix="sshchat-dl-", suffix=suffix)
    os.close(fd)
    path = Path(tmp_name)
    path.write_bytes(data)
    # Prefer magic bytes over ticket mime — CF/HTML error pages often keep .jpg names.
    return {
        "_kind": "media",
        "name": filename,
        "mime": mime,
        "path": str(path),
        "size": len(data),
        "is_image": _sniff_raster_image(data),
    }


def _media_from_local_path(
    path: Path,
    *,
    display_name: str | None = None,
    sender: str | None = None,
) -> dict[str, Any]:
    """Build a media history entry from an already-local file (sender preview)."""
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(str(path))
    name = (display_name or resolved.name).replace("\\", "_").replace("/", "_")[:200] or "file"
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    # Copy into a stable temp so clipboard temps / user moves don't break history.
    suffix = Path(name).suffix or mimetypes.guess_extension(mime) or ".bin"
    fd, tmp_name = tempfile.mkstemp(prefix="sshchat-sent-", suffix=suffix)
    os.close(fd)
    dest = Path(tmp_name)
    shutil.copy2(resolved, dest)
    return {
        "_kind": "media",
        "name": name,
        "mime": mime,
        "path": str(dest),
        "size": dest.stat().st_size,
        "is_image": _path_looks_like_image(dest),
        "sender": (sender or "").strip() or None,
    }


def _prepare_tk_preview_png(path: Path, max_px: int = _MAX_INLINE_IMAGE_PX) -> Path | None:
    """
    Convert any supported image to a small RGB PNG for tk.PhotoImage.
    Avoids ImageTk/RGBA crashes that hard-abort some Windows Tk builds.
    """
    if not path.is_file():
        return None
    suffix = path.suffix.lower()
    # Native PhotoImage formats — still downscale via Pillow when available.
    if not _HAS_PIL or Image is None:
        if suffix in {".png", ".gif", ".ppm", ".pgm"}:
            return path
        return None
    try:
        with Image.open(path) as im:
            im.load()
            if getattr(im, "n_frames", 1) > 1:
                im.seek(0)
            im.info.pop("icc_profile", None)
            if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
                bg = Image.new("RGB", im.size, (255, 255, 255))
                rgba = im.convert("RGBA")
                bg.paste(rgba, mask=rgba.split()[-1])
                rgb = bg
            else:
                rgb = im.convert("RGB")
            rgb.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
            fd, tmp_name = tempfile.mkstemp(prefix="sshchat-prev-", suffix=".png")
            os.close(fd)
            out = Path(tmp_name)
            rgb.save(out, format="PNG", optimize=True)
            return out
    except Exception:
        return None


def _http_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=body,
        method=method.upper(),
        headers=headers or {},
    )
    try:
        raw = _urlopen_read(req, timeout=60).decode("utf-8", errors="replace")
    except RuntimeError:
        raise
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        return {}
    if payload.get("error"):
        raise RuntimeError(str(payload["error"]))
    return payload


def _is_dns_error(exc: BaseException) -> bool:
    cur: BaseException | None = exc
    seen: set[int] = set()
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, socket.gaierror):
            return True
        if isinstance(cur, OSError) and getattr(cur, "errno", None) in (8, -2, -3):
            return True
        nxt = getattr(cur, "__cause__", None) or getattr(cur, "reason", None)
        cur = nxt if isinstance(nxt, BaseException) else None
    text = str(exc).lower()
    return any(
        s in text
        for s in (
            "nodename nor servname",
            "name or service not known",
            "getaddrinfo failed",
            "unknown host",
        )
    )


def _http_base_candidates(url: str, fallback_host: str) -> list[str]:
    """Ordered base URLs to reach file/canvas HTTP when the server-advertised host fails."""
    parsed = urllib.parse.urlparse(url.strip())
    scheme = parsed.scheme or "https"
    orig_host = (parsed.hostname or "").strip()
    orig_port = parsed.port
    fb = (fallback_host or "").strip()

    def _make_base(sch: str, host: str, port: int | None) -> str:
        host = host.strip().strip("[]")
        if not host:
            return ""
        if port and not (
            (sch == "http" and port == 80) or (sch == "https" and port == 443)
        ):
            return f"{sch}://{host}:{port}"
        return f"{sch}://{host}"

    out: list[str] = []
    seen: set[str] = set()

    def add(sch: str, host: str, port: int | None) -> None:
        base = _make_base(sch, host, port)
        if base and base not in seen:
            seen.add(base)
            out.append(base)

    add(scheme, orig_host, orig_port)
    if fb and fb.lower() != orig_host.lower():
        add(scheme, fb, orig_port)
        # Cloudflare links use https:443; LAN clients often need the local HTTP port.
        if scheme == "https" and orig_port in (None, 443):
            add("http", fb, 8443)
            add("https", fb, 8443)
        elif orig_port not in (None, 80, 443):
            add("http", fb, orig_port)

    if not out:
        raise ValueError("invalid http url")
    return out


def _canvas_token_from_url(url: str) -> tuple[str, str]:
    parsed = urllib.parse.urlparse(url.strip())
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2 or parts[0] != "canvas":
        raise ValueError("invalid canvas url")
    bases = _http_base_candidates(url, "")
    return bases[0], parts[1]


def _pil_rgb_image(path: Path, max_px: int = _MAX_PREVIEW_SOURCE_PX) -> Any | None:
    """Load image as RGB PIL.Image, capped on longest side. None if unavailable."""
    global _LAST_PIL_ERROR
    _LAST_PIL_ERROR = ""
    if not _HAS_PIL or Image is None:
        _LAST_PIL_ERROR = "未包含 Pillow"
        return None
    if not path.is_file():
        _LAST_PIL_ERROR = "本地文件不存在"
        return None
    try:
        with open(path, "rb") as f:
            head = f.read(64)
        if not _sniff_raster_image(head):
            if head.lstrip()[:1] in (b"<", b"{") or head.lstrip().lower().startswith(
                (b"<!doctype", b"<html")
            ):
                _LAST_PIL_ERROR = "内容不是图片（可能是网页/错误页）"
            else:
                _LAST_PIL_ERROR = "无法识别的图片格式"
            return None
        with Image.open(path) as im:
            im.load()
            if getattr(im, "n_frames", 1) > 1:
                im.seek(0)
            # Drop ICC — broken imagingcms in some frozen builds aborts convert.
            im.info.pop("icc_profile", None)
            if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
                bg = Image.new("RGB", im.size, (255, 255, 255))
                rgba = im.convert("RGBA")
                bg.paste(rgba, mask=rgba.split()[-1])
                rgb = bg
            else:
                rgb = im.convert("RGB")
            if max(rgb.size) > max_px:
                rgb = rgb.copy()
                rgb.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
            else:
                rgb = rgb.copy()
            return rgb
    except Exception as e:
        _LAST_PIL_ERROR = str(e).strip() or type(e).__name__
        return None


def _pil_to_photoimage(im: Any) -> tk.PhotoImage | None:
    """Encode a PIL RGB image as tk.PhotoImage via PNG base64 (no ImageTk)."""
    if im is None or not _HAS_PIL or Image is None:
        return None
    try:
        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return tk.PhotoImage(data=b64)
    except Exception:
        return None


class _HoverTip:
    """Compact tooltip for icon toolbar buttons."""

    def __init__(self, widget: tk.Misc, text: str = "") -> None:
        self.widget = widget
        self.text = text
        self._tip: tk.Toplevel | None = None
        self._after: str | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def set_text(self, text: str) -> None:
        self.text = text

    def _schedule(self, _event=None) -> None:
        self._cancel()
        try:
            self._after = self.widget.after(400, self._show)
        except tk.TclError:
            self._after = None

    def _cancel(self) -> None:
        if self._after is not None:
            try:
                self.widget.after_cancel(self._after)
            except (tk.TclError, ValueError):
                pass
            self._after = None

    def _show(self) -> None:
        self._after = None
        if not self.text or self._tip is not None:
            return
        try:
            if not self.widget.winfo_ismapped():
                return
            x = self.widget.winfo_rootx() + 8
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        except tk.TclError:
            return
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x}+{y}")
        try:
            tip.attributes("-topmost", True)
        except tk.TclError:
            pass
        lbl = tk.Label(
            tip,
            text=self.text,
            justify=tk.LEFT,
            background="#ffffe0",
            foreground="#222",
            relief=tk.SOLID,
            borderwidth=1,
            padx=6,
            pady=3,
            font=("TkDefaultFont", 10),
        )
        lbl.pack()
        self._tip = tip

    def _hide(self, _event=None) -> None:
        self._cancel()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None


def _place_toplevel_near_widget(
    win: tk.Toplevel,
    anchor: tk.Misc,
    *,
    gap: int = 4,
    align: str = "left",
) -> None:
    """Place a Toplevel just below *anchor* in screen coordinates."""
    try:
        win.update_idletasks()
        anchor.update_idletasks()
        ax = anchor.winfo_rootx()
        ay = anchor.winfo_rooty()
        aw = max(anchor.winfo_width(), 1)
        ah = max(anchor.winfo_height(), 1)
        ww = max(win.winfo_reqwidth(), 1)
        wh = max(win.winfo_reqheight(), 1)
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
    except tk.TclError:
        return

    if align == "right":
        x = ax + aw - ww
    elif align == "center":
        x = ax + (aw - ww) // 2
    else:
        x = ax

    y = ay + ah + gap
    if y + wh > sh:
        y = max(0, ay - wh - gap)

    x = max(0, min(x, sw - ww))
    y = max(0, min(y, sh - wh))
    win.geometry(f"+{x}+{y}")


class ImagePreviewWindow:
    """Zoomable image preview in a Toplevel (Canvas + scrollbars, never Text embed)."""

    def __init__(
        self,
        master: tk.Misc,
        path: Path,
        name: str,
        *,
        on_save: Any,
        on_close: Any = None,
    ) -> None:
        self.path = path
        self.name = name
        self.on_save = on_save
        self._on_close = on_close
        self._pil = _pil_rgb_image(path)
        if self._pil is None:
            raise RuntimeError(_LAST_PIL_ERROR or "无法解码图片")
        self._scale = 1.0
        self._photo: tk.PhotoImage | None = None
        self._photo_refs: list[Any] = []
        self._fit_pending = True

        self.win = tk.Toplevel(master)
        self.win.title(f"预览 — {name}")
        self.win.geometry("720x560")
        self.win.minsize(360, 280)
        self.win.transient(master)
        self.win.protocol("WM_DELETE_WINDOW", self.close)

        bar = ttk.Frame(self.win)
        bar.pack(fill=tk.X, padx=8, pady=6)
        ttk.Button(bar, text="－", width=3, command=lambda: self._zoom_by(1 / 1.25)).pack(
            side=tk.LEFT
        )
        ttk.Button(bar, text="＋", width=3, command=lambda: self._zoom_by(1.25)).pack(
            side=tk.LEFT, padx=(4, 0)
        )
        ttk.Button(bar, text="适应窗口", command=self._fit_to_window).pack(
            side=tk.LEFT, padx=(8, 0)
        )
        ttk.Button(bar, text="100%", command=lambda: self._set_scale(1.0)).pack(
            side=tk.LEFT, padx=(4, 0)
        )
        self.var_zoom = tk.StringVar(value="100%")
        ttk.Label(bar, textvariable=self.var_zoom).pack(side=tk.LEFT, padx=(10, 0))
        ttk.Button(bar, text="另存为", command=lambda: self.on_save(self.path, self.name)).pack(
            side=tk.RIGHT
        )
        ttk.Label(bar, text="滚轮缩放 · 拖拽平移").pack(side=tk.RIGHT, padx=(0, 12))

        body = ttk.Frame(self.win)
        body.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        self.canvas = tk.Canvas(body, bg="#2b2b2b", highlightthickness=0)
        self._scroll_y = ttk.Scrollbar(body, orient=tk.VERTICAL, command=self.canvas.yview)
        self._scroll_x = ttk.Scrollbar(body, orient=tk.HORIZONTAL, command=self.canvas.xview)
        self.canvas.configure(
            xscrollcommand=self._scroll_x.set,
            yscrollcommand=self._scroll_y.set,
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self._scroll_y.grid(row=0, column=1, sticky="ns")
        self._scroll_x.grid(row=1, column=0, sticky="ew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        self._drag_last: tuple[int, int] | None = None
        self.canvas.bind("<ButtonPress-1>", self._on_drag_start)
        self.canvas.bind("<B1-Motion>", self._on_drag_move)
        self.canvas.bind("<ButtonRelease-1>", self._on_drag_end)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        # Also catch wheel when the window has focus but pointer is over chrome.
        self.win.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Button-4>", lambda e: self._zoom_at(1.25, e.x, e.y))
        self.canvas.bind("<Button-5>", lambda e: self._zoom_at(1 / 1.25, e.x, e.y))
        self.win.bind("<Button-4>", lambda e: self._zoom_by(1.25))
        self.win.bind("<Button-5>", lambda e: self._zoom_by(1 / 1.25))
        self.win.bind("<plus>", lambda _e: self._zoom_by(1.25))
        self.win.bind("<minus>", lambda _e: self._zoom_by(1 / 1.25))
        self.win.bind("<equal>", lambda _e: self._zoom_by(1.25))
        self.win.bind("<KP_Add>", lambda _e: self._zoom_by(1.25))
        self.win.bind("<KP_Subtract>", lambda _e: self._zoom_by(1 / 1.25))
        self.win.bind("<Configure>", self._on_configure, add="+")
        self.canvas.bind("<Configure>", self._on_canvas_configure, add="+")

        self._render()

    def close(self) -> None:
        try:
            self.win.destroy()
        except tk.TclError:
            pass
        cb = self._on_close
        if cb is not None:
            try:
                cb()
            except Exception:
                pass

    def _on_configure(self, _event=None) -> None:
        return

    def _on_canvas_configure(self, _event=None) -> None:
        if self._fit_pending and self.canvas.winfo_width() > 40:
            self._fit_pending = False
            self._fit_to_window()

    def _on_drag_start(self, event) -> None:
        self._drag_last = (event.x, event.y)
        self.canvas.scan_mark(event.x, event.y)

    def _on_drag_move(self, event) -> None:
        if self._drag_last is None:
            return
        self.canvas.scan_dragto(event.x, event.y, gain=1)

    def _on_drag_end(self, _event) -> None:
        self._drag_last = None

    def _on_mousewheel(self, event) -> None:
        # Windows / macOS: event.delta; zoom toward cursor.
        delta = getattr(event, "delta", 0) or 0
        if delta == 0:
            return
        factor = 1.25 if delta > 0 else 1 / 1.25
        self._zoom_at(factor, event.x, event.y)

    def _zoom_by(self, factor: float) -> None:
        cw = max(self.canvas.winfo_width(), 1)
        ch = max(self.canvas.winfo_height(), 1)
        self._zoom_at(factor, cw // 2, ch // 2)

    def _set_scale(self, scale: float) -> None:
        self._scale = max(_PREVIEW_ZOOM_MIN, min(_PREVIEW_ZOOM_MAX, float(scale)))
        self._render()

    def _fit_to_window(self) -> None:
        cw = max(self.canvas.winfo_width() - 8, 40)
        ch = max(self.canvas.winfo_height() - 8, 40)
        iw, ih = self._pil.size
        if iw < 1 or ih < 1:
            return
        scale = min(cw / iw, ch / ih, 1.0)
        self._set_scale(scale)

    def _zoom_at(self, factor: float, canvas_x: int, canvas_y: int) -> None:
        old = self._scale
        new = max(_PREVIEW_ZOOM_MIN, min(_PREVIEW_ZOOM_MAX, old * factor))
        if abs(new - old) < 1e-6:
            return
        try:
            ix = float(self.canvas.canvasx(canvas_x))
            iy = float(self.canvas.canvasy(canvas_y))
        except tk.TclError:
            ix = float(canvas_x)
            iy = float(canvas_y)
        self._scale = new
        self._render()
        try:
            ratio = new / old
            tw = max(1, int(round(self._pil.size[0] * self._scale)))
            th = max(1, int(round(self._pil.size[1] * self._scale)))
            nx, ny = ix * ratio, iy * ratio
            left = (nx - canvas_x) / tw
            top = (ny - canvas_y) / th
            self.canvas.xview_moveto(max(0.0, min(1.0, left)))
            self.canvas.yview_moveto(max(0.0, min(1.0, top)))
        except tk.TclError:
            pass

    def _render(self) -> None:
        iw, ih = self._pil.size
        tw = max(1, int(round(iw * self._scale)))
        th = max(1, int(round(ih * self._scale)))
        try:
            if tw == iw and th == ih:
                resized = self._pil
            else:
                resized = self._pil.resize((tw, th), Image.Resampling.LANCZOS)
            photo = _pil_to_photoimage(resized)
        except Exception:
            photo = None
        if photo is None:
            return
        self._photo = photo
        self._photo_refs.append(photo)
        # Cap refs so long zoom sessions don't leak forever.
        if len(self._photo_refs) > 8:
            self._photo_refs = self._photo_refs[-4:]
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor=tk.NW, image=photo)
        self.canvas.configure(scrollregion=(0, 0, tw, th))
        self.var_zoom.set(f"{int(round(self._scale * 100))}%")


def _chromium_app_binaries() -> list[str]:
    """Candidate Chromium-based browsers that support --app= and --start-maximized."""
    if sys.platform == "darwin":
        return [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            "/Applications/Arc.app/Contents/MacOS/Arc",
        ]
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", "")
        pf = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        pf86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        return [
            os.path.join(local, "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(pf, "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(pf86, "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(local, "Microsoft", "Edge", "Application", "msedge.exe"),
            os.path.join(pf, "Microsoft", "Edge", "Application", "msedge.exe"),
            shutil.which("chrome") or "",
            shutil.which("msedge") or "",
            shutil.which("chromium") or "",
        ]
    # Linux / other
    return [
        shutil.which("google-chrome") or "",
        shutil.which("google-chrome-stable") or "",
        shutil.which("chromium") or "",
        shutil.which("chromium-browser") or "",
        shutil.which("microsoft-edge") or "",
        shutil.which("brave-browser") or "",
    ]


def _open_browser_tab(url: str) -> bool:
    """Open a URL in a normal browser tab (not --app=) so file downloads work."""
    target = (url or "").strip()
    if not target:
        return False
    popen_kwargs: dict[str, Any] = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        popen_kwargs.update(_windows_hidden_subprocess_kwargs())
    else:
        popen_kwargs["start_new_session"] = True
    for binary in _chromium_app_binaries():
        if not binary or not os.path.isfile(binary):
            continue
        try:
            subprocess.Popen([binary, target], **popen_kwargs)
            return True
        except OSError:
            continue
    if sys.platform == "darwin":
        try:
            subprocess.Popen(["open", target], **popen_kwargs)
            return True
        except OSError:
            pass
    if sys.platform == "win32":
        try:
            os.startfile(target)  # type: ignore[attr-defined]
            return True
        except OSError:
            pass
    return False


def _piano_auth_trampoline_url(piano_url: str, boot: dict[str, Any]) -> str:
    """Local HTML trampoline → piano page with #boot= session (key never in URL)."""
    boot_json = json.dumps(boot, separators=(",", ":"), ensure_ascii=False)
    fragment = "boot=" + urllib.parse.quote(boot_json, safe="")
    target = f"{piano_url.rstrip('/')}#{fragment}"
    fd, path = tempfile.mkstemp(prefix="sshchat-piano-", suffix=".html")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(
            "<!DOCTYPE html><meta charset=utf-8>"
            f"<script>location.replace({json.dumps(target)});</script>"
            "<p>Opening piano…</p>\n"
        )
    return Path(path).resolve().as_uri()


def _piano_handoff_unavailable(err: BaseException) -> bool:
    msg = str(err).strip()
    return msg in ("网址无效", "handoff failed", "not found") or msg.startswith("HTTP 404")


def _piano_http_retryable(err: BaseException) -> bool:
    if _is_dns_error(err):
        return True
    msg = str(err).strip().lower()
    return "nodename nor servname" in msg or "getaddrinfo failed" in msg


def _piano_open_url(url: str, key: str, *, fallback_host: str = "") -> str:
    """Return a browser URL to open piano without putting the access key in the link."""
    parsed = urllib.parse.urlparse(url.strip())
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2 or parts[0] != "piano":
        raise ValueError("invalid piano url")
    token = parts[1]
    key_clean = str(key or "").strip().upper()
    if len(key_clean) != 6:
        raise ValueError("invalid piano key")
    last_err = "无法连接钢琴服务"
    for base in _http_base_candidates(url, fallback_host):
        piano_url = f"{base}/piano/{token}"
        handoff_err: BaseException | None = None
        # Prefer one-time handoff (new servers).
        try:
            data = _http_json(
                f"{base}/piano/{token}/handoff",
                method="POST",
                headers={"Content-Type": "application/json"},
                body=json.dumps({"key": key_clean}).encode("utf-8"),
            )
            code = str(data.get("handoff") or "").strip()
            if code:
                safe_code = urllib.parse.quote(code, safe="")
                return f"{piano_url}/open/{safe_code}"
        except Exception as e:
            handoff_err = e
            if _piano_http_retryable(e):
                last_err = str(e)
                continue
            if not _piano_handoff_unavailable(e):
                last_err = str(e)
                continue
        # Fallback: auth in Tk, pass short-lived ticket via #boot= fragment (not the key).
        try:
            auth = _http_json(
                f"{base}/piano/{token}/auth",
                method="POST",
                headers={"Content-Type": "application/json"},
                body=json.dumps({"key": key_clean}).encode("utf-8"),
            )
            ticket = str(auth.get("ticket") or "").strip()
            if not ticket:
                raise RuntimeError("auth failed")
            boot = {
                "ticket": ticket,
                "participant": auth.get("participant") or "",
                "room": auth.get("room") or "",
                "expires": auth.get("expires") or 0,
            }
            return _piano_auth_trampoline_url(piano_url, boot)
        except Exception as e:
            if handoff_err and _piano_handoff_unavailable(handoff_err):
                last_err = str(e)
            else:
                last_err = str(e)
            if _piano_http_retryable(e):
                continue
    raise RuntimeError(last_err)


def _open_canvas_app_window(url: str, *, maximized: bool = True) -> bool:
    """Open Excalidraw in a dedicated Chromium app window (optionally maximized)."""
    target = (url or "").strip()
    if not target:
        return False
    # Chromium --app= often drops #fragments. Launch via a tiny file:// trampoline
    # that location.replace()'s to the real URL so #k=XXXXXX survives.
    launch = target
    trampoline_path: str | None = None
    if "#" in target:
        try:
            fd, trampoline_path = tempfile.mkstemp(
                prefix="sshchat-canvas-", suffix=".html"
            )
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(
                    "<!DOCTYPE html><meta charset=utf-8>"
                    f"<script>location.replace({json.dumps(target)});</script>"
                    "<p>Opening SSHChat canvas…</p>\n"
                )
            launch = Path(trampoline_path).resolve().as_uri()
        except OSError:
            trampoline_path = None
            launch = target
    for binary in _chromium_app_binaries():
        if not binary or not os.path.isfile(binary):
            continue
        args = [binary, f"--app={launch}"]
        if maximized:
            # Prefer true fullscreen fill; fall back still works if unsupported.
            args.extend(["--start-fullscreen", "--start-maximized"])
        try:
            popen_kwargs: dict[str, Any] = {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            }
            if sys.platform == "win32":
                popen_kwargs.update(_windows_hidden_subprocess_kwargs())
            else:
                popen_kwargs["start_new_session"] = True
            subprocess.Popen(args, **popen_kwargs)
            return True
        except OSError:
            continue
    if sys.platform == "darwin":
        # Safari / default handler — pass the real URL (with hash), not file://.
        try:
            subprocess.Popen(
                ["open", target],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except OSError:
            pass
    if sys.platform == "win32":
        # Last resort: default browser via os.startfile (no console).
        try:
            os.startfile(target)  # type: ignore[attr-defined]
            return True
        except OSError:
            pass
    return False


class NativeCanvasWindow:
    """Tk canvas client talking to the same /canvas HTTP API as the web page."""

    def __init__(
        self,
        master: tk.Misc,
        url: str,
        key: str,
        *,
        reachability_host: str = "",
    ) -> None:
        self.master = master
        self.url = url
        self.key = key.strip().upper()
        self.reachability_host = reachability_host.strip()
        parsed = urllib.parse.urlparse(url.strip())
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) < 2 or parts[0] != "canvas":
            raise ValueError("invalid canvas url")
        self.token = parts[1]
        self.base = ""
        self._base_candidates = _http_base_candidates(url, self.reachability_host)
        self.ticket = ""
        self.since = 0
        self.color = "#222222"
        self.width = 3.0
        self._drawing = False
        self._points: list[list[float]] = []
        self._history: list[dict[str, Any]] = []
        self._syncing = False
        self._poll_count = 0
        self._poll_id: str | int | None = None
        self._closed = False

        self.win = tk.Toplevel(master)
        self.win.title("SSHChat 共享画布")
        self.win.geometry("900x640")
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self.win.bind("<FocusIn>", lambda _e: self._on_focus())
        self.win.bind("<Map>", lambda _e: self._on_focus())

        bar = ttk.Frame(self.win)
        bar.pack(fill=tk.X, padx=8, pady=6)
        self.var_status = tk.StringVar(value="正在进入画布…")
        ttk.Label(bar, textvariable=self.var_status).pack(side=tk.LEFT)
        self._maximized = False
        self._btn_max = ttk.Button(bar, text="最大化", command=self._toggle_maximize)
        self._btn_max.pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(bar, text="清空", command=self._clear).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(bar, text="关闭", command=self.close).pack(side=tk.RIGHT)

        tools = ttk.Frame(self.win)
        tools.pack(fill=tk.X, padx=8, pady=(0, 4))
        ttk.Label(tools, text="颜色").pack(side=tk.LEFT)
        self.var_color = tk.StringVar(value=self.color)
        ttk.Entry(tools, textvariable=self.var_color, width=10).pack(side=tk.LEFT, padx=(4, 12))
        ttk.Label(tools, text="粗细").pack(side=tk.LEFT)
        self.var_width = tk.StringVar(value="3")
        ttk.Entry(tools, textvariable=self.var_width, width=4).pack(side=tk.LEFT, padx=(4, 0))

        self.canvas = tk.Canvas(self.win, bg="#f7f3ea", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        self.canvas.bind("<ButtonPress-1>", self._on_down)
        self.canvas.bind("<B1-Motion>", self._on_move)
        self.canvas.bind("<ButtonRelease-1>", self._on_up)

        threading.Thread(target=self._auth_worker, daemon=True).start()

    def _toggle_maximize(self) -> None:
        self._maximized = not self._maximized
        try:
            self.win.attributes("-fullscreen", self._maximized)
        except tk.TclError:
            try:
                self.win.state("zoomed" if self._maximized else "normal")
            except tk.TclError:
                if self._maximized:
                    sw = self.win.winfo_screenwidth()
                    sh = self.win.winfo_screenheight()
                    self.win.geometry(f"{sw}x{sh}+0+0")
                else:
                    self.win.geometry("900x640")
        try:
            self._btn_max.configure(text="还原" if self._maximized else "最大化")
        except tk.TclError:
            pass

    def close(self) -> None:
        self._closed = True
        if self._poll_id is not None:
            try:
                self.win.after_cancel(self._poll_id)
            except tk.TclError:
                pass
            self._poll_id = None
        try:
            self.win.destroy()
        except tk.TclError:
            pass

    def _view_size(self) -> tuple[float, float]:
        w = max(1.0, float(self.canvas.winfo_width()))
        h = max(1.0, float(self.canvas.winfo_height()))
        return w, h

    def _to_logical(self, x: float, y: float) -> list[float]:
        vw, vh = self._view_size()
        lx = max(0.0, min(float(_CANVAS_LOGICAL_W), x * _CANVAS_LOGICAL_W / vw))
        ly = max(0.0, min(float(_CANVAS_LOGICAL_H), y * _CANVAS_LOGICAL_H / vh))
        return [round(lx, 2), round(ly, 2)]

    def _to_view(self, x: float, y: float) -> tuple[float, float]:
        vw, vh = self._view_size()
        return x * vw / _CANVAS_LOGICAL_W, y * vh / _CANVAS_LOGICAL_H

    def _draw_stroke(self, points: list[list[float]], color: str, width: float) -> None:
        if not points:
            return
        coords: list[float] = []
        for pt in points:
            vx, vy = self._to_view(float(pt[0]), float(pt[1]))
            coords.extend((vx, vy))
        if len(points) == 1:
            vx, vy = self._to_view(float(points[0][0]), float(points[0][1]))
            coords.extend((vx + 0.5, vy))
        self.canvas.create_line(
            *coords,
            fill=color or "#222222",
            width=max(1.0, float(width or 3)),
            capstyle=tk.ROUND,
            joinstyle=tk.ROUND,
            smooth=True,
        )

    def _auth_worker(self) -> None:
        err: str | None = None
        ticket = ""
        meta = ""
        base = ""
        for candidate in self._base_candidates:
            try:
                data = _http_json(
                    f"{candidate}/canvas/{self.token}/auth",
                    method="POST",
                    headers={"Content-Type": "application/json"},
                    body=json.dumps({"key": self.key}).encode("utf-8"),
                )
                ticket = str(data.get("ticket") or "")
                bits = []
                if data.get("participant"):
                    bits.append(f"你: {data['participant']}")
                if data.get("room"):
                    bits.append(f"房间: #{data['room']}")
                meta = " · ".join(bits)
                if not ticket:
                    raise RuntimeError("auth failed")
                base = candidate
                err = None
                break
            except RuntimeError as e:
                err = str(e)
                break
            except Exception as e:
                err = str(e)
        self.master.after(0, lambda: self._auth_done(ticket, meta, err, base))

    def _auth_done(
        self,
        ticket: str,
        meta: str,
        err: str | None,
        base: str = "",
    ) -> None:
        if self._closed:
            return
        if err or not ticket or not base:
            self.var_status.set(f"进入失败: {err or 'unknown'}")
            return
        self.base = base
        self.ticket = ticket
        self.var_status.set(meta or "已连接")
        self._sync_once(initial=True)
        self._schedule_poll()

    def _schedule_poll(self) -> None:
        if self._closed:
            return
        self._poll_id = self.win.after(900, self._poll_tick)

    def _on_focus(self) -> None:
        if self._closed or not self.ticket:
            return
        self._replay_history()
        self._sync_once(initial=True)

    def _remember_stroke(self, ev: dict[str, Any]) -> None:
        if str(ev.get("kind") or "") != "stroke":
            return
        seq = int(ev.get("seq") or 0)
        if seq and any(int(h.get("seq") or 0) == seq for h in self._history):
            return
        self._history.append(dict(ev))

    def _replay_history(self) -> None:
        try:
            self.canvas.delete("all")
        except tk.TclError:
            return
        for ev in self._history:
            pts = ev.get("points") or []
            if isinstance(pts, list):
                self._draw_stroke(
                    [
                        [float(p[0]), float(p[1])]
                        for p in pts
                        if isinstance(p, (list, tuple)) and len(p) >= 2
                    ],
                    str(ev.get("color") or "#222"),
                    float(ev.get("width") or 3),
                )

    def _sync_worker(self, *, rebuild: bool = False) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            since = 0 if rebuild else self.since
            data = _http_json(
                f"{self.base}/canvas/{self.token}/sync"
                f"?since={since}&ticket={urllib.parse.quote(self.ticket)}",
                method="GET",
                headers={"X-Canvas-Ticket": self.ticket},
            )
            self.master.after(0, lambda: self._apply_sync(data, None, rebuild=rebuild))
        except Exception as e:
            self.master.after(0, lambda: self._apply_sync(None, str(e), rebuild=rebuild))

    def _poll_tick(self) -> None:
        self._poll_id = None
        if self._closed:
            return
        self._poll_count += 1
        rebuild = self._poll_count % 40 == 0
        threading.Thread(
            target=self._sync_worker, kwargs={"rebuild": rebuild}, daemon=True
        ).start()

    def _sync_once(self, *, initial: bool) -> None:
        threading.Thread(
            target=self._sync_worker, kwargs={"rebuild": initial}, daemon=True
        ).start()

    def _apply_sync(
        self, data: dict[str, Any] | None, err: str | None, *, rebuild: bool = False
    ) -> None:
        self._syncing = False
        if self._closed:
            return
        if err or data is None:
            self.var_status.set(f"同步出错: {err or 'unknown'}")
            self._schedule_poll()
            return
        if rebuild:
            self.since = 0
            self._history.clear()
            try:
                self.canvas.delete("all")
            except tk.TclError:
                pass
        for ev in data.get("events") or []:
            kind = str(ev.get("kind") or "")
            seq = int(ev.get("seq") or 0)
            self.since = max(self.since, seq)
            if kind == "clear":
                self._history.clear()
                try:
                    self.canvas.delete("all")
                except tk.TclError:
                    pass
            elif kind == "stroke":
                self._remember_stroke(ev if isinstance(ev, dict) else {})
                pts = ev.get("points") or []
                if isinstance(pts, list):
                    self._draw_stroke(
                        [
                            [float(p[0]), float(p[1])]
                            for p in pts
                            if isinstance(p, (list, tuple)) and len(p) >= 2
                        ],
                        str(ev.get("color") or "#222"),
                        float(ev.get("width") or 3),
                    )
        bits = []
        if data.get("participant"):
            bits.append(f"你: {data['participant']}")
        if data.get("room"):
            bits.append(f"房间: #{data['room']}")
        if bits:
            self.var_status.set(" · ".join(bits))
        self._schedule_poll()

    def _read_tools(self) -> None:
        color = self.var_color.get().strip() or "#222222"
        if not color.startswith("#"):
            color = "#222222"
        self.color = color
        try:
            self.width = max(1.0, min(32.0, float(self.var_width.get().strip() or "3")))
        except ValueError:
            self.width = 3.0

    def _on_down(self, event) -> None:
        if not self.ticket:
            return
        self._read_tools()
        self._drawing = True
        self._points = [self._to_logical(event.x, event.y)]
        self._draw_stroke(self._points, self.color, self.width)

    def _on_move(self, event) -> None:
        if not self._drawing:
            return
        pt = self._to_logical(event.x, event.y)
        prev = self._points[-1]
        self._points.append(pt)
        self._draw_stroke([prev, pt], self.color, self.width)

    def _on_up(self, _event=None) -> None:
        if not self._drawing:
            return
        self._drawing = False
        pts = list(self._points)
        self._points = []
        if not pts or not self.ticket:
            return

        def worker() -> None:
            err: str | None = None
            seq = 0
            try:
                data = _http_json(
                    f"{self.base}/canvas/{self.token}/stroke",
                    method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "X-Canvas-Ticket": self.ticket,
                    },
                    body=json.dumps(
                        {"color": self.color, "width": self.width, "points": pts}
                    ).encode("utf-8"),
                )
                seq = int((data.get("event") or {}).get("seq") or 0)
            except Exception as e:
                err = str(e)
            self.master.after(0, lambda s=seq, e=err, p=pts: self._stroke_done(s, e, p))

        threading.Thread(target=worker, daemon=True).start()

    def _stroke_done(self, seq: int, err: str | None, pts: list[list[float]] | None = None) -> None:
        if self._closed:
            return
        if err:
            self.var_status.set(f"笔画同步失败: {err}")
            return
        if seq:
            self.since = max(self.since, seq)
            self._remember_stroke(
                {
                    "seq": seq,
                    "kind": "stroke",
                    "color": self.color,
                    "width": self.width,
                    "points": list(pts or []),
                }
            )

    def _clear(self) -> None:
        if not self.ticket:
            return
        if not messagebox.askyesno("SSHChat", "确定清空共享画布？（所有人都会清空）"):
            return

        def worker() -> None:
            err: str | None = None
            seq = 0
            try:
                data = _http_json(
                    f"{self.base}/canvas/{self.token}/clear",
                    method="POST",
                    headers={"X-Canvas-Ticket": self.ticket},
                )
                seq = int((data.get("event") or {}).get("seq") or 0)
            except Exception as e:
                err = str(e)
            self.master.after(0, lambda: self._clear_done(seq, err))

        threading.Thread(target=worker, daemon=True).start()

    def _clear_done(self, seq: int, err: str | None) -> None:
        if self._closed:
            return
        if err:
            self.var_status.set(f"清空失败: {err}")
            return
        self._history.clear()
        self.canvas.delete("all")
        if seq:
            self.since = max(self.since, seq)


def _clipboard_existing_path(root: tk.Misc) -> Path | None:
    try:
        text = str(root.clipboard_get() or "").strip().strip('"')
    except tk.TclError:
        text = ""
    if text:
        if text.startswith("file:"):
            parsed = urllib.parse.urlparse(text)
            candidate = Path(urllib.parse.unquote(parsed.path))
        else:
            candidate = Path(text).expanduser()
        try:
            if candidate.is_file():
                return candidate.resolve()
        except OSError:
            pass
    if sys.platform == "darwin":
        try:
            out = subprocess.check_output(
                ["osascript", "-e", "POSIX path of (the clipboard as «class furl»)"],
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=2,
            ).strip()
            candidate = Path(out)
            if candidate.is_file():
                return candidate.resolve()
        except (subprocess.SubprocessError, OSError):
            pass
    return None


def _temp_clip_png() -> Path:
    fd, name = tempfile.mkstemp(prefix="sshchat-clip-", suffix=".png")
    os.close(fd)
    return Path(name)


def _mac_clipboard_image_file() -> Path | None:
    """Write PNG from macOS clipboard to a temp file, if present."""
    if sys.platform != "darwin":
        return None
    out = _temp_clip_png()
    script = (
        f'set outPath to "{out}"\n'
        "try\n"
        "  set pngData to the clipboard as «class PNGf»\n"
        "  set f to open for access POSIX file outPath with write permission\n"
        "  set eof f to 0\n"
        "  write pngData to f\n"
        "  close access f\n"
        "  return outPath\n"
        "on error\n"
        "  try\n"
        "    close access POSIX file outPath\n"
        "  end try\n"
        '  return ""\n'
        "end try"
    )
    try:
        got = subprocess.check_output(
            ["osascript", "-e", script],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        ).strip()
        if got and Path(got).is_file() and Path(got).stat().st_size > 0:
            return Path(got)
    except (subprocess.SubprocessError, OSError):
        pass
    try:
        out.unlink(missing_ok=True)
    except OSError:
        pass
    return None


def _windows_hidden_subprocess_kwargs() -> dict[str, Any]:
    """Avoid flashing a console window when spawning helpers on Windows."""
    if sys.platform != "win32":
        return {}
    kwargs: dict[str, Any] = {}
    # CREATE_NO_WINDOW = 0x08000000 (Python 3.7+)
    create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    kwargs["creationflags"] = create_no_window
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        kwargs["startupinfo"] = si
    except (AttributeError, OSError):
        pass
    return kwargs


def _windows_clipboard_image_file() -> Path | None:
    """Write PNG from Windows clipboard (screenshot / copied image) to a temp file."""
    if sys.platform != "win32":
        return None
    # Prefer Pillow: no child process, no console flash on Ctrl+V.
    try:
        from PIL import ImageGrab

        grabbed = ImageGrab.grabclipboard()
        if grabbed is not None:
            out = _temp_clip_png()
            try:
                # ImageGrab may return a list of file paths for copied files.
                if isinstance(grabbed, list):
                    for item in grabbed:
                        candidate = Path(str(item))
                        if candidate.is_file():
                            return candidate.resolve()
                    return None
                grabbed.save(out, format="PNG")
                if out.is_file() and out.stat().st_size > 0:
                    return out
            except OSError:
                try:
                    out.unlink(missing_ok=True)
                except OSError:
                    pass
    except Exception:
        pass

    out = _temp_clip_png()
    # Escape for PowerShell single-quoted string ('' = literal ')
    ps_path = str(out).replace("'", "''")
    script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "Add-Type -AssemblyName System.Drawing; "
        "$img = [System.Windows.Forms.Clipboard]::GetImage(); "
        "if ($null -eq $img) { exit 2 }; "
        f"$img.Save('{ps_path}', [System.Drawing.Imaging.ImageFormat]::Png)"
    )
    try:
        r = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-WindowStyle",
                "Hidden",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            timeout=15,
            check=False,
            **_windows_hidden_subprocess_kwargs(),
        )
        if r.returncode == 0 and out.is_file() and out.stat().st_size > 0:
            return out
    except (subprocess.SubprocessError, OSError):
        pass
    try:
        out.unlink(missing_ok=True)
    except OSError:
        pass
    return None


def _linux_clipboard_image_file() -> Path | None:
    """Write PNG from Wayland/X11 clipboard to a temp file, if tools exist."""
    if sys.platform.startswith("win") or sys.platform == "darwin":
        return None
    out = _temp_clip_png()
    cmds: list[list[str]] = [
        ["wl-paste", "--type", "image/png"],
        ["xclip", "-selection", "clipboard", "-t", "image/png", "-o"],
    ]
    for cmd in cmds:
        try:
            with open(out, "wb") as f:
                r = subprocess.run(
                    cmd,
                    stdout=f,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    check=False,
                )
            if r.returncode == 0 and out.is_file() and out.stat().st_size > 0:
                return out
        except (subprocess.SubprocessError, OSError, FileNotFoundError):
            continue
    try:
        out.unlink(missing_ok=True)
    except OSError:
        pass
    return None


def _clipboard_image_file() -> Path | None:
    """Platform clipboard bitmap → temp PNG (for paste-to-/sendfile)."""
    if sys.platform == "darwin":
        return _mac_clipboard_image_file()
    if sys.platform == "win32":
        return _windows_clipboard_image_file()
    return _linux_clipboard_image_file()


def _clean_chunk(s: str) -> str:
    s = _OSC_RE.sub("", s)
    s = _CSI_RE.sub("", s)
    s = _MANGLED_CSI_RE.sub("", s)
    s = _OTHER_ESC_RE.sub("", s)
    s = _CTRL_CHARS_RE.sub("", s)
    # Normalize lone CR from PTY without merging unrelated lines.
    s = s.replace("\r\n", "\n").replace("\r", "")
    return s


def _skip_line(line: str) -> bool:
    t = line.strip()
    if not t:
        return False
    if t.startswith("?[") and (t.endswith("A") or t.endswith("K")):
        return True
    if t.startswith("WARNING: your terminal doesn't support cursor position requests"):
        return True
    # prompt_toolkit redraw / local tty echo, not chat content.
    if t == ">" or t.startswith("> "):
        return True
    return False


def _strip_time_prefixes(line: str) -> str:
    out = line
    while True:
        nxt = _TIME_PREFIX_RE.sub("", out, count=1)
        if nxt == out:
            return out
        out = nxt.lstrip()


def _parse_chat_line(line: str) -> tuple[str, str, str] | None:
    t = _LEADING_GARBAGE_RE.sub("", line.rstrip("\n"))
    t = _TIME_ANY_RE.sub("", t)
    t = _PROMPT_PREFIX_RE.sub("", t).strip()
    m_room = _ROOM_CHAT_LINE_RE.match(t)
    if m_room:
        return m_room.group(1), m_room.group(2), m_room.group(3)
    m = _CHAT_LINE_RE.match(t)
    if not m:
        # PTY line-wrap sometimes drops the opening "[" from "[+] …" join notices.
        m_join = re.match(r"^\+?\] (.+)$", t)
        if m_join:
            return "", "+", m_join.group(1)
        all_matches = re.findall(r"\[([^\]]+)\] ([^\[]*)", t)
        if all_matches:
            sender, body = all_matches[-1]
            return "", sender.strip(), body
        return None
    return "", m.group(1), m.group(2)


class SSHChatGUI:
    def __init__(self, config_path: Path, *, force_full_ui: bool = False) -> None:
        self.config_path = config_path.expanduser().resolve()
        self._bundle = None if force_full_ui else load_bundled_site_config()
        self.root = tk.Tk()
        title = "SSHChat"
        if self._bundle:
            h = str(self._bundle["host"]).strip()
            title = f"SSHChat · {h}"
        self.root.title(title)
        self.root.minsize(520, 420)

        self._ssh: paramiko.SSHClient | None = None
        self._chan: paramiko.Channel | None = None
        self._chan_send_lock = threading.Lock()
        self._reader_thread: threading.Thread | None = None
        self._connect_thread: threading.Thread | None = None
        self._out_q: queue.Queue[tuple[int, str | None]] = queue.Queue()
        self._drain_after_id: str | int | None = None
        self._connecting = threading.Event()
        self._session_id = 0
        # SSH login name; used in reader thread — do not read StringVar there (Tk is not thread-safe).
        self._session_user = ""
        self._display_times: deque[datetime] = deque(maxlen=2048)
        self._alert_sound = (os.environ.get("SSHCHAT_ALERT_SOUND") or "auto").strip().lower()
        self._rooms_order: list[str] = ["default"]
        self._active_room = "default"
        self._room_unread: dict[str, int] = {"default": 0}
        self._room_history: dict[str, list[Any]] = {"default": []}
        self._paste_pending: dict[str, Any] | None = None
        self._pending_file_meta: dict[str, str] = {}
        self._expecting_own_canvas = False
        self._paste_timer: str | int | None = None
        self._suggest_win: tk.Misc | None = None
        self._suggest_list: tk.Listbox | None = None
        self._suggest_items: list[str] = []
        self._suggest_ui_job: str | int | None = None
        self._suggest_ui_pending: list[str] | None | object = _SUGGEST_UI_IDLE
        self._suggest_focus_job: str | int | None = None
        self._click_refresh_job: str | int | None = None
        self._room_list_refresh_job: str | int | None = None
        self._room_select_guard = False
        self._photo_refs: list[Any] = []
        self._media_save_targets: dict[str, tuple[str, str]] = {}
        self._media_preview_targets: dict[str, tuple[str, str]] = {}
        self._canvas_open_targets: dict[str, tuple[str, str]] = {}
        self._piano_open_targets: dict[str, tuple[str, str]] = {}
        self._media_tag_seq = 0
        self._preview_win: ImagePreviewWindow | None = None
        self._send_target_kind = "room"
        self._send_target_value = ""
        self._online_users: list[str] = []
        self._recent_users: list[str] = []
        self._expecting_names = False

        self._build_ui()
        self._apply_profile(load_client_config(self.config_path))
        self.root.bind("<Map>", self._on_window_mapped, add="+")
        self.root.bind("<Unmap>", self._on_window_unmapped, add="+")
        self.root.bind("<FocusIn>", self._on_root_focus_in, add="+")
        # bind_all: root-only ButtonPress never fires for ttk.Button children, so
        # orphaned suggestion chrome would keep eating clicks until the window moves.
        self.root.bind_all("<ButtonPress-1>", self._on_any_button_press, add="+")
        self.root.bind("<<Paste>>", self._on_paste_file, add="+")
        self.root.bind("<Command-v>", self._on_paste_file, add="+")
        self.root.bind("<Control-v>", self._on_paste_file, add="+")
        self._is_minimized = False

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _should_drop_line(self, line: str) -> bool:
        # Plain messages are no longer optimistically echoed locally; the server
        # broadcast (also delivered to ourselves) is the single source of truth,
        # so we only render server-formatted lines and filter PTY redraw noise.
        normalized = _strip_time_prefixes(line)
        if _skip_line(normalized):
            return True
        # prompt_toolkit's "> something" commit echo when the GUI sends a line.
        stripped = normalized.strip()
        if stripped.startswith(">"):
            tail = _PROMPT_PREFIX_RE.sub("", stripped, count=1).strip()
            if not tail or "[" not in tail:
                return True
        # Long input submitted through the remote PTY can be echoed back as
        # terminal-wrapped fragments. Real chat/server lines are bracketed
        # ([#room] [user] ..., [*] ..., [OK] ...); fragments are not.
        return _parse_chat_line(normalized.rstrip("\r")) is None

    def _build_ui(self) -> None:
        pad = {"padx": 6, "pady": 4}
        top = ttk.Frame(self.root)
        top.pack(fill=tk.X, **pad)

        bh = str(self._bundle["host"]).strip() if self._bundle else ""
        try:
            bp = int(self._bundle.get("ssh_port", 22)) if self._bundle else 22
        except (TypeError, ValueError):
            bp = 22

        ttk.Label(top, text="主机").grid(row=0, column=0, sticky="w")
        self.var_host = tk.StringVar(value=bh)
        ttk.Entry(top, textvariable=self.var_host, width=22).grid(
            row=0, column=1, sticky="ew", padx=(4, 8)
        )

        ttk.Label(top, text="用户").grid(row=0, column=2, sticky="w")
        self.var_user = tk.StringVar()
        ttk.Entry(top, textvariable=self.var_user, width=14).grid(
            row=0, column=3, sticky="ew", padx=(4, 8)
        )

        ttk.Label(top, text="SSH 端口").grid(row=0, column=4, sticky="w")
        self.var_port = tk.StringVar(value=str(bp))
        ttk.Entry(top, textvariable=self.var_port, width=6).grid(
            row=0, column=5, sticky="w", padx=(4, 8)
        )

        top.columnconfigure(1, weight=1)

        bar = ttk.Frame(self.root)
        bar.pack(fill=tk.X, padx=6, pady=(0, 4))
        self.btn_connect = ttk.Button(bar, text="连接", command=self._connect_clicked)
        self.btn_connect.pack(side=tk.LEFT)
        self.btn_disconnect = ttk.Button(
            bar, text="断开", command=self._disconnect, state=tk.DISABLED
        )
        self.btn_disconnect.pack(side=tk.LEFT, padx=(8, 0))

        self.var_status = tk.StringVar(value="就绪")
        ttk.Label(self.root, textvariable=self.var_status).pack(
            anchor="w", padx=10, pady=(0, 2)
        )

        mono = tkfont.nametofont("TkFixedFont")
        body = ttk.Frame(self.root)
        body.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 4))

        left = ttk.Frame(body)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 8))
        ttk.Label(left, text="频道").pack(anchor="w")
        self.room_list = tk.Listbox(left, width=20, height=18, exportselection=False)
        self.room_list.pack(fill=tk.Y, expand=True)
        # Aqua: <<ListboxSelect>> alone often needs a focus click first; drive
        # switches from the pointer y via nearest() so one click always works.
        self.room_list.bind("<ButtonPress-1>", lambda _e: self.room_list.focus_set(), add="+")
        self.room_list.bind("<ButtonRelease-1>", self._on_room_list_click, add="+")
        self.room_list.bind("<<ListboxSelect>>", self._on_room_selected)

        self.log = scrolledtext.ScrolledText(
            body,
            height=20,
            wrap=tk.WORD,
            font=mono,
            state=tk.DISABLED,
        )
        self.log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.log.tag_configure("meta", foreground="#7f8c8d")
        self.log.tag_configure("me", foreground="#2e7d32")
        self.log.tag_configure("peer", foreground="#1565c0")
        # Match Electron: muted gray for [*] system lines (not bright purple).
        self.log.tag_configure("system", foreground="#969696")
        self.log.tag_configure("notice", foreground="#6a737d")
        self.log.tag_configure("xq_red", foreground="#c62828")
        self.log.tag_configure("xq_black", foreground="#263238")
        self.log.tag_configure("media_save", foreground="#0b57d0", underline=True)
        self.log.tag_configure("media_preview", foreground="#0b57d0", underline=True)
        self.log.tag_configure("canvas_open", foreground="#0b57d0", underline=True)
        self.log.tag_configure("piano_open", foreground="#0b57d0", underline=True)
        self.log.bind("<<Paste>>", self._on_paste_file, add="+")
        # DISABLED Text swallows tag_bind on Aqua/Win; handle clicks at widget level.
        self.log.bind("<Button-1>", self._on_log_click, add="+")

        bot = ttk.Frame(self.root)
        bot.pack(fill=tk.X, padx=8, pady=(0, 8))
        bot.columnconfigure(0, weight=1)
        self._suggest_slot = ttk.Frame(bot)
        self._input_row = ttk.Frame(bot)
        self._input_row.grid(row=1, column=0, sticky="ew")
        self.var_input = tk.StringVar()
        self.entry = ttk.Entry(self._input_row, textvariable=self.var_input)
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.entry.bind("<Return>", self._on_entry_return)
        self.entry.bind("<Tab>", self._on_entry_tab)
        self.entry.bind("<Down>", self._on_entry_down)
        self.entry.bind("<Up>", self._on_entry_up)
        self.entry.bind("<Escape>", self._on_entry_escape)
        self.entry.bind("<KeyRelease>", self._on_entry_keyrelease, add="+")
        self.entry.bind("<FocusOut>", self._on_entry_focus_out, add="+")
        self.entry.bind("<<Paste>>", self._on_paste_file, add="+")
        self.entry.bind("<Command-v>", self._on_paste_file, add="+")
        self.entry.bind("<Control-v>", self._on_paste_file, add="+")
        self.btn_send, _ = self._pack_icon_btn(
            self._input_row, "➤", "发送", self._send_clicked
        )
        self.btn_send_target, self._send_target_tip = self._pack_icon_btn(
            self._input_row, "＠", self._send_target_label(), self._show_send_target_picker
        )
        self.btn_send_file, _ = self._pack_icon_btn(
            self._input_row, "📎", "发文件", self._pick_and_send_file
        )
        self.btn_canvas, _ = self._pack_icon_btn(
            self._input_row, "🎨", "画板", self._start_canvas
        )
        self.btn_piano, _ = self._pack_icon_btn(
            self._input_row, "🎹", "钢琴", self._start_piano
        )
        self.btn_library, _ = self._pack_icon_btn(
            self._input_row, "📚", "图书馆", self._start_library
        )
        self.btn_clear, _ = self._pack_icon_btn(
            self._input_row, "⌫", "清屏", self._clear_active_room
        )
        hint = ttk.Label(
            self.root,
            text="提示: 悬停图标看说明；「＠」可选私聊/房间；指定用户可留言或发文件",
            foreground="#666",
        )
        hint.pack(anchor="w", padx=10, pady=(0, 6))
        self._refresh_room_list()

    def _on_window_mapped(self, _event=None) -> None:
        # On some macOS/Tk builds, first paint can leave controls seemingly
        # unresponsive until a manual move/resize. Force a post-map refresh.
        self._is_minimized = False
        self.root.after_idle(self._stabilize_initial_interaction)
        self.root.after(200, self._stabilize_initial_interaction)
        self.root.after(60, self._render_active_room)
        self.root.after(60, self._render_active_room)

    def _on_window_unmapped(self, _event=None) -> None:
        try:
            self._is_minimized = self.root.state() == "iconic"
        except tk.TclError:
            self._is_minimized = False
        self._hide_suggestions()

    def _widget_in_suggestions(self, w) -> bool:
        if (
            w is self.entry
            or w is self._suggest_list
            or w is self._suggest_win
            or w is self._suggest_slot
        ):
            return True
        if self._suggest_win is None:
            return False
        try:
            return str(w).startswith(str(self._suggest_win))
        except tk.TclError:
            return False

    def _on_any_button_press(self, event) -> None:
        # Dismiss in-window completion when clicking elsewhere in the main window.
        # Ignore other Toplevels (image preview, pickers) so we don't steal their clicks.
        if self._suggest_win is None:
            return
        w = event.widget
        try:
            if w.winfo_toplevel() is not self.root:
                return
        except tk.TclError:
            return
        if self._widget_in_suggestions(w):
            return
        self._hide_suggestions()

    def _on_root_focus_in(self, event=None) -> None:
        # FocusIn propagates from children; only refresh when the toplevel activates.
        # Never pulse/lift here — that steals the click that just activated the window.
        if event is not None and event.widget is not self.root:
            return
        self._schedule_click_target_refresh(delay_ms=80)

    def _on_aux_window_closed(self) -> None:
        self._schedule_click_target_refresh(delay_ms=40)

    def _schedule_click_target_refresh(self, delay_ms: int = 0) -> None:
        if self._click_refresh_job is not None:
            try:
                self.root.after_cancel(self._click_refresh_job)
            except (tk.TclError, ValueError):
                pass

        def _run() -> None:
            self._click_refresh_job = None
            self._refresh_click_targets()

        try:
            if delay_ms <= 0:
                self._click_refresh_job = self.root.after_idle(_run)
            else:
                self._click_refresh_job = self.root.after(delay_ms, _run)
        except tk.TclError:
            pass

    def _refresh_click_targets(self) -> None:
        """Aqua keeps stale click targets until geometry actually changes.

        Re-setting the same WxH+X+Y string is often a no-op; a 1px nudge matches
        what happens when the user drags the window to make buttons work again.
        """
        try:
            self.root.update_idletasks()
            if sys.platform == "darwin":
                geo = self.root.geometry()
                m = re.fullmatch(r"(\d+x\d+)([+-]\d+)([+-]\d+)", geo)
                if m:
                    size, x, y = m.group(1), int(m.group(2)), int(m.group(3))
                    self.root.geometry(f"{size}{x + 1}{y}")
                    self.root.update_idletasks()
                    self.root.geometry(f"{size}{x}{y}")
                    return
            geom = self.root.winfo_geometry()
            self.root.geometry(geom)
        except tk.TclError:
            pass

    def _on_entry_focus_out(self, _event=None) -> None:
        # Delay so a click on the suggestion list can land first.
        if self._suggest_focus_job is not None:
            try:
                self.root.after_cancel(self._suggest_focus_job)
            except (tk.TclError, ValueError):
                pass
        self._suggest_focus_job = self.root.after(120, self._hide_suggestions_if_focus_left)

    def _hide_suggestions_if_focus_left(self) -> None:
        self._suggest_focus_job = None
        if self._suggest_win is None:
            return
        try:
            focus = self.root.focus_get()
        except tk.TclError:
            focus = None
        if focus is not None and self._widget_in_suggestions(focus):
            return
        self._hide_suggestions()

    def _stabilize_initial_interaction(self) -> None:
        try:
            self.root.update_idletasks()
        except tk.TclError:
            return
        # Avoid focus_force / lift / topmost — on Aqua they leave hit-testing
        # stale until the user manually moves the window.
        self._refresh_click_targets()
        try:
            if self.btn_connect.instate(("disabled",)):
                self.btn_connect.state(("!disabled",))
        except tk.TclError:
            pass
        try:
            if not self._chan or self._chan.closed:
                self.entry.focus_set()
        except tk.TclError:
            pass

    def _format_time(self, ts: datetime) -> str:
        return ts.strftime("%H:%M:%S")

    def _room_label(self, room: str) -> str:
        unread = self._room_unread.get(room, 0)
        active = room == self._active_room
        base = f"#{room}"
        if active:
            base = f"* {base}"
        return f"{base} ({unread})" if unread > 0 else base

    def _ensure_room(self, room: str) -> None:
        if not room:
            return
        if room not in self._rooms_order:
            self._rooms_order.append(room)
        self._room_unread.setdefault(room, 0)
        self._room_history.setdefault(room, [])

    def _refresh_room_list(self) -> None:
        if not hasattr(self, "room_list"):
            return
        labels = [self._room_label(room) for room in self._rooms_order]
        self._room_select_guard = True
        try:
            size = int(self.room_list.size())
            # Prefer in-place label updates: full delete/rebuild on Aqua eats
            # clicks that land while unread badges are changing.
            if size == len(labels):
                for i, label in enumerate(labels):
                    if self.room_list.get(i) != label:
                        self.room_list.delete(i)
                        self.room_list.insert(i, label)
            else:
                self.room_list.delete(0, tk.END)
                for label in labels:
                    self.room_list.insert(tk.END, label)
            if self._active_room in self._rooms_order:
                idx = self._rooms_order.index(self._active_room)
                self.room_list.selection_clear(0, tk.END)
                self.room_list.selection_set(idx)
                self.room_list.activate(idx)
                self.room_list.see(idx)
        except tk.TclError:
            pass
        finally:
            self._room_select_guard = False

    def _schedule_room_list_refresh(self) -> None:
        if self._room_list_refresh_job is not None:
            try:
                self.root.after_cancel(self._room_list_refresh_job)
            except (tk.TclError, ValueError):
                pass
        try:
            self._room_list_refresh_job = self.root.after(40, self._flush_room_list_refresh)
        except tk.TclError:
            self._room_list_refresh_job = None

    def _flush_room_list_refresh(self) -> None:
        self._room_list_refresh_job = None
        self._refresh_room_list()
        self._schedule_click_target_refresh(delay_ms=40)

    def _render_active_room(self) -> None:
        entries = self._room_history.get(self._active_room, [])
        self.log.configure(state=tk.NORMAL)
        self.log.delete("1.0", tk.END)
        self._photo_refs.clear()
        self._media_save_targets.clear()
        self._media_preview_targets.clear()
        self._canvas_open_targets.clear()
        self._piano_open_targets.clear()
        for entry in entries:
            if isinstance(entry, dict) and entry.get("_kind") == "media":
                self._insert_media_entry(entry)
            else:
                text, tag = entry
                self._insert_log_fragment(text, tag)
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def _switch_room_local(self, room: str, *, send_switch: bool = False) -> None:
        if (
            room != self._active_room
            and self._paste_pending
            and not self._paste_pending.get("consumed")
        ):
            self._fail_paste("已切换房间")
        self._ensure_room(room)
        self._active_room = room
        self._room_unread[room] = 0
        self._refresh_room_list()
        self._render_active_room()
        if self._send_target_kind == "room":
            self._refresh_send_target_button()
        if send_switch and self._chan and not self._chan.closed:
            try:
                self._chan_send_bytes((f"/switch {room}\n").encode("utf-8"))
            except Exception:
                pass
        self._schedule_click_target_refresh(delay_ms=40)

    def _send_target_label(self) -> str:
        if self._send_target_kind == "user" and self._send_target_value:
            return f"私聊 {self._send_target_value}"
        if self._send_target_kind == "named_room" and self._send_target_value:
            return f"房间 #{self._send_target_value}"
        return f"当前房间 #{self._active_room}"

    def _sendfile_command(self) -> str:
        if self._send_target_kind == "user" and self._send_target_value:
            return f"/sendfile {self._send_target_value}"
        if self._send_target_kind == "named_room" and self._send_target_value:
            return f"/sendfile #{self._send_target_value}"
        return "/sendfile"

    def _canvas_command(self) -> str:
        if self._send_target_kind == "user" and self._send_target_value:
            return f"/canvas {self._send_target_value}"
        if self._send_target_kind == "named_room" and self._send_target_value:
            return f"/canvas #{self._send_target_value}"
        return "/canvas"

    def _piano_command(self) -> str:
        if self._send_target_kind == "user" and self._send_target_value:
            return f"/piano {self._send_target_value}"
        if self._send_target_kind == "named_room" and self._send_target_value:
            return f"/piano #{self._send_target_value}"
        return "/piano"

    def _pack_icon_btn(self, parent, icon: str, tip: str, command):
        btn = ttk.Button(parent, text=icon, width=3, command=command)
        btn.pack(side=tk.LEFT, padx=(4, 0))
        return btn, _HoverTip(btn, tip)

    def _outbound_text(self, draft: str) -> str:
        t = draft.strip()
        if t.startswith("/"):
            return draft
        if self._send_target_kind == "user" and self._send_target_value:
            return f"/msg {self._send_target_value} {draft}"
        if self._send_target_kind == "named_room" and self._send_target_value:
            return f"/msg #{self._send_target_value} {draft}"
        return draft

    def _set_send_target(self, kind: str, value: str = "") -> None:
        self._send_target_kind = kind
        self._send_target_value = value.strip()
        if kind == "user" and self._send_target_value:
            self._remember_recent_user(self._send_target_value)
        self._refresh_send_target_button()
        self._save_profile(warn_on_error=False)

    def _remember_recent_user(self, nick: str) -> None:
        n = nick.strip()
        if not n:
            return
        me = self.var_user.get().strip()
        if me and n.lower() == me.lower():
            return
        self._recent_users = [n] + [x for x in self._recent_users if x.lower() != n.lower()]
        self._recent_users = self._recent_users[:12]

    def _refresh_send_target_button(self) -> None:
        if hasattr(self, "_send_target_tip"):
            self._send_target_tip.set_text(self._send_target_label())

    def _start_canvas(self) -> None:
        if not self._chan or self._chan.closed:
            messagebox.showwarning("SSHChat", "请先连接")
            return
        cmd = self._canvas_command()
        self._append_chat_line(f"[*] 正在创建共享画板…（{cmd}）", local_sent=True)
        self._expecting_own_canvas = True
        try:
            self._chan_send_bytes((cmd + "\n").encode("utf-8"))
        except Exception as e:
            messagebox.showerror("SSHChat", f"发送失败: {e}")

    def _start_piano(self) -> None:
        if not self._chan or self._chan.closed:
            messagebox.showwarning("SSHChat", "请先连接")
            return
        cmd = self._piano_command()
        self._append_chat_line(f"[*] 正在开启房间钢琴…（{cmd}）", local_sent=True)
        try:
            self._chan_send_bytes((cmd + "\n").encode("utf-8"))
        except Exception as e:
            messagebox.showerror("SSHChat", f"发送失败: {e}")

    def _start_library(self) -> None:
        if not self._chan or self._chan.closed:
            messagebox.showwarning("SSHChat", "请先连接")
            return
        self._append_chat_line("[*] 正在打开图书馆…（/library）", local_sent=True)
        try:
            self._chan_send_bytes(b"/library\n")
        except Exception as e:
            messagebox.showerror("SSHChat", f"发送失败: {e}")

    def _refresh_online_users(self) -> None:
        if not self._chan or self._chan.closed:
            return
        self._expecting_names = True
        try:
            self._chan_send_bytes(b"/names\n")
        except Exception as e:
            self._expecting_names = False
            messagebox.showerror("SSHChat", f"发送失败: {e}")

    def _show_send_target_picker(self) -> None:
        if not self._chan or self._chan.closed:
            messagebox.showwarning("SSHChat", "请先连接")
            return

        win = tk.Toplevel(self.root)
        win.title("发送至")
        win.transient(self.root)
        win.grab_set()

        def _close_picker() -> None:
            try:
                win.grab_release()
            except tk.TclError:
                pass
            try:
                win.destroy()
            except tk.TclError:
                pass
            self._schedule_click_target_refresh(delay_ms=40)

        items: list[tuple[str, str, str]] = []
        items.append((f"当前房间 #{self._active_room}", "room", ""))

        me = self.var_user.get().strip().lower()
        seen: set[str] = set()

        def add_user(nick: str) -> None:
            n = nick.strip()
            if not n or (me and n.lower() == me):
                return
            key = n.lower()
            if key in seen:
                return
            seen.add(key)
            items.append((f"私聊 {n}", "user", n))

        for u in self._online_users:
            add_user(u)
        for u in self._recent_users:
            add_user(u)

        lb = tk.Listbox(win, width=44, height=min(14, max(4, len(items) + 2)))
        lb.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        for label, _, _ in items:
            lb.insert(tk.END, label)

        btn_row = ttk.Frame(win)
        btn_row.pack(fill=tk.X, padx=8, pady=(0, 8))

        def apply_choice() -> None:
            sel = lb.curselection()
            if not sel:
                return
            _, kind, value = items[sel[0]]
            self._set_send_target(kind, value)
            _close_picker()

        def pick_named_user() -> None:
            r = simpledialog.askstring(
                "私聊指定用户",
                "昵称（不在线也会留言发文件）:",
                parent=win,
            )
            if r:
                nick = r.strip()
                if nick:
                    self._set_send_target("user", nick)
                    _close_picker()

        def pick_named_room() -> None:
            r = simpledialog.askstring("发送到房间", "房间名（不含 #）:", parent=win)
            if r:
                room = r.strip().lstrip("#")
                if room:
                    self._set_send_target("named_room", room)
                    _close_picker()

        def refresh_online() -> None:
            self._refresh_online_users()
            self._set_status("正在刷新在线用户…")
            _close_picker()

        ttk.Button(btn_row, text="确定", command=apply_choice).pack(side=tk.LEFT)
        ttk.Button(btn_row, text="指定用户…", command=pick_named_user).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(btn_row, text="指定房间…", command=pick_named_room).pack(side=tk.LEFT, padx=8)
        ttk.Button(btn_row, text="刷新在线", command=refresh_online).pack(side=tk.LEFT)
        ttk.Button(btn_row, text="取消", command=_close_picker).pack(side=tk.RIGHT)
        win.protocol("WM_DELETE_WINDOW", _close_picker)
        lb.bind("<Double-Button-1>", lambda _e: apply_choice())
        if items:
            lb.selection_set(0)
        _place_toplevel_near_widget(win, self.btn_send_target, align="right")

    def _on_room_list_click(self, event) -> None:
        try:
            idx = int(self.room_list.nearest(event.y))
        except (tk.TclError, TypeError, ValueError):
            return
        self._activate_room_at(idx)
        self._schedule_click_target_refresh(delay_ms=40)

    def _on_room_selected(self, _event=None) -> None:
        if self._room_select_guard:
            return
        if not self.room_list.curselection():
            return
        idx = int(self.room_list.curselection()[0])
        self._activate_room_at(idx)
        self._schedule_click_target_refresh(delay_ms=40)

    def _activate_room_at(self, idx: int) -> None:
        if idx < 0 or idx >= len(self._rooms_order):
            return
        room = self._rooms_order[idx]
        if room == self._active_room:
            return
        self._switch_room_local(room, send_switch=True)

    def _insert_log_fragment(self, text: str, tag: str) -> None:
        if (
            "{{R}}" in text
            or "{{B}}" in text
            or "【" in text
            or "〔" in text
            or re.search(r"[+\-!][车马炮相仕帅将士象兵卒]", text)
        ):
            self._insert_xiangqi_colored(text, tag)
            return
        if tag:
            self.log.insert(tk.END, text, (tag,))
        else:
            self.log.insert(tk.END, text)

    def _insert_xiangqi_colored(self, text: str, base_tag: str) -> None:
        pos = 0
        for m in _XQ_COLOR_SEG.finditer(text):
            if m.start() > pos:
                chunk = text[pos : m.start()]
                if base_tag:
                    self.log.insert(tk.END, chunk, (base_tag,))
                else:
                    self.log.insert(tk.END, chunk)
            if m.group(1) is not None:
                piece_out, ptag = "!" + m.group(1), "xq_red"
            elif m.group(2) is not None:
                piece_out, ptag = "+" + m.group(2), "xq_red"
            else:
                piece_out, ptag = "-" + m.group(3), "xq_black"
            self.log.insert(tk.END, piece_out, (ptag,))
            pos = m.end()
        if pos < len(text):
            tail = text[pos:]
            if base_tag:
                self.log.insert(tk.END, tail, (base_tag,))
            else:
                self.log.insert(tk.END, tail)

    def _append_room_entry(self, room: str, text: str, tag: str = "") -> None:
        self._ensure_room(room)
        history = self._room_history[room]
        history.append((text, tag))
        if len(history) > _MAX_ROOM_HISTORY:
            del history[: len(history) - _MAX_ROOM_HISTORY]
        if room == self._active_room and not self._is_minimized:
            self.log.configure(state=tk.NORMAL)
            self._insert_log_fragment(text, tag)
            self.log.see(tk.END)
            self.log.configure(state=tk.DISABLED)
        else:
            self._room_unread[room] = self._room_unread.get(room, 0) + 1
            self._schedule_room_list_refresh()

    def _format_file_size(self, size: int) -> str:
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size / (1024 * 1024):.1f} MB"

    def _make_inline_photo(self, path: Path) -> Any | None:
        """Build a small RGB PNG PhotoImage via base64 (safe for Label, not Text)."""
        preview: Path | None = None
        photo = None
        try:
            preview = _prepare_tk_preview_png(path)
            if preview is None or not preview.is_file():
                return None
            b64 = base64.b64encode(preview.read_bytes()).decode("ascii")
            photo = tk.PhotoImage(data=b64)
            w, h = int(photo.width()), int(photo.height())
            if w > _MAX_INLINE_IMAGE_PX or h > _MAX_INLINE_IMAGE_PX:
                factor = max(
                    (w + _MAX_INLINE_IMAGE_PX - 1) // _MAX_INLINE_IMAGE_PX,
                    (h + _MAX_INLINE_IMAGE_PX - 1) // _MAX_INLINE_IMAGE_PX,
                    1,
                )
                if factor > 1:
                    photo = photo.subsample(factor, factor)
        except Exception:
            photo = None
        finally:
            if preview is not None and preview != path:
                try:
                    preview.unlink(missing_ok=True)
                except OSError:
                    pass
        if photo is None:
            return None
        self._photo_refs.append(photo)
        return photo

    def _on_log_click(self, event) -> None:
        self.log.focus_set()
        try:
            index = self.log.index(f"@{event.x},{event.y}")
            tags = self.log.tag_names(index)
        except tk.TclError:
            return
        for tag in tags:
            prev = self._media_preview_targets.get(tag)
            if prev:
                self._open_media_preview(Path(prev[0]), prev[1])
                return
            canvas = self._canvas_open_targets.get(tag)
            if canvas:
                self._open_native_canvas(canvas[0], canvas[1])
                return
            piano = self._piano_open_targets.get(tag)
            if piano:
                self._open_native_piano(piano[0], piano[1])
                return
            save = self._media_save_targets.get(tag)
            if save:
                self._save_media_as(Path(save[0]), save[1])
                return

    def _open_media_preview(
        self, path: Path, name: str, *, quiet: bool = False
    ) -> None:
        """Show zoomable image preview in a Toplevel — never embed into chat Text."""
        if not path.is_file():
            msg = "本地文件已失效，请重新接收"
            if quiet:
                self._set_status(msg)
            else:
                messagebox.showwarning("SSHChat", msg)
            return
        if not _HAS_PIL:
            msg = f"当前客户端未包含 Pillow，无法预览: {name}（可另存为后打开）"
            if quiet:
                self._set_status(msg)
            else:
                messagebox.showinfo("SSHChat", msg)
            return
        try:
            if self._preview_win is not None:
                try:
                    self._preview_win.close()
                except Exception:
                    pass
            self._preview_win = ImagePreviewWindow(
                self.root,
                path,
                name,
                on_save=self._save_media_as,
                on_close=self._on_aux_window_closed,
            )
        except Exception as e:
            # Auto-open after send/receive must not look like "发送失败".
            if quiet:
                self._set_status(f"预览跳过: {e}")
            else:
                messagebox.showerror("SSHChat", f"预览失败: {e}")

    def _insert_media_entry(self, media: dict[str, Any]) -> None:
        """
        Insert file/image notice without embedding images or child widgets in Text.

        Aqua Tk (macOS) and some Windows builds hard-crash on Text.image_create
        and Text.window_create; preview opens in a separate Toplevel instead.
        """
        name = str(media.get("name") or "file")
        size = int(media.get("size") or 0)
        path = Path(str(media.get("path") or ""))
        is_image = bool(media.get("is_image") and path.is_file())
        kind = "图片" if is_image else "文件"
        sender = str(media.get("sender") or "").strip()
        prefix = f"来自 {sender} · " if sender else ""
        caption = f"{prefix}[{kind}] {name} ({self._format_file_size(size)})  "
        try:
            self.log.insert(tk.END, caption, ("notice",))
        except tk.TclError:
            return

        self._media_tag_seq += 1
        tag = f"media_id_{self._media_tag_seq}"
        self._media_save_targets[tag] = (str(path), name)
        try:
            if is_image:
                self._media_preview_targets[tag] = (str(path), name)
                self.log.insert(tk.END, "预览", ("media_preview", tag))
                self.log.insert(tk.END, "  ")
            self.log.insert(tk.END, "另存为", ("media_save", tag))
            self.log.insert(tk.END, "\n")
        except tk.TclError:
            pass

    def _append_room_media(self, room: str, media: dict[str, Any]) -> None:
        self._ensure_room(room)
        history = self._room_history[room]
        history.append(dict(media))
        if len(history) > _MAX_ROOM_HISTORY:
            del history[: len(history) - _MAX_ROOM_HISTORY]
        if room == self._active_room and not self._is_minimized:
            try:
                self.log.configure(state=tk.NORMAL)
                self._insert_media_entry(media)
                self.log.see(tk.END)
                self.log.configure(state=tk.DISABLED)
                # Do not auto-open image windows — privacy (shared screen / shoulder).
                # User clicks 「预览」 when ready.
            except Exception as e:
                self._set_status(f"预览失败: {e}")
                try:
                    self.log.configure(state=tk.DISABLED)
                except tk.TclError:
                    pass
                try:
                    self._append_chat_line(
                        f"[*] 已收到文件: {media.get('name') or 'file'}",
                        local_sent=True,
                    )
                except Exception:
                    pass
        else:
            self._room_unread[room] = self._room_unread.get(room, 0) + 1
            self._schedule_room_list_refresh()

    def _save_media_as(self, path: Path, name: str) -> None:
        if not path.is_file():
            messagebox.showwarning("SSHChat", "本地文件已失效，请重新接收")
            return
        dest = filedialog.asksaveasfilename(
            title="保存文件",
            initialfile=name,
            defaultextension=path.suffix,
        )
        if not dest:
            return
        try:
            shutil.copy2(path, dest)
            self._set_status(f"已保存: {dest}")
        except OSError as e:
            messagebox.showerror("SSHChat", f"保存失败: {e}")

    def _update_rooms_from_system(self, body: str) -> None:
        # Examples:
        # "Active room #ops. /names /rooms ..."
        # "Rooms: #default, *#ops"
        # "Joined #ops and switched from #default to #ops"
        # "Switched from #ops to #dev"
        # "Left #dev, switched to #default"
        m_active = re.search(r"Active room\s+#([a-zA-Z0-9_-]{1,32})", body, re.I)
        if m_active:
            self._switch_room_local(m_active.group(1), send_switch=False)
        m = re.search(r"Rooms:\s*(.*)$", body)
        if m:
            rooms_text = m.group(1)
            found = re.findall(r"\*?#([a-zA-Z0-9_-]{1,32})", rooms_text)
            if found:
                for r in found:
                    self._ensure_room(r)
                active = re.search(r"\*#([a-zA-Z0-9_-]{1,32})", rooms_text)
                if active:
                    self._switch_room_local(active.group(1), send_switch=False)
                else:
                    self._refresh_room_list()
            return
        m2 = re.search(r"to\s+#([a-zA-Z0-9_-]{1,32})", body)
        if m2:
            self._switch_room_local(m2.group(1), send_switch=False)

    def _append_log(self, text: str) -> None:
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, text)
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def _append_chat_line(self, line: str, *, local_sent: bool = False) -> None:
        cleaned = _LEADING_GARBAGE_RE.sub("", line.rstrip("\n"))
        cleaned = _TIME_ANY_RE.sub("", cleaned).strip()
        t = cleaned
        if not t:
            return
        if self._expecting_names:
            names = _parse_names_line(t)
            if names is not None:
                _room, members = names
                self._online_users = members
                self._expecting_names = False
                self._refresh_send_target_button()
                return
        parsed = _parse_chat_line(t)
        if self._expecting_names and parsed and parsed[1] == "*":
            names = _parse_names_line(f"[*] {parsed[2]}")
            if names is not None:
                _room, members = names
                self._online_users = members
                self._expecting_names = False
                self._refresh_send_target_button()
                return
        if (
            parsed
            and parsed[0] == ""
            and parsed[1] == "*"
            and parsed[2].strip() == "Screen cleared."
        ):
            self._clear_active_room(announce=False)
            return
        if parsed and parsed[1] == "*" and self._try_handle_upload_invite(parsed[2]):
            return
        if parsed and parsed[1] == "*" and self._try_handle_canvas_invite(parsed[2]):
            return
        if parsed and parsed[1] == "*" and self._try_handle_piano_invite(parsed[2]):
            return
        if parsed and parsed[1] == "*" and self._try_handle_download_invite(parsed[2]):
            return
        if parsed and parsed[1] == "*":
            body = parsed[2].strip()
            if _SECURE_BANNER_START_RE.match(body):
                self._pending_file_meta = {}
            field = _parse_file_meta_field(body)
            if field:
                self._pending_file_meta[field[0]] = field[1]
        if parsed and parsed[1] == "*" and _is_secure_invite_noise(parsed[2]):
            return
        ts = datetime.now()
        self._display_times.append(ts)
        time_label = self._format_time(ts)
        room_for_line = self._active_room
        prefix = f"[{time_label}] "
        if parsed:
            room, sender, body = parsed
            room_for_line = room or self._active_room
            if room:
                prefix += f"[#{room}] "
            me = self.var_user.get().strip()
            is_system_sender = sender in {"+", "-", "*", "!"}
            if local_sent and is_system_sender:
                role_tag = "notice"
            elif is_system_sender:
                role_tag = "meta" if sender in {"+", "-", "!"} else "system"
            else:
                role_tag = "me" if sender == me else "peer"
            if not local_sent:
                if role_tag == "peer":
                    self._alert_beep()
                elif sender in {"+", "-"} or (
                    sender == "!" and (" left " in body or " joined " in body)
                ):
                    self._alert_beep()
            self._append_room_entry(room_for_line, prefix, "meta")
            self._append_room_entry(room_for_line, f"[{sender}] {body}\n", role_tag)
            if sender == "*":
                self._update_rooms_from_system(body)
        elif local_sent:
            self._append_room_entry(room_for_line, prefix, "meta")
            self._append_room_entry(room_for_line, t + "\n", "me")
        else:
            lowered = t.lower()
            if " joined " in lowered or " left " in lowered:
                self._alert_beep()
            self._append_room_entry(room_for_line, prefix, "meta")
            self._append_room_entry(room_for_line, t + "\n")

    def _append_rendered_block(self, text: str) -> None:
        for line in text.splitlines():
            if line:
                self._append_chat_line(line)

    def _set_status(self, s: str) -> None:
        self.var_status.set(s)

    def _apply_profile(self, cfg: dict[str, Any] | None) -> None:
        if not cfg:
            return
        if isinstance(cfg.get("host"), str) and cfg["host"].strip():
            self.var_host.set(cfg["host"].strip())
        if cfg.get("ssh_port") is not None:
            self.var_port.set(str(cfg["ssh_port"]))
        if isinstance(cfg.get("user"), str):
            self.var_user.set(cfg["user"])
        kind = cfg.get("send_target_kind")
        if isinstance(kind, str) and kind in ("room", "user", "named_room"):
            self._send_target_kind = kind
            value = cfg.get("send_target_value")
            self._send_target_value = str(value).strip() if value else ""
        recent = cfg.get("send_target_recent")
        if isinstance(recent, list):
            self._recent_users = [
                str(x).strip() for x in recent if str(x).strip()
            ][:12]
        self._refresh_send_target_button()

    def _collect_profile_dict(self) -> dict[str, Any]:
        user = self.var_user.get().strip()
        if not user:
            raise ValueError("请填写用户名")
        port_s = self.var_port.get().strip() or "22"
        try:
            port_n = int(port_s)
        except ValueError:
            raise ValueError("SSH 端口必须是数字") from None
        host = self.var_host.get().strip()
        if not host:
            raise ValueError("请填写主机")
        data: dict[str, Any] = {
            "host": host,
            "user": user,
            "ssh_port": port_n,
        }
        existing = load_client_config(self.config_path) or {}
        if isinstance(existing.get("extra_ssh_options"), list):
            data["extra_ssh_options"] = existing["extra_ssh_options"]
        data["send_target_kind"] = self._send_target_kind
        data["send_target_value"] = self._send_target_value
        data["send_target_recent"] = self._recent_users[:12]
        return data

    def _save_profile(self, *, warn_on_error: bool = True) -> bool:
        try:
            data = self._collect_profile_dict()
        except ValueError as e:
            if warn_on_error:
                messagebox.showwarning("SSHChat", str(e))
            return False
        try:
            save_client_config(self.config_path, data)
        except OSError as e:
            if warn_on_error:
                messagebox.showerror("SSHChat", f"无法写入配置: {e}")
            return False
        return True

    def _alert_beep(self) -> None:
        self.root.bell()
        if self._alert_sound in ("none", "off", "0"):
            return
        backends = ["canberra", "paplay", "aplay"] if self._alert_sound == "auto" else [self._alert_sound]
        for backend in backends:
            if backend == "canberra" and shutil.which("canberra-gtk-play"):
                subprocess.Popen(
                    ["canberra-gtk-play", "-i", "message-new-instant", "-d", "SSHChat"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return
            if backend == "paplay" and shutil.which("paplay"):
                for sound in (
                    "/usr/share/sounds/freedesktop/stereo/message.oga",
                    "/usr/share/sounds/freedesktop/stereo/complete.oga",
                ):
                    if os.path.exists(sound):
                        subprocess.Popen(["paplay", sound], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        return
            if backend == "aplay" and shutil.which("aplay"):
                for sound in (
                    "/usr/share/sounds/alsa/Front_Center.wav",
                    "/usr/share/sounds/alsa/Noise.wav",
                ):
                    if os.path.exists(sound):
                        subprocess.Popen(["aplay", "-q", sound], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        return

    def _connect_clicked(self) -> None:
        if self._connecting.is_set():
            return
        try:
            port = int(self.var_port.get().strip() or "22")
        except ValueError:
            messagebox.showwarning("SSHChat", "SSH 端口必须是数字")
            return
        host = self.var_host.get().strip()
        user = self.var_user.get().strip()
        if not user:
            messagebox.showwarning("SSHChat", "请填写用户名")
            return
        if not host:
            messagebox.showwarning("SSHChat", "请填写主机")
            return

        self._connecting.set()
        self.btn_connect.configure(state=tk.DISABLED)
        self._set_status("正在连接…")

        def worker() -> None:
            err: str | None = None
            try:
                self._connect_ssh(host, port, user)
            except Exception as e:
                err = str(e)
            finally:
                self._connecting.clear()
            if err is not None:
                self.root.after(0, lambda msg=err: self._connect_failed(msg))

        self._connect_thread = threading.Thread(target=worker, daemon=True)
        self._connect_thread.start()

    def _connect_ssh(self, host: str, port: int, user: str) -> None:
        ssh = paramiko.SSHClient()
        try:
            ssh.load_system_host_keys()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            connect_kw: dict[str, Any] = {
                "hostname": host,
                "port": port,
                "username": user,
                "timeout": 20,
                "banner_timeout": 30,
                "auth_timeout": 30,
                "allow_agent": True,
                "look_for_keys": True,
            }
            ssh.connect(**connect_kw)
            chan = ssh.invoke_shell(term="xterm", width=120, height=36)
        except Exception:
            try:
                ssh.close()
            except Exception:
                pass
            raise

        self._session_user = user.strip()
        self._ssh = ssh
        self._chan = chan
        self.root.after(0, self._connect_succeeded)

    def _connect_succeeded(self) -> None:
        self._save_profile(warn_on_error=False)
        self.btn_disconnect.configure(state=tk.NORMAL)
        self._set_status("已连接（SSH 会话）")
        self._rooms_order = ["default"]
        self._active_room = "default"
        self._room_unread = {"default": 0}
        self._room_history = {"default": []}
        self._refresh_room_list()
        self._render_active_room()
        self._session_id += 1
        sid = self._session_id
        self._reader_thread = threading.Thread(target=self._reader_loop, args=(sid,), daemon=True)
        self._reader_thread.start()
        self._schedule_drain()
        self._online_users = []
        self._expecting_names = False
        self._refresh_send_target_button()
        self.root.after(500, self._refresh_online_users)

    def _connect_failed(self, msg: str) -> None:
        self.btn_connect.configure(state=tk.NORMAL)
        self._ssh = None
        self._chan = None
        self._session_user = ""
        self._set_status("连接失败")
        messagebox.showerror("SSHChat", msg)

    def _chan_send_bytes(self, data: bytes) -> None:
        ch = self._chan
        if ch is None or ch.closed:
            raise RuntimeError("连接已断开")
        with self._chan_send_lock:
            off = 0
            while off < len(data):
                n = ch.send(data[off:])
                if n == 0:
                    time.sleep(0.02)
                    continue
                off += n

    def _reader_loop(self, sid: int) -> None:
        assert self._chan is not None
        chan = self._chan
        buf = bytearray()
        try:
            while True:
                try:
                    data = chan.recv(65536)
                except Exception:
                    break
                if not data:
                    break
                buf.extend(data)
                while True:
                    nl = buf.find(b"\n")
                    if nl < 0:
                        break
                    line_bytes = bytes(buf[:nl])
                    del buf[: nl + 1]
                    line_bytes = line_bytes.replace(b"\r\n", b"\n").replace(b"\r", b"")
                    text = line_bytes.decode("utf-8", errors="replace")
                    text = _clean_chunk(text)
                    if self._should_drop_line(text):
                        continue
                    self._out_q.put((sid, text + "\n"))
        finally:
            if buf:
                tail = bytes(buf).replace(b"\r\n", b"\n").replace(b"\r", b"")
                if tail.strip():
                    text = _clean_chunk(tail.decode("utf-8", errors="replace"))
                    if text.strip() and not self._should_drop_line(text):
                        self._out_q.put((sid, text + "\n"))
            self._out_q.put((sid, None))

    def _schedule_drain(self) -> None:
        self._drain_after_id = self.root.after(40, self._drain_queue)

    def _drain_queue(self) -> None:
        self._drain_after_id = None
        buf: list[str] = []
        processed = 0
        remote_eof = False
        try:
            while True:
                sid, item = self._out_q.get_nowait()
                if sid != self._session_id:
                    continue
                if item is None:
                    remote_eof = True
                    break
                buf.append(item)
                processed += 1
                if processed >= _DRAIN_BATCH_ITEMS:
                    break
        except queue.Empty:
            pass
        if buf:
            self._append_rendered_block("".join(buf))
        if remote_eof:
            self._on_remote_eof()
            return
        if processed >= _DRAIN_BATCH_ITEMS:
            # Yield to Tk so minimized/restore won't lock the UI
            # when there is a large backlog of buffered messages.
            self._schedule_drain()
            return
        ch = self._chan
        if ch is not None and not ch.closed:
            self._schedule_drain()

    def _on_remote_eof(self) -> None:
        if self._ssh is None and self._chan is None:
            return
        self._append_chat_line("[*] 与服务器的连接已结束。")
        self._disconnect(clear_log=False)
        self._set_status("已断开")

    def _clear_active_room(self, announce: bool = True) -> None:
        self._room_history[self._active_room] = []
        self._render_active_room()
        if announce:
            self._append_room_entry(
                self._active_room, "[*] Screen cleared.\n", "system"
            )

    def _cancel_paste_timer(self) -> None:
        if self._paste_timer is not None:
            try:
                self.root.after_cancel(self._paste_timer)
            except tk.TclError:
                pass
            self._paste_timer = None

    def _paste_busy(self) -> bool:
        return self._paste_pending is not None

    def _fail_paste(self, message: str) -> None:
        pending = self._paste_pending
        self._cancel_paste_timer()
        self._paste_pending = None
        name = pending["name"] if pending else "file"
        self._set_status(f"发文件失败: {message}")
        self._append_chat_line(f"[*] 发文件失败（{name}）: {message}", local_sent=True)

    def _start_paste_sendfile(self, path: Path) -> None:
        if not self._chan or self._chan.closed:
            messagebox.showwarning("SSHChat", "请先连接")
            return
        if self._paste_busy():
            messagebox.showinfo("SSHChat", "已有文件正在上传，请稍候")
            return
        try:
            resolved = path.expanduser().resolve()
        except OSError as e:
            messagebox.showerror("SSHChat", f"无法读取文件: {e}")
            return
        if not resolved.is_file():
            messagebox.showwarning("SSHChat", "不是有效文件")
            return
        self._cancel_paste_timer()
        self._paste_pending = {
            "path": resolved,
            "name": resolved.name,
            "consumed": False,
            "started": time.time(),
        }
        cmd = self._sendfile_command()
        self._set_status(f"等待上传通道: {resolved.name}")
        self._append_chat_line(
            f"[*] 正在发文件: {resolved.name}（{cmd}）", local_sent=True
        )
        self._paste_timer = self.root.after(
            int(_PASTE_UPLOAD_TIMEOUT_S * 1000), self._on_paste_timeout
        )
        try:
            self._chan_send_bytes((cmd + "\n").encode("utf-8"))
        except Exception as e:
            self._fail_paste(str(e))

    def _on_paste_timeout(self) -> None:
        self._paste_timer = None
        if self._paste_pending and not self._paste_pending.get("consumed"):
            self._fail_paste("timeout")

    def _try_handle_upload_invite(self, body: str) -> bool:
        text = body.strip()
        pending = self._paste_pending
        if pending and not pending.get("consumed"):
            if _SENDFILE_FAIL_RE.search(text):
                self._fail_paste(text.replace("[*]", "").strip()[:120] or "send_failed")
                return True
        if not pending or pending.get("consumed"):
            return False
        m = _GUI_OPEN_UPLOAD_RE.match(text)
        if not m:
            return False
        pending["consumed"] = True
        self._cancel_paste_timer()
        url, key = m.group(1), m.group(2).upper()
        path: Path = pending["path"]
        name = pending["name"]
        self._set_status(f"上传中: {name}")

        def worker() -> None:
            err: str | None = None
            remote = name
            try:
                remote = _upload_secure_file(url, key, path)
            except Exception as e:
                err = str(e)
            self.root.after(
                0,
                lambda n=name, r=remote, e=err, p=path: self._paste_upload_finished(
                    n, r, e, local_path=p
                ),
            )

        threading.Thread(target=worker, daemon=True).start()
        return True

    def _paste_upload_finished(
        self,
        name: str,
        remote: str,
        err: str | None,
        *,
        local_path: Path | None = None,
    ) -> None:
        self._paste_pending = None
        self._cancel_paste_timer()
        if err:
            self._set_status(f"发文件失败: {err}")
            self._append_chat_line(
                f"[*] 发文件失败（{name}）: {err}", local_sent=True
            )
            return
        self._set_status(f"已上传: {remote}")
        shown = False
        if local_path is not None:
            try:
                media = _media_from_local_path(
                    local_path,
                    display_name=remote or name,
                    sender=self.var_user.get().strip(),
                )
                self._append_room_media(self._active_room, media)
                shown = True
            except Exception as e:
                self._append_chat_line(
                    f"[*] 已上传: {remote}（本地预览失败: {e}）", local_sent=True
                )
                shown = True
        if not shown:
            self._append_chat_line(f"[*] 已上传: {remote}", local_sent=True)

    def _try_handle_canvas_invite(self, body: str) -> bool:
        m = _GUI_OPEN_CANVAS_RE.match(body.strip())
        if not m:
            return False
        url, key = m.group(1), m.group(2).upper()
        me = self.var_user.get().strip()
        creator = str(self._pending_file_meta.get("sender") or "").strip()
        own = self._expecting_own_canvas or (
            bool(creator and me) and creator.lower() == me.lower()
        )
        self._expecting_own_canvas = False
        if own:
            self._open_native_canvas(url, key)
        else:
            self._offer_canvas_open(url, key)
        return True

    def _try_handle_piano_invite(self, body: str) -> bool:
        m = _GUI_OPEN_PIANO_RE.match(body.strip())
        if not m:
            return False
        url, key = m.group(1), m.group(2).upper()
        self._open_native_piano(url, key)
        return True

    def _offer_canvas_open(self, url: str, key: str) -> None:
        self._media_tag_seq += 1
        tag = f"canvas_id_{self._media_tag_seq}"
        self._canvas_open_targets[tag] = (url, key)
        try:
            self.log.configure(state=tk.NORMAL)
            self.log.insert(tk.END, "[*] 收到共享画布邀请  ", ("notice",))
            self.log.insert(tk.END, "打开画布", ("canvas_open", tag))
            self.log.insert(tk.END, "\n")
            self.log.see(tk.END)
            self.log.configure(state=tk.DISABLED)
        except tk.TclError:
            try:
                self.log.configure(state=tk.DISABLED)
            except tk.TclError:
                pass
            self._append_chat_line(
                f"[*] 收到共享画布邀请（点消息区「打开画布」）", local_sent=True
            )
        self._set_status("收到共享画布（未自动打开，可点「打开画布」）")
        self._alert_beep()
        self._schedule_click_target_refresh(delay_ms=40)

    def _try_handle_download_invite(self, body: str) -> bool:
        m = _GUI_OPEN_DOWNLOAD_RE.match(body.strip())
        if not m:
            return False
        url, key = m.group(1), m.group(2).upper()
        room = self._active_room
        meta_snapshot = dict(self._pending_file_meta)
        self._pending_file_meta = {}
        sender = str(meta_snapshot.get("sender") or "").strip()
        me = self.var_user.get().strip()
        # Same nick on another device sent this file; that session already got
        # a recipient slot — don't burn the one-time link on this GUI copy.
        if sender and me and sender.lower() == me.lower():
            self._set_status("已在其他设备发送，跳过自动下载")
            return True
        self._set_status("正在接收文件…")
        self._alert_beep()

        def worker() -> None:
            err: str | None = None
            media: dict[str, Any] | None = None
            try:
                media = _fetch_secure_download(url, key)
            except Exception as e:
                err = str(e)
            self.root.after(
                0,
                lambda r=room, m=media, e=err, meta=meta_snapshot: self._download_finished(
                    r, m, e, meta
                ),
            )

        threading.Thread(target=worker, daemon=True).start()
        return True

    def _download_finished(
        self,
        room: str,
        media: dict[str, Any] | None,
        err: str | None,
        meta: dict[str, str] | None = None,
    ) -> None:
        sender = str((meta or self._pending_file_meta).get("sender") or "").strip() or None
        if err or not media:
            detail = err or "unknown"
            if sender:
                detail = f"{detail}（来自 {sender}）"
            self._set_status(f"收文件失败: {detail}")
            self._append_chat_line(f"[*] 收文件失败: {detail}", local_sent=True)
            return
        name = str(media.get("name") or "file")
        self._set_status(f"已接收: {name}")
        self._pending_file_meta = {}
        if sender and isinstance(media, dict):
            media = dict(media)
            media["sender"] = sender
        try:
            self._append_room_media(room, media)
        except Exception as e:
            self._append_chat_line(
                f"[*] 已接收: {name}（显示失败: {e}）", local_sent=True
            )

    def _open_native_canvas(self, url: str, key: str) -> None:
        # Excalidraw board is the web page; #k= autofills client-side only.
        try:
            target = f"{url}#k={urllib.parse.quote(str(key or '').upper())}"
            if _open_canvas_app_window(target, maximized=True):
                self._append_chat_line("[*] 已打开共享画布", local_sent=True)
            else:
                webbrowser.open(target)
                self._append_chat_line("[*] 已在系统浏览器打开共享画布", local_sent=True)
        except Exception as e:
            self._append_chat_line(f"[*] 打开画布失败: {e}", local_sent=True)

    def _reachability_host(self) -> str:
        if self._bundle:
            h = str(self._bundle.get("host") or "").strip()
            if h:
                return h
        try:
            return self.var_host.get().strip()
        except tk.TclError:
            return ""

    def _open_native_piano(self, url: str, key: str) -> None:
        try:
            # Key stays in Tk → server handoff; browser opens a one-time link only.
            target = _piano_open_url(url, key, fallback_host=self._reachability_host())
            if _open_canvas_app_window(target, maximized=True):
                self._append_chat_line("[*] 已打开房间钢琴", local_sent=True)
            else:
                webbrowser.open(target)
                self._append_chat_line("[*] 已在系统浏览器打开房间钢琴", local_sent=True)
        except Exception as e:
            self._append_chat_line(f"[*] 打开钢琴失败: {e}", local_sent=True)

    def _pick_and_send_file(self) -> None:
        path = filedialog.askopenfilename(title="选择要发送的文件")
        if path:
            self._start_paste_sendfile(Path(path))

    def _on_paste_file(self, event=None):
        if not self._chan or self._chan.closed or self._paste_busy():
            return None
        path = _clipboard_existing_path(self.root)
        if path is None:
            # Plain text (not a file path): let the Entry paste normally.
            # Do NOT probe image clipboard here — on Windows that used to spawn
            # PowerShell and flash a console on every Ctrl+V.
            try:
                clip_text = str(self.root.clipboard_get() or "").strip()
            except tk.TclError:
                clip_text = ""
            if clip_text:
                return None
            path = _clipboard_image_file()
        if path is None:
            return None
        self._start_paste_sendfile(path)
        return "break"

    def _cancel_suggestion_ui_job(self) -> None:
        if self._suggest_ui_job is None:
            return
        try:
            self.root.after_cancel(self._suggest_ui_job)
        except (tk.TclError, ValueError):
            pass
        self._suggest_ui_job = None

    def _destroy_suggestion_widgets(self) -> bool:
        had = self._suggest_win is not None
        if self._suggest_win is not None:
            try:
                self._suggest_win.destroy()
            except tk.TclError:
                pass
        self._suggest_win = None
        self._suggest_list = None
        return had

    def _apply_suggestion_ui(self) -> None:
        self._suggest_ui_job = None
        pending = self._suggest_ui_pending
        self._suggest_ui_pending = _SUGGEST_UI_IDLE
        if pending is _SUGGEST_UI_IDLE:
            return
        if self._suggest_focus_job is not None:
            try:
                self.root.after_cancel(self._suggest_focus_job)
            except (tk.TclError, ValueError):
                pass
            self._suggest_focus_job = None

        had = self._destroy_suggestion_widgets()
        if pending is None:
            self._suggest_items = []
            try:
                self._suggest_slot.grid_remove()
            except tk.TclError:
                pass
            if had:
                self._schedule_click_target_refresh()
            return

        items = pending
        if not items:
            self._suggest_items = []
            try:
                self._suggest_slot.grid_remove()
            except tk.TclError:
                pass
            if had:
                self._schedule_click_target_refresh()
            return

        self._suggest_items = items
        # In-window slot (not place/overrideredirect): floating layers on Aqua keep
        # eating button clicks when their screen rect goes stale.
        try:
            self._suggest_slot.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        except tk.TclError:
            pass
        frame = ttk.Frame(self._suggest_slot, relief=tk.SOLID, borderwidth=1)
        lst = tk.Listbox(frame, height=min(8, len(items)), exportselection=False)
        lst.pack(fill=tk.BOTH, expand=True)
        for item in items:
            lst.insert(tk.END, item)
        lst.selection_set(0)
        lst.activate(0)
        lst.bind("<ButtonRelease-1>", lambda _e: self._apply_selected_suggestion())
        frame.pack(fill=tk.BOTH, expand=True)
        self._suggest_win = frame
        self._suggest_list = lst
        if had:
            self._schedule_click_target_refresh()

    def _queue_suggestion_ui(self, items: list[str] | None) -> None:
        self._suggest_ui_pending = items
        self._cancel_suggestion_ui_job()
        try:
            self._suggest_ui_job = self.root.after_idle(self._apply_suggestion_ui)
        except tk.TclError:
            pass

    def _flush_suggestion_ui(self) -> None:
        if self._suggest_ui_pending is _SUGGEST_UI_IDLE and self._suggest_ui_job is None:
            return
        self._cancel_suggestion_ui_job()
        self._apply_suggestion_ui()

    def _hide_suggestions(self) -> None:
        if self._suggest_focus_job is not None:
            try:
                self.root.after_cancel(self._suggest_focus_job)
            except (tk.TclError, ValueError):
                pass
            self._suggest_focus_job = None
        self._queue_suggestion_ui(None)

    def _show_suggestions(self, items: list[str]) -> None:
        self._queue_suggestion_ui(items)

    def _selected_suggestion_index(self) -> int:
        if not self._suggest_list or not self._suggest_items:
            return 0
        sel = self._suggest_list.curselection()
        return int(sel[0]) if sel else 0

    def _should_commit_suggestion_on_enter(self) -> bool:
        if not self._suggest_list or not self._suggest_items:
            return False
        current = self.var_input.get().rstrip()
        chosen = self._suggest_items[self._selected_suggestion_index()].rstrip()
        # Only fill in a partial prefix (e.g. "/game m" → "/game move ").
        # If the user already typed "/game move 5 12", Enter should send.
        return len(current) < len(chosen) and chosen.startswith(current)

    def _apply_selected_suggestion(self) -> None:
        self._flush_suggestion_ui()
        if not self._suggest_list or not self._suggest_items:
            return
        sel = self._suggest_list.curselection()
        idx = int(sel[0]) if sel else 0
        if idx < 0 or idx >= len(self._suggest_items):
            return
        chosen = self._suggest_items[idx]
        # Commands get a trailing space so the next arg is ready.
        self.var_input.set(chosen if chosen.endswith(" ") else chosen + " ")
        self.entry.icursor(tk.END)
        self._hide_suggestions()
        self._schedule_click_target_refresh(delay_ms=0)
        self.entry.focus_set()

    def _completion_users(self) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for u in list(self._online_users) + list(self._recent_users):
            n = (u or "").strip()
            if not n:
                continue
            key = n.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(n)
        return out

    def _refresh_command_suggestions(self) -> list[str]:
        text = self.var_input.get()
        items = _command_completions(
            text,
            rooms=list(self._rooms_order),
            users=self._completion_users(),
        )[:12]
        if text.startswith("/") and items:
            self._show_suggestions(items)
        else:
            self._hide_suggestions()
        return items

    def _on_entry_keyrelease(self, event) -> None:
        if event.keysym in (
            "Tab",
            "Return",
            "Escape",
            "Up",
            "Down",
            "Shift_L",
            "Shift_R",
            "Control_L",
            "Control_R",
        ):
            return
        text = self.var_input.get()
        if text.startswith("/"):
            self._refresh_command_suggestions()
        else:
            self._hide_suggestions()

    def _on_entry_tab(self, _event=None):
        self._flush_suggestion_ui()
        text = self.var_input.get()
        if not text.startswith("/"):
            return "break"
        # Suggestions already open → Tab cycles the highlight (Enter applies).
        if self._suggest_list is not None and self._suggest_items:
            self._on_entry_down()
            return "break"
        items = _command_completions(
            text,
            rooms=list(self._rooms_order),
            users=self._completion_users(),
        )
        if not items:
            self._hide_suggestions()
            return "break"
        if len(items) == 1:
            self.var_input.set(items[0] + " ")
            self.entry.icursor(tk.END)
            self._hide_suggestions()
            return "break"
        shared = _longest_common_prefix(items)
        if len(shared) > len(text):
            self.var_input.set(shared)
            self.entry.icursor(tk.END)
        self._show_suggestions(items[:12])
        return "break"

    def _on_entry_down(self, _event=None):
        self._flush_suggestion_ui()
        if not self._suggest_list:
            return None
        size = self._suggest_list.size()
        if size <= 0:
            return "break"
        sel = self._suggest_list.curselection()
        idx = (int(sel[0]) + 1) % size if sel else 0
        self._suggest_list.selection_clear(0, tk.END)
        self._suggest_list.selection_set(idx)
        self._suggest_list.activate(idx)
        self._suggest_list.see(idx)
        return "break"

    def _on_entry_up(self, _event=None):
        self._flush_suggestion_ui()
        if not self._suggest_list:
            return None
        size = self._suggest_list.size()
        if size <= 0:
            return "break"
        sel = self._suggest_list.curselection()
        idx = (int(sel[0]) - 1) % size if sel else size - 1
        self._suggest_list.selection_clear(0, tk.END)
        self._suggest_list.selection_set(idx)
        self._suggest_list.activate(idx)
        self._suggest_list.see(idx)
        return "break"

    def _on_entry_escape(self, _event=None):
        if self._suggest_win is not None:
            self._hide_suggestions()
            return "break"
        return None

    def _on_entry_return(self, _event=None):
        self._flush_suggestion_ui()
        if self._should_commit_suggestion_on_enter():
            self._apply_selected_suggestion()
            return "break"
        self._hide_suggestions()
        self._send_clicked()
        return "break"

    def _send_clicked(self) -> None:
        if not self._chan or self._chan.closed:
            return
        self._flush_suggestion_ui()
        line = self.var_input.get()
        self.var_input.set("")
        self._hide_suggestions()
        self._schedule_click_target_refresh(delay_ms=0)
        if not line.strip():
            return
        low = line.strip()
        if re.fullmatch(r"/(?:clear|cls)", low, flags=re.I):
            self._clear_active_room()
            return
        try:
            m_join = re.match(r"^/(?:join|switch)\s+([a-zA-Z0-9_-]{1,32})\s*$", low)
            if m_join:
                self._switch_room_local(m_join.group(1), send_switch=False)
            m_part = re.match(r"^/part\s+([a-zA-Z0-9_-]{1,32})\s*$", low)
            if m_part:
                room = m_part.group(1)
                if room in self._rooms_order and len(self._rooms_order) > 1:
                    self._rooms_order = [r for r in self._rooms_order if r != room]
                    self._room_unread.pop(room, None)
                    self._room_history.pop(room, None)
                    if self._active_room == room:
                        self._active_room = self._rooms_order[0]
                    self._refresh_room_list()
                    self._render_active_room()
            outbound = self._outbound_text(line)
            # Slash commands have no [user]-prefixed broadcast; show a local hint.
            # Plain messages rely on the server broadcast (which the server also
            # delivers back to us) to render exactly once.
            if outbound.startswith("/"):
                self._append_chat_line(f"[*] {outbound}", local_sent=True)
            self._chan_send_bytes((outbound + "\n").encode("utf-8"))
        except Exception as e:
            messagebox.showerror("SSHChat", f"发送失败: {e}")
            self._disconnect()

    def _disconnect(self, clear_log: bool = True) -> None:
        self._cancel_paste_timer()
        self._paste_pending = None
        if self._drain_after_id is not None:
            try:
                self.root.after_cancel(self._drain_after_id)
            except tk.TclError:
                pass
            self._drain_after_id = None

        while True:
            try:
                self._out_q.get_nowait()
            except queue.Empty:
                break

        ch = self._chan
        sh = self._ssh
        self._chan = None
        self._ssh = None
        self._session_user = ""

        if ch:
            try:
                ch.close()
            except Exception:
                pass
        if sh:
            try:
                sh.close()
            except Exception:
                pass

        self.btn_connect.configure(state=tk.NORMAL)
        self.btn_disconnect.configure(state=tk.DISABLED)
        self._online_users = []
        self._expecting_names = False
        if clear_log:
            self._rooms_order = ["default"]
            self._active_room = "default"
            self._room_unread = {"default": 0}
            self._room_history = {"default": []}
            self._send_target_kind = "room"
            self._send_target_value = ""
            self._refresh_room_list()
            self._render_active_room()
        self._refresh_send_target_button()

    def _on_close(self) -> None:
        self._flush_suggestion_ui()
        self._disconnect(clear_log=False)
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(description="SSHChat 图形客户端（SSH + tkinter）")
    parser.add_argument(
        "--config",
        "-c",
        type=Path,
        default=default_client_config_path(),
        help="用户偏好（默认仅保存用户名；认证用本机 ~/.ssh，与 ssh 一致）",
    )
    parser.add_argument(
        "--full-ui",
        action="store_true",
        help="忽略内置 client-bundle.json，显示完整主机/端口表单（开发与排障）",
    )
    args = parser.parse_args()
    SSHChatGUI(args.config, force_full_ui=args.full_ui).run()


if __name__ == "__main__":
    main()
