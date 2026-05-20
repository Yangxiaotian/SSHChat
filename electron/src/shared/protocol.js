"use strict";
// Shared types between main and renderer processes
Object.defineProperty(exports, "__esModule", { value: true });
exports.PATTERNS = exports.IPC_CHANNELS = void 0;
// IPC Channel names
exports.IPC_CHANNELS = {
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
    SAVE_CONFIG: 'config:save',
    LOAD_CONFIG: 'config:load',
};
// Message parsing patterns (from sshchat_gui.py)
exports.PATTERNS = {
    ROOM_CHAT: /^\[#([^\]]+)\]\s+\[([^\]]+)\] (.*)$/,
    CHAT: /^\[([^\]]+)\] (.*)$/,
    SYSTEM: /^\[\*\]\s*(.*)$/,
    PM: /^\[PM from ([^\]]+)\] (.*)$/,
    JOIN: /^\[+\]\s*(.+?)\s+joined\s+#(\S+)/,
    LEAVE: /^\[!\]\s*(.+?)\s+left\s+#(\S+)/,
    ROOMS: /^Rooms:\s*(.*)$/,
    ROOM_SWITCH: /to\s+#([a-zA-Z0-9_-]{1,32})/,
};
//# sourceMappingURL=protocol.js.map