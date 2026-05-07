#!/usr/bin/env python3
"""
SSHChat 简易启动器：根据 JSON 中的 host / user / ssh_port 调用系统 ssh，
进入服务器上的强制命令客户端。

用户名前缀取自配置文件里的 ``user``（须与远端 SSH 登录名一致）。
本机需已安装 OpenSSH；认证与「ssh 用户名@主机」相同。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from sshchat_client_util import default_client_config_path, load_client_config

DEFAULT_CONFIG_PATH = default_client_config_path()

_MINIMAL_JSON = """
{
  "host": "chat.example.com",
  "user": "alice",
  "ssh_port": 22
}
""".strip()


def _load_config(path: Path) -> dict[str, Any]:
    cfg = load_client_config(path.expanduser().resolve())
    if cfg is None:
        print(f"[SSHChat] 找不到配置文件: {path}", file=sys.stderr)
        print(
            "[SSHChat] 请创建该文件，仅含 host、user、ssh_port（可选），例如：",
            file=sys.stderr,
        )
        print(_MINIMAL_JSON, file=sys.stderr)
        raise SystemExit(2)
    return cfg


def _build_ssh_argv(
    cfg: dict[str, Any],
    overrides: dict[str, str | None],
) -> list[str]:
    ssh_bin = shutil.which("ssh")
    if not ssh_bin:
        print(
            "[SSHChat] 未找到 ssh 命令。请安装 OpenSSH 客户端。",
            file=sys.stderr,
        )
        raise SystemExit(1)

    host = overrides.get("host") or cfg.get("host")
    user = overrides.get("user") or cfg.get("user")
    if not host or not user:
        print("[SSHChat] 配置中缺少 host 或 user。", file=sys.stderr)
        raise SystemExit(2)

    ssh_port = overrides.get("ssh_port") or cfg.get("ssh_port", 22)
    try:
        port_n = int(ssh_port)
    except (TypeError, ValueError):
        print("[SSHChat] ssh_port 必须是整数。", file=sys.stderr)
        raise SystemExit(2)

    extra = cfg.get("extra_ssh_options")
    if extra is not None and not isinstance(extra, list):
        print("[SSHChat] extra_ssh_options 必须是字符串数组。", file=sys.stderr)
        raise SystemExit(2)

    argv: list[str] = [ssh_bin, "-tt"]
    for opt in extra or []:
        if not isinstance(opt, str) or not opt.strip():
            continue
        argv.extend(["-o", opt.strip()])
    if port_n != 22:
        argv.extend(["-p", str(port_n)])
    argv.append(f"{user}@{host}")

    return argv


def main() -> int:
    parser = argparse.ArgumentParser(description="SSHChat 简易连接：根据 JSON 调用 ssh。")
    parser.add_argument(
        "--config",
        "-c",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"配置文件路径（默认: {DEFAULT_CONFIG_PATH}）",
    )
    parser.add_argument("--host", help="覆盖配置中的 host")
    parser.add_argument("--user", help="覆盖配置中的 user")
    parser.add_argument(
        "--ssh-port",
        type=int,
        help="覆盖配置中的 ssh_port（SSH 端口，非聊天端口）",
    )
    args = parser.parse_args()

    cfg = _load_config(args.config.expanduser().resolve())
    overrides: dict[str, str | None] = {
        "host": args.host,
        "user": args.user,
        "ssh_port": str(args.ssh_port) if args.ssh_port is not None else None,
    }
    argv = _build_ssh_argv(cfg, overrides)
    try:
        proc = subprocess.run(argv)
    except OSError as e:
        print(f"[SSHChat] 无法启动 ssh: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return proc.returncode if proc.returncode is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
