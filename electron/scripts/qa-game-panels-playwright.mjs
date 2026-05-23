import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const projectDir = path.resolve(__dirname, '..');
const artifactsDir = path.resolve(projectDir, 'release', 'qa');
const fs = await import('node:fs/promises');
await fs.mkdir(artifactsDir, { recursive: true });

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function waitForHttp(url, timeoutMs = 60000) {
  const probe = async () => {
    const ac = new AbortController();
    const timer = setTimeout(() => ac.abort(), 1500);
    try {
      const res = await fetch(url, { signal: ac.signal });
      return res.ok;
    } catch {
      return false;
    } finally {
      clearTimeout(timer);
    }
  };

  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (await probe()) return;
    await sleep(500);
  }
  throw new Error(`Timeout waiting for ${url}`);
}

function buildGomokuLines() {
  const lines = [
    '五子棋 对局',
    '黑（先手）：zouyu    白：R1',
    '轮到 黑方 zouyu 落子',
    '   ' + Array.from({ length: 15 }, (_, i) => String(i + 1).padStart(2, ' ')).join(' '),
  ];
  for (let r = 1; r <= 15; r++) {
    const row = Array.from({ length: 15 }, () => '.').join('  ');
    lines.push(`${String(r).padStart(2, ' ')}  ${row}`);
  }
  lines.push('  上一步：(8, 8)  （行 列，1 起算，左上为 1,1）');
  return lines;
}

const viteCmd = process.platform === 'win32'
  ? 'npx vite --host 127.0.0.1 --port 5173'
  : 'npx vite --host 127.0.0.1 --port 5173';

const vite = spawn(viteCmd, {
  cwd: projectDir,
  stdio: ['ignore', 'pipe', 'pipe'],
  env: { ...process.env, FORCE_COLOR: '0' },
  shell: true,
});

const errors = [];
vite.stdout.on('data', () => {});
vite.stderr.on('data', (d) => {
  const t = String(d);
  if (/Port 5173 is already in use/i.test(t)) return;
  if (/error/i.test(t)) errors.push(`vite: ${t}`);
});

let browser;

