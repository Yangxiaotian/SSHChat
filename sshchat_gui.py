#!/usr/bin/env python3
"""
SSHChat 图形客户端：通过 SSH（与命令行相同）进入服务端强制命令聊天界面，
在窗口中显示远端输出并发送输入。需已安装 paramiko，见 requirements-gui.txt。
"""

from __future__ import annotations

import argparse
import codecs
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from collections import deque
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk
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
_RESIDUAL_ONLY_RE = re.compile(r"[\s,，。！？!?、;；:：\->]*")
_CHAT_LINE_RE = re.compile(r"^\[([^\]]+)\]\s*(.*)$")
_ROOM_CHAT_LINE_RE = re.compile(r"^\[#([^\]]+)\]\s+\[([^\]]+)\]\s*(.*)$")
_TIME_PREFIX_RE = re.compile(r"^\[\d{2}:\d{2}:\d{2}\]\s*")
_TIME_ANY_RE = re.compile(r"\[\d{2}:\d{2}:\d{2}\]\s*")
_CTRL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_LEADING_GARBAGE_RE = re.compile(r"^[\s\uFFFD\u25A1\uFEFF\u00A0]+")
_SPACE_RE = re.compile(r"\s+")
_MAX_ROOM_HISTORY = 4000
_DRAIN_BATCH_ITEMS = 200


def _clean_chunk(s: str) -> str:
    s = _OSC_RE.sub("", s)
    s = _CSI_RE.sub("", s)
    s = _OTHER_ESC_RE.sub("", s)
    s = _CTRL_CHARS_RE.sub("", s)
    # Keep line semantics for CRLF-based streams from PTY/SSH.
    s = s.replace("\r\n", "\n").replace("\r", "\n")
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
        all_matches = re.findall(r"\[([^\]]+)\]\s*([^\[]*)", t)
        if all_matches:
            sender, body = all_matches[-1]
            return "", sender.strip(), body.strip()
        return None
    return "", m.group(1), m.group(2)


