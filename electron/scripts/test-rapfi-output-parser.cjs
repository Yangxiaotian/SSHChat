const assert = require('node:assert/strict');
const path = require('node:path');

const {
  parseRapfiMoveLine,
  parseRapfiFallbackMoveLine,
} = require(path.join(__dirname, '..', 'dist', 'main', 'rapfi-output.js'));

const okCases = [
  ['8,8', { row: 9, col: 9 }],
  ['bestmove 4,5', { row: 6, col: 5 }],
  ['  0,14  ', { row: 15, col: 1 }],
  ['bestmove H8', { row: 8, col: 8 }],
];

for (const [line, expected] of okCases) {
  assert.deepEqual(parseRapfiFallbackMoveLine(line), expected, `expected final move from ${line}`);
}

const ignoredCases = [
  'info depth 9 pv 4,5 6,7 score 120',
  'candidate 4,5 value=0.31',
  'MESSAGE searching 4,5',
  'pos: 4,5 best so far',
  'bestline H8 J9',
  '15,8',
  '-1,8',
  '4,15',
];

for (const line of ignoredCases) {
  assert.equal(parseRapfiMoveLine(line), null, `must not parse non-final line: ${line}`);
  assert.equal(parseRapfiFallbackMoveLine(line), null, `must not fallback parse non-final line: ${line}`);
}

console.log('rapfi output parser tests passed');

