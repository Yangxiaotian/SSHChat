"""HTML page and request helpers for the shared room piano (served by FileHTTP).

UI: embedded piano keyboard. Sync: note on/off events over URL+key → ticket gate.
Sample MP3s from Wscats/piano (public/samples/piano), served at /piano-samples/.
"""

from __future__ import annotations

import html
import json
import mimetypes
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

import piano_sharing

if TYPE_CHECKING:
    from http.server import BaseHTTPRequestHandler

# Wscats/piano notes.js — 61 notes C2–C7
PIANO_NOTES: Dict[str, str] = {
    "A2": "a54.mp3",
    "A3": "a69.mp3",
    "A4": "a80.mp3",
    "A5": "a74.mp3",
    "A6": "a66.mp3",
    "A#3": "b69.mp3",
    "A#4": "b80.mp3",
    "A#5": "b74.mp3",
    "A#6": "b66.mp3",
    "B2": "a55.mp3",
    "B3": "a82.mp3",
    "B4": "a65.mp3",
    "B5": "a75.mp3",
    "B6": "a78.mp3",
    "C2": "a49.mp3",
    "C3": "a56.mp3",
    "C4": "a84.mp3",
    "C5": "a83.mp3",
    "C6": "a76.mp3",
    "C7": "a77.mp3",
    "C#2": "b49.mp3",
    "C#3": "b56.mp3",
    "C#4": "b84.mp3",
    "C#5": "b83.mp3",
    "C#6": "b76.mp3",
    "D2": "a50.mp3",
    "D3": "a57.mp3",
    "D4": "a89.mp3",
    "D5": "a68.mp3",
    "D6": "a90.mp3",
    "D#2": "b50.mp3",
    "D#3": "b57.mp3",
    "D#4": "b89.mp3",
    "D#5": "b68.mp3",
    "D#6": "b90.mp3",
    "E2": "a51.mp3",
    "E3": "a48.mp3",
    "E4": "a85.mp3",
    "E5": "a70.mp3",
    "E6": "a88.mp3",
    "F2": "a52.mp3",
    "F3": "a81.mp3",
    "F4": "a73.mp3",
    "F5": "a71.mp3",
    "F6": "a67.mp3",
    "F#2": "b52.mp3",
    "F#3": "b81.mp3",
    "F#4": "b73.mp3",
    "F#5": "b71.mp3",
    "F#6": "b67.mp3",
    "G2": "a53.mp3",
    "G3": "a87.mp3",
    "G4": "a79.mp3",
    "G5": "a72.mp3",
    "G6": "a86.mp3",
    "G#2": "b53.mp3",
    "G#3": "b87.mp3",
    "G#4": "b79.mp3",
    "G#5": "b72.mp3",
    "G#6": "b86.mp3",
}

# Desktop: white keys = consecutive physical keys (low→high); blacks on upper row / numpad.
_WHITE_NAMES = ("C", "D", "E", "F", "G", "A", "B")
_DESKTOP_WHITE_CODES: List[str] = [
    "KeyZ", "KeyX", "KeyC", "KeyV", "KeyB", "KeyN", "KeyM", "Comma", "Period", "Slash",
    "KeyA", "KeyS", "KeyD", "KeyF", "KeyG", "KeyH", "KeyJ", "KeyK", "KeyL", "Semicolon", "Quote",
    "BracketLeft", "BracketRight", "Backslash",
    "Digit1", "Digit2", "Digit3", "Digit4", "Digit5", "Digit6", "Digit7", "Digit8", "Digit9", "Digit0", "Minus", "Equal",
]
_DESKTOP_BLACK_CODES: List[str] = [
    "KeyQ", "KeyW", "KeyE", "KeyR", "KeyT", "KeyY", "KeyU", "KeyI", "KeyO", "KeyP",
    "Backquote",
    "Numpad7", "Numpad8", "Numpad9", "NumpadSubtract",
    "Numpad4", "Numpad5", "Numpad6", "NumpadAdd",
    "Numpad1", "Numpad2", "Numpad3", "NumpadEnter",
    "Numpad0",
]
_CODE_LABELS: Dict[str, str] = {
    "Backquote": "`",
    "Digit1": "1", "Digit2": "2", "Digit3": "3", "Digit4": "4", "Digit5": "5",
    "Digit6": "6", "Digit7": "7", "Digit8": "8", "Digit9": "9", "Digit0": "0",
    "Minus": "-", "Equal": "=",
    "KeyQ": "Q", "KeyW": "W", "KeyE": "E", "KeyR": "R", "KeyT": "T", "KeyY": "Y",
    "KeyU": "U", "KeyI": "I", "KeyO": "O", "KeyP": "P",
    "BracketLeft": "[", "BracketRight": "]", "Backslash": "\\",
    "KeyA": "A", "KeyS": "S", "KeyD": "D", "KeyF": "F", "KeyG": "G", "KeyH": "H",
    "KeyJ": "J", "KeyK": "K", "KeyL": "L", "Semicolon": ";", "Quote": "'",
    "KeyZ": "Z", "KeyX": "X", "KeyC": "C", "KeyV": "V", "KeyB": "B", "KeyN": "N",
    "KeyM": "M", "Comma": ",", "Period": ".", "Slash": "/",
    "Numpad7": "Num7", "Numpad8": "Num8", "Numpad9": "Num9", "NumpadSubtract": "Num-",
    "Numpad4": "Num4", "Numpad5": "Num5", "Numpad6": "Num6", "NumpadAdd": "Num+",
    "Numpad1": "Num1", "Numpad2": "Num2", "Numpad3": "Num3", "NumpadEnter": "Num↵",
    "Numpad0": "Num0", "NumpadDecimal": "Num.",
}


def _ordered_white_notes() -> List[str]:
    out: List[str] = []
    for oct in range(2, 8):
        for name in _WHITE_NAMES:
            note = f"{name}{oct}"
            if note in PIANO_NOTES:
                out.append(note)
    return out


def _ordered_black_notes() -> List[str]:
    out: List[str] = []
    for oct in range(2, 8):
        for name in ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"):
            if "#" not in name:
                continue
            note = f"{name}{oct}"
            if note in PIANO_NOTES:
                out.append(note)
    return out


_WHITE_NOTES = _ordered_white_notes()
_BLACK_NOTES = _ordered_black_notes()
assert len(_DESKTOP_WHITE_CODES) >= len(_WHITE_NOTES), "desktop white key row too short"
assert len(_DESKTOP_BLACK_CODES) >= len(_BLACK_NOTES), "desktop black key row too short"

NOTE_TO_CODE: Dict[str, str] = {}
for i, note in enumerate(_WHITE_NOTES):
    NOTE_TO_CODE[note] = _DESKTOP_WHITE_CODES[i]
for i, note in enumerate(_BLACK_NOTES):
    NOTE_TO_CODE[note] = _DESKTOP_BLACK_CODES[i]

KEY_TO_NOTE: Dict[str, str] = {code: note for note, code in NOTE_TO_CODE.items()}
NOTE_TO_BIND: Dict[str, str] = {
    note: _CODE_LABELS.get(code, code) for note, code in NOTE_TO_CODE.items()
}


def _key_entry(name: Optional[str]) -> Dict[str, Any]:
    if not name:
        return {"name": None, "bind": None, "code": None}
    code = NOTE_TO_CODE.get(name)
    return {
        "name": name,
        "bind": NOTE_TO_BIND.get(name),
        "code": code,
    }


PIANO_KEYS: List[Dict[str, Any]] = []
for _oct in range(2, 7):
    PIANO_KEYS.extend(
        [
            {"white": _key_entry(f"C{_oct}"), "black": _key_entry(f"C#{_oct}")},
            {"white": _key_entry(f"D{_oct}"), "black": _key_entry(f"D#{_oct}")},
            {"white": _key_entry(f"E{_oct}"), "black": _key_entry(None)},
            {"white": _key_entry(f"F{_oct}"), "black": _key_entry(f"F#{_oct}")},
            {"white": _key_entry(f"G{_oct}"), "black": _key_entry(f"G#{_oct}")},
            {"white": _key_entry(f"A{_oct}"), "black": _key_entry(f"A#{_oct}")},
            {"white": _key_entry(f"B{_oct}"), "black": _key_entry(None)},
        ]
    )
PIANO_KEYS.append({"white": _key_entry("C7"), "black": _key_entry(None)})

# Mobile: split keyboard into octave bands (GarageBand-style segments).
PIANO_SEGMENTS: List[Dict[str, Any]] = [
    {"id": "low", "from": 2, "to": 3, "label_en": "Low · C2–B3", "label_zh": "低音 · C2–B3"},
    {"id": "mid", "from": 4, "to": 5, "label_en": "Mid · C4–B5", "label_zh": "中音 · C4–B5"},
    {"id": "high", "from": 6, "to": 7, "label_en": "High · C6–C7", "label_zh": "高音 · C6–C7"},
]

