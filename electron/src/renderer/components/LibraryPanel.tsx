import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useChatStore } from '../store/chatStore';
import type { LibraryBookItem } from '../lib/libraryMessages';

export default function LibraryPanel() {
  const { libraryView } = useChatStore();
  const [loadingId, setLoadingId] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const readerBodyRef = useRef<HTMLDivElement>(null);

  const { books, bookmarks, reading } = libraryView;

  const filteredBooks = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return books;
    const terms = q.split(/\s+/);
    return books.filter((item) => {
      const haystack = `${item.name} ${item.format}`.toLowerCase();
      return terms.every((term) => haystack.includes(term));
    });
  }, [books, query]);

  const showSearch = books.length > 10;

  useEffect(() => {
    const el = readerBodyRef.current;
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
  }, [reading?.page, reading?.title]);

  const handleRefresh = async () => {
    await window.api.sendMessage('/library');
  };

  const handleShowBookmarks = async () => {
    await window.api.sendMessage('/library bookmarks');
  };

  const handleOpen = async (item: LibraryBookItem) => {
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

      {showSearch && (
        <div className="library-search">
          <input
            type="search"
            className="library-search-input"
            placeholder="按书名或格式查找…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          {query.trim() && (
            <div className="library-search-meta">
              找到 {filteredBooks.length} / {books.length} 本
            </div>
          )}
        </div>
      )}

      {reading && (
        <div className="library-reader">
          <div className="library-reader-header">
            <div className="library-reader-title">{reading.title}</div>
            <div className="library-reader-meta">
              第 {reading.page} / {reading.total} 页
            </div>
          </div>
          <div ref={readerBodyRef} className="library-reader-body">
            {reading.body.trim() || '（空白页）'}
          </div>
          <div className="library-reader-actions">
            <button className="mini-btn" onClick={handlePrev} disabled={reading.page <= 1}>Prev</button>
            <button className="mini-btn" onClick={handleNext} disabled={reading.page >= reading.total}>Next</button>
            <button className="mini-btn" onClick={handleClose}>Close</button>
          </div>
        </div>
      )}

      {books.length === 0 ? (
        <div className="library-empty">No books loaded. Click refresh to fetch catalog.</div>
      ) : filteredBooks.length === 0 ? (
        <div className="library-empty">No books match your search.</div>
      ) : (
        <div className="library-list">
          {filteredBooks.map((item) => (
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
