import React, { useState, useRef, useEffect, useMemo } from 'react';
import { useChatStore } from '../store/chatStore';
import { SHAKE_TOKEN } from '../../shared/protocol';
import { useTranslation } from '../i18n';

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
  { name: '/leave', key: 'input.commands.leave' },
  { name: '/clear', key: 'input.commands.clear' },
  { name: '/game', key: 'input.commands.game' },
  { name: '/news', key: 'input.commands.news' },
  { name: '/library', key: 'input.commands.library' },
  { name: '/lib', key: 'input.commands.library' },
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

function buildSuggestions(
  value: string,
  commands: { name: string; desc: string }[],
  history: string[],
  recentLabel: string,
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
  const inputRef = useRef<HTMLInputElement>(null);
  const sendingRef = useRef(false);
  const { status, activeRoom, composerText, setComposerText, clearMessages } = useChatStore();
  const { t } = useTranslation();
  const HISTORY_KEY = 'sshchat:input-history:v1';

  const commands = useMemo(
    () => COMMAND_KEYS.map((cmd) => ({ name: cmd.name, desc: t(cmd.key) })),
    [t],
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
    const merged = buildSuggestions(value, commands, history, t('common.recentInput')).slice(0, 10);
    setSuggestions(merged);
    setActiveSuggestion(0);
    const isTopLevelCommand = value.startsWith('/') && !value.includes(' ');
    const isGameUndoCommand = value.toLowerCase().startsWith('/game undo');
    setShowSuggestions(
      isTopLevelCommand || isGameUndoCommand
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
      value.toLowerCase().startsWith('/game undo');

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

  const clearComposer = () => {
    setComposerText('');
    setShowSuggestions(false);
    inputRef.current?.focus();
  };

  return (
    <div className="input-bar">
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
