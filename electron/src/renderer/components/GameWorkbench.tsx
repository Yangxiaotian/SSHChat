import React, { useMemo, useState } from 'react';
import { useChatStore } from '../store/chatStore';
import ChessPanel from './games/ChessPanel';
import GomokuPanel from './games/GomokuPanel';
import XiangqiPanel from './games/XiangqiPanel';
import HoldemPanel from './games/HoldemPanel';
import ZjhPanel from './games/ZjhPanel';
import SanguoPanel from './games/SanguoPanel';
import WerewolfPanel from './games/WerewolfPanel';
import NiuTouPanel from './games/NiuTouPanel';
import { GameCommandFactory, quickByGame } from './games/commandFactory';
import { GameKind } from './games/types';

function detectGameKind(text: string): GameKind {
  const t = text.toLowerCase();
  if (t.includes('chess')) return 'chess';
  if (t.includes('gomoku')) return 'gomoku';
  if (t.includes('xiangqi') || t.includes('cchess')) return 'xiangqi';
  if (t.includes('sanguo') || t.includes('sgs')) return 'sanguo';
  if (t.includes('werewolf') || t.includes('langrensha') || t.includes('狼人')) return 'werewolf';
  if (t.includes('holdem') || t.includes('texas') || t.includes('poker') || t.includes('德州')) return 'holdem';
  if (t.includes('zjh') || t.includes('zhajinhua') || t.includes('炸金花')) return 'zjh';
  if (t.includes('niutou') || t.includes('ntw') || t.includes('牛头王') || t.includes('6 nimmt')) return 'niutou';
  return 'none';
}

function extractBoardBlock(systemLines: string[]): { board: string; game: GameKind } {
  const headers = ['chess', 'gomoku', 'xiangqi', 'sanguo', 'werewolf', 'holdem', 'zjh', 'niutou'];
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

const gameTips: Record<GameKind, string> = {
  none: '先用上方快捷按钮创建游戏，优先使用点击交互，不必手敲命令。',
  chess: '点击棋盘两次完成走子（起点 -> 终点）。',
  gomoku: '直接点击落子点即可发送坐标。',
  xiangqi: '点击棋子起点后再点终点完成走子。',
  sanguo: '先选目标玩家，再点技能按钮执行。',
  werewolf: '先选目标玩家，再点投票/刀人/查验/毒人。',
  holdem: '优先用按钮操作：过牌/跟注/加注/全下/弃牌。',
  zjh: '先看牌再决策，支持跟注、加注、比牌、弃牌。',
  niutou: '每回合先选一张牌；若小于所有行尾，必须选择吃一行。',
};

export default function GameWorkbench() {
  const { messages, activeRoom, privacyMode, status, setComposerText, users } = useChatStore();
  const [moveText, setMoveText] = useState('');

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
    await send(GameCommandFactory.move(text));
  };

  const onMove = async () => {
    const t = moveText.trim();
    if (!t) return;
    const cmd = t.startsWith('/') ? t : GameCommandFactory.move(t);
    await send(cmd);
    setMoveText('');
  };

  const fillToComposer = (cmd: string) => setComposerText(`${cmd} `);
  const disabled = status !== 'connected';

  return (
    <div className="game-workbench">
      <div className="game-workbench-header">
        <span>{privacyMode ? 'Diagnostics View' : 'Game Workbench'}</span>
        <div className="game-workbench-actions">
          <button className="mini-btn" disabled={disabled} onClick={() => fillToComposer('/game show')}>Show Cmd</button>
          <button className="mini-btn" disabled={disabled} onClick={() => fillToComposer('/game help')}>Help Cmd</button>
          <button className="mini-btn" disabled={disabled} onClick={() => fillToComposer('/game list')}>List Cmd</button>
        </div>
      </div>

      <div className="game-workbench-quick">
        {quickByGame[game].map((q) => (
          <button key={q.label} className="mini-btn" disabled={disabled} onClick={() => fillToComposer(q.cmd)}>
            {q.label}
          </button>
        ))}
      </div>
      <div className="game-workbench-hint">{gameTips[game]}</div>

      {game === 'chess' && <ChessPanel disabled={disabled} users={users} sendMove={sendMove} />}
      {game === 'gomoku' && <GomokuPanel disabled={disabled} onPick={(r, c) => send(GameCommandFactory.gomokuMove(r, c))} />}
      {game === 'xiangqi' && <XiangqiPanel disabled={disabled} onMove={(fr, fc, tr, tc) => send(GameCommandFactory.xiangqiCoordMove(fr, fc, tr, tc))} />}

      {game === 'sanguo' && <SanguoPanel disabled={disabled} users={users} onCmd={(cmd) => sendMove(cmd)} />}
      {game === 'werewolf' && <WerewolfPanel disabled={disabled} users={users} onCmd={(cmd) => sendMove(cmd)} />}

      {game === 'holdem' && <HoldemPanel disabled={disabled} onCmd={(cmd) => sendMove(cmd)} boardText={board} />}
      {game === 'zjh' && <ZjhPanel disabled={disabled} users={users} onCmd={(cmd) => sendMove(cmd)} boardText={board} />} 
      {game === 'niutou' && <NiuTouPanel disabled={disabled} boardText={board} onCmd={(cmd) => sendMove(cmd)} />}

      <pre className="game-workbench-body">{board || 'No active board in this room.'}</pre>

      <div className="game-workbench-input">
        <textarea
          className="game-workbench-command"
          value={moveText}
          onChange={(e) => setMoveText(e.target.value)}
          placeholder={privacyMode ? 'Run command...' : 'Enter move...'}
          disabled={disabled}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              onMove();
            }
          }}
        />
        <button className="send-button" onClick={onMove} disabled={disabled || !moveText.trim()}>Apply</button>
      </div>
    </div>
  );
}


