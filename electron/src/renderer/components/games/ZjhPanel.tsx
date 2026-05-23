import React, { useState } from 'react';
import PokerCardsView from './PokerCardsView';

type Props = {
  disabled: boolean;
  users: string[];
  nickname: string;
  onCmd: (cmd: string) => void;
  boardText: string;
};

function extractCards(linePrefix: string, text: string): string[] {
  const line = text.split('\n').find((l) => l.includes(linePrefix));
  if (!line) return [];
  const idx = Math.max(line.indexOf('：'), line.indexOf(':'));
  if (idx < 0) return [];
  return line.slice(idx + 1).trim().split(/\s+/).filter(Boolean);
}

function extractScores(text: string): Array<{ name: string; score: number }> {
  const out: Array<{ name: string; score: number }> = [];
  for (const line of text.split('\n')) {
    const m = line.match(/^#\d+\s+([^:：]+)[:：]\s+积分=(\d+)/);
    if (m) out.push({ name: m[1].trim(), score: Number(m[2]) });
  }
  return out;
}

function extractSeats(text: string): Array<{ name: string; alive: boolean }> {
  const out: Array<{ name: string; alive: boolean }> = [];
  for (const line of text.split('\n')) {
    const m = line.match(/^#\d+\s+([^:：]+)[:：]\s+积分=\d+\s+(.+)$/);
    if (!m) continue;
    const name = m[1].trim();
    const status = m[2].trim();
    const alive = status.includes('存活') || status.includes('alive');
    out.push({ name, alive });
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
      const m2 = t.match(/炸金花\s+状态[:：]\s*(.+)$/);
      if (!state && m2) state = m2[1].trim();
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

function includesAny(source: string, needles: string[]): boolean {
  const s = source.toLowerCase();
  return needles.some((n) => s.includes(n.toLowerCase()));
}

export default function ZjhPanel({ disabled, users, nickname, onCmd, boardText }: Props) {
  const [raiseAmount, setRaiseAmount] = useState('1');
  const [target, setTarget] = useState('');
  const handCards = extractCards('你的手牌', boardText);
  const scores = extractScores(boardText);
  const seats = extractSeats(boardText);
  const aliveSeatNames = seats.filter((s) => s.alive).map((s) => s.name);
  const allSeatNames = seats.map((s) => s.name);
  const candidates = (aliveSeatNames.length > 0 ? aliveSeatNames : (allSeatNames.length > 0 ? allSeatNames : users)).filter((u) => u && u !== nickname);
  const meta = parseMeta(boardText);
  const isHost = !!meta.host && meta.host === nickname;
  const isPlaying = includesAny(meta.state, ['进行中', 'playing']);
  const isWaiting = includesAny(meta.state, ['等待开始', 'waiting']);
  const isEnded = includesAny(meta.state, ['已结束', 'ended']);
  const myTurn = !!meta.turn && meta.turn === nickname;
  const canStart = isHost && (isWaiting || isEnded);
  const canAct = isPlaying && myTurn;
  const canTuneBot = isHost;

  return (
    <div className="game-interaction-panel">
      <div className="game-interaction-title">炸金花互动面板</div>
      {(isPlaying && meta.turn && !myTurn) && (
        <div className="game-workbench-hint">当前轮到：{meta.turn}，你的操作按钮已暂时禁用。</div>
      )}
      <PokerCardsView title="你的手牌" cards={handCards} />
      {scores.length > 0 && (
        <div className="game-chip-row">
          {scores.map((s) => (
            <span key={s.name} className="game-workbench-hint">{s.name}：剩余积分 {s.score}</span>
          ))}
        </div>
      )}
      <div className="game-chip-row">
        <button className="mini-btn" disabled={disabled || !canStart} onClick={() => onCmd('start')}>发牌开始</button>
        <button className="mini-btn" disabled={disabled || !canAct} onClick={() => onCmd('look')}>看牌</button>
        <button className="mini-btn" disabled={disabled || !canAct} onClick={() => onCmd('follow')}>跟注</button>
        <button className="mini-btn" disabled={disabled || !canAct} onClick={() => onCmd('fold')}>弃牌</button>
        <button className="mini-btn" disabled={disabled} onClick={() => onCmd('/game end')}>结束对局</button>
      </div>
      <div className="game-chip-row">
        <input className="game-mini-input" value={raiseAmount} onChange={(e) => setRaiseAmount(e.target.value)} placeholder="加注金额" disabled={disabled} />
        <button className="mini-btn" disabled={disabled || !canAct || !raiseAmount.trim()} onClick={() => onCmd(`raise ${raiseAmount.trim()}`)}>加注</button>
      </div>
      <div className="game-chip-row">
        {candidates.map((u) => (
          <button key={u} className={`mini-btn ${target === u ? 'active' : ''}`} onClick={() => setTarget(u)}>{u}</button>
        ))}
        <button className="mini-btn" disabled={disabled || !canAct || !target} onClick={() => onCmd(`compare ${target}`)}>比牌</button>
      </div>
      {candidates.length === 0 && (
        <div className="game-workbench-hint">当前没有可比牌目标，请先等待其他玩家/机器人入局并存活。</div>
      )}
      <div className="game-chip-row">
        <span className="game-workbench-hint">机器人难度</span>
        <button className="mini-btn" disabled={disabled || !canTuneBot} onClick={() => onCmd('bot easy')}>Easy</button>
        <button className="mini-btn" disabled={disabled || !canTuneBot} onClick={() => onCmd('bot hard')}>Hard</button>
        <button className="mini-btn" disabled={disabled || !canTuneBot} onClick={() => onCmd('bot pro')}>Pro</button>
      </div>
    </div>
  );
}
