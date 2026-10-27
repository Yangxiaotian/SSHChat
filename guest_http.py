"""One-shot guest chat page + WebSocket, served on FileHTTP."""

from __future__ import annotations

import html
import json
import struct
import threading
import time
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

import guest_sharing
import piano_ws

if TYPE_CHECKING:
    from http.server import BaseHTTPRequestHandler

# Filled by server.bind_chat so guests join the running process's rooms
# (`python server.py` is __main__, not the `server` module).
_attach_guest = None
_process_client_line = None
_remove_client = None


def bind_chat(*, attach_guest, process_client_line, remove_client) -> None:
    global _attach_guest, _process_client_line, _remove_client
    _attach_guest = attach_guest
    _process_client_line = process_client_line
    _remove_client = remove_client


_TEXTS = {
    "zh": {
        "title": "SSHChat 临时聊天",
        "room": "房间",
        "you": "你是",
        "enter": "进入聊天",
        "send": "发送",
        "hint": "只能打开一次，进入后 {minutes} 分钟。页面倒计时显示分和秒。昵称由系统随机分配。请勿刷新，刷新后立即失效。",
        "used": "这个链接已经用过（或刚刷新过），不能再打开。请让对方重新发一条 /guest。",
        "expired": "临时聊天已结束。",
        "gone": "链接无效或已过期。请让对方重新发一条 /guest。",
        "left": "剩余",
        "placeholder": "说点什么…",
        "fail": "进不去。",
    },
    "en": {
        "title": "SSHChat guest chat",
        "room": "Room",
        "you": "You are",
        "enter": "Enter chat",
        "send": "Send",
        "hint": "One open only. After you enter you have {minutes} minutes. The countdown shows minutes and seconds. Your nick is assigned at random. Do not refresh — a reload kills the link immediately.",
        "used": "This link was already used (or you refreshed). It cannot be opened again. Ask them to send /guest once more.",
        "expired": "Guest chat has ended.",
        "gone": "This link is invalid or expired. Ask them to send /guest once more.",
        "left": "Left",
        "placeholder": "Message…",
        "fail": "Could not join.",
    },
}


def _lang(handler: "BaseHTTPRequestHandler") -> str:
    parsed = urlparse(handler.path)
    q = parse_qs(parsed.query or "")
    raw = ((q.get("lang") or [""])[0] or "").strip().lower()
    if raw.startswith("zh"):
        return "zh"
    if raw.startswith("en"):
        return "en"
    accept = (handler.headers.get("Accept-Language") or "").lower()
    if "zh" in accept:
        return "zh"
    return "en"


def _send_html(handler: "BaseHTTPRequestHandler", page: str, code: int = 200) -> None:
    body = page.encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _send_json(handler: "BaseHTTPRequestHandler", code: int, data: dict) -> None:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _closed_page(handler: "BaseHTTPRequestHandler", lang: str, reason: str) -> None:
    text = _TEXTS[lang]
    msg = text.get(reason) or text["gone"]
    page = (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
        f"<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{html.escape(text['title'])}</title></head>"
        f"<body style=\"font-family:sans-serif;padding:1.5rem\">"
        f"<p>{html.escape(msg)}</p></body></html>"
    )
    _send_html(handler, page, 410)


