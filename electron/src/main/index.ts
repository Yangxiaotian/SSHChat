import { app, BrowserWindow, ipcMain, Menu } from 'electron';
import * as path from 'path';
import * as fs from 'fs';
import * as http from 'http';
import * as https from 'https';
import * as os from 'os';
import { URL } from 'url';
import { exec, spawn, spawnSync, ChildProcessWithoutNullStreams } from 'child_process';
import { SSHManager } from './ssh-manager';
import { ConfigManager } from './config-manager';
import { ChatHistoryManager } from './chat-history-manager';
import { parseRapfiFallbackMoveLine, parseRapfiMoveLine } from './rapfi-output';
import { parseServerLine, extractRoomsFromSystem, extractActiveRoom, extractUsersSnapshot, buildSendMessage } from './chat-protocol';
import {
  ConnectionConfig,
  IPC_CHANNELS,
  ConnectionStatus,
  GomokuRapfiAnalyzeRequest,
  GomokuRapfiAnalyzeResponse,
  GoKataGoAnalyzeRequest,
  GoKataGoAnalyzeResponse,
  GoKataGoSuggestion,
  XiangqiPikafishAnalyzeRequest,
  XiangqiPikafishAnalyzeResponse,
  ChatHistoryIdentity,
  ChatHistorySnapshot,
} from '../shared/protocol';

type SecureWebKind = 'canvas' | 'upload' | 'download';

function buildSecureKeyAutofillScript(kind: SecureWebKind, key: string): string {
  const safeKey = JSON.stringify(String(key || '').trim().toUpperCase());
  const safeKind = JSON.stringify(kind);
  // Fill the page key field. For canvas/download, also trigger unlock/verify.
  // For upload, only fill the key so the user can still pick a file.
  // Key never appears in the window URL.
  return `(() => {
    const key = ${safeKey};
    const kind = ${safeKind};
    const input = document.getElementById('key');
    if (!input) return { ok: false, error: 'key input missing' };
    input.focus();
    input.value = key;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    if (kind === 'upload') return { ok: true, mode: 'upload-fill-only' };
    const unlock = document.getElementById('unlockBtn');
    if (unlock) { unlock.click(); return { ok: true, mode: 'canvas' }; }
    const submit = document.getElementById('submitBtn');
    if (submit) { submit.click(); return { ok: true, mode: 'download' }; }
    const form = document.getElementById('downloadForm');
    if (form) { form.requestSubmit ? form.requestSubmit() : form.submit(); return { ok: true, mode: 'form' }; }
    return { ok: false, error: 'submit control missing' };
  })()`;
}

async function openSecureWebSession(payload: {
  kind: SecureWebKind;
  url: string;
  key: string;
}): Promise<{ ok: boolean; error?: string }> {
  const url = String(payload?.url || '').trim();
  const key = String(payload?.key || '').trim().toUpperCase();
  const kind = payload?.kind;
  if (!url || !/^https?:\/\//i.test(url)) {
    return { ok: false, error: 'Invalid URL' };
  }
  if (!key || key.length !== 6) {
    return { ok: false, error: 'Invalid key' };
  }
  if (kind !== 'canvas' && kind !== 'upload' && kind !== 'download') {
    return { ok: false, error: 'Invalid kind' };
  }

  const win = new BrowserWindow({
    width: kind === 'canvas' ? 1100 : 900,
    height: kind === 'canvas' ? 820 : 720,
    autoHideMenuBar: true,
    title: kind === 'canvas' ? 'SSHChat Canvas' : kind === 'upload' ? 'SSHChat Upload' : 'SSHChat File',
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
    },
  });

  try {
    await win.loadURL(url);
  } catch (e) {
    try {
      win.close();
    } catch {
      // ignore
    }
    return { ok: false, error: e instanceof Error ? e.message : 'Failed to open page' };
  }

  const tryFill = async () => {
    try {
      if (win.isDestroyed()) return;
      await win.webContents.executeJavaScript(buildSecureKeyAutofillScript(kind, key), true);
    } catch {
      // Page may still be settling; a later did-finish-load/dom-ready retry helps.
    }
  };

  win.webContents.once('dom-ready', () => {
    void tryFill();
  });
  win.webContents.once('did-finish-load', () => {
    void tryFill();
  });
  // One delayed retry for slow Cloudflare/tunnel pages.
  setTimeout(() => {
    void tryFill();
  }, 800);

  return { ok: true };
}

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
const chatHistoryManager = new ChatHistoryManager();
let currentRoom = 'default';
let currentNickname = '';
let knownRooms = new Set<string>(['default']);
let lastConfig: ConnectionConfig | null = null;
let reconnectTimer: NodeJS.Timeout | null = null;
let reconnectAttempts = 0;
const MAX_RECONNECT_ATTEMPTS = 5;
const RECONNECT_BASE_DELAY_MS = 3000;
const singleInstanceLock = app.requestSingleInstanceLock();

