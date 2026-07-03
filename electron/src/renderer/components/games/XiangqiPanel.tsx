import React, { useEffect, useMemo, useRef, useState } from 'react';

type Props = {
  disabled: boolean;
  nickname: string;
  boardText: string;
  onMove: (fr: number, fc: number, tr: number, tc: number) => void;
};

type Piece = {
  row: number;
  col: number;
  actualRow: number;
  actualCol: number;
  symbol: string;
  isRed: boolean;
};

type Side = 1 | -1;

type StrategyId = 'pikafish_external' | 'pro_balance' | 'pro_attack' | 'pro_defense';

type Move = {
  fr: number;
  fc: number;
  tr: number;
  tc: number;
  score: number;
};

type AssistantResult = {
  suggestions: Move[];
  depth: number;
  nodes: number;
  ms: number;
  note: string;
};

type XiangqiPhase = 'opening' | 'middle' | 'endgame';

type ParsedBoard = {
  pieces: Map<string, Piece>;
  board: number[][];
  pieceCount: number;
  flipped: boolean;
  toActual: (displayRow: number, displayCol: number) => { row: number; col: number };
  toDisplay: (actualRow: number, actualCol: number) => { row: number; col: number };
};

const ROWS = 10;
const COLS = 9;
const RED: Side = 1;
const BLACK: Side = -1;

const PT = {
  K: 1,
  A: 2,
  B: 3,
  N: 4,
  R: 5,
  C: 6,
  P: 7,
} as const;

const PIECE_VALUE: Record<number, number> = {
  [PT.K]: 100000,
  [PT.A]: 120,
  [PT.B]: 120,
  [PT.N]: 430,
  [PT.R]: 900,
  [PT.C]: 470,
  [PT.P]: 90,
};

const STRATEGIES: Array<{ id: StrategyId; label: string }> = [
  { id: 'pikafish_external', label: 'Pikafish\u804c\u4e1a\u5f15\u64ce\uff08\u63a8\u8350\uff09' },
  { id: 'pro_balance', label: '\u804c\u4e1a\u5747\u8861\uff08\u63a8\u8350\uff09' },
  { id: 'pro_attack', label: '\u804c\u4e1a\u8fdb\u653b' },
  { id: 'pro_defense', label: '\u804c\u4e1a\u9632\u5b88' },
];

const RED_UNIQUE = new Set(['\u5e05', '\u4ed5', '\u76f8', '\u5175']);
const BLACK_UNIQUE = new Set(['\u5c06', '\u58eb', '\u8c61', '\u5352']);

function pieceTypeFromSymbol(sym: string): number | 0 {
  if (sym === '\u5e05' || sym === '\u5c06') return PT.K;
  if (sym === '\u4ed5' || sym === '\u58eb') return PT.A;
  if (sym === '\u76f8' || sym === '\u8c61') return PT.B;
  if (sym === '\u9a6c') return PT.N;
  if (sym === '\u8f66') return PT.R;
  if (sym === '\u70ae') return PT.C;
  if (sym === '\u5175' || sym === '\u5352') return PT.P;
  return 0;
}

function parseTurnInfo(boardText: string): { name: string; side: Side | null } {
  for (const line of boardText.split('\n')) {
    const t = line.trim();
    const m = t.match(/^\u8f6e\u5230\s*(\u7ea2|\u9ed1)\u65b9\s*(\S+)\s*\u8d70\u5b50/i);
    if (m) {
      return { name: m[2].trim(), side: m[1] === '\u7ea2' ? RED : BLACK };
    }
    const m2 = t.match(/^(turn|\u8f6e\u5230)[:\uff1a]\s*(.+)$/i);
    if (m2) {
      return { name: m2[2].trim(), side: null };
    }
  }
  return { name: '', side: null };
}

function parseSeatInfo(boardText: string): { redName: string; blackName: string } {
  for (const line of boardText.split('\n')) {
    const m = line.match(/\u7ea2[:\uff1a]\s*(\S+)\s+\u9ed1[:\uff1a]\s*(\S+)/);
    if (m) {
      return { redName: m[1].trim(), blackName: m[2].trim() };
    }
  }
  return { redName: '', blackName: '' };
}

function parseLastMoveSide(boardText: string): Side | null {
  const line = boardText.split('\n').find((l) => /\u4e0a\u4e00\u6b65[:\uff1a]/.test(l));
  if (!line) return null;
  const moveText = line.split(/[:\uff1a]/).slice(1).join(':');
  if (/[1-9]/.test(moveText)) return BLACK;
  if (/[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d]/.test(moveText)) return RED;
  return null;
}

function parseBoard(boardText: string, turnSide: Side | null, viewerSide: Side | null): ParsedBoard {
  const pieces = new Map<string, Piece>();
  const board = Array.from({ length: ROWS }, () => Array(COLS).fill(0));
  const selfBottom = boardText.includes('\u5df1\u65b9\u5728\u4e0b\u65b9');
  const redTopBlackBottom = /\u7ea2\u65b9.*[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d]/.test(boardText) && /\u9ed1\u65b9.*9\s+8\s+7|\u9ed1\u65b9.*\u4ece\u53f3\u5411\u5de6/.test(boardText);
  const blackTopRedBottom = /\u9ed1\u65b9.*1\s+2\s+3|\u9ed1\u65b9\s*1\uFF5E9/.test(boardText) && /\u7ea2\u65b9.*\u4e5d\s+\u516b\s+\u4e03|\u7ea2\u65b9\u7eb5\u7ebf/.test(boardText);
  const flipped =
    selfBottom ||
    redTopBlackBottom ||
    (!blackTopRedBottom && viewerSide === BLACK);
  const toActual = (displayRow: number, displayCol: number) => ({
    row: flipped ? ROWS + 1 - displayRow : displayRow,
    col: flipped ? COLS + 1 - displayCol : displayCol,
  });
  const toDisplay = (actualRow: number, actualCol: number) => ({
    row: flipped ? ROWS + 1 - actualRow : actualRow,
    col: flipped ? COLS + 1 - actualCol : actualCol,
  });
  if (!boardText.trim()) return { pieces, board, pieceCount: 0, flipped, toActual, toDisplay };

  let rowNum = 1;
  const lastMoverSide = parseLastMoveSide(boardText) ?? (turnSide ? ((-turnSide) as Side) : null);

  // Parse fixed-width terminal board rows: +red, -black, !last move, dot empty, * marker.
  const foundBoardRows: Array<{ row: number | null; tokens: string[] }> = [];
  const tokenRe = /(?:[+\-!][^\s]{1,2}|[\u00b7*])/g;
  for (const rawLine of boardText.split('\n')) {
    const line = rawLine.trim();
    if (!line) continue;
    const tokens = line.match(tokenRe);
    if (!tokens || tokens.length < COLS) continue;
    const rowTokens = tokens.slice(0, COLS);
    const allCellLike = rowTokens.every((t) => t === '\u00b7' || t === '*' || /^[+\-!]/.test(t));
    if (!allCellLike) continue;
    const explicitRow = line.match(/^(\d{1,2})\s+/);
    const parsedRow = explicitRow ? Number(explicitRow[1]) : null;
    foundBoardRows.push({
      row: parsedRow && parsedRow >= 1 && parsedRow <= ROWS ? parsedRow : null,
      tokens: rowTokens,
    });
  }

  const boardRows = foundBoardRows.slice(-ROWS);

  for (const row of boardRows) {
    const displayRow = row.row ?? rowNum;
    rowNum = Math.max(rowNum + 1, displayRow + 1);
    if (displayRow < 1 || displayRow > ROWS) continue;
    const rowTokens = row.tokens;
    for (let c = 0; c < COLS; c += 1) {
      const displayCol = c + 1;
      const actual = toActual(displayRow, displayCol);
      const cell = rowTokens[c];
      if (cell === '\u00b7' || cell === '*') continue;
      const match = cell.match(/^([+\-!])(.*)$/);
      if (!match) continue;

            const marker = match[1];
            const symbol = match[2];
      if (!symbol || symbol === '\u00b7' || symbol === '*') continue;

      let isRed = marker === '+';
      if (marker === '-') {
        isRed = false;
      } else if (marker === '!') {
        if (RED_UNIQUE.has(symbol)) isRed = true;
        else if (BLACK_UNIQUE.has(symbol)) isRed = false;
        else if (lastMoverSide) isRed = lastMoverSide === RED;
        else isRed = actual.row >= 6;
      }

      pieces.set(`${displayRow}-${displayCol}`, {
        row: displayRow,
        col: displayCol,
        actualRow: actual.row,
        actualCol: actual.col,
        symbol,
        isRed,
      });

      const pt = pieceTypeFromSymbol(symbol);
      if (pt) {
        board[actual.row - 1][actual.col - 1] = (isRed ? RED : BLACK) * pt;
      }
    }
  }

  let pieceCount = 0;
  for (let r = 0; r < ROWS; r += 1) {
    for (let c = 0; c < COLS; c += 1) {
      if (board[r][c] !== 0) pieceCount += 1;
    }
  }

  return { pieces, board, pieceCount, flipped, toActual, toDisplay };
}

