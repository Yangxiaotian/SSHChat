import React from 'react';

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

function parseTurnName(boardText: string): string {
  for (const line of boardText.split('\n')) {
    const t = line.trim();
    const m = t.match(/^轮到\s+(黑|白)方\s+(.+)\s+落子$/);
    if (m) return m[2].trim();
  }
  return '';
}

function parseBoard(boardText: string): Cell[][] {
  const lines = boardText.split('\n');
  const rowLines = lines.filter((l) => /^\s*\d+\s+.*[.#o(]/.test(l));
  if (rowLines.length < 15) return [];

  const header = lines.find((l) => /^\s+\d+\s+\d+/.test(l));
  const headerCols = (header?.match(/\d+/g) || []).map((n) => Number(n));
  if (headerCols.length !== 15) return [];
  const flipped = headerCols[0] > headerCols[headerCols.length - 1];

  const out: Cell[][] = [];
  for (const rowLine of rowLines.slice(0, 15)) {
    const rowMatch = rowLine.match(/^\s*(\d+)\s+/);
    if (!rowMatch) continue;
    const rowNum = Number(rowMatch[1]);
    const mappedRow = flipped ? 16 - rowNum : rowNum;
    const payload = rowLine.slice(rowMatch[0].length);
    const tokens = payload.match(/\(#\)|\(o\)|\(\.\)|[#.o]/g);
    if (!tokens || tokens.length !== 15) continue;

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
  return out.length === 15 ? out : [];
}

function defaultBoard(): Cell[][] {
  return Array.from({ length: 15 }, (_, rIx) =>
    Array.from({ length: 15 }, (_, cIx) => ({
      stone: '.' as const,
      last: false,
      row: rIx + 1,
      col: cIx + 1,
    })),
  );
}

export default function GomokuPanel({ disabled, nickname, boardText, onPick }: Props) {
  const parsed = parseBoard(boardText);
  const cells = parsed.length === 15 ? parsed : defaultBoard();
  const turnName = parseTurnName(boardText);
  const myTurn = !!turnName && turnName === nickname;
  const canPlay = !disabled && (!turnName || myTurn);

  return (
    <div className="game-interaction-panel">
      <div className="game-interaction-title">五子棋棋盘（点击落子）</div>
      {turnName && !myTurn && (
        <div className="game-workbench-hint">当前轮到：{turnName}，你的落子按钮已暂时禁用。</div>
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
