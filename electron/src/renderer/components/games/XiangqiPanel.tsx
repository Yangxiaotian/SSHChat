import React, { useState } from 'react';

type Props = {
  disabled: boolean;
  onMove: (fr: number, fc: number, tr: number, tc: number) => void;
};

export default function XiangqiPanel({ disabled, onMove }: Props) {
  const [from, setFrom] = useState<{ r: number; c: number } | null>(null);

  return (
    <div className="game-interaction-panel">
      <div className="game-interaction-title">Xiangqi Board Click Mode (click from {'->'} to)</div>
      <div className="xiangqi-grid">
        {Array.from({ length: 10 }, (_, rIx) =>
          Array.from({ length: 9 }, (_, cIx) => {
            const row = rIx + 1;
            const col = cIx + 1;
            const selected = from?.r === row && from?.c === col;
            return (
              <button
                key={`${row}-${col}`}
                className={`xiangqi-cell ${selected ? 'selected' : ''}`}
                onClick={() => {
                  if (!from) {
                    setFrom({ r: row, c: col });
                    return;
                  }
                  onMove(from.r, from.c, row, col);
                  setFrom(null);
                }}
                disabled={disabled}
                title={`${row},${col}`}
              >
                {row},{col}
              </button>
            );
          }),
        )}
      </div>
    </div>
  );
}
