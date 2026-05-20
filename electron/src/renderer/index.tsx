import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './styles/vscode-dark.css';

if (!(window as any).api) {
  const isElectronRuntime = navigator.userAgent.toLowerCase().includes('electron');
  const noopUnsub = () => {};
  (window as any).api = {
    loadConfig: async () => null,
    saveConfig: async () => true,
    connect: async () => ({
      success: false,
      error: isElectronRuntime
        ? 'Electron runtime detected, but preload API injection failed. Please restart app.'
        : 'Browser preview mode: run in Electron for real connection.',
    }),
    disconnect: async () => true,
    isConnected: async () => false,
    sendMessage: async () => false,
    joinRoom: async () => false,
    switchRoom: async () => false,
    requestUsers: async () => false,
    requestNews: async () => false,
    notifyAttention: async () => true,
    onChatMessage: () => noopUnsub,
    onRoomUpdate: () => noopUnsub,
    onUserUpdate: () => noopUnsub,
    onConnectionStatus: () => noopUnsub,
    onError: () => noopUnsub,
  };
  if (isElectronRuntime) {
    // eslint-disable-next-line no-console
    console.error('[VsCodeEn] preload API missing in Electron runtime');
  }
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
