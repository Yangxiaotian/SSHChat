"""HTML page and request helpers for the shared canvas (served by FileHTTP)."""

from __future__ import annotations

import html
import json
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

import canvas_sharing

if TYPE_CHECKING:
    from http.server import BaseHTTPRequestHandler


CANVAS_TEXTS = {
    "en": {
        "title": "SSHChat Shared Canvas",
        "gate_title": "Enter access key",
        "gate_sub": "Open the link from chat, then type the 6-character key shown separately",
        "key_label": "Access key *",
        "key_placeholder": "Enter 6-character key",
        "unlock": "Unlock canvas",
        "verifying": "Verifying...",
        "alert_key": "Please enter the 6-character key",
        "retry": "Retry",
        "you": "You",
        "room": "Room",
        "expires": "Expires",
        "color": "Color",
        "width": "Width",
        "clear": "Clear board",
        "clear_confirm": "Clear the shared board for everyone?",
        "hint": "Draw with mouse or finger. Changes sync to other participants.",
        "status_ready": "Connected",
        "status_sync": "Syncing…",
        "status_err": "Sync error — will retry",
        "closed": "This canvas is closed or expired",
    },
    "zh": {
        "title": "SSHChat 共享画布",
        "gate_title": "输入访问密钥",
        "gate_sub": "打开聊天里发来的网址，再输入单独给出的 6 位密钥",
        "key_label": "访问密钥 *",
        "key_placeholder": "输入6位密钥",
        "unlock": "进入画布",
        "verifying": "验证中...",
        "alert_key": "请输入6位密钥",
        "retry": "重试",
        "you": "你",
        "room": "房间",
        "expires": "过期",
        "color": "颜色",
        "width": "粗细",
        "clear": "清空画布",
        "clear_confirm": "确定清空共享画布？（所有人都会清空）",
        "hint": "用鼠标或手指绘画，笔画会同步给其他参与者。",
        "status_ready": "已连接",
        "status_sync": "同步中…",
        "status_err": "同步出错，将自动重试",
        "closed": "画布已关闭或过期",
    },
}


def generate_canvas_page(token: str, lang: str = "en") -> str:
    lang = "zh" if str(lang or "").lower().startswith("zh") else "en"
    S = CANVAS_TEXTS[lang]
    html_lang = "zh-CN" if lang == "zh" else "en"
    i18n = {
        "alertKey": S["alert_key"],
        "verifying": S["verifying"],
        "retry": S["retry"],
        "unlock": S["unlock"],
        "clearConfirm": S["clear_confirm"],
        "statusReady": S["status_ready"],
        "statusSync": S["status_sync"],
        "statusErr": S["status_err"],
        "you": S["you"],
        "room": S["room"],
        "expires": S["expires"],
        "closed": S["closed"],
    }
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
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: "IBM Plex Sans", "Segoe UI", "PingFang SC", "Noto Sans SC", sans-serif;
            background:
                radial-gradient(circle at 12% 18%, rgba(196,92,38,0.14), transparent 42%),
                radial-gradient(circle at 88% 8%, rgba(47,111,106,0.16), transparent 40%),
                linear-gradient(160deg, #ebe4d6 0%, #dfe8e4 55%, #efe8dc 100%);
            min-height: 100vh;
            color: var(--ink);
        }}
        .wrap {{
            max-width: 1100px;
            margin: 0 auto;
            padding: 20px 16px 40px;
        }}
        .card {{
            background: rgba(247, 243, 234, 0.94);
            border: 1px solid var(--line);
            border-radius: 18px;
            box-shadow: 0 18px 50px rgba(40, 30, 20, 0.12);
            overflow: hidden;
        }}
        .header {{
            padding: 22px 24px 16px;
            border-bottom: 1px solid var(--line);
            background: linear-gradient(120deg, rgba(196,92,38,0.08), rgba(47,111,106,0.08));
        }}
        h1 {{
            font-family: "Fraunces", "Songti SC", Georgia, serif;
            font-size: 28px;
            font-weight: 600;
            letter-spacing: 0.01em;
        }}
        .sub {{ opacity: 0.75; margin-top: 6px; font-size: 14px; }}
        .gate, .board {{ padding: 22px 24px 28px; }}
        .board {{ display: none; }}
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
        button.secondary {{
            background: transparent;
            color: var(--ink);
            border: 1px solid var(--line);
        }}
        button:disabled {{ opacity: 0.5; cursor: not-allowed; }}
        .toolbar {{
            display: flex;
            flex-wrap: wrap;
            gap: 12px;
            align-items: center;
            margin-bottom: 14px;
        }}
        .toolbar label {{ margin: 0; display: flex; align-items: center; gap: 8px; }}
        .meta {{
            font-size: 13px;
            opacity: 0.7;
            margin-bottom: 10px;
            display: flex;
            flex-wrap: wrap;
            gap: 12px;
        }}
        .stage {{
            position: relative;
            width: 100%;
            background: #fffdf8;
            border: 1px solid var(--line);
            border-radius: 14px;
            overflow: hidden;
            touch-action: none;
        }}
        canvas {{
            display: block;
            width: 100%;
            height: auto;
            cursor: crosshair;
            background:
                linear-gradient(90deg, rgba(0,0,0,0.03) 1px, transparent 1px),
                linear-gradient(rgba(0,0,0,0.03) 1px, transparent 1px);
            background-size: 24px 24px;
        }}
        .hint {{ margin-top: 12px; font-size: 13px; opacity: 0.65; }}
        .status {{
            margin-left: auto;
            font-size: 12px;
            padding: 4px 10px;
            border-radius: 999px;
            background: rgba(47,111,106,0.12);
            color: var(--accent-2);
        }}
        .status.err {{ background: rgba(196,92,38,0.15); color: var(--accent); }}
    </style>
