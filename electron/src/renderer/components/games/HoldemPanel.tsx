import React, { useState } from 'react';
import PokerCardsView from './PokerCardsView';

type Props = {
  disabled: boolean;
  nickname: string;
  onCmd: (cmd: string) => void;
  boardText: string;
};

function extractCards(linePrefix: string, text: string): string[] {
  const line = text.split('\n').find((l) => l.includes(linePrefix));
  if (!line) return [];
  const idx = Math.max(line.indexOf('：'), line.indexOf(':'));
  if (idx < 0) return [];
  const cards = line.slice(idx + 1).trim().split(/\s+/).filter(Boolean);
  if (cards.length === 1 && (cards[0] === '未发' || cards[0] === '无')) return [];
  return cards;
}

function extractScores(text: string): Array<{ name: string; score: number }> {
  const out: Array<{ name: string; score: number }> = [];
  for (const line of text.split('\n')) {
    const m = line.match(/^#\d+\s+([^:：]+)[:：]\s+积分=(\d+)/);
    if (m) out.push({ name: m[1].trim(), score: Number(m[2]) });
  }
  return out;
}

function parseMeta(text: string): { state: string; turn: string; host: string } {
  let state = '';
  let turn = '';
  let host = '';
  for (const line of text.split('\n')) {
    const t = line.trim();
    if (!state) {
      const m = t.match(/(状态|state)[:：]\s*(.+)$/i);
      if (m) state = m[2].trim();
    }
    if (!state) {
      const m = t.match(/德州扑克\s+状态[:：]\s*(.+)$/);
      if (m) state = m[1].trim();
    }
    if (!turn) {
      const m = t.match(/^(turn|轮到)[:：]\s*(.+)$/i);
      if (m) turn = m[2].trim();
    }
    if (!host) {
      const m = t.match(/^#1\s+([^:：]+)[:：]/);
      if (m) host = m[1].trim();
    }
  }
  return { state, turn, host };
}

export default function HoldemPanel({ disabled, nickname, onCmd, boardText }: Props) {
  const [raiseAmount, setRaiseAmount] = useState('10');
  const handCards = extractCards('你的手牌', boardText);
  const boardCards = extractCards('公共牌', boardText);
  const scores = extractScores(boardText);
  const meta = parseMeta(boardText);
  const isHost = !!meta.host && meta.host === nickname;
  const isPlaying = meta.state.includes('进行中');
  const isWaiting = meta.state.includes('等待开始');
  const isEnded = meta.state.includes('已结束');
  const myTurn = !!meta.turn && meta.turn === nickname;
  const canStart = isHost && (isWaiting || isEnded);
  const canAct = isPlaying && myTurn;
  const canTuneBot = isHost;
  return (
    <div className="game-interaction-panel">
      <div className="game-interaction-title">德州扑克互动面板</div>
      {(isPlaying && meta.turn && !myTurn) && (
        <div className="game-workbench-hint">当前轮到：{meta.turn}，你的操作按钮已暂时禁用。</div>
      )}
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
        <button className="mini-btn" disabled={disabled || !canStart} onClick={() => onCmd('start')}>发牌开始</button>
        <button className="mini-btn" disabled={disabled || !canAct} onClick={() => onCmd('check')}>过牌</button>
        <button className="mini-btn" disabled={disabled || !canAct} onClick={() => onCmd('call')}>跟注</button>
        <button className="mini-btn" disabled={disabled || !canAct} onClick={() => onCmd('allin')}>全下</button>
        <button className="mini-btn" disabled={disabled || !canAct} onClick={() => onCmd('fold')}>弃牌</button>
        <button className="mini-btn" disabled={disabled} onClick={() => onCmd('/game end')}>结束对局</button>
      </div>
      <div className="game-chip-row">
        <input className="monitor-input" value={raiseAmount} onChange={(e) => setRaiseAmount(e.target.value)} placeholder="加注金额" disabled={disabled} />
        <button className="mini-btn" disabled={disabled || !canAct || !raiseAmount.trim()} onClick={() => onCmd(`raise ${raiseAmount.trim()}`)}>加注</button>
      </div>
      <div className="game-chip-row">
        <span className="game-workbench-hint">机器人难度</span>
        <button className="mini-btn" disabled={disabled || !canTuneBot} onClick={() => onCmd('bot easy')}>Easy</button>
        <button className="mini-btn" disabled={disabled || !canTuneBot} onClick={() => onCmd('bot hard')}>Hard</button>
        <button className="mini-btn" disabled={disabled || !canTuneBot} onClick={() => onCmd('bot pro')}>Pro</button>
      </div>
    </div>
  );
}
