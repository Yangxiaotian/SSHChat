import React, { useMemo, useState } from 'react';

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

type StrategyId = 'pro_balance' | 'pro_attack' | 'pro_defense';

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
  { id: 'pro_balance', label: '职业均衡（推荐）' },
  { id: 'pro_attack', label: '职业进攻' },
  { id: 'pro_defense', label: '职业防守' },
];

const RED_UNIQUE = new Set(['帅', '仕', '相', '兵']);
const BLACK_UNIQUE = new Set(['将', '士', '象', '卒']);

function pieceTypeFromSymbol(sym: string): number | 0 {
  if (sym === '帅' || sym === '将') return PT.K;
  if (sym === '仕' || sym === '士') return PT.A;
  if (sym === '相' || sym === '象') return PT.B;
  if (sym === '马') return PT.N;
  if (sym === '车') return PT.R;
  if (sym === '炮') return PT.C;
  if (sym === '兵' || sym === '卒') return PT.P;
  return 0;
}

function parseTurnInfo(boardText: string): { name: string; side: Side | null } {
  for (const line of boardText.split('\n')) {
    const t = line.trim();
    const m = t.match(/^轮到\s*(红|黑)方\s*(\S+)\s*走子/i);
    if (m) {
      return { name: m[2].trim(), side: m[1] === '红' ? RED : BLACK };
    }
    const m2 = t.match(/^(turn|轮到)[:：]\s*(.+)$/i);
    if (m2) {
      return { name: m2[2].trim(), side: null };
    }
  }
  return { name: '', side: null };
}

function parseSeatInfo(boardText: string): { redName: string; blackName: string } {
  for (const line of boardText.split('\n')) {
    const m = line.match(/红：\s*(\S+)\s+黑：\s*(\S+)/);
    if (m) {
      return { redName: m[1].trim(), blackName: m[2].trim() };
    }
  }
  return { redName: '', blackName: '' };
}

function parseBoard(boardText: string, turnSide: Side | null): ParsedBoard {
  const pieces = new Map<string, Piece>();
  const board = Array.from({ length: ROWS }, () => Array(COLS).fill(0));
  const flipped = boardText.includes('己方在下方');
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
  const lastMoverSide = turnSide ? ((-turnSide) as Side) : null;

  // 只取“棋盘行”：每行应有 9 个格子 token（+车 / -马 / !炮 / · / *）
  const boardRows: Array<{ row: number | null; tokens: string[] }> = [];
  const tokenRe = /(?:[+\-!][^\s]{1,2}|[·*])/g;
  for (const rawLine of boardText.split('\n')) {
    const line = rawLine.trim();
    if (!line) continue;
    const tokens = line.match(tokenRe);
    if (!tokens || tokens.length < COLS) continue;
    const rowTokens = tokens.slice(0, COLS);
    const allCellLike = rowTokens.every((t) => t === '·' || t === '*' || /^[+\-!]/.test(t));
    if (!allCellLike) continue;
    const explicitRow = line.match(/^(\d{1,2})\s+/);
    const parsedRow = explicitRow ? Number(explicitRow[1]) : null;
    boardRows.push({
      row: parsedRow && parsedRow >= 1 && parsedRow <= ROWS ? parsedRow : null,
      tokens: rowTokens,
    });
    if (boardRows.length >= ROWS) break;
  }

  for (const row of boardRows) {
    const displayRow = row.row ?? rowNum;
    rowNum = Math.max(rowNum + 1, displayRow + 1);
    if (displayRow < 1 || displayRow > ROWS) continue;
    const rowTokens = row.tokens;
    for (let c = 0; c < COLS; c += 1) {
      const displayCol = c + 1;
      const actual = toActual(displayRow, displayCol);
      const cell = rowTokens[c];
      if (cell === '·' || cell === '*') continue;
      const match = cell.match(/^([+\-!])(.*)$/);
      if (!match) continue;

            const marker = match[1];
            const symbol = match[2];
      if (!symbol || symbol === '·' || symbol === '*') continue;

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
    }
  }

  const myK = kingPos(board, side);
  const oppSide = (side === RED ? BLACK : RED) as Side;
  const oppK = kingPos(board, oppSide);
  if (myK && isAttacked(board, myK[0], myK[1], oppSide)) score -= 140;
  if (oppK && isAttacked(board, oppK[0], oppK[1], side)) score += 120;

  return score;
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
  let s = 0;
  if (target !== 0) {
    s += PIECE_VALUE[pieceType(target)] * 10 - PIECE_VALUE[pt] * 2;
  }
  s += (4 - Math.abs(m.tr - 4)) * 3;
  s += (4 - Math.abs(m.tc - 4)) * 3;
  if (pt === PT.R || pt === PT.C) s += 8;
  return s;
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
    return evaluateFor(board, side, strategy);
  }

  moves.sort((a, b) => moveOrder(board, b) - moveOrder(board, a));

  let best = -1_000_000;
  for (const mv of moves) {
    const captured = board[mv.tr][mv.tc];
    board[mv.tr][mv.tc] = board[mv.fr][mv.fc];
    board[mv.fr][mv.fc] = 0;

    const v = -negamax(board, (side === RED ? BLACK : RED) as Side, depth - 1, -beta, -alpha, ctx, strategy);

    board[mv.fr][mv.fc] = board[mv.tr][mv.tc];
    board[mv.tr][mv.tc] = captured;

    if (v > best) best = v;
    if (v > alpha) alpha = v;
    if (alpha >= beta) break;
  }

  return best;
}

