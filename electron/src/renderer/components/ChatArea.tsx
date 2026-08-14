import React, { useRef, useEffect, useMemo, useState } from 'react';
import { useChatStore } from '../store/chatStore';
import TabBar from './TabBar';
import InputBar from './InputBar';
import MessageBubble from './MessageBubble';
import SecureLinkCard from './SecureLinkCard';
import GameWorkbench from './GameWorkbench';
import CanvasPanel from './CanvasPanel';
import { groupSecureLinkMessages } from '../lib/secureLinks';
import {
  extractFileFromDataTransfer,
  getPasteUploadState,
  startPasteSendFile,
} from '../lib/pasteUpload';

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
    /^\d{1,2}\s+(?:[.#o●○·#]\s+){4,}/i.test(raw) ||
    /^\d{1,2}\s+(?:[.#o●○·]\s+){8,}[.#o●○·]\s*$/i.test(raw) ||
    /^(?:\d{1,2}\s+){8,}\d{1,2}\s*$/.test(raw) ||
    /^((row|turn|state|pot|street|current_bet)\s*[:=])/.test(t) ||
    /^#\d+\s+[^:：]+[:：]/.test(raw) ||
    /^-\s+\S+\s+\((alive|out)\)/i.test(raw) ||
    /^\s+\d+(?:\s+\d+){4,}\s*$/.test(raw) ||
    /^(红|黑|白)方\s+\S+\s+走\s+/.test(raw) ||
    /^[+\-!·].{6,}/.test(raw) ||
    /←.*(红|黑|白)方/.test(raw) ||
    /图例[：:]/.test(raw) ||
    /楚河汉界/.test(raw) ||
    /等宽字体/.test(raw) ||
    /积分体系/.test(raw) ||
    /对局[（(]/.test(raw) ||
    /^(go|chess|gomoku|xiangqi|doushou|holdem|zjh|niutou|sanguo|werewolf|mahjong)\b/.test(t) ||
    /^(三国杀|牛头王|斗兽棋|德州扑克|炸金花|狼人|麻将)/.test(raw) ||
    /[♔♕♖♗♘♙♚♛♜♝♞♟]/.test(raw) ||
    /^\s+[a-h](?:\s+[a-h]){7}\s*$/i.test(raw) ||
    /方\s+\S+\s+落子/.test(raw) ||
    /方\s+\S+\s+停一手/.test(raw) ||
    /^劫点/.test(raw) ||
    /闷牌|已弃牌|已看牌|当前回合|牌堆|军争/.test(raw) ||
    (/【/.test(raw) && /】/.test(raw)) ||
    /行棋/.test(raw)
  ) {
    return true;
  }

  const keywords = [
    '当前房间正在进行',
    '可直接加入',
    '同一房间同一时刻仅允许一场进行中的对局',
    '可玩游戏',
    '你的手牌',
    '公共牌',
    '贴目',
    '提子',
    '停一手',
    '底池=',
    '当前注=',
    '当前注：',
    '落子：',
    '走子：',
    '走子',
    '轮到：',
    '轮到 黑',
    '轮到 白',
    '轮到 红',
    '轮到 黑方',
    '轮到 白方',
    '轮到 红方',
    'gomoku',
    '围棋',
    'chess',
    'xiangqi',
    'holdem',
    'zjh',
    'niutou',
    'sanguo',
    'werewolf',
    'doushou',
    '斗兽棋',
    '国际象棋',
    '五子棋',
    '中国象棋',
    '上一步',
    '己方在下方',
    '行棋',
    '落子',
    'mahjong',
    '麻将',
  ];

  return keywords.some((k) => t.includes(k.toLowerCase()));
}

export default function ChatArea() {
  const { messages, activeRoom, nickname, status, privacyMode, doNotDisturb, clearMessages, canvasSession } = useChatStore();
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
  const [dragOver, setDragOver] = useState(false);

  const roomMessages = messages.get(activeRoom) || [];
  const visibleMessages = useMemo(() => {
    return roomMessages.filter((msg) => {
      if (msg.hidden) return false;
      if (msg.type === 'chat' || msg.type === 'pm') return true;
      if (msg.type === 'game') return false;
      if (msg.type === 'system') return !isGameFloodMessage(msg.content);
      return false;
    });
  }, [roomMessages]);

  const timelineItems = useMemo(
    () => groupSecureLinkMessages(visibleMessages),
    [visibleMessages],
  );

  useEffect(() => {
    if (!stickToBottom) return;
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [timelineItems, stickToBottom]);

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
    const el = chatScrollRef.current;
    if (!el) return;
    let raf = 0;
    const ro = new ResizeObserver(() => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const scrollTop = el.scrollTop;
        void el.offsetHeight;
        if (el.scrollTop !== scrollTop) {
          el.scrollTop = scrollTop;
        }
      });
    });
    ro.observe(el);
    return () => {
      ro.disconnect();
      cancelAnimationFrame(raf);
    };
  }, []);

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
        {canvasSession ? (
          <div className="canvas-pane">
            <CanvasPanel />
          </div>
        ) : (
          <div
            className={`game-pane ${doNotDisturb ? 'game-pane-dnd' : ''}`}
            style={doNotDisturb ? undefined : { height: `${workbenchHeight}px` }}
          >
            <GameWorkbench />
          </div>
        )}
        {!doNotDisturb && !canvasSession && (
        <div
          className={`chat-splitter ${isResizing ? 'active' : ''}`}
          title="Drag to resize game panel height"
          onMouseDown={() => setIsResizing(true)}
          onDoubleClick={() => setWorkbenchHeight(DEFAULT_WORKBENCH_HEIGHT)}
        >
          <span className="chat-splitter-grip">...</span>
        </div>
        )}
        {canvasSession ? (
          <div className="chat-splitter canvas-splitter" aria-hidden>
            <span className="chat-splitter-grip">...</span>
          </div>
        ) : null}

        <div className="chat-messages-surface">
          <div
            ref={chatScrollRef}
            className={`chat-messages${dragOver ? ' drag-over' : ''}`}
            onScroll={onChatScroll}
            onDragEnter={(e) => {
              if (status !== 'connected') return;
              if (![...e.dataTransfer.types].includes('Files')) return;
              e.preventDefault();
              setDragOver(true);
            }}
            onDragOver={(e) => {
              if (status !== 'connected') return;
              if (![...e.dataTransfer.types].includes('Files')) return;
              e.preventDefault();
              e.dataTransfer.dropEffect = 'copy';
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragOver(false);
              if (status !== 'connected' || getPasteUploadState().busy) return;
              const file = extractFileFromDataTransfer(e.dataTransfer);
              if (!file) return;
              void startPasteSendFile(file, activeRoom);
            }}
            onPaste={(e) => {
              if (status !== 'connected' || getPasteUploadState().busy) return;
              const file = extractFileFromDataTransfer(e.clipboardData);
              if (!file) return;
              e.preventDefault();
              void startPasteSendFile(file, activeRoom);
            }}
          >
            {timelineItems.length === 0 ? (
              <div className="chat-empty">
                <div className="chat-empty-icon">{'</>'}</div>
                <div className="chat-empty-text">
                  {status === 'connected'
                    ? `No messages in #${activeRoom} yet`
                    : 'Connect to start session'}
                </div>
              </div>
            ) : (
              timelineItems.map((item) =>
                item.type === 'secure-link' ? (
                  <SecureLinkCard key={item.id} payload={item.payload} />
                ) : (
                  <MessageBubble
                    key={item.message.id}
                    message={item.message}
                    isMe={item.message.sender === nickname}
                    currentNickname={nickname}
                  />
                ),
              )
            )}
            <div ref={messagesEndRef} />
          </div>
        </div>
      </div>

      <InputBar />
    </div>
  );
}