function normalizeRoomName(room: string): string | null {
  const normalized = room.trim().replace(/^#/, '');
  return /^[a-zA-Z0-9_-]{1,32}$/.test(normalized) ? normalized : null;
}

function rememberRooms(rooms: string[]): string[] {
  for (const rawRoom of rooms) {
    const room = normalizeRoomName(rawRoom);
    if (room) {
      knownRooms.add(room);
    }
  }
  knownRooms.add('default');
  return [...knownRooms];
}

function pushRoomState(activeRoom = currentRoom): void {
  const normalized = normalizeRoomName(activeRoom) || 'default';
  currentRoom = normalized;
  rememberRooms([normalized]);
  sendToRenderer(IPC_CHANNELS.ROOM_UPDATE, [...knownRooms], currentRoom);
}

function intEnv(name: string, fallback: number, min: number, max: number): number {
  const raw = Number.parseInt(process.env[name] || '', 10);
  if (!Number.isFinite(raw)) return fallback;
  return Math.max(min, Math.min(max, raw));
}

function defaultRapfiHashMb(): number {
  const totalMb = Math.floor(os.totalmem() / 1024 / 1024);
  if (totalMb >= 32768) return 2048;
  if (totalMb >= 16384) return 1024;
  return 512;
}

const RAPFI_ANALYZE_DEFAULT_TIMEOUT_MS = intEnv('RAPFI_DEFAULT_TIMEOUT_MS', 12000, 1000, 90000);
const RAPFI_ANALYZE_MAX_TIMEOUT_MS = intEnv('RAPFI_MAX_TIMEOUT_MS', 75000, 5000, 120000);
const RAPFI_HASH_MB = intEnv('RAPFI_HASH_MB', defaultRapfiHashMb(), 128, 4096);
const RAPFI_PONDER_HASH_MB = intEnv(
  'RAPFI_PONDER_HASH_MB',
  Math.max(512, Math.min(RAPFI_HASH_MB, 2048)),
  128,
  RAPFI_HASH_MB,
);
const RAPFI_THREADS = intEnv('RAPFI_THREADS', 0, 0, 128);
const RAPFI_PONDER_THREADS = intEnv(
  'RAPFI_PONDER_THREADS',
  Math.max(1, Math.min(2, Math.floor(os.cpus().length / 4))),
  1,
  16,
);
const RAPFI_MAX_DEPTH = intEnv('RAPFI_MAX_DEPTH', 99, 8, 200);
const RAPFI_RULE = intEnv('RAPFI_RULE', 4, 0, 6);
let resolvedRapfiExecutableCache: string | null | undefined;
const KATAGO_DEFAULT_TIMEOUT_MS = intEnv('KATAGO_TIMEOUT_MS', 60000, 1500, 180000);
const KATAGO_DEFAULT_MAX_VISITS = intEnv('KATAGO_MAX_VISITS', 96, 8, 2000);
const KATAGO_WARMUP_TIMEOUT_MS = intEnv('KATAGO_WARMUP_TIMEOUT_MS', 120000, 30000, 180000);
let resolvedKataGoPathsCache:
  | { ok: true; exe: string; model: string; config: string }
  | { ok: false; error: string }
  | undefined;
function defaultPikafishThreads(): number {
  const logical = os.cpus().length || 1;
  if (logical <= 4) return Math.max(1, logical - 1);
  if (logical <= 8) return Math.max(2, logical - 2);
  return Math.max(4, Math.min(10, logical - 2));
}

function defaultPikafishHashMb(): number {
  const totalGb = os.totalmem() / 1024 / 1024 / 1024;
  if (totalGb >= 48) return 2048;
  if (totalGb >= 24) return 1024;
  if (totalGb >= 12) return 768;
  return 384;
}

const PIKAFISH_DEFAULT_TIMEOUT_MS = intEnv('PIKAFISH_TIMEOUT_MS', 8000, 1500, 30000);
const PIKAFISH_THREADS = intEnv('PIKAFISH_THREADS', defaultPikafishThreads(), 1, 128);
const PIKAFISH_HASH_MB = intEnv('PIKAFISH_HASH_MB', defaultPikafishHashMb(), 16, 8192);
let resolvedPikafishExecutableCache: string | null | undefined;

function withKataGoConfigValue(text: string, key: string, value: string): string {
  const line = `${key} = ${value}`;
  const pattern = new RegExp(`^\\s*#?\\s*${key}\\s*=.*$`, 'm');
  if (pattern.test(text)) return text.replace(pattern, line);
  return `${text.trimEnd()}\n${line}\n`;
}

function prepareKataGoConfig(baseConfig: string): string {
  try {
    let text = fs.readFileSync(baseConfig, 'utf8');
    text = withKataGoConfigValue(text, 'useEvalCache', 'true');
    text = withKataGoConfigValue(text, 'evalCacheMinVisits', '16');

    const dir = path.join(app.getPath('userData'), 'engines', 'katago');
    fs.mkdirSync(dir, { recursive: true });
    const out = path.join(dir, 'sshchat-analysis.cfg');
    let old = '';
    try {
      old = fs.readFileSync(out, 'utf8');
    } catch {
      old = '';
    }
    if (old !== text) fs.writeFileSync(out, text, 'utf8');
    return out;
  } catch {
    return baseConfig;
  }
}

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

function rapfiExeExists(p: string): boolean {
  try {
    return fs.existsSync(p) && fs.statSync(p).isFile();
  } catch {
    return false;
  }
}

function probeRapfiExecutable(p: string): boolean {
  try {
    const r = spawnSync(p, [], {
      cwd: path.dirname(p),
      input: 'START 15\nEND\n',
      encoding: 'utf8',
      timeout: 2500,
      windowsHide: true,
    });
    const out = `${r.stdout || ''}\n${r.stderr || ''}`;
    return !r.error && out.includes('OK');
  } catch {
    return false;
  }
}

function resolveRapfiExecutable(): string | null {
  if (resolvedRapfiExecutableCache !== undefined) return resolvedRapfiExecutableCache;
  ensureRapfiLayout();
  const envPath = process.env.RAPFI_PATH;
  const appDir = path.dirname(app.getPath('exe'));
  const resourcesDir = process.resourcesPath || path.join(appDir, 'resources');
  const projectDir = path.resolve(__dirname, '../../..');
  const rapfiExeNames = [
    'pbrain-rapfi-windows-avx512vnni.exe',
    'pbrain-rapfi-windows-avx512.exe',
    'pbrain-rapfi-windows-avxvnni.exe',
    'pbrain-rapfi-windows-avx2.exe',
    'pbrain-rapfi-windows-sse.exe',
    'rapfi.exe',
  ];
  const candidates = [
    envPath || '',
    ...rapfiExeNames.map((n) => path.join(resourcesDir, 'engines', 'rapfi', n)),
    ...rapfiExeNames.map((n) => path.join(resourcesDir, 'rapfi', n)),
    ...rapfiExeNames.map((n) => path.join(appDir, 'engines', 'rapfi', n)),
    ...rapfiExeNames.map((n) => path.join(appDir, 'rapfi', n)),
    ...rapfiExeNames.map((n) => path.join(appDir, 'Rapfi-engine', n)),
    ...rapfiExeNames.map((n) => path.join(projectDir, 'engines', 'rapfi', n)),
    ...rapfiExeNames.map((n) => path.join(projectDir, 'third_party', 'rapfi', n)),
    ...rapfiExeNames.map((n) => path.join(projectDir, 'rapfi', n)),
  ].filter(Boolean);

  for (const p of candidates) {
    if (!rapfiExeExists(p)) continue;
    if (envPath && p === envPath) {
      resolvedRapfiExecutableCache = p;
      return p;
    }
    if (probeRapfiExecutable(p)) {
      resolvedRapfiExecutableCache = p;
      return p;
    }
  }
  resolvedRapfiExecutableCache = null;
  return null;
}

function probePikafishExecutable(p: string): boolean {
  try {
    const evalFile = findPikafishEvalFile(p);
    const r = spawnSync(p, [], {
      cwd: path.dirname(p),
      input: `uci\n${evalFile ? `setoption name EvalFile value ${evalFile}\n` : ''}isready\nquit\n`,
      encoding: 'utf8',
      timeout: 6000,
      windowsHide: true,
    });
    const out = `${r.stdout || ''}\n${r.stderr || ''}`.toLowerCase();
    return !r.error && out.includes('uciok') && out.includes('readyok');
  } catch {
    return false;
  }
}

function resolvePikafishExecutable(): string | null {
  if (resolvedPikafishExecutableCache !== undefined) return resolvedPikafishExecutableCache;
  const envPath = process.env.PIKAFISH_PATH;
  const appDir = path.dirname(app.getPath('exe'));
  const resourcesDir = process.resourcesPath || path.join(appDir, 'resources');
  const projectDir = path.resolve(__dirname, '../../..');
  const exeNames = [
    'pikafish-avxvnni.exe',
    'pikafish-avx2.exe',
    'pikafish-bmi2.exe',
    'pikafish-sse41-popcnt.exe',
    'pikafish-vnni512.exe',
    'pikafish-avx512icl.exe',
    'pikafish-avx512.exe',
    'pikafish.exe',
    'Pikafish.exe',
    'pikafish-modern.exe',
    'pikafish-x86-64.exe',
  ];
  const exeRank = new Map(exeNames.map((name, idx) => [name.toLowerCase(), idx]));
  const dirs = [
    path.join(resourcesDir, 'engines', 'pikafish'),
    path.join(resourcesDir, 'engines', 'Pikafish'),
    path.join(resourcesDir, 'pikafish'),
    path.join(resourcesDir, 'Pikafish'),
    path.join(appDir, 'engines', 'pikafish'),
    path.join(appDir, 'engines', 'Pikafish'),
    path.join(appDir, 'Pikafish-engine'),
    path.join(appDir, 'pikafish'),
    path.join(appDir, 'Pikafish'),
    path.join(projectDir, 'electron', 'engines', 'pikafish'),
    path.join(projectDir, 'electron', 'engines', 'Pikafish'),
    path.join(projectDir, 'engines', 'pikafish'),
    path.join(projectDir, 'engines', 'Pikafish'),
    path.join(projectDir, 'third_party', 'pikafish'),
    path.join(projectDir, 'third_party', 'Pikafish'),
    path.join(projectDir, 'pikafish'),
    path.join(projectDir, 'Pikafish'),
  ];
  const candidates = [
    envPath || '',
    ...dirs.flatMap((dir) => exeNames.map((n) => path.join(dir, n))),
  ].filter(Boolean);

  const discovered: string[] = [];
  for (const dir of dirs) {
    try {
      if (!fs.existsSync(dir)) continue;
      const stack = [dir];
      while (stack.length > 0) {
        const cur = stack.pop()!;
        for (const name of fs.readdirSync(cur)) {
          const full = path.join(cur, name);
          const st = fs.statSync(full);
          if (st.isDirectory()) {
            const rel = path.relative(dir, full).split(path.sep);
            if (rel.length <= 2) stack.push(full);
            continue;
          }
          if (/pikafish.*\.exe$/i.test(name)) discovered.push(full);
        }
      }
    } catch {
      // ignore unreadable search dirs
    }
  }

  discovered.sort((a, b) => {
    const ar = exeRank.get(path.basename(a).toLowerCase()) ?? 999;
    const br = exeRank.get(path.basename(b).toLowerCase()) ?? 999;
    if (ar !== br) return ar - br;
    return a.localeCompare(b);
  });
  candidates.push(...discovered);

  for (const p of Array.from(new Set(candidates))) {
    if (!rapfiExeExists(p)) continue;
    if (envPath && p === envPath) {
      resolvedPikafishExecutableCache = p;
      return p;
    }
    if (probePikafishExecutable(p)) {
      resolvedPikafishExecutableCache = p;
      return p;
    }
  }
  resolvedPikafishExecutableCache = null;
  return null;
}

function sanitizePikafishTimeout(timeoutMs?: number): number {
  if (!Number.isFinite(timeoutMs)) return PIKAFISH_DEFAULT_TIMEOUT_MS;
  return Math.max(1500, Math.min(30000, Math.floor(timeoutMs!)));
}

function validateXiangqiBoard10(board: number[][]): boolean {
  if (!Array.isArray(board) || board.length !== 10) return false;
  for (const row of board) {
    if (!Array.isArray(row) || row.length !== 9) return false;
    for (const v of row) {
      if (!Number.isInteger(v) || v < -7 || v > 7) return false;
    }
  }
  return true;
}

function xiangqiPieceToFen(cell: number): string {
  const red = cell > 0;
  const pt = Math.abs(cell);
  const map: Record<number, string> = {
    1: 'k',
    2: 'a',
    3: 'b',
    4: 'n',
    5: 'r',
    6: 'c',
    7: 'p',
  };
  const ch = map[pt] || '';
  return red ? ch.toUpperCase() : ch;
}

function xiangqiBoardToFen(board: number[][], side: 1 | -1): string {
  const rows = board.map((row) => {
    let out = '';
    let empty = 0;
    for (const cell of row) {
      if (cell === 0) {
        empty += 1;
        continue;
      }
      if (empty) {
        out += String(empty);
        empty = 0;
      }
      out += xiangqiPieceToFen(cell);
    }
    if (empty) out += String(empty);
    return out || '9';
  });
  return `${rows.join('/')} ${side === 1 ? 'w' : 'b'} - - 0 1`;
}

function parsePikafishMove(raw: string): XiangqiPikafishAnalyzeResponse['move'] | null {
  const m = raw.trim().match(/^([a-i])([0-9])([a-i])([0-9])$/i);
  if (!m) return null;
  const fc = m[1].toLowerCase().charCodeAt(0) - 96;
  const fr = 10 - Number(m[2]);
  const tc = m[3].toLowerCase().charCodeAt(0) - 96;
  const tr = 10 - Number(m[4]);
  if (fr < 1 || fr > 10 || tr < 1 || tr > 10 || fc < 1 || fc > 9 || tc < 1 || tc > 9) return null;
  return { fr, fc, tr, tc, raw };
}

function findPikafishEvalFile(enginePath: string): string | null {
  const roots = [
    path.dirname(enginePath),
    path.dirname(path.dirname(enginePath)),
  ];
  for (const root of roots) {
    try {
      const p = path.join(root, 'pikafish.nnue');
      if (rapfiExeExists(p)) return p;
    } catch {
      // ignore
    }
  }
  return null;
}

function appendPikafishLog(line: string): void {
  try {
    const dir = path.join(app.getPath('userData'), 'logs');
    fs.mkdirSync(dir, { recursive: true });
    const file = path.join(dir, 'pikafish.log');
    if (fs.existsSync(file) && fs.statSync(file).size > 1024 * 1024) {
      fs.renameSync(file, path.join(dir, 'pikafish.old.log'));
    }
    fs.appendFileSync(file, `${new Date().toISOString()} ${line}\n`, 'utf8');
  } catch {
    // Logging must never break the engine call.
  }
}

function analyzeXiangqiByPikafish(payload: XiangqiPikafishAnalyzeRequest): Promise<XiangqiPikafishAnalyzeResponse> {
  const startedAt = Date.now();
  const enginePath = resolvePikafishExecutable();
  if (!enginePath) {
    appendPikafishLog('resolve=missing');
    return Promise.resolve({
      ok: false,
      ms: Date.now() - startedAt,
      error: '未找到 Pikafish 可执行文件。请放到 engines/pikafish/pikafish.exe，或设置 PIKAFISH_PATH。',
    });
  }
  if (!validateXiangqiBoard10(payload.board)) {
    return Promise.resolve({ ok: false, ms: Date.now() - startedAt, enginePath, error: '象棋棋盘数据非法（要求10x9）。' });
  }
  if (payload.side !== 1 && payload.side !== -1) {
    return Promise.resolve({ ok: false, ms: Date.now() - startedAt, enginePath, error: '执子参数非法。' });
  }

  const timeoutMs = sanitizePikafishTimeout(payload.timeoutMs);
  const fen = xiangqiBoardToFen(payload.board, payload.side);
  appendPikafishLog(`start exe="${enginePath}" side=${payload.side} timeoutMs=${timeoutMs} threads=${PIKAFISH_THREADS} hashMb=${PIKAFISH_HASH_MB} fen="${fen}"`);

  return new Promise((resolve) => {
    let proc: ChildProcessWithoutNullStreams | null = null;
    let stdoutBuf = '';
    let stderrTail = '';
    let done = false;
    let phase: 'boot' | 'uci' | 'ready' | 'newgame' | 'search' = 'boot';
    let lastInfo = '';
    let searchStartedAt = 0;
    let stopSent = false;
    let stopTimer: NodeJS.Timeout | undefined;

    const writeLines = (lines: string[]) => {
      if (!proc || proc.killed) return;
      proc.stdin.write(`${lines.join('\n')}\n`);
    };

    const finish = (resp: XiangqiPikafishAnalyzeResponse) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      if (stopTimer) clearTimeout(stopTimer);
      try {
        if (proc && !proc.killed) proc.kill();
      } catch {
        // ignore
      }
      appendPikafishLog(`finish ok=${resp.ok ? 1 : 0} phase=${phase} ms=${resp.ms} move=${resp.move?.raw || ''} error="${resp.error || ''}" lastInfo="${lastInfo}"`);
      resolve(resp);
    };

    const timer = setTimeout(() => {
      finish({
        ok: false,
        ms: Date.now() - startedAt,
        enginePath,
        error: stderrTail
          ? `Pikafish 超时（阶段：${phase}）：${stderrTail}`
          : `Pikafish 超时（阶段：${phase}，${timeoutMs}ms；已请求停止：${stopSent ? '是' : '否'}；最近信息：${lastInfo || '无'}）`,
      });
    }, timeoutMs + 12000);

    try {
      proc = spawn(enginePath, [], { cwd: path.dirname(enginePath), stdio: 'pipe', windowsHide: true });
    } catch (err) {
      finish({
        ok: false,
        ms: Date.now() - startedAt,
        enginePath,
        error: `Pikafish 启动失败：${err instanceof Error ? err.message : 'spawn failed'}`,
      });
      return;
    }

    proc.stdout.setEncoding('utf8');
    proc.stderr.setEncoding('utf8');
    proc.stderr.on('data', (chunk: string) => {
      stderrTail = `${stderrTail}\n${chunk}`.split(/\r?\n/).slice(-6).join(' | ').trim();
    });
    proc.stdout.on('data', (chunk: string) => {
      stdoutBuf += chunk;
      const lines = stdoutBuf.split(/\r?\n/);
      stdoutBuf = lines.pop() || '';
      for (const line of lines) {
        const text = line.trim();
        if (!text) continue;
        if (/^(info|string|id|option)\b/i.test(text)) {
          lastInfo = text.slice(0, 260);
        }
        if (/^uciok\b/i.test(text) && phase === 'uci') {
          phase = 'ready';
          const evalFile = findPikafishEvalFile(enginePath);
          writeLines([
            ...(evalFile ? [`setoption name EvalFile value ${evalFile}`] : []),
            `setoption name Threads value ${PIKAFISH_THREADS}`,
            `setoption name Hash value ${PIKAFISH_HASH_MB}`,
            'setoption name MultiPV value 1',
            'setoption name Move Overhead value 30',
            'setoption name UCI_ShowWDL value true',
            'setoption name NumaPolicy value auto',
            'isready',
          ]);
          continue;
        }
        if (/^readyok\b/i.test(text) && phase === 'ready') {
          phase = 'newgame';
          writeLines([
            'ucinewgame',
            'isready',
          ]);
          continue;
        }
        if (/^readyok\b/i.test(text) && phase === 'newgame') {
          phase = 'search';
          searchStartedAt = Date.now();
          stopTimer = setTimeout(() => {
            if (done || phase !== 'search' || !proc || proc.killed) return;
            stopSent = true;
            appendPikafishLog(`stop phase=search elapsed=${Date.now() - searchStartedAt} timeoutMs=${timeoutMs} lastInfo="${lastInfo}"`);
            try {
              proc.stdin.write('stop\n');
            } catch {
              // The normal guard will report a timeout if stdin is already closed.
            }
          }, timeoutMs + 1200);
          writeLines([
            `position fen ${fen}`,
            `go movetime ${timeoutMs}`,
          ]);
          continue;
        }
        const best = text.match(/^bestmove\s+(\S+)/i);
        if (!best) continue;
        const move = parsePikafishMove(best[1]);
        finish({
          ok: !!move,
          ms: Date.now() - startedAt,
          enginePath,
          move: move || undefined,
          error: move ? undefined : `Pikafish 返回了无法识别的着法：${best[1]}（最近信息：${lastInfo || '无'}）`,
        });
        return;
      }
    });
    proc.on('error', (err) => {
      finish({ ok: false, ms: Date.now() - startedAt, enginePath, error: `Pikafish 运行错误：${err.message}` });
    });
    proc.on('close', () => {
      finish({
        ok: false,
        ms: Date.now() - startedAt,
        enginePath,
        error: stderrTail || `Pikafish 进程已退出但没有返回 bestmove（阶段：${phase}，搜索耗时：${searchStartedAt ? Date.now() - searchStartedAt : 0}ms，最近信息：${lastInfo || '无'}）。`,
      });
    });

    try {
      phase = 'uci';
      writeLines(['uci']);
    } catch (err) {
      finish({
        ok: false,
        ms: Date.now() - startedAt,
        enginePath,
        error: `Pikafish 发送命令失败：${err instanceof Error ? err.message : 'stdin write failed'}`,
      });
    }
  });
}

