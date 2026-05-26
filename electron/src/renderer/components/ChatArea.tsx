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

function isGameFloodMessage(content: string): boolean {
  const raw = content.trim();
  const t = raw.toLowerCase();
  if (!t) return false;

  if (
    /^\d+\s*,\s*\d+$/.test(t) ||
    /^\d{1,2}\s+(?:[.#o●○·]\s+){8,}[.#o●○·]\s*$/i.test(raw) ||
    /^(?:\d{1,2}\s+){8,}\d{1,2}\s*$/.test(raw) ||
    /^((row|turn|state|pot|street|current_bet)\s*[:=])/.test(t) ||
    /^#\d+\s+[^:：]+[:：]/.test(raw) ||
    /^-\s+\S+\s+\((alive|out)\)/i.test(raw)
  ) {
    return true;
  }

  const keywords = [
    '当前房间正在进行',
    '可直接加入',
    '同一房间同一时刻仅允许一场进行中的对局',
    '可玩游戏',
    '国际象棋',
    '五子棋',
    '中国象棋',
    '三国杀',
    '狼人杀',
    '德州扑克',
    '炸金花',
    '牛头王',
    '你的手牌',
    '公共牌',
    '落子',
    '底池',
    '当前注',
    '轮到',
    'gomoku',
    'chess',
    'xiangqi',
    'holdem',
    'zjh',
    'niutou',
    'sanguo',
    'werewolf',
  ];

  return keywords.some((k) => t.includes(k.toLowerCase()));
}

export default function ChatArea() {
  const { messages, activeRoom, nickname, status, privacyMode, clearMessages } = useChatStore();
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const chatScrollRef = useRef<HTMLDivElement>(null);
  const splitRootRef = useRef<HTMLDivElement>(null);
  const [stickToBottom, setStickToBottom] = useState(true);
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
    return roomMessages.filter((msg) => {
      if (msg.type === 'chat' || msg.type === 'pm') return true;
      if (msg.type === 'game') return false;
      if (msg.type === 'system') return !isGameFloodMessage(msg.content);
      return false;
    });
  }, [roomMessages]);

  useEffect(() => {
    if (!stickToBottom) return;
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [visibleMessages, stickToBottom]);

  useEffect(() => {
    setStickToBottom(true);
  }, [activeRoom]);

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

  const onChatScroll = () => {
    const el = chatScrollRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight <= 36;
    if (nearBottom !== stickToBottom) {
      setStickToBottom(nearBottom);
    }
  };

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
          <div ref={chatScrollRef} className="chat-messages" onScroll={onChatScroll}>
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
