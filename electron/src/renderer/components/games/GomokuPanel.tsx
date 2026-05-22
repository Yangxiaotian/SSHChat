import React, { useMemo, useState } from 'react';

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
  | 'master_balance'
  | 'killer_combo'
  | 'trap_double_three'
  | 'defense_counter'
  | 'opening_tianyuan'
  | 'opening_star'
  | 'opening_diagonal';

type PatternStats = {
  five: number;
  openFour: number;
  closedFour: number;
  brokenFour: number;
  openThree: number;
  closedThree: number;
  openTwo: number;
};

type MoveEval = {
  row: number;
  col: number;
  score: number;
  reasons: string[];
};

const BOARD_SIZE = 15;

const STRATEGY_LABEL: Record<StrategyId, string> = {
  master_balance: '大师均衡流（默认）',
  killer_combo: '必胜杀棋流（冲四做杀）',
  trap_double_three: '陷阱双三流（诱导反杀）',
  defense_counter: '铁壁反击流（先守后攻）',
  opening_tianyuan: '开局套路·天元压制',
  opening_star: '开局套路·星位牵制',
  opening_diagonal: '开局套路·斜月穿心',
};

const OPENING_BOOK: Record<'#' | 'o', Record<StrategyId, Array<[number, number]>>> = {
  '#': {
    master_balance: [[8, 8], [8, 9], [9, 8], [8, 7], [7, 8], [9, 9], [7, 7]],
    killer_combo: [[8, 8], [8, 9], [9, 8], [7, 8], [8, 10], [10, 8], [9, 9]],
    trap_double_three: [[8, 8], [7, 8], [9, 8], [8, 7], [8, 9], [7, 9], [9, 7]],
    defense_counter: [[8, 8], [8, 7], [8, 9], [7, 8], [9, 8], [7, 7], [9, 9]],
    opening_tianyuan: [[8, 8], [8, 9], [9, 8], [8, 7], [7, 8], [9, 9], [7, 7]],
    opening_star: [[8, 8], [7, 7], [9, 9], [7, 9], [9, 7], [6, 8], [10, 8]],
    opening_diagonal: [[8, 8], [7, 9], [9, 7], [7, 7], [9, 9], [6, 10], [10, 6]],
  },
  o: {
    master_balance: [[8, 9], [9, 8], [8, 7], [7, 8], [9, 9], [7, 7], [9, 7], [7, 9]],
    killer_combo: [[8, 9], [9, 8], [8, 7], [7, 8], [9, 9], [7, 7], [10, 8], [8, 10]],
    trap_double_three: [[7, 8], [9, 8], [8, 7], [8, 9], [7, 9], [9, 7], [7, 7], [9, 9]],
    defense_counter: [[8, 9], [8, 7], [7, 8], [9, 8], [7, 7], [9, 9], [7, 9], [9, 7]],
    opening_tianyuan: [[8, 9], [9, 8], [8, 7], [7, 8], [9, 9], [7, 7], [7, 9], [9, 7]],
    opening_star: [[7, 7], [9, 9], [7, 9], [9, 7], [8, 6], [8, 10], [6, 8], [10, 8]],
    opening_diagonal: [[7, 9], [9, 7], [7, 7], [9, 9], [6, 10], [10, 6], [6, 6], [10, 10]],
  },
};

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

function sideToInt(side: Side): 1 | -1 {
  return side === '#' ? 1 : -1;
}