function sanitizeTimeout(timeoutMs?: number): number {
  if (!Number.isFinite(timeoutMs)) return RAPFI_ANALYZE_DEFAULT_TIMEOUT_MS;
  return Math.max(400, Math.min(RAPFI_ANALYZE_MAX_TIMEOUT_MS, Math.floor(timeoutMs!)));
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

function cloneBoard15(board: number[][]): number[][] {
  return board.map((row) => row.slice());
}

function boardAfterRapfiMove(board: number[][], mySide: 1 | -1, move?: { row?: number; col?: number }): number[][] | null {
  const row = move?.row;
  const col = move?.col;
  if (!row || !col || row < 1 || row > 15 || col < 1 || col > 15) return null;
  const r = row - 1;
  const c = col - 1;
  if (board[r]?.[c] !== 0) return null;
  const next = cloneBoard15(board);
  next[r][c] = mySide;
  return next;
}

function diffBoard15(prev: number[][], next: number[][]): Array<{ r: number; c: number; from: number; to: number }> {
  const out: Array<{ r: number; c: number; from: number; to: number }> = [];
  for (let r = 0; r < 15; r++) {
    for (let c = 0; c < 15; c++) {
      if (prev[r][c] !== next[r][c]) {
        out.push({ r, c, from: prev[r][c], to: next[r][c] });
      }
    }
  }
  return out;
}

function isMyTurnByBoard(board: number[][], mySide: 1 | -1): boolean {
  let black = 0;
  let white = 0;
  for (let r = 0; r < 15; r++) {
    for (let c = 0; c < 15; c++) {
      if (board[r][c] === 1) black += 1;
      else if (board[r][c] === -1) white += 1;
    }
  }
  if (mySide === 1) return black === white;
  return black === white + 1;
}

function hasFiveOnBoard(board: number[][], side: 1 | -1): boolean {
  const dirs = [[1, 0], [0, 1], [1, 1], [1, -1]] as const;
  for (let r = 0; r < 15; r++) {
    for (let c = 0; c < 15; c++) {
      if (board[r][c] !== side) continue;
      for (const [dr, dc] of dirs) {
        const prevR = r - dr;
        const prevC = c - dc;
        if (prevR >= 0 && prevR < 15 && prevC >= 0 && prevC < 15 && board[prevR][prevC] === side) continue;
        let len = 0;
        let rr = r;
        let cc = c;
        while (rr >= 0 && rr < 15 && cc >= 0 && cc < 15 && board[rr][cc] === side) {
          len += 1;
          rr += dr;
          cc += dc;
        }
        if (side === 1 ? len === 5 : len >= 5) return true;
      }
    }
  }
  return false;
}

function gomokuWinnerOnBoard(board: number[][]): 1 | -1 | null {
  if (hasFiveOnBoard(board, 1)) return 1;
  if (hasFiveOnBoard(board, -1)) return -1;
  return null;
}

function immediateWinningPoints(board: number[][], side: 1 | -1): Array<{ r: number; c: number }> {
  const points: Array<{ r: number; c: number }> = [];
  for (let r = 0; r < 15; r++) {
    for (let c = 0; c < 15; c++) {
      if (board[r][c] !== 0) continue;
      board[r][c] = side;
      if (hasFiveOnBoard(board, side)) {
        points.push({ r, c });
      }
      board[r][c] = 0;
    }
  }
  return points;
}

function hasStrongLineThreat(board: number[][], side: 1 | -1): boolean {
  const dirs = [[1, 0], [0, 1], [1, 1], [1, -1]] as const;
  for (let r = 0; r < 15; r++) {
    for (let c = 0; c < 15; c++) {
      if (board[r][c] !== side) continue;
      for (const [dr, dc] of dirs) {
        const prevR = r - dr;
        const prevC = c - dc;
        if (prevR >= 0 && prevR < 15 && prevC >= 0 && prevC < 15 && board[prevR][prevC] === side) {
          continue;
        }
        let len = 0;
        let rr = r;
        let cc = c;
        while (rr >= 0 && rr < 15 && cc >= 0 && cc < 15 && board[rr][cc] === side) {
          len += 1;
          rr += dr;
          cc += dc;
        }
        const leftR = r - dr;
        const leftC = c - dc;
        const rightR = rr;
        const rightC = cc;
        const openEnds =
          (leftR >= 0 && leftR < 15 && leftC >= 0 && leftC < 15 && board[leftR][leftC] === 0 ? 1 : 0) +
          (rightR >= 0 && rightR < 15 && rightC >= 0 && rightC < 15 && board[rightR][rightC] === 0 ? 1 : 0);
        if (len >= 4 && openEnds >= 1) return true;
        if (len === 3 && openEnds === 2) return true;
      }
    }
  }
  return false;
}

function tacticalEmergencyReason(board: number[][], mySide: 1 | -1): string | null {
  const opp = mySide === 1 ? -1 : 1;
  if (immediateWinningPoints(board, mySide).length > 0) return 'my-one-move-win';
  if (immediateWinningPoints(board, opp).length > 0) return 'opp-one-move-win';
  if (hasStrongLineThreat(board, mySide)) return 'my-strong-line-threat';
  if (hasStrongLineThreat(board, opp)) return 'opp-strong-line-threat';
  return null;
}

function buildRapfiInitLines(timeoutMs: number): string[] {
  const lines: string[] = [];
  lines.push(`INFO timeout_turn ${timeoutMs}`);
  lines.push(`INFO max_depth ${RAPFI_MAX_DEPTH}`);
  lines.push(`INFO rule ${RAPFI_RULE}`);
  return lines;
}

function buildRapfiBoardCommands(
  board: number[][],
  mySide: 1 | -1,
  timeoutMs: number,
  inited: boolean,
  hashMb: number,
  forceRestart = false,
  threadCount = RAPFI_THREADS,
): string[] {
  const lines: string[] = [];
  if (!inited) {
    lines.push('START 15');
    lines.push(`INFO hash_size ${hashMb * 1024}`);
    if (threadCount > 0) {
      lines.push(`INFO thread_num ${threadCount}`);
    }
  } else if (forceRestart) {
    lines.push('RESTART');
  }
  lines.push(...buildRapfiInitLines(timeoutMs));
  lines.push('BOARD');
  for (let r = 0; r < 15; r++) {
    for (let c = 0; c < 15; c++) {
      const v = board[r][c];
      if (v === 0) continue;
      // Piskvork/Rapfi BOARD uses absolute stone colors:
      // 1 = black(first), 2 = white(second). Do not encode own/opponent here,
      // otherwise Renju forbidden-move rules are evaluated from the wrong side.
      const who = v === 1 ? 1 : 2;
      lines.push(`${c},${r},${who}`);
    }
  }
  lines.push('DONE');
  return lines;
}

function buildRapfiTurnCommands(row0: number, col0: number, timeoutMs: number): string[] {
  const lines: string[] = [];
  lines.push(...buildRapfiInitLines(timeoutMs));
  lines.push(`TURN ${col0},${row0}`);
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
  private lastEngineBoard: number[][] | null = null;
  private lastMySide: 1 | -1 | null = null;
  private lastFullBoardStones = -1;
  private pending: RapfiPendingRequest | null = null;
  private queue: Promise<void> = Promise.resolve();
  private seq = 0;
  private queuedCount = 0;
  private traceFile: string | null = null;

  constructor(
    private readonly label: string,
    private readonly hashMb: number,
    private readonly allowIncremental: boolean,
    private readonly rememberEngineBoard: boolean,
    private readonly threadCount = RAPFI_THREADS,
  ) {}

  private trace(msg: string): void {
    try {
      if (!this.traceFile) {
        const dir = path.join(app.getPath('userData'), 'logs');
        fs.mkdirSync(dir, { recursive: true });
        this.traceFile = path.join(dir, `rapfi-${this.label}-trace.log`);
      }
      const line = `[${new Date().toISOString()}] [${this.label}] ${msg}\n`;
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
      const direct = parseRapfiMoveLine(line);
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
      if (this.pending && /\b-?\d+\s*,\s*-?\d+\b/.test(line)) {
        this.trace(`req#${this.pending.reqId} ignored-coordinate-line ${JSON.stringify(line.trim().slice(0, 180))}`);
      }

      const fb = parseRapfiFallbackMoveLine(line);
      if (fb && this.pending) this.pending.fallbackMove = fb;
    }

    // Some engines may flush bestmove without trailing newline; parse tail defensively.
    if (this.pending && this.stdoutBuf.trim()) {
      const tailDirect = parseRapfiMoveLine(this.stdoutBuf);
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
      const tailFallback = parseRapfiFallbackMoveLine(this.stdoutBuf);
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
    this.lastEngineBoard = null;
    this.lastMySide = null;
    this.lastFullBoardStones = -1;
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
      this.proc = spawn(targetPath, [], { cwd: path.dirname(targetPath), stdio: 'pipe', windowsHide: true });
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
    const requestMode = payload.mode === 'ponder' ? 'ponder' : 'move';
    this.trace(
      `req#${reqId} run-start mode=${requestMode} queuedMs=${t0 - queuedAt} stones=${stones} ` +
        `mySide=${payload.mySide} timeoutIn=${payload.timeoutMs ?? -1} hashMb=${this.hashMb}`,
    );
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
    const winner = gomokuWinnerOnBoard(payload.board);
    if (winner !== null) {
      this.trace(`req#${reqId} fail terminal-board winner=${winner}`);
      return {
        ok: false,
        ms: Date.now() - t0,
        enginePath,
        error: `${winner === 1 ? '黑方' : '白方'}已连五，当前局面应已结束，不再请求 Rapfi。`,
      };
    }

    const ensured = this.ensureProcess(enginePath);
    if (!ensured.ok) {
      this.trace(`req#${reqId} fail ensure-process err=${JSON.stringify(ensured.error)}`);
      return { ok: false, ms: Date.now() - t0, enginePath, error: ensured.error };
    }

    const timeoutMs = sanitizeTimeout(payload.timeoutMs);
    let cmdMode = 'full-board';
    let cmdReason = 'startup';
    let cmds: string[] = [];

    const tacticalReason = tacticalEmergencyReason(payload.board, payload.mySide);
    // Opening is where plan quality matters most. Rebuild the full board in the
    // first few plies instead of trusting incremental state from prior turns.
    const needsOpeningFullBoard = stones <= 12;
    const needsPeriodicResync = this.lastFullBoardStones < 0 || stones - this.lastFullBoardStones >= 8;
    const canTryIncremental =
      this.allowIncremental &&
      requestMode !== 'ponder' &&
      !tacticalReason &&
      !needsOpeningFullBoard &&
      !needsPeriodicResync &&
      this.inited &&
      this.lastEngineBoard !== null &&
      this.lastMySide === payload.mySide &&
      isMyTurnByBoard(payload.board, payload.mySide);

    if (canTryIncremental) {
      const diffs = diffBoard15(this.lastEngineBoard!, payload.board);
      if (diffs.length === 1) {
        const d = diffs[0];
        // 增量前提：真实棋盘包含了上次引擎建议手，只额外新增了对手一手。
        if (d.from === 0 && d.to !== 0 && d.to === -payload.mySide) {
          cmds = buildRapfiTurnCommands(d.r, d.c, timeoutMs);
          cmdMode = 'incremental-turn';
          cmdReason = `delta=1 opp@${d.r},${d.c}`;
        }
      }
      if (cmds.length === 0) {
        cmdReason = `delta-fallback`;
      }
    }

    if (cmds.length === 0) {
      // 回退整盘重建：处理开局、悔棋、多步差分、跨端续玩、执子变化等情况。
      const forceRestart = this.inited;
      cmds = buildRapfiBoardCommands(
        payload.board,
        payload.mySide,
        timeoutMs,
        this.inited,
        this.hashMb,
        forceRestart,
        this.threadCount,
      );
      cmdMode = 'full-board';
      if (!this.inited) cmdReason = 'cold-start';
      else if (tacticalReason) cmdReason = `tactical-full-board:${tacticalReason}`;
      else if (needsOpeningFullBoard) cmdReason = 'opening-full-board';
      else if (needsPeriodicResync) cmdReason = 'periodic-resync';
      else if (requestMode === 'ponder') cmdReason = 'ponder-isolated-board';
      else if (this.lastEngineBoard === null) cmdReason = 'no-engine-board';
      else if (this.lastMySide !== payload.mySide) cmdReason = 'side-switched';
      else if (!isMyTurnByBoard(payload.board, payload.mySide)) cmdReason = 'not-my-turn-or-unknown';
      else cmdReason = 'non-incremental-delta';
    }

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
          const cur = this.pending;
          if (cur.fallbackMove) {
            this.completePending({
              ok: true,
              row: cur.fallbackMove.row,
              col: cur.fallbackMove.col,
              ms: Date.now() - cur.startedAt,
              enginePath: cur.enginePath,
            });
          } else {
            const errText = this.stderrTail.slice(-4).join(' | ');
            this.trace(`req#${reqId} timeout errTail=${JSON.stringify(errText)}`);
            this.completePending({
              ok: false,
              ms: Date.now() - cur.startedAt,
              enginePath: cur.enginePath,
              error: errText ? `Rapfi 超时（${timeoutMs}ms）: ${errText}` : `Rapfi 超时（${timeoutMs}ms）`,
            });
          }
          // 软超时不杀进程，保留 Hash 缓存
          // 设置硬超时：如果引擎长时间无响应则杀掉进程
          const savedSeq = this.seq;
          setTimeout(() => {
            // 仅当没有新请求启动时才杀进程（避免影响后续请求）
            if (this.proc && !this.proc.killed && this.seq === savedSeq) {
              this.trace(`req#${reqId} hard timeout, disposing process`);
              this.disposeProcess();
            }
          }, timeoutMs + 3000);
        }, timeoutMs + 1400 + startupExtra),
      };
      this.pending = req;
      this.inited = true;
      try {
        this.proc!.stdin.write(cmds.join('\n') + '\n');
        this.trace(
          `req#${reqId} write-ok timeoutMs=${timeoutMs} startupExtra=${startupExtra} mode=${cmdMode} reason=${cmdReason}`,
        );
        if (cmdMode === 'full-board') {
          this.lastFullBoardStones = stones;
        }
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
        if (resp.ok && this.rememberEngineBoard && payload.mode !== 'ponder' && isMyTurnByBoard(payload.board, payload.mySide)) {
          const engineBoard = boardAfterRapfiMove(payload.board, payload.mySide, resp);
          this.lastEngineBoard = engineBoard;
          this.lastMySide = engineBoard ? payload.mySide : null;
        } else if (!resp.ok && this.rememberEngineBoard && payload.mode !== 'ponder') {
          // 失败后下次强制整盘重建，避免增量状态漂移。
          this.lastEngineBoard = null;
          this.lastMySide = null;
        }
        this.trace(`req#${reqId} settle ok=${resp.ok ? 1 : 0} totalMs=${Date.now() - queuedAt}`);
        return resp;
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : String(err);
        this.lastEngineBoard = null;
        this.lastMySide = null;
        this.trace(`req#${reqId} settle-exception err=${JSON.stringify(msg)}`);
        throw err;
      })
      .finally(() => {
        this.queuedCount = Math.max(0, this.queuedCount - 1);
        this.trace(`req#${reqId} dequeue queued=${this.queuedCount}`);
      });
  }
}

