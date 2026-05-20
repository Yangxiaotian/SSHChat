import React, { useEffect } from 'react';
import { useChatStore } from '../store/chatStore';

export default function UserList() {
  const { users, activeRoom, status } = useChatStore();

  useEffect(() => {
    if (status === 'connected') {
      window.api.requestUsers();
    }
  }, [activeRoom, status]);

  return (
    <div className="user-list">
      <div className="room-section-header" style={{ cursor: 'default' }}>
        <span style={{ flex: 1 }}>#{activeRoom}</span>
        <span style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <button
            className="mini-btn"
            style={{ height: '20px', padding: '0 6px' }}
            onClick={() => window.api.requestUsers()}
            title="Refresh online users"
          >
            Refresh
          </button>
          {users.length} online
        </span>
      </div>
      {users.length === 0 ? (
        <div className="user-item" style={{ color: 'var(--vscode-text-secondary)', fontStyle: 'italic' }}>
          No users online
        </div>
      ) : (
        users.map((user, idx) => (
          <div key={`${user}-${idx}`} className="user-item">
            <span className="user-icon">o</span>
            <span className="user-name">{user}</span>
          </div>
        ))
      )}
    </div>
  );
}