function pieceSide(cell: number): Side | 0 {
  if (cell > 0) return RED;
  if (cell < 0) return BLACK;
  return 0;
}

function pieceType(cell: number): number {
  return Math.abs(cell);
}

function inBoard(r: number, c: number): boolean {
  return r >= 0 && r < ROWS && c >= 0 && c < COLS;
}

function inPalace(r: number, c: number, side: Side): boolean {
  if (c < 3 || c > 5) return false;
  return side === RED ? r >= 7 && r <= 9 : r >= 0 && r <= 2;
}

function kingPos(board: number[][], side: Side): [number, number] | null {
  const target = side * PT.K;
  for (let r = 0; r < ROWS; r += 1) {
    for (let c = 0; c < COLS; c += 1) {
      if (board[r][c] === target) return [r, c];
    }
  }
  return null;
}

function flyingKings(board: number[][]): boolean {
  const rk = kingPos(board, RED);
  const bk = kingPos(board, BLACK);
  if (!rk || !bk) return false;
  if (rk[1] !== bk[1]) return false;
  const lo = Math.min(rk[0], bk[0]);
  const hi = Math.max(rk[0], bk[0]);
  for (let r = lo + 1; r < hi; r += 1) {
    if (board[r][rk[1]] !== 0) return false;
  }
  return true;
}

function genPseudo(board: number[][], r: number, c: number, capturesOnly = false): Array<[number, number]> {
  const cell = board[r][c];
  if (cell === 0) return [];
  const side = pieceSide(cell) as Side;
  const pt = pieceType(cell);
  const out: Array<[number, number]> = [];

  const add = (tr: number, tc: number) => {
    if (!inBoard(tr, tc)) return;
    const target = board[tr][tc];
    if (target !== 0 && pieceSide(target) === side) return;
    if (capturesOnly && target === 0) return;
    out.push([tr, tc]);
  };

  if (pt === PT.K) {
    [[0, 1], [0, -1], [1, 0], [-1, 0]].forEach(([dr, dc]) => {
      const tr = r + dr;
      const tc = c + dc;
      if (inPalace(tr, tc, side)) add(tr, tc);
    });
  } else if (pt === PT.A) {
    [[1, 1], [1, -1], [-1, 1], [-1, -1]].forEach(([dr, dc]) => {
      const tr = r + dr;
      const tc = c + dc;
      if (inPalace(tr, tc, side)) add(tr, tc);
    });
  } else if (pt === PT.B) {
    [[2, 2], [2, -2], [-2, 2], [-2, -2]].forEach(([dr, dc]) => {
      const tr = r + dr;
      const tc = c + dc;
      const er = r + dr / 2;
      const ec = c + dc / 2;
      if (!inBoard(tr, tc)) return;
      if (board[er][ec] !== 0) return;
      if (side === RED && tr < 5) return;
      if (side === BLACK && tr > 4) return;
      add(tr, tc);
    });
  } else if (pt === PT.N) {
    const legs: Array<[number, number, number, number]> = [
      [-2, -1, -1, 0], [-2, 1, -1, 0], [2, -1, 1, 0], [2, 1, 1, 0],
      [-1, -2, 0, -1], [-1, 2, 0, 1], [1, -2, 0, -1], [1, 2, 0, 1],
    ];
    legs.forEach(([dr, dc, lr, lc]) => {
      const legR = r + lr;
      const legC = c + lc;
      if (!inBoard(legR, legC)) return;
      if (board[legR][legC] !== 0) return;
      add(r + dr, c + dc);
    });
  } else if (pt === PT.R) {
    [[0, 1], [0, -1], [1, 0], [-1, 0]].forEach(([dr, dc]) => {
      let tr = r + dr;
      let tc = c + dc;
      while (inBoard(tr, tc)) {
        if (board[tr][tc] === 0) {
          if (!capturesOnly) out.push([tr, tc]);
        } else {
          if (pieceSide(board[tr][tc]) !== side) out.push([tr, tc]);
          break;
        }
        tr += dr;
        tc += dc;
      }
    });
  } else if (pt === PT.C) {
    [[0, 1], [0, -1], [1, 0], [-1, 0]].forEach(([dr, dc]) => {
      let tr = r + dr;
      let tc = c + dc;
      let jumped = false;
      while (inBoard(tr, tc)) {
        if (board[tr][tc] === 0) {
          if (!capturesOnly && !jumped) out.push([tr, tc]);
        } else if (!jumped) {
          jumped = true;
        } else {
          if (pieceSide(board[tr][tc]) !== side) out.push([tr, tc]);
          break;
        }
        tr += dr;
        tc += dc;
      }
    });
  } else if (pt === PT.P) {
    if (side === RED) {
      add(r - 1, c);
      if (r <= 4) {
        add(r, c - 1);
        add(r, c + 1);
      }
    } else {
      add(r + 1, c);
      if (r >= 5) {
        add(r, c - 1);
        add(r, c + 1);
      }
    }
  }

  return out;
}

