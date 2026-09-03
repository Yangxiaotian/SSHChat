import React, { useMemo, useState } from 'react';
import { useTranslation } from '../../i18n';

type Cell = { row: number; col: number; token: string; last: boolean };
type Props = { disabled: boolean; nickname: string; boardText: string; onMove: (payload: string) => void };

const EN_TO_ZH: Record<string, string> = {
  G: '将', A: '士', E: '象', R: '车', H: '马', C: '炮', S: '卒',
};
const ZH_TO_EN: Record<string, string> = {
  将: 'G', 士: 'A', 象: 'E', 车: 'R', 马: 'H', 炮: 'C', 卒: 'S',
};

/** Match one darkchess cell: optional ! last-mark, then +将 / -马 / +G / ? / . */
const CELL_RE = /(?:!?[+-](?:[将士象车马炮卒]|[GAERHCS])|!?[.?])/g;

function parseBoardRowTokens(body: string): string[] | null {
  const tokens = body.match(CELL_RE);
  if (!tokens || tokens.length !== 8) return null;
  return tokens;
}

function parseLastSummary(text: string): string {
  for (const raw of text.split('\n')) {
    const line = raw.trim();
    const zh = line.match(/^上一步[:：]\s*(.+)$/);
    if (zh) return zh[1].trim();
    const en = line.match(/^Last move[:：]\s*(.+)$/i);
    if (en) return en[1].trim();
  }
  return '';
}

function parseBoard(text: string): { cells: Cell[]; lastSummary: string } {
  const cells: Cell[] = Array.from({ length: 32 }, (_, index) => ({
    row: Math.floor(index / 8) + 1,
    col: (index % 8) + 1,
    token: '.',
    last: false,
  }));
  const lastSummary = parseLastSummary(text);
  const highlight = new Set<string>();

  // Prefer explicit coords in the summary text.
  for (const m of lastSummary.matchAll(/\((\d+)\s*,\s*(\d+)\)/g)) {
    highlight.add(`${Number(m[1])},${Number(m[2])}`);
  }

  for (const line of text.split('\n')) {
    const trimmed = line.trim();
    if (/^\d+(\s+\d+){7}$/.test(trimmed)) continue;

    const match = line.match(/^\s*([1-4])\s+(.+\S.*)$/);
    if (!match) continue;
    const row = Number(match[1]);
    const tokens = parseBoardRowTokens(match[2]);
    if (!tokens) continue;
    tokens.forEach((rawToken, colIndex) => {
      const cell = cells[(row - 1) * 8 + colIndex];
      const marked = rawToken.startsWith('!');
      const token = marked ? rawToken.slice(1) : rawToken;
      cell.token = token || '.';
      cell.last = marked || highlight.has(`${row},${colIndex + 1}`);
    });
  }

  return { cells, lastSummary };
}

function turnName(text: string): string {
  for (const raw of [...text.split('\n')].reverse()) {
    const line = raw.trim();
    let m = line.match(/^Turn:\s+(.+?)\s+\(player [12]\)$/i);
    if (m) return m[1].trim();
    m = line.match(/^轮到[:：]\s*(.+?)(?:（玩家[12]）|\s*\(player [12]\))$/);
    if (m) return m[1].trim();
  }
  return '';
}

function label(token: string, locale: string): string {
  const key = token.replace(/^[-+!]/, '');
  if (locale === 'zh') return EN_TO_ZH[key] || key;
  return ZH_TO_EN[key] || key;
}

function pieceSide(token: string): 'red' | 'black' | null {
  if (token.startsWith('+')) return 'red';
  if (token.startsWith('-')) return 'black';
  return null;
}

