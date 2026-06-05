import React, { useMemo, useState } from 'react';
import { useTranslation } from '../../i18n';

type Props = {
  disabled: boolean;
  users: string[];
  nickname: string;
  boardText: string;
  onCmd: (cmd: string) => void;
};

function parseState(boardText: string): { state: string; turn: string; host: string; seated: string[] } {
  let state = '';
  let turn = '';
  let host = '';
  const seated: string[] = [];

  for (const raw of boardText.split('\n')) {
    const line = raw.trim();
    if (!state) {
      const m = line.match(/sanguo\s+状态[:：]\s*(\S+)/i) || line.match(/状态[:：]\s*(waiting|playing|ended|等待|进行|结束)\S*/i);
      if (m) state = m[1].trim();
    }
    if (!turn) {
      const m = line.match(/^轮到\s*#?\d*\s*([^\s]+)\s*的回合/);
      if (m) turn = m[1].trim();
      const m2 = line.match(/^(turn|轮到)[:：]\s*(.+)$/i);
      if (!turn && m2) turn = m2[2].trim();
    }
    if (!host) {
      const m = line.match(/^#1[:：]?\s*([^\s]+)/) || line.match(/^#1\s*：\s*([^\s]+)/) || line.match(/^房主[:：]\s*([^\s]+)/);
      if (m) host = m[1].trim();
    }
    const seat = line.match(/^#\d+\s*[:：]\s*([^\s（(]+)/);
    if (seat) seated.push(seat[1].trim());
  }
  return { state, turn, host, seated };
}

function includesAny(source: string, needles: string[]): boolean {
  const lower = source.toLowerCase();
  return needles.some((n) => lower.includes(n.toLowerCase()));
}

export default function SanguoPanel({ disabled, users, nickname, boardText, onCmd }: Props) {
  const [target, setTarget] = useState('');
  const { t } = useTranslation();
  const meta = useMemo(() => parseState(boardText), [boardText]);
  const candidates = useMemo(() => {
    const base = meta.seated.length > 0 ? meta.seated : users;
    return base.filter((u) => u && u !== nickname);
  }, [meta.seated, users, nickname]);

  const isHost = !!meta.host && meta.host === nickname;
  const isWaiting = includesAny(meta.state, ['waiting', '等待']);
  const isPlaying = includesAny(meta.state, ['playing', '进行']);
  const isEnded = includesAny(meta.state, ['ended', '结束']);
  const myTurn = !!meta.turn && meta.turn === nickname;

  const canStart = isHost && (isWaiting || isEnded);
  const canAct = isPlaying && myTurn;

  return (
    <div className="game-interaction-panel">
      <div className="game-interaction-title">{t('game.sanguo.title')}</div>
      {(isPlaying && meta.turn && !myTurn) && (
        <div className="game-workbench-hint">{t('game.sanguo.turn', { name: meta.turn })}</div>
      )}

      <div className="game-chip-row">
        {candidates.map((u) => (
          <button key={u} className={`mini-btn ${target === u ? 'active' : ''}`} onClick={() => setTarget(u)}>
            {u}
          </button>
        ))}
      </div>

      <div className="game-chip-row">
        <button className="mini-btn" disabled={disabled || !canStart} onClick={() => onCmd('start')}>{t('game.sanguo.start')}</button>
        <button className="mini-btn" disabled={disabled || !canAct || !target} onClick={() => onCmd(`杀 ${target}`)}>{t('game.sanguo.sha')}</button>
        <button className="mini-btn" disabled={disabled || !canAct || !target} onClick={() => onCmd(`决斗 ${target}`)}>{t('game.sanguo.duel')}</button>
        <button className="mini-btn" disabled={disabled || !canAct || !target} onClick={() => onCmd(`火攻 ${target}`)}>{t('game.sanguo.fire')}</button>
        <button className="mini-btn" disabled={disabled || !canAct} onClick={() => onCmd('pass')}>{t('game.sanguo.pass')}</button>
        <button className="mini-btn" disabled={disabled} onClick={() => onCmd('generals')}>{t('game.sanguo.generals')}</button>
        <button className="mini-btn" disabled={disabled} onClick={() => onCmd('/game end')}>{t('game.end')}</button>
      </div>
    </div>
  );
}
