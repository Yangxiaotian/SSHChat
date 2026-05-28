import React, { Component, ErrorInfo, ReactNode, useEffect, useState } from 'react';
import { useChatStore } from './store/chatStore';
import { SHAKE_TOKEN } from '../shared/protocol';
import ActivityBar from './components/ActivityBar';
import Sidebar from './components/Sidebar';
import ChatArea from './components/ChatArea';
import StatusBar from './components/StatusBar';
import LoginDialog from './components/LoginDialog';

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

type BoundaryState = {
  hasError: boolean;
  message: string;
};

class RendererErrorBoundary extends Component<{ children: ReactNode }, BoundaryState> {
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
        <h3 style={{ marginTop: 0 }}>界面异常已拦截</h3>
        <p>已避免白屏。请点击“重载界面”恢复。</p>
        <pre style={{ whiteSpace: 'pre-wrap', color: '#f2c08a' }}>{this.state.message || '未知错误'}</pre>
        <button className="mini-btn" onClick={this.onReload}>重载界面</button>
      </div>
    );
  }
}

export default function App() {
  const { status, showLogin, theme, privacyMode, activeRoom } = useChatStore();
  const [sidebarVisible, setSidebarVisible] = useState(true);

  useEffect(() => {
    window.api.loadConfig().then((config) => {
      if (config) {
        useChatStore.getState().setConfig(config);
        useChatStore.getState().setNickname(config.user);
      }
    });

    const unsubMessage = window.api.onChatMessage((message) => {
      const { nickname: me, activeRoom: currentRoom } = useChatStore.getState();
      const isShakeSignal = message.content.trim() === SHAKE_TOKEN;
      if (isShakeSignal) {
        // Show who sent the shake in chat
        const sender = message.sender === me ? 'You' : message.sender;
        useChatStore.getState().addMessage({
          id: `shake_${Date.now()}`,
          room: message.room,
          sender: '*',
          content: `${sender} sent a room shake`,
          timestamp: Date.now(),
          type: 'system',
        });
        window.api.shakeWindow();
        return;
      }
      useChatStore.getState().addMessage(message);
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
        window.api.requestUsers();
      } else if (nextStatus === 'disconnected') {
        useChatStore.getState().setShowLogin(true);
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
    if (status === 'connected') {
      window.api.requestUsers();
    }
  }, [activeRoom, status]);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    document.title = privacyMode ? 'VsCodeEn' : 'SSHChat';
  }, [theme, privacyMode]);

  return (
    <RendererErrorBoundary>
      <div className="app-container">
        <div className="titlebar">
          <span className="titlebar-icon">{'</>'}</span>
          <span className="titlebar-title">
            {privacyMode ? 'VsCodeEn' : `SSHChat${status === 'connected' ? '' : ' (Offline)'}`}
          </span>
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
