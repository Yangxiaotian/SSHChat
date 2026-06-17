import React, { useEffect, useMemo, useRef, useState } from 'react';

type Props = {
  disabled: boolean;
  nickname: string;
  boardText: string;
  onPick: (row: number, col: number) => void;
};

type Stone = '.' | '#' | 'o';
type Side = '#' | 'o';

type Cell = {
  row: number;
  col: number;
  stone: Stone;
  last: boolean;
  optimistic?: boolean;
};

type StrategyId =
  | 'auto'
  | 'balance'
  | 'pressure_attack'
  | 'iron_defense'
  | 'pro_forcing'
  | 'rapfi_external'
  | 'double_three'
  | 'four_three'
  | 'serial_rush'
  | 'center_control';

type StrategyOption = {
  id: StrategyId;
  label: string;
  desc: string;
};

type StrategyProfile = {
  attack: number;
  defense: number;
  trap: number;
  opening: number;
  risk: number;
  lookahead: number;
  width: number;
};

type Threat = {
  five: number;
  openFour: number;
  closedFour: number;
  brokenFour: number;
  openThree: number;
  brokenThree: number;
  openTwo: number;
  forks: number;
};

type Move = { r: number; c: number };

type Suggestion = {
  row: number;
  col: number;
  score: number;
  reason: string;
  style: string;
  predictedReply?: string;
};

type SearchStats = {
  depth: number;
  nodes: number;
  ms: number;
  timedOut: boolean;
};

type OpponentPlan = {
  label: string;
  detail: string;
  pressure: number;
  counterFocus: string;
  disruptTargets: string[];
};

type TacticStage = 'opening' | 'midgame' | 'finishing';

type TacticDef = {
  id: string;
  label: string;
  stage: TacticStage;
  summary: string;
  tags: Array<'center' | 'edge' | 'diag' | 'jump' | 'fork' | 'block' | 'rush' | 'stabilize' | 'convert' | 'trap'>;
};

type ActiveTacticPlan = {
  stage: TacticStage;
  poolSize: number;
  active: TacticDef[];
  weighted: Array<{ id: string; w: number }>;
  crossPhase: number;
};

type DecisionPlan = {
  mode: '先布局' | '先反制' | '堵点后反制' | '堵点后继续主策略';
  winRate: number;
  threatLevel: '低' | '中' | '高' | '致命';
  myWinSteps: number | null;
  oppWinSteps: number | null;
  immediateThreatPoints: Move[];
  summary: string;
  priorityLine: string;
  stageGoal: string;
};

type BlockActionClass = '只堵' | '堵后反制' | '堵后回主线' | '非堵点';

type EngineResult = {
  suggestions: Suggestion[];
  strategy: Exclude<StrategyId, 'auto'>;
  stats: SearchStats;
  opponentPlan: OpponentPlan;
  tacticPlan: ActiveTacticPlan;
  decision: DecisionPlan;
};

type TTFlag = 'exact' | 'lower' | 'upper';

type TTEntry = {
  depth: number;
  score: number;
  flag: TTFlag;
  best?: Move;
};

type SearchCtx = {
  startMs: number;
  timeBudgetMs: number;
  nodeBudget: number;
  nodes: number;
  timedOut: boolean;
  tt: Map<string, TTEntry>;
  widthFactor: number;
};

const BOARD_SIZE = 15;
const CENTER = 8;
const SEARCH_TIME_BUDGET_MS = 880;
const SEARCH_NODE_BUDGET = 160000;
const MIN_LOOKAHEAD_DEPTH = 8;
const OFFTURN_TIME_BUDGET_MS = 360;
const OFFTURN_NODE_BUDGET = 42000;
const OFFTURN_MAX_DEPTH = 8;
const ANALYSIS_STABLE_DELAY_MS = 140;
const ANALYSIS_CACHE_LIMIT = 80;
const OPENING_VARIATION_ANCHORS: Array<[number, number]> = [
  [8, 8], [8, 7], [7, 8], [8, 9], [9, 8],
  [7, 7], [7, 9], [9, 7], [9, 9],
  [6, 8], [8, 6], [10, 8], [8, 10],
  [6, 7], [7, 6], [10, 9], [9, 10],
  [6, 9], [9, 6], [10, 7],
];

const STRATEGIES: StrategyOption[] = [
  { id: 'auto', label: '智能博弈（推荐）', desc: '按局势自动切换攻防与杀招节奏。' },
  { id: 'balance', label: '均衡控局', desc: '稳健推进，攻守均衡，不给对手轻易抓手。' },
  { id: 'pressure_attack', label: '先手压迫', desc: '持续制造先手威胁，逼对手被动应手。' },
  { id: 'iron_defense', label: '铁壁反击', desc: '先化解对手杀机，再抓反打窗口。' },
  { id: 'pro_forcing', label: '职业威胁链', desc: '优先处理强制线交集防点，再转入高质量反击。' },
  { id: 'rapfi_external', label: 'Rapfi职业引擎', desc: '调用外部Rapfi引擎给出职业级建议；失败自动回退内置。' },
  { id: 'double_three', label: '双三诱杀', desc: '布局双三结构，制造一防难尽的陷阱。' },
  { id: 'four_three', label: '四三做杀', desc: '冲四配活三，形成高压强制交换。' },
  { id: 'serial_rush', label: '连环冲四', desc: '冲四接冲四，追求连续手筋击穿防线。' },
  { id: 'center_control', label: '中腹运营', desc: '抢中腹效率位，兼顾扩张与转身。' },
];

const PROFILE_MAP: Record<Exclude<StrategyId, 'auto'>, StrategyProfile> = {
  balance: { attack: 1.0, defense: 1.0, trap: 1.0, opening: 1.0, risk: 1.0, lookahead: 1.0, width: 1.0 },
  pressure_attack: { attack: 1.2, defense: 0.92, trap: 1.06, opening: 1.0, risk: 0.92, lookahead: 1.05, width: 1.0 },
  iron_defense: { attack: 0.9, defense: 1.32, trap: 0.9, opening: 0.95, risk: 1.25, lookahead: 1.0, width: 1.0 },
  pro_forcing: { attack: 1.05, defense: 1.35, trap: 1.18, opening: 1.0, risk: 1.28, lookahead: 1.08, width: 0.95 },
  rapfi_external: { attack: 1.0, defense: 1.1, trap: 1.0, opening: 1.0, risk: 1.1, lookahead: 0.72, width: 0.78 },
  double_three: { attack: 1.05, defense: 0.95, trap: 1.36, opening: 1.0, risk: 0.95, lookahead: 1.08, width: 1.02 },
  four_three: { attack: 1.15, defense: 1.0, trap: 1.2, opening: 1.0, risk: 0.95, lookahead: 1.08, width: 1.0 },
  serial_rush: { attack: 1.28, defense: 0.85, trap: 1.06, opening: 0.95, risk: 0.9, lookahead: 1.12, width: 0.96 },
  center_control: { attack: 0.95, defense: 1.0, trap: 1.0, opening: 1.3, risk: 1.0, lookahead: 0.96, width: 1.08 },
};

const OPENING_TACTICS: TacticDef[] = [
  { id: 'op_01', label: '天元抢先', stage: 'opening', summary: '抢占中心效率位，优先建立全向机动。', tags: ['center', 'stabilize'] },
  { id: 'op_02', label: '星位牵制', stage: 'opening', summary: '用星位形成弹性支点，便于向中腹转身。', tags: ['center', 'convert'] },
  { id: 'op_03', label: '斜线潜伏', stage: 'opening', summary: '沿对角线暗铺骨架，等待中盘加速。', tags: ['diag', 'trap'] },
  { id: 'op_04', label: '双翼对撑', stage: 'opening', summary: '左右两翼同时布点，防止被单边压制。', tags: ['edge', 'stabilize'] },
  { id: 'op_05', label: '边角诱敌', stage: 'opening', summary: '边角试探诱导对手外扩，再抢回中枢。', tags: ['edge', 'convert'] },
  { id: 'op_06', label: '跳连铺桥', stage: 'opening', summary: '提前布置跳连桥点，为中盘连手做准备。', tags: ['jump', 'trap'] },
  { id: 'op_07', label: '三角据点', stage: 'opening', summary: '构建三角支撑，提升局部稳定性。', tags: ['center', 'stabilize'] },
  { id: 'op_08', label: '中线压边', stage: 'opening', summary: '中心发力压制边路，限制对手展开。', tags: ['center', 'block'] },
  { id: 'op_09', label: '对角牵引', stage: 'opening', summary: '利用对角牵引分散对手防守资源。', tags: ['diag', 'convert'] },
  { id: 'op_10', label: '虚实开局', stage: 'opening', summary: '真假威胁并行，制造判断成本。', tags: ['trap', 'stabilize'] },
  { id: 'op_11', label: '厚势起手', stage: 'opening', summary: '先做厚势再图先手，避免早期风险。', tags: ['stabilize', 'block'] },
  { id: 'op_12', label: '弱侧试探', stage: 'opening', summary: '先打弱侧探路，观察对手应对习惯。', tags: ['edge', 'convert'] },
  { id: 'op_13', label: '抢先压边', stage: 'opening', summary: '先手压边争节奏，争取先手链。', tags: ['edge', 'rush'] },
  { id: 'op_14', label: '二路潜行', stage: 'opening', summary: '二路线低姿态布子，后续抬升威胁。', tags: ['edge', 'trap'] },
  { id: 'op_15', label: '中轴切换', stage: 'opening', summary: '在纵横中轴间切换，保持转向自由。', tags: ['center', 'convert'] },
  { id: 'op_16', label: '反向星位', stage: 'opening', summary: '反向支点对冲对手节奏。', tags: ['center', 'block'] },
  { id: 'op_17', label: '假先手布网', stage: 'opening', summary: '以小先手诱导落点，暗中织网。', tags: ['trap', 'convert'] },
  { id: 'op_18', label: '边中联动', stage: 'opening', summary: '边路与中路联动，减少孤子。', tags: ['edge', 'center', 'stabilize'] },
  { id: 'op_19', label: '斜跳双桥', stage: 'opening', summary: '斜跳形成双桥，争取中盘爆发。', tags: ['diag', 'jump', 'trap'] },
  { id: 'op_20', label: '控域扩张', stage: 'opening', summary: '先控制关键域，再向外扩张。', tags: ['center', 'block', 'stabilize'] },
];

const MIDGAME_TACTICS: TacticDef[] = [
  { id: 'mid_01', label: '双三诱杀', stage: 'midgame', summary: '双活三同时施压，逼出防守漏洞。', tags: ['fork', 'trap', 'rush'] },
  { id: 'mid_02', label: '四三做杀', stage: 'midgame', summary: '冲四配活三形成强制交换。', tags: ['rush', 'fork'] },
  { id: 'mid_03', label: '连环冲四', stage: 'midgame', summary: '连续冲四压缩对手决策空间。', tags: ['rush', 'convert'] },
  { id: 'mid_04', label: '断桥拆骨', stage: 'midgame', summary: '切断跳连桥点，拆解对手骨架。', tags: ['block', 'jump'] },
  { id: 'mid_05', label: '中腹控厚', stage: 'midgame', summary: '通过中腹厚势掌控节奏。', tags: ['center', 'stabilize'] },
  { id: 'mid_06', label: '弱侧突入', stage: 'midgame', summary: '攻击对手弱侧，迫使其回防。', tags: ['convert', 'rush'] },
  { id: 'mid_07', label: '反手借力', stage: 'midgame', summary: '借对手先手反做强点。', tags: ['stabilize', 'convert'] },
  { id: 'mid_08', label: '牵制转杀', stage: 'midgame', summary: '先牵制后转主攻线。', tags: ['trap', 'convert'] },
  { id: 'mid_09', label: '边中转换', stage: 'midgame', summary: '边路势能转入中腹形成二次威胁。', tags: ['edge', 'center', 'convert'] },
  { id: 'mid_10', label: '对角贯穿', stage: 'midgame', summary: '打通对角线制造长距离联动。', tags: ['diag', 'rush'] },
  { id: 'mid_11', label: '桥点压制', stage: 'midgame', summary: '持续压制对手关键桥点。', tags: ['jump', 'block'] },
  { id: 'mid_12', label: '伪先手反套', stage: 'midgame', summary: '识破伪先手后反套其节奏。', tags: ['trap', 'block'] },
  { id: 'mid_13', label: '破眼封喉', stage: 'midgame', summary: '封锁对手转折眼位。', tags: ['block', 'rush'] },
  { id: 'mid_14', label: '厚势反击', stage: 'midgame', summary: '以厚势吃薄，稳中反击。', tags: ['stabilize', 'block'] },
  { id: 'mid_15', label: '多点施压', stage: 'midgame', summary: '多点同时施压分散防守。', tags: ['fork', 'convert'] },
  { id: 'mid_16', label: '梯度推进', stage: 'midgame', summary: '由弱到强逐步抬高威胁级别。', tags: ['stabilize', 'rush'] },
  { id: 'mid_17', label: '分割战场', stage: 'midgame', summary: '切分棋盘减少对手联动。', tags: ['block', 'center'] },
  { id: 'mid_18', label: '陷阱回马', stage: 'midgame', summary: '诱敌深入后回马反打。', tags: ['trap', 'convert'] },
  { id: 'mid_19', label: '锁边压中', stage: 'midgame', summary: '锁边限制外扩并压中。', tags: ['edge', 'block'] },
  { id: 'mid_20', label: '攻守双链', stage: 'midgame', summary: '进攻链与防守链并行推进。', tags: ['fork', 'stabilize', 'convert'] },
];

