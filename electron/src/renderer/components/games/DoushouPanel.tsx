import React, { useMemo, useState } from 'react';

type Side = 'red' | 'black';

type Piece = {
  side: Side;
  label: string;
};

type Cell = {
  row: number;
  col: number;
  terrain: string;
  piece?: Piece;
  last?: boolean;
};

type Props = {
  disabled: boolean;
  nickname: string;
  boardText: string;
  onMove: (fr: number, fc: number, tr: number, tc: number) => void;
};

const ROWS = 9;
const COLS = 7;
const EMPTY = new Set(['路', '.', '·', '*', '!']);
const TERRAIN = new Set(['红穴', '黑穴', '红陷', '黑陷', '河']);
const PIECES = new Set(['鼠', '猫', '狗', '狼', '豹', '虎', '狮', '象']);

function parseSide(board: string, nickname: string): Side | null {
  const lines = board.split('\n');
  for (const line of lines) {
    const both = line.match(/红(?:方)?(?:（[^）]+）)?[:：]\s*(\S+).*黑(?:方)?(?:（[^）]+）)?[:：]\s*(\S+)/);
    if (both) {
      if (both[1] === nickname) return 'red';
      if (both[2] === nickname) return 'black';
    }
    const red = line.match(/红(?:方)?(?:（[^）]+）)?[:：]\s*(\S+)/);
    if (red?.[1] === nickname) return 'red';
    const black = line.match(/黑(?:方)?(?:（[^）]+）)?[:：]\s*(\S+)/);
    if (black?.[1] === nickname) return 'black';
  }
  return null;
}

function parseTurn(board: string): { side: Side | null; name: string } {
  for (const line of board.split('\n')) {
    const m = line.trim().match(/^轮到\s+(红|黑)方?\s+(.+?)\s+(?:行棋|走棋|行动|移动)/);
    if (m) return { side: m[1] === '红' ? 'red' : 'black', name: m[2].trim() };
  }
  return { side: null, name: '' };
}

function parseBoard(board: string): Cell[][] {
  const cells: Cell[][] = Array.from({ length: ROWS }, (_, r) =>
    Array.from({ length: COLS }, (_, c) => ({ row: r + 1, col: c + 1, terrain: '' })),
  );

  for (const rawLine of board.split('\n')) {
    const m = rawLine.match(/^\s*(\d+)\s+(.+)$/);
    if (!m) continue;
    const row = Number(m[1]);
    if (row < 1 || row > ROWS) continue;
    const tokens = m[2].trim().split(/\s+/).slice(0, COLS);
    if (tokens.length < COLS) continue;

    tokens.forEach((token, idx) => {
      const cell = cells[row - 1][idx];
      cell.last = token === '!';
      if (token.startsWith('+') || token.startsWith('-')) {
        const label = token.slice(1);
        if (PIECES.has(label)) {
          cell.piece = { side: token.startsWith('+') ? 'red' : 'black', label };
          cell.terrain = '';
        }
      } else if (TERRAIN.has(token)) {
        cell.terrain = token;
        cell.piece = undefined;
      } else if (EMPTY.has(token)) {
        cell.terrain = '';
        cell.piece = undefined;
      }
    });
  }

  return cells;
}

function terrainClass(terrain: string): string {
  if (terrain === '河') return 'river';
  if (terrain.endsWith('穴')) return 'den';
  if (terrain.endsWith('陷')) return 'trap';
  return '';
}

function sideLabel(side: Side | null): string {
  if (side === 'red') return '红方';
  if (side === 'black') return '黑方';
  return '未入座';
}

export default function DoushouPanel({ disabled, nickname, boardText, onMove }: Props) {
  const board = useMemo(() => parseBoard(boardText), [boardText]);
  const mySide = useMemo(() => parseSide(boardText, nickname), [boardText, nickname]);
  const turn = useMemo(() => parseTurn(boardText), [boardText]);
  const [selected, setSelected] = useState<{ row: number; col: number } | null>(null);
  const myTurn = !!mySide && turn.side === mySide;

  const pick = (cell: Cell) => {
    if (disabled || !myTurn) return;
    if (!selected) {
      if (cell.piece?.side === mySide) setSelected({ row: cell.row, col: cell.col });
      return;
    }
    if (selected.row === cell.row && selected.col === cell.col) {
      setSelected(null);
      return;
    }
    if (cell.piece?.side === mySide) {
      setSelected({ row: cell.row, col: cell.col });
      return;
    }
    onMove(selected.row, selected.col, cell.row, cell.col);
    setSelected(null);
  };

  return (
    <div className="game-interaction-panel">
      <div className="game-interaction-title">斗兽棋棋盘（先点己方动物，再点目标格）</div>
      <div className="game-workbench-hint">
        你的身份：{sideLabel(mySide)}；当前轮到：{sideLabel(turn.side)} {turn.name || ''}
      </div>
      {!myTurn && <div className="game-workbench-hint">当前不是你的回合，棋盘已锁定。</div>}
      <div className="doushou-board" role="grid" aria-label="斗兽棋棋盘">
        {board.flat().map((cell) => {
          const isSelected = selected?.row === cell.row && selected?.col === cell.col;
          const ownPiece = cell.piece?.side === mySide;
          const classes = [
            'doushou-cell',
            terrainClass(cell.terrain),
            cell.piece?.side === 'red' ? 'red-piece' : '',
            cell.piece?.side === 'black' ? 'black-piece' : '',
            cell.last ? 'last' : '',
            isSelected ? 'selected' : '',
            myTurn && ownPiece ? 'own' : '',
          ].filter(Boolean).join(' ');
          return (
            <button
              key={`${cell.row}-${cell.col}`}
              className={classes}
              title={`${cell.row},${cell.col}${cell.terrain ? ` ${cell.terrain}` : ''}${cell.piece ? ` ${sideLabel(cell.piece.side)}${cell.piece.label}` : ''}`}
              disabled={disabled || !myTurn}
              onClick={() => pick(cell)}
            >
              {cell.piece ? <span>{cell.piece.label}</span> : <small>{cell.terrain}</small>}
            </button>
          );
        })}
      </div>
    </div>
  );
}
