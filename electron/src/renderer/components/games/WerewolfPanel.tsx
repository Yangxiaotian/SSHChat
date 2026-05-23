import React, { useMemo, useState } from 'react';

type Props = {
  disabled: boolean;
  users: string[];
  nickname: string;
  boardText: string;
  onCmd: (cmd: string) => void;
};

function parseMeta(boardText: string): { state: string; host: string; alive: string[]; role: string } {
  let state = '';
  let host = '';
  let role = '';
  const alive: string[] = [];

  for (const raw of boardText.split('\n')) {
    const line = raw.trim();
    if (!state) {
      const m = line.match(/werewolf state[:：]\s*(\w+)/i) || line.match(/状态[:：]\s*(\w+)/i);
      if (m) state = m[1].trim();
    }
    if (!host) {
      const m = line.match(/^\-\s+([^\s]+)\s+\(alive\)/i) || line.match(/^#1[:：]?\s*([^\s]+)/);
      if (m) host = m[1].trim();
    }
    const aliveList = line.match(/^alive[:：]\s*(.+)$/i);
    if (aliveList) {
      for (const token of aliveList[1].split(/[,，]/).map((x) => x.trim())) {
        if (token) alive.push(token);
      }
    }
    if (!role) {
      const m = line.match(/your role[:：]\s*(\w+)/i) || line.match(/你的身份[:：]\s*([^\s]+)/);
      if (m) role = m[1].trim().toLowerCase();
    }
  }
  return { state, host, alive, role };
}

export default function WerewolfPanel({ disabled, users, nickname, boardText, onCmd }: Props) {
  const [target, setTarget] = useState('');
  const meta = useMemo(() => parseMeta(boardText), [boardText]);
  const candidates = useMemo(() => {
    const base = meta.alive.length > 0 ? meta.alive : users;
    return base.filter((u) => u && u !== nickname);
  }, [meta.alive, users, nickname]);

  const state = meta.state.toLowerCase();
  const isHost = !!meta.host && meta.host === nickname;
  const waiting = state === 'waiting';
  const night = state === 'night';
  const day = state === 'day';
  const ended = state === 'ended';
  const role = meta.role;

  const canStart = isHost && (waiting || ended);
  const canVote = day;
  const canWolfKill = night && role === 'wolf';
  const canSeerCheck = night && role === 'seer';
  const canWitchSave = night && role === 'witch';
  const canWitchPoison = night && role === 'witch';
  const canWitchPass = night && role === 'witch';

  return (
    <div className="game-interaction-panel">
      <div className="game-interaction-title">狼人杀互动面板</div>
      <div className="game-workbench-hint">
        当前阶段：{meta.state || '未知'}
        {role ? `，你的身份：${role}` : '，你的身份：未公开（请查看系统私信）'}
      </div>

      <div className="game-chip-row">
        {candidates.map((u) => (
          <button key={u} className={`mini-btn ${target === u ? 'active' : ''}`} onClick={() => setTarget(u)}>
            {u}
          </button>
        ))}
      </div>

      <div className="game-chip-row">
        <button className="mini-btn" disabled={disabled || !canStart} onClick={() => onCmd('start')}>开始对局</button>
        <button className="mini-btn" disabled={disabled || !canVote || !target} onClick={() => onCmd(`vote ${target}`)}>投票</button>
        <button className="mini-btn" disabled={disabled || !canWolfKill || !target} onClick={() => onCmd(`kill ${target}`)}>刀人</button>
        <button className="mini-btn" disabled={disabled || !canSeerCheck || !target} onClick={() => onCmd(`check ${target}`)}>查验</button>
        <button className="mini-btn" disabled={disabled || !canWitchSave} onClick={() => onCmd('save')}>救人</button>
        <button className="mini-btn" disabled={disabled || !canWitchPoison || !target} onClick={() => onCmd(`poison ${target}`)}>毒人</button>
        <button className="mini-btn" disabled={disabled || !canWitchPass} onClick={() => onCmd('pass')}>放弃夜技</button>
        <button className="mini-btn" disabled={disabled} onClick={() => onCmd('/game end')}>结束对局</button>
      </div>
    </div>
  );
}