def render_guest_page(link: guest_sharing.GuestLink, lang: str) -> str:
    text = _TEXTS[lang]
    minutes = link.minutes or guest_sharing.DEFAULT_MINUTES
    boot = {
        "token": link.token,
        "room": link.room,
        "lang": lang,
    }
    boot_json = json.dumps(boot, ensure_ascii=False)
    labels = json.dumps(
        {
            "fail": text["fail"],
            "expired": text["expired"],
            "used": text["used"],
            "left": text["left"],
            "you": text["you"],
        },
        ensure_ascii=False,
    )
    return f"""<!DOCTYPE html>
<html lang="{'zh-CN' if lang == 'zh' else 'en'}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{html.escape(text['title'])}</title>
<style>
html,body {{ margin:0; height:100%; background:#111; color:#eee; font-family:sans-serif; }}
#app {{ display:flex; flex-direction:column; height:100%; }}
#bar {{ padding:.6rem 1rem; background:#000; display:flex; justify-content:space-between; gap:1rem; }}
#left {{ font-variant-numeric:tabular-nums; font-weight:bold; color:#f66; }}
#gate, #chat {{ flex:1; display:flex; flex-direction:column; }}
#chat {{ display:none; }}
#log {{ flex:1; overflow:auto; padding:1rem; white-space:pre-wrap; word-break:break-word; font-family:ui-monospace,monospace; font-size:14px; }}
#row {{ display:flex; padding:.5rem; gap:.5rem; }}
#row input {{ flex:1; padding:.6rem; font-size:16px; }}
#gate {{ padding:1.5rem; max-width:28rem; }}
#gate p {{ line-height:1.5; color:#ccc; }}
button {{ padding:.6rem 1rem; font-size:16px; }}
.warn {{ color:#f6a; }}
</style>
</head>
<body>
<div id="app">
  <div id="bar">
    <div id="who">{html.escape(text['title'])} · {html.escape(text['room'])} #{html.escape(link.room)}</div>
    <div id="left"></div>
  </div>
  <div id="gate">
    <p class="warn">{html.escape(text['hint'].format(minutes=minutes))}</p>
    <p><button type="button" id="go">{html.escape(text['enter'])}</button></p>
    <p id="err" class="warn"></p>
  </div>
  <div id="chat">
    <div id="log"></div>
    <form id="row">
      <input id="msg" maxlength="500" placeholder="{html.escape(text['placeholder'])}" autocomplete="off">
      <button type="submit">{html.escape(text['send'])}</button>
    </form>
  </div>
</div>
<script>
var boot = {boot_json};
var L = {labels};
var ws, exp = 0;
function fmt(s) {{
  s = Math.max(0, s|0);
  var m = Math.floor(s/60), r = s % 60;
  var ss = (r<10?'0':'')+r;
  if (boot.lang === 'zh') return m + '分' + ss + '秒';
  return m + 'm ' + ss + 's';
}}
function tick() {{
  if (!exp) return;
  var left = Math.max(0, Math.floor(exp - Date.now()/1000));
  document.getElementById('left').textContent = L.left + ' ' + fmt(left);
  if (left <= 0) over(L.expired);
}}
function over(msg) {{
  document.getElementById('log').textContent += '\\n' + msg;
  document.getElementById('msg').disabled = true;
  try {{ ws && ws.close(); }} catch (e) {{}}
}}
function append(s) {{
  var log = document.getElementById('log');
  log.textContent += s;
  log.scrollTop = log.scrollHeight;
}}
function proto() {{ return location.protocol === 'https:' ? 'wss:' : 'ws:'; }}
function connect(ticket) {{
  ws = new WebSocket(proto() + '//' + location.host + '/guest/' + boot.token + '/ws?k=' + encodeURIComponent(ticket));
  ws.onmessage = function (ev) {{
    var m;
    try {{ m = JSON.parse(ev.data); }} catch (e) {{ return; }}
    if (m.t === 'line') append(m.s);
    if (m.t === 'hello' && m.exp) exp = m.exp;
    if (m.t === 'end') over(m.why === 'timeout' ? L.expired : L.used);
  }};
  ws.onclose = function () {{
    if (exp && Date.now()/1000 < exp) over(L.used);
  }};
}}
document.getElementById('go').onclick = function () {{
  fetch('/guest/' + boot.token + '/join?lang=' + encodeURIComponent(boot.lang), {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: '{{}}'
  }}).then(function (r) {{ return r.json().then(function (j) {{ return {{ok: r.ok, j: j}}; }}); }})
    .then(function (x) {{
      if (!x.ok || !x.j.ok) {{
        document.getElementById('err').textContent = (x.j && x.j.error) || L.fail;
        return;
      }}
      exp = x.j.exp;
      document.getElementById('who').textContent = L.you + ' ' + x.j.nick + ' · #' + boot.room;
      document.getElementById('gate').style.display = 'none';
      document.getElementById('chat').style.display = 'flex';
      connect(x.j.ticket);
      tick();
    }}).catch(function () {{
      document.getElementById('err').textContent = L.fail;
    }});
}};
document.getElementById('row').onsubmit = function (e) {{
  e.preventDefault();
  var box = document.getElementById('msg');
  var s = box.value;
  if (!s || !ws || ws.readyState !== 1) return;
  ws.send(JSON.stringify({{t: 'msg', s: s}}));
  box.value = '';
}};
setInterval(tick, 250);
</script>
</body>
</html>
"""


def handle_guest_get(handler: "BaseHTTPRequestHandler") -> bool:
    parsed = urlparse(handler.path)
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if not parts or parts[0] != "guest":
        return False
    lang = _lang(handler)
    if len(parts) < 2:
        _closed_page(handler, lang, "gone")
        return True
    if len(parts) >= 3 and parts[2] == "ws":
        return False
    token = parts[1]
    link = guest_sharing.guest_store.get(token)
    if link is None:
        _closed_page(handler, lang, "gone")
        return True
    if link.opened_at:
        _closed_page(handler, lang, "used")
        return True
    _send_html(handler, render_guest_page(link, lang))
    return True


def handle_guest_post(handler: "BaseHTTPRequestHandler") -> bool:
    parsed = urlparse(handler.path)
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) != 3 or parts[0] != "guest" or parts[2] != "join":
        return False
    lang = _lang(handler)
    text = _TEXTS[lang]
    token = parts[1]
    if hasattr(handler, "_read_json_body"):
        handler._read_json_body()  # type: ignore[attr-defined]
    link, err = guest_sharing.guest_store.open_once(token)
    if link is None:
        _send_json(handler, 410, {"ok": False, "error": text.get(err, text["used"])})
        return True
    link.locale = lang
    _schedule_kick(link)
    _send_json(
        handler,
        200,
        {
            "ok": True,
            "ticket": link.ws_ticket,
            "exp": int(link.chat_expires_at),
            "nick": link.nick,
            "room": link.room,
        },
    )
    return True