def _normalize_payload_text(s: str) -> str:
    return _SPACE_RE.sub(" ", s.strip())


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
        self._reader_thread: threading.Thread | None = None
        self._connect_thread: threading.Thread | None = None
        self._out_q: queue.Queue[tuple[int, str | None]] = queue.Queue()
        self._drain_after_id: str | int | None = None
        self._connecting = threading.Event()
        self._line_buf = ""
        self._session_id = 0
        self._pending_lock = threading.Lock()
        self._pending_sent: deque[str] = deque(maxlen=64)
        self._display_times: deque[datetime] = deque(maxlen=2048)
        self._alert_sound = (os.environ.get("SSHCHAT_ALERT_SOUND") or "auto").strip().lower()
        self._rooms_order: list[str] = ["default"]
        self._active_room = "default"
        self._room_unread: dict[str, int] = {"default": 0}
        self._room_history: dict[str, list[tuple[str, str]]] = {"default": []}

        self._build_ui()
        self._apply_profile(load_client_config(self.config_path))
        self.root.bind("<Map>", self._on_window_mapped, add="+")
        self.root.bind("<Unmap>", self._on_window_unmapped, add="+")
        self._is_minimized = False

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _remember_sent(self, payload: str) -> None:
        text = payload
        if not text:
            return
        with self._pending_lock:
            self._pending_sent.append(text)

    def _consume_sent_exact(self, payload: str) -> bool:
        normalized_payload = _normalize_payload_text(payload)
        with self._pending_lock:
            for sent in list(self._pending_sent):
                normalized_sent = _normalize_payload_text(sent)
                if (
                    sent == payload
                    or normalized_sent == normalized_payload
                    or normalized_sent in normalized_payload
                    or normalized_payload in normalized_sent
                ):
                    self._pending_sent.remove(sent)
                    return True
        return False

    def _consume_if_prompt_echo(self, line: str) -> bool:
        t = line.strip()
        if not t or "[" in t:
            return False
        if ">" not in t:
            return False
        normalized = _PROMPT_PREFIX_RE.sub(" ", t).strip()
        if not normalized:
            return True
        with self._pending_lock:
            pending = list(self._pending_sent)
        for sent in reversed(pending):
            if not sent:
                continue
            if sent not in normalized:
                continue
            rest = normalized.replace(sent, " ")
            if _RESIDUAL_ONLY_RE.fullmatch(rest):
                with self._pending_lock:
                    if sent in self._pending_sent:
                        self._pending_sent.remove(sent)
                return True
        return False

    def _should_drop_line(self, line: str) -> bool:
        normalized = _strip_time_prefixes(line)
        if _skip_line(normalized):
            return True
        parsed = _parse_chat_line(normalized.rstrip("\r"))
        if parsed:
            me = self.var_user.get().strip()
            _room, sender, body = parsed
            sender = sender.strip()
            if me and sender == me:
                # Server echo of our own message; we already displayed it locally.
                self._consume_sent_exact(body)
                return True
        if self._consume_if_prompt_echo(normalized):
            return True
        return False

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

        bot = ttk.Frame(self.root)
        bot.pack(fill=tk.X, padx=8, pady=(0, 8))
        self.var_input = tk.StringVar()
        self.entry = ttk.Entry(bot, textvariable=self.var_input)
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.entry.bind("<Return>", lambda e: self._send_clicked())
        ttk.Button(bot, text="发送", command=self._send_clicked).pack(
            side=tk.LEFT, padx=(8, 0)
        )
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
            if tag:
                self.log.insert(tk.END, text, (tag,))
            else:
                self.log.insert(tk.END, text)
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
                self._chan.send((f"/switch {room}\n").encode("utf-8"))
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

    def _append_room_entry(self, room: str, text: str, tag: str = "") -> None:
        self._ensure_room(room)
        history = self._room_history[room]
        history.append((text, tag))
        if len(history) > _MAX_ROOM_HISTORY:
            del history[: len(history) - _MAX_ROOM_HISTORY]
        if room == self._active_room and not self._is_minimized:
            self.log.configure(state=tk.NORMAL)
            if tag:
                self.log.insert(tk.END, text, (tag,))
            else:
                self.log.insert(tk.END, text)
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
        ts = datetime.now()
        self._display_times.append(ts)
        time_label = self._format_time(ts)
        parsed = _parse_chat_line(t)
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
        self._set_status("连接失败")
        messagebox.showerror("SSHChat", msg)

    def _reader_loop(self, sid: int) -> None:
        assert self._chan is not None
        dec = codecs.getincrementaldecoder("utf-8")(errors="replace")
        chan = self._chan
        try:
            while True:
                try:
                    data = chan.recv(65536)
                except Exception:
                    break
                if not data:
                    break
                text = _clean_chunk(dec.decode(data, final=False))
                if not text:
                    continue
                out: list[str] = []
                for ch in text:
                    if ch == "\r":
                        self._line_buf = ""
                    elif ch == "\b":
                        self._line_buf = self._line_buf[:-1]
                    elif ch == "\n":
                        if not self._should_drop_line(self._line_buf):
                            out.append(self._line_buf + "\n")
                        self._line_buf = ""
                    else:
                        self._line_buf += ch
                if out:
                    for line in out:
                        self._out_q.put((sid, line))
        finally:
            try:
                dec.decode(b"", final=True)
            except Exception:
                pass
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

    def _send_clicked(self) -> None:
        if not self._chan or self._chan.closed:
            return
        line = self.var_input.get()
        self.var_input.set("")
        if not line.strip():
            return
        try:
            self._remember_sent(line)
            low = line.strip()
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
            me = self.var_user.get().strip() or "me"
            if not line.startswith("/"):
                self._append_chat_line(f"[#{self._active_room}] [{me}] {line}", local_sent=True)
            else:
                self._append_chat_line(f"[*] {line}", local_sent=True)
            self._chan.send((line + "\n").encode("utf-8"))
        except Exception as e:
            messagebox.showerror("SSHChat", f"发送失败: {e}")
            self._disconnect()

    def _disconnect(self, clear_log: bool = True) -> None:
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
        self._line_buf = ""

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
