import React, { useMemo, useState } from 'react';
import { useTranslation } from '../../i18n';

type Cell = { row: number; col: number; own: string; enemy: string; ownLast: boolean; enemyLast: boolean };
type Props = { disabled: boolean; nickname: string; boardText: string; onMove: (payload: string) => void };

const SHIPS: Array<[string, number]> = [
  ['carrier', 5], ['battleship', 4], ['cruiser', 3], ['submarine', 3], ['destroyer', 2],
];

function parseBoard(text: string): Cell[] {
  const cells: Cell[] = Array.from({ length: 100 }, (_, index) => ({
    row: Math.floor(index / 10) + 1,
    col: index % 10 + 1,
    own: '.',
    enemy: '?',
    ownLast: false,
    enemyLast: false,
  }));
  for (const raw of text.split('\n')) {
    const match = raw.match(/^\s*(\d+)\s+(.+)$/);
    if (!match) continue;
    const row = Number(match[1]);
    if (row < 1 || row > 10) continue;
    const sections = match[2].trim().split(/\s{4,}/);
    if (sections.length !== 2) continue;
    const own = sections[0].split(/\s+/).slice(0, 10);
    const enemy = sections[1].split(/\s+/).slice(0, 10);
    if (own.length !== 10 || enemy.length !== 10) continue;
    for (let col = 1; col <= 10; col += 1) {
      const cell = cells[(row - 1) * 10 + col - 1];
      const ownToken = own[col - 1];
      const enemyToken = enemy[col - 1];
      cell.ownLast = ownToken.startsWith('!');
      cell.enemyLast = enemyToken.startsWith('!');
      cell.own = cell.ownLast ? ownToken.slice(1) : ownToken;
      cell.enemy = cell.enemyLast ? enemyToken.slice(1) : enemyToken;
    }
  }
  return cells;
}

function gameState(text: string): string {
  return text.match(/^battleship game \(([^)]+)\)/im)?.[1]?.toLowerCase() || 'waiting';
}

function turnName(text: string): string {
  return [...text.split('\n')].reverse().find((line) => /^Turn:\s+/i.test(line.trim()))?.trim().replace(/^Turn:\s+/i, '') || '';
}

function prettyToken(token: string): string {
  if (token === 'X') return 'X';
  if (token === 'o') return '·';
  if (token === 'S') return '■';
  return '';
}

export default function BattleshipPanel({ disabled, nickname, boardText, onMove }: Props) {
  const { locale } = useTranslation();
  const cells = useMemo(() => parseBoard(boardText), [boardText]);
  const state = useMemo(() => gameState(boardText), [boardText]);
  const currentTurn = useMemo(() => turnName(boardText), [boardText]);
  const [ship, setShip] = useState(SHIPS[0][0]);
  const [orientation, setOrientation] = useState<'h' | 'v'>('h');
  const myTurn = currentTurn === nickname;
  const setup = state === 'setup';

  const clickCell = (cell: Cell) => {
    if (disabled) return;
    if (setup) {
      onMove(`place ${ship} ${cell.row} ${cell.col} ${orientation}`);
      return;
    }
    if (state === 'playing' && myTurn && cell.enemy === '?') {
      onMove(`fire ${cell.row} ${cell.col}`);
    }
  };

  return (
    <div className="game-interaction-panel">
      <div className="game-interaction-title">{locale === 'zh' ? '海战棋（先布舰，再轮流开火）' : 'Battleship (place ships, then fire by turn)'}</div>
      {setup && (
        <div className="battleship-setup-controls">
          {SHIPS.map(([name, length]) => (
            <button type="button" key={name} className={`mini-btn ${ship === name ? 'selected' : ''}`} disabled={disabled} onClick={() => setShip(name)}>
              {name} ({length})
            </button>
          ))}
          <button type="button" className="mini-btn" disabled={disabled} onClick={() => setOrientation((value) => value === 'h' ? 'v' : 'h')}>
            {locale === 'zh' ? `方向：${orientation === 'h' ? '横' : '竖'}` : `Direction: ${orientation.toUpperCase()}`}
          </button>
          <button type="button" className="mini-btn" disabled={disabled} onClick={() => onMove('ready')}>
            {locale === 'zh' ? '确认布阵' : 'Ready'}
          </button>
        </div>
      )}
      {state === 'playing' && <div className="game-workbench-hint">{currentTurn ? (locale === 'zh' ? `当前回合：${currentTurn}` : `Turn: ${currentTurn}`) : ''}</div>}
      <div className="battleship-labels"><span>{locale === 'zh' ? '己方海域' : 'Your fleet'}</span><span>{locale === 'zh' ? '对手海域' : 'Opponent waters'}</span></div>
      <div className="battleship-grids">
        <div className="battleship-grid" role="grid" aria-label={locale === 'zh' ? '己方海域' : 'Your fleet'}>
          {cells.map((cell) => <button type="button" key={`own-${cell.row}-${cell.col}`} className={`battleship-cell own-${cell.own.toLowerCase()} ${cell.ownLast ? 'last' : ''}`} disabled={!setup || disabled} onClick={() => clickCell(cell)}>{prettyToken(cell.own)}</button>)}
        </div>
        <div className="battleship-grid" role="grid" aria-label={locale === 'zh' ? '对手海域' : 'Opponent waters'}>
          {cells.map((cell) => <button type="button" key={`enemy-${cell.row}-${cell.col}`} className={`battleship-cell enemy-${cell.enemy.toLowerCase()} ${cell.enemyLast ? 'last' : ''}`} disabled={disabled || state !== 'playing' || !myTurn || cell.enemy !== '?'} onClick={() => clickCell(cell)}>{prettyToken(cell.enemy)}</button>)}
        </div>
      </div>
    </div>
  );
}
