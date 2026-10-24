"""HTML playlist player + audio for /music (served by FileHTTP)."""
from __future__ import annotations

import html
import json
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, unquote, urlparse

import music

if TYPE_CHECKING:
    from http.server import BaseHTTPRequestHandler

_TEXTS = {
    "zh": {
        "title": "点歌",
        "unlock": "解锁",
        "key_ph": "6 位密钥",
        "bad_key": "密钥不对",
        "empty": "曲库是空的。把 mp3 / m4a / ogg / flac / wav 放到服务端 music 目录。",
        "closed": "播放页已过期。请在聊天里重新发送 /music play。",
        "close": "关闭",
        "prev": "上一首",
        "next": "下一首",
        "hint": "点列表切歌；播完自动下一首。",
        "play_fail": "无法播放。点列表重试。",
    },
    "en": {
        "title": "Music",
        "unlock": "Unlock",
        "key_ph": "6-char key",
        "bad_key": "Wrong key",
        "empty": "Library is empty. Place mp3 / m4a / ogg / flac / wav in the server music directory.",
        "closed": "This player expired. Send /music play again in chat.",
        "close": "Close",
        "prev": "Prev",
        "next": "Next",
        "hint": "Tap a song to play. Advances automatically.",
        "play_fail": "Can't play. Tap the list to retry.",
    },
}


def _lang_of(handler: "BaseHTTPRequestHandler") -> str:
    try:
        from file_http_server import _page_locale

        loc = _page_locale(handler)
    except Exception:
        loc = "en"
    return "zh" if str(loc).lower().startswith("zh") else "en"


