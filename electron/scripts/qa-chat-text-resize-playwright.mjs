import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const projectDir = path.resolve(__dirname, '..');

const SAMPLE_LINES = [
  '进入香港这个场景时，我们有一位主持人两次激动得口误，把驻港部队说成"戒严部队"，领导就叮嘱说，你可要好好练，如果直播时来这么一个口误，那不光你完蛋了，我们整个团队都完蛋了。万幸',
  '，直播时没出错。  还有一位记者，在香港特首府邸做直播，按照既定程序，英国派驻的最后一位特首彭定康会定时定点离开特首府，结果，彭定康的车开出特首府后并没有马上离开军港，而是沿着',
  '特首府转了一圈又一圈，三圈之后才离开了。',
];

const EXPECTED = SAMPLE_LINES.join('');

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

const viteCmd = 'npx vite --host 127.0.0.1 --port 5174';
const vite = spawn(viteCmd, {
  cwd: projectDir,
  stdio: ['ignore', 'pipe', 'pipe'],
  env: { ...process.env, FORCE_COLOR: '0' },
  shell: true,
});

let browser;

try {
  await waitForHttp('http://127.0.0.1:5174', 90_000);

  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();

  await page.addInitScript(() => {
    const noop = () => true;
    let emitChat = null;
    // @ts-ignore
    window.api = {
      loadConfig: async () => null,
      saveConfig: async () => true,
      connect: async () => ({ success: true }),
      disconnect: async () => true,
      isConnected: async () => true,
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
      onChatMessage: (cb) => {
        emitChat = cb;
        return noop;
      },
      onRoomUpdate: (cb) => {
        cb(['default'], 'default');
        return noop;
      },
      onUserUpdate: () => noop,
      onConnectionStatus: (cb) => {
        cb('connected');
        return noop;
      },
      onError: () => noop,
    };
    // @ts-ignore
    window.__emitChat = (message) => emitChat?.(message);
  });

  await page.goto('http://127.0.0.1:5174', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(800);

  const cancelBtn = page.locator('button:has-text("Cancel")').first();
  if (await cancelBtn.isVisible().catch(() => false)) {
    await cancelBtn.click();
  }

  await page.waitForSelector('.chat-messages', { timeout: 20_000 });

  for (let i = 0; i < SAMPLE_LINES.length; i += 1) {
    await page.evaluate(
      ({ line, index }) => {
        // @ts-ignore
        window.__emitChat({
          id: `lib_line_${index}`,
          room: 'default',
          sender: '*',
          content: line,
          timestamp: Date.now(),
          type: 'system',
        });
      },
      { line: SAMPLE_LINES[i], index: i },
    );
  }

  await page.waitForSelector('.message-text.system', { timeout: 10_000 });

  const readVisibleText = () =>
    page.evaluate(() =>
      Array.from(document.querySelectorAll('.message-text.system'))
        .map((el) => el.textContent || '')
        .join(''),
    );

  const before = await readVisibleText();
  if (!before.includes('直播时没出错') || !before.includes('香港特首府邸做直播')) {
    throw new Error(`Baseline text missing before resize: ${before.slice(-80)}`);
  }

  await page.setViewportSize({ width: 720, height: 640 });
  await page.waitForTimeout(200);
  await page.locator('.chat-splitter').hover();
  const splitBox = await page.locator('.chat-splitter').boundingBox();
  if (splitBox) {
    await page.mouse.move(splitBox.x + splitBox.width / 2, splitBox.y + splitBox.height / 2);
    await page.mouse.down();
    await page.mouse.move(splitBox.x + splitBox.width / 2, splitBox.y + splitBox.height / 2 + 80);
    await page.mouse.up();
  }
  await page.waitForTimeout(250);

  await page.setViewportSize({ width: 520, height: 520 });
  await page.waitForTimeout(250);
  await page.setViewportSize({ width: 960, height: 760 });
  await page.waitForTimeout(250);

  const after = await readVisibleText();
  if (after !== EXPECTED) {
    throw new Error(
      `Text changed after resize.\nExpected length ${EXPECTED.length}, got ${after.length}.\nMissing snippet: ${EXPECTED.replace(after, '').slice(0, 120)}`,
    );
  }

  console.log('QA PASS: chat text survives viewport and splitter resize.');
} finally {
  if (browser) {
    await browser.close().catch(() => {});
  }
  vite.kill('SIGTERM');
}
