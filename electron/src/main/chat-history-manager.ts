import { app } from 'electron';
import * as fs from 'fs';
import * as path from 'path';
import {
  ChatHistoryIdentity,
  ChatHistorySnapshot,
  ChatMessage,
} from '../shared/protocol';

const HISTORY_VERSION = 1;
const MAX_ACCOUNTS = 20;
const MAX_ROOMS_PER_ACCOUNT = 64;
const MAX_MESSAGES_PER_ROOM = 1200;
const VALID_MESSAGE_TYPES = new Set<ChatMessage['type']>([
  'chat',
  'system',
  'pm',
  'game',
  'join',
  'leave',
]);

type StoredAccount = {
  rooms: Record<string, ChatMessage[]>;
  roomNames?: string[];
  updatedAt: number;
};

type StoredHistory = {
  version: number;
  accounts: Record<string, StoredAccount>;
};

function emptyHistory(): StoredHistory {
  return { version: HISTORY_VERSION, accounts: {} };
}

function identityKey(identity: ChatHistoryIdentity): string {
  const host = identity.host.trim().toLowerCase();
  const user = identity.user.trim().toLowerCase();
  const port = Number.isInteger(identity.chatPort) ? identity.chatPort : 12345;
  return `${host}:${port}:${user}`;
}

function sanitizeMessage(value: unknown): ChatMessage | null {
  if (!value || typeof value !== 'object') return null;
  const message = value as Partial<ChatMessage>;
  if (
    typeof message.id !== 'string'
    || typeof message.room !== 'string'
    || typeof message.sender !== 'string'
    || typeof message.content !== 'string'
    || typeof message.timestamp !== 'number'
    || !Number.isFinite(message.timestamp)
    || !VALID_MESSAGE_TYPES.has(message.type as ChatMessage['type'])
  ) {
    return null;
  }
  const room = message.room.trim();
  if (!room) return null;
  return {
    id: message.id,
    room,
    sender: message.sender,
    content: message.content,
    timestamp: message.timestamp,
    type: message.type as ChatMessage['type'],
  };
}

function sanitizeRooms(value: unknown): Record<string, ChatMessage[]> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
  const rooms: Record<string, ChatMessage[]> = {};
  const entries = Object.entries(value).slice(-MAX_ROOMS_PER_ACCOUNT);
  for (const [rawRoom, rawMessages] of entries) {
    const room = rawRoom.trim();
    if (!room || !Array.isArray(rawMessages)) continue;
    rooms[room] = rawMessages
      .map(sanitizeMessage)
      .filter((message): message is ChatMessage => message !== null)
      .slice(-MAX_MESSAGES_PER_ROOM);
  }
  return rooms;
}

function sanitizeRoomNames(value: unknown, rooms: Record<string, ChatMessage[]>): string[] {
  const names = new Set<string>(['default', ...Object.keys(rooms)]);
  if (Array.isArray(value)) {
    for (const rawName of value) {
      if (typeof rawName !== 'string') continue;
      const name = rawName.trim();
      if (/^[a-zA-Z0-9_-]{1,32}$/.test(name)) {
        names.add(name);
      }
    }
  }
  return [...names].slice(0, MAX_ROOMS_PER_ACCOUNT);
}

export class ChatHistoryManager {
  private readonly historyPath: string;

  constructor(baseDir = app.getPath('userData')) {
    this.historyPath = path.join(baseDir, 'chat-history.json');
  }

  getHistoryPath(): string {
    return this.historyPath;
  }

  load(identity: ChatHistoryIdentity): ChatHistorySnapshot {
    const account = this.readHistory().accounts[identityKey(identity)];
    if (!account) return { rooms: {}, roomNames: ['default'] };
    const rooms = sanitizeRooms(account.rooms);
    return { rooms, roomNames: sanitizeRoomNames(account.roomNames, rooms) };
  }

  save(identity: ChatHistoryIdentity, snapshot: ChatHistorySnapshot): boolean {
    if (!identity.host.trim() || !identity.user.trim()) return false;
    const history = this.readHistory();
    const rooms = sanitizeRooms(snapshot.rooms);
    history.accounts[identityKey(identity)] = {
      rooms,
      roomNames: sanitizeRoomNames(snapshot.roomNames, rooms),
      updatedAt: Date.now(),
    };
    history.accounts = Object.fromEntries(
      Object.entries(history.accounts)
        .sort(([, a], [, b]) => b.updatedAt - a.updatedAt)
        .slice(0, MAX_ACCOUNTS),
    );
    this.writeHistory(history);
    return true;
  }

  private readHistory(): StoredHistory {
    try {
      if (!fs.existsSync(this.historyPath)) return emptyHistory();
      const parsed = JSON.parse(fs.readFileSync(this.historyPath, 'utf-8')) as Partial<StoredHistory>;
      if (parsed.version !== HISTORY_VERSION || !parsed.accounts || typeof parsed.accounts !== 'object') {
        return emptyHistory();
      }
      const accounts: Record<string, StoredAccount> = {};
      for (const [key, rawAccount] of Object.entries(parsed.accounts)) {
        if (!rawAccount || typeof rawAccount !== 'object') continue;
        const account = rawAccount as Partial<StoredAccount>;
        const rooms = sanitizeRooms(account.rooms);
        accounts[key] = {
          rooms,
          roomNames: sanitizeRoomNames(account.roomNames, rooms),
          updatedAt: typeof account.updatedAt === 'number' && Number.isFinite(account.updatedAt)
            ? account.updatedAt
            : 0,
        };
      }
      return { version: HISTORY_VERSION, accounts };
    } catch (error) {
      console.error('[ChatHistory] Failed to read history:', error);
      return emptyHistory();
    }
  }

  private writeHistory(history: StoredHistory): void {
    fs.mkdirSync(path.dirname(this.historyPath), { recursive: true });
    const tempPath = `${this.historyPath}.tmp`;
    fs.writeFileSync(tempPath, JSON.stringify(history), 'utf-8');
    try {
      fs.renameSync(tempPath, this.historyPath);
    } catch {
      fs.copyFileSync(tempPath, this.historyPath);
      fs.unlinkSync(tempPath);
    }
  }
}
