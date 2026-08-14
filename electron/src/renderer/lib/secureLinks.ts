/**
 * Detect multi-line file/canvas invite system messages and collapse them
 * into a single actionable card for the Electron GUI.
 *
 * Terminal clients still see the raw URL + key lines from the server.
 */

import type { ChatMessage } from '../../shared/protocol';

export type SecureLinkKind = 'canvas' | 'upload' | 'download';

export type SecureLinkPayload = {
  kind: SecureLinkKind;
  url: string;
  key: string;
  title?: string;
  subtitle?: string;
};

export type TimelineItem =
  | { type: 'message'; message: ChatMessage }
  | { type: 'secure-link'; id: string; payload: SecureLinkPayload; messages: ChatMessage[] };

const BANNER_START =
  /^(=+\s*)?(共享画布|文件上传信息|收到新文件|Shared\s+canvas|File\s+upload|New\s+file)/i;
const BANNER_END = /^=+/;
const URL_LABEL = /(画布网址|上传网址|下载网址|Canvas\s*URL|Upload\s*URL|Download\s*URL|网址)\s*:?\s*$/i;
const KEY_LINE =
  /^(?:访问密钥|上传密钥|下载密钥|Access\s*key|Upload\s*key|Download\s*key|密钥)\s*[:：]\s*([A-Z0-9]{6})\s*$/i;
const HTTP_URL = /^(https?:\/\/\S+)\s*$/i;
const GUI_OPEN =
  /^gui-open\s+(canvas|upload|download)\s+(https?:\/\/\S+)\s+([A-Z0-9]{6})\s*$/i;

function kindFromBanner(text: string): SecureLinkKind | null {
  const t = text.toLowerCase();
  if (t.includes('画布') || t.includes('canvas')) return 'canvas';
  if (t.includes('上传') || t.includes('upload')) return 'upload';
  if (t.includes('收到新文件') || t.includes('new file') || t.includes('download')) {
    return 'download';
  }
  return null;
}

function extractMeta(lines: string[], kind: SecureLinkKind): Partial<SecureLinkPayload> {
  const meta: Partial<SecureLinkPayload> = { kind };
  for (const line of lines) {
    const sender = line.match(/^(?:发起人|发件人|From|Sender)\s*[:：]\s*(.+)$/i);
    if (sender) meta.subtitle = sender[1].trim();
    const filename = line.match(/^(?:文件名|Filename)\s*[:：]\s*(.+)$/i);
    if (filename) meta.title = filename[1].trim();
    const room = line.match(/^(?:范围|来自房间|Room)\s*[:：]\s*(.+)$/i);
    if (room && !meta.subtitle) meta.subtitle = room[1].trim();
  }
  return meta;
}

function parseBlock(messages: ChatMessage[]): SecureLinkPayload | null {
  const lines = messages.map((m) => m.content.trim()).filter(Boolean);

  // Prefer explicit machine helper line if present.
  for (const line of lines) {
    const gui = GUI_OPEN.exec(line);
    if (gui) {
      const kind = gui[1].toLowerCase() as SecureLinkKind;
      return {
        kind,
        url: gui[2],
        key: gui[3].toUpperCase(),
        ...extractMeta(lines, kind),
      };
    }
  }

  if (!lines.length) return null;
  const kind = kindFromBanner(lines[0]) || kindFromBanner(lines.join('\n'));
  if (!kind) return null;

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
  return {
    kind,
    url,
    key,
    ...extractMeta(lines, kind),
  };
}

function isInviteNoise(content: string): boolean {
  const t = content.trim();
  if (!t) return true;
  if (BANNER_START.test(t) || BANNER_END.test(t)) return true;
  if (URL_LABEL.test(t) || KEY_LINE.test(t) || HTTP_URL.test(t)) return true;
  if (GUI_OPEN.test(t)) return true;
  if (/^(说明|Instructions?)\s*:?\s*$/i.test(t)) return true;
  if (/^\d+\.\s+/.test(t)) return true;
  if (/^(发起人|发件人|文件名|大小|范围|来自房间|标题|接收者|From|Sender|Filename|Size|Room|Recipients?)\s*[:：]/i.test(t)) {
    return true;
  }
  if (/^经联邦节点/.test(t)) return true;
  if (/图形客户端会折叠/.test(t)) return true;
  return false;
}

/**
 * Collapse consecutive system invite lines into secure-link cards.
 * Non-invite messages pass through unchanged.
 */
export function groupSecureLinkMessages(messages: ChatMessage[]): TimelineItem[] {
  const out: TimelineItem[] = [];
  let i = 0;
  while (i < messages.length) {
    const msg = messages[i];
    if (msg.type !== 'system') {
      out.push({ type: 'message', message: msg });
      i += 1;
      continue;
    }

    const text = msg.content.trim();
    const guiAlone = GUI_OPEN.exec(text);
    if (guiAlone) {
      const kind = guiAlone[1].toLowerCase() as SecureLinkKind;
      out.push({
        type: 'secure-link',
        id: `secure_${msg.id}`,
        payload: {
          kind,
          url: guiAlone[2],
          key: guiAlone[3].toUpperCase(),
        },
        messages: [msg],
      });
      i += 1;
      continue;
    }

    if (!BANNER_START.test(text)) {
      out.push({ type: 'message', message: msg });
      i += 1;
      continue;
    }

    const block: ChatMessage[] = [msg];
    let j = i + 1;
    while (j < messages.length) {
      const next = messages[j];
      if (next.type !== 'system') break;
      // Stop if a new banner starts after we already have an end marker.
      const nt = next.content.trim();
      if (block.length > 1 && BANNER_START.test(nt) && !BANNER_END.test(nt)) break;
      if (!isInviteNoise(nt) && !BANNER_START.test(nt) && !BANNER_END.test(nt)) {
        // Unrelated system line — end block before it.
        break;
      }
      block.push(next);
      j += 1;
      if (BANNER_END.test(nt) && block.length > 2) {
        // Include a trailing gui-open helper line if the server sent one.
        if (j < messages.length && messages[j].type === 'system') {
          const trailing = messages[j].content.trim();
          if (GUI_OPEN.test(trailing)) {
            block.push(messages[j]);
            j += 1;
          }
        }
        break;
      }
    }

    const payload = parseBlock(block);
    if (payload) {
      out.push({
        type: 'secure-link',
        id: `secure_${block[0].id}`,
        payload,
        messages: block,
      });
    } else {
      for (const m of block) out.push({ type: 'message', message: m });
    }
    i = j;
  }
  return out;
}

export function defaultSecureLinkTitle(kind: SecureLinkKind, locale: 'en' | 'zh'): string {
  if (locale === 'zh') {
    if (kind === 'canvas') return '共享画布';
    if (kind === 'upload') return '上传文件';
    return '收到文件';
  }
  if (kind === 'canvas') return 'Shared canvas';
  if (kind === 'upload') return 'Upload file';
  return 'Incoming file';
}

export function defaultSecureLinkAction(kind: SecureLinkKind, locale: 'en' | 'zh'): string {
  if (locale === 'zh') {
    if (kind === 'canvas') return '打开画布';
    if (kind === 'upload') return '去上传';
    return '打开文件';
  }
  if (kind === 'canvas') return 'Open canvas';
  if (kind === 'upload') return 'Upload';
  return 'Open file';
}
