from __future__ import annotations

import os
import getpass
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.data_structures import Size
from prompt_toolkit.output.vt100 import Vt100_Output
from prompt_toolkit.patch_stdout import StdoutProxy, patch_stdout

from sshchat_client_util import (
    default_client_config_path,
    extract_completion_hints,
    load_client_config,
    name_arg_completions,
    save_client_config,
)

SERVER_IP = os.environ.get("SSHCHAT_SERVER", "127.0.0.1")
PORT = int(os.environ.get("SSHCHAT_PORT", "12345"))

# ``pwd`` is Unix-only; the terminal client also runs on Windows.
name = getpass.getuser() or os.environ.get("USERNAME") or os.environ.get("USER") or "user"

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
_DND_TURN_HINT = "Your turn to move"
_DND_TURN_HINT_ZH = "轮到你操作"
_DND_LOCK = threading.Lock()
_DND_HINT_LOCK = threading.Lock()
_DND_LAST_HINT_AT = 0.0
_DND_HINT_COOLDOWN = 1.5
_SEND_LOCK = threading.Lock()
_GAME_BYPASS_SECONDS = 45.0
_GAME_BYPASS_UNTIL = 0.0
_PENDING_INPUT_ECHOES: deque[str] = deque(maxlen=32)
# Server sends CSI alone on one line; SSH/PTY often strips or replaces ESC (shows as "?[2J?[H").
_CLEAR_CSI = b"\x1b[2J\x1b[H"
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
    "show": None,
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

_DND_SUBCOMMANDS = {
    "on": None,
    "off": None,
}

_GAME_NAMES = (
    "chess", "xiangqi", "gomoku", "go", "reversi", "darkchess", "battleship",
    "junqi", "doushou", "sanguo", "werewolf", "drawguess", "holdem", "zjh", "niutou", "mahjong",
)

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

_LANG_SUBCOMMANDS = {
    "en": None,
    "zh": None,
    "english": None,
    "chinese": None,
    "中文": None,
    "英文": None,
}

_SUBCOMMANDS_BY_CMD = {
    "/game": sorted(_GAME_SUBCOMMANDS),
    "/news": sorted(_NEWS_SUBCOMMANDS),
    "/library": sorted(_LIBRARY_SUBCOMMANDS),
    "/lib": sorted(_LIBRARY_SUBCOMMANDS),
    "/dict": sorted(_DICT_SUBCOMMANDS),
    "/dnd": sorted(_DND_SUBCOMMANDS),
    "/lang": sorted(_LANG_SUBCOMMANDS),
    "/language": sorted(_LANG_SUBCOMMANDS),
}

_NESTED_SUBCOMMANDS: dict[tuple[str, str], tuple[str, ...]] = {
    ("/game", "undo"): ("accept", "reject", "cancel"),
    ("/game", "new"): _GAME_NAMES,
    ("/game", "on"): _GAME_NAMES,
    ("/game", "off"): _GAME_NAMES,
}

# Learned from /rooms, /names, and chat traffic for Tab completion.
_KNOWN_ROOMS: set[str] = {"default"}
_KNOWN_USERS: set[str] = set()
_COMPLETION_LOCK = threading.Lock()


def _remember_completion_hints(*, rooms: list[str] | None = None, users: list[str] | None = None) -> None:
    with _COMPLETION_LOCK:
        if rooms:
            for r in rooms:
                key = r.strip().lstrip("#")
                if key:
                    _KNOWN_ROOMS.add(key)
        if users:
            for u in users:
                key = u.strip()
                if key and key not in _SYSTEM_SENDERS:
                    _KNOWN_USERS.add(key)


def _completion_rooms() -> list[str]:
    with _COMPLETION_LOCK:
        return sorted(_KNOWN_ROOMS, key=str.lower)


def _completion_users() -> list[str]:
    with _COMPLETION_LOCK:
        return sorted(_KNOWN_USERS, key=str.lower)


def _absorb_completion_line(text: str) -> None:
    rooms, users = extract_completion_hints(text)
    if rooms or users:
        _remember_completion_hints(rooms=rooms, users=users)


