import React, { useMemo } from 'react';
import { useTranslation } from '../../i18n';
import { parseReversiBoard, reversiMovePayload, type ReversiCell, type ReversiStone } from './reversiBoard';

type Props = {
  disabled: boolean;
  nickname: string;
  boardText: string;
  onMove: (payload: string) => void;
};

const DIRECTIONS = [
  [-1, -1], [-1, 0], [-1, 1], [0, -1],
  [0, 1], [1, -1], [1, 0], [1, 1],
] as const;

function stoneAt(cells: ReversiCell[], row: number, col: number): ReversiStone | null {
  if (row < 1 || row > 8 || col < 1 || col > 8) return null;
  return cells[(row - 1) * 8 + col - 1].stone;
}

function legalMoves(cells: ReversiCell[], stone: ReversiStone): Set<string> {
  if (stone === '.') return new Set();
  const other = stone === '#' ? 'o' : '#';
  const moves = new Set<string>();
  for (let row = 1; row <= 8; row += 1) {
    for (let col = 1; col <= 8; col += 1) {
      if (stoneAt(cells, row, col) !== '.') continue;
      for (const [dr, dc] of DIRECTIONS) {
        let r = row + dr;
        let c = col + dc;
        let bracketed = false;
        while (stoneAt(cells, r, c) === other) {
          bracketed = true;
          r += dr;
          c += dc;
        }
        if (bracketed && stoneAt(cells, r, c) === stone) {
          moves.add(`${row},${col}`);
          break;
        }
      }
    }
  }
  return moves;
}

function parseTurn(text: string): { side: '#' | 'o' | null; name: string } {
  const line = [...text.split('\n')].reverse().find((item) => /^Turn:\s+(Black|White)\s+/i.test(item.trim()));
  if (!line) return { side: null, name: '' };
  const match = line.trim().match(/^Turn:\s+(Black|White)\s+(.+)$/i);
  if (!match) return { side: null, name: '' };
  return { side: match[1].toLowerCase() === 'black' ? '#' : 'o', name: match[2].trim() };
}

function parseScore(cells: ReversiCell[]): { black: number; white: number } {
  return cells.reduce(
    (score, cell) => ({
      black: score.black + (cell.stone === '#' ? 1 : 0),
      white: score.white + (cell.stone === 'o' ? 1 : 0),
    }),
    { black: 0, white: 0 },
  );
}

export default function ReversiPanel({ disabled, nickname, boardText, onMove }: Props) {
  const { locale } = useTranslation();
  const cells = useMemo(() => parseReversiBoard(boardText), [boardText]);
  const turn = useMemo(() => parseTurn(boardText), [boardText]);
  const score = useMemo(() => parseScore(cells), [cells]);
  const moves = useMemo(() => legalMoves(cells, turn.side || '.'), [cells, turn.side]);
  const myTurn = !!nickname && turn.name === nickname;
  const canAct = !disabled && myTurn;

  const pick = (cell: ReversiCell) => {
    if (!canAct || cell.stone !== '.' || !moves.has(`${cell.row},${cell.col}`)) return;
    onMove(reversiMovePayload(cell.row, cell.col));
  };

  return (
    <div className="game-interaction-panel">
      <div className="game-interaction-title">{locale === 'zh' ? '黑白棋（点击高亮空位落子）' : 'Reversi (click a highlighted square)'}</div>
      <div className="game-workbench-hint">
        {locale === 'zh' ? `黑 ${score.black} · 白 ${score.white}` : `Black ${score.black} · White ${score.white}`}
        {turn.name ? ` · ${locale === 'zh' ? '当前' : 'Turn'}: ${turn.name}` : ''}
      </div>
      {!myTurn && turn.name && (
        <div className="game-workbench-hint">{locale === 'zh' ? `等待 ${turn.name} 落子` : `Waiting for ${turn.name}`}</div>
      )}
      {myTurn && moves.size === 0 && (
        <button className="mini-btn" disabled={disabled} onClick={() => onMove('pass')}>
          {locale === 'zh' ? '停一手' : 'Pass'}
        </button>
      )}
      <div className="reversi-grid" role="grid" aria-label={locale === 'zh' ? '黑白棋棋盘' : 'Reversi board'}>
        {cells.map((cell) => {
          const legal = myTurn && cell.stone === '.' && moves.has(`${cell.row},${cell.col}`);
          return (
            <button
              key={`${cell.row}-${cell.col}`}
              type="button"
              className={`reversi-cell ${cell.last ? 'last' : ''} ${legal ? 'legal' : ''}`}
              disabled={!canAct || !legal}
              onClick={() => pick(cell)}
              aria-label={`${cell.row},${cell.col}`}
            >
              {cell.stone !== '.' && <span className={`reversi-stone ${cell.stone === '#' ? 'black' : 'white'}`} />}
            </button>
          );
        })}
      </div>
    </div>
  );
}
