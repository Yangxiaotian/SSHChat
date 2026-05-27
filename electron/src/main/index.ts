import { app, BrowserWindow, ipcMain, Menu } from 'electron';
import * as path from 'path';
import * as fs from 'fs';
import { exec, spawn, ChildProcessWithoutNullStreams } from 'child_process';
import { SSHManager } from './ssh-manager';
import { ConfigManager } from './config-manager';
import { parseServerLine, extractRoomsFromSystem, extractActiveRoom, extractUsersSnapshot, buildSendMessage } from './chat-protocol';
import { ConnectionConfig, IPC_CHANNELS, ConnectionStatus, GomokuRapfiAnalyzeRequest, GomokuRapfiAnalyzeResponse } from '../shared/protocol';

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
const RAPFI_ANALYZE_DEFAULT_TIMEOUT_MS = 3200;
const RAPFI_ANALYZE_MAX_TIMEOUT_MS = 12000;

function ensureRapfiLayout(): void {
  const appDir = path.dirname(app.getPath('exe'));
  const srcDir = path.join(appDir, 'Rapfi-engine');
  const dstDir = path.join(appDir, 'engines', 'rapfi');
  const dstExe = path.join(dstDir, 'rapfi.exe');
  const srcExe = path.join(srcDir, 'pbrain-rapfi-windows-avx2.exe');
  try {
    if (fs.existsSync(dstExe)) return;
    if (!fs.existsSync(srcDir)) return;
    fs.mkdirSync(dstDir, { recursive: true });
    fs.cpSync(srcDir, dstDir, { recursive: true, force: true });
    if (!fs.existsSync(dstExe) && fs.existsSync(srcExe)) {
      fs.copyFileSync(srcExe, dstExe);
    }
  } catch {
    // ignore auto-repair errors, resolver will still try normal paths
  }
}

function resolveRapfiExecutable(): string | null {
  ensureRapfiLayout();
  const envPath = process.env.RAPFI_PATH;
  const appDir = path.dirname(app.getPath('exe'));
  const projectDir = path.resolve(__dirname, '../../..');
  const rapfiExeNames = [
    'rapfi.exe',
    'pbrain-rapfi-windows-avx2.exe',
    'pbrain-rapfi-windows-avxvnni.exe',
    'pbrain-rapfi-windows-avx512.exe',
    'pbrain-rapfi-windows-avx512vnni.exe',
    'pbrain-rapfi-windows-sse.exe',
  ];
  const candidates = [
    envPath || '',
    ...rapfiExeNames.map((n) => path.join(appDir, 'engines', 'rapfi', n)),
    ...rapfiExeNames.map((n) => path.join(appDir, 'rapfi', n)),
    ...rapfiExeNames.map((n) => path.join(appDir, 'Rapfi-engine', n)),
    ...rapfiExeNames.map((n) => path.join(projectDir, 'engines', 'rapfi', n)),
    ...rapfiExeNames.map((n) => path.join(projectDir, 'third_party', 'rapfi', n)),
    ...rapfiExeNames.map((n) => path.join(projectDir, 'rapfi', n)),
  ].filter(Boolean);

  for (const p of candidates) {
    try {
      if (fs.existsSync(p) && fs.statSync(p).isFile()) return p;
    } catch {
      // ignore broken path
    }
  }
  return null;
}

function sanitizeTimeout(timeoutMs?: number): number {
  if (!Number.isFinite(timeoutMs)) return RAPFI_ANALYZE_DEFAULT_TIMEOUT_MS;
  return Math.max(400, Math.min(RAPFI_ANALYZE_MAX_TIMEOUT_MS, Math.floor(timeoutMs!)));
}

function parseMoveFromLine(line: string): { row: number; col: number } | null {
  const m = line.trim().match(/(?:bestmove\s+)?(-?\d+)\s*,\s*(-?\d+)/i);
  if (!m) return null;
  const x = Number(m[1]);
  const y = Number(m[2]);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  if (x < 0 || x > 14 || y < 0 || y > 14) return null;
  // protocol uses x,y with 0-based top-left origin; UI uses row,col in 1-based.
  return { row: y + 1, col: x + 1 };
}

