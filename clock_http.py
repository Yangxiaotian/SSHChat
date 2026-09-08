"""Kindle-friendly chess clock page. No JavaScript.

State lives in clock_sharing. The page is plain HTML links plus a meta
refresh, so an e-ink browser can open it and tap whose turn just ended.
"""

from __future__ import annotations

import html
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

import clock_sharing

if TYPE_CHECKING:
    from http.server import BaseHTTPRequestHandler

_TEXTS = {
    "zh": {
        "title": "棋钟",
        "red": "红方",
        "black": "黑方",
        "note": "红方=先手（象棋红 / 国际象棋白）。走完的一方点自己的大按钮。",
        "kindle": "本页不用脚本，可在 Kindle 浏览器打开。时间由服务器计算。",
        "paused": "未开始",
        "running": "思考中",
        "waiting": "等待",
        "flag": "超时",
        "hit": "走完了",
        "start": "开始计时",
        "pause": "暂停",
        "reset": "重开",
        "base": "每方时间",
        "inc": "每步加秒",
        "closed": "棋钟已关闭或过期。请在聊天里重新发送 /clock。",
        "sec": "秒",
        "min": "分",
    },
    "en": {
        "title": "Chess clock",
        "red": "Red",
        "black": "Black",
        "note": "Red moves first (xiangqi Red / chess White). After you move, tap your own big button.",
        "kindle": "No scripts. Open in the Kindle browser. The server keeps the time.",
        "paused": "Not started",
        "running": "Thinking",
        "waiting": "Waiting",
        "flag": "Time",
        "hit": "Done",
        "start": "Start",
        "pause": "Pause",
        "reset": "Reset",
        "base": "Time each",
        "inc": "Increment",
        "closed": "This clock is closed or expired. Send /clock again in chat.",
        "sec": "s",
        "min": "m",
    },
}

_BASE_PRESETS = (5, 10, 15, 30, 60)
_INC_PRESETS = (0, 2, 3, 5, 10)


def _lang_of(session: clock_sharing.ClockSession, query: dict) -> str:
    raw = (query.get("lang") or [""])[0].strip().lower()
    if raw.startswith("en"):
        session.lang = "en"
    elif raw.startswith("zh"):
        session.lang = "zh"
    return "en" if session.lang == "en" else "zh"


def _send_html(handler: "BaseHTTPRequestHandler", page: str, code: int = 200) -> None:
    body = page.encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _redirect(handler: "BaseHTTPRequestHandler", token: str) -> None:
    handler.send_response(302)
    handler.send_header("Location", f"/clock/{token}")
    handler.send_header("Content-Length", "0")
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()


def _closed_page(handler: "BaseHTTPRequestHandler", lang: str) -> None:
    text = _TEXTS[lang]
    page = (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
        f"<title>{html.escape(text['title'])}</title></head>"
        f"<body><p>{html.escape(text['closed'])}</p></body></html>"
    )
    _send_html(handler, page, 404)


def _refresh_seconds(session: clock_sharing.ClockSession) -> int:
    if session.running is None:
        return 0
    left = min(session.remaining_ms("red"), session.remaining_ms("black"))
    if session.running == "red":
        left = session.remaining_ms("red")
    elif session.running == "black":
        left = session.remaining_ms("black")
    if left <= 60_000:
        return 1
    return 5


def _side_block(session: clock_sharing.ClockSession, side: str, token: str, text: dict) -> str:
    label = text[side]
    ms = session.remaining_ms(side)
    shown = clock_sharing.format_clock(ms)
    active = session.running == side
    flagged = session.flagged == side
    klass = "on" if active else "off"
    status = text["flag"] if flagged else (text["running"] if active else text["waiting"])
    if session.running is None and session.flagged is None:
        status = text["paused"]
        href = f"/clock/{token}/go/{side}"
        action = text["start"]
    elif flagged or session.flagged:
        href = ""
        action = text["flag"] if flagged else text["waiting"]
    elif active:
        href = f"/clock/{token}/go/{side}"
        action = text["hit"]
    else:
        href = ""
        action = text["waiting"]
    if href:
        button = (
            f"<a class=\"btn\" href=\"{html.escape(href)}\">"
            f"{html.escape(label)} {html.escape(action)}</a>"
        )
    else:
        button = f"<p class=\"wait\">{html.escape(label)} {html.escape(action)}</p>"
    return (
        f"<table class=\"{klass}\"><tr><td>"
        f"<div class=\"name\">{html.escape(label)} · {html.escape(status)}</div>"
        f"<div class=\"time\">{html.escape(shown)}</div>"
        f"{button}"
        "</td></tr></table>"
    )