PIANO_TEXTS = {
    "en": {
        "title": "SSHChat Room Piano",
        "gate_title": "Enter access key",
        "gate_sub": "Open the link from chat, then type the 6-character key shown separately",
        "key_label": "Access key *",
        "key_placeholder": "Enter 6-character key",
        "unlock": "Unlock piano",
        "verifying": "Verifying...",
        "alert_key": "Please enter the 6-character key",
        "retry": "Retry",
        "you": "You",
        "room": "Room",
        "expires": "Expires",
        "hint": "Desktop: whites Z→M,A→',[,1→= (36 keys, low→high); blacks Q→P,`, numpad. Phone: three stacked rows.",
        "seg_low": "Low · C2–B3",
        "seg_mid": "Mid · C4–B5",
        "seg_high": "High · C6–C7",
        "status_ready": "Connected",
        "status_sync": "Syncing…",
        "status_err": "Sync error — will retry",
        "closed": "This piano is closed or expired",
        "loading": "Loading piano…",
        "record": "Record",
        "stop": "Stop",
        "export": "Export",
        "share": "Share link",
        "recording": "Recording…",
        "export_ok": "Recording saved to your device",
        "share_ok": "Replay link copied to clipboard",
        "share_fail": "Could not create share link",
        "share_empty": "Record something first",
        "replay_title": "Piano replay",
        "replay_by": "By",
        "replay_play": "Play",
        "replay_pause": "Pause",
        "replay_restart": "Restart",
        "replay_not_found": "Recording not found or expired",
        "replay_loading": "Loading replay…",
    },
    "zh": {
        "title": "SSHChat 房间钢琴",
        "gate_title": "输入访问密钥",
        "gate_sub": "打开聊天里发来的网址，再输入单独给出的 6 位密钥",
        "key_label": "访问密钥 *",
        "key_placeholder": "输入6位密钥",
        "unlock": "进入钢琴",
        "verifying": "验证中...",
        "alert_key": "请输入6位密钥",
        "retry": "重试",
        "you": "你",
        "room": "房间",
        "expires": "过期",
        "hint": "电脑：白键 Z→M、A→'、[→\\、1→= 连续排列（低音→高音）；黑键 Q→P、`、小键盘。手机：三行分段。",
        "seg_low": "低音 · C2–B3",
        "seg_mid": "中音 · C4–B5",
        "seg_high": "高音 · C6–C7",
        "status_ready": "已连接",
        "status_sync": "同步中…",
        "status_err": "同步出错，将自动重试",
        "closed": "钢琴已关闭或过期",
        "loading": "正在加载钢琴…",
        "record": "录制",
        "stop": "停止",
        "export": "导出",
        "share": "分享链接",
        "recording": "录制中…",
        "export_ok": "录制已保存到本地",
        "share_ok": "重放链接已复制到剪贴板",
        "share_fail": "无法创建分享链接",
        "share_empty": "请先录制一段演奏",
        "replay_title": "钢琴重放",
        "replay_by": "演奏者",
        "replay_play": "播放",
        "replay_pause": "暂停",
        "replay_restart": "重播",
        "replay_not_found": "录制不存在或已过期",
        "replay_loading": "正在加载重放…",
    },
}

_SAMPLES_DIR = Path(__file__).resolve().parent / "piano_samples"
_SAMPLE_FNAME_RE = re.compile(r"^[ab]\d{2}\.mp3$", re.IGNORECASE)
_VALID_SAMPLES = frozenset(PIANO_NOTES.values())


def samples_dir() -> Path:
    return Path(os.environ.get("SSHCHAT_PIANO_SAMPLES_DIR", str(_SAMPLES_DIR)))


def _safe_sample_fname(fname: str) -> Optional[str]:
    fname = (fname or "").strip()
    if not fname or not _SAMPLE_FNAME_RE.match(fname):
        return None
    low = fname.lower()
    if low not in _VALID_SAMPLES:
        return None
    return low


