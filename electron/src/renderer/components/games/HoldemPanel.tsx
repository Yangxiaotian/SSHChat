import React, { useMemo, useState } from 'react';
import PokerCardsView from './PokerCardsView';

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

function streetHint(street: string, boardCards: string[]): string {
  const s = street.toLowerCase();
  if (s.includes('preflop') || s.includes('翻牌前')) {
    return '翻牌前公共牌未发出，本轮下注结束后会自动发出 3 张翻牌。';
  }
  if (boardCards.length === 3) return '当前是翻牌轮，本轮结束后进入转牌。';
  if (boardCards.length === 4) return '当前是转牌轮，本轮结束后进入河牌。';
  if (boardCards.length >= 5) return '当前是河牌轮，本轮结束后进入摊牌结算。';
  return '';
}

export default function HoldemPanel({ disabled, nickname, onCmd, boardText }: Props) {
  const [raiseAmount, setRaiseAmount] = useState('10');

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
  const canTuneBot = isHost;

  const amountNum = Number(raiseAmount.trim());
  const hasValidRaise = Number.isFinite(amountNum) && amountNum > 0;

  const stageText =
    meta.street ||
    (isPlaying ? '进行中' : isWaiting ? '等待开始' : isEnded ? '已结束' : '未知');

  const phaseHint = streetHint(meta.street, boardCards);

  const actionBlockReason = !isPlaying
    ? '当前不在进行中'
    : !meta.turn
      ? '未解析到当前行动玩家，请点刷新局面'
      : !myTurn
        ? `当前轮到 ${meta.turn}`
        : '';

  const startReason = !isHost
    ? '仅房主可发牌开始'
    : isPlaying
      ? '当前对局进行中，不能重复发牌'
      : '';

  return (
    <div className="game-interaction-panel">
      <div className="game-interaction-title">德州扑克互动面板</div>
      <div className="game-workbench-hint">阶段：{stageText}</div>
      <div className="game-workbench-hint">底池={meta.pot}，当前注={meta.currentBet}</div>
      {phaseHint && <div className="game-workbench-hint">{phaseHint}</div>}

      {isPlaying && !myTurn && meta.turn && (
        <div className="game-workbench-hint">当前轮到：{meta.turn}</div>
      )}
      {isPlaying && myTurn && (
        <div className="game-workbench-hint">当前轮到你操作：可过牌/跟注/加注/全下/弃牌。</div>
      )}

      <div className="game-workbench-hint">
        命令（中英文等价）：开始 start · 过牌 check · 跟注 call · 加注 raise N · 弃牌 fold · 全下 allin
      </div>
      <div className="game-workbench-hint">手敲示例：/game move 跟注 或 /game move call；/game show 帮助 看完整对照</div>

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
        <button
          className={`mini-btn ${canStart ? 'ready' : ''}`}
          disabled={disabled || !canStart}
          title={startReason}
          onClick={() => onCmd('start')}
        >
          发牌开始
        </button>
        <button className={`mini-btn ${canAct ? 'ready' : ''}`} disabled={disabled || !canAct} onClick={() => onCmd('check')} title={actionBlockReason}>过牌</button>
        <button className={`mini-btn ${canAct ? 'ready' : ''}`} disabled={disabled || !canAct} onClick={() => onCmd('call')} title={actionBlockReason}>跟注</button>
        <button className={`mini-btn ${canAct ? 'ready' : ''}`} disabled={disabled || !canAct} onClick={() => onCmd('allin')} title={actionBlockReason}>全下</button>
        <button className={`mini-btn ${canAct ? 'ready' : ''}`} disabled={disabled || !canAct} onClick={() => onCmd('fold')} title={actionBlockReason}>弃牌</button>
        <button className="mini-btn" disabled={disabled} onClick={() => onCmd('/game end')}>结束对局</button>
      </div>

      <div className="game-chip-row">
        <input
          className="game-mini-input"
          value={raiseAmount}
          onChange={(e) => setRaiseAmount(e.target.value)}
          placeholder="加注金额"
          disabled={disabled}
        />
        <button
          className={`mini-btn ${canAct && hasValidRaise ? 'ready' : ''}`}
          disabled={disabled || !canAct || !hasValidRaise}
          title={!hasValidRaise ? '请输入大于 0 的加注金额' : actionBlockReason}
          onClick={() => onCmd(`raise ${raiseAmount.trim()}`)}
        >
          加注
        </button>
      </div>

      <div className="game-chip-row">
        <span className="game-workbench-hint">机器人难度</span>
        <button className={`mini-btn ${canTuneBot ? 'ready' : ''}`} disabled={disabled || !canTuneBot} onClick={() => onCmd('bot easy')}>Easy</button>
        <button className={`mini-btn ${canTuneBot ? 'ready' : ''}`} disabled={disabled || !canTuneBot} onClick={() => onCmd('bot hard')}>Hard</button>
        <button className={`mini-btn ${canTuneBot ? 'ready' : ''}`} disabled={disabled || !canTuneBot} onClick={() => onCmd('bot pro')}>Pro</button>
      </div>
    </div>
  );
}