class SSHChatCommandCompleter(Completer):
    """Prefix-complete top-level /commands, nested subcommands, and room/nick args."""

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

        parts = text.split()
        if not text.endswith(" "):
            if len(parts) == 1:
                prefix = parts[0]
                for cmd in _TOP_COMMANDS:
                    if cmd.startswith(prefix):
                        yield Completion(cmd, start_position=-len(prefix))
                return
            if len(parts) >= 3:
                cmd = parts[0].lower()
                sub = parts[1].lower()
                prefix = parts[-1].lower()
                nested = _NESTED_SUBCOMMANDS.get((cmd, sub), ())
                for item in nested:
                    if item.startswith(prefix):
                        yield Completion(item, start_position=-len(parts[-1]))
                if nested:
                    return
            cmd = parts[0].lower()
            sub_prefix = parts[-1]
            matched_sub = False
            for sub in _SUBCOMMANDS_BY_CMD.get(cmd, ()):
                if sub.startswith(sub_prefix):
                    matched_sub = True
                    yield Completion(sub, start_position=-len(sub_prefix))
            if matched_sub:
                return
            # Room / nick argument (replace from command start via full strings).
            for full in name_arg_completions(
                text, rooms=_completion_rooms(), users=_completion_users()
            ):
                # Replace from start of current arg.
                yield Completion(full.split(" ", 1)[-1], start_position=-len(parts[-1]))
            return

        parts = text.rstrip().split()
        if len(parts) >= 2:
            cmd = parts[0].lower()
            sub = parts[1].lower()
            nested = _NESTED_SUBCOMMANDS.get((cmd, sub), ())
            if nested:
                for item in nested:
                    yield Completion(item + " ", start_position=0)
                return
        cmd = parts[0].lower()
        subs = _SUBCOMMANDS_BY_CMD.get(cmd, ())
        if subs:
            for sub in subs:
                yield Completion(sub + " ", start_position=0)
            return
        for full in name_arg_completions(
            text, rooms=_completion_rooms(), users=_completion_users()
        ):
            arg = full.split(" ", 1)[-1]
            yield Completion(arg + " ", start_position=0)


def _load_dnd_setting() -> bool:
    env = (os.environ.get("SSHCHAT_DND") or "").strip().lower()
    if env in ("1", "on", "yes", "true"):
        return True
    if env in ("0", "off", "no", "false"):
        return False
    cfg = load_client_config(default_client_config_path())
    if isinstance(cfg, dict) and isinstance(cfg.get("doNotDisturb"), bool):
        return cfg["doNotDisturb"]
    return False


def _persist_dnd(enabled: bool) -> None:
    path = default_client_config_path()
    cfg = load_client_config(path) or {}
    cfg["doNotDisturb"] = enabled
    save_client_config(path, cfg)


def _set_dnd(enabled: bool) -> None:
    global _DND_ENABLED, _GAME_BYPASS_UNTIL
    with _DND_LOCK:
        _DND_ENABLED = enabled
        if enabled:
            _GAME_BYPASS_UNTIL = 0.0
    _persist_dnd(enabled)


def _dnd_enabled() -> bool:
    with _DND_LOCK:
        return _DND_ENABLED


def _note_game_command() -> None:
    global _GAME_BYPASS_UNTIL
    with _DND_LOCK:
        _GAME_BYPASS_UNTIL = time.monotonic() + _GAME_BYPASS_SECONDS


def _clear_game_bypass() -> None:
    global _GAME_BYPASS_UNTIL
    with _DND_LOCK:
        _GAME_BYPASS_UNTIL = 0.0


def _game_bypass_active() -> bool:
    with _DND_LOCK:
        return time.monotonic() < _GAME_BYPASS_UNTIL


_DND_ENABLED = _load_dnd_setting()


def _is_reading_content_line(payload: str) -> bool:
    """News/library/help lines should never be suppressed in DND mode."""
    t = payload.strip()
    if not t:
        return False
    if re.match(r"^---.+---$", t):
        return True
    if re.match(r"^\d+\.\s+\[", t):
        return True
    if t.startswith("《") or "【图书馆】" in t or t.startswith("[图书馆]") or "[Library]" in t:
        return True
    if t.startswith("Usage:") or t.startswith("用法：") or t.startswith("用法:"):
        return True
    if t.startswith("/news") or t.startswith("/library") or t.startswith("/lib") or t.startswith("/lang"):
        return True
    return False