def generate_piano_page(token: str, lang: str = "en") -> str:
    lang = "zh" if str(lang or "").lower().startswith("zh") else "en"
    S = PIANO_TEXTS[lang]
    html_lang = "zh-CN" if lang == "zh" else "en"
    i18n = {
        "alertKey": S["alert_key"],
        "verifying": S["verifying"],
        "retry": S["retry"],
        "unlock": S["unlock"],
        "statusReady": S["status_ready"],
        "statusSync": S["status_sync"],
        "statusErr": S["status_err"],
        "you": S["you"],
        "room": S["room"],
        "expires": S["expires"],
        "closed": S["closed"],
        "loading": S["loading"],
        "record": S["record"],
        "stop": S["stop"],
        "export": S["export"],
        "share": S["share"],
        "recording": S["recording"],
        "exportOk": S["export_ok"],
        "shareOk": S["share_ok"],
        "shareFail": S["share_fail"],
        "shareEmpty": S["share_empty"],
    }
    notes_json = json.dumps(PIANO_NOTES, ensure_ascii=False)
    keys_json = json.dumps(PIANO_KEYS, ensure_ascii=False)
    key_map_json = json.dumps(KEY_TO_NOTE, ensure_ascii=False)
    segments_json = json.dumps(
        [
            {
                "id": s["id"],
                "from": s["from"],
                "to": s["to"],
                "label": s["label_zh" if lang == "zh" else "label_en"],
            }
            for s in PIANO_SEGMENTS
        ],
        ensure_ascii=False,
    )
    return f"""<!DOCTYPE html>
<html lang="{html_lang}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>{html.escape(S['title'])}</title>
    <style>
        :root {{
            --ink: #1a1f2e;
            --paper: #f7f3ea;
            --line: #d9d0c0;
            --accent: #c45c26;
            --accent-2: #2f6f6a;
            --remote: #5b4d9e;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        html, body {{ height: 100%; height: 100dvh; }}
        body {{
            font-family: "IBM Plex Sans", "Segoe UI", "PingFang SC", "Noto Sans SC", sans-serif;
            background:
                radial-gradient(circle at 12% 18%, rgba(196,92,38,0.14), transparent 42%),
                radial-gradient(circle at 88% 8%, rgba(47,111,106,0.16), transparent 40%),
                linear-gradient(160deg, #ebe4d6 0%, #dfe8e4 55%, #efe8dc 100%);
            color: var(--ink);
        }}
        .wrap {{
            max-width: 1200px;
            margin: 0 auto;
            padding: 16px;
            height: 100%;
            display: flex;
            flex-direction: column;
        }}
        .wrap.piano-on {{
            max-width: none;
            padding: 0;
        }}
        .card {{
            background: rgba(247, 243, 234, 0.94);
            border: 1px solid var(--line);
            border-radius: 18px;
            box-shadow: 0 18px 50px rgba(40, 30, 20, 0.12);
            overflow: hidden;
            flex: 1;
            display: flex;
            flex-direction: column;
            min-height: 0;
        }}
        .wrap.piano-on .card {{
            border-radius: 0;
            border: 0;
            box-shadow: none;
        }}
        .header {{
            padding: 18px 20px 12px;
            border-bottom: 1px solid var(--line);
            background: linear-gradient(120deg, rgba(196,92,38,0.08), rgba(47,111,106,0.08));
            flex-shrink: 0;
        }}
        .wrap.piano-on .header {{ display: none; }}
        h1 {{
            font-family: "Fraunces", "Songti SC", Georgia, serif;
            font-size: 26px;
            font-weight: 600;
        }}
        .sub {{ opacity: 0.75; margin-top: 6px; font-size: 14px; }}
        .gate, .stage-wrap {{ padding: 20px; }}
        .stage-wrap {{
            display: none;
            flex: 1;
            flex-direction: column;
            min-height: 0;
            padding-bottom: 12px;
        }}
        .wrap.piano-on .stage-wrap {{ padding: 8px 10px 10px; }}
        label {{ display: block; font-size: 13px; margin-bottom: 8px; opacity: 0.8; }}
        input[type=text] {{
            width: 100%;
            max-width: 280px;
            padding: 12px 14px;
            border: 1px solid var(--line);
            border-radius: 10px;
            font-size: 18px;
            letter-spacing: 0.2em;
            text-transform: uppercase;
            background: #fffdf8;
        }}
        button {{
            border: 0;
            border-radius: 999px;
            padding: 11px 18px;
            font-size: 14px;
            cursor: pointer;
            background: var(--accent);
            color: white;
        }}
        button:disabled {{ opacity: 0.5; cursor: not-allowed; }}
        .meta {{
            font-size: 13px;
            opacity: 0.7;
            margin-bottom: 8px;
            display: flex;
            flex-wrap: wrap;
            gap: 12px;
            flex-shrink: 0;
        }}
        .room-badge {{
            font-size: 12px;
            font-weight: 600;
            padding: 4px 10px;
            border-radius: 999px;
            background: rgba(47,111,106,0.16);
            color: var(--accent-2);
            white-space: nowrap;
        }}
        .toolbar {{
            display: flex;
            flex-wrap: wrap;
            gap: 12px;
            align-items: center;
            margin-bottom: 8px;
            flex-shrink: 0;
        }}
        .status {{
            margin-left: auto;
            font-size: 12px;
            padding: 4px 10px;
            border-radius: 999px;
            background: rgba(47,111,106,0.12);
            color: var(--accent-2);
        }}
        .status.err {{ background: rgba(196,92,38,0.15); color: var(--accent); }}
        .tb-btn {{
            background: rgba(47,111,106,0.14);
            color: var(--accent-2);
            padding: 6px 12px;
            font-size: 12px;
        }}
        .tb-btn.recording {{
            background: rgba(196,92,38,0.22);
            color: var(--accent);
        }}
        .tb-btn:disabled {{ opacity: 0.4; cursor: not-allowed; }}
        .rec-time {{
            font-size: 12px;
            font-variant-numeric: tabular-nums;
            opacity: 0.75;
            min-width: 3.2em;
        }}
        .piano-scroll {{
            flex: 1;
            min-height: 0;
            overflow-x: auto;
            overflow-y: hidden;
            -webkit-overflow-scrolling: touch;
            border: 1px solid var(--line);
            border-radius: 12px;
            background: linear-gradient(-65deg, #000, #222, #000, #666, #222 75%);
            padding: 8px 12px 16px;
            touch-action: manipulation;
        }}
        .piano-stack {{
            display: none;
            flex-direction: column;
            gap: 8px;
            height: 100%;
            min-height: 0;
        }}
        .piano-scroll.segmented {{
            overflow-x: hidden;
            overflow-y: auto;
        }}
        .piano-scroll.segmented .piano-stack {{
            display: flex;
            flex-direction: column;
            gap: 6px;
            height: 100%;
            min-height: 100%;
        }}
        .piano-scroll.segmented > .piano {{ display: none; }}
        .piano-segment {{
            flex: 1 1 33%;
            min-height: 88px;
            display: flex;
            flex-direction: column;
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 10px;
            padding: 4px 6px 6px;
            background: rgba(0, 0, 0, 0.22);
        }}
        .piano-segment__label {{
            flex-shrink: 0;
            font-size: 10px;
            letter-spacing: 0.04em;
            color: rgba(255, 255, 255, 0.72);
            margin-bottom: 4px;
            padding-left: 2px;
        }}
        .piano-segment .piano {{
            flex: 1;
            height: auto;
            min-height: 72px;
            max-height: none;
            width: 100%;
            touch-action: none;
        }}
        .piano-segment .piano-key {{
            flex: 0 0 calc(100% / var(--slot-count, 14));
            width: calc(100% / var(--slot-count, 14));
            max-width: calc(100% / var(--slot-count, 14));
            min-width: 0;
        }}
        .piano-segment .piano-key__black {{
            width: 58%;
            right: -29%;
        }}
        .piano {{
            display: flex;
            height: 22vh;
            min-height: 140px;
            max-height: 220px;
            justify-content: flex-start;
            align-items: stretch;
            user-select: none;
            touch-action: manipulation;
        }}
        .piano-key {{
            position: relative;
            flex: 0 0 auto;
            width: 44px;
            height: 100%;
        }}
        .piano-key__white {{
            width: 100%;
            height: 100%;
            border-radius: 0 0 6px 6px;
            background: linear-gradient(-30deg, #f8f8f8, #fff);
            border: 1px solid #bbb;
            box-shadow: inset 0 -4px 8px rgba(0,0,0,0.08);
            cursor: pointer;
            position: relative;
            z-index: 1;
        }}
        .piano-key__black {{
            position: absolute;
            top: 0;
            right: -14px;
            width: 28px;
            height: 62%;
            border-radius: 0 0 4px 4px;
            background: linear-gradient(-20deg, #222, #000, #222);
            border: 1px solid #111;
            box-shadow: 2px 2px 6px rgba(0,0,0,0.45);
            cursor: pointer;
            z-index: 2;
        }}
        .piano-key__white.active {{
            background: linear-gradient(-20deg, #3330fb, #000, #222);
        }}
        .piano-key__black.active {{
            background: linear-gradient(-20deg, #3330fb, #111, #000);
        }}
        .piano-key__white.remote {{
            background: linear-gradient(-20deg, #8b7fd8, #5b4d9e, #3d3270);
        }}
        .piano-key__black.remote {{
            background: linear-gradient(-20deg, #6a5bb8, #4a3d8a, #2e2560);
        }}
        .piano-note {{
            position: absolute;
            bottom: 6px;
            left: 50%;
            transform: translateX(-50%);
            font-size: 9px;
            opacity: 0.55;
            pointer-events: none;
            white-space: nowrap;
            text-align: center;
            line-height: 1.15;
        }}
        .piano-bind {{
            display: block;
            font-size: 8px;
            opacity: 0.85;
            font-weight: 600;
        }}
        .piano-key__black .piano-note {{ color: #fff; opacity: 0.7; }}
        .piano-key__black .piano-bind {{ color: #eee; opacity: 0.95; }}
        .hint {{ margin-top: 8px; font-size: 13px; opacity: 0.65; flex-shrink: 0; }}
        .hint-mobile {{ display: none; }}
        .loading {{
            display: none;
            align-items: center;
            justify-content: center;
            padding: 24px;
            font-size: 14px;
            opacity: 0.8;
        }}
        @media (pointer: coarse), (max-width: 900px) {{
            .hint-desktop {{ display: none; }}
            .hint-mobile {{ display: block; }}
            .piano-bind {{ display: none; }}
            .piano-note > span:first-child {{ display: none; }}
            .piano-scroll.segmented {{
                overflow-x: hidden;
                overflow-y: auto;
                padding: 4px 6px 8px;
            }}
            .piano-scroll.segmented .piano-stack {{
                flex-direction: column;
                gap: 6px;
            }}
            .piano-segment .piano {{
                min-height: 80px;
            }}
            .wrap.piano-on .stage-wrap {{
                padding: 4px 6px 6px;
            }}
            .wrap.piano-on .meta .meta-detail {{ display: none; }}
        }}
        @media (pointer: coarse) and (orientation: landscape), (max-width: 900px) and (orientation: landscape) {{
            .wrap.piano-on .hint {{ display: none; }}
            .wrap.piano-on .toolbar {{ margin-bottom: 2px; }}
            .piano-segment {{
                flex: 1 1 0;
                min-height: 0;
            }}
            .piano-segment .piano {{
                min-height: 0;
            }}
            .piano-segment__label {{
                font-size: 9px;
                margin-bottom: 2px;
            }}
        }}
    </style>
</head>
<body>
    <div class="wrap" id="wrap">
        <div class="card">
            <div class="header">
                <h1>{html.escape(S['title'])}</h1>
                <p class="sub" id="subtitle">{html.escape(S['gate_sub'])}</p>
            </div>
            <div class="gate" id="gate">
                <label for="key">{html.escape(S['key_label'])}</label>
                <div style="display:flex; gap:12px; flex-wrap:wrap; align-items:center; margin-top:4px;">
                    <input id="key" type="text" maxlength="6" autocomplete="off"
                           placeholder="{html.escape(S['key_placeholder'])}" />
                    <button id="unlockBtn" type="button">{html.escape(S['unlock'])}</button>
                </div>
            </div>
            <script>
            (function () {{
                function takeKey() {{
                    try {{
                        var inj = (window.__SSHCHAT_KEY || '').toString().trim().toUpperCase();
                        if (/^[A-Z0-9]{{6}}$/.test(inj)) {{
                            try {{ delete window.__SSHCHAT_KEY; }} catch (_) {{}}
                            return inj;
                        }}
                    }} catch (_) {{}}
                    var hash = location.hash || '';
                    var hm = hash.match(/(?:^|[&#])k=([A-Za-z0-9]{{6}})/);
                    if (hm) {{
                        try {{
                            history.replaceState(null, '', location.pathname + location.search);
                        }} catch (_) {{}}
                        return hm[1].toUpperCase();
                    }}
                    try {{
                        var q = new URLSearchParams(location.search || '');
                        var qk = (q.get('k') || '').trim().toUpperCase();
                        if (/^[A-Z0-9]{{6}}$/.test(qk)) {{
                            q.delete('k');
                            var qs = q.toString();
                            try {{
                                history.replaceState(
                                    null, '',
                                    location.pathname + (qs ? '?' + qs : '') + (location.hash || '')
                                );
                            }} catch (_) {{}}
                            return qk;
                        }}
                    }} catch (_) {{}}
                    return '';
                }}
                var k = takeKey();
                if (!k) return;
                window.__SSHCHAT_KEY = k;
                var el = document.getElementById('key');
                if (el) el.value = k;
            }})();
            </script>
            <div class="stage-wrap" id="stageWrap">
                <div class="meta" id="meta"></div>
                <div class="toolbar">
                    <span class="room-badge" id="roomBadge" hidden></span>
                    <button type="button" class="tb-btn" id="recBtn">{html.escape(S['record'])}</button>
                    <span class="rec-time" id="recTime" hidden>0:00</span>
                    <button type="button" class="tb-btn" id="exportBtn" disabled>{html.escape(S['export'])}</button>
                    <button type="button" class="tb-btn" id="shareBtn" disabled>{html.escape(S['share'])}</button>
                    <span class="status" id="status">{html.escape(S['status_ready'])}</span>
                </div>
                <div class="loading" id="loading">{html.escape(S['loading'])}</div>
                <div class="piano-scroll" id="pianoScroll">
                    <div class="piano-stack" id="pianoStack"></div>
                    <div class="piano" id="piano"></div>
                </div>
                <p class="hint hint-desktop">{html.escape(S['hint'])}</p>
                <p class="hint hint-mobile">{html.escape(S['hint'])}</p>
            </div>
        </div>
    </div>
    <script>
    (function () {{
        const token = {json.dumps(token)};
        const i18n = {json.dumps(i18n, ensure_ascii=False)};
        const notes = {notes_json};
        const pianoKeys = {keys_json};
        const keyMap = {key_map_json};
        const pianoSegments = {segments_json};

        function keyLabelEl(noteName, bind) {{
            const wrap = document.createElement('span');
            wrap.className = 'piano-note';
            const nameEl = document.createElement('span');
            nameEl.textContent = noteName;
            wrap.appendChild(nameEl);
            if (bind) {{
                const bindEl = document.createElement('span');
                bindEl.className = 'piano-bind';
                bindEl.textContent = bind;
                wrap.appendChild(bindEl);
            }}
            return wrap;
        }}

        const keyInput = document.getElementById('key');
        const unlockBtn = document.getElementById('unlockBtn');
        const gate = document.getElementById('gate');
        const stageWrap = document.getElementById('stageWrap');
        const wrap = document.getElementById('wrap');
        const statusEl = document.getElementById('status');
        const metaEl = document.getElementById('meta');
        const roomBadgeEl = document.getElementById('roomBadge');
        const loadingEl = document.getElementById('loading');
        const pianoScrollEl = document.getElementById('pianoScroll');
        const pianoStackEl = document.getElementById('pianoStack');
        const pianoEl = document.getElementById('piano');
        const recBtn = document.getElementById('recBtn');
        const recTimeEl = document.getElementById('recTime');
        const exportBtn = document.getElementById('exportBtn');
        const shareBtn = document.getElementById('shareBtn');

        let ticket = '';
        let lastSeq = 0;
        let pollTimer = null;
        let syncing = false;
        let selfName = '';
        const keyEls = Object.create(null);
        const flashTimers = Object.create(null);
        const heldKeys = Object.create(null);
        const ownEventSeqs = new Set();
        let audioCtx = null;
        const audioBuffers = Object.create(null);
        let audioReady = false;
        let unlockPromise = null;
        let pushQueue = Promise.resolve();
        let remoteTimeBase = null;
        const REMOTE_STALE_MS = 500;

        let isRecording = false;
        let recordStartMs = 0;
        let recordTimer = null;
        let recordedEvents = [];
        let lastRecording = null;

        function fmtRecTime(sec) {{
            const s = Math.max(0, Math.floor(sec));
            const m = Math.floor(s / 60);
            const r = s % 60;
            return m + ':' + String(r).padStart(2, '0');
        }}

        function updateRecUi() {{
            if (isRecording) {{
                recBtn.textContent = i18n.stop;
                recBtn.classList.add('recording');
                recTimeEl.hidden = false;
            }} else {{
                recBtn.textContent = i18n.record;
                recBtn.classList.remove('recording');
                recTimeEl.hidden = !lastRecording;
            }}
            const hasRec = !!(lastRecording && lastRecording.events && lastRecording.events.length);
            exportBtn.disabled = !hasRec;
            shareBtn.disabled = !hasRec;
        }}

        function tickRecTime() {{
            if (!isRecording) return;
            const sec = (performance.now() - recordStartMs) / 1000;
            recTimeEl.textContent = fmtRecTime(sec);
        }}

        function recordEvent(note, action) {{
            if (!isRecording) return;
            recordedEvents.push({{
                t: (performance.now() - recordStartMs) / 1000,
                note: note,
                action: action,
            }});
        }}

        function buildRecordingPayload() {{
            if (!lastRecording) return null;
            return {{
                version: 1,
                author: selfName || lastRecording.author || '',
                title: lastRecording.title || '',
                duration: lastRecording.duration || 0,
                events: lastRecording.events || [],
            }};
        }}

        function startRecording() {{
            if (isRecording) return;
            isRecording = true;
            recordStartMs = performance.now();
            recordedEvents = [];
            lastRecording = null;
            recTimeEl.textContent = '0:00';
            recordTimer = setInterval(tickRecTime, 200);
            updateRecUi();
        }}

        function stopRecording() {{
            if (!isRecording) return;
            isRecording = false;
            if (recordTimer) {{
                clearInterval(recordTimer);
                recordTimer = null;
            }}
            const duration = recordedEvents.length
                ? recordedEvents[recordedEvents.length - 1].t
                : 0;
            lastRecording = {{
                author: selfName,
                title: '',
                duration: duration,
                events: recordedEvents.slice(),
            }};
            recTimeEl.textContent = fmtRecTime(duration);
            updateRecUi();
        }}

        function toggleRecording() {{
            if (isRecording) stopRecording();
            else startRecording();
        }}

        function exportRecording() {{
            const payload = buildRecordingPayload();
            if (!payload || !payload.events.length) {{
                alert(i18n.shareEmpty);
                return;
            }}
            const blob = new Blob([JSON.stringify(payload, null, 2)], {{
                type: 'application/json',
            }});
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'piano-recording-' + Date.now() + '.json';
            document.body.appendChild(a);
            a.click();
            a.remove();
            setTimeout(function () {{ URL.revokeObjectURL(url); }}, 1000);
            setStatus(i18n.exportOk, false);
        }}

        async function shareRecording() {{
            const payload = buildRecordingPayload();
            if (!payload || !payload.events.length) {{
                alert(i18n.shareEmpty);
                return;
            }}
            shareBtn.disabled = true;
            try {{
                const res = await fetch('/piano/' + token + '/recording', {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/json',
                        'X-Piano-Ticket': ticket,
                    }},
                    cache: 'no-store',
                    body: JSON.stringify({{
                        title: payload.title || '',
                        duration: payload.duration,
                        events: payload.events,
                    }}),
                }});
                const data = await res.json().catch(function () {{ return {{}}; }});
                if (!res.ok) throw new Error(data.error || i18n.shareFail);
                const link = location.origin + '/piano-replay/' + data.id;
                try {{
                    await navigator.clipboard.writeText(link);
                }} catch (_) {{
                    prompt(i18n.shareOk, link);
                    setStatus(i18n.shareOk, false);
                    return;
                }}
                setStatus(i18n.shareOk, false);
            }} catch (e) {{
                alert((e && e.message) || i18n.shareFail);
            }} finally {{
                updateRecUi();
            }}
        }}

        recBtn.addEventListener('click', toggleRecording);
        exportBtn.addEventListener('click', exportRecording);
        shareBtn.addEventListener('click', function () {{ void shareRecording(); }});

        function setStatus(text, err) {{
            statusEl.textContent = text;
            statusEl.classList.toggle('err', !!err);
        }}

        function setRoomBadge(room) {{
            const label = (room || '').trim();
            if (!label) {{
                roomBadgeEl.hidden = true;
                roomBadgeEl.textContent = '';
                return;
            }}
            roomBadgeEl.hidden = false;
            roomBadgeEl.textContent = i18n.room + ' #' + label;
        }}

        function fmtExpires(ts) {{
            try {{
                return new Date(ts * 1000).toLocaleString();
            }} catch (_) {{
                return '';
            }}
        }}

        function hashFragmentKey() {{
            const existing = (window.__SSHCHAT_KEY || '').toString().trim().toUpperCase();
            if (/^[A-Z0-9]{{6}}$/.test(existing)) {{
                try {{ delete window.__SSHCHAT_KEY; }} catch (_) {{}}
                return existing;
            }}
            const m = (location.hash || '').match(/(?:^|[&#])k=([A-Za-z0-9]{{6}})/);
            if (!m) return '';
            try {{
                history.replaceState(null, '', location.pathname + location.search);
            }} catch (_) {{}}
            return m[1].toUpperCase();
        }}

        function sampleUrl(note) {{
            const file = notes[note];
            return file ? ('/piano-samples/' + file) : '';
        }}

        function ensureAudioCtx() {{
            if (audioCtx) return audioCtx;
            const Ctx = window.AudioContext || window.webkitAudioContext;
            if (!Ctx) return null;
            audioCtx = new Ctx();
            return audioCtx;
        }}

        async function preloadSamples() {{
            const ctx = ensureAudioCtx();
            if (!ctx) return false;
            const jobs = Object.keys(notes).map(async function (note) {{
                const url = sampleUrl(note);
                if (!url || audioBuffers[note]) return;
                const res = await fetch(url, {{ cache: 'force-cache' }});
                if (!res.ok) return;
                const buf = await res.arrayBuffer();
                audioBuffers[note] = await ctx.decodeAudioData(buf);
            }});
            await Promise.all(jobs);
            audioReady = Object.keys(audioBuffers).length > 0;
            return audioReady;
        }}

        function unlockAudio() {{
            if (unlockPromise) return unlockPromise;
            const ctx = ensureAudioCtx();
            if (!ctx) return Promise.resolve(false);
            unlockPromise = ctx.resume().then(function () {{
                return preloadSamples();
            }}).then(function () {{
                return true;
            }}).catch(function () {{
                unlockPromise = null;
                return false;
            }});
            return unlockPromise;
        }}

        function flashKey(note, remote) {{
            const el = keyEls[note];
            if (!el) return;
            el.classList.remove('active', 'remote');
            if (remote) el.classList.add('remote');
            else el.classList.add('active');
            if (flashTimers[note]) clearTimeout(flashTimers[note]);
            flashTimers[note] = setTimeout(function () {{
                el.classList.remove('active', 'remote');
                delete flashTimers[note];
            }}, remote ? 700 : 450);
        }}

        function playRemoteNote(note) {{
            if (!notes[note]) return;
            const ctx = ensureAudioCtx();
            const buffer = audioBuffers[note];
            if (ctx && ctx.state === 'running' && buffer) {{
                const src = ctx.createBufferSource();
                src.buffer = buffer;
                src.connect(ctx.destination);
                try {{
                    src.start(0);
                    flashKey(note, true);
                    return;
                }} catch (_) {{}}
            }}
            const url = sampleUrl(note);
            if (!url) return;
            const a = new Audio(url);
            try {{
                a.currentTime = 0;
                void a.play().then(function () {{
                    flashKey(note, true);
                }}).catch(function () {{}});
            }} catch (_) {{}}
        }}

        function playNote(note, remote) {{
            if (!notes[note]) return;
            if (remote) {{
                playRemoteNote(note);
                return;
            }}
            void unlockAudio().then(function () {{
                const ctx = ensureAudioCtx();
                const buffer = audioBuffers[note];
                if (ctx && ctx.state === 'running' && buffer) {{
                    const src = ctx.createBufferSource();
                    src.buffer = buffer;
                    src.connect(ctx.destination);
                    try {{
                        src.start(0);
                        flashKey(note, false);
                        return;
                    }} catch (_) {{}}
                }}
                const url = sampleUrl(note);
                if (!url) return;
                const a = new Audio(url);
                try {{
                    a.currentTime = 0;
                    void a.play().then(function () {{
                        flashKey(note, false);
                    }}).catch(function () {{}});
                }} catch (_) {{}}
            }});
        }}

        function resetRemoteTimeBase(evtTs) {{
            const ts = typeof evtTs === 'number' ? evtTs : 0;
            const perf = performance.now();
            if (!remoteTimeBase) {{
                remoteTimeBase = {{ serverTs: ts, perfMs: perf }};
                return;
            }}
            const expected = remoteTimeBase.perfMs + (ts - remoteTimeBase.serverTs) * 1000;
            if (perf - expected > REMOTE_STALE_MS) {{
                remoteTimeBase = {{ serverTs: ts, perfMs: perf }};
            }}
        }}

        function scheduleRemoteEvent(evt) {{
            const ts = typeof evt.ts === 'number' ? evt.ts : 0;
            resetRemoteTimeBase(ts);
            const when = remoteTimeBase.perfMs + (ts - remoteTimeBase.serverTs) * 1000;
            const delay = Math.max(0, when - performance.now());
            if (evt.action === 'on') {{
                setTimeout(function () {{ playRemoteNote(evt.note); }}, delay);
            }} else if (evt.action === 'off') {{
                setTimeout(function () {{ unflashKey(evt.note); }}, delay);
            }}
        }}

        function unflashKey(note) {{
            const el = keyEls[note];
            if (!el) return;
            el.classList.remove('active', 'remote');
            if (flashTimers[note]) {{
                clearTimeout(flashTimers[note]);
                delete flashTimers[note];
            }}
        }}

        function noteOctave(name) {{
            if (!name) return 0;
            const m = String(name).match(/(\\d+)$/);
            return m ? parseInt(m[1], 10) : 0;
        }}

        function keysForSegment(fromOct, toOct) {{
            return pianoKeys.filter(function (item) {{
                const o = noteOctave(item.white.name);
                return o >= fromOct && o <= toOct;
            }});
        }}

        function useSegmentedLayout() {{
            if (window.matchMedia('(pointer: coarse)').matches) return true;
            if (window.matchMedia('(max-width: 900px)').matches) return true;
            if (window.matchMedia('(max-height: 900px)').matches) return true;
            // Phone / iPad WebViews often report "fine" pointer — use touch + viewport.
            if ('ontouchstart' in window && Math.min(window.innerWidth, window.innerHeight) <= 1024) {{
                return true;
            }}
            return false;
        }}

        function bindKeyPointer(el, noteName) {{
            el.addEventListener('pointerdown', function (e) {{
                e.preventDefault();
                noteOn(noteName);
            }});
            el.addEventListener('pointerup', function () {{ noteOff(noteName); }});
            el.addEventListener('pointercancel', function () {{ noteOff(noteName); }});
            if (!useSegmentedLayout()) {{
                el.addEventListener('pointerleave', function () {{ noteOff(noteName); }});
            }}
        }}

        function appendKeyItem(container, item, showBind) {{
            const wrapKey = document.createElement('div');
            wrapKey.className = 'piano-key';

            const white = document.createElement('div');
            white.className = 'piano-key__white';
            white.dataset.note = item.white.name;
            white.dataset.type = 'white';
            white.appendChild(keyLabelEl(item.white.name, showBind ? (item.white.bind || '') : ''));
            keyEls[item.white.name] = white;
            bindKeyPointer(white, item.white.name);

            wrapKey.appendChild(white);

            if (item.black && item.black.name) {{
                const black = document.createElement('div');
                black.className = 'piano-key__black';
                black.dataset.note = item.black.name;
                black.dataset.type = 'black';
                black.appendChild(keyLabelEl(item.black.name, showBind ? (item.black.bind || '') : ''));
                keyEls[item.black.name] = black;
                bindKeyPointer(black, item.black.name);

                wrapKey.appendChild(black);
            }}

            container.appendChild(wrapKey);
        }}

        function buildKeyboard() {{
            pianoEl.replaceChildren();
            pianoStackEl.replaceChildren();
            const segmented = useSegmentedLayout();
            pianoScrollEl.classList.toggle('segmented', segmented);
            const showBind = !segmented;
            if (!segmented) {{
                for (const item of pianoKeys) {{
                    appendKeyItem(pianoEl, item, showBind);
                }}
                return;
            }}
            const slotCount = 14;
            for (const seg of pianoSegments) {{
                const segWrap = document.createElement('div');
                segWrap.className = 'piano-segment';
                segWrap.dataset.segment = seg.id || '';

                const label = document.createElement('div');
                label.className = 'piano-segment__label';
                label.textContent = seg.label || '';
                segWrap.appendChild(label);

                const row = document.createElement('div');
                row.className = 'piano';
                row.style.setProperty('--slot-count', String(slotCount));
                const items = keysForSegment(seg.from, seg.to);
                for (const item of items) {{
                    appendKeyItem(row, item, showBind);
                }}
                segWrap.appendChild(row);
                pianoStackEl.appendChild(segWrap);
            }}
        }}

        window.addEventListener('orientationchange', function () {{
            if (!ticket) return;
            setTimeout(buildKeyboard, 120);
        }});
        window.addEventListener('resize', function () {{
            if (!ticket) return;
            clearTimeout(window.__pianoResizeTimer);
            window.__pianoResizeTimer = setTimeout(buildKeyboard, 120);
        }});

        async function pushNote(note, action, clientTs) {{
            if (!ticket) return;
            const ts = typeof clientTs === 'number' ? clientTs : Date.now() / 1000;
            pushQueue = pushQueue.then(async function () {{
                try {{
                    const res = await fetch('/piano/' + token + '/note', {{
                        method: 'POST',
                        headers: {{
                            'Content-Type': 'application/json',
                            'X-Piano-Ticket': ticket,
                        }},
                        cache: 'no-store',
                        body: JSON.stringify({{ note: note, action: action, ts: ts }}),
                    }});
                    const data = await res.json().catch(function () {{ return {{}}; }});
                    const evt = data.event;
                    if (evt && typeof evt.seq === 'number') {{
                        ownEventSeqs.add(evt.seq);
                    }}
                }} catch (_) {{}}
            }});
            return pushQueue;
        }}

        function noteOn(note) {{
            if (!ticket || !notes[note]) return;
            if (heldKeys[note]) return;
            heldKeys[note] = true;
            const ts = Date.now() / 1000;
            playNote(note, false);
            recordEvent(note, 'on');
            void pushNote(note, 'on', ts);
        }}

        function noteOff(note) {{
            if (!heldKeys[note]) return;
            delete heldKeys[note];
            unflashKey(note);
            recordEvent(note, 'off');
            void pushNote(note, 'off', Date.now() / 1000);
        }}

        function noteFromKeyEvent(e) {{
            if (e.code && keyMap[e.code]) return keyMap[e.code];
            const k = (e.key || '').length === 1 ? e.key.toUpperCase() : '';
            if (k && keyMap['Key' + k]) return keyMap['Key' + k];
            if (k && keyMap['Digit' + k]) return keyMap['Digit' + k];
            return '';
        }}

        function bindKeyboard() {{
            document.addEventListener('keydown', function (e) {{
                if (!ticket || e.repeat) return;
                if (e.target === keyInput) return;
                const note = noteFromKeyEvent(e);
                if (!note || !notes[note]) return;
                e.preventDefault();
                noteOn(note);
            }});
            document.addEventListener('keyup', function (e) {{
                if (!ticket) return;
                const note = noteFromKeyEvent(e);
                if (!note) return;
                noteOff(note);
            }});
        }}

        async function auth() {{
            const key = (keyInput.value || '').trim().toUpperCase();
            if (key.length !== 6) {{
                alert(i18n.alertKey);
                return;
            }}
            unlockBtn.disabled = true;
            unlockBtn.textContent = i18n.verifying;
            try {{
                const res = await fetch('/piano/' + token + '/auth', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    cache: 'no-store',
                    body: JSON.stringify({{ key: key }}),
                }});
                const data = await res.json().catch(function () {{ return {{}}; }});
                if (!res.ok) throw new Error(data.error || 'auth failed');
                ticket = data.ticket;
                selfName = data.participant || '';
                gate.style.display = 'none';
                stageWrap.style.display = 'flex';
                wrap.classList.add('piano-on');
                setRoomBadge(data.room || '');
                metaEl.innerHTML =
                    '<span class="meta-detail">' + i18n.you + ': ' + (data.participant || '') + '</span>' +
                    (data.room ? '<span class="meta-detail">' + i18n.room + ': #' + data.room + '</span>' : '') +
                    (data.expires ? '<span class="meta-detail">' + i18n.expires + ': ' + fmtExpires(data.expires) + '</span>' : '');
                buildKeyboard();
                bindKeyboard();
                loadingEl.style.display = 'none';
                void unlockAudio();
                await syncOnce(true);
                pollTimer = setInterval(function () {{ void syncOnce(false); }}, 50);
            }} catch (e) {{
                alert((e && e.message) || i18n.statusErr);
                unlockBtn.disabled = false;
                unlockBtn.textContent = i18n.unlock;
            }}
        }}

        async function syncOnce(initial) {{
            if (!ticket || syncing) return;
            syncing = true;
            if (!initial) setStatus(i18n.statusSync, false);
            try {{
                const res = await fetch(
                    '/piano/' + token + '/sync?since=' + lastSeq + '&ticket=' + encodeURIComponent(ticket),
                    {{
                        headers: {{ 'X-Piano-Ticket': ticket }},
                        cache: 'no-store',
                    }}
                );
                const data = await res.json().catch(function () {{ return {{}}; }});
                if (!res.ok) throw new Error(data.error || 'sync failed');
                const events = (data.events || []).slice().sort(function (a, b) {{
                    return (a.seq || 0) - (b.seq || 0);
                }});
                for (const evt of events) {{
                    if (typeof evt.seq === 'number' && evt.seq > lastSeq) {{
                        lastSeq = evt.seq;
                    }}
                    if (initial) continue;
                    if (!evt.note || !notes[evt.note]) continue;
                    if (ownEventSeqs.has(evt.seq)) continue;
                    scheduleRemoteEvent(evt);
                }}
                setStatus(i18n.statusReady, false);
            }} catch (_) {{
                setStatus(i18n.statusErr, true);
            }} finally {{
                syncing = false;
            }}
        }}

        unlockBtn.addEventListener('click', auth);
        keyInput.addEventListener('keydown', function (e) {{
            if (e.key === 'Enter') auth();
        }});

        const injected = (window.__SSHCHAT_KEY || '').toString().trim().toUpperCase();
        try {{ delete window.__SSHCHAT_KEY; }} catch (_) {{}}
        const autofill = (injected.length === 6 ? injected : '') || hashFragmentKey();
        if (autofill) {{
            keyInput.value = autofill;
            setTimeout(function () {{ void auth(); }}, 50);
        }}

        window.paintAll = function () {{}};
    }})();
    </script>
</body>
</html>"""


