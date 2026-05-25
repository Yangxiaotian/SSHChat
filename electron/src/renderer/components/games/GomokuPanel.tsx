import React, { useMemo, useState } from 'react';

type Props = {
  disabled: boolean;
  nickname: string;
  boardText: string;
  onPick: (row: number, col: number) => void;
};

type Stone = '.' | '#' | 'o';
type Side = '#' | 'o';

type Cell = {
  row: number;
  col: number;
  stone: Stone;
  last: boolean;
};

type StrategyId =
  | 'auto'
  | 'balance'
  | 'pressure_attack'
  | 'iron_defense'
  | 'double_three'
  | 'four_three'
  | 'serial_rush'
  | 'center_control';

type StrategyOption = {
  id: StrategyId;
  label: string;
  desc: string;
};

type StrategyProfile = {
  attack: number;
  defense: number;
  trap: number;
  risk: number;
  opening: number;
};

type Threat = {
  five: number;
  openFour: number;
  closedFour: number;
  brokenFour: number;
  openThree: number;
  brokenThree: number;
  openTwo: number;
  forks: number;
};

type Suggestion = {
  row: number;
  col: number;
  score: number;
  reason: string;
  style: string;
};

const BOARD_SIZE = 15;
const CENTER = 8;

const STRATEGIES: StrategyOption[] = [
  { id: 'auto', label: '智能博弈（推荐）', desc: '按局势自动切换攻防与杀招节奏。' },
  { id: 'balance', label: '均衡控局', desc: '稳健推进，攻守均衡，不给对手轻易抓手。' },
  { id: 'pressure_attack', label: '先手压迫', desc: '持续制造先手威胁，逼对手被动应手。' },
  { id: 'iron_defense', label: '铁壁反击', desc: '先化解对手杀机，再抓反打窗口。' },
  { id: 'double_three', label: '双三诱杀', desc: '布局双三结构，制造一防难尽的陷阱。' },
  { id: 'four_three', label: '四三做杀', desc: '冲四配活三，形成高压强制交换。' },
  { id: 'serial_rush', label: '连环冲四', desc: '冲四接冲四，追求连续手筋击穿防线。' },
  { id: 'center_control', label: '中腹运营', desc: '抢中腹效率位，兼顾扩张与转身。' },
];

const PROFILE_MAP: Record<Exclude<StrategyId, 'auto'>, StrategyProfile> = {
  balance: { attack: 1.0, defense: 1.0, trap: 1.0, risk: 1.0, opening: 1.0 },
  pressure_attack: { attack: 1.2, defense: 0.9, trap: 1.05, risk: 0.95, opening: 1.0 },
  iron_defense: { attack: 0.9, defense: 1.3, trap: 0.9, risk: 1.2, opening: 0.95 },
  double_three: { attack: 1.05, defense: 0.95, trap: 1.35, risk: 0.9, opening: 1.0 },
  four_three: { attack: 1.15, defense: 1.0, trap: 1.2, risk: 0.95, opening: 1.0 },
  serial_rush: { attack: 1.28, defense: 0.85, trap: 1.05, risk: 0.9, opening: 0.95 },
  center_control: { attack: 0.95, defense: 1.0, trap: 1.0, risk: 1.0, opening: 1.28 },
};

const DIRS: Array<[number, number]> = [
  [1, 0],
  [0, 1],
  [1, 1],
  [1, -1],
];

function createEmptyBoard(): Cell[][] {
  return Array.from({ length: BOARD_SIZE }, (_, r) =>
    Array.from({ length: BOARD_SIZE }, (_, c) => ({
      row: r + 1,
      col: c + 1,
      stone: '.',
      last: false,
    })),
  );
}

function parseTurnInfo(boardText: string): { name: string; side: Side | null } {
  for (const raw of boardText.split('\n')) {
    const line = raw.trim();
    const m = line.match(/^轮到\s*(黑|白)方\s*(.+?)\s*落子/);
    if (m) {
      return { name: m[2].trim(), side: m[1] === '黑' ? '#' : 'o' };
    }
    const m2 = line.match(/^(turn|轮到)\s*[:：]\s*(.+)$/i);
    if (m2) {
      return { name: m2[2].trim(), side: null };
    }
  }
  return { name: '', side: null };
}

