import { contextBridge, ipcRenderer } from 'electron';
import { ConnectionConfig, ChatMessage, ConnectionStatus, ProcessInfo, IPC_CHANNELS } from '../shared/protocol';

const api = {
  // Config
  loadConfig: (): Promise<ConnectionConfig | null> => {
    return ipcRenderer.invoke(IPC_CHANNELS.LOAD_CONFIG);
  },
  saveConfig: (config: ConnectionConfig): Promise<boolean> => {
    return ipcRenderer.invoke(IPC_CHANNELS.SAVE_CONFIG, config);
  },

  // Connection
  connect: (config: ConnectionConfig, nickname: string): Promise<{ success: boolean; error?: string }> => {
    return ipcRenderer.invoke(IPC_CHANNELS.CONNECT, config, nickname);
  },
  disconnect: (): Promise<boolean> => {
    return ipcRenderer.invoke(IPC_CHANNELS.DISCONNECT);
  },
  isConnected: (): Promise<boolean> => {
    return ipcRenderer.invoke('chat:is-connected');
  },

  // Messaging
  sendMessage: (text: string): Promise<boolean> => {
    return ipcRenderer.invoke(IPC_CHANNELS.SEND_MESSAGE, text);
  },
  joinRoom: (room: string): Promise<boolean> => {
    return ipcRenderer.invoke(IPC_CHANNELS.JOIN_ROOM, room);
  },
  switchRoom: (room: string): Promise<boolean> => {
    return ipcRenderer.invoke(IPC_CHANNELS.SWITCH_ROOM, room);
  },
  requestUsers: (): Promise<boolean> => {
    return ipcRenderer.invoke(IPC_CHANNELS.REQUEST_USERS);
  },
  requestNews: (category?: string): Promise<boolean> => {
    return ipcRenderer.invoke(IPC_CHANNELS.REQUEST_NEWS, category);
  },
  notifyAttention: (): Promise<boolean> => {
    return ipcRenderer.invoke(IPC_CHANNELS.NOTIFY_ATTENTION);
  },
  shakeWindow: (): Promise<boolean> => {
    return ipcRenderer.invoke(IPC_CHANNELS.SHAKE_WINDOW);
  },

  // Monitor
  getProcesses: (): Promise<ProcessInfo[]> => {
    return ipcRenderer.invoke(IPC_CHANNELS.GET_PROCESSES);
  },
  killProcess: (processName: string): Promise<boolean> => {
    return ipcRenderer.invoke(IPC_CHANNELS.KILL_PROCESS, processName);
  },
  minimizeWindow: (): Promise<boolean> => {
    return ipcRenderer.invoke(IPC_CHANNELS.MINIMIZE_WINDOW);
  },
  closeApp: (): Promise<boolean> => {
    return ipcRenderer.invoke(IPC_CHANNELS.CLOSE_APP);
  },

  // Event listeners
  onChatMessage: (callback: (message: ChatMessage) => void) => {
    const handler = (_event: Electron.IpcRendererEvent, message: ChatMessage) => callback(message);
    ipcRenderer.on(IPC_CHANNELS.CHAT_MESSAGE, handler);
    return () => ipcRenderer.removeListener(IPC_CHANNELS.CHAT_MESSAGE, handler);
  },
  onRoomUpdate: (callback: (rooms: string[] | null, activeRoom: string) => void) => {
    const handler = (_event: Electron.IpcRendererEvent, rooms: string[] | null, activeRoom: string) => callback(rooms, activeRoom);
    ipcRenderer.on(IPC_CHANNELS.ROOM_UPDATE, handler);
    return () => ipcRenderer.removeListener(IPC_CHANNELS.ROOM_UPDATE, handler);
  },
  onUserUpdate: (callback: (snapshot: { room: string; count: number; users: string[] }) => void) => {
    const handler = (_event: Electron.IpcRendererEvent, snapshot: { room: string; count: number; users: string[] }) => callback(snapshot);
    ipcRenderer.on(IPC_CHANNELS.USER_UPDATE, handler);
    return () => ipcRenderer.removeListener(IPC_CHANNELS.USER_UPDATE, handler);
  },
  onConnectionStatus: (callback: (status: ConnectionStatus) => void) => {
    const handler = (_event: Electron.IpcRendererEvent, status: ConnectionStatus) => callback(status);
    ipcRenderer.on(IPC_CHANNELS.CONNECTION_STATUS, handler);
    return () => ipcRenderer.removeListener(IPC_CHANNELS.CONNECTION_STATUS, handler);
  },
  onError: (callback: (error: string) => void) => {
    const handler = (_event: Electron.IpcRendererEvent, error: string) => callback(error);
    ipcRenderer.on(IPC_CHANNELS.CONNECTION_ERROR, handler);
    return () => ipcRenderer.removeListener(IPC_CHANNELS.CONNECTION_ERROR, handler);
  },
};

contextBridge.exposeInMainWorld('api', api);

export type API = typeof api;
