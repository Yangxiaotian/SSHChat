import React, { useEffect, useMemo, useState } from 'react';
import { useChatStore } from '../store/chatStore';

type NewsItem = {
  id: string;
  categoryLabel: string;
  categoryToken: string;
  index: number;
  source: string;
  title: string;
  body: string;
};

function inferCategoryToken(label: string): string {
  const l = label.toLowerCase();
  if (l.includes('中文') || l.includes('cn')) return '中文';
  if (l.includes('国际') || l.includes('world') || l.includes('intl')) return '国际';
  if (l.includes('科技') || l.includes('tech')) return '科技';
  return 'all';
}

export default function NewsPanel() {
  const { messages, activeRoom } = useChatStore();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loadingId, setLoadingId] = useState<string | null>(null);

  const newsItems = useMemo(() => {
    const roomMessages = messages.get(activeRoom) || [];
    const systemMsgs = roomMessages.filter((m) => m.type === 'system' && m.sender === '*');

    const items: NewsItem[] = [];
    let currentCategoryLabel = '';
    let currentCategoryToken = 'all';
    let current: NewsItem | null = null;

    for (const msg of systemMsgs) {
      const content = msg.content.trimEnd();
      const sectionMatch = content.match(/^---\s*(.+?)\s*---$/);
      if (sectionMatch) {
        currentCategoryLabel = sectionMatch[1];
        currentCategoryToken = inferCategoryToken(currentCategoryLabel);
        if (current) {
          items.push(current);
          current = null;
        }
        continue;
      }

      const titleMatch = content.match(/^(\d+)\.\s+\[([^\]]+)\]\s+(.+)$/);
      if (titleMatch) {
        if (current) items.push(current);
        const idx = Number.parseInt(titleMatch[1], 10);
        current = {
          id: `${currentCategoryToken}:${idx}`,
          categoryLabel: currentCategoryLabel || '新闻',
          categoryToken: currentCategoryToken,
          index: idx,
          source: titleMatch[2],
          title: titleMatch[3],
          body: '',
        };
        continue;
      }

      if (current && content.startsWith('    ')) {
        const line = content.trim();
        if (line) current.body += `${line} `;
      }
    }
    if (current) items.push(current);

    const deduped = new Map<string, NewsItem>();
    for (const item of items) deduped.set(item.id, item);
    return Array.from(deduped.values());
  }, [messages, activeRoom]);

  const grouped = useMemo(() => {
    const m = new Map<string, NewsItem[]>();
    for (const item of newsItems) {
      const arr = m.get(item.categoryLabel) || [];
      arr.push(item);
      m.set(item.categoryLabel, arr);
    }
    return Array.from(m.entries());
  }, [newsItems]);

  const handleRefresh = async () => {
    await window.api.requestNews();
  };

  const handleOpenDetail = async (item: NewsItem) => {
    setSelectedId(item.id);
    setLoadingId(item.id);
    await window.api.sendMessage(`/news detail ${item.categoryToken} ${item.index}`);
    setLoadingId(null);
  };

  const handleFetchArticle = async (item: NewsItem) => {
    await window.api.sendMessage(`/news fetch ${item.categoryToken} ${item.index}`);
  };

  return (
    <div className="news-panel">
      <div className="news-toolbar">
        <button className="send-button" onClick={handleRefresh}>Refresh News</button>
      </div>

      {grouped.length === 0 ? (
        <div className="news-empty">No news loaded. Click refresh to fetch.</div>
      ) : (
        grouped.map(([label, items]) => (
          <div key={label} className="news-category">
            <div className="news-category-title">{label}</div>
            {items.map((item) => {
              const expanded = selectedId === item.id;
              return (
                <div key={item.id} className={`news-item ${expanded ? 'expanded' : ''}`}>
                  <button className="news-item-main" onClick={() => handleOpenDetail(item)}>
                    <div className="news-item-meta">
                      <span className="news-rank">#{item.index}</span>
                      <span className="news-source">{item.source}</span>
                    </div>
                    <div className="news-title">{item.title}</div>
                    {item.body && <div className="news-body">{item.body.trim()}</div>}
                    {loadingId === item.id && <div className="news-loading">Loading detail...</div>}
                  </button>
                  <div className="news-item-actions">
                    <button className="mini-btn" onClick={() => handleFetchArticle(item)}>Fetch</button>
                  </div>
                </div>
              );
            })}
          </div>
        ))
      )}
    </div>
  );
}

