import React, { useMemo } from 'react';

type Props = {
  disabled: boolean;
  nickname: string;
  boardText: string;
  onPick: (row: number, col: number) => void;
};

type Cell = {
  stone: '.' | '#' | 'o';
  last: boolean;
  row: number;
  col: number;
};

type Side = '#' | 'o';

type StrategyId =
  | 'auto'
  | 'master_balance'
  | 'killer_combo'
  | 'trap_double_three'
  | 'defense_counter'
  | 'attack_focus'
  | 'sente_play'
  | 'influence_play'
  | 'opening_tianyuan'
  | 'opening_star'
  | 'opening_diagonal'
  | 'opening_huayue'
  | 'opening_yuyue';

type MoveEval = {
  row: number;
  col: number;
  score: number;
  reasons: string[];
};

const BOARD_SIZE = 15;
const SEARCH_DEPTH = 8;
const TIME_LIMIT_MS = 2500;

const STRATEGY_LABEL: Record<StrategyId, string> = {
  auto: '智能自适应（推荐）',
  master_balance: '大师均衡流',
  killer_combo: '必胜杀棋流（冲四做杀）',
  trap_double_three: '陷阱双三流（诱导反杀）',
  defense_counter: '铁壁反击流（先守后攻）',
  attack_focus: '凌厉攻势流（连续做杀）',
  sente_play: '先手掌控流（永不脱先）',
  influence_play: '厚势压迫流（外势为王）',
  opening_tianyuan: '开局·天元压制',
  opening_star: '开局·星位牵制',
  opening_diagonal: '开局·斜月穿心',
  opening_huayue: '开局·花月经典',
  opening_yuyue: '开局·雨月稳健',
};

// ── Professional Opening Book ──────────────────────────────────────
// Standard openings for 15x15 Gomoku, 1-indexed [row, col]
const OPENING_BOOK: Record<'#' | 'o', Record<StrategyId, Array<[number, number]>>> = {
  '#': {
    auto: [[8, 8], [8, 9], [9, 8], [8, 7], [7, 8], [9, 9], [7, 7], [7, 9], [9, 7], [6, 8], [10, 8]],
    master_balance: [[8, 8], [8, 9], [9, 8], [8, 7], [7, 8], [9, 9], [7, 7], [7, 9], [9, 7], [6, 8], [10, 8]],
    killer_combo: [[8, 8], [8, 9], [9, 8], [7, 8], [8, 10], [10, 8], [9, 9], [6, 8], [7, 10], [10, 7]],
    trap_double_three: [[8, 8], [7, 8], [9, 8], [8, 7], [8, 9], [7, 9], [9, 7], [7, 7], [9, 9], [6, 8], [10, 8]],
    defense_counter: [[8, 8], [8, 7], [8, 9], [7, 8], [9, 8], [7, 7], [9, 9], [7, 9], [9, 7], [6, 7], [10, 9]],
    attack_focus: [[8, 8], [8, 9], [9, 9], [7, 7], [9, 8], [10, 7], [7, 10], [8, 10], [10, 8], [6, 8]],
    sente_play: [[8, 8], [8, 9], [9, 8], [7, 8], [8, 7], [9, 9], [7, 7], [10, 8], [8, 10], [6, 8]],
    influence_play: [[8, 8], [7, 7], [9, 9], [7, 9], [9, 7], [6, 6], [10, 10], [6, 10], [10, 6], [8, 6]],
    opening_tianyuan: [[8, 8], [8, 9], [9, 8], [8, 7], [7, 8], [9, 9], [7, 7], [7, 9], [9, 7], [6, 8], [10, 8]],
    opening_star: [[8, 8], [7, 7], [9, 9], [7, 9], [9, 7], [6, 8], [10, 8], [8, 6], [8, 10], [5, 7]],
    opening_diagonal: [[8, 8], [7, 9], [9, 7], [7, 7], [9, 9], [6, 10], [10, 6], [6, 6], [10, 10], [8, 6]],
    opening_huayue: [[8, 8], [9, 8], [9, 9], [7, 7], [8, 7], [10, 8], [7, 8], [7, 9], [10, 10], [6, 6]],
    opening_yuyue: [[8, 8], [7, 8], [8, 9], [9, 7], [7, 7], [8, 6], [9, 9], [10, 8], [6, 8], [7, 10]],
  },
  o: {
    auto: [[8, 9], [9, 8], [8, 7], [7, 8], [9, 9], [7, 7], [9, 7], [7, 9], [10, 8], [6, 8]],
    master_balance: [[8, 9], [9, 8], [8, 7], [7, 8], [9, 9], [7, 7], [9, 7], [7, 9], [10, 8], [6, 8]],
    killer_combo: [[8, 9], [9, 8], [8, 7], [7, 8], [9, 9], [7, 7], [10, 8], [8, 10], [6, 8], [10, 7]],
    trap_double_three: [[7, 8], [9, 8], [8, 7], [8, 9], [7, 9], [9, 7], [7, 7], [9, 9], [10, 8], [6, 8]],
    defense_counter: [[8, 9], [8, 7], [7, 8], [9, 8], [7, 7], [9, 9], [7, 9], [9, 7], [6, 7], [10, 9]],
    attack_focus: [[8, 9], [9, 9], [7, 7], [8, 7], [9, 8], [10, 7], [7, 10], [6, 8], [10, 8], [8, 10]],
    sente_play: [[8, 9], [9, 8], [8, 7], [7, 8], [9, 9], [7, 7], [10, 8], [8, 10], [6, 8], [7, 10]],
    influence_play: [[7, 7], [9, 9], [7, 9], [9, 7], [6, 6], [10, 10], [6, 10], [10, 6], [8, 6], [6, 8]],
    opening_tianyuan: [[8, 9], [9, 8], [8, 7], [7, 8], [9, 9], [7, 7], [7, 9], [9, 7], [10, 8], [6, 8]],
    opening_star: [[7, 7], [9, 9], [7, 9], [9, 7], [8, 6], [8, 10], [6, 8], [10, 8], [5, 7], [11, 9]],
    opening_diagonal: [[7, 9], [9, 7], [7, 7], [9, 9], [6, 10], [10, 6], [6, 6], [10, 10], [8, 6], [6, 8]],
    opening_huayue: [[8, 7], [7, 8], [9, 9], [7, 7], [10, 8], [6, 8], [8, 10], [7, 10], [9, 6], [10, 10]],
    opening_yuyue: [[8, 9], [7, 8], [9, 7], [8, 7], [7, 7], [9, 9], [10, 8], [6, 8], [8, 6], [10, 6]],
  },
};