def handle_piano_samples_get(handler: "BaseHTTPRequestHandler") -> bool:
    """Serve piano MP3 samples from piano_samples/ at /piano-samples/{fname}."""
    parsed = urlparse(handler.path)
    parts = parsed.path.strip("/").split("/")
    if len(parts) != 2 or parts[0] != "piano-samples":
        return False

    fname = _safe_sample_fname(parts[1])
    if not fname:
        handler._send_error_json(404, "sample not found")  # type: ignore[attr-defined]
        return True

    path = samples_dir() / fname
    if not path.is_file():
        handler._send_error_json(404, "sample not found")  # type: ignore[attr-defined]
        return True

    mime = mimetypes.guess_type(fname)[0] or "audio/mpeg"
    try:
        size = path.stat().st_size
        handler.send_response(200)
        handler.send_header("Content-Type", mime)
        handler.send_header("Content-Length", str(size))
        handler.send_header("Cache-Control", "public, max-age=86400")
        handler.end_headers()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                handler.wfile.write(chunk)
    except Exception as e:
        print(f"[PianoHTTP] sample error: {e}")
    return True


def handle_piano_get(handler: "BaseHTTPRequestHandler") -> bool:
    """Return True if the request was a piano GET and was handled."""
    parsed = urlparse(handler.path)
    parts = parsed.path.strip("/").split("/")
    if not parts or parts[0] != "piano":
        return False

    store = piano_sharing.piano_store
    lang = "en"
    try:
        from file_http_server import _page_locale

        lang = _page_locale(handler)
    except Exception:
        pass

    if len(parts) == 2:
        token = parts[1]
        session = store.get_by_token(token)
        loc = "zh" if str(lang).lower().startswith("zh") else "en"
        closed_msg = PIANO_TEXTS[loc]["closed"]
        if session is None:
            handler._send_html_error(404, closed_msg, lang=lang)  # type: ignore[attr-defined]
            return True
        ok, err = store._alive(session)
        if not ok:
            handler._send_html_error(403, err or closed_msg, lang=lang)  # type: ignore[attr-defined]
            return True
        handler._send_html_page(generate_piano_page(token, lang=lang))  # type: ignore[attr-defined]
        return True

    if len(parts) == 3 and parts[2] == "sync":
        token = parts[1]
        ticket = (handler.headers.get("X-Piano-Ticket") or "").strip()
        qs = parse_qs(parsed.query or "")
        if not ticket:
            ticket = (qs.get("ticket") or [""])[0].strip()
        since_raw = (qs.get("since") or ["0"])[0]
        try:
            since = int(since_raw)
        except ValueError:
            since = 0
        payload, err = store.sync_since(token, ticket, since)
        if payload is None:
            handler._send_error_json(403, err)  # type: ignore[attr-defined]
            return True
        handler._send_json_response(200, payload)  # type: ignore[attr-defined]
        return True

    handler._send_error_json(404, "网址无效")  # type: ignore[attr-defined]
    return True


