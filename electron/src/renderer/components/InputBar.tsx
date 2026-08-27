import React, { useState, useRef, useEffect, useMemo } from 'react';
import { useChatStore } from '../store/chatStore';
import { SHAKE_TOKEN } from '../../shared/protocol';
import { useTranslation } from '../i18n';
import {
  clearPasteUpload,
  extractFileFromDataTransfer,
  getPasteUploadState,
  startPasteSendFile,
  subscribePasteUpload,
  type PasteUploadState,
} from '../lib/pasteUpload';

const COMMAND_KEYS = [
  { name: '/help', key: 'input.commands.help' },
  { name: '/names', key: 'input.commands.names' },
  { name: '/rooms', key: 'input.commands.rooms' },
  { name: '/join', key: 'input.commands.join' },
  { name: '/switch', key: 'input.commands.switch' },
  { name: '/part', key: 'input.commands.part' },
  { name: '/msg', key: 'input.commands.msg' },
  { name: '/sendfile', key: 'input.commands.sendfile' },
  { name: '/file', key: 'input.commands.sendfile' },
  { name: '/canvas', key: 'input.commands.canvas' },
  { name: '/board', key: 'input.commands.canvas' },
  { name: '/leave', key: 'input.commands.leave' },
  { name: '/clear', key: 'input.commands.clear' },
  { name: '/game', key: 'input.commands.game' },
  { name: '/news', key: 'input.commands.news' },
  { name: '/library', key: 'input.commands.library' },
  { name: '/lib', key: 'input.commands.library' },
  { name: '/lang', key: 'input.commands.lang' },
  { name: '/dict', key: 'input.commands.dict' },
  { name: '/announce', key: 'input.commands.announce' },
] as const;

type SuggestionItem = { value: string; desc: string; source: 'command' | 'history' };

function longestCommonPrefix(values: string[]): string {
  if (!values.length) return '';
  let prefix = values[0];
  for (const value of values.slice(1)) {
    while (!value.startsWith(prefix)) {
      prefix = prefix.slice(0, -1);
      if (!prefix) return '';
    }
  }
  return prefix;
}

const GAME_UNDO_ACTIONS = ['accept', 'reject', 'cancel'] as const;
const ROOM_ARG_CMDS = new Set(['/join', '/switch', '/part']);
const USER_OR_ROOM_ARG_CMDS = new Set(['/msg', '/sendfile', '/file']);
const USER_ARG_CMDS = new Set(['/leave', '/unmsg']);

function uniqKeepOrder(items: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of items) {
    const key = raw.trim();
    if (!key) continue;
    const low = key.toLowerCase();
    if (seen.has(low)) continue;
    seen.add(low);
    out.push(key);
  }
  return out;
}

