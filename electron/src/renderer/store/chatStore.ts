import { create } from 'zustand';
import {
  ChatMessage,
  ChatHistoryIdentity,
  ChatHistorySnapshot,
  ConnectionConfig,
  ConnectionStatus,
  GomokuRapfiAnalyzeRequest,
  GomokuRapfiAnalyzeResponse,
  GoKataGoAnalyzeRequest,
  GoKataGoAnalyzeResponse,
  XiangqiPikafishAnalyzeRequest,
  XiangqiPikafishAnalyzeResponse,
  ProcessInfo,
  RoomInfo,
} from '../../shared/protocol';
import { initLocaleFromStorage, persistLocale, type Locale } from '../i18n';

const DND_STORAGE_KEY = 'sshchat:dnd:v1';

function initDndFromStorage(): boolean {
  try {
    return localStorage.getItem(DND_STORAGE_KEY) === '1';
  } catch {
    return false;
  }
}

function persistDnd(value: boolean): void {
  try {
    localStorage.setItem(DND_STORAGE_KEY, value ? '1' : '0');
  } catch {
    // no-op
  }
}
import {
  applyLibrarySystemMessage,
  emptyLibraryViewState,
  rebuildLibraryViewFromMessages,
  type LibraryViewState,
} from '../lib/libraryMessages';

declare global {
  interface Window {
    api: {
      loadConfig: () => Promise<ConnectionConfig | null>;
      saveConfig: (config: ConnectionConfig) => Promise<boolean>;
      loadChatHistory: (identity: ChatHistoryIdentity) => Promise<ChatHistorySnapshot>;
      saveChatHistory: (identity: ChatHistoryIdentity, snapshot: ChatHistorySnapshot) => Promise<boolean>;
      flushChatHistory: (identity: ChatHistoryIdentity, snapshot: ChatHistorySnapshot) => boolean;
      connect: (config: ConnectionConfig, nickname: string) => Promise<{ success: boolean; error?: string }>;
      disconnect: () => Promise<boolean>;
      isConnected: () => Promise<boolean>;
      sendMessage: (text: string) => Promise<boolean>;
      joinRoom: (room: string) => Promise<boolean>;
      switchRoom: (room: string) => Promise<boolean>;
      requestUsers: () => Promise<boolean>;
      requestNews: (category?: string) => Promise<boolean>;
      notifyAttention: () => Promise<boolean>;
      shakeWindow: () => Promise<boolean>;
      getProcesses: () => Promise<ProcessInfo[]>;
      killProcess: (processName: string) => Promise<boolean>;
      minimizeWindow: () => Promise<boolean>;
      closeApp: () => Promise<boolean>;
      analyzeGomokuRapfi: (payload: GomokuRapfiAnalyzeRequest) => Promise<GomokuRapfiAnalyzeResponse>;
      analyzeGoKataGo: (payload: GoKataGoAnalyzeRequest) => Promise<GoKataGoAnalyzeResponse>;
      warmupGoKataGo: () => Promise<GoKataGoAnalyzeResponse>;
      analyzeXiangqiPikafish: (payload: XiangqiPikafishAnalyzeRequest) => Promise<XiangqiPikafishAnalyzeResponse>;
      openSecureWebSession: (payload: {
        kind: 'canvas' | 'upload' | 'download';
        url: string;
        key: string;
      }) => Promise<{ ok: boolean; error?: string }>;
      uploadSecureFile: (payload: {
        url: string;
        key: string;
        filename: string;
        mime: string;
        data: ArrayBuffer;
      }) => Promise<{ ok: boolean; filename?: string; error?: string }>;
      canvasHttp: (payload: {
        url: string;
        method?: 'GET' | 'POST';
        headers?: Record<string, string>;
        body?: string;
      }) => Promise<{ ok: boolean; status: number; json?: any; error?: string }>;
      onChatMessage: (callback: (message: ChatMessage) => void) => () => void;
      onRoomUpdate: (callback: (rooms: string[] | null, activeRoom: string) => void) => () => void;
      onUserUpdate: (callback: (snapshot: { room: string; count: number; users: string[] }) => void) => () => void;
      onConnectionStatus: (callback: (status: ConnectionStatus) => void) => () => void;
      onError: (callback: (error: string) => void) => () => void;
    };
  }
}

interface ChatState {
  status: ConnectionStatus;
  error: string | null;
  config: ConnectionConfig | null;
  nickname: string;

  rooms: RoomInfo[];
  activeRoom: string;

  messages: Map<string, ChatMessage[]>;
  users: string[];

  showLogin: boolean;
  sidebarView: 'rooms' | 'users' | 'news' | 'library' | 'monitor';
  theme: 'dark' | 'light';
  locale: Locale;
  privacyMode: boolean;
  doNotDisturb: boolean;
  composerText: string;
  libraryView: LibraryViewState;
  canvasSession: { url: string; key: string } | null;
  canvasMaximized: boolean;

