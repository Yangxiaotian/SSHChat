export type RapfiMove = { row: number; col: number };

export function parseRapfiMoveLine(line: string): RapfiMove | null {
  const m = line.trim().match(/^(?:bestmove\s+)?(-?\d+)\s*,\s*(-?\d+)\s*$/i);
  if (!m) return null;
  const x = Number(m[1]);
  const y = Number(m[2]);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  if (x < 0 || x > 14 || y < 0 || y > 14) return null;
  return { row: y + 1, col: x + 1 };
}

function parseAlphaMoveToken(token: string): RapfiMove | null {
  const m = token.trim().match(/^([A-Za-z])\s*(\d{1,2})$/);
  if (!m) return null;
  const file = m[1].toUpperCase();
  const row = Number(m[2]);
  if (!Number.isFinite(row) || row < 1 || row > 15) return null;
  const col = file.charCodeAt(0) - 'A'.charCodeAt(0) + 1;
  if (col < 1 || col > 15) return null;
  return { row, col };
}

export function parseRapfiFallbackMoveLine(line: string): RapfiMove | null {
  const direct = parseRapfiMoveLine(line);
  if (direct) return direct;
  const trimmed = line.trim();
  if (/^(?:bestmove\s+)?[A-Za-z]\s*\d{1,2}$/i.test(trimmed)) {
    return parseAlphaMoveToken(trimmed.replace(/^bestmove\s+/i, ''));
  }
  return null;
}