// Formal user-facing suggestions must favor correctness over speed. Incremental
// TURN can be fast, but when engine state drifts it may return a shallow move.
// Keep formal analysis on full BOARD rebuilds; ponder can stay isolated.
const rapfiService = new RapfiEngineService('move', RAPFI_HASH_MB, false, false, RAPFI_THREADS);
const rapfiPonderService = new RapfiEngineService('ponder', RAPFI_PONDER_HASH_MB, false, false, RAPFI_PONDER_THREADS);

async function analyzeGomokuByRapfi(payload: GomokuRapfiAnalyzeRequest): Promise<GomokuRapfiAnalyzeResponse> {
  return payload.mode === 'ponder' ? rapfiPonderService.analyze(payload) : rapfiService.analyze(payload);
}

const GO_COLUMNS = 'ABCDEFGHJKLMNOPQRST';

function resolveKataGoPaths():
  | { ok: true; exe: string; model: string; config: string }
  | { ok: false; error: string } {
  if (resolvedKataGoPathsCache) return resolvedKataGoPathsCache;
  const appDir = path.dirname(app.getPath('exe'));
  const resourcesDir = process.resourcesPath || path.join(appDir, 'resources');
  const projectDir = path.resolve(__dirname, '../../..');
  const baseDirs = [
    path.join(resourcesDir, 'engines', 'katago'),
    path.join(resourcesDir, 'katago'),
    path.join(appDir, 'engines', 'katago'),
    path.join(appDir, 'KataGo'),
    path.join(projectDir, 'electron', 'engines', 'katago'),
    path.join(projectDir, 'engines', 'katago'),
    path.join(projectDir, 'katago'),
    path.join(projectDir, 'third_party', 'katago'),
  ];
  const exeNames = ['katago.exe', 'katago-opencl.exe', 'katago-cpu.exe', 'katago-cuda.exe', 'katago'];
  const envExe = process.env.KATAGO_PATH || '';
  const nestedBaseDirs = baseDirs.flatMap((dir) => {
    try {
      return fs.readdirSync(dir, { withFileTypes: true })
        .filter((d) => d.isDirectory() && !d.name.startsWith('_'))
        .map((d) => path.join(dir, d.name));
    } catch {
      return [];
    }
  });
  const exeCandidates = [
    envExe,
    ...baseDirs.flatMap((dir) => exeNames.map((n) => path.join(dir, n))),
    ...nestedBaseDirs.flatMap((dir) => exeNames.map((n) => path.join(dir, n))),
  ].filter(Boolean);
  const exe = exeCandidates.find(rapfiExeExists);
  if (!exe) {
    resolvedKataGoPathsCache = {
      ok: false,
      error: '未找到 KataGo 可执行文件。请放到 engines/katago/katago.exe，或设置 KATAGO_PATH。',
    };
    return resolvedKataGoPathsCache;
  }

  const exeDir = path.dirname(exe);
  const searchDirs = Array.from(new Set([
    exeDir,
    path.dirname(exeDir),
    ...baseDirs,
    ...baseDirs.map((dir) => path.join(dir, 'katago-v1.16.5-opencl-windows-x64')),
  ]));
  const envModel = process.env.KATAGO_MODEL || '';
  const model = envModel && rapfiExeExists(envModel)
    ? envModel
    : searchDirs.flatMap((dir) => {
        try {
          return fs.readdirSync(dir).map((n) => path.join(dir, n));
        } catch {
          return [];
        }
      })
      .filter((p) => !p.toLowerCase().includes(`${path.sep}_download${path.sep}`))
      .sort((a, b) => {
        try {
          return fs.statSync(b).size - fs.statSync(a).size;
        } catch {
          return 0;
        }
      })
      .find((p) => /\.(bin|txt|onnx)(\.gz)?$/i.test(p) && rapfiExeExists(p));
  if (!model) {
    resolvedKataGoPathsCache = {
      ok: false,
      error: '未找到 KataGo 模型文件。请放到 engines/katago/，或设置 KATAGO_MODEL。',
    };
    return resolvedKataGoPathsCache;
  }

  const configNames = [
    'analysis_example.cfg',
    'analysis.cfg',
    'gtp.cfg',
    'default_gtp.cfg',
    'gtp_example.cfg',
    path.join('configs', 'analysis.cfg'),
    path.join('configs', 'gtp.cfg'),
    path.join('configs', 'gtp_example.cfg'),
  ];
  const envConfig = process.env.KATAGO_CONFIG || '';
  const config = envConfig && rapfiExeExists(envConfig)
    ? envConfig
    : searchDirs.flatMap((dir) => configNames.map((n) => path.join(dir, n))).find(rapfiExeExists);
  if (!config) {
    resolvedKataGoPathsCache = {
      ok: false,
      error: '未找到 KataGo 配置文件 analysis.cfg/gtp.cfg。请放到 engines/katago/，或设置 KATAGO_CONFIG。',
    };
    return resolvedKataGoPathsCache;
  }

  resolvedKataGoPathsCache = { ok: true, exe, model, config: prepareKataGoConfig(config) };
  return resolvedKataGoPathsCache;
}

