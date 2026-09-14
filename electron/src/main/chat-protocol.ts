import { ChatMessage, PATTERNS } from '../shared/protocol';

let messageIdCounter = 0;

function generateId(): string {
  return `msg_${Date.now()}_${++messageIdCounter}`;
}

export function parseServerLine(line: string, currentRoom: string): ChatMessage | null {
  const trimmed = line.trim();
  if (!trimmed) return null;
  const stripClockPrefix = (text: string): string =>
    text.replace(/^\d{2}:\d{2}:\d{2}\s+/, '');
  const withoutClock = stripClockPrefix(trimmed);

  if (/^>>\s+/.test(withoutClock)) {
    // Local input echo like ">> 1" should not be shown in chat timeline.
    return null;
  }

  // Normalize prefixed prompt/echo time tokens, e.g.
  // ">[09:02:49] [#default] [alice] hi" or "[09:02:49] [#default] [alice] hi".
  let normalized = withoutClock;
  while (true) {
    const next = normalized.replace(/^>?\[\d{2}:\d{2}:\d{2}\]\s*/, '');
    if (next === normalized) break;
    normalized = next;
  }

  // Room chat: [#room] [name] text
  const roomMatch = PATTERNS.ROOM_CHAT.exec(normalized);
  if (roomMatch) {
    const room = roomMatch[1];
    const sender = roomMatch[2];
    const content = roomMatch[3];

    if (sender === '*') {
      return {
        id: generateId(),
        room,
        sender: '*',
        content,
        timestamp: Date.now(),
        type: 'system',
      };
    }

    return {
      id: generateId(),
      room,
      sender,
      content,
      timestamp: Date.now(),
      type: 'chat',
    };
  }

  // PM: [PM from name] text
  const pmMatch = PATTERNS.PM.exec(normalized);
  if (pmMatch) {
    return {
      id: generateId(),
      room: currentRoom,
      sender: pmMatch[1],
      content: pmMatch[2],
      timestamp: Date.now(),
      type: 'pm',
    };
  }

  // Join: [+] name joined #room
  const joinMatch = PATTERNS.JOIN.exec(normalized);
  if (joinMatch) {
    return {
      id: generateId(),
      room: joinMatch[2],
      sender: '+',
      content: `${joinMatch[1]} joined #${joinMatch[2]}`,
      timestamp: Date.now(),
      type: 'join',
    };
  }

  // Leave: [!] name left #room
  const leaveMatch = PATTERNS.LEAVE.exec(normalized);
  if (leaveMatch) {
    return {
      id: generateId(),
      room: leaveMatch[2],
      sender: '!',
      content: `${leaveMatch[1]} left #${leaveMatch[2]}`,
      timestamp: Date.now(),
      type: 'leave',
    };
  }

  // System message: [*] text
  const sysMatch = PATTERNS.SYSTEM.exec(normalized);
  if (sysMatch) {
    return {
      id: generateId(),
      room: currentRoom,
      sender: '*',
      content: sysMatch[1],
      timestamp: Date.now(),
      type: 'system',
    };
  }

  // Plain chat: [name] text
  const chatMatch = PATTERNS.CHAT.exec(normalized);
  if (chatMatch) {
    return {
      id: generateId(),
      room: currentRoom,
      sender: chatMatch[1],
      content: chatMatch[2],
      timestamp: Date.now(),
      type: 'chat',
    };
  }

  // Fallback: system message
  return {
    id: generateId(),
    room: currentRoom,
    sender: '*',
    content: normalized,
    timestamp: Date.now(),
    type: 'system',
  };
}

export function extractRoomsFromSystem(message: string): string[] | null {
  const roomsMatch = PATTERNS.ROOMS.exec(message);
  if (roomsMatch) {
    const roomsText = roomsMatch[1];
    const rooms = roomsText.match(/\*?#([a-zA-Z0-9_-]{1,32})/g);
    if (rooms) {
      return rooms.map(r => r.replace(/^\*?#/, ''));
    }
  }
  return null;
}

export function extractActiveRoom(message: string): string | null {
  const explicit = message.match(/Active room\s+#([a-zA-Z0-9_-]{1,32})/i);
  if (explicit) {
    return explicit[1];
  }
  const switchMatch = PATTERNS.ROOM_SWITCH.exec(message);
  if (switchMatch) {
    return switchMatch[1];
  }
  const activeMatch = message.match(/\*#([a-zA-Z0-9_-]{1,32})/);
  if (activeMatch) {
    return activeMatch[1];
  }
  return null;
}

export function extractUsersSnapshot(
  message: string,
): { room: string; count: number; users: string[] } | null {
  const m = message.match(/^#([a-zA-Z0-9_-]{1,32})\s+\((\d+)\):\s*(.*)$/);
  if (!m) return null;

  const room = m[1];
  const count = Number.parseInt(m[2], 10);
  const raw = m[3].trim();
  const users =
    !raw || raw === '(empty)'
      ? []
      : raw.split(',').map((u) => u.trim()).filter(Boolean);

  return { room, count: Number.isNaN(count) ? users.length : count, users };
}

export function buildSendMessage(nickname: string, text: string): string {
  return `[${nickname}] ${text}\n`;
}
