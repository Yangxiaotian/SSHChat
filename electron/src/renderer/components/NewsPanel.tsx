import React, { useState, useEffect } from 'react';
import { useChatStore } from '../store/chatStore';

export default function NewsPanel() {
  const { messages, activeRoom } = useChatStore();
  const [newsItems, setNewsItems] = useState<{ title: string; source: string; body: string }[]>([]);

  useEffect(() => {
    // Parse news from system messages
    const roomMessages = messages.get(activeRoom) || [];
    const newsMessages = roomMessages.filter(m => m.type === 'system' && m.sender === '*');

    const items: { title: string; source: string; body: string }[] = [];
    let currentItem: { title: string; source: string; body: string } | null = null;

    for (const msg of newsMessages) {
      const content = msg.content;
      if (content.startsWith('---') && content.includes('---')) {
        if (currentItem) {
          items.push(currentItem);
        }
        currentItem = null;
        continue;
      }
      const titleMatch = content.match(/^\d+\.\s+\[([^\]]+)\]\s+(.*)$/);
      if (titleMatch) {
        if (currentItem) {
          items.push(currentItem);
        }
        currentItem = {
          source: titleMatch[1],
          title: titleMatch[2],
          body: '',
        };
        continue;
      }
      if (currentItem && content.startsWith('   ')) {
        currentItem.body += content.trim() + ' ';
      }
    }
    if (currentItem) {
      items.push(currentItem);
    }

    setNewsItems(items);
  }, [messages, activeRoom]);

  const handleRefresh = () => {
    window.api.requestNews();
  };

  return (
    <div className="news-panel">
      <div style={{ marginBottom: '12px' }}>
        <button
          className="send-button"
          onClick={handleRefresh}
          style={{ width: '100%' }}
        >
          Refresh News
        </button>
      </div>
      {newsItems.length === 0 ? (
        <div style={{ color: 'var(--vscode-text-secondary)', fontStyle: 'italic', padding: '8px 0' }}>
          No news loaded. Click refresh to fetch.
        </div>
      ) : (
        newsItems.map((item, index) => (
          <div key={index} className="news-item">
            <div className="news-source">{item.source}</div>
            <div className="news-title">{item.title}</div>
            {item.body && <div className="news-body">{item.body.slice(0, 150)}...</div>}
          </div>
        ))
      )}
    </div>
  );
}