function validateGoBoard19(board: number[][]): boolean {
  if (!Array.isArray(board) || board.length !== 19) return false;
  for (const row of board) {
    if (!Array.isArray(row) || row.length !== 19) return false;
    for (const v of row) {
      if (v !== 0 && v !== 1 && v !== 2) return false;
    }
  }
  return true;
}

function goUiToGtp(row: number, col: number): string {
  return `${GO_COLUMNS[col - 1]}${20 - row}`;
}

function goGtpToUi(loc: string): { row: number; col: number } | null {
  const t = String(loc || '').trim().toUpperCase();
  if (!t || t === 'PASS') return null;
  const m = t.match(/^([A-HJ-T])(\d{1,2})$/);
  if (!m) return null;
  const col = GO_COLUMNS.indexOf(m[1]) + 1;
  const rank = Number(m[2]);
  const row = 20 - rank;
  if (col < 1 || col > 19 || row < 1 || row > 19) return null;
  return { row, col };
}

function sanitizeKataGoMoves(moves?: GoKataGoAnalyzeRequest['moves']): Array<['B' | 'W', string]> {
  if (!Array.isArray(moves)) return [];
  const out: Array<['B' | 'W', string]> = [];
  for (const item of moves) {
    const player = item?.player;
    if (player !== 'B' && player !== 'W') continue;
    const raw = String(item?.move || '').trim().toUpperCase();
    if (raw === 'PASS') {
      out.push([player, 'pass']);
      continue;
    }
    if (!goGtpToUi(raw)) continue;
    out.push([player, raw]);
  }
  return out;
}

