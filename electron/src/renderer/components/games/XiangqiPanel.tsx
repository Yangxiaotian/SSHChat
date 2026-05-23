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
  symbol: string;
  isRed: boolean;
};

function parseTurnName(boardText: string): string {
  for (const line of boardText.split('\n')) {
    const t = line.trim();
    const m = t.match(/^(turn|轮到)[:：]\s*(.+)$/i);
    if (m) return m[2].trim();
  }
  return '';
}

function parseBoardText(boardText: string): Map<string, Piece> {
  const pieces = new Map<string, Piece>();
  if (!boardText.trim()) return pieces;

  const lines = boardText.split('\n');
  let rowNum = 1;

  for (const line of lines) {
    const trimmed = line.trim();

    if (
      trimmed.includes('←') ||
      trimmed.includes('图例') ||
      trimmed.includes('楚河汉界') ||
      trimmed.includes('上一步') ||
      trimmed.includes('己方在下方') ||
      !trimmed
    ) {
      continue;
    }

    if (/^\s+\d+|^\s+[一二三四五六七八九]/.test(trimmed)) {
      continue;
    }

    if (rowNum <= 10) {
      const lineContent = trimmed.replace(/^\s*\d+\s*/, '');
      let colNum = 1;
      let pos = 0;

      while (pos < lineContent.length && colNum <= 9) {
        const cell = lineContent.substring(pos, pos + 4);
        const cleaned = cell.trim();

        if (cleaned && cleaned !== '·' && cleaned !== '*') {
          const match = cleaned.match(/^([+\-!])(.*)/);
          if (match) {
            const marker = match[1];
            const symbol = match[2];
            if (symbol && symbol !== '·' && symbol !== '*') {
              pieces.set(`${rowNum}-${colNum}`, {
                row: rowNum,
                col: colNum,
                symbol,
                isRed: marker === '+' || marker === '!',
              });
            }
          }
        }

        pos += 4;
        colNum++;
      }

      rowNum++;
    }
  }

  return pieces;
}

export default function XiangqiPanel({ disabled, nickname, boardText, onMove }: Props) {
  const [from, setFrom] = useState<{ r: number; c: number } | null>(null);
  const pieces = useMemo(() => parseBoardText(boardText), [boardText]);
  const turnName = useMemo(() => parseTurnName(boardText), [boardText]);
  const myTurn = !!turnName && turnName === nickname;
  const canPlay = !disabled && (!turnName || myTurn);

  return (
    <div className="game-interaction-panel">
      <div className="game-interaction-title">中国象棋棋盘（先点起点，再点终点）</div>
      {(turnName && !myTurn) && (
        <div className="game-workbench-hint">当前轮到：{turnName}，你的走子按钮已暂时禁用。</div>
      )}
      <div className="xiangqi-grid">
        {Array.from({ length: 10 }, (_, rIx) =>
          Array.from({ length: 9 }, (_, cIx) => {
            const row = rIx + 1;
            const col = cIx + 1;
            const key = `${row}-${col}`;
            const piece = pieces.get(key);
            const selected = from?.r === row && from?.c === col;

            return (
              <button
                key={key}
                className={`xiangqi-cell ${selected ? 'selected' : ''} ${piece ? (piece.isRed ? 'red-piece' : 'black-piece') : ''}`}
                onClick={() => {
                  if (!canPlay) return;
                  if (!from) {
                    setFrom({ r: row, c: col });
                    return;
                  }
                  if (from.r === row && from.c === col) {
                    setFrom(null);
                    return;
                  }
                  onMove(from.r, from.c, row, col);
                  setFrom(null);
                }}
                disabled={!canPlay}
                title={`${row},${col}`}
              >
                {piece ? piece.symbol : `${row},${col}`}
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
