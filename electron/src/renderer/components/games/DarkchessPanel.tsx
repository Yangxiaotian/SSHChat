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
  const key = token.replace(/^[-+]/, '');
  return labels[key]?.[locale === 'zh' ? 0 : 1] || token;
}

function pieceSide(token: string): 'red' | 'black' | null {
  if (token.startsWith('+')) return 'red';
  if (token.startsWith('-')) return 'black';
  return null;
}

function sideForNickname(text: string, nickname: string): 'red' | 'black' | null {
  const match = text.match(/^darkchess game \([^)]*\)\s+Player 1:\s+(.+?)\s+Player 2:\s+(.+)$/im);
  const sides = text.match(/^Sides:\s+P1\s+(red|black|unknown)\s+P2\s+(red|black|unknown)$/im);
  if (!match || !sides) return null;
  const wanted = nickname.trim().toLowerCase();
  const player = match[1].trim().toLowerCase() === wanted ? 1 : match[2].trim().toLowerCase() === wanted ? 2 : 0;
  const side = player === 1 ? sides[1].toLowerCase() : player === 2 ? sides[2].toLowerCase() : '';
  return side === 'red' || side === 'black' ? side : null;
}

function sameNickname(left: string, right: string): boolean {
  return left.trim().toLowerCase() === right.trim().toLowerCase();
}

export default function DarkchessPanel({ disabled, nickname, boardText, onMove }: Props) {
  const { locale } = useTranslation();
  const cells = useMemo(() => parseBoard(boardText), [boardText]);
  const currentTurn = useMemo(() => turnName(boardText), [boardText]);
  const mySide = useMemo(() => sideForNickname(boardText, nickname), [boardText, nickname]);
  const [selected, setSelected] = useState<Cell | null>(null);
  const [actionHint, setActionHint] = useState('');
  const myTurn = !!nickname && !!currentTurn && sameNickname(currentTurn, nickname);
  const canAct = !disabled && myTurn;

  React.useEffect(() => {
    setSelected(null);
    setActionHint('');
  }, [boardText]);

  const pick = (cell: Cell) => {
    if (disabled) {
      setActionHint(locale === 'zh' ? '当前连接已断开，暂时不能落子。' : 'The connection is offline; moves are disabled.');
      return;
    }
    if (!nickname) {
      setActionHint(locale === 'zh' ? '尚未识别当前用户，暂时不能落子。' : 'Your nickname is not available yet.');
      return;
    }
    if (!currentTurn || !myTurn) {
      setActionHint(currentTurn
        ? (locale === 'zh' ? `当前轮到 ${currentTurn}，请等待对手操作。` : `It is ${currentTurn}'s turn; please wait.`)
        : (locale === 'zh' ? '暂未识别当前回合，请先刷新局面。' : 'The current turn is not available; refresh the board.'));
      return;
    }
    setActionHint('');
    if (!selected) {
      if (cell.token === '?') onMove(`flip ${cell.row} ${cell.col}`);
      else if (cell.token !== '.' && (!mySide || pieceSide(cell.token) === mySide)) setSelected(cell);
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
    if (cell.token === '?') return;
    onMove(`move ${selected.row} ${selected.col} ${cell.row} ${cell.col}`);
    setSelected(null);
  };

  return (
    <div className="game-interaction-panel">
      <div className="game-interaction-title">{locale === 'zh' ? '暗棋 / 翻翻棋（点击暗子翻开）' : 'Dark Chess (click a face-down piece to flip)'}</div>
      <div className="game-workbench-hint">{locale === 'zh' ? '红方：暖红；黑方：冷灰；?：未翻开' : 'Red: warm red · Black: cool gray · ?: face-down'}</div>
      {currentTurn && <div className="game-workbench-hint">{locale === 'zh' ? `当前回合：${currentTurn}` : `Turn: ${currentTurn}`}</div>}
      {!myTurn && currentTurn && <div className="game-workbench-hint">{locale === 'zh' ? '等待对手操作' : 'Waiting for the opponent'}</div>}
      <div className="darkchess-grid" role="grid" aria-label={locale === 'zh' ? '暗棋棋盘' : 'Dark chess board'}>
        {cells.map((cell) => (
          <button
            type="button"
            key={`${cell.row}-${cell.col}`}
            className={`darkchess-cell ${cell.last ? 'last' : ''} ${selected?.row === cell.row && selected?.col === cell.col ? 'selected' : ''} ${cell.token === '?' ? 'hidden-piece' : ''} ${pieceSide(cell.token) ? `piece-${pieceSide(cell.token)}` : ''}`}
            aria-disabled={!canAct}
            onClick={() => pick(cell)}
            aria-label={`${cell.row},${cell.col}`}
          >
            {cell.token === '?' ? '?' : cell.token === '.' ? '' : label(cell.token, locale)}
          </button>
        ))}
      </div>
      {actionHint && <div className="game-workbench-hint darkchess-action-hint" role="status">{actionHint}</div>}
      {selected && <div className="game-workbench-hint">{locale === 'zh' ? `已选 ${selected.row},${selected.col}，再点目标位置` : `Selected ${selected.row},${selected.col}; choose a destination`}</div>}
    </div>
  );
}
