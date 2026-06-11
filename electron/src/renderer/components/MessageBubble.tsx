import React from 'react';
import { ChatMessage } from '../../shared/protocol';

interface MessageBubbleProps {
  message: ChatMessage;
  isMe: boolean;
  currentNickname?: string;
}

function highlightMentions(text: string, currentNickname: string): React.ReactNode[] {
  if (!currentNickname) return [text];
  const parts = text.split(/(@\w+)/g);
  return parts.map((part, i) => {
    if (part === `@${currentNickname}` || part === `@${currentNickname.toLowerCase()}`) {
      return <span key={i} className="mention-highlight">{part}</span>;
    }
    return part;
  });
}

export default function MessageBubble({ message, isMe, currentNickname }: MessageBubbleProps) {
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

  const handleCopy = async () => {
    try {
      const text = showSender ? `${message.sender}: ${message.content}` : message.content;
      await navigator.clipboard.writeText(text);
    } catch {
      // no-op
    }
  };

  const renderContent = () => {
    if (message.type === 'chat' && currentNickname) {
      return highlightMentions(message.content, currentNickname);
    }
    return message.content;
  };

  return (
    <div className="message">
      <span className="message-time">{formatTime(message.timestamp)}</span>
      <div className="message-content">
        {showSender && <span className={`message-sender ${senderClass}`}>{message.sender}</span>}
        <div className={`message-text ${textClass}`}>{renderContent()}</div>
        <button className="message-copy" onClick={handleCopy} title="Copy">
          Copy
        </button>
      </div>
    </div>
  );
}
