"""Fullscreen chess clock page.

Timing runs in the browser with a small script (setInterval + Date.now),
the same kind of script chess-clock.com uses, so a Kindle can tick smoothly.
Labels are top/bottom, not a color, so chess and xiangqi can share one clock.
"""

from __future__ import annotations

import html
import json
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

import clock_sharing

if TYPE_CHECKING:
    from http.server import BaseHTTPRequestHandler

_TEXTS = {
    "zh": {
        "title": "棋钟",
        "top": "上方",
        "bottom": "下方",
        "note": "走完的一方点自己这边。不区分红黑或白黑。上方已倒置，对面可正着看。",
        "paused": "未开始",
        "running": "思考中",
        "waiting": "等待",
        "flag": "超时",
        "hit": "走完了",
        "start": "开始",
        "pause": "暂停",
        "reset": "重开",
        "setup": "时间",
        "hide": "收起",
        "full": "全屏",
        "base": "每方",
        "inc": "加秒",
        "closed": "棋钟已关闭或过期。请在聊天里重新发送 /clock。",
        "min": "分",
        "sec": "秒",
    },
    "en": {
        "title": "Chess clock",
        "top": "Top",
        "bottom": "Bottom",
        "note": "After you move, tap your own side. No color names. Top is rotated for the player across the table.",
        "paused": "Not started",
        "running": "Thinking",
        "waiting": "Waiting",
        "flag": "Time",
        "hit": "Done",
        "start": "Start",
        "pause": "Pause",
        "reset": "Reset",
        "setup": "Time",
        "hide": "Hide",
        "full": "Full",
        "base": "Each",
        "inc": "Inc",
        "closed": "This clock is closed or expired. Send /clock again in chat.",
        "min": "m",
        "sec": "s",
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


def _int_q(query: dict, key: str, default: int = 0) -> int:
    try:
        return int((query.get(key) or [str(default)])[0])
    except ValueError:
        return default


def render_clock_page(session: clock_sharing.ClockSession, lang: str) -> str:
    text = _TEXTS[lang]
    token = session.token
    snap = session.snapshot()
    boot = {
        "token": token,
        "top": snap["top_ms"],
        "bottom": snap["bottom_ms"],
        "running": snap["running"] or "",
        "flagged": snap["flagged"] or "",
        "inc": snap["inc_ms"],
        "base": snap["base_ms"],
    }
    boot_json = json.dumps(boot, ensure_ascii=False)
    labels = json.dumps(
        {
            "top": text["top"],
            "bottom": text["bottom"],
            "paused": text["paused"],
            "running": text["running"],
            "waiting": text["waiting"],
            "flag": text["flag"],
            "hit": text["hit"],
            "start": text["start"],
            "setup": text["setup"],
            "hide": text["hide"],
        },
        ensure_ascii=False,
    )
    other_lang = "en" if lang == "zh" else "zh"
    other_label = "English" if lang == "zh" else "中文"
    base_links = []
    inc_links = []
    inc_sec = session.inc_ms // 1000
    base_min = session.base_ms // 60000
    for minutes in _BASE_PRESETS:
        mark = " *" if minutes == base_min else ""
        base_links.append(
            f"<a href=\"#\" onclick=\"return setBase({minutes});\">"
            f"{minutes}{html.escape(text['min'])}{mark}</a>"
        )
    for sec in _INC_PRESETS:
        mark = " *" if sec == inc_sec else ""
        inc_links.append(
            f"<a href=\"#\" onclick=\"return setInc({sec});\">"
            f"+{sec}{html.escape(text['sec'])}{mark}</a>"
        )
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>{html.escape(text['title'])}</title>
<style>
html, body {{
  margin: 0;
  padding: 0;
  height: 100%;
  width: 100%;
  background: #fff;
  color: #000;
  font-family: sans-serif;
  overflow: hidden;
}}
#app {{
  position: absolute;
  left: 0;
  top: 0;
  right: 0;
  bottom: 0;
}}
.side {{
  position: absolute;
  left: 0;
  right: 0;
  height: 46%;
  border: 8px solid #000;
  box-sizing: border-box;
  text-align: center;
}}
#top {{
  top: 0;
  /* Opposite player sits across the device; keep their half upright. */
  transform: rotate(180deg);
}}
#bottom {{ bottom: 0; }}
.on {{ background: #000; color: #fff; }}
.name {{
  font-size: 4vh;
  margin-top: 1vh;
}}
.time {{
  font-size: 16vh;
  font-weight: bold;
  line-height: 1;
  margin-top: 2vh;
}}
.act {{
  font-size: 5vh;
  font-weight: bold;
  margin-top: 1vh;
}}
#mid {{
  position: absolute;
  left: 0;
  right: 0;
  top: 46%;
  height: 8%;
  text-align: center;
  font-size: 3vh;
  line-height: 8vh;
  z-index: 3;
  background: #fff;
}}
#mid a {{
  color: #000;
  margin: 0 1vw;
}}
#setup {{
  display: none;
  position: absolute;
  left: 4%;
  right: 4%;
  top: 10%;
  max-height: 34%;
  overflow: auto;
  background: #fff;
  color: #000;
  border: 4px solid #000;
  padding: 2vh;
  font-size: 3.5vh;
  z-index: 2;
  box-sizing: border-box;
}}
#setup a {{ color: #000; margin: 0 1vw; }}
#setup .close {{
  display: block;
  text-align: center;
  font-weight: bold;
  margin-bottom: 1vh;
}}
</style>
</head>
<body>
<div id="app">
  <div id="top" class="side" onclick="hit('top')">
    <div class="name" id="topName"></div>
    <div class="time" id="topTime">00:00</div>
    <div class="act" id="topAct"></div>
  </div>
  <div id="mid">
    <a href="#" onclick="return pauseClock();">{html.escape(text['pause'])}</a>
    <a href="#" onclick="return resetClock();">{html.escape(text['reset'])}</a>
    <a href="#" onclick="return toggleSetup();" id="setupBtn">{html.escape(text['setup'])}</a>
    <a href="#" onclick="return goFull();">{html.escape(text['full'])}</a>
    <a href="/clock/{token}?lang={other_lang}">{html.escape(other_label)}</a>
  </div>
  <div id="bottom" class="side" onclick="hit('bottom')">
    <div class="name" id="bottomName"></div>
    <div class="time" id="bottomTime">00:00</div>
    <div class="act" id="bottomAct"></div>
  </div>
  <div id="setup" onclick="event.stopPropagation();">
    <a href="#" class="close" onclick="return toggleSetup();">{html.escape(text['hide'])}</a>
    <p>{html.escape(text['note'])}</p>
    <p>{html.escape(text['base'])}: {' '.join(base_links)}</p>
    <p>{html.escape(text['inc'])}: {' '.join(inc_links)}</p>
  </div>
