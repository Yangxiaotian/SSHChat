export type GameKind =
  | 'none'
  | 'chess'
  | 'gomoku'
  | 'go'
  | 'xiangqi'
  | 'doushou'
  | 'sanguo'
  | 'werewolf'
  | 'holdem'
  | 'zjh'
  | 'niutou';

export type QuickAction = { label: string; cmd: string };

export type GamePanelProps = {
  disabled: boolean;
  sendMove: (payload: string) => Promise<void>;
  users: string[];
};