  monitorEnabled: boolean;
  monitorPersonCount: number;
  monitorTargetProcesses: string[];
  monitorAction: 'minimize' | 'close' | 'kill';
  monitorCooldown: boolean;

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
  resetLibraryView: () => void;
  rebuildLibraryView: (messages: ChatMessage[]) => void;
  hydrateMessages: (rooms: Record<string, ChatMessage[]>, roomNames?: string[]) => void;
  resetMessages: () => void;
  resetWorkspace: () => void;
  setUsers: (users: string[]) => void;
  setShowLogin: (show: boolean) => void;
  setSidebarView: (view: 'rooms' | 'users' | 'news' | 'library' | 'monitor') => void;
  setTheme: (theme: 'dark' | 'light') => void;
  toggleTheme: () => void;
  setLocale: (locale: Locale) => void;
  toggleLocale: () => void;
  setPrivacyMode: (value: boolean) => void;
  togglePrivacyMode: () => void;
  setDoNotDisturb: (value: boolean) => void;
  toggleDoNotDisturb: () => void;
  setComposerText: (value: string) => void;
  clearMessages: (room?: string) => void;
  openCanvas: (session: { url: string; key: string }) => void;
  closeCanvas: () => void;
  setCanvasMaximized: (value: boolean) => void;

  setMonitorEnabled: (enabled: boolean) => void;
  setMonitorPersonCount: (count: number) => void;
  addMonitorTargetProcess: (name: string) => void;
  removeMonitorTargetProcess: (name: string) => void;
  setMonitorAction: (action: 'minimize' | 'close' | 'kill') => void;
  setMonitorCooldown: (cooldown: boolean) => void;
}

export const useChatStore = create<ChatState>((set, get) => ({
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
  locale: initLocaleFromStorage(),
  privacyMode: true,
  doNotDisturb: initDndFromStorage(),
  composerText: '',
  libraryView: emptyLibraryViewState(),
  canvasSession: null,
  canvasMaximized: false,

  monitorEnabled: false,
  monitorPersonCount: 0,
  monitorTargetProcesses: [],
  monitorAction: 'minimize',
  monitorCooldown: false,

  setStatus: (status) => set({ status }),
  setError: (error) => set({ error }),
  setConfig: (config) => set({ config }),
  setNickname: (nickname) => set({ nickname }),
  setRooms: (rooms) => {
    if (!Array.isArray(rooms)) {
      return;
    }
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
    const hasRoom = rooms.some((r) => r.name === room);
    const nextRooms = hasRoom
      ? rooms
      : [...rooms, { name: room, isDefault: room === 'default', unreadCount: 0, lastActivity: Date.now() }];
    set({
      activeRoom: room,
      rooms: nextRooms.map((r) => (r.name === room ? { ...r, unreadCount: 0, lastActivity: Date.now() } : r)),
    });
  },

  addRoom: (room) => {
    const { rooms } = get();
    if (!rooms.find((r) => r.name === room)) {
      set({ rooms: [...rooms, { name: room, isDefault: room === 'default', unreadCount: 0, lastActivity: Date.now() }] });
    }
  },

  removeRoom: (room) => {
    const { rooms, activeRoom } = get();
    const filtered = rooms.filter((r) => r.name !== room);
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
      rooms: rooms.map((r) => (r.name === room ? { ...r, unreadCount: count } : r)),
    });
  },

  addMessage: (message) => {
    const { messages, activeRoom, rooms, libraryView } = get();
    let nextMessage = message;
    let nextLibraryView = libraryView;
    if (message.type === 'system' && message.sender === '*') {
      const effect = applyLibrarySystemMessage(libraryView, message.content);
      nextLibraryView = effect.state;
      if (effect.hideFromChat) {
        nextMessage = { ...message, hidden: true };
      }
    }

    const roomMessages = messages.get(message.room) || [];
    if (roomMessages.some((item) => item.id === message.id)) {
      return;
    }
    if (
      (message.type === 'chat' || message.type === 'pm')
      && roomMessages.some((item) =>
        item.type === message.type
        && item.sender === message.sender
        && item.content === message.content
        && Math.abs(message.timestamp - item.timestamp) <= 2000,
      )
    ) {
      return;
    }
    const MAX_MESSAGES = 1200;
    const trimmed = roomMessages.length >= MAX_MESSAGES ? roomMessages.slice(-MAX_MESSAGES + 1) : roomMessages;
    const newMessages = new Map(messages);
    newMessages.set(message.room, [...trimmed, nextMessage]);

    let newRooms = rooms.some((r) => r.name === message.room)
      ? rooms
      : [...rooms, { name: message.room, isDefault: message.room === 'default', unreadCount: 0, lastActivity: Date.now() }];
    if (message.room !== activeRoom && message.type !== 'system') {
      newRooms = newRooms.map((r) =>
        r.name === message.room ? { ...r, unreadCount: r.unreadCount + 1, lastActivity: Date.now() } : r,
      );
    } else {
      newRooms = newRooms.map((r) =>
        r.name === message.room ? { ...r, lastActivity: Date.now() } : r,
      );
    }

    set({ messages: newMessages, rooms: newRooms, libraryView: nextLibraryView });
  },

  resetLibraryView: () => set({ libraryView: emptyLibraryViewState() }),

  rebuildLibraryView: (roomMessages) =>
    set({ libraryView: rebuildLibraryViewFromMessages(roomMessages) }),

  hydrateMessages: (historyRooms, roomNames) => {
    const { messages, rooms } = get();
    const mergedMessages = new Map<string, ChatMessage[]>();
    const allRoomNames = new Set([...Object.keys(historyRooms), ...messages.keys()]);
    for (const room of allRoomNames) {
      const history = Array.isArray(historyRooms[room]) ? historyRooms[room] : [];
      const current = messages.get(room) || [];
      const byId = new Map<string, ChatMessage>();
      for (const message of [...history, ...current]) {
        byId.set(message.id, message);
      }
      mergedMessages.set(
        room,
        [...byId.values()]
          .sort((a, b) => a.timestamp - b.timestamp)
          .slice(-1200),
      );
    }

    const savedRoomNames = Array.isArray(roomNames) ? roomNames : [];
    const knownRooms = new Set(rooms.map((room) => room.name));
    const nextRooms = [...rooms];
    for (const room of new Set(['default', ...savedRoomNames, ...Object.keys(historyRooms)])) {
      if (knownRooms.has(room)) continue;
      const roomHistory = historyRooms[room] || [];
      nextRooms.push({
        name: room,
        isDefault: room === 'default',
        unreadCount: 0,
        lastActivity: roomHistory.length > 0
          ? roomHistory[roomHistory.length - 1].timestamp
          : Date.now(),
      });
    }
    set({ messages: mergedMessages, rooms: nextRooms });
  },

  resetMessages: () => set({ messages: new Map() }),
  resetWorkspace: () => set({
    messages: new Map(),
    rooms: [{ name: 'default', isDefault: true, unreadCount: 0, lastActivity: Date.now() }],
    activeRoom: 'default',
    users: [],
    canvasSession: null,
    canvasMaximized: false,
  }),

  setUsers: (users) => set({ users }),
  setShowLogin: (show) => set({ showLogin: show }),
  setSidebarView: (view) => set({ sidebarView: view }),
  setTheme: (theme) => set({ theme }),
  toggleTheme: () => set((state) => ({ theme: state.theme === 'dark' ? 'light' : 'dark' })),
  setLocale: (locale) => {
    persistLocale(locale);
    set({ locale });
    void window.api?.sendMessage?.(`/lang ${locale}`);
  },
  toggleLocale: () =>
    set((state) => {
      const next: Locale = state.locale === 'zh' ? 'en' : 'zh';
      persistLocale(next);
      void window.api?.sendMessage?.(`/lang ${next}`);
      return { locale: next };
    }),
  setPrivacyMode: (value) => set({ privacyMode: value }),
  togglePrivacyMode: () => set((state) => ({ privacyMode: !state.privacyMode })),
  setDoNotDisturb: (value) => {
    persistDnd(value);
    set({ doNotDisturb: value });
  },
  toggleDoNotDisturb: () =>
    set((state) => {
      const next = !state.doNotDisturb;
      persistDnd(next);
      return { doNotDisturb: next };
    }),
  setComposerText: (value) => set({ composerText: value }),
  clearMessages: (room) => {
    const target = room ?? get().activeRoom;
    const { messages } = get();
    const next = new Map(messages);
    next.set(target, []);
    set({ messages: next });
  },
  openCanvas: (session) => set({ canvasSession: session, canvasMaximized: true }),
  closeCanvas: () => set({ canvasSession: null, canvasMaximized: false }),
  setCanvasMaximized: (value) => set({ canvasMaximized: value }),

  setMonitorEnabled: (enabled) => set({ monitorEnabled: enabled }),
  setMonitorPersonCount: (count) => set({ monitorPersonCount: count }),
  addMonitorTargetProcess: (name) => {
    const { monitorTargetProcesses } = get();
    if (!monitorTargetProcesses.includes(name)) {
      set({ monitorTargetProcesses: [...monitorTargetProcesses, name] });
    }
  },
  removeMonitorTargetProcess: (name) => {
    const { monitorTargetProcesses } = get();
    set({ monitorTargetProcesses: monitorTargetProcesses.filter((p) => p !== name) });
  },
  setMonitorAction: (action) => set({ monitorAction: action }),
  setMonitorCooldown: (cooldown) => set({ monitorCooldown: cooldown }),
}));