def generate_music_page(
    token: str,
    lang: str,
    start_index: int,
    songs: list,
) -> str:
    loc = "zh" if str(lang).lower().startswith("zh") else "en"
    t = _TEXTS[loc]
    payload = []
    for item in songs or []:
        row = music._coerce_player_row(item)
        if row:
            payload.append(row)
    songs_js = json.dumps(payload, ensure_ascii=False)
    token_js = json.dumps(token)
    start = max(1, int(start_index or 1))
    title = html.escape(t["title"])
    empty = html.escape(t["empty"])
    return f"""<!DOCTYPE html>
<html lang="{loc}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>{title}</title>
<style>
:root {{ color-scheme: dark; }}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; font-family: system-ui, sans-serif; background: #111; color: #eee;
  min-height: 100vh;
}}
#lock, #stageWrap {{
  min-height: 100vh; padding: 48px 16px calc(24px + env(safe-area-inset-bottom));
}}
#lock {{
  display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 12px;
}}
#stageWrap {{ display: none; flex-direction: column; gap: 12px; max-width: 640px; margin: 0 auto; }}
h1 {{ font-size: 1.2rem; margin: 0; }}
input, button {{ font: inherit; padding: 10px 14px; border-radius: 8px; border: 0; }}
#key {{
  width: 10em; letter-spacing: .2em; text-align: center; text-transform: uppercase;
  background: #222; color: #eee;
}}
button {{ background: #3b82f6; color: #fff; }}
#closeBtn {{
  position: fixed; z-index: 9; background: #333; color: #ddd;
  top: calc(12px + env(safe-area-inset-top, 0px));
  right: calc(12px + env(safe-area-inset-right, 0px));
}}
.hint, .empty, .bad, .err {{ color: #f87171; font-size: .9rem; }}
.hint, .empty {{ color: #aaa; }}
audio {{ width: 100%; }}
.now {{ font-weight: 600; word-break: break-all; }}
#list {{ list-style: none; margin: 0; padding: 0; }}
#list li {{
  padding: 10px 12px; border-bottom: 1px solid #2a2a2a; cursor: pointer;
  display: flex; justify-content: space-between; gap: 8px;
}}
#list li.active {{ background: #1e3a5f; }}
#list .sz {{ color: #888; font-size: .85rem; white-space: nowrap; }}
.row {{ display: flex; gap: 8px; flex-wrap: wrap; }}
</style>
</head>
<body>
<button id="closeBtn" type="button">{html.escape(t["close"])}</button>
<div id="lock">
  <h1>{title}</h1>
  <input id="key" maxlength="6" autocomplete="off" placeholder="{html.escape(t["key_ph"])}">
  <button id="unlockBtn" type="button">{html.escape(t["unlock"])}</button>
  <div class="bad" id="bad" hidden></div>
</div>
<div id="stageWrap">
  <h1>{title}</h1>
  <div class="now" id="now"></div>
  <audio id="player" controls playsinline webkit-playsinline></audio>
  <div class="row">
    <button id="prevBtn" type="button">{html.escape(t["prev"])}</button>
    <button id="nextBtn" type="button">{html.escape(t["next"])}</button>
  </div>
  <div class="hint">{html.escape(t["hint"])}</div>
  <div class="err" id="err" hidden>{html.escape(t["play_fail"])}</div>
  <div class="empty" id="empty" hidden>{empty}</div>
  <ul id="list"></ul>
</div>
<script>
const SONGS = {songs_js};
const TOKEN = {token_js};
const START = {start};
const BAD_KEY = {json.dumps(t["bad_key"])};
const PLAY_FAIL = {json.dumps(t["play_fail"])};
let current = 0;
let playGen = 0;
let waCtx = null;
let waNode = null;
function $(id) {{ return document.getElementById(id); }}
function fileUrl(s) {{
  var u = location.origin + "/music/" + TOKEN + "/file/" + encodeURIComponent(s.name || "");
  if (s.origin) u += "?origin=" + encodeURIComponent(s.origin);
  return u;
}}
function songLabel(s) {{
  return s.origin ? ((s.name || "") + " @" + s.origin) : (s.name || "");
}}
function stopWebAudio() {{
  if (waNode) {{ try {{ waNode.stop(); }} catch (e) {{}} waNode = null; }}
}}
function inviteKey() {{
  try {{
    var inj = (window.__SSHCHAT_KEY || "").toString().trim().toUpperCase();
    if (/^[A-Z0-9]{{6}}$/.test(inj)) return inj;
  }} catch (e) {{}}
  var hash = (location.hash || "").replace(/^#/, "").toUpperCase();
  if (/^[A-Z0-9]{{6}}$/.test(hash)) return hash;
  return "";
}}
function renderList() {{
  const ul = $("list");
  ul.innerHTML = "";
  SONGS.forEach((s, i) => {{
    const li = document.createElement("li");
    if (i === current) li.className = "active";
    li.innerHTML = "<span></span><span class='sz'></span>";
    li.children[0].textContent = songLabel(s);
    li.children[1].textContent = s.size;
    li.onclick = () => playAt(i);
    ul.appendChild(li);
  }});
}}
function playViaWebAudio(url) {{
  fetch(url, {{ cache: "no-store" }}).then((res) => {{
    if (!res.ok) throw new Error("HTTP " + res.status);
    return res.arrayBuffer();
  }}).then((buf) => {{
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) throw new Error("no AudioContext");
    waCtx = waCtx || new Ctx();
    return waCtx.resume().then(() => waCtx.decodeAudioData(buf.slice(0)));
  }}).then((decoded) => {{
    stopWebAudio();
    waNode = waCtx.createBufferSource();
    waNode.buffer = decoded;
    waNode.connect(waCtx.destination);
    waNode.onended = () => playAt(current + 1);
    waNode.start(0);
    $("err").hidden = true;
  }}).catch((e) => {{
    $("err").hidden = false;
    $("err").textContent = PLAY_FAIL + (e && e.message ? (" " + e.message) : "");
  }});
}}
function playAt(i) {{
  if (!SONGS.length) return;
  current = (i + SONGS.length) % SONGS.length;
  const s = SONGS[current];
  const gen = ++playGen;
  $("now").textContent = songLabel(s);
  $("err").hidden = true;
  renderList();
  stopWebAudio();
  const a = $("player");
  const url = fileUrl(s);
  a.onerror = () => {{ if (gen === playGen) playViaWebAudio(url); }};
  a.src = url;
  a.load();
  a.play().catch(() => {{}});
}}
function closePage() {{
  try {{ if (window.SSHChatNative && SSHChatNative.close) SSHChatNative.close(); }} catch (e) {{}}
  try {{ window.close(); }} catch (e) {{}}
}}
function unlock() {{
  const key = ($("key").value || "").trim().toUpperCase();
  $("bad").hidden = true;
  fetch("/music/" + TOKEN + "/auth", {{
    method: "POST",
    headers: {{ "Content-Type": "application/json" }},
    cache: "no-store",
    body: JSON.stringify({{ key: key }}),
  }}).then((res) => res.json().then((data) => ({{ res: res, data: data }}), () => ({{ res: res, data: {{}} }})))
    .then((x) => {{
      if (!x.res.ok) throw new Error((x.data && x.data.error) || BAD_KEY);
      $("lock").style.display = "none";
      $("stageWrap").style.display = "flex";
      if (!SONGS.length) {{ $("empty").hidden = false; }}
      else {{ playAt(Math.max(0, START - 1)); }}
    }})
    .catch(() => {{ $("bad").hidden = false; $("bad").textContent = BAD_KEY; }});
}}
$("closeBtn").onclick = closePage;
$("unlockBtn").onclick = unlock;
$("key").addEventListener("keydown", (e) => {{ if (e.key === "Enter") unlock(); }});
$("prevBtn").onclick = () => playAt(current - 1);
$("nextBtn").onclick = () => playAt(current + 1);
$("player").addEventListener("ended", () => playAt(current + 1));
var boot = inviteKey();
if (boot) $("key").value = boot;
</script>
</body>
</html>
"""


