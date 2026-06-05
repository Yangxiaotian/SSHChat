import React, { useState, useEffect } from 'react';
import { useChatStore } from '../store/chatStore';
import { ConnectionConfig } from '../../shared/protocol';
import { useTranslation } from '../i18n';

export default function LoginDialog() {
  const { config, status, error, setShowLogin } = useChatStore();
  const { t } = useTranslation();
  const [host, setHost] = useState('');
  const [user, setUser] = useState('');
  const [sshPort, setSshPort] = useState('22');
  const [chatPort, setChatPort] = useState('12345');
  const [saveConfig, setSaveConfig] = useState(true);
  const [localError, setLocalError] = useState('');

  useEffect(() => {
    if (config) {
      setHost(config.host);
      setUser(config.user);
      setSshPort(config.sshPort.toString());
      setChatPort((config.chatPort || 12345).toString());
    }
  }, [config]);

  const handleConnect = async () => {
    setLocalError('');

    if (!host.trim()) {
      setLocalError(t('login.enterHost'));
      return;
    }
    if (!user.trim()) {
      setLocalError(t('login.enterUser'));
      return;
    }

    const portNum = parseInt(sshPort);
    if (isNaN(portNum) || portNum < 1 || portNum > 65535) {
      setLocalError(t('login.invalidSshPort'));
      return;
    }

    const chatPortNum = parseInt(chatPort);
    if (isNaN(chatPortNum) || chatPortNum < 1 || chatPortNum > 65535) {
      setLocalError(t('login.invalidChatPort'));
      return;
    }

    const connectionConfig: ConnectionConfig = {
      host: host.trim(),
      user: user.trim(),
      sshPort: portNum,
      chatPort: chatPortNum,
    };

    useChatStore.getState().setConfig(connectionConfig);
    useChatStore.getState().setNickname(user.trim());

    if (saveConfig) {
      await window.api.saveConfig(connectionConfig);
    }

    const result = await window.api.connect(connectionConfig, user.trim());
    if (!result.success) {
      setLocalError(result.error || t('login.connectFailed'));
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleConnect();
    }
    if (e.key === 'Escape') {
      if (status !== 'connecting') {
        setShowLogin(false);
      }
    }
  };

  const isConnecting = status === 'connecting';

  return (
    <div className="login-overlay" onClick={(e) => {
      if (e.target === e.currentTarget && !isConnecting) {
        setShowLogin(false);
      }
    }}>
      <div className="login-dialog animate-slide-in" onKeyDown={handleKeyDown}>
        <div className="login-title">{t('login.title')}</div>
        <div className="login-subtitle">{t('login.subtitle')}</div>

        <div className="login-field">
          <label className="login-label">{t('login.host')}</label>
          <input
            type="text"
            className="login-input"
            value={host}
            onChange={(e) => setHost(e.target.value)}
            placeholder={t('login.hostPlaceholder')}
            disabled={isConnecting}
            autoFocus
          />
        </div>

        <div className="login-field">
          <label className="login-label">{t('login.username')}</label>
          <input
            type="text"
            className="login-input"
            value={user}
            onChange={(e) => setUser(e.target.value)}
            placeholder={t('login.usernamePlaceholder')}
            disabled={isConnecting}
          />
        </div>

        <div className="login-row">
          <div className="login-field">
            <label className="login-label">{t('login.sshPort')}</label>
            <input
              type="text"
              className="login-input"
              value={sshPort}
              onChange={(e) => setSshPort(e.target.value)}
              placeholder="22"
              disabled={isConnecting}
            />
          </div>

          <div className="login-field">
            <label className="login-label">{t('login.chatPort')}</label>
            <input
              type="text"
              className="login-input"
              value={chatPort}
              onChange={(e) => setChatPort(e.target.value)}
              placeholder="12345"
              disabled={isConnecting}
            />
          </div>
        </div>

        <label className="login-checkbox">
          <input
            type="checkbox"
            checked={saveConfig}
            onChange={(e) => setSaveConfig(e.target.checked)}
            disabled={isConnecting}
          />
          <span className="login-checkbox-label">{t('login.saveSettings')}</span>
        </label>

        {(localError || error) && (
          <div className="login-error">
            {localError || error}
          </div>
        )}

        {isConnecting && (
          <div className="login-status">
            <span className="login-spinner"></span>
            {t('login.connectingTo', { host })}
          </div>
        )}

        <div className="login-buttons">
          <button
            className="login-button secondary"
            onClick={() => setShowLogin(false)}
            disabled={isConnecting}
          >
            {t('common.cancel')}
          </button>
          <button
            className="login-button primary"
            onClick={handleConnect}
            disabled={isConnecting}
          >
            {isConnecting ? t('common.connecting') : t('common.connect')}
          </button>
        </div>
      </div>
    </div>
  );
}
