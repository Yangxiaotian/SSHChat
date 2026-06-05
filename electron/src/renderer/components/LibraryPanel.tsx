import React, { useMemo, useState } from 'react';
import { useChatStore } from '../store/chatStore';

type BookItem = {
  id: string;
  index: number;
  format: string;
  name: string;
  size: string;
  bookmarkPage?: number;
};

type BookmarkItem = {
  label: string;
  page: number;
};

type ReadingState = {
  title: string;
  page: number;
  total: number;
  body: string;
};

function parseLibraryMessages(messages: { type: string; sender: string; content: string }[]) {
  const systemMsgs = messages.filter((m) => m.type === 'system' && m.sender === '*');

  const books: BookItem[] = [];
  const bookmarks: BookmarkItem[] = [];
  let inCatalog = false;
  let inBookmarks = false;
  let reading: ReadingState | null = null;
  let currentReading: ReadingState | null = null;

  for (const msg of systemMsgs) {
    const content = msg.content.trimEnd();

    if (/^---\s*图书馆\s*---$/.test(content)) {
      inCatalog = true;
      inBookmarks = false;
      continue;
    }

    if (/^---\s*我的书签\s*---$/.test(content)) {
      inBookmarks = true;
      inCatalog = false;
      continue;
    }

    const bookMatch = content.match(
      /^(\d+)\.\s+\[([A-Z]+)\]\s+(.+?)\s+\(([^)]+)\)(?:\s+·\s+书签第\s+(\d+)\s+页)?$/,
    );
    if (inCatalog && bookMatch) {
      const idx = Number.parseInt(bookMatch[1], 10);
      const bookmarkPage = bookMatch[5] ? Number.parseInt(bookMatch[5], 10) : undefined;
      books.push({
        id: String(idx),
        index: idx,
        format: bookMatch[2],
        name: bookMatch[3],
        size: bookMatch[4],
        bookmarkPage,
      });
      continue;
    }

    const bookmarkMatch = content.match(/^(.+?)\s+·\s+第\s+(\d+)\s+页$/);
    if (inBookmarks && bookmarkMatch) {
      bookmarks.push({
        label: bookmarkMatch[1],
        page: Number.parseInt(bookmarkMatch[2], 10),
      });
      continue;
    }

    const pageMatch = content.match(/^---\s*《(.+?)》\s*第\s*(\d+)\/(\d+)\s*页\s*---$/);
    if (pageMatch) {
      if (currentReading) reading = currentReading;
      currentReading = {
        title: pageMatch[1],
        page: Number.parseInt(pageMatch[2], 10),
        total: Number.parseInt(pageMatch[3], 10),
        body: '',
      };
      continue;
    }

    if (currentReading && content.startsWith('    ')) {
      const line = content.trim();
      if (line && !line.startsWith('翻页：')) {
        currentReading.body += `${line}\n`;
      }
    }
  }

  if (currentReading) reading = currentReading;
  return { books, bookmarks, reading };
}

export default function LibraryPanel() {
  const { messages, activeRoom } = useChatStore();
  const [loadingId, setLoadingId] = useState<string | null>(null);

  const { books, bookmarks, reading } = useMemo(() => {
    const roomMessages = messages.get(activeRoom) || [];
    return parseLibraryMessages(roomMessages);
  }, [messages, activeRoom]);

  const handleRefresh = async () => {
    await window.api.sendMessage('/library');
  };

  const handleShowBookmarks = async () => {
    await window.api.sendMessage('/library bookmarks');
  };

  const handleOpen = async (item: BookItem) => {
    setLoadingId(item.id);
    await window.api.sendMessage(`/library open ${item.index}`);
    setLoadingId(null);
  };

  const handleNext = async () => {
    await window.api.sendMessage('/library next');
  };

  const handlePrev = async () => {
    await window.api.sendMessage('/library prev');
  };

  const handleClose = async () => {
    await window.api.sendMessage('/library close');
  };

  return (
    <div className="library-panel">
      <div className="library-toolbar">
        <button className="send-button" onClick={handleRefresh}>Refresh Library</button>
        <button className="mini-btn" onClick={handleShowBookmarks}>My Bookmarks</button>
      </div>

      {reading && (
        <div className="library-reader">
          <div className="library-reader-header">
            <div className="library-reader-title">{reading.title}</div>
            <div className="library-reader-meta">
              第 {reading.page} / {reading.total} 页
            </div>
          </div>
          <div className="library-reader-body">{reading.body.trim() || '（空白页）'}</div>
          <div className="library-reader-actions">
            <button className="mini-btn" onClick={handlePrev} disabled={reading.page <= 1}>Prev</button>
            <button className="mini-btn" onClick={handleNext} disabled={reading.page >= reading.total}>Next</button>
            <button className="mini-btn" onClick={handleClose}>Close</button>
          </div>
        </div>
      )}

      {books.length === 0 ? (
        <div className="library-empty">No books loaded. Click refresh to fetch catalog.</div>
      ) : (
        <div className="library-list">
          {books.map((item) => (
            <div key={item.id} className="library-item">
              <button className="library-item-main" onClick={() => handleOpen(item)}>
                <div className="library-item-meta">
                  <span className="library-rank">#{item.index}</span>
                  <span className="library-format">{item.format}</span>
                  <span className="library-size">{item.size}</span>
                  {item.bookmarkPage !== undefined && (
                    <span className="library-bookmark">书签 p.{item.bookmarkPage}</span>
                  )}
                </div>
                <div className="library-name">{item.name}</div>
                {loadingId === item.id && <div className="library-loading">Opening...</div>}
              </button>
            </div>
          ))}
        </div>
      )}

      {bookmarks.length > 0 && (
        <div className="library-bookmarks">
          <div className="library-bookmarks-title">My Bookmarks</div>
          {bookmarks.map((item) => (
            <div key={`${item.label}-${item.page}`} className="library-bookmark-row">
              <span className="library-bookmark-label">{item.label}</span>
              <span className="library-bookmark-page">p.{item.page}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
