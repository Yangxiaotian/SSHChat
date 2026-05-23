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
  | 'attack'
  | 'defense'
  | 'trap'
  | 'opening_center'
  | 'opening_star';

type StrategyOption = {
  id: StrategyId;
  label: string;
};

type Suggestion = {
  row: number;
  col: number;
  score: number;
  reason: string;
};

const BOARD_SIZE = 15;
const STRATEGIES: StrategyOption[] = [
  { id: 'auto', label: '智能自适应（推荐）' },
  { id: 'balance', label: '均衡控盘' },
  { id: 'attack', label: '连续进攻' },
  { id: 'defense', label: '稳健防反' },
  { id: 'trap', label: '双三陷阱' },
  { id: 'opening_center', label: '开局天元' },
  { id: 'opening_star', label: '开局星位' },
];

const DIRS: Array<[number, number]> = [
  [1, 0],
  [0, 1],
  [1, 1],
  [1, -1],
];

function createEmptyBoard(): Cell[][] {
  return Array.from({ length: BOARD_SIZE }, (_, rIx) =>
    Array.from({ length: BOARD_SIZE }, (_, cIx) => ({
      row: rIx + 1,
      col: cIx + 1,
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
    const m2 = line.match(/^(turn|轮到)[:：]\s*(.+)$/i);
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
    const nums = (lines[i].match(/\d+/g) || []).map((n) => Number(n));
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
    const payload = rowMatch[2];
    const chunks = payload.match(/\(#\)|\(o\)|\(\.\)|#|o|\./g) || [];
    if (chunks.length < BOARD_SIZE) continue;

    for (let cIx = 0; cIx < BOARD_SIZE; cIx++) {
      const colLabel = headerCols[cIx];
      if (colLabel < 1 || colLabel > BOARD_SIZE) continue;
      const chunk = chunks[cIx];
      const cleaned = chunk.replace(/[()]/g, '');
      const stone: Stone = cleaned === '#' ? '#' : cleaned === 'o' ? 'o' : '.';
      board[rowLabel - 1][colLabel - 1] = {
        row: rowLabel,
        col: colLabel,
        stone,
        last: chunk.startsWith('('),
      };
    }
    parsedRows += 1;
  }

  return board;
}

function toMatrix(cells: Cell[][]): number[][] {
  return cells.map((row) =>
    row.map((cell) => (cell.stone === '#' ? 1 : cell.stone === 'o' ? -1 : 0)),
  );
}

function inBounds(r: number, c: number): boolean {
  return r >= 0 && r < BOARD_SIZE && c >= 0 && c < BOARD_SIZE;
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

function wouldWin(board: number[][], r: number, c: number, side: number): boolean {
  for (const [dr, dc] of DIRS) {
    const total = 1 + countDir(board, r, c, dr, dc, side) + countDir(board, r, c, -dr, -dc, side);
    if (total >= 5) return true;
  }
  return false;
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

function evaluateLine(board: number[][], r: number, c: number, side: number): number {
  let score = 0;
  for (const [dr, dc] of DIRS) {
    const left = countDir(board, r, c, -dr, -dc, side);
    const right = countDir(board, r, c, dr, dc, side);
    const total = left + right + 1;
    score += total * total * 15;
  }
  return score;
}

function openingBonus(strategy: StrategyId, row: number, col: number): number {
  const center = 8;
  const manhattan = Math.abs(row - center) + Math.abs(col - center);
  if (strategy === 'opening_center') {
    return Math.max(0, 200 - manhattan * 40);
  }
  if (strategy === 'opening_star') {
    const starPoints = new Set(['4,4', '4,12', '12,4', '12,12', '8,8']);
    return starPoints.has(`${row},${col}`) ? 200 : 0;
  }
  return Math.max(0, 120 - manhattan * 18);
}

function strategyWeight(strategy: StrategyId): { attack: number; defense: number; trap: number } {
  switch (strategy) {
    case 'attack':
      return { attack: 1.2, defense: 0.9, trap: 1.0 };
    case 'defense':
      return { attack: 0.95, defense: 1.25, trap: 0.9 };
    case 'trap':
      return { attack: 1.05, defense: 1.0, trap: 1.35 };
    case 'balance':
    case 'opening_center':
    case 'opening_star':
    case 'auto':
    default:
      return { attack: 1.0, defense: 1.0, trap: 1.0 };
  }
}

function deriveStrategy(board: number[][], mySide: Side, selected: StrategyId): StrategyId {
  if (selected !== 'auto') return selected;
  const my = mySide === '#' ? 1 : -1;
  const opp = -my;

  let myThreat = 0;
  let oppThreat = 0;
  let stones = 0;
  for (let r = 0; r < BOARD_SIZE; r++) {
    for (let c = 0; c < BOARD_SIZE; c++) {
      if (board[r][c] !== 0) stones += 1;
      if (board[r][c] === my) myThreat += evaluateLine(board, r, c, my);
      if (board[r][c] === opp) oppThreat += evaluateLine(board, r, c, opp);
    }
  }
  if (stones <= 6) return 'opening_center';
  if (oppThreat > myThreat * 1.15) return 'defense';
  if (myThreat > oppThreat * 1.2) return 'attack';
  return 'balance';
}

function computeSuggestions(board: number[][], mySide: Side, selected: StrategyId): Suggestion[] {
  const my = mySide === '#' ? 1 : -1;
  const opp = -my;
  const effectiveStrategy = deriveStrategy(board, mySide, selected);
  const weight = strategyWeight(effectiveStrategy);

  const candidates: Suggestion[] = [];
  let stones = 0;
  for (let r = 0; r < BOARD_SIZE; r++) {
    for (let c = 0; c < BOARD_SIZE; c++) {
      if (board[r][c] !== 0) stones += 1;
    }
  }

  for (let r = 0; r < BOARD_SIZE; r++) {
    for (let c = 0; c < BOARD_SIZE; c++) {
      if (board[r][c] !== 0) continue;
      if (stones > 0 && !hasNeighbor(board, r, c, 2)) continue;

      board[r][c] = my;
      const meWin = wouldWin(board, r, c, my);
      const attackScore = evaluateLine(board, r, c, my);
      board[r][c] = 0;

      board[r][c] = opp;
      const blockWin = wouldWin(board, r, c, opp);
      const defenseScore = evaluateLine(board, r, c, opp);
      board[r][c] = 0;

      let score = 0;
      let reason = '均衡推进，保持先手压力。';
      if (meWin) {
        score = 1_000_000;
        reason = '此手可直接连五取胜。';
      } else if (blockWin) {
        score = 950_000;
        reason = '此手可封堵对手的立即胜点。';
      } else {
        score += attackScore * weight.attack;
        score += defenseScore * 0.85 * weight.defense;
        score += openingBonus(effectiveStrategy, r + 1, c + 1);
        if (effectiveStrategy === 'trap') {
          score += (attackScore + defenseScore) * 0.2 * weight.trap;
          reason = '构造双向威胁，诱导对手防守失衡。';
        } else if (effectiveStrategy === 'defense') {
          reason = '优先压制对手连线，稳健反击。';
        } else if (effectiveStrategy === 'attack') {
          reason = '扩大主动进攻线，争取连续先手。';
        } else if (effectiveStrategy === 'opening_center' || effectiveStrategy === 'opening_star') {
          reason = '按开局定式占位，后续更易成形。';
        }
      }

      candidates.push({
        row: r + 1,
        col: c + 1,
        score: Math.round(score),
        reason,
      });
    }
  }

  candidates.sort((a, b) => b.score - a.score);
  return candidates.slice(0, 3);
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

  const suggestions = useMemo(() => {
    if (!mySide) return [];
    return computeSuggestions(matrix.map((row) => row.slice()), mySide, strategy);
  }, [matrix, mySide, strategy]);

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
              <div className="game-chip-row">
                <select className="game-select" value={strategy} onChange={(e) => setStrategy(e.target.value as StrategyId)}>
                  {STRATEGIES.map((s) => (
                    <option key={s.id} value={s.id}>{s.label}</option>
                  ))}
                </select>
              </div>
              <div className="game-chip-row" style={{ marginTop: 6, flexWrap: 'wrap' }}>
                {suggestions.map((s, idx) => (
                  <button
                    key={`${s.row}-${s.col}-${idx}`}
                    className="mini-btn"
                    disabled={!canPlay}
                    onClick={() => onPick(s.row, s.col)}
                    title={s.reason}
                  >
                    建议{idx + 1}：{s.row},{s.col}
                  </button>
                ))}
              </div>
              {suggestions[0] && (
                <div className="game-advisor-detail" style={{ marginTop: 6 }}>
                  当前首选：第 {suggestions[0].row} 行，第 {suggestions[0].col} 列。理由：{suggestions[0].reason}
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