</head>
<body>
    <div class="wrap">
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
            <div class="board" id="board">
                <div class="meta" id="meta"></div>
                <div class="toolbar">
                    <label>{html.escape(S['color'])}
                        <input id="color" type="color" value="#1a1f2e" />
                    </label>
                    <label>{html.escape(S['width'])}
                        <input id="width" type="range" min="1" max="24" value="4" />
                    </label>
                    <button class="secondary" id="clearBtn" type="button">{html.escape(S['clear'])}</button>
                    <span class="status" id="status">{html.escape(S['status_ready'])}</span>
                </div>
                <div class="stage">
                    <canvas id="cv" width="{canvas_sharing.LOGICAL_WIDTH}" height="{canvas_sharing.LOGICAL_HEIGHT}"></canvas>
                </div>
                <p class="hint">{html.escape(S['hint'])}</p>
            </div>
        </div>
    </div>
    <script>
    (function () {{
        const token = {json.dumps(token)};
        const i18n = {json.dumps(i18n, ensure_ascii=False)};
        const keyInput = document.getElementById('key');
        const unlockBtn = document.getElementById('unlockBtn');
        const gate = document.getElementById('gate');
        const board = document.getElementById('board');
        const canvas = document.getElementById('cv');
        const ctx = canvas.getContext('2d');
        const colorEl = document.getElementById('color');
        const widthEl = document.getElementById('width');
        const clearBtn = document.getElementById('clearBtn');
        const statusEl = document.getElementById('status');
        const metaEl = document.getElementById('meta');
        const subtitle = document.getElementById('subtitle');

        let ticket = '';
        let since = 0;
        let drawing = false;
        let current = null;
        let pollTimer = null;
        let syncing = false;
        let pollCount = 0;
        /** Strokes since last clear — used to rebuild if the bitmap is wiped. */
        let history = [];

        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';

        keyInput.addEventListener('input', function () {{
            this.value = this.value.toUpperCase().replace(/[^A-Z0-9]/g, '');
        }});

        function setStatus(text, err) {{
            statusEl.textContent = text;
            statusEl.classList.toggle('err', !!err);
        }}

        function logicalPos(evt) {{
            const rect = canvas.getBoundingClientRect();
            const src = (evt.touches && evt.touches[0]) ? evt.touches[0] : evt;
            const x = (src.clientX - rect.left) * (canvas.width / rect.width);
            const y = (src.clientY - rect.top) * (canvas.height / rect.height);
            return [
                Math.max(0, Math.min(canvas.width, x)),
                Math.max(0, Math.min(canvas.height, y))
            ];
        }}

        function drawStroke(stroke) {{
            const pts = stroke.points || [];
            if (pts.length < 1) return;
            ctx.strokeStyle = stroke.color || '#222';
            ctx.lineWidth = stroke.width || 3;
            ctx.beginPath();
            ctx.moveTo(pts[0][0], pts[0][1]);
            for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
            if (pts.length === 1) {{
                ctx.lineTo(pts[0][0] + 0.01, pts[0][1]);
            }}
            ctx.stroke();
        }}

        function clearLocal() {{
            ctx.clearRect(0, 0, canvas.width, canvas.height);
        }}

        function rememberStroke(ev) {{
            if (!ev || ev.kind !== 'stroke') return;
            const seq = Number(ev.seq || 0);
            if (seq && history.some(h => Number(h.seq || 0) === seq)) return;
            history.push(ev);
        }}

        function replayHistory() {{
            clearLocal();
            for (const ev of history) drawStroke(ev);
        }}

        /** Full paint: history + in-progress stroke (WebView often wipes the bitmap). */
        function paintAll() {{
            replayHistory();
            if (drawing && current && current.length) {{
                drawStroke({{
                    color: colorEl.value,
                    width: Number(widthEl.value),
                    points: current
                }});
            }}
        }}
        // Android WebView onResume can call this after the bitmap was discarded.
        window.paintAll = paintAll;

        function applyEvent(ev) {{
            if (!ev) return;
            if (ev.kind === 'clear') {{
                history = [];
                return;
            }}
            if (ev.kind === 'stroke') rememberStroke(ev);
        }}

        async function auth() {{
            const key = keyInput.value.trim();
            if (!key || key.length !== 6) {{
                alert(i18n.alertKey);
                return;
            }}
            unlockBtn.disabled = true;
            unlockBtn.textContent = i18n.verifying;
            try {{
                const res = await fetch('/canvas/' + token + '/auth', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ key: key }})
                }});
                const data = await res.json().catch(() => ({{}}));
                if (!res.ok) throw new Error(data.error || 'auth failed');
                ticket = data.ticket;
                gate.style.display = 'none';
                board.style.display = 'block';
                subtitle.textContent = data.title || '';
                renderMeta(data);
                await syncOnce(true);
                if (pollTimer) clearInterval(pollTimer);
                pollTimer = setInterval(() => syncOnce(false), 900);
            }} catch (e) {{
                alert(e.message || String(e));
                unlockBtn.disabled = false;
                unlockBtn.textContent = i18n.retry;
            }}
        }}

        function renderMeta(data) {{
            const bits = [];
            if (data.participant) bits.push(i18n.you + ': ' + data.participant);
            if (data.room) bits.push(i18n.room + ': #' + data.room);
            if (data.expires) {{
                const d = new Date(data.expires * 1000);
                bits.push(i18n.expires + ': ' + d.toLocaleString());
            }}
            metaEl.textContent = bits.join(' · ');
        }}

        async function syncOnce(initial) {{
            if (!ticket || syncing) return;
            syncing = true;
            try {{
                // Periodic / visibility full rebuild: canvas bitmaps can be wiped by
                // the browser while `since` stays high, leaving only new strokes.
                pollCount += 1;
                const rebuild = !!initial || (pollCount % 40 === 0);
                if (rebuild) since = 0;
                if (!initial && !rebuild) setStatus(i18n.statusSync, false);
                const res = await fetch(
                    '/canvas/' + token + '/sync?since=' + since
                    + '&ticket=' + encodeURIComponent(ticket),
                    {{
                        headers: {{ 'X-Canvas-Ticket': ticket }},
                        cache: 'no-store'
                    }}
                );
                const data = await res.json().catch(() => ({{}}));
                if (!res.ok) {{
                    const msg = data.error || ('HTTP ' + res.status);
                    if (res.status === 403) {{
                        ticket = '';
                        if (pollTimer) {{ clearInterval(pollTimer); pollTimer = null; }}
                        board.style.display = 'none';
                        gate.style.display = 'block';
                        unlockBtn.disabled = false;
                        unlockBtn.textContent = i18n.retry;
                        setStatus(i18n.statusErr, true);
                        throw new Error(msg);
                    }}
                    throw new Error(msg);
                }}
                renderMeta(data);
                const events = data.events || [];
                if (rebuild) history = [];
                for (const ev of events) {{
                    applyEvent(ev);
                    since = Math.max(since, Number(ev.seq || 0));
                }}
                // Android WebView often drops the canvas bitmap while `since`
                // stays ahead — redraw from history every poll.
                paintAll();
                setStatus(i18n.statusReady, false);
            }} catch (e) {{
                setStatus((e && e.message) ? (i18n.statusErr + ': ' + e.message) : i18n.statusErr, true);
            }} finally {{
                syncing = false;
            }}
        }}

        async function postStroke(points) {{
            if (!ticket || points.length < 1) return;
            try {{
                const res = await fetch('/canvas/' + token + '/stroke', {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/json',
                        'X-Canvas-Ticket': ticket
                    }},
                    body: JSON.stringify({{
                        color: colorEl.value,
                        width: Number(widthEl.value),
                        points: points
                    }}),
                    cache: 'no-store'
                }});
                const data = await res.json().catch(() => ({{}}));
                if (!res.ok) throw new Error(data.error || 'stroke failed');
                const ev = data.event || {{
                    kind: 'stroke',
                    color: colorEl.value,
                    width: Number(widthEl.value),
                    points: points,
                    seq: 0
                }};
                if (!ev.kind) ev.kind = 'stroke';
                rememberStroke(ev);
                if (ev.seq) since = Math.max(since, Number(ev.seq));
            }} catch (e) {{
                setStatus((e && e.message) ? (i18n.statusErr + ': ' + e.message) : i18n.statusErr, true);
            }}
        }}

        function startDraw(evt) {{
            // Ignore the synthetic mouse event that follows touch.
            if (evt.pointerType === 'mouse' && evt.sourceCapabilities
                && evt.sourceCapabilities.firesTouchEvents) return;
            if (evt.cancelable) evt.preventDefault();
            drawing = true;
            current = [logicalPos(evt)];
            drawStroke({{
                color: colorEl.value,
                width: Number(widthEl.value),
                points: current
            }});
        }}
        function moveDraw(evt) {{
            if (!drawing || !current) return;
            if (evt.cancelable) evt.preventDefault();
            const p = logicalPos(evt);
            const last = current[current.length - 1];
            if (Math.hypot(p[0] - last[0], p[1] - last[1]) < 1.5) return;
            current.push(p);
            drawStroke({{
                color: colorEl.value,
                width: Number(widthEl.value),
                points: current.slice(-2)
            }});
        }}
        async function endDraw(evt) {{
            if (!drawing) return;
            if (evt && evt.cancelable) evt.preventDefault();
            drawing = false;
            const pts = current || [];
            current = null;
            await postStroke(pts);
        }}

        canvas.addEventListener('mousedown', startDraw);
        canvas.addEventListener('mousemove', moveDraw);
        window.addEventListener('mouseup', endDraw);
        canvas.addEventListener('touchstart', startDraw, {{ passive: false }});
        canvas.addEventListener('touchmove', moveDraw, {{ passive: false }});
        canvas.addEventListener('touchend', endDraw);
        document.addEventListener('visibilitychange', () => {{
            if (document.visibilityState === 'visible' && ticket) {{
                paintAll();
                syncOnce(true);
            }}
        }});
        window.addEventListener('pageshow', () => {{
            if (ticket) paintAll();
        }});

        clearBtn.addEventListener('click', async () => {{
            if (!confirm(i18n.clearConfirm)) return;
            try {{
                const res = await fetch('/canvas/' + token + '/clear', {{
                    method: 'POST',
                    headers: {{ 'X-Canvas-Ticket': ticket }}
                }});
                const data = await res.json().catch(() => ({{}}));
                if (!res.ok) throw new Error(data.error || 'clear failed');
                clearLocal();
                history = [];
                if (data.event && data.event.seq) since = Math.max(since, Number(data.event.seq));
            }} catch (e) {{
                alert(e.message || String(e));
            }}
        }});

        unlockBtn.addEventListener('click', auth);
        keyInput.addEventListener('keydown', (e) => {{
            if (e.key === 'Enter') auth();
        }});
    }})();
    </script>
