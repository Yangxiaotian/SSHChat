import React from 'react';
import { useChatStore } from '../store/chatStore';
import RoomList from './RoomList';
import UserList from './UserList';
import NewsPanel from './NewsPanel';
import MonitorPanel from './MonitorPanel';

export default function Sidebar() {
  const { sidebarView, privacyMode } = useChatStore();

  const titles = privacyMode
    ? {
        rooms: 'EXPLORER',
        users: 'TEAM',
        news: 'FEED',
        monitor: 'WATCHER',
      }
    : {
        rooms: 'ROOMS',
        users: 'ONLINE USERS',
        news: 'NEWS',
        monitor: 'MONITOR',
      };

  return (
    <div className="sidebar">
      <div className="sidebar-header">{titles[sidebarView]}</div>
      <div className="sidebar-content">
        {sidebarView === 'rooms' && <RoomList />}
        {sidebarView === 'users' && <UserList />}
        {sidebarView === 'news' && <NewsPanel />}
        {sidebarView === 'monitor' && <MonitorPanel />}
      </div>
    </div>
  );
}
