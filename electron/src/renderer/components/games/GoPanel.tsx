import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from '../../i18n';

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

function goStoneCount(matrix: number[][]): number {
  let n = 0;
  for (const row of matrix) {
    for (const v of row) {
      if (v !== 0) n += 1;
    }
  }
  return n;
}

function isGoSideTurnByMatrix(matrix: number[][], side: 1 | 2): boolean {
  let black = 0;
  let white = 0;
  for (const row of matrix) {
    for (const v of row) {
      if (v === 1) black += 1;
      else if (v === 2) white += 1;
    }
  }
  return side === 1 ? black === white : black === white + 1;
}

function isLegalEmptyPoint(matrix: number[][], row: number, col: number): boolean {
  const r = row - 1;
  const c = col - 1;
  return r >= 0 && r < BOARD_SIZE && c >= 0 && c < BOARD_SIZE && matrix[r]?.[c] === 0;
}

function filterPlayableMoves(moves: AdvisorMove[], matrix: number[][]): AdvisorMove[] {
  return moves.filter((m) => isLegalEmptyPoint(matrix, m.row, m.col));
}

function goNeighbors(r: number, c: number): Array<[number, number]> {
  const out: Array<[number, number]> = [];
  for (const [dr, dc] of [[1, 0], [-1, 0], [0, 1], [0, -1]] as const) {
    const nr = r + dr;
    const nc = c + dc;
    if (nr >= 0 && nr < BOARD_SIZE && nc >= 0 && nc < BOARD_SIZE) out.push([nr, nc]);
  }
  return out;
}

function pointKey(r: number, c: number): string {
  return `${r},${c}`;
}

function cloneMatrix(matrix: number[][]): number[][] {
  return matrix.map((row) => row.slice());
}

function collectGoGroup(matrix: number[][], r: number, c: number): { stones: Set<string>; liberties: Set<string>; color: number } {
  const color = matrix[r]?.[c] || 0;
  const stones = new Set<string>();
  const liberties = new Set<string>();
  if (!color) return { stones, liberties: new Set([pointKey(r, c)]), color };
  const stack: Array<[number, number]> = [[r, c]];
  while (stack.length) {
    const [cr, cc] = stack.pop()!;
    const k = pointKey(cr, cc);
    if (stones.has(k)) continue;
    stones.add(k);
    for (const [nr, nc] of goNeighbors(cr, cc)) {
      const v = matrix[nr][nc];
      if (v === 0) liberties.add(pointKey(nr, nc));
      else if (v === color && !stones.has(pointKey(nr, nc))) stack.push([nr, nc]);
    }
  }
  return { stones, liberties, color };
}

function groupId(group: Set<string>): string {
  return [...group].sort().join('|');
}

function simulateGoMove(matrix: number[][], r: number, c: number, side: 1 | 2) {
  if (matrix[r]?.[c] !== 0) return null;
  const opp = side === 1 ? 2 : 1;
  const beforeOwn = new Map<string, { stones: Set<string>; liberties: Set<string> }>();
  const beforeOpp = new Map<string, { stones: Set<string>; liberties: Set<string> }>();
  for (const [nr, nc] of goNeighbors(r, c)) {
    if (matrix[nr][nc] === side) {
      const g = collectGoGroup(matrix, nr, nc);
      beforeOwn.set(groupId(g.stones), g);
    } else if (matrix[nr][nc] === opp) {
      const g = collectGoGroup(matrix, nr, nc);
      beforeOpp.set(groupId(g.stones), g);
    }
  }

  const next = cloneMatrix(matrix);
  next[r][c] = side;
  const captured: string[] = [];
  for (const g of beforeOpp.values()) {
    const sample = [...g.stones][0];
    const [sr, sc] = sample.split(',').map(Number);
    if (next[sr]?.[sc] !== opp) continue;
    const after = collectGoGroup(next, sr, sc);
    if (after.liberties.size === 0) {
      for (const p of after.stones) {
        const [pr, pc] = p.split(',').map(Number);
        next[pr][pc] = 0;
        captured.push(p);
      }
    }
  }

  const ownAfter = collectGoGroup(next, r, c);
  if (ownAfter.liberties.size === 0) return null;

  let savesOwnAtari = 0;
  let savedStones = 0;
  for (const g of beforeOwn.values()) {
    if (g.liberties.size === 1 && g.liberties.has(pointKey(r, c))) {
      savesOwnAtari += 1;
      savedStones += g.stones.size;
    }
  }

  let attacksOppAtari = 0;
  let attackedStones = 0;
  for (const g of beforeOpp.values()) {
    if (g.liberties.size === 2 && g.liberties.has(pointKey(r, c))) {
      attacksOppAtari += 1;
      attackedStones += g.stones.size;
    }
  }

  return {
    next,
    capturedCount: captured.length,
    ownLiberties: ownAfter.liberties.size,
    ownGroupSize: ownAfter.stones.size,
    adjacentOwnGroups: beforeOwn.size,
    adjacentOppGroups: beforeOpp.size,
    savesOwnAtari,
    savedStones,
    attacksOppAtari,
    attackedStones,
  };
}

