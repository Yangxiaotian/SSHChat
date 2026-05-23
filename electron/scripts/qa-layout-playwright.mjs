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

const errors = [];
const logs = [];

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForHttp(url, timeoutMs = 60_000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    try {
      const res = await fetch(url);
      if (res.ok) return true;
    } catch {
      // retry
    }
    await sleep(500);
  }
  throw new Error(`Timeout waiting for ${url}`);
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
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

vite.stdout.on('data', (d) => logs.push(String(d)));
vite.stderr.on('data', (d) => logs.push(String(d)));

let browser;
let page;

try {
  await waitForHttp('http://127.0.0.1:5173', 90_000);

  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  page = await context.newPage();

  page.on('pageerror', (err) => errors.push(`pageerror: ${err.message}`));
  page.on('console', (msg) => {
    if (msg.type() === 'error') errors.push(`console.error: ${msg.text()}`);
  });

  await page.addInitScript(() => {
    const noop = () => true;
    // @ts-ignore
    window.api = {
      loadConfig: async () => null,
      saveConfig: async () => true,
      connect: async () => ({ success: false, error: 'mock: offline' }),
      disconnect: async () => true,
      isConnected: async () => false,
      sendMessage: async () => true,
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
      onChatMessage: () => noop,
      onRoomUpdate: () => noop,
      onUserUpdate: () => noop,
      onConnectionStatus: () => noop,
      onError: () => noop,
    };
  });

  await page.goto('http://127.0.0.1:5173', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1000);

  const cancelBtn = page.locator('button:has-text("Cancel")').first();
  if (await cancelBtn.isVisible().catch(() => false)) {
    await cancelBtn.click();
  }

  const splitter = page.locator('.chat-splitter');
  await splitter.waitFor({ state: 'visible', timeout: 20_000 });

  await page.screenshot({ path: path.resolve(artifactsDir, 'layout-before.png') });

  const before = await page.evaluate(() => {
    const split = document.querySelector('.chat-main-split')?.getBoundingClientRect();
    const game = document.querySelector('.game-pane')?.getBoundingClientRect();
    const chat = document.querySelector('.chat-messages')?.getBoundingClientRect();
    const input = document.querySelector('.input-bar')?.getBoundingClientRect();
    return { split, game, chat, input };
  });

  assert(before?.split && before?.game && before?.chat && before?.input, '布局关键区域未找到');
  assert(before.chat.height >= 120, '聊天区初始高度过小');
  assert(before.input.height >= 30, '输入区未正常渲染');

  const splitBox = await splitter.boundingBox();
  assert(!!splitBox, '分割条无可交互区域');

  await page.mouse.move(splitBox.x + splitBox.width / 2, splitBox.y + splitBox.height / 2);
  await page.mouse.down();
  await page.mouse.move(splitBox.x + splitBox.width / 2, splitBox.y + splitBox.height / 2 + 100);
  await page.mouse.up();
  await page.waitForTimeout(300);

  const collapseBtn = page.locator('button:has-text("收起面板")').first();
  if (await collapseBtn.isVisible().catch(() => false)) {
    await collapseBtn.click();
    await page.waitForTimeout(250);
    const expandBtn = page.locator('button:has-text("展开面板")').first();
    if (await expandBtn.isVisible().catch(() => false)) {
      await expandBtn.click();
    }
  }

  const after = await page.evaluate(() => {
    const game = document.querySelector('.game-pane')?.getBoundingClientRect();
    const chat = document.querySelector('.chat-messages')?.getBoundingClientRect();
    const input = document.querySelector('.input-bar')?.getBoundingClientRect();
    return { game, chat, input };
  });

  assert(after?.game && after?.chat && after?.input, '拖拽后布局读取失败');
  assert(after.chat.height >= 120, '拖拽后聊天区不可见');
  assert(after.input.height >= 30, '拖拽后输入区异常');
  assert(after.game.height > before.game.height, '拖拽后游戏区高度未变化');

  await page.screenshot({ path: path.resolve(artifactsDir, 'layout-after.png') });

  const inputDisabled = await page.locator('.input-field').first().isDisabled();
  if (!inputDisabled) {
    await page.locator('.input-field').first().click();
    await page.locator('.input-field').first().fill('/game list');
  } else {
    const visible = await page.locator('.input-field').first().isVisible();
    assert(visible, '离线态输入框未渲染');
  }
  await page.screenshot({ path: path.resolve(artifactsDir, 'layout-input-focus.png') });

  if (errors.length > 0) {
    throw new Error(`检测到前端错误:\n${errors.join('\n')}`);
  }

  console.log('QA PASS: layout and core controls are functional.');
  console.log(`Artifacts: ${artifactsDir}`);
} finally {
  if (browser) {
    await browser.close().catch(() => {});
  }
  vite.kill('SIGTERM');
}
