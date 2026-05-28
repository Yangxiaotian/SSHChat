import React, { useState, useRef, useEffect } from 'react';
import { useChatStore } from '../store/chatStore';
import { SHAKE_TOKEN } from '../../shared/protocol';

const COMMANDS = [
  { name: '/help', desc: 'Show help' },
  { name: '/names', desc: 'List users in room' },
  { name: '/rooms', desc: 'List joined rooms' },
  { name: '/join', desc: 'Join a room' },
  { name: '/switch', desc: 'Switch active room' },
  { name: '/part', desc: 'Leave a room' },
  { name: '/msg', desc: 'Send private message' },
  { name: '/clear', desc: 'Clear local view (current room)' },
  { name: '/game', desc: 'Game commands' },
  { name: '/news', desc: 'Show news' },
  { name: '/announce', desc: 'Room announcement' },
];

export default function InputBar() {
  type SuggestionItem = { value: string; desc: string; source: 'command' | 'history' };
  const [suggestions, setSuggestions] = useState<SuggestionItem[]>([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [history, setHistory] = useState<string[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const { status, activeRoom, composerText, setComposerText } = useChatStore();
  const HISTORY_KEY = 'sshchat:input-history:v1';

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
  const { clearMessages } = useChatStore();

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value;
    setComposerText(value);
    const keyword = value.trim().toLowerCase();
    const commandMatches =
      value.startsWith('/') && !value.includes(' ')
        ? COMMANDS.filter((cmd) => cmd.name.startsWith(value.toLowerCase())).map((cmd) => ({
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
              desc: 'Recent input',
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
    const trimmed = composerText.trim();
    if (!trimmed) return;

    if (/^\/(?:clear|cls)$/i.test(trimmed)) {
      clearMessages();
      setComposerText('');
      setShowSuggestions(false);
      return;
    }

    await window.api.sendMessage(trimmed);
    const next = [trimmed, ...history.filter((item) => item !== trimmed)].slice(0, 10);
    persistHistory(next);
    setComposerText('');
    setShowSuggestions(false);
  };

  const handleSuggestionClick = (value: string, source: 'command' | 'history') => {
    setComposerText(source === 'command' ? `${value} ` : value);
    setShowSuggestions(false);
    inputRef.current?.focus();
  };

  const isConnected = status === 'connected';
  const triggerRoomShake = async () => {
    if (!isConnected) return;
    // Send shake signal to server; all clients (including sender via echo) will shake
    await window.api.sendMessage(SHAKE_TOKEN);
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
          placeholder={isConnected ? `Message #${activeRoom}...` : 'Not connected'}
          disabled={!isConnected}
        />
        <button
          className="send-button"
          onClick={triggerRoomShake}
          disabled={!isConnected}
          title="Shake all clients in this room"
        >
          Shake
        </button>
        <button
          className="send-button"
          onClick={handleSend}
          disabled={!isConnected || !composerText.trim()}
        >
          Send
        </button>
      </div>
    </div>
  );
}
