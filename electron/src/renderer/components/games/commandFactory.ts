import { GameKind, QuickAction } from './types';

export const quickByGame: Record<GameKind, QuickAction[]> = {
  none: [
    { label: '新开国际象棋', cmd: '/game new chess' },
    { label: '国际象棋 AI', cmd: '/game new chess ai normal' },
    { label: '新开五子棋', cmd: '/game new gomoku' },
    { label: '五子棋 AI（人机）', cmd: '/game new gomoku ai normal' },
    { label: '新开围棋', cmd: '/game new go' },
    { label: '新开象棋', cmd: '/game new xiangqi' },
    { label: '象棋 AI', cmd: '/game new xiangqi ai normal' },
    { label: '新开三国杀', cmd: '/game new sanguo' },
    { label: '新开狼人杀', cmd: '/game new werewolf' },
    { label: '新开德州', cmd: '/game new holdem' },
    { label: '新开炸金花', cmd: '/game new zjh' },
    { label: '新开牛头王', cmd: '/game new niutou' },
    { label: '我的棋类积分', cmd: '/game rating' },
  ],
  chess: [
    { label: '显示棋盘', cmd: '/game show' },
    { label: '查看积分', cmd: '/game rating chess' },
    { label: '加入对局', cmd: '/game join' },
    { label: '查看席位', cmd: '/game seats' },
    { label: '申请悔棋', cmd: '/game undo' },
    { label: '同意悔棋', cmd: '/game undo accept' },
    { label: 'PGN', cmd: '/game pgn' },
    { label: '认输', cmd: '/game resign' },
    { label: '结束对局', cmd: '/game end' },
  ],
  gomoku: [
    { label: '显示棋盘', cmd: '/game show' },
    { label: '查看积分', cmd: '/game rating gomoku' },
    { label: '加入对局', cmd: '/game join' },
    { label: '查看席位', cmd: '/game seats' },
    { label: '申请悔棋', cmd: '/game undo' },
    { label: '同意悔棋', cmd: '/game undo accept' },
    { label: '认输', cmd: '/game resign' },
    { label: '终止对局', cmd: '/game abort' },
    { label: '结束对局', cmd: '/game end' },
  ],
  go: [
    { label: '显示棋盘', cmd: '/game show' },
    { label: '查看积分', cmd: '/game rating go' },
    { label: '加入对局', cmd: '/game join' },
    { label: '查看席位', cmd: '/game seats' },
    { label: '停一手', cmd: '/game move pass' },
    { label: '申请悔棋', cmd: '/game undo' },
    { label: '同意悔棋', cmd: '/game undo accept' },
    { label: '认输', cmd: '/game resign' },
    { label: '终止对局', cmd: '/game abort' },
    { label: '结束对局', cmd: '/game end' },
  ],
  xiangqi: [
    { label: '显示棋盘', cmd: '/game show' },
    { label: '查看积分', cmd: '/game rating xiangqi' },
    { label: '加入对局', cmd: '/game join' },
    { label: '查看席位', cmd: '/game seats' },
    { label: '申请悔棋', cmd: '/game undo' },
    { label: '同意悔棋', cmd: '/game undo accept' },
    { label: '认输', cmd: '/game resign' },
    { label: '终止对局', cmd: '/game abort' },
    { label: '结束对局', cmd: '/game end' },
  ],
  sanguo: [
    { label: '显示局面', cmd: '/game show' },
    { label: '加入对局', cmd: '/game join' },
    { label: '开始', cmd: '/game move 开始' },
    { label: '查看席位', cmd: '/game seats' },
    { label: '武将池', cmd: '/game move 武将' },
    { label: '过', cmd: '/game move 过' },
    { label: '结束对局', cmd: '/game end' },
  ],
  werewolf: [
    { label: '显示局面', cmd: '/game show' },
    { label: '加入对局', cmd: '/game join' },
    { label: '查看席位', cmd: '/game seats' },
    { label: '开始', cmd: '/game move start' },
    { label: '过', cmd: '/game move pass' },
    { label: '结束对局', cmd: '/game end' },
  ],
  holdem: [
    { label: '显示牌面', cmd: '/game show' },
    { label: '加入对局', cmd: '/game join' },
    { label: '查看席位', cmd: '/game seats' },
    { label: '开始', cmd: '/game move start' },
    { label: '过牌', cmd: '/game move check' },
    { label: '跟注', cmd: '/game move call' },
    { label: '全下', cmd: '/game move allin' },
    { label: '弃牌', cmd: '/game move fold' },
    { label: '机器人硬核', cmd: '/game move bot hard' },
    { label: '结束对局', cmd: '/game end' },
  ],
  zjh: [
    { label: '显示牌面', cmd: '/game show' },
    { label: '加入对局', cmd: '/game join' },
    { label: '查看席位', cmd: '/game seats' },
    { label: '开始', cmd: '/game move start' },
    { label: '看牌', cmd: '/game move look' },
    { label: '跟注', cmd: '/game move follow' },
    { label: '弃牌', cmd: '/game move fold' },
    { label: '机器人硬核', cmd: '/game move bot hard' },
    { label: '结束对局', cmd: '/game end' },
  ],
  niutou: [
    { label: '显示局面', cmd: '/game show' },
    { label: '加入对局', cmd: '/game join' },
    { label: '查看席位', cmd: '/game seats' },
    { label: '开始', cmd: '/game move start' },
    { label: '机器人硬核', cmd: '/game move bot hard' },
    { label: '结束对局', cmd: '/game end' },
  ],
};

export const GameCommandFactory = {
  move(payload: string): string {
    return `/game move ${payload.trim()}`;
  },
  chessMove(from: string, to: string): string {
    return GameCommandFactory.move(`${from}${to}`);
  },
  gomokuMove(row: number, col: number): string {
    return GameCommandFactory.move(`${row} ${col}`);
  },
  goMove(row: number, col: number): string {
    return GameCommandFactory.move(`${row} ${col}`);
  },
  xiangqiCoordMove(fr: number, fc: number, tr: number, tc: number): string {
    return GameCommandFactory.move(`${fr} ${fc} ${tr} ${tc}`);
  },
};
