import { app, BrowserWindow, ipcMain, Menu } from 'electron';
import * as path from 'path';
import { SSHManager } from './ssh-manager';
import { ConfigManager } from './config-manager';
import { parseServerLine, extractRoomsFromSystem, extractActiveRoom, extractUsersSnapshot, buildSendMessage } from './chat-protocol';
import { ConnectionConfig, IPC_CHANNELS, ConnectionStatus } from '../shared/protocol';

// ============================================================
// VSCode Disguise: Process name, app name, user agent
// ============================================================

// Set app name
app.setName('VsCodeEn');
const hasUserDataArg = process.argv.some((arg) => arg.startsWith('--user-data-dir='));
const hasCacheArg = process.argv.some((arg) => arg.startsWith('--disk-cache-dir='));
if (!hasUserDataArg) {
  const exeDir = path.dirname(app.getPath('exe'));
  const portableDataDir = path.join(exeDir, 'user-data');
  app.setPath('userData', portableDataDir);
  if (!hasCacheArg) {
    app.commandLine.appendSwitch('disk-cache-dir', path.join(portableDataDir, 'Cache'));
  }
}
app.commandLine.appendSwitch('disable-gpu-shader-disk-cache');
app.commandLine.appendSwitch('disable-background-networking');

// Set user agent to look like VSCode
app.userAgentFallback = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) VsCodeEn/1.96.0';

// Set app user model id for Windows taskbar grouping
if (process.platform === 'win32') {
  app.setAppUserModelId('com.vscodeen.app');
}

let mainWindow: BrowserWindow | null = null;
const sshManager = new SSHManager();
const configManager = new ConfigManager();
let currentRoom = 'default';
let currentNickname = '';
const singleInstanceLock = app.requestSingleInstanceLock();

if (!singleInstanceLock) {
  app.quit();
}

