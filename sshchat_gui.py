#!/usr/bin/env python3
"""
SSHChat 图形客户端：通过 SSH（与命令行相同）进入服务端强制命令聊天界面，
在窗口中显示远端输出并发送输入。需已安装 paramiko，见 requirements-gui.txt。
"""

from __future__ import annotations

import argparse
import codecs
import queue
import re
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
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


def _strip_ansi(s: str) -> str:
    s = _OSC_RE.sub("", s)
    s = _CSI_RE.sub("", s)
    s = _OTHER_ESC_RE.sub("", s)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    return s


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
        self._out_q: queue.Queue[str | None] = queue.Queue()
        self._drain_after_id: str | int | None = None
        self._connecting = threading.Event()

        self._build_ui()
        self._apply_profile(load_client_config(self.config_path))

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        pad = {"padx": 6, "pady": 4}
        if self._bundle:
            bh = str(self._bundle["host"]).strip()
            bp = int(self._bundle.get("ssh_port", 22))
            top = ttk.Frame(self.root)
            top.pack(fill=tk.X, **pad)
            ttk.Label(
                top,
                text=f"SSH 服务器（已内置）: {bh}    端口 {bp}",
            ).pack(anchor="w")
            ttk.Label(
                top,
                text="请输入服务器上的 Linux 用户名。认证与命令行 ssh 相同：使用本机 ~/.ssh 下标准私钥与 ssh-agent（不在此选择密钥文件）。",
                wraplength=640,
            ).pack(anchor="w", pady=(6, 0))
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
            ttk.Label(
                self.root,
                text="认证：默认 ~/.ssh 与 ssh-agent，与系统 ssh 客户端一致。",
            ).pack(anchor="w", padx=10, pady=(0, 2))

        opts = ttk.Frame(self.root)
        opts.pack(fill=tk.X, padx=6, pady=(0, 4))
        self.var_strict_host = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            opts,
            text="严格校验主机密钥（使用本机 ~/.ssh/known_hosts，未知主机将拒绝）",
            variable=self.var_strict_host,
        ).pack(side=tk.LEFT)

        bar = ttk.Frame(self.root)
        bar.pack(fill=tk.X, padx=6, pady=(0, 4))
        self.btn_connect = ttk.Button(bar, text="连接", command=self._connect_clicked)
        self.btn_connect.pack(side=tk.LEFT)
        self.btn_disconnect = ttk.Button(
            bar, text="断开", command=self._disconnect, state=tk.DISABLED
        )
        self.btn_disconnect.pack(side=tk.LEFT, padx=(8, 0))
        save_lbl = "保存（用户名）" if self._bundle else "保存配置"
        ttk.Button(bar, text=save_lbl, command=self._save_profile_clicked).pack(
            side=tk.RIGHT
        )

        self.var_status = tk.StringVar(value="就绪")
        ttk.Label(self.root, textvariable=self.var_status).pack(
            anchor="w", padx=10, pady=(0, 2)
        )

        mono = tkfont.nametofont("TkFixedFont")
        self.log = scrolledtext.ScrolledText(
            self.root,
            height=20,
            wrap=tk.WORD,
            font=mono,
            state=tk.DISABLED,
        )
        self.log.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 4))

        bot = ttk.Frame(self.root)
        bot.pack(fill=tk.X, padx=8, pady=(0, 8))
        self.var_input = tk.StringVar()
        self.entry = ttk.Entry(bot, textvariable=self.var_input)
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.entry.bind("<Return>", lambda e: self._send_clicked())
        ttk.Button(bot, text="发送", command=self._send_clicked).pack(
            side=tk.LEFT, padx=(8, 0)
        )

    def _append_log(self, text: str) -> None:
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, text)
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

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

    def _save_profile_clicked(self) -> None:
        try:
            data = self._collect_profile_dict()
        except ValueError as e:
            messagebox.showwarning("SSHChat", str(e))
            return
        try:
            save_client_config(self.config_path, data)
        except OSError as e:
            messagebox.showerror("SSHChat", f"无法写入配置: {e}")
            return
        self._set_status(f"已保存: {self.config_path}")

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
            if self.var_strict_host.get():
                ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
            else:
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
        self.btn_disconnect.configure(state=tk.NORMAL)
        self._set_status("已连接（SSH 会话）")
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()
        self._schedule_drain()

    def _connect_failed(self, msg: str) -> None:
        self.btn_connect.configure(state=tk.NORMAL)
        self._ssh = None
        self._chan = None
        self._set_status("连接失败")
        messagebox.showerror("SSHChat", msg)

    def _reader_loop(self) -> None:
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
                text = _strip_ansi(dec.decode(data, final=False))
                if text:
                    self._out_q.put(text)
        finally:
            try:
                dec.decode(b"", final=True)
            except Exception:
                pass
            self._out_q.put(None)

    def _schedule_drain(self) -> None:
        self._drain_after_id = self.root.after(40, self._drain_queue)

    def _drain_queue(self) -> None:
        self._drain_after_id = None
        buf: list[str] = []
        try:
            while True:
                item = self._out_q.get_nowait()
                if item is None:
                    self._on_remote_eof()
                    return
                buf.append(item)
        except queue.Empty:
            pass
        if buf:
            self._append_log("".join(buf))
        ch = self._chan
        if ch is not None and not ch.closed:
            self._schedule_drain()

    def _on_remote_eof(self) -> None:
        if self._ssh is None and self._chan is None:
            return
        self._append_log("\n[本地] 与服务器的连接已结束。\n")
        self._disconnect(clear_log=False)
        self._set_status("已断开")

    def _send_clicked(self) -> None:
        if not self._chan or self._chan.closed:
            return
        line = self.var_input.get()
        self.var_input.set("")
        try:
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
            self.log.configure(state=tk.NORMAL)
            self.log.delete("1.0", tk.END)
            self.log.configure(state=tk.DISABLED)

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
