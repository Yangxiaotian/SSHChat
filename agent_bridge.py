"""Call the local Cursor `agent` CLI for SSHChat /agent guest links.

Security defaults (see plan):
- mode=ask: read-only tools, no Shell/Write
- mode=full: tightened allowlist + deny for secrets/destructive cmds
- default --add-dir $HOME (SSHCHAT_AGENT_HOME / HOME); opt out with SSHCHAT_AGENT_ADD_HOME=0
- redact secret-shaped text before it hits chat
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

import password_gate

# ponytail: hard cap so a stuck agent cannot pin guest WS threads forever
DEFAULT_TIMEOUT_SEC = 600
AGENT_NICK = "~agent"
MAX_UPLOAD_BYTES = 12 * 1024 * 1024
_BOT_AT = re.compile(r"(?i)(?:@|＠)bot\b")

_SECRET_RE = re.compile(
    r"(?:"
    r"sk-[A-Za-z0-9_-]{16,}"
    r"|crsr_[a-fA-F0-9]{20,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"
    r"|(?i:api[_-]?key|secret|password|passwd|token)\s*[:=]\s*\S{8,}"
    r"|(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{48,}={0,2}(?![A-Za-z0-9+/])"
    r")"
)

_TURN_PREFIX_ASK = (
    "You are ~agent on this SSHChat host in READ-ONLY (ask) mode.\n"
    "This turn is already an @bot request (the server stripped the @bot token).\n"
    "Always answer the Message below — do NOT claim it lacks @bot.\n"
    "Primary cwd is this temp folder (uploads/ for files). Stay inside it.\n"
    "You may read files and fetch docs; do NOT run shell, write files, or "
    "read secrets (.env, credentials, keys, printenv).\n"
    "If the user asks to commit/push/edit, say they need `/agent full` then "
    "`/agent allow` (HOME is add-dir by default; secrets still denied).\n"
    "Never echo secret values into chat. Keep replies brief for chat.\n\n"
    "Message:\n"
)
_TURN_PREFIX_FULL = (
    "You are ~agent on this SSHChat host in FULL mode (restricted tools).\n"
    "This turn is already an @bot request (the server stripped the @bot token).\n"
    "Always answer the Message below — do NOT claim it lacks @bot.\n"
    "Primary cwd is this temp folder (uploads/ for files).\n"
    "Shell/write are limited by allow/deny lists. Never sudo, never dump env "
    "secrets, never echo API keys/passwords into chat.\n"
    "HOME is add-dir by default (unless SSHCHAT_AGENT_ADD_HOME=0). "
    "Keep replies brief for chat.\n\n"
    "Message:\n"
)
_RESUME_HINT_ASK = (
    "(Continue: this is an @bot request; @bot was stripped by the server. "
    "You are still in READ-ONLY ask mode — no shell/write. "
    "Never claim the message lacks @bot. Never paste secrets.)\n\n"
)
_RESUME_HINT_FULL = (
    "(Continue: this is an @bot request; @bot was stripped by the server. "
    "You are in FULL mode with allow/deny limits. "
    "Never claim the message lacks @bot. Never paste secrets.)\n\n"
)

_DENY = [
    "Read(**/.env*)",
    "Read(**/*credential*)",
    "Read(**/*credentials*)",
    "Read(**/*.pem)",
    "Read(**/*.key)",
    "Read(**/id_rsa*)",
    "Read(**/id_ed25519*)",
    "Read(**/.ssh/**)",
    "Write(**/.env*)",
    "Write(**/*.pem)",
    "Write(**/*.key)",
    "Shell(sudo)",
    "Shell(rm)",
    "Shell(chmod)",
    "Shell(chown)",
    "Shell(dd)",
    "Shell(mkfs)",
    "Shell(shutdown)",
    "Shell(reboot)",
    "Shell(printenv)",
    "Shell(env)",
]


def _webfetch_allows() -> list[str]:
    raw = (os.environ.get("SSHCHAT_AGENT_WEBFETCH") or "").strip()
    if raw:
        return [f"WebFetch({p.strip()})" for p in raw.split(",") if p.strip()]
    return [
        "WebFetch(docs.cursor.com)",
        "WebFetch(*.github.com)",
        "WebFetch(docs.github.com)",
        "WebFetch(*.python.org)",
        "WebFetch(pypi.org)",
    ]


def _agent_home() -> str:
    """Home for Cursor CLI auth/files and default --add-dir.

    Prefer SSHCHAT_AGENT_HOME. Never put HOME= in shared sshchat.env (chat.sh
    sources it for every SSH user).
    """
    for key in ("SSHCHAT_AGENT_HOME", "HOME"):
        val = (os.environ.get(key) or "").strip()
        if val:
            return val
    return ""


def cli_json_for_mode(mode: str, workspace_dir: str = "") -> str:
    """Return .cursor/cli.json text for ask|full."""
    mode = "full" if mode == "full" else "ask"
    ws = (workspace_dir or "").rstrip("/") or "."
    home = _agent_home().rstrip("/")
    if mode == "ask":
        allow = [
            "Read(**)",
            "WebSearch(*)",
            *_webfetch_allows(),
        ]
    else:
        allow = [
            "Read(**)",
            f"Write({ws}/**)",
            "Write(uploads/**)",
            "WebSearch(*)",
            *_webfetch_allows(),
            "Shell(ls)",
            "Shell(pwd)",
            "Shell(cat)",
            "Shell(head)",
            "Shell(tail)",
            "Shell(rg)",
            "Shell(grep)",
            "Shell(find)",
            "Shell(git)",
            "Shell(curl)",
            "Shell(python3)",
            "Shell(python)",
            "Shell(node)",
            "Shell(npm)",
            "Shell(which)",
            "Shell(echo)",
            "Shell(uname)",
            "Shell(date)",
            "Shell(wc)",
            "Shell(sort)",
            "Shell(sed)",
            "Shell(awk)",
            "Shell(jq)",
        ]
        # full: also allow writes under HOME when it is part of the workspace view
        if home and _add_home_enabled():
            allow.append(f"Write({home}/**)")
    return json.dumps(
        {"permissions": {"allow": allow, "deny": list(_DENY)}},
        indent=2,
        ensure_ascii=False,
    )


def _add_home_enabled() -> bool:
    raw = (os.environ.get("SSHCHAT_AGENT_ADD_HOME") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def agent_bin() -> Optional[str]:
    override = (os.environ.get("SSHCHAT_AGENT_BIN") or "").strip()
    if override and os.path.isfile(override) and os.access(override, os.X_OK):
        return override
    found = shutil.which("agent") or shutil.which("cursor-agent")
    if found:
        return found
    home = _agent_home()
    if home:
        for name in ("agent", "cursor-agent"):
            path = os.path.join(home, ".local", "bin", name)
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
    return None


def tmp_root() -> str:
    return (os.environ.get("SSHCHAT_AGENT_TMP") or "/tmp/sshchat-agent").rstrip("/")


def safe_room_slug(room: str) -> str:
    s = re.sub(r"[^\w.\-]+", "_", (room or "").strip(), flags=re.UNICODE)
    s = s.strip("._")[:80]
    return s or "room"


def safe_token_slug(token: str) -> str:
    s = re.sub(r"[^\w.\-]+", "_", (token or "").strip())
    s = s.strip("._")[:64]
    return s or "link"


def workspace_for_room(room: str) -> str:
    """Room parent dir (legacy). Prefer workspace_for_link."""
    return os.path.join(tmp_root(), safe_room_slug(room))


def workspace_for_link(room: str, token: str) -> str:
    return os.path.join(tmp_root(), safe_room_slug(room), safe_token_slug(token))


def workspace() -> str:
    """Legacy fallback (tests / misconfig). Prefer per-link workspace."""
    return (
        os.environ.get("SSHCHAT_AGENT_WORKSPACE")
        or os.environ.get("CURSOR_WORKSPACE")
        or workspace_for_link("default", "default")
    )


def parse_bot_address(text: str) -> tuple[bool, str]:
    """True if message @bots the agent. Returns (hit, text with @bot stripped)."""
    raw = text or ""
    if not _BOT_AT.search(raw):
        return False, raw.strip()
    cleaned = _BOT_AT.sub(" ", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return True, cleaned


def redact_secrets(text: str) -> str:
    """Replace secret-shaped spans before chat delivery."""
    if not text:
        return text
    return _SECRET_RE.sub("[redacted]", text)


def agent_config_path() -> Path:
    raw = (os.environ.get("SSHCHAT_AGENT_CONFIG") or "").strip()
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parent / "agent" / "config.json"


def configured_agent_password() -> str:
    """Password from SSHCHAT_AGENT_PASSWORD or agent/config.json `password`."""
    return password_gate.load_password_from_config(
        env_key="SSHCHAT_AGENT_PASSWORD",
        path=agent_config_path(),
    )


def check_agent_password(user: str, password: str) -> tuple[str, dict]:
    """Verify /agent create password (ok | unset | locked | bad)."""
    return password_gate.check_password(
        scope="agent",
        user=user,
        password=password,
        expected=configured_agent_password(),
    )


def reset_agent_password_attempts(user: str = "") -> None:
    """Test helper: clear lockout state."""
    password_gate.reset_attempts(scope="agent", user=user)


def write_cli_json(path: str, mode: str = "ask") -> None:
    cursor_dir = Path(path) / ".cursor"
    cursor_dir.mkdir(parents=True, exist_ok=True)
    (cursor_dir / "cli.json").write_text(
        cli_json_for_mode(mode, workspace_dir=path), encoding="utf-8"
    )


def reset_workspace(room: str, token: str = "", *, mode: str = "ask") -> str:
    """Wipe and recreate /tmp/sshchat-agent/<room>/<token>/ for one /agent link."""
    path = (
        workspace_for_link(room, token)
        if (token or "").strip()
        else workspace_for_room(room)
    )
    shutil.rmtree(path, ignore_errors=True)
    uploads = os.path.join(path, "uploads")
    os.makedirs(uploads, mode=0o700, exist_ok=True)
    mode = "full" if mode == "full" else "ask"
    readme = Path(path) / "CONTEXT.md"
    readme.write_text(
        f"# SSHChat /agent workspace\n\n"
        f"Room: #{room}\n"
        f"Link: {safe_token_slug(token) if token else '(room)'}\n"
        f"Mode: {mode}\n"
        f"Created: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"Users address you with @bot. Files land in `uploads/`.\n"
        f"Ignore plain human chat and games unless @bot asks.\n"
        f"Never echo secrets into chat.\n",
        encoding="utf-8",
    )
    write_cli_json(path, mode=mode)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def extra_add_dirs(*, exclude: str = "") -> list[str]:
    """Host dirs passed as --add-dir.

    By default includes SSHCHAT_AGENT_HOME or HOME. Disable with
    SSHCHAT_AGENT_ADD_HOME=0. Extra paths: SSHCHAT_AGENT_ADD_DIRS (colon-separated).
    """
    out: list[str] = []
    seen: set[str] = set()
    ex = ""
    if exclude:
        try:
            ex = os.path.realpath(exclude)
        except OSError:
            ex = exclude

    def _add(path: str) -> None:
        p = (path or "").strip()
        if not p or not os.path.isdir(p):
            return
        try:
            real = os.path.realpath(p)
        except OSError:
            real = p
        if ex and real == ex:
            return
        if real in seen:
            return
        seen.add(real)
        out.append(real)

    if _add_home_enabled():
        _add(_agent_home())
    raw = (os.environ.get("SSHCHAT_AGENT_ADD_DIRS") or "").strip()
    for part in raw.split(":"):
        _add(part)
    override = (os.environ.get("SSHCHAT_AGENT_WORKSPACE") or "").strip()
    if override:
        _add(override)
    return out


def allowed_cwd_roots(link_ws: str = "") -> list[str]:
    """Directories under which /agent cwd may point."""
    roots: list[str] = []
    seen: set[str] = set()

    def _add(path: str) -> None:
        p = (path or "").strip()
        if not p or not os.path.isdir(p):
            return
        try:
            real = os.path.realpath(p)
        except OSError:
            real = p
        if real in seen:
            return
        seen.add(real)
        roots.append(real)

    _add(link_ws)
    _add((os.environ.get("SSHCHAT_AGENT_WORKSPACE") or "").strip())
    if _add_home_enabled():
        _add(_agent_home())
    for part in (os.environ.get("SSHCHAT_AGENT_ADD_DIRS") or "").split(":"):
        _add(part.strip())
    return roots


def resolve_cwd(path: str, *, link_ws: str = "") -> tuple[Optional[str], str]:
    """Resolve /agent cwd path. Returns (realpath, err) err in ok|empty|missing|denied."""
    raw = (path or "").strip()
    if not raw or raw in (".", "reset", "clear", "-"):
        # reset to link temp workspace
        if link_ws and os.path.isdir(link_ws):
            return os.path.realpath(link_ws), "ok"
        return None, "empty"
    if raw.startswith("~"):
        home = _agent_home() or os.path.expanduser("~")
        raw = os.path.join(home, raw[2:].lstrip("/")) if raw != "~" else home
    if not os.path.isabs(raw):
        base = _agent_home() or os.getcwd()
        raw = os.path.join(base, raw)
    if not os.path.isdir(raw):
        return None, "missing"
    try:
        real = os.path.realpath(raw)
    except OSError:
        return None, "missing"
    roots = allowed_cwd_roots(link_ws)
    if not roots:
        return None, "denied"
    for root in roots:
        try:
            if real == root or real.startswith(root + os.sep):
                return real, "ok"
        except Exception:
            continue
    return None, "denied"


def effective_workspace(link) -> str:
    """Primary --workspace for a GuestLink (runtime cwd or temp link dir)."""
    cwd = (getattr(link, "agent_cwd", None) or "").strip()
    if cwd and os.path.isdir(cwd):
        return cwd
    ws = (getattr(link, "agent_workspace", None) or "").strip()
    if ws:
        return ws
    override = (os.environ.get("SSHCHAT_AGENT_WORKSPACE") or "").strip()
    if override and os.path.isdir(override):
        return os.path.realpath(override)
    return workspace()


def save_upload(room_or_ws: str, filename: str, data: bytes) -> str:
    """Save bytes under uploads/. `room_or_ws` may be a room name or absolute workspace."""
    raw = data or b""
    if not raw:
        raise ValueError("empty")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("too_big")
    if os.path.isabs(room_or_ws) and (
        os.path.isdir(room_or_ws) or room_or_ws.startswith(tmp_root())
    ):
        root = room_or_ws
        os.makedirs(root, mode=0o700, exist_ok=True)
    else:
        root = workspace_for_room(room_or_ws)
    uploads = os.path.join(root, "uploads")
    os.makedirs(uploads, mode=0o700, exist_ok=True)
    base = os.path.basename(filename or "upload.bin").replace("\x00", "")
    base = re.sub(r"[^\w.\-]+", "_", base, flags=re.UNICODE)[:120] or "upload.bin"
    dest = os.path.join(uploads, f"{int(time.time())}_{base}")
    with open(dest, "wb") as f:
        f.write(raw)
    return dest


def turn(
    prompt: str,
    *,
    session_id: str = "",
    workspace_dir: str = "",
    upload_dir: str = "",
    mode: str = "ask",
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> tuple[str, str]:
    """Run one agent turn. Returns (reply_text, session_id).

    workspace_dir: primary --workspace / cwd (runtime /agent cwd).
    upload_dir: link temp dir kept as --add-dir so uploads/ stay visible.
    """
    exe = agent_bin()
    if not exe:
        return ("[*] agent CLI not found on PATH (install Cursor CLI / `agent`).", "")
    text = (prompt or "").strip()
    if not text:
        return ("", session_id or "")
    ws = (workspace_dir or "").strip() or workspace()
    os.makedirs(ws, mode=0o700, exist_ok=True)
    mode = "full" if mode == "full" else "ask"
    # Permissions live under the primary workspace .cursor/cli.json
    write_cli_json(ws, mode=mode)
    if session_id:
        hint = _RESUME_HINT_FULL if mode == "full" else _RESUME_HINT_ASK
        turn_text = f"{hint}{text}"
    else:
        prefix = _TURN_PREFIX_FULL if mode == "full" else _TURN_PREFIX_ASK
        turn_text = f"{prefix}{text}"

    cmd = [
        exe,
        "-p",
        "--output-format",
        "json",
        "--trust",
        "--workspace",
        ws,
    ]
    if mode == "ask":
        cmd.extend(["--mode", "ask"])
    else:
        # force = skip interactive prompts; still gated by cli.json allow/deny
        cmd.extend(["--force", "--sandbox", "disabled"])
    extras = extra_add_dirs(exclude=ws)
    up = (upload_dir or "").strip()
    if up and os.path.isdir(up):
        try:
            up_real = os.path.realpath(up)
        except OSError:
            up_real = up
        if up_real != os.path.realpath(ws) and up_real not in extras:
            extras.insert(0, up_real)
    for extra in extras:
        cmd.extend(["--add-dir", extra])
    if session_id:
        cmd.extend(["--resume", session_id])
    cmd.append(turn_text)

    env = os.environ.copy()
    ah = _agent_home()
    if ah:
        env["HOME"] = ah
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=max(30, int(timeout_sec)),
            env=env,
            cwd=ws,
            # Isolate from server.py's process group so agent/tool signals
            # (killpg / Ctrl-C style) cannot take down the chat daemon.
            start_new_session=True,
        )
    except subprocess.TimeoutExpired:
        return ("[*] agent timed out.", session_id or "")
    except OSError as e:
        return (f"[*] agent failed to start: {e}", session_id or "")

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if not out:
        msg = err or f"agent exited {proc.returncode}"
        return (f"[*] {msg}", session_id or "")

    reply, sid = _parse_result(out)
    if reply is None:
        reply = out
    if not sid:
        sid = session_id or ""
    return (redact_secrets(reply), sid)


def _parse_result(stdout: str) -> tuple[Optional[str], str]:
    last: Optional[dict] = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            last = obj
    if last is None:
        try:
            obj = json.loads(stdout)
        except json.JSONDecodeError:
            return None, ""
        if not isinstance(obj, dict):
            return None, ""
        last = obj
    sid = str(last.get("session_id") or "")
    if last.get("is_error"):
        result = str(last.get("result") or last.get("error") or "agent error")
        return result, sid
    if "result" in last:
        return str(last.get("result") or ""), sid
    return None, sid
