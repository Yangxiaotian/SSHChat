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
    const trimmed = line.trim();
    if (trimmed.startsWith('---') && out.length > 0) break;
    if (/^\[\*\]\s/.test(trimmed) && out.length > 0) break;
    if (/^\/game\s+/i.test(trimmed) && out.length > 0) break;
    if (/^>>\s+/.test(trimmed) && out.length > 0) break;
    if (/^\[\d{2}:\d{2}:\d{2}\]/.test(trimmed) && out.length > 0) break;
    out.push(line);
  }
  return { board: out.join('\n'), game };
}

function isLikelyGameLine(line: string): boolean {
  const t = line.trim().toLowerCase();
  if (!t) return false;
  const keywords = [
    'chess',
    'gomoku',
    'xiangqi',
    'holdem',
    'zjh',
    'niutou',
    'sanguo',
    'werewolf',
    'state:',
    'turn:',
    'street:',
    'pot=',
    'row1:',
    'row2:',
    'row3:',
    'row4:',
    'your hand',
    '你的手牌',
    '公共牌',
    '开了一局',
    '对局',
  ];
  return keywords.some((k) => t.includes(k));
}

function inferOpenGame(lines: string[]): GameKind {
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i];
    if (!line.includes('开了一局') && !line.includes(' state:') && !line.includes('对局')) {
      continue;
    }
    const kind = detectGameKind(line);
    if (kind !== 'none') return kind;
  }
  return 'none';
}

function gameLabel(game: GameKind): string {
  const map: Record<GameKind, string> = {
    none: '游戏',
    chess: '国际象棋',
    gomoku: '五子棋',
    xiangqi: '中国象棋',
    sanguo: '三国杀',
    werewolf: '狼人杀',
    holdem: '德州扑克',
    zjh: '炸金花',
    niutou: '牛头王',
  };
  return map[game];
}

function parseTurnName(board: string): string {
  const line = board.split('\n').find((l) => /^(turn|轮到)[:：]/i.test(l.trim()));
  if (!line) return '';
  return line.replace(/^(turn|轮到)[:：]\s*/i, '').trim();
}

function inSeats(board: string, nickname: string): boolean {
  const esc = nickname.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const re = new RegExp(`^#\\d+\\s+${esc}:`, 'm');
  return re.test(board);
}