</div>
<script>
var S = {boot_json};
var L = {labels};
var deadline = 0;

function nowMs() {{
  return (new Date()).getTime();
}}

function fmt(ms) {{
  if (ms < 0) ms = 0;
  var total = Math.floor(ms / 1000);
  var h = Math.floor(total / 3600);
  var m = Math.floor((total % 3600) / 60);
  var s = total % 60;
  function pad(n) {{ return n < 10 ? "0" + n : "" + n; }}
  if (h > 0) return h + ":" + pad(m) + ":" + pad(s);
  return pad(m) + ":" + pad(s);
}}

function remain(side) {{
  if (S.flagged) return S[side];
  if (S.running === side && deadline) {{
    var left = deadline - nowMs();
    if (left < 0) left = 0;
    return left;
  }}
  return S[side];
}}

function other(side) {{
  return side === "top" ? "bottom" : "top";
}}

function freeze() {{
  if (S.running) {{
    var left = deadline - nowMs();
    if (left < 0) left = 0;
    S[S.running] = left;
  }}
}}

function saveLocal() {{
  freeze();
  try {{
    localStorage.setItem("sshchat-clock-" + S.token, JSON.stringify(S));
  }} catch (e) {{}}
}}

function loadLocal() {{
  try {{
    var raw = localStorage.getItem("sshchat-clock-" + S.token);
    if (!raw) return;
    var saved = JSON.parse(raw);
    if (!saved || saved.base !== S.base || saved.inc !== S.inc) return;
    S.top = saved.top;
    S.bottom = saved.bottom;
    S.running = saved.running || "";
    S.flagged = saved.flagged || "";
  }} catch (e) {{}}
}}

function ping() {{
  var run = S.running || "";
  var flag = S.flagged || "";
  var url = "/clock/" + S.token + "/save?top=" + Math.floor(remain("top"))
    + "&bottom=" + Math.floor(remain("bottom"))
    + "&run=" + encodeURIComponent(run)
    + "&flag=" + encodeURIComponent(flag);
  var img = new Image();
  img.src = url + "&t=" + nowMs();
}}

