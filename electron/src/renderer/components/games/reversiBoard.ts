export type ReversiStone = '.' | '#' | 'o';

export type ReversiCell = {
  row: number;
  col: number;
  stone: ReversiStone;
  last: boolean;
};

export function parseReversiBoard(text: string): ReversiCell[] {
  const cells: ReversiCell[] = Array.from({ length: 64 }, (_, index) => ({
    row: Math.floor(index / 8) + 1,
    col: (index % 8) + 1,
    stone: '.',
    last: false,
  }));

  for (const rawLine of text.split('\n')) {
    const match = rawLine.match(/^\s*(\d+)\s+(.+)$/);
    if (!match) continue;
    const row = Number(match[1]);
    if (row < 1 || row > 8) continue;
    const tokens = match[2].trim().split(/\s+/).slice(0, 8);
    if (tokens.length !== 8) continue;
    tokens.forEach((rawToken, index) => {
      const last = rawToken.startsWith('!');
      const token = last ? rawToken.slice(1) : rawToken;
      if (token !== '.' && token !== '#' && token !== 'o') return;
      const cell = cells[(row - 1) * 8 + index];
      cell.stone = token;
      cell.last = last;
    });
  }
  return cells;
}

export function reversiMovePayload(row: number, col: number): string {
  return `${row} ${col}`;
}
