import React from 'react';
import { ChatMessage } from '../../shared/protocol';

interface MessageBubbleProps {
  message: ChatMessage;
  isMe: boolean;
}

export default function MessageBubble({ message, isMe }: MessageBubbleProps) {
  const formatTime = (timestamp: number): string =>
    new Date(timestamp).toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });

  const showSender = message.type === 'chat' || message.type === 'pm';

  const senderClass =
    message.type === 'pm'
      ? 'pm'
      : message.sender === '*' || message.sender === '+' || message.sender === '!'
        ? 'system'
        : isMe
          ? 'me'
          : 'peer';

  const textClass =
    message.type === 'system' || message.type === 'join' || message.type === 'leave'
      ? 'system'
      : message.type === 'pm'
        ? 'pm'
        : '';

  return (
    <div className="message">
      <span className="message-time">{formatTime(message.timestamp)}</span>
      <div className="message-content">
        {showSender && <span className={`message-sender ${senderClass}`}>{message.sender}</span>}
        <span className={`message-text ${textClass}`}>{message.content}</span>
      </div>
    </div>
  );
}