function arm(side) {{
  S.running = side;
  deadline = nowMs() + S[side];
}}

function paint() {{
  var sides = ["top", "bottom"];
  var i;
  for (i = 0; i < sides.length; i++) {{
    var side = sides[i];
    var left = remain(side);
    var el = document.getElementById(side);
    var name = document.getElementById(side + "Name");
    var time = document.getElementById(side + "Time");
    var act = document.getElementById(side + "Act");
    var status = L.waiting;
    var action = L.waiting;
    if (S.flagged === side) {{
      status = L.flag;
      action = L.flag;
      left = 0;
    }} else if (S.flagged) {{
      status = L.waiting;
      action = L.waiting;
    }} else if (!S.running) {{
      status = L.paused;
      action = L.start;
    }} else if (S.running === side) {{
      status = L.running;
      action = L.hit;
    }}
    name.innerHTML = L[side] + " · " + status;
    time.innerHTML = fmt(left);
    act.innerHTML = L[side] + " " + action;
    if (S.running === side && !S.flagged) el.className = "side on";
    else el.className = "side";
  }}
  if (S.running && !S.flagged && remain(S.running) <= 0) {{
    S[S.running] = 0;
    S.flagged = S.running;
    S.running = "";
    deadline = 0;
    saveLocal();
    ping();
    paint();
  }}
}}

function hit(side) {{
  if (S.flagged) return;
  if (!S.running) {{
    arm(side);
  }} else if (S.running === side) {{
    var left = deadline - nowMs();
    if (left < 0) left = 0;
    if (left <= 0) {{
      S[side] = 0;
      S.flagged = side;
      S.running = "";
      deadline = 0;
    }} else {{
      S[side] = left + S.inc;
      arm(other(side));
    }}
  }} else {{
    return;
  }}
  saveLocal();
  ping();
  paint();
  goFull();
}}

function pauseClock() {{
  if (S.running) {{
    var side = S.running;
    var left = deadline - nowMs();
    if (left < 0) left = 0;
    S[side] = left;
    S.running = "";
    deadline = 0;
    saveLocal();
    ping();
    paint();
  }}
  return false;
}}

function resetClock() {{
  S.top = S.base;
  S.bottom = S.base;
  S.running = "";
  S.flagged = "";
  deadline = 0;
  saveLocal();
  ping();
  paint();
  return false;
}}

function setBase(minutes) {{
  if (S.running) return false;
  S.base = minutes * 60 * 1000;
  resetClock();
  return false;
}}

function setInc(sec) {{
  if (S.running) return false;
  S.inc = sec * 1000;
  saveLocal();
  ping();
  return false;
}}

function toggleSetup() {{
  var box = document.getElementById("setup");
  var btn = document.getElementById("setupBtn");
  if (box.style.display === "block") {{
    box.style.display = "none";
    btn.innerHTML = L.setup;
  }} else {{
    box.style.display = "block";
    btn.innerHTML = L.hide;
  }}
  return false;
}}

function goFull() {{
  var el = document.documentElement;
  var req = el.requestFullscreen || el.webkitRequestFullscreen || el.webkitRequestFullScreen;
  if (req) {{
    try {{ req.call(el); }} catch (e) {{}}
  }}
  return false;
}}

loadLocal();
if (S.running) arm(S.running);
paint();
setInterval(paint, 200);
setInterval(function() {{ saveLocal(); }}, 2000);
</script>
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
    if action == "save":
        run = (query.get("run") or [""])[0].strip()
        flag = (query.get("flag") or [""])[0].strip()
        session.apply_report(
            top_ms=_int_q(query, "top"),
            bottom_ms=_int_q(query, "bottom"),
            running=run or None,
            flagged=flag or None,
        )
        handler.send_response(204)
        handler.send_header("Content-Length", "0")
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        return True
    if action == "go" and len(parts) >= 4:
        side = parts[3]
        if side == "red":
            side = "top"
        elif side == "black":
            side = "bottom"
        session.hit(side)
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
        minutes = _int_q(query, "m")
        inc = _int_q(query, "inc")
        session.set_times(minutes * 60 * 1000, inc * 1000)
        _redirect(handler, token)
        return True
    if action:
        _redirect(handler, token)
        return True

    _send_html(handler, render_clock_page(session, lang))
    return True