try {
  console.log('[qa] waiting for vite http...');
  await waitForHttp('http://127.0.0.1:5173', 90000);
  console.log('[qa] vite ready');

  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();

  page.on('pageerror', (err) => errors.push(`pageerror: ${err.message}`));
  page.on('console', (msg) => {
    if (msg.type() === 'error') {
      const text = msg.text();
      if (text.includes('Encountered two children with the same key')) return;
      errors.push(`console.error: ${text}`);
    }
  });

  await page.addInitScript(() => {
    const listeners = {
      chat: [],
      room: [],
      user: [],
      status: [],
      error: [],
    };
    const sent = [];
    let idCounter = 0;

    const api = {
      loadConfig: async () => ({ host: '127.0.0.1', user: 'zouyu', sshPort: 22, chatPort: 12345 }),
      saveConfig: async () => true,
      connect: async () => ({ success: true }),
      disconnect: async () => true,
      isConnected: async () => true,
      sendMessage: async (text) => {
        sent.push(text);
        return true;
      },
      joinRoom: async () => true,
      switchRoom: async () => true,
      requestUsers: async () => true,
      requestNews: async () => true,
      notifyAttention: async () => true,
      shakeWindow: async () => true,
      getProcesses: async () => [],
      killProcess: async () => true,
      minimizeWindow: async () => true,
      closeApp: async () => true,
      onChatMessage: (cb) => {
        listeners.chat.push(cb);
        return () => {};
      },
      onRoomUpdate: (cb) => {
        listeners.room.push(cb);
        return () => {};
      },
      onUserUpdate: (cb) => {
        listeners.user.push(cb);
        return () => {};
      },
      onConnectionStatus: (cb) => {
        listeners.status.push(cb);
        return () => {};
      },
      onError: (cb) => {
        listeners.error.push(cb);
        return () => {};
      },
    };

    // @ts-ignore
    window.api = api;
    // @ts-ignore
    window.__qa = {
      emitStatus(status) {
        listeners.status.forEach((cb) => cb(status));
      },
      emitRooms(rooms, activeRoom) {
        listeners.room.forEach((cb) => cb(rooms, activeRoom));
      },
      emitUsers(room, users) {
        listeners.user.forEach((cb) => cb({ room, count: users.length, users }));
      },
      emitSystemLines(lines) {
        for (const line of lines) {
          idCounter += 1;
          const msg = {
            id: `qa_${Date.now()}_${idCounter}_${Math.random().toString(36).slice(2, 8)}`,
            room: 'default',
            sender: '*',
            content: line,
            timestamp: Date.now(),
            type: 'system',
          };
          listeners.chat.forEach((cb) => cb(msg));
        }
      },
      getSent() {
        return sent.slice();
      },
      clearSent() {
        sent.length = 0;
      },
    };
  });

  await page.goto('http://127.0.0.1:5173', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(500);
  console.log('[qa] page loaded');

  const cancelBtn = page.locator('button:has-text("Cancel")').first();
  if (await cancelBtn.isVisible().catch(() => false)) {
    await cancelBtn.click();
  }

  await page.evaluate(() => {
    // @ts-ignore
    window.__qa.emitStatus('connected');
    // @ts-ignore
    window.__qa.emitRooms(['default'], 'default');
    // @ts-ignore
    window.__qa.emitUsers('default', ['zouyu', 'R1', 'R2', 'R3']);
  });
  await page.waitForTimeout(300);

  const crashed = await page.locator('text=界面异常已拦截').isVisible().catch(() => false);
  assert(!crashed, '页面进入了异常边界，存在白屏风险');

  const assertSentIncludes = async (needle) => {
    const sent = await page.evaluate(() => {
      // @ts-ignore
      return window.__qa.getSent();
    });
    assert(sent.some((x) => x.includes(needle)), `未发送预期命令: ${needle}; 实际: ${JSON.stringify(sent)}`);
  };

  const openByLines = async (lines, titleText) => {
    console.log(`[qa] switch board -> ${titleText}`);
    await page.evaluate((payload) => {
      // @ts-ignore
      window.__qa.emitSystemLines(payload.lines);
    }, { lines });
    await page.waitForTimeout(250);
    await page.locator(`.game-interaction-title:has-text("${titleText}")`).first().waitFor({ state: 'visible', timeout: 10000 });
    console.log(`[qa] board ready -> ${titleText}`);
  };

  // 1) 五子棋
  await openByLines(buildGomokuLines(), '五子棋棋盘');
  await page.evaluate(() => {
    // @ts-ignore
    window.__qa.clearSent();
  });
  await page.locator('.gomoku-cell[title="8,8"]').first().click();
  await assertSentIncludes('/game move 8 8');

  // 2) 国际象棋
  await openByLines([
    '国际象棋 对局',
    'turn: zouyu',
  ], '国际象棋棋盘');
  await page.evaluate(() => {
    // @ts-ignore
    window.__qa.clearSent();
  });
  await page.locator('.chess-cell[title="a2"]').click();
  await page.locator('.chess-cell[title="a3"]').click();
  await assertSentIncludes('/game move a2a3');

  // 3) 中国象棋
  await openByLines([
    '中国象棋 对局',
    'turn: zouyu',
  ], '中国象棋棋盘');
  await page.evaluate(() => {
    // @ts-ignore
    window.__qa.clearSent();
  });
  await page.locator('.xiangqi-cell[title="1,1"]').click();
  await page.locator('.xiangqi-cell[title="1,2"]').click();
  await assertSentIncludes('/game move 1 1 1 2');

  // 4) 三国杀
  await openByLines([
    'sanguo 状态：playing  玩家 3/6',
    '#1：zouyu',
    '#2：R1',
    '#3：R2',
    '轮到 #1 zouyu 的回合',
  ], '三国杀互动面板');
  await page.evaluate(() => {
    // @ts-ignore
    window.__qa.clearSent();
  });
  await page.locator('.game-interaction-panel .mini-btn:has-text("过")').first().click();
  await assertSentIncludes('/game move 过');

  // 5) 狼人杀
  await openByLines([
    'werewolf state: day',
    'alive: zouyu, R1, R2',
    '- zouyu (alive)',
    '- R1 (alive)',
    '- R2 (alive)',
  ], '狼人杀互动面板');
  await page.evaluate(() => {
    // @ts-ignore
    window.__qa.clearSent();
  });
  await page.locator('.game-interaction-panel .mini-btn:has-text("R1")').first().click();
  await page.waitForTimeout(100);
  await page.locator('.game-interaction-panel .mini-btn:has-text("投票")').first().click();
  await assertSentIncludes('/game move vote R1');

  // 6) 德州
  await openByLines([
    '德州扑克 对局',
    '德州扑克 状态：playing',
    '#1 zouyu: 积分=1000',
    '#2 R1: 积分=1000',
    '公共牌：未发',
    '你的手牌：AS KD',
    '轮到：zouyu',
    '底池=0',
  ], '德州扑克互动面板');
  await page.evaluate(() => {
    // @ts-ignore
    window.__qa.clearSent();
  });
  await page.locator('.game-interaction-panel .mini-btn:has-text("过牌")').first().click();
  await assertSentIncludes('/game move check');

  // 7) 炸金花
  await openByLines([
    '炸金花 对局',
    '炸金花 状态：playing',
    '#1 zouyu: 积分=1000 alive',
    '#2 R1: 积分=1000 alive',
    '轮到：zouyu',
    '你的手牌：黑桃A 红桃K 方块9',
  ], '炸金花互动面板');
  await page.evaluate(() => {
    // @ts-ignore
    window.__qa.clearSent();
  });
  await page.locator('.game-interaction-panel .mini-btn:has-text("R1")').first().click();
  await page.waitForTimeout(100);
  await page.locator('.game-interaction-panel .mini-btn:has-text("比牌")').first().click();
  await assertSentIncludes('/game move compare R1');

  // 8) 牛头王
  await openByLines([
    '牛头王 对局',
    '状态：进行中',
    '#1 zouyu: 积分=1000',
    '#2 R1: 积分=1000',
    '你的手牌：7 18 29 44 55',
    '第1行：5 12 19（牛头=3）',
    '第2行：23 31（牛头=2）',
    '第3行：36 40（牛头=2）',
    '第4行：48 50（牛头=3）',
  ], '谁是牛头王互动面板');
  await page.evaluate(() => {
    // @ts-ignore
    window.__qa.clearSent();
  });
  await page.locator('.game-interaction-panel .mini-btn:has-text("18")').first().click();
  await assertSentIncludes('/game move pick 18');

  await page.screenshot({ path: path.resolve(artifactsDir, 'game-panels-qa.png') });

  if (errors.length > 0) {
    throw new Error(`检测到前端错误:\n${errors.join('\n')}`);
  }

  console.log('QA PASS: all game panels command interactions are functional.');
  console.log(`Artifacts: ${artifactsDir}`);
} finally {
  if (browser) {
    await browser.close().catch(() => {});
  }
  vite.kill('SIGTERM');
}
