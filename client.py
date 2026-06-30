import os
import pwd
import re
import shutil
import socket
import subprocess
import sys
import threading
from collections import deque
from datetime import datetime
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.data_structures import Size
from prompt_toolkit.output.vt100 import Vt100_Output
from prompt_toolkit.patch_stdout import patch_stdout

SERVER_IP = os.environ.get("SSHCHAT_SERVER", "127.0.0.1")
PORT = int(os.environ.get("SSHCHAT_PORT", "12345"))

name = pwd.getpwuid(os.getuid()).pw_name

# beep: terminal bell | notify: desktop notification (macOS / Linux) | all | none
_ALERT = (os.environ.get("SSHCHAT_ALERT") or "beep").strip().lower()
_ALERT_SOUND = (os.environ.get("SSHCHAT_ALERT_SOUND") or "auto").strip().lower()
# One ASCII space after `]` separates sender from body; do not use `\s+` here
# or leading spaces in the message (e.g. ASCII art / board padding) are lost.
_ROOM_CHAT_PREFIX = re.compile(r"^\[#([^\]]+)\]\s+\[([^\]]+)\] (.*)$")
_CHAT_PREFIX = re.compile(r"^\[([^\]]+)\] (.*)$")
_SYSTEM_SENDERS = frozenset(("+", "!", "*"))
_STOP = threading.Event()
_DISCONNECTED = threading.Event()
_DISPLAY_TIMES: deque[datetime] = deque(maxlen=2048)
_SEND_LOCK = threading.Lock()
_PENDING_INPUT_ECHOES: deque[str] = deque(maxlen=32)
# Server sends CSI alone on one line; SSH/PTY often strips or replaces ESC (shows as "?[2J?[H").
_CLEAR_CSI_STRICT = re.compile(r"^\s*\x1b\[2J\x1b\[H\s*$")
_CLEAR_CSI_MANGLED = re.compile(r"^\s*\?\[2J\?\[H\s*$")
_SCREEN_CLEARED_ACK_RE = re.compile(r"^\[\*\]\s*Screen cleared\.\s*$")
# Xiangqi: server sends {{R}}…{{/R}} markup. SSH/PTY often eats ESC (shows "?[91m"),
# so never emit ANSI on SSH sessions unless SSHCHAT_XIANGQI_COLOR=ansi is forced.
_XQ_RED_MARK = re.compile(r"\{\{R\}\}(.*?)\{\{/R\}\}")
_XQ_BLACK_MARK = re.compile(r"\{\{B\}\}(.*?)\{\{/B\}\}")
_RAW_ANSI_INLINE = re.compile(r"\033\[[0-9;?]*[A-Za-z]")
_MANGLED_CSI_INLINE = re.compile(r"\?\[[\d;?]*[A-Za-z]")

_LIBRARY_SUBCOMMANDS = {
    "open": None,
    "read": None,
    "next": None,
    "n": None,
    "prev": None,
    "p": None,
    "page": None,
    "find": None,
    "search": None,
    "bookmarks": None,
    "bookmark": None,
    "reset": None,
    "close": None,
    "info": None,
    "help": None,
}

_GAME_SUBCOMMANDS = {
    "help": None,
    "list": None,
    "new": None,
    "join": None,
    "show": None,
    "move": None,
    "resign": None,
    "undo": None,
    "abort": None,
    "end": None,
    "on": None,
    "off": None,
    "seats": None,
    "rating": None,
    "pgn": None,
}

_NEWS_SUBCOMMANDS = {
    "中文": None,
    "国际": None,
    "科技": None,
    "all": None,
    "detail": None,
    "详情": None,
    "fetch": None,
    "全文": None,
}

_DICT_SUBCOMMANDS = {
    "en": None,
    "cn": None,
    "hh": None,
    "help": None,
    "英": None,
    "中": None,
    "汉": None,
}

_TOP_COMMANDS = (
    "/help",
    "/names",
    "/users",
    "/rooms",
    "/join",
    "/switch",
    "/part",
    "/msg",
    "/announce",
    "/game",
    "/news",
    "/library",
    "/lib",
    "/dict",
    "/clear",
    "/cls",
)

_SUBCOMMANDS_BY_CMD = {
    "/game": sorted(_GAME_SUBCOMMANDS),
    "/news": sorted(_NEWS_SUBCOMMANDS),
    "/library": sorted(_LIBRARY_SUBCOMMANDS),
    "/lib": sorted(_LIBRARY_SUBCOMMANDS),
    "/dict": sorted(_DICT_SUBCOMMANDS),
}