function searchTop(board: number[][], side: Side, strategy: StrategyId, depth: number, timeMs: number, maxNodes: number): { moves: Move[]; nodes: number; aborted: boolean } {
  const ctx: SearchContext = {
    deadline: Date.now() + timeMs,
    maxNodes,
    nodes: 0,
  };

  const moves = legalMoves(board, side);
  moves.sort((a, b) => moveOrder(board, b) - moveOrder(board, a));

  const scored: Move[] = [];
  let aborted = false;

  try {
    for (const mv of moves) {
      checkBudget(ctx);
      const captured = board[mv.tr][mv.tc];
      board[mv.tr][mv.tc] = board[mv.fr][mv.fc];
      board[mv.fr][mv.fc] = 0;

      const score = -negamax(board, (side === RED ? BLACK : RED) as Side, depth - 1, -1_000_000, 1_000_000, ctx, strategy);

      board[mv.fr][mv.fc] = board[mv.tr][mv.tc];
      board[mv.tr][mv.tc] = captured;

      scored.push({ ...mv, score });
    }
  } catch {
    aborted = true;
  }

  scored.sort((a, b) => b.score - a.score);
  return { moves: scored, nodes: ctx.nodes, aborted };
}

function cloneBoard(board: number[][]): number[][] {
  return board.map((r) => r.slice());
}

function moveLabel(m: Move): string {
  return `${m.fr + 1},${m.fc + 1} -> ${m.tr + 1},${m.tc + 1}`;
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
  const baseDepth = pieceCount <= 14 ? 5 : pieceCount <= 24 ? 4 : 3;
  const depth = strategy === 'pro_defense' ? Math.min(5, baseDepth + 1) : baseDepth;
  const timeMs = strategy === 'pro_attack' ? 340 : strategy === 'pro_defense' ? 380 : 320;
  const maxNodes = strategy === 'pro_defense' ? 110000 : 90000;

  const sideToPlay = turnSide ?? mySide;

  if (myTurn || sideToPlay === mySide) {
    const res = searchTop(cloneBoard(board), mySide, strategy, depth, timeMs, maxNodes);
    return {
      suggestions: res.moves.slice(0, 3),
      depth,
      nodes: res.nodes,
      ms: Date.now() - start,
      note: res.aborted ? '预算到达，使用已搜索结果。' : '已完成全深度搜索。',
    };
  }

  // 对手回合：先预测对手最优应手，再给我方应对建议。
  const opp = (mySide === RED ? BLACK : RED) as Side;
  const oppRes = searchTop(cloneBoard(board), opp, strategy, Math.max(2, depth - 1), Math.floor(timeMs * 0.5), Math.floor(maxNodes * 0.45));
  if (oppRes.moves.length === 0) {
    return {
      suggestions: [],
      depth,
      nodes: oppRes.nodes,
      ms: Date.now() - start,
      note: '对手暂无合法着法。',
    };
  }

  const board2 = cloneBoard(board);
  const first = oppRes.moves[0];
  board2[first.tr][first.tc] = board2[first.fr][first.fc];
  board2[first.fr][first.fc] = 0;

  const myRes = searchTop(board2, mySide, strategy, depth, timeMs, maxNodes);
  return {
    suggestions: myRes.moves.slice(0, 3),
    depth,
    nodes: oppRes.nodes + myRes.nodes,
    ms: Date.now() - start,
    note: `已预判对手首选 ${moveLabel(first)}，给出你的应对方案。`,
  };
}

