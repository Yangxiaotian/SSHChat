import React, { useState } from 'react';

type Props = {
  disabled: boolean;
  users: string[];
  onCmd: (cmd: string) => void;
};

export default function WerewolfPanel({ disabled, users, onCmd }: Props) {
  const [target, setTarget] = useState('');
  return (
    <div className="game-interaction-panel">
      <div className="game-interaction-title">狼人杀互动面板</div>
      <div className="game-chip-row">
        {users.map((u) => (
          <button key={u} className={`mini-btn ${target === u ? 'active' : ''}`} onClick={() => setTarget(u)}>{u}</button>
        ))}
      </div>
      <div className="game-chip-row">
        <button className="mini-btn" disabled={disabled || !target} onClick={() => onCmd(`vote ${target}`)}>投票</button>
        <button className="mini-btn" disabled={disabled || !target} onClick={() => onCmd(`kill ${target}`)}>刀人</button>
        <button className="mini-btn" disabled={disabled || !target} onClick={() => onCmd(`check ${target}`)}>查验</button>
        <button className="mini-btn" disabled={disabled} onClick={() => onCmd('save')}>救人</button>
        <button className="mini-btn" disabled={disabled || !target} onClick={() => onCmd(`poison ${target}`)}>毒人</button>
      </div>
    </div>
  );
}