def handle_piano_post(handler: "BaseHTTPRequestHandler") -> bool:
    """Return True if the request was a piano POST and was handled."""
    parsed = urlparse(handler.path)
    parts = parsed.path.strip("/").split("/")
    if not parts or parts[0] != "piano" or len(parts) < 3:
        return False

    store = piano_sharing.piano_store
    token = parts[1]
    action = parts[2]
    ticket = (handler.headers.get("X-Piano-Ticket") or "").strip()

    if action == "auth":
        body = handler._read_json_body()  # type: ignore[attr-defined]
        key = str(body.get("key", "")).strip().upper()
        session, participant, access, err = store.issue_access_ticket(token, key)
        if session is None or not access:
            handler._send_error_json(403, err)  # type: ignore[attr-defined]
            return True
        handler._send_json_response(  # type: ignore[attr-defined]
            200,
            {
                "ticket": access,
                "participant": participant,
                "creator": session.creator,
                "room": session.room,
                "title": session.title,
                "expires": session.expires,
            },
        )
        return True

    if action == "note":
        body = handler._read_json_body()  # type: ignore[attr-defined]
        note = str(body.get("note", "")).strip()
        note_action = str(body.get("action", "on")).strip().lower()
        client_ts = body.get("ts")
        result, err = store.push_note(
            token,
            ticket,
            note=note,
            action=note_action,
            client_ts=client_ts,
        )
        if result is None:
            handler._send_error_json(403, err)  # type: ignore[attr-defined]
            return True
        handler._send_json_response(200, result)  # type: ignore[attr-defined]
        return True

    if action == "recording":
        body = handler._read_json_body()  # type: ignore[attr-defined]
        title = str(body.get("title", "")).strip()
        events = body.get("events")
        duration = body.get("duration", 0)
        if not isinstance(events, list):
            handler._send_error_json(400, "无效录制")  # type: ignore[attr-defined]
            return True
        rec_id, err = store.save_recording(
            token,
            ticket,
            title=title,
            events=events,
            duration=duration,
        )
        if rec_id is None:
            handler._send_error_json(403, err)  # type: ignore[attr-defined]
            return True
        handler._send_json_response(200, {"id": rec_id})  # type: ignore[attr-defined]
        return True

    handler._send_error_json(404, "网址无效")  # type: ignore[attr-defined]
    return True