function inside(r: number, c: number): boolean {
  return r >= 0 && r < BOARD_SIZE && c >= 0 && c < BOARD_SIZE;
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

function moveCount(board: number[][]): number {
  let n = 0;
  for (let r = 0; r < BOARD_SIZE; r++) {
    for (let c = 0; c < BOARD_SIZE; c++) {
      if (board[r][c] !== 0) n += 1;
    }
  }
  return n;
}

function hasNeighbor(board: number[][], r: number, c: number, dist = 2): boolean {
  for (let dr = -dist; dr <= dist; dr++) {
    for (let dc = -dist; dc <= dist; dc++) {
      if (dr === 0 && dc === 0) continue;
      const rr = r + dr;
      const cc = c + dc;
      if (inside(rr, cc) && board[rr][cc] !== 0) return true;
    }
  }
  return false;
}

function lineDetail(board: number[][], r: number, c: number, side: 1 | -1, dr: number, dc: number) {
  let left = 0;
  let rr = r - dr;
  let cc = c - dc;
  while (inside(rr, cc) && board[rr][cc] === side) {
    left += 1;
    rr -= dr;
    cc -= dc;
  }
  const leftOpen = inside(rr, cc) && board[rr][cc] === 0;

  let right = 0;
  rr = r + dr;
  cc = c + dc;
  while (inside(rr, cc) && board[rr][cc] === side) {
    right += 1;
    rr += dr;
    cc += dc;
  }
  const rightOpen = inside(rr, cc) && board[rr][cc] === 0;

  const len = left + 1 + right;

  const seqArr: string[] = [];
  for (let i = -4; i <= 4; i++) {
    const sr = r + i * dr;
    const sc = c + i * dc;
    if (!inside(sr, sc)) {
      seqArr.push('b');
      continue;
    }
    if (sr === r && sc === c) {
      seqArr.push('x');
      continue;
    }
    const v = board[sr][sc];
    if (v === 0) seqArr.push('.');
    else if (v === side) seqArr.push('x');
    else seqArr.push('o');
  }

  return {
    len,
    leftOpen,
    rightOpen,
    seq: seqArr.join(''),
  };
}

function analyzeMove(board: number[][], r: number, c: number, side: 1 | -1): PatternStats {
  const stats: PatternStats = {
    five: 0,
    openFour: 0,
    closedFour: 0,
    brokenFour: 0,
    openThree: 0,
    closedThree: 0,
    openTwo: 0,
  };

  const dirs: Array<[number, number]> = [
    [1, 0],
    [0, 1],
    [1, 1],
    [1, -1],
  ];

  for (const [dr, dc] of dirs) {
    const d = lineDetail(board, r, c, side, dr, dc);
    if (d.len >= 5) {
      stats.five += 1;
      continue;
    }
    if (d.len === 4) {
      if (d.leftOpen && d.rightOpen) stats.openFour += 1;
      else if (d.leftOpen || d.rightOpen) stats.closedFour += 1;
    }
    if (d.len === 3) {
      if (d.leftOpen && d.rightOpen) stats.openThree += 1;
      else if (d.leftOpen || d.rightOpen) stats.closedThree += 1;
    }
    if (d.len === 2 && d.leftOpen && d.rightOpen) {
      stats.openTwo += 1;
    }

    if (/\.xx\.x\.|\.x\.xx\./.test(d.seq) || /\.xxx\.x|x\.xxx\./.test(d.seq)) {
      stats.brokenFour += 1;
    }
    if (/\.xx\.\.|\.\.xx\.|\.x\.x\./.test(d.seq)) {
      stats.openThree += 0.5;
    }
  }

  return stats;
}

function openingSuggestion(board: number[][], mySide: Side, strategy: StrategyId): MoveEval | null {
  const n = moveCount(board);
  if (n > 8) return null;
  const plan = OPENING_BOOK[mySide][strategy] || OPENING_BOOK[mySide].master_balance;
  for (const [row, col] of plan) {
    const r = row - 1;
    const c = col - 1;
    if (!inside(r, c) || board[r][c] !== 0) continue;
    if (n === 0 || hasNeighbor(board, r, c, 3)) {
      return {
        row,
        col,
        score: 800000,
        reasons: ['按开局套路推进，优先抢关键形点。'],
      };
    }
  }
  return null;
}

function scoreByStrategy(my: PatternStats, oppThreat: PatternStats, row: number, col: number, strategy: StrategyId): number {
  const centerDist = Math.abs(row - 8) + Math.abs(col - 8);

  const weightMap: Record<StrategyId, Record<string, number>> = {
    master_balance: {
      myFive: 100000000,
      myOpenFour: 2500000,
      myClosedFour: 600000,
      myBrokenFour: 500000,
      myOpenThree: 160000,
      myClosedThree: 40000,
      myOpenTwo: 9000,
      blockFive: 90000000,
      blockOpenFour: 2200000,
      blockClosedFour: 450000,
      blockOpenThree: 90000,
      center: -2800,
    },
    killer_combo: {
      myFive: 100000000,
      myOpenFour: 3000000,
      myClosedFour: 780000,
      myBrokenFour: 780000,
      myOpenThree: 190000,
      myClosedThree: 50000,
      myOpenTwo: 7000,
      blockFive: 88000000,
      blockOpenFour: 1800000,
      blockClosedFour: 380000,
      blockOpenThree: 70000,
      center: -2200,
    },
    trap_double_three: {
      myFive: 100000000,
      myOpenFour: 2400000,
      myClosedFour: 580000,
      myBrokenFour: 920000,
      myOpenThree: 250000,
      myClosedThree: 90000,
      myOpenTwo: 20000,
      blockFive: 86000000,
      blockOpenFour: 1700000,
      blockClosedFour: 350000,
      blockOpenThree: 85000,
      center: -2400,
    },
    defense_counter: {
      myFive: 100000000,
      myOpenFour: 2200000,
      myClosedFour: 520000,
      myBrokenFour: 420000,
      myOpenThree: 130000,
      myClosedThree: 30000,
      myOpenTwo: 6000,
      blockFive: 98000000,
      blockOpenFour: 3000000,
      blockClosedFour: 800000,
      blockOpenThree: 160000,
      center: -2000,
    },
    opening_tianyuan: {
      myFive: 100000000,
      myOpenFour: 2500000,
      myClosedFour: 580000,
      myBrokenFour: 500000,
      myOpenThree: 150000,
      myClosedThree: 35000,
      myOpenTwo: 8000,
      blockFive: 90000000,
      blockOpenFour: 2200000,
      blockClosedFour: 450000,
      blockOpenThree: 90000,
      center: -3200,
    },
    opening_star: {
      myFive: 100000000,
      myOpenFour: 2400000,
      myClosedFour: 560000,
      myBrokenFour: 600000,
      myOpenThree: 160000,
      myClosedThree: 40000,
      myOpenTwo: 10000,
      blockFive: 90000000,
      blockOpenFour: 2000000,
      blockClosedFour: 420000,
      blockOpenThree: 90000,
      center: -2500,
    },
    opening_diagonal: {
      myFive: 100000000,
      myOpenFour: 2500000,
      myClosedFour: 580000,
      myBrokenFour: 700000,
      myOpenThree: 180000,
      myClosedThree: 45000,
      myOpenTwo: 12000,
      blockFive: 90000000,
      blockOpenFour: 2100000,
      blockClosedFour: 420000,
      blockOpenThree: 95000,
      center: -2100,
    },
  };

  const w = weightMap[strategy];

  let score = 0;
  score += my.five * w.myFive;
  score += my.openFour * w.myOpenFour;
  score += my.closedFour * w.myClosedFour;
  score += my.brokenFour * w.myBrokenFour;
  score += my.openThree * w.myOpenThree;
  score += my.closedThree * w.myClosedThree;
  score += my.openTwo * w.myOpenTwo;

  score += oppThreat.five * w.blockFive;
  score += oppThreat.openFour * w.blockOpenFour;
  score += oppThreat.closedFour * w.blockClosedFour;
  score += oppThreat.openThree * w.blockOpenThree;

  if (my.openThree >= 2) score += 360000;
  if (my.openFour >= 1 && my.openThree >= 1) score += 420000;
  if (oppThreat.openFour >= 1) score += 300000;

  score += centerDist * w.center;

  return score;
}

function suggestMoves(board: number[][], mySide: Side, strategy: StrategyId): MoveEval[] {
  const myVal = sideToInt(mySide);
  const oppVal: 1 | -1 = myVal === 1 ? -1 : 1;

  const opening = openingSuggestion(board, mySide, strategy);

  const candidates: MoveEval[] = [];
  for (let r = 0; r < BOARD_SIZE; r++) {
    for (let c = 0; c < BOARD_SIZE; c++) {
      if (board[r][c] !== 0) continue;
      if (!hasNeighbor(board, r, c, 2) && moveCount(board) > 0) continue;

      const my = analyzeMove(board, r, c, myVal);
      const oppThreat = analyzeMove(board, r, c, oppVal);
      const score = scoreByStrategy(my, oppThreat, r + 1, c + 1, strategy);
      const reasons: string[] = [];

      if (my.five > 0) reasons.push('此手可直接连五终结。');
      if (oppThreat.five > 0) reasons.push('此手必须先堵住对手成五点。');
      if (my.openFour > 0) reasons.push('此手可形成活四，下一手威胁极大。');
      if (my.brokenFour > 0) reasons.push('此手可形成冲四/跳四，便于连续做杀。');
      if (my.openThree >= 2) reasons.push('此手可构成双活三，形成复合进攻。');
      if (oppThreat.openFour > 0) reasons.push('此手能封堵对手活四路线。');
      if (reasons.length === 0) reasons.push('此手兼顾形势与中心控制。');

      candidates.push({ row: r + 1, col: c + 1, score, reasons });
    }
  }

  candidates.sort((a, b) => b.score - a.score);
  const top = candidates.slice(0, 3);

  if (opening && top.length > 0 && opening.score >= top[0].score * 0.85) {
    return [
      {
        row: opening.row,
        col: opening.col,
        score: opening.score,
        reasons: [...opening.reasons, '开局阶段优先抢位，后续更易转入杀棋节奏。'],
      },
      ...top.filter((m) => !(m.row === opening.row && m.col === opening.col)).slice(0, 2),
    ];
  }

  return top;
}

export default function GomokuPanel({ disabled, nickname, boardText, onPick }: Props) {
  const [strategy, setStrategy] = useState<StrategyId>('master_balance');

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
  const moves = useMemo(() => {
    if (!mySide) return [];
    try {
      return suggestMoves(board, mySide, strategy);
    } catch (e) {
      console.error('[GomokuPanel] suggestMoves failed:', e);
      return [];
    }
  }, [board, mySide, strategy]);

  const isHiddenMaster = nickname === 'zouyu';

  return (
    <div className="game-interaction-panel">
      <div className="game-interaction-title">五子棋棋盘（点击落子）</div>
      {turnName && !myTurn && (
        <div className="game-workbench-hint">当前轮到：{turnName}，你的落子按钮已暂时禁用。</div>
      )}

      {isHiddenMaster && (
        <div className="game-advisor game-advisor-info" style={{ marginBottom: 8 }}>
          <div className="game-advisor-title">隐藏功能：大师级五子棋助手</div>
          {mySide ? (
            <>
              <div className="game-advisor-detail">
                你当前执{mySide === '#' ? '黑' : '白'}。当前落子方：{turnSide === '#' ? '黑' : turnSide === 'o' ? '白' : '未知'}。
              </div>
              <div className="game-chip-row" style={{ marginTop: 6 }}>
                <select
                  className="monitor-input"
                  value={strategy}
                  onChange={(e) => setStrategy(e.target.value as StrategyId)}
                  disabled={disabled}
                >
                  <option value="master_balance">{STRATEGY_LABEL.master_balance}</option>
                  <option value="killer_combo">{STRATEGY_LABEL.killer_combo}</option>
                  <option value="trap_double_three">{STRATEGY_LABEL.trap_double_three}</option>
                  <option value="defense_counter">{STRATEGY_LABEL.defense_counter}</option>
                  <option value="opening_tianyuan">{STRATEGY_LABEL.opening_tianyuan}</option>
                  <option value="opening_star">{STRATEGY_LABEL.opening_star}</option>
                  <option value="opening_diagonal">{STRATEGY_LABEL.opening_diagonal}</option>
                </select>
              </div>
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
                      建议{idx + 1}：{m.row},{m.col}
                    </button>
                  ))}
                </div>
              )}
              {moves[0] && (
                <div className="game-workbench-hint" style={{ marginTop: 6 }}>
                  当前首选：第 {moves[0].row} 行，第 {moves[0].col} 列。理由：{moves[0].reasons.join('；')}
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
