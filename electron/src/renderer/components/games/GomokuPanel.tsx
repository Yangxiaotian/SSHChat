import React from 'react';

type Props = {
  disabled: boolean;
  onPick: (row: number, col: number) => void;
};

export default function GomokuPanel({ disabled, onPick }: Props) {
  return (
    <div className="game-interaction-panel">
      <div className="game-interaction-title">五子棋棋盘（点击落子）</div>
      <div className="gomoku-grid">
        {Array.from({ length: 15 }, (_, rIx) =>
          Array.from({ length: 15 }, (_, cIx) => {
            const row = rIx + 1;
            const col = cIx + 1;
            return (
              <button
                key={`${row}-${col}`}
                className="gomoku-cell"
                onClick={() => onPick(row, col)}
                disabled={disabled}
                title={`${row},${col}`}
              >
                {row === 8 && col === 8 ? '◎' : '·'}
              </button>
            );
          }),
        )}
      </div>
    </div>
  );
}
