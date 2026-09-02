import React, { Component, ErrorInfo, ReactNode, useEffect, useState } from 'react';
import { useChatStore } from './store/chatStore';
import {
  ChatHistoryIdentity,
  ChatHistorySnapshot,
  ChatMessage,
  ConnectionConfig,
  RoomInfo,
  SHAKE_TOKEN,
} from '../shared/protocol';
import { useTranslation } from './i18n';
import ActivityBar from './components/ActivityBar';
import Sidebar from './components/Sidebar';
import ChatArea from './components/ChatArea';
import StatusBar from './components/StatusBar';
import LoginDialog from './components/LoginDialog';
import { tryHandleUploadInviteLine } from './lib/pasteUpload';

let audioCtx: AudioContext | null = null;
function playNotificationSound(): void {
  try {
    if (!audioCtx) audioCtx = new AudioContext();
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.connect(gain);
    gain.connect(audioCtx.destination);
    osc.frequency.value = 800;
    gain.gain.value = 0.1;
    osc.start();
    gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + 0.15);
    osc.stop(audioCtx.currentTime + 0.15);
  } catch {
    // ignore audio errors
  }
}

function getHistoryIdentity(config: ConnectionConfig | null): ChatHistoryIdentity | null {
  if (!config?.host.trim() || !config.user.trim()) return null;
  return {
    host: config.host,
    chatPort: config.chatPort || 12345,
    user: config.user,
  };
}

function getHistoryIdentityKey(identity: ChatHistoryIdentity): string {
  return `${identity.host.trim().toLowerCase()}:${identity.chatPort || 12345}:${identity.user.trim().toLowerCase()}`;
}

function getHistorySnapshot(messages: Map<string, ChatMessage[]>, rooms: RoomInfo[]): ChatHistorySnapshot {
  return {
    roomNames: rooms.map((room) => room.name),
    rooms: Object.fromEntries(
      [...messages.entries()].map(([room, roomMessages]) => [
        room,
        roomMessages.filter((message) => message.type !== 'game'),
      ]),
    ),
  };
}

type BoundaryState = {
  hasError: boolean;
  message: string;
};

class RendererErrorBoundary extends Component<{ children: ReactNode; t: (key: string) => string }, BoundaryState> {
  state: BoundaryState = {
    hasError: false,
    message: '',
  };

  static getDerivedStateFromError(error: unknown): BoundaryState {
    return {
      hasError: true,
      message: error instanceof Error ? error.message : String(error),
    };
  }

  componentDidCatch(error: unknown, errorInfo: ErrorInfo) {
    // Keep details in terminal for troubleshooting white-screen issues.
    console.error('[RendererErrorBoundary]', error, errorInfo.componentStack);
  }

  private onReload = () => {
    window.location.reload();
  };

  render() {
    if (!this.state.hasError) {
      return this.props.children;
    }
    return (
      <div style={{ padding: 20, color: '#ddd', background: '#1e1e1e', height: '100vh' }}>
        <h3 style={{ marginTop: 0 }}>{this.props.t('app.errorTitle')}</h3>
        <p>{this.props.t('app.errorHint')}</p>
        <pre style={{ whiteSpace: 'pre-wrap', color: '#f2c08a' }}>{this.state.message || this.props.t('app.unknownError')}</pre>
        <button className="mini-btn" onClick={this.onReload}>{this.props.t('common.reload')}</button>
      </div>
    );
  }
}

