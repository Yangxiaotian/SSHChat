import React, { useState } from 'react';
import PokerCardsView from './PokerCardsView';

type Props = {
  disabled: boolean;
  users: string[];
  onCmd: (cmd: string) => void;
  boardText: string;
};

function extractCards(linePrefix: string, text: string): string[] {
  const line = text.split('\n').find((l) => l.includes(linePrefix));
  if (!line) return [];
  const idx = line.indexOf('：');
  if (idx < 0) return [];
  return line.slice(idx + 1).trim().split(/\s+/).filter(Boolean);
}

export default function ZjhPanel({ disabled, users, onCmd, boardText }: Props) {
  const [raiseAmount, setRaiseAmount] = useState('1');
  const [target, setTarget] = useState('');
  const handCards = extractCards('你的手牌', boardText);
  return (
    <div className="game-interaction-panel">
      <div className="game-interaction-title">炸金花互动面板</div>
      <PokerCardsView title="你的手牌" cards={handCards} />
      <div className="game-chip-row">
        <button className="mini-btn" disabled={disabled} onClick={() => onCmd('start')}>开始</button>
        <button className="mini-btn" disabled={disabled} onClick={() => onCmd('look')}>看牌</button>
        <button className="mini-btn" disabled={disabled} onClick={() => onCmd('follow')}>跟注</button>
        <button className="mini-btn" disabled={disabled} onClick={() => onCmd('fold')}>弃牌</button>
      </div>
      <div className="game-chip-row">
        <input className="monitor-input" value={raiseAmount} onChange={(e) => setRaiseAmount(e.target.value)} placeholder="加注金额" disabled={disabled} />
        <button className="mini-btn" disabled={disabled || !raiseAmount.trim()} onClick={() => onCmd(`raise ${raiseAmount.trim()}`)}>加注</button>
      </div>
      <div className="game-chip-row">
        {users.map((u) => (
          <button key={u} className={`mini-btn ${target === u ? 'active' : ''}`} onClick={() => setTarget(u)}>{u}</button>
        ))}
        <button className="mini-btn" disabled={disabled || !target} onClick={() => onCmd(`compare ${target}`)}>比牌</button>
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
