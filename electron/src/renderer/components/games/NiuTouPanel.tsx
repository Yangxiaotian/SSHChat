import React from 'react';

type Props = {
  disabled: boolean;
  boardText: string;
  onCmd: (cmd: string) => void;
};

function extractHand(boardText: string): number[] {
  const line = boardText.split('\n').find((l) => l.toLowerCase().includes('your hand') || l.includes('你的手牌'));
  if (!line) return [];
  const nums = line.match(/\d+/g);
  return nums ? nums.map((x) => Number(x)) : [];
}

function parseRows(boardText: string): string[] {
  return boardText.split('\n').filter((l) => l.toLowerCase().includes('row') || l.includes('行：'));
}

export default function NiuTouPanel({ disabled, boardText, onCmd }: Props) {
  const hand = extractHand(boardText);
  const rows = parseRows(boardText);
  return (
    <div className="game-interaction-panel">
      <div className="game-interaction-title">谁是牛头王互动面板（新手引导）</div>
      <div className="game-workbench-hint">每回合点一张手牌；若提示必须吃行，再点“吃第1~4行”。牛头越少越好。</div>
      {rows.length > 0 && (
        <div className="game-chip-row">
          {rows.map((r, i) => (
            <div key={i} className="poker-card">{r}</div>
          ))}
        </div>
      )}
      <div className="game-chip-row">
        <button className="mini-btn" disabled={disabled} onClick={() => onCmd('start')}>开始</button>
        <button className="mini-btn" disabled={disabled} onClick={() => onCmd('row 1')}>吃第1行</button>
        <button className="mini-btn" disabled={disabled} onClick={() => onCmd('row 2')}>吃第2行</button>
        <button className="mini-btn" disabled={disabled} onClick={() => onCmd('row 3')}>吃第3行</button>
        <button className="mini-btn" disabled={disabled} onClick={() => onCmd('row 4')}>吃第4行</button>
      </div>
      <div className="game-chip-row">
        {hand.map((n) => (
          <button key={n} className="mini-btn" disabled={disabled} onClick={() => onCmd(`pick ${n}`)}>{n}</button>
        ))}
      </div>
      <div className="game-chip-row">
        <span className="game-workbench-hint">机器人难度</span>
        <button className="mini-btn" disabled={disabled} onClick={() => onCmd('bot easy')}>Easy</button>
        <button className="mini-btn" disabled={disabled} onClick={() => onCmd('bot hard')}>Hard</button>
        <button className="mini-btn" disabled={disabled} onClick={() => onCmd('bot pro')}>Pro</button>
      </div>
    </div>
  );
}
