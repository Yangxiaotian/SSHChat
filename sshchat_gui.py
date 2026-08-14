#!/usr/bin/env python3
"""
SSHChat 图形客户端：通过 SSH（与命令行相同）进入服务端强制命令聊天界面，
在窗口中显示远端输出并发送输入。需已安装 paramiko，见 requirements-gui.txt。
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import queue
import re
import shutil
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
from collections import deque
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
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
_SENDFILE_FAIL_RE = re.compile(
    r"没有其他用户|文件传输功能未启用|创建文件传输失败|"
    r"File transfer is disabled|no other users",
    re.I,
)
_MAX_ROOM_HISTORY = 4000
_DRAIN_BATCH_ITEMS = 200
_PASTE_UPLOAD_TIMEOUT_S = 45.0

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


def _command_completions(text: str) -> list[str]:
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
    if not subs:
        return []
    if trailing_space and len(parts) == 1:
        return [f"{parts[0]} {sub}" for sub in subs]
    if len(parts) >= 2 and not trailing_space:
        prefix = parts[1]
        return [f"{parts[0]} {sub}" for sub in subs if sub.startswith(prefix)]
    return []


def _is_ssl_verify_error(exc: BaseException) -> bool:
    if isinstance(exc, ssl.SSLError):
        return True
    reason = getattr(exc, "reason", None)
    if isinstance(reason, ssl.SSLError):
        return True
    msg = str(exc)
    return "CERTIFICATE_VERIFY_FAILED" in msg or "SSL:" in msg


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


def _mac_clipboard_image_file() -> Path | None:
    """Write PNG from macOS clipboard to a temp file, if present."""
    if sys.platform != "darwin":
        return None
    out = Path(tempfile.mkstemp(prefix="sshchat-clip-", suffix=".png")[1])
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
        self._room_history: dict[str, list[tuple[str, str]]] = {"default": []}
        self._paste_pending: dict[str, Any] | None = None
        self._paste_timer: str | int | None = None
        self._suggest_win: tk.Toplevel | None = None
        self._suggest_list: tk.Listbox | None = None
        self._suggest_items: list[str] = []

        self._build_ui()
        self._apply_profile(load_client_config(self.config_path))
        self.root.bind("<Map>", self._on_window_mapped, add="+")
        self.root.bind("<Unmap>", self._on_window_unmapped, add="+")
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
        if self._bundle:
            bh = str(self._bundle["host"]).strip()
            bp = int(self._bundle.get("ssh_port", 22))
            top = ttk.Frame(self.root)
            top.pack(fill=tk.X, **pad)
            ttk.Label(
                top,
                text=f"SSH 服务器: {bh}    端口 {bp}",
            ).pack(anchor="w")
            row_u = ttk.Frame(self.root)
            row_u.pack(fill=tk.X, **pad)
            ttk.Label(row_u, text="用户名").pack(side=tk.LEFT)
            self.var_user = tk.StringVar()
            ttk.Entry(row_u, textvariable=self.var_user, width=28).pack(
                side=tk.LEFT, padx=(8, 0), fill=tk.X, expand=True
            )
            self.var_host = tk.StringVar(value=bh)
            self.var_port = tk.StringVar(value=str(bp))
        else:
            top = ttk.Frame(self.root)
            top.pack(fill=tk.X, **pad)

            ttk.Label(top, text="主机").grid(row=0, column=0, sticky="w")
            self.var_host = tk.StringVar()
            ttk.Entry(top, textvariable=self.var_host, width=22).grid(
                row=0, column=1, sticky="ew", padx=(4, 8)
            )

            ttk.Label(top, text="用户").grid(row=0, column=2, sticky="w")
            self.var_user = tk.StringVar()
            ttk.Entry(top, textvariable=self.var_user, width=14).grid(
                row=0, column=3, sticky="ew", padx=(4, 8)
            )

            ttk.Label(top, text="SSH 端口").grid(row=0, column=4, sticky="w")
            self.var_port = tk.StringVar(value="22")
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
        self.log.tag_configure("system", foreground="#8e24aa")
        self.log.tag_configure("xq_red", foreground="#c62828")
        self.log.tag_configure("xq_black", foreground="#263238")
        self.log.bind("<<Paste>>", self._on_paste_file, add="+")
        # Allow paste/drop focus even when text widget is disabled.
        self.log.bind("<Button-1>", lambda e: self.log.focus_set(), add="+")

        bot = ttk.Frame(self.root)
        bot.pack(fill=tk.X, padx=8, pady=(0, 8))
        self.var_input = tk.StringVar()
        self.entry = ttk.Entry(bot, textvariable=self.var_input)
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.entry.bind("<Return>", self._on_entry_return)
        self.entry.bind("<Tab>", self._on_entry_tab)
        self.entry.bind("<Down>", self._on_entry_down)
        self.entry.bind("<Up>", self._on_entry_up)
        self.entry.bind("<Escape>", self._on_entry_escape)
        self.entry.bind("<KeyRelease>", self._on_entry_keyrelease, add="+")
        self.entry.bind("<<Paste>>", self._on_paste_file, add="+")
        self.entry.bind("<Command-v>", self._on_paste_file, add="+")
        self.entry.bind("<Control-v>", self._on_paste_file, add="+")
        ttk.Button(bot, text="发送", command=self._send_clicked).pack(
            side=tk.LEFT, padx=(8, 0)
        )
        ttk.Button(bot, text="发文件", command=self._pick_and_send_file).pack(
            side=tk.LEFT, padx=(8, 0)
        )
        ttk.Button(bot, text="清屏", command=self._clear_active_room).pack(
            side=tk.LEFT, padx=(8, 0)
        )
        hint = ttk.Label(
            self.root,
            text="提示: Tab 补全命令；/clear 清屏；可粘贴截图/文件路径，或点「发文件」",
            foreground="#666",
        )
        hint.pack(anchor="w", padx=10, pady=(0, 6))
        self._refresh_room_list()

    def _on_window_mapped(self, _event=None) -> None:
        # On some macOS/Tk builds, first paint can leave controls seemingly
        # unresponsive until a manual move/resize. Force a post-map refresh.
        self._is_minimized = False
        self.root.after_idle(self._stabilize_initial_interaction)
        self.root.after(120, self._stabilize_initial_interaction)
        self.root.after(320, self._stabilize_initial_interaction)
        self.root.after(60, self._render_active_room)

    def _on_window_unmapped(self, _event=None) -> None:
        try:
            self._is_minimized = self.root.state() == "iconic"
        except tk.TclError:
            self._is_minimized = False

    def _stabilize_initial_interaction(self) -> None:
        try:
            self.root.update_idletasks()
        except tk.TclError:
            return
        try:
            self.root.lift()
            self.root.focus_force()
        except tk.TclError:
            pass
        if self.btn_connect.instate(("disabled",)):
            self.btn_connect.state(("!disabled",))
        if not self._chan or self._chan.closed:
            self.entry.focus_set()

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
        self.room_list.delete(0, tk.END)
        for room in self._rooms_order:
            self.room_list.insert(tk.END, self._room_label(room))
        if self._active_room in self._rooms_order:
            idx = self._rooms_order.index(self._active_room)
            self.room_list.selection_clear(0, tk.END)
            self.room_list.selection_set(idx)
            self.room_list.activate(idx)

    def _render_active_room(self) -> None:
        entries = self._room_history.get(self._active_room, [])
        self.log.configure(state=tk.NORMAL)
        self.log.delete("1.0", tk.END)
        for text, tag in entries:
            self._insert_log_fragment(text, tag)
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def _switch_room_local(self, room: str, *, send_switch: bool = False) -> None:
        self._ensure_room(room)
        self._active_room = room
        self._room_unread[room] = 0
        self._refresh_room_list()
        self._render_active_room()
        if send_switch and self._chan and not self._chan.closed:
            try:
                self._chan_send_bytes((f"/switch {room}\n").encode("utf-8"))
            except Exception:
                pass

    def _on_room_selected(self, _event=None) -> None:
        if not self.room_list.curselection():
            return
        idx = int(self.room_list.curselection()[0])
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
            self._refresh_room_list()

    def _update_rooms_from_system(self, body: str) -> None:
        # Examples:
        # "Rooms: #default, *#ops"
        # "Joined #ops and switched from #default to #ops"
        # "Switched from #ops to #dev"
        # "Left #dev, switched to #default"
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
        parsed = _parse_chat_line(t)
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
            role_tag = "system" if is_system_sender else ("me" if sender == me else "peer")
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
        if not self._bundle:
            if isinstance(cfg.get("host"), str):
                self.var_host.set(cfg["host"])
            port = cfg.get("ssh_port", 22)
            self.var_port.set(str(port))
        if isinstance(cfg.get("user"), str):
            self.var_user.set(cfg["user"])

    def _collect_profile_dict(self) -> dict[str, Any]:
        user = self.var_user.get().strip()
        if not user:
            raise ValueError("请填写用户名")
        if self._bundle:
            host = str(self._bundle["host"]).strip()
            port_n = int(self._bundle.get("ssh_port", 22))
        else:
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
        if self._bundle:
            try:
                port = int(self._bundle.get("ssh_port", 22))
            except (TypeError, ValueError):
                port = 22
            host = str(self._bundle["host"]).strip()
        else:
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
        self._set_status(f"等待上传通道: {resolved.name}")
        self._append_chat_line(
            f"[*] 正在发文件: {resolved.name}（/sendfile）", local_sent=True
        )
        self._paste_timer = self.root.after(
            int(_PASTE_UPLOAD_TIMEOUT_S * 1000), self._on_paste_timeout
        )
        try:
            self._chan_send_bytes(b"/sendfile\n")
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
                0, lambda: self._paste_upload_finished(name, remote, err)
            )

        threading.Thread(target=worker, daemon=True).start()
        return True

    def _paste_upload_finished(
        self, name: str, remote: str, err: str | None
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
        self._append_chat_line(f"[*] 已上传: {remote}", local_sent=True)

    def _pick_and_send_file(self) -> None:
        path = filedialog.askopenfilename(title="选择要发送的文件")
        if path:
            self._start_paste_sendfile(Path(path))

    def _on_paste_file(self, event=None):
        if not self._chan or self._chan.closed or self._paste_busy():
            return None
        path = _clipboard_existing_path(self.root)
        if path is None:
            path = _mac_clipboard_image_file()
        if path is None:
            return None
        self._start_paste_sendfile(path)
        return "break"

    def _hide_suggestions(self) -> None:
        if self._suggest_win is not None:
            try:
                self._suggest_win.destroy()
            except tk.TclError:
                pass
        self._suggest_win = None
        self._suggest_list = None
        self._suggest_items = []

    def _show_suggestions(self, items: list[str]) -> None:
        self._hide_suggestions()
        if not items:
            return
        self._suggest_items = items
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        lst = tk.Listbox(win, height=min(8, len(items)), exportselection=False)
        lst.pack(fill=tk.BOTH, expand=True)
        for item in items:
            lst.insert(tk.END, item)
        lst.selection_set(0)
        lst.activate(0)
        lst.bind("<ButtonRelease-1>", lambda _e: self._apply_selected_suggestion())
        self.entry.update_idletasks()
        x = self.entry.winfo_rootx()
        y = self.entry.winfo_rooty() - min(160, 20 * len(items) + 8)
        if y < 0:
            y = self.entry.winfo_rooty() + self.entry.winfo_height()
        win.geometry(f"{max(self.entry.winfo_width(), 220)}x{min(160, 20 * len(items) + 4)}+{x}+{y}")
        self._suggest_win = win
        self._suggest_list = lst

    def _apply_selected_suggestion(self) -> None:
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
        self.entry.focus_set()

    def _refresh_command_suggestions(self) -> list[str]:
        text = self.var_input.get()
        items = _command_completions(text)[:12]
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
        text = self.var_input.get()
        if not text.startswith("/"):
            return "break"
        items = _command_completions(text)
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
        self._hide_suggestions()
        self._send_clicked()
        return "break"

    def _send_clicked(self) -> None:
        if not self._chan or self._chan.closed:
            return
        line = self.var_input.get()
        self.var_input.set("")
        self._hide_suggestions()
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
            # Slash commands have no [user]-prefixed broadcast; show a local hint.
            # Plain messages rely on the server broadcast (which the server also
            # delivers back to us) to render exactly once.
            if line.startswith("/"):
                self._append_chat_line(f"[*] {line}", local_sent=True)
            self._chan_send_bytes((line + "\n").encode("utf-8"))
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
        if clear_log:
            self._rooms_order = ["default"]
            self._active_room = "default"
            self._room_unread = {"default": 0}
            self._room_history = {"default": []}
            self._refresh_room_list()
            self._render_active_room()

    def _on_close(self) -> None:
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