class SSHChatCommandCompleter(Completer):
    """Prefix-complete top-level /commands and nested subcommands."""

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return

        if " " not in text:
            prefix = text
            for cmd in _TOP_COMMANDS:
                if cmd.startswith(prefix):
                    yield Completion(cmd, start_position=-len(prefix))
            return

        if not text.endswith(" "):
            parts = text.split()
            if len(parts) == 1:
                prefix = parts[0]
                for cmd in _TOP_COMMANDS:
                    if cmd.startswith(prefix):
                        yield Completion(cmd, start_position=-len(prefix))
                return
            cmd = parts[0].lower()
            sub_prefix = parts[-1]
            for sub in _SUBCOMMANDS_BY_CMD.get(cmd, ()):
                if sub.startswith(sub_prefix):
                    yield Completion(sub, start_position=-len(sub_prefix))
            return

        cmd = text.rstrip().split()[0].lower()
        for sub in _SUBCOMMANDS_BY_CMD.get(cmd, ()):
            yield Completion(sub + " ", start_position=0)


_COMMAND_COMPLETER = SSHChatCommandCompleter()


def _terminal_size() -> Size:
    try:
        wh = shutil.get_terminal_size()
        return Size(rows=wh.lines, columns=wh.columns)
    except OSError:
        return Size(rows=24, columns=80)


def _spawn_quiet(cmd: list[str]) -> bool:
    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


