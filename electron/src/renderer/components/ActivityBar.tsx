import React from 'react';
import { useChatStore } from '../store/chatStore';
import { useTranslation } from '../i18n';

interface ActivityBarProps {
  onToggleSidebar: () => void;
  sidebarVisible: boolean;
}

export default function ActivityBar({ onToggleSidebar, sidebarVisible }: ActivityBarProps) {
  const { sidebarView, setSidebarView } = useChatStore();
  const { t } = useTranslation();

  const topItems = [
    { id: 'rooms' as const, icon: 'E', tooltip: t('activity.rooms') },
    { id: 'users' as const, icon: 'U', tooltip: t('activity.users') },
    { id: 'news' as const, icon: 'N', tooltip: t('activity.news') },
    { id: 'library' as const, icon: 'L', tooltip: t('activity.library') },
    { id: 'monitor' as const, icon: 'M', tooltip: t('activity.monitor') },
  ];

  const bottomItems = [
    { id: 'settings' as const, icon: 'S', tooltip: t('activity.settings') },
  ];

  const handleItemClick = (id: string) => {
    if (id === 'settings') {
      return;
    }
    if (sidebarView === id && sidebarVisible) {
      onToggleSidebar();
    } else {
      setSidebarView(id as 'rooms' | 'users' | 'news' | 'library' | 'monitor');
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
