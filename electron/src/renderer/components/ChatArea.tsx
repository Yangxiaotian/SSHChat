import React, { useRef, useEffect, useMemo } from 'react';
import { useChatStore } from '../store/chatStore';
import TabBar from './TabBar';
import InputBar from './InputBar';
import MessageBubble from './MessageBubble';
import GameWorkbench from './GameWorkbench';

export default function ChatArea() {
  const { messages, activeRoom, nickname, status, privacyMode } = useChatStore();
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const roomMessages = messages.get(activeRoom) || [];
  const visibleMessages = useMemo(() => {
    return roomMessages.filter(
      (msg) => msg.type === 'chat' || msg.type === 'pm' || msg.type === 'game',
    );
  }, [roomMessages]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [visibleMessages]);

  return (
    <div className="chat-container">
      <TabBar />

      <div className="breadcrumb">
        <span className="breadcrumb-item">
          <span>{privacyMode ? 'Workspace' : 'SSHChat'}</span>
        </span>
        <span className="breadcrumb-separator">{'>'}</span>
        <span className="breadcrumb-item">
          <span>#{activeRoom}</span>
        </span>
      </div>

      <GameWorkbench />

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

      <InputBar />
    </div>
  );
}
