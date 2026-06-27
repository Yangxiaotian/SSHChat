export type ContinuitySide = 1 | -1;

export type ContinuityMove = {
  row: number;
  col: number;
};

export type ContinuityBranch = {
  predictedSignature: string;
  opponent: ContinuityMove;
  reply?: ContinuityMove;
  replyMs?: number;
  rank: number;
};

export type ContinuityPlan = {
  baseSignature: string;
  mySide: ContinuitySide;
  createdAt: number;
  branches: ContinuityBranch[];
};

export type PonderReply = ContinuityMove & {
  ms: number;
  opponent: ContinuityMove;
  branchRank: number;
  plannedAt: number;
};

export function continuityCacheKey(
  signature: string,
  side: '#' | 'o',
  strategy: string,
): string {
  return `${signature}|${side}|1|${strategy}`;
}

export function mergeContinuityMoves(
  primary: ContinuityMove | null,
  candidates: ContinuityMove[],
  limit = 3,
): ContinuityMove[] {
  const out: ContinuityMove[] = [];
  const seen = new Set<string>();
  for (const move of primary ? [primary, ...candidates] : candidates) {
    if (
      !Number.isInteger(move.row) ||
      !Number.isInteger(move.col) ||
      move.row < 1 ||
      move.row > 15 ||
      move.col < 1 ||
      move.col > 15
    ) {
      continue;
    }
    const key = `${move.row},${move.col}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(move);
    if (out.length >= Math.max(1, limit)) break;
  }
  return out;
}

export function freshPonderReply(
  cache: Map<string, PonderReply>,
  key: string,
  now = Date.now(),
  maxAgeMs = 120_000,
): PonderReply | null {
  const hit = cache.get(key);
  if (!hit) return null;
  if (now - hit.plannedAt > maxAgeMs) {
    cache.delete(key);
    return null;
  }
  return hit;
}

export function matchContinuityBranch(
  plan: ContinuityPlan | null,
  signature: string,
): ContinuityBranch | null {
  if (!plan) return null;
  return plan.branches.find((branch) => branch.predictedSignature === signature) || null;
}