const FINISH_TACTICS: TacticDef[] = [
  { id: 'fin_01', label: '活四终结', stage: 'finishing', summary: '构造双端活四直接收官。', tags: ['rush', 'convert'] },
  { id: 'fin_02', label: '冲四连杀', stage: 'finishing', summary: '冲四接冲四，连续强制。', tags: ['rush', 'fork'] },
  { id: 'fin_03', label: '双冲四绝杀', stage: 'finishing', summary: '双冲四并发，一手难防。', tags: ['fork', 'rush'] },
  { id: 'fin_04', label: '四三必杀', stage: 'finishing', summary: '四三结构完成强制终结。', tags: ['fork', 'convert'] },
  { id: 'fin_05', label: '双三转五', stage: 'finishing', summary: '双三诱导后转成五。', tags: ['trap', 'convert'] },
  { id: 'fin_06', label: '桥断反杀', stage: 'finishing', summary: '断其桥点并立刻反杀。', tags: ['jump', 'block', 'rush'] },
  { id: 'fin_07', label: '斜线穿刺', stage: 'finishing', summary: '对角穿刺形成隐蔽终结点。', tags: ['diag', 'rush'] },
  { id: 'fin_08', label: '回头绝喉', stage: 'finishing', summary: '假撤真杀，回头封喉。', tags: ['trap', 'rush'] },
  { id: 'fin_09', label: '中枢爆破', stage: 'finishing', summary: '中枢爆破带动多线终结。', tags: ['center', 'fork'] },
  { id: 'fin_10', label: '边路封死', stage: 'finishing', summary: '边路封死并转入致命点。', tags: ['edge', 'block', 'convert'] },
  { id: 'fin_11', label: '梯形杀网', stage: 'finishing', summary: '梯形结构形成终结网。', tags: ['trap', 'fork'] },
  { id: 'fin_12', label: '借手杀', stage: 'finishing', summary: '利用对手应手顺势成杀。', tags: ['convert', 'rush'] },
  { id: 'fin_13', label: '压迫封线', stage: 'finishing', summary: '封线压迫令对手无转身。', tags: ['block', 'rush'] },
  { id: 'fin_14', label: '二次先手链', stage: 'finishing', summary: '先手链二次抬升后终结。', tags: ['convert', 'rush'] },
  { id: 'fin_15', label: '复合双威胁', stage: 'finishing', summary: '双威胁叠加形成终局优势。', tags: ['fork', 'trap'] },
  { id: 'fin_16', label: '中心反扣', stage: 'finishing', summary: '中心反扣夺回主动并绝杀。', tags: ['center', 'convert'] },
  { id: 'fin_17', label: '斜跳断魂', stage: 'finishing', summary: '斜跳与断桥联动收官。', tags: ['diag', 'jump', 'rush'] },
  { id: 'fin_18', label: '围点打援', stage: 'finishing', summary: '围点后打援点一击制胜。', tags: ['block', 'trap'] },
  { id: 'fin_19', label: '反制转终结', stage: 'finishing', summary: '先反制后转必杀，稳杀转换。', tags: ['block', 'convert'] },
  { id: 'fin_20', label: '终盘锁喉', stage: 'finishing', summary: '全域锁喉，压缩至唯一败着。', tags: ['stabilize', 'rush', 'fork'] },
];

const ALL_TACTICS: TacticDef[] = [...OPENING_TACTICS, ...MIDGAME_TACTICS, ...FINISH_TACTICS];

const DIRS: Array<[number, number]> = [
  [1, 0],
  [0, 1],
  [1, 1],
  [1, -1],
];

const ZOBRIST = buildZobrist();

function buildZobrist(): bigint[][] {
  const n = BOARD_SIZE * BOARD_SIZE;
  let seed = 0x9e3779b97f4a7c15n;
  const next = () => {
    seed = (seed * 6364136223846793005n + 1442695040888963407n) & ((1n << 64n) - 1n);
    return seed;
  };
  const z = Array.from({ length: n }, () => [0n, 0n]);
  for (let i = 0; i < n; i++) {
    z[i][0] = next();
    z[i][1] = next();
  }
  return z;
}

function createEmptyBoard(): Cell[][] {
  return Array.from({ length: BOARD_SIZE }, (_, r) =>
    Array.from({ length: BOARD_SIZE }, (_, c) => ({
      row: r + 1,
      col: c + 1,
      stone: '.',
      last: false,
    })),
  );
}

function parseTurnInfo(boardText: string): { name: string; side: Side | null } {
  for (const raw of boardText.split('\n')) {
    const line = raw.trim();
    const m = line.match(/^轮到\s*(黑|白)方\s*(.+?)\s*落子/);
    if (m) {
      return { name: m[2].trim(), side: m[1] === '黑' ? '#' : 'o' };
    }
    const m2 = line.match(/^(turn|轮到)\s*[:：]\s*(.+)$/i);
    if (m2) {
      return { name: m2[2].trim(), side: null };
    }
  }
  return { name: '', side: null };
}

function parseSeats(boardText: string): { blackName: string; whiteName: string } {
  for (const raw of boardText.split('\n')) {
    const line = raw.trim();
    const m1 = line.match(/黑（先手）[:：]\s*([^\s]+)\s+白[:：]\s*([^\s]+)/);
    if (m1) return { blackName: m1[1].trim(), whiteName: m1[2].trim() };
    const m2 = line.match(/黑[:：]\s*([^\s]+)\s+白[:：]\s*([^\s]+)/);
    if (m2) return { blackName: m2[1].trim(), whiteName: m2[2].trim() };
  }
  return { blackName: '', whiteName: '' };
}

