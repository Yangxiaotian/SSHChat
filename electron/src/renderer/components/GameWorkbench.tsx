import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useChatStore } from '../store/chatStore';
import ChessPanel from './games/ChessPanel';
import GomokuPanel from './games/GomokuPanel';
import GoPanel from './games/GoPanel';
import XiangqiPanel from './games/XiangqiPanel';
import DoushouPanel from './games/DoushouPanel';
import HoldemPanel from './games/HoldemPanel';
import ZjhPanel from './games/ZjhPanel';
import SanguoPanel from './games/SanguoPanel';
import WerewolfPanel from './games/WerewolfPanel';
import NiuTouPanel from './games/NiuTouPanel';
import { GameCommandFactory, getQuickByGame } from './games/commandFactory';
import { GameKind } from './games/types';
import { buildGameMove, t as translate, type Locale } from '../i18n';

function detectGameKind(text: string): GameKind {
  const t = text.toLowerCase();
  if (t.includes('doushou') || t.includes('jungle') || t.includes('斗兽棋') || t.includes('斗兽')) return 'doushou';
  if (t.includes('xiangqi') || t.includes('cchess') || t.includes('中国象棋') || t.includes('象棋')) return 'xiangqi';
  if (t.includes('chess') || t.includes('国际象棋')) return 'chess';
  if (t.includes('gomoku') || t.includes('五子棋')) return 'gomoku';
  if (/\bgo\b/.test(t) || t.includes('weiqi') || t.includes('baduk') || t.includes('围棋')) return 'go';
  if (t.includes('sanguo') || t.includes('sgs')) return 'sanguo';
  if (t.includes('werewolf') || t.includes('langrensha') || t.includes('狼人')) return 'werewolf';
  if (t.includes('holdem') || t.includes('texas') || t.includes('poker') || t.includes('德州')) return 'holdem';
  if (t.includes('zjh') || t.includes('zhajinhua') || t.includes('炸金花')) return 'zjh';
  if (t.includes('niutou') || t.includes('ntw') || t.includes('牛头王') || t.includes('6 nimmt')) return 'niutou';
  return 'none';
}

const cnToGameKind: Record<string, GameKind> = {
  '国际象棋': 'chess',
  '五子棋': 'gomoku',
  '围棋': 'go',
  '中国象棋': 'xiangqi',
  '斗兽棋': 'doushou',
  '三国杀': 'sanguo',
  '狼人杀': 'werewolf',
  '德州扑克': 'holdem',
  '炸金花': 'zjh',
  '牛头王': 'niutou',
};

function extractBoardBlock(systemLines: string[]): { board: string; game: GameKind } {
  const headers = ['doushou', '斗兽棋', 'xiangqi', '中国象棋', 'chess', '国际象棋', 'gomoku', '五子棋', 'go', '围棋', 'sanguo', 'werewolf', 'holdem', 'zjh', 'niutou', '三国杀', '狼人杀', '德州扑克', '炸金花', '牛头王'];
  let start = -1;
  let game: GameKind = 'none';
  for (let i = systemLines.length - 1; i >= 0; i--) {
    const line = systemLines[i].toLowerCase();
    const hit = headers.find((h) => line.includes(`${h} `) || line.includes(`${h}(`) || line.includes(`${h}对局`));
    if (hit) {
      start = i;
      game = cnToGameKind[hit] || (hit as GameKind);
      break;
    }
  }
  if (start < 0) return { board: '', game: 'none' };
  const out: string[] = [];
  for (let i = start; i < systemLines.length; i++) {
    const line = systemLines[i];
    const trimmed = line.trim();
    if (trimmed.startsWith('---') && out.length > 0) break;
    if (/^\[\*\]\s/.test(trimmed) && out.length > 0) break;
    if (/^\/game\s+/i.test(trimmed) && out.length > 0) break;
    if (/^>>\s+/.test(trimmed) && out.length > 0) break;
    if (/^\[\d{2}:\d{2}:\d{2}\]/.test(trimmed) && out.length > 0) break;
    out.push(line);
  }
  return { board: out.join('\n'), game };
}