function sanitizeKataGoTimeout(timeoutMs?: number): number {
  if (!Number.isFinite(timeoutMs)) return KATAGO_DEFAULT_TIMEOUT_MS;
  return Math.max(1500, Math.min(180000, Math.floor(timeoutMs!)));
}

function sanitizeKataGoVisits(maxVisits?: number): number {
  if (!Number.isFinite(maxVisits)) return KATAGO_DEFAULT_MAX_VISITS;
  return Math.max(8, Math.min(2000, Math.floor(maxVisits!)));
}

function sanitizeKataGoMaxTime(maxTimeSec?: number): number | undefined {
  if (!Number.isFinite(maxTimeSec)) return undefined;
  return Math.max(1, Math.min(60, Number(maxTimeSec!.toFixed(2))));
}

type KataGoPendingRequest = {
  id: string;
  reqId: number;
  resolve: (resp: GoKataGoAnalyzeResponse) => void;
  queuedAt: number;
  startedAt: number;
  timeoutMs: number;
  enginePath: string;
  modelPath: string;
  configPath: string;
  timer: NodeJS.Timeout;
};

class KataGoAnalysisService {
  private proc: ChildProcessWithoutNullStreams | null = null;
  private procKey = '';
  private stdoutBuf = '';
  private stderrTail: string[] = [];
  private pending: KataGoPendingRequest | null = null;
  private queue: Promise<void> = Promise.resolve();
  private seq = 0;
  private latestReqId = 0;
  private warmed = false;
  private timeoutStreak = 0;

  private trace(message: string): void {
    try {
      const dir = path.join(app.getPath('userData'), 'logs');
      fs.mkdirSync(dir, { recursive: true });
      fs.appendFileSync(
        path.join(dir, 'katago-service.log'),
        `${new Date().toISOString()} ${message}\n`,
        'utf8',
      );
    } catch {
      // Diagnostics must never break gameplay.
    }
  }