function sideForNickname(text: string, nickname: string): 'red' | 'black' | null {
  const wanted = nickname.trim().toLowerCase();
  const en = text.match(
    /^darkchess(?:\s+game)?\s*\([^)]*\)\s+Player 1:\s+(.+?)\s+Player 2:\s+(.+)$/im,
  );
  const zh = text.match(
    /^darkchess\s+对局（[^）]+）\s+玩家1[:：]\s*(.+?)\s+玩家2[:：]\s*(.+)$/im,
  );
  const match = en || zh;
  const sidesEn = text.match(/^Sides:\s+P1\s+(red|black|unknown)\s+P2\s+(red|black|unknown)$/im);
  const sidesZh = text.match(
    /^阵营[:：]\s*P1\s+(红|黑|未定|red|black|unknown)\s+P2\s+(红|黑|未定|red|black|unknown)$/im,
  );
  const sides = sidesEn || sidesZh;
  if (!match || !sides) return null;
  const p1 = match[1].trim().toLowerCase();
  const p2 = match[2].trim().toLowerCase();
  const player = p1 === wanted ? 1 : p2 === wanted ? 2 : 0;
  const raw = (player === 1 ? sides[1] : player === 2 ? sides[2] : '').toLowerCase();
  if (raw === 'red' || raw === '红') return 'red';
  if (raw === 'black' || raw === '黑') return 'black';
  return null;
}

function sameNickname(left: string, right: string): boolean {
  return left.trim().toLowerCase() === right.trim().toLowerCase();
}

export default function DarkchessPanel({ disabled, nickname, boardText, onMove }: Props) {
  const { locale } = useTranslation();
  const { cells, lastSummary } = useMemo(() => parseBoard(boardText), [boardText]);
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
      if (cell.token === '?') {
        onMove(`flip ${cell.row} ${cell.col}`);
        return;
      }
      if (cell.token !== '.' && (!mySide || pieceSide(cell.token) === mySide)) {
        setSelected(cell);
      }
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
      <div className="game-interaction-title">
        {locale === 'zh' ? '暗棋 / 翻翻棋（点击暗子翻开）' : 'Dark Chess (click a face-down piece to flip)'}
      </div>
      <div className="game-workbench-hint">
        {locale === 'zh'
          ? '红方：暖红；黑方：冷灰；?：未翻开；子：将士象车马炮卒'
          : 'Red: warm red · Black: cool gray · ?: face-down · G/A/E/R/H/C/S'}
      </div>
      {lastSummary ? (
        <div className="game-workbench-hint">
          {locale === 'zh' ? `上一步：${lastSummary}` : `Last move: ${lastSummary}`}
        </div>
      ) : null}
      {currentTurn && (
        <div className="game-workbench-hint">
          {locale === 'zh' ? `当前回合：${currentTurn}` : `Turn: ${currentTurn}`}
        </div>
      )}
      {!myTurn && currentTurn && (
        <div className="game-workbench-hint">{locale === 'zh' ? '等待对手操作' : 'Waiting for the opponent'}</div>
      )}
      <div className="darkchess-grid" role="grid" aria-label={locale === 'zh' ? '暗棋棋盘' : 'Dark chess board'}>
        {cells.map((cell) => (
          <button
            type="button"
            key={`${cell.row}-${cell.col}`}
            className={`darkchess-cell ${cell.last ? 'last' : ''} ${
              selected?.row === cell.row && selected?.col === cell.col ? 'selected' : ''
            } ${cell.token === '?' ? 'hidden-piece' : ''} ${
              pieceSide(cell.token) ? `piece-${pieceSide(cell.token)}` : ''
            }`}
            aria-disabled={!canAct}
            onClick={() => pick(cell)}
            aria-label={`${cell.row},${cell.col} ${cell.token}`}
          >
            {cell.token === '?' ? '?' : cell.token === '.' ? '' : label(cell.token, locale)}
          </button>
        ))}
      </div>
      {actionHint && (
        <div className="game-workbench-hint darkchess-action-hint" role="status">
          {actionHint}
        </div>
      )}
      {selected && (
        <div className="game-workbench-hint">
          {locale === 'zh'
            ? `已选 ${selected.row},${selected.col}，再点目标位置`
            : `Selected ${selected.row},${selected.col}; choose a destination`}
        </div>
      )}
    </div>
  );
}