function isAttacked(board: number[][], row: number, col: number, bySide: Side): boolean {
  for (let r = 0; r < ROWS; r += 1) {
    for (let c = 0; c < COLS; c += 1) {
      if (pieceSide(board[r][c]) !== bySide) continue;
      const caps = genPseudo(board, r, c, true);
      for (const [tr, tc] of caps) {
        if (tr === row && tc === col) return true;
      }
      if (pieceType(board[r][c]) === PT.K && board[row][col] === -bySide * PT.K && c === col && Math.abs(r - row) > 1) {
        const lo = Math.min(r, row);
        const hi = Math.max(r, row);
        let blocked = false;
        for (let rr = lo + 1; rr < hi; rr += 1) {
          if (board[rr][col] !== 0) {
            blocked = true;
            break;
          }
        }
        if (!blocked) return true;
      }
    }
  }
  return false;
}

function legalMoves(board: number[][], side: Side): Move[] {
  const out: Move[] = [];
  for (let r = 0; r < ROWS; r += 1) {
    for (let c = 0; c < COLS; c += 1) {
      if (pieceSide(board[r][c]) !== side) continue;
      const candidates = genPseudo(board, r, c, false);
      for (const [tr, tc] of candidates) {
        const captured = board[tr][tc];
        board[tr][tc] = board[r][c];
        board[r][c] = 0;

        const illegalFace = flyingKings(board);
        const k = kingPos(board, side);
        const checked = k ? isAttacked(board, k[0], k[1], (side === RED ? BLACK : RED) as Side) : false;

        board[r][c] = board[tr][tc];
        board[tr][tc] = captured;

        if (illegalFace || checked) continue;
        out.push({ fr: r, fc: c, tr, tc, score: 0 });
      }
    }
  }
  return out;
}

function piecePosBonus(pt: number, r: number, c: number, side: Side): number {
  const center = 4 - Math.abs(c - 4);
  const progress = side === RED ? 9 - r : r;

  if (pt === PT.P) {
    const crossed = side === RED ? r <= 4 : r >= 5;
    return (crossed ? 70 : 20) + progress * 4 + center * 8;
  }
  if (pt === PT.R) return center * 10 + progress * 2;
  if (pt === PT.C) return center * 9 + progress * 2;
  if (pt === PT.N) return center * 10 + progress * 3;
  if (pt === PT.B || pt === PT.A) return center * 2;
  if (pt === PT.K) return center * 3;
  return 0;
}

function styleScale(strategy: StrategyId, pt: number): number {
  if (strategy === 'pro_attack') {
    if (pt === PT.R || pt === PT.C || pt === PT.N || pt === PT.P) return 1.1;
    if (pt === PT.A || pt === PT.B) return 0.92;
  }
  if (strategy === 'pro_defense') {
    if (pt === PT.A || pt === PT.B || pt === PT.K) return 1.12;
    if (pt === PT.P) return 0.95;
  }
  return 1.0;
}

function evaluateFor(board: number[][], side: Side, strategy: StrategyId): number {
  let score = 0;
  const oppSide = (side === RED ? BLACK : RED) as Side;
  for (let r = 0; r < ROWS; r += 1) {
    for (let c = 0; c < COLS; c += 1) {
      const cell = board[r][c];
      if (cell === 0) continue;
      const s = pieceSide(cell) as Side;
      const pt = pieceType(cell);
      const base = PIECE_VALUE[pt] * styleScale(strategy, pt);
      const pos = piecePosBonus(pt, r, c, s);
      const v = base + pos;
      score += s === side ? v : -v;

      const attackedByOpp = isAttacked(board, r, c, s === side ? oppSide : side);
      const protectedByOwn = isAttacked(board, r, c, s);
      const safety = PIECE_VALUE[pt] * (attackedByOpp ? (protectedByOwn ? 0.08 : 0.22) : 0);
      score += s === side ? -safety : safety;
    }
  }

  const myK = kingPos(board, side);
  const oppK = kingPos(board, oppSide);
  if (myK && isAttacked(board, myK[0], myK[1], oppSide)) score -= 420;
  if (oppK && isAttacked(board, oppK[0], oppK[1], side)) score += 360;

  score += legalMoves(board, side).length * 6;
  score -= legalMoves(board, oppSide).length * 5;

  return score;
}

function opposite(side: Side): Side {
  return (side === RED ? BLACK : RED) as Side;
}

function gamePhase(pieceCount: number): XiangqiPhase {
  if (pieceCount >= 28) return 'opening';
  if (pieceCount <= 14) return 'endgame';
  return 'middle';
}

function pieceCountOnBoard(board: number[][]): number {
  let n = 0;
  for (const row of board) {
    for (const cell of row) {
      if (cell !== 0) n += 1;
    }
  }
  return n;
}

function isInCheck(board: number[][], side: Side): boolean {
  const k = kingPos(board, side);
  return !!k && isAttacked(board, k[0], k[1], opposite(side));
}

function applyMove(board: number[][], mv: Move): number {
  const captured = board[mv.tr][mv.tc];
  board[mv.tr][mv.tc] = board[mv.fr][mv.fc];
  board[mv.fr][mv.fc] = 0;
  return captured;
}

function undoMove(board: number[][], mv: Move, captured: number): void {
  board[mv.fr][mv.fc] = board[mv.tr][mv.tc];
  board[mv.tr][mv.tc] = captured;
}

function givesCheck(board: number[][], mv: Move, side: Side): boolean {
  const captured = applyMove(board, mv);
  const check = isInCheck(board, opposite(side));
  undoMove(board, mv, captured);
  return check;
}

function isMateMove(board: number[][], mv: Move, side: Side): boolean {
  const captured = applyMove(board, mv);
  const opp = opposite(side);
  const mate = isInCheck(board, opp) && legalMoves(board, opp).length === 0;
  undoMove(board, mv, captured);
  return mate;
}

function bestImmediateThreat(board: number[][], side: Side): number {
  let best = 0;
  for (const mv of legalMoves(board, side)) {
    if (isMateMove(board, mv, side)) return 900000;
    const target = board[mv.tr][mv.tc];
    if (target !== 0) best = Math.max(best, PIECE_VALUE[pieceType(target)]);
    if (givesCheck(board, mv, side)) best = Math.max(best, 180);
  }
  return best;
}

function bestImmediateCaptureValue(board: number[][], side: Side): number {
  let best = 0;
  for (const mv of legalMoves(board, side)) {
    const target = board[mv.tr][mv.tc];
    if (target !== 0) best = Math.max(best, PIECE_VALUE[pieceType(target)]);
  }
  return best;
}

function exchangeAfterMove(board: number[][], mv: Move, side: Side): { targetValue: number; moverValue: number; recapturable: boolean; netIfRecaptured: number } {
  const mover = board[mv.fr][mv.fc];
  const target = board[mv.tr][mv.tc];
  const moverValue = PIECE_VALUE[pieceType(mover)];
  const targetValue = target === 0 ? 0 : PIECE_VALUE[pieceType(target)];
  const captured = applyMove(board, mv);
  const recapturable = legalMoves(board, opposite(side)).some((reply) => reply.tr === mv.tr && reply.tc === mv.tc);
  undoMove(board, mv, captured);
  return {
    targetValue,
    moverValue,
    recapturable,
    netIfRecaptured: targetValue - moverValue,
  };
}