def _is_game_flood_line(payload: str) -> bool:
    raw = payload.strip()
    t = raw.lower()
    if not t:
        return False
    if (
        re.match(r"^\d+\s*,\s*\d+$", t)
        or re.match(r"^\d{1,2}\s+(?:[.#o●○·#]\s+){4,}", raw, re.I)
        or re.match(r"^\d{1,2}\s+(?:[.#o●○·]\s+){8,}[.#o●○·]\s*$", raw, re.I)
        or re.match(r"^(?:\d{1,2}\s+){8,}\d{1,2}\s*$", raw)
        or re.match(r"^((row|turn|state|pot|street|current_bet)\s*[:=])", t)
        or re.match(r"^#\d+\s+[^:：]+[:：]", raw)
        or re.match(r"^-\s+\S+\s+\((alive|out)\)", raw, re.I)
        or re.match(r"^\s+\d+(?:\s+\d+){4,}\s*$", raw)
        or re.match(r"^\s*\d{1,2}\s+(?:\([.#o#●○]\)|[.#o#●○])(?:\s+(?:\([.#o#●○]\)|[.#o#●○])){4,}", raw, re.I)
    ):
        return True
    keywords = (
        "当前房间正在进行",
        "可直接加入",
        "同一房间同一时刻仅允许一场进行中的对局",
        "可玩游戏",
        "Playable games",
        "你的手牌",
        "Your hand",
        "公共牌",
        "community cards",
        "贴目",
        "提子",
        "停一手",
        "底池=",
        "当前注=",
        "当前注：",
        "落子：",
        "走子：",
        "轮到：",
        "Turn:",
        "to move",
        "轮到 黑",
        "轮到 白",
        "轮到 红",
        "轮到 黑方",
        "轮到 白方",
        "轮到 红方",
        "Not your turn",
        "gomoku",
        "围棋",
        "chess",
        "xiangqi",
        "reversi",
        "darkchess",
        "battleship",
        "junqi",
        "holdem",
        "zjh",
        "niutou",
        "sanguo",
        "werewolf",
        "drawguess",
        "doushou",
        "斗兽棋",
        "国际象棋",
        "五子棋",
        "黑白棋",
        "暗棋",
        "翻翻棋",
        "海战棋",
        "军棋",
        "中国象棋",
        "德州扑克",
        "炸金花",
        "牛头王",
        "三国杀",
        "狼人杀",
        "你画我猜",
        "对局",
        "开了一局",
        "上一步",
        "己方在下方",
        "楚河汉界",
        "图例：",
        "等宽字体",
        "被将军",
        "将军）",
        "rating=",
        "Leaderboard",
    )
    return any(k.lower() in t for k in keywords)


def _is_game_context_line(payload: str) -> bool:
    """Broader than flood: covers game show headers/ratings that arrive before the board."""
    if _is_game_flood_line(payload):
        return True
    t = payload.strip()
    if not t:
        return False
    if "积分体系" in t or "积分=" in t or "等级=" in t or "战绩=" in t:
        return True
    if "rating=" in t.lower() or "level=" in t.lower() or "W/L/D=" in t:
        return True
    if re.search(r"对局[（(]", t) or "对局状态" in t:
        return True
    if re.match(r"^#\d+\s+\S", t):
        return True
    if re.match(r"^(黑|白|红)方\s+\S", t) or re.match(r"^\s+(黑|白|红)方", t):
        return True
    if re.match(r"^(黑|白|红)[：:]", t):
        return True
    if re.match(r"^(红|黑|白)方\s+\S+\s+走\s+", t):
        return True
    if re.match(r"^[+\-!·]", t) and len(t) > 6:
        return True
    if "←" in t and re.search(r"(红|黑|白)方", t):
        return True
    if "纵线" in t and "方" in t:
        return True
    if re.match(r"^[+\-!·*]", t) and ("楚河" in t or len(t) > 12):
        return True
    if re.match(r"^(alive|players|votes|state|street)[:：]", t, re.I):
        return True
    if re.match(r"^row\d+[:：]", t, re.I) or re.match(r"^第[1-4]行", t):
        return True
    if "方走 " in t or "方 走 " in t or "走子" in t or "行棋" in t:
        return True
    if re.search(r"方\s+\S+\s+落子", t) or re.search(r"方\s+\S+\s+停一手", t):
        return True
    if re.search(r"[♔♕♖♗♘♙♚♛♜♝♞♟]", payload):
        return True
    if re.match(r"^[a-h](?:\s+[a-h]){7}\s*$", t):
        return True
    if re.match(
        r"^(go|chess|gomoku|xiangqi|doushou|reversi|darkchess|battleship|junqi|holdem|zjh|niutou|sanguo|werewolf|drawguess|mahjong)\b",
        t,
    ):
        return True
    if re.match(r"^(三国杀|牛头王|斗兽棋|德州扑克|炸金花|狼人|你画我猜|麻将)", t):
        return True
    if t.startswith("劫点") or "闷牌" in t or "已弃牌" in t or "已看牌" in t:
        return True
    if "当前回合" in t or "牌堆" in t or "军争" in t:
        return True
    if "【" in t and "】" in t:
        return True
    return False


