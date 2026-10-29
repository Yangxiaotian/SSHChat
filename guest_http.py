"""One-shot guest chat page + WebSocket, served on FileHTTP."""

from __future__ import annotations

import cgi
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
_publish_guest_image = None


def bind_chat(
    *,
    attach_guest,
    process_client_line,
    remove_client,
    publish_guest_image=None,
) -> None:
    global _attach_guest, _process_client_line, _remove_client, _publish_guest_image
    _attach_guest = attach_guest
    _process_client_line = process_client_line
    _remove_client = remove_client
    _publish_guest_image = publish_guest_image


_TEXTS = {
    "zh": {
        "title": "SSHChat 临时聊天",
        "room": "房间",
        "you": "你是",
        "enter": "进入聊天",
        "send": "发送",
        "pic": "图片",
        "hint": "只能打开一次，进入后 {minutes} 分钟。页面倒计时显示分和秒。系统昵称随机；可填备注，聊天里显示为 ~g…（备注）。可发文字和图片，并用游戏区下棋。请勿刷新，刷新后立即失效。",
        "remark": "备注（可选）",
        "remark_ph": "怎么称呼你",
        "game": "游戏",
        "gjoin": "加入",
        "gshow": "刷新棋盘",
        "gresign": "认输",
        "gpass": "停一手",
        "gnew": "开局",
        "ghint_stone": "点空位落子",
        "ghint_piece": "先点己方棋子，再点目标格",
        "ghint_dark": "点问号翻面；或先点己子再点目标",
        "ghint_card": "社交/牌类请看聊天，在输入框操作",
        "games": [
            ["gomoku", "五子棋"],
            ["reversi", "黑白棋"],
            ["xiangqi", "象棋"],
            ["chess", "国际象棋"],
            ["go", "围棋"],
            ["darkchess", "暗棋"],
            ["doushou", "斗兽棋"],
        ],
        "used": "这个链接已经用过（或刚刷新过），不能再打开。请让对方重新发一条 /guest。",
        "expired": "临时聊天已结束。",
        "gone": "链接无效或已过期。请让对方重新发一条 /guest。",
        "left": "剩余",
        "placeholder": "说点什么…",
        "fail": "进不去。",
        "uploading": "正在发送图片…",
        "upload_ok": "图片已发送。",
        "upload_fail": "图片发送失败。",
        "not_image": "只能发送图片（jpg/png/gif/webp）。",
        "too_big": "图片太大（最多 8MB）。",
        "file_off": "文件服务未启用，无法发图。",
    },
    "en": {
        "title": "SSHChat guest chat",
        "room": "Room",
        "you": "You are",
        "enter": "Enter chat",
        "send": "Send",
        "pic": "Image",
        "hint": "One open only. After you enter you have {minutes} minutes. The countdown shows minutes and seconds. Your nick is random; an optional remark shows as ~g… (remark). Text, images, and the game panel are allowed. Do not refresh — a reload kills the link immediately.",
        "remark": "Remark (optional)",
        "remark_ph": "What should we call you",
        "game": "Games",
        "gjoin": "Join",
        "gshow": "Refresh",
        "gresign": "Resign",
        "gpass": "Pass",
        "gnew": "New",
        "ghint_stone": "Tap an empty point to move",
        "ghint_piece": "Tap your piece, then the destination",
        "ghint_dark": "Tap ? to flip, or tap a piece then a destination",
        "ghint_card": "Social/card games: follow chat and type in the box",
        "games": [
            ["gomoku", "Gomoku"],
            ["reversi", "Reversi"],
            ["xiangqi", "Xiangqi"],
            ["chess", "Chess"],
            ["go", "Go"],
            ["darkchess", "Dark chess"],
            ["doushou", "Jungle"],
        ],
        "used": "This link was already used (or you refreshed). It cannot be opened again. Ask them to send /guest once more.",
        "expired": "Guest chat has ended.",
        "gone": "This link is invalid or expired. Ask them to send /guest once more.",
        "left": "Left",
        "placeholder": "Message…",
        "fail": "Could not join.",
        "uploading": "Sending image…",
        "upload_ok": "Image sent.",
        "upload_fail": "Could not send image.",
        "not_image": "Images only (jpg/png/gif/webp).",
        "too_big": "Image too large (max 8MB).",
        "file_off": "File service is off; cannot send images.",
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


# Board UI for guests. Not an f-string — braces are JS.
_GUEST_GAME_JS = r"""
var gameBuf = [], snap = null, fromCell = null, gOpen = false, gDead = false;
var HEADERS = [
  ['xiangqi', /^xiangqi (?:对局)?[（(]([^）)]*)[）)]/],
  ['chess', /^chess (?:对局)?[（(]([^）)]*)[）)]/],
  ['go', /^go (?:对局)?[（(]([^）)]*)[）)]/],
  ['gomoku', /^gomoku (?:对局)?[（(]([^）)]*)[）)]/],
  ['doushou', /^doushou (?:对局)?[（(]([^）)]*)[）)]/],
  ['darkchess', /^darkchess (?:对局)?[（(]([^）)]*)[）)]/],
  ['reversi', /^reversi game [（(]([^）)]*)[）)]/]
];
function cmdGame(rest) {
  if (!ws || ws.readyState !== 1 || gDead) return;
  ws.send(JSON.stringify({t: 'msg', s: rest ? ('/game ' + rest) : '/game'}));
}
function detectHeader(line) {
  var t = String(line || '').replace(/^\[#[^\]]+\] \[\*\] /, '').replace(/^\[\*\]\s?/, '').trim();
  for (var i = 0; i < HEADERS.length; i++) {
    var m = HEADERS[i][1].exec(t);
    if (m) return [HEADERS[i][0], (m[1] || '').trim(), t];
  }
  return null;
}
function ingestGame(chunk) {
  var parts = String(chunk || '').split(/\r?\n/);
  for (var i = 0; i < parts.length; i++) {
    var line = parts[i];
    if (!line) continue;
    var m = /^\[#[^\]]+\] \[\*\] (.*)$/.exec(line);
    gameBuf.push(m ? m[1] : line.replace(/^\[\*\]\s?/, ''));
  }
  if (gameBuf.length > 400) gameBuf = gameBuf.slice(-300);
  var next = parseGame(gameBuf);
  if (next) { snap = next; fromCell = null; renderGame(); }
}
function parseGame(lines) {
  var idx = -1, kind = '', state = '';
  for (var i = lines.length - 1; i >= 0; i--) {
    var h = detectHeader(lines[i]);
    if (!h) continue;
    idx = i; kind = h[0]; state = h[1]; break;
  }
  if (idx < 0) return null;
  var block = lines.slice(idx);
  var s = {kind: kind, state: state, header: block[0].trim(), board: null, flipped: false};
  if (kind === 'gomoku' || kind === 'go' || kind === 'reversi') parseStones(s, block);
  else if (kind === 'chess') parseChess(s, block);
  else if (kind === 'xiangqi') parseXiangqi(s, block);
  else if (kind === 'darkchess') parseDark(s, block);
  else if (kind === 'doushou') parseDoushou(s, block);
  return s;
}
function allMatches(re, s) {
  var out = [], m;
  re.lastIndex = 0;
  while ((m = re.exec(s))) out.push(m);
  return out;
}
function parseStones(s, block) {
  var grid = [], labels = [], colLabels = [];
  var tokRe = /\(([.#o])\)|!?([.#o])/g;
  for (var i = 1; i < block.length; i++) {
    var hdr = /^\s+(\d{1,2})(?:\s+\d{1,2})+\s*$/.exec(block[i]);
    if (hdr && !colLabels.length) { colLabels = block[i].trim().split(/\s+/); continue; }
    var m = /^\s*(\d{1,2})\s(.*)$/.exec(block[i]);
    if (!m) continue;
    var toks = allMatches(tokRe, m[2]).map(function (x) {
      return {s: x[1] || x[2], hi: !!x[1] || x[0].charAt(0) === '!'};
    });
    if (toks.length < 5) continue;
    if (grid.length && toks.length !== grid[0].length) continue;
    grid.push(toks);
    labels.push(+m[1]);
  }
  if (!grid.length || grid.length !== grid[0].length) return;
  var cells = [], last = {};
  for (var r = 0; r < grid.length; r++) for (var c = 0; c < grid[r].length; c++) {
    var t = grid[r][c];
    cells.push(t.s === '#' ? 'B' : t.s === 'o' ? 'W' : '');
    if (t.hi) last[r * grid.length + c] = 1;
  }
  s.board = {rows: grid.length, cols: grid[0].length, cells: cells, last: last, rowLabels: labels, colLabels: colLabels};
}
function parseChess(s, block) {
  var files = [], rows = [], ranks = [];
  var tokRe = /\((.)\)|([♔♕♖♗♘♙♚♛♜♝♞♟·])/g;
  for (var i = 1; i < block.length; i++) {
    if (!files.length && /^\s*([a-h])(?:\s+[a-h]){7}\s*$/.test(block[i])) {
      files = block[i].trim().split(/\s+/); continue;
    }
    var m = /^\s*(\d{1,2})\s(.*)$/.exec(block[i]);
    if (!m) continue;
    var toks = allMatches(tokRe, m[2]).map(function (x) {
      return {s: x[1] || x[2], hi: !!x[1]};
    });
    if (toks.length !== 8) continue;
    rows.push(toks); ranks.push(+m[1]);
    if (rows.length === 8) break;
  }
  if (rows.length !== 8) return;
  if (!files.length) files = 'abcdefgh'.split('');
  var cells = [], last = {};
  for (var r = 0; r < 8; r++) for (var c = 0; c < 8; c++) {
    var t = rows[r][c];
    cells.push(t.s === '·' ? '' : t.s);
    if (t.hi) last[r * 8 + c] = 1;
  }
  s.board = {rows: 8, cols: 8, cells: cells, last: last, rowLabels: ranks, colLabels: files};
  s.flipped = files[0] === 'h';
}
function collectRows(block, cols, maxRows, tokRe, numbered) {
  var rows = [], labels = [];
  for (var i = 1; i < block.length && rows.length < maxRows; i++) {
    var rest = block[i], label = 0;
    if (numbered) {
      var m = /^\s*(\d{1,2})\s(.*)$/.exec(block[i]);
      if (!m) continue;
      label = +m[1]; rest = m[2];
    }
    tokRe.lastIndex = 0;
    var toks = rest.match(tokRe);
    if (!toks || toks.length !== cols) continue;
    rows.push(toks); labels.push(label);
  }
  return {rows: rows, labels: labels};
}
function parseXiangqi(s, block) {
  var pack = collectRows(block, 9, 10, /[+\-!][^\s·*+\-!]|·|\*/g, false);
  if (pack.rows.length !== 10) return;
  s.flipped = block.some(function (ln) { return ln.indexOf('己方在下方') >= 0 || /you are at the bottom/i.test(ln); });
  var cells = [], last = {};
  for (var r = 0; r < 10; r++) for (var c = 0; c < 9; c++) {
    var tok = pack.rows[r][c], i = r * 9 + c;
    if (tok === '·') { cells.push(''); continue; }
    if (tok === '*') { cells.push(''); last[i] = 1; continue; }
    var red = tok.charAt(0) === '+' || '帅仕相兵'.indexOf(tok.charAt(1)) >= 0;
    if (tok.charAt(0) === '-') red = false;
    if (tok.charAt(0) === '!') last[i] = 1;
    cells.push((red ? 'r' : 'b') + tok.slice(1));
  }
  s.board = {rows: 10, cols: 9, cells: cells, last: last, rowLabels: [], colLabels: []};
}
function parseDark(s, block) {
  var pack = collectRows(block, 8, 4, /!?(?:[+\-][^\s!+\-?.]|\?|\.)/g, true);
  if (pack.rows.length !== 4) return;
  var letters = {G:'将', A:'士', E:'象', R:'车', H:'马', C:'炮', S:'卒'};
  var cells = [], last = {};
  for (var r = 0; r < 4; r++) for (var c = 0; c < 8; c++) {
    var tok = pack.rows[r][c], i = r * 8 + c;
    if (tok.charAt(0) === '!') { last[i] = 1; tok = tok.slice(1); }
    if (tok === '?') cells.push('?');
    else if (tok === '.') cells.push('');
    else cells.push((tok.charAt(0) === '+' ? 'r' : 'b') + (letters[tok.charAt(1)] || tok.charAt(1)));
  }
  s.board = {rows: 4, cols: 8, cells: cells, last: last, rowLabels: pack.labels, colLabels: []};
}
function parseDoushou(s, block) {
  var pack = collectRows(block, 7, 9, /!?[+\-][^\s·!]|黑陷|黑穴|红陷|红穴|河|·|bT|bD|rT|rD|RV/g, true);
  if (pack.rows.length !== 9) return;
  var letters = {R:'鼠', C:'猫', D:'狗', W:'狼', P:'豹', T:'虎', L:'狮', E:'象'};
  var colLabels = [];
  for (var i = 0; i < block.length; i++) {
    if (/^\s*([1-7])(?:\s+[1-7]){6}\s*$/.test(block[i])) { colLabels = block[i].trim().split(/\s+/); break; }
  }
  var cells = [], last = {};
  for (var r = 0; r < 9; r++) for (var c = 0; c < 7; c++) {
    var tok = pack.rows[r][c], i = r * 7 + c;
    if (tok.charAt(0) === '!') { last[i] = 1; tok = tok.slice(1); }
    if (tok.length === 2 && (tok.charAt(0) === '+' || tok.charAt(0) === '-'))
      cells.push((tok.charAt(0) === '+' ? 'r' : 'b') + (letters[tok.charAt(1)] || tok.charAt(1)));
    else if (tok === '·' || tok === '.' || tok === '*') cells.push('');
    else if (tok === 'RV') cells.push('河');
    else if (tok === 'rD') cells.push('红穴');
    else if (tok === 'bD') cells.push('黑穴');
    else if (tok === 'rT') cells.push('红陷');
    else if (tok === 'bT') cells.push('黑陷');
    else cells.push(tok);
  }
  s.board = {rows: 9, cols: 7, cells: cells, last: last, rowLabels: pack.labels, colLabels: colLabels};
  s.flipped = pack.labels[0] === 9;
}
function srvRC(s, r, c) {
  var b = s.board;
  if (s.kind === 'xiangqi') {
    return [s.flipped ? b.rows - r : r + 1, s.flipped ? b.cols - c : c + 1];
  }
  var row = (b.rowLabels && b.rowLabels[r] != null) ? b.rowLabels[r] : r + 1;
  var col;
  if (b.colLabels && b.colLabels[c] != null && /^\d+$/.test(b.colLabels[c])) col = +b.colLabels[c];
  else col = c + 1;
  return [row, col];
}
function chessSq(s, r, c) {
  var files = (s.board.colLabels && s.board.colLabels.length) ? s.board.colLabels : 'abcdefgh'.split('');
  var ranks = (s.board.rowLabels && s.board.rowLabels.length) ? s.board.rowLabels : [8,7,6,5,4,3,2,1];
  return String(files[c]) + String(ranks[r]);
}
function onCell(r, c) {
  var s = snap; if (!s || !s.board || gDead) return;
  var b = s.board, tok = b.cells[r * b.cols + c] || '';
  if (s.kind === 'go' || s.kind === 'gomoku' || s.kind === 'reversi') {
    if (tok && s.kind !== 'reversi') return;
    var rc = srvRC(s, r, c);
    cmdGame('move ' + rc[0] + ' ' + rc[1]);
    return;
  }
  if (s.kind === 'chess') {
    if (!fromCell) {
      if (!tok) return;
      fromCell = [r, c]; renderGame(); return;
    }
    var a = chessSq(s, fromCell[0], fromCell[1]), z = chessSq(s, r, c);
    fromCell = null;
    if (a !== z) cmdGame('move ' + a + z);
    renderGame(); return;
  }
  if (s.kind === 'darkchess') {
    var d = srvRC(s, r, c);
    if (!fromCell) {
      if (tok === '?') { cmdGame('move flip ' + d[0] + ' ' + d[1]); return; }
      if (!tok) return;
      fromCell = [r, c]; renderGame(); return;
    }
    var fr = fromCell[0], fc = fromCell[1];
    var f = srvRC(s, fr, fc);
    fromCell = null;
    if (!(fr === r && fc === c))
      cmdGame('move move ' + f[0] + ' ' + f[1] + ' ' + d[0] + ' ' + d[1]);
    renderGame(); return;
  }
  if (s.kind === 'xiangqi' || s.kind === 'doushou') {
    if (!fromCell) {
      if (!tok || tok === '?' || tok.length < 2) return;
      fromCell = [r, c]; renderGame(); return;
    }
    var a2 = srvRC(s, fromCell[0], fromCell[1]), z2 = srvRC(s, r, c);
    fromCell = null;
    if (a2[0] === z2[0] && a2[1] === z2[1]) { renderGame(); return; }
    if (s.kind === 'xiangqi') cmdGame('move coord ' + a2[0] + ' ' + a2[1] + ' ' + z2[0] + ' ' + z2[1]);
    else cmdGame('move ' + a2[0] + ' ' + a2[1] + ' ' + z2[0] + ' ' + z2[1]);
    renderGame();
  }
}
function btn(label, fn) {
  var b = document.createElement('button');
  b.type = 'button'; b.textContent = label; b.onclick = fn;
  return b;
}
function renderGame() {
  var body = document.getElementById('gbody');
  var acts = document.getElementById('gacts');
  var neu = document.getElementById('gnew');
  var boardEl = document.getElementById('gboard');
  var hint = document.getElementById('ghint');
  var stat = document.getElementById('gstat');
  acts.innerHTML = ''; neu.innerHTML = ''; boardEl.innerHTML = '';
  acts.appendChild(btn(L.gjoin, function () { cmdGame('join'); }));
  acts.appendChild(btn(L.gshow, function () { cmdGame('show'); }));
  acts.appendChild(btn(L.gresign, function () { cmdGame('resign'); }));
  if (snap && snap.kind === 'go') acts.appendChild(btn(L.gpass, function () { cmdGame('move pass'); }));
  (boot.games || []).forEach(function (g) {
    neu.appendChild(btn(g[1], function () { cmdGame('new ' + g[0]); }));
  });
  if (!snap) {
    stat.textContent = '';
    hint.textContent = L.gnew;
    return;
  }
  stat.textContent = snap.header;
  if (!gOpen) { gOpen = true; body.style.display = 'block'; }
  var b = snap.board;
  if (!b) {
    hint.textContent = L.ghint_card;
    return;
  }
  boardEl.style.gridTemplateColumns = 'repeat(' + b.cols + ', minmax(1.35rem, 1fr))';
  for (var r = 0; r < b.rows; r++) for (var c = 0; c < b.cols; c++) {
    (function (rr, cc) {
      var cell = document.createElement('button');
      cell.type = 'button';
      var tok = b.cells[rr * b.cols + cc] || '';
      var cls = '';
      if (tok === 'B') cls = 'c-B';
      else if (tok === 'W') cls = 'c-W';
      else if (tok.charAt(0) === 'r') cls = 'c-r';
      else if (tok.charAt(0) === 'b') cls = 'c-b';
      if (b.last && b.last[rr * b.cols + cc]) cls += ' c-last';
      if (fromCell && fromCell[0] === rr && fromCell[1] === cc) cls += ' c-sel';
      cell.className = cls;
      cell.textContent = tok === 'B' ? '●' : tok === 'W' ? '○' : (tok.charAt(0) === 'r' || tok.charAt(0) === 'b' ? tok.slice(1) : tok);
      cell.onclick = function () { onCell(rr, cc); };
      boardEl.appendChild(cell);
    })(r, c);
  }
  if (snap.kind === 'darkchess') hint.textContent = L.ghint_dark;
  else if (snap.kind === 'xiangqi' || snap.kind === 'chess' || snap.kind === 'doushou') hint.textContent = L.ghint_piece;
  else hint.textContent = L.ghint_stone;
}
document.getElementById('gtog').onclick = function () {
  gOpen = !gOpen;
  document.getElementById('gbody').style.display = gOpen ? 'block' : 'none';
  if (gOpen) renderGame();
};
"""


def render_guest_page(link: guest_sharing.GuestLink, lang: str) -> str:
    text = _TEXTS[lang]
    minutes = link.minutes or guest_sharing.DEFAULT_MINUTES
    boot = {
        "token": link.token,
        "room": link.room,
        "lang": lang,
        "maxImage": guest_sharing.MAX_IMAGE_BYTES,
        "games": text["games"],
    }
    boot_json = json.dumps(boot, ensure_ascii=False)
    labels = json.dumps(
        {
            "fail": text["fail"],
            "expired": text["expired"],
            "used": text["used"],
            "left": text["left"],
            "you": text["you"],
            "uploading": text["uploading"],
            "upload_ok": text["upload_ok"],
            "upload_fail": text["upload_fail"],
            "not_image": text["not_image"],
            "too_big": text["too_big"],
            "file_off": text["file_off"],
            "remark": text["remark"],
            "game": text["game"],
            "gjoin": text["gjoin"],
            "gshow": text["gshow"],
            "gresign": text["gresign"],
            "gpass": text["gpass"],
            "gnew": text["gnew"],
            "ghint_stone": text["ghint_stone"],
            "ghint_piece": text["ghint_piece"],
            "ghint_dark": text["ghint_dark"],
            "ghint_card": text["ghint_card"],
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
#log img.preview {{ display:block; max-width:min(100%,320px); max-height:240px; margin:.4rem 0; border-radius:6px; }}
#row {{ display:flex; padding:.5rem; gap:.5rem; align-items:center; }}
#row input[type=text] {{ flex:1; padding:.6rem; font-size:16px; }}
#gate {{ padding:1.5rem; max-width:28rem; }}
#gate p {{ line-height:1.5; color:#ccc; }}
#gate input {{ width:100%; max-width:16rem; padding:.5rem; font-size:16px; box-sizing:border-box; }}
button {{ padding:.6rem 1rem; font-size:16px; }}
.warn {{ color:#f6a; }}
#pic {{ display:none; }}
#game {{ background:#1a1a1a; border-bottom:1px solid #333; flex-shrink:0; }}
#ghead {{ display:flex; align-items:center; gap:.6rem; padding:.4rem .7rem; }}
#gstat {{ flex:1; font-size:13px; color:#bbb; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
#gbody {{ display:none; padding:0 .7rem .7rem; }}
#gacts, #gnew {{ display:flex; flex-wrap:wrap; gap:.35rem; margin-bottom:.45rem; }}
#gacts button, #gnew button, #gtog {{ padding:.35rem .6rem; font-size:13px; }}
#gboard {{ display:grid; gap:1px; overflow:auto; max-height:min(52vh, 420px); width:max-content; max-width:100%; }}
#gboard button {{ min-width:1.45rem; min-height:1.45rem; padding:0; font-size:12px; aspect-ratio:1; line-height:1; touch-action:manipulation; }}
#gboard button.c-B {{ background:#222; color:#eee; border-radius:50%; }}
#gboard button.c-W {{ background:#f2f2f2; color:#111; border-radius:50%; }}
#gboard button.c-r {{ color:#e66; background:#2a1a1a; }}
#gboard button.c-b {{ color:#9cf; background:#1a1a2a; }}
#gboard button.c-last {{ outline:2px solid gold; }}
#gboard button.c-sel {{ outline:2px solid #6cf; }}
#ghint {{ font-size:12px; color:#888; margin-top:.35rem; }}
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
    <p>
      <label for="remark">{html.escape(text['remark'])}</label><br>
      <input id="remark" type="text" maxlength="{guest_sharing.MAX_REMARK_CHARS}" placeholder="{html.escape(text['remark_ph'])}" autocomplete="nickname">
    </p>
    <p><button type="button" id="go">{html.escape(text['enter'])}</button></p>
    <p id="err" class="warn"></p>
  </div>
  <div id="chat">
    <div id="game">
      <div id="ghead">
        <button type="button" id="gtog">{html.escape(text['game'])}</button>
        <span id="gstat"></span>
      </div>
      <div id="gbody">
        <div id="gacts"></div>
        <div id="gnew"></div>
        <div id="gboard"></div>
        <div id="ghint"></div>
      </div>
    </div>
    <div id="log"></div>
    <form id="row">
      <input id="msg" type="text" maxlength="500" placeholder="{html.escape(text['placeholder'])}" autocomplete="off">
      <button type="button" id="pick">{html.escape(text['pic'])}</button>
      <input id="pic" type="file" accept="image/jpeg,image/png,image/gif,image/webp,image/*">
      <button type="submit">{html.escape(text['send'])}</button>
    </form>
  </div>
</div>
<script>
var boot = {boot_json};
var L = {labels};
var ws, exp = 0, ticket = '', busy = false;
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
  append('\\n' + msg);
  gDead = true;
  document.getElementById('msg').disabled = true;
  document.getElementById('pick').disabled = true;
  document.getElementById('gtog').disabled = true;
  try {{ ws && ws.close(); }} catch (e) {{}}
}}
function append(s) {{
  var log = document.getElementById('log');
  log.appendChild(document.createTextNode(s));
  log.scrollTop = log.scrollHeight;
  maybePreview(s);
}}
function appendImg(src, alt) {{
  var log = document.getElementById('log');
  var img = document.createElement('img');
  img.className = 'preview';
  img.alt = alt || 'image';
  img.src = src;
  log.appendChild(img);
  log.appendChild(document.createTextNode('\\n'));
  log.scrollTop = log.scrollHeight;
}}
function maybePreview(chunk) {{
  var m = /gui-open\\s+download\\s+(https?:\\/\\/\\S+)\\s+([A-Z0-9]{{6}})/i.exec(chunk);
  if (!m) return;
  var url = m[1], key = m[2].toUpperCase();
  var path = url.replace(/^https?:\\/\\/[^/]+/i, '');
  var parts = path.split('/').filter(Boolean);
  if (parts.length < 2 || parts[0] !== 'download') return;
  var token = parts[1];
  fetch('/download/' + token + '/ticket', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{key: key}})
  }}).then(function (r) {{ return r.json(); }}).then(function (j) {{
    if (!j || !j.preview) return;
    appendImg(j.preview, j.filename || 'image');
  }}).catch(function () {{}});
}}
function proto() {{ return location.protocol === 'https:' ? 'wss:' : 'ws:'; }}
function connect(tkt) {{
  ticket = tkt;
  ws = new WebSocket(proto() + '//' + location.host + '/guest/' + boot.token + '/ws?k=' + encodeURIComponent(ticket));
  ws.onopen = function () {{ cmdGame('show'); }};
  ws.onmessage = function (ev) {{
    var m;
    try {{ m = JSON.parse(ev.data); }} catch (e) {{ return; }}
    if (m.t === 'line') {{ append(m.s); ingestGame(m.s); }}
    if (m.t === 'hello' && m.exp) exp = m.exp;
    if (m.t === 'end') over(m.why === 'timeout' ? L.expired : L.used);
  }};
  ws.onclose = function () {{
    if (exp && Date.now()/1000 < exp) over(L.used);
  }};
}}
function sendImage(file) {{
  if (!file || !ticket || busy) return;
  if (file.size > boot.maxImage) {{ append('\\n' + L.too_big + '\\n'); return; }}
  if (file.type && file.type.indexOf('image/') !== 0) {{ append('\\n' + L.not_image + '\\n'); return; }}
  busy = true;
  append('\\n' + L.uploading + '\\n');
  var localUrl = '';
  try {{ localUrl = URL.createObjectURL(file); appendImg(localUrl, file.name || 'image'); }} catch (e) {{}}
  var fd = new FormData();
  fd.append('file', file, file.name || 'image.jpg');
  fetch('/guest/' + boot.token + '/image?k=' + encodeURIComponent(ticket) + '&lang=' + encodeURIComponent(boot.lang), {{
    method: 'POST',
    body: fd
  }}).then(function (r) {{ return r.json().then(function (j) {{ return {{ok: r.ok, j: j}}; }}); }})
    .then(function (x) {{
      busy = false;
      if (!x.ok || !x.j.ok) {{
        var err = (x.j && x.j.error) || L.upload_fail;
        if (x.j && x.j.code === 'not_image') err = L.not_image;
        if (x.j && x.j.code === 'too_big') err = L.too_big;
        if (x.j && x.j.code === 'file_off') err = L.file_off;
        append(err + '\\n');
        return;
      }}
      append(L.upload_ok + '\\n');
    }}).catch(function () {{
      busy = false;
      append(L.upload_fail + '\\n');
    }});
}}
document.getElementById('go').onclick = function () {{
  var remark = (document.getElementById('remark') && document.getElementById('remark').value) || '';
  fetch('/guest/' + boot.token + '/join?lang=' + encodeURIComponent(boot.lang), {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{remark: remark}})
  }}).then(function (r) {{ return r.json().then(function (j) {{ return {{ok: r.ok, j: j}}; }}); }})
    .then(function (x) {{
      if (!x.ok || !x.j.ok) {{
        document.getElementById('err').textContent = (x.j && x.j.error) || L.fail;
        return;
      }}
      exp = x.j.exp;
      document.getElementById('who').textContent = L.you + ' ' + (x.j.label || x.j.nick) + ' · #' + boot.room;
      document.getElementById('gate').style.display = 'none';
      document.getElementById('chat').style.display = 'flex';
      connect(x.j.ticket);
      tick();
    }}).catch(function () {{
      document.getElementById('err').textContent = L.fail;
    }});
}};
document.getElementById('pick').onclick = function () {{
  document.getElementById('pic').click();
}};
document.getElementById('pic').onchange = function () {{
  var f = this.files && this.files[0];
  this.value = '';
  if (f) sendImage(f);
}};
document.addEventListener('paste', function (e) {{
  if (!ticket || !e.clipboardData) return;
  var items = e.clipboardData.items || [];
  for (var i = 0; i < items.length; i++) {{
    if (items[i].type && items[i].type.indexOf('image/') === 0) {{
      var f = items[i].getAsFile();
      if (f) {{ e.preventDefault(); sendImage(f); return; }}
    }}
  }}
}});
document.getElementById('row').onsubmit = function (e) {{
  e.preventDefault();
  var box = document.getElementById('msg');
  var s = box.value;
  if (!s || !ws || ws.readyState !== 1) return;
  ws.send(JSON.stringify({{t: 'msg', s: s}}));
  box.value = '';
}};
setInterval(tick, 250);
{_GUEST_GAME_JS}
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
    if len(parts) != 3 or parts[0] != "guest":
        return False
    if parts[2] == "join":
        return _handle_guest_join(handler, parts[1])
    if parts[2] == "image":
        return _handle_guest_image(handler, parts[1])
    return False


