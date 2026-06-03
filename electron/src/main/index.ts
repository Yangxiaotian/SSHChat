import { app, BrowserWindow, ipcMain, Menu } from 'electron';
import * as path from 'path';
import * as fs from 'fs';
import * as os from 'os';
import { exec, spawn, spawnSync, ChildProcessWithoutNullStreams } from 'child_process';
import { SSHManager } from './ssh-manager';
import { ConfigManager } from './config-manager';
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
} from '../shared/protocol';

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
let lastConfig: ConnectionConfig | null = null;
let reconnectTimer: NodeJS.Timeout | null = null;
let reconnectAttempts = 0;
const MAX_RECONNECT_ATTEMPTS = 5;
const RECONNECT_BASE_DELAY_MS = 3000;
const singleInstanceLock = app.requestSingleInstanceLock();

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

const RAPFI_ANALYZE_DEFAULT_TIMEOUT_MS = intEnv('RAPFI_DEFAULT_TIMEOUT_MS', 8000, 1000, 60000);
const RAPFI_ANALYZE_MAX_TIMEOUT_MS = intEnv('RAPFI_MAX_TIMEOUT_MS', 45000, 5000, 90000);
const RAPFI_HASH_MB = intEnv('RAPFI_HASH_MB', defaultRapfiHashMb(), 128, 4096);
const RAPFI_PONDER_HASH_MB = intEnv(
  'RAPFI_PONDER_HASH_MB',
  Math.max(256, Math.min(1024, Math.floor(RAPFI_HASH_MB / 2))),
  128,
  RAPFI_HASH_MB,
);
const RAPFI_THREADS = intEnv('RAPFI_THREADS', 0, 0, 128);
const RAPFI_MIN_DEPTH = intEnv('RAPFI_MIN_DEPTH', 40, 8, 99);
let resolvedRapfiExecutableCache: string | null | undefined;
const KATAGO_DEFAULT_TIMEOUT_MS = intEnv('KATAGO_TIMEOUT_MS', 60000, 1500, 180000);
const KATAGO_DEFAULT_MAX_VISITS = intEnv('KATAGO_MAX_VISITS', 96, 8, 2000);
let resolvedKataGoPathsCache:
  | { ok: true; exe: string; model: string; config: string }
  | { ok: false; error: string }
  | undefined;

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

function buildRapfiInitLines(timeoutMs: number): string[] {
  const lines: string[] = [];
  lines.push(`INFO timeout_turn ${timeoutMs}`);
  lines.push(`INFO depth ${RAPFI_MIN_DEPTH}`);
  lines.push('INFO rule 4');
  return lines;
}

function buildRapfiBoardCommands(
  board: number[][],
  mySide: 1 | -1,
  timeoutMs: number,
  inited: boolean,
  hashMb: number,
  forceRestart = false,
): string[] {
  const lines: string[] = [];
  if (!inited) {
    lines.push('START 15');
    lines.push(`INFO hash ${hashMb}`);
    if (RAPFI_THREADS > 0) {
      lines.push(`INFO threads ${RAPFI_THREADS}`);
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
      const who = v === mySide ? 1 : 2;
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
    this.lastEngineBoard = null;
    this.lastMySide = null;
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

    const canTryIncremental =
      this.allowIncremental &&
      requestMode !== 'ponder' &&
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
      cmds = buildRapfiBoardCommands(payload.board, payload.mySide, timeoutMs, this.inited, this.hashMb, forceRestart);
      cmdMode = 'full-board';
      if (!this.inited) cmdReason = 'cold-start';
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

const rapfiService = new RapfiEngineService('move', RAPFI_HASH_MB, true, true);
const rapfiPonderService = new RapfiEngineService('ponder', RAPFI_PONDER_HASH_MB, false, false);

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

  resolvedKataGoPathsCache = { ok: true, exe, model, config };
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

function sanitizeKataGoTimeout(timeoutMs?: number): number {
  if (!Number.isFinite(timeoutMs)) return KATAGO_DEFAULT_TIMEOUT_MS;
  return Math.max(1500, Math.min(180000, Math.floor(timeoutMs!)));
}

function sanitizeKataGoVisits(maxVisits?: number): number {
  if (!Number.isFinite(maxVisits)) return KATAGO_DEFAULT_MAX_VISITS;
  return Math.max(8, Math.min(2000, Math.floor(maxVisits!)));
}

type KataGoPendingRequest = {
  id: string;
  resolve: (resp: GoKataGoAnalyzeResponse) => void;
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

  private async runAnalyze(payload: GoKataGoAnalyzeRequest, reqId: number): Promise<GoKataGoAnalyzeResponse> {
    const startedAt = Date.now();
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
    const query = {
      id,
      rules: 'chinese',
      komi: Number.isFinite(payload.komi) ? payload.komi : 6.5,
      boardXSize: 19,
      boardYSize: 19,
      initialStones,
      moves: [],
      initialPlayer: turn,
      maxVisits: sanitizeKataGoVisits(payload.maxVisits),
      includeOwnership: false,
    };

    return new Promise<GoKataGoAnalyzeResponse>((resolve) => {
      const pending: KataGoPendingRequest = {
        id,
        resolve,
        startedAt,
        timeoutMs,
        enginePath: paths.exe,
        modelPath: paths.model,
        configPath: paths.config,
        timer: setTimeout(() => {
          if (!this.pending) return;
          const tail = this.stderrTail.slice(-5).join(' | ');
          this.completePending({
            ok: false,
            ms: Date.now() - startedAt,
            enginePath: paths.exe,
            modelPath: paths.model,
            configPath: paths.config,
            error: tail ? `KataGo 分析超时：${tail}` : `KataGo 分析超时（${timeoutMs}ms）`,
          });
          this.disposeProcess();
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
    const task = this.queue.then(() => this.runAnalyze(payload, reqId));
    this.queue = task.then(() => undefined, () => undefined);
    return task;
  }
}

const kataGoService = new KataGoAnalysisService();

async function analyzeGoByKataGo(payload: GoKataGoAnalyzeRequest): Promise<GoKataGoAnalyzeResponse> {
  return kataGoService.analyze(payload);
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

  // Connect
  ipcMain.handle(IPC_CHANNELS.CONNECT, async (_event, config: ConnectionConfig, nickname: string) => {
    currentNickname = nickname;
    currentRoom = 'default';
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
          attemptReconnect();
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

  // Go: KataGo external engine analysis
  ipcMain.handle(IPC_CHANNELS.GO_KATAGO_ANALYZE, async (_event, payload: GoKataGoAnalyzeRequest) => {
    return analyzeGoByKataGo(payload);
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

