import React, { useEffect, useMemo, useRef, useState } from 'react';

type Props = {
  disabled: boolean;
  nickname: string;
  boardText: string;
  onPick: (row: number, col: number) => void;
  onCmd: (cmd: string) => void;
};

type Stone = '.' | '#' | 'o';

type Cell = {
  row: number;
  col: number;
  stone: Stone;
  last: boolean;
};

const BOARD_SIZE = 19;

type AdvisorMove = {
  row: number;
  col: number;
  label: string;
  detail: string;
  source: 'katago' | 'fallback';
};

function emptyBoard(): Cell[][] {
  return Array.from({ length: BOARD_SIZE }, (_, r) =>
    Array.from({ length: BOARD_SIZE }, (_, c) => ({
      row: r + 1,
      col: c + 1,
      stone: '.',
      last: false,
    })),
  );
}

function parseBoard(boardText: string): Cell[][] {
  const board = emptyBoard();
  for (const raw of boardText.split('\n')) {
    const m = raw.match(/^\s*(\d{1,2})\s+(.+)$/);
    if (!m) continue;
    const rowNo = Number(m[1]);
    if (!Number.isFinite(rowNo) || rowNo < 1 || rowNo > BOARD_SIZE) continue;
    const matches = [...m[2].matchAll(/\(([#o.])\)|[#o.]/g)];
    if (matches.length < BOARD_SIZE) continue;
    for (let i = 0; i < BOARD_SIZE; i++) {
      const token = matches[i][0];
      const stone = (matches[i][1] || token) as Stone;
      board[rowNo - 1][i] = {
        row: rowNo,
        col: i + 1,
        stone,
        last: token.startsWith('('),
      };
    }
  }
  return board;
}

function parseTurn(boardText: string): { name: string; side: 'black' | 'white' | null } {
  const line = boardText.split('\n').find((l) => /^轮到\s+/.test(l.trim()));
  if (!line) return { name: '', side: null };
  const m = line.trim().match(/^轮到\s+(黑|白)方\s+(.+?)\s+落子/);
  if (!m) return { name: '', side: null };
  return { side: m[1] === '黑' ? 'black' : 'white', name: m[2].trim() };
}

function parseSeats(boardText: string): { black: string; white: string } {
  const out = { black: '', white: '' };
  const header = boardText.match(/黑：(.+?)\s+白：(.+?)(?:\n|$)/);
  if (header) {
    out.black = header[1].trim();
    out.white = header[2].trim();
  }
  for (const raw of boardText.split('\n')) {
    const line = raw.trim();
    const black = line.match(/^黑方(?:（先手）)?：(.+)$/);
    if (black) out.black = black[1].replace(/\(.+\)/, '').trim();
    const white = line.match(/^白方：(.+)$/);
    if (white) out.white = white[1].replace(/\(.+\)/, '').trim();
  }
  if (out.white === '空席') out.white = '';
  return out;
}

function parseMeta(boardText: string): string[] {
  return boardText
    .split('\n')
    .filter((l) => /贴目|提子|结果|双方连续停一手/.test(l))
    .slice(0, 3);
}

function toMatrix(cells: Cell[][]): number[][] {
  return cells.map((row) => row.map((cell) => (cell.stone === '#' ? 1 : cell.stone === 'o' ? 2 : 0)));
}

function boardSignature(matrix: number[][]): string {
  return matrix.map((row) => row.join('')).join('|');
}

function fallbackGoSuggestions(matrix: number[][], side: 1 | 2): AdvisorMove[] {
  const opp = side === 1 ? 2 : 1;
  const stones: Array<[number, number]> = [];
  for (let r = 0; r < BOARD_SIZE; r++) {
    for (let c = 0; c < BOARD_SIZE; c++) {
      if (matrix[r][c] !== 0) stones.push([r, c]);
    }
  }
  const candidates: Array<{ row: number; col: number; score: number; why: string }> = [];
  const seen = new Set<string>();
  const anchors = stones.length ? stones : [[9, 9] as [number, number]];
  for (const [ar, ac] of anchors) {
    for (let dr = -2; dr <= 2; dr++) {
      for (let dc = -2; dc <= 2; dc++) {
        const r = ar + dr;
        const c = ac + dc;
        if (r < 0 || r >= BOARD_SIZE || c < 0 || c >= BOARD_SIZE || matrix[r][c] !== 0) continue;
        const key = `${r},${c}`;
        if (seen.has(key)) continue;
        seen.add(key);
        let ownNear = 0;
        let oppNear = 0;
        for (let rr = Math.max(0, r - 2); rr <= Math.min(BOARD_SIZE - 1, r + 2); rr++) {
          for (let cc = Math.max(0, c - 2); cc <= Math.min(BOARD_SIZE - 1, c + 2); cc++) {
            if (matrix[rr][cc] === side) ownNear += 1;
            else if (matrix[rr][cc] === opp) oppNear += 1;
          }
        }
        const center = 18 - Math.abs(r - 9) - Math.abs(c - 9);
        const starBonus = ((r === 3 || r === 9 || r === 15) && (c === 3 || c === 9 || c === 15)) ? 6 : 0;
        const score = center + starBonus + ownNear * 4 + oppNear * 3;
        candidates.push({
          row: r + 1,
          col: c + 1,
          score,
          why: ownNear >= oppNear ? '兼顾扩张和连接' : '靠近对方阵地，制造压力',
        });
      }
    }
  }
  return candidates
    .sort((a, b) => b.score - a.score || a.row - b.row || a.col - b.col)
    .slice(0, 3)
    .map((m, i) => ({
      row: m.row,
      col: m.col,
      label: `建议${i + 1}: ${m.row},${m.col}`,
      detail: `内置建议：${m.why}`,
      source: 'fallback',
    }));
}

export default function GoPanel({ disabled, nickname, boardText, onPick, onCmd }: Props) {
  const cells = useMemo(() => parseBoard(boardText), [boardText]);
  const turn = useMemo(() => parseTurn(boardText), [boardText]);
  const seats = useMemo(() => parseSeats(boardText), [boardText]);
  const meta = useMemo(() => parseMeta(boardText), [boardText]);
  const matrix = useMemo(() => toMatrix(cells), [cells]);
  const sig = useMemo(() => boardSignature(matrix), [matrix]);
  const mySide = nickname === seats.black ? 'black' : nickname === seats.white ? 'white' : null;
  const mySideNum: 1 | 2 | null = mySide === 'black' ? 1 : mySide === 'white' ? 2 : null;
  const myTurn = !!turn.name && turn.name === nickname;
  const isHiddenMaster = nickname === 'zouyu';
  const [katagoPending, setKataGoPending] = useState(false);
  const [katagoError, setKataGoError] = useState('');
  const [katagoMoves, setKataGoMoves] = useState<AdvisorMove[]>([]);
  const katagoSeqRef = useRef(0);
  const katagoOkKeyRef = useRef('');
  const katagoFailKeyRef = useRef('');
  const fallbackMoves = useMemo(
    () => (mySideNum ? fallbackGoSuggestions(matrix, mySideNum) : []),
    [matrix, mySideNum],
  );
  const shownMoves = katagoMoves.length > 0 ? katagoMoves : fallbackMoves;

  useEffect(() => {
    if (!isHiddenMaster || !mySideNum || !sig) {
      setKataGoMoves([]);
      setKataGoError('');
      setKataGoPending(false);
      return;
    }
    const key = `${sig}|${mySideNum}`;
    if (katagoOkKeyRef.current === key || katagoFailKeyRef.current === key || katagoPending) return;
    const seq = katagoSeqRef.current + 1;
    katagoSeqRef.current = seq;
    setKataGoPending(true);
    setKataGoError('');
    const guard = new Promise<never>((_, reject) => {
      window.setTimeout(() => reject(new Error('KataGo 响应超时（前端保护）')), 75000);
    });
    Promise.race([
      window.api.analyzeGoKataGo({
        board: matrix,
        mySide: mySideNum,
        komi: 6.5,
        maxVisits: 96,
        timeoutMs: 60000,
      }),
      guard,
    ])
      .then((resp) => {
        if (katagoSeqRef.current !== seq) return;
        if (resp.ok && resp.suggestions?.length) {
          setKataGoMoves(resp.suggestions.slice(0, 3).map((m, i) => {
            const wr = typeof m.winrate === 'number' ? `胜率${Math.round(m.winrate * 100)}%` : '';
            const lead = typeof m.scoreLead === 'number' ? `目差${m.scoreLead.toFixed(1)}` : '';
            const visits = typeof m.visits === 'number' ? `访问${m.visits}` : '';
            return {
              row: m.row,
              col: m.col,
              label: `KataGo${i + 1}: ${m.row},${m.col}`,
              detail: [wr, lead, visits].filter(Boolean).join('，') || 'KataGo 推荐',
              source: 'katago',
            };
          }));
          katagoOkKeyRef.current = key;
          setKataGoError('');
        } else {
          setKataGoMoves([]);
          setKataGoError(resp.error || 'KataGo 未返回可用建议');
          katagoFailKeyRef.current = key;
        }
      })
      .catch((err: unknown) => {
        if (katagoSeqRef.current !== seq) return;
        setKataGoMoves([]);
        setKataGoError(err instanceof Error ? err.message : 'KataGo 调用失败');
        katagoFailKeyRef.current = key;
      })
      .finally(() => {
        if (katagoSeqRef.current === seq) setKataGoPending(false);
      });
  }, [isHiddenMaster, mySideNum, sig, matrix, katagoPending]);

  return (
    <div className="game-interaction-panel">
      <div className="game-interaction-title">围棋棋盘（19 路，点击交叉点落子）</div>
      <div className="game-advisor-detail">
        你当前身份：{mySide === 'black' ? '黑方' : mySide === 'white' ? '白方' : '未入座'}
        {turn.name ? `，当前轮到：${turn.side === 'black' ? '黑方' : '白方'} ${turn.name}` : ''}
      </div>
      {meta.map((line) => (
        <div key={line} className="game-advisor-detail">{line}</div>
      ))}
      <div className="game-chip-row" style={{ marginTop: 6 }}>
        <button className="mini-btn" disabled={disabled} onClick={() => onCmd('pass')}>停一手</button>
        <button className="mini-btn" disabled={disabled} onClick={() => onCmd('/game join')}>加入对局</button>
        <button className="mini-btn" disabled={disabled} onClick={() => onCmd('/game seats')}>查看席位</button>
        <button className="mini-btn" disabled={disabled} onClick={() => onCmd('/game resign')}>认输</button>
      </div>
      {turn.name && !myTurn && (
        <div className="game-workbench-hint">当前不是你的回合，可先观察气口、劫点和下一手方向。</div>
      )}
      {isHiddenMaster && mySideNum && (
        <div className="game-advisor game-advisor-info" style={{ marginTop: 8, marginBottom: 8 }}>
          <div className="game-advisor-title">隐藏功能：KataGo 围棋助手</div>
          <div className="game-advisor-detail">
            状态：{katagoPending ? '分析中...' : katagoMoves.length ? '已接入 KataGo' : katagoError ? `已回退内置：${katagoError}` : '等待分析'}
          </div>
          <div className="game-chip-row" style={{ marginTop: 6, flexWrap: 'wrap' }}>
            {shownMoves.map((m) => (
              <button
                key={`${m.source}-${m.row}-${m.col}`}
                className="mini-btn"
                disabled={disabled || (!!turn.name && !myTurn)}
                onClick={() => onPick(m.row, m.col)}
                title={m.detail}
              >
                {m.label}
              </button>
            ))}
          </div>
          {shownMoves[0] && (
            <div className="game-advisor-detail" style={{ marginTop: 6 }}>
              首选：第 {shownMoves[0].row} 行，第 {shownMoves[0].col} 列。{shownMoves[0].detail}
            </div>
          )}
        </div>
      )}
      <div className="go-board-wrap">
        <div className="go-board">
          {cells.map((row) =>
            row.map((cell) => (
              <button
                key={`${cell.row}-${cell.col}`}
                className={`go-point ${cell.stone === '#' ? 'black' : ''} ${cell.stone === 'o' ? 'white' : ''} ${cell.last ? 'last' : ''} ${shownMoves[0]?.row === cell.row && shownMoves[0]?.col === cell.col ? 'suggested' : ''}`}
                disabled={disabled || cell.stone !== '.' || !mySide || (!!turn.name && !myTurn)}
                onClick={() => onPick(cell.row, cell.col)}
                title={`第${cell.row}行，第${cell.col}列`}
              >
                <span />
              </button>
            )),
          )}
        </div>
      </div>
      <div className="game-advisor-detail" style={{ marginTop: 6 }}>
        规则：黑先，白贴目 6.5；禁止自杀和立即回提；连续两次停一手后自动数子。
      </div>
    </div>
  );
}