def _serve_audio(handler: "BaseHTTPRequestHandler", path, filename: str) -> None:
    # Same recipe as /piano-samples: 200 + whole file. No Content-Disposition
    # (Android MediaPlayer treats that as a download). Ignore Range so a
    # Cloudflare 206 rewrite cannot stall <audio>.
    try:
        size = path.stat().st_size
        fh = open(path, "rb")
    except OSError as e:
        print(f"[MusicHTTP] open error: {e}")
        handler._send_error_json(500, "无法读取音频")  # type: ignore[attr-defined]
        return
    with fh:
        handler.send_response(200)
        handler.send_header("Content-Type", music.mime_for(filename))
        handler.send_header("Content-Length", str(size))
        handler.send_header("Cache-Control", "no-transform, private, max-age=60")
        handler.send_header("Access-Control-Allow-Origin", "*")
        handler.end_headers()
        if handler.command == "HEAD":
            return
        while True:
            chunk = fh.read(8192)
            if not chunk:
                break
            handler.wfile.write(chunk)


def _closed(handler: "BaseHTTPRequestHandler") -> str:
    lang = _lang_of(handler)
    loc = "zh" if lang == "zh" else "en"
    return _TEXTS[loc]["closed"]


def handle_music_post(handler: "BaseHTTPRequestHandler") -> bool:
    """POST /music/{token}/auth with JSON {key}."""
    parsed = urlparse(handler.path)
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) != 3 or parts[0] != "music" or parts[2] != "auth":
        return False
    lang = _lang_of(handler)
    loc = "zh" if lang == "zh" else "en"
    session = music.music_store.get(parts[1])
    if session is None:
        handler._send_error_json(404, _TEXTS[loc]["closed"])  # type: ignore[attr-defined]
        return True
    body = handler._read_json_body()  # type: ignore[attr-defined]
    key = str(body.get("key", "")).strip().upper()
    if key != session.key:
        handler._send_error_json(403, _TEXTS[loc]["bad_key"])  # type: ignore[attr-defined]
        return True
    songs = music.player_rows()
    handler._send_json_response(  # type: ignore[attr-defined]
        200,
        {
            "ok": True,
            "start": int(session.start_index or 1),
            "songs": songs,
        },
    )
    return True


def handle_music_get(handler: "BaseHTTPRequestHandler") -> bool:
    """Return True if this was a /music request and it was handled."""
    parsed = urlparse(handler.path)
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if not parts or parts[0] != "music":
        return False

    lang = _lang_of(handler)
    closed = _closed(handler)

    if len(parts) < 2:
        handler._send_error_json(404, closed)  # type: ignore[attr-defined]
        return True

    token = parts[1]
    session = music.music_store.get(token)
    if session is None:
        handler._send_html_error(404, closed, lang=lang)  # type: ignore[attr-defined]
        return True

    if len(parts) == 2:
        if handler.command == "HEAD":
            handler.send_response(200)
            handler.send_header("Content-Type", "text/html; charset=utf-8")
            handler.send_header("Cache-Control", "no-store")
            handler.end_headers()
            return True
        songs = music.player_rows()
        handler._send_html_page(  # type: ignore[attr-defined]
            generate_music_page(token, lang, session.start_index, songs)
        )
        return True

    if len(parts) >= 4 and parts[2] == "file":
        name = unquote("/".join(parts[3:]))
        origin = ""
        try:
            origin = str((parse_qs(parsed.query).get("origin") or [""])[0] or "").strip()
        except Exception:
            origin = ""
        row = music.pick_player_row(music.player_rows(), name, origin)
        if row is None:
            handler._send_error_json(404, closed)  # type: ignore[attr-defined]
            return True
        remote_origin = str(row.get("origin") or "").strip()
        if remote_origin:
            loc_url = music.music_store.remote_file_url(
                remote_origin, str(row.get("name") or name), session.nick
            )
            if not loc_url:
                handler._send_error_json(502, closed)  # type: ignore[attr-defined]
                return True
            handler.send_response(302)
            handler.send_header("Location", loc_url)
            handler.send_header("Cache-Control", "no-store")
            handler.send_header("Access-Control-Allow-Origin", "*")
            handler.end_headers()
            return True
        path = music.resolve_song_file(music.default_music_dir(), str(row.get("name") or name))
        if path is None:
            handler._send_error_json(404, closed)  # type: ignore[attr-defined]
            return True
        try:
            _serve_audio(handler, path, path.name)
        except Exception as e:
            print(f"[MusicHTTP] serve error: {e}")
        return True

    handler._send_error_json(404, closed)  # type: ignore[attr-defined]
    return True
