import React, { useMemo, useState } from 'react';

type Props = {
  disabled: boolean;
  nickname: string;
  boardText: string;
  sendMove: (payload: string) => Promise<void>;
};

const FILES = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'];

function parseTurnName(boardText: string): string {
  for (const line of boardText.split('\n')) {
    const t = line.trim();
    const m = t.match(/^(turn|轮到)[:：]\s*(.+)$/i);
    if (m) return m[2].trim();
  }
  return '';
}

export default function ChessPanel({ disabled, nickname, boardText, sendMove }: Props) {
  const [from, setFrom] = useState<string | null>(null);
  const turnName = useMemo(() => parseTurnName(boardText), [boardText]);
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
                disabled={!canPlay}
                title={sq}
              >
                <span>{sq}</span>
              </button>
            );
          });
        })}
      </div>
      <div className="game-chip-row">
        <button className="mini-btn" disabled={disabled || !from} onClick={() => setFrom(null)}>取消选中</button>
      </div>
    </div>
  );
}
