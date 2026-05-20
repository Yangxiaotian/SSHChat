import React from 'react';
import { useChatStore } from '../store/chatStore';

export default function TabBar() {
  const { rooms, activeRoom, setActiveRoom, removeRoom } = useChatStore();

  const handleTabClick = (roomName: string) => {
    setActiveRoom(roomName);
    window.api.switchRoom(roomName);
  };

  const handleClose = (e: React.MouseEvent, roomName: string) => {
    e.stopPropagation();
    if (roomName === 'default') return;
    window.api.sendMessage(`/part ${roomName}`);
    removeRoom(roomName);
  };

  return (
    <div className="tabbar">
      {rooms.map((room) => (
        <div
          key={room.name}
          className={`tab ${activeRoom === room.name ? 'active' : ''}`}
          onClick={() => handleTabClick(room.name)}
        >
          <span># {room.name}</span>
          {room.unreadCount > 0 && <span className="tab-unread">{room.unreadCount}</span>}
          {room.name !== 'default' && (
            <span className="tab-close" onClick={(e) => handleClose(e, room.name)}>
              x
            </span>
          )}
        </div>
      ))}
    </div>
  );
}