function parseBoard(boardText: string): Cell[][] {
  const board = createEmptyBoard();
  if (!boardText.trim()) return board;

  const lines = boardText.split('\n');
  let headerCols: number[] = [];
  let headerIdx = -1;

  for (let i = 0; i < lines.length; i++) {
    const nums = (lines[i].match(/\d+/g) || []).map(Number);
    if (nums.length >= BOARD_SIZE && nums.every((n) => n >= 1 && n <= BOARD_SIZE)) {
      headerCols = nums.slice(0, BOARD_SIZE);
      headerIdx = i;
      break;
    }
  }

  if (headerIdx < 0 || headerCols.length !== BOARD_SIZE) return board;

  let parsedRows = 0;
  for (let i = headerIdx + 1; i < lines.length && parsedRows < BOARD_SIZE; i++) {
    const rowMatch = lines[i].match(/^\s*(\d+)\s+(.+)$/);
    if (!rowMatch) continue;
    const rowLabel = Number(rowMatch[1]);
    if (rowLabel < 1 || rowLabel > BOARD_SIZE) continue;

    const cellsText = rowMatch[2];
    const tokens = cellsText.match(/\(#\)|\(o\)|\(\.\)|#|o|\./g) || [];
    if (tokens.length < BOARD_SIZE) continue;

    for (let cIx = 0; cIx < BOARD_SIZE; cIx++) {
      const colLabel = headerCols[cIx];
      if (colLabel < 1 || colLabel > BOARD_SIZE) continue;

      const token = tokens[cIx];
      const plain = token.replace(/[()]/g, '');
      const stone: Stone = plain === '#' ? '#' : plain === 'o' ? 'o' : '.';

      board[rowLabel - 1][colLabel - 1] = {
        row: rowLabel,
        col: colLabel,
        stone,
        last: token.startsWith('('),
      };
    }
    parsedRows += 1;
  }

  return board;
}

function toMatrix(cells: Cell[][]): number[][] {
  return cells.map((row) => row.map((cell) => (cell.stone === '#' ? 1 : cell.stone === 'o' ? -1 : 0)));
}

function inBounds(r: number, c: number): boolean {
  return r >= 0 && r < BOARD_SIZE && c >= 0 && c < BOARD_SIZE;
}

function stoneCount(board: number[][]): number {
  let n = 0;
  for (let r = 0; r < BOARD_SIZE; r++) {
    for (let c = 0; c < BOARD_SIZE; c++) if (board[r][c] !== 0) n += 1;
  }
  return n;
}

function hasNeighbor(board: number[][], r: number, c: number, dist = 2): boolean {
  for (let rr = Math.max(0, r - dist); rr <= Math.min(BOARD_SIZE - 1, r + dist); rr++) {
    for (let cc = Math.max(0, c - dist); cc <= Math.min(BOARD_SIZE - 1, c + dist); cc++) {
      if (rr === r && cc === c) continue;
      if (board[rr][cc] !== 0) return true;
    }
  }
  return false;
}

function countDir(board: number[][], r: number, c: number, dr: number, dc: number, side: number): number {
  let n = 0;
  let rr = r + dr;
  let cc = c + dc;
  while (inBounds(rr, cc) && board[rr][cc] === side) {
    n += 1;
    rr += dr;
    cc += dc;
  }
  return n;
}

function isWinByPlaced(board: number[][], r: number, c: number, side: number): boolean {
  for (const [dr, dc] of DIRS) {
    const total = 1 + countDir(board, r, c, dr, dc, side) + countDir(board, r, c, -dr, -dc, side);
    if (total >= 5) return true;
  }
  return false;
}

function winnerOnBoard(board: number[][]): 1 | -1 | null {
  for (const side of [1, -1] as const) {
    for (let r = 0; r < BOARD_SIZE; r++) {
      for (let c = 0; c < BOARD_SIZE; c++) {
        if (board[r][c] !== side) continue;
        for (const [dr, dc] of DIRS) {
          const prevR = r - dr;
          const prevC = c - dc;
          if (inBounds(prevR, prevC) && board[prevR][prevC] === side) continue;
          let len = 0;
          let rr = r;
          let cc = c;
          while (inBounds(rr, cc) && board[rr][cc] === side) {
            len += 1;
            rr += dr;
            cc += dc;
          }
          if (len >= 5) return side;
        }
      }
    }
  }
  return null;
}

/** 检测对手在指定位置附近是否有活三/冲四等威胁 */
function detectOpponentThreat(board: number[][], row: number, col: number, mySide: number): 'defend' | null {
  const opp = -mySide;
  // 模拟对手在此落子，检查是否形成活三或冲四
  const sim = board.map((r) => r.slice());
  const r0 = row - 1, c0 = col - 1;
  if (!inBounds(r0, c0)) return null;
  sim[r0][c0] = opp;
  for (const [dr, dc] of DIRS) {
    const cnt = 1 + countDir(sim, r0, c0, dr, dc, opp) + countDir(sim, r0, c0, -dr, -dc, opp);
    if (cnt >= 4) return 'defend'; // 对手能成四或更多，必须堵
  }
  // 检查对手在此位置周围是否有活三（未落子时已有3连且两端空）
  for (const [dr, dc] of DIRS) {
    const cnt = countDir(board, r0, c0, dr, dc, opp) + countDir(board, r0, c0, -dr, -dc, opp);
    if (cnt >= 3) {
      // 检查两端是否空
      const end1r = r0 + (cnt > countDir(board, r0, c0, dr, dc, opp) ? -dr : dr) * (countDir(board, r0, c0, dr, dc, opp) + 1);
      const end1c = c0 + (cnt > countDir(board, r0, c0, dr, dc, opp) ? -dc : dc) * (countDir(board, r0, c0, dr, dc, opp) + 1);
      const end2r = r0 + dr * (countDir(board, r0, c0, dr, dc, opp) + 1);
      const end2c = c0 + dc * (countDir(board, r0, c0, dr, dc, opp) + 1);
      if (inBounds(end1r, end1c) && board[end1r][end1c] === 0 &&
          inBounds(end2r, end2c) && board[end2r][end2c] === 0) {
        return 'defend'; // 对手有活三，必须堵
      }
    }
  }
  return null;
}

function urgentDefenseSeverity(t: Threat): number {
  if (t.five > 0) return 5;
  if (t.openFour > 0) return 4;
  if (t.closedFour > 0 || t.brokenFour > 0) return 3;
  if (t.forks > 0 && t.openThree + t.brokenThree >= 2) return 2;
  return 0;
}

function urgentDefenseLabel(t: Threat): { style: string; reason: string } {
  if (t.five > 0) {
    return { style: '必堵杀点', reason: '对手下一手可直接连五，必须先堵。' };
  }
  if (t.openFour > 0) {
    return { style: '强制防守', reason: '对手三连已可转活四，先封住冲四点。' };
  }
  if (t.closedFour > 0 || t.brokenFour > 0) {
    return { style: '强制防守', reason: '对手三连/跳三已可转冲四，不能继续无脑走自己。' };
  }
  return { style: '反制双威胁', reason: '对手可形成双三/多线威胁，先切断关键连接点。' };
}

function findUrgentDefenseMove(board: number[][], mySide: number): Suggestion | null {
  // 如果我方已有一步胜，优先直接获胜，不被防守兜底打断。
  if (immediateWinningPoints(board, mySide).length > 0) return null;

  const opp = -mySide;
  const candidates: Move[] = [];
  for (let r = 0; r < BOARD_SIZE; r++) {
    for (let c = 0; c < BOARD_SIZE; c++) {
      if (board[r][c] !== 0) continue;
      if (!hasNeighbor(board, r, c, 2)) continue;
      candidates.push({ r, c });
    }
  }

  let best: { move: Move; threat: Threat; severity: number; score: number } | null = null;
  for (const mv of candidates) {
    const threat = analyzeThreatAt(board, mv.r, mv.c, opp);
    const severity = urgentDefenseSeverity(threat);
    if (severity <= 0) continue;

    board[mv.r][mv.c] = mySide;
    const remainingWins = immediateWinningPointsScoped(board, opp, 24).length;
    const remainingForcing = bestForcingSeverity(board, opp);
    const counterShape = directionalPotential(board, mv.r, mv.c, mySide);
    board[mv.r][mv.c] = 0;

    const score =
      severity * 10_000_000 +
      threatScore(threat) * 8 +
      counterShape * 800 -
      remainingWins * 2_200_000 -
      remainingForcing * 260_000;
    if (!best || score > best.score) {
      best = { move: mv, threat, severity, score };
    }
  }

  if (!best) return null;
  const label = urgentDefenseLabel(best.threat);
  return {
    row: best.move.r + 1,
    col: best.move.c + 1,
    score: Number.MAX_SAFE_INTEGER - 1,
    reason: `${label.reason}（Rapfi未返回时由内置兜底提供）`,
    style: label.style,
  };
}

function lineString(board: number[][], r: number, c: number, dr: number, dc: number, side: number): string {
  let s = '';
  for (let k = -4; k <= 4; k++) {
    const rr = r + k * dr;
    const cc = c + k * dc;
    if (!inBounds(rr, cc)) {
      s += 'O';
      continue;
    }
    const v = board[rr][cc];
    if (v === 0) s += '.';
    else if (v === side) s += 'X';
    else s += 'O';
  }
  return s;
}

function emptyThreat(): Threat {
  return {
    five: 0,
    openFour: 0,
    closedFour: 0,
    brokenFour: 0,
    openThree: 0,
    brokenThree: 0,
    openTwo: 0,
    forks: 0,
  };
}

function analyzeThreatAt(board: number[][], r: number, c: number, side: number): Threat {
  if (!inBounds(r, c) || board[r][c] !== 0) return emptyThreat();

  board[r][c] = side;
  const t = emptyThreat();

  for (const [dr, dc] of DIRS) {
    const left = countDir(board, r, c, -dr, -dc, side);
    const right = countDir(board, r, c, dr, dc, side);
    const total = left + right + 1;

    const leftEndR = r - (left + 1) * dr;
    const leftEndC = c - (left + 1) * dc;
    const rightEndR = r + (right + 1) * dr;
    const rightEndC = c + (right + 1) * dc;

    const leftOpen = inBounds(leftEndR, leftEndC) && board[leftEndR][leftEndC] === 0;
    const rightOpen = inBounds(rightEndR, rightEndC) && board[rightEndR][rightEndC] === 0;
    const openEnds = (leftOpen ? 1 : 0) + (rightOpen ? 1 : 0);

    if (total >= 5) {
      t.five += 1;
    } else if (total === 4) {
      if (openEnds === 2) t.openFour += 1;
      else if (openEnds === 1) t.closedFour += 1;
    } else if (total === 3) {
      if (openEnds === 2) t.openThree += 1;
      else if (openEnds === 1) t.brokenThree += 1;
    } else if (total === 2 && openEnds === 2) {
      t.openTwo += 1;
    }

    const line = lineString(board, r, c, dr, dc, side);
    if (/X\.XXX|XX\.XX|XXX\.X/.test(line)) t.brokenFour += 1;
    if (/\.XX\.X\.|\.X\.XX\./.test(line)) t.openThree += 1;
    if (/XX\.\.X|X\.\.XX|X\.X\.X/.test(line)) t.brokenThree += 1;
  }

  const fourPressure = t.openFour * 2 + t.closedFour + t.brokenFour;
  const threePressure = t.openThree + t.brokenThree;
  if (fourPressure >= 2) t.forks += 1;
  if (threePressure >= 2) t.forks += 1;

  board[r][c] = 0;
  return t;
}

function threatScore(t: Threat): number {
  return (
    t.five * 1_000_000 +
    t.openFour * 260_000 +
    t.closedFour * 110_000 +
    t.brokenFour * 95_000 +
    t.openThree * 33_000 +
    t.brokenThree * 16_000 +
    t.openTwo * 2_800 +
    t.forks * 150_000
  );
}

function forcingSeverityFromThreat(t: Threat): number {
  if (t.five > 0 || t.openFour > 0) return 3;
  if (t.closedFour > 0 || t.brokenFour > 0 || t.forks > 0) return 2;
  if (t.openThree > 0 || t.brokenThree > 0) return 1;
  return 0;
}

function forcingStartMoves(board: number[][], side: number, limit = 10): Move[] {
  const cands = collectCandidates(board, side, Math.max(12, limit * 2), 1);
  const scored: Array<{ mv: Move; sev: number; s: number }> = [];
  for (const mv of cands) {
    if (board[mv.r][mv.c] !== 0) continue;
    const t = analyzeThreatAt(board, mv.r, mv.c, side);
    const sev = forcingSeverityFromThreat(t);
    if (sev <= 0) continue;
    scored.push({ mv, sev, s: threatScore(t) + directionalPotential(board, mv.r, mv.c, side) * 120 });
  }
  scored.sort((a, b) => b.sev - a.sev || b.s - a.s);
  return scored.slice(0, limit).map((x) => x.mv);
}

function bestForcingSeverity(board: number[][], side: number): number {
  let best = 0;
  const cands = collectCandidates(board, side, 10, 1);
  for (const mv of cands) {
    if (board[mv.r][mv.c] !== 0) continue;
    const sev = forcingSeverityFromThreat(analyzeThreatAt(board, mv.r, mv.c, side));
    if (sev > best) best = sev;
    if (best >= 3) return 3;
  }
  return best;
}

function survivingForcingStarts(boardAfterMyMove: number[][], opp: number, starts: Move[]): number {
  let n = 0;
  for (const mv of starts) {
    if (boardAfterMyMove[mv.r][mv.c] !== 0) continue;
    const sev = forcingSeverityFromThreat(analyzeThreatAt(boardAfterMyMove, mv.r, mv.c, opp));
    if (sev >= 2) n += 1;
  }
  return n;
}

function intersectsMoveSet(a: Move[], b: Move[]): Move[] {
  const bs = new Set<string>(b.map((m) => `${m.r},${m.c}`));
  return a.filter((m) => bs.has(`${m.r},${m.c}`));
}

function defenseIntersectionAgainstForcingStarts(board: number[][], opp: number, topN = 3): Move[] {
  const my = -opp;
  const starts = forcingStartMoves(board, opp, topN);
  if (starts.length === 0) return [];

  const defenseCandidates = uniqMoves([
    ...starts,
    ...collectCandidates(board, my, 14, 1),
    ...collectCandidates(board, opp, 10, 1),
  ]).filter((m) => board[m.r][m.c] === 0);
  if (defenseCandidates.length === 0) return [];

  let intersection = defenseCandidates.slice();
  for (const st of starts) {
    const valid: Move[] = [];
    for (const d of defenseCandidates) {
      if (board[d.r][d.c] !== 0) continue;
      board[d.r][d.c] = my;
      let safe = false;

      if (d.r === st.r && d.c === st.c) {
        safe = true;
      } else if (board[st.r][st.c] === 0) {
        board[st.r][st.c] = opp;
        const sev = bestForcingSeverity(board, opp);
        const imm = immediateWinningPointsScoped(board, opp, 10).length;
        safe = sev <= 1 && imm === 0;
        board[st.r][st.c] = 0;
      }

      board[d.r][d.c] = 0;
      if (safe) valid.push(d);
    }

    if (valid.length === 0) return [];
    intersection = intersectsMoveSet(intersection, valid);
    if (intersection.length === 0) return [];
  }

  return uniqMoves(intersection);
}

function openingBonus(row: number, col: number): number {
  const dist = Math.abs(row - CENTER) + Math.abs(col - CENTER);
  return Math.max(0, 160 - dist * 18);
}

function directionalPotential(board: number[][], r: number, c: number, side: number): number {
  let score = 0;
  for (const [dr, dc] of DIRS) {
    const left = countDir(board, r, c, -dr, -dc, side);
    const right = countDir(board, r, c, dr, dc, side);
    const total = left + right + 1;

    const leftOpenR = r - (left + 1) * dr;
    const leftOpenC = c - (left + 1) * dc;
    const rightOpenR = r + (right + 1) * dr;
    const rightOpenC = c + (right + 1) * dc;
    const openEnds =
      (inBounds(leftOpenR, leftOpenC) && board[leftOpenR][leftOpenC] === 0 ? 1 : 0) +
      (inBounds(rightOpenR, rightOpenC) && board[rightOpenR][rightOpenC] === 0 ? 1 : 0);

    score += total * total * 14 + openEnds * 10;
  }
  return score;
}

function moveIndex(r: number, c: number): number {
  return r * BOARD_SIZE + c;
}

function hashOfBoard(board: number[][]): bigint {
  let h = 0n;
  for (let r = 0; r < BOARD_SIZE; r++) {
    for (let c = 0; c < BOARD_SIZE; c++) {
      const v = board[r][c];
      if (v === 0) continue;
      h ^= ZOBRIST[moveIndex(r, c)][v === 1 ? 0 : 1];
    }
  }
  return h;
}

function hashKey(hash: bigint, sideToMove: number): string {
  return `${sideToMove > 0 ? 'B' : 'W'}:${hash.toString()}`;
}

function collectCandidates(board: number[][], side: number, limit = 24, widthFactor = 1): Move[] {
  const stones = stoneCount(board);
  if (stones === 0) return [{ r: CENTER - 1, c: CENTER - 1 }];

  const dist = stones <= 8 ? 3 : 2;
  const raw: Array<{ r: number; c: number; score: number }> = [];
  const opp = -side;

  for (let r = 0; r < BOARD_SIZE; r++) {
    for (let c = 0; c < BOARD_SIZE; c++) {
      if (board[r][c] !== 0) continue;
      if (!hasNeighbor(board, r, c, dist)) continue;

      const selfThreat = threatScore(analyzeThreatAt(board, r, c, side));
      const oppThreat = threatScore(analyzeThreatAt(board, r, c, opp));
      const shape = directionalPotential(board, r, c, side);
      const center = openingBonus(r + 1, c + 1) * 180;
      const score = selfThreat * 1.05 + oppThreat * 0.96 + shape * 130 + center;
      raw.push({ r, c, score });
    }
  }

  raw.sort((a, b) => b.score - a.score);
  const finalLimit = Math.max(8, Math.min(30, Math.round(limit * widthFactor)));
  return raw.slice(0, finalLimit).map((x) => ({ r: x.r, c: x.c }));
}

function topThreatComposite(board: number[][], side: number): number {
  const cands = collectCandidates(board, side, 10, 1);
  if (cands.length === 0) return 0;
  const vals = cands
    .map((m) => threatScore(analyzeThreatAt(board, m.r, m.c, side)))
    .sort((a, b) => b - a);
  const a = vals[0] || 0;
  const b = vals[1] || 0;
  const c = vals[2] || 0;
  return a + (b + c) * 0.38;
}

function boardEval(board: number[][], my: number): number {
  const opp = -my;
  const myTop = topThreatComposite(board, my);
  const oppTop = topThreatComposite(board, opp);
  return myTop - oppTop * 1.08;
}

function centerMassScore(board: number[][], side: number): number {
  let score = 0;
  for (let r = 0; r < BOARD_SIZE; r++) {
    for (let c = 0; c < BOARD_SIZE; c++) {
      if (board[r][c] !== side) continue;
      const dist = Math.abs(r + 1 - CENTER) + Math.abs(c + 1 - CENTER);
      score += Math.max(0, 10 - dist);
    }
  }
  return score;
}

function edgeMassScore(board: number[][], side: number): number {
  let score = 0;
  for (let r = 0; r < BOARD_SIZE; r++) {
    for (let c = 0; c < BOARD_SIZE; c++) {
      if (board[r][c] !== side) continue;
      const nearEdge = r <= 2 || r >= BOARD_SIZE - 3 || c <= 2 || c >= BOARD_SIZE - 3;
      if (nearEdge) score += 1;
    }
  }
  return score;
}

function diagonalMassScore(board: number[][], side: number): number {
  let score = 0;
  for (let r = 0; r < BOARD_SIZE; r++) {
    for (let c = 0; c < BOARD_SIZE; c++) {
      if (board[r][c] !== side) continue;
      const d1 = Math.abs(r - c);
      const d2 = Math.abs(r + c - (BOARD_SIZE - 1));
      if (d1 <= 1 || d2 <= 1) score += 1;
    }
  }
  return score;
}

function jumpLinkScore(board: number[][], side: number): number {
  let n = 0;
  for (let r = 0; r < BOARD_SIZE; r++) {
    for (let c = 0; c < BOARD_SIZE; c++) {
      if (board[r][c] !== side) continue;
      for (const [dr, dc] of DIRS) {
        const r1 = r + dr;
        const c1 = c + dc;
        const r2 = r + dr * 2;
        const c2 = c + dc * 2;
        if (!inBounds(r2, c2)) continue;
        if (board[r1][c1] === 0 && board[r2][c2] === side) n += 1;
      }
    }
  }
  return n;
}

function detectOpponentPlan(board: number[][], opp: number): OpponentPlan {
  const cands = collectCandidates(board, opp, 12, 1);
  const threats = cands.map((m) => analyzeThreatAt(board, m.r, m.c, opp));
  const total = threats.reduce(
    (acc, t) => {
      acc.openFour += t.openFour;
      acc.closedFour += t.closedFour + t.brokenFour;
      acc.openThree += t.openThree + t.brokenThree;
      acc.forks += t.forks;
      return acc;
    },
    { openFour: 0, closedFour: 0, openThree: 0, forks: 0 },
  );

  const center = centerMassScore(board, opp);
  const edge = edgeMassScore(board, opp);
  const diag = diagonalMassScore(board, opp);
  const jumps = jumpLinkScore(board, opp);
  const topPressure = topThreatComposite(board, opp);
  const pressure =
    topPressure + total.forks * 90_000 + total.openThree * 9_000 + jumps * 5_500 + diag * 1_200;

  if (total.openFour > 0 || total.forks >= 2) {
    return {
      label: '强制进攻链',
      detail: '对手在构造冲四/双威胁，优先切断其强制手序列。',
      pressure,
      counterFocus: '先封喉再反打',
      disruptTargets: ['冲四落点', '双威胁交汇点', '桥接点'],
    };
  }
  if (jumps >= 8 && total.openThree >= 2) {
    return {
      label: '跳连骨架渗透',
      detail: '对手在利用跳连串联攻击骨架，需优先切断中间桥点。',
      pressure,
      counterFocus: '拆桥断骨',
      disruptTargets: ['跳连中点', '二次连接点', '转折拐点'],
    };
  }
  if (total.openThree >= 3) {
    return {
      label: '双三诱杀布局',
      detail: '对手在铺设多点活三，需破其连接点而非只堵端点。',
      pressure,
      counterFocus: '破连接防复活',
      disruptTargets: ['活三连接位', '潜在四三转换位', '先手续点'],
    };
  }
  if (diag >= 10 && total.openThree >= 1) {
    return {
      label: '斜线潜伏推进',
      detail: '对手沿对角主线蓄势，建议斜向卡位并破其转折点。',
      pressure,
      counterFocus: '斜向卡位',
      disruptTargets: ['对角骨架点', '对角跳连点', '对角终结口'],
    };
  }
  if (center >= 45) {
    return {
      label: '中腹渗透控盘',
      detail: '对手抢中腹效率位，建议切其骨架并夺回转身点。',
      pressure,
      counterFocus: '争中控厚',
      disruptTargets: ['中心中继点', '中腹桥点', '二次扩张位'],
    };
  }
  if (edge >= 12) {
    return {
      label: '边翼扩张转中',
      detail: '对手边路展开后准备转中，需提前占中继点阻断转身。',
      pressure,
      counterFocus: '锁边截流',
      disruptTargets: ['边中转换口', '边路跳点', '反压中线点'],
    };
  }
  return {
    label: '假先手牵制',
    detail: '对手以牵制手试探节奏，避免被带节奏，保持我方主计划。',
    pressure,
    counterFocus: '稳节奏反套',
    disruptTargets: ['牵制假点', '节奏转换点', '主攻起手点'],
  };
}

function guaranteedContinuation(board: number[][], my: number, opp: number): number {
  const oppMoves = collectCandidates(board, opp, 6, 1);
  if (oppMoves.length === 0) return topThreatComposite(board, my);
  let worst = Number.MAX_SAFE_INTEGER;
  for (const mv of oppMoves) {
    if (board[mv.r][mv.c] !== 0) continue;
    board[mv.r][mv.c] = opp;
    const v = topThreatComposite(board, my);
    board[mv.r][mv.c] = 0;
    if (v < worst) worst = v;
  }
  return worst === Number.MAX_SAFE_INTEGER ? 0 : worst;
}

function tacticStageByBoard(board: number[][]): TacticStage {
  const stones = stoneCount(board);
  if (stones <= 14) return 'opening';
  if (stones <= 44) return 'midgame';
  return 'finishing';
}

function phaseTactics(stage: TacticStage): TacticDef[] {
  if (stage === 'opening') return OPENING_TACTICS;
  if (stage === 'midgame') return MIDGAME_TACTICS;
  return FINISH_TACTICS;
}

type TacticSignal = {
  center: number;
  edge: number;
  diag: number;
  jumps: number;
  pressure: number;
  stones: number;
  urgentDefense: number;
  conversionNeed: number;
  attackMode: number;
};

function buildTacticSignal(
  board: number[][],
  opponentPlan: OpponentPlan,
  strategy: Exclude<StrategyId, 'auto'>,
): TacticSignal {
  const stones = stoneCount(board);
  return {
    center: centerMassScore(board, 1) + centerMassScore(board, -1),
    edge: edgeMassScore(board, 1) + edgeMassScore(board, -1),
    diag: diagonalMassScore(board, 1) + diagonalMassScore(board, -1),
    jumps: jumpLinkScore(board, 1) + jumpLinkScore(board, -1),
    pressure: opponentPlan.pressure,
    stones,
    urgentDefense: opponentPlan.label.includes('强制') || opponentPlan.label.includes('双三') ? 1 : 0,
    conversionNeed: stones >= 30 ? 1 : 0,
    attackMode: strategy === 'pressure_attack' || strategy === 'serial_rush' ? 1 : 0,
  };
}

function stageBias(tacticStage: TacticStage, current: TacticStage): number {
  if (tacticStage === current) return 1.0;
  if (
    (tacticStage === 'opening' && current === 'midgame') ||
    (tacticStage === 'midgame' && current === 'finishing')
  ) return 0.78;
  if (
    (tacticStage === 'midgame' && current === 'opening') ||
    (tacticStage === 'finishing' && current === 'midgame')
  ) return 0.72;
  return 0.58;
}

function tacticWeightFromSignal(
  tactic: TacticDef,
  signal: TacticSignal,
  currentStage: TacticStage,
  strategy: Exclude<StrategyId, 'auto'>,
): number {
  let w = stageBias(tactic.stage, currentStage);
  const tags = tactic.tags;

  if (tags.includes('center')) w += Math.min(0.3, signal.center / 120);
  if (tags.includes('edge')) w += Math.min(0.25, signal.edge / 80);
  if (tags.includes('diag')) w += Math.min(0.22, signal.diag / 90);
  if (tags.includes('jump')) w += Math.min(0.26, signal.jumps / 70);
  if (tags.includes('block')) w += signal.urgentDefense ? 0.35 : Math.min(0.2, signal.pressure / 400000);
  if (tags.includes('fork')) w += signal.attackMode ? 0.2 : 0.12;
  if (tags.includes('rush')) w += signal.attackMode ? 0.26 : 0.08;
  if (tags.includes('stabilize')) w += signal.urgentDefense ? 0.18 : 0.1;
  if (tags.includes('convert')) w += signal.conversionNeed ? 0.2 : 0.09;
  if (tags.includes('trap')) w += signal.stones >= 12 && signal.stones <= 42 ? 0.16 : 0.05;

  if (strategy === 'iron_defense' && tags.includes('block')) w += 0.12;
  if (strategy === 'center_control' && tags.includes('center')) w += 0.12;
  if (strategy === 'double_three' && (tags.includes('trap') || tags.includes('fork'))) w += 0.12;
  if (strategy === 'four_three' && (tags.includes('rush') || tags.includes('convert'))) w += 0.12;

  return w;
}

function selectActiveTactics(
  board: number[][],
  stage: TacticStage,
  opponentPlan: OpponentPlan,
  strategy: Exclude<StrategyId, 'auto'>,
): ActiveTacticPlan {
  const signal = buildTacticSignal(board, opponentPlan, strategy);
  const weighted = ALL_TACTICS.map((t) => ({
    t,
    w: tacticWeightFromSignal(t, signal, stage, strategy),
  }));

  weighted.sort((a, b) => b.w - a.w || a.t.id.localeCompare(b.t.id));
  const active = weighted.slice(0, 8).map((x) => x.t);
  const crossPhase = active.filter((t) => t.stage !== stage).length;
  return {
    stage,
    poolSize: ALL_TACTICS.length,
    active,
    weighted: weighted.slice(0, 20).map((x) => ({ id: x.t.id, w: x.w })),
    crossPhase,
  };
}

function tacticBonusForMove(
  mv: Move,
  boardAfterMyMove: number[][],
  myThreat: Threat,
  blockThreat: Threat,
  continuation: number,
  plan: ActiveTacticPlan,
): number {
  const row = mv.r + 1;
  const col = mv.c + 1;
  const nearCenter = Math.abs(row - CENTER) + Math.abs(col - CENTER) <= 3;
  const nearEdge = row <= 3 || row >= BOARD_SIZE - 2 || col <= 3 || col >= BOARD_SIZE - 2;
  const onDiag = Math.abs(mv.r - mv.c) <= 1 || Math.abs(mv.r + mv.c - (BOARD_SIZE - 1)) <= 1;
  const jump = jumpLinkScore(boardAfterMyMove, 1) + jumpLinkScore(boardAfterMyMove, -1);
  const weightMap = new Map<string, number>(plan.weighted.map((x) => [x.id, x.w]));

  let bonus = 0;
  for (const t of plan.active) {
    const w = weightMap.get(t.id) ?? 1;
    if (t.tags.includes('center') && nearCenter) bonus += 18_000 * w;
    if (t.tags.includes('edge') && nearEdge) bonus += 12_000 * w;
    if (t.tags.includes('diag') && onDiag) bonus += 13_000 * w;
    if (t.tags.includes('jump') && jump >= 6) bonus += 10_000 * w;
    if (t.tags.includes('fork') && (myThreat.forks > 0 || myThreat.openThree + myThreat.brokenThree >= 2)) bonus += 32_000 * w;
    if (t.tags.includes('block') && (blockThreat.openFour > 0 || blockThreat.openThree + blockThreat.brokenThree >= 2)) bonus += 26_000 * w;
    if (t.tags.includes('rush') && (myThreat.openFour > 0 || myThreat.closedFour + myThreat.brokenFour > 0)) bonus += 22_000 * w;
    if (t.tags.includes('stabilize') && continuation >= 130_000) bonus += 15_000 * w;
    if (t.tags.includes('convert') && continuation >= 150_000) bonus += 18_000 * w;
    if (t.tags.includes('trap') && (myThreat.forks > 0 || myThreat.openThree >= 1)) bonus += 20_000 * w;
  }
  return Math.round(bonus);
}

function immediateWinningPoints(board: number[][], side: number): Move[] {
  const out: Move[] = [];
  for (let r = 0; r < BOARD_SIZE; r++) {
    for (let c = 0; c < BOARD_SIZE; c++) {
      if (board[r][c] !== 0) continue;
      board[r][c] = side;
      const win = isWinByPlaced(board, r, c, side);
      board[r][c] = 0;
      if (win) out.push({ r, c });
    }
  }
  return out;
}

function immediateWinningPointsScoped(board: number[][], side: number, probeLimit = 18): Move[] {
  const out: Move[] = [];
  const cands = uniqMoves([
    ...collectCandidates(board, side, probeLimit, 1),
    ...collectCandidates(board, -side, Math.max(6, Math.floor(probeLimit * 0.6)), 1),
  ]);
  for (const mv of cands) {
    if (board[mv.r][mv.c] !== 0) continue;
    board[mv.r][mv.c] = side;
    const win = isWinByPlaced(board, mv.r, mv.c, side);
    board[mv.r][mv.c] = 0;
    if (win) out.push(mv);
  }
  return out;
}

function setupKillMoves(board: number[][], side: number, probeLimit = 14): Move[] {
  const out: Move[] = [];
  const cands = uniqMoves([
    ...immediateWinningPointsScoped(board, side, probeLimit + 4),
    ...collectCandidates(board, side, probeLimit, 1),
  ]);

  for (const mv of cands) {
    if (board[mv.r][mv.c] !== 0) continue;
    board[mv.r][mv.c] = side;
    const directWin = isWinByPlaced(board, mv.r, mv.c, side);
    const followWins = directWin ? [{ r: mv.r, c: mv.c }] : immediateWinningPointsScoped(board, side, probeLimit);
    board[mv.r][mv.c] = 0;
    if (directWin || followWins.length >= 2) out.push(mv);
  }
  return out;
}

function estimateWinSteps(board: number[][], side: number): number | null {
  if (immediateWinningPointsScoped(board, side, 20).length > 0) return 1;
  if (setupKillMoves(board, side, 12).length > 0) return 2;

  const firstMoves = uniqMoves([
    ...collectCandidates(board, side, 10, 1),
    ...setupKillMoves(board, side, 8),
  ]);

  for (const mv of firstMoves) {
    if (board[mv.r][mv.c] !== 0) continue;
    board[mv.r][mv.c] = side;
    if (isWinByPlaced(board, mv.r, mv.c, side)) {
      board[mv.r][mv.c] = 0;
      return 1;
    }

    const opp = -side;
    const replies = uniqMoves([
      ...immediateWinningPoints(board, opp),
      ...collectCandidates(board, opp, 8, 1),
    ]);
    const defensiveReplies = replies.length > 0 ? replies : collectCandidates(board, opp, 4, 1);

    let allCovered = true;
    for (const rep of defensiveReplies) {
      if (board[rep.r][rep.c] !== 0) continue;
      board[rep.r][rep.c] = opp;
      const canContinueKill = immediateWinningPointsScoped(board, side, 12).length > 0 || setupKillMoves(board, side, 8).length > 0;
      board[rep.r][rep.c] = 0;
      if (!canContinueKill) {
        allCovered = false;
        break;
      }
    }
    board[mv.r][mv.c] = 0;
    if (allCovered) return 3;
  }

  return null;
}

function uniqMoves(moves: Move[]): Move[] {
  const seen = new Set<string>();
  const out: Move[] = [];
  for (const m of moves) {
    const k = `${m.r},${m.c}`;
    if (seen.has(k)) continue;
    seen.add(k);
    out.push(m);
  }
  return out;
}

function bestOpponentReply(board: number[][], opp: number): { move: Move | null; threat: number } {
  const cands = collectCandidates(board, opp, 8, 1);
  let best: Move | null = null;
  let bestThreat = -1;
  for (const mv of cands) {
    if (board[mv.r][mv.c] !== 0) continue;
    const t = threatScore(analyzeThreatAt(board, mv.r, mv.c, opp));
    if (t > bestThreat) {
      bestThreat = t;
      best = mv;
    }
  }
  return { move: best, threat: Math.max(0, bestThreat) };
}

function winRateFromEval(evalScore: number): number {
  const x = Math.max(-2_000_000, Math.min(2_000_000, evalScore));
  const p = 1 / (1 + Math.exp(-x / 220000));
  return Math.max(0.01, Math.min(0.99, p));
}

function stonesOnBoard(board: number[][]): number {
  return stoneCount(board);
}

function isSideTurnByMatrix(board: number[][], side: 1 | -1): boolean {
  let black = 0;
  let white = 0;
  for (const row of board) {
    for (const v of row) {
      if (v === 1) black += 1;
      else if (v === -1) white += 1;
    }
  }
  return side === 1 ? black === white : black === white + 1;
}

function pseudoJitter(seed: number, row: number, col: number, stones: number): number {
  const x = Math.sin(seed * 0.00137 + row * 12.9898 + col * 78.233 + stones * 0.618);
  return (x - Math.floor(x)) - 0.5;
}

function openingVariationBonus(row: number, col: number, stones: number, variationSeed: number): number {
  if (stones > 5) return 0;
  const idx = Math.abs((variationSeed + stones * 13) % OPENING_VARIATION_ANCHORS.length);
  const [ar, ac] = OPENING_VARIATION_ANCHORS[idx];
  const d = Math.abs(row - ar) + Math.abs(col - ac);
  const main = Math.max(0, 140_000 - d * 22_000);
  const [br, bc] = OPENING_VARIATION_ANCHORS[(idx + 7) % OPENING_VARIATION_ANCHORS.length];
  const d2 = Math.abs(row - br) + Math.abs(col - bc);
  const secondary = Math.max(0, 70_000 - d2 * 16_000);
  return main + secondary;
}

function decidePlan(
  board: number[][],
  my: number,
  opp: number,
  opponentPlan: OpponentPlan,
  myWinSteps: number | null,
  oppWinSteps: number | null,
): DecisionPlan {
  const myEval = boardEval(board, my);
  const winRate = winRateFromEval(myEval);
  const threatPts = immediateWinningPoints(board, opp);
  const p = opponentPlan.pressure;

  const threatLevel: DecisionPlan['threatLevel'] =
    threatPts.length >= 2 || p >= 420000 ? '致命' : threatPts.length === 1 || p >= 250000 ? '高' : p >= 120000 ? '中' : '低';

  let mode: DecisionPlan['mode'] = '先布局';
  let summary = '我方局面可控，优先推进主布局并维持先手。';

  if (myWinSteps === 1) {
    mode = '先布局';
    summary = '我方一步可赢，直接执行终结手，不再处理次级威胁。';
  } else if (oppWinSteps === 1) {
    mode = '先反制';
    summary = '对手一步可赢，必须先堵并拆其后续杀链。';
  } else if (oppWinSteps !== null && (myWinSteps === null || oppWinSteps <= myWinSteps)) {
    mode = '堵点后反制';
    summary = `对手胜势更快（${oppWinSteps}步以内），优先卡其关键手再夺主动。`;
  } else if (myWinSteps !== null && (oppWinSteps === null || myWinSteps < oppWinSteps)) {
    mode = '先布局';
    summary = `我方胜势更快（${myWinSteps}步以内），应主动推进并维持压迫。`;
  } else if (threatPts.length > 0) {
    if (winRate < 0.55) {
      mode = '先反制';
      summary = '对手存在可赢点，先封堵并拆其后续强制链。';
    } else {
      mode = '堵点后继续主策略';
      summary = '先堵对手可赢点，再快速回到主攻布局压制。';
    }
  } else if (threatLevel === '高' || threatLevel === '致命') {
    mode = '堵点后反制';
    summary = '对手威胁较强，先卡其关键转换位，再反制抢回主动。';
  } else if (winRate < 0.42) {
    mode = '先反制';
    summary = '胜率偏低，先稳局反制，避免被对手滚雪球。';
  } else {
    mode = '先布局';
    summary = '当前可按主计划推进，兼顾压制与后续绝杀准备。';
  }

  const stage = tacticStageByBoard(board);
  const stageGoal =
    stage === 'opening'
      ? '开局目标：控域与效率优先，避免无意义外扩。'
      : stage === 'midgame'
        ? '中盘目标：争取先手链，同时拆解对手骨架连接。'
        : '终盘目标：将优势转化为强制终结，减少对手反扑窗口。';
  const priorityLine = '优先级总线：对手可赢点 > 对手强制链 > 我方可转化进攻 > 纯布局';

  return { mode, winRate, threatLevel, myWinSteps, oppWinSteps, immediateThreatPoints: threatPts, summary, priorityLine, stageGoal };
}

function classifyBlockAction(
  hasImmediateThreat: boolean,
  blocksImmediateWin: boolean,
  oppWinAfter: number,
  disruptionGain: number,
  continuation: number,
): BlockActionClass {
  if (!hasImmediateThreat || !blocksImmediateWin) return hasImmediateThreat ? '只堵' : '非堵点';
  if (oppWinAfter > 0 || disruptionGain >= 90_000) return '堵后反制';
  if (continuation >= 150_000) return '堵后回主线';
  return '只堵';
}

function stageGoalBonus(
  stage: TacticStage,
  continuation: number,
  disruptionGain: number,
  tactical: number,
  defensive: number,
): number {
  if (stage === 'opening') return continuation * 0.1 + defensive * 0.04;
  if (stage === 'midgame') return disruptionGain * 0.15 + tactical * 0.05;
  return tactical * 0.09 + continuation * 0.08;
}

function priorityBusAdjust(
  decision: DecisionPlan,
  meDirectWin: boolean,
  hasImmediateThreat: boolean,
  blocksImmediateWin: boolean,
  blockClass: BlockActionClass,
  convertibleRush: boolean,
): number {
  // 固定优先级：可赢点 > 强制链 > 可转化进攻 > 纯布局
  if (meDirectWin) return 2_800_000_000;
  if (hasImmediateThreat && !blocksImmediateWin) return -2_100_000_000;
  if (hasImmediateThreat && blocksImmediateWin) {
    if (blockClass === '堵后反制') return 320_000;
    if (blockClass === '堵后回主线') return 260_000;
    return 150_000; // 只堵
  }
  if (decision.mode === '先反制' || decision.mode === '堵点后反制') return 120_000;
  if (convertibleRush) return 110_000;
  return 40_000;
}

function shouldAbort(ctx: SearchCtx): boolean {
  if (ctx.timedOut) return true;
  const elapsed = Date.now() - ctx.startMs;
  if (elapsed >= ctx.timeBudgetMs || ctx.nodes >= ctx.nodeBudget) {
    ctx.timedOut = true;
    return true;
  }
  return false;
}

function depthMoveLimit(depth: number, widthFactor: number): number {
  if (depth >= 9) return Math.max(2, Math.round(2 * widthFactor));
  if (depth >= 8) return Math.max(2, Math.round(2 * widthFactor));
  if (depth >= 7) return Math.max(3, Math.round(4 * widthFactor));
  if (depth >= 6) return Math.max(4, Math.round(5 * widthFactor));
  if (depth >= 5) return Math.max(5, Math.round(6 * widthFactor));
  if (depth >= 4) return Math.max(6, Math.round(7 * widthFactor));
  if (depth >= 3) return Math.max(8, Math.round(9 * widthFactor));
  return Math.max(10, Math.round(11 * widthFactor));
}

function negamax(
  board: number[][],
  hash: bigint,
  sideToMove: number,
  my: number,
  depth: number,
  alphaIn: number,
  betaIn: number,
  ctx: SearchCtx,
  lastMove?: Move,
): number {
  ctx.nodes += 1;
  if (shouldAbort(ctx)) return boardEval(board, my);

  if (lastMove && isWinByPlaced(board, lastMove.r, lastMove.c, -sideToMove)) {
    return -1_100_000_000 - depth * 1000;
  }

  if (depth <= 0) return boardEval(board, my);

  const key = hashKey(hash, sideToMove);
  const entry = ctx.tt.get(key);
  let alpha = alphaIn;
  let beta = betaIn;

  if (entry && entry.depth >= depth) {
    if (entry.flag === 'exact') return entry.score;
    if (entry.flag === 'lower') alpha = Math.max(alpha, entry.score);
    if (entry.flag === 'upper') beta = Math.min(beta, entry.score);
    if (alpha >= beta) return entry.score;
  }

  let moves = collectCandidates(board, sideToMove, depthMoveLimit(depth, ctx.widthFactor), ctx.widthFactor);

  if (entry?.best) {
    moves = [entry.best, ...moves.filter((m) => !(m.r === entry.best!.r && m.c === entry.best!.c))];
  }

  if (moves.length === 0) return boardEval(board, my);

  let best = -Number.MAX_SAFE_INTEGER;
  let bestMove: Move | undefined;

  for (const mv of moves) {
    if (board[mv.r][mv.c] !== 0) continue;
    board[mv.r][mv.c] = sideToMove;

    const idx = moveIndex(mv.r, mv.c);
    const nextHash = hash ^ ZOBRIST[idx][sideToMove === 1 ? 0 : 1];

    let score: number;
    if (isWinByPlaced(board, mv.r, mv.c, sideToMove)) {
      score = 1_100_000_000 + depth * 1000;
    } else {
      score = -negamax(board, nextHash, -sideToMove, my, depth - 1, -beta, -alpha, ctx, mv);
    }

    board[mv.r][mv.c] = 0;

    if (score > best) {
      best = score;
      bestMove = mv;
    }

    if (score > alpha) alpha = score;
    if (alpha >= beta || shouldAbort(ctx)) break;
  }

  const flag: TTFlag = best <= alphaIn ? 'upper' : best >= betaIn ? 'lower' : 'exact';
  ctx.tt.set(key, { depth, score: best, flag, best: bestMove });

  return best;
}

function chooseAutoStrategy(board: number[][], mySide: Side): Exclude<StrategyId, 'auto'> {
  const my = mySide === '#' ? 1 : -1;
  const opp = -my;
  const myTop = topThreatComposite(board, my);
  const oppTop = topThreatComposite(board, opp);
  const oppForcing = forcingStartMoves(board, opp, 8).length;
  const stones = stoneCount(board);

  if (oppForcing >= 3 || oppTop >= 300_000) return 'pro_forcing';
  if (oppTop >= 240_000) return 'iron_defense';
  if (stones <= 8) return 'center_control';
  if (myTop >= 180_000 && myTop > oppTop * 1.18) return 'pressure_attack';
  if (myTop >= 145_000 && oppTop >= 120_000) return 'four_three';
  if (myTop >= 130_000 && oppTop < 100_000) return 'serial_rush';
  return 'balance';
}

function resolveProfile(board: number[][], mySide: Side, selected: StrategyId): {
  strategy: Exclude<StrategyId, 'auto'>;
  profile: StrategyProfile;
} {
  const strategy = selected === 'auto' ? chooseAutoStrategy(board, mySide) : selected;
  return { strategy, profile: PROFILE_MAP[strategy] };
}

function chooseDepth(board: number[][], profile: StrategyProfile): number {
  const stones = stoneCount(board);
  let d = MIN_LOOKAHEAD_DEPTH;
  if (stones <= 10) d = 8;
  else if (stones <= 28) d = 9;
  else d = 8;
  if (profile.lookahead >= 1.1 && stones >= 16) d += 1;
  return Math.max(MIN_LOOKAHEAD_DEPTH, Math.min(10, d));
}

function reasonFromThreat(my: Threat, block: Threat, strategy: Exclude<StrategyId, 'auto'>): { reason: string; style: string } {
  if (my.five > 0) return { reason: '此手直接连五，立即终结。', style: '必胜杀招' };
  if (block.five > 0) return { reason: '先封堵对手成五点，避免被一击致命。', style: '硬性防守' };
  if (my.openFour > 0 || my.brokenFour > 0) return { reason: '制造冲四/活四威胁，迫使对手被动单防。', style: '强迫手筋' };
  if (my.forks > 0 || my.openThree + my.brokenThree >= 2) return { reason: '构造双向威胁（双三/四三），形成一防难尽。', style: '陷阱布局' };
  if (block.openFour > 0 || block.closedFour > 0 || block.brokenFour > 0) return { reason: '压缩对手成势线路，同时保留己方反击弹性。', style: '反制控局' };

  switch (strategy) {
    case 'pressure_attack':
      return { reason: '抢先手扩张火力线，持续给对手出难题。', style: '压迫进攻' };
    case 'iron_defense':
      return { reason: '先稳住要害，再找最省手反击点。', style: '稳健反击' };
    case 'pro_forcing':
      return { reason: '优先压掉对手强制线交集，再转入高胜率反击。', style: '职业威胁链' };
    case 'rapfi_external':
      return { reason: '当前策略优先参考外部职业引擎建议，内置评估作兜底。', style: '外部引擎' };
    case 'double_three':
      return { reason: '优先铺设双三骨架，等待对手防型失衡。', style: '双三诱杀' };
    case 'four_three':
      return { reason: '围绕四三结构走棋，追求强制应手转换。', style: '四三做杀' };
    case 'serial_rush':
      return { reason: '保持冲四节奏，连手压迫对手防线。', style: '连环冲四' };
    case 'center_control':
      return { reason: '占中腹效率位，兼顾扩张与转身。', style: '中腹运营' };
    case 'balance':
    default:
      return { reason: '攻守平衡推进，保持手段完整性。', style: '均衡控局' };
  }
}

function computeSuggestions(
  boardInput: number[][],
  mySide: Side,
  selected: StrategyId,
  variationSeed: number,
  myTurn: boolean,
): EngineResult {
  const board = boardInput.map((row) => row.slice());
  const my = mySide === '#' ? 1 : -1;
  const opp = -my;
  const stones = stonesOnBoard(board);

  const { strategy, profile } = resolveProfile(board, mySide, selected);
  const meImmediateWins = immediateWinningPoints(board, my);
  const oppImmediateWins = immediateWinningPoints(board, opp);
  const meSetupKills = setupKillMoves(board, my, 12);
  const oppSetupKills = setupKillMoves(board, opp, 12);
  const rootCandidates = uniqMoves([
    ...oppImmediateWins,
    ...oppSetupKills,
    ...meImmediateWins,
    ...meSetupKills,
    ...collectCandidates(board, my, 14, profile.width),
  ]);
  const targetDepth = myTurn ? chooseDepth(board, profile) : Math.min(OFFTURN_MAX_DEPTH, chooseDepth(board, profile));
  const opponentPlan = detectOpponentPlan(board, opp);
  const tacticPlan = selectActiveTactics(board, tacticStageByBoard(board), opponentPlan, strategy);
  const myWinStepsNow = estimateWinSteps(board, my);
  const oppWinStepsNow = estimateWinSteps(board, opp);
  const decision = decidePlan(board, my, opp, opponentPlan, myWinStepsNow, oppWinStepsNow);
  const baselineOppPressure = opponentPlan.pressure;
  const oppForcingStartsNow = forcingStartMoves(board, opp, 10);
  const forcingDefenseIntersectionNow = defenseIntersectionAgainstForcingStarts(board, opp, 3);

  const ctx: SearchCtx = {
    startMs: Date.now(),
    timeBudgetMs: myTurn
      ? Math.round(SEARCH_TIME_BUDGET_MS * profile.lookahead)
      : Math.max(220, Math.round(OFFTURN_TIME_BUDGET_MS * profile.lookahead)),
    nodeBudget: myTurn
      ? Math.round(SEARCH_NODE_BUDGET * profile.lookahead)
      : Math.max(16000, Math.round(OFFTURN_NODE_BUDGET * profile.lookahead)),
    nodes: 0,
    timedOut: false,
    tt: new Map<string, TTEntry>(),
    widthFactor: profile.width,
  };

  const hash = hashOfBoard(board);
  const scored: Suggestion[] = [];

  for (const mv of rootCandidates) {
    if (board[mv.r][mv.c] !== 0) continue;

    const myThreat = analyzeThreatAt(board, mv.r, mv.c, my);
    const blockThreat = analyzeThreatAt(board, mv.r, mv.c, opp);
    const meDirectWin = myThreat.five > 0;
    const mustBlock = blockThreat.five > 0;
    const blocksOppSetupNow = oppSetupKills.some((p) => p.r === mv.r && p.c === mv.c);
    const createsMySetupNow = meSetupKills.some((p) => p.r === mv.r && p.c === mv.c);

    board[mv.r][mv.c] = my;
    const idx = moveIndex(mv.r, mv.c);
    const nextHash = hash ^ ZOBRIST[idx][my === 1 ? 0 : 1];

    let lookaheadScore = 0;
    if (meDirectWin) {
      lookaheadScore = 1_200_000_000;
    } else {
      lookaheadScore = -negamax(
        board,
        nextHash,
        opp,
        my,
        Math.max(1, targetDepth - 1),
        -Number.MAX_SAFE_INTEGER,
        Number.MAX_SAFE_INTEGER,
        ctx,
        mv,
      );
    }

    const shape = directionalPotential(board, mv.r, mv.c, my);
    const afterOppPressure = topThreatComposite(board, opp);
    const disruptionGain = Math.max(0, baselineOppPressure - afterOppPressure);
    const continuation = guaranteedContinuation(board, my, opp);
    const oppReply = bestOpponentReply(board, opp);
    const tacticBonus = tacticBonusForMove(mv, board, myThreat, blockThreat, continuation, tacticPlan);
    const myForcingSeverityAfter = meDirectWin ? 3 : bestForcingSeverity(board, my);
    const oppForcingSeverityAfter = bestForcingSeverity(board, opp);
    const survivingOppForcing = survivingForcingStarts(board, opp, oppForcingStartsNow);
    const blocksImmediateWin = decision.immediateThreatPoints.some((p) => p.r === mv.r && p.c === mv.c);
    const inForcingDefenseIntersection = forcingDefenseIntersectionNow.some((p) => p.r === mv.r && p.c === mv.c);
    const oppWinAfter = immediateWinningPointsScoped(board, opp, 14).length;
    const meWinAfter = meDirectWin ? 1 : immediateWinningPointsScoped(board, my, 14).length;
    const oppSetupAfter = oppSetupKills.length > 0 || afterOppPressure >= 170_000 ? setupKillMoves(board, opp, 8).length : 0;
    const myFastAfter = meDirectWin ? 1 : meWinAfter > 0 ? 2 : createsMySetupNow ? 3 : null;
    const oppFastAfter = oppWinAfter > 0 ? 1 : oppSetupAfter > 0 ? 2 : null;
    board[mv.r][mv.c] = 0;

    const tactical = threatScore(myThreat) * profile.attack;
    const defensive = threatScore(blockThreat) * 0.92 * profile.defense;
    const trap = (myThreat.forks * 150_000 + (myThreat.openThree + myThreat.brokenThree) * 11_000) * profile.trap;
    const opening = openingBonus(mv.r + 1, mv.c + 1) * 240 * profile.opening;
    const planning = Math.max(0, lookaheadScore) * 0.78 * profile.lookahead;
    const riskPenalty = Math.max(0, -lookaheadScore) * 0.45 * profile.risk;
    const antiLayout = disruptionGain * 0.72 * profile.defense;
    const minorThreatIntercept =
      decision.threatLevel === '低' || decision.threatLevel === '中'
        ? (blockThreat.openThree + blockThreat.brokenThree) * 22_000 + (blockThreat.openTwo > 0 ? 12_000 : 0)
        : 0;
    const stageBonus = stageGoalBonus(tacticPlan.stage, continuation, disruptionGain, tactical, defensive);
    const shallowRush =
      myThreat.openFour === 0 &&
      myThreat.forks === 0 &&
      myThreat.openThree <= 1 &&
      (myThreat.closedFour > 0 || myThreat.brokenFour > 0);
    const weakContinuation = continuation < 120_000;
    const convertibleRush = continuation >= 155_000 || myThreat.openThree + myThreat.brokenThree >= 2;
    const blockClass = classifyBlockAction(
      decision.immediateThreatPoints.length > 0,
      blocksImmediateWin,
      oppWinAfter,
      disruptionGain,
      continuation,
    );
    const shallowPenalty = shallowRush ? afterOppPressure * (weakContinuation ? 0.48 : 0.18) : 0;
    const rushGatePenalty = shallowRush && !meDirectWin && !convertibleRush ? 75_000 + afterOppPressure * 0.14 : 0;
    const mustBlockPenalty =
      decision.immediateThreatPoints.length > 0 && !blocksImmediateWin ? 900_000 + decision.immediateThreatPoints.length * 220_000 : 0;
    const postBlockRiskPenalty =
      decision.immediateThreatPoints.length > 0 && blocksImmediateWin && oppWinAfter > 0 ? 500_000 + oppWinAfter * 180_000 : 0;
    const setupBlockPenalty =
      oppSetupKills.length > 0 && !blocksOppSetupNow && !meDirectWin
        ? 300_000 + oppSetupKills.length * 120_000
        : 0;
    const postSetupRiskPenalty =
      oppSetupAfter > 0 && !meDirectWin && meWinAfter < 2 ? 420_000 + oppSetupAfter * 160_000 : 0;
    const setupInitiativeBonus = createsMySetupNow ? 260_000 : 0;
    const proactiveThreatBonus = meWinAfter >= 2 ? 180_000 : 0;
    const forcingSurvivalPenalty = survivingOppForcing > 0 ? survivingOppForcing * 150_000 : 0;
    const forcingIntersectionPenalty =
      forcingDefenseIntersectionNow.length > 0 && !inForcingDefenseIntersection && !meDirectWin
        ? 320_000 + forcingDefenseIntersectionNow.length * 80_000
        : 0;
    const forcingIntersectionBonus =
      forcingDefenseIntersectionNow.length > 0 && inForcingDefenseIntersection ? 180_000 : 0;
    const forcingRaceAdjust =
      myForcingSeverityAfter > oppForcingSeverityAfter
        ? 220_000 + (myForcingSeverityAfter - oppForcingSeverityAfter) * 80_000
        : myForcingSeverityAfter < oppForcingSeverityAfter && !meDirectWin
          ? -360_000 - (oppForcingSeverityAfter - myForcingSeverityAfter) * 140_000
          : 0;
    const blindRushPenalty =
      myForcingSeverityAfter < 2 && oppForcingSeverityAfter >= 2 && !meDirectWin
        ? 260_000 + afterOppPressure * 0.12
        : 0;
    const tempoPriorityAdjust =
      myFastAfter === 1
        ? 800_000
        : oppFastAfter === 1
          ? -780_000
          : oppFastAfter !== null && (myFastAfter === null || oppFastAfter <= myFastAfter)
            ? -260_000
            : myFastAfter !== null && (oppFastAfter === null || myFastAfter < oppFastAfter)
              ? 180_000
              : 0;
    const replyPenalty = oppReply.threat > 0 ? oppReply.threat * 0.18 : 0;
    const decisionBonus =
      decision.mode === '先布局'
        ? tactical * 0.08 + continuation * 0.12
        : decision.mode === '先反制'
          ? defensive * 0.14 + antiLayout * 0.12
          : decision.mode === '堵点后反制'
            ? defensive * 0.12 + disruptionGain * 0.2
            : defensive * 0.1 + tactical * 0.06;
    const priorityAdjust = priorityBusAdjust(
      decision,
      meDirectWin,
      decision.immediateThreatPoints.length > 0,
      blocksImmediateWin,
      blockClass,
      convertibleRush,
    );

    let score =
      tactical +
      defensive +
      trap +
      opening +
      shape * 120 +
      planning +
      antiLayout +
      tacticBonus -
      riskPenalty -
      shallowPenalty -
      rushGatePenalty -
      mustBlockPenalty -
      postBlockRiskPenalty -
      setupBlockPenalty -
      postSetupRiskPenalty +
      -replyPenalty +
      setupInitiativeBonus +
      proactiveThreatBonus +
      forcingIntersectionBonus +
      forcingRaceAdjust -
      forcingSurvivalPenalty -
      forcingIntersectionPenalty -
      blindRushPenalty +
      tempoPriorityAdjust +
      decisionBonus +
      stageBonus +
      priorityAdjust;
    // 开局多变体：在合理范围内引入可控差异，避免每局同套路。
    score += openingVariationBonus(mv.r + 1, mv.c + 1, stones, variationSeed);
    // 小威胁不等于不管：对潜在布局威胁做前置拦截加分。
    score += minorThreatIntercept;
    // 轻微抖动打破等分僵局，提升对弈多样性（不影响核心优先级）。
    score += Math.round(pseudoJitter(variationSeed, mv.r + 1, mv.c + 1, stones) * 8000);
    if (meDirectWin) score += 2_000_000_000;
    if (mustBlock) score += 1_500_000_000;

    const info = reasonFromThreat(myThreat, blockThreat, strategy);
    scored.push({
      row: mv.r + 1,
      col: mv.c + 1,
      score: Math.round(score),
      reason: shallowRush
        ? weakContinuation
          ? `${info.reason}（该冲势后续链条偏弱，已降权避免被轻易化解）`
          : convertibleRush
            ? `${info.reason}（该冲势具备后续衔接，可转入强制手序）`
            : `${info.reason}（该冲势尚未形成可靠续手，优先保布局压制）`
        : decision.immediateThreatPoints.length > 0 && !blocksImmediateWin
          ? `${info.reason}（该点未覆盖对手可赢点，已降权）`
          : oppSetupKills.length > 0 && !blocksOppSetupNow
            ? `${info.reason}（该点未覆盖对手二/三步杀链入口，已降权）`
            : forcingDefenseIntersectionNow.length > 0 && !inForcingDefenseIntersection && !meDirectWin
              ? `${info.reason}（该点不在强制线交集防点中，已降权）`
            : myForcingSeverityAfter < oppForcingSeverityAfter && !meDirectWin
              ? `${info.reason}（对手强制威胁级别更高，先抢反制主动）`
            : decision.immediateThreatPoints.length > 0 && blocksImmediateWin && oppWinAfter > 0
              ? `${info.reason}（虽堵点但对手仍有后续赢点，继续反制）`
            : oppSetupAfter > 0
              ? `${info.reason}（此手后对手仍保留二/三步杀链，需继续压制）`
            : blockClass === '堵后反制'
              ? `${info.reason}（该手属于“堵后反制”，可直接抢回主动）`
              : blockClass === '堵后回主线'
                ? `${info.reason}（该手属于“堵后回主线”，封堵后保持我方主计划）`
                : info.reason,
      style:
        decision.immediateThreatPoints.length > 0 && blocksImmediateWin
          ? `${blockClass} / ${info.style}`
          : disruptionGain > 80_000
            ? `破局反制 / ${info.style}`
            : info.style,
      predictedReply: oppReply.move ? `${oppReply.move.r + 1},${oppReply.move.c + 1}` : undefined,
    });

    if (shouldAbort(ctx)) break;
  }

  scored.sort((a, b) => b.score - a.score);

  const stats: SearchStats = {
    depth: targetDepth,
    nodes: ctx.nodes,
    ms: Date.now() - ctx.startMs,
    timedOut: ctx.timedOut,
  };

  return {
    suggestions: scored.slice(0, 3),
    strategy,
    stats,
    opponentPlan,
    tacticPlan,
    decision,
  };
}

function defaultEngineResult(): EngineResult {
  return {
    suggestions: [],
    strategy: 'balance',
    stats: { depth: 0, nodes: 0, ms: 0, timedOut: false },
    opponentPlan: { label: '未知', detail: '未识别', pressure: 0, counterFocus: '未知', disruptTargets: [] },
    tacticPlan: { stage: 'opening', poolSize: 0, active: [], weighted: [], crossPhase: 0 },
    decision: {
      mode: '先布局',
      winRate: 0.5,
      threatLevel: '低',
      myWinSteps: null,
      oppWinSteps: null,
      immediateThreatPoints: [],
      summary: '未识别到局面，默认均衡布局。',
      priorityLine: '优先级总线：对手可赢点 > 对手强制链 > 我方可转化进攻 > 纯布局',
      stageGoal: '开局目标：控域与效率优先，避免无意义外扩。',
    },
  };
}

function clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n));
}