// ── Board Parsing (unchanged) ──────────────────────────────────────

function parseTurnName(boardText: string): string {
  for (const line of boardText.split('\n')) {
    const t = line.trim();
    const m = t.match(/^轮到\s+(黑|白)方\s+(.+)\s+落子$/);
    if (m) return m[2].trim();
  }
  return '';
}

function parseTurnSide(boardText: string): Side | null {
  for (const line of boardText.split('\n')) {
    const t = line.trim();
    const m = t.match(/^轮到\s+(黑|白)方\s+.+\s+落子$/);
    if (m) return m[1] === '黑' ? '#' : 'o';
  }
  return null;
}

function parseSeatedSides(boardText: string): { blackName: string; whiteName: string } {
  let blackName = '';
  let whiteName = '';
  for (const raw of boardText.split('\n')) {
    const line = raw.trim();
    const m1 = line.match(/黑：(.+?)\s+白：(.+)$/);
    if (m1) {
      blackName = m1[1].trim();
      whiteName = m1[2].trim();
      break;
    }
    const m2 = line.match(/^黑方（先手）：(.+)$/);
    const m3 = line.match(/^白方：(.+)$/);
    if (m2) blackName = m2[1].trim();
    if (m3) whiteName = m3[1].trim();
  }
  return { blackName, whiteName };
}