</body>
</html>"""


def handle_canvas_get(handler: "BaseHTTPRequestHandler") -> bool:
    """Return True if the request was a canvas GET and was handled."""
    parsed = urlparse(handler.path)
    parts = parsed.path.strip("/").split("/")
    if not parts or parts[0] != "canvas":
        return False

    store = canvas_sharing.canvas_store
    lang = "en"
    try:
        # reuse file server locale helper if present
        from file_http_server import _page_locale

        lang = _page_locale(handler)
    except Exception:
        pass

    if len(parts) == 2:
        token = parts[1]
        session = store.get_by_token(token)
        loc = "zh" if str(lang).lower().startswith("zh") else "en"
        closed_msg = CANVAS_TEXTS[loc]["closed"]
        if session is None:
            handler._send_html_error(404, closed_msg, lang=lang)  # type: ignore[attr-defined]
            return True
        ok, err = store._alive(session)
        if not ok:
            handler._send_html_error(403, err or closed_msg, lang=lang)  # type: ignore[attr-defined]
            return True
        handler._send_html_page(generate_canvas_page(token, lang=lang))  # type: ignore[attr-defined]
        return True

    if len(parts) == 3 and parts[2] == "sync":
        token = parts[1]
        ticket = (handler.headers.get("X-Canvas-Ticket") or "").strip()
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


def handle_canvas_post(handler: "BaseHTTPRequestHandler") -> bool:
    """Return True if the request was a canvas POST and was handled."""
    parsed = urlparse(handler.path)
    parts = parsed.path.strip("/").split("/")
    if not parts or parts[0] != "canvas" or len(parts) < 3:
        return False

    store = canvas_sharing.canvas_store
    token = parts[1]
    action = parts[2]
    ticket = (handler.headers.get("X-Canvas-Ticket") or "").strip()

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
                "width": canvas_sharing.LOGICAL_WIDTH,
                "height": canvas_sharing.LOGICAL_HEIGHT,
            },
        )
        return True

    if action == "stroke":
        body = handler._read_json_body(limit=512 * 1024)  # type: ignore[attr-defined]
        event, err = store.add_stroke(
            token,
            ticket,
            color=str(body.get("color") or "#222222"),
            width=body.get("width", 3),
            points=body.get("points"),
        )
        if event is None:
            handler._send_error_json(403, err)  # type: ignore[attr-defined]
            return True
        handler._send_json_response(200, {"event": event})  # type: ignore[attr-defined]
        return True

    if action == "clear":
        # clear has empty/optional body
        try:
            length = int(handler.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > 0:
            handler.rfile.read(min(length, 64 * 1024))
        event, err = store.clear_board(token, ticket)
        if event is None:
            handler._send_error_json(403, err)  # type: ignore[attr-defined]
            return True
        handler._send_json_response(200, {"event": event})  # type: ignore[attr-defined]
        return True

    handler._send_error_json(404, "网址无效")  # type: ignore[attr-defined]
    return True