function parseAlphaMoveToken(token: string): { row: number; col: number } | null {
  const m = token.trim().match(/^([A-Za-z])\s*(\d{1,2})$/);
  if (!m) return null;
  const file = m[1].toUpperCase();
  const row = Number(m[2]);
  if (!Number.isFinite(row) || row < 1 || row > 15) return null;
  const col = file.charCodeAt(0) - 'A'.charCodeAt(0) + 1;
  if (col < 1 || col > 15) return null;
  return { row, col };
}

function parseFallbackMoveFromLine(line: string): { row: number; col: number } | null {
  const direct = parseMoveFromLine(line);
  if (direct) return direct;
  const parts = line.trim().split(/\s+/);
  for (const p of parts) {
    const alpha = parseAlphaMoveToken(p);
    if (alpha) return alpha;
  }
  return null;
}

function validateBoard15(board: number[][]): boolean {
  if (!Array.isArray(board) || board.length !== 15) return false;
  for (const row of board) {
    if (!Array.isArray(row) || row.length !== 15) return false;
    for (const v of row) {
      if (v !== -1 && v !== 0 && v !== 1) return false;
    }
  }
  return true;
}

function buildRapfiBoardCommands(
  board: number[][],
  mySide: 1 | -1,
  timeoutMs: number,
  inited: boolean,
): string[] {
  const lines: string[] = [];
  lines.push(`INFO timeout_turn ${timeoutMs}`);
  lines.push(inited ? 'RESTART' : 'START 15');
  lines.push('INFO rule 0');
  lines.push('BOARD');
  for (let r = 0; r < 15; r++) {
    for (let c = 0; c < 15; c++) {
      const v = board[r][c];
      if (v === 0) continue;
      const who = v === mySide ? 1 : 2;
      lines.push(`${c},${r},${who}`);
    }
  }
  lines.push('DONE');
  return lines;
}

type RapfiPendingRequest = {
  reqId: number;
  resolve: (resp: GomokuRapfiAnalyzeResponse) => void;
  startedAt: number;
  queuedAt: number;
  timeoutMs: number;
  enginePath: string;
  fallbackMove: { row: number; col: number } | null;
  timer: NodeJS.Timeout;
};

class RapfiEngineService {
  private proc: ChildProcessWithoutNullStreams | null = null;
  private enginePath: string | null = null;
  private stdoutBuf = '';
  private stderrTail: string[] = [];
  private inited = false;
  private pending: RapfiPendingRequest | null = null;
  private queue: Promise<void> = Promise.resolve();
  private seq = 0;
  private queuedCount = 0;
  private traceFile: string | null = null;

  private trace(msg: string): void {
    try {
      if (!this.traceFile) {
        const dir = path.join(app.getPath('userData'), 'logs');
        fs.mkdirSync(dir, { recursive: true });
        this.traceFile = path.join(dir, 'rapfi-trace.log');
      }
      const line = `[${new Date().toISOString()}] ${msg}\n`;
      fs.appendFile(this.traceFile, line, () => {});
    } catch {
      // ignore trace errors
    }
  }

  private keepStderr(line: string): void {
    if (!line.trim()) return;
    this.stderrTail.push(line.trim());
    if (this.stderrTail.length > 24) this.stderrTail = this.stderrTail.slice(-24);
  }

  private completePending(resp: GomokuRapfiAnalyzeResponse): void {
    if (!this.pending) return;
    const p = this.pending;
    this.pending = null;
    clearTimeout(p.timer);
    const qMs = p.startedAt - p.queuedAt;
    this.trace(
      `req#${p.reqId} complete ok=${resp.ok ? 1 : 0} queueMs=${qMs} runMs=${Date.now() - p.startedAt} totalMs=${Date.now() - p.queuedAt} ` +
        `timeoutMs=${p.timeoutMs} err=${resp.error ? JSON.stringify(resp.error) : '""'}`,
    );
    p.resolve(resp);
  }

