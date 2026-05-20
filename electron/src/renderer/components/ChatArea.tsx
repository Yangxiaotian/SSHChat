import React, { useRef, useEffect } from 'react';
import { useChatStore } from '../store/chatStore';
import TabBar from './TabBar';
import InputBar from './InputBar';
import MessageBubble from './MessageBubble';
import GameWorkbench from './GameWorkbench';

export default function ChatArea() {
  const { messages, activeRoom, nickname, status, privacyMode, clearMessages } = useChatStore();
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const roomMessages = messages.get(activeRoom) || [];

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [roomMessages]);

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

      <GameWorkbench />

      <div className="chat-messages">
        {roomMessages.length === 0 ? (
          <div className="chat-empty">
            <div className="chat-empty-icon">{'</>'}</div>
            <div className="chat-empty-text">
              {status === 'connected'
                ? `No messages in #${activeRoom} yet`
                : 'Connect to start session'}
            </div>
          </div>
        ) : (
          roomMessages.map((msg) => (
            <MessageBubble key={msg.id} message={msg} isMe={msg.sender === nickname} />
          ))
        )}
        <div ref={messagesEndRef} />
      </div>

      <InputBar />
    </div>
  );
}
