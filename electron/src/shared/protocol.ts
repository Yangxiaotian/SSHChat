// Shared types between main and renderer processes

export interface ConnectionConfig {
  host: string;
  user: string;
  sshPort: number;
  chatPort?: number; // default 12345
}

export interface ChatMessage {
  id: string;
  room: string;
  sender: string;
  content: string;
  timestamp: number;
  type: 'chat' | 'system' | 'pm' | 'game' | 'join' | 'leave';
  /** Library page body lines are hidden from the chat timeline. */
  hidden?: boolean;
}

export interface RoomInfo {
  name: string;
  isDefault: boolean;
  unreadCount: number;
  lastActivity: number;
}

export interface UserInfo {
  name: string;
  room: string;
}

export interface ProcessInfo {
  pid: number;
  name: string;
}

export interface GomokuRapfiAnalyzeRequest {
  board: number[][];
  mySide: 1 | -1;
  timeoutMs?: number;
  mode?: 'move' | 'ponder';
}

export interface GomokuRapfiAnalyzeResponse {
  ok: boolean;
  row?: number;
  col?: number;
  ms: number;
  enginePath?: string;
  error?: string;
}

export interface GoKataGoAnalyzeRequest {
  board: number[][];
  mySide: 1 | 2;
  komi?: number;
  moves?: Array<{ player: 'B' | 'W'; move: string }>;
  maxVisits?: number;
  maxTimeSec?: number;
  timeoutMs?: number;
}

export type GoKataGoSuggestion = {
  row: number;
  col: number;
  winrate?: number;
  scoreLead?: number;
  visits?: number;
  order: number;
};

export interface GoKataGoAnalyzeResponse {
  ok: boolean;
  ms: number;
  suggestions?: GoKataGoSuggestion[];
  enginePath?: string;
  modelPath?: string;
  configPath?: string;
  error?: string;
}

export type ConnectionStatus = 'disconnected' | 'connecting' | 'connected' | 'error';

// IPC Channel names
export const IPC_CHANNELS = {
  // Main -> Renderer
  CHAT_MESSAGE: 'chat:message',
  ROOM_UPDATE: 'chat:room-update',
  USER_UPDATE: 'chat:user-update',
  CONNECTION_STATUS: 'chat:connection-status',
  CONNECTION_ERROR: 'chat:error',
  NEWS_DATA: 'chat:news',

  // Renderer -> Main
  SEND_MESSAGE: 'chat:send',
  JOIN_ROOM: 'chat:join',
  SWITCH_ROOM: 'chat:switch',
  CONNECT: 'chat:connect',
  DISCONNECT: 'chat:disconnect',
  REQUEST_USERS: 'chat:request-users',
  REQUEST_NEWS: 'chat:request-news',
  NOTIFY_ATTENTION: 'chat:notify-attention',
  SHAKE_WINDOW: 'chat:shake-window',
  SAVE_CONFIG: 'config:save',
  LOAD_CONFIG: 'config:load',

  // Monitor
  GET_PROCESSES: 'monitor:get-processes',
  KILL_PROCESS: 'monitor:kill-process',
  MINIMIZE_WINDOW: 'monitor:minimize-window',
  CLOSE_APP: 'monitor:close-app',

  // Gomoku external engine
  GOMOKU_RAPFI_ANALYZE: 'gomoku:rapfi-analyze',

  // Go external engine
  GO_KATAGO_ANALYZE: 'go:katago-analyze',
  GO_KATAGO_WARMUP: 'go:katago-warmup',
} as const;

// Special message tokens
export const SHAKE_TOKEN = '__VSCODEEN_SHAKE__';

// Message parsing patterns (from sshchat_gui.py)
export const PATTERNS = {
  ROOM_CHAT: /^\[#([^\]]+)\]\s+\[([^\]]+)\] (.*)$/,
  CHAT: /^\[([^\]]+)\] (.*)$/,
  SYSTEM: /^\[\*\]\s*(.*)$/,
  PM: /^\[PM from ([^\]]+)\] (.*)$/,
  JOIN: /^\[\+\]\s*(.+?)\s+joined\s+#(\S+)/,
  LEAVE: /^\[!\]\s*(.+?)\s+left\s+#(\S+)/,
  ROOMS: /^Rooms:\s*(.*)$/,
  ROOM_SWITCH: /to\s+#([a-zA-Z0-9_-]{1,32})/,
} as const;
