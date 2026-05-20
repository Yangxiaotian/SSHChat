import React, { useEffect, useState } from 'react';
import { useChatStore } from './store/chatStore';
import ActivityBar from './components/ActivityBar';
import Sidebar from './components/Sidebar';
import ChatArea from './components/ChatArea';
import StatusBar from './components/StatusBar';
import LoginDialog from './components/LoginDialog';

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
      useChatStore.getState().addMessage(message);
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
      if (rooms) {
        const roomInfos = rooms.map((name) => ({
          name,
          isDefault: name === 'default',
          unreadCount: 0,
          lastActivity: Date.now(),
        }));
        useChatStore.getState().setRooms(roomInfos);
      }
      useChatStore.getState().setActiveRoom(activeRoom);
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
  );
}