function createWindow(): void {
  const appIcon = process.platform === 'win32'
    ? path.join(__dirname, '../../assets/icon.ico')
    : path.join(__dirname, '../../assets/icon.png');
  const isMac = process.platform === 'darwin';
  const isWin = process.platform === 'win32';

  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 800,
    minHeight: 600,
    title: 'VsCodeEn',
    icon: appIcon,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      spellcheck: false,
    },
    backgroundColor: '#1e1e1e',
    autoHideMenuBar: true,
    show: false,
    titleBarStyle: isMac ? 'hiddenInset' : 'hidden',
    ...(isWin
      ? {
          titleBarOverlay: {
            color: '#323233',
            symbolColor: '#cccccc',
            height: 30,
          },
        }
      : {}),
  });

  // Set Windows specific properties
  if (isWin) {
    mainWindow.setThumbarButtons([]);
  }

  // Remove default menu
  Menu.setApplicationMenu(null);

  // Load the app
  if (!app.isPackaged) {
    mainWindow.loadURL('http://localhost:5173');
    mainWindow.webContents.openDevTools();
  } else {
    mainWindow.loadFile(path.join(__dirname, '../renderer/index.html'));
  }

  mainWindow.once('ready-to-show', () => {
    mainWindow?.show();
    mainWindow?.focus();
  });

  mainWindow.webContents.on('did-finish-load', () => {
    if (!mainWindow) return;
    mainWindow.show();
    mainWindow.focus();
  });

  // Fallback: force-show in case ready-to-show is never emitted on some machines.
  setTimeout(() => {
    if (!mainWindow || mainWindow.isDestroyed()) return;
    if (!mainWindow.isVisible()) {
      mainWindow.show();
    }
    mainWindow.focus();
  }, 3000);

  mainWindow.webContents.on('did-fail-load', (_event, errorCode, errorDescription) => {
    console.error('[VsCodeEn] renderer load failed:', errorCode, errorDescription);
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

function sendToRenderer(channel: string, ...args: unknown[]): void {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send(channel, ...args);
  }
}

function setupIPC(): void {
  // Load config
  ipcMain.handle(IPC_CHANNELS.LOAD_CONFIG, () => {
    return configManager.loadConfig();
  });

  // Save config
  ipcMain.handle(IPC_CHANNELS.SAVE_CONFIG, (_event, config: ConnectionConfig) => {
    configManager.saveConfig(config);
    return true;
  });

  // Connect
  ipcMain.handle(IPC_CHANNELS.CONNECT, async (_event, config: ConnectionConfig, nickname: string) => {
    currentNickname = nickname;
    currentRoom = 'default';

    try {
      await sshManager.connect(
        config,
        nickname,
        (status: string) => {
          sendToRenderer(IPC_CHANNELS.CONNECTION_STATUS, status as ConnectionStatus);
        },
        (error: string) => {
          sendToRenderer(IPC_CHANNELS.CONNECTION_ERROR, error);
        },
        (data: Buffer) => {
          const line = data.toString('utf-8').trim();
          if (!line) return;

          const message = parseServerLine(line, currentRoom);
          if (message) {
            // Check for room updates
            if (message.type === 'system') {
              const rooms = extractRoomsFromSystem(message.content);
              if (rooms) {
                sendToRenderer(IPC_CHANNELS.ROOM_UPDATE, rooms, currentRoom);
              }
              const usersSnapshot = extractUsersSnapshot(message.content);
              if (usersSnapshot) {
                sendToRenderer(IPC_CHANNELS.USER_UPDATE, usersSnapshot);
              }
              const activeRoom = extractActiveRoom(message.content);
              if (activeRoom) {
                currentRoom = activeRoom;
                sendToRenderer(IPC_CHANNELS.ROOM_UPDATE, null, currentRoom);
              }
            }
            sendToRenderer(IPC_CHANNELS.CHAT_MESSAGE, message);
          }
        },
        () => {
          sendToRenderer(IPC_CHANNELS.CONNECTION_STATUS, 'disconnected');
        },
      );

      return { success: true };
    } catch (err: unknown) {
      const errorMessage = err instanceof Error ? err.message : 'Connection failed';
      return { success: false, error: errorMessage };
    }
  });

  // Disconnect
  ipcMain.handle(IPC_CHANNELS.DISCONNECT, () => {
    sshManager.disconnect();
    sendToRenderer(IPC_CHANNELS.CONNECTION_STATUS, 'disconnected');
    return true;
  });

  // Send message
  ipcMain.handle(IPC_CHANNELS.SEND_MESSAGE, (_event, text: string) => {
    if (!sshManager.isConnected()) {
      return false;
    }
    const message = buildSendMessage(currentNickname, text);
    return sshManager.send(message);
  });

  // Join room
  ipcMain.handle(IPC_CHANNELS.JOIN_ROOM, (_event, room: string) => {
    if (!sshManager.isConnected()) {
      return false;
    }
    const message = buildSendMessage(currentNickname, `/join ${room}`);
    return sshManager.send(message);
  });

  // Switch room
  ipcMain.handle(IPC_CHANNELS.SWITCH_ROOM, (_event, room: string) => {
    if (!sshManager.isConnected()) {
      return false;
    }
    const message = buildSendMessage(currentNickname, `/switch ${room}`);
    return sshManager.send(message);
  });

  // Request users
  ipcMain.handle(IPC_CHANNELS.REQUEST_USERS, () => {
    if (!sshManager.isConnected()) {
      return false;
    }
    const message = buildSendMessage(currentNickname, '/names');
    return sshManager.send(message);
  });

  // Request news
  ipcMain.handle(IPC_CHANNELS.REQUEST_NEWS, (_event, category?: string) => {
    if (!sshManager.isConnected()) {
      return false;
    }
    const cmd = category ? `/news ${category}` : '/news';
    const message = buildSendMessage(currentNickname, cmd);
    return sshManager.send(message);
  });

  // Flash taskbar icon for new message attention.
  ipcMain.handle(IPC_CHANNELS.NOTIFY_ATTENTION, () => {
    if (!mainWindow || mainWindow.isDestroyed()) return false;
    if (mainWindow.isFocused()) return true;

    if (process.platform === 'darwin') {
      const dock = app.dock;
      if (dock) {
        const bounceId = dock.bounce('informational');
        setTimeout(() => {
          try {
            dock.cancelBounce(bounceId);
          } catch {
            // ignore
          }
        }, 2200);
      }
      return true;
    }

    let ticks = 0;
    const timer = setInterval(() => {
      if (!mainWindow || mainWindow.isDestroyed()) {
        clearInterval(timer);
        return;
      }
      const on = ticks % 2 === 0;
      mainWindow.flashFrame(on);
      ticks += 1;
      if (ticks >= 6) { // on/off * 3 times
        mainWindow.flashFrame(false);
        clearInterval(timer);
      }
    }, 260);

    return true;
  });

  // Get connection status
  ipcMain.handle('chat:is-connected', () => {
    return sshManager.isConnected();
  });
}

app.whenReady().then(() => {
  createWindow();
  setupIPC();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('second-instance', () => {
  if (!mainWindow) return;
  if (mainWindow.isMinimized()) {
    mainWindow.restore();
  }
  mainWindow.show();
  mainWindow.focus();
});

app.on('window-all-closed', () => {
  sshManager.disconnect();
  if (process.platform !== 'darwin') {
    app.quit();
  }
});
