import React, { useMemo, useState } from 'react';

type Props = {
  disabled: boolean;
  nickname: string;
  boardText: string;
  sendMove: (payload: string) => Promise<void>;
};

const FILES = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'];

type Cell = {
  square: string;
  piece: string;
  last: boolean;
};

function parseTurnName(boardText: string): string {
  for (const line of boardText.split('\n')) {
    const t = line.trim();
    const m0 = t.match(/^轮到\s*(?:白|黑)方\s*(.+?)(?:（.*)?$/);
    if (m0) return m0[1].trim();
    const m = t.match(/^(turn|轮到)[:：]\s*(.+)$/i);
    if (m) return m[2].trim();
  }
  return '';
}

function createEmptyBoard(): Cell[] {
  const cells: Cell[] = [];
  for (let rIx = 0; rIx < 8; rIx += 1) {
    const rank = 8 - rIx;
    for (const file of FILES) {
      cells.push({ square: `${file}${rank}`, piece: '', last: false });
    }
  }
  return cells;
}

function parseBoard(boardText: string): Cell[] {
  const board = createEmptyBoard();
  const index = new Map(board.map((cell) => [cell.square, cell] as const));
  const lines = boardText.split('\n');
  let files: string[] = [];

  for (const raw of lines) {
    const trimmed = raw.trim();
    const headerFiles = trimmed.toLowerCase().match(/[a-h]/g);
    if (headerFiles && headerFiles.length >= 8) {
      files = headerFiles.slice(0, 8);
      continue;
    }
    const rowMatch = raw.match(/^\s*([1-8])\s+(.+)$/);
    if (!rowMatch || files.length !== 8) continue;
    const rank = rowMatch[1];
    const tokens = rowMatch[2].match(/\([♔♕♖♗♘♙♚♛♜♝♞♟·]\)|[♔♕♖♗♘♙♚♛♜♝♞♟·]/g) || [];
    if (tokens.length < 8) continue;
    for (let i = 0; i < 8; i += 1) {
      const square = `${files[i]}${rank}`;
      const cell = index.get(square);
      if (!cell) continue;
      const token = tokens[i];
      const plain = token.replace(/[()]/g, '');
      cell.piece = plain === '·' ? '' : plain;
      cell.last = token.includes('(');
    }
  }

  return board;
}

export default function ChessPanel({ disabled, nickname, boardText, sendMove }: Props) {
  const [from, setFrom] = useState<string | null>(null);
  const turnName = useMemo(() => parseTurnName(boardText), [boardText]);
  const cells = useMemo(() => parseBoard(boardText), [boardText]);
  const myTurn = !!turnName && turnName === nickname;
  const canPlay = !disabled && (!turnName || myTurn);

  const onCellClick = async (sq: string) => {
    if (!canPlay) return;
    if (!from) {
      setFrom(sq);
      return;
    }
    if (from === sq) {
      setFrom(null);
      return;
    }
    await sendMove(`${from}${sq}`);
    setFrom(null);
  };

  return (
    <div className="game-interaction-panel">
      <div className="game-interaction-title">国际象棋棋盘（先点起点，再点终点）</div>
      {(turnName && !myTurn) && (
        <div className="game-workbench-hint">当前轮到：{turnName}，你的走子按钮已暂时禁用。</div>
      )}
      <div className="chess-grid">
        {cells.map((cell, idx) => {
          const rIx = Math.floor(idx / 8);
          const cIx = idx % 8;
          const dark = (rIx + cIx) % 2 === 1;
          const selected = from === cell.square;
          return (
            <button
              key={cell.square}
              className={`chess-cell ${dark ? 'dark' : 'light'} ${selected ? 'selected' : ''} ${cell.last ? 'last' : ''}`}
              onClick={() => onCellClick(cell.square)}
              disabled={!canPlay}
              title={cell.square}
            >
              <span className="chess-piece">{cell.piece}</span>
            </button>
          );
        })}
      </div>
      <div className="game-chip-row">
        <button className="mini-btn" disabled={disabled || !from} onClick={() => setFrom(null)}>取消选中</button>
      </div>
    </div>
  );
}
