import React, { useMemo, useState } from 'react';
import { useChatStore } from '../store/chatStore';

type GameKind = 'none' | 'chess' | 'gomoku' | 'xiangqi' | 'sanguo' | 'werewolf' | 'holdem' | 'zjh';

const FILES = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'];
const RANKS = ['1', '2', '3', '4', '5', '6', '7', '8'];

function detectGameKind(text: string): GameKind {
  const t = text.toLowerCase();
  if (t.includes('chess')) return 'chess';
  if (t.includes('gomoku')) return 'gomoku';
  if (t.includes('xiangqi') || t.includes('cchess')) return 'xiangqi';
  if (t.includes('sanguo') || t.includes('sgs')) return 'sanguo';
  if (t.includes('werewolf') || t.includes('langrensha') || t.includes('狼人')) return 'werewolf';
  if (t.includes('holdem') || t.includes('texas') || t.includes('poker') || t.includes('德州')) return 'holdem';
  if (t.includes('zjh') || t.includes('zhajinhua') || t.includes('炸金花')) return 'zjh';
  return 'none';
}

function extractBoardBlock(systemLines: string[]): { board: string; game: GameKind } {
  const headers = ['chess', 'gomoku', 'xiangqi', 'sanguo', 'werewolf', 'holdem', 'zjh'];
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
    { label: 'New Holdem', cmd: '/game new holdem' },
    { label: 'New ZJH', cmd: '/game new zjh' },
  ],
  chess: [
    { label: 'Show', cmd: '/game show' },
    { label: 'Join', cmd: '/game join' },
    { label: 'Seats', cmd: '/game seats' },
    { label: 'Undo', cmd: '/game undo' },
    { label: 'Accept Undo', cmd: '/game undo accept' },
    { label: 'PGN', cmd: '/game pgn' },
    { label: 'Resign', cmd: '/game resign' },
  ],
  gomoku: [
    { label: 'Show', cmd: '/game show' },
    { label: 'Join', cmd: '/game join' },
    { label: 'Seats', cmd: '/game seats' },
    { label: 'Undo', cmd: '/game undo' },
    { label: 'Accept Undo', cmd: '/game undo accept' },
    { label: 'Resign', cmd: '/game resign' },
    { label: 'Abort', cmd: '/game abort' },
  ],
  xiangqi: [
    { label: 'Show', cmd: '/game show' },
    { label: 'Join', cmd: '/game join' },
    { label: 'Seats', cmd: '/game seats' },
    { label: 'Undo', cmd: '/game undo' },
    { label: 'Accept Undo', cmd: '/game undo accept' },
    { label: 'Resign', cmd: '/game resign' },
    { label: 'Abort', cmd: '/game abort' },
  ],
  sanguo: [
    { label: 'Show', cmd: '/game show' },
    { label: 'Start', cmd: '/game move 开始' },
    { label: 'Seats', cmd: '/game seats' },
    { label: 'Generals', cmd: '/game move 武将' },
    { label: 'Pass', cmd: '/game move 过' },
  ],
  werewolf: [
    { label: 'Show', cmd: '/game show' },
    { label: 'Join', cmd: '/game join' },
    { label: 'Seats', cmd: '/game seats' },
    { label: 'Start', cmd: '/game move start' },
    { label: 'Pass', cmd: '/game move pass' },
  ],
  holdem: [
    { label: 'Show', cmd: '/game show' },
    { label: 'Join', cmd: '/game join' },
    { label: 'Seats', cmd: '/game seats' },
    { label: 'Start', cmd: '/game move start' },
    { label: 'Check', cmd: '/game move check' },
    { label: 'Call', cmd: '/game move call' },
    { label: 'All-in', cmd: '/game move allin' },
    { label: 'Fold', cmd: '/game move fold' },
  ],
  zjh: [
    { label: 'Show', cmd: '/game show' },
    { label: 'Join', cmd: '/game join' },
    { label: 'Seats', cmd: '/game seats' },
    { label: 'Start', cmd: '/game move start' },
    { label: 'Look', cmd: '/game move look' },
    { label: 'Follow', cmd: '/game move follow' },
    { label: 'Fold', cmd: '/game move fold' },
  ],
};