  private handleStdoutLines(chunk: string): void {
    this.stdoutBuf += chunk;
    const parts = this.stdoutBuf.split(/\r?\n/);
    this.stdoutBuf = parts.pop() || '';

    for (const line of parts) {
      const direct = parseMoveFromLine(line);
      if (direct && this.pending) {
        this.trace(`req#${this.pending.reqId} stdout-move ${direct.row},${direct.col}`);
        this.completePending({
          ok: true,
          row: direct.row,
          col: direct.col,
          ms: Date.now() - this.pending.startedAt,
          enginePath: this.pending.enginePath,
        });
        return;
      }

      const fb = parseFallbackMoveFromLine(line);
      if (fb && this.pending) this.pending.fallbackMove = fb;
    }

    // Some engines may flush bestmove without trailing newline; parse tail defensively.
    if (this.pending && this.stdoutBuf.trim()) {
      const tailDirect = parseMoveFromLine(this.stdoutBuf);
      if (tailDirect) {
        this.trace(`req#${this.pending.reqId} stdout-tail-move ${tailDirect.row},${tailDirect.col}`);
        this.completePending({
          ok: true,
          row: tailDirect.row,
          col: tailDirect.col,
          ms: Date.now() - this.pending.startedAt,
          enginePath: this.pending.enginePath,
        });
        this.stdoutBuf = '';
        return;
      }
      const tailFallback = parseFallbackMoveFromLine(this.stdoutBuf);
      if (tailFallback) this.pending.fallbackMove = tailFallback;
    }
  }

  private disposeProcess(): void {
    if (this.proc && !this.proc.killed) {
      try {
        this.proc.kill();
      } catch {
        // ignore
      }
    }
    this.proc = null;
    this.enginePath = null;
    this.stdoutBuf = '';
    this.stderrTail = [];
    this.inited = false;
  }

  disposeAll(): void {
    this.trace(`service-dispose pending=${this.pending ? 1 : 0} queued=${this.queuedCount}`);
    if (this.pending) {
      this.completePending({
        ok: false,
        ms: Date.now() - this.pending.startedAt,
        enginePath: this.pending.enginePath,
        error: 'Rapfi 服务已关闭',
      });
    }
    this.disposeProcess();
  }