def generate_piano_replay_page(recording_id: str, lang: str = "en") -> str:
    lang = "zh" if str(lang or "").lower().startswith("zh") else "en"
    S = PIANO_TEXTS[lang]
    html_lang = "zh-CN" if lang == "zh" else "en"
    i18n = {
        "replayTitle": S["replay_title"],
        "replayBy": S["replay_by"],
        "replayPlay": S["replay_play"],
        "replayPause": S["replay_pause"],
        "replayRestart": S["replay_restart"],
        "replayLoading": S["replay_loading"],
        "replayNotFound": S["replay_not_found"],
    }
    notes_json = json.dumps(PIANO_NOTES, ensure_ascii=False)
    keys_json = json.dumps(PIANO_KEYS, ensure_ascii=False)
    segments_json = json.dumps(
        [
            {
                "id": s["id"],
                "from": s["from"],
                "to": s["to"],
                "label": s["label_zh" if lang == "zh" else "label_en"],
            }
            for s in PIANO_SEGMENTS
        ],
        ensure_ascii=False,
    )
    return f"""<!DOCTYPE html>
<html lang="{html_lang}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>{html.escape(S['replay_title'])}</title>
    <style>
        :root {{
            --ink: #1a1f2e;
            --paper: #f7f3ea;
            --line: #d9d0c0;
            --accent: #c45c26;
            --accent-2: #2f6f6a;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        html, body {{ height: 100%; height: 100dvh; }}
        body {{
            font-family: "IBM Plex Sans", "Segoe UI", "PingFang SC", "Noto Sans SC", sans-serif;
            background:
                radial-gradient(circle at 12% 18%, rgba(196,92,38,0.14), transparent 42%),
                radial-gradient(circle at 88% 8%, rgba(47,111,106,0.16), transparent 40%),
                linear-gradient(160deg, #ebe4d6 0%, #dfe8e4 55%, #efe8dc 100%);
            color: var(--ink);
        }}
        .wrap {{ max-width: none; padding: 8px 10px; height: 100%; display: flex; flex-direction: column; }}
        .card {{
            background: rgba(247, 243, 234, 0.94);
            flex: 1;
            display: flex;
            flex-direction: column;
            min-height: 0;
        }}
        .toolbar {{
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            align-items: center;
            margin-bottom: 8px;
            flex-shrink: 0;
        }}
        h1 {{
            font-family: "Fraunces", "Songti SC", Georgia, serif;
            font-size: 20px;
            font-weight: 600;
            margin-right: 8px;
        }}
        .meta {{ font-size: 13px; opacity: 0.7; }}
        button {{
            border: 0;
            border-radius: 999px;
            padding: 8px 14px;
            font-size: 13px;
            cursor: pointer;
            background: var(--accent-2);
            color: white;
        }}
        button:disabled {{ opacity: 0.45; cursor: not-allowed; }}
        .progress {{
            margin-left: auto;
            font-size: 12px;
            font-variant-numeric: tabular-nums;
            opacity: 0.75;
        }}
        .piano-scroll {{
            flex: 1;
            min-height: 0;
            overflow-x: auto;
            overflow-y: hidden;
            border: 1px solid var(--line);
            border-radius: 12px;
            background: linear-gradient(-65deg, #000, #222, #000, #666, #222 75%);
            padding: 8px 12px 16px;
            touch-action: manipulation;
        }}
        .piano-stack {{ display: none; flex-direction: column; gap: 8px; height: 100%; min-height: 0; }}
        .piano-scroll.segmented {{ overflow-x: hidden; overflow-y: auto; }}
        .piano-scroll.segmented .piano-stack {{ display: flex; gap: 6px; height: 100%; min-height: 100%; }}
        .piano-scroll.segmented > .piano {{ display: none; }}
        .piano-segment {{
            flex: 1 1 33%;
            min-height: 88px;
            display: flex;
            flex-direction: column;
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 10px;
            padding: 4px 6px 6px;
            background: rgba(0,0,0,0.22);
        }}
        .piano-segment__label {{
            flex-shrink: 0;
            font-size: 10px;
            color: rgba(255,255,255,0.72);
            margin-bottom: 4px;
        }}
        .piano-segment .piano {{ flex: 1; height: auto; min-height: 72px; width: 100%; }}
        .piano-segment .piano-key {{
            flex: 0 0 calc(100% / var(--slot-count, 14));
            width: calc(100% / var(--slot-count, 14));
            max-width: calc(100% / var(--slot-count, 14));
            min-width: 0;
        }}
        .piano-segment .piano-key__black {{ width: 58%; right: -29%; }}
        .piano {{
            display: flex;
            height: 22vh;
            min-height: 140px;
            max-height: 220px;
            user-select: none;
        }}
        .piano-key {{ position: relative; flex: 0 0 auto; width: 44px; height: 100%; }}
        .piano-key__white {{
            width: 100%; height: 100%;
            border-radius: 0 0 6px 6px;
            background: linear-gradient(-30deg, #f8f8f8, #fff);
            border: 1px solid #bbb;
            position: relative; z-index: 1;
        }}
        .piano-key__black {{
            position: absolute; top: 0; right: -14px;
            width: 28px; height: 62%;
            border-radius: 0 0 4px 4px;
            background: linear-gradient(-20deg, #222, #000, #222);
            border: 1px solid #111;
            z-index: 2;
        }}
        .piano-key__white.active {{ background: linear-gradient(-20deg, #3330fb, #000, #222); }}
        .piano-key__black.active {{ background: linear-gradient(-20deg, #3330fb, #111, #000); }}
        .err-msg {{ padding: 24px; text-align: center; opacity: 0.8; }}
    </style>
</head>
<body>
    <div class="wrap">
        <div class="card">
            <div class="toolbar">
                <h1 id="title">{html.escape(S['replay_title'])}</h1>
                <span class="meta" id="meta"></span>
                <button type="button" id="playBtn" disabled>{html.escape(S['replay_play'])}</button>
                <button type="button" id="restartBtn" disabled>{html.escape(S['replay_restart'])}</button>
                <span class="progress" id="progress">0:00 / 0:00</span>
            </div>
            <div class="err-msg" id="errMsg" hidden></div>
            <div class="piano-scroll" id="pianoScroll">
                <div class="piano-stack" id="pianoStack"></div>
                <div class="piano" id="piano"></div>
            </div>
        </div>
    </div>
    <script>
    (function () {{
        const recordingId = {json.dumps(recording_id)};
        const i18n = {json.dumps(i18n, ensure_ascii=False)};
        const notes = {notes_json};
        const pianoKeys = {keys_json};
        const pianoSegments = {segments_json};

        const playBtn = document.getElementById('playBtn');
        const restartBtn = document.getElementById('restartBtn');
        const progressEl = document.getElementById('progress');
        const metaEl = document.getElementById('meta');
        const errMsg = document.getElementById('errMsg');
        const pianoScrollEl = document.getElementById('pianoScroll');
        const pianoStackEl = document.getElementById('pianoStack');
        const pianoEl = document.getElementById('piano');
        const keyEls = Object.create(null);
        const flashTimers = Object.create(null);

        let recording = null;
        let playing = false;
        let playStartMs = 0;
        let pauseAt = 0;
        let timers = [];
        let progressTimer = null;
        let audioCtx = null;
        const audioBuffers = Object.create(null);
        let unlockPromise = null;

        function fmtTime(sec) {{
            const s = Math.max(0, Math.floor(sec));
            const m = Math.floor(s / 60);
            const r = s % 60;
            return m + ':' + String(r).padStart(2, '0');
        }}

        function ensureAudioCtx() {{
            if (audioCtx) return audioCtx;
            const Ctx = window.AudioContext || window.webkitAudioContext;
            if (!Ctx) return null;
            audioCtx = new Ctx();
            return audioCtx;
        }}

        async function preloadSamples() {{
            const ctx = ensureAudioCtx();
            if (!ctx) return false;
            await Promise.all(Object.keys(notes).map(async function (note) {{
                const file = notes[note];
                if (!file || audioBuffers[note]) return;
                const res = await fetch('/piano-samples/' + file, {{ cache: 'force-cache' }});
                if (!res.ok) return;
                const buf = await res.arrayBuffer();
                audioBuffers[note] = await ctx.decodeAudioData(buf);
            }}));
            return Object.keys(audioBuffers).length > 0;
        }}

        function unlockAudio() {{
            if (unlockPromise) return unlockPromise;
            const ctx = ensureAudioCtx();
            if (!ctx) return Promise.resolve(false);
            unlockPromise = ctx.resume().then(preloadSamples).then(function () {{ return true; }});
            return unlockPromise;
        }}

        function flashKey(note) {{
            const el = keyEls[note];
            if (!el) return;
            el.classList.add('active');
            if (flashTimers[note]) clearTimeout(flashTimers[note]);
            flashTimers[note] = setTimeout(function () {{
                el.classList.remove('active');
                delete flashTimers[note];
            }}, 450);
        }}

        function playNote(note) {{
            if (!notes[note]) return;
            const ctx = ensureAudioCtx();
            const buffer = audioBuffers[note];
            if (ctx && ctx.state === 'running' && buffer) {{
                const src = ctx.createBufferSource();
                src.buffer = buffer;
                src.connect(ctx.destination);
                try {{ src.start(0); flashKey(note); return; }} catch (_) {{}}
            }}
            const file = notes[note];
            if (!file) return;
            const a = new Audio('/piano-samples/' + file);
            void a.play().then(function () {{ flashKey(note); }}).catch(function () {{}});
        }}

        function noteOctave(name) {{
            const m = String(name || '').match(/(\\d+)$/);
            return m ? parseInt(m[1], 10) : 0;
        }}

        function keysForSegment(fromOct, toOct) {{
            return pianoKeys.filter(function (item) {{
                const o = noteOctave(item.white.name);
                return o >= fromOct && o <= toOct;
            }});
        }}

        function useSegmentedLayout() {{
            if (window.matchMedia('(pointer: coarse)').matches) return true;
            if (window.matchMedia('(max-width: 900px)').matches) return true;
            if ('ontouchstart' in window && Math.min(window.innerWidth, window.innerHeight) <= 1024) return true;
            return false;
        }}

        function appendKeyItem(container, item) {{
            const wrapKey = document.createElement('div');
            wrapKey.className = 'piano-key';
            const white = document.createElement('div');
            white.className = 'piano-key__white';
            white.dataset.note = item.white.name;
            keyEls[item.white.name] = white;
            wrapKey.appendChild(white);
            if (item.black && item.black.name) {{
                const black = document.createElement('div');
                black.className = 'piano-key__black';
                black.dataset.note = item.black.name;
                keyEls[item.black.name] = black;
                wrapKey.appendChild(black);
            }}
            container.appendChild(wrapKey);
        }}

        function buildKeyboard() {{
            pianoEl.replaceChildren();
            pianoStackEl.replaceChildren();
            const segmented = useSegmentedLayout();
            pianoScrollEl.classList.toggle('segmented', segmented);
            if (!segmented) {{
                for (const item of pianoKeys) appendKeyItem(pianoEl, item);
                return;
            }}
            for (const seg of pianoSegments) {{
                const segWrap = document.createElement('div');
                segWrap.className = 'piano-segment';
                const label = document.createElement('div');
                label.className = 'piano-segment__label';
                label.textContent = seg.label || '';
                segWrap.appendChild(label);
                const row = document.createElement('div');
                row.className = 'piano';
                row.style.setProperty('--slot-count', '14');
                for (const item of keysForSegment(seg.from, seg.to)) appendKeyItem(row, item);
                segWrap.appendChild(row);
                pianoStackEl.appendChild(segWrap);
            }}
        }}

        function clearTimers() {{
            for (const t of timers) clearTimeout(t);
            timers = [];
            if (progressTimer) {{ clearInterval(progressTimer); progressTimer = null; }}
        }}

        function currentPlayhead() {{
            if (!playing) return pauseAt;
            return pauseAt + (performance.now() - playStartMs) / 1000;
        }}

        function updateProgress() {{
            const dur = recording ? recording.duration : 0;
            progressEl.textContent = fmtTime(currentPlayhead()) + ' / ' + fmtTime(dur);
        }}

        function scheduleFrom(offsetSec) {{
            clearTimers();
            if (!recording) return;
            const events = recording.events || [];
            for (const evt of events) {{
                const t = typeof evt.t === 'number' ? evt.t : 0;
                if (t < offsetSec) continue;
                const delay = (t - offsetSec) * 1000;
                timers.push(setTimeout(function () {{
                    if (!playing) return;
                    if (evt.action === 'on') playNote(evt.note);
                }}, delay));
            }}
            const remain = Math.max(0, recording.duration - offsetSec) * 1000;
            timers.push(setTimeout(function () {{
                if (!playing) return;
                playing = false;
                pauseAt = recording.duration;
                playBtn.textContent = i18n.replayPlay;
                updateProgress();
            }}, remain));
            progressTimer = setInterval(updateProgress, 200);
        }}

        function startPlayback(fromSec) {{
            void unlockAudio().then(function () {{
                playing = true;
                pauseAt = fromSec || 0;
                playStartMs = performance.now();
                playBtn.textContent = i18n.replayPause;
                scheduleFrom(pauseAt);
                updateProgress();
            }});
        }}

        function pausePlayback() {{
            if (!playing) return;
            playing = false;
            pauseAt = currentPlayhead();
            clearTimers();
            playBtn.textContent = i18n.replayPlay;
            updateProgress();
        }}

        function togglePlay() {{
            if (!recording) return;
            if (playing) pausePlayback();
            else if (pauseAt >= recording.duration) startPlayback(0);
            else startPlayback(pauseAt);
        }}

        function restartPlayback() {{
            pausePlayback();
            pauseAt = 0;
            startPlayback(0);
        }}

        playBtn.addEventListener('click', togglePlay);
        restartBtn.addEventListener('click', restartPlayback);

        async function load() {{
            buildKeyboard();
            try {{
                const res = await fetch('/piano-replay/' + recordingId + '/data', {{ cache: 'no-store' }});
                const data = await res.json().catch(function () {{ return {{}}; }});
                if (!res.ok) throw new Error(data.error || i18n.replayNotFound);
                recording = data;
                const title = (data.title || '').trim();
                if (title) document.getElementById('title').textContent = title;
                metaEl.textContent = i18n.replayBy + ': ' + (data.author || '') +
                    (data.duration ? (' · ' + fmtTime(data.duration)) : '');
                progressEl.textContent = '0:00 / ' + fmtTime(data.duration || 0);
                playBtn.disabled = false;
                restartBtn.disabled = false;
            }} catch (e) {{
                errMsg.hidden = false;
                errMsg.textContent = (e && e.message) || i18n.replayNotFound;
                pianoScrollEl.hidden = true;
            }}
        }}

        void load();
    }})();
    </script>
</body>
</html>"""


