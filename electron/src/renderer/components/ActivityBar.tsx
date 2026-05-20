import React from 'react';
import { useChatStore } from '../store/chatStore';

interface ActivityBarProps {
  onToggleSidebar: () => void;
  sidebarVisible: boolean;
}

export default function ActivityBar({ onToggleSidebar, sidebarVisible }: ActivityBarProps) {
  const { sidebarView, setSidebarView } = useChatStore();

  const topItems = [
    { id: 'rooms' as const, icon: 'E', tooltip: 'Explorer' },
    { id: 'users' as const, icon: 'U', tooltip: 'Online Users' },
    { id: 'news' as const, icon: 'N', tooltip: 'News' },
  ];

  const bottomItems = [
    { id: 'settings' as const, icon: 'S', tooltip: 'Settings' },
  ];

  const handleItemClick = (id: string) => {
    if (id === 'settings') {
      return;
    }
    if (sidebarView === id && sidebarVisible) {
      onToggleSidebar();
    } else {
      setSidebarView(id as 'rooms' | 'users' | 'news');
      if (!sidebarVisible) onToggleSidebar();
    }
  };

  return (
    <div className="activitybar">
      <div className="activitybar-top">
        {topItems.map((item) => (
          <div
            key={item.id}
            className={`activitybar-item ${sidebarView === item.id && sidebarVisible ? 'active' : ''}`}
            onClick={() => handleItemClick(item.id)}
            title={item.tooltip}
          >
            <span className="activitybar-icon">{item.icon}</span>
          </div>
        ))}
      </div>
      <div className="activitybar-bottom">
        {bottomItems.map((item) => (
          <div
            key={item.id}
            className="activitybar-item"
            onClick={() => handleItemClick(item.id)}
            title={item.tooltip}
          >
            <span className="activitybar-icon">{item.icon}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
