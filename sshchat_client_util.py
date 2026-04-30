"""Shared helpers for SSHChat local clients (CLI launcher + GUI)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# PyInstaller sets sys.frozen to True for frozen apps.
def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def default_client_config_path() -> Path:
    env = (os.environ.get("SSHCHAT_CLIENT_CONFIG") or "").strip()
    if env:
        return Path(env).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / "SSHChat" / "client.json"
    return Path.home() / ".config" / "sshchat" / "client.json"


def load_client_config(path: Path) -> dict[str, Any] | None:
    path = path.expanduser().resolve()
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def save_client_config(path: Path, data: dict[str, Any]) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def bundled_site_search_paths() -> list[Path]:
    """Paths checked in order for deploy-time client-bundle.json (embedded or sidecar)."""
    paths: list[Path] = []
    env = (os.environ.get("SSHCHAT_BUNDLE_FILE") or "").strip()
    if env:
        paths.append(Path(env).expanduser())
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        paths.append(Path(meipass) / "client-bundle.json")
    if _is_frozen():
        paths.append(Path(sys.executable).resolve().parent / "client-bundle.json")
    here = Path(__file__).resolve().parent
    paths.append(here / "client-bundle.json")
    return paths


def load_bundled_site_config() -> dict[str, Any] | None:
    """
    End-user SSH target only: hostname/IP + sshd port for "ssh user@host -p port".
    Not SSHCHAT_SERVER / chat TCP (often 127.0.0.1 on the server). When present, GUI
    installers hide these fields and only ask for the Linux username (key must match
    authorized_keys).
    """
    for p in bundled_site_search_paths():
        cfg = load_client_config(p)
        if not cfg:
            continue
        host = cfg.get("host")
        if not isinstance(host, str) or not host.strip():
            continue
        port = cfg.get("ssh_port", 22)
        try:
            port_n = int(port)
        except (TypeError, ValueError):
            port_n = 22
        out = dict(cfg)
        out["host"] = host.strip()
        out["ssh_port"] = port_n
        return out
    return None
