import React, { useState } from 'react';
import { GamePanelProps } from './types';

const FILES = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'];

export default function ChessPanel({ disabled, sendMove }: GamePanelProps) {
  const [from, setFrom] = useState<string | null>(null);

  const onCellClick = async (sq: string) => {
    if (!from) {
      setFrom(sq);
      return;
    }
    await sendMove(`${from}${sq}`);
    setFrom(null);
  };

  return (
    <div className="game-interaction-panel">
      <div className="game-interaction-title">Chess Board Click Mode (click from {'->'} to)</div>
      <div className="chess-grid">
        {Array.from({ length: 8 }, (_, rIx) => {
          const rank = 8 - rIx;
          return FILES.map((f, cIx) => {
            const sq = `${f}${rank}`;
            const dark = (rIx + cIx) % 2 === 1;
            const selected = from === sq;
            return (
              <button
                key={sq}
                className={`chess-cell ${dark ? 'dark' : 'light'} ${selected ? 'selected' : ''}`}
                onClick={() => onCellClick(sq)}
                disabled={disabled}
                title={sq}
              >
                <span>{sq}</span>
              </button>
            );
          });
        })}
      </div>
    </div>
  );
}