  private terminatePending(reason: string): void {
    if (!this.pending || !this.proc || this.proc.killed) return;
    const target = this.pending;
    const id = `terminate-${Date.now()}-${target.reqId}`;
    try {
      this.proc.stdin.write(JSON.stringify({
        id,
        action: 'terminate',
        terminateId: target.id,
      }) + '\n');
      this.trace(`req#${target.reqId} terminate reason=${reason}`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'stdin write failed';
      this.trace(`req#${target.reqId} terminate-failed err=${JSON.stringify(msg)}`);
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
    this.procKey = '';
    this.stdoutBuf = '';
    this.stderrTail = [];
    this.warmed = false;
    this.timeoutStreak = 0;
  }

  disposeAll(): void {
    if (this.pending) {
      this.completePending({
        ok: false,
        ms: Date.now() - this.pending.startedAt,
        enginePath: this.pending.enginePath,
        modelPath: this.pending.modelPath,
        configPath: this.pending.configPath,
        error: 'KataGo 服务已关闭',
      });
    }
    this.disposeProcess();
  }

  private keepStderr(chunk: string): void {
    for (const line of chunk.split(/\r?\n/)) {
      if (!line.trim()) continue;
      this.stderrTail.push(line.trim());
    }
    if (this.stderrTail.length > 32) this.stderrTail = this.stderrTail.slice(-32);
  }

  private completePending(resp: GoKataGoAnalyzeResponse): void {
    if (!this.pending) return;
    const p = this.pending;
    this.pending = null;
    clearTimeout(p.timer);
    this.trace(
      `req#${p.reqId} complete ok=${resp.ok ? 1 : 0} queueMs=${p.startedAt - p.queuedAt} ` +
      `runMs=${Date.now() - p.startedAt} totalMs=${Date.now() - p.queuedAt} ` +
      `err=${resp.error ? JSON.stringify(resp.error) : '""'}`,
    );
    if (resp.ok) this.timeoutStreak = 0;
    p.resolve(resp);
  }

  private parseResponse(obj: any, pending: KataGoPendingRequest): GoKataGoAnalyzeResponse | null {
    if (!obj || obj.id !== pending.id) return null;
    if (typeof obj.error === 'string') {
      const field = typeof obj.field === 'string' ? `${obj.field}: ` : '';
      return {
        ok: false,
        suggestions: [],
        ms: Date.now() - pending.startedAt,
        enginePath: pending.enginePath,
        modelPath: pending.modelPath,
        configPath: pending.configPath,
        error: `${field}${obj.error}`,
      };
    }
    if (obj.isDuringSearch) return null;
    if (obj.noResults === true) {
      return {
        ok: false,
        suggestions: [],
        ms: Date.now() - pending.startedAt,
        enginePath: pending.enginePath,
        modelPath: pending.modelPath,
        configPath: pending.configPath,
        error: '旧局面分析已终止。',
      };
    }
    if (!Array.isArray(obj.moveInfos)) {
      return {
        ok: false,
        suggestions: [],
        ms: Date.now() - pending.startedAt,
        enginePath: pending.enginePath,
        modelPath: pending.modelPath,
        configPath: pending.configPath,
        error: 'KataGo 响应缺少 moveInfos。',
      };
    }
    const suggestions: GoKataGoSuggestion[] = [];
    for (const info of obj.moveInfos.slice(0, 5)) {
      const move = goGtpToUi(info.move);
      if (!move) continue;
      suggestions.push({
        ...move,
        winrate: typeof info.winrate === 'number' ? info.winrate : undefined,
        scoreLead: typeof info.scoreLead === 'number' ? info.scoreLead : undefined,
        visits: typeof info.visits === 'number' ? info.visits : undefined,
        order: suggestions.length + 1,
      });
    }
    return {
      ok: suggestions.length > 0,
      suggestions,
      ms: Date.now() - pending.startedAt,
      enginePath: pending.enginePath,
      modelPath: pending.modelPath,
      configPath: pending.configPath,
      error: suggestions.length > 0 ? undefined : 'KataGo 未返回可用落点。',
    };
  }

  private handleStdout(chunk: string): void {
    this.stdoutBuf += chunk;
    const lines = this.stdoutBuf.split(/\r?\n/);
    this.stdoutBuf = lines.pop() || '';
    for (const line of lines) {
      if (!this.pending || !line.trim()) continue;
      try {
          const obj = JSON.parse(line);
          const resp = this.parseResponse(obj, this.pending);
          if (resp) {
            if (resp.ok) {
              this.warmed = true;
            }
            this.completePending(resp);
            return;
          }
      } catch {
        // KataGo may print non-JSON startup lines; stderr tail keeps useful errors.
      }
    }
  }

  private ensureProcess(paths: { exe: string; model: string; config: string }): { ok: true } | { ok: false; error: string } {
    const key = `${paths.exe}|${paths.model}|${paths.config}`;
    if (this.proc && !this.proc.killed && this.procKey === key) return { ok: true };
    this.disposeProcess();
    try {
      this.proc = spawn(paths.exe, ['analysis', '-model', paths.model, '-config', paths.config], {
        cwd: path.dirname(paths.exe),
        stdio: 'pipe',
        windowsHide: true,
      });
      this.procKey = key;
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'spawn failed';
      return { ok: false, error: `KataGo 启动失败：${msg}` };
    }
    this.proc.stdout.setEncoding('utf8');
    this.proc.stderr.setEncoding('utf8');
    this.proc.stdout.on('data', (c: string) => this.handleStdout(c));
    this.proc.stderr.on('data', (c: string) => this.keepStderr(c));
    this.proc.on('error', (err) => {
      if (this.pending) {
        this.completePending({
          ok: false,
          ms: Date.now() - this.pending.startedAt,
          enginePath: this.pending.enginePath,
          modelPath: this.pending.modelPath,
          configPath: this.pending.configPath,
          error: `KataGo 运行错误：${err.message}`,
        });
      }
      this.disposeProcess();
    });
    this.proc.on('close', () => {
      if (this.pending) {
        const tail = this.stderrTail.slice(-5).join(' | ');
        this.completePending({
          ok: false,
          ms: Date.now() - this.pending.startedAt,
          enginePath: this.pending.enginePath,
          modelPath: this.pending.modelPath,
          configPath: this.pending.configPath,
          error: tail || 'KataGo 进程已退出。',
        });
      }
      this.disposeProcess();
    });
    return { ok: true };
  }

  private async runAnalyze(
    payload: GoKataGoAnalyzeRequest,
    reqId: number,
    queuedAt: number,
  ): Promise<GoKataGoAnalyzeResponse> {
    const startedAt = Date.now();
    if (reqId !== this.latestReqId) {
      this.trace(`req#${reqId} skip-stale queueMs=${startedAt - queuedAt}`);
      return { ok: false, ms: startedAt - queuedAt, error: '旧局面请求已跳过。' };
    }
    const paths = resolveKataGoPaths();
    if (!paths.ok) return { ok: false, ms: Date.now() - startedAt, error: paths.error };
    if (!validateGoBoard19(payload.board)) {
      return { ok: false, ms: Date.now() - startedAt, error: '围棋棋盘数据非法（要求19x19且元素为0/1/2）。' };
    }
    if (payload.mySide !== 1 && payload.mySide !== 2) {
      return { ok: false, ms: Date.now() - startedAt, error: '执子参数非法。' };
    }
    const ensured = this.ensureProcess(paths);
    if (!ensured.ok) {
      return {
        ok: false,
        ms: Date.now() - startedAt,
        enginePath: paths.exe,
        modelPath: paths.model,
        configPath: paths.config,
        error: ensured.error,
      };
    }

    const timeoutMs = sanitizeKataGoTimeout(payload.timeoutMs);
    const id = `go-${Date.now()}-${reqId}`;
    const initialStones: Array<[string, string]> = [];
    for (let r = 0; r < 19; r++) {
      for (let c = 0; c < 19; c++) {
        const v = payload.board[r][c];
        if (v === 0) continue;
        initialStones.push([v === 1 ? 'B' : 'W', goUiToGtp(r + 1, c + 1)]);
      }
    }
    const turn = payload.mySide === 1 ? 'B' : 'W';
    const maxTime = sanitizeKataGoMaxTime(payload.maxTimeSec);
    const historyMoves = sanitizeKataGoMoves(payload.moves);
    this.trace(
      `req#${reqId} start queueMs=${startedAt - queuedAt} stones=${initialStones.length} ` +
      `moves=${historyMoves.length} visits=${sanitizeKataGoVisits(payload.maxVisits)} ` +
      `maxTime=${maxTime ?? -1} timeoutMs=${timeoutMs}`,
    );
    const overrideSettings: Record<string, number> = {};
    if (maxTime) overrideSettings.maxTime = maxTime;
    const query = {
      id,
      rules: 'chinese',
      komi: Number.isFinite(payload.komi) ? payload.komi : 6.5,
      boardXSize: 19,
      boardYSize: 19,
      initialStones: historyMoves.length > 0 ? [] : initialStones,
      moves: historyMoves,
      initialPlayer: turn,
      maxVisits: sanitizeKataGoVisits(payload.maxVisits),
      ...(Object.keys(overrideSettings).length > 0 ? { overrideSettings } : {}),
      includeOwnership: false,
    };

    return new Promise<GoKataGoAnalyzeResponse>((resolve) => {
      const pending: KataGoPendingRequest = {
        id,
        reqId,
        resolve,
        queuedAt,
        startedAt,
        timeoutMs,
        enginePath: paths.exe,
        modelPath: paths.model,
        configPath: paths.config,
        timer: setTimeout(() => {
          if (!this.pending) return;
          const tail = this.stderrTail.slice(-5).join(' | ');
          this.timeoutStreak += 1;
          this.terminatePending('analysis-timeout');
          this.completePending({
            ok: false,
            ms: Date.now() - startedAt,
            enginePath: paths.exe,
            modelPath: paths.model,
            configPath: paths.config,
            error: tail ? `KataGo 分析超时：${tail}` : `KataGo 分析超时（${timeoutMs}ms）`,
          });
          if (this.timeoutStreak >= 2) {
            this.trace(`process-restart timeoutStreak=${this.timeoutStreak}`);
            this.disposeProcess();
          }
        }, timeoutMs + 1000),
      };
      this.pending = pending;
      try {
        this.proc!.stdin.write(JSON.stringify(query) + '\n');
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'stdin write failed';
        this.completePending({
          ok: false,
          ms: Date.now() - startedAt,
          enginePath: paths.exe,
          modelPath: paths.model,
          configPath: paths.config,
          error: `KataGo 发送命令失败：${msg}`,
        });
        this.disposeProcess();
      }
    });
  }

  analyze(payload: GoKataGoAnalyzeRequest): Promise<GoKataGoAnalyzeResponse> {
    const reqId = ++this.seq;
    const queuedAt = Date.now();
    this.latestReqId = reqId;
    this.trace(`req#${reqId} enqueue`);
    this.terminatePending('newer-position');
    const task = this.queue.then(() => this.runAnalyze(payload, reqId, queuedAt));
    this.queue = task.then(() => undefined, () => undefined);
    return task;
  }

  warmup(): Promise<GoKataGoAnalyzeResponse> {
    const paths = resolveKataGoPaths();
    if (paths.ok && this.proc && !this.proc.killed && this.warmed) {
      return Promise.resolve({
        ok: true,
        ms: 0,
        suggestions: [],
        enginePath: paths.exe,
        modelPath: paths.model,
        configPath: paths.config,
      });
    }
    if (this.pending) {
      return Promise.resolve({
        ok: true,
        ms: 0,
        suggestions: [],
        enginePath: paths.ok ? paths.exe : undefined,
        modelPath: paths.ok ? paths.model : undefined,
        configPath: paths.ok ? paths.config : undefined,
      });
    }
    const board = Array.from({ length: 19 }, () => Array.from({ length: 19 }, () => 0));
    return this.analyze({
      board,
      mySide: 1,
      komi: 6.5,
      maxVisits: 8,
      maxTimeSec: 2,
      timeoutMs: KATAGO_WARMUP_TIMEOUT_MS,
    });
  }
}

const kataGoService = new KataGoAnalysisService();

async function analyzeGoByKataGo(payload: GoKataGoAnalyzeRequest): Promise<GoKataGoAnalyzeResponse> {
  return kataGoService.analyze(payload);
}

async function warmupGoByKataGo(): Promise<GoKataGoAnalyzeResponse> {
  return kataGoService.warmup();
}

function attemptReconnect(): void {
  if (!lastConfig || !currentNickname) return;
  if (reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
    sendToRenderer(IPC_CHANNELS.CONNECTION_ERROR, `Reconnect failed after ${MAX_RECONNECT_ATTEMPTS} attempts`);
    reconnectAttempts = 0;
    return;
  }
  reconnectAttempts++;
  const delay = RECONNECT_BASE_DELAY_MS * reconnectAttempts;
  sendToRenderer(IPC_CHANNELS.CONNECTION_STATUS, 'connecting' as ConnectionStatus);
  reconnectTimer = setTimeout(async () => {
    try {
      await sshManager.connect(
        lastConfig!,
        currentNickname,
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
            if (message.type === 'system') {
              const rooms = extractRoomsFromSystem(message.content);
              if (rooms) {
                sendToRenderer(IPC_CHANNELS.ROOM_UPDATE, rememberRooms(rooms), currentRoom);
              }
              const usersSnapshot = extractUsersSnapshot(message.content);
              if (usersSnapshot) {
                sendToRenderer(IPC_CHANNELS.USER_UPDATE, usersSnapshot);
              }
              const activeRoom = extractActiveRoom(message.content);
              if (activeRoom) {
                pushRoomState(activeRoom);
              }
            }
            if (message.room) {
              rememberRooms([message.room]);
              if (message.type === 'join' || message.type === 'leave') {
                sendToRenderer(IPC_CHANNELS.ROOM_UPDATE, [...knownRooms], currentRoom);
              }
            }
            sendToRenderer(IPC_CHANNELS.CHAT_MESSAGE, message);
          }
        },
        () => {
          sendToRenderer(IPC_CHANNELS.CONNECTION_STATUS, 'disconnected');
          attemptReconnect();
        },
      );
      reconnectAttempts = 0;
    } catch {
      attemptReconnect();
    }
  }, delay);
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

  ipcMain.handle(IPC_CHANNELS.LOAD_CHAT_HISTORY, (_event, identity: ChatHistoryIdentity) => {
    return chatHistoryManager.load(identity);
  });

  ipcMain.handle(
    IPC_CHANNELS.SAVE_CHAT_HISTORY,
    (_event, identity: ChatHistoryIdentity, snapshot: ChatHistorySnapshot) => {
      return chatHistoryManager.save(identity, snapshot);
    },
  );

  ipcMain.on(
    IPC_CHANNELS.FLUSH_CHAT_HISTORY,
    (event, identity: ChatHistoryIdentity, snapshot: ChatHistorySnapshot) => {
      try {
        event.returnValue = chatHistoryManager.save(identity, snapshot);
      } catch (error) {
        console.error('[ChatHistory] Failed to flush history:', error);
        event.returnValue = false;
      }
    },
  );

