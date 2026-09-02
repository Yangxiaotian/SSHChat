import React, { useMemo, useState } from 'react';
import { useTranslation } from '../../i18n';

type Cell = { row: number; col: number; token: string; last: boolean };
type Props = { disabled: boolean; nickname: string; boardText: string; onMove: (payload: string) => void };

const PIECES: Array<[string, string, string]> = [
  ['flag', '军旗', 'F'], ['commander', '司令', 'C'], ['army', '军长', 'A'],
  ['division', '师长', 'D'], ['brigade', '旅长', 'B'], ['regiment', '团长', 'R'],
  ['battalion', '营长', 'T'], ['company', '连长', 'N'], ['platoon', '排长', 'P'],
  ['engineer', '工兵', 'E'], ['mine', '地雷', 'M'], ['bomb', '炸弹', 'O'],
];

function parseBoard(text: string): Cell[] {
  const cells: Cell[] = Array.from({ length: 60 }, (_, index) => ({
    row: Math.floor(index / 5) + 1,
    col: index % 5 + 1,
    token: '.',
    last: false,
  }));
  for (const line of text.split('\n')) {
    const match = line.match(/^\s*(\d+)\s+(.+)$/);
    if (!match) continue;
    const row = Number(match[1]);
    if (row < 1 || row > 12) continue;
    const tokens = match[2].trim().split(/\s+/).slice(0, 5);
    if (tokens.length !== 5) continue;
    tokens.forEach((raw, colIndex) => {
      const last = raw.startsWith('!');
      cells[(row - 1) * 5 + colIndex] = { row, col: colIndex + 1, token: last ? raw.slice(1) : raw, last };
    });
  }
  return cells;
}

function gameState(text: string): string {
  return text.match(/^junqi game \(([^)]+)\)/im)?.[1]?.toLowerCase() || 'waiting';
}

function turnName(text: string): string {
  return [...text.split('\n')].reverse().find((line) => /^Turn:\s+/i.test(line.trim()))?.trim().replace(/^Turn:\s+/i, '') || '';
}

function pieceLabel(token: string, locale: string): string {
  const item = PIECES.find(([, zh, code]) => code === token.replace(/^[-+]/, ''));
  return item ? (locale === 'zh' ? item[1] : item[2]) : token;
}

function pieceSide(token: string): 'red' | 'blue' | null {
  if (token.startsWith('+')) return 'red';
  if (token.startsWith('-')) return 'blue';
  return null;
}

function sideForNickname(text: string, nickname: string): 'red' | 'blue' | null {
  const match = text.match(/^junqi game \([^)]*\)\s+Red:\s+(.+?)\s+Blue:\s+(.+)$/im);
  if (!match) return null;
  const wanted = nickname.trim().toLowerCase();
  if (match[1].trim().toLowerCase() === wanted) return 'red';
  if (match[2].trim().toLowerCase() === wanted) return 'blue';
  return null;
}

export default function JunqiPanel({ disabled, nickname, boardText, onMove }: Props) {
  const { locale } = useTranslation();
  const cells = useMemo(() => parseBoard(boardText), [boardText]);
  const state = useMemo(() => gameState(boardText), [boardText]);
  const currentTurn = useMemo(() => turnName(boardText), [boardText]);
  const mySide = useMemo(() => sideForNickname(boardText, nickname), [boardText, nickname]);
  const [piece, setPiece] = useState(PIECES[0][0]);
  const [selected, setSelected] = useState<Cell | null>(null);
  const myTurn = !!nickname && currentTurn === nickname;
  const setup = state === 'setup';

  const pick = (cell: Cell) => {
    if (disabled) return;
    if (setup) {
      onMove(`setup ${piece} ${cell.row} ${cell.col}`);
      return;
    }
    if (!myTurn || state !== 'playing') return;
    if (!selected) {
      if ((cell.token.startsWith('+') || cell.token.startsWith('-')) && (!mySide || pieceSide(cell.token) === mySide)) setSelected(cell);
      return;
    }
    if (selected.row === cell.row && selected.col === cell.col) {
      setSelected(null);
      return;
    }
    if (cell.token !== '?' && cell.token !== '.' && pieceSide(cell.token) === mySide) {
      setSelected(cell);
      return;
    }
    onMove(`move ${selected.row} ${selected.col} ${cell.row} ${cell.col}`);
    setSelected(null);
  };

  return (
    <div className="game-interaction-panel">
      <div className="game-interaction-title">{locale === 'zh' ? '军棋（先布阵，再轮流行棋）' : 'Junqi (place your army, then move by turn)'}</div>
      <div className="game-workbench-hint">{locale === 'zh' ? '红方：暖红；蓝方：冷蓝；?：对手未揭示棋子' : 'Red: warm red · Blue: cool blue · ?: hidden opponent piece'}</div>
      {setup && (
        <div className="junqi-setup-controls">
          {PIECES.map(([name, zh, code]) => (
            <button type="button" key={name} className={`mini-btn ${piece === name ? 'selected' : ''}`} disabled={disabled} onClick={() => setPiece(name)}>
              {locale === 'zh' ? `${zh} ${code}` : `${name} ${code}`}
            </button>
          ))}
          <button type="button" className="mini-btn" disabled={disabled} onClick={() => onMove('ready')}>
            {locale === 'zh' ? '完成布阵' : 'Ready'}
          </button>
        </div>
      )}
      {currentTurn && <div className="game-workbench-hint">{locale === 'zh' ? `当前回合：${currentTurn}` : `Turn: ${currentTurn}`}</div>}
      {!setup && !myTurn && currentTurn && <div className="game-workbench-hint">{locale === 'zh' ? `等待 ${currentTurn} 行棋` : `Waiting for ${currentTurn}`}</div>}
      {selected && <div className="game-workbench-hint">{locale === 'zh' ? `已选 ${selected.row},${selected.col}，再点目标位置` : `Selected ${selected.row},${selected.col}; choose a destination`}</div>}
      <div className="junqi-grid" role="grid" aria-label={locale === 'zh' ? '军棋棋盘' : 'Junqi board'}>
        {cells.map((cell) => (
          <button
            type="button"
            key={`${cell.row}-${cell.col}`}
            className={`junqi-cell ${cell.last ? 'last' : ''} ${selected?.row === cell.row && selected?.col === cell.col ? 'selected' : ''} ${cell.token === '?' ? 'hidden-piece' : ''} ${pieceSide(cell.token) ? `piece-${pieceSide(cell.token)}` : ''}`}
            disabled={disabled}
            onClick={() => pick(cell)}
            aria-label={`${cell.row},${cell.col}`}
          >
            {cell.token === '?' ? '?' : cell.token === '.' ? '' : pieceLabel(cell.token, locale)}
          </button>
        ))}
      </div>
      <div className="game-workbench-hint">{locale === 'zh' ? '对手棋子保持隐藏；军旗、地雷不能移动，炸弹相遇同归于尽。' : 'Opponent pieces stay hidden; flags and mines cannot move, bombs remove both pieces.'}</div>
    </div>
  );
}
