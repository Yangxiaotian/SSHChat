"""HTML page and request helpers for the shared canvas (served by FileHTTP).

UI: Excalidraw (CDN). Sync: Excalidraw element patches over WebSocket
(URL+key → ticket), with HTTP poll fallback. Clients push dirty elements
only; peers merge by id/version (same rule as the server).
"""

from __future__ import annotations

import html
import json
import secrets
import threading
from typing import TYPE_CHECKING, Optional
from urllib.parse import parse_qs, urlparse

import canvas_sharing
import canvas_ws
import piano_ws

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
        "clear": "Clear",
        "clear_confirm": "Clear the shared board for everyone?",
        "close": "Close",
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
        "clear": "清空",
        "clear_confirm": "确定清空共享画布？（所有人都会清空）",
        "close": "关闭",
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
            gap: 8px;
            align-items: center;
            margin-bottom: 8px;
            flex-shrink: 0;
        }}
        .tb-btn {{
            background: rgba(47,111,106,0.14);
            color: var(--accent-2);
            border: 0;
            border-radius: 999px;
            padding: 6px 12px;
            font-size: 12px;
            cursor: pointer;
        }}
        .tb-btn:disabled {{ opacity: 0.4; cursor: not-allowed; }}
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
            flex-shrink: 0;
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
            // Fill key ASAP (before Excalidraw CDN module resolves). Tk trampoline
            // sets window.name=sshchat-k:XXXXXX (survives --app= hash drops);
            // Electron/hash still use #k=; native WebViews may set __SSHCHAT_KEY.
            (function () {{
                function takeKey() {{
                    try {{
                        var wn = (window.name || '').toString();
                        var wm = wn.match(/^sshchat-k:([A-Za-z0-9]{{6}})$/i);
                        if (wm) {{
                            try {{ window.name = ''; }} catch (_) {{}}
                            return wm[1].toUpperCase();
                        }}
                    }} catch (_) {{}}
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
                    <button class="tb-btn" id="clearBtn" type="button">{html.escape(S['clear'])}</button>
                    <button class="tb-btn" id="closeBtn" type="button" hidden>{html.escape(S['close'])}</button>
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
    let sceneGen = 0;
    let syncing = false;
    let applyingRemote = false;
    let pushTimer = null;
    let pushInFlight = false;
    let clearInFlight = false;
    let suppressPushUntil = 0;
    let localDirty = false;
    let pendingPushSig = '';
    let pendingPushEls = null;
    let pendingPushFiles = null;
    let pushAckTimer = null;
    let api = null;
    let lastLocalSig = '';
    // Server watermark: rank/file ids we believe the hub already has.
    let syncedRank = Object.create(null);
    let syncedSig = Object.create(null);
    let syncedFiles = Object.create(null);
    // Ids we already told the server are deleted (or saw deleted from peers).
    let syncedDeleted = Object.create(null);
    let canvasWs = null;
    let canvasWsLive = false;
    let canvasWsForceHttp = false;
    let canvasWsRetryTimer = null;
    let httpSyncActive = false;
    // True while the primary pointer is down on the board — ease sync load so
    // clone/stringify does not steal frames from Excalidraw input.
    let drawingActive = false;
    let pendingRemote = null;
    let localQuietUntil = 0;
    let quietFlushTimer = null;
    // Piano uses ~16ms note flush; canvas payloads are larger — keep WS snappy
    // but avoid main-thread clone storms (tool clicks / clear felt frozen).
    const PUSH_MS_WS = 48;
    const PUSH_MS_DRAWING = 160;
    const PUSH_MS_HTTP = 280;

    function whenIdle(fn, timeoutMs) {{
        // Run after paint / when the browser has spare time — keeps the pen smooth.
        const timeout = typeof timeoutMs === 'number' ? timeoutMs : 80;
        if (typeof requestIdleCallback === 'function') {{
            requestIdleCallback(() => {{ try {{ fn(); }} catch (_) {{}} }}, {{ timeout: timeout }});
            return;
        }}
        if (typeof requestAnimationFrame === 'function') {{
            requestAnimationFrame(() => {{
                setTimeout(() => {{ try {{ fn(); }} catch (_) {{}} }}, 0);
            }});
            return;
        }}
        setTimeout(() => {{ try {{ fn(); }} catch (_) {{}} }}, 0);
    }}

    function bumpLocalQuiet(ms) {{
        // Block remote updateScene while Excalidraw owns the pen — that was
        // causing "写不出来" / warped glyphs mid-phrase.
        const hold = typeof ms === 'number' ? ms : 500;
        localQuietUntil = Math.max(localQuietUntil, Date.now() + hold);
        if (quietFlushTimer) clearTimeout(quietFlushTimer);
        quietFlushTimer = setTimeout(() => {{
            quietFlushTimer = null;
            if (drawingActive || Date.now() < localQuietUntil) return;
            whenIdle(() => {{
                if (drawingActive || Date.now() < localQuietUntil) return;
                flushPendingRemote();
                if (localDirty && !pushInFlight) schedulePush(0);
            }}, 32);
        }}, hold + 16);
    }}

    function isPenHot() {{
        return drawingActive || Date.now() < localQuietUntil;
    }}

    function adoptSceneGen(value) {{
        const g = Number(value);
        if (Number.isFinite(g) && g >= 0) sceneGen = g;
    }}

    function abortPendingPush() {{
        if (pushTimer) {{ clearTimeout(pushTimer); pushTimer = null; }}
        if (pushAckTimer) {{ clearTimeout(pushAckTimer); pushAckTimer = null; }}
        pushInFlight = false;
        pendingPushSig = '';
        pendingPushEls = null;
        pendingPushFiles = null;
        localDirty = false;
    }}

    function resetSyncWatermark() {{
        syncedRank = Object.create(null);
        syncedSig = Object.create(null);
        syncedFiles = Object.create(null);
        syncedDeleted = Object.create(null);
    }}

    function markBoardClearedLocally() {{
        abortPendingPush();
        resetSyncWatermark();
        pendingRemote = null;
        drawingActive = false;
        localQuietUntil = 0;
        if (quietFlushTimer) {{ clearTimeout(quietFlushTimer); quietFlushTimer = null; }}
        lastLocalSig = sceneSig([], {{}});
        localDirty = false;
        // Excalidraw may emit a late onChange with pre-clear elements; ignore
        // and re-reset briefly so we do not push them under the new scene_gen.
        suppressPushUntil = Date.now() + 1000;
    }}

    function setStatus(text, err) {{
        // Avoid layout thrash mid-stroke (status flips were visible as pen stalls).
        if (drawingActive && !err) return;
        statusEl.textContent = text;
        statusEl.classList.toggle('err', !!err);
    }}

    function flushPendingRemote() {{
        if (!pendingRemote) return;
        const data = pendingRemote;
        pendingRemote = null;
        applyRemotePayload(data);
    }}

    function endStrokeGesture() {{
        const wasDrawing = drawingActive;
        drawingActive = false;
        if (!wasDrawing) {{
            if (localDirty) schedulePush(0);
            return;
        }}
        // Keep remote updateScene away until Excalidraw finishes the stroke.
        bumpLocalQuiet(550);
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
        try {{
            var wn = (window.name || '').toString();
            var wm = wn.match(/^sshchat-k:([A-Za-z0-9]{{6}})$/i);
            if (wm) {{
                try {{ window.name = ''; }} catch (_) {{}}
                return wm[1].toUpperCase();
            }}
        }} catch (_) {{}}
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
        // Include points/updated — freehand often grows points without bumping version.
        const n = (elements || []).length;
        let v = 0;
        let pts = 0;
        let upd = 0;
        for (const el of elements || []) {{
            v += (el.version || 0);
            pts += Array.isArray(el.points) ? el.points.length : 0;
            upd += Number(el.updated) || 0;
        }}
        const fk = files ? Object.keys(files).length : 0;
        return n + ':' + v + ':' + pts + ':' + upd + ':' + fk;
    }}

    function elementRank(el) {{
        const version = Number(el && el.version) || 0;
        const nonce = Number(el && el.versionNonce) || 0;
        return version * 1e13 + nonce;
    }}

    function elementPointsLen(el) {{
        return Array.isArray(el && el.points) ? el.points.length : 0;
    }}

    function elementSyncSig(el) {{
        if (!el) return '';
        return (
            elementRank(el) + ':' +
            (Number(el.updated) || 0) + ':' +
            elementPointsLen(el) + ':' +
            (el.isDeleted ? 1 : 0) + ':' +
            (Number(el.width) || 0) + ':' +
            (Number(el.height) || 0)
        );
    }}

    function elementNewerThan(a, b) {{
        // Same version/nonce: newer updated wins. Never prefer longer point
        // lists — final freehand is often simplified (fewer points); keeping
        // the longer mid-stroke copy makes glyphs look warped.
        return (Number(a && a.updated) || 0) > (Number(b && b.updated) || 0);
    }}

    function cloneJson(value) {{
        // Prefer structuredClone; fall back to JSON for older WebViews.
        try {{
            if (typeof structuredClone === 'function') return structuredClone(value);
        }} catch (_) {{}}
        try {{
            return JSON.parse(JSON.stringify(value));
        }} catch (_) {{
            return value;
        }}
    }}

    function freezeElements(els) {{
        // Freehand mutates points in place — copy elements cheaply without
        // JSON-serializing the whole scene on every push (that lagged tool taps).
        const out = [];
        for (const el of els || []) {{
            if (!el || typeof el !== 'object') continue;
            const copy = Object.assign({{}}, el);
            if (Array.isArray(el.points)) {{
                copy.points = el.points.map((p) => (Array.isArray(p) ? p.slice() : p));
            }}
            if (Array.isArray(el.pressures)) copy.pressures = el.pressures.slice();
            if (Array.isArray(el.simulatePressure)) {{
                copy.simulatePressure = el.simulatePressure.slice();
            }}
            if (Array.isArray(el.groupIds)) copy.groupIds = el.groupIds.slice();
            if (Array.isArray(el.boundElements)) {{
                copy.boundElements = el.boundElements.slice();
            }}
            out.push(copy);
        }}
        return out;
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

    function rankVersion(rank) {{
        return Math.max(0, Math.floor(Number(rank) / 1e13) || 0);
    }}

    function makeTombstone(id, syncedRankVal) {{
        // Undo may drop an element with no isDeleted marker. Emit a synthetic
        // tombstone so merge-based sync cannot resurrect the server copy.
        const version = rankVersion(syncedRankVal) + 1;
        return {{
            id: id,
            type: 'rectangle',
            x: 0,
            y: 0,
            width: 0,
            height: 0,
            angle: 0,
            strokeColor: 'transparent',
            backgroundColor: 'transparent',
            fillStyle: 'solid',
            strokeWidth: 1,
            strokeStyle: 'solid',
            roughness: 0,
            opacity: 0,
            groupIds: [],
            frameId: null,
            roundness: null,
            seed: 1,
            version: version,
            versionNonce: (Date.now() % 1000000000) + 1,
            isDeleted: true,
            boundElements: null,
            updated: Date.now(),
            link: null,
            locked: false,
        }};
    }}

    function markLocalPushed(els, files) {{
        for (const el of els || []) {{
            if (el && typeof el.id === 'string' && el.id) {{
                const r = elementRank(el);
                const prev = syncedRank[el.id];
                if (prev == null || r >= prev) syncedRank[el.id] = r;
                syncedSig[el.id] = elementSyncSig(el);
                if (el.isDeleted) syncedDeleted[el.id] = true;
                else delete syncedDeleted[el.id];
            }}
        }}
        for (const fid of Object.keys(files || {{}})) {{
            syncedFiles[fid] = true;
        }}
    }}

    function noteRemoteSynced(remoteEls, remoteFiles) {{
        // Advance watermark only up to what the server sent — keep local-newer
        // strokes dirty so we still push them after a peer patch merges in.
        for (const el of remoteEls || []) {{
            if (!el || typeof el.id !== 'string' || !el.id) continue;
            const r = elementRank(el);
            const prev = syncedRank[el.id];
            if (prev == null || r > prev) syncedRank[el.id] = r;
            // Always refresh content sig for accepted remotes (points may grow
            // at the same version).
            syncedSig[el.id] = elementSyncSig(el);
            if (el.isDeleted) syncedDeleted[el.id] = true;
            else delete syncedDeleted[el.id];
        }}
        for (const fid of Object.keys(remoteFiles || {{}})) {{
            syncedFiles[fid] = true;
        }}
    }}

    function shouldAcceptRemoteEl(el, liveById) {{
        if (!el || typeof el.id !== 'string' || !el.id) return false;
        const live = liveById[el.id];
        const remoteRank = elementRank(el);
        if (live) {{
            const liveRank = elementRank(live);
            if (remoteRank > liveRank) return true;
            if (remoteRank < liveRank) return false;
            // Tie: tombstone wins; else take newer updated (not more points).
            if (el.isDeleted && !live.isDeleted) return true;
            if (!el.isDeleted && live.isDeleted) return false;
            return elementNewerThan(el, live);
        }}
        // Missing locally.
        if (syncedDeleted[el.id]) {{
            // We already believe it is deleted — ignore stale live echoes.
            if (el.isDeleted) return false;
            return remoteRank > (syncedRank[el.id] || 0);
        }}
        if (syncedRank[el.id] != null) {{
            // Vanished via undo (awaiting/sending tombstone): suppress stale copies.
            return remoteRank > syncedRank[el.id];
        }}
        return true;
    }}

    function buildScenePatch(elements, files) {{
        const dirtyEls = [];
        const liveIds = Object.create(null);
        for (const el of elements || []) {{
            if (!el || typeof el.id !== 'string' || !el.id) continue;
            liveIds[el.id] = true;
            // Compare content sig — not rank alone — so mid-stroke points sync.
            if (syncedSig[el.id] !== elementSyncSig(el)) dirtyEls.push(el);
        }}
        // Synced ids missing from the live scene (undo) need an explicit tombstone.
        for (const id of Object.keys(syncedRank)) {{
            if (liveIds[id]) continue;
            if (syncedDeleted[id]) continue;
            dirtyEls.push(makeTombstone(id, syncedRank[id]));
        }}
        const dirtyFiles = {{}};
        const fileMap = files || {{}};
        for (const el of dirtyEls) {{
            const fid = el.fileId;
            if (fid && fileMap[fid] && !syncedFiles[fid]) {{
                dirtyFiles[fid] = fileMap[fid];
            }}
        }}
        for (const fid of Object.keys(fileMap)) {{
            if (!syncedFiles[fid] && fileMap[fid]) dirtyFiles[fid] = fileMap[fid];
        }}
        return {{
            elements: dirtyEls,
            files: dirtyFiles,
            empty: dirtyEls.length === 0 && Object.keys(dirtyFiles).length === 0,
        }};
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
            else if (el.isDeleted !== old.isDeleted) {{
                byId[el.id] = preferDeletedOnTie(old, el);
            }} else if (elementNewerThan(el, old)) {{
                byId[el.id] = el;
            }} else {{
                byId[el.id] = old;
            }}
        }}
        return Object.keys(byId).map((k) => byId[k]);
    }}

    async function auth(explicitKey) {{
        const key = (explicitKey || keyInput.value || '').trim().toUpperCase();
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
            connectCanvasWs();
            ensureHttpSyncLoop();
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
                if (Date.now() < suppressPushUntil) {{
                    const liveCount = (elements || []).filter((el) => el && !el.isDeleted).length;
                    if (liveCount > 0 && api) {{
                        applyingRemote = true;
                        try {{
                            if (api.resetScene) api.resetScene();
                            else api.updateScene({{ elements: [], ...remoteUpdateOpts }});
                            lastLocalSig = sceneSig([], {{}});
                        }} finally {{
                            applyingRemote = false;
                        }}
                    }} else {{
                        lastLocalSig = sceneSig(elements, files);
                    }}
                    localDirty = false;
                    return;
                }}
                const sig = sceneSig(elements, files);
                if (sig === lastLocalSig) return;
                lastLocalSig = sig;
                localDirty = true;
                // While the pen is hot, never schedule freeze/stringify.
                if (!isPenHot()) schedulePush();
            }},
        }}));
        loadingEl.style.display = 'none';
        // Track pen-down on the draw surface so sync yields to stroke input.
        if (!el.__sshchatStrokeFlush) {{
            el.__sshchatStrokeFlush = true;
            el.addEventListener('pointerdown', (ev) => {{
                if (ev.isPrimary === false) return;
                if (ev.button != null && ev.button !== 0) return;
                const t = ev.target;
                // Toolbar/UI lives in the same root — only treat canvas as drawing.
                if (!t || (t.tagName !== 'CANVAS' && !(t.closest && t.closest('canvas')))) {{
                    return;
                }}
                drawingActive = true;
                bumpLocalQuiet(700);
                // Cancel a pending mid-flight timer so we do not stringify mid-glyph.
                if (pushTimer) {{ clearTimeout(pushTimer); pushTimer = null; }}
            }}, true);
            const flushTail = () => {{ endStrokeGesture(); }};
            el.addEventListener('pointerup', flushTail, true);
            el.addEventListener('pointercancel', flushTail, true);
            el.addEventListener('lostpointercapture', flushTail, true);
            el.addEventListener('touchend', flushTail, true);
        }}
    }}

    function schedulePush(delayMs) {{
        // While Excalidraw owns the pen (or quiet window), only mark dirty —
        // bumpLocalQuiet will flush afterward.
        if (isPenHot()) {{
            localDirty = true;
            return;
        }}
        if (pushTimer) clearTimeout(pushTimer);
        let ms;
        if (typeof delayMs === 'number') {{
            ms = delayMs;
        }} else if (!canvasWsLive) {{
            ms = PUSH_MS_HTTP;
        }} else {{
            ms = PUSH_MS_WS;
        }}
        pushTimer = setTimeout(() => {{ void pushScene(); }}, ms);
    }}

    function finishLocalPush() {{
        if (pushAckTimer) {{ clearTimeout(pushAckTimer); pushAckTimer = null; }}
        whenIdle(() => {{
            if (isPenHot()) {{
                localDirty = true;
                return;
            }}
            const liveNow = liveScene();
            const leftover = buildScenePatch(liveNow.elements, liveNow.files);
            if (!leftover.empty) {{
                localDirty = true;
                schedulePush(canvasWsLive ? 0 : undefined);
            }} else {{
                localDirty = false;
                lastLocalSig = sceneSig(liveNow.elements, liveNow.files);
            }}
            setStatus(i18n.statusReady, false);
        }}, 64);
    }}

    async function pushScene() {{
        if (!ticket || applyingRemote || !api || clearInFlight) return;
        if (Date.now() < suppressPushUntil) return;
        // Never freeze/stringify on the hot pen path.
        if (isPenHot()) {{
            localDirty = true;
            return;
        }}
        if (pushInFlight) {{
            localDirty = true;
            return;
        }}
        // Yield to input/paint before doing any heavy snapshot work.
        await new Promise((resolve) => whenIdle(resolve, 64));
        if (isPenHot() || pushInFlight || !ticket || clearInFlight) {{
            if (isPenHot()) localDirty = true;
            return;
        }}
        const live = liveScene();
        const elements = live.elements;
        const files = live.files;
        const patch = buildScenePatch(elements, files);
        if (patch.empty) {{
            localDirty = false;
            lastLocalSig = sceneSig(elements, files);
            return;
        }}
        const snapEls = freezeElements(patch.elements);
        const snapFiles = Object.keys(patch.files || {{}}).length
            ? cloneJson(patch.files)
            : {{}};
        const genAtStart = sceneGen;
        pushInFlight = true;
        pendingPushSig = sceneSig(snapEls, snapFiles);
        pendingPushEls = snapEls;
        pendingPushFiles = snapFiles;
        setStatus(i18n.statusSync, false);
        if (canvasWsLive && canvasWs && canvasWs.readyState === 1) {{
            try {{
                // Stringify after another idle slice so Excalidraw owns the frame.
                await new Promise((resolve) => whenIdle(resolve, 64));
                if (isPenHot()) {{
                    pushInFlight = false;
                    pendingPushSig = '';
                    pendingPushEls = null;
                    pendingPushFiles = null;
                    localDirty = true;
                    return;
                }}
                if (!canvasWs || canvasWs.readyState !== 1) {{
                    canvasWsLive = false;
                    pushInFlight = false;
                    localDirty = true;
                    schedulePush();
                    return;
                }}
                const body = JSON.stringify({{
                    type: 'scene',
                    elements: snapEls,
                    files: snapFiles,
                    scene_gen: genAtStart,
                }});
                canvasWs.send(body);
                if (pushAckTimer) clearTimeout(pushAckTimer);
                pushAckTimer = setTimeout(() => {{
                    pushAckTimer = null;
                    if (!pushInFlight) return;
                    pushInFlight = false;
                    pendingPushSig = '';
                    pendingPushEls = null;
                    pendingPushFiles = null;
                    canvasWsLive = false;
                    localDirty = true;
                    schedulePush();
                }}, 12000);
                return;
            }} catch (_) {{
                canvasWsLive = false;
            }}
        }}
        try {{
            await new Promise((resolve) => whenIdle(resolve, 64));
            if (isPenHot()) {{
                pushInFlight = false;
                pendingPushSig = '';
                pendingPushEls = null;
                pendingPushFiles = null;
                localDirty = true;
                return;
            }}
            const res = await fetch('/canvas/' + token + '/scene', {{
                method: 'POST',
                headers: {{
                    'Content-Type': 'application/json',
                    'X-Canvas-Ticket': ticket,
                }},
                cache: 'no-store',
                body: JSON.stringify({{
                    elements: snapEls,
                    files: snapFiles,
                    scene_gen: genAtStart,
                }}),
            }});
            const data = await res.json().catch(() => ({{}}));
            if (!res.ok) throw new Error(data.error || 'scene failed');
            if (typeof data.rev === 'number') rev = data.rev;
            adoptSceneGen(data.scene_gen);
            markLocalPushed(snapEls, snapFiles);
            finishLocalPush();
        }} catch (_) {{
            setStatus(i18n.statusErr, true);
            localDirty = true;
            void syncOnce(false).then(() => {{
                if (localDirty) schedulePush();
            }});
        }} finally {{
            pushInFlight = false;
            pendingPushSig = '';
            pendingPushEls = null;
            pendingPushFiles = null;
        }}
    }}

    function applyRemotePayload(data) {{
        if (!data || !api) return;
        const remoteRev = Number(data.rev || 0);
        const mtype = String(data.type || data.kind || '');
        if (mtype === 'clear') {{
            pendingRemote = null;
            drawingActive = false;
            if (remoteRev < rev) return;
            markBoardClearedLocally();
            clearInFlight = false;
            adoptSceneGen(data.scene_gen);
            applyingRemote = true;
            try {{
                if (api.resetScene) api.resetScene();
                else api.updateScene({{ elements: [], ...remoteUpdateOpts }});
                lastLocalSig = sceneSig([], {{}});
                localDirty = false;
            }} finally {{
                applyingRemote = false;
            }}
            rev = Math.max(rev, remoteRev);
            return;
        }}
        // Never updateScene while the pen is hot — that stalls/warps Excalidraw.
        if (isPenHot()) {{
            if (!pendingRemote) {{
                pendingRemote = data;
            }} else {{
                pendingRemote = {{
                    rev: Math.max(Number(pendingRemote.rev) || 0, remoteRev),
                    scene_gen: data.scene_gen != null
                        ? data.scene_gen
                        : pendingRemote.scene_gen,
                    type: data.type || pendingRemote.type,
                    elements: mergeElements(
                        pendingRemote.elements || [],
                        data.elements || []
                    ),
                    files: Object.assign(
                        {{}},
                        pendingRemote.files || {{}},
                        data.files || {{}}
                    ),
                }};
            }}
            return;
        }}
        if (remoteRev < rev) return;
        adoptSceneGen(data.scene_gen);
        const remoteEls = data.elements || [];
        const remoteFiles = data.files || {{}};
        const live = liveScene();
        const liveById = Object.create(null);
        for (const el of live.elements || []) {{
            if (el && typeof el.id === 'string' && el.id) liveById[el.id] = el;
        }}
        // Drop stale echoes (own strokes / pre-erase copies) so updateScene
        // does not redraw them as 重笔 or resurrect undone strokes.
        const accepted = [];
        for (const el of remoteEls) {{
            if (shouldAcceptRemoteEl(el, liveById)) accepted.push(el);
        }}
        const fileKeys = Object.keys(remoteFiles || {{}});
        if (accepted.length === 0 && fileKeys.length === 0) {{
            if (remoteRev > rev) rev = remoteRev;
            return;
        }}
        noteRemoteSynced(accepted, remoteFiles);
        let nextEls;
        // Empty remote + no local pending ⇒ peer clear / empty board.
        // Otherwise merge so an older poll cannot wipe unpushed strokes.
        if (remoteEls.length === 0 && !localDirty && !pushInFlight) {{
            nextEls = [];
        }} else {{
            // Live as base: keep in-progress strokes; apply only accepted remote.
            nextEls = mergeElements(live.elements, accepted);
        }}
        const nextFiles = Object.assign({{}}, live.files || {{}}, remoteFiles);
        const nextSig = sceneSig(nextEls, nextFiles);
        const curSig = sceneSig(live.elements, live.files);
        if (nextSig !== curSig || fileKeys.length) {{
            applyingRemote = true;
            try {{
                // addFiles expects BinaryFileData[]; getFiles()/sync return a map.
                const fileList = Object.values(remoteFiles).filter(
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
        if (remoteRev > rev) rev = remoteRev;
    }}

    function ensureHttpSyncLoop() {{
        if (httpSyncActive) return;
        httpSyncActive = true;
        void syncLoop();
    }}

    async function syncLoop() {{
        while (httpSyncActive && ticket) {{
            try {{
                if (canvasWsLive) {{
                    await syncOnce(false);
                    // WS is primary; keep HTTP backup rare to avoid full-scene jank.
                    await new Promise((r) => setTimeout(r, 8000));
                }} else {{
                    await syncOnce(false);
                    await new Promise((r) => setTimeout(r, 1200));
                }}
            }} catch (_) {{
                await new Promise((r) => setTimeout(r, 400));
            }}
        }}
    }}

    function scheduleCanvasWsRetry() {{
        if (canvasWsRetryTimer != null || !ticket || canvasWsForceHttp) return;
        canvasWsRetryTimer = setTimeout(() => {{
            canvasWsRetryTimer = null;
            connectCanvasWs();
        }}, 1500);
    }}

    function connectCanvasWs() {{
        if (!ticket || canvasWsForceHttp) return;
        if (canvasWs && (canvasWs.readyState === 0 || canvasWs.readyState === 1)) return;
        const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const url = proto + '//' + location.host + '/canvas/' + token +
            '/ws?ticket=' + encodeURIComponent(ticket);
        let ws;
        try {{
            ws = new WebSocket(url);
        }} catch (_) {{
            canvasWsForceHttp = true;
            ensureHttpSyncLoop();
            return;
        }}
        canvasWs = ws;
        ws.onopen = function () {{
            canvasWsLive = true;
            setStatus(i18n.statusReady, false);
        }};
        ws.onmessage = function (ev) {{
            let data = null;
            try {{
                data = JSON.parse(ev.data);
            }} catch (_) {{
                return;
            }}
            if (!data || typeof data !== 'object') return;
            const mtype = String(data.type || '');
            if (mtype === 'ack') {{
                if (pushAckTimer) {{ clearTimeout(pushAckTimer); pushAckTimer = null; }}
                if (typeof data.rev === 'number') rev = data.rev;
                adoptSceneGen(data.scene_gen);
                const sig = pendingPushSig;
                const pushedEls = pendingPushEls;
                const pushedFiles = pendingPushFiles;
                pushInFlight = false;
                pendingPushSig = '';
                pendingPushEls = null;
                pendingPushFiles = null;
                if (String(data.kind || '') === 'clear') {{
                    clearInFlight = false;
                    markBoardClearedLocally();
                    setStatus(i18n.statusReady, false);
                    return;
                }}
                if (clearInFlight) {{
                    setStatus(i18n.statusReady, false);
                    return;
                }}
                if (pushedEls || pushedFiles) {{
                    markLocalPushed(pushedEls || [], pushedFiles || {{}});
                }}
                if (sig) finishLocalPush();
                else setStatus(i18n.statusReady, false);
                return;
            }}
            if (mtype === 'scene' || mtype === 'clear') {{
                applyRemotePayload(data);
                setStatus(i18n.statusReady, false);
                return;
            }}
            if (mtype === 'error') {{
                if (pushAckTimer) {{ clearTimeout(pushAckTimer); pushAckTimer = null; }}
                pushInFlight = false;
                pendingPushSig = '';
                pendingPushEls = null;
                pendingPushFiles = null;
                clearInFlight = false;
                setStatus(i18n.statusErr, true);
                // Prefer resync: stale pre-clear pushes must not loop.
                void syncOnce(false).then(() => {{
                    if (localDirty) schedulePush();
                }});
            }}
        }};
        ws.onclose = function () {{
            canvasWsLive = false;
            if (canvasWs === ws) canvasWs = null;
            if (pushAckTimer) {{ clearTimeout(pushAckTimer); pushAckTimer = null; }}
            if (pushInFlight) {{
                pushInFlight = false;
                localDirty = true;
                schedulePush();
            }}
            ensureHttpSyncLoop();
            scheduleCanvasWsRetry();
        }};
        ws.onerror = function () {{
            try {{ ws.close(); }} catch (_) {{}}
        }};
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
                applyRemotePayload({{
                    rev: remoteRev,
                    scene_gen: data.scene_gen,
                    elements: data.elements || [],
                    files: data.files || {{}},
                }});
                rev = remoteRev;
            }} else if (remoteRev > rev) {{
                rev = remoteRev;
            }}
            adoptSceneGen(data.scene_gen);
            setStatus(i18n.statusReady, false);
        }} catch (_) {{
            setStatus(i18n.statusErr, true);
        }} finally {{
            syncing = false;
        }}
    }}

    clearBtn.addEventListener('click', () => {{
        if (!ticket) return;
        if (!confirm(i18n.clearConfirm)) return;
        clearBtn.disabled = true;
        markBoardClearedLocally();
        clearInFlight = true;
        // Yield a frame so the button/disabled state paints before resetScene.
        requestAnimationFrame(() => {{
            void (async () => {{
                try {{
                    if (canvasWsLive && canvasWs && canvasWs.readyState === 1) {{
                        canvasWs.send(JSON.stringify({{ type: 'clear' }}));
                        applyingRemote = true;
                        try {{
                            if (api) {{
                                if (api.resetScene) api.resetScene();
                                else api.updateScene({{ elements: [], ...remoteUpdateOpts }});
                            }}
                            lastLocalSig = sceneSig([], {{}});
                            localDirty = false;
                        }} finally {{
                            applyingRemote = false;
                        }}
                        return;
                    }}
                    const res = await fetch('/canvas/' + token + '/clear', {{
                        method: 'POST',
                        headers: {{ 'X-Canvas-Ticket': ticket }},
                        cache: 'no-store',
                    }});
                    const data = await res.json().catch(() => ({{}}));
                    if (!res.ok) throw new Error(data.error || 'clear failed');
                    rev = Number(data.rev || rev + 1);
                    adoptSceneGen(data.scene_gen);
                    clearInFlight = false;
                    markBoardClearedLocally();
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
                    clearInFlight = false;
                    setStatus(i18n.statusErr, true);
                }} finally {{
                    clearBtn.disabled = false;
                }}
            }})();
        }});
    }});

    // Embedded clients (Android/iOS/Electron) expose SSHChatNative.close —
    // show a compact toolbar close instead of a floating overlay that covers sync status.
    (function wireClose() {{
        const closeBtn = document.getElementById('closeBtn');
        if (!closeBtn) return;
        function hasNativeClose() {{
            if (window.__SSHCHAT_EMBEDDED__) return true;
            try {{
                if (window.SSHChatNative && window.SSHChatNative.close) return true;
            }} catch (_) {{}}
            return false;
        }}
        function reveal() {{
            if (!hasNativeClose()) return;
            closeBtn.hidden = false;
        }}
        closeBtn.addEventListener('click', function () {{
            try {{
                if (window.SSHChatNative && window.SSHChatNative.close) {{
                    window.SSHChatNative.close();
                    return;
                }}
            }} catch (_) {{}}
            try {{
                window.webkit.messageHandlers.sshchatClose.postMessage({{}});
            }} catch (_) {{}}
        }});
        reveal();
        setTimeout(reveal, 50);
        setTimeout(reveal, 300);
    }})();

    unlockBtn.addEventListener('click', () => {{ void auth(); }});
    keyInput.addEventListener('keydown', (e) => {{
        if (e.key === 'Enter') void auth();
    }});

    // Prefer key injected by native WebView (set before this module finishes).
    // Hash (#k=) / window.name remain for Electron/Tk. Do NOT unlock until
    // listeners exist — early btn.click() is a no-op while esm.sh is loading.
    const injected = (window.__SSHCHAT_KEY || '').toString().trim().toUpperCase();
    try {{ delete window.__SSHCHAT_KEY; }} catch (_) {{}}
    const autofill = (injected.length === 6 ? injected : '') || hashFragmentKey();
    if (autofill) {{
        keyInput.value = autofill;
        // Pass key explicitly so Chrome autofill cannot overwrite before POST.
        setTimeout(() => {{ void auth(autofill); }}, 50);
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


def _broadcast_canvas_update(
    result: Optional[dict],
    *,
    exclude_conn_id: Optional[str] = None,
    msg_type: str = "scene",
) -> None:
    if not result:
        return
    session_id = str(result.get("session_id") or "").strip()
    if not session_id:
        return
    payload = {
        "type": msg_type,
        "rev": result.get("rev", 0),
        "scene_gen": result.get("scene_gen", 0),
        "author": result.get("author") or "",
        "elements": [],
        "files": {},
    }
    if msg_type == "clear":
        payload["kind"] = "clear"
    else:
        # Prefer incremental patch when present (peers merge by id/version).
        if "patch_elements" in result:
            payload["elements"] = result.get("patch_elements") or []
            payload["files"] = result.get("patch_files") or {}
        else:
            payload["elements"] = result.get("elements") or []
            payload["files"] = result.get("files") or {}
    canvas_ws.canvas_ws_hub.broadcast(
        session_id,
        payload,
        exclude_conn_id=exclude_conn_id,
    )


def _ws_conn_id() -> str:
    return secrets.token_urlsafe(12)


def handle_canvas_websocket(handler: "BaseHTTPRequestHandler") -> bool:
    """Upgrade GET /canvas/<token>/ws?ticket=... to a WebSocket scene channel."""
    parsed = urlparse(handler.path)
    parts = parsed.path.strip("/").split("/")
    if len(parts) != 3 or parts[0] != "canvas" or parts[2] != "ws":
        return False

    upgrade = (handler.headers.get("Upgrade") or "").strip().lower()
    connection = (handler.headers.get("Connection") or "").lower()
    sec_key = (handler.headers.get("Sec-WebSocket-Key") or "").strip()
    if upgrade != "websocket" or "upgrade" not in connection or not sec_key:
        handler._send_error_json(400, "需要 WebSocket 升级")  # type: ignore[attr-defined]
        return True

    qs = parse_qs(parsed.query or "")
    ticket = (qs.get("ticket") or [""])[0].strip()
    if not ticket:
        ticket = (handler.headers.get("X-Canvas-Ticket") or "").strip()
    token = parts[1]
    store = canvas_sharing.canvas_store
    session, participant, err = store.resolve_ticket(token, ticket)
    if session is None or participant is None:
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

    client = canvas_ws.CanvasWsClient(
        conn_id=_ws_conn_id(),
        session_id=session.session_id,
        participant=participant,
        token=token,
        sock=sock,
    )

    def on_scene(
        ws_client: canvas_ws.CanvasWsClient,
        elements,
        files,
        scene_gen=None,
    ) -> Optional[dict]:
        result, _err = store.apply_scene(
            ws_client.token,
            ticket,
            elements=elements,
            files=files,
            scene_gen=scene_gen,
        )
        if result is None:
            return None
        # Broadcast off the WS read-loop thread so stringify/sendall does not
        # delay the drawer ack (felt as pen stalls under load).
        exclude = ws_client.conn_id

        def _bcast() -> None:
            try:
                _broadcast_canvas_update(result, exclude_conn_id=exclude)
            except Exception:
                pass

        threading.Thread(target=_bcast, daemon=True).start()
        return result

    def on_clear(ws_client: canvas_ws.CanvasWsClient) -> Optional[dict]:
        result, _err = store.clear_board(ws_client.token, ticket)
        if result is None:
            return None
        exclude = ws_client.conn_id

        def _bcast() -> None:
            try:
                _broadcast_canvas_update(
                    result, exclude_conn_id=exclude, msg_type="clear"
                )
            except Exception:
                pass

        threading.Thread(target=_bcast, daemon=True).start()
        return result

    canvas_ws.run_canvas_ws_session(client, on_scene=on_scene, on_clear=on_clear)
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
                "scene_gen": session.scene_gen,
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
            scene_gen=body.get("scene_gen"),
        )
        if result is None:
            handler._send_error_json(403, err)  # type: ignore[attr-defined]
            return True
        _broadcast_canvas_update(result)
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
        _broadcast_canvas_update(event, msg_type="clear")
        handler._send_json_response(200, event)  # type: ignore[attr-defined]
        return True

    handler._send_error_json(404, "网址无效")  # type: ignore[attr-defined]
    return True