function opponentCaptureIfIgnored(matrix: number[][], r: number, c: number, side: 1 | 2): number {
  const opp = side === 1 ? 2 : 1;
  const sim = simulateGoMove(matrix, r, c, opp);
  return sim?.capturedCount || 0;
}

function buildGoInfluence(matrix: number[][]): Record<1 | 2, number[][]> {
  const influence = {
    1: Array.from({ length: BOARD_SIZE }, () => Array(BOARD_SIZE).fill(0)),
    2: Array.from({ length: BOARD_SIZE }, () => Array(BOARD_SIZE).fill(0)),
  } as Record<1 | 2, number[][]>;
  for (let r = 0; r < BOARD_SIZE; r++) {
    for (let c = 0; c < BOARD_SIZE; c++) {
      const side = matrix[r][c] as 1 | 2 | 0;
      if (!side) continue;
      for (let dr = -5; dr <= 5; dr++) {
        for (let dc = -5; dc <= 5; dc++) {
          const nr = r + dr;
          const nc = c + dc;
          if (nr < 0 || nr >= BOARD_SIZE || nc < 0 || nc >= BOARD_SIZE) continue;
          const dist = Math.abs(dr) + Math.abs(dc);
          if (dist === 0 || dist > 5) continue;
          influence[side][nr][nc] += Math.max(1, 7 - dist);
        }
      }
    }
  }
  return influence;
}