  private ensureProcess(targetPath: string): { ok: true } | { ok: false; error: string } {
    if (this.proc && !this.proc.killed && this.enginePath === targetPath) return { ok: true };

    this.disposeProcess();
    try {
      this.proc = spawn(targetPath, [], { stdio: 'pipe', windowsHide: true });
      this.enginePath = targetPath;
      this.trace(`spawn-ok path=${JSON.stringify(targetPath)}`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'spawn failed';
      this.trace(`spawn-fail path=${JSON.stringify(targetPath)} err=${JSON.stringify(msg)}`);
      return { ok: false, error: `Rapfi 启动失败: ${msg}` };
    }

    this.proc.stdout.setEncoding('utf8');
    this.proc.stdout.on('data', (chunk: string) => this.handleStdoutLines(chunk));
    this.proc.stderr.setEncoding('utf8');
    this.proc.stderr.on('data', (chunk: string) => this.keepStderr(chunk));

    this.proc.on('error', (err) => {
      const msg = `Rapfi 运行错误: ${err.message}`;
      this.trace(`proc-error err=${JSON.stringify(msg)}`);
      if (this.pending) {
        this.completePending({
          ok: !!this.pending.fallbackMove,
          row: this.pending.fallbackMove?.row,
          col: this.pending.fallbackMove?.col,
          ms: Date.now() - this.pending.startedAt,
          enginePath: this.pending.enginePath,
          error: this.pending.fallbackMove ? undefined : msg,
        });
      }
      this.disposeProcess();
    });

    this.proc.on('close', () => {
      this.trace(`proc-close pending=${this.pending ? 1 : 0}`);
      if (this.pending) {
        const errText = this.stderrTail.slice(-4).join(' | ');
        this.completePending({
          ok: !!this.pending.fallbackMove,
          row: this.pending.fallbackMove?.row,
          col: this.pending.fallbackMove?.col,
          ms: Date.now() - this.pending.startedAt,
          enginePath: this.pending.enginePath,
          error: this.pending.fallbackMove ? undefined : (errText || 'Rapfi 进程意外退出'),
        });
      }
      this.disposeProcess();
    });

    return { ok: true };
  }

  private async runAnalyze(payload: GomokuRapfiAnalyzeRequest, reqId: number, queuedAt: number): Promise<GomokuRapfiAnalyzeResponse> {
    const t0 = Date.now();
    const stones = payload.board.reduce((acc, row) => acc + row.reduce((n, v) => n + (v === 0 ? 0 : 1), 0), 0);
    this.trace(`req#${reqId} run-start queuedMs=${t0 - queuedAt} stones=${stones} mySide=${payload.mySide} timeoutIn=${payload.timeoutMs ?? -1}`);
    const enginePath = resolveRapfiExecutable();
    if (!enginePath) {
      this.trace(`req#${reqId} fail no-engine`);
      return {
        ok: false,
        ms: Date.now() - t0,
        error: '未找到 Rapfi 可执行文件。请放置到 engines/rapfi/rapfi.exe 或设置 RAPFI_PATH。',
      };
    }
    if (!validateBoard15(payload.board)) {
      this.trace(`req#${reqId} fail bad-board`);
      return { ok: false, ms: Date.now() - t0, enginePath, error: '棋盘数据非法（要求15x15且元素为-1/0/1）。' };
    }
    if (payload.mySide !== 1 && payload.mySide !== -1) {
      this.trace(`req#${reqId} fail bad-side side=${payload.mySide}`);
      return { ok: false, ms: Date.now() - t0, enginePath, error: '执子参数非法。' };
    }

    const ensured = this.ensureProcess(enginePath);
    if (!ensured.ok) {
      this.trace(`req#${reqId} fail ensure-process err=${JSON.stringify(ensured.error)}`);
      return { ok: false, ms: Date.now() - t0, enginePath, error: ensured.error };
    }

    const timeoutMs = sanitizeTimeout(payload.timeoutMs);
    return new Promise<GomokuRapfiAnalyzeResponse>((resolve) => {
      const startedAt = Date.now();
      const startupExtra = this.inited ? 0 : 1800;
      const req: RapfiPendingRequest = {
        reqId,
        resolve,
        startedAt,
        queuedAt,
        timeoutMs,
        enginePath,
        fallbackMove: null,
        timer: setTimeout(() => {
          if (!this.pending) return;
          if (this.pending.fallbackMove) {
            this.completePending({
              ok: true,
              row: this.pending.fallbackMove.row,
              col: this.pending.fallbackMove.col,
              ms: Date.now() - this.pending.startedAt,
              enginePath: this.pending.enginePath,
            });
          } else {
            const errText = this.stderrTail.slice(-4).join(' | ');
            this.trace(`req#${reqId} timeout errTail=${JSON.stringify(errText)}`);
            this.completePending({
              ok: false,
              ms: Date.now() - this.pending.startedAt,
              enginePath: this.pending.enginePath,
              error: errText ? `Rapfi 超时（${timeoutMs}ms）: ${errText}` : `Rapfi 超时（${timeoutMs}ms）`,
            });
          }
          // timeout means current search might still be alive; restart process for clean next request.
          this.disposeProcess();
        }, timeoutMs + 1400 + startupExtra),
      };
      this.pending = req;

      const cmds = buildRapfiBoardCommands(payload.board, payload.mySide, timeoutMs, this.inited);
      this.inited = true;
      try {
        this.proc!.stdin.write(cmds.join('\n') + '\n');
        this.trace(`req#${reqId} write-ok timeoutMs=${timeoutMs} startupExtra=${startupExtra}`);
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'stdin write failed';
        this.trace(`req#${reqId} write-fail err=${JSON.stringify(msg)}`);
        this.completePending({
          ok: false,
          ms: Date.now() - startedAt,
          enginePath,
          error: `Rapfi 发送命令失败: ${msg}`,
        });
        this.disposeProcess();
      }
    });
  }

  analyze(payload: GomokuRapfiAnalyzeRequest): Promise<GomokuRapfiAnalyzeResponse> {
    const reqId = ++this.seq;
    const queuedAt = Date.now();
    this.queuedCount += 1;
    this.trace(`req#${reqId} enqueue queued=${this.queuedCount}`);

    const task = this.queue.then(() => this.runAnalyze(payload, reqId, queuedAt));
    this.queue = task.then(() => undefined, () => undefined);
    return task
      .then((resp) => {
        this.trace(`req#${reqId} settle ok=${resp.ok ? 1 : 0} totalMs=${Date.now() - queuedAt}`);
        return resp;
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : String(err);
        this.trace(`req#${reqId} settle-exception err=${JSON.stringify(msg)}`);
        throw err;
      })
      .finally(() => {
        this.queuedCount = Math.max(0, this.queuedCount - 1);
        this.trace(`req#${reqId} dequeue queued=${this.queuedCount}`);
      });
  }
}

const rapfiService = new RapfiEngineService();

async function analyzeGomokuByRapfi(payload: GomokuRapfiAnalyzeRequest): Promise<GomokuRapfiAnalyzeResponse> {
  return rapfiService.analyze(payload);
}

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
      backgroundThrottling: false,
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

function shakeMainWindow(): boolean {
  if (!mainWindow || mainWindow.isDestroyed()) return false;
  if (mainWindow.isMinimized()) {
    mainWindow.restore();
  }
  mainWindow.show();
  const base = mainWindow.getBounds();
  const sequence: Array<[number, number]> = [
    [-16, 0],
    [16, 0],
    [-12, 0],
    [12, 0],
    [0, -8],
    [0, 8],
    [0, 0],
  ];
  if (process.platform === 'darwin' && app.dock) {
    try {
      const bounceId = app.dock.bounce('critical');
      setTimeout(() => {
        try {
          app.dock?.cancelBounce(bounceId);
        } catch {
          // ignore
        }
      }, 1600);
    } catch {
      // ignore
    }
  }
  sequence.forEach(([dx, dy], idx) => {
    setTimeout(() => {
      if (!mainWindow || mainWindow.isDestroyed()) return;
      try {
        mainWindow.setBounds({ ...base, x: base.x + dx, y: base.y + dy });
      } catch {
        // ignore transient setBounds issues
      }
      if (idx === sequence.length - 1) {
        try {
          mainWindow.setBounds(base);
          mainWindow.focus();
        } catch {
          // ignore
        }
      }
    }, idx * 60);
  });
  return true;
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

  ipcMain.handle(IPC_CHANNELS.SHAKE_WINDOW, () => {
    return shakeMainWindow();
  });

  // Get connection status
  ipcMain.handle('chat:is-connected', () => {
    return sshManager.isConnected();
  });

  // Monitor: get running processes
  ipcMain.handle(IPC_CHANNELS.GET_PROCESSES, () => {
    return new Promise<{ pid: number; name: string }[]>((resolve) => {
      if (process.platform === 'win32') {
        exec('tasklist /FO CSV /NH', (err, stdout) => {
          if (err) { resolve([]); return; }
          const processes: { pid: number; name: string }[] = [];
          const seen = new Set<string>();
          for (const line of stdout.split('\n')) {
            const match = line.match(/^"([^"]+)","(\d+)"/);
            if (match) {
              const name = match[1];
              const pid = parseInt(match[2], 10);
              if (!seen.has(name)) {
                seen.add(name);
                processes.push({ pid, name });
              }
            }
          }
          processes.sort((a, b) => a.name.localeCompare(b.name));
          resolve(processes);
        });
      } else {
        // macOS/Linux: skip header line manually for portability
        exec('ps -eo pid,comm', (err, stdout) => {
          if (err) { resolve([]); return; }
          const processes: { pid: number; name: string }[] = [];
          const seen = new Set<string>();
          const lines = stdout.split('\n');
          for (let i = 1; i < lines.length; i++) {
            const parts = lines[i].trim().split(/\s+/);
            if (parts.length >= 2) {
              const pid = parseInt(parts[0], 10);
              const name = parts[1];
              if (!isNaN(pid) && name && !seen.has(name)) {
                seen.add(name);
                processes.push({ pid, name });
              }
            }
          }
          processes.sort((a, b) => a.name.localeCompare(b.name));
          resolve(processes);
        });
      }
    });
  });