function parseSeats(boardText: string): { blackName: string; whiteName: string } {
  for (const raw of boardText.split('\n')) {
    const line = raw.trim();
    const m1 = line.match(/黑（先手）[:：]\s*([^\s]+)\s+白[:：]\s*([^\s]+)/);
    if (m1) return { blackName: m1[1].trim(), whiteName: m1[2].trim() };
    const m2 = line.match(/黑[:：]\s*([^\s]+)\s+白[:：]\s*([^\s]+)/);
    if (m2) return { blackName: m2[1].trim(), whiteName: m2[2].trim() };
  }
  return { blackName: '', whiteName: '' };
}

function parseBoard(boardText: string): Cell[][] {
  const board = createEmptyBoard();
  if (!boardText.trim()) return board;

  const lines = boardText.split('\n');
  let headerCols: number[] = [];
  let headerIdx = -1;

  for (let i = 0; i < lines.length; i++) {
    const nums = (lines[i].match(/\d+/g) || []).map(Number);
    if (nums.length >= BOARD_SIZE && nums.every((n) => n >= 1 && n <= BOARD_SIZE)) {
      headerCols = nums.slice(0, BOARD_SIZE);
      headerIdx = i;
      break;
    }
  }

  if (headerIdx < 0 || headerCols.length !== BOARD_SIZE) return board;

  let parsedRows = 0;
  for (let i = headerIdx + 1; i < lines.length && parsedRows < BOARD_SIZE; i++) {
    const rowMatch = lines[i].match(/^\s*(\d+)\s+(.+)$/);
    if (!rowMatch) continue;
    const rowLabel = Number(rowMatch[1]);
    if (rowLabel < 1 || rowLabel > BOARD_SIZE) continue;

    const cellsText = rowMatch[2];
    const tokens = cellsText.match(/\(#\)|\(o\)|\(\.\)|#|o|\./g) || [];
    if (tokens.length < BOARD_SIZE) continue;

    for (let cIx = 0; cIx < BOARD_SIZE; cIx++) {
      const colLabel = headerCols[cIx];
      if (colLabel < 1 || colLabel > BOARD_SIZE) continue;

      const token = tokens[cIx];
      const plain = token.replace(/[()]/g, '');
      const stone: Stone = plain === '#' ? '#' : plain === 'o' ? 'o' : '.';

      board[rowLabel - 1][colLabel - 1] = {
        row: rowLabel,
        col: colLabel,
        stone,
        last: token.startsWith('('),
      };
    }
    parsedRows += 1;
  }

  return board;
}

function toMatrix(cells: Cell[][]): number[][] {
  return cells.map((row) => row.map((cell) => (cell.stone === '#' ? 1 : cell.stone === 'o' ? -1 : 0)));
}

function inBounds(r: number, c: number): boolean {
  return r >= 0 && r < BOARD_SIZE && c >= 0 && c < BOARD_SIZE;
}

function hasNeighbor(board: number[][], r: number, c: number, dist = 2): boolean {
  for (let rr = Math.max(0, r - dist); rr <= Math.min(BOARD_SIZE - 1, r + dist); rr++) {
    for (let cc = Math.max(0, c - dist); cc <= Math.min(BOARD_SIZE - 1, c + dist); cc++) {
      if (rr === r && cc === c) continue;
      if (board[rr][cc] !== 0) return true;
    }
  }
  return false;
}

function countDir(board: number[][], r: number, c: number, dr: number, dc: number, side: number): number {
  let n = 0;
  let rr = r + dr;
  let cc = c + dc;
  while (inBounds(rr, cc) && board[rr][cc] === side) {
    n += 1;
    rr += dr;
    cc += dc;
  }
  return n;
}

function directionalPotential(board: number[][], r: number, c: number, side: number): number {
  let score = 0;
  for (const [dr, dc] of DIRS) {
    const left = countDir(board, r, c, -dr, -dc, side);
    const right = countDir(board, r, c, dr, dc, side);
    const total = left + right + 1;

    const leftOpenR = r - (left + 1) * dr;
    const leftOpenC = c - (left + 1) * dc;
    const rightOpenR = r + (right + 1) * dr;
    const rightOpenC = c + (right + 1) * dc;
    const openEnds =
      (inBounds(leftOpenR, leftOpenC) && board[leftOpenR][leftOpenC] === 0 ? 1 : 0) +
      (inBounds(rightOpenR, rightOpenC) && board[rightOpenR][rightOpenC] === 0 ? 1 : 0);

    score += total * total * 14 + openEnds * 10;
  }
  return score;
}

function lineString(board: number[][], r: number, c: number, dr: number, dc: number, side: number): string {
  let s = '';
  for (let k = -4; k <= 4; k++) {
    const rr = r + k * dr;
    const cc = c + k * dc;
    if (!inBounds(rr, cc)) {
      s += 'O';
      continue;
    }
    const v = board[rr][cc];
    if (v === 0) s += '.';
    else if (v === side) s += 'X';
    else s += 'O';
  }
  return s;
}

function emptyThreat(): Threat {
  return {
    five: 0,
    openFour: 0,
    closedFour: 0,
    brokenFour: 0,
    openThree: 0,
    brokenThree: 0,
    openTwo: 0,
    forks: 0,
  };
}

function analyzeThreatAt(board: number[][], r: number, c: number, side: number): Threat {
  if (!inBounds(r, c) || board[r][c] !== 0) return emptyThreat();

  board[r][c] = side;
  const t = emptyThreat();

  for (const [dr, dc] of DIRS) {
    const left = countDir(board, r, c, -dr, -dc, side);
    const right = countDir(board, r, c, dr, dc, side);
    const total = left + right + 1;

    const leftEndR = r - (left + 1) * dr;
    const leftEndC = c - (left + 1) * dc;
    const rightEndR = r + (right + 1) * dr;
    const rightEndC = c + (right + 1) * dc;

    const leftOpen = inBounds(leftEndR, leftEndC) && board[leftEndR][leftEndC] === 0;
    const rightOpen = inBounds(rightEndR, rightEndC) && board[rightEndR][rightEndC] === 0;
    const openEnds = (leftOpen ? 1 : 0) + (rightOpen ? 1 : 0);

    if (total >= 5) {
      t.five += 1;
    } else if (total === 4) {
      if (openEnds === 2) t.openFour += 1;
      else if (openEnds === 1) t.closedFour += 1;
    } else if (total === 3) {
      if (openEnds === 2) t.openThree += 1;
      else if (openEnds === 1) t.brokenThree += 1;
    } else if (total === 2 && openEnds === 2) {
      t.openTwo += 1;
    }

    const line = lineString(board, r, c, dr, dc, side);
    if (/X\.XXX|XX\.XX|XXX\.X/.test(line)) t.brokenFour += 1;
    if (/\.XX\.X\.|\.X\.XX\./.test(line)) t.openThree += 1;
    if (/XX\.\.X|X\.\.XX|X\.X\.X/.test(line)) t.brokenThree += 1;
  }

  const fourPressure = t.openFour * 2 + t.closedFour + t.brokenFour;
  const threePressure = t.openThree + t.brokenThree;
  if (fourPressure >= 2) t.forks += 1;
  if (threePressure >= 2) t.forks += 1;

  board[r][c] = 0;
  return t;
}

function threatScore(t: Threat): number {
  return (
    t.five * 1_000_000 +
    t.openFour * 240_000 +
    t.closedFour * 95_000 +
    t.brokenFour * 85_000 +
    t.openThree * 28_000 +
    t.brokenThree * 14_000 +
    t.openTwo * 3_200 +
    t.forks * 120_000
  );
}

function isWinByPlaced(board: number[][], r: number, c: number, side: number): boolean {
  for (const [dr, dc] of DIRS) {
    const total = 1 + countDir(board, r, c, dr, dc, side) + countDir(board, r, c, -dr, -dc, side);
    if (total >= 5) return true;
  }
  return false;
}

function openingBonus(row: number, col: number): number {
  const dist = Math.abs(row - CENTER) + Math.abs(col - CENTER);
  return Math.max(0, 150 - dist * 18);
}

function collectCandidates(board: number[][], side: number, limit = 26): Array<[number, number]> {
  let stones = 0;
  for (let r = 0; r < BOARD_SIZE; r++) {
    for (let c = 0; c < BOARD_SIZE; c++) {
      if (board[r][c] !== 0) stones += 1;
    }
  }

  if (stones === 0) return [[CENTER - 1, CENTER - 1]];

  const raw: Array<{ r: number; c: number; score: number }> = [];
  const opp = -side;
  for (let r = 0; r < BOARD_SIZE; r++) {
    for (let c = 0; c < BOARD_SIZE; c++) {
      if (board[r][c] !== 0) continue;
      if (!hasNeighbor(board, r, c, stones <= 8 ? 3 : 2)) continue;

      const atk = directionalPotential(board, r, c, side);
      const def = directionalPotential(board, r, c, opp);
      const cen = openingBonus(r + 1, c + 1);
      raw.push({ r, c, score: atk + def * 0.95 + cen * 0.8 });
    }
  }

  raw.sort((a, b) => b.score - a.score);
  return raw.slice(0, limit).map((x) => [x.r, x.c]);
}

function topThreatComposite(board: number[][], side: number, limit = 10): number {
  const cands = collectCandidates(board, side, limit);
  if (cands.length === 0) return 0;
  const values = cands
    .map(([r, c]) => threatScore(analyzeThreatAt(board, r, c, side)))
    .sort((a, b) => b - a);
  const top1 = values[0] || 0;
  const top2 = values[1] || 0;
  const top3 = values[2] || 0;
  return top1 + (top2 + top3) * 0.36;
}

function boardEval(board: number[][], my: number): number {
  const opp = -my;
  const myTop = topThreatComposite(board, my, 10);
  const oppTop = topThreatComposite(board, opp, 10);
  return myTop - oppTop * 1.08;
}

function boardKey(board: number[][], sideToMove: number, depth: number): string {
  let s = `${sideToMove}|${depth}|`;
  for (let r = 0; r < BOARD_SIZE; r++) {
    for (let c = 0; c < BOARD_SIZE; c++) {
      const v = board[r][c];
      s += v === 0 ? '0' : v === 1 ? '1' : '2';
    }
  }
  return s;
}

function searchWidth(depth: number): number {
  if (depth >= 3) return 8;
  if (depth === 2) return 10;
  return 12;
}

function minimax(
  board: number[][],
  sideToMove: number,
  my: number,
  depth: number,
  alpha: number,
  beta: number,
  cache: Map<string, number>,
): number {
  if (depth <= 0) return boardEval(board, my);
  const key = boardKey(board, sideToMove, depth);
  const hit = cache.get(key);
  if (hit !== undefined) return hit;

  const cands = collectCandidates(board, sideToMove, searchWidth(depth));
  if (cands.length === 0) {
    const v = boardEval(board, my);
    cache.set(key, v);
    return v;
  }

  const maximizing = sideToMove === my;
  let best = maximizing ? -Number.MAX_SAFE_INTEGER : Number.MAX_SAFE_INTEGER;

  for (const [r, c] of cands) {
    if (board[r][c] !== 0) continue;
    board[r][c] = sideToMove;

    let value: number;
    if (isWinByPlaced(board, r, c, sideToMove)) {
      // 越早形成终结手，分值越高（或越低）。
      value = sideToMove === my ? 1_200_000_000 + depth * 1000 : -1_200_000_000 - depth * 1000;
    } else {
      value = minimax(board, -sideToMove, my, depth - 1, alpha, beta, cache);
    }

    board[r][c] = 0;

    if (maximizing) {
      if (value > best) best = value;
      if (best > alpha) alpha = best;
    } else {
      if (value < best) best = value;
      if (best < beta) beta = best;
    }
    if (beta <= alpha) break;
  }

  cache.set(key, best);
  return best;
}

function chooseLookaheadDepth(board: number[][], rootCandidateCount: number): number {
  let stones = 0;
  for (let r = 0; r < BOARD_SIZE; r++) {
    for (let c = 0; c < BOARD_SIZE; c++) {
      if (board[r][c] !== 0) stones += 1;
    }
  }

  // 开局分支很大，先用 2 层；中盘默认 3 层；后盘分支变少可拉到 4 层。
  if (stones <= 6) return 2;
  if (stones >= 26 && rootCandidateCount <= 14) return 4;
  return 3;
}

function pickAutoStrategy(board: number[][], mySide: Side): Exclude<StrategyId, 'auto'> {
  const my = mySide === '#' ? 1 : -1;
  const opp = -my;
  const mine = collectCandidates(board, my, 12);
  const theirs = collectCandidates(board, opp, 12);

  const myBest = mine.reduce((acc, [r, c]) => Math.max(acc, threatScore(analyzeThreatAt(board, r, c, my))), 0);
  const oppBest = theirs.reduce((acc, [r, c]) => Math.max(acc, threatScore(analyzeThreatAt(board, r, c, opp))), 0);

  let stones = 0;
  for (let r = 0; r < BOARD_SIZE; r++) {
    for (let c = 0; c < BOARD_SIZE; c++) if (board[r][c] !== 0) stones += 1;
  }

  if (oppBest >= 240_000) return 'iron_defense';
  if (stones <= 8) return 'center_control';
  if (myBest >= 180_000 && myBest > oppBest * 1.18) return 'pressure_attack';
  if (myBest >= 150_000 && oppBest >= 120_000) return 'four_three';
  if (myBest >= 120_000 && oppBest < 100_000) return 'serial_rush';
  return 'balance';
}

function resolveProfile(board: number[][], mySide: Side, selected: StrategyId): {
  strategy: Exclude<StrategyId, 'auto'>;
  profile: StrategyProfile;
} {
  const strategy = selected === 'auto' ? pickAutoStrategy(board, mySide) : selected;
  return { strategy, profile: PROFILE_MAP[strategy] };
}

function reasonFromThreat(my: Threat, block: Threat, strategy: Exclude<StrategyId, 'auto'>): { reason: string; style: string } {
  if (my.five > 0) return { reason: '此手直接连五，立即终结。', style: '必胜杀招' };
  if (block.five > 0) return { reason: '先手封堵对手成五点，避免被一击致命。', style: '硬性防守' };
  if (my.openFour > 0 || my.brokenFour > 0) {
    return { reason: '制造冲四/活四威胁，迫使对手被动单防。', style: '强迫手筋' };
  }
  if (my.forks > 0 || my.openThree + my.brokenThree >= 2) {
    return { reason: '构造双向威胁（双三/四三），形成一防难尽。', style: '陷阱布局' };
  }
  if (block.openFour > 0 || block.closedFour > 0 || block.brokenFour > 0) {
    return { reason: '压缩对手成势线路，同时保留己方反击弹性。', style: '反制控局' };
  }

  switch (strategy) {
    case 'pressure_attack':
      return { reason: '抢先手扩张火力线，持续给对手出难题。', style: '压迫进攻' };
    case 'iron_defense':
      return { reason: '先稳住要害，再找最省手反击点。', style: '稳健反击' };
    case 'double_three':
      return { reason: '优先铺设双三骨架，等待对手防型失衡。', style: '双三诱杀' };
    case 'four_three':
      return { reason: '围绕四三结构走棋，追求强制应手转换。', style: '四三做杀' };
    case 'serial_rush':
      return { reason: '保持冲四节奏，连手压迫对手防线。', style: '连环冲四' };
    case 'center_control':
      return { reason: '占中腹高效位，兼顾扩张与转身。', style: '中腹运营' };
    case 'balance':
    default:
      return { reason: '攻守平衡推进，保持手段完整性。', style: '均衡控局' };
  }
}

function computeSuggestions(boardInput: number[][], mySide: Side, selected: StrategyId): {
  suggestions: Suggestion[];
  strategy: Exclude<StrategyId, 'auto'>;
  depth: number;
} {
  const board = boardInput.map((row) => row.slice());
  const my = mySide === '#' ? 1 : -1;
  const opp = -my;

  const { strategy, profile } = resolveProfile(board, mySide, selected);
  const candidates = collectCandidates(board, my, 26);
  const lookaheadDepth = chooseLookaheadDepth(board, candidates.length);
  const cache = new Map<string, number>();
  const scored: Suggestion[] = [];

  for (const [r, c] of candidates) {
    const blockThreat = analyzeThreatAt(board, r, c, opp);
    const myThreat = analyzeThreatAt(board, r, c, my);

    const meDirectWin = myThreat.five > 0;
    const mustBlock = blockThreat.five > 0;

    board[r][c] = my;
    const lookahead =
      meDirectWin
        ? 1_200_000_000
        : minimax(board, opp, my, Math.max(0, lookaheadDepth - 1), -Number.MAX_SAFE_INTEGER, Number.MAX_SAFE_INTEGER, cache);
    const conn = directionalPotential(board, r, c, my);
    board[r][c] = 0;

    const tactical = threatScore(myThreat) * profile.attack;
    const defensive = threatScore(blockThreat) * 0.92 * profile.defense;
    const trap = (myThreat.forks * 140_000 + (myThreat.openThree + myThreat.brokenThree) * 9_000) * profile.trap;
    const opening = openingBonus(r + 1, c + 1) * 230 * profile.opening;
    const riskPenalty = Math.max(0, -lookahead) * 0.4 * profile.risk;
    const planningBonus = Math.max(0, lookahead) * 0.78;

    let score = tactical + defensive + trap + opening + conn * 120 + planningBonus - riskPenalty;

    if (meDirectWin) score += 2_000_000_000;
    if (mustBlock) score += 1_500_000_000;

    const info = reasonFromThreat(myThreat, blockThreat, strategy);
    scored.push({ row: r + 1, col: c + 1, score: Math.round(score), reason: info.reason, style: info.style });
  }

  scored.sort((a, b) => b.score - a.score);
  return { suggestions: scored.slice(0, 3), strategy, depth: lookaheadDepth };
}

export default function GomokuPanel({ disabled, nickname, boardText, onPick }: Props) {
  const [strategy, setStrategy] = useState<StrategyId>('auto');

  const cells = useMemo(() => parseBoard(boardText), [boardText]);
  const turnInfo = useMemo(() => parseTurnInfo(boardText), [boardText]);
  const seats = useMemo(() => parseSeats(boardText), [boardText]);
  const matrix = useMemo(() => toMatrix(cells), [cells]);

  const mySide: Side | null = nickname === seats.blackName ? '#' : nickname === seats.whiteName ? 'o' : null;
  const myTurn = !!turnInfo.name && turnInfo.name === nickname;
  const canPlay = !disabled && (!turnInfo.name || myTurn);
  const isHiddenMaster = nickname === 'zouyu';

  const result = useMemo(() => {
    if (!mySide) return { suggestions: [] as Suggestion[], strategy: 'balance' as Exclude<StrategyId, 'auto'>, depth: 0 };
    return computeSuggestions(matrix, mySide, strategy);
  }, [matrix, mySide, strategy]);

  const selectedDesc = STRATEGIES.find((s) => s.id === strategy)?.desc || '';

  return (
    <div className="game-interaction-panel">
      <div className="game-interaction-title">五子棋棋盘（点击直接落子）</div>
      {turnInfo.name && !myTurn && (
        <div className="game-workbench-hint">当前轮到：{turnInfo.name}，你的落子按钮已暂时禁用。</div>
      )}

      {isHiddenMaster && (
        <div className="game-advisor game-advisor-info" style={{ marginBottom: 8 }}>
          <div className="game-advisor-title">隐藏功能：大师级五子棋助手</div>
          {mySide ? (
            <>
              <div className="game-advisor-detail">
                你当前执子：{mySide === '#' ? '黑子' : '白子'}
                {turnInfo.side ? `，当前落子方：${turnInfo.side === '#' ? '黑子' : '白子'}` : ''}
              </div>
              <div className="game-advisor-detail">策略说明：{selectedDesc}</div>
              <div className="game-chip-row">
                <select className="game-select" value={strategy} onChange={(e) => setStrategy(e.target.value as StrategyId)}>
                  {STRATEGIES.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="game-advisor-detail" style={{ marginTop: 6 }}>
                本手采用策略：{STRATEGIES.find((s) => s.id === result.strategy)?.label || '均衡控局'}
                {result.depth > 0 ? `，前瞻预测：${result.depth} 层` : ''}
              </div>
              <div className="game-chip-row" style={{ marginTop: 6, flexWrap: 'wrap' }}>
                {result.suggestions.map((s, idx) => (
                  <button
                    key={`${s.row}-${s.col}-${idx}`}
                    className="mini-btn"
                    disabled={!canPlay}
                    onClick={() => onPick(s.row, s.col)}
                    title={`${s.style}：${s.reason}`}
                  >
                    建议{idx + 1}：{s.row},{s.col}
                  </button>
                ))}
              </div>
              {result.suggestions[0] && (
                <div className="game-advisor-detail" style={{ marginTop: 6 }}>
                  当前首选：第 {result.suggestions[0].row} 行，第 {result.suggestions[0].col} 列。战术：{result.suggestions[0].style}。理由：
                  {result.suggestions[0].reason}
                </div>
              )}
            </>
          ) : (
            <div className="game-advisor-detail">未识别到你的黑白席位，请先加入当前五子棋对局。</div>
          )}
        </div>
      )}

      <div className="gomoku-grid">
        {cells.map((row) =>
          row.map((cell) => {
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
                key={`${cell.row}-${cell.col}`}
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
