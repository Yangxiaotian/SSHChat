#!/usr/bin/env node
/**
 * Smoke test for secure-link invite collapsing (no vitest in this package).
 */
const assert = require('assert');

// Minimal copy of grouping helpers for CI without building the renderer.
const BANNER_START =
  /^(=+\s*)?(共享画布|文件上传信息|收到新文件|Shared\s+canvas|File\s+upload|New\s+file)/i;
const BANNER_END = /^=+/;
const URL_LABEL = /(画布网址|上传网址|下载网址|Canvas\s*URL|Upload\s*URL|Download\s*URL|网址)\s*:?\s*$/i;
const KEY_LINE =
  /^(?:访问密钥|上传密钥|下载密钥|Access\s*key|Upload\s*key|Download\s*key|密钥)\s*[:：]\s*([A-Z0-9]{6})\s*$/i;
const HTTP_URL = /^(https?:\/\/\S+)\s*$/i;
const GUI_OPEN =
  /^gui-open\s+(canvas|upload|download)\s+(https?:\/\/\S+)\s+([A-Z0-9]{6})\s*$/i;

function parse(lines) {
  for (const line of lines) {
    const gui = GUI_OPEN.exec(line);
    if (gui) {
      return { kind: gui[1].toLowerCase(), url: gui[2], key: gui[3].toUpperCase() };
    }
  }
  let url = '';
  let key = '';
  let expectUrl = false;
  for (const line of lines) {
    if (URL_LABEL.test(line)) {
      expectUrl = true;
      continue;
    }
    const keyMatch = KEY_LINE.exec(line);
    if (keyMatch) {
      key = keyMatch[1].toUpperCase();
      continue;
    }
    const urlMatch = HTTP_URL.exec(line);
    if (urlMatch && (expectUrl || !url)) {
      url = urlMatch[1];
      expectUrl = false;
    }
  }
  if (!url || !key) return null;
  return { kind: 'canvas', url, key };
}

const block = [
  '========== 共享画布 ==========',
  '发起人: alice',
  '画布网址:',
  'https://example.com/canvas/tok',
  '访问密钥: ABC123',
  '=====================================',
  'gui-open canvas https://example.com/canvas/tok ABC123',
];
assert.ok(BANNER_START.test(block[0]));
assert.ok(BANNER_END.test(block[5]));
const parsed = parse(block);
assert.strictEqual(parsed.url, 'https://example.com/canvas/tok');
assert.strictEqual(parsed.key, 'ABC123');
assert.strictEqual(parsed.kind, 'canvas');
console.log('secure-links smoke ok');
