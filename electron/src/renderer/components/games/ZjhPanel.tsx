import React, { useState } from 'react';
import PokerCardsView from './PokerCardsView';
import { useTranslation } from '../../i18n';

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
    const m = line.match(/^#\d+\s+([^:：]+)\s*[:：]\s*积分\s*=\s*(\d+)/);
    if (m) out.push({ name: m[1].trim(), score: Number(m[2]) });
  }
  return out;
}

function extractSeats(text: string): Array<{ name: string; alive: boolean }> {
  const out: Array<{ name: string; alive: boolean }> = [];
  for (const line of text.split('\n')) {
    const m = line.match(/^#\d+\s+([^:：]+)\s*[:：]\s*积分\s*=\s*\d+\s*(.*)$/);
    if (!m) continue;
    const name = m[1].trim();
    const status = (m[2] || '').trim().toLowerCase();
    const alive = status.includes('存活') || status.includes('alive');
    out.push({ name, alive });
  }
  return out;
}

function parseMeta(text: string): { state: string; turn: string; host: string; pot: number; currentBet: number } {
  let state = '';
  let turn = '';
  let host = '';
  let pot = 0;
  let currentBet = 0;
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
      const m = t.match(/^#1\s+([^:：]+)\s*[:：]/);
      if (m) host = m[1].trim();
    }
    if (!pot) {
      const m = t.match(/^底池\s*=\s*(\d+)/);
      if (m) pot = Number(m[1]);
    }
    if (!currentBet) {
      const m = t.match(/^当前注\s*=\s*(\d+)/);
      if (m) currentBet = Number(m[1]);
    }
  }
  return { state, turn, host, pot, currentBet };
}

function includesAny(source: string, needles: string[]): boolean {
  const s = source.toLowerCase();
  return needles.some((n) => s.includes(n.toLowerCase()));
}

export default function ZjhPanel({ disabled, users, nickname, onCmd, boardText }: Props) {
  const [raiseAmount, setRaiseAmount] = useState('1');
  const [target, setTarget] = useState('');
  const { t } = useTranslation();
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
  const amountNum = Number(raiseAmount.trim());
  const hasValidRaise = Number.isFinite(amountNum) && amountNum > 0;

  const stateText = isPlaying ? t('game.zjh.playing') : isWaiting ? t('game.zjh.waiting') : isEnded ? t('game.zjh.ended') : (meta.state || t('game.zjh.unknown'));
  const startReason = !isHost
    ? t('game.zjh.hostOnly')
    : isPlaying
      ? t('game.zjh.alreadyPlaying')
      : '';

  return (
    <div className="game-interaction-panel">
      <div className="game-interaction-title">{t('game.zjh.title')}</div>
      <div className="game-workbench-hint">{t('game.zjh.status', { state: stateText, pot: meta.pot, currentBet: meta.currentBet })}</div>

      {(isPlaying && meta.turn && !myTurn) && (
        <div className="game-workbench-hint">{t('game.zjh.turn', { name: meta.turn })}</div>
      )}
      {(isPlaying && myTurn) && (
        <div className="game-workbench-hint">{t('game.zjh.yourTurn')}</div>
      )}

      <PokerCardsView title={t('game.zjh.yourHand')} cards={handCards} />

      {scores.length > 0 && (
        <div className="game-chip-row">
          {scores.map((s) => (
            <span key={s.name} className="game-workbench-hint">{s.name}：{t('game.zjh.chips')} {s.score}</span>
          ))}
        </div>
      )}

      <div className="game-chip-row">
        <button
          className={`mini-btn ${canStart ? 'ready' : ''}`}
          disabled={disabled || !canStart}
          title={startReason}
          onClick={() => onCmd('start')}
        >
          {t('game.zjh.dealStart')}
        </button>
        <button className={`mini-btn ${canAct ? 'ready' : ''}`} disabled={disabled || !canAct} onClick={() => onCmd('look')}>{t('game.zjh.look')}</button>
        <button className={`mini-btn ${canAct ? 'ready' : ''}`} disabled={disabled || !canAct} onClick={() => onCmd('follow')}>{t('game.zjh.follow')}</button>
        <button className={`mini-btn ${canAct ? 'ready' : ''}`} disabled={disabled || !canAct} onClick={() => onCmd('fold')}>{t('game.zjh.fold')}</button>
        <button className="mini-btn" disabled={disabled} onClick={() => onCmd('/game end')}>{t('game.end')}</button>
      </div>

      <div className="game-chip-row">
        <input className="game-mini-input" value={raiseAmount} onChange={(e) => setRaiseAmount(e.target.value)} placeholder={t('game.zjh.raiseAmount')} disabled={disabled} />
        <button
          className={`mini-btn ${canAct && hasValidRaise ? 'ready' : ''}`}
          disabled={disabled || !canAct || !hasValidRaise}
          title={!hasValidRaise ? t('game.zjh.invalidRaise') : ''}
          onClick={() => onCmd(`raise ${raiseAmount.trim()}`)}
        >
          {t('game.zjh.raise')}
        </button>
      </div>

      <div className="game-chip-row">
        {candidates.map((u) => (
          <button
            key={u}
            className={`mini-btn ${target === u ? 'active ready' : ''}`}
            disabled={disabled}
            onClick={() => setTarget(u)}
          >
            {u}
          </button>
        ))}
        <button className={`mini-btn ${canAct && !!target ? 'ready' : ''}`} disabled={disabled || !canAct || !target} onClick={() => onCmd(`compare ${target}`)}>{t('game.zjh.compare')}</button>
      </div>

      {candidates.length === 0 && (
        <div className="game-workbench-hint">{t('game.zjh.noTarget')}</div>
      )}

      <div className="game-chip-row">
        <span className="game-workbench-hint">{t('game.zjh.botLevel')}</span>
        <button className={`mini-btn ${canTuneBot ? 'ready' : ''}`} disabled={disabled || !canTuneBot || !isWaiting} onClick={() => onCmd('bot add')}>{t('game.zjh.botAdd')}</button>
        <button className={`mini-btn ${canTuneBot ? 'ready' : ''}`} disabled={disabled || !canTuneBot} onClick={() => onCmd('bot easy')}>Easy</button>
        <button className={`mini-btn ${canTuneBot ? 'ready' : ''}`} disabled={disabled || !canTuneBot} onClick={() => onCmd('bot hard')}>Hard</button>
        <button className={`mini-btn ${canTuneBot ? 'ready' : ''}`} disabled={disabled || !canTuneBot} onClick={() => onCmd('bot pro')}>Pro</button>
      </div>
    </div>
  );
}
