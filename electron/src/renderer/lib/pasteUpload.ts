/**
 * Paste/drop a file in the Electron chat → /sendfile → auto-upload.
 * The binary upload runs in the main process (no CORS against the file host).
 */

export type PasteUploadStatus =
  | { phase: 'idle' }
  | { phase: 'waiting'; filename: string; startedAt: number }
  | { phase: 'uploading'; filename: string; startedAt: number }
  | { phase: 'done'; filename: string; remoteName?: string }
  | { phase: 'error'; filename: string; error: string };

type PendingUpload = {
  file: File;
  filename: string;
  room: string;
  startedAt: number;
  consumed: boolean;
};

const GUI_OPEN_UPLOAD =
  /^gui-open\s+upload\s+(https?:\/\/\S+)\s+([A-Z0-9]{6})\s*$/i;

const INVITE_TIMEOUT_MS = 45_000;

let pending: PendingUpload | null = null;
let status: PasteUploadStatus = { phase: 'idle' };
let timeoutId: ReturnType<typeof setTimeout> | null = null;
const listeners = new Set<(s: PasteUploadState) => void>();

export type PasteUploadState = {
  status: PasteUploadStatus;
  busy: boolean;
};

function emit() {
  const snapshot: PasteUploadState = {
    status,
    busy: status.phase === 'waiting' || status.phase === 'uploading',
  };
  for (const fn of listeners) {
    try {
      fn(snapshot);
    } catch {
      // ignore subscriber errors
    }
  }
}

function setStatus(next: PasteUploadStatus) {
  status = next;
  emit();
}

function clearTimer() {
  if (timeoutId) {
    clearTimeout(timeoutId);
    timeoutId = null;
  }
}

function renameClipboardFile(file: File): File {
  const rawName = (file.name || '').trim();
  const looksGeneric =
    !rawName
    || rawName === 'image.png'
    || rawName === 'image.jpg'
    || rawName === 'image.jpeg'
    || rawName === 'blob';
  if (!looksGeneric) return file;
  const ext = (file.type && file.type.split('/')[1]) || 'png';
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  return new File([file], `clipboard-${stamp}.${ext}`, {
    type: file.type || 'application/octet-stream',
    lastModified: Date.now(),
  });
}

/** Pull the first file/image from a paste or drop DataTransfer. */
export function extractFileFromDataTransfer(dt: DataTransfer | null): File | null {
  if (!dt) return null;
  if (dt.files && dt.files.length > 0) {
    return renameClipboardFile(dt.files[0]);
  }
  if (dt.items) {
    for (const item of Array.from(dt.items)) {
      if (item.kind === 'file') {
        const file = item.getAsFile();
        if (file) return renameClipboardFile(file);
      }
    }
  }
  return null;
}

export function getPasteUploadState(): PasteUploadState {
  return {
    status,
    busy: status.phase === 'waiting' || status.phase === 'uploading',
  };
}

export function subscribePasteUpload(fn: (s: PasteUploadState) => void): () => void {
  listeners.add(fn);
  fn(getPasteUploadState());
  return () => listeners.delete(fn);
}

export function clearPasteUpload() {
  clearTimer();
  pending = null;
  setStatus({ phase: 'idle' });
}

async function performUpload(url: string, key: string, file: File) {
  setStatus({ phase: 'uploading', filename: file.name, startedAt: pending?.startedAt || Date.now() });
  try {
    const buffer = await file.arrayBuffer();
    const result = await window.api.uploadSecureFile({
      url,
      key,
      filename: file.name,
      mime: file.type || 'application/octet-stream',
      data: buffer,
    });
    if (!result?.ok) {
      throw new Error(result?.error || 'upload failed');
    }
    setStatus({
      phase: 'done',
      filename: file.name,
      remoteName: result.filename || file.name,
    });
    pending = null;
    clearTimer();
    // Auto-clear success banner after a moment.
    setTimeout(() => {
      if (status.phase === 'done') clearPasteUpload();
    }, 5000);
  } catch (e) {
    const message = e instanceof Error ? e.message : String(e);
    setStatus({ phase: 'error', filename: file.name, error: message });
    pending = null;
    clearTimer();
  }
}

/**
 * If a system line is an upload invite and we have a pending paste, upload it.
 * Returns true when the line was consumed as an auto-upload trigger.
 */
export function tryHandleUploadInviteLine(content: string): boolean {
  const text = String(content || '').trim();
  if (pending && !pending.consumed) {
    if (
      /没有其他用户|文件传输功能未启用|创建文件传输失败|File transfer is disabled|no other users/i.test(
        text,
      )
    ) {
      clearTimer();
      setStatus({
        phase: 'error',
        filename: pending.filename,
        error: text.replace(/^\[?\*?\]?\s*/, '').slice(0, 120) || 'send_failed',
      });
      pending = null;
      return true;
    }
  }
  if (!pending || pending.consumed) return false;
  const match = GUI_OPEN_UPLOAD.exec(text);
  if (!match) return false;
  pending.consumed = true;
  clearTimer();
  const url = match[1];
  const key = match[2].toUpperCase();
  const file = pending.file;
  void performUpload(url, key, file);
  return true;
}

/** Start /sendfile and wait for the matching gui-open upload invite. */
export async function startPasteSendFile(file: File, room: string): Promise<boolean> {
  if (getPasteUploadState().busy) {
    return false;
  }
  if (!window.api?.sendMessage) return false;

  const named = renameClipboardFile(file);
  clearTimer();
  pending = {
    file: named,
    filename: named.name,
    room,
    startedAt: Date.now(),
    consumed: false,
  };
  setStatus({ phase: 'waiting', filename: named.name, startedAt: Date.now() });

  timeoutId = setTimeout(() => {
    if (pending && !pending.consumed) {
      setStatus({
        phase: 'error',
        filename: pending.filename,
        error: 'timeout',
      });
      pending = null;
    }
  }, INVITE_TIMEOUT_MS);

  const ok = await window.api.sendMessage('/sendfile');
  if (!ok) {
    clearTimer();
    pending = null;
    setStatus({
      phase: 'error',
      filename: named.name,
      error: 'send_failed',
    });
    return false;
  }
  return true;
}