def _alert_beep() -> None:
    # Terminal bell first; some terminals mute this by default.
    print("\a", end="", flush=True)
    # macOS audible fallback when terminal bell is disabled.
    if shutil.which("osascript"):
        subprocess.run(
            ["osascript", "-e", "beep 1"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    # Stronger macOS fallback for SSH sessions: play a system sound file.
    if shutil.which("afplay"):
        sound = "/System/Library/Sounds/Glass.aiff"
        if os.path.exists(sound):
            _spawn_quiet(["afplay", sound])
    if _ALERT_SOUND in ("none", "off", "0"):
        return
    # Linux fallback for terminals that mute BEL:
    # try configured backend or auto-detect order.
    backends = ["canberra", "paplay", "aplay"] if _ALERT_SOUND == "auto" else [_ALERT_SOUND]
    for backend in backends:
        if backend == "canberra" and shutil.which("canberra-gtk-play"):
            if _spawn_quiet(["canberra-gtk-play", "-i", "message-new-instant", "-d", "SSHChat"]):
                return
        if backend == "paplay" and shutil.which("paplay"):
            for sound in (
                "/usr/share/sounds/freedesktop/stereo/message.oga",
                "/usr/share/sounds/freedesktop/stereo/complete.oga",
            ):
                if os.path.exists(sound) and _spawn_quiet(["paplay", sound]):
                    return
        if backend == "aplay" and shutil.which("aplay"):
            for sound in (
                "/usr/share/sounds/alsa/Front_Center.wav",
                "/usr/share/sounds/alsa/Noise.wav",
            ):
                if os.path.exists(sound):
                    _spawn_quiet(["aplay", "-q", sound])
                    return


def _alert_notify(sender: str, preview: str) -> None:
    title = "SSHChat"
    subtitle = sender
    body = preview if preview else "(message)"
    if shutil.which("osascript"):
        # argv avoids brittle AppleScript string escaping
        subprocess.run(
            [
                "osascript",
                "-e",
                "on run argv\n"
                '\tdisplay notification (item 1 of argv) with title '
                "(item 2 of argv) subtitle (item 3 of argv)\n"
                "end run",
                body[:400],
                title,
                subtitle[:200],
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    elif shutil.which("notify-send"):
        subprocess.run(
            ["notify-send", "-a", title, f"{title} — {subtitle}", body[:400]],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )


def maybe_alert_incoming(sender: str, preview: str) -> None:
    if _ALERT in ("", "none", "0", "off"):
        return
    if _ALERT in ("beep", "all", "both"):
        _alert_beep()
    if _ALERT in ("notify", "all", "both"):
        _alert_notify(sender, preview)


def _parse_chat_line(line: str) -> tuple[str, str, str]:
    """Return (room, sender, payload); room is empty for legacy format."""
    t = line.rstrip("\r")
    m = _ROOM_CHAT_PREFIX.match(t)
    if m:
        return m.group(1), m.group(2), m.group(3)
    m = _CHAT_PREFIX.match(t)
    if m:
        return "", m.group(1), m.group(2)
    return "", "", ""


def _line_is_peer_chat(line: str, my_name: str) -> tuple[bool, str, str]:
    """Return (is_peer_chat, sender, preview) for a single line without trailing \\n."""
    _room, sender, payload = _parse_chat_line(line)
    if not sender:
        return False, "", ""
    if sender in _SYSTEM_SENDERS or sender == my_name:
        return False, sender, payload
    return True, sender, payload


def _format_time(ts: datetime) -> str:
    return ts.strftime("%H:%M:%S")


def _xiangqi_use_ansi() -> bool:
    pref = (
        os.environ.get("SSHCHAT_XIANGQI_COLOR")
        or os.environ.get("SSHCHAT_COLOR")
        or "auto"
    ).strip().lower()
    if pref in ("0", "off", "none", "no", "plain", "paren", "markers"):
        return False
    if pref in ("ansi", "color", "1", "yes", "on"):
        if os.environ.get("SSHCHAT_XIANGQI_COLOR_FORCE") == "1":
            return sys.stdout.isatty()
        if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_CLIENT"):
            return False
        return sys.stdout.isatty()
    # auto: local TTY only; SSH chat sessions use (红) / <黑> markers instead.
    if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_CLIENT"):
        return False
    return sys.stdout.isatty()


def _expand_xiangqi_color(text: str) -> str:
    """Render xiangqi markup for this terminal (ANSI, or SSH-safe parentheses)."""
    text = _RAW_ANSI_INLINE.sub("", text)
    text = _MANGLED_CSI_INLINE.sub("", text)
    if _xiangqi_use_ansi():

        def _red(m: re.Match[str]) -> str:
            return f"\033[91m{m.group(1)}\033[0m"

        def _black(m: re.Match[str]) -> str:
            return f"\033[1;37m{m.group(1)}\033[0m"

        text = _XQ_RED_MARK.sub(_red, text)
        return _XQ_BLACK_MARK.sub(_black, text)
    # Legacy markup → +/-/! prefix form.
    text = _XQ_RED_MARK.sub(r"+\1", text)
    text = _XQ_BLACK_MARK.sub(r"-\1", text)
    text = re.sub(r"【(.*?)】", r"+\1", text)
    text = re.sub(r"〔(.*?)〕", r"-\1", text)
    return text


def _format_display_line(line: str, my_name: str) -> str:
    # If line was already decorated by a local renderer, avoid double-prefixing.
    if re.match(r"^\[\d{2}:\d{2}:\d{2}\] ", line):
        return _expand_xiangqi_color(
            line + ("\n" if not line.endswith("\n") else "")
        )
    ts = datetime.now()
    _DISPLAY_TIMES.append(ts)
    time_label = _format_time(ts)
    room, sender, payload = _parse_chat_line(line)
    if not sender:
        return _expand_xiangqi_color(f"[{time_label}] {line}\n")
    if room:
        body = _expand_xiangqi_color(payload)
        return f"[{time_label}] [#{room}] [{sender}] {body}\n"
    body = _expand_xiangqi_color(payload)
    return f"[{time_label}] [{sender}] {body}\n"


def _should_skip_display_line(line: str) -> bool:
    t = line.strip()
    if not t:
        return True
    # prompt redraw or local input echo fragments from PTY.
    if t == ">" or t.startswith("> "):
        return True
    return False


def _remember_sent_input(payload: str) -> None:
    with _SEND_LOCK:
        _PENDING_INPUT_ECHOES.append(payload)


def _consume_sent_input_echo(line: str) -> bool:
    t = line.strip()
    if not t:
        return False
    with _SEND_LOCK:
        if t in _PENDING_INPUT_ECHOES:
            _PENDING_INPUT_ECHOES.remove(t)
            return True
    return False


def _terminal_hard_clear() -> None:
    """Clear the real terminal; needed when CSI bytes are mangled in SSH/PTY."""
    if not sys.stdout.isatty():
        return
    try:
        if sys.platform == "win32":
            os.system("cls")
        elif shutil.which("clear"):
            subprocess.run(["clear"], check=False)
        else:
            sys.stdout.buffer.write(b"\x1b[2J\x1b[H")
            sys.stdout.flush()
    except Exception:
        pass


def _is_clear_csi_line(line: str) -> bool:
    t = line.strip()
    return bool(_CLEAR_CSI_STRICT.match(t) or _CLEAR_CSI_MANGLED.match(t))


def recv_msg(sock, my_name: str):
    byte_buf = bytearray()
    while True:
        try:
            data = sock.recv(1024)
            if not data:
                print("\n[ERROR] server disconnected")
                _DISCONNECTED.set()
                break

            byte_buf.extend(data)
            while True:
                nl = byte_buf.find(b"\n")
                if nl < 0:
                    break
                line_bytes = bytes(byte_buf[:nl])
                del byte_buf[: nl + 1]
                line_bytes = line_bytes.replace(b"\r", b"")
                text = line_bytes.decode("utf-8", errors="replace").replace("\a", "")

                ok, sender, preview = _line_is_peer_chat(text, my_name)
                if ok:
                    maybe_alert_incoming(sender, preview)

                if _should_skip_display_line(text):
                    continue
                if _consume_sent_input_echo(text):
                    continue
                if _is_clear_csi_line(text):
                    continue
                if _SCREEN_CLEARED_ACK_RE.match(text.strip()):
                    _terminal_hard_clear()
                out = _format_display_line(text, my_name)
                print(out, end="", flush=True)

        except Exception:
            print("\n[ERROR] receive failed")
            _DISCONNECTED.set()
            break

    sock.close()
    _STOP.set()


def main():
    _STOP.clear()
    _DISCONNECTED.clear()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        s.connect((SERVER_IP, PORT))
    except Exception:
        print("[ERROR] cannot connect to server")
        return

    s.send((name + "\n").encode("utf-8"))

    print("[OK] connected as " + name)
    print(
        "Commands: /names  /rooms  /join <room>  /switch <room>  "
        "/msg #<room> <text> | /msg <nick> <text>  /part <room>  "
        "/announce  /game  /news  /news fetch <类> <号>  /dict  /library (/lib)  /clear  /help"
    )
    print("Tip: type / then press Tab to complete commands (like a shell).")
    print(
        f"Alerts (SSHCHAT_ALERT={_ALERT}): beep | notify | all | none — "
        "peer chat lines only"
    )
    print(
        "Alert sound backend "
        f"(SSHCHAT_ALERT_SOUND={_ALERT_SOUND}): auto | canberra | paplay | aplay | none"
    )

    threading.Thread(target=recv_msg, args=(s, name), daemon=True).start()

    # Some forced-command SSH sessions may not provide a TTY for prompt_toolkit.
    use_prompt_toolkit = sys.stdin.isatty() and sys.stdout.isatty()
    if not use_prompt_toolkit:
        print("[*] non-interactive terminal detected; fallback input mode")

    if use_prompt_toolkit:
        # GUI / Paramiko / some PTYs do not answer CPR (cursor position requests);
        # prompt_toolkit then prints a noisy WARNING on each prompt without this.
        ptk_session = PromptSession(
            output=Vt100_Output(sys.stdout, _terminal_size, enable_cpr=False),
            completer=_COMMAND_COMPLETER,
            complete_while_typing=True,
        )
        with patch_stdout():
            while True:
                if _STOP.is_set():
                    print("[INFO] disconnected")
                    break
                try:
                    msg = ptk_session.prompt("> ")

                    if msg.strip() == "":
                        continue

                    _remember_sent_input(msg)
                    s.send(("[" + name + "] " + msg + "\n").encode("utf-8"))

                except (KeyboardInterrupt, EOFError):
                    print("\n[INFO] exit")
                    break
                except Exception:
                    print("[ERROR] send failed")
                    break
    else:
        while True:
            if _STOP.is_set():
                print("[INFO] disconnected")
                break
            try:
                sys.stdout.write("> ")
                sys.stdout.flush()
                msg = sys.stdin.readline()
                if msg == "":
                    print("\n[INFO] stdin closed")
                    break
                msg = msg.rstrip("\r\n")
                if msg.strip() == "":
                    continue
                _remember_sent_input(msg)
                s.send(("[" + name + "] " + msg + "\n").encode("utf-8"))
            except (KeyboardInterrupt, EOFError):
                print("\n[INFO] exit")
                break
            except Exception:
                print("[ERROR] send failed")
                break

    s.close()
    if _DISCONNECTED.is_set():
        # Let chat.sh decide whether to auto-restart client.
        return 75
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
