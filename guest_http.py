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
_publish_guest_voice = None


def bind_chat(
    *,
    attach_guest,
    process_client_line,
    remove_client,
    publish_guest_image=None,
    publish_guest_voice=None,
) -> None:
    global _attach_guest, _process_client_line, _remove_client
    global _publish_guest_image, _publish_guest_voice
    _attach_guest = attach_guest
    _process_client_line = process_client_line
    _remove_client = remove_client
    _publish_guest_image = publish_guest_image
    _publish_guest_voice = publish_guest_voice


_TEXTS = {
    "zh": {
        "title": "SSHChat 临时聊天",
        "room": "房间",
        "you": "你是",
        "enter": "进入聊天",
        "send": "发送",
        "pic": "图片",
        "voice": "语音",
        "voice_rec": "松开",
        "hint": "只能打开一次，进入后 {minutes} 分钟。页面倒计时显示分和秒。系统昵称随机；可填备注，聊天里显示为 ~g…（备注）。可发文字、图片和语音，并用游戏区下棋。最小化或切走浏览器不会提前结束；同一标签刷新可恢复。不要关掉这个标签。",
        "remark": "备注（可选）",
        "remark_ph": "怎么称呼你",
        "game": "游戏",
        "gclose": "收起",
        "gjoin": "加入",
        "gshow": "刷新棋盘",
        "gresign": "认输",
        "gresign_sent": "已发送认输…",
        "gpass": "停一手",
        "gnew": "开局",
        "ghint_stone": "点空位落子",
        "ghint_piece": "先点己方棋子，再点目标格",
        "ghint_dark": "点问号翻面；或先点己子再点目标",
        "ghint_card": "社交/牌类请看聊天，在输入框操作",
        "gpromo": "升变选择",
        "gpromo_q": "后",
        "gpromo_r": "车",
        "gpromo_b": "象",
        "gpromo_n": "马",
        "gpromo_cancel": "取消",
        "games": [
            ["gomoku", "五子棋"],
            ["reversi", "黑白棋"],
            ["xiangqi", "象棋"],
            ["chess", "国际象棋"],
            ["go", "围棋"],
            ["darkchess", "暗棋"],
            ["doushou", "斗兽棋"],
            ["niutou", "牛头王"],
        ],
        "gstart": "开始",
        "grow": "行",
        "ghand": "手牌",
        "ghint_niutou": "点手牌出牌",
        "ghint_niutou_row": "这张牌比所有行尾都小，点一行吃掉",
        "gended": "对局已结束，可在下方开新局",
        "used": "这个链接已经用过（或刚刷新过），不能再打开。请让对方重新发一条 /guest。",
        "expired": "临时聊天已结束。",
        "gone": "链接无效或已过期。请让对方重新发一条 /guest。",
        "left": "剩余",
        "placeholder": "说点什么…",
        "fail": "进不去。",
        "uploading": "正在发送图片…",
        "upload_ok": "图片已发送。",
        "upload_fail": "图片发送失败。",
        "voice_uploading": "正在发送语音…",
        "voice_ok": "语音已发送。",
        "voice_fail": "语音发送失败。",
        "voice_short": "录音太短。",
        "voice_denied": "需要麦克风权限才能发语音。",
        "voice_unsupported": "当前浏览器不支持录音。",
        "not_image": "只能发送图片（jpg/png/gif/webp）。",
        "not_audio": "只能发送语音（webm/ogg/m4a/wav/mp3）。",
        "too_big": "文件太大（图片最多 8MB，语音最多 4MB）。",
        "file_off": "文件服务未启用，无法发图或语音。",
        "joining": "正在进入…",
        "rejoin": "连接中断，正在重连…",
    },
    "en": {
        "title": "SSHChat guest chat",
        "room": "Room",
        "you": "You are",
        "enter": "Enter chat",
        "send": "Send",
        "pic": "Image",
        "voice": "Talk",
        "voice_rec": "Send",
        "hint": "One open only. After you enter you have {minutes} minutes. The countdown shows minutes and seconds. Your nick is random; an optional remark shows as ~g… (remark). Text, images, voice notes, and the game panel are allowed. Minimizing or switching away does not end it early; a reload in this same tab can resume. Do not close this tab.",
        "remark": "Remark (optional)",
        "remark_ph": "What should we call you",
        "game": "Games",
        "gclose": "Close",
        "gjoin": "Join",
        "gshow": "Refresh",
        "gresign": "Resign",
        "gresign_sent": "Resign sent…",
        "gpass": "Pass",
        "gnew": "New",
        "ghint_stone": "Tap an empty point to move",
        "ghint_piece": "Tap your piece, then the destination",
        "ghint_dark": "Tap ? to flip, or tap a piece then a destination",
        "ghint_card": "Social/card games: follow chat and type in the box",
        "gpromo": "Promote to",
        "gpromo_q": "Queen",
        "gpromo_r": "Rook",
        "gpromo_b": "Bishop",
        "gpromo_n": "Knight",
        "gpromo_cancel": "Cancel",
        "games": [
            ["gomoku", "Gomoku"],
            ["reversi", "Reversi"],
            ["xiangqi", "Xiangqi"],
            ["chess", "Chess"],
            ["go", "Go"],
            ["darkchess", "Dark chess"],
            ["doushou", "Jungle"],
            ["niutou", "6 Nimmt!"],
        ],
        "gstart": "Start",
        "grow": "Row",
        "ghand": "Hand",
        "ghint_niutou": "Tap a card in your hand",
        "ghint_niutou_row": "Lower than every row end — tap a row to take it",
        "gended": "Game over — start a new game below",
        "used": "This link was already used (or you refreshed). It cannot be opened again. Ask them to send /guest once more.",
        "expired": "Guest chat has ended.",
        "gone": "This link is invalid or expired. Ask them to send /guest once more.",
        "left": "Left",
        "placeholder": "Message…",
        "fail": "Could not join.",
        "uploading": "Sending image…",
        "upload_ok": "Image sent.",
        "upload_fail": "Could not send image.",
        "voice_uploading": "Sending voice…",
        "voice_ok": "Voice sent.",
        "voice_fail": "Could not send voice.",
        "voice_short": "Recording too short.",
        "voice_denied": "Microphone permission is required for voice notes.",
        "voice_unsupported": "This browser cannot record audio.",
        "not_image": "Images only (jpg/png/gif/webp).",
        "not_audio": "Voice only (webm/ogg/m4a/wav/mp3).",
        "too_big": "File too large (images max 8MB, voice max 4MB).",
        "file_off": "File service is off; cannot send images or voice.",
        "joining": "Entering…",
        "rejoin": "Disconnected, reconnecting…",
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


def _ui_pack(lang: str) -> dict:
    """Client strings for one locale (both packs are embedded for live switching)."""
    text = _TEXTS[lang]
    return {
        "title": text["title"],
        "room": text["room"],
        "enter": text["enter"],
        "send": text["send"],
        "pic": text["pic"],
        "placeholder": text["placeholder"],
        "hint": text["hint"],
        "remark": text["remark"],
        "remark_ph": text["remark_ph"],
        "fail": text["fail"],
        "expired": text["expired"],
        "used": text["used"],
        "left": text["left"],
        "you": text["you"],
        "uploading": text["uploading"],
        "upload_ok": text["upload_ok"],
        "upload_fail": text["upload_fail"],
        "voice_uploading": text["voice_uploading"],
        "voice_ok": text["voice_ok"],
        "voice_fail": text["voice_fail"],
        "voice_short": text["voice_short"],
        "voice_denied": text["voice_denied"],
        "voice_unsupported": text["voice_unsupported"],
        "voice": text["voice"],
        "voice_rec": text["voice_rec"],
        "not_image": text["not_image"],
        "not_audio": text["not_audio"],
        "too_big": text["too_big"],
        "file_off": text["file_off"],
        "game": text["game"],
        "gclose": text["gclose"],
        "gjoin": text["gjoin"],
        "gshow": text["gshow"],
        "gresign": text["gresign"],
        "gresign_sent": text["gresign_sent"],
        "gpass": text["gpass"],
        "gnew": text["gnew"],
        "ghint_stone": text["ghint_stone"],
        "ghint_piece": text["ghint_piece"],
        "ghint_dark": text["ghint_dark"],
        "ghint_card": text["ghint_card"],
        "gpromo": text["gpromo"],
        "gpromo_q": text["gpromo_q"],
        "gpromo_r": text["gpromo_r"],
        "gpromo_b": text["gpromo_b"],
        "gpromo_n": text["gpromo_n"],
        "gpromo_cancel": text["gpromo_cancel"],
        "gstart": text["gstart"],
        "grow": text["grow"],
        "ghand": text["ghand"],
        "ghint_niutou": text["ghint_niutou"],
        "ghint_niutou_row": text["ghint_niutou_row"],
        "gended": text["gended"],
        "joining": text["joining"],
        "rejoin": text["rejoin"],
        "games": text["games"],
    }


def _closed_page(handler: "BaseHTTPRequestHandler", lang: str, reason: str) -> None:
    text = _TEXTS[lang]
    msg = text.get(reason) or text["gone"]
    page = (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
        f"<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{html.escape(text['title'])}</title></head>"
        f"<body style=\"font-family:sans-serif;padding:1.5rem;background:#fff;color:#111\">"
        f"<p>{html.escape(msg)}</p></body></html>"
    )
    _send_html(handler, page, 410)


# Board UI for guests. Not an f-string — braces are JS.
_GUEST_GAME_JS = r"""
var gameBuf = [], snap = null, fromCell = null, gOpen = false, gDead = false, pendingPromo = null;
var lastResult = '';
var HEADERS = [
  ['xiangqi', /^xiangqi (?:对局)?[（(]([^）)]*)[）)]/],
  ['xiangqi', /^xiangqi 对局状态[：:]\s*(\S+)/],
  ['chess', /^chess (?:对局)?[（(]([^）)]*)[）)]/],
  ['chess', /^chess 对局状态[：:]\s*(\S+)/],
  ['go', /^go (?:对局)?[（(]([^）)]*)[）)]/],
  ['gomoku', /^gomoku (?:对局)?[（(]([^）)]*)[）)]/],
  ['gomoku', /^gomoku 对局状态[：:]\s*(\S+)/],
  ['doushou', /^doushou (?:对局)?[（(]([^）)]*)[）)]/],
  ['darkchess', /^darkchess (?:对局)?[（(]([^）)]*)[）)]/],
  ['reversi', /^reversi game [（(]([^）)]*)[）)]/],
  ['niutou', /^(?:牛头王 状态|6 Nimmt! status)[：:]\s*(.+)$/],
  ['niutou', /^牛头王开始/],
  ['niutou', /^6 Nimmt! start/]
];
var END_RE = /对局结束|认负|resigns?|获胜|和棋|1-0|0-1|1\/2|game over|\bdraw\b|wins?\b|离开 —|离开 —|终止了对局|取消了对局/i;
/* /game end removes the room game — not the same as resign's "ended" board. */
var ROOM_CLEARED_RE = /结束了本房的对局/;
function isEnded(s) {
  if (!s) return false;
  if (s.ended) return true;
  var st = String(s.state || '');
  return /^ended$/i.test(st) || /结束/.test(st);
}
function isStoneKind(k) {
  return k === 'go' || k === 'gomoku' || k === 'reversi';
}
function clearRoomGame(reason) {
  snap = null;
  fromCell = null;
  lastResult = reason || '';
  hidePromo();
  if (gOpen) renderGame();
  else {
    var neu = document.getElementById('gnew');
    if (neu) neu.className = '';
    var stat = document.getElementById('gstat');
    if (stat) stat.textContent = reason || (L.gended || '');
  }
}
function gameNote(msg) {
  if (!msg) return;
  var t = String(msg).replace(/^\s+|\s+$/g, '');
  if (!t) return;
  var stat = document.getElementById('gstat');
  var hint = document.getElementById('ghint');
  if (stat) stat.textContent = t;
  if (hint && gOpen) hint.textContent = t;
}
function markGameOver(text) {
  var t = String(text || '').replace(/^\s+|\s+$/g, '');
  if (!t || !END_RE.test(t)) return false;
  lastResult = t;
  if (snap) {
    snap.ended = true;
    snap.result = t;
  }
  gameNote(t);
  var neu = document.getElementById('gnew');
  if (neu) neu.className = '';
  return true;
}
function maybeGameNote(text) {
  /* Fullscreen board covers chat — surface resign / seat / end on the board chrome. */
  if (!text) return;
  var t = String(text).replace(/^\s+|\s+$/g, '');
  if (!t) return;
  if (ROOM_CLEARED_RE.test(t)) {
    clearRoomGame(t);
    return;
  }
  if (!gOpen) return;
  if (markGameOver(t)) return;
  if (/不是对局|无需认|没有进行中|无法识别|无法走|不是你的回合|You are not|not one of the players|has not started|already ended/.test(t)) {
    gameNote(t);
  }
}
function cmdGame(rest) {
  if (!ws || ws.readyState !== 1 || gDead) {
    gameNote(L.rejoin || L.fail);
    return;
  }
  if (rest && rest.indexOf('new ') === 0) {
    lastResult = '';
    hidePromo();
    fromCell = null;
    if (snap) { snap.ended = false; snap.result = ''; }
  }
  ws.send(JSON.stringify({t: 'msg', s: rest ? ('/game ' + rest) : '/game'}));
  if (rest === 'resign') gameNote(L.gresign_sent || L.gresign);
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
  var sawEnd = false;
  var clearedMsg = '';
  for (var i = 0; i < parts.length; i++) {
    var line = parts[i];
    if (!line) continue;
    var m = /^\[#[^\]]+\] \[\*\] (.*)$/.exec(line);
    var body = m ? m[1] : line.replace(/^\[\*\]\s?/, '');
    gameBuf.push(body);
    if (ROOM_CLEARED_RE.test(body)) clearedMsg = body;
    if (markGameOver(body)) sawEnd = true;
  }
  if (gameBuf.length > 400) gameBuf = gameBuf.slice(-300);
  if (clearedMsg) {
    clearRoomGame(clearedMsg);
    return;
  }
  var next = parseGame(gameBuf);
  if (next) {
    if (lastResult && isEnded(next) && !next.result) next.result = lastResult;
    snap = next;
    fromCell = null;
    hidePromo();
    renderGame();
  } else if (sawEnd && snap) {
    snap.ended = true;
    if (lastResult) snap.result = lastResult;
    renderGame();
  }
}
function findRecentResult(lines) {
  for (var i = lines.length - 1; i >= 0 && i >= lines.length - 40; i--) {
    var t = String(lines[i] || '').replace(/^\s+|\s+$/g, '');
    if (END_RE.test(t)) return t;
  }
  return '';
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
  var s = {kind: kind, state: state, header: block[0].trim(), board: null, flipped: false, ended: false, result: ''};
  if (/^ended$/i.test(state) || /结束/.test(state)) s.ended = true;
  if (kind === 'gomoku' || kind === 'go' || kind === 'reversi') parseStones(s, block);
  else if (kind === 'chess') parseChess(s, block);
  else if (kind === 'xiangqi') parseXiangqi(s, block);
  else if (kind === 'darkchess') parseDark(s, block);
  else if (kind === 'doushou') parseDoushou(s, block);
  else if (kind === 'niutou') parseNiutou(s, block);
  if (s.ended) s.result = findRecentResult(lines) || lastResult || '';
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
function parseNiutou(s, block) {
  s.rows = [[], [], [], []];
  s.rowBulls = [0, 0, 0, 0];
  s.hand = [];
  s.mustRow = false;
  s.seats = [];
  var rowRe = /^(?:第(\d)行|Row (\d))[：:]\s*([\d\s]+?)\s*[（(](?:牛头|bulls)=(\d+)[）)]/;
  var seatRe = /^-\s+(\S+?)[：:]\s*(?:牛头|bulls)=(\d+)[，,]\s*(?:手牌数|cards)=(\d+)/;
  var handRe = /^(?:你的手牌|Your hand)[：:]\s*(.*)$/;
  for (var i = 1; i < block.length; i++) {
    var t = block[i].trim();
    var m = rowRe.exec(t);
    if (m) {
      var idx = +(m[1] || m[2]) - 1;
      if (idx >= 0 && idx < 4) {
        s.rows[idx] = m[3].trim().split(/\s+/).map(Number).filter(function (n) { return n === n; });
        s.rowBulls[idx] = +m[4];
      }
      continue;
    }
    m = seatRe.exec(t);
    if (m) { s.seats.push(m[1] + ' 🐂' + m[2]); continue; }
    m = handRe.exec(t);
    if (m) {
      s.hand = m[1].trim().split(/\s+/).filter(function (x) { return /^\d+$/.test(x); });
      continue;
    }
    if (t.indexOf('必须选择一行') >= 0 || /must choose a row/i.test(t) || /must pick a row/i.test(t))
      s.mustRow = true;
  }
}
function ntwBulls(n) {
  n = +n;
  if (n === 55) return 7;
  if (n % 11 === 0) return 5;
  if (n % 10 === 0) return 3;
  if (n % 5 === 0) return 2;
  return 1;
}
function ntwCard(n, onClick) {
  var b = document.createElement('button');
  b.type = 'button';
  b.className = 'ntw';
  b.textContent = n + ' 🐂' + ntwBulls(n);
  if (onClick) b.onclick = onClick;
  else b.disabled = true;
  return b;
}
function renderNiutou() {
  var boardEl = document.getElementById('gboard');
  var hint = document.getElementById('ghint');
  boardEl.className = 'ntw';
  var s = snap;
  if (s.seats && s.seats.length) {
    var seats = document.createElement('div');
    seats.className = 'ntw-line';
    seats.textContent = s.seats.join(' · ');
    boardEl.appendChild(seats);
  }
  for (var i = 0; i < 4; i++) {
    var line = document.createElement('div');
    line.className = 'ntw-line';
    var lab = document.createElement('span');
    lab.textContent = (L.grow || '行') + (i + 1);
    line.appendChild(lab);
    (s.rows[i] || []).forEach(function (n) { line.appendChild(ntwCard(n)); });
    var bulls = document.createElement('span');
    bulls.textContent = '🐂' + (s.rowBulls[i] || 0);
    line.appendChild(bulls);
    if (s.mustRow) {
      (function (idx) {
        var take = document.createElement('button');
        take.type = 'button';
        take.className = 'ntw ntw-take';
        take.textContent = L.grow + idx;
        take.onclick = function () { cmdGame('move row ' + idx); };
        line.appendChild(take);
      })(i + 1);
    }
    boardEl.appendChild(line);
  }
  var hand = document.createElement('div');
  hand.className = 'ntw-line';
  var hl = document.createElement('span');
  hl.textContent = L.ghand || '手牌';
  hand.appendChild(hl);
  if (!s.mustRow) {
    (s.hand || []).forEach(function (n) {
      hand.appendChild(ntwCard(n, function () { cmdGame('move pick ' + n); }));
    });
  }
  boardEl.appendChild(hand);
  hint.textContent = s.mustRow ? L.ghint_niutou_row : L.ghint_niutou;
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
  if (isEnded(s)) {
    gameNote(s.result || lastResult || L.gended || s.header);
    return;
  }
  var b = s.board, tok = b.cells[r * b.cols + c] || '';
  if (s.kind === 'go' || s.kind === 'gomoku' || s.kind === 'reversi') {
    if (tok && s.kind !== 'reversi') return;
    var rc = srvRC(s, r, c);
    cmdGame('move ' + rc[0] + ' ' + rc[1]);
    return;
  }
  if (s.kind === 'chess') {
    if (pendingPromo) return;
    if (!fromCell) {
      if (!tok) return;
      fromCell = [r, c]; renderGame(); return;
    }
    var fr = fromCell[0], fc = fromCell[1];
    var a = chessSq(s, fr, fc), z = chessSq(s, r, c);
    var fromTok = b.cells[fr * b.cols + fc] || '';
    fromCell = null;
    if (a !== z) {
      var mv = a + z;
      var whitePawn = fromTok === '♙' || fromTok === 'P';
      var blackPawn = fromTok === '♟' || fromTok === 'p';
      if ((whitePawn && z.charAt(1) === '8') || (blackPawn && z.charAt(1) === '1')) {
        renderGame();
        showPromo(mv, whitePawn);
        return;
      }
      cmdGame('move ' + mv);
    }
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
function hidePromo() {
  pendingPromo = null;
  var box = document.getElementById('gpromo');
  if (!box) return;
  box.style.display = 'none';
  box.innerHTML = '';
}
function showPromo(mv, white) {
  pendingPromo = {mv: mv, white: !!white};
  var box = document.getElementById('gpromo');
  if (!box) {
    cmdGame('move ' + mv + 'q');
    return;
  }
  box.style.display = 'flex';
  box.innerHTML = '';
  var lab = document.createElement('span');
  lab.textContent = L.gpromo || 'Promote';
  box.appendChild(lab);
  var opts = [
    ['q', L.gpromo_q || 'Queen'],
    ['r', L.gpromo_r || 'Rook'],
    ['b', L.gpromo_b || 'Bishop'],
    ['n', L.gpromo_n || 'Knight']
  ];
  opts.forEach(function (o) {
    box.appendChild(btn(o[1], function () {
      var uci = mv + o[0];
      hidePromo();
      cmdGame('move ' + uci);
    }));
  });
  box.appendChild(btn(L.gpromo_cancel || 'Cancel', function () {
    hidePromo();
    fromCell = null;
    renderGame();
  }));
  gameNote(L.gpromo || '');
  try { fitBoard(); } catch (e) {}
}
function btn(label, fn) {
  var b = document.createElement('button');
  b.type = 'button'; b.textContent = label;
  /* Kindle Silk often drops click; touchend + short lock avoids double-fire. */
  var lock = 0;
  function run(ev) {
    var now = Date.now();
    if (now < lock) return;
    lock = now + 450;
    try { if (ev && ev.preventDefault) ev.preventDefault(); } catch (e) {}
    fn();
  }
  b.onclick = run;
  b.ontouchend = run;
  return b;
}
function cellLabel(tok) {
  if (tok === 'B') return '●';
  if (tok === 'W') return '○';
  var body = (tok.charAt(0) === 'r' || tok.charAt(0) === 'b') ? tok.slice(1) : tok;
  if (boot.lang !== 'en' || !snap) return body;
  var m = snap.kind === 'doushou' ? {
    '狮':'L','虎':'T','豹':'P','狼':'W','狗':'D','猫':'C','鼠':'R','象':'E',
    '河':'RV','红穴':'rD','黑穴':'bD','红陷':'rT','黑陷':'bT'
  } : snap.kind === 'darkchess' ? {
    '将':'G','士':'A','象':'E','车':'R','马':'H','炮':'C','卒':'S'
  } : snap.kind === 'xiangqi' ? {
    '帅':'K','将':'K','仕':'A','士':'A','相':'E','象':'E','车':'R','马':'H','炮':'C','兵':'P','卒':'P'
  } : null;
  return (m && m[body]) || body;
}
function setGameOpen(on) {
  gOpen = !!on;
  var body = document.getElementById('gbody');
  var game = document.getElementById('game');
  var t = document.getElementById('gtog');
  if (body) body.style.display = gOpen ? 'block' : 'none';
  if (game) {
    if (gOpen) game.className = 'gfs';
    else game.className = '';
  }
  if (t) t.textContent = gOpen ? (L.gclose || L.game) : L.game;
  if (gOpen) setTimeout(fitBoard, 0);
}
function chromeH() {
  var n = 0, ids = ['ghead', 'gacts', 'gnew', 'gpromo', 'ghint'];
  for (var i = 0; i < ids.length; i++) {
    var el = document.getElementById(ids[i]);
    if (!el || el.className === 'hid' || el.style.display === 'none') continue;
    n += el.offsetHeight || 0;
  }
  return n + 16;
}
function boardPadX() {
  var body = document.getElementById('gbody');
  if (!body) return 16;
  var cs = window.getComputedStyle ? getComputedStyle(body) : null;
  if (!cs) return 16;
  return (parseFloat(cs.paddingLeft) || 0) + (parseFloat(cs.paddingRight) || 0);
}
function boardCellSize(b) {
  var ww = window.innerWidth || document.documentElement.clientWidth || 320;
  var wh = window.innerHeight || document.documentElement.clientHeight || 480;
  /* gbody 左右 padding 未扣时，棋盘会略宽于可视区，手机端右侧被 overflow 裁掉一点 */
  var availW = Math.max(1, ww - boardPadX() - 2);
  var cell = Math.floor(Math.min(availW / b.cols, (wh - chromeH()) / b.rows));
  return cell < 18 ? 18 : cell;
}
function stoneStars(n) {
  if (n === 19) return [3, 9, 15];
  if (n === 15) return [3, 7, 11];
  if (n === 9) return [2, 4, 6];
  return [];
}
function drawStonesCanvas(canvas, b, cell) {
  var ctx = canvas.getContext('2d');
  if (!ctx) return;
  var w = b.cols * cell, h = b.rows * cell;
  canvas.width = w;
  canvas.height = h;
  canvas.style.width = w + 'px';
  canvas.style.height = h + 'px';
  ctx.fillStyle = '#c4a673';
  ctx.fillRect(0, 0, w, h);
  ctx.strokeStyle = '#5d4037';
  ctx.lineWidth = 1;
  var r, c;
  for (r = 0; r < b.rows; r++) {
    var y = (r + 0.5) * cell;
    ctx.beginPath();
    ctx.moveTo(0.5 * cell, y);
    ctx.lineTo((b.cols - 0.5) * cell, y);
    ctx.stroke();
  }
  for (c = 0; c < b.cols; c++) {
    var x = (c + 0.5) * cell;
    ctx.beginPath();
    ctx.moveTo(x, 0.5 * cell);
    ctx.lineTo(x, (b.rows - 0.5) * cell);
    ctx.stroke();
  }
  var stars = stoneStars(b.rows);
  ctx.fillStyle = '#3e2723';
  for (r = 0; r < stars.length; r++) for (c = 0; c < stars.length; c++) {
    ctx.beginPath();
    ctx.arc((stars[c] + 0.5) * cell, (stars[r] + 0.5) * cell, cell * 0.08, 0, Math.PI * 2);
    ctx.fill();
  }
  for (r = 0; r < b.rows; r++) for (c = 0; c < b.cols; c++) {
    var tok = b.cells[r * b.cols + c] || '';
    if (!tok) continue;
    var cx = (c + 0.5) * cell, cy = (r + 0.5) * cell, rad = cell * 0.42;
    ctx.beginPath();
    ctx.arc(cx, cy, rad, 0, Math.PI * 2);
    ctx.fillStyle = tok === 'B' ? '#111' : '#fff';
    ctx.fill();
    if (tok === 'W') {
      ctx.strokeStyle = '#666';
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }
    if (b.last && b.last[r * b.cols + c]) {
      ctx.beginPath();
      ctx.arc(cx, cy, cell * 0.18, 0, Math.PI * 2);
      ctx.strokeStyle = '#e65100';
      ctx.lineWidth = 2;
      ctx.stroke();
    }
  }
}
function hitStoneCell(ev, canvas, b, cell) {
  var rect = canvas.getBoundingClientRect();
  var clientX = (ev.changedTouches && ev.changedTouches[0])
    ? ev.changedTouches[0].clientX
    : ev.clientX;
  var clientY = (ev.changedTouches && ev.changedTouches[0])
    ? ev.changedTouches[0].clientY
    : ev.clientY;
  var x = (clientX - rect.left) * (canvas.width / Math.max(rect.width, 1));
  var y = (clientY - rect.top) * (canvas.height / Math.max(rect.height, 1));
  var c = Math.floor(x / cell), r = Math.floor(y / cell);
  if (r < 0 || c < 0 || r >= b.rows || c >= b.cols) return null;
  return [r, c];
}
function renderStonesBoard(boardEl, b, ended) {
  boardEl.className = 'stones';
  boardEl.style.background = 'transparent';
  var canvas = document.createElement('canvas');
  canvas.id = 'gstone';
  canvas.style.display = 'block';
  canvas.style.margin = '0 auto';
  canvas.style.touchAction = 'manipulation';
  var cell = boardCellSize(b);
  drawStonesCanvas(canvas, b, cell);
  boardEl.style.width = (cell * b.cols) + 'px';
  boardEl.style.height = (cell * b.rows) + 'px';
  boardEl.style.maxHeight = 'none';
  boardEl.appendChild(canvas);
  if (ended) return;
  var lock = 0;
  function tap(ev) {
    var now = Date.now();
    if (now < lock) return;
    lock = now + 450;
    try { if (ev && ev.preventDefault) ev.preventDefault(); } catch (e) {}
    var cellNow = canvas.width / Math.max(b.cols, 1);
    var rc = hitStoneCell(ev, canvas, b, cellNow);
    if (rc) onCell(rc[0], rc[1]);
  }
  canvas.onclick = tap;
  canvas.ontouchend = tap;
}
function fitBoard() {
  if (!gOpen || !snap || !snap.board) return;
  var boardEl = document.getElementById('gboard');
  if (!boardEl || boardEl.className === 'ntw') return;
  var b = snap.board;
  var cell = boardCellSize(b);
  var bw = cell * b.cols, bh = cell * b.rows;
  boardEl.style.width = bw + 'px';
  boardEl.style.height = bh + 'px';
  boardEl.style.maxHeight = 'none';
  if (boardEl.className === 'stones') {
    var canvas = document.getElementById('gstone');
    if (canvas) drawStonesCanvas(canvas, b, cell);
    return;
  }
  var tds = boardEl.getElementsByTagName('td');
  for (var i = 0; i < tds.length; i++) {
    tds[i].style.width = cell + 'px';
    tds[i].style.height = cell + 'px';
  }
  var btns = boardEl.getElementsByTagName('button');
  var fs1 = Math.max(11, Math.floor(cell * 0.58));
  /* 黑陷/红穴 等二字地形：按 Android 缩小字号，避免手机端撑破格子 */
  var fs2 = Math.max(9, Math.floor(cell * 0.36));
  for (var j = 0; j < btns.length; j++) {
    btns[j].style.width = cell + 'px';
    btns[j].style.height = cell + 'px';
    var lab = btns[j].textContent || '';
    btns[j].style.fontSize = (lab.length >= 2 ? fs2 : fs1) + 'px';
  }
}
function renderGame() {
  var keepPromo = pendingPromo;
  hidePromo();
  var acts = document.getElementById('gacts');
  var neu = document.getElementById('gnew');
  var boardEl = document.getElementById('gboard');
  var hint = document.getElementById('ghint');
  var stat = document.getElementById('gstat');
  acts.innerHTML = ''; neu.innerHTML = ''; boardEl.innerHTML = ''; boardEl.className = '';
  boardEl.removeAttribute('style');
  acts.appendChild(btn(L.gjoin, function () { cmdGame('join'); }));
  acts.appendChild(btn(L.gshow, function () { cmdGame('show'); }));
  acts.appendChild(btn(L.gresign, function () {
    cmdGame('resign');
    setTimeout(function () { cmdGame('show'); }, 350);
  }));
  /* Match mobile: go + reversi expose pass (停一手). */
  if (snap && !isEnded(snap) && (snap.kind === 'go' || snap.kind === 'reversi'))
    acts.appendChild(btn(L.gpass, function () { cmdGame('move pass'); }));
  if (snap && (String(snap.state).indexOf('等待') >= 0 || /wait/i.test(snap.state)))
    acts.appendChild(btn(L.gstart, function () { cmdGame('move start'); }));
  (boot.games || []).forEach(function (g) {
    neu.appendChild(btn(g[1], function () { cmdGame('new ' + g[0]); }));
  });
  if (!snap) {
    stat.textContent = '';
    hint.textContent = L.gnew;
    neu.className = '';
    if (keepPromo) showPromo(keepPromo.mv, keepPromo.white);
    return;
  }
  var ended = isEnded(snap);
  var result = snap.result || lastResult || '';
  stat.textContent = ended && result ? result : snap.header;
  if (!gOpen) setGameOpen(true);
  /* Hide new-game picker only while a live game is on the board. */
  neu.className = (!ended && (snap.board || snap.kind === 'niutou')) ? 'hid' : '';
  if (snap.kind === 'niutou') {
    renderNiutou();
    if (ended) {
      gameNote(result || L.gended || snap.header);
      hint.textContent = L.gended || result || snap.header;
    }
    if (keepPromo) showPromo(keepPromo.mv, keepPromo.white);
    return;
  }
  var b = snap.board;
  if (!b) {
    hint.textContent = ended ? (L.gended || result || snap.header) : L.ghint_card;
    neu.className = '';
    if (ended && result) gameNote(result);
    if (keepPromo) showPromo(keepPromo.mv, keepPromo.white);
    return;
  }
  /* Match mobile: go / gomoku / reversi stones sit on line intersections. */
  if (isStoneKind(snap.kind)) {
    renderStonesBoard(boardEl, b, ended);
  } else {
    var table = document.createElement('table');
    table.style.borderCollapse = 'collapse';
    table.style.margin = '0 auto';
    for (var r = 0; r < b.rows; r++) {
      var tr = document.createElement('tr');
      for (var c = 0; c < b.cols; c++) {
        (function (rr, cc) {
          var td = document.createElement('td');
          var cell = document.createElement('button');
          cell.type = 'button';
          var tok = b.cells[rr * b.cols + cc] || '';
          var cls = '';
          if (tok === 'B') cls = 'c-B';
          else if (tok === 'W') cls = 'c-W';
          else if (tok.charAt(0) === 'r') cls = 'c-r';
          else if (tok.charAt(0) === 'b') cls = 'c-b';
          if (b.last && b.last[rr * b.cols + cc]) cls += ' c-last';
          if (!ended && fromCell && fromCell[0] === rr && fromCell[1] === cc) cls += ' c-sel';
          cell.className = cls;
          cell.textContent = cellLabel(tok);
          if (ended) cell.disabled = true;
          else cell.onclick = function () { onCell(rr, cc); };
          td.appendChild(cell);
          tr.appendChild(td);
        })(r, c);
      }
      table.appendChild(tr);
    }
    boardEl.appendChild(table);
  }
  if (ended) {
    gameNote(result || L.gended || snap.header);
    hint.textContent = (L.gended || '') + (result ? ' · ' + result : '');
  } else if (snap.kind === 'darkchess') hint.textContent = L.ghint_dark;
  else if (snap.kind === 'xiangqi' || snap.kind === 'chess' || snap.kind === 'doushou') hint.textContent = L.ghint_piece;
  else hint.textContent = L.ghint_stone;
  if (keepPromo && !ended) showPromo(keepPromo.mv, keepPromo.white);
  setTimeout(fitBoard, 0);
}
(function () {
  var t = document.getElementById('gtog');
  if (t) t.onclick = function () {
    setGameOpen(!gOpen);
    if (gOpen) renderGame();
  };
  var resizeTimer = null;
  window.addEventListener('resize', function () {
    if (resizeTimer) clearTimeout(resizeTimer);
    resizeTimer = setTimeout(fitBoard, 80);
  });
  window.addEventListener('orientationchange', function () { setTimeout(fitBoard, 200); });
})();
"""


def render_guest_page(link: guest_sharing.GuestLink, lang: str) -> str:
    text = _TEXTS[lang]
    minutes = link.minutes or guest_sharing.DEFAULT_MINUTES
    boot = {
        "token": link.token,
        "room": link.room,
        "lang": lang,
        "minutes": minutes,
        "maxImage": guest_sharing.MAX_IMAGE_BYTES,
        "maxVoice": guest_sharing.MAX_VOICE_BYTES,
        "games": text["games"],
    }
    boot_json = json.dumps(boot, ensure_ascii=False)
    packs_json = json.dumps(
        {"zh": _ui_pack("zh"), "en": _ui_pack("en")},
        ensure_ascii=False,
    )
    return f"""<!DOCTYPE html>
<html lang="{'zh-CN' if lang == 'zh' else 'en'}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<meta name="robots" content="noindex">
<title>{html.escape(text['title'])}</title>
<style>
html,body {{ margin:0; height:100%; height:100vh; height:100dvh; overflow:hidden; background:#fff; color:#111; font-family:sans-serif; }}
#app {{ display:flex; flex-direction:column; height:100%; overflow:hidden; }}
#bar {{ padding:.45rem .8rem; background:#f0f0f0; color:#111; border-bottom:1px solid #ccc; display:flex; align-items:center; gap:.45rem; flex-shrink:0; }}
#who {{ flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:14px; }}
#langs {{ display:flex; gap:.25rem; flex-shrink:0; }}
#langs button {{ padding:.25rem .45rem; font-size:12px; }}
#langs button.on {{ background:#111; color:#fff; }}
#left {{ font-variant-numeric:tabular-nums; font-weight:bold; color:#c00; white-space:nowrap; font-size:13px; flex-shrink:0; }}
#gate, #chat {{ flex:1; display:flex; flex-direction:column; min-height:0; }}
#chat {{ display:none; }}
#log {{ flex:1; overflow:auto; padding:.7rem .8rem; display:flex; flex-direction:column; gap:.45rem; }}
#log .bubble {{ max-width:82%; padding:.45rem .7rem; border-radius:14px; word-break:break-word; font-size:15px; line-height:1.4; }}
#log .bubble.them {{ align-self:flex-start; background:#eee; color:#111; }}
#log .bubble.me {{ align-self:flex-end; background:#d4edda; color:#041; }}
#log .bubble .nm {{ font-size:11px; color:#555; margin-bottom:.15rem; }}
#log .bubble.me .nm {{ color:#063; }}
#log .sys {{ align-self:center; max-width:92%; font-size:12px; color:#666; text-align:center; }}
#log img.preview {{ display:block; max-width:min(100%,240px); max-height:200px; margin-top:.3rem; border-radius:8px; }}
#log audio.preview {{ display:block; width:min(100%,240px); margin-top:.25rem; }}
#row {{ display:flex; flex-wrap:nowrap; padding:.4rem .45rem; gap:.35rem; align-items:center; flex-shrink:0; background:#f5f5f5; border-top:1px solid #ccc; }}
#row input[type=text] {{ flex:1 1 auto; padding:.55rem .6rem; font-size:16px; min-width:0; width:0; }}
#gate {{ padding:1.5rem; max-width:28rem; background:#fff; color:#111; }}
#gate p {{ line-height:1.5; color:#333; }}
#gate input {{ width:100%; max-width:16rem; padding:.5rem; font-size:16px; box-sizing:border-box; background:#fff; color:#111; border:1px solid #999; }}
button {{ padding:.55rem .7rem; font-size:15px; }}
#row button {{ flex:0 0 auto; padding:.55rem .55rem; font-size:14px; white-space:nowrap; }}
#voice {{ touch-action:none; user-select:none; -webkit-user-select:none; white-space:nowrap; }}
#voice.rec {{ background:#c62828; color:#fff; }}
.warn {{ color:#a40; }}
#pic {{ display:none; }}
#game {{ background:#fff; color:#111; border-bottom:1px solid #ccc; flex-shrink:0; }}
#ghead {{ display:flex; align-items:center; gap:.6rem; padding:.4rem .7rem; flex-shrink:0; }}
#gstat {{ flex:1; font-size:13px; color:#333; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
#gbody {{ display:none; padding:0 .7rem .7rem; }}
#gacts, #gnew, #gpromo {{ display:flex; flex-wrap:wrap; gap:.35rem; margin-bottom:.45rem; flex-shrink:0; align-items:center; }}
#gpromo {{ display:none; }}
#gacts button, #gnew button, #gpromo button, #gtog {{ padding:.45rem .7rem; font-size:14px; }}
#gboard {{ overflow:auto; max-height:52vh; width:100%; max-width:100%; box-sizing:border-box; background:#ccc; border-collapse:collapse; }}
#gboard td {{ padding:0; }}
#gboard button {{ display:block; box-sizing:border-box; width:100%; height:100%; margin:0; padding:0; font-size:12px; line-height:1; overflow:hidden; white-space:nowrap; touch-action:manipulation; background:#fff; color:#111; border:1px solid #999; }}
#gboard button.c-B {{ background:#111; color:#fff; border-radius:50%; }}
#gboard button.c-W {{ background:#fff; color:#111; border-radius:50%; border:2px solid #111; }}
/* Kindle/e-ink: red/black → white/black fill so sides stay distinct. */
#gboard button.c-r {{ color:#111; background:#fff; border:2px solid #111; font-weight:bold; }}
#gboard button.c-b {{ color:#fff; background:#111; border:2px solid #111; font-weight:bold; }}
#gboard button.c-last {{ outline:2px solid #000; outline-offset:-2px; }}
#gboard button.c-sel {{ outline:3px solid #000; outline-offset:-3px; }}
#gboard.ntw {{ display:block; background:transparent; max-height:none; }}
#gboard.ntw .ntw-line {{ display:block; margin:.3rem 0; }}
#gboard.ntw button.ntw {{ display:inline-block; width:auto; height:auto; min-width:2.5rem; min-height:2.4rem; padding:.15rem .4rem; font-size:12px; background:#fff; color:#111; border-radius:6px; border:1px solid #111; margin:0 .2rem .2rem 0; }}
#gboard.ntw button.ntw-take {{ background:#111; color:#fff; border:1px solid #111; min-width:auto; }}
#ghint {{ font-size:12px; color:#555; margin-top:.35rem; flex-shrink:0; }}
/* Fullscreen game — no media query (Kindle Silk width is unreliable). */
#game.gfs {{
  position:fixed; top:0; right:0; bottom:0; left:0; z-index:30;
  display:block; background:#fff; color:#111; border:none;
  height:100%; height:100vh; width:100%; overflow:hidden;
}}
#game.gfs #ghead {{ position:relative; z-index:1; }}
#game.gfs #gbody {{
  display:block !important; padding:.35rem .4rem .5rem; overflow:hidden;
  height:auto;
}}
#game.gfs #gboard {{ max-height:none; margin:0 auto; overflow:hidden; }}
#gboard.stones {{ background:transparent; margin:0 auto; }}
#gboard.stones canvas {{ display:block; margin:0 auto; }}
#game.gfs #gnew.hid {{ display:none; }}
</style>
</head>
<body>
<div id="app">
  <div id="bar">
    <div id="who">{html.escape(text['title'])} · {html.escape(text['room'])} #{html.escape(link.room)}</div>
    <div id="langs">
      <button type="button" id="langZh" class="{'on' if lang == 'zh' else ''}">中文</button>
      <button type="button" id="langEn" class="{'on' if lang == 'en' else ''}">EN</button>
    </div>
    <div id="left"></div>
  </div>
  <div id="gate">
    <p class="warn" id="gateHint">{html.escape(text['hint'].format(minutes=minutes))}</p>
    <p>
      <label for="remark" id="remarkLab">{html.escape(text['remark'])}</label><br>
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
        <div id="gpromo"></div>
        <div id="gboard"></div>
        <div id="ghint"></div>
      </div>
    </div>
    <div id="log"></div>
    <form id="row">
      <input id="msg" type="text" maxlength="500" placeholder="{html.escape(text['placeholder'])}" autocomplete="off">
      <button type="button" id="voice">{html.escape(text['voice'])}</button>
      <button type="button" id="pick">{html.escape(text['pic'])}</button>
      <input id="pic" type="file" accept="image/jpeg,image/png,image/gif,image/webp,image/*">
      <button type="submit" id="send">{html.escape(text['send'])}</button>
    </form>
  </div>
</div>
<script>
var boot = {boot_json};
var PACKS = {packs_json};
var L = PACKS[boot.lang] || PACKS.en;
var ws, exp = 0, ticket = '', busy = false, ended = false, reconnectTimer = null, toldRejoin = false;
var meLabel = '', meNick = '', pendingSys = [];
function applyLang(lang) {{
  if (!PACKS[lang]) return;
  boot.lang = lang;
  L = PACKS[lang];
  boot.games = L.games;
  document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en';
  document.title = L.title;
  var zhBtn = document.getElementById('langZh');
  var enBtn = document.getElementById('langEn');
  if (zhBtn) zhBtn.className = lang === 'zh' ? 'on' : '';
  if (enBtn) enBtn.className = lang === 'en' ? 'on' : '';
  if (!meLabel) {{
    document.getElementById('who').textContent = L.title + ' · ' + L.room + ' #' + boot.room;
  }} else {{
    document.getElementById('who').textContent = L.you + ' ' + meLabel + ' · #' + boot.room;
  }}
  var hint = document.getElementById('gateHint');
  if (hint) hint.textContent = String(L.hint || '').split('{{minutes}}').join(String(boot.minutes));
  var lab = document.getElementById('remarkLab');
  if (lab) lab.textContent = L.remark;
  var rem = document.getElementById('remark');
  if (rem) rem.placeholder = L.remark_ph;
  var go = document.getElementById('go');
  if (go) go.textContent = L.enter;
  var msg = document.getElementById('msg');
  if (msg) msg.placeholder = L.placeholder;
  var voice = document.getElementById('voice');
  if (voice && voice.className.indexOf('rec') < 0) voice.textContent = L.voice;
  var pick = document.getElementById('pick');
  if (pick) pick.textContent = L.pic;
  var send = document.getElementById('send');
  if (send) send.textContent = L.send;
  var tog = document.getElementById('gtog');
  if (tog) tog.textContent = gOpen ? (L.gclose || L.game) : L.game;
  try {{ tick(); }} catch (e) {{}}
  try {{ if (typeof renderGame === 'function') renderGame(); }} catch (e) {{}}
  try {{
    var u = new URL(location.href);
    u.searchParams.set('lang', lang);
    history.replaceState(null, '', u.pathname + u.search);
  }} catch (e) {{}}
}}
function sessionKey() {{ return 'sshchat-guest-' + boot.token; }}
function saveSession() {{
  if (!ticket || !exp) return;
  try {{
    sessionStorage.setItem(sessionKey(), JSON.stringify({{
      k: ticket, exp: exp, who: document.getElementById('who').textContent,
      meLabel: meLabel, meNick: meNick
    }}));
  }} catch (e) {{}}
}}
function loadSession() {{
  try {{
    var o = JSON.parse(sessionStorage.getItem(sessionKey()) || 'null');
    if (!o || !o.k || !o.exp || o.exp <= Date.now()/1000) return null;
    return o;
  }} catch (e) {{ return null; }}
}}
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
  if (ended) return;
  ended = true;
  gDead = true;
  try {{ stopVoice(false); }} catch (e) {{}}
  append('\\n' + msg);
  document.getElementById('msg').disabled = true;
  document.getElementById('pick').disabled = true;
  var vb = document.getElementById('voice');
  if (vb) vb.disabled = true;
  var tog = document.getElementById('gtog');
  if (tog) tog.disabled = true;
  try {{ if (ws) {{ ws.onclose = null; ws.close(); }} }} catch (e) {{}}
}}
function scrollLog() {{
  var log = document.getElementById('log');
  log.scrollTop = log.scrollHeight;
}}
function isMe(name) {{
  return !!name && (name === meLabel || name === meNick);
}}
function flushSys() {{
  if (!pendingSys.length) return;
  if (pendingSys.length >= 4) addSys(pendingSys[0]);
  else for (var i = 0; i < pendingSys.length; i++) addSys(pendingSys[i]);
  pendingSys = [];
}}
function addSys(text) {{
  if (!text) return;
  var d = document.createElement('div');
  d.className = 'sys';
  d.textContent = text;
  document.getElementById('log').appendChild(d);
  scrollLog();
  try {{ if (typeof maybeGameNote === 'function') maybeGameNote(text); }} catch (e) {{}}
}}
function addBubble(who, text) {{
  flushSys();
  var wrap = document.createElement('div');
  wrap.className = 'bubble ' + (isMe(who) ? 'me' : 'them');
  if (!isMe(who) && who) {{
    var n = document.createElement('div');
    n.className = 'nm';
    n.textContent = who;
    wrap.appendChild(n);
  }}
  var b = document.createElement('div');
  b.className = 'txt';
  b.textContent = text;
  wrap.appendChild(b);
  document.getElementById('log').appendChild(wrap);
  scrollLog();
}}
function addLine(line) {{
  line = String(line || '').replace(/\\s+$/,'');
  if (!line) return;
  var m = /^\\[#[^\\]]+\\] \\[([^\\]]+)\\] (.*)$/.exec(line);
  if (m && m[1] !== '*') {{ addBubble(m[1], m[2]); return; }}
  var star = /^\\[#[^\\]]+\\] \\[\\*\\] (.*)$/.exec(line);
  if (star) {{ pendingSys.push(star[1]); return; }}
  var plus = /^\\[\\+\\] (.*)$/.exec(line);
  if (plus) {{ flushSys(); addSys(plus[1]); return; }}
  var minus = /^\\[-\\] (.*)$/.exec(line);
  if (minus) {{ flushSys(); addSys(minus[1]); return; }}
  flushSys();
  addSys(line);
}}
function append(s) {{
  var parts = String(s || '').split(/\\r?\\n/);
  for (var i = 0; i < parts.length; i++) addLine(parts[i]);
  flushSys();
  maybePreview(s);
}}
function appendImg(src, alt, mine) {{
  flushSys();
  var wrap = document.createElement('div');
  wrap.className = 'bubble ' + (mine ? 'me' : 'them');
  var img = document.createElement('img');
  img.className = 'preview';
  img.alt = alt || 'image';
  img.src = src;
  wrap.appendChild(img);
  document.getElementById('log').appendChild(wrap);
  scrollLog();
}}
function appendAudio(src, name, mine) {{
  flushSys();
  var wrap = document.createElement('div');
  wrap.className = 'bubble ' + (mine ? 'me' : 'them');
  if (!mine && name) {{
    var n = document.createElement('div');
    n.className = 'nm';
    n.textContent = name;
    wrap.appendChild(n);
  }}
  var audio = document.createElement('audio');
  audio.className = 'preview';
  audio.controls = true;
  audio.preload = 'metadata';
  audio.src = src;
  wrap.appendChild(audio);
  document.getElementById('log').appendChild(wrap);
  scrollLog();
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
    var mime = String(j.mime || '').toLowerCase();
    var fn = String(j.filename || '').toLowerCase();
    if (mime.indexOf('audio/') === 0 || (mime === 'video/webm' && fn.indexOf('voice') === 0)) {{
      appendAudio(j.preview, j.filename || 'voice');
      return;
    }}
    if (mime.indexOf('image/') === 0 || !mime) {{
      appendImg(j.preview, j.filename || 'image');
    }}
  }}).catch(function () {{}});
}}
function proto() {{ return location.protocol === 'https:' ? 'wss:' : 'ws:'; }}
function scheduleReconnect() {{
  if (ended || reconnectTimer || !ticket) return;
  if (!toldRejoin) {{ toldRejoin = true; append('\\n' + (L.rejoin || '') + '\\n'); }}
  reconnectTimer = setTimeout(function () {{
    reconnectTimer = null;
    if (!ended) connect(ticket);
  }}, 800);
}}
function connect(tkt) {{
  ticket = tkt;
  saveSession();
  try {{ if (ws) {{ ws.onclose = null; if (ws.readyState < 2) ws.close(); }} }} catch (e) {{}}
  ws = new WebSocket(proto() + '//' + location.host + '/guest/' + boot.token + '/ws?k=' + encodeURIComponent(ticket));
  ws.onopen = function () {{
    toldRejoin = false;
    if (typeof cmdGame === 'function') cmdGame('show');
  }};
  ws.onmessage = function (ev) {{
    var m;
    try {{ m = JSON.parse(ev.data); }} catch (e) {{ return; }}
    if (m.t === 'line') {{ append(m.s); if (typeof ingestGame === 'function') ingestGame(m.s); }}
    if (m.t === 'hello' && m.exp) {{ exp = m.exp; saveSession(); }}
    if (m.t === 'end') over(m.why === 'timeout' ? L.expired : L.used);
  }};
  ws.onclose = function () {{
    if (ended) return;
    if (!exp || Date.now()/1000 >= exp) {{ over(L.expired); return; }}
    scheduleReconnect();
  }};
}}
function sendImage(file) {{
  if (!file || !ticket || busy) return;
  if (file.size > boot.maxImage) {{ append('\\n' + L.too_big + '\\n'); return; }}
  if (file.type && file.type.indexOf('image/') !== 0) {{ append('\\n' + L.not_image + '\\n'); return; }}
  busy = true;
  append('\\n' + L.uploading + '\\n');
  var localUrl = '';
  try {{ localUrl = URL.createObjectURL(file); appendImg(localUrl, file.name || 'image', true); }} catch (e) {{}}
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
var mediaRec = null, mediaStream = null, voiceChunks = [], voiceStart = 0, voiceMime = '';
function pickVoiceMime() {{
  // Prefer mp4/aac when available (Safari); Chrome still lands on webm/opus.
  var cands = ['audio/mp4', 'audio/aac', 'audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/ogg'];
  if (!window.MediaRecorder) return '';
  for (var i = 0; i < cands.length; i++) {{
    try {{ if (MediaRecorder.isTypeSupported(cands[i])) return cands[i]; }} catch (e) {{}}
  }}
  return '';
}}
function voiceFileExt(mime) {{
  var m = String(mime || '').toLowerCase();
  if (m.indexOf('wav') >= 0) return '.wav';
  if (m.indexOf('mp4') >= 0 || m.indexOf('m4a') >= 0 || m.indexOf('aac') >= 0) return '.m4a';
  if (m.indexOf('mpeg') >= 0 || m.indexOf('mp3') >= 0) return '.mp3';
  if (m.indexOf('ogg') >= 0) return '.ogg';
  return '.webm';
}}
function voiceNeedsWav(mime) {{
  var m = String(mime || '').toLowerCase();
  // Native Android/iOS players often fail on browser Opus-in-WebM/Ogg.
  return m.indexOf('webm') >= 0 || m.indexOf('ogg') >= 0 || m.indexOf('opus') >= 0;
}}
function writeWavString(view, offset, str) {{
  for (var i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
}}
function audioBufferToWav(buf) {{
  var numCh = buf.numberOfChannels;
  var sampleRate = buf.sampleRate;
  var frames = buf.length;
  var ch0 = buf.getChannelData(0);
  var ch1 = numCh > 1 ? buf.getChannelData(1) : null;
  var bytes = frames * 2;
  var ab = new ArrayBuffer(44 + bytes);
  var view = new DataView(ab);
  writeWavString(view, 0, 'RIFF');
  view.setUint32(4, 36 + bytes, true);
  writeWavString(view, 8, 'WAVE');
  writeWavString(view, 12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeWavString(view, 36, 'data');
  view.setUint32(40, bytes, true);
  var off = 44;
  for (var i = 0; i < frames; i++) {{
    var s = ch1 ? ((ch0[i] + ch1[i]) * 0.5) : ch0[i];
    s = Math.max(-1, Math.min(1, s));
    view.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    off += 2;
  }}
  return ab;
}}
function blobToPlayableVoice(blob, mime) {{
  if (!blob || !voiceNeedsWav(mime)) {{
    return Promise.resolve({{blob: blob, mime: mime || 'audio/webm'}});
  }}
  var AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return Promise.resolve({{blob: blob, mime: mime || 'audio/webm'}});
  var ctx = new AC();
  return blob.arrayBuffer().then(function (raw) {{
    return ctx.decodeAudioData(raw.slice(0));
  }}).then(function (buf) {{
    try {{ ctx.close(); }} catch (e) {{}}
    var wav = audioBufferToWav(buf);
    return {{blob: new Blob([wav], {{type: 'audio/wav'}}), mime: 'audio/wav'}};
  }}).catch(function () {{
    try {{ ctx.close(); }} catch (e) {{}}
    return {{blob: blob, mime: mime || 'audio/webm'}};
  }});
}}
function setVoiceUi(rec) {{
  var btn = document.getElementById('voice');
  if (!btn) return;
  if (rec) {{
    btn.classList.add('rec');
    btn.textContent = L.voice_rec || L.voice;
  }} else {{
    btn.classList.remove('rec');
    btn.textContent = L.voice;
  }}
}}
function releaseVoiceStream() {{
  try {{
    if (mediaStream) {{
      var tracks = mediaStream.getTracks();
      for (var i = 0; i < tracks.length; i++) tracks[i].stop();
    }}
  }} catch (e) {{}}
  mediaStream = null;
}}
function sendVoiceBlob(blob, mime) {{
  if (!blob || !ticket || busy) return;
  if (blob.size > (boot.maxVoice || 4194304)) {{ append('\\n' + L.too_big + '\\n'); return; }}
  busy = true;
  append('\\n' + L.voice_uploading + '\\n');
  var name = 'voice-' + Date.now() + voiceFileExt(mime);
  try {{
    var localUrl = URL.createObjectURL(blob);
    appendAudio(localUrl, name, true);
  }} catch (e) {{}}
  var fd = new FormData();
  fd.append('file', blob, name);
  fetch('/guest/' + boot.token + '/voice?k=' + encodeURIComponent(ticket) + '&lang=' + encodeURIComponent(boot.lang), {{
    method: 'POST',
    body: fd
  }}).then(function (r) {{ return r.json().then(function (j) {{ return {{ok: r.ok, j: j}}; }}); }})
    .then(function (x) {{
      busy = false;
      if (!x.ok || !x.j.ok) {{
        var err = (x.j && x.j.error) || L.voice_fail;
        if (x.j && x.j.code === 'not_audio') err = L.not_audio;
        if (x.j && x.j.code === 'too_big') err = L.too_big;
        if (x.j && x.j.code === 'file_off') err = L.file_off;
        append(err + '\\n');
        return;
      }}
      append(L.voice_ok + '\\n');
    }}).catch(function () {{
      busy = false;
      append(L.voice_fail + '\\n');
    }});
}}
function stopVoice(send) {{
  var rec = mediaRec;
  mediaRec = null;
  setVoiceUi(false);
  if (!rec) {{
    releaseVoiceStream();
    voiceChunks = [];
    return;
  }}
  var elapsed = Date.now() - voiceStart;
  var mime = voiceMime || rec.mimeType || '';
  rec.ondataavailable = function (ev) {{
    if (ev.data && ev.data.size) voiceChunks.push(ev.data);
  }};
  rec.onstop = function () {{
    releaseVoiceStream();
    var chunks = voiceChunks;
    voiceChunks = [];
    if (!send) return;
    if (elapsed < 400) {{ append('\\n' + L.voice_short + '\\n'); return; }}
    if (!chunks.length) {{ append('\\n' + L.voice_fail + '\\n'); return; }}
    var blob = new Blob(chunks, {{type: mime || 'audio/webm'}});
    blobToPlayableVoice(blob, mime).then(function (out) {{
      sendVoiceBlob(out.blob, out.mime);
    }});
  }};
  try {{ rec.stop(); }} catch (e) {{
    releaseVoiceStream();
    voiceChunks = [];
  }}
}}
function startVoice(ev) {{
  if (ev) {{
    try {{ ev.preventDefault(); }} catch (e) {{}}
  }}
  if (!ticket || busy || ended || mediaRec) return;
  voiceMime = pickVoiceMime();
  if (!voiceMime && !window.MediaRecorder) {{
    append('\\n' + L.voice_unsupported + '\\n');
    return;
  }}
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {{
    append('\\n' + L.voice_unsupported + '\\n');
    return;
  }}
  navigator.mediaDevices.getUserMedia({{audio: true}}).then(function (stream) {{
    if (ended || !ticket || mediaRec) {{
      try {{ stream.getTracks().forEach(function (t) {{ t.stop(); }}); }} catch (e) {{}}
      return;
    }}
    mediaStream = stream;
    voiceChunks = [];
    voiceStart = Date.now();
    try {{
      mediaRec = voiceMime
        ? new MediaRecorder(stream, {{mimeType: voiceMime}})
        : new MediaRecorder(stream);
      voiceMime = mediaRec.mimeType || voiceMime || '';
    }} catch (e) {{
      releaseVoiceStream();
      append('\\n' + L.voice_unsupported + '\\n');
      return;
    }}
    mediaRec.ondataavailable = function (ev) {{
      if (ev.data && ev.data.size) voiceChunks.push(ev.data);
    }};
    try {{ mediaRec.start(250); }} catch (e2) {{
      releaseVoiceStream();
      mediaRec = null;
      append('\\n' + L.voice_fail + '\\n');
      return;
    }}
    setVoiceUi(true);
  }}).catch(function () {{
    append('\\n' + L.voice_denied + '\\n');
  }});
}}
(function () {{
  var zh = document.getElementById('langZh');
  var en = document.getElementById('langEn');
  if (zh) zh.onclick = function () {{ applyLang('zh'); }};
  if (en) en.onclick = function () {{ applyLang('en'); }};
}})();
document.getElementById('go').onclick = function () {{
  var btn = this;
  var err = document.getElementById('err');
  if (btn.disabled) return;
  btn.disabled = true;
  err.textContent = L.joining || '';
  var remark = (document.getElementById('remark') && document.getElementById('remark').value) || '';
  fetch('/guest/' + boot.token + '/join?lang=' + encodeURIComponent(boot.lang), {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{remark: remark}})
  }}).then(function (r) {{
    return r.text().then(function (t) {{
      var j = {{}};
      try {{ j = JSON.parse(t); }} catch (e) {{}}
      return {{ok: r.ok, j: j}};
    }});
  }}).then(function (x) {{
      if (!x.ok || !x.j.ok) {{
        btn.disabled = false;
        err.textContent = (x.j && x.j.error) || L.fail;
        return;
      }}
      exp = x.j.exp;
      meNick = x.j.nick || '';
      meLabel = x.j.label || meNick;
      document.getElementById('who').textContent = L.you + ' ' + (meLabel || meNick) + ' · #' + boot.room;
      document.getElementById('gate').style.display = 'none';
      document.getElementById('chat').style.display = 'flex';
      connect(x.j.ticket);
      tick();
      saveSession();
    }}).catch(function () {{
      btn.disabled = false;
      err.textContent = L.fail;
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
(function () {{
  var btn = document.getElementById('voice');
  if (!btn) return;
  var armed = false;
  function down(ev) {{
    if (btn.disabled) return;
    armed = true;
    startVoice(ev);
  }}
  function up(ev) {{
    if (!armed) return;
    armed = false;
    stopVoice(true);
  }}
  function cancel(ev) {{
    if (!armed) return;
    armed = false;
    stopVoice(false);
  }}
  btn.addEventListener('pointerdown', down);
  btn.addEventListener('pointerup', up);
  btn.addEventListener('pointercancel', cancel);
  btn.addEventListener('pointerleave', function (ev) {{
    if (armed && mediaRec) cancel(ev);
  }});
  btn.addEventListener('contextmenu', function (ev) {{ try {{ ev.preventDefault(); }} catch (e) {{}} }});
}})();
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
(function () {{
  var s = loadSession();
  if (!s) return;
  ticket = s.k;
  exp = s.exp;
  meLabel = s.meLabel || '';
  meNick = s.meNick || '';
  if (s.who) document.getElementById('who').textContent = s.who;
  document.getElementById('gate').style.display = 'none';
  document.getElementById('chat').style.display = 'flex';
  connect(ticket);
  tick();
}})();
document.addEventListener('visibilitychange', function () {{
  if (document.hidden || ended || !ticket) return;
  if (!ws || ws.readyState > 1) connect(ticket);
}});
window.addEventListener('pageshow', function () {{
  if (ended || !ticket) return;
  if (!ws || ws.readyState > 1) connect(ticket);
}});
</script>
<script>
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
        if link.closed or time.time() >= (link.chat_expires_at or 0):
            _closed_page(handler, lang, "expired")
            return True
        # Same tab can resume via sessionStorage ticket; other browsers still
        # cannot join. Chrome tab discard reloads the URL after minimize.
        _send_html(handler, render_guest_page(link, lang))
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
    if parts[2] == "voice":
        return _handle_guest_voice(handler, parts[1])
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


def _read_guest_upload(
    handler: "BaseHTTPRequestHandler",
    *,
    max_bytes: int,
    text: dict,
) -> tuple[bytes | None, str, str | None]:
    """Return (raw, filename, error_code). error_code set on failure."""
    content_type = handler.headers.get("Content-Type", "")
    if not content_type.startswith("multipart/form-data"):
        return None, "", "bad"
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
        return None, "", "bad"
    if "file" not in form:
        return None, "", "empty"
    file_item = form["file"]
    if not getattr(file_item, "file", None):
        return None, "", "empty"
    raw = file_item.file.read(max_bytes + 1)
    if not raw:
        return None, "", "empty"
    if len(raw) > max_bytes:
        return None, "", "too_big"
    filename = getattr(file_item, "filename", None) or "upload.bin"
    return raw, filename, None


def _handle_guest_voice(handler: "BaseHTTPRequestHandler", token: str) -> bool:
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
    if _publish_guest_voice is None:
        _send_json(
            handler,
            503,
            {"ok": False, "code": "file_off", "error": text["file_off"]},
        )
        return True
    raw, filename, code = _read_guest_upload(
        handler, max_bytes=guest_sharing.MAX_VOICE_BYTES, text=text
    )
    if code:
        msg = text.get(code) or text["voice_fail"]
        status = 413 if code == "too_big" else 400
        _send_json(handler, status, {"ok": False, "code": code, "error": msg})
        return True
    if not guest_sharing.looks_like_audio(raw or b"", filename):
        _send_json(
            handler,
            400,
            {"ok": False, "code": "not_audio", "error": text["not_audio"]},
        )
        return True
    nick = link.nick or guest_sharing.random_guest_nick()
    ok, pub_code = _publish_guest_voice(nick, link.room, filename, raw or b"")
    if not ok:
        msg = text.get(pub_code) or text["voice_fail"]
        status = (
            413
            if pub_code == "too_big"
            else 400
            if pub_code == "not_audio"
            else 503
        )
        _send_json(
            handler, status, {"ok": False, "code": pub_code or "fail", "error": msg}
        )
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
