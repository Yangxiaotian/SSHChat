import React from 'react';
import { useChatStore } from '../store/chatStore';
import { useTranslation } from '../i18n';

export default function StatusBar() {
  const {
    status,
    activeRoom,
    rooms,
    nickname,
    theme,
    toggleTheme,
    toggleLocale,
    privacyMode,
    togglePrivacyMode,
    setShowLogin,
  } = useChatStore();
  const { t } = useTranslation();

  const statusText: Record<string, string> = {
    disconnected: t('status.disconnected'),
    connecting: t('status.connecting'),
    connected: t('status.connected'),
    error: t('status.error'),
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
        <span className="statusbar-item">{t('common.rooms', { count: rooms.length })}</span>
        <span className="statusbar-separator"></span>
        <span className="statusbar-item clickable" onClick={toggleLocale} title="中文 / English">
          {t('status.language')}
        </span>
        <span className="statusbar-separator"></span>
        <span className="statusbar-item clickable" onClick={toggleTheme}>
          {theme === 'dark' ? t('status.dark') : t('status.light')}
        </span>
        <span className="statusbar-separator"></span>
        <span className="statusbar-item clickable" onClick={togglePrivacyMode}>
          {privacyMode ? t('status.focus') : t('status.visible')}
        </span>
      </div>
    </div>
  );
}
