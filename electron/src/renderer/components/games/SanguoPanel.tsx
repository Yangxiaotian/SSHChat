import React, { useState } from 'react';

type Props = {
  disabled: boolean;
  users: string[];
  onCmd: (cmd: string) => void;
};

export default function SanguoPanel({ disabled, users, onCmd }: Props) {
  const [target, setTarget] = useState('');
  return (
    <div className="game-interaction-panel">
      <div className="game-interaction-title">三国杀互动面板</div>
      <div className="game-chip-row">
        {users.map((u) => (
          <button key={u} className={`mini-btn ${target === u ? 'active' : ''}`} onClick={() => setTarget(u)}>{u}</button>
        ))}
      </div>
      <div className="game-chip-row">
        <button className="mini-btn" disabled={disabled || !target} onClick={() => onCmd(`杀 ${target}`)}>杀</button>
        <button className="mini-btn" disabled={disabled || !target} onClick={() => onCmd(`决斗 ${target}`)}>决斗</button>
        <button className="mini-btn" disabled={disabled || !target} onClick={() => onCmd(`火攻 ${target}`)}>火攻</button>
        <button className="mini-btn" disabled={disabled} onClick={() => onCmd('过')}>过</button>
        <button className="mini-btn" disabled={disabled} onClick={() => onCmd('开始')}>开始</button>
      </div>
    </div>
  );
}
