import React, { useState, useRef } from 'react';
import { useChatStore } from '../store/chatStore';

const COMMANDS = [
  { name: '/help', desc: 'Show help' },
  { name: '/names', desc: 'List users in room' },
  { name: '/rooms', desc: 'List joined rooms' },
  { name: '/join', desc: 'Join a room' },
  { name: '/switch', desc: 'Switch active room' },
  { name: '/part', desc: 'Leave a room' },
  { name: '/msg', desc: 'Send private message' },
  { name: '/clear', desc: 'Clear screen' },
  { name: '/game', desc: 'Game commands' },
  { name: '/news', desc: 'Show news' },
  { name: '/announce', desc: 'Room announcement' },
];

export default function InputBar() {
  const [suggestions, setSuggestions] = useState<typeof COMMANDS>([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const { status, activeRoom, composerText, setComposerText } = useChatStore();

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value;
    setComposerText(value);

    // Show command suggestions
    if (value.startsWith('/') && !value.includes(' ')) {
      const filtered = COMMANDS.filter(cmd =>
        cmd.name.startsWith(value.toLowerCase())
      );
      setSuggestions(filtered);
      setShowSuggestions(filtered.length > 0 && value.length > 1);
    } else {
      setShowSuggestions(false);
    }
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

    await window.api.sendMessage(trimmed);
    setComposerText('');
    setShowSuggestions(false);
  };

  const handleSuggestionClick = (command: string) => {
    setComposerText(command + ' ');
    setShowSuggestions(false);
    inputRef.current?.focus();
  };

  const isConnected = status === 'connected';

  return (
    <div className="input-bar">
      <div className="input-wrapper" style={{ position: 'relative' }}>
        {showSuggestions && (
          <div className="command-suggestions">
            {suggestions.map((cmd) => (
              <div
                key={cmd.name}
                className="command-item"
                onClick={() => handleSuggestionClick(cmd.name)}
              >
                <span className="command-name">{cmd.name}</span>
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
          onClick={handleSend}
          disabled={!isConnected || !composerText.trim()}
        >
          Send
        </button>
      </div>
    </div>
  );
}