  // Monitor: kill process by name
  ipcMain.handle(IPC_CHANNELS.KILL_PROCESS, (_event, processName: string) => {
    return new Promise<boolean>((resolve) => {
      // Validate process name: only allow alphanumeric, dots, underscores, hyphens
      if (!/^[a-zA-Z0-9._-]+$/.test(processName)) {
        resolve(false);
        return;
      }
      const cmd = process.platform === 'win32'
        ? `taskkill /F /IM "${processName}"`
        : `pkill -f "${processName}"`;
      exec(cmd, (err) => {
        resolve(!err);
      });
    });
  });

  // Monitor: minimize current window
  ipcMain.handle(IPC_CHANNELS.MINIMIZE_WINDOW, () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.minimize();
      return true;
    }
    return false;
  });

  // Monitor: close/quit app
  ipcMain.handle(IPC_CHANNELS.CLOSE_APP, () => {
    app.quit();
    return true;
  });

  // Gomoku: Rapfi external engine analysis
  ipcMain.handle(IPC_CHANNELS.GOMOKU_RAPFI_ANALYZE, async (_event, payload: GomokuRapfiAnalyzeRequest) => {
    return analyzeGomokuByRapfi(payload);
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
  rapfiService.disposeAll();
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('before-quit', () => {
  rapfiService.disposeAll();
});

