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
  { name: '/clear', key: 'input.commands.clear' },
  { name: '/game', key: 'input.commands.game' },
  { name: '/news', key: 'input.commands.news' },
  { name: '/library', key: 'input.commands.library' },
  { name: '/dict', key: 'input.commands.dict' },
  { name: '/announce', key: 'input.commands.announce' },
] as const;

export default function InputBar() {
  type SuggestionItem = { value: string; desc: string; source: 'command' | 'history' };
  const [suggestions, setSuggestions] = useState<SuggestionItem[]>([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
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

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value;
    setComposerText(value);
    const keyword = value.trim().toLowerCase();
    const commandMatches =
      value.startsWith('/') && !value.includes(' ')
        ? commands.filter((cmd) => cmd.name.startsWith(value.toLowerCase())).map((cmd) => ({
            value: cmd.name,
            desc: cmd.desc,
            source: 'command' as const,
          }))
        : [];
    const historyMatches =
      keyword.length > 0
        ? history
            .filter((item) => item.toLowerCase().includes(keyword))
            .slice(0, 10)
            .map((item) => ({
              value: item,
              desc: t('common.recentInput'),
              source: 'history' as const,
            }))
        : [];
    const merged: SuggestionItem[] = [...commandMatches];
    for (const item of historyMatches) {
      if (!merged.find((v) => v.value === item.value)) {
        merged.push(item);
      }
    }
    setSuggestions(merged.slice(0, 10));
    setShowSuggestions(merged.length > 0 && keyword.length > 0);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
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
    setComposerText(source === 'command' ? `${value} ` : value);
    setShowSuggestions(false);
    inputRef.current?.focus();
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
            {suggestions.map((cmd) => (
              <div
                key={`${cmd.source}:${cmd.value}`}
                className="command-item"
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