function hostName(board: string): string {
  const line = board.split('\n').find((l) => /^#1\s+[^:]+:/.test(l.trim()));
  if (!line) return '';
  const m = line.trim().match(/^#1\s+([^:]+):/);
  return m ? m[1].trim() : '';
}

function gameMoveHint(game: GameKind): string {
  const map: Record<GameKind, string> = {
    none: '可先点“可玩列表”或新开一局。',
    chess: '在棋盘上点起点和终点，或输入 UCI/SAN。',
    gomoku: '直接点棋盘落子点。',
    xiangqi: '先点棋子，再点目标位置。',
    sanguo: '按按钮出牌或“过”，必要时先看武将池。',
    werewolf: '先选目标玩家，再点对应技能。',
    holdem: '优先使用过牌/跟注/加注/全下/弃牌按钮。',
    zjh: '建议先看牌，再决定跟注、加注或比牌。',
    niutou: '先选手牌；若提示吃行，再选第1~4行。',
  };
  return map[game];
}

function latestIssue(lines: string[]): string {
  for (let i = lines.length - 1; i >= 0; i--) {
    const l = lines[i];
    if (
      l.includes('执行失败') ||
      l.includes('未知游戏') ||
      l.includes('not your turn') ||
      l.includes('not enough') ||
      l.includes('usage:')
    ) {
      return l;
    }
  }
  return '';
}

type Advisor = {
  title: string;
  detail: string;
  level: 'info' | 'warn' | 'error';
  primaryCmd?: string;
  primaryLabel?: string;
  secondaryCmd?: string;
  secondaryLabel?: string;
};

function buildAdvisor(game: GameKind, board: string, systemLines: string[], nickname: string): Advisor {
  const issue = latestIssue(systemLines);
  if (issue) {
    return {
      title: '上一步操作失败',
      detail: issue,
      level: 'error',
      primaryCmd: '/game show',
      primaryLabel: '刷新局面',
      secondaryCmd: '/game help',
      secondaryLabel: '查看玩法',
    };
  }
  if (game === 'none') {
    return {
      title: '当前房间暂无进行中的对局',
      detail: '你可以直接新开任意游戏，或先点“可玩列表”查看已上线游戏。',
      level: 'info',
      primaryCmd: '/game list',
      primaryLabel: '可玩列表',
    };
  }
  if (!board.trim()) {
    return {
      title: `检测到 ${gameLabel(game)} 对局`,
      detail: '可先加入对局，再显示局面。',
      level: 'info',
      primaryCmd: '/game join',
      primaryLabel: '加入对局',
      secondaryCmd: '/game show',
      secondaryLabel: '显示局面',
    };
  }
  const stateLine = board.split('\n').find((l) => l.includes('state:')) || '';
  const turn = parseTurnName(board);
  const joined = inSeats(board, nickname);
  if (!joined) {
    return {
      title: `当前房间正在进行 ${gameLabel(game)}，你可直接加入`,
      detail: '同一房间同一时刻只会有一场进行中的对局；加入后即可操作。',
      level: 'warn',
      primaryCmd: '/game join',
      primaryLabel: '加入对局',
      secondaryCmd: '/game seats',
      secondaryLabel: '查看席位',
    };
  }
  if (stateLine.includes('waiting')) {
    const host = hostName(board);
    if (host && host === nickname) {
      return {
        title: '你是房主，可以开始对局',
        detail: '开局后可按面板按钮操作；若人数不足会自动补机器人。',
        level: 'info',
        primaryCmd: '/game move start',
        primaryLabel: '开始对局',
        secondaryCmd: '/game seats',
        secondaryLabel: '查看席位',
      };
    }
    return {
      title: '已加入，等待房主开始',
      detail: host ? `当前房主：${host}` : '可用“查看席位”确认房主。',
      level: 'warn',
      primaryCmd: '/game seats',
      primaryLabel: '查看席位',
    };
  }
  if (turn) {
    if (turn === nickname) {
      return {
        title: '轮到你操作',
        detail: gameMoveHint(game),
        level: 'info',
        primaryCmd: '/game show',
        primaryLabel: '刷新局面',
      };
    }
    return {
      title: `当前轮到 ${turn}`,
      detail: '你可以先观察局面，提前规划下一步。',
      level: 'warn',
      primaryCmd: '/game show',
      primaryLabel: '刷新局面',
    };
  }
  return {
    title: `${gameLabel(game)} 对局进行中`,
    detail: gameMoveHint(game),
    level: 'info',
    primaryCmd: '/game show',
    primaryLabel: '刷新局面',
  };
}

function sanitizeBoard(raw: string): string {
  if (!raw.trim()) return '';
  const noisy = ['commands:', '/names /rooms /join', 'alerts(', 'alert sound backend'];
  const lines = raw
    .split('\n')
    .map((l) => l.replace(/\r/g, '').trimEnd())
    .filter((l) => !noisy.some((n) => l.toLowerCase().includes(n)));
  const out: string[] = [];
  let prev = '';
  for (const line of lines) {
    if (line.trim() === '' && prev.trim() === '') continue;
    if (line === prev) continue;
    out.push(line);
    prev = line;
  }
  return out.join('\n').trim();
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
  const { messages, activeRoom, privacyMode, status, setComposerText, users, nickname } = useChatStore();
  const [moveText, setMoveText] = useState('');
  const [showBoard, setShowBoard] = useState(true);
  const [showAdvanced, setShowAdvanced] = useState(false);

  const roomMessages = messages.get(activeRoom) || [];
  const { board, game, systemLines } = useMemo(() => {
    const allLines = roomMessages
      .filter((m) => m.type === 'system' || m.type === 'game')
      .map((m) => m.content);
    const gameLines = allLines.filter(isLikelyGameLine);
    const parsed = extractBoardBlock(gameLines.length > 0 ? gameLines : allLines);
    if (parsed.game === 'none' && parsed.board) {
      return { board: parsed.board, game: detectGameKind(parsed.board), systemLines: allLines };
    }
    if (parsed.game !== 'none') return { ...parsed, systemLines: allLines };
    const openGame = inferOpenGame(allLines);
    return { board: parsed.board, game: openGame, systemLines: allLines };
  }, [roomMessages]);
  const advisor = useMemo(
    () => buildAdvisor(game, board, systemLines, nickname),
    [game, board, systemLines, nickname],
  );
  const cleanBoard = useMemo(() => sanitizeBoard(board), [board]);
  const hasBoard = cleanBoard.length > 0;

  const send = async (cmd: string) => {
    if (status !== 'connected') return false;
    return window.api.sendMessage(cmd);
  };

  const sendMove = async (payload: string) => {
    const text = payload.trim();
    if (!text) return;
    if (text.startsWith('/')) {
      await send(text);
      return;
    }
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
        <span>{privacyMode ? '诊断视图' : '游戏工作台'} · 当前：{gameLabel(game)}</span>
        <div className="game-workbench-actions">
          <button className="mini-btn" disabled={disabled} onClick={() => fillToComposer('/game show')}>显示局面</button>
          <button className="mini-btn" disabled={disabled} onClick={() => fillToComposer('/game help')}>玩法帮助</button>
          <button className="mini-btn" disabled={disabled} onClick={() => fillToComposer('/game list')}>可玩列表</button>
          {game !== 'none' && (
            <button className="mini-btn" disabled={disabled} onClick={() => fillToComposer('/game end')}>结束对局</button>
          )}
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
      <div className={`game-advisor game-advisor-${advisor.level}`}>
        <div className="game-advisor-title">{advisor.title}</div>
        <div className="game-advisor-detail">{advisor.detail}</div>
        <div className="game-advisor-actions">
          {advisor.primaryCmd && advisor.primaryLabel && (
            <button className="mini-btn" disabled={disabled} onClick={() => fillToComposer(advisor.primaryCmd)}>
              {advisor.primaryLabel}
            </button>
          )}
          {advisor.secondaryCmd && advisor.secondaryLabel && (
            <button className="mini-btn" disabled={disabled} onClick={() => fillToComposer(advisor.secondaryCmd)}>
              {advisor.secondaryLabel}
            </button>
          )}
        </div>
      </div>

      {game === 'chess' && <ChessPanel disabled={disabled} users={users} sendMove={sendMove} />}
      {game === 'gomoku' && <GomokuPanel disabled={disabled} onPick={(r, c) => send(GameCommandFactory.gomokuMove(r, c))} />}
      {game === 'xiangqi' && <XiangqiPanel disabled={disabled} onMove={(fr, fc, tr, tc) => send(GameCommandFactory.xiangqiCoordMove(fr, fc, tr, tc))} />}

      {game === 'sanguo' && <SanguoPanel disabled={disabled} users={users} onCmd={(cmd) => sendMove(cmd)} />}
      {game === 'werewolf' && <WerewolfPanel disabled={disabled} users={users} onCmd={(cmd) => sendMove(cmd)} />}

      {game === 'holdem' && <HoldemPanel disabled={disabled} onCmd={(cmd) => sendMove(cmd)} boardText={board} />}
      {game === 'zjh' && <ZjhPanel disabled={disabled} users={users} onCmd={(cmd) => sendMove(cmd)} boardText={board} />} 
      {game === 'niutou' && <NiuTouPanel disabled={disabled} boardText={board} onCmd={(cmd) => sendMove(cmd)} />}

      <div className="game-workbench-toolbar">
        <span className="game-workbench-hint-inline">同一房间同一时刻仅允许一场进行中的对局</span>
        <div className="game-workbench-toolbar-actions">
          {hasBoard && (
            <button className="mini-btn" disabled={disabled} onClick={() => setShowBoard((v) => !v)}>
              {showBoard ? '收起局面原文' : '展开局面原文'}
            </button>
          )}
          <button className="mini-btn" disabled={disabled} onClick={() => setShowAdvanced((v) => !v)}>
            {showAdvanced ? '收起高级命令' : '高级命令'}
          </button>
        </div>
      </div>

      {hasBoard && showBoard && <pre className="game-workbench-body">{cleanBoard}</pre>}
      {!hasBoard && <div className="game-workbench-empty">当前房间暂无已展示的游戏局面。</div>}

      {showAdvanced ? (
        <div className="game-workbench-input">
          <textarea
            className="game-workbench-command"
            value={moveText}
            onChange={(e) => setMoveText(e.target.value)}
            placeholder={privacyMode ? '输入命令...' : '输入走法或操作...'}
            disabled={disabled}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                onMove();
              }
            }}
          />
          <button className="send-button" onClick={onMove} disabled={disabled || !moveText.trim()}>发送</button>
        </div>
      ) : (
        <div className="game-workbench-input-compact">优先使用上方交互按钮；需要手动命令时再展开“高级命令”。</div>
      )}
    </div>
  );
}


