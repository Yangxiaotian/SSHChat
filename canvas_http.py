"""HTML page and request helpers for the shared canvas (served by FileHTTP).

UI: Excalidraw (CDN). Sync: Excalidraw elements JSON over the existing
URL+key → ticket gate (no Excalidraw room server).
"""

from __future__ import annotations

import html
import json
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

import canvas_sharing

if TYPE_CHECKING:
    from http.server import BaseHTTPRequestHandler


# Pin CDN build so self-hosted servers stay reproducible.
# Must use ?external=react,react-dom so importmap React is shared (else useEffect on null).
# ponytail: CDN fonts/JS; vendor under /canvas-assets/ if offline/China breaks.
EXCALIDRAW_VER = "0.18.0"
REACT_VER = "18.3.1"
EXCALIDRAW_CSS = (
    f"https://esm.sh/@excalidraw/excalidraw@{EXCALIDRAW_VER}/dist/prod/index.css"
)
EXCALIDRAW_ASSET = (
    f"https://esm.sh/@excalidraw/excalidraw@{EXCALIDRAW_VER}/dist/prod/"
)
EXCALIDRAW_PKG = (
    f"https://esm.sh/@excalidraw/excalidraw@{EXCALIDRAW_VER}"
    f"?external=react,react-dom"
)
REACT_PKG = f"https://esm.sh/react@{REACT_VER}"
REACT_DOM_PKG = f"https://esm.sh/react-dom@{REACT_VER}?deps=react@{REACT_VER}"


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
        "clear": "Clear board",
        "clear_confirm": "Clear the shared board for everyone?",
        "hint": "Powered by Excalidraw. Edits sync to other participants.",
        "status_ready": "Connected",
        "status_sync": "Syncing…",
        "status_err": "Sync error — will retry",
        "closed": "This canvas is closed or expired",
        "loading": "Loading whiteboard…",
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
        "clear": "清空画布",
        "clear_confirm": "确定清空共享画布？（所有人都会清空）",
        "hint": "基于 Excalidraw。图形/文字会同步给其他参与者。",
        "status_ready": "已连接",
        "status_sync": "同步中…",
        "status_err": "同步出错，将自动重试",
        "closed": "画布已关闭或过期",
        "loading": "正在加载画板…",
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
        "loading": S["loading"],
    }
    return f"""<!DOCTYPE html>
<html lang="{html_lang}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>{html.escape(S['title'])}</title>
    <link rel="stylesheet" href="{EXCALIDRAW_CSS}" />
    <script>window.EXCALIDRAW_ASSET_PATH = {json.dumps(EXCALIDRAW_ASSET)};</script>
    <script type="importmap">
    {{
      "imports": {{
        "react": "{REACT_PKG}",
        "react/jsx-runtime": "{REACT_PKG}/jsx-runtime",
        "react-dom": "{REACT_DOM_PKG}",
        "react-dom/client": "https://esm.sh/react-dom@{REACT_VER}/client?deps=react@{REACT_VER}"
      }}
    }}
    </script>
    <style>
        :root {{
            --ink: #1a1f2e;
            --paper: #f7f3ea;
            --line: #d9d0c0;
            --accent: #c45c26;
            --accent-2: #2f6f6a;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        html, body {{ height: 100%; }}
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
        .wrap.board-on {{
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
        .wrap.board-on .card {{
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
        .wrap.board-on .header {{ display: none; }}
        h1 {{
            font-family: "Fraunces", "Songti SC", Georgia, serif;
            font-size: 26px;
            font-weight: 600;
        }}
        .sub {{ opacity: 0.75; margin-top: 6px; font-size: 14px; }}
        .gate, .board {{ padding: 20px; }}
        .board {{ display: none; flex: 1; flex-direction: column; min-height: 0; padding-bottom: 12px; }}
        .wrap.board-on .board {{ padding: 8px 10px 10px; }}
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
            margin-bottom: 8px;
            flex-shrink: 0;
        }}
        .meta {{
            font-size: 13px;
            opacity: 0.7;
            margin-bottom: 8px;
            display: flex;
            flex-wrap: wrap;
            gap: 12px;
            flex-shrink: 0;
        }}
        .stage {{
            flex: 1;
            min-height: 420px;
            position: relative;
            background: #fff;
            border: 1px solid var(--line);
            border-radius: 12px;
            overflow: hidden;
        }}
        .wrap.board-on .stage {{
            border-radius: 8px;
            min-height: 0;
        }}
        #excalidraw-root {{ width: 100%; height: 100%; }}
        .hint {{ margin-top: 8px; font-size: 13px; opacity: 0.65; flex-shrink: 0; }}
        .status {{
            margin-left: auto;
            font-size: 12px;
            padding: 4px 10px;
            border-radius: 999px;
            background: rgba(47,111,106,0.12);
            color: var(--accent-2);
        }}
        .status.err {{ background: rgba(196,92,38,0.15); color: var(--accent); }}
        .loading {{
            position: absolute; inset: 0; display: flex; align-items: center;
            justify-content: center; background: rgba(255,253,248,0.9); z-index: 2;
            font-size: 14px; opacity: 0.8;
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
            // Fill key ASAP (before Excalidraw CDN module resolves). Tk/Electron
            // pass #k=XXXXXX; native WebViews may set window.__SSHCHAT_KEY.
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
                    // Rare fallback if a launcher dropped the fragment but kept ?k=
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
            <div class="board" id="board">
                <div class="meta" id="meta"></div>
                <div class="toolbar">
                    <button class="secondary" id="clearBtn" type="button">{html.escape(S['clear'])}</button>
                    <span class="status" id="status">{html.escape(S['status_ready'])}</span>
                </div>
                <div class="stage">
                    <div class="loading" id="loading">{html.escape(S['loading'])}</div>
                    <div id="excalidraw-root"></div>
                </div>
                <p class="hint">{html.escape(S['hint'])}</p>
            </div>
        </div>
    </div>
    <script type="module">
    import React from "react";
    import {{ createRoot }} from "react-dom/client";
    import * as ExcalidrawLib from "{EXCALIDRAW_PKG}";

    const {{ Excalidraw, CaptureUpdateAction }} = ExcalidrawLib;
    // 0.18+: NEVER; older builds fall back to commitToHistory:false
    const remoteUpdateOpts = CaptureUpdateAction
        ? {{ captureUpdate: CaptureUpdateAction.NEVER }}
        : {{ commitToHistory: false }};

    const token = {json.dumps(token)};
    const i18n = {json.dumps(i18n, ensure_ascii=False)};
    const keyInput = document.getElementById('key');
    const unlockBtn = document.getElementById('unlockBtn');
    const gate = document.getElementById('gate');
    const board = document.getElementById('board');
    const wrap = document.getElementById('wrap');
    const clearBtn = document.getElementById('clearBtn');
    const statusEl = document.getElementById('status');
    const metaEl = document.getElementById('meta');
    const loadingEl = document.getElementById('loading');

    let ticket = '';
    let rev = 0;
    let pollTimer = null;
    let syncing = false;
    let applyingRemote = false;
    let pushTimer = null;
    let pushInFlight = false;
    let localDirty = false;
    let api = null;
    let lastLocalSig = '';

    function setStatus(text, err) {{
        statusEl.textContent = text;
        statusEl.classList.toggle('err', !!err);
    }}

    function showLoadError(msg) {{
        if (loadingEl) {{
            loadingEl.style.display = 'flex';
            loadingEl.textContent = msg;
            loadingEl.style.color = '#a33';
        }}
        setStatus(msg, true);
    }}

    function fmtExpires(ts) {{
        try {{
            return new Date(ts * 1000).toLocaleString();
        }} catch (_) {{
            return '';
        }}
    }}

    function hashFragmentKey() {{
        // Prefer early classic-script fill; keep as fallback if module loads first.
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

    function sceneSig(elements, files) {{
        // Cheap change detector — enough to skip no-op pushes.
        const n = (elements || []).length;
        let v = 0;
        for (const el of elements || []) v += (el.version || 0);
        const fk = files ? Object.keys(files).length : 0;
        return n + ':' + v + ':' + fk;
    }}

    function elementRank(el) {{
        const version = Number(el && el.version) || 0;
        const nonce = Number(el && el.versionNonce) || 0;
        return version * 1e13 + nonce;
    }}

    function liveScene() {{
        if (!api) return {{ elements: [], files: {{}} }};
        // Must include deleted tombstones — eraser sets isDeleted; getSceneElements()
        // omits them, so a push would leave the old non-deleted copy on the server
        // and the next sync would resurrect erased strokes.
        const elements = api.getSceneElementsIncludingDeleted
            ? api.getSceneElementsIncludingDeleted()
            : (api.getSceneElements ? api.getSceneElements() : []);
        const files = api.getFiles ? api.getFiles() : {{}};
        return {{ elements: elements || [], files: files || {{}} }};
    }}

    function preferDeletedOnTie(a, b) {{
        // Same version/nonce: keep the deleted copy so an erase is not undone.
        if (a && a.isDeleted && !(b && b.isDeleted)) return a;
        if (b && b.isDeleted && !(a && a.isDeleted)) return b;
        return b || a;
    }}

    function mergeElements(base, incoming) {{
        // Same id/version rule as server: higher version (then nonce) wins.
        const byId = Object.create(null);
        for (const el of base || []) {{
            if (el && typeof el.id === 'string' && el.id) byId[el.id] = el;
        }}
        for (const el of incoming || []) {{
            if (!el || typeof el.id !== 'string' || !el.id) continue;
            const old = byId[el.id];
            if (!old) {{
                byId[el.id] = el;
                continue;
            }}
            const ra = elementRank(el);
            const rb = elementRank(old);
            if (ra > rb) byId[el.id] = el;
            else if (ra < rb) byId[el.id] = old;
            else byId[el.id] = preferDeletedOnTie(old, el);
        }}
        return Object.keys(byId).map((k) => byId[k]);
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
            const res = await fetch('/canvas/' + token + '/auth', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                cache: 'no-store',
                body: JSON.stringify({{ key }}),
            }});
            const data = await res.json().catch(() => ({{}}));
            if (!res.ok) throw new Error(data.error || 'auth failed');
            ticket = data.ticket;
            gate.style.display = 'none';
            board.style.display = 'flex';
            wrap.classList.add('board-on');
            metaEl.innerHTML =
                '<span>' + i18n.you + ': ' + (data.participant || '') + '</span>' +
                (data.room ? '<span>' + i18n.room + ': ' + data.room + '</span>' : '') +
                (data.expires ? '<span>' + i18n.expires + ': ' + fmtExpires(data.expires) + '</span>' : '');
            await mountExcalidraw();
            await syncOnce(true);
            pollTimer = setInterval(() => syncOnce(false), 1200);
        }} catch (e) {{
            alert((e && e.message) || i18n.statusErr);
            unlockBtn.disabled = false;
            unlockBtn.textContent = i18n.unlock;
        }}
    }}

    async function mountExcalidraw() {{
        if (!Excalidraw) {{
            showLoadError('Excalidraw load failed');
            throw new Error('Excalidraw missing');
        }}
        const el = document.getElementById('excalidraw-root');
        const root = createRoot(el);
        root.render(React.createElement(Excalidraw, {{
            langCode: {json.dumps("zh-CN" if lang == "zh" else "en")},
            UIOptions: {{ canvasActions: {{ loadScene: false, saveToActiveFile: false }} }},
            excalidrawAPI: (a) => {{ api = a; }},
            onChange: (elements, _appState, files) => {{
                if (applyingRemote || !ticket) return;
                const sig = sceneSig(elements, files);
                if (sig === lastLocalSig) return;
                lastLocalSig = sig;
                localDirty = true;
                schedulePush();
            }},
        }}));
        loadingEl.style.display = 'none';
    }}

    function schedulePush() {{
        if (pushTimer) clearTimeout(pushTimer);
        // Debounce uploads; always read the live scene when the timer fires so
        // mid-debounce strokes are not dropped from the POST body.
        pushTimer = setTimeout(() => {{ void pushScene(); }}, 450);
    }}

    async function pushScene() {{
        if (!ticket || applyingRemote || !api || pushInFlight) return;
        const live = liveScene();
        const elements = live.elements;
        const files = live.files;
        const sigAtStart = sceneSig(elements, files);
        pushInFlight = true;
        setStatus(i18n.statusSync, false);
        try {{
            const res = await fetch('/canvas/' + token + '/scene', {{
                method: 'POST',
                headers: {{
                    'Content-Type': 'application/json',
                    'X-Canvas-Ticket': ticket,
                }},
                cache: 'no-store',
                body: JSON.stringify({{
                    elements: elements || [],
                    files: files || {{}},
                }}),
            }});
            const data = await res.json().catch(() => ({{}}));
            if (!res.ok) throw new Error(data.error || 'scene failed');
            if (typeof data.rev === 'number') rev = data.rev;
            const liveNow = liveScene();
            if (sceneSig(liveNow.elements, liveNow.files) === sigAtStart) {{
                localDirty = false;
                lastLocalSig = sigAtStart;
            }} else {{
                // User kept drawing during the POST — schedule another push.
                localDirty = true;
                schedulePush();
            }}
            setStatus(i18n.statusReady, false);
        }} catch (_) {{
            setStatus(i18n.statusErr, true);
            localDirty = true;
            schedulePush();
        }} finally {{
            pushInFlight = false;
        }}
    }}

    async function syncOnce(initial) {{
        if (!ticket || syncing) return;
        syncing = true;
        if (!initial) setStatus(i18n.statusSync, false);
        try {{
            const res = await fetch(
                '/canvas/' + token + '/sync?since=' + rev + '&ticket=' + encodeURIComponent(ticket),
                {{
                    headers: {{ 'X-Canvas-Ticket': ticket }},
                    cache: 'no-store',
                }}
            );
            const data = await res.json().catch(() => ({{}}));
            if (!res.ok) throw new Error(data.error || 'sync failed');
            const remoteRev = Number(data.rev || 0);
            if (data.changed && remoteRev >= rev && api) {{
                const remoteEls = data.elements || [];
                const live = liveScene();
                let nextEls;
                // Empty remote + no local pending ⇒ peer clear / empty board.
                // Otherwise merge so an older poll cannot wipe unpushed strokes
                // (the "newest strokes vanish, then come back" race).
                if (
                    remoteEls.length === 0 &&
                    !localDirty &&
                    !pushInFlight
                ) {{
                    nextEls = [];
                }} else {{
                    nextEls = mergeElements(remoteEls, live.elements);
                }}
                const nextFiles = Object.assign({{}}, live.files || {{}}, data.files || {{}});
                const nextSig = sceneSig(nextEls, nextFiles);
                const curSig = sceneSig(live.elements, live.files);
                if (nextSig !== curSig || (data.files && Object.keys(data.files).length)) {{
                    applyingRemote = true;
                    try {{
                        // addFiles expects BinaryFileData[]; getFiles()/sync return a map.
                        // Files must be registered before image elements or peers see placeholders.
                        const remoteFileMap = data.files || {{}};
                        const fileList = Object.values(remoteFileMap).filter(
                            (f) => f && typeof f === 'object' && f.dataURL
                        );
                        if (fileList.length && api.addFiles) {{
                            try {{ api.addFiles(fileList); }} catch (_) {{}}
                        }}
                        api.updateScene({{
                            elements: nextEls,
                            ...remoteUpdateOpts,
                        }});
                        lastLocalSig = nextSig;
                    }} finally {{
                        applyingRemote = false;
                    }}
                }}
                rev = remoteRev;
            }} else if (remoteRev > rev) {{
                rev = remoteRev;
            }}
            setStatus(i18n.statusReady, false);
        }} catch (_) {{
            setStatus(i18n.statusErr, true);
        }} finally {{
            syncing = false;
        }}
    }}

    clearBtn.addEventListener('click', async () => {{
        if (!ticket) return;
        if (!confirm(i18n.clearConfirm)) return;
        try {{
            const res = await fetch('/canvas/' + token + '/clear', {{
                method: 'POST',
                headers: {{ 'X-Canvas-Ticket': ticket }},
                cache: 'no-store',
            }});
            const data = await res.json().catch(() => ({{}}));
            if (!res.ok) throw new Error(data.error || 'clear failed');
            rev = Number(data.rev || rev + 1);
            localDirty = false;
            if (pushTimer) {{ clearTimeout(pushTimer); pushTimer = null; }}
            if (api) {{
                applyingRemote = true;
                try {{
                    api.resetScene();
                    lastLocalSig = sceneSig([], {{}});
                }} finally {{
                    applyingRemote = false;
                }}
            }}
        }} catch (_) {{
            setStatus(i18n.statusErr, true);
        }}
    }});

    unlockBtn.addEventListener('click', auth);
    keyInput.addEventListener('keydown', (e) => {{
        if (e.key === 'Enter') auth();
    }});

    // Prefer key injected by native WebView (set before this module finishes).
    // Hash (#k=) remains for Electron/Tk. Do NOT unlock until listeners exist —
    // early btn.click() from clients is a no-op while esm.sh is still loading.
    const injected = (window.__SSHCHAT_KEY || '').toString().trim().toUpperCase();
    try {{ delete window.__SSHCHAT_KEY; }} catch (_) {{}}
    const autofill = (injected.length === 6 ? injected : '') || hashFragmentKey();
    if (autofill) {{
        keyInput.value = autofill;
        setTimeout(() => {{ void auth(); }}, 50);
    }}

    // Mobile WebView may call paintAll() on resume — no-op for Excalidraw.
    window.paintAll = function () {{}};
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
                "rev": session.rev,
            },
        )
        return True

    if action == "scene":
        body = handler._read_json_body(limit=canvas_sharing.MAX_SCENE_BYTES + 65536)  # type: ignore[attr-defined]
        result, err = store.apply_scene(
            token,
            ticket,
            elements=body.get("elements"),
            files=body.get("files"),
        )
        if result is None:
            handler._send_error_json(403, err)  # type: ignore[attr-defined]
            return True
        handler._send_json_response(200, result)  # type: ignore[attr-defined]
        return True

    if action == "stroke":
        handler._send_error_json(410, "请使用网页画板（Excalidraw）")  # type: ignore[attr-defined]
        return True

    if action == "clear":
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
        handler._send_json_response(200, event)  # type: ignore[attr-defined]
        return True

    handler._send_error_json(404, "网址无效")  # type: ignore[attr-defined]
    return True