def _parse_turn_name(payload: str) -> str:
    for line in payload.split("\n"):
        trimmed = line.strip()
        if re.match(r"^(turn|轮到)[:：]", trimmed, re.I):
            name = re.sub(r"^(turn|轮到)[:：]\s*", "", trimmed, flags=re.I).strip()
            return name.split()[0] if name else ""
        m = re.match(r"^轮到\s+(?:黑|白|红)(?:方)?\s+(\S+)", trimmed)
        if m:
            return re.split(r"[（(]", m.group(1))[0].strip()
        m = re.match(
            r"^(?:Black|White|Red)(?:\s+to\s+move)?[:：]\s*(\S+)",
            trimmed,
            re.I,
        )
        if m:
            return re.split(r"[（(]", m.group(1))[0].strip()
        m = re.match(r"^Turn:\s*(\S+)", trimmed, re.I)
        if m:
            return re.split(r"[（(]", m.group(1))[0].strip()
    return ""


def _is_my_turn_line(payload: str, my_name: str) -> bool:
    if not my_name:
        return False
    turn = _parse_turn_name(payload)
    if turn and turn == my_name:
        return True
    trimmed = payload.strip()
    m = re.match(r"^轮到[：:]\s*(.+)$", trimmed)
    if m and m.group(1).strip() == my_name:
        return True
    m = re.match(r"^Turn[:：]\s*(.+)$", trimmed, re.I)
    return bool(m and m.group(1).strip().split()[0] == my_name)


def _is_game_error_line(payload: str) -> bool:
    """Game command errors must always be visible in DND mode."""
    t = payload.strip()
    if not t:
        return False
    markers = (
        "不是你的回合",
        "Not your turn",
        "无法",
        "cannot",
        "用法：",
        "用法:",
        "Usage:",
        "错误",
        "error",
        "非法",
        "无效",
        "invalid",
        "失败",
        "failed",
        "未知",
        "unknown",
        "未进行",
        "未开始",
    )
    return any(m in t for m in markers)


def _is_game_command_feedback_line(payload: str) -> bool:
    """Command outcomes and status lines must stay visible in DND mode."""
    if _is_game_error_line(payload):
        return True
    t = payload.strip()
    if not t:
        return False
    if t.startswith("Terminal:"):
        return True
    markers = (
        "已有进行",
        "没有进行",
        "席位",
        "已经是",
        "你不是",
        "加入为",
        "加入了",
        "开了一局",
        "无法开局",
        "未上线",
        "请先",
        "只有房主",
        "检测到你在",
        "已自动续玩",
        "从另一终端",
        "对局开始",
        "可 /game join",
        "可 /game show",
        "AI 练习局",
        "已在本房",
        "执行失败",
        "对局已结束",
        "等待白方",
        "等待黑方",
        "等另一位玩家",
    )
    return any(m in t for m in markers)