export default function GameWorkbench() {
  const { messages, activeRoom, privacyMode, status, setComposerText, users } = useChatStore();
  const [moveText, setMoveText] = useState('');
  const [raiseAmount, setRaiseAmount] = useState('10');
  const [zjhRaiseAmount, setZjhRaiseAmount] = useState('1');
  const [chessFrom, setChessFrom] = useState<string | null>(null);
  const [xqFrom, setXqFrom] = useState<{ r: number; c: number } | null>(null);
  const [targetName, setTargetName] = useState('');

  const roomMessages = messages.get(activeRoom) || [];
  const { board, game } = useMemo(() => {
    const systemLines = roomMessages.filter((m) => m.type === 'system' || m.type === 'game').map((m) => m.content);
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

  const sendMove = async (payload: string) => {
    const text = payload.trim();
    if (!text) return;
    await send(`/game move ${text}`);
  };

  const onMove = async () => {
    const t = moveText.trim();
    if (!t) return;
    const cmd = t.startsWith('/') ? t : `/game move ${t}`;
    await send(cmd);
    setMoveText('');
  };

  const fillToComposer = (cmd: string) => setComposerText(`${cmd} `);

  const onChessCellClick = async (sq: string) => {
    if (!chessFrom) {
      setChessFrom(sq);
      return;
    }
    await sendMove(`${chessFrom}${sq}`);
    setChessFrom(null);
  };

  const onGomokuCellClick = async (row: number, col: number) => {
    await sendMove(`${row} ${col}`);
  };

  const onXqCellClick = async (row: number, col: number) => {
    if (!xqFrom) {
      setXqFrom({ r: row, c: col });
      return;
    }
    await sendMove(`${xqFrom.r} ${xqFrom.c} ${row} ${col}`);
    setXqFrom(null);
  };

  const playerCandidates = users;

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

      {game === 'chess' && (
        <div className="game-interaction-panel">
          <div className="game-interaction-title">Chess Board Click Mode (click from {'->'} to)</div>
          <div className="chess-grid">
            {Array.from({ length: 8 }, (_, rIx) => {
              const rank = 8 - rIx;
              return FILES.map((f, cIx) => {
                const sq = `${f}${rank}`;
                const dark = (rIx + cIx) % 2 === 1;
                const selected = chessFrom === sq;
                return (
                  <button
                    key={sq}
                    className={`chess-cell ${dark ? 'dark' : 'light'} ${selected ? 'selected' : ''}`}
                    onClick={() => onChessCellClick(sq)}
                    disabled={status !== 'connected'}
                    title={sq}
                  >
                    <span>{sq}</span>
                  </button>
                );
              });
            })}
          </div>
        </div>
      )}

      {game === 'gomoku' && (
        <div className="game-interaction-panel">
          <div className="game-interaction-title">Gomoku Board Click Mode</div>
          <div className="gomoku-grid">
            {Array.from({ length: 15 }, (_, rIx) =>
              Array.from({ length: 15 }, (_, cIx) => {
                const row = rIx + 1;
                const col = cIx + 1;
                return (
                  <button
                    key={`${row}-${col}`}
                    className="gomoku-cell"
                    onClick={() => onGomokuCellClick(row, col)}
                    disabled={status !== 'connected'}
                    title={`${row},${col}`}
                  >
                    {row === 8 && col === 8 ? '◎' : '·'}
                  </button>
                );
              }),
            )}
          </div>
        </div>
      )}

      {game === 'xiangqi' && (
        <div className="game-interaction-panel">
          <div className="game-interaction-title">Xiangqi Board Click Mode (click from {'->'} to)</div>
          <div className="xiangqi-grid">
            {Array.from({ length: 10 }, (_, rIx) =>
              Array.from({ length: 9 }, (_, cIx) => {
                const row = rIx + 1;
                const col = cIx + 1;
                const selected = xqFrom?.r === row && xqFrom?.c === col;
                return (
                  <button
                    key={`${row}-${col}`}
                    className={`xiangqi-cell ${selected ? 'selected' : ''}`}
                    onClick={() => onXqCellClick(row, col)}
                    disabled={status !== 'connected'}
                    title={`${row},${col}`}
                  >
                    {row},{col}
                  </button>
                );
              }),
            )}
          </div>
        </div>
      )}

      {game === 'sanguo' && (
        <div className="game-interaction-panel">
          <div className="game-interaction-title">Sanguo Action Panel</div>
          <div className="game-chip-row">
            {playerCandidates.map((u) => (
              <button key={u} className={`mini-btn ${targetName === u ? 'active' : ''}`} onClick={() => setTargetName(u)}>{u}</button>
            ))}
          </div>
          <div className="game-chip-row">
            <button className="mini-btn" disabled={!targetName || status !== 'connected'} onClick={() => sendMove(`杀 ${targetName}`)}>杀</button>
            <button className="mini-btn" disabled={!targetName || status !== 'connected'} onClick={() => sendMove(`决斗 ${targetName}`)}>决斗</button>
            <button className="mini-btn" disabled={!targetName || status !== 'connected'} onClick={() => sendMove(`火攻 ${targetName}`)}>火攻</button>
            <button className="mini-btn" disabled={status !== 'connected'} onClick={() => sendMove('过')}>过</button>
          </div>
        </div>
      )}

      {game === 'werewolf' && (
        <div className="game-interaction-panel">
          <div className="game-interaction-title">Werewolf Action Panel</div>
          <div className="game-chip-row">
            {playerCandidates.map((u) => (
              <button key={u} className={`mini-btn ${targetName === u ? 'active' : ''}`} onClick={() => setTargetName(u)}>{u}</button>
            ))}
          </div>
          <div className="game-chip-row">
            <button className="mini-btn" disabled={!targetName || status !== 'connected'} onClick={() => sendMove(`vote ${targetName}`)}>Vote</button>
            <button className="mini-btn" disabled={!targetName || status !== 'connected'} onClick={() => sendMove(`kill ${targetName}`)}>Kill</button>
            <button className="mini-btn" disabled={!targetName || status !== 'connected'} onClick={() => sendMove(`check ${targetName}`)}>Check</button>
            <button className="mini-btn" disabled={status !== 'connected'} onClick={() => sendMove('save')}>Save</button>
            <button className="mini-btn" disabled={!targetName || status !== 'connected'} onClick={() => sendMove(`poison ${targetName}`)}>Poison</button>
          </div>
        </div>
      )}

      {game === 'holdem' && (
        <div className="game-interaction-panel">
          <div className="game-interaction-title">Texas Holdem Action Panel</div>
          <div className="game-chip-row">
            <button className="mini-btn" disabled={status !== 'connected'} onClick={() => sendMove('start')}>Start</button>
            <button className="mini-btn" disabled={status !== 'connected'} onClick={() => sendMove('check')}>Check</button>
            <button className="mini-btn" disabled={status !== 'connected'} onClick={() => sendMove('call')}>Call</button>
            <button className="mini-btn" disabled={status !== 'connected'} onClick={() => sendMove('allin')}>All-in</button>
            <button className="mini-btn" disabled={status !== 'connected'} onClick={() => sendMove('fold')}>Fold</button>
          </div>
          <div className="game-chip-row">
            <input
              className="monitor-input"
              value={raiseAmount}
              onChange={(e) => setRaiseAmount(e.target.value)}
              placeholder="raise amount"
              disabled={status !== 'connected'}
            />
            <button
              className="mini-btn"
              disabled={status !== 'connected' || !raiseAmount.trim()}
              onClick={() => sendMove(`raise ${raiseAmount.trim()}`)}
            >
              Raise
            </button>
          </div>
        </div>
      )}

      {game === 'zjh' && (
        <div className="game-interaction-panel">
          <div className="game-interaction-title">Zha Jin Hua Panel</div>
          <div className="game-chip-row">
            <button className="mini-btn" disabled={status !== 'connected'} onClick={() => sendMove('start')}>Start</button>
            <button className="mini-btn" disabled={status !== 'connected'} onClick={() => sendMove('look')}>Look</button>
            <button className="mini-btn" disabled={status !== 'connected'} onClick={() => sendMove('follow')}>Follow</button>
            <button className="mini-btn" disabled={status !== 'connected'} onClick={() => sendMove('fold')}>Fold</button>
          </div>
          <div className="game-chip-row">
            <input
              className="monitor-input"
              value={zjhRaiseAmount}
              onChange={(e) => setZjhRaiseAmount(e.target.value)}
              placeholder="raise amount"
              disabled={status !== 'connected'}
            />
            <button
              className="mini-btn"
              disabled={status !== 'connected' || !zjhRaiseAmount.trim()}
              onClick={() => sendMove(`raise ${zjhRaiseAmount.trim()}`)}
            >
              Raise
            </button>
          </div>
          <div className="game-chip-row">
            {playerCandidates.map((u) => (
              <button key={u} className={`mini-btn ${targetName === u ? 'active' : ''}`} onClick={() => setTargetName(u)}>{u}</button>
            ))}
            <button
              className="mini-btn"
              disabled={status !== 'connected' || !targetName}
              onClick={() => sendMove(`compare ${targetName}`)}
            >
              Compare
            </button>
          </div>
        </div>
      )}

      <pre className="game-workbench-body">{board || 'No active board in this room.'}</pre>

      <div className="game-workbench-input">
        <textarea
          className="game-workbench-command"
          value={moveText}
          onChange={(e) => setMoveText(e.target.value)}
          placeholder={privacyMode ? 'Run command...' : 'Enter move...'}
          disabled={status !== 'connected'}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              onMove();
            }
          }}
        />
        <button className="send-button" onClick={onMove} disabled={status !== 'connected' || !moveText.trim()}>Apply</button>
      </div>
    </div>
  );
}