def _handle_guest_join(handler: "BaseHTTPRequestHandler", token: str) -> bool:
    lang = _lang(handler)
    text = _TEXTS[lang]
    body = {}
    if hasattr(handler, "_read_json_body"):
        raw = handler._read_json_body()  # type: ignore[attr-defined]
        if isinstance(raw, dict):
            body = raw
    link, err = guest_sharing.guest_store.open_once(token)
    if link is None:
        _send_json(handler, 410, {"ok": False, "error": text.get(err, text["used"])})
        return True
    link.locale = lang
    link.remark = guest_sharing.sanitize_remark(str(body.get("remark") or ""))
    _schedule_kick(link)
    _send_json(
        handler,
        200,
        {
            "ok": True,
            "ticket": link.ws_ticket,
            "exp": int(link.chat_expires_at),
            "nick": link.nick,
            "label": guest_sharing.display_nick(link.nick, link.remark),
            "room": link.room,
        },
    )
    return True


def _handle_guest_image(handler: "BaseHTTPRequestHandler", token: str) -> bool:
    lang = _lang(handler)
    text = _TEXTS[lang]
    ticket = ((parse_qs(urlparse(handler.path).query or "").get("k") or [""])[0] or "").strip()
    link, err = guest_sharing.guest_store.ws_ok(token, ticket)
    if link is None:
        _send_json(
            handler,
            403,
            {"ok": False, "code": err or "gone", "error": text.get(err, text["used"])},
        )
        return True
    if _publish_guest_image is None:
        _send_json(
            handler,
            503,
            {"ok": False, "code": "file_off", "error": text["file_off"]},
        )
        return True
    content_type = handler.headers.get("Content-Type", "")
    if not content_type.startswith("multipart/form-data"):
        _send_json(handler, 400, {"ok": False, "code": "bad", "error": text["upload_fail"]})
        return True
    try:
        form = cgi.FieldStorage(
            fp=handler.rfile,
            headers=handler.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": content_type,
            },
        )
    except Exception:
        _send_json(handler, 400, {"ok": False, "code": "bad", "error": text["upload_fail"]})
        return True
    if "file" not in form:
        _send_json(handler, 400, {"ok": False, "code": "empty", "error": text["upload_fail"]})
        return True
    file_item = form["file"]
    if not getattr(file_item, "file", None):
        _send_json(handler, 400, {"ok": False, "code": "empty", "error": text["upload_fail"]})
        return True
    raw = file_item.file.read(guest_sharing.MAX_IMAGE_BYTES + 1)
    if not raw:
        _send_json(handler, 400, {"ok": False, "code": "empty", "error": text["upload_fail"]})
        return True
    if len(raw) > guest_sharing.MAX_IMAGE_BYTES:
        _send_json(handler, 413, {"ok": False, "code": "too_big", "error": text["too_big"]})
        return True
    filename = getattr(file_item, "filename", None) or "image.jpg"
    if not guest_sharing.looks_like_image(raw, filename):
        _send_json(
            handler,
            400,
            {"ok": False, "code": "not_image", "error": text["not_image"]},
        )
        return True
    nick = link.nick or guest_sharing.random_guest_nick()
    ok, code = _publish_guest_image(nick, link.room, filename, raw)
    if not ok:
        msg = text.get(code) or text["upload_fail"]
        status = 413 if code == "too_big" else 400 if code == "not_image" else 503
        _send_json(handler, status, {"ok": False, "code": code or "fail", "error": msg})
        return True
    _send_json(handler, 200, {"ok": True})
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
        nick = (
            attach(
                gconn,
                nick,
                link.room,
                link.locale,
                minutes=link.minutes,
                remark=link.remark,
            )
            or nick
        )
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
