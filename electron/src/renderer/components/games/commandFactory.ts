import type { Locale } from '../../i18n/types';
import { buildGameMove, buildGameMoveFromKey } from '../../i18n/gameCommands';
import { t } from '../../i18n';
import { GameKind, QuickAction } from './types';

function qa(labelKey: string, cmd: string, locale: Locale): QuickAction {
  return { label: t(locale, labelKey), cmd };
}

export function getQuickByGame(locale: Locale, game: GameKind): QuickAction[] {
  const m = (key: string, cmd: string) => qa(`game.quick.${key}`, cmd, locale);
  const move = (key: string, verb: string, gameKind?: GameKind) =>
    qa(`game.quick.${key}`, buildGameMove(locale, verb, gameKind), locale);

  const catalog: Record<GameKind, QuickAction[]> = {
    none: [
      m('newChess', '/game new chess'),
      m('chessAi', '/game new chess ai normal'),
      m('newGomoku', '/game new gomoku'),
      m('gomokuAi', '/game new gomoku ai normal'),
      m('newGo', '/game new go'),
      m('newXiangqi', '/game new xiangqi'),
      m('xiangqiAi', '/game new xiangqi ai normal'),
      { label: locale === 'zh' ? '新开斗兽棋' : 'New Jungle', cmd: '/game new doushou' },
      m('newSanguo', '/game new sanguo'),
      m('newWerewolf', '/game new werewolf'),
      m('newHoldem', '/game new holdem'),
      m('newZjh', '/game new zjh'),
      m('newNiutou', '/game new niutou'),
      m('myRating', '/game rating'),
    ],
    chess: [
      m('show', '/game show'),
      m('rating', '/game rating chess'),
      m('join', '/game join'),
      m('seats', '/game seats'),
      m('undo', '/game undo'),
      m('undoAccept', '/game undo accept'),
      m('pgn', '/game pgn'),
      m('resign', '/game resign'),
      m('end', '/game end'),
    ],
    gomoku: [
      m('show', '/game show'),
      m('rating', '/game rating gomoku'),
      m('join', '/game join'),
      m('seats', '/game seats'),
      m('undo', '/game undo'),
      m('undoAccept', '/game undo accept'),
      m('resign', '/game resign'),
      m('abort', '/game abort'),
      m('end', '/game end'),
    ],
    go: [
      m('show', '/game show'),
      m('rating', '/game rating go'),
      m('join', '/game join'),
      m('seats', '/game seats'),
      move('pass', 'pass', 'go'),
      m('undo', '/game undo'),
      m('undoAccept', '/game undo accept'),
      m('resign', '/game resign'),
      m('abort', '/game abort'),
      m('end', '/game end'),
    ],
    xiangqi: [
      m('show', '/game show'),
      m('rating', '/game rating xiangqi'),
      m('join', '/game join'),
      m('seats', '/game seats'),
      m('undo', '/game undo'),
      m('undoAccept', '/game undo accept'),
      m('resign', '/game resign'),
      m('abort', '/game abort'),
      m('end', '/game end'),
    ],
    doushou: [
      m('show', '/game show'),
      { label: locale === 'zh' ? '查看积分' : 'Rating', cmd: '/game rating doushou' },
      m('join', '/game join'),
      m('seats', '/game seats'),
      m('undo', '/game undo'),
      m('undoAccept', '/game undo accept'),
      m('resign', '/game resign'),
      m('abort', '/game abort'),
      m('end', '/game end'),
    ],
    sanguo: [
      m('show', '/game show'),
      m('join', '/game join'),
      move('sanguoStart', 'start', 'sanguo'),
      m('seats', '/game seats'),
      move('sanguoGenerals', 'generals', 'sanguo'),
      move('sanguoPass', 'pass', 'sanguo'),
      m('end', '/game end'),
    ],
    werewolf: [
      m('show', '/game show'),
      m('join', '/game join'),
      m('seats', '/game seats'),
      qa('game.quick.werewolfStart', buildGameMove(locale, 'start', 'werewolf'), locale),
      qa('game.quick.werewolfPass', buildGameMove(locale, 'pass', 'werewolf'), locale),
      m('end', '/game end'),
    ],
    holdem: [
      m('show', '/game show'),
      m('join', '/game join'),
      m('seats', '/game seats'),
      move('werewolfStart', 'start', 'holdem'),
      move('holdemCheck', 'check', 'holdem'),
      move('holdemCall', 'call', 'holdem'),
      move('holdemAllin', 'allin', 'holdem'),
      move('holdemFold', 'fold', 'holdem'),
      qa('game.quick.holdemBot', buildGameMove(locale, 'bot hard', 'holdem'), locale),
      m('end', '/game end'),
    ],
    zjh: [
      m('show', '/game show'),
      m('join', '/game join'),
      m('seats', '/game seats'),
      move('werewolfStart', 'start', 'zjh'),
      move('zjhLook', 'look', 'zjh'),
      move('zjhFollow', 'follow', 'zjh'),
      move('zjhFold', 'fold', 'zjh'),
      qa('game.quick.niutouBot', buildGameMove(locale, 'bot hard', 'zjh'), locale),
      m('end', '/game end'),
    ],
    niutou: [
      m('show', '/game show'),
      m('join', '/game join'),
      m('seats', '/game seats'),
      move('werewolfStart', 'start', 'niutou'),
      qa('game.quick.niutouBot', buildGameMove(locale, 'bot hard', 'niutou'), locale),
      m('end', '/game end'),
    ],
  };

  return catalog[game] ?? catalog.none;
}

export const GameCommandFactory = {
  move(payload: string, locale: Locale, game?: GameKind): string {
    return buildGameMove(locale, payload.trim(), game);
  },
  chessMove(from: string, to: string, locale: Locale): string {
    return GameCommandFactory.move(`${from}${to}`, locale);
  },
  gomokuMove(row: number, col: number, locale: Locale): string {
    return GameCommandFactory.move(`${row} ${col}`, locale);
  },
  goMove(row: number, col: number, locale: Locale): string {
    return GameCommandFactory.move(`${row} ${col}`, locale, 'go');
  },
  xiangqiCoordMove(fr: number, fc: number, tr: number, tc: number, locale: Locale): string {
    return GameCommandFactory.move(`coord ${fr} ${fc} ${tr} ${tc}`, locale, 'xiangqi');
  },
  doushouCoordMove(fr: number, fc: number, tr: number, tc: number, locale: Locale): string {
    return GameCommandFactory.move(`${fr} ${fc} ${tr} ${tc}`, locale, 'doushou');
  },
  hostStart(locale: Locale, game?: GameKind): string {
    return buildGameMoveFromKey(locale, 'start');
  },
};

/** @deprecated Use getQuickByGame(locale, game) */
export const quickByGame = {} as Record<GameKind, QuickAction[]>;
