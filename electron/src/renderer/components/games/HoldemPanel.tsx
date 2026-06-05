import React, { useMemo, useState } from 'react';
import PokerCardsView from './PokerCardsView';
import { useTranslation } from '../../i18n';

type Props = {
  disabled: boolean;
  nickname: string;
  onCmd: (cmd: string) => void;
  boardText: string;
};

type Meta = {
  state: string;
  turn: string;
  host: string;
  street: string;
  pot: number;
  currentBet: number;
};

function splitLines(text: string): string[] {
  return text.split('\n').map((l) => l.trim());
}

function parseValueAfterSeparator(line: string): string {
  const idx = line.search(/[:：]/);
  if (idx < 0) return '';
  return line.slice(idx + 1).trim();
}

function isUnknownBoardToken(token: string): boolean {
  const t = token.trim().toLowerCase();
  return t === '未发' || t === '无' || t === 'none' || t === '-';
}

function extractCards(text: string, prefixes: string[]): string[] {
  const lines = splitLines(text);
  const line = lines.find((l) => prefixes.some((p) => l.includes(p)));
  if (!line) return [];
  const value = parseValueAfterSeparator(line);
  if (!value) return [];
  const cards = value.split(/\s+/).filter(Boolean);
  if (cards.length === 1 && isUnknownBoardToken(cards[0])) return [];
  return cards;
}

function extractScores(text: string): Array<{ name: string; score: number }> {
  const out: Array<{ name: string; score: number }> = [];
  const re = /^#\d+\s+([^:：]+)\s*[:：]\s*(?:积分|chips)\s*=\s*(\d+)/i;
  for (const line of splitLines(text)) {
    const m = line.match(re);
    if (!m) continue;
    out.push({ name: m[1].trim(), score: Number(m[2]) });
  }
  return out;
}