function opponentMoveValue(matrix: number[][], r: number, c: number, side: 1 | 2): number {
  const opp = side === 1 ? 2 : 1;
  const sim = simulateGoMove(matrix, r, c, opp);
  if (!sim) return 0;
  const capture = sim.capturedCount > 0 ? 2100 + sim.capturedCount * 520 : 0;
  const attack = sim.attacksOppAtari > 0 ? 1250 + sim.attackedStones * 210 : 0;
  const rescue = sim.savesOwnAtari > 0 ? 1350 + sim.savedStones * 220 : 0;
  const connect = sim.adjacentOwnGroups >= 2 ? 680 + sim.ownGroupSize * 16 : 0;
  const cut = sim.adjacentOppGroups >= 2 ? 820 : 0;
  const liberties = Math.min(sim.ownLiberties, 6) * 90;
  return capture + attack + rescue + connect + cut + liberties;
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
  const influenceMap = buildGoInfluence(matrix);
  const anchors = stones.length ? stones : [[3, 3], [3, 15], [9, 9], [15, 3], [15, 15]] as Array<[number, number]>;
  for (const [ar, ac] of anchors) {
    for (let dr = -3; dr <= 3; dr++) {
      for (let dc = -3; dc <= 3; dc++) {
        const r = ar + dr;
        const c = ac + dc;
        if (r < 0 || r >= BOARD_SIZE || c < 0 || c >= BOARD_SIZE || matrix[r][c] !== 0) continue;
        const key = `${r},${c}`;
        if (seen.has(key)) continue;
        seen.add(key);
        let ownNear = 0;
        let oppNear = 0;
        let emptyNear = 0;
        for (let rr = Math.max(0, r - 2); rr <= Math.min(BOARD_SIZE - 1, r + 2); rr++) {
          for (let cc = Math.max(0, c - 2); cc <= Math.min(BOARD_SIZE - 1, c + 2); cc++) {
            if (matrix[rr][cc] === side) ownNear += 1;
            else if (matrix[rr][cc] === opp) oppNear += 1;
            else emptyNear += 1;
          }
        }
        const sim = simulateGoMove(matrix, r, c, side);
        if (!sim) continue;
        const oppCapture = opponentCaptureIfIgnored(matrix, r, c, side);
        const opponentValue = opponentMoveValue(matrix, r, c, side);
        const ownInfluence = influenceMap[side][r][c];
        const oppInfluence = influenceMap[opp][r][c];
        const contested = Math.min(ownInfluence, oppInfluence);
        const enemyMoyo = Math.max(0, oppInfluence - ownInfluence);
        const ownMoyo = Math.max(0, ownInfluence - oppInfluence);
        const center = 18 - Math.abs(r - 9) - Math.abs(c - 9);
        const starBonus = ((r === 3 || r === 9 || r === 15) && (c === 3 || c === 9 || c === 15)) ? 6 : 0;
        const fillOwnEyePenalty = ownNear >= 3 && oppNear === 0 ? 950 : 0;
        const selfAtariPenalty = sim.ownLiberties === 1 && sim.capturedCount === 0 ? 1200 : 0;
        const deepInEnemyPenalty =
          enemyMoyo >= 22 && ownNear === 0 && sim.capturedCount === 0 && sim.ownLiberties <= 2 ? 900 : 0;
        const connection = sim.adjacentOwnGroups >= 2 ? 760 + sim.ownGroupSize * 18 : 0;
        const cut = sim.adjacentOppGroups >= 2 ? 680 : 0;
        const capture = sim.capturedCount > 0 ? 2200 + sim.capturedCount * 520 : 0;
        const rescue = sim.savesOwnAtari > 0 ? 1900 + sim.savedStones * 260 : 0;
        const attack = sim.attacksOppAtari > 0 ? 1050 + sim.attackedStones * 180 : 0;
        const defense = oppCapture > 0 ? 1650 + oppCapture * 420 : 0;
        const denyOpponentPlan = Math.min(2100, Math.round(opponentValue * 0.46));
        const reduceEnemyMoyo =
          enemyMoyo >= 10 && sim.ownLiberties >= 3
            ? Math.min(1700, enemyMoyo * 42 + contested * 18 + oppNear * 55)
            : 0;
        const buildOwnMoyo = ownMoyo >= 8 && oppNear <= 1 ? Math.min(850, ownMoyo * 24 + ownNear * 45) : 0;
        const libertyShape = Math.min(sim.ownLiberties, 6) * 110 + Math.min(emptyNear, 16) * 8;
        const influence = center * 18 + starBonus * 80 + ownNear * 54 + oppNear * 66 + contested * 16;
        const score =
          capture +
          rescue +
          defense +
          attack +
          denyOpponentPlan +
          reduceEnemyMoyo +
          buildOwnMoyo +
          connection +
          cut +
          libertyShape +
          influence -
          selfAtariPenalty -
          fillOwnEyePenalty -
          deepInEnemyPenalty;
        let why = '全局评估：兼顾布局、连接和双方气口';
        if (capture) why = `可提掉对方 ${sim.capturedCount} 子，优先获得实利`;
        else if (rescue) why = `救回己方被打吃棋块（${sim.savedStones} 子），先稳住基本盘`;
        else if (defense) why = `防止对手在此提掉我方 ${oppCapture} 子，先消除威胁`;
        else if (attack) why = `压缩对方气口，制造打吃压力（目标约 ${sim.attackedStones} 子）`;
        else if (denyOpponentPlan >= 900) why = '抢占对手下一手价值点，提前打断对方布局';
        else if (reduceEnemyMoyo >= 700) why = '打入/削减对方势力范围，防止对手围大空';
        else if (connection) why = '连接己方棋块，减少被分断风险';
        else if (cut) why = '切断对方棋形，打乱对手布局';
        else if (buildOwnMoyo >= 450) why = '扩张己方势力，同时保留后续连接';
        else if (fillOwnEyePenalty) why = '该点接近己方眼位，已降权避免无意义填眼';
        candidates.push({
          row: r + 1,
          col: c + 1,
          score,
          why,
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
  const { t } = useTranslation();
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
  const [katagoStartedAt, setKataGoStartedAt] = useState(0);
  const [katagoStatusTick, setKataGoStatusTick] = useState(Date.now());
  const [katagoLastMeta, setKataGoLastMeta] = useState('');
  const katagoSeqRef = useRef(0);
  const katagoOkKeyRef = useRef('');
  const katagoFailCooldownRef = useRef<{ key: string; until: number }>({ key: '', until: 0 });
  const katagoCacheRef = useRef<Map<string, AdvisorMove[]>>(new Map());
  const katagoEverOkRef = useRef(false);
  const fallbackMoves = useMemo(
    () => (mySideNum ? fallbackGoSuggestions(matrix, mySideNum) : []),
    [matrix, mySideNum],
  );
  const playableKataGoMoves = useMemo(() => filterPlayableMoves(katagoMoves, matrix), [katagoMoves, matrix]);
  const shownMoves = playableKataGoMoves.length > 0 ? playableKataGoMoves : fallbackMoves;
  const katagoElapsedSec = katagoPending && katagoStartedAt > 0
    ? Math.max(0, Math.floor((katagoStatusTick - katagoStartedAt) / 1000))
    : 0;
  const katagoStatusText = katagoPending
    ? `KataGo 分析中 ${katagoElapsedSec}s，已先显示内置全局建议`
    : playableKataGoMoves.length
      ? `已接入 KataGo${katagoLastMeta ? `（${katagoLastMeta}）` : ''}`
      : katagoError
        ? `已回退内置：${katagoError}`
        : '等待 KataGo，当前显示内置全局建议';

  useEffect(() => {
    if (!katagoPending) return;
    const timer = window.setInterval(() => setKataGoStatusTick(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [katagoPending]);

  useEffect(() => {
    if (!isHiddenMaster || !mySideNum || !sig || disabled) {
      katagoSeqRef.current += 1;
      setKataGoMoves([]);
      setKataGoError('');
      setKataGoPending(false);
      setKataGoStartedAt(0);
      return;
    }

    const key = `${sig}|${mySideNum}`;
    const likelyMyTurn = myTurn || (!turn.name && isGoSideTurnByMatrix(matrix, mySideNum));

    if (!likelyMyTurn) {
      katagoSeqRef.current += 1;
      setKataGoPending(false);
      setKataGoMoves([]);
      setKataGoError('');
      setKataGoStartedAt(0);
      return;
    }

    const cached = katagoCacheRef.current.get(key);
    const playableCached = cached ? filterPlayableMoves(cached, matrix) : [];
    if (cached?.length && playableCached.length === 0) {
      katagoCacheRef.current.delete(key);
      katagoOkKeyRef.current = '';
    } else if (playableCached.length) {
      setKataGoMoves(playableCached);
      setKataGoError('');
      katagoOkKeyRef.current = key;
      return;
    }

    if (katagoOkKeyRef.current === key || katagoPending) return;
    if (katagoFailCooldownRef.current.key === key && Date.now() < katagoFailCooldownRef.current.until) return;

    const seq = katagoSeqRef.current + 1;
    katagoSeqRef.current = seq;
    setKataGoPending(true);
    setKataGoStartedAt(Date.now());
    setKataGoStatusTick(Date.now());
    setKataGoError('');

    const stoneN = goStoneCount(matrix);
    const maxVisits = stoneN <= 80 ? 64 : stoneN <= 180 ? 96 : 128;
    const timeoutMs = katagoEverOkRef.current ? 30000 : 45000;
    const guard = new Promise<never>((_, reject) => {
      window.setTimeout(() => reject(new Error('KataGo 响应超时（前端保护）')), timeoutMs + 5000);
    });

    Promise.race([
      window.api.analyzeGoKataGo({
        board: matrix,
        mySide: mySideNum,
        komi: 6.5,
        maxVisits,
        timeoutMs,
      }),
      guard,
    ])
      .then((resp) => {
        if (katagoSeqRef.current !== seq) return;
        if (resp.ok && resp.suggestions?.length) {
          const moves = resp.suggestions.slice(0, 6).map((m, i) => {
            const wr = typeof m.winrate === 'number' ? `胜率${Math.round(m.winrate * 100)}%` : '';
            const lead = typeof m.scoreLead === 'number' ? `目差${m.scoreLead.toFixed(1)}` : '';
            const visits = typeof m.visits === 'number' ? `访问${m.visits}` : '';
            return {
              row: m.row,
              col: m.col,
              label: `KataGo${i + 1}: ${m.row},${m.col}`,
              detail: [wr, lead, visits].filter(Boolean).join('，') || 'KataGo 推荐',
              source: 'katago' as const,
            };
          }).filter((m) => isLegalEmptyPoint(matrix, m.row, m.col)).slice(0, 3);
          if (moves.length === 0) {
            setKataGoMoves([]);
            setKataGoError('KataGo 返回的建议点当前已不可落子，已自动过滤。');
            katagoFailCooldownRef.current = { key, until: Date.now() + 5000 };
            return;
          }
          setKataGoMoves(moves);
          setKataGoLastMeta(`耗时${resp.ms}ms${moves[0]?.detail ? `，${moves[0].detail}` : ''}`);
          katagoCacheRef.current.set(key, moves);
          while (katagoCacheRef.current.size > 24) {
            const first = katagoCacheRef.current.keys().next();
            if (first.done) break;
            katagoCacheRef.current.delete(first.value);
          }
          katagoEverOkRef.current = true;
          katagoOkKeyRef.current = key;
          katagoFailCooldownRef.current = { key: '', until: 0 };
          setKataGoError('');
        } else {
          setKataGoMoves([]);
          setKataGoError(resp.error || 'KataGo 未返回可用建议');
          katagoFailCooldownRef.current = { key, until: Date.now() + 15000 };
        }
      })
      .catch((err: unknown) => {
        if (katagoSeqRef.current !== seq) return;
        setKataGoMoves([]);
        setKataGoError(err instanceof Error ? err.message : 'KataGo 调用失败');
        katagoFailCooldownRef.current = { key, until: Date.now() + 15000 };
      })
      .finally(() => {
        if (katagoSeqRef.current === seq) {
          setKataGoPending(false);
          setKataGoStartedAt(0);
        }
      });
  }, [isHiddenMaster, mySideNum, sig, matrix, disabled, myTurn, turn.name, katagoPending]);
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
        <button className="mini-btn" disabled={disabled} onClick={() => onCmd('pass')}>{t('game.go.pass')}</button>
        <button className="mini-btn" disabled={disabled} onClick={() => onCmd('/game join')}>{t('game.go.join')}</button>
        <button className="mini-btn" disabled={disabled} onClick={() => onCmd('/game seats')}>{t('game.go.seats')}</button>
        <button className="mini-btn" disabled={disabled} onClick={() => onCmd('/game resign')}>{t('game.go.resign')}</button>
      </div>
      {turn.name && !myTurn && (
        <div className="game-workbench-hint">当前不是你的回合，可先观察气口、劫点和下一手方向。</div>
      )}
      {isHiddenMaster && mySideNum && (
        <div className="game-advisor game-advisor-info" style={{ marginTop: 8, marginBottom: 8 }}>
          <div className="game-advisor-title">隐藏功能：KataGo 围棋助手</div>
          <div className="game-advisor-detail">
            状态：{katagoStatusText}
          </div>
          <div className="game-advisor-detail">
            内置建议会同时评估我方执子、对方气口、提子、救棋、连接、切断和自紧气风险。
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