function signatureOfMatrix(board: number[][]): string {
  return board.map((row) => row.map((v) => (v === 1 ? '1' : v === -1 ? '2' : '0')).join('')).join('|');
}

function cloneMatrix(board: number[][]): number[][] {
  return board.map((row) => row.slice());
}

function matrixWithMove(board: number[][], row: number, col: number, side: 1 | -1): number[][] | null {
  const r = row - 1;
  const c = col - 1;
  if (r < 0 || r >= BOARD_SIZE || c < 0 || c >= BOARD_SIZE) return null;
  if (board[r]?.[c] !== 0) return null;
  const next = cloneMatrix(board);
  next[r][c] = side;
  return next;
}

function rememberLimited<K, V>(cache: Map<K, V>, key: K, value: V, limit: number): void {
  cache.set(key, value);
  while (cache.size > limit) {
    const first = cache.keys().next();
    if (first.done) break;
    cache.delete(first.value);
  }
}

export default function GomokuPanel({ disabled, nickname, boardText, onPick }: Props) {
  const [strategy, setStrategy] = useState<StrategyId>('rapfi_external');
  const [variationSeed, setVariationSeed] = useState<number>(() => Math.floor(Math.random() * 1_000_000));
  const [rapfiSuggestion, setRapfiSuggestion] = useState<{ row: number; col: number; ms: number; error?: string } | null>(null);
  const [rapfiPending, setRapfiPending] = useState<boolean>(false);
  const [rapfiPonderPending, setRapfiPonderPending] = useState<boolean>(false);
  const [optimisticPick, setOptimisticPick] = useState<{ row: number; col: number; side: Side; token: number } | null>(null);
  const rapfiReqSeqRef = useRef<number>(0);
  const rapfiPonderSeqRef = useRef<number>(0);
  const rapfiLastKeyRef = useRef<string>('');
  const rapfiOkKeyRef = useRef<string>('');
  const rapfiInFlightKeyRef = useRef<string>('');
  const rapfiFailCooldownRef = useRef<{ key: string; until: number }>({ key: '', until: 0 });
  const rapfiWantedKeyRef = useRef<string>('');
  const rapfiPonderKeyRef = useRef<string>('');
  const rapfiPonderDoneKeyRef = useRef<string>('');
  const rapfiPonderCacheRef = useRef<Map<string, { row: number; col: number; ms: number }>>(new Map());
  const optimisticTimerRef = useRef<number | null>(null);
  const prevStoneCountRef = useRef<number>(0);
  const analysisCacheRef = useRef<Map<string, EngineResult>>(new Map());
  const [analysisSignature, setAnalysisSignature] = useState<string>('');
  const [analysisMatrix, setAnalysisMatrix] = useState<number[][]>([]);
  const [analysisMyTurn, setAnalysisMyTurn] = useState<boolean>(false);
  const [analysisPending, setAnalysisPending] = useState<boolean>(false);

  const cells = useMemo(() => parseBoard(boardText), [boardText]);
  const turnInfo = useMemo(() => parseTurnInfo(boardText), [boardText]);
  const seats = useMemo(() => parseSeats(boardText), [boardText]);
  const matrix = useMemo(() => toMatrix(cells), [cells]);
  const boardSignature = useMemo(() => signatureOfMatrix(matrix), [matrix]);
  const stoneN = useMemo(() => stonesOnBoard(matrix), [matrix]);
  const boardWinner = useMemo(() => winnerOnBoard(matrix), [matrix]);

  const mySide: Side | null = nickname === seats.blackName ? '#' : nickname === seats.whiteName ? 'o' : null;

  useEffect(() => {
    const prev = prevStoneCountRef.current;
    // 新局检测：从中后盘回到接近空盘时切换开局变体，避免每局同套路。
    if (stoneN <= 1 && prev >= 6) {
      setVariationSeed((Math.floor(Math.random() * 1_000_000) + Date.now()) % 1_000_000);
    }
    prevStoneCountRef.current = stoneN;
  }, [stoneN]);

  const myTurn = !!turnInfo.name && turnInfo.name === nickname;
  const canPick = !disabled && boardWinner === null;
  const isHiddenMaster = nickname === 'zouyu';
  const hwThreads =
    typeof navigator !== 'undefined' && Number.isFinite(navigator.hardwareConcurrency)
      ? Math.max(2, Math.floor(navigator.hardwareConcurrency))
      : 4;
  const rapfiTimeoutMs = useMemo(() => {
    let base = 8500;
    if (hwThreads <= 4) base = 6500;
    else if (hwThreads <= 6) base = 9000;
    else if (hwThreads <= 8) base = 13000;
    else if (hwThreads <= 12) base = 18000;
    else base = 22000;

    if (stoneN <= 10) base = Math.round(base * 1.55);
    else if (stoneN <= 30) base = Math.round(base * 1.35);
    else base = Math.round(base * 1.75);
    if (!myTurn) base = Math.round(base * 0.9);

    return clamp(base, 6000, 38000);
  }, [hwThreads, stoneN, myTurn]);
  const rapfiGuardTimeoutMs = useMemo(() => {
    return clamp(rapfiTimeoutMs + 8000, 15000, 52000);
  }, [rapfiTimeoutMs]);

  useEffect(() => {
    if (!optimisticPick) return;
    if (matrix[optimisticPick.row - 1]?.[optimisticPick.col - 1] !== 0) {
      if (optimisticTimerRef.current !== null) {
        window.clearTimeout(optimisticTimerRef.current);
        optimisticTimerRef.current = null;
      }
      setOptimisticPick(null);
    }
  }, [boardSignature, matrix, optimisticPick]);

  useEffect(() => {
    return () => {
      if (optimisticTimerRef.current !== null) {
        window.clearTimeout(optimisticTimerRef.current);
      }
    };
  }, []);

  useEffect(() => {
    setAnalysisPending(true);
    const timer = window.setTimeout(() => {
      setAnalysisSignature(boardSignature);
      setAnalysisMatrix(matrix);
      setAnalysisMyTurn(myTurn);
      setAnalysisPending(false);
    }, ANALYSIS_STABLE_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [boardSignature, matrix, myTurn]);

  const result = useMemo(() => {
    if (!mySide || !isHiddenMaster) {
      return defaultEngineResult();
    }
    if (!analysisSignature || analysisMatrix.length !== BOARD_SIZE) return defaultEngineResult();
    const cacheKey = `${analysisSignature}|${mySide}|${strategy}|${variationSeed}|${analysisMyTurn ? 1 : 0}`;
    const cached = analysisCacheRef.current.get(cacheKey);
    if (cached) return cached;
    const next = computeSuggestions(analysisMatrix, mySide, strategy, variationSeed, analysisMyTurn);
    const cache = analysisCacheRef.current;
    cache.set(cacheKey, next);
    while (cache.size > ANALYSIS_CACHE_LIMIT) {
      const first = cache.keys().next();
      if (first.done) break;
      cache.delete(first.value);
    }
    return next;
  }, [analysisSignature, analysisMatrix, mySide, strategy, variationSeed, isHiddenMaster, analysisMyTurn]);

  useEffect(() => {
    if (!isHiddenMaster || strategy !== 'rapfi_external' || !mySide || disabled) {
      rapfiReqSeqRef.current += 1;
      setRapfiPending(false);
      setRapfiSuggestion(null);
      setRapfiPonderPending(false);
      rapfiLastKeyRef.current = '';
      rapfiOkKeyRef.current = '';
      rapfiInFlightKeyRef.current = '';
      rapfiWantedKeyRef.current = '';
      return;
    }

    if (!analysisSignature || analysisMatrix.length !== BOARD_SIZE || analysisPending) return;
    const mySideNum: 1 | -1 = mySide === '#' ? 1 : -1;
    const winner = winnerOnBoard(analysisMatrix);
    if (winner !== null) {
      rapfiReqSeqRef.current += 1;
      setRapfiPending(false);
      setRapfiSuggestion({
        row: 0,
        col: 0,
        ms: 0,
        error: `${winner === 1 ? '黑方' : '白方'}已连五，当前局面应已结束，不再请求 Rapfi。`,
      });
      rapfiInFlightKeyRef.current = '';
      return;
    }
    const likelyMyTurn = analysisMyTurn || (!turnInfo.name && isSideTurnByMatrix(analysisMatrix, mySideNum));
    const reqKey = `${analysisSignature}|${mySide}|1|${strategy}`;
    rapfiWantedKeyRef.current = reqKey;

    if (!likelyMyTurn) {
      rapfiReqSeqRef.current += 1;
      setRapfiPending(false);
      rapfiInFlightKeyRef.current = '';
      return;
    }

    // 后台预判只做缓存命中提示，不直接展示为正式建议；正式落点必须来自 move 服务。
    // 防止请求排队堆积：同一时刻仅允许一个Rapfi请求在飞行。
    // 期间若局面变化，先记录wanted key，等当前请求完成后再自动分析最新局面。
    if (rapfiPending) {
      return;
    }
    if (rapfiOkKeyRef.current === reqKey) {
      return;
    }
    if (rapfiInFlightKeyRef.current === reqKey) {
      return;
    }
    if (
      rapfiFailCooldownRef.current.key === reqKey &&
      Date.now() < rapfiFailCooldownRef.current.until
    ) {
      return;
    }
    rapfiLastKeyRef.current = reqKey;
    rapfiInFlightKeyRef.current = reqKey;

    const seq = rapfiReqSeqRef.current + 1;
    rapfiReqSeqRef.current = seq;
    setRapfiSuggestion(null);
    setRapfiPending(true);

    const guardTimeout = new Promise<never>((_, reject) => {
      setTimeout(() => reject(new Error('Rapfi 响应超时（前端保护）')), rapfiGuardTimeoutMs);
    });

    Promise.race([
      window.api.analyzeGomokuRapfi({
        board: analysisMatrix,
        mySide: mySideNum,
        timeoutMs: rapfiTimeoutMs,
        mode: 'move',
      }),
      guardTimeout,
    ])
      .then((resp) => {
        if (rapfiReqSeqRef.current !== seq) return;
        const r = resp as { ok: boolean; row?: number; col?: number; ms: number; error?: string };
        if (resp.ok && resp.row && resp.col) {
          setRapfiSuggestion({ row: r.row!, col: r.col!, ms: r.ms });
          rapfiOkKeyRef.current = reqKey;
          rapfiFailCooldownRef.current = { key: '', until: 0 };
        } else {
          setRapfiSuggestion({ row: 0, col: 0, ms: r.ms || 0, error: r.error || 'Rapfi未返回可用坐标' });
          rapfiFailCooldownRef.current = { key: reqKey, until: Date.now() + 2500 };
        }
      })
      .catch((err: unknown) => {
        if (rapfiReqSeqRef.current !== seq) return;
        setRapfiSuggestion({
          row: 0,
          col: 0,
          ms: 0,
          error: err instanceof Error ? err.message : 'Rapfi调用失败',
        });
        rapfiFailCooldownRef.current = { key: reqKey, until: Date.now() + 2500 };
      })
      .finally(() => {
        if (rapfiReqSeqRef.current === seq) {
          setRapfiPending(false);
          if (rapfiInFlightKeyRef.current === reqKey) rapfiInFlightKeyRef.current = '';
        }
      });
  }, [analysisSignature, analysisMatrix, analysisPending, analysisMyTurn, strategy, isHiddenMaster, mySide, disabled, turnInfo.name, rapfiPending, rapfiTimeoutMs, rapfiGuardTimeoutMs]);

  useEffect(() => {
    const canPonder =
      isHiddenMaster &&
      strategy === 'rapfi_external' &&
      !!mySide &&
      !disabled &&
      !rapfiPending &&
      !!turnInfo.name &&
      !myTurn &&
      !!analysisSignature &&
      analysisMatrix.length === BOARD_SIZE &&
      !analysisPending;

    if (!canPonder) return;

    const ponderKey = `${analysisSignature}|${mySide}|${strategy}`;
    if (
      rapfiPonderPending ||
      !!rapfiPonderKeyRef.current ||
      rapfiPonderDoneKeyRef.current === ponderKey
    ) {
      return;
    }

    rapfiPonderKeyRef.current = ponderKey;
    const seq = rapfiPonderSeqRef.current + 1;
    rapfiPonderSeqRef.current = seq;
    setRapfiPonderPending(true);

    const mySideNum: 1 | -1 = mySide === '#' ? 1 : -1;
    const opponentSideNum: 1 | -1 = mySideNum === 1 ? -1 : 1;
    const opponentTimeoutMs = clamp(Math.round(rapfiTimeoutMs * 0.35), 2500, 12000);
    const replyTimeoutMs = clamp(Math.round(rapfiTimeoutMs * 0.45), 3000, 16000);

    const withGuard = <T,>(promise: Promise<T>, timeoutMs: number): Promise<T> => {
      return Promise.race([
        promise,
        new Promise<never>((_, reject) => {
          window.setTimeout(() => reject(new Error('Rapfi后台预判超时')), timeoutMs + 6000);
        }),
      ]);
    };

    const run = async () => {
      const opponentResp = await withGuard(
        window.api.analyzeGomokuRapfi({
          board: analysisMatrix,
          mySide: opponentSideNum,
          timeoutMs: opponentTimeoutMs,
          mode: 'ponder',
        }),
        opponentTimeoutMs,
      );
      if (!opponentResp.ok || !opponentResp.row || !opponentResp.col) return;
      if (rapfiPonderSeqRef.current !== seq || rapfiPonderKeyRef.current !== ponderKey) return;
      const predictedBoard = matrixWithMove(analysisMatrix, opponentResp.row, opponentResp.col, opponentSideNum);
      if (!predictedBoard) return;

      const replyResp = await withGuard(
        window.api.analyzeGomokuRapfi({
          board: predictedBoard,
          mySide: mySideNum,
          timeoutMs: replyTimeoutMs,
          mode: 'ponder',
        }),
        replyTimeoutMs,
      );
      if (!replyResp.ok || !replyResp.row || !replyResp.col) return;
      if (rapfiPonderSeqRef.current !== seq || rapfiPonderKeyRef.current !== ponderKey) return;

      const predictedSig = signatureOfMatrix(predictedBoard);
      const hitKey = `${predictedSig}|${mySide}|1|${strategy}`;
      rememberLimited(rapfiPonderCacheRef.current, hitKey, {
        row: replyResp.row,
        col: replyResp.col,
        ms: opponentResp.ms + replyResp.ms,
      }, 24);
    };

    run()
      .catch(() => {
        // 后台预判失败不打扰正式建议，轮到自己时仍走正式高时限分析。
      })
      .finally(() => {
        if (rapfiPonderSeqRef.current === seq) {
          rapfiPonderDoneKeyRef.current = ponderKey;
          rapfiPonderKeyRef.current = '';
          setRapfiPonderPending(false);
        }
      });
  }, [
    analysisSignature,
    analysisMatrix,
    analysisPending,
    strategy,
    isHiddenMaster,
    mySide,
    disabled,
    turnInfo.name,
    myTurn,
    rapfiPending,
    rapfiPonderPending,
    rapfiTimeoutMs,
  ]);

  const shownSuggestions = useMemo(() => {
    const base = result.suggestions.slice();
    const mySideNum = mySide === '#' ? 1 : mySide === 'o' ? -1 : null;
    const winner = analysisMatrix.length === BOARD_SIZE ? winnerOnBoard(analysisMatrix) : null;
    if (winner !== null) {
      return [{
        row: 0,
        col: 0,
        score: Number.MAX_SAFE_INTEGER,
        reason: `${winner === 1 ? '黑方' : '白方'}已经连五，局面是终局；助手不会继续给下一手，请刷新/结束当前对局。`,
        style: '终局检测',
      }];
    }
    const forcedDefense =
      mySideNum && analysisMatrix.length === BOARD_SIZE
        ? findUrgentDefenseMove(analysisMatrix, mySideNum)
        : null;
    const directWins =
      mySideNum && analysisMatrix.length === BOARD_SIZE
        ? immediateWinningPoints(analysisMatrix, mySideNum)
        : [];
    if (directWins.length > 0) {
      return directWins.slice(0, 3).map((move, idx) => ({
        row: move.r + 1,
        col: move.c + 1,
        score: Number.MAX_SAFE_INTEGER - idx,
        reason: '我方下一手可直接连五，优先落下赢棋点，不再考虑对手威胁。',
        style: '一步必胜',
      }));
    }
    if (
      strategy === 'rapfi_external' &&
      rapfiSuggestion &&
      !rapfiSuggestion.error &&
      rapfiSuggestion.row >= 1 &&
      rapfiSuggestion.row <= BOARD_SIZE &&
      rapfiSuggestion.col >= 1 &&
      rapfiSuggestion.col <= BOARD_SIZE
    ) {
      return [{
        row: rapfiSuggestion.row,
        col: rapfiSuggestion.col,
        score: Number.MAX_SAFE_INTEGER,
        reason: `Rapfi建议落子（耗时${rapfiSuggestion.ms}ms）`,
        style: 'Rapfi职业引擎',
      }];
    }
    if (strategy === 'rapfi_external' && !rapfiSuggestion?.error) {
      return [{
        row: 0,
        col: 0,
        score: 0,
        reason: 'Rapfi 正在分析，本模式不使用内置兜底建议；请等待职业引擎返回。',
        style: 'Rapfi分析中',
      }];
    }
    return forcedDefense ? [forcedDefense] : base;
  }, [result.suggestions, rapfiSuggestion, strategy, analysisMatrix, mySide]);
  const clickableSuggestions = useMemo(
    () => shownSuggestions.filter((s) => s.row >= 1 && s.row <= BOARD_SIZE && s.col >= 1 && s.col <= BOARD_SIZE),
    [shownSuggestions],
  );
  const primarySuggestion = clickableSuggestions[0] ?? null;

  const renderedCells = useMemo(() => {
    if (!optimisticPick) return cells;
    return cells.map((row) =>
      row.map((cell) => {
        if (cell.row !== optimisticPick.row || cell.col !== optimisticPick.col || cell.stone !== '.') return cell;
        return {
          ...cell,
          stone: optimisticPick.side,
          last: true,
          optimistic: true,
        };
      }),
    );
  }, [cells, optimisticPick]);

  const onPickFast = (row: number, col: number) => {
    const r = row - 1;
    const c = col - 1;
    if (r < 0 || r >= BOARD_SIZE || c < 0 || c >= BOARD_SIZE) return;
    if (matrix[r][c] === 0) {
      const infer: Side | null = mySide ?? (turnInfo.side === '#' || turnInfo.side === 'o' ? turnInfo.side : null);
      if (infer) {
        const token = Date.now();
        setOptimisticPick({ row, col, side: infer, token });
        if (optimisticTimerRef.current !== null) window.clearTimeout(optimisticTimerRef.current);
        optimisticTimerRef.current = window.setTimeout(() => {
          setOptimisticPick((cur) => (cur && cur.token === token ? null : cur));
          optimisticTimerRef.current = null;
        }, 1500);
      }
    }
    onPick(row, col);
  };

  return (
    <div className="game-interaction-panel">
      <div className="game-interaction-title">五子棋棋盘（点击直接落子）</div>
      {turnInfo.name && !myTurn && (
        <div className="game-workbench-hint">当前轮到：{turnInfo.name}，你的落子按钮已暂时禁用。</div>
      )}

      {isHiddenMaster && (
        <div className="game-advisor game-advisor-info" style={{ marginBottom: 8 }}>
          <div className="game-advisor-title">隐藏功能：大师级五子棋助手</div>
          {mySide ? (
            <>
              <div className="game-advisor-detail">
                你当前执子：{mySide === '#' ? '黑子' : '白子'}
                {turnInfo.side ? `，当前落子方：${turnInfo.side === '#' ? '黑子' : '白子'}` : ''}
              </div>
              <div className="game-chip-row">
                <select className="game-select" value={strategy} onChange={(e) => setStrategy(e.target.value as StrategyId)}>
                  {STRATEGIES.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="game-advisor-detail" style={{ marginTop: 6 }}>
                策略：{STRATEGIES.find((s) => s.id === result.strategy)?.label || '均衡控局'}｜
                决策：{result.decision.mode}｜
                胜率：{Math.round(result.decision.winRate * 100)}%｜
                威胁：{result.decision.threatLevel}｜
                我方胜势：{result.decision.myWinSteps ? `${result.decision.myWinSteps}步` : '未成型'}｜
                对方胜势：{result.decision.oppWinSteps ? `${result.decision.oppWinSteps}步` : '未成型'}｜
                前瞻：{Math.max(MIN_LOOKAHEAD_DEPTH, result.stats.depth)}层
              </div>
              <div className="game-advisor-detail" style={{ marginTop: 4 }}>
                对手战术：{result.opponentPlan.label}｜反制：{result.opponentPlan.counterFocus}
              </div>
              {strategy === 'rapfi_external' && (
                <div className="game-advisor-detail" style={{ marginTop: 4 }}>
                  Rapfi状态：
                  {boardWinner !== null
                    ? `终局：${boardWinner === 1 ? '黑方' : '白方'}已连五`
                    : analysisPending || rapfiPending
                    ? '正式分析中...（等待 Rapfi move 结果）'
                    : rapfiSuggestion && !rapfiSuggestion.error && rapfiPonderPending
                      ? `已接入（${rapfiSuggestion.ms}ms），后台预判下一手...`
                    : rapfiPonderPending
                      ? '后台预判中...'
                    : rapfiSuggestion?.error
                      ? `失败（已回退内置）：${rapfiSuggestion.error}`
                      : rapfiSuggestion
                        ? `已接入（${rapfiSuggestion.ms}ms）`
                        : '等待分析'}
                </div>
              )}
              {strategy === 'rapfi_external' && rapfiSuggestion && !rapfiSuggestion.error && (
                <div className="game-advisor-detail" style={{ marginTop: 4 }}>
                  Rapfi原始落子：第 {rapfiSuggestion.row} 行，第 {rapfiSuggestion.col} 列（耗时{rapfiSuggestion.ms}ms）
                  {shownSuggestions[0] &&
                  shownSuggestions[0].row >= 1 &&
                  shownSuggestions[0].col >= 1 &&
                  (shownSuggestions[0].row !== rapfiSuggestion.row || shownSuggestions[0].col !== rapfiSuggestion.col)
                    ? `；当前展示为更高优先级点：第 ${shownSuggestions[0].row} 行，第 ${shownSuggestions[0].col} 列`
                    : '；当前展示采用 Rapfi 建议'}
                </div>
              )}
              <div className="game-chip-row" style={{ marginTop: 6, flexWrap: 'wrap' }}>
                {clickableSuggestions.map((s, idx) => (
                  <button
                    key={`${s.row}-${s.col}-${idx}`}
                    className="mini-btn"
                    disabled={!canPick}
                    onClick={() => onPickFast(s.row, s.col)}
                    title={`${s.style}：${s.reason}`}
                  >
                    建议{idx + 1}：{s.row},{s.col}
                  </button>
                ))}
              </div>
              {shownSuggestions[0] && (
                <div className="game-advisor-detail" style={{ marginTop: 6 }}>
                  {shownSuggestions[0].row >= 1 && shownSuggestions[0].col >= 1
                    ? `当前首选：第 ${shownSuggestions[0].row} 行，第 ${shownSuggestions[0].col} 列。`
                    : ''}
                  战术：{shownSuggestions[0].style}。理由：
                  {shownSuggestions[0].reason}
                  {shownSuggestions[0].predictedReply ? `（预计对手应手：${shownSuggestions[0].predictedReply}）` : ''}
                </div>
              )}
            </>
          ) : (
            <div className="game-advisor-detail">未识别到你的黑白席位，请先加入当前五子棋对局。</div>
          )}
        </div>
      )}

      <div className="gomoku-grid">
        {renderedCells.map((row) =>
          row.map((cell) => {
            const text = cell.stone === '#' ? '●' : cell.stone === 'o' ? '○' : '·';
            const cls = [
              'gomoku-cell',
              cell.stone === '#' ? 'black' : '',
              cell.stone === 'o' ? 'white' : '',
              cell.last ? 'last' : '',
              cell.optimistic ? 'optimistic' : '',
              primarySuggestion?.row === cell.row && primarySuggestion?.col === cell.col ? 'suggested' : '',
            ]
              .filter(Boolean)
              .join(' ');
            const suggestionTitle =
              primarySuggestion?.row === cell.row && primarySuggestion?.col === cell.col
                ? `；建议落点：${primarySuggestion.style}，${primarySuggestion.reason}`
                : '';

            return (
              <button
                key={`${cell.row}-${cell.col}`}
                className={cls}
                onClick={() => onPickFast(cell.row, cell.col)}
                disabled={!canPick}
                title={`${cell.row},${cell.col}${suggestionTitle}`}
              >
                {text}
              </button>
            );
          }),
        )}
      </div>
    </div>
  );
}

