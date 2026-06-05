import React from 'react';
import { useChatStore } from '../store/chatStore';
import { useTranslation } from '../i18n';
import RoomList from './RoomList';
import UserList from './UserList';
import NewsPanel from './NewsPanel';
import LibraryPanel from './LibraryPanel';
import MonitorPanel from './MonitorPanel';

export default function Sidebar() {
  const { sidebarView } = useChatStore();
  const { t } = useTranslation();

  const titles = {
    rooms: t('sidebar.rooms'),
    users: t('sidebar.users'),
    news: t('sidebar.news'),
    library: t('sidebar.library'),
    monitor: t('sidebar.monitor'),
  };

  return (
    <div className="sidebar">
      <div className="sidebar-header">{titles[sidebarView]}</div>
      <div className="sidebar-content">
        {sidebarView === 'rooms' && <RoomList />}
        {sidebarView === 'users' && <UserList />}
        {sidebarView === 'news' && <NewsPanel />}
        {sidebarView === 'library' && <LibraryPanel />}
        {sidebarView === 'monitor' && <MonitorPanel />}
      </div>
    </div>
  );
}