function parseBoard(boardText: string): Cell[][] {
  const lines = boardText.split('\n');
  const rowLines = lines.filter((l) => /^\s*\d+\s+.*[.#o(]/.test(l));
  if (rowLines.length < BOARD_SIZE) return [];

  const header = lines.find((l) => /^\s+\d+\s+\d+/.test(l));
  const headerCols = (header?.match(/\d+/g) || []).map((n) => Number(n));
  if (headerCols.length !== BOARD_SIZE) return [];
  const flipped = headerCols[0] > headerCols[headerCols.length - 1];

  const out: Cell[][] = [];
  for (const rowLine of rowLines.slice(0, BOARD_SIZE)) {
    const rowMatch = rowLine.match(/^\s*(\d+)\s+/);
    if (!rowMatch) continue;
    const rowNum = Number(rowMatch[1]);
    const mappedRow = flipped ? BOARD_SIZE + 1 - rowNum : rowNum;
    const payload = rowLine.slice(rowMatch[0].length);
    const tokens = payload.match(/\(#\)|\(o\)|\(\.\)|[#.o]/g);
    if (!tokens || tokens.length !== BOARD_SIZE) continue;

    out.push(
      tokens.map((token, idx) => {
        const last = token.startsWith('(');
        const plain = (last ? token[1] : token[0]) as '#' | 'o' | '.';
        return {
          stone: plain === '#' || plain === 'o' ? plain : '.',
          last,
          row: mappedRow,
          col: headerCols[idx],
        };
      }),
    );
  }
  return out.length === BOARD_SIZE ? out : [];
}

function defaultBoard(): Cell[][] {
  return Array.from({ length: BOARD_SIZE }, (_, rIx) =>
    Array.from({ length: BOARD_SIZE }, (_, cIx) => ({
      stone: '.' as const,
      last: false,
      row: rIx + 1,
      col: cIx + 1,
    })),
  );
}

function parseMatrix(cells: Cell[][]): number[][] {
  const board = Array.from({ length: BOARD_SIZE }, () => Array.from({ length: BOARD_SIZE }, () => 0));
  for (const row of cells) {
    for (const cell of row) {
      const r = cell.row - 1;
      const c = cell.col - 1;
      board[r][c] = cell.stone === '#' ? 1 : cell.stone === 'o' ? -1 : 0;
    }
  }
  return board;
}

// ── Core Engine ────────────────────────────────────────────────────

function inside(r: number, c: number): boolean {
  return r >= 0 && r < BOARD_SIZE && c >= 0 && c < BOARD_SIZE;
}

function moveCount(board: number[][]): number {
  let n = 0;
  for (let r = 0; r < BOARD_SIZE; r++) {
    for (let c = 0; c < BOARD_SIZE; c++) {
      if (board[r][c] !== 0) n += 1;
    }
  }
  return n;
}

function hasNeighbor(board: number[][], r: number, c: number, dist: number): boolean {
  const rMin = Math.max(0, r - dist);
  const rMax = Math.min(BOARD_SIZE - 1, r + dist);
  const cMin = Math.max(0, c - dist);
  const cMax = Math.min(BOARD_SIZE - 1, c + dist);
  for (let rr = rMin; rr <= rMax; rr++) {
    for (let cc = cMin; cc <= cMax; cc++) {
      if (rr === r && cc === c) continue;
      if (board[rr][cc] !== 0) return true;
    }
  }
  return false;
}

function cloneBoard(board: number[][]): number[][] {
  return board.map((row) => row.slice());
}

function boardKey(board: number[][]): string {
  let key = '';
  for (let r = 0; r < BOARD_SIZE; r++) {
    for (let c = 0; c < BOARD_SIZE; c++) {
      key += board[r][c] === 1 ? '1' : board[r][c] === -1 ? '2' : '0';
    }
  }
  return key;
}

function hasFive(board: number[][], r: number, c: number, side: number): boolean {
  const dirs: Array<[number, number]> = [[1, 0], [0, 1], [1, 1], [1, -1]];
  for (const [dr, dc] of dirs) {
    let count = 1;
    let rr = r + dr, cc = c + dc;
    while (inside(rr, cc) && board[rr][cc] === side) { count++; rr += dr; cc += dc; }
    rr = r - dr; cc = c - dc;
    while (inside(rr, cc) && board[rr][cc] === side) { count++; rr -= dr; cc -= dc; }
    if (count >= 5) return true;
  }
  return false;
}

// ── Pattern Evaluation ─────────────────────────────────────────────

type PatternCount = {
  five: number;
  liveFour: number;
  deadFour: number;
  liveThree: number;
  deadThree: number;
  liveTwo: number;
};

function countPatterns(board: number[][], side: number): PatternCount {
  const result: PatternCount = { five: 0, liveFour: 0, deadFour: 0, liveThree: 0, deadThree: 0, liveTwo: 0 };
  const dirs: Array<[number, number]> = [[1, 0], [0, 1], [1, 1], [1, -1]];
  const opp = -side;

  for (let r = 0; r < BOARD_SIZE; r++) {
    for (let c = 0; c < BOARD_SIZE; c++) {
      if (board[r][c] !== side) continue;
      for (const [dr, dc] of dirs) {
        // Avoid double-counting: only count from the "start" of each line
        const pr = r - dr, pc = c - dc;
        if (inside(pr, pc) && board[pr][pc] === side) continue;

        // Count consecutive stones
        let len = 0;
        let rr = r, cc = c;
        while (inside(rr, cc) && board[rr][cc] === side) { len++; rr += dr; cc += dc; }

        // Check openness on each end
        const leftOpen = true; // we started from the edge of the group
        const rightOpen = inside(rr, cc) && board[rr][cc] === 0;

        // Scan further for gaps (broken patterns)
        // Pattern: side*len + 0 + side (e.g., XX_X or XXX_X)
        let gapLen = 0;
        if (inside(rr, cc) && board[rr][cc] === 0) {
          const gr = rr + dr, gc = cc + dc;
          if (inside(gr, gc) && board[gr][gc] === side) {
            let gr2 = gr, gc2 = gc;
            while (inside(gr2, gc2) && board[gr2][gc2] === side) { gapLen++; gr2 += dr; gc2 += dc; }
          }
        }

        const totalWithGap = len + gapLen;

        if (len >= 5) {
          result.five++;
        } else if (len === 4) {
          if (leftOpen && rightOpen) result.liveFour++;
          else if (rightOpen) result.deadFour++;
        } else if (len === 3) {
          if (leftOpen && rightOpen) result.liveThree++;
          else if (rightOpen) result.deadThree++;
        } else if (len === 2) {
          if (leftOpen && rightOpen) result.liveTwo++;
        }

        // Gap patterns: XX_X (broken four) or X_XX
        if (totalWithGap === 4 && gapLen > 0) {
          // This is a broken four / jump four
          result.deadFour++;
        }
        if (totalWithGap === 3 && gapLen > 0 && rightOpen) {
          result.deadThree++;
        }
      }
    }
  }
  return result;
}

function evaluateBoard(board: number[][]): number {
  const mine = countPatterns(board, 1);
  const opp = countPatterns(board, -1);

  // Five: instant win
  if (mine.five > 0) return 100000000;
  if (opp.five > 0) return -100000000;

  // Live four: unstoppable
  if (mine.liveFour > 0) return 50000000;
  if (opp.liveFour > 0) return -50000000;

  // Dead four + live three: forcing win
  if (mine.deadFour >= 2 || (mine.deadFour >= 1 && mine.liveThree >= 1)) return 10000000;
  if (opp.deadFour >= 2 || (opp.deadFour >= 1 && opp.liveThree >= 1)) return -10000000;

  // Double live three: very strong
  if (mine.liveThree >= 2) return 5000000;
  if (opp.liveThree >= 2) return -5000000;

  let score = 0;
  score += mine.deadFour * 800000;
  score += mine.liveThree * 500000;
  score += mine.deadThree * 50000;
  score += mine.liveTwo * 8000;
  score -= opp.deadFour * 800000;
  score -= opp.liveThree * 500000;
  score -= opp.deadThree * 50000;
  score -= opp.liveTwo * 8000;

  // Counter-attack potential: reward positions that create multi-directional threats
  // This helps turn defense into offense (化被动为主动)
  let myDirs = 0, oppDirs = 0;
  const dirs: Array<[number, number]> = [[1, 0], [0, 1], [1, 1], [1, -1]];
  for (let r = 0; r < BOARD_SIZE; r++) {
    for (let c = 0; c < BOARD_SIZE; c++) {
      if (board[r][c] === 0) continue;
      const side = board[r][c];
      for (const [dr, dc] of dirs) {
        const pr = r - dr, pc = c - dc;
        if (inside(pr, pc) && board[pr][pc] === side) continue;
        let len = 0;
        let rr = r, cc = c;
        while (inside(rr, cc) && board[rr][cc] === side) { len++; rr += dr; cc += dc; }
        if (len >= 2) {
          const endOpen = inside(rr, cc) && board[rr][cc] === 0;
          if (endOpen) {
            if (side === 1) myDirs++;
            else oppDirs++;
          }
        }
      }
    }
  }
  // More directional threats = more counter-attack potential
  score += (myDirs - oppDirs) * 3000;

  // Center preference
  for (let r = 0; r < BOARD_SIZE; r++) {
    for (let c = 0; c < BOARD_SIZE; c++) {
      if (board[r][c] !== 0) {
        const centerDist = Math.abs(r - 7) + Math.abs(c - 7);
        score += board[r][c] * (14 - centerDist) * -30;
      }
    }
  }

  return score;
}

// ── Strategy-Weighted Evaluation ───────────────────────────────────

const STRATEGY_WEIGHTS: Record<StrategyId, {
  myFive: number; myLiveFour: number; myDeadFour: number; myLiveThree: number; myDeadThree: number; myLiveTwo: number;
  oppFive: number; oppLiveFour: number; oppDeadFour: number; oppLiveThree: number; center: number;
}> = {
  auto: { myFive: 100000000, myLiveFour: 50000000, myDeadFour: 800000, myLiveThree: 500000, myDeadThree: 50000, myLiveTwo: 8000, oppFive: 95000000, oppLiveFour: 45000000, oppDeadFour: 750000, oppLiveThree: 400000, center: -30 },
  master_balance: { myFive: 100000000, myLiveFour: 50000000, myDeadFour: 800000, myLiveThree: 500000, myDeadThree: 50000, myLiveTwo: 8000, oppFive: 95000000, oppLiveFour: 45000000, oppDeadFour: 750000, oppLiveThree: 400000, center: -30 },
  killer_combo: { myFive: 100000000, myLiveFour: 55000000, myDeadFour: 1000000, myLiveThree: 600000, myDeadThree: 60000, myLiveTwo: 7000, oppFive: 90000000, oppLiveFour: 40000000, oppDeadFour: 650000, oppLiveThree: 350000, center: -25 },
  trap_double_three: { myFive: 100000000, myLiveFour: 48000000, myDeadFour: 900000, myLiveThree: 700000, myDeadThree: 90000, myLiveTwo: 20000, oppFive: 88000000, oppLiveFour: 42000000, oppDeadFour: 700000, oppLiveThree: 380000, center: -28 },
  defense_counter: { myFive: 100000000, myLiveFour: 45000000, myDeadFour: 700000, myLiveThree: 400000, myDeadThree: 40000, myLiveTwo: 6000, oppFive: 98000000, oppLiveFour: 48000000, oppDeadFour: 900000, oppLiveThree: 500000, center: -20 },
  attack_focus: { myFive: 100000000, myLiveFour: 58000000, myDeadFour: 1200000, myLiveThree: 650000, myDeadThree: 70000, myLiveTwo: 10000, oppFive: 85000000, oppLiveFour: 38000000, oppDeadFour: 600000, oppLiveThree: 300000, center: -35 },
  sente_play: { myFive: 100000000, myLiveFour: 52000000, myDeadFour: 850000, myLiveThree: 550000, myDeadThree: 55000, myLiveTwo: 9000, oppFive: 92000000, oppLiveFour: 44000000, oppDeadFour: 720000, oppLiveThree: 420000, center: -30 },
  influence_play: { myFive: 100000000, myLiveFour: 50000000, myDeadFour: 750000, myLiveThree: 480000, myDeadThree: 45000, myLiveTwo: 12000, oppFive: 90000000, oppLiveFour: 42000000, oppDeadFour: 680000, oppLiveThree: 380000, center: -50 },
  opening_tianyuan: { myFive: 100000000, myLiveFour: 50000000, myDeadFour: 800000, myLiveThree: 500000, myDeadThree: 50000, myLiveTwo: 8000, oppFive: 95000000, oppLiveFour: 45000000, oppDeadFour: 750000, oppLiveThree: 400000, center: -40 },
  opening_star: { myFive: 100000000, myLiveFour: 50000000, myDeadFour: 800000, myLiveThree: 500000, myDeadThree: 50000, myLiveTwo: 10000, oppFive: 95000000, oppLiveFour: 45000000, oppDeadFour: 750000, oppLiveThree: 400000, center: -35 },
  opening_diagonal: { myFive: 100000000, myLiveFour: 50000000, myDeadFour: 800000, myLiveThree: 520000, myDeadThree: 55000, myLiveTwo: 12000, oppFive: 95000000, oppLiveFour: 45000000, oppDeadFour: 750000, oppLiveThree: 400000, center: -30 },
  opening_huayue: { myFive: 100000000, myLiveFour: 50000000, myDeadFour: 850000, myLiveThree: 550000, myDeadThree: 55000, myLiveTwo: 9000, oppFive: 95000000, oppLiveFour: 45000000, oppDeadFour: 750000, oppLiveThree: 400000, center: -32 },
  opening_yuyue: { myFive: 100000000, myLiveFour: 50000000, myDeadFour: 780000, myLiveThree: 480000, myDeadThree: 48000, myLiveTwo: 7500, oppFive: 96000000, oppLiveFour: 46000000, oppDeadFour: 780000, oppLiveThree: 420000, center: -28 },
};

function evaluateWithStrategy(board: number[][], strategy: StrategyId): number {
  const w = STRATEGY_WEIGHTS[strategy];
  const mine = countPatterns(board, 1);
  const opp = countPatterns(board, -1);

  if (mine.five > 0) return w.myFive;
  if (opp.five > 0) return -w.oppFive;
  if (mine.liveFour > 0) return w.myLiveFour;
  if (opp.liveFour > 0) return -w.oppLiveFour;

  if (mine.deadFour >= 2 || (mine.deadFour >= 1 && mine.liveThree >= 1)) return w.myDeadFour * 12;
  if (opp.deadFour >= 2 || (opp.deadFour >= 1 && opp.liveThree >= 1)) return -w.oppDeadFour * 12;

  if (mine.liveThree >= 2) return w.myLiveThree * 10;
  if (opp.liveThree >= 2) return -w.oppLiveThree * 10;

  let score = 0;
  score += mine.deadFour * w.myDeadFour;
  score += mine.liveThree * w.myLiveThree;
  score += mine.deadThree * (w.myDeadThree || 50000);
  score += mine.liveTwo * (w.myLiveTwo || 8000);
  score -= opp.deadFour * w.oppDeadFour;
  score -= opp.liveThree * w.oppLiveThree;
  score -= opp.deadThree * (w.myDeadThree || 50000);
  score -= opp.liveTwo * (w.myLiveTwo || 8000);

  for (let r = 0; r < BOARD_SIZE; r++) {
    for (let c = 0; c < BOARD_SIZE; c++) {
      if (board[r][c] !== 0) {
        const centerDist = Math.abs(r - 7) + Math.abs(c - 7);
        score += board[r][c] * (14 - centerDist) * w.center;
      }
    }
  }
  return score;
}

// ── Move Generation & Ordering ─────────────────────────────────────

function genCandidates(board: number[][], limit: number): Array<[number, number]> {
  const moves = moveCount(board);
  const candidates: Array<[number, number, number]> = []; // [r, c, threatScore]

  for (let r = 0; r < BOARD_SIZE; r++) {
    for (let c = 0; c < BOARD_SIZE; c++) {
      if (board[r][c] !== 0) continue;
      if (moves > 0 && !hasNeighbor(board, r, c, 2)) continue;

      // Score this candidate by the patterns it creates/blocks
      let threatScore = 0;

      board[r][c] = 1;
      const myP = countPatterns(board, 1);
      if (myP.five > 0) threatScore += 100000000;
      if (myP.liveFour > 0) threatScore += 5000000;
      if (myP.deadFour > 0) threatScore += 800000;
      if (myP.liveThree > 0) threatScore += 500000;
      if (myP.deadThree > 0) threatScore += 50000;
      board[r][c] = 0;

      board[r][c] = -1;
      const oppP = countPatterns(board, -1);
      if (oppP.five > 0) threatScore += 95000000;
      if (oppP.liveFour > 0) threatScore += 4500000;
      if (oppP.deadFour > 0) threatScore += 750000;
      if (oppP.liveThree > 0) threatScore += 400000;
      board[r][c] = 0;

      candidates.push([r, c, threatScore]);
    }
  }

  candidates.sort((a, b) => b[2] - a[2]);
  return candidates.slice(0, limit).map(([r, c]) => [r, c]);
}

// ── VCF/VCT Solver (Threat-Space Search) ───────────────────────────
// Searches for forced-win sequences using only threat moves (fours).

function findWinFour(board: number[][], side: number): [number, number] | null {
  const opp = -side;
  for (let r = 0; r < BOARD_SIZE; r++) {
    for (let c = 0; c < BOARD_SIZE; c++) {
      if (board[r][c] !== 0) continue;
      board[r][c] = side;
      const win = hasFive(board, r, c, side);
      board[r][c] = 0;
      if (win) return [r, c];
    }
  }
  return null;
}

function findBlockFour(board: number[][], side: number): [number, number] | null {
  // Find a move that blocks the opponent's four
  const opp = -side;
  for (let r = 0; r < BOARD_SIZE; r++) {
    for (let c = 0; c < BOARD_SIZE; c++) {
      if (board[r][c] !== 0) continue;
      board[r][c] = opp;
      const threat = hasFive(board, r, c, opp);
      board[r][c] = 0;
      if (threat) return [r, c];
    }
  }
  return null;
}

function findMyFours(board: number[][], side: number): Array<[number, number]> {
  const fours: Array<[number, number]> = [];
  for (let r = 0; r < BOARD_SIZE; r++) {
    for (let c = 0; c < BOARD_SIZE; c++) {
      if (board[r][c] !== 0) continue;
      board[r][c] = side;
      if (hasFive(board, r, c, side)) fours.push([r, c]);
      board[r][c] = 0;
    }
  }
  return fours;
}

function findMyThreats(board: number[][], side: number): Array<[number, number]> {
  // Threats: moves that create a dead four (forcing response)
  const threats: Array<[number, number]> = [];
  const opp = -side;
  for (let r = 0; r < BOARD_SIZE; r++) {
    for (let c = 0; c < BOARD_SIZE; c++) {
      if (board[r][c] !== 0) continue;
      board[r][c] = side;
      // Check if this move creates a four (dead or live)
      const dirs: Array<[number, number]> = [[1, 0], [0, 1], [1, 1], [1, -1]];
      let createsFour = false;
      for (const [dr, dc] of dirs) {
        let count = 1;
        let rr = r + dr, cc = c + dc;
        while (inside(rr, cc) && board[rr][cc] === side) { count++; rr += dr; cc += dc; }
        rr = r - dr; cc = c - dc;
        while (inside(rr, cc) && board[rr][cc] === side) { count++; rr -= dr; cc -= dc; }
        if (count === 4) { createsFour = true; break; }
      }
      board[r][c] = 0;
      if (createsFour) threats.push([r, c]);
    }
  }
  return threats;
}

function solveVCF(board: number[][], side: Side, depth: number, maxDepth: number): [number, number] | null {
  if (depth >= maxDepth) return null;
  const s = side === '#' ? 1 : -1;

  // 1. Can we win immediately?
  const win = findWinFour(board, s);
  if (win) return win;

  // 2. Find all forcing moves (create a four)
  const threats = findMyThreats(board, s);
  if (threats.length === 0) return null;

  for (const [tr, tc] of threats) {
    board[tr][tc] = s;

    // Opponent must block
    const block = findBlockFour(board, s);
    if (!block) {
      board[tr][tc] = 0;
      return [tr, tc];
    }

    // Opponent blocks
    board[block[0]][block[1]] = -s;

    // Recurse
    const result = solveVCF(board, side, depth + 2, maxDepth);
    if (result) {
      board[block[0]][block[1]] = 0;
      board[tr][tc] = 0;
      return [tr, tc];
    }

    board[block[0]][block[1]] = 0;
    board[tr][tc] = 0;
  }

  return null;
}

// ── Alpha-Beta Search with Iterative Deepening ─────────────────────

let searchNodeCount = 0;
let searchDeadline = 0;
let searchTimeUp = false;

function negamax(board: number[][], depth: number, alpha: number, beta: number, color: number): number {
  searchNodeCount++;
  if (searchNodeCount % 1024 === 0 && Date.now() > searchDeadline) {
    searchTimeUp = true;
    return 0;
  }

  // Terminal check: does the previous move win?
  const eval0 = evaluateBoard(board);
  if (Math.abs(eval0) >= 100000000) return eval0 * color;

  if (depth <= 0) return eval0 * color;

  const candidates = genCandidates(board, 20);
  if (candidates.length === 0) return eval0 * color;

  let best = -Infinity;
  for (const [r, c] of candidates) {
    board[r][c] = color;
    const score = -negamax(board, depth - 1, -beta, -alpha, -color);
    board[r][c] = 0;

    if (searchTimeUp) return best === -Infinity ? 0 : best;

    if (score > best) best = score;
    if (best > alpha) alpha = best;
    if (alpha >= beta) break;
  }
  return best;
}

function findBestMove(board: number[][], side: Side, strategy: StrategyId): MoveEval | null {
  const color = side === '#' ? 1 : -1;
  const candidates = genCandidates(board, 25);
  if (candidates.length === 0) return null;

  // Quick win check
  for (const [r, c] of candidates) {
    board[r][c] = color;
    if (hasFive(board, r, c, color)) {
      board[r][c] = 0;
      return { row: r + 1, col: c + 1, score: 100000000, reasons: ['此手可直接连五终结！'] };
    }
    board[r][c] = 0;
  }

  // Quick block check - find all blocking moves
  const oppColor = -color;
  const blockMoves: Array<[number, number]> = [];
  for (const [r, c] of candidates) {
    board[r][c] = oppColor;
    if (hasFive(board, r, c, oppColor)) {
      blockMoves.push([r, c]);
    }
    board[r][c] = 0;
  }

  if (blockMoves.length > 0) {
    // Multiple blocking options: prefer the one that also creates counter-threats (化被动为主动)
    let bestBlock = blockMoves[0];
    let bestBlockScore = -Infinity;

    for (const [r, c] of blockMoves) {
      board[r][c] = color;
      const myAfterBlock = countPatterns(board, color);
      board[r][c] = 0;

      let blockScore = 0;
      if (myAfterBlock.liveFour > 0) blockScore += 50000000;
      if (myAfterBlock.deadFour > 0) blockScore += 800000;
      if (myAfterBlock.liveThree > 0) blockScore += 500000;
      if (myAfterBlock.deadThree > 0) blockScore += 50000;

      if (blockScore > bestBlockScore) {
        bestBlockScore = blockScore;
        bestBlock = [r, c];
      }
    }

    const hasCounter = bestBlockScore > 100000;
    const reason = hasCounter
      ? '必须封堵对手成五，且此手同时制造反击威胁！'
      : '对手即将成五，必须封堵！';
    return { row: bestBlock[0] + 1, col: bestBlock[1] + 1, score: 95000000, reasons: [reason] };
  }

  // VCF solver
  const vcfMove = solveVCF(cloneBoard(board), color === 1 ? '#' : 'o', 0, 14);
  if (vcfMove) {
    return { row: vcfMove[0] + 1, col: vcfMove[1] + 1, score: 80000000, reasons: ['找到连续冲四必胜路线！'] };
  }

  // Alpha-beta iterative deepening
  searchDeadline = Date.now() + TIME_LIMIT_MS;
  searchTimeUp = false;

  let bestMove: [number, number] = candidates[0];
  let bestScore = -Infinity;

  for (let depth = 2; depth <= SEARCH_DEPTH; depth += 2) {
    searchNodeCount = 0;
    searchTimeUp = false;
    searchDeadline = Date.now() + TIME_LIMIT_MS;

    let depthBest = -Infinity;
    let depthBestMove = candidates[0];

    for (const [r, c] of candidates) {
      if (searchTimeUp) break;

      board[r][c] = color;
      const score = -negamax(board, depth - 1, -Infinity, -depthBest, -color);
      board[r][c] = 0;

      if (!searchTimeUp && score > depthBest) {
        depthBest = score;
        depthBestMove = [r, c];
      }
    }

    if (!searchTimeUp) {
      bestMove = depthBestMove;
      bestScore = depthBest;
    }
  }

  // Build reasons with counter-attack awareness
  const reasons: string[] = [];
  board[bestMove[0]][bestMove[1]] = color;
  const myP = countPatterns(board, color);
  const oppP = countPatterns(board, -color);
  board[bestMove[0]][bestMove[1]] = 0;

  if (myP.liveFour > 0) reasons.push('形成活四，必胜之势。');
  else if (myP.deadFour >= 2) reasons.push('形成双冲四，强制取胜。');
  else if (myP.deadFour > 0 && myP.liveThree > 0) reasons.push('冲四+活三连续进攻，化守为攻。');
  else if (myP.liveThree >= 2) reasons.push('双活三，复合进攻。');
  else if (myP.liveThree > 0) reasons.push('形成活三，制造威胁。');
  else if (myP.deadFour > 0) reasons.push('冲四施压，迫对手防守。');

  if (oppP.liveFour > 0) reasons.push('封堵对手活四。');
  else if (oppP.liveThree >= 2) reasons.push('破坏对手双活三。');
  else if (oppP.liveThree > 0) reasons.push('限制对手活三发展。');

  if (reasons.length === 0) reasons.push('兼顾攻守，占据要点，为后续反击铺路。');

  return { row: bestMove[0] + 1, col: bestMove[1] + 1, score: bestScore, reasons };
}

// ── Dynamic Position Analysis ──────────────────────────────────────

type GamePhase = 'opening' | 'early_mid' | 'middlegame' | 'late_mid' | 'endgame';

type PositionAssessment = {
  phase: GamePhase;
  myThreatLevel: number;     // 0-10: how many threats I have
  oppThreatLevel: number;    // 0-10: how many threats opponent has
  advantage: number;         // negative = behind, 0 = even, positive = ahead
  criticalDefense: boolean;  // opponent has imminent winning threat
  hasAttackContinuation: boolean; // I have forcing sequence
  recommendedStrategy: StrategyId;
  situationDesc: string;
};

function assessPosition(board: number[][], mySide: Side): PositionAssessment {
  const myVal = mySide === '#' ? 1 : -1;
  const oppVal = -myVal;
  const n = moveCount(board);
  const myP = countPatterns(board, myVal);
  const oppP = countPatterns(board, oppVal);

  // Phase detection
  let phase: GamePhase;
  if (n <= 6) phase = 'opening';
  else if (n <= 20) phase = 'early_mid';
  else if (n <= 60) phase = 'middlegame';
  else if (n <= 100) phase = 'late_mid';
  else phase = 'endgame';

  // Threat level (0-10)
  let myThreat = 0;
  myThreat += myP.five * 10;
  myThreat += myP.liveFour * 8;
  myThreat += myP.deadFour * 4;
  myThreat += myP.liveThree * 3;
  myThreat += myP.deadThree * 1;
  myThreat = Math.min(10, myThreat);

  let oppThreat = 0;
  oppThreat += oppP.five * 10;
  oppThreat += oppP.liveFour * 8;
  oppThreat += oppP.deadFour * 4;
  oppThreat += oppP.liveThree * 3;
  oppThreat += oppP.deadThree * 1;
  oppThreat = Math.min(10, oppThreat);

  // Advantage assessment
  const advantage = myThreat - oppThreat;

  // Critical defense needed?
  const criticalDefense = oppP.five > 0 || oppP.liveFour > 0 || oppP.deadFour >= 2 || (oppP.deadFour > 0 && oppP.liveThree > 0);

  // Attack continuation available?
  const hasAttackContinuation = myP.five > 0 || myP.liveFour > 0 || myP.deadFour >= 2 || (myP.deadFour > 0 && myP.liveThree > 0) || myP.liveThree >= 2;

  // Auto strategy selection
  let recommendedStrategy: StrategyId;
  let situationDesc: string;

  if (phase === 'opening') {
    // Opening: use a standard opening
    recommendedStrategy = 'master_balance';
    situationDesc = '开局阶段，按套路抢占要点。';
  } else if (criticalDefense && !hasAttackContinuation) {
    // Must defend
    recommendedStrategy = 'defense_counter';
    situationDesc = '对手攻势凶猛，转入防守反击。';
  } else if (criticalDefense && hasAttackContinuation) {
    // Both sides have threats - who moves first matters
    recommendedStrategy = 'sente_play';
    situationDesc = '双方均有威胁，抢先手是关键。';
  } else if (oppThreat >= 5 && !hasAttackContinuation) {
    // Opponent has strong initiative
    recommendedStrategy = 'defense_counter';
    situationDesc = '对手掌握主动，先稳固防守再寻反击。';
  } else if (hasAttackContinuation && oppThreat <= 2) {
    // I have strong attack, opponent is weak
    recommendedStrategy = 'attack_focus';
    situationDesc = '我方攻势占优，连续进攻不给喘息。';
  } else if (myP.liveThree >= 2 || (myP.liveThree > 0 && myP.deadThree > 0)) {
    // Multiple threes - trap strategy
    recommendedStrategy = 'trap_double_three';
    situationDesc = '多路活三布局，设陷阱诱导对手犯错。';
  } else if (myThreat >= 4 && oppThreat >= 3) {
    // Both have moderate threats - need initiative
    recommendedStrategy = 'sente_play';
    situationDesc = '形势胶着，保持先手不脱先是关键。';
  } else if (phase === 'middlegame' && advantage >= 1) {
    // Slight advantage in middlegame - build influence
    recommendedStrategy = 'influence_play';
    situationDesc = '中盘略优，构建厚势压缩对手空间。';
  } else if (phase === 'endgame') {
    // Endgame: every move is critical
    recommendedStrategy = 'master_balance';
    situationDesc = '终盘阶段，精确计算每一步。';
  } else {
    // Default: balanced play
    recommendedStrategy = 'master_balance';
    situationDesc = '形势平稳，均衡发展。';
  }

  return {
    phase,
    myThreatLevel: myThreat,
    oppThreatLevel: oppThreat,
    advantage,
    criticalDefense,
    hasAttackContinuation,
    recommendedStrategy,
    situationDesc,
  };
}

function phaseLabel(phase: GamePhase): string {
  const labels: Record<GamePhase, string> = {
    opening: '开局',
    early_mid: '序盘',
    middlegame: '中盘',
    late_mid: '中后盘',
    endgame: '终盘',
  };
  return labels[phase];
}

// ── Strategy Suggestion (wraps the engine) ─────────────────────────

function suggestMoves(board: number[][], mySide: Side, strategy: StrategyId): { moves: MoveEval[]; assessment?: PositionAssessment } {
  const myVal = mySide === '#' ? 1 : -1;

  // Auto mode: analyze position and pick best strategy
  let effectiveStrategy = strategy;
  let assessment: PositionAssessment | undefined;
  if (strategy === 'auto') {
    assessment = assessPosition(board, mySide);
    effectiveStrategy = assessment.recommendedStrategy;
  }

  // Opening book
  const n = moveCount(board);
  if (n <= 8) {
    const plan = OPENING_BOOK[mySide][effectiveStrategy] || OPENING_BOOK[mySide].master_balance;
    for (const [row, col] of plan) {
      const r = row - 1, c = col - 1;
      if (!inside(r, c) || board[r][c] !== 0) continue;
      if (n === 0 || hasNeighbor(board, r, c, 3)) {
        return {
          moves: [{
            row, col, score: 80000000,
            reasons: assessment
              ? [assessment.situationDesc, '按开局套路推进，抢占关键形点。']
              : ['按开局套路推进，抢占关键形点。'],
          }],
          assessment,
        };
      }
    }
  }

  // Find the best move via search
  const best = findBestMove(board, mySide, effectiveStrategy);
  if (!best) return { moves: [], assessment };

  // Prepend situation analysis to first move reasons
  if (assessment) {
    best.reasons = [assessment.situationDesc, ...best.reasons];
  }

  // Generate top-3 alternatives for display
  const results: MoveEval[] = [best];
  const candidates = genCandidates(board, 15);
  for (const [r, c] of candidates) {
    if (results.length >= 3) break;
    if (r === best.row - 1 && c === best.col - 1) continue;

    board[r][c] = myVal;
    const score = evaluateWithStrategy(board, effectiveStrategy);
    board[r][c] = 0;

    const reasons: string[] = [];
    board[r][c] = myVal;
    const myP = countPatterns(board, myVal);
    board[r][c] = 0;
    if (myP.liveFour > 0) reasons.push('可形成活四。');
    if (myP.deadFour > 0) reasons.push('可冲四进攻。');
    if (myP.liveThree > 0) reasons.push('可形成活三。');
    if (reasons.length === 0) reasons.push('备选落子点。');

    results.push({ row: r + 1, col: c + 1, score, reasons });
  }

  return { moves: results, assessment };
}

// ── Component ──────────────────────────────────────────────────────

export default function GomokuPanel({ disabled, nickname, boardText, onPick }: Props) {
  const cells = useMemo(() => {
    try {
      const parsed = parseBoard(boardText);
      return parsed.length === BOARD_SIZE ? parsed : defaultBoard();
    } catch (e) {
      console.error('[GomokuPanel] parseBoard failed:', e);
      return defaultBoard();
    }
  }, [boardText]);

  const turnName = parseTurnName(boardText);
  const myTurn = !!turnName && turnName === nickname;
  const canPlay = !disabled && (!turnName || myTurn);

  const { blackName, whiteName } = useMemo(() => {
    try {
      return parseSeatedSides(boardText);
    } catch (e) {
      console.error('[GomokuPanel] parseSeatedSides failed:', e);
      return { blackName: '', whiteName: '' };
    }
  }, [boardText]);
  const mySide: Side | null = nickname === blackName ? '#' : nickname === whiteName ? 'o' : null;
  const turnSide = useMemo(() => parseTurnSide(boardText), [boardText]);

  const board = useMemo(() => {
    try {
      return parseMatrix(cells);
    } catch (e) {
      console.error('[GomokuPanel] parseMatrix failed:', e);
      return Array.from({ length: BOARD_SIZE }, () => Array.from({ length: BOARD_SIZE }, () => 0));
    }
  }, [cells]);

  const { moves, assessment } = useMemo(() => {
    if (!mySide) return { moves: [] as MoveEval[] };
    try {
      return suggestMoves(board, mySide, 'auto');
    } catch (e) {
      console.error('[GomokuPanel] suggestMoves failed:', e);
      return { moves: [] as MoveEval[] };
    }
  }, [board, mySide]);

  const isHiddenMaster = nickname === 'zouyu';

  return (
    <div className="game-interaction-panel">
      <div className="game-interaction-title">五子棋棋盘（点击落子）</div>
      {turnName && !myTurn && (
        <div className="game-workbench-hint">当前轮到：{turnName}，你的落子按钮已暂时禁用。</div>
      )}

      {isHiddenMaster && (
      <div className="game-advisor game-advisor-info" style={{ marginBottom: 8 }}>
        <div className="game-advisor-title">大师级五子棋助手</div>
        {mySide ? (
          <>
            <div className="game-advisor-detail">
              执{mySide === '#' ? '黑' : '白'} · 当前落子方：{turnSide === '#' ? '黑' : turnSide === 'o' ? '白' : '未知'}
            </div>
            {assessment && (
              <div style={{ marginTop: 6, padding: '6px 8px', background: 'rgba(255,255,255,0.05)', borderRadius: 4, fontSize: 12 }}>
                <div style={{ fontWeight: 600, marginBottom: 2 }}>
                  局势分析：{phaseLabel(assessment.phase)} · {assessment.situationDesc}
                </div>
                <div>
                  我方威胁 {assessment.myThreatLevel}/10 · 对手威胁 {assessment.oppThreatLevel}/10 ·
                  {assessment.advantage > 0 ? ' 优势' : assessment.advantage < 0 ? ' 劣势' : ' 均势'}
                  {' → '}当前策略：<strong>{STRATEGY_LABEL[assessment.recommendedStrategy]}</strong>
                </div>
              </div>
            )}
            {moves.length > 0 && (
              <div className="game-chip-row" style={{ marginTop: 6, flexWrap: 'wrap' }}>
                {moves.map((m, idx) => (
                  <button
                    key={`${m.row}-${m.col}-${idx}`}
                    className="mini-btn"
                    disabled={!canPlay}
                    onClick={() => onPick(m.row, m.col)}
                    title={m.reasons.join('；')}
                  >
                    {idx === 0 ? '首选' : `备选${idx}`}：({m.row},{m.col})
                  </button>
                ))}
              </div>
            )}
            {moves[0] && (
              <div style={{ marginTop: 6, padding: '4px 8px', background: 'rgba(255,255,255,0.03)', borderRadius: 4, fontSize: 12 }}>
                <strong>推荐落子：第 {moves[0].row} 行，第 {moves[0].col} 列</strong>
                <br />
                理由：{moves[0].reasons.join('；')}
              </div>
            )}
          </>
        ) : (
          <div className="game-advisor-detail">未识别到你在本局的黑白席位，请先加入当前五子棋对局。</div>
        )}
      </div>
      )}

      <div className="gomoku-grid">
        {cells.map((rowCells, rIx) =>
          rowCells.map((cell, cIx) => {
            const text = cell.stone === '#' ? '●' : cell.stone === 'o' ? '○' : '·';
            const cls = [
              'gomoku-cell',
              cell.stone === '#' ? 'black' : '',
              cell.stone === 'o' ? 'white' : '',
              cell.last ? 'last' : '',
            ]
              .filter(Boolean)
              .join(' ');
            return (
              <button
                key={`${rIx + 1}-${cIx + 1}`}
                className={cls}
                onClick={() => onPick(cell.row, cell.col)}
                disabled={!canPlay}
                title={`${cell.row},${cell.col}`}
              >
                {text}
              </button>
            );
          }),
        )}
      </div>
    </div>
  );
}
