export type GameKind = 'none' | 'chess' | 'gomoku' | 'xiangqi' | 'sanguo' | 'werewolf';

export type QuickAction = { label: string; cmd: string };

export type GamePanelProps = {
  disabled: boolean;
  sendMove: (payload: string) => Promise<void>;
  users: string[];
};
