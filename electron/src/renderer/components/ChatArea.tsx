import React, { useRef, useEffect, useMemo, useState } from 'react';
import { useChatStore } from '../store/chatStore';
import TabBar from './TabBar';
import InputBar from './InputBar';
import MessageBubble from './MessageBubble';
import GameWorkbench from './GameWorkbench';

const WORKBENCH_HEIGHT_KEY = 'sshchat:workbench-height:v1';
const WORKBENCH_MIN_HEIGHT = 96;
const CHAT_MIN_HEIGHT = 180;
const DEFAULT_WORKBENCH_HEIGHT = 340;

export default function ChatArea() {
  const { messages, activeRoom, nickname, status, privacyMode, clearMessages } = useChatStore();
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const splitRootRef = useRef<HTMLDivElement>(null);
  const [workbenchHeight, setWorkbenchHeight] = useState<number>(() => {
    try {
      const raw = localStorage.getItem(WORKBENCH_HEIGHT_KEY);
      const val = raw ? Number(raw) : Number.NaN;
      if (!Number.isFinite(val)) return DEFAULT_WORKBENCH_HEIGHT;
      return Math.max(WORKBENCH_MIN_HEIGHT, Math.round(val));
    } catch {
      return DEFAULT_WORKBENCH_HEIGHT;
    }
  });
  const [isResizing, setIsResizing] = useState(false);

  const roomMessages = messages.get(activeRoom) || [];
  const visibleMessages = useMemo(() => {
    return roomMessages.filter(
      (msg) => msg.type === 'chat' || msg.type === 'pm' || msg.type === 'game' || msg.type === 'system',
    );
  }, [roomMessages]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [visibleMessages]);

  useEffect(() => {
    try {
      localStorage.setItem(WORKBENCH_HEIGHT_KEY, String(workbenchHeight));
    } catch {
      // Ignore localStorage failures.
    }
  }, [workbenchHeight]);

  useEffect(() => {
    if (!isResizing) return;
    const onMove = (evt: MouseEvent) => {
      const root = splitRootRef.current;
      if (!root) return;
      const rect = root.getBoundingClientRect();
      const maxHeight = Math.max(WORKBENCH_MIN_HEIGHT, rect.height - CHAT_MIN_HEIGHT);
      const desired = evt.clientY - rect.top;
      const next = Math.max(WORKBENCH_MIN_HEIGHT, Math.min(maxHeight, Math.round(desired)));
      setWorkbenchHeight(next);
    };
    const onUp = () => setIsResizing(false);
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
  }, [isResizing]);

  return (
    <div className="chat-container">
      <TabBar />

      <div className="breadcrumb">
        <div className="breadcrumb-path">
          <span className="breadcrumb-item">
            <span>{privacyMode ? 'Workspace' : 'SSHChat'}</span>
          </span>
          <span className="breadcrumb-separator">{'>'}</span>
          <span className="breadcrumb-item">
            <span>#{activeRoom}</span>
          </span>
        </div>
        {status === 'connected' && (
          <button
            type="button"
            className="breadcrumb-clear-btn"
            title="Clear local messages in this room (not sent to server)"
            onClick={() => clearMessages()}
          >
            Clear
          </button>
        )}
      </div>

      <div ref={splitRootRef} className="chat-main-split">
        <div className="game-pane" style={{ height: `${workbenchHeight}px` }}>
          <GameWorkbench />
        </div>
        <div
          className={`chat-splitter ${isResizing ? 'active' : ''}`}
          title="Drag to resize game panel height"
          onMouseDown={() => setIsResizing(true)}
          onDoubleClick={() => setWorkbenchHeight(DEFAULT_WORKBENCH_HEIGHT)}
        >
          <span className="chat-splitter-grip">...</span>
        </div>

        <div className="chat-messages-surface">
          <div className="chat-messages">
            {visibleMessages.length === 0 ? (
              <div className="chat-empty">
                <div className="chat-empty-icon">{'</>'}</div>
                <div className="chat-empty-text">
                  {status === 'connected'
                    ? `No messages in #${activeRoom} yet`
                    : 'Connect to start session'}
                </div>
              </div>
            ) : (
              visibleMessages.map((msg) => (
                <MessageBubble key={msg.id} message={msg} isMe={msg.sender === nickname} />
              ))
            )}
            <div ref={messagesEndRef} />
          </div>
        </div>
      </div>

      <InputBar />
    </div>
  );
}
