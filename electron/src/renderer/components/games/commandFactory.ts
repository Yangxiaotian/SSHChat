import { GameKind, QuickAction } from './types';

export const quickByGame: Record<GameKind, QuickAction[]> = {
  none: [
    { label: 'New Chess', cmd: '/game new chess' },
    { label: 'New Gomoku', cmd: '/game new gomoku' },
    { label: 'New Xiangqi', cmd: '/game new xiangqi' },
    { label: 'New Sanguo', cmd: '/game new sanguo' },
    { label: 'New Werewolf', cmd: '/game new werewolf' },
  ],
  chess: [
    { label: 'Show', cmd: '/game show' },
    { label: 'Join', cmd: '/game join' },
    { label: 'Seats', cmd: '/game seats' },
    { label: 'PGN', cmd: '/game pgn' },
    { label: 'Resign', cmd: '/game resign' },
  ],
  gomoku: [
    { label: 'Show', cmd: '/game show' },
    { label: 'Join', cmd: '/game join' },
    { label: 'Seats', cmd: '/game seats' },
    { label: 'Resign', cmd: '/game resign' },
    { label: 'Abort', cmd: '/game abort' },
  ],
  xiangqi: [
    { label: 'Show', cmd: '/game show' },
    { label: 'Join', cmd: '/game join' },
    { label: 'Seats', cmd: '/game seats' },
    { label: 'Resign', cmd: '/game resign' },
    { label: 'Abort', cmd: '/game abort' },
  ],
  sanguo: [
    { label: 'Show', cmd: '/game show' },
    { label: 'Start', cmd: '/game move 开始' },
    { label: 'Seats', cmd: '/game seats' },
    { label: 'Generals', cmd: '/game move 武将' },
    { label: 'Pass', cmd: '/game move 过' },
  ],
  werewolf: [
    { label: 'Show', cmd: '/game show' },
    { label: 'Join', cmd: '/game join' },
    { label: 'Seats', cmd: '/game seats' },
    { label: 'Start', cmd: '/game move start' },
    { label: 'Pass', cmd: '/game move pass' },
  ],
};

type SanguoAction = 'attack' | 'duel' | 'fire' | 'pass';
type WerewolfAction = 'vote' | 'kill' | 'check' | 'save' | 'poison';

export const GameCommandFactory = {
  move(payload: string): string {
    return `/game move ${payload.trim()}`;
  },
  chessMove(from: string, to: string): string {
    return this.move(`${from}${to}`);
  },
  gomokuMove(row: number, col: number): string {
    return this.move(`${row} ${col}`);
  },
  xiangqiCoordMove(fr: number, fc: number, tr: number, tc: number): string {
    return this.move(`${fr} ${fc} ${tr} ${tc}`);
  },
  sanguoAction(action: SanguoAction, target?: string): string {
    if (action === 'pass') return this.move('过');
    if (!target) return this.move('过');
    if (action === 'attack') return this.move(`杀 ${target}`);
    if (action === 'duel') return this.move(`决斗 ${target}`);
    return this.move(`火攻 ${target}`);
  },
  werewolfAction(action: WerewolfAction, target?: string): string {
    if (action === 'save') return this.move('save');
    if (!target) return this.move('pass');
    if (action === 'vote') return this.move(`vote ${target}`);
    if (action === 'kill') return this.move(`kill ${target}`);
    if (action === 'check') return this.move(`check ${target}`);
    return this.move(`poison ${target}`);
  },
};
