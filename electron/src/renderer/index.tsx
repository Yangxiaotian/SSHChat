import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './styles/vscode-dark.css';

if (!(window as any).api) {
  const noopUnsub = () => {};
  (window as any).api = {
    loadConfig: async () => null,
    saveConfig: async () => true,
    connect: async () => ({ success: false, error: 'Browser preview mode: run in Electron for real connection.' }),
    disconnect: async () => true,
    isConnected: async () => false,
    sendMessage: async () => false,
    joinRoom: async () => false,
    switchRoom: async () => false,
    requestUsers: async () => false,
    requestNews: async () => false,
    onChatMessage: () => noopUnsub,
    onRoomUpdate: () => noopUnsub,
    onUserUpdate: () => noopUnsub,
    onConnectionStatus: () => noopUnsub,
    onError: () => noopUnsub,
  };
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