function nameArgSuggestions(
  value: string,
  rooms: string[],
  users: string[],
): SuggestionItem[] {
  if (!value.startsWith('/')) return [];
  const trailingSpace = value.endsWith(' ');
  const parts = value.trimEnd().split(/\s+/).filter(Boolean);
  if (!parts.length) return [];
  const cmd = parts[0].toLowerCase();
  const roomNames = uniqKeepOrder(rooms.map((r) => r.replace(/^#/, '')));
  const userNames = uniqKeepOrder(users);

  let cands: string[] = [];
  let desc = '';
  if (ROOM_ARG_CMDS.has(cmd)) {
    cands = roomNames;
    desc = 'room';
  } else if (USER_OR_ROOM_ARG_CMDS.has(cmd)) {
    cands = [...userNames, ...roomNames.map((r) => `#${r}`)];
    desc = 'user / #room';
  } else if (USER_ARG_CMDS.has(cmd)) {
    cands = userNames;
    desc = 'user';
  } else {
    return [];
  }

  let matched = cands;
  if (trailingSpace && parts.length === 1) {
    // all candidates
  } else if (parts.length >= 2 && !trailingSpace) {
    const prefix = parts[1];
    const pl = prefix.toLowerCase();
    const bare = pl.replace(/^#/, '');
    matched = cands.filter((c) => {
      const cl = c.toLowerCase();
      if (pl === '#') return c.startsWith('#');
      if (cl.startsWith(pl)) return true;
      if (c.startsWith('#') && c.slice(1).toLowerCase().startsWith(bare)) return true;
      if (!c.startsWith('#') && cl.startsWith(bare) && prefix.startsWith('#')) return true;
      return false;
    });
  } else {
    return [];
  }

  return matched.map((c) => ({
    value: `${parts[0]} ${c}`,
    desc,
    source: 'command' as const,
  }));
}

function buildSuggestions(
  value: string,
  commands: { name: string; desc: string }[],
  history: string[],
  recentLabel: string,
  rooms: string[],
  users: string[],
): SuggestionItem[] {
  const gameUndoPrefix = '/game undo';
  if (value.toLowerCase().startsWith(gameUndoPrefix)) {
    const tail = value.slice(gameUndoPrefix.length).trimStart().toLowerCase();
    return GAME_UNDO_ACTIONS.filter(
      (action) => !tail || action.startsWith(tail) || (tail.length >= 2 && action.startsWith(tail)),
    ).map((action) => ({
      value: `${gameUndoPrefix} ${action}`,
      desc: 'undo action',
      source: 'command' as const,
    }));
  }

  if (value.startsWith('/') && !value.includes(' ')) {
    const lower = value.toLowerCase();
    return commands
      .filter((cmd) => cmd.name.startsWith(lower))
      .map((cmd) => ({
        value: cmd.name,
        desc: cmd.desc,
        source: 'command' as const,
      }));
  }

  const nameItems = nameArgSuggestions(value, rooms, users);
  if (nameItems.length) return nameItems;

  const keyword = value.trim().toLowerCase();
  if (!keyword) return [];
  return history
    .filter((item) => item.toLowerCase().includes(keyword))
    .slice(0, 10)
    .map((item) => ({
      value: item,
      desc: recentLabel,
      source: 'history' as const,
    }));
}

export default function InputBar() {
  const [suggestions, setSuggestions] = useState<SuggestionItem[]>([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [activeSuggestion, setActiveSuggestion] = useState(0);
  const [history, setHistory] = useState<string[]>([]);
  const [isSending, setIsSending] = useState(false);
  const [pasteState, setPasteState] = useState<PasteUploadState>(() => ({
    status: { phase: 'idle' },
    busy: false,
  }));
  const inputRef = useRef<HTMLInputElement>(null);
  const sendingRef = useRef(false);
  const { status, activeRoom, composerText, setComposerText, clearMessages, rooms, users } = useChatStore();
  const { t } = useTranslation();
  const HISTORY_KEY = 'sshchat:input-history:v1';

  useEffect(() => subscribePasteUpload(setPasteState), []);

  // Global paste: screenshot/file in clipboard → auto /sendfile + upload.
  // Text-only pastes are left alone for the composer.
  useEffect(() => {
    const onWindowPaste = (e: ClipboardEvent) => {
      if (status !== 'connected') return;
      if (getPasteUploadState().busy) return;
      const file = extractFileFromDataTransfer(e.clipboardData);
      if (!file) return;
      e.preventDefault();
      void startPasteSendFile(file, useChatStore.getState().activeRoom);
    };
    window.addEventListener('paste', onWindowPaste);
    return () => window.removeEventListener('paste', onWindowPaste);
  }, [status]);

  const commands = useMemo(
    () => COMMAND_KEYS.map((cmd) => ({ name: cmd.name, desc: t(cmd.key) })),
    [t],
  );

  const roomNames = useMemo(
    () => uniqKeepOrder([activeRoom, ...rooms.map((r) => r.name)].filter(Boolean)),
    [activeRoom, rooms],
  );

  useEffect(() => {
    try {
      const raw = localStorage.getItem(HISTORY_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) {
        setHistory(parsed.filter((item): item is string => typeof item === 'string').slice(0, 10));
      }
    } catch {
      // no-op
    }
  }, []);

  const persistHistory = (next: string[]) => {
    setHistory(next);
    try {
      localStorage.setItem(HISTORY_KEY, JSON.stringify(next));
    } catch {
      // no-op
    }
  };

  const refreshSuggestions = (value: string) => {
    const merged = buildSuggestions(
      value,
      commands,
      history,
      t('common.recentInput'),
      roomNames,
      users,
    ).slice(0, 10);
    setSuggestions(merged);
    setActiveSuggestion(0);
    const isTopLevelCommand = value.startsWith('/') && !value.includes(' ');
    const isGameUndoCommand = value.toLowerCase().startsWith('/game undo');
    const isNameArgCommand = nameArgSuggestions(value, roomNames, users).length > 0;
    setShowSuggestions(
      isTopLevelCommand || isGameUndoCommand || isNameArgCommand
        ? merged.length > 0
        : merged.length > 0 && value.trim().length > 0,
    );
    return merged;
  };

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value;
    setComposerText(value);
    refreshSuggestions(value);
  };

  const applySuggestion = (item: SuggestionItem) => {
    setComposerText(item.source === 'command' ? `${item.value} ` : item.value);
    setShowSuggestions(false);
    inputRef.current?.focus();
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    const value = e.currentTarget.value;

    const canTabComplete =
      (value.startsWith('/') && !value.includes(' ')) ||
      value.toLowerCase().startsWith('/game undo') ||
      nameArgSuggestions(value, roomNames, users).length > 0;

    if (e.key === 'Tab' && canTabComplete) {
      e.preventDefault();
      const items = refreshSuggestions(value);
      const cmdItems = items.filter((item) => item.source === 'command');
      if (!cmdItems.length) return;

      if (cmdItems.length === 1) {
        applySuggestion(cmdItems[0]);
        return;
      }

      const names = cmdItems.map((item) => item.value);
      const shared = longestCommonPrefix(names);
      if (shared.length > value.length) {
        setComposerText(shared);
        refreshSuggestions(shared);
        return;
      }

      if (cmdItems.length > 1 && value === '/') {
        setShowSuggestions(true);
        return;
      }

      const picked = cmdItems[activeSuggestion] ?? cmdItems[0];
      applySuggestion(picked);
      return;
    }

    if (e.key === 'ArrowDown' && showSuggestions && suggestions.length > 0) {
      e.preventDefault();
      setActiveSuggestion((prev) => (prev + 1) % suggestions.length);
      return;
    }
    if (e.key === 'ArrowUp' && showSuggestions && suggestions.length > 0) {
      e.preventDefault();
      setActiveSuggestion((prev) => (prev - 1 + suggestions.length) % suggestions.length);
      return;
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
    if (e.key === 'Escape') {
      setShowSuggestions(false);
    }
  };

  const handleSend = async () => {
    if (sendingRef.current) return;
    const trimmed = composerText.trim();
    if (!trimmed) return;

    if (/^\/(?:clear|cls)$/i.test(trimmed)) {
      clearMessages();
      setComposerText('');
      setShowSuggestions(false);
      return;
    }

    sendingRef.current = true;
    setIsSending(true);
    try {
      const ok = await window.api.sendMessage(trimmed);
      if (!ok) return;
      const next = [trimmed, ...history.filter((item) => item !== trimmed)].slice(0, 10);
      persistHistory(next);
      setComposerText('');
      setShowSuggestions(false);
    } finally {
      sendingRef.current = false;
      setIsSending(false);
    }
  };

  const handleSuggestionClick = (value: string, source: 'command' | 'history') => {
    applySuggestion({ value, desc: '', source });
  };

  const isConnected = status === 'connected';
  const triggerRoomShake = async () => {
    if (!isConnected) return;
    await window.api.sendMessage(SHAKE_TOKEN);
  };

  const sendQuickCommand = async (command: string) => {
    if (!isConnected || sendingRef.current) return;
    sendingRef.current = true;
    setIsSending(true);
    try {
      await window.api.sendMessage(command);
    } finally {
      sendingRef.current = false;
      setIsSending(false);
    }
  };

  const clearComposer = () => {
    setComposerText('');
    setShowSuggestions(false);
    inputRef.current?.focus();
  };

  const pasteBannerText = (() => {
    const s = pasteState.status;
    if (s.phase === 'waiting') return t('pasteUpload.waiting', { name: s.filename });
    if (s.phase === 'uploading') return t('pasteUpload.uploading', { name: s.filename });
    if (s.phase === 'done') {
      return t('pasteUpload.done', { name: s.remoteName || s.filename });
    }
    if (s.phase === 'error') {
      if (s.error === 'busy') return t('pasteUpload.busy');
      if (s.error === 'timeout') return t('pasteUpload.timeout');
      if (s.error === 'send_failed') return t('pasteUpload.sendFailed');
      return t('pasteUpload.error', { error: s.error });
    }
    return '';
  })();

  return (
    <div className="input-bar">
      {pasteBannerText ? (
        <div className={`paste-upload-banner phase-${pasteState.status.phase}`}>
          <span>{pasteBannerText}</span>
          {(pasteState.status.phase === 'error' || pasteState.status.phase === 'done') && (
            <button type="button" className="paste-upload-dismiss" onClick={() => clearPasteUpload()}>
              {t('pasteUpload.dismiss')}
            </button>
          )}
        </div>
      ) : null}
      <div className="input-quick-actions">
        <button
          type="button"
          className="quick-action-btn"
          disabled={!isConnected || isSending || pasteState.busy}
          title={t('input.quick.fileTitle')}
          onClick={() => void sendQuickCommand('/sendfile')}
        >
          {t('input.quick.file')}
        </button>
        <button
          type="button"
          className="quick-action-btn"
          disabled={!isConnected || isSending}
          title={t('input.quick.canvasTitle')}
          onClick={() => void sendQuickCommand('/canvas')}
        >
          {t('input.quick.canvas')}
        </button>
        <span className="quick-action-hint">{t('input.quick.pasteHint')}</span>
      </div>
      <div className="input-wrapper" style={{ position: 'relative' }}>
        {showSuggestions && (
          <div className="command-suggestions">
            {suggestions.map((cmd, index) => (
              <div
                key={`${cmd.source}:${cmd.value}`}
                className={`command-item${index === activeSuggestion ? ' active' : ''}`}
                onClick={() => handleSuggestionClick(cmd.value, cmd.source)}
              >
                <span className="command-name">{cmd.value}</span>
                <span className="command-desc">{cmd.desc}</span>
              </div>
            ))}
          </div>
        )}
        <input
          ref={inputRef}
          type="text"
          className="input-field"
          value={composerText}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          placeholder={isConnected ? t('input.placeholder', { room: activeRoom }) : t('input.notConnected')}
          disabled={!isConnected || isSending}
        />
        <button
          className="send-button"
          onClick={triggerRoomShake}
          disabled={!isConnected}
          title={t('input.shakeTitle')}
        >
          {t('common.shake')}
        </button>
        <button
          className="send-button secondary-button"
          onClick={clearComposer}
          disabled={!composerText}
          title="清空当前输入框，不清聊天记录"
        >
          清空输入
        </button>
        <button
          className="send-button"
          onClick={handleSend}
          disabled={!isConnected || isSending || !composerText.trim()}
        >
          {t('common.send')}
        </button>
      </div>
    </div>
  );
}