function attackLineCount(board: number[][], row: number, col: number, side: Side): number {
  let count = 0;
  for (const mv of legalMoves(board, side)) {
    if (mv.tr === row && mv.tc === col) count += 1;
  }
  return count;
}

function palacePressure(board: number[][], side: Side): number {
  const opp = opposite(side);
  const k = kingPos(board, opp);
  if (!k) return 0;
  let pressure = 0;
  for (const mv of legalMoves(board, side)) {
    const dist = Math.abs(mv.tr - k[0]) + Math.abs(mv.tc - k[1]);
    const pt = pieceType(board[mv.fr][mv.fc]);
    if (dist <= 2) pressure += pt === PT.R ? 90 : pt === PT.C ? 75 : pt === PT.N ? 70 : 25;
    if (givesCheck(board, mv, side)) pressure += 180;
  }
  return pressure;
}

function fortressWeakness(board: number[][], side: Side): number {
  const k = kingPos(board, side);
  if (!k) return 0;
  let guards = 0;
  let elephants = 0;
  let pinnedNearKing = 0;
  for (let r = 0; r < ROWS; r += 1) {
    for (let c = 0; c < COLS; c += 1) {
      if (pieceSide(board[r][c]) !== side) continue;
      const pt = pieceType(board[r][c]);
      if (pt === PT.A) guards += 1;
      if (pt === PT.B) elephants += 1;
      if ((pt === PT.N || pt === PT.P) && Math.abs(r - k[0]) + Math.abs(c - k[1]) <= 2) pinnedNearKing += 1;
    }
  }
  return (2 - guards) * 140 + (2 - elephants) * 90 + pinnedNearKing * 55;
}

function moveDevelopmentScore(board: number[][], mv: Move, side: Side, phase: XiangqiPhase): number {
  if (phase !== 'opening') return 0;
  const pt = pieceType(board[mv.fr][mv.fc]);
  const centerFile = 4 - Math.abs(mv.tc - 4);
  let score = 0;

  if (pt === PT.R) {
    if (mv.tc === 1 || mv.tc === 7 || mv.tr === 4 || mv.tr === 5) score += 230;
    score += Math.abs(mv.tr - mv.fr) * 18;
  }
  if (pt === PT.N) {
    score += 170;
    if (mv.tc >= 2 && mv.tc <= 6) score += 55;
  }
  if (pt === PT.P) {
    score += 90;
    if (mv.tc === 2 || mv.tc === 4 || mv.tc === 6) score += 35;
  }
  if (pt === PT.C) {
    const target = board[mv.tr][mv.tc];
    const exchange = exchangeAfterMove(board, mv, side);
    score -= 80;
    if (target !== 0 && (!exchange.recapturable || exchange.netIfRecaptured > 0)) score += 180;
    if (mv.tr === 4 || mv.tr === 5) score += 70;
  }
  score += centerFile * 10;
  return score;
}

function killPatternScore(board: number[][], mv: Move, side: Side): number {
  const mover = board[mv.fr][mv.fc];
  const pt = pieceType(mover);
  const captured = applyMove(board, mv);
  const opp = opposite(side);
  const oppK = kingPos(board, opp);
  let score = 0;

  if (oppK) {
    if (isInCheck(board, opp)) score += 420;
    if (!flyingKings(board) && board[mv.tr][mv.tc] !== 0 && pt === PT.R && mv.tc === oppK[1]) score += 140;

    const dist = Math.abs(mv.tr - oppK[0]) + Math.abs(mv.tc - oppK[1]);
    if (pt === PT.N && dist <= 3) score += 180; // 鍗фЫ椹?鎸傝椹殑杩戝皢闂ㄥ舰鎬併€?    if (pt === PT.C && mv.tc === oppK[1]) score += 150; // 涓偖銆侀噸鐐€侀┈鍚庣偖鍊惧悜銆?    if (pt === PT.R && (mv.tc === 3 || mv.tc === 5 || mv.tc === oppK[1])) score += 190; // 鑲嬮亾/涓矾杞﹀帇鍒躲€?    score += Math.max(0, 4 - dist) * 35;
  }

  const pressure = palacePressure(board, side);
  const oppWeak = fortressWeakness(board, opp);
  score += Math.min(pressure, 700) * 0.35;
  score += Math.min(oppWeak, 500) * 0.4;

  undoMove(board, mv, captured);
  return Math.round(score);
}

function strategicRuleScore(board: number[][], mv: Move, side: Side, strategy: StrategyId, phase: XiangqiPhase): number {
  const mover = board[mv.fr][mv.fc];
  const target = board[mv.tr][mv.tc];
  const pt = pieceType(mover);
  const exchange = exchangeAfterMove(board, mv, side);
  let score = moveDevelopmentScore(board, mv, side, phase);

  if (target !== 0) {
    if (exchange.netIfRecaptured > 0 || !exchange.recapturable) score += 260;
    if (exchange.recapturable && exchange.netIfRecaptured <= 0) score -= 850 + exchange.moverValue * 2;
  }

  const captured = applyMove(board, mv);
  const opp = opposite(side);
  const oppMate = legalMoves(board, opp).some((reply) => isMateMove(board, reply, opp));
  const oppBestCapture = bestImmediateCaptureValue(board, opp);
  const myThreat = bestImmediateThreat(board, side);
  const oppThreat = bestImmediateThreat(board, opp);
  const attackedLanding = isAttacked(board, mv.tr, mv.tc, opp);
  const protectedLanding = isAttacked(board, mv.tr, mv.tc, side);
  undoMove(board, mv, captured);

  // 閾佸緥锛氫笉鍏佽缁欏鏂圭洿鎺ユ潃妫嬶紱浜忔崲銆佺櫧閫佽溅椹偖蹇呴』寮烘儵缃氥€?  if (oppMate) score -= 500000;
  if (oppThreat >= 900000) score -= 120000;
  if (attackedLanding && !protectedLanding) score -= PIECE_VALUE[pt] * 5;
  if (oppBestCapture >= PIECE_VALUE[pt] && attackedLanding) score -= Math.round(oppBestCapture * 1.4);

  // 姣忔瑕佸埗閫犲▉鑳侊紱浼樺娍闃舵浼樺厛绠€鍖栵紝鍔ｅ娍闃舵淇濈暀澶嶆潅鍜屽厛鎵嬨€?  if (myThreat >= 900000) score += 120000;
  else if (myThreat >= 900) score += 900;
  else if (myThreat >= 430) score += 360;
  else score -= phase === 'opening' ? 40 : 160;

  score += killPatternScore(board, mv, side);
  if (givesCheck(board, mv, side)) score += strategy === 'pro_attack' ? 520 : 380;

  if (phase === 'middle') {
    if (pt === PT.R && (mv.tc === 1 || mv.tc === 7 || mv.tc === 3 || mv.tc === 5)) score += 130;
    if ((pt === PT.N || pt === PT.C) && target !== 0 && exchange.targetValue >= PIECE_VALUE[PT.R]) score += 1300;
    if (strategy !== 'pro_defense') score += Math.min(palacePressure(board, side), 600) * 0.25;
  }

  if (phase === 'endgame') {
    if (pt === PT.P) score += 220 + (side === RED ? 9 - mv.tr : mv.tr) * 20;
    if (pt === PT.N) score += 140;
    if (pt === PT.C) score -= 35;
    if (pt === PT.K && mv.tc === 4) score += 90;
    if (target !== 0 || givesCheck(board, mv, side)) score += 160;
  }

  return Math.round(score);
}