function riverLabel(mySide: Side | null): string {
  if (mySide === BLACK) return '汉界';
  return '楚河';
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
  const [strategy, setStrategy] = useState<StrategyId>('pro_balance');

  const turn = useMemo(() => parseTurnInfo(boardText), [boardText]);
  const seats = useMemo(() => parseSeatInfo(boardText), [boardText]);
  const parsed = useMemo(() => parseBoard(boardText, turn.side), [boardText, turn.side]);

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
      return buildAssistant(parsed.board, mySide, turn.side, myTurn, parsed.pieceCount, strategy);
    } catch {
      return {
        suggestions: [],
        depth: 0,
        nodes: 0,
        ms: 0,
        note: '助手计算异常，建议刷新局面后重试。',
      };
    }
  }, [isMaster, mySide, boardSignature]);

  const cell = 48;
  const pad = 20;
  const boardW = pad * 2 + cell * 8;
  const boardH = pad * 2 + cell * 9;

  const riverY = pad + cell * 4.5;

  return (
    <div className="game-interaction-panel">
      <div className="game-interaction-title">中国象棋棋盘（真实棋盘，先点起点再点终点）</div>
      {turn.name && !myTurn && (
        <div className="game-workbench-hint">当前轮到：{turn.name}，你暂时不能走子。</div>
      )}

      {isMaster && (
        <div className="game-advisor game-advisor-info" style={{ marginBottom: 8 }}>
          <div className="game-advisor-title">隐藏功能：象棋职业助手</div>
          <div className="game-chip-row" style={{ alignItems: 'center' }}>
            <select className="game-select" value={strategy} onChange={(e) => setStrategy(e.target.value as StrategyId)}>
              {STRATEGIES.map((s) => (
                <option key={s.id} value={s.id}>{s.label}</option>
              ))}
            </select>
            <span className="game-workbench-hint">
              {mySide ? `你当前执${mySide === RED ? '红' : '黑'}。` : '未识别你的执子（先入座红/黑方）。'}
            </span>
          </div>
          {assistant && (
            <>
              <div className="game-workbench-hint">
                搜索深度：{assistant.depth} 层，节点：{assistant.nodes}，耗时：{assistant.ms}ms。
              </div>
              <div className="game-workbench-hint">{assistant.note}</div>
              <div className="game-chip-row">
                {assistant.suggestions.slice(0, 3).map((mv, idx) => (
                  <button
                    key={`${mv.fr}-${mv.fc}-${mv.tr}-${mv.tc}-${idx}`}
                    className={`mini-btn ${(canPlay && myTurn) ? 'ready' : ''}`}
                    disabled={disabled || !canPlay || !myTurn}
                    onClick={() => onMove(mv.fr + 1, mv.fc + 1, mv.tr + 1, mv.tc + 1)}
                    title={`评分 ${mv.score}`}
                  >
                    建议{idx + 1}: {moveLabel(mv)}
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
        <button className="mini-btn" disabled={disabled || !from} onClick={() => setFrom(null)}>取消选中</button>
      </div>
    </div>
  );
}
