import { GameKind, QuickAction } from './types';

export const quickByGame: Record<GameKind, QuickAction[]> = {
  none: [
    { label: 'New Chess', cmd: '/game new chess' },
    { label: 'New Gomoku', cmd: '/game new gomoku' },
    { label: 'New Xiangqi', cmd: '/game new xiangqi' },
    { label: 'New Sanguo', cmd: '/game new sanguo' },
    { label: 'New Werewolf', cmd: '/game new werewolf' },
    { label: 'New Holdem', cmd: '/game new holdem' },
    { label: 'New ZJH', cmd: '/game new zjh' },
    { label: 'New Niutou', cmd: '/game new niutou' },
  ],
  chess: [
    { label: 'Show', cmd: '/game show' },
    { label: 'Join', cmd: '/game join' },
    { label: 'Seats', cmd: '/game seats' },
    { label: 'Undo', cmd: '/game undo' },
    { label: 'Accept Undo', cmd: '/game undo accept' },
    { label: 'PGN', cmd: '/game pgn' },
    { label: 'Resign', cmd: '/game resign' },
  ],
  gomoku: [
    { label: 'Show', cmd: '/game show' },
    { label: 'Join', cmd: '/game join' },
    { label: 'Seats', cmd: '/game seats' },
    { label: 'Undo', cmd: '/game undo' },
    { label: 'Accept Undo', cmd: '/game undo accept' },
    { label: 'Resign', cmd: '/game resign' },
    { label: 'Abort', cmd: '/game abort' },
  ],
  xiangqi: [
    { label: 'Show', cmd: '/game show' },
    { label: 'Join', cmd: '/game join' },
    { label: 'Seats', cmd: '/game seats' },
    { label: 'Undo', cmd: '/game undo' },
    { label: 'Accept Undo', cmd: '/game undo accept' },
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
  holdem: [
    { label: 'Show', cmd: '/game show' },
    { label: 'Join', cmd: '/game join' },
    { label: 'Seats', cmd: '/game seats' },
    { label: 'Start', cmd: '/game move start' },
    { label: 'Check', cmd: '/game move check' },
    { label: 'Call', cmd: '/game move call' },
    { label: 'All-in', cmd: '/game move allin' },
    { label: 'Fold', cmd: '/game move fold' },
    { label: 'Bot Hard', cmd: '/game move bot hard' },
  ],
  zjh: [
    { label: 'Show', cmd: '/game show' },
    { label: 'Join', cmd: '/game join' },
    { label: 'Seats', cmd: '/game seats' },
    { label: 'Start', cmd: '/game move start' },
    { label: 'Look', cmd: '/game move look' },
    { label: 'Follow', cmd: '/game move follow' },
    { label: 'Fold', cmd: '/game move fold' },
    { label: 'Bot Hard', cmd: '/game move bot hard' },
  ],
  niutou: [
    { label: 'Show', cmd: '/game show' },
    { label: 'Join', cmd: '/game join' },
    { label: 'Seats', cmd: '/game seats' },
    { label: 'Start', cmd: '/game move start' },
    { label: 'Bot Hard', cmd: '/game move bot hard' },
  ],
};

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
};
