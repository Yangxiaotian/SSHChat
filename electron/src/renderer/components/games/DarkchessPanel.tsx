import React, { useMemo, useState } from 'react';
import { useTranslation } from '../../i18n';

type Cell = { row: number; col: number; token: string; last: boolean };
type Props = { disabled: boolean; nickname: string; boardText: string; onMove: (payload: string) => void };

function parseBoard(text: string): Cell[] {
  const cells: Cell[] = Array.from({ length: 32 }, (_, index) => ({
    row: Math.floor(index / 8) + 1,
    col: index % 8 + 1,
    token: '.',
    last: false,
  }));
  for (const line of text.split('\n')) {
    const match = line.match(/^\s*([1-4])\s+(.+)$/);
    if (!match) continue;
    const row = Number(match[1]);
    const tokens = match[2].trim().split(/\s+/).slice(0, 8);
    if (tokens.length !== 8) continue;
    tokens.forEach((token, colIndex) => {
      const last = token.startsWith('!');
      const cell = cells[(row - 1) * 8 + colIndex];
      cell.token = last ? token.slice(1) : token;
      cell.last = last;
    });
  }
  return cells;
}

function turnName(text: string): string {
  const line = [...text.split('\n')].reverse().find((item) => /^Turn:\s+.+\(player [12]\)$/i.test(item.trim()));
  return line?.trim().replace(/^Turn:\s+/i, '').replace(/\s+\(player [12]\)$/i, '') || '';
}

function label(token: string, locale: string): string {
  const labels: Record<string, [string, string]> = {
    G: ['将', 'G'], A: ['士', 'A'], E: ['象', 'E'], R: ['车', 'R'],
    H: ['马', 'H'], C: ['炮', 'C'], S: ['卒', 'S'],
  };
  const key = token.slice(1);
  return labels[key]?.[locale === 'zh' ? 0 : 1] || token;
}

export default function DarkchessPanel({ disabled, nickname, boardText, onMove }: Props) {
  const { locale } = useTranslation();
  const cells = useMemo(() => parseBoard(boardText), [boardText]);
  const currentTurn = useMemo(() => turnName(boardText), [boardText]);
  const [selected, setSelected] = useState<Cell | null>(null);
  const myTurn = !!nickname && currentTurn === nickname;
  const canAct = !disabled && myTurn;

  const pick = (cell: Cell) => {
    if (!canAct) return;
    if (!selected) {
      if (cell.token === '?') onMove(`flip ${cell.row} ${cell.col}`);
      else if (cell.token !== '.') setSelected(cell);
      return;
    }
    if (selected.row === cell.row && selected.col === cell.col) {
      setSelected(null);
      return;
    }
    if (cell.token !== '?' && cell.token !== '.') {
      setSelected(cell);
      return;
    }
    onMove(`move ${selected.row} ${selected.col} ${cell.row} ${cell.col}`);
    setSelected(null);
  };

  return (
    <div className="game-interaction-panel">
      <div className="game-interaction-title">{locale === 'zh' ? '暗棋 / 翻翻棋（点击暗子翻开）' : 'Dark Chess (click a face-down piece to flip)'}</div>
      {currentTurn && <div className="game-workbench-hint">{locale === 'zh' ? `当前回合：${currentTurn}` : `Turn: ${currentTurn}`}</div>}
      {!myTurn && currentTurn && <div className="game-workbench-hint">{locale === 'zh' ? '等待对手操作' : 'Waiting for the opponent'}</div>}
      <div className="darkchess-grid" role="grid" aria-label={locale === 'zh' ? '暗棋棋盘' : 'Dark chess board'}>
        {cells.map((cell) => (
          <button
            type="button"
            key={`${cell.row}-${cell.col}`}
            className={`darkchess-cell ${cell.last ? 'last' : ''} ${selected?.row === cell.row && selected?.col === cell.col ? 'selected' : ''} ${cell.token === '?' ? 'hidden-piece' : ''}`}
            disabled={!canAct}
            onClick={() => pick(cell)}
            aria-label={`${cell.row},${cell.col}`}
          >
            {cell.token === '?' ? '?' : cell.token === '.' ? '' : label(cell.token, locale)}
          </button>
        ))}
      </div>
      {selected && <div className="game-workbench-hint">{locale === 'zh' ? `已选 ${selected.row},${selected.col}，再点目标位置` : `Selected ${selected.row},${selected.col}; choose a destination`}</div>}
    </div>
  );
}