def handle_guest_websocket(handler: "BaseHTTPRequestHandler") -> bool:
    parsed = urlparse(handler.path)
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) != 3 or parts[0] != "guest" or parts[2] != "ws":
        return False
    upgrade = (handler.headers.get("Upgrade") or "").strip().lower()
    connection = (handler.headers.get("Connection") or "").lower()
    sec_key = (handler.headers.get("Sec-WebSocket-Key") or "").strip()
    if upgrade != "websocket" or "upgrade" not in connection or not sec_key:
        handler.send_response(400)
        handler.end_headers()
        return True
    ticket = ((parse_qs(parsed.query or "").get("k") or [""])[0] or "").strip()
    token = parts[1]
    link, err = guest_sharing.guest_store.ws_ok(token, ticket)
    if link is None:
        handler.send_response(403)
        handler.send_header("Content-Type", "text/plain; charset=utf-8")
        handler.end_headers()
        try:
            handler.wfile.write((err or "forbidden").encode("utf-8"))
        except Exception:
            pass
        return True

    accept = piano_ws.ws_accept_key(sec_key)
    handler.send_response(101, "Switching Protocols")
    handler.send_header("Upgrade", "websocket")
    handler.send_header("Connection", "Upgrade")
    handler.send_header("Sec-WebSocket-Accept", accept)
    handler.end_headers()
    try:
        handler.wfile.flush()
    except Exception:
        pass
    handler.close_connection = True
    sock = handler.connection
    piano_ws.try_enable_tcp_nodelay(sock)
    try:
        sock.settimeout(None)
    except OSError:
        pass
    _run_guest_ws(link, sock)
    return True


def _schedule_kick(link: guest_sharing.GuestLink) -> None:
    if link.kick_started:
        return
    link.kick_started = True

    def wait() -> None:
        delay = link.chat_expires_at - time.time()
        if delay > 0:
            time.sleep(delay)
        if link.closed:
            return
        if time.time() < link.chat_expires_at - 0.05:
            return
        conn = link.conn
        try:
            if conn is not None:
                conn.send_json({"t": "end", "why": "timeout"})
        except Exception:
            pass
        try:
            if conn is not None and _remove_client is not None:
                _remove_client(conn)
        except Exception:
            pass
        guest_sharing.guest_store.close_token(link.token)

    threading.Thread(target=wait, daemon=True, name="guest-kick").start()


def _run_guest_ws(link: guest_sharing.GuestLink, sock) -> None:
    attach = _attach_guest
    process_line = _process_client_line
    drop = _remove_client
    if attach is None or process_line is None or drop is None:
        raise RuntimeError("guest chat is not bound to the running server")

    gconn = guest_sharing.GuestConn()
    gconn.attach_sock(sock)
    old = link.conn
    link.conn = gconn
    if old is not None and old is not gconn:
        try:
            old.send_json({"t": "end", "why": "replaced"})
        except Exception:
            pass
        try:
            drop(old)
        except Exception:
            pass
    attached = False
    try:
        gconn.send_json(
            {
                "t": "hello",
                "exp": int(link.chat_expires_at),
                "room": link.room,
                "nick": link.nick,
            }
        )
        nick = link.nick or guest_sharing.random_guest_nick()
        nick = attach(gconn, nick, link.room, link.locale, minutes=link.minutes) or nick
        attached = True
        link.nick = nick
        while not gconn.closed:
            if time.time() >= link.chat_expires_at:
                gconn.send_json({"t": "end", "why": "timeout"})
                break
            try:
                opcode, payload = piano_ws.decode_frame(sock)
            except (ConnectionError, OSError, ValueError, struct.error):
                break
            if opcode == piano_ws.OP_CLOSE:
                break
            if opcode == piano_ws.OP_PING:
                try:
                    sock.sendall(
                        piano_ws.encode_frame(payload, opcode=piano_ws.OP_PONG)
                    )
                except OSError:
                    break
                continue
            if opcode == piano_ws.OP_PONG:
                continue
            if opcode not in (piano_ws.OP_TEXT, piano_ws.OP_BINARY):
                continue
            try:
                msg = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(msg, dict):
                continue
            if str(msg.get("t") or "") != "msg":
                continue
            text = str(msg.get("s") or "")[: guest_sharing.MAX_MSG_CHARS]
            if not text.strip():
                continue
            process_line(gconn, text.encode("utf-8"))
    finally:
        if link.conn is gconn:
            link.conn = None
        if attached:
            try:
                drop(gconn)
            except Exception:
                pass
        try:
            gconn.close()
        except Exception:
            pass
