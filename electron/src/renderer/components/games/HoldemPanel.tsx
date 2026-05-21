import React, { useState } from 'react';
import PokerCardsView from './PokerCardsView';

type Props = {
  disabled: boolean;
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

function extractScores(text: string): Array<{ name: string; score: number }> {
  const out: Array<{ name: string; score: number }> = [];
  for (const line of text.split('\n')) {
    const m = line.match(/^#\d+\s+([^:]+):\s+score=(\d+)/);
    if (m) out.push({ name: m[1].trim(), score: Number(m[2]) });
  }
  return out;
}

export default function HoldemPanel({ disabled, onCmd, boardText }: Props) {
  const [raiseAmount, setRaiseAmount] = useState('10');
  const handCards = extractCards('你的手牌', boardText);
  const boardCards = extractCards('公共牌', boardText);
  const scores = extractScores(boardText);
  return (
    <div className="game-interaction-panel">
      <div className="game-interaction-title">德州扑克互动面板</div>
      <PokerCardsView title="你的手牌" cards={handCards} />
      <PokerCardsView title="公共牌" cards={boardCards} />
      {scores.length > 0 && (
        <div className="game-chip-row">
          {scores.map((s) => (
            <span key={s.name} className="game-workbench-hint">{s.name}：剩余积分 {s.score}</span>
          ))}
        </div>
      )}
      <div className="game-chip-row">
        <button className="mini-btn" disabled={disabled} onClick={() => onCmd('start')}>开始</button>
        <button className="mini-btn" disabled={disabled} onClick={() => onCmd('check')}>过牌</button>
        <button className="mini-btn" disabled={disabled} onClick={() => onCmd('call')}>跟注</button>
        <button className="mini-btn" disabled={disabled} onClick={() => onCmd('allin')}>全下</button>
        <button className="mini-btn" disabled={disabled} onClick={() => onCmd('fold')}>弃牌</button>
        <button className="mini-btn" disabled={disabled} onClick={() => onCmd('/game end')}>结束对局</button>
      </div>
      <div className="game-chip-row">
        <input className="monitor-input" value={raiseAmount} onChange={(e) => setRaiseAmount(e.target.value)} placeholder="加注金额" disabled={disabled} />
        <button className="mini-btn" disabled={disabled || !raiseAmount.trim()} onClick={() => onCmd(`raise ${raiseAmount.trim()}`)}>加注</button>
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