  // Connect
  ipcMain.handle(IPC_CHANNELS.CONNECT, async (_event, config: ConnectionConfig, nickname: string) => {
    currentNickname = nickname;
    currentRoom = 'default';
    knownRooms = new Set<string>(['default']);
    lastConfig = config;
    reconnectAttempts = 0;
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }

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
                sendToRenderer(IPC_CHANNELS.ROOM_UPDATE, rememberRooms(rooms), currentRoom);
              }
              const usersSnapshot = extractUsersSnapshot(message.content);
              if (usersSnapshot) {
                sendToRenderer(IPC_CHANNELS.USER_UPDATE, usersSnapshot);
              }
              const activeRoom = extractActiveRoom(message.content);
              if (activeRoom) {
                pushRoomState(activeRoom);
              }
            }
            if (message.room) {
              rememberRooms([message.room]);
              if (message.type === 'join' || message.type === 'leave') {
                sendToRenderer(IPC_CHANNELS.ROOM_UPDATE, [...knownRooms], currentRoom);
              }
            }
            sendToRenderer(IPC_CHANNELS.CHAT_MESSAGE, message);
          }
        },
        () => {
          sendToRenderer(IPC_CHANNELS.CONNECTION_STATUS, 'disconnected');
          attemptReconnect();
        },
      );

      pushRoomState('default');
      sshManager.send(buildSendMessage(currentNickname, '/rooms'));
      return { success: true };
    } catch (err: unknown) {
      const errorMessage = err instanceof Error ? err.message : 'Connection failed';
      return { success: false, error: errorMessage };
    }
  });

  // Disconnect
  ipcMain.handle(IPC_CHANNELS.DISCONNECT, () => {
    lastConfig = null;
    reconnectAttempts = 0;
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
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
    const ok = sshManager.send(message);
    if (ok) {
      const trimmed = text.trim();
      const roomCommand = trimmed.match(/^\/(join|switch|part)\s+#?([a-zA-Z0-9_-]{1,32})/i);
      if (roomCommand) {
        const [, command, rawRoom] = roomCommand;
        const roomName = normalizeRoomName(rawRoom);
        if (roomName) {
          if (command.toLowerCase() === 'part') {
            knownRooms.delete(roomName);
            if (currentRoom === roomName) {
              currentRoom = 'default';
            }
            pushRoomState(currentRoom);
          } else {
            pushRoomState(roomName);
          }
          sshManager.send(buildSendMessage(currentNickname, '/rooms'));
        }
      }
    }
    return ok;
  });

  // Join room
  ipcMain.handle(IPC_CHANNELS.JOIN_ROOM, (_event, room: string) => {
    if (!sshManager.isConnected()) {
      return false;
    }
    const roomName = normalizeRoomName(room);
    if (!roomName) return false;
    const message = buildSendMessage(currentNickname, `/join ${roomName}`);
    const ok = sshManager.send(message);
    if (ok) {
      pushRoomState(roomName);
      sshManager.send(buildSendMessage(currentNickname, '/rooms'));
    }
    return ok;
  });

  // Switch room
  ipcMain.handle(IPC_CHANNELS.SWITCH_ROOM, (_event, room: string) => {
    if (!sshManager.isConnected()) {
      return false;
    }
    const roomName = normalizeRoomName(room);
    if (!roomName) return false;
    const message = buildSendMessage(currentNickname, `/switch ${roomName}`);
    const ok = sshManager.send(message);
    if (ok) {
      pushRoomState(roomName);
      sshManager.send(buildSendMessage(currentNickname, '/rooms'));
    }
    return ok;
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

  // Game engine assistant endpoints are disabled in shared builds.
  ipcMain.handle(IPC_CHANNELS.GOMOKU_RAPFI_ANALYZE, async (_event, _payload: GomokuRapfiAnalyzeRequest): Promise<GomokuRapfiAnalyzeResponse> => {
    return { ok: false, ms: 0, error: 'Game assistant disabled in this build.' };
  });

  ipcMain.handle(IPC_CHANNELS.GO_KATAGO_ANALYZE, async (_event, _payload: GoKataGoAnalyzeRequest): Promise<GoKataGoAnalyzeResponse> => {
    return { ok: false, ms: 0, suggestions: [], error: 'Game assistant disabled in this build.' };
  });
  ipcMain.handle(IPC_CHANNELS.GO_KATAGO_WARMUP, async (): Promise<GoKataGoAnalyzeResponse> => {
    return { ok: false, ms: 0, suggestions: [], error: 'Game assistant disabled in this build.' };
  });

  ipcMain.handle(IPC_CHANNELS.XIANGQI_PIKAFISH_ANALYZE, async (_event, _payload: XiangqiPikafishAnalyzeRequest): Promise<XiangqiPikafishAnalyzeResponse> => {
    return { ok: false, ms: 0, error: 'Game assistant disabled in this build.' };
  });

  ipcMain.handle(
    IPC_CHANNELS.OPEN_SECURE_WEB_SESSION,
    async (_event, payload: { kind: SecureWebKind; url: string; key: string }) => {
      return openSecureWebSession(payload);
    },
  );

  ipcMain.handle(
    IPC_CHANNELS.UPLOAD_SECURE_FILE,
    async (
      _event,
      payload: {
        url: string;
        key: string;
        filename: string;
        mime: string;
        data: ArrayBuffer;
      },
    ): Promise<{ ok: boolean; filename?: string; error?: string }> => {
      const url = String(payload?.url || '').trim();
      const key = String(payload?.key || '').trim().toUpperCase();
      const filename = String(payload?.filename || 'file').replace(/[\\/]/g, '_').slice(0, 200) || 'file';
      const mime = String(payload?.mime || 'application/octet-stream');
      if (!url || !/^https?:\/\//i.test(url)) {
        return { ok: false, error: 'Invalid upload URL' };
      }
      if (!key || key.length !== 6) {
        return { ok: false, error: 'Invalid upload key' };
      }
      if (!payload?.data) {
        return { ok: false, error: 'Empty file data' };
      }

      const bytes = Buffer.from(payload.data);
      try {
        return await postSecureUpload(url, key, filename, mime, bytes, false);
      } catch (e) {
        if (url.toLowerCase().startsWith('https:') && isTlsCertError(e)) {
          try {
            return await postSecureUpload(url, key, filename, mime, bytes, true);
          } catch (e2) {
            return { ok: false, error: e2 instanceof Error ? e2.message : String(e2) };
          }
        }
        return { ok: false, error: e instanceof Error ? e.message : String(e) };
      }
    },
  );
}

function isTlsCertError(e: unknown): boolean {
  const err = e as { code?: string; message?: string } | null;
  const code = String(err?.code || '');
  const msg = String(err?.message || e || '');
  return (
    code === 'UNABLE_TO_VERIFY_LEAF_SIGNATURE' ||
    code === 'CERT_HAS_EXPIRED' ||
    code === 'DEPTH_ZERO_SELF_SIGNED_CERT' ||
    code === 'SELF_SIGNED_CERT_IN_CHAIN' ||
    code === 'UNABLE_TO_GET_ISSUER_CERT_LOCALLY' ||
    /CERTIFICATE_VERIFY_FAILED|certificate|CERT_|SSL/i.test(msg)
  );
}

function postSecureUpload(
  urlStr: string,
  key: string,
  filename: string,
  mime: string,
  bytes: Buffer,
  insecure: boolean,
): Promise<{ ok: boolean; filename?: string; error?: string }> {
  const u = new URL(urlStr);
  const boundary = `----SSHChat${Date.now().toString(16)}${Math.random().toString(16).slice(2)}`;
  const preamble = Buffer.from(
    `--${boundary}\r\n` +
      `Content-Disposition: form-data; name="file"; filename="${filename}"\r\n` +
      `Content-Type: ${mime}\r\n\r\n`,
    'utf8',
  );
  const epilogue = Buffer.from(`\r\n--${boundary}--\r\n`, 'utf8');
  const body = Buffer.concat([preamble, bytes, epilogue]);
  const lib = u.protocol === 'https:' ? https : http;
  const options: https.RequestOptions = {
    protocol: u.protocol,
    hostname: u.hostname,
    port: u.port || (u.protocol === 'https:' ? 443 : 80),
    path: `${u.pathname}${u.search}`,
    method: 'POST',
    headers: {
      'X-Upload-Key': key,
      'Content-Type': `multipart/form-data; boundary=${boundary}`,
      'Content-Length': body.length,
    },
    rejectUnauthorized: !insecure,
  };

  return new Promise((resolve, reject) => {
    const req = lib.request(options, (res) => {
      const chunks: Buffer[] = [];
      res.on('data', (c) => chunks.push(Buffer.isBuffer(c) ? c : Buffer.from(c)));
      res.on('end', () => {
        const raw = Buffer.concat(chunks).toString('utf8');
        let result: { error?: string; filename?: string } = {};
        try {
          result = raw.trim() ? (JSON.parse(raw) as typeof result) : {};
        } catch {
          result = {};
        }
        if ((res.statusCode || 500) >= 400) {
          resolve({ ok: false, error: result.error || `HTTP ${res.statusCode}` });
          return;
        }
        resolve({ ok: true, filename: result.filename || filename });
      });
    });
    req.on('error', reject);
    req.setTimeout(120_000, () => {
      req.destroy(new Error('upload timeout'));
    });
    req.write(body);
    req.end();
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
  rapfiPonderService.disposeAll();
  kataGoService.disposeAll();
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('before-quit', () => {
  rapfiService.disposeAll();
  rapfiPonderService.disposeAll();
  kataGoService.disposeAll();
});

