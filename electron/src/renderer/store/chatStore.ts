import { create } from 'zustand';
import { ChatMessage, ConnectionConfig, ConnectionStatus, RoomInfo } from '../../shared/protocol';

declare global {
  interface Window {
    api: {
      loadConfig: () => Promise<ConnectionConfig | null>;
      saveConfig: (config: ConnectionConfig) => Promise<boolean>;
      connect: (config: ConnectionConfig, nickname: string) => Promise<{ success: boolean; error?: string }>;
      disconnect: () => Promise<boolean>;
      isConnected: () => Promise<boolean>;
      sendMessage: (text: string) => Promise<boolean>;
      joinRoom: (room: string) => Promise<boolean>;
      switchRoom: (room: string) => Promise<boolean>;
      requestUsers: () => Promise<boolean>;
      requestNews: (category?: string) => Promise<boolean>;
      notifyAttention: () => Promise<boolean>;
      onChatMessage: (callback: (message: ChatMessage) => void) => () => void;
      onRoomUpdate: (callback: (rooms: string[] | null, activeRoom: string) => void) => () => void;
      onUserUpdate: (callback: (snapshot: { room: string; count: number; users: string[] }) => void) => () => void;
      onConnectionStatus: (callback: (status: ConnectionStatus) => void) => () => void;
      onError: (callback: (error: string) => void) => () => void;
    };
  }
}

interface ChatState {
  // Connection
  status: ConnectionStatus;
  error: string | null;
  config: ConnectionConfig | null;
  nickname: string;

  // Rooms
  rooms: RoomInfo[];
  activeRoom: string;

  // Messages
  messages: Map<string, ChatMessage[]>;

  // Users
  users: string[];

  // UI State
  showLogin: boolean;
  sidebarView: 'rooms' | 'users' | 'news';
  theme: 'dark' | 'light';
  privacyMode: boolean;
  composerText: string;

  // Actions
  setStatus: (status: ConnectionStatus) => void;
  setError: (error: string | null) => void;
  setConfig: (config: ConnectionConfig | null) => void;
  setNickname: (nickname: string) => void;
  setRooms: (rooms: RoomInfo[]) => void;
  setActiveRoom: (room: string) => void;
  addRoom: (room: string) => void;
  removeRoom: (room: string) => void;
  setRoomUnread: (room: string, count: number) => void;
  addMessage: (message: ChatMessage) => void;
  setUsers: (users: string[]) => void;
  setShowLogin: (show: boolean) => void;
  setSidebarView: (view: 'rooms' | 'users' | 'news') => void;
  setTheme: (theme: 'dark' | 'light') => void;
  toggleTheme: () => void;
  setPrivacyMode: (value: boolean) => void;
  togglePrivacyMode: () => void;
  setComposerText: (value: string) => void;
  /** 清空某房间本地消息；省略 room 时为当前活动房间 */
  clearMessages: (room?: string) => void;
}

export const useChatStore = create<ChatState>((set, get) => ({
  // Initial state
  status: 'disconnected',
  error: null,
  config: null,
  nickname: '',
  rooms: [{ name: 'default', isDefault: true, unreadCount: 0, lastActivity: Date.now() }],
  activeRoom: 'default',
  messages: new Map(),
  users: [],
  showLogin: true,
  sidebarView: 'rooms',
  theme: 'dark',
  privacyMode: true,
  composerText: '',

  // Actions
  setStatus: (status) => set({ status }),
  setError: (error) => set({ error }),
  setConfig: (config) => set({ config }),
  setNickname: (nickname) => set({ nickname }),
  setRooms: (rooms) => {
    const { rooms: prevRooms } = get();
    const byName = new Map(prevRooms.map((r) => [r.name, r]));
    const merged = rooms.map((next) => {
      const prev = byName.get(next.name);
      return prev
        ? { ...next, unreadCount: prev.unreadCount, lastActivity: prev.lastActivity }
        : next;
    });
    for (const prev of prevRooms) {
      if (!merged.find((r) => r.name === prev.name)) {
        merged.push(prev);
      }
    }
    set({ rooms: merged });
  },
  setActiveRoom: (room) => {
    const { rooms } = get();
    set({
      activeRoom: room,
      rooms: rooms.map((r) => (r.name === room ? { ...r, unreadCount: 0 } : r)),
    });
  },

  addRoom: (room) => {
    const { rooms } = get();
    if (!rooms.find(r => r.name === room)) {
      set({ rooms: [...rooms, { name: room, isDefault: room === 'default', unreadCount: 0, lastActivity: Date.now() }] });
    }
  },

  removeRoom: (room) => {
    const { rooms, activeRoom } = get();
    const filtered = rooms.filter(r => r.name !== room);
    if (filtered.length === 0) {
      filtered.push({ name: 'default', isDefault: true, unreadCount: 0, lastActivity: Date.now() });
    }
    set({
      rooms: filtered,
      activeRoom: activeRoom === room ? filtered[0].name : activeRoom,
    });
  },

  setRoomUnread: (room, count) => {
    const { rooms } = get();
    set({
      rooms: rooms.map(r => r.name === room ? { ...r, unreadCount: count } : r),
    });
  },

  addMessage: (message) => {
    const { messages, activeRoom, rooms } = get();
    const roomMessages = messages.get(message.room) || [];
    const newMessages = new Map(messages);
    newMessages.set(message.room, [...roomMessages, message]);

    // Update unread count if not active room
    let newRooms = rooms;
    if (message.room !== activeRoom && message.type !== 'system') {
      newRooms = rooms.map(r =>
        r.name === message.room ? { ...r, unreadCount: r.unreadCount + 1, lastActivity: Date.now() } : r
      );
    }

    set({ messages: newMessages, rooms: newRooms });
  },

  setUsers: (users) => set({ users }),
  setShowLogin: (show) => set({ showLogin: show }),
  setSidebarView: (view) => set({ sidebarView: view }),
  setTheme: (theme) => set({ theme }),
  toggleTheme: () => set((state) => ({ theme: state.theme === 'dark' ? 'light' : 'dark' })),
  setPrivacyMode: (value) => set({ privacyMode: value }),
  togglePrivacyMode: () => set((state) => ({ privacyMode: !state.privacyMode })),
  setComposerText: (value) => set({ composerText: value }),
  clearMessages: (room) => {
    const target = room ?? get().activeRoom;
    const { messages } = get();
    const next = new Map(messages);
    next.set(target, []);
    set({ messages: next });
  },
}));