function moveTacticalScore(board: number[][], mv: Move, side: Side, strategy: StrategyId, phase: XiangqiPhase): number {
  const mover = board[mv.fr][mv.fc];
  const target = board[mv.tr][mv.tc];
  const pt = pieceType(mover);
  const exchange = exchangeAfterMove(board, mv, side);
  let s = 0;
  if (target !== 0) {
    if (exchange.recapturable) {
      s += exchange.netIfRecaptured * 24;
      if (exchange.netIfRecaptured <= 0) s -= exchange.moverValue * 8;
    } else {
      s += exchange.targetValue * 14 - exchange.moverValue * 2;
    }
  }
  if (isMateMove(board, mv, side)) s += 1_000_000;
  else if (givesCheck(board, mv, side)) s += 900;

  const captured = applyMove(board, mv);
  const unsafe = isAttacked(board, mv.tr, mv.tc, opposite(side));
  const protectedByOwn = isAttacked(board, mv.tr, mv.tc, side);
  const oppThreat = bestImmediateThreat(board, opposite(side));
  undoMove(board, mv, captured);

  if (unsafe && !protectedByOwn) s -= PIECE_VALUE[pt] * 4;
  if (unsafe && protectedByOwn) s -= PIECE_VALUE[pt];
  if (target === 0 && exchange.recapturable) s -= exchange.moverValue * 5;
  s -= Math.min(oppThreat, 1500);
  s += (4 - Math.abs(mv.tr - 4)) * 8;
  s += (4 - Math.abs(mv.tc - 4)) * 4;
  if (pt === PT.R || pt === PT.C || pt === PT.N) s += 30;
  s += strategicRuleScore(board, mv, side, strategy, phase);
  return s;
}

type SearchContext = {
  deadline: number;
  maxNodes: number;
  nodes: number;
};

function checkBudget(ctx: SearchContext): void {
  if (Date.now() > ctx.deadline || ctx.nodes > ctx.maxNodes) {
    throw new Error('TIMEOUT');
  }
}

function moveOrder(board: number[][], m: Move): number {
  const mover = board[m.fr][m.fc];
  const target = board[m.tr][m.tc];
  const pt = pieceType(mover);
  const side = pieceSide(mover) as Side;
  let s = 0;
  if (target !== 0) {
    s += PIECE_VALUE[pieceType(target)] * 10 - PIECE_VALUE[pt] * 2;
  }
  if (givesCheck(board, m, side)) s += 700;
  if (target !== 0 && !isAttacked(board, m.tr, m.tc, side)) s += 120;
  s += (4 - Math.abs(m.tr - 4)) * 3;
  s += (4 - Math.abs(m.tc - 4)) * 3;
  if (pt === PT.R || pt === PT.C) s += 8;
  return s;
}

function quickRootMoveScore(board: number[][], mv: Move, side: Side, strategy: StrategyId, phase: XiangqiPhase): number {
  const mover = board[mv.fr][mv.fc];
  const target = board[mv.tr][mv.tc];
  const pt = pieceType(mover);
  const exchange = exchangeAfterMove(board, mv, side);
  let score = (4 - Math.abs(mv.tr - 4)) * 8 + (4 - Math.abs(mv.tc - 4)) * 5;

  if (target !== 0) {
    if (exchange.recapturable) {
      score += exchange.netIfRecaptured * 20;
      if (exchange.netIfRecaptured <= 0) score -= exchange.moverValue * 7;
    } else {
      score += exchange.targetValue * 12 - exchange.moverValue * 2;
    }
  } else if (exchange.recapturable) {
    score -= exchange.moverValue * 4;
  }
  if (pt === PT.R || pt === PT.C || pt === PT.N) score += 40;
  if (givesCheck(board, mv, side)) score += 900;
  score += strategicRuleScore(board, mv, side, strategy, phase);

  return score;
}

function tacticalMoves(board: number[][], side: Side): Move[] {
  return legalMoves(board, side)
    .filter((mv) => board[mv.tr][mv.tc] !== 0 || givesCheck(board, mv, side))
    .sort((a, b) => moveOrder(board, b) - moveOrder(board, a))
    .slice(0, 14);
}

function quiescence(board: number[][], side: Side, alpha: number, beta: number, ctx: SearchContext, strategy: StrategyId, qDepth = 3): number {
  ctx.nodes += 1;
  checkBudget(ctx);

  let stand = evaluateFor(board, side, strategy);
  if (stand >= beta) return beta;
  if (stand > alpha) alpha = stand;
  if (qDepth <= 0) return alpha;

  for (const mv of tacticalMoves(board, side)) {
    const captured = applyMove(board, mv);
    const v = -quiescence(board, opposite(side), -beta, -alpha, ctx, strategy, qDepth - 1);
    undoMove(board, mv, captured);

    if (v >= beta) return beta;
    if (v > alpha) alpha = v;
  }

  return alpha;
}

function negamax(board: number[][], side: Side, depth: number, alpha: number, beta: number, ctx: SearchContext, strategy: StrategyId): number {
  ctx.nodes += 1;
  checkBudget(ctx);

  const moves = legalMoves(board, side);
  if (moves.length === 0) {
    const k = kingPos(board, side);
    const inCheck = k ? isAttacked(board, k[0], k[1], (side === RED ? BLACK : RED) as Side) : false;
    return inCheck ? -900000 + (6 - depth) * 10 : 0;
  }

  if (depth <= 0) {
    return quiescence(board, side, alpha, beta, ctx, strategy);
  }

  moves.sort((a, b) => moveOrder(board, b) - moveOrder(board, a));

  let best = -1_000_000;
  for (const mv of moves) {
    const captured = applyMove(board, mv);
    const v = -negamax(board, (side === RED ? BLACK : RED) as Side, depth - 1, -beta, -alpha, ctx, strategy);
    undoMove(board, mv, captured);

    if (v > best) best = v;
    if (v > alpha) alpha = v;
    if (alpha >= beta) break;
  }

  return best;
}

function searchTop(board: number[][], side: Side, strategy: StrategyId, depth: number, timeMs: number, maxNodes: number, phase: XiangqiPhase): { moves: Move[]; nodes: number; aborted: boolean } {
  const ctx: SearchContext = {
    deadline: Date.now() + timeMs,
    maxNodes,
    nodes: 0,
  };

  const moves = legalMoves(board, side);
  moves.sort((a, b) => quickRootMoveScore(board, b, side, strategy, phase) - quickRootMoveScore(board, a, side, strategy, phase));
  const fallback = moves.slice(0, 3).map((mv) => ({
    ...mv,
    score: quickRootMoveScore(board, mv, side, strategy, phase),
  }));

  const scored: Move[] = [];
  let aborted = false;

  try {
    for (const mv of moves) {
      checkBudget(ctx);
      if (isMateMove(board, mv, side)) {
        scored.push({ ...mv, score: 950000 + moveTacticalScore(board, mv, side, strategy, phase) });
        continue;
      }

      const captured = applyMove(board, mv);
      const searchScore = -negamax(board, (side === RED ? BLACK : RED) as Side, depth - 1, -1_000_000, 1_000_000, ctx, strategy);
      undoMove(board, mv, captured);

      scored.push({ ...mv, score: searchScore + Math.round(moveTacticalScore(board, mv, side, strategy, phase) * 0.18) });
    }
  } catch {
    aborted = true;
  }

  const seen = new Set(scored.map((mv) => `${mv.fr},${mv.fc},${mv.tr},${mv.tc}`));
  for (const mv of fallback) {
    const key = `${mv.fr},${mv.fc},${mv.tr},${mv.tc}`;
    if (!seen.has(key)) {
      scored.push(mv);
      seen.add(key);
    }
  }

  scored.sort((a, b) => b.score - a.score);
  return { moves: scored.slice(0, 3), nodes: ctx.nodes, aborted };
}

function cloneBoard(board: number[][]): number[][] {
  return board.map((r) => r.slice());
}

function moveLabel(m: Move): string {
  return `${m.fr + 1},${m.fc + 1} -> ${m.tr + 1},${m.tc + 1}`;
}

function moveDisplayLabel(m: Move, parsed: ParsedBoard): string {
  const from = parsed.toDisplay(m.fr + 1, m.fc + 1);
  const to = parsed.toDisplay(m.tr + 1, m.tc + 1);
  return `${from.row},${from.col} -> ${to.row},${to.col}`;
}

function buildAssistant(
  board: number[][],
  mySide: Side,
  turnSide: Side | null,
  myTurn: boolean,
  pieceCount: number,
  strategy: StrategyId,
): AssistantResult {
  const start = Date.now();
  const phase = gamePhase(pieceCount);
  const baseDepth = pieceCount <= 14 ? 6 : pieceCount <= 24 ? 5 : 4;
  const depth = strategy === 'pro_defense' ? Math.min(6, baseDepth + 1) : baseDepth;
  const timeMs = strategy === 'pro_attack' ? 620 : strategy === 'pro_defense' ? 760 : 680;
  const maxNodes = strategy === 'pro_defense' ? 260000 : strategy === 'pro_attack' ? 220000 : 240000;

  const sideToPlay = turnSide ?? mySide;

  if (myTurn || sideToPlay === mySide) {
    const res = searchTop(cloneBoard(board), mySide, strategy, depth, timeMs, maxNodes, phase);
    return {
      suggestions: res.moves.slice(0, 3),
      depth,
      nodes: res.nodes,
      ms: Date.now() - start,
      note: res.aborted ? '\u9884\u7b97\u5230\u8fbe\uff0c\u4f7f\u7528\u5df2\u641c\u7d22\u7ed3\u679c\u3002' : '\u5df2\u5b8c\u6210\u5168\u6df1\u5ea6\u641c\u7d22\u3002',
    };
  }

  // 瀵规墜鍥炲悎锛氬厛棰勬祴瀵规墜鏈€浼樺簲鎵嬶紝鍐嶇粰鎴戞柟搴斿寤鸿銆?
  const opp = (mySide === RED ? BLACK : RED) as Side;
  const oppRes = searchTop(cloneBoard(board), opp, strategy, Math.max(3, depth - 1), Math.floor(timeMs * 0.65), Math.floor(maxNodes * 0.6), phase);
  if (oppRes.moves.length === 0) {
    return {
      suggestions: [],
      depth,
      nodes: oppRes.nodes,
      ms: Date.now() - start,
      note: '\u5bf9\u624b\u6682\u65e0\u5408\u6cd5\u7740\u6cd5\u3002',
    };
  }

  const board2 = cloneBoard(board);
  const first = oppRes.moves[0];
  board2[first.tr][first.tc] = board2[first.fr][first.fc];
  board2[first.fr][first.fc] = 0;

  const myRes = searchTop(board2, mySide, strategy, depth, timeMs, maxNodes, gamePhase(pieceCountOnBoard(board2)));
  return {
    suggestions: myRes.moves.slice(0, 3),
    depth,
    nodes: oppRes.nodes + myRes.nodes,
    ms: Date.now() - start,
    note: '\u5df2\u9884\u5224\u5bf9\u624b\u9996\u9009 ' + moveLabel(first) + '\uff0c\u7ed9\u51fa\u4f60\u7684\u5e94\u5bf9\u65b9\u6848\u3002',
  };
}

function buildLightAssistant(board: number[][], mySide: Side, pieceCount: number, strategy: StrategyId): AssistantResult {
  const start = Date.now();
  const phase = gamePhase(pieceCount);
  const moves = legalMoves(cloneBoard(board), mySide)
    .map((mv) => ({ ...mv, score: quickRootMoveScore(board, mv, mySide, strategy, phase) }))
    .sort((a, b) => b.score - a.score)
    .slice(0, 3);
  return {
    suggestions: moves,
    depth: 1,
    nodes: moves.length,
    ms: Date.now() - start,
    note: 'Pikafish 分析中，当前仅显示轻量备选，避免棋盘刷新卡顿。',
  };
}

function isLegalSuggestion(board: number[][], side: Side, mv: Move): boolean {
  if (!inBoard(mv.fr, mv.fc) || !inBoard(mv.tr, mv.tc)) return false;
  const src = board[mv.fr][mv.fc];
  const dst = board[mv.tr][mv.tc];
  if (src === 0 || pieceSide(src) !== side) return false;
  if (dst !== 0 && pieceSide(dst) === side) return false;
  return legalMoves(board, side).some((m) =>
    m.fr === mv.fr &&
    m.fc === mv.fc &&
    m.tr === mv.tr &&
    m.tc === mv.tc,
  );
}

function sanitizeSuggestions(board: number[][], side: Side | null, moves: Move[]): Move[] {
  if (!side) return [];
  const seen = new Set<string>();
  const out: Move[] = [];
  for (const mv of moves) {
    const key = `${mv.fr},${mv.fc},${mv.tr},${mv.tc}`;
    if (seen.has(key)) continue;
    seen.add(key);
    if (isLegalSuggestion(board, side, mv)) out.push(mv);
  }
  return out;
}

function riverLabel(mySide: Side | null): string {
  if (mySide === BLACK) return '\u6c49\u754c';
  return '\u695a\u6cb3';
}

function palaceLines(cell: number, pad: number): string {
  const x3 = pad + 3 * cell;
  const x5 = pad + 5 * cell;
  const y0 = pad;
  const y2 = pad + 2 * cell;
  const y7 = pad + 7 * cell;
  const y9 = pad + 9 * cell;

  return [
    `M ${x3} ${y0} L ${x5} ${y2}`,
    `M ${x5} ${y0} L ${x3} ${y2}`,
    `M ${x3} ${y7} L ${x5} ${y9}`,
    `M ${x5} ${y7} L ${x3} ${y9}`,
  ].join(' ');
}

