import React from 'react';
import { useChatStore } from '../store/chatStore';
import RoomList from './RoomList';
import UserList from './UserList';
import NewsPanel from './NewsPanel';

export default function Sidebar() {
  const { sidebarView, privacyMode } = useChatStore();

  const titles = privacyMode
    ? {
        rooms: 'EXPLORER',
        users: 'TEAM',
        news: 'FEED',
      }
    : {
        rooms: 'ROOMS',
        users: 'ONLINE USERS',
        news: 'NEWS',
      };

  return (
    <div className="sidebar">
      <div className="sidebar-header">{titles[sidebarView]}</div>
      <div className="sidebar-content">
        {sidebarView === 'rooms' && <RoomList />}
        {sidebarView === 'users' && <UserList />}
        {sidebarView === 'news' && <NewsPanel />}
      </div>
    </div>
  );
}