def _is_dnd_game_session_command(cmd: str) -> bool:
    lower = cmd.strip().lower()
    if not lower.startswith("/game"):
        return False
    parts = lower.split(None, 2)
    if len(parts) < 2:
        return False
    return parts[1] in frozenset(
        (
            "join",
            "new",
            "end",
            "resign",
            "abort",
            "undo",
            "leave",
            "sit",
            "stand",
        )
    )


def _dnd_print_game_session_ack(cmd: str) -> None:
    lower = cmd.strip().lower()
    parts = lower.split(None, 2)
    action = parts[1] if len(parts) >= 2 else ""
    hints = {
        "join": "Join request sent (DND hides the board; see tips below)",
        "new": "New-game request sent (DND hides the board; see tips below)",
        "end": "End-game request sent",
        "resign": "Resign request sent",
        "abort": "Abort request sent",
        "undo": "Undo request sent",
    }
    hint = hints.get(action, "Game command sent")
    print(f"[*] {hint}\n", end="", flush=True)


def _is_dnd_game_action_command(cmd: str) -> bool:
    lower = cmd.strip().lower()
    if not lower.startswith("/game"):
        return False
    parts = lower.split(None, 2)
    if len(parts) < 2:
        return False
    action = parts[1]
    action_subs = frozenset(
        (
            "move",
            "play",
            "pass",
            "resign",
            "fold",
            "call",
            "raise",
            "check",
            "bet",
            "allin",
            "弃牌",
            "跟注",
            "加注",
            "过牌",
            "看牌",
            "比牌",
            "受击",
            "拼点",
        )
    )
    return action in action_subs


def _dnd_print_game_action_ack() -> None:
    print("[*] Move submitted (DND hides board updates)\n", end="", flush=True)


def _dnd_system_action(payload: str, my_name: str) -> str | None:
    """
    Return None to show normally, '' to suppress, 'turn_hint' for compact turn line.

    Turn handling is evaluated before the /game show bypass so that:
    - opponent-to-move clears a peek bypass (avoid watching their thinking time);
    - my-to-move reopens bypass so the oriented board that follows can display.
    """
    if not _dnd_enabled():
        return None
    if _is_game_command_feedback_line(payload):
        return None
    if _is_reading_content_line(payload):
        return None
    turn = _parse_turn_name(payload)
    if turn:
        if my_name and turn == my_name:
            # Opponent just finished; private oriented board arrives next — let it through.
            _note_game_command()
            return "turn_hint"
        # /game show while waiting ends with「轮到 对方」; drop the peek bypass
        # so 对方继续行棋时不会再刷局面。
        _clear_game_bypass()
        return ""
    if _game_bypass_active():
        return None
    if _is_game_context_line(payload):
        return ""
    return None


def _dnd_print_turn_hint(my_name: str) -> None:
    global _DND_LAST_HINT_AT
    now = time.monotonic()
    with _DND_HINT_LOCK:
        if now - _DND_LAST_HINT_AT < _DND_HINT_COOLDOWN:
            return
        _DND_LAST_HINT_AT = now
    out = _format_display_line(f"[*] {_DND_TURN_HINT}", my_name)
    print(out, end="", flush=True)


def _is_dnd_game_read_command(cmd: str) -> bool:
    """While DND is on, only read-only /game commands may show full board output."""
    lower = cmd.strip().lower()
    if not lower.startswith("/game"):
        return False
    readonly = (
        "/game show",
        "/game seats",
        "/game help",
        "/game list",
        "/game rating",
        "/game pgn",
    )
    return any(lower == prefix or lower.startswith(prefix + " ") for prefix in readonly)


def _try_handle_local_command(msg: str) -> bool:
    stripped = msg.strip()
    lower = stripped.lower()
    if lower == "/dnd":
        state = "on" if _dnd_enabled() else "off"
        print(f"[*] Do-not-disturb: {state} (/dnd on | /dnd off)")
        return True
    if lower == "/dnd on":
        already = _dnd_enabled()
        _set_dnd(True)
        if already:
            print("[*] DND already on; game broadcast filtering restored")
        else:
            print("[*] DND on: game broadcasts suppressed; one-line hint on your turn")
        return True
    if lower == "/dnd off":
        _set_dnd(False)
        print("[*] DND off")
        return True
    if lower in ("/clear", "/cls"):
        _terminal_hard_clear()
        return True
    return False