function isXiangqiBoardLine(line: string): boolean {
  const trimmed = line.trim();
  if (/^图例：[+\-!·*]/.test(trimmed)) return true;
  if (trimmed.includes('楚河汉界')) return true;
  if (/←\s*(黑方|红方)/.test(line)) return true;
  const tokenRe = /(?:[+\-!][^\s]{1,2}|[·*])/g;
  const tokens = trimmed.match(tokenRe);
  if (!tokens || tokens.length < 5) return false;
  return tokens.slice(0, 9).every((t) => t === '·' || t === '*' || /^[+\-!]/.test(t));
}

function isLikelyGameLine(line: string): boolean {
  if (isXiangqiBoardLine(line)) return true;
  if (/^\s+\d+\s+\d+\s+\d+/.test(line)) return true;
  if (/^\s*\d+\s+(?:\(#\)|\(o\)|\(\.\)|[#.o])(?:\s+(?:\(#\)|\(o\)|\(\.\)|[#.o])){4,}\s*$/.test(line)) return true;
  if (/^\s*[1-8]\s+(?:\([♔♕♖♗♘♙♚♛♜♝♞♟·]\)|[♔♕♖♗♘♙♚♛♜♝♞♟·])(?:\s+(?:\([♔♕♖♗♘♙♚♛♜♝♞♟·]\)|[♔♕♖♗♘♙♚♛♜♝♞♟·])){7}\s*$/.test(line)) return true;
  if (/^\s+[a-h](?:\s+[a-h]){7}\s*$/.test(line.trim().toLowerCase())) return true;
  if (/^#\d+\s+[^:：]+[:：]/.test(line.trim())) return true;
  if (/^(红|黑|白)[:：]\s*\S+/.test(line.trim())) return true;
  if (/^红[:：]\s*\S+\s+黑[:：]\s*\S+/.test(line.trim())) return true;
  if (/^doushou\s+对局|斗兽棋棋盘/.test(line.trim())) return true;
  if (/^\s*[1-9]\s+(?:[+\-][鼠猫狗狼豹虎狮象]|红穴|黑穴|红陷|黑陷|河|·|!)(?:\s+(?:[+\-][鼠猫狗狼豹虎狮象]|红穴|黑穴|红陷|黑陷|河|·|!)){6}\s*$/.test(line)) return true;
  if (/^-\s+\S+\s+\((alive|out)\)/i.test(line.trim())) return true;
  if (/^轮到\s+/.test(line.trim())) return true;
  if (/^上一步[:：]/.test(line.trim())) return true;
  if (/^(alive|players|votes)[:：]/i.test(line.trim())) return true;
  const t = line.trim().toLowerCase();
  if (!t) return false;
  const keywords = [
    'chess',
    'gomoku',
    'go',
    'xiangqi',
    'doushou',
    'holdem',
    'zjh',
    'niutou',
    'sanguo',
    'werewolf',
    'state:',
    '状态：',
    'turn:',
    '轮到：',
    'street:',
    '阶段：',
    'pot=',
    '底池=',
    '当前注=',
    'row1:',
    'row2:',
    'row3:',
    'row4:',
    '第1行：',
    '第2行：',
    '第3行：',
    '第4行：',
    'your hand',
    '你的手牌',
    '公共牌',
    '国际象棋',
    '五子棋',
    '围棋',
    '中国象棋',
    '斗兽棋',
    '德州扑克',
    '炸金花',
    '牛头王',
    '当前房间正在进行',
    '可直接加入',
    '开了一局',
    '对局',
  ];
  return keywords.some((k) => t.includes(k));
}

function inferOpenGame(lines: string[]): GameKind {
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i];
    const isOpenLine =
      /开了一局/i.test(line) ||
      /(对局状态|状态[:：])/.test(line) ||
      /当前房间正在进行/.test(line);
    if (!isOpenLine) {
      continue;
    }
    const kind = detectGameKind(line);
    if (kind !== 'none') return kind;
  }
  return 'none';
}

function findLastIndex(lines: string[], predicate: (line: string) => boolean): number {
  for (let i = lines.length - 1; i >= 0; i--) {
    if (predicate(lines[i])) return i;
  }
  return -1;
}

function hasFreshNoActiveGame(allLines: string[]): boolean {
  const noGameIdx = findLastIndex(allLines, (l) => /本房没有进行中的对局/.test(l));
  if (noGameIdx < 0) return false;
  const activeSignalIdx = findLastIndex(allLines, (l) =>
    /开了一局|当前房间正在进行|对局状态|状态[:：]\s*(进行中|等待开始|已结束)|state:\s*(playing|waiting|ended)/i.test(l),
  );
  return noGameIdx > activeSignalIdx;
}

function gameLabel(game: GameKind, locale: Locale): string {
  if (game === 'doushou') return locale === 'zh' ? '斗兽棋' : 'Jungle';
  return translate(locale, `game.names.${game}`);
}

function gameMoveHint(game: GameKind, locale: Locale): string {
  if (game === 'doushou') return locale === 'zh' ? '先点自己的动物，再点目标格移动或吃子。' : 'Select your animal, then select a target square.';
  return translate(locale, `game.hints.${game}`);
}

function gameTip(game: GameKind, locale: Locale): string {
  if (game === 'doushou') return locale === 'zh' ? '斗兽棋：鼠可入河，狮虎可跳河，进入对方兽穴获胜。' : 'Jungle: rats enter rivers, lions/tigers jump rivers, entering enemy den wins.';
  return translate(locale, `game.tips.${game}`);
}

function parseTurnName(board: string): string {
  const line = board.split('\n').find((l) => /^(turn|轮到)[:：]/i.test(l.trim()));
  if (!line) {
    const cnLine = board.split('\n').find((l) => /^轮到\s+(黑|白|红)(?:方)?\s+.+/.test(l.trim()));
    // xiangqi uses 走子; gomoku/go use 落子; doushou may use 行棋 / 走棋.
    const m = cnLine?.trim().match(/^轮到\s+(?:黑|白|红)(?:方)?\s+(\S+?)(?:\s+(?:落子|走子|走棋|行棋)|（|$)/);
    return m ? m[1].trim() : '';
  }
  return line.replace(/^(turn|轮到)[:：]\s*/i, '').trim().split(/\s+/)[0] || '';
}

function inSeats(board: string, nickname: string): boolean {
  const esc = nickname.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const re = new RegExp(`^#\\d+\\s+${esc}[:：]`, 'm');
  if (re.test(board)) return true;
  return new RegExp(`(?:黑|白|红)方(?:（[^）]+）)?[:：]\\s*${esc}(?:\\s|$)`, 'm').test(board) ||
    new RegExp(`(?:黑|白|红)[:：]\\s*${esc}(?:\\s|$)`, 'm').test(board);
}

function hostName(board: string): string {
  const line = board.split('\n').find((l) => /^#1\s+[^:：]+[:：]/.test(l.trim()));
  if (!line) {
    const black = board.match(/黑方(?:（[^）]+）)?[:：]\s*([^\s]+)/) || board.match(/黑[:：]\s*([^\s]+)/);
    return black ? black[1].trim() : '';
  }
  const m = line.trim().match(/^#1\s+([^:：]+)[:：]/);
  return m ? m[1].trim() : '';
}

function latestIssue(lines: string[]): string {
  const tailStart = Math.max(0, lines.length - 80);
  let seenFreshBoard = false;
  for (let i = lines.length - 1; i >= tailStart; i--) {
    const l = lines[i];
    if (
      l.includes('状态：') ||
      l.includes('轮到：') ||
      l.includes('公共牌') ||
      l.includes('底池=') ||
      l.includes('第1行：') ||
      l.includes('row1:')
    ) {
      seenFreshBoard = true;
    }
    if (
      l.includes('执行失败') ||
      l.includes('未知游戏') ||
      l.includes('用法：') ||
      l.includes('积分不足') ||
      l.includes('not enough') ||
      l.includes('usage:')
    ) {
      if (!seenFreshBoard) return l;
      return '';
    }
  }
  return '';
}

function extractPlayerStats(board: string, locale: Locale): Array<{ name: string; label: string; value: number }> {
  const out: Array<{ name: string; label: string; value: number }> = [];
  for (const line of board.split('\n')) {
    const score = line.match(/^#\d+\s+([^:：]+)\s*[:：]\s*积分\s*=\s*(\d+)/);
    if (score) {
      out.push({ name: score[1].trim(), label: translate(locale, 'game.advisor.scoreLabel'), value: Number(score[2]) });
      continue;
    }
    const bull = line.match(/^\-\s+([^:：]+)[:：]\s+牛头=(\d+)/);
    if (bull) {
      out.push({ name: bull[1].trim(), label: translate(locale, 'game.advisor.bullLabel'), value: Number(bull[2]) });
    }
  }
  return out;
}

type Advisor = {
  title: string;
  detail: string;
  level: 'info' | 'warn' | 'error';
  primaryCmd?: string;
  primaryLabel?: string;
  secondaryCmd?: string;
  secondaryLabel?: string;
};

function isMyActiveTurn(game: GameKind, board: string, nickname: string): boolean {
  if (game === 'none' || !board.trim() || !nickname) return false;
  if (!inSeats(board, nickname)) return false;
  const stateLine = board.split('\n').find((l) => l.includes('state:') || l.includes('状态：')) || '';
  if (stateLine.includes('waiting') || stateLine.includes('等待开始')) return false;
  if (stateLine.includes('ended') || stateLine.includes('已结束')) return false;
  return parseTurnName(board) === nickname;
}

function buildAdvisor(locale: Locale, game: GameKind, board: string, systemLines: string[], nickname: string): Advisor {
  const tr = (key: string, vars?: Record<string, string | number>) => translate(locale, key, vars);
  const issue = latestIssue(systemLines);
  if (issue) {
    return {
      title: tr('game.advisor.lastFailed'),
      detail: issue,
      level: 'error',
      primaryCmd: '/game show',
      primaryLabel: tr('game.advisor.refresh'),
      secondaryCmd: '/game help',
      secondaryLabel: tr('game.advisor.viewHelp'),
    };
  }
  if (game === 'none') {
    return {
      title: tr('game.advisor.noActive'),
      detail: tr('game.advisor.noActiveDetail'),
      level: 'info',
      primaryCmd: '/game list',
      primaryLabel: tr('game.list'),
    };
  }
  if (!board.trim()) {
    return {
      title: tr('game.advisor.detected', { name: gameLabel(game, locale) }),
      detail: tr('game.advisor.detectedDetail'),
      level: 'info',
      primaryCmd: '/game join',
      primaryLabel: tr('game.quick.join'),
      secondaryCmd: '/game show',
      secondaryLabel: tr('game.showBoard'),
    };
  }
  const stateLine = board.split('\n').find((l) => l.includes('state:') || l.includes('状态：')) || '';
  const turn = parseTurnName(board);
  const joined = inSeats(board, nickname);
  if (!joined) {
    return {
      title: tr('game.advisor.inProgress', { name: gameLabel(game, locale) }),
      detail: tr('game.advisor.inProgressDetail'),
      level: 'warn',
      primaryCmd: '/game join',
      primaryLabel: tr('game.quick.join'),
      secondaryCmd: '/game seats',
      secondaryLabel: tr('game.quick.seats'),
    };
  }
  if (stateLine.includes('waiting') || stateLine.includes('等待开始')) {
    const host = hostName(board);
    if (host && host === nickname) {
      return {
        title: tr('game.advisor.hostCanStart'),
        detail: tr('game.advisor.hostCanStartDetail'),
        level: 'info',
        primaryCmd: buildGameMove(locale, 'start', game),
        primaryLabel: tr('game.advisor.startGame'),
        secondaryCmd: '/game seats',
        secondaryLabel: tr('game.quick.seats'),
      };
    }
    return {
      title: tr('game.advisor.waitingHost'),
      detail: host ? tr('game.advisor.waitingHostDetailNamed', { host }) : tr('game.advisor.waitingHostDetailGeneric'),
      level: 'warn',
      primaryCmd: '/game seats',
      primaryLabel: tr('game.quick.seats'),
    };
  }
  if (stateLine.includes('ended') || stateLine.includes('已结束')) {
    const host = hostName(board);
    if (host && host === nickname) {
      return {
        title: tr('game.advisor.hostRestart'),
        detail: tr('game.advisor.hostRestartDetail'),
        level: 'info',
        primaryCmd: buildGameMove(locale, 'start', game),
        primaryLabel: tr('game.advisor.dealStart'),
        secondaryCmd: '/game seats',
        secondaryLabel: tr('game.quick.seats'),
      };
    }
    return {
      title: tr('game.advisor.endedWait'),
      detail: host ? tr('game.advisor.waitingHostDetailNamed', { host }) : tr('game.advisor.endedWaitDetail'),
      level: 'warn',
      primaryCmd: '/game seats',
      primaryLabel: tr('game.quick.seats'),
    };
  }
  if (turn) {
    if (turn === nickname) {
      return {
        title: tr('game.advisor.yourTurnTitle'),
        detail: gameMoveHint(game, locale),
        level: 'info',
        primaryCmd: '/game show',
        primaryLabel: tr('game.advisor.refresh'),
      };
    }
    return {
      title: tr('game.advisor.waitingNamed', { name: turn }),
      detail: tr('game.advisor.waitingNamedDetail'),
      level: 'warn',
      primaryCmd: '/game show',
      primaryLabel: tr('game.advisor.refresh'),
    };
  }
  return {
    title: tr('game.advisor.inProgressNamed', { name: gameLabel(game, locale) }),
    detail: gameMoveHint(game, locale),
    level: 'info',
    primaryCmd: '/game show',
    primaryLabel: tr('game.advisor.refresh'),
  };
}

function sanitizeBoard(raw: string): string {
  if (!raw.trim()) return '';
  const noisy = ['commands:', '/names /rooms /join', 'alerts(', 'alert sound backend'];
  const lines = raw
    .split('\n')
    .map((l) => l.replace(/\r/g, '').trimEnd())
    .filter((l) => !noisy.some((n) => l.toLowerCase().includes(n)));
  const out: string[] = [];
  let prev = '';
  for (const line of lines) {
    if (line.trim() === '' && prev.trim() === '') continue;
    if (line === prev) continue;
    out.push(line);
    prev = line;
  }
  return out.join('\n').trim();
}

function isCompleteXiangqiBoard(board: string): boolean {
  if (!board.trim()) return false;
  const tokenRe = /(?:[+\-!][^\s]{1,2}|[·*])/g;
  let rowCount = 0;
  for (const rawLine of board.split('\n')) {
    const tokens = rawLine.trim().match(tokenRe);
    if (!tokens || tokens.length < 9) continue;
    if (tokens.slice(0, 9).every((t) => t === '·' || t === '*' || /^[+\-!]/.test(t))) {
      rowCount += 1;
    }
  }
  return rowCount >= 10 && /楚河汉界/.test(board);
}

export default function GameWorkbench() {
  const { messages, activeRoom, privacyMode, status, users, nickname, locale, doNotDisturb, setComposerText } = useChatStore();
  const tr = (key: string, vars?: Record<string, string | number>) => translate(locale, key, vars);
  const [moveText, setMoveText] = useState('');
  const [showBoard, setShowBoard] = useState(true);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [showWorkbenchContent, setShowWorkbenchContent] = useState(true);
  const [actionHint, setActionHint] = useState('');
  const syncRoomRef = useRef('');

  const roomMessages = messages.get(activeRoom) || [];
  const { board, game, systemLines } = useMemo(() => {
    const allLines = roomMessages
      .filter((m) => m.type === 'system' || m.type === 'game')
      .map((m) => m.content);

    if (hasFreshNoActiveGame(allLines)) {
      return { board: '', game: 'none' as GameKind, systemLines: allLines };
    }

    const noGameIdx = findLastIndex(allLines, (l) => /本房没有进行中的对局/.test(l));
    const scopedLinesRaw = noGameIdx >= 0 ? allLines.slice(noGameIdx + 1) : allLines;
    const scopedLines = scopedLinesRaw.slice(-360);
    const gameLines = scopedLines.filter(isLikelyGameLine);
    const parsed = extractBoardBlock(gameLines);
    if (parsed.game === 'none' && parsed.board) {
      return { board: parsed.board, game: detectGameKind(parsed.board), systemLines: scopedLines };
    }
    if (parsed.game !== 'none') return { ...parsed, systemLines: scopedLines };
    const openGame = inferOpenGame(scopedLines);
    return { board: parsed.board, game: openGame, systemLines: scopedLines };
  }, [roomMessages]);
  const advisor = useMemo(
    () => buildAdvisor(locale, game, board, systemLines, nickname),
    [locale, game, board, systemLines, nickname],
  );
  const rawCleanBoard = useMemo(() => sanitizeBoard(board), [board]);
  const stableBoardRef = useRef<{ game: GameKind; board: string }>({ game: 'none', board: '' });
  let cleanBoard = rawCleanBoard;
  if (game === 'xiangqi') {
    if (isCompleteXiangqiBoard(rawCleanBoard)) {
      stableBoardRef.current = { game, board: rawCleanBoard };
    } else if (stableBoardRef.current.game === 'xiangqi' && stableBoardRef.current.board) {
      cleanBoard = stableBoardRef.current.board;
    }
  } else if (game !== stableBoardRef.current.game) {
    stableBoardRef.current = { game, board: rawCleanBoard };
  }
  const hasBoard = cleanBoard.length > 0;
  const playerStats = useMemo(() => extractPlayerStats(cleanBoard, locale), [cleanBoard, locale]);
  const quickActions = useMemo(() => getQuickByGame(locale, game), [locale, game]);
  const myTurn = useMemo(() => isMyActiveTurn(game, board, nickname), [game, board, nickname]);

  const send = async (cmd: string) => {
    if (status !== 'connected') return false;
    return window.api.sendMessage(cmd);
  };

  const runAction = async (cmd: string) => {
    const ok = await send(cmd);
    if (!ok) {
      setActionHint(tr('game.sendFailed', { cmd }));
      return false;
    }
    setActionHint(tr('game.sent', { cmd }));
    if (shouldRefreshAfter(cmd)) {
      await send('/game show');
    }
    return true;
  };

  const shouldRefreshAfter = (cmd: string): boolean => {
    const t = cmd.trim().toLowerCase();
    if (!t.startsWith('/game')) return false;
    if (t === '/game show' || t === '/game help' || t === '/game list' || t.startsWith('/game rating')) return false;
    return true;
  };

  useEffect(() => {
    if (status !== 'connected') {
      syncRoomRef.current = '';
      return;
    }
    if (syncRoomRef.current === activeRoom) return;
    syncRoomRef.current = activeRoom;
    void send('/game show');
    void send('/game list');
  }, [activeRoom, status]);

  const sendMove = async (payload: string) => {
    const text = payload.trim();
    if (!text) return;
    if (text.startsWith('/')) {
      await runAction(text);
      return;
    }
    const cmd = GameCommandFactory.move(text, locale, game);
    await runAction(cmd);
  };

  const onMove = async () => {
    const raw = moveText.trim();
    if (!raw) return;
    const cmd = raw.startsWith('/') ? raw : GameCommandFactory.move(raw, locale, game);
    await runAction(cmd);
    setMoveText('');
  };

  const disabled = status !== 'connected';

  if (doNotDisturb) {
    if (!myTurn) return null;
    return (
      <div className="game-workbench game-workbench-dnd">
        <span className="game-workbench-dnd-text">{tr('game.advisor.yourTurnTitle')}</span>
      </div>
    );
  }

  return (
    <div className={`game-workbench ${showWorkbenchContent ? 'expanded' : 'collapsed'}`}>
      <div className="game-workbench-header">
        <span>{privacyMode ? tr('game.workbenchPrivacy') : tr('game.workbench')} · {tr('game.current', { name: gameLabel(game, locale) })}</span>
        <div className="game-workbench-actions">
          <button className="mini-btn" onClick={() => setShowWorkbenchContent((v) => !v)}>
            {showWorkbenchContent ? tr('game.collapse') : tr('game.expand')}
          </button>
          <button className="mini-btn" disabled={disabled} onClick={() => runAction('/game show')}>{tr('game.showBoard')}</button>
          <button className="mini-btn" disabled={disabled} onClick={() => runAction('/game help')}>{tr('game.help')}</button>
          <button className="mini-btn" disabled={disabled} onClick={() => runAction('/game list')}>{tr('game.list')}</button>
          {game !== 'none' && (
            <button className="mini-btn" disabled={disabled} onClick={() => runAction('/game end')}>{tr('game.end')}</button>
          )}
        </div>
      </div>

      {showWorkbenchContent && (
        <>
          <div className="game-workbench-quick">
            {quickActions.map((q) => (
              <button key={`${q.label}:${q.cmd}`} className="mini-btn" disabled={disabled} onClick={() => runAction(q.cmd)}>
                {q.label}
              </button>
            ))}
          </div>
          {actionHint && <div className="game-workbench-hint">{actionHint}</div>}
          <div className="game-workbench-hint">{gameTip(game, locale)}</div>
          <div className={`game-advisor game-advisor-${advisor.level}`}>
            <div className="game-advisor-title">{advisor.title}</div>
            <div className="game-advisor-detail">{advisor.detail}</div>
            <div className="game-advisor-actions">
              {advisor.primaryCmd && advisor.primaryLabel && (
                <button className="mini-btn" disabled={disabled} onClick={() => runAction(advisor.primaryCmd!)}>
                  {advisor.primaryLabel}
                </button>
              )}
              {advisor.secondaryCmd && advisor.secondaryLabel && (
                <button className="mini-btn" disabled={disabled} onClick={() => runAction(advisor.secondaryCmd!)}>
                  {advisor.secondaryLabel}
                </button>
              )}
            </div>
          </div>

          {game === 'chess' && <ChessPanel disabled={disabled} nickname={nickname} boardText={board} sendMove={sendMove} />}
          {game === 'gomoku' && <GomokuPanel disabled={disabled} nickname={nickname} boardText={board} onPick={(r, c) => sendMove(GameCommandFactory.gomokuMove(r, c, locale))} onSoulDraft={setComposerText} />}
          {game === 'go' && <GoPanel disabled={disabled} nickname={nickname} boardText={board} onPick={(r, c) => sendMove(GameCommandFactory.goMove(r, c, locale))} onCmd={(cmd) => sendMove(cmd)} />}
          {game === 'xiangqi' && <XiangqiPanel disabled={disabled} nickname={nickname} boardText={cleanBoard} onMove={(fr, fc, tr, tc) => sendMove(GameCommandFactory.xiangqiCoordMove(fr, fc, tr, tc, locale))} />}
          {game === 'doushou' && <DoushouPanel disabled={disabled} nickname={nickname} boardText={cleanBoard} onMove={(fr, fc, tr, tc) => sendMove(GameCommandFactory.doushouCoordMove(fr, fc, tr, tc, locale))} />}

          {game === 'sanguo' && <SanguoPanel disabled={disabled} users={users} nickname={nickname} boardText={board} onCmd={(cmd) => sendMove(cmd)} />}
          {game === 'werewolf' && <WerewolfPanel disabled={disabled} users={users} nickname={nickname} boardText={board} onCmd={(cmd) => sendMove(cmd)} />}

          {game === 'holdem' && <HoldemPanel disabled={disabled} nickname={nickname} onCmd={(cmd) => sendMove(cmd)} boardText={board} />}
          {game === 'zjh' && <ZjhPanel disabled={disabled} users={users} nickname={nickname} onCmd={(cmd) => sendMove(cmd)} boardText={board} />}
          {game === 'niutou' && <NiuTouPanel disabled={disabled} nickname={nickname} boardText={board} onCmd={(cmd) => sendMove(cmd)} />}

          {playerStats.length > 0 && (
            <div className="game-chip-row">
              {playerStats.map((s) => (
                <span key={`${s.name}-${s.label}`} className="game-workbench-hint">{s.name}：{s.label} {s.value}</span>
              ))}
            </div>
          )}

          <div className="game-workbench-toolbar">
            <span className="game-workbench-hint-inline">{tr('game.oneGameRule')}</span>
            <div className="game-workbench-toolbar-actions">
              {hasBoard && (
                <button className="mini-btn" disabled={disabled} onClick={() => setShowBoard((v) => !v)}>
                  {showBoard ? tr('game.advisor.collapseBoard') : tr('game.advisor.expandBoard')}
                </button>
              )}
              <button className="mini-btn" disabled={disabled} onClick={() => setShowAdvanced((v) => !v)}>
                {showAdvanced ? tr('game.hideAdvanced') : tr('game.advancedInput')}
              </button>
            </div>
          </div>

          {hasBoard && showBoard && <pre className="game-workbench-body">{cleanBoard}</pre>}
          {!hasBoard && <div className="game-workbench-empty">{tr('game.advisor.noBoard')}</div>}

          {showAdvanced ? (
            <div className="game-workbench-input">
              <textarea
                className="game-workbench-command"
                value={moveText}
                onChange={(e) => setMoveText(e.target.value)}
                placeholder={tr('game.movePlaceholder')}
                disabled={disabled}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    onMove();
                  }
                }}
              />
              <button className="send-button" onClick={onMove} disabled={disabled || !moveText.trim()}>{tr('game.sendMove')}</button>
            </div>
          ) : (
            <div className="game-workbench-input-compact">{tr('game.advisor.advancedCompact')}</div>
          )}
        </>
      )}
      {!showWorkbenchContent && (
        <div className="game-workbench-input-compact">{tr('game.advisor.collapsedCompact')}</div>
      )}
    </div>
  );
}