export default function XiangqiPanel({ disabled, nickname, boardText, onMove }: Props) {
  const [from, setFrom] = useState<{ row: number; col: number; displayRow: number; displayCol: number } | null>(null);
  const [strategy, setStrategy] = useState<StrategyId>('pikafish_external');
  const [pikafishPending, setPikafishPending] = useState(false);
  const [pikafishResult, setPikafishResult] = useState<{ key: string; move: Move | null; ms: number; error?: string; enginePath?: string } | null>(null);
  const pikafishSeqRef = useRef(0);
  const pikafishCacheRef = useRef<Map<string, { move: Move | null; ms: number; error?: string; enginePath?: string }>>(new Map());

  const turn = useMemo(() => parseTurnInfo(boardText), [boardText]);
  const seats = useMemo(() => parseSeatInfo(boardText), [boardText]);
  const viewerSide: Side | null = useMemo(() => {
    if (seats.redName && seats.redName === nickname) return RED;
    if (seats.blackName && seats.blackName === nickname) return BLACK;
    return null;
  }, [seats.redName, seats.blackName, nickname]);
  const parsed = useMemo(() => parseBoard(boardText, turn.side, viewerSide), [boardText, turn.side, viewerSide]);

  const myTurn = !!turn.name && turn.name === nickname;
  const canPlay = !disabled && (!turn.name || myTurn);
  const isMaster = nickname === 'zouyu';

  const mySide: Side | null = useMemo(() => {
    if (seats.redName && seats.redName === nickname) return RED;
    if (seats.blackName && seats.blackName === nickname) return BLACK;
    if (turn.side && turn.name === nickname) return turn.side;
    return null;
  }, [seats.redName, seats.blackName, turn.side, turn.name, nickname]);

  const boardSignature = useMemo(() => {
    const s = parsed.board.map((row) => row.join(',')).join('|');
    return `${s}::${turn.side ?? 0}::${strategy}::${myTurn ? 1 : 0}`;
  }, [parsed.board, turn.side, strategy, myTurn]);

  const assistant = useMemo<AssistantResult | null>(() => {
    if (!isMaster || !mySide) return null;
    try {
      if (strategy === 'pikafish_external') {
        return buildLightAssistant(parsed.board, mySide, parsed.pieceCount, 'pro_balance');
      }
      return buildAssistant(parsed.board, mySide, turn.side, myTurn, parsed.pieceCount, strategy);
    } catch {
      return {
        suggestions: [],
        depth: 0,
        nodes: 0,
        ms: 0,
        note: '\u52a9\u624b\u8ba1\u7b97\u5f02\u5e38\uff0c\u5efa\u8bae\u5237\u65b0\u5c40\u9762\u540e\u91cd\u8bd5\u3002',
      };
    }
  }, [isMaster, mySide, boardSignature]);

  const pikafishKey = `${boardSignature}::${mySide ?? 0}`;
  useEffect(() => {
    if (!isMaster || !mySide || strategy !== 'pikafish_external' || disabled || parsed.pieceCount <= 0) {
      setPikafishPending(false);
      return;
    }
    const sideToMove = turn.side ?? mySide;
    if (sideToMove !== mySide) {
      setPikafishPending(false);
      return;
    }

    const cached = pikafishCacheRef.current.get(pikafishKey);
    if (cached) {
      setPikafishResult({ key: pikafishKey, ...cached });
      setPikafishPending(false);
      return;
    }

    const seq = pikafishSeqRef.current + 1;
    pikafishSeqRef.current = seq;
    setPikafishPending(true);

    const timeoutMs = parsed.pieceCount <= 14 ? 12000 : parsed.pieceCount <= 24 ? 10000 : 8500;
    window.api.analyzeXiangqiPikafish({
      board: parsed.board,
      side: mySide,
      timeoutMs,
    }).then((resp) => {
      if (pikafishSeqRef.current !== seq) return;
      const move = resp.ok && resp.move
        ? {
            fr: resp.move.fr - 1,
            fc: resp.move.fc - 1,
            tr: resp.move.tr - 1,
            tc: resp.move.tc - 1,
            score: 999999,
          }
        : null;
      const next = {
        move,
        ms: resp.ms,
        error: resp.ok ? undefined : (resp.error || 'Pikafish 未返回可用着法'),
        enginePath: resp.enginePath,
      };
      pikafishCacheRef.current.set(pikafishKey, next);
      while (pikafishCacheRef.current.size > 32) {
        const first = pikafishCacheRef.current.keys().next();
        if (first.done) break;
        pikafishCacheRef.current.delete(first.value);
      }
      setPikafishResult({ key: pikafishKey, ...next });
    }).catch((err) => {
      if (pikafishSeqRef.current !== seq) return;
      setPikafishResult({
        key: pikafishKey,
        move: null,
        ms: 0,
        error: err instanceof Error ? err.message : 'Pikafish 调用失败',
      });
    }).finally(() => {
      if (pikafishSeqRef.current === seq) setPikafishPending(false);
    });
  }, [isMaster, mySide, strategy, disabled, pikafishKey, parsed.pieceCount, parsed.board, turn.side]);

  const shownAssistant = useMemo<AssistantResult | null>(() => {
    if (!assistant) return null;
    const fallbackSuggestions = sanitizeSuggestions(parsed.board, mySide, assistant.suggestions);
    if (strategy !== 'pikafish_external') {
      return {
        ...assistant,
        suggestions: fallbackSuggestions,
      };
    }
    if (pikafishResult?.key === pikafishKey && pikafishResult.move) {
      const pikafishSuggestions = sanitizeSuggestions(parsed.board, mySide, [pikafishResult.move]);
      if (pikafishSuggestions.length === 0) {
        return {
          ...assistant,
          suggestions: fallbackSuggestions,
          note: `Pikafish 返回的坐标与当前棋盘不匹配，已拦截空起点建议并回退内置助手（耗时${pikafishResult.ms}ms）。`,
        };
      }
      return {
        suggestions: sanitizeSuggestions(parsed.board, mySide, [pikafishSuggestions[0], ...assistant.suggestions.filter((m) =>
          m.fr !== pikafishResult.move!.fr ||
          m.fc !== pikafishResult.move!.fc ||
          m.tr !== pikafishResult.move!.tr ||
          m.tc !== pikafishResult.move!.tc,
        )]).slice(0, 3),
        depth: assistant.depth,
        nodes: assistant.nodes,
        ms: pikafishResult.ms,
        note: `Pikafish 已给出最强建议（耗时${pikafishResult.ms}ms），内置助手作为备选。`,
      };
    }
    if (pikafishResult?.key === pikafishKey && pikafishResult.error) {
      return {
        ...assistant,
        suggestions: fallbackSuggestions,
        note: `Pikafish 暂不可用，已回退内置职业助手：${pikafishResult.error}`,
      };
    }
    if (pikafishPending) {
      return {
        ...assistant,
        suggestions: fallbackSuggestions,
        note: 'Pikafish 分析中，先显示内置职业助手建议，结果返回后自动替换。',
      };
    }
    return {
      ...assistant,
      suggestions: fallbackSuggestions,
    };
  }, [assistant, strategy, pikafishResult, pikafishKey, pikafishPending, parsed.board, mySide]);

  const cell = 48;
  const pad = 28;
  const boardW = pad * 2 + cell * 8;
  const boardH = pad * 2 + cell * 9;

  const riverY = pad + cell * 4.5;

  return (
    <div className="game-interaction-panel">
      <div className="game-interaction-title">{'\u4e2d\u56fd\u8c61\u68cb\u68cb\u76d8\uff08\u771f\u5b9e\u68cb\u76d8\uff0c\u5148\u70b9\u8d77\u70b9\u518d\u70b9\u7ec8\u70b9\uff09'}</div>
      {turn.name && !myTurn && (
        <div className="game-workbench-hint">{'\u5f53\u524d\u8f6e\u5230\uff1a'}{turn.name}{'\uff0c\u4f60\u6682\u65f6\u4e0d\u80fd\u8d70\u5b50\u3002'}</div>
      )}

      {isMaster && (
        <div className="game-advisor game-advisor-info" style={{ marginBottom: 8 }}>
          <div className="game-advisor-title">{'\u9690\u85cf\u529f\u80fd\uff1a\u8c61\u68cb\u804c\u4e1a\u52a9\u624b'}</div>
          <div className="game-chip-row" style={{ alignItems: 'center' }}>
            <select className="game-select" value={strategy} onChange={(e) => setStrategy(e.target.value as StrategyId)}>
              {STRATEGIES.map((s) => (
                <option key={s.id} value={s.id}>{s.label}</option>
              ))}
            </select>
            <span className="game-workbench-hint">
              {mySide ? ('\u4f60\u5f53\u524d\u6267' + (mySide === RED ? '\u7ea2' : '\u9ed1') + '\u3002') : '\u672a\u8bc6\u522b\u4f60\u7684\u6267\u5b50\uff08\u8bf7\u5148\u5165\u5ea7\u7ea2/\u9ed1\u65b9\uff09\u3002'}
            </span>
          </div>
          {shownAssistant && (
            <>
              <div className="game-workbench-hint">
                {strategy === 'pikafish_external' ? 'Pikafish：' : '\u641c\u7d22\u6df1\u5ea6\uff1a'}
                {strategy === 'pikafish_external'
                  ? (pikafishPending ? '\u5206\u6790\u4e2d...' : (pikafishResult?.error ? '\u56de\u9000\u5185\u7f6e' : '\u5df2\u63a5\u5165'))
                  : `${shownAssistant.depth} \u5c42\uff0c\u8282\u70b9\uff1a${shownAssistant.nodes}`}
                {'\uff0c\u8017\u65f6\uff1a'}{shownAssistant.ms}{'ms\u3002'}
              </div>
              <div className="game-workbench-hint">{shownAssistant.note}</div>
              <div className="game-chip-row">
                {shownAssistant.suggestions.length === 0 && (
                  <span className="game-workbench-hint">{'\u6682\u65e0\u53ef\u7528\u5efa\u8bae\uff1a\u5f53\u524d\u6ca1\u6709\u8bc6\u522b\u5230\u5408\u6cd5\u8d70\u6cd5\u6216\u5c40\u9762\u9700\u8981\u5237\u65b0\u3002'}</span>
                )}
                {shownAssistant.suggestions.slice(0, 3).map((mv, idx) => (
                  <button
                    key={`${mv.fr}-${mv.fc}-${mv.tr}-${mv.tc}-${idx}`}
                    className={`mini-btn ${canPlay ? 'ready' : ''}`}
                    disabled={disabled || !canPlay}
                    onClick={() => onMove(mv.fr + 1, mv.fc + 1, mv.tr + 1, mv.tc + 1)}
                    title={`\u771f\u5b9e\u5750\u6807\uff1a${moveLabel(mv)}\uff1b\u8bc4\u5206 ${mv.score}`}
                  >
                    {'\u5efa\u8bae'}{idx + 1}: {moveDisplayLabel(mv, parsed)}
                  </button>
                ))}
              </div>
            </>
          )}
        </div>
      )}

      <div className="xiangqi-board" style={{ width: boardW, height: boardH }}>
        <svg className="xiangqi-board-lines" viewBox={`0 0 ${boardW} ${boardH}`} aria-hidden>
          {Array.from({ length: 10 }, (_, r) => {
            const y = pad + r * cell;
            return <line key={`h-${r}`} x1={pad} y1={y} x2={pad + cell * 8} y2={y} />;
          })}
          {Array.from({ length: 9 }, (_, c) => {
            const x = pad + c * cell;
            if (c === 0 || c === 8) {
              return <line key={`v-${c}`} x1={x} y1={pad} x2={x} y2={pad + cell * 9} />;
            }
            return (
              <g key={`v-${c}`}>
                <line x1={x} y1={pad} x2={x} y2={pad + cell * 4} />
                <line x1={x} y1={pad + cell * 5} x2={x} y2={pad + cell * 9} />
              </g>
            );
          })}
          <path d={palaceLines(cell, pad)} />
        </svg>

        <div className="xiangqi-river-label" style={{ top: riverY - 12 }}>{riverLabel(mySide)}</div>

        {Array.from({ length: ROWS }, (_, rIx) =>
          Array.from({ length: COLS }, (_, cIx) => {
            const row = rIx + 1;
            const col = cIx + 1;
            const key = `${row}-${col}`;
            const piece = parsed.pieces.get(key);
            const selected = from?.displayRow === row && from?.displayCol === col;
            const actual = parsed.toActual(row, col);

            return (
              <button
                key={key}
                className={`xiangqi-cell xiangqi-piece ${selected ? 'selected' : ''} ${piece ? (piece.isRed ? 'red-piece' : 'black-piece') : 'empty-point'}`}
                style={{ left: pad + cIx * cell, top: pad + rIx * cell }}
                onClick={() => {
                  if (!canPlay) return;

                  if (!from) {
                    if (!piece) return;
                    if (mySide && piece.isRed !== (mySide === RED)) return;
                    setFrom({
                      row: piece.actualRow,
                      col: piece.actualCol,
                      displayRow: row,
                      displayCol: col,
                    });
                    return;
                  }

                  if (from.displayRow === row && from.displayCol === col) {
                    setFrom(null);
                    return;
                  }

                  onMove(from.row, from.col, actual.row, actual.col);
                  setFrom(null);
                }}
                disabled={!canPlay}
                title={`${row},${col}`}
              >
                {piece ? piece.symbol : ''}
              </button>
            );
          }),
        )}
      </div>

      <div className="game-chip-row">
        <button className="mini-btn" disabled={disabled || !from} onClick={() => setFrom(null)}>{'\u53d6\u6d88\u9009\u4e2d'}</button>
      </div>
    </div>
  );
}