def handle_piano_replay_get(handler: "BaseHTTPRequestHandler") -> bool:
    parsed = urlparse(handler.path)
    parts = parsed.path.strip("/").split("/")
    if not parts or parts[0] != "piano-replay":
        return False

    store = piano_sharing.piano_store
    lang = "en"
    try:
        from file_http_server import _page_locale

        lang = _page_locale(handler)
    except Exception:
        pass
    loc = "zh" if str(lang).lower().startswith("zh") else "en"
    not_found = PIANO_TEXTS[loc]["replay_not_found"]

    if len(parts) == 2:
        recording_id = parts[1]
        rec = store.get_recording(recording_id)
        if rec is None:
            handler._send_html_error(404, not_found, lang=lang)  # type: ignore[attr-defined]
            return True
        handler._send_html_page(generate_piano_replay_page(recording_id, lang=lang))  # type: ignore[attr-defined]
        return True

    if len(parts) == 3 and parts[2] == "data":
        recording_id = parts[1]
        rec = store.get_recording(recording_id)
        if rec is None:
            handler._send_error_json(404, not_found)  # type: ignore[attr-defined]
            return True
        handler._send_json_response(  # type: ignore[attr-defined]
            200,
            {
                "version": 1,
                "id": rec.recording_id,
                "author": rec.author,
                "title": rec.title,
                "duration": rec.duration,
                "events": rec.events,
            },
        )
        return True

    handler._send_error_json(404, not_found)  # type: ignore[attr-defined]
    return True
