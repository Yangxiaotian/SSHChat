import React from 'react';
import { useChatStore } from '../store/chatStore';

export default function StatusBar() {
  const {
    status,
    activeRoom,
    rooms,
    nickname,
    theme,
    privacyMode,
    toggleTheme,
    togglePrivacyMode,
    setShowLogin,
  } = useChatStore();

  const statusText: Record<string, string> = {
    disconnected: 'Disconnected',
    connecting: 'Connecting...',
    connected: 'Connected',
    error: 'Error',
  };

  const statusColor: Record<string, string> = {
    disconnected: 'var(--vscode-error)',
    connecting: 'var(--vscode-warning)',
    connected: 'var(--vscode-statusbar)',
    error: 'var(--vscode-error)',
  };

  return (
    <div className="statusbar" style={{ backgroundColor: statusColor[status] }}>
      <div className="statusbar-left">
        <span
          className={`statusbar-item ${status === 'disconnected' || status === 'error' ? 'clickable' : ''}`}
          onClick={() => {
            if (status === 'disconnected' || status === 'error') setShowLogin(true);
          }}
        >
          {statusText[status]}
        </span>
        {status === 'connected' && (
          <>
            <span className="statusbar-separator"></span>
            <span className="statusbar-item">{nickname}</span>
            <span className="statusbar-separator"></span>
            <span className="statusbar-item">#{activeRoom}</span>
          </>
        )}
      </div>
      <div className="statusbar-right">
        <span className="statusbar-item">{rooms.length} rooms</span>
        <span className="statusbar-separator"></span>
        <span className="statusbar-item clickable" onClick={toggleTheme}>
          {theme === 'dark' ? 'Dark' : 'Light'}
        </span>
        <span className="statusbar-separator"></span>
        <span className="statusbar-item clickable" onClick={togglePrivacyMode}>
          {privacyMode ? 'Focus' : 'Visible'}
        </span>
      </div>
    </div>
  );
}