function parseMeta(text: string): Meta {
  let state = '';
  let turn = '';
  let host = '';
  let street = '';
  let pot = 0;
  let currentBet = 0;

  for (const line of splitLines(text)) {
    if (!state) {
      const m = line.match(/(?:状态|state)\s*[:：]\s*(.+)$/i);
      if (m) state = m[1].trim();
    }

    if (!turn) {
      const m = line.match(/^(?:turn|轮到)\s*[:：]\s*(.+)$/i);
      if (m) turn = m[1].trim();
    }

    if (!turn) {
      const m = line.match(/^#\d+\s+([^:：]+)\s*[:：].*[（(](?:行动中|acting)[）)]/i);
      if (m) turn = m[1].trim();
    }

    if (!host) {
      const m = line.match(/^#1\s+([^:：]+)\s*[:：]/);
      if (m) host = m[1].trim();
    }

    if (!street) {
      const m = line.match(/(?:阶段|street)\s*[:：]\s*(.+)$/i);
      if (m) street = m[1].trim();
    }

    if (!pot) {
      const m = line.match(/(?:底池|pot)\s*=\s*(\d+)/i);
      if (m) pot = Number(m[1]);
    }

    if (!currentBet) {
      const m = line.match(/(?:当前注|current_bet)\s*=\s*(\d+)/i);
      if (m) currentBet = Number(m[1]);
    }
  }

  return { state, turn, host, street, pot, currentBet };
}

function includesAny(source: string, needles: string[]): boolean {
  const s = source.toLowerCase();
  return needles.some((n) => s.includes(n.toLowerCase()));
}

function streetHint(street: string, boardCards: string[], tr: (key: string) => string): string {
  const s = street.toLowerCase();
  if (s.includes('preflop') || s.includes('翻牌前')) {
    return tr('game.holdem.preflop');
  }
  if (boardCards.length === 3) return tr('game.holdem.flop');
  if (boardCards.length === 4) return tr('game.holdem.turnStreet');
  if (boardCards.length >= 5) return tr('game.holdem.river');
  return '';
}

export default function HoldemPanel({ disabled, nickname, onCmd, boardText }: Props) {
  const [raiseAmount, setRaiseAmount] = useState('10');
  const { t } = useTranslation();

  const handCards = useMemo(() => extractCards(boardText, ['你的手牌', 'your hand']), [boardText]);
  const boardCards = useMemo(() => extractCards(boardText, ['公共牌', 'board']), [boardText]);
  const scores = useMemo(() => extractScores(boardText), [boardText]);
  const meta = useMemo(() => parseMeta(boardText), [boardText]);

  const isHost = !!meta.host && meta.host === nickname;
  const isPlaying = includesAny(meta.state, ['进行中', 'playing']);
  const isWaiting = includesAny(meta.state, ['等待开始', 'waiting']);
  const isEnded = includesAny(meta.state, ['已结束', 'ended']);
  const myTurn = !!meta.turn && meta.turn === nickname;

  const canStart = isHost && (isWaiting || isEnded);
  const canAct = isPlaying && myTurn;
  const canLook = isPlaying && handCards.length === 0;
  const canTuneBot = isHost;

  const amountNum = Number(raiseAmount.trim());
  const hasValidRaise = Number.isFinite(amountNum) && amountNum > 0;

  const stageText =
    meta.street ||
    (isPlaying ? t('game.holdem.playing') : isWaiting ? t('game.holdem.waiting') : isEnded ? t('game.holdem.ended') : t('game.holdem.unknown'));

  const phaseHint = streetHint(meta.street, boardCards, t);

  const actionBlockReason = !isPlaying
    ? t('game.holdem.notPlaying')
    : !meta.turn
      ? t('game.holdem.noTurn')
      : !myTurn
        ? t('game.holdem.waitingTurn', { name: meta.turn })
        : '';

  const startReason = !isHost
    ? t('game.holdem.hostOnly')
    : isPlaying
      ? t('game.holdem.alreadyPlaying')
      : '';

  return (
    <div className="game-interaction-panel">
      <div className="game-interaction-title">{t('game.holdem.title')}</div>
      <div className="game-workbench-hint">{t('game.holdem.stage', { stage: stageText })}</div>
      <div className="game-workbench-hint">{t('game.holdem.pot', { pot: meta.pot, currentBet: meta.currentBet })}</div>
      {phaseHint && <div className="game-workbench-hint">{phaseHint}</div>}

      {isPlaying && !myTurn && meta.turn && (
        <div className="game-workbench-hint">{t('game.holdem.turn', { name: meta.turn })}</div>
      )}
      {isPlaying && myTurn && (
        <div className="game-workbench-hint">{t('game.holdem.yourTurn')}</div>
      )}
      {isPlaying && handCards.length === 0 && (
        <div className="game-workbench-hint">{t('game.holdem.lookHint')}</div>
      )}

      <div className="game-workbench-hint">{t('game.holdem.cmdHint')}</div>
      <div className="game-workbench-hint">{t('game.holdem.cmdExample')}</div>

      <PokerCardsView title={t('game.holdem.yourHand')} cards={handCards} />
      <PokerCardsView title={t('game.holdem.board')} cards={boardCards} />

      {scores.length > 0 && (
        <div className="game-chip-row">
          {scores.map((s) => (
            <span key={s.name} className="game-workbench-hint">{s.name}：{t('game.holdem.chips')} {s.score}</span>
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
          {t('game.holdem.dealStart')}
        </button>
        <button className={`mini-btn ${canLook ? 'ready' : ''}`} disabled={disabled || !canLook} onClick={() => onCmd('look')}>{t('game.holdem.look')}</button>
        <button className={`mini-btn ${canAct ? 'ready' : ''}`} disabled={disabled || !canAct} onClick={() => onCmd('check')} title={actionBlockReason}>{t('game.holdem.check')}</button>
        <button className={`mini-btn ${canAct ? 'ready' : ''}`} disabled={disabled || !canAct} onClick={() => onCmd('call')} title={actionBlockReason}>{t('game.holdem.call')}</button>
        <button className={`mini-btn ${canAct ? 'ready' : ''}`} disabled={disabled || !canAct} onClick={() => onCmd('allin')} title={actionBlockReason}>{t('game.holdem.allin')}</button>
        <button className={`mini-btn ${canAct ? 'ready' : ''}`} disabled={disabled || !canAct} onClick={() => onCmd('fold')} title={actionBlockReason}>{t('game.holdem.fold')}</button>
        <button className="mini-btn" disabled={disabled} onClick={() => onCmd('/game end')}>{t('game.end')}</button>
      </div>

      <div className="game-chip-row">
        <input
          className="game-mini-input"
          value={raiseAmount}
          onChange={(e) => setRaiseAmount(e.target.value)}
          placeholder={t('game.holdem.raiseAmount')}
          disabled={disabled}
        />
        <button
          className={`mini-btn ${canAct && hasValidRaise ? 'ready' : ''}`}
          disabled={disabled || !canAct || !hasValidRaise}
          title={!hasValidRaise ? t('game.holdem.invalidRaise') : actionBlockReason}
          onClick={() => onCmd(`raise ${raiseAmount.trim()}`)}
        >
          {t('game.holdem.raise')}
        </button>
      </div>

      <div className="game-chip-row">
        <span className="game-workbench-hint">{t('game.holdem.botLevel')}</span>
        <button className={`mini-btn ${canTuneBot ? 'ready' : ''}`} disabled={disabled || !canTuneBot} onClick={() => onCmd('bot easy')}>Easy</button>
        <button className={`mini-btn ${canTuneBot ? 'ready' : ''}`} disabled={disabled || !canTuneBot} onClick={() => onCmd('bot hard')}>Hard</button>
        <button className={`mini-btn ${canTuneBot ? 'ready' : ''}`} disabled={disabled || !canTuneBot} onClick={() => onCmd('bot pro')}>Pro</button>
      </div>
    </div>
  );
}