def _prepare_outgoing(msg: str) -> bool:
    if _try_handle_local_command(msg):
        return False
    lower = msg.strip().lower()
    if lower.startswith("/game"):
        if not _dnd_enabled() or _is_dnd_game_read_command(lower):
            _note_game_command()
        elif _is_dnd_game_session_command(lower):
            _dnd_print_game_session_ack(lower)
        elif _is_dnd_game_action_command(lower):
            _dnd_print_game_action_ack()
    return True


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
    # System messages (games, news, library) display without timestamp/room prefix
    if sender in _SYSTEM_SENDERS:
        body = _expand_xiangqi_color(payload)
        return f"[{sender}] {body}\n"
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


def _get_real_stdout():
    stdout = sys.stdout
    if isinstance(stdout, StdoutProxy):
        original = stdout.original_stdout
        if original is not None:
            return original
    return sys.__stdout__


def _terminal_is_tty() -> bool:
    real = _get_real_stdout()
    return bool(real is not None and real.isatty())


def _clear_stdout_proxy_pending() -> None:
    stdout = sys.stdout
    if not isinstance(stdout, StdoutProxy):
        return
    try:
        with stdout._lock:
            stdout._buffer.clear()
    except Exception:
        pass


def _clear_with_prompt_toolkit_output() -> bool:
    """Clear via prompt_toolkit renderer/output; return True when attempted."""
    try:
        from prompt_toolkit.application import get_app_or_none

        app = get_app_or_none()
        if app is not None and app.is_running:
            import asyncio

            async def _clear_renderer() -> None:
                app.renderer.clear()

            asyncio.run_coroutine_threadsafe(_clear_renderer(), app.loop).result(timeout=1)
            return True

        stdout = sys.stdout
        if isinstance(stdout, StdoutProxy):
            output = stdout._output
            output.erase_screen()
            output.cursor_goto(0, 0)
            output.flush()
            return True
    except Exception:
        return False
    return False


def _write_real_clear_csi() -> None:
    real = _get_real_stdout()
    if real is None:
        return
    try:
        if not real.isatty():
            return
        if hasattr(real, "buffer"):
            real.buffer.write(_CLEAR_CSI)
        else:
            real.write(_CLEAR_CSI.decode("ascii"))
        real.flush()
    except Exception:
        pass


def _raw_terminal_clear() -> None:
    _write_real_clear_csi()


def _clear_terminal_with_prompt_sync() -> None:
    """Clear screen without desyncing prompt_toolkit's prompt rendering."""
    if not _terminal_is_tty():
        return
    _clear_stdout_proxy_pending()
    _clear_with_prompt_toolkit_output()
    _write_real_clear_csi()


def _terminal_hard_clear() -> None:
    """Clear the real terminal; keep prompt_toolkit cursor state in sync when active."""
    _clear_terminal_with_prompt_sync()


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
                _absorb_completion_line(text)
                if _is_clear_csi_line(text):
                    _terminal_hard_clear()
                    continue
                if _SCREEN_CLEARED_ACK_RE.match(text.strip()):
                    _terminal_hard_clear()
                    continue
                _room, sender, payload = _parse_chat_line(text)
                if sender in _SYSTEM_SENDERS:
                    dnd_action = _dnd_system_action(payload, my_name)
                    if dnd_action == "":
                        continue
                    if dnd_action == "turn_hint":
                        _dnd_print_turn_hint(my_name)
                        continue
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
        "/msg #<room> <text> | /msg <nick> <text> (offline=leave msg)  "
        "/sendfile | /sendfile <nick> | /sendfile #<room>  "
        "/canvas | /canvas <nick> | /canvas #<room>  "
        "/leave [nick]|<nick> <n>  /part <room>  "
        "/announce  /game  /news  /news fetch <cat> <n>  /dict  /library (/lib)  "
        "/lang en|zh  /dnd on|off  /clear  /help"
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
    if _dnd_enabled():
        print(
            "[*] DND is on (/dnd off to disable; only /game show and similar "
            "read commands temporarily show the full board)"
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

                    if not _prepare_outgoing(msg):
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
                if not _prepare_outgoing(msg):
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
