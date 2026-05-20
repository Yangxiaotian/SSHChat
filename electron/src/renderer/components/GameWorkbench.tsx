import React, { useMemo, useState } from 'react';
import { useChatStore } from '../store/chatStore';

type GameKind = 'none' | 'chess' | 'gomoku' | 'xiangqi' | 'sanguo' | 'werewolf';

function detectGameKind(text: string): GameKind {
  const t = text.toLowerCase();
  if (t.includes('chess')) return 'chess';
  if (t.includes('gomoku')) return 'gomoku';
  if (t.includes('xiangqi') || t.includes('cchess')) return 'xiangqi';
  if (t.includes('sanguo') || t.includes('sgs')) return 'sanguo';
  if (t.includes('werewolf') || t.includes('langrensha') || t.includes('狼人')) return 'werewolf';
  return 'none';
}

function extractBoardBlock(systemLines: string[]): { board: string; game: GameKind } {
  const headers = ['chess', 'gomoku', 'xiangqi', 'sanguo', 'werewolf'];
  let start = -1;
  let game: GameKind = 'none';
  for (let i = systemLines.length - 1; i >= 0; i--) {
    const line = systemLines[i].toLowerCase();
    const hit = headers.find((h) => line.includes(`${h} `) || line.includes(`${h}(`) || line.includes(`${h}对局`));
    if (hit) {
      start = i;
      game = hit as GameKind;
      break;
    }
  }
  if (start < 0) return { board: '', game: 'none' };

  const out: string[] = [];
  for (let i = start; i < systemLines.length; i++) {
    const line = systemLines[i];
    if (line.startsWith('---') && out.length > 0) break;
    out.push(line);
  }
  return { board: out.join('\n'), game };
}

const quickByGame: Record<GameKind, Array<{ label: string; cmd: string }>> = {
  none: [
    { label: 'New Chess', cmd: '/game new chess' },
    { label: 'New Gomoku', cmd: '/game new gomoku' },
    { label: 'New Xiangqi', cmd: '/game new xiangqi' },
    { label: 'New Sanguo', cmd: '/game new sanguo' },
    { label: 'New Werewolf', cmd: '/game new werewolf' },
  ],
  chess: [
    { label: 'Show', cmd: '/game show' },
    { label: 'Join', cmd: '/game join' },
    { label: 'Seats', cmd: '/game seats' },
    { label: 'PGN', cmd: '/game pgn' },
    { label: 'Resign', cmd: '/game resign' },
  ],
  gomoku: [
    { label: 'Show', cmd: '/game show' },
    { label: 'Join', cmd: '/game join' },
    { label: 'Seats', cmd: '/game seats' },
    { label: 'Resign', cmd: '/game resign' },
    { label: 'Abort', cmd: '/game abort' },
  ],
  xiangqi: [
    { label: 'Show', cmd: '/game show' },
    { label: 'Join', cmd: '/game join' },
    { label: 'Seats', cmd: '/game seats' },
    { label: 'Resign', cmd: '/game resign' },
    { label: 'Abort', cmd: '/game abort' },
  ],
  sanguo: [
    { label: 'Show', cmd: '/game show' },
    { label: 'Start', cmd: '/game move start' },
    { label: 'Seats', cmd: '/game seats' },
    { label: 'Generals', cmd: '/game move generals' },
    { label: 'Pass', cmd: '/game move pass' },
  ],
  werewolf: [
    { label: 'Show', cmd: '/game show' },
    { label: 'Join', cmd: '/game join' },
    { label: 'Seats', cmd: '/game seats' },
    { label: 'Start', cmd: '/game move start' },
    { label: 'Vote', cmd: '/game move vote ' },
  ],
};

export default function GameWorkbench() {
  const { messages, activeRoom, privacyMode, status, setComposerText } = useChatStore();
  const [moveText, setMoveText] = useState('');
  const roomMessages = messages.get(activeRoom) || [];

  const { board, game } = useMemo(() => {
    const systemLines = roomMessages
      .filter((m) => m.type === 'system' || m.type === 'game')
      .map((m) => m.content);
    const parsed = extractBoardBlock(systemLines);
    if (parsed.game === 'none' && parsed.board) {
      return { board: parsed.board, game: detectGameKind(parsed.board) };
    }
    return parsed;
  }, [roomMessages]);

  const send = async (cmd: string) => {
    if (status !== 'connected') return false;
    return window.api.sendMessage(cmd);
  };

  const onMove = async () => {
    const t = moveText.trim();
    if (!t) return;
    const cmd = t.startsWith('/') ? t : `/game move ${t}`;
    await send(cmd);
    setMoveText('');
  };

  const fillToComposer = (cmd: string) => {
    setComposerText(cmd + ' ');
  };

  return (
    <div className="game-workbench">
      <div className="game-workbench-header">
        <span>{privacyMode ? 'Diagnostics View' : 'Game Workbench'}</span>
        <div className="game-workbench-actions">
          <button className="mini-btn" disabled={status !== 'connected'} onClick={() => fillToComposer('/game show')}>Show Cmd</button>
          <button className="mini-btn" disabled={status !== 'connected'} onClick={() => fillToComposer('/game help')}>Help Cmd</button>
          <button className="mini-btn" disabled={status !== 'connected'} onClick={() => fillToComposer('/game list')}>List Cmd</button>
        </div>
      </div>

      <div className="game-workbench-quick">
        {quickByGame[game].map((q) => (
          <button key={q.label} className="mini-btn" disabled={status !== 'connected'} onClick={() => fillToComposer(q.cmd)}>
            {q.label}
          </button>
        ))}
      </div>

      <pre className="game-workbench-body">{board || 'No active board in this room.'}</pre>

      <div className="game-workbench-input">
        <input
          className="input-field"
          value={moveText}
          onChange={(e) => setMoveText(e.target.value)}
          placeholder={privacyMode ? 'Run command...' : 'Enter move...'}
          disabled={status !== 'connected'}
          onKeyDown={(e) => {
            if (e.key === 'Enter') onMove();
          }}
        />
        <button className="send-button" onClick={onMove} disabled={status !== 'connected' || !moveText.trim()}>Apply</button>
      </div>
    </div>
  );
}
