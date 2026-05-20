import React, { useState } from 'react';
import { useChatStore } from '../store/chatStore';

export default function RoomList() {
  const { rooms, activeRoom, setActiveRoom } = useChatStore();
  const [expanded, setExpanded] = useState(true);
  const [showJoinDialog, setShowJoinDialog] = useState(false);
  const [newRoomName, setNewRoomName] = useState('');

  const handleRoomClick = (roomName: string) => {
    setActiveRoom(roomName);
    window.api.switchRoom(roomName);
  };

  const handleJoin = () => {
    const name = newRoomName.trim();
    if (!name) return;
    window.api.joinRoom(name);
    setNewRoomName('');
    setShowJoinDialog(false);
  };

  return (
    <div className="room-list">
      <div className="room-section-header" onClick={() => setExpanded(!expanded)}>
        <span className="room-section-icon">{expanded ? 'v' : '>'}</span>
        <span style={{ flex: 1 }}>ROOMS</span>
        <span
          className="sidebar-header-action"
          onClick={(e) => {
            e.stopPropagation();
            setShowJoinDialog(true);
          }}
          style={{ opacity: 1 }}
          title="Join Room"
        >
          +
        </span>
      </div>

      {showJoinDialog && (
        <div style={{ padding: '4px 12px 8px 24px' }}>
          <input
            type="text"
            className="input-field"
            value={newRoomName}
            onChange={(e) => setNewRoomName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleJoin();
              if (e.key === 'Escape') {
                setShowJoinDialog(false);
                setNewRoomName('');
              }
            }}
            placeholder="Room name..."
            autoFocus
            style={{ height: '26px', fontSize: '12px' }}
          />
          <div style={{ display: 'flex', gap: '4px', marginTop: '4px' }}>
            <button className="send-button" onClick={handleJoin} style={{ height: '24px', padding: '0 8px', fontSize: '11px' }}>
              Join
            </button>
            <button
              className="send-button"
              onClick={() => {
                setShowJoinDialog(false);
                setNewRoomName('');
              }}
              style={{ height: '24px', padding: '0 8px', fontSize: '11px', backgroundColor: 'var(--vscode-button-secondary)' }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {expanded && (
        <div>
          {rooms.map((room) => (
            <div
              key={room.name}
              className={`room-item ${activeRoom === room.name ? 'active' : ''}`}
              onClick={() => handleRoomClick(room.name)}
            >
              <span className="room-icon">#</span>
              <span className="room-name">{room.name}</span>
              {room.unreadCount > 0 && <span className="room-unread">{room.unreadCount}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