def render_clock_page(session: clock_sharing.ClockSession, lang: str) -> str:
    text = _TEXTS[lang]
    token = session.token
    session.snapshot()
    refresh = _refresh_seconds(session)
    refresh_tag = (
        f"<meta http-equiv=\"refresh\" content=\"{refresh};url=/clock/{token}\">"
        if refresh
        else ""
    )
    inc_sec = session.inc_ms // 1000
    base_min = session.base_ms // 60000
    other_lang = "en" if lang == "zh" else "zh"
    other_label = "English" if lang == "zh" else "中文"
    presets = []
    incs = []
    if session.running is None:
        for minutes in _BASE_PRESETS:
            mark = " *" if minutes == base_min else ""
            presets.append(
                f"<a href=\"/clock/{token}/set?m={minutes}&amp;inc={inc_sec}\">"
                f"{minutes}{html.escape(text['min'])}{mark}</a>"
            )
        for sec in _INC_PRESETS:
            mark = " *" if sec == inc_sec else ""
            incs.append(
                f"<a href=\"/clock/{token}/set?m={base_min}&amp;inc={sec}\">"
                f"+{sec}{html.escape(text['sec'])}{mark}</a>"
            )
    controls = (
        f"<p class=\"ctrl\">"
        f"<a href=\"/clock/{token}/pause\">{html.escape(text['pause'])}</a>"
        " · "
        f"<a href=\"/clock/{token}/reset\">{html.escape(text['reset'])}</a>"
        "</p>"
    )
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width">
{refresh_tag}
<title>{html.escape(text['title'])}</title>
<style>
body {{ margin: 8px; background: #fff; color: #000; font-family: serif; }}
a {{ color: #000; }}
h1 {{ font-size: 28px; margin: 0 0 8px 0; }}
p {{ font-size: 18px; line-height: 1.4; }}
table {{ width: 100%; border-collapse: collapse; margin: 10px 0; }}
td {{ text-align: center; padding: 16px 8px; border: 6px solid #000; }}
.name {{ font-size: 22px; }}
.time {{ font-size: 72px; font-weight: bold; letter-spacing: 2px; }}
.on {{ background: #000; color: #fff; }}
.on a {{ color: #fff; }}
.btn {{
  display: block;
  font-size: 32px;
  font-weight: bold;
  padding: 18px 8px;
  margin: 12px 4px 0 4px;
  border: 4px solid #000;
  background: #fff;
  color: #000;
  text-decoration: none;
}}
.on .btn {{ border-color: #fff; background: #000; color: #fff; }}
.wait {{ font-size: 22px; }}
.ctrl a, .set a {{ font-size: 20px; margin: 0 6px; }}
</style>
</head>
<body>
<h1>{html.escape(text['title'])} {base_min}{html.escape(text['min'])}+{inc_sec}</h1>
<p>{html.escape(text['note'])}</p>
<p>{html.escape(text['kindle'])}</p>
{_side_block(session, "black", token, text)}
{controls}
{_side_block(session, "red", token, text)}
{("".join([
    f"<p class='set'>{html.escape(text['base'])}: {' '.join(presets)}</p>",
    f"<p class='set'>{html.escape(text['inc'])}: {' '.join(incs)}</p>",
]) if presets else "")}
<p><a href="/clock/{token}?lang={other_lang}">{html.escape(other_label)}</a></p>
</body>
</html>
"""


def handle_clock_get(handler: "BaseHTTPRequestHandler") -> bool:
    parsed = urlparse(handler.path)
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if not parts or parts[0] != "clock":
        return False
    if len(parts) < 2:
        _closed_page(handler, "zh")
        return True

    token = parts[1]
    query = parse_qs(parsed.query or "")
    session = clock_sharing.clock_store.get_by_token(token)
    if session is None:
        _closed_page(handler, "zh")
        return True
    lang = _lang_of(session, query)

    action = parts[2] if len(parts) >= 3 else ""
    if action == "go" and len(parts) >= 4:
        session.hit(parts[3])
        _redirect(handler, token)
        return True
    if action == "pause":
        session.pause()
        _redirect(handler, token)
        return True
    if action == "reset":
        session.reset()
        _redirect(handler, token)
        return True
    if action == "set":
        try:
            minutes = int((query.get("m") or ["0"])[0])
            inc = int((query.get("inc") or ["0"])[0])
        except ValueError:
            minutes, inc = 0, 0
        session.set_times(minutes * 60 * 1000, inc * 1000)
        _redirect(handler, token)
        return True
    if action:
        _redirect(handler, token)
        return True

    _send_html(handler, render_clock_page(session, lang))
    return True