export default function App() {
  const { status, showLogin, theme, privacyMode, activeRoom, config, messages, rooms, locale, toggleLocale } = useChatStore();
  const { t } = useTranslation();
  const [sidebarVisible, setSidebarVisible] = useState(true);
  const loadedHistoryKey = React.useRef<string | null>(null);
  const activeHistoryKey = React.useRef<string | null>(null);
  const activeHistoryIdentity = React.useRef<ChatHistoryIdentity | null>(null);
  const historyLoadSequence = React.useRef(0);

  useEffect(() => {
    window.api.loadConfig().then((config) => {
      if (config) {
        useChatStore.getState().setConfig(config);
        useChatStore.getState().setNickname(config.user);
        // Load saved values into the form, but never connect without an explicit user action.
        // This gives the user a chance to correct an outdated host or port first.
        useChatStore.getState().setShowLogin(true);
      }
    });

    const unsubMessage = window.api.onChatMessage((message) => {
      const { nickname: me, activeRoom: currentRoom } = useChatStore.getState();
      const isShakeSignal = message.content.trim() === SHAKE_TOKEN;
      if (isShakeSignal) {
        // Show who sent the shake in chat
        const sender = message.sender === me ? t('common.you') : message.sender;
        useChatStore.getState().addMessage({
          id: `shake_${Date.now()}`,
          room: message.room,
          sender: '*',
          content: t('app.roomShake', { sender }),
          timestamp: Date.now(),
          type: 'system',
        });
        window.api.shakeWindow();
        return;
      }
      useChatStore.getState().addMessage(message);
      if (message.type === 'system') {
        tryHandleUploadInviteLine(message.content);
      }
      const isPeerMessage = message.sender !== me && (message.type === 'chat' || message.type === 'pm' || message.type === 'game');
      const needAttention = isPeerMessage && (message.room !== currentRoom || !document.hasFocus());
      if (needAttention) {
        window.api.notifyAttention();
        playNotificationSound();
      }
      if (message.type === 'join' || message.type === 'leave') {
        window.api.requestUsers();
      }
    });

    const unsubUsers = window.api.onUserUpdate((snapshot) => {
      if (snapshot.room === useChatStore.getState().activeRoom) {
        useChatStore.getState().setUsers(snapshot.users);
      }
    });

    const unsubRoom = window.api.onRoomUpdate((rooms, activeRoom) => {
      if (Array.isArray(rooms)) {
        const roomInfos = rooms.map((name) => ({
          name,
          isDefault: name === 'default',
          unreadCount: 0,
          lastActivity: Date.now(),
        }));
        useChatStore.getState().setRooms(roomInfos);
      }
      if (typeof activeRoom === 'string' && activeRoom.trim()) {
        useChatStore.getState().setActiveRoom(activeRoom);
      }
    });

    const unsubStatus = window.api.onConnectionStatus((nextStatus) => {
      useChatStore.getState().setStatus(nextStatus);
      if (nextStatus === 'connected') {
        useChatStore.getState().setShowLogin(false);
        useChatStore.getState().setError(null);
        const { messages, activeRoom, locale } = useChatStore.getState();
        useChatStore.getState().rebuildLibraryView(messages.get(activeRoom) || []);
        window.api.requestUsers();
        // Keep TCP chat language in sync with the GUI locale preference.
        void window.api.sendMessage(`/lang ${locale}`);
      } else if (nextStatus === 'disconnected') {
        useChatStore.getState().setShowLogin(true);
        useChatStore.getState().resetLibraryView();
      }
    });

    const unsubError = window.api.onError((error) => {
      useChatStore.getState().setError(error);
    });

    return () => {
      unsubMessage();
      unsubRoom();
      unsubStatus();
      unsubError();
      unsubUsers();
    };
  }, []);

  useEffect(() => {
    const identity = getHistoryIdentity(config);
    if (!identity) return;
    const key = getHistoryIdentityKey(identity);
    const sequence = ++historyLoadSequence.current;

    if (activeHistoryKey.current !== key) {
      if (
        activeHistoryIdentity.current
        && loadedHistoryKey.current === activeHistoryKey.current
      ) {
        window.api.flushChatHistory(
          activeHistoryIdentity.current,
          getHistorySnapshot(useChatStore.getState().messages, useChatStore.getState().rooms),
        );
      }
      activeHistoryKey.current = key;
      activeHistoryIdentity.current = identity;
      loadedHistoryKey.current = null;
      useChatStore.getState().resetWorkspace();
    }

    window.api.loadChatHistory(identity)
      .then((snapshot) => {
        if (sequence !== historyLoadSequence.current || activeHistoryKey.current !== key) return;
        useChatStore.getState().hydrateMessages(snapshot.rooms, snapshot.roomNames);
        loadedHistoryKey.current = key;
      })
      .catch((error) => {
        console.error('[ChatHistory] Failed to load:', error);
        if (sequence === historyLoadSequence.current && activeHistoryKey.current === key) {
          loadedHistoryKey.current = key;
        }
      });
  }, [config]);

  useEffect(() => {
    const identity = getHistoryIdentity(config);
    if (!identity) return;
    const key = getHistoryIdentityKey(identity);
    if (loadedHistoryKey.current !== key) return;

    const timer = window.setTimeout(() => {
      window.api.saveChatHistory(
        identity,
        getHistorySnapshot(useChatStore.getState().messages, useChatStore.getState().rooms),
      )
        .catch((error) => console.error('[ChatHistory] Failed to save:', error));
    }, 250);
    return () => window.clearTimeout(timer);
  }, [config, messages, rooms]);

  useEffect(() => {
    const flushBeforeClose = () => {
      const identity = activeHistoryIdentity.current;
      if (!identity || loadedHistoryKey.current !== activeHistoryKey.current) return;
      window.api.flushChatHistory(
        identity,
        getHistorySnapshot(useChatStore.getState().messages, useChatStore.getState().rooms),
      );
    };
    window.addEventListener('beforeunload', flushBeforeClose);
    return () => window.removeEventListener('beforeunload', flushBeforeClose);
  }, []);

  useEffect(() => {
    if (status === 'connected') {
      window.api.requestUsers();
    }
  }, [activeRoom, status]);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    document.title = privacyMode ? 'VsCodeEn' : 'SSHChat';
  }, [theme, privacyMode]);

  return (
    <RendererErrorBoundary t={t}>
      <div className="app-container">
        <div className="titlebar">
          <span className="titlebar-icon">{'</>'}</span>
          <span className="titlebar-title">
            {privacyMode ? 'VsCodeEn' : `${t('app.title')}${status === 'connected' ? '' : ` (${t('status.disconnected')})`}`}
          </span>
          <div className="titlebar-actions">
            <button
              type="button"
              className="titlebar-language-button"
              onClick={toggleLocale}
              title={locale === 'zh' ? 'Switch to English' : '切换到中文'}
              aria-label={locale === 'zh' ? 'Switch to English' : '切换到中文'}
            >
              {locale === 'zh' ? 'EN' : '中文'}
            </button>
          </div>
        </div>

        <div className="main-content">
          <ActivityBar
            onToggleSidebar={() => setSidebarVisible(!sidebarVisible)}
            sidebarVisible={sidebarVisible}
          />
          {sidebarVisible && <Sidebar />}
          <ChatArea />
        </div>

        <StatusBar />
        {showLogin && <LoginDialog />}
      </div>
    </RendererErrorBoundary>
  );
}
