const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

const sourcePath = path.resolve(__dirname, '../src/renderer/components/games/reversiBoard.ts');
assert.ok(fs.existsSync(sourcePath), 'reversiBoard.ts must exist');
const source = fs.readFileSync(sourcePath, 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  fileName: sourcePath,
}).outputText;
const moduleBox = { exports: {} };
vm.runInNewContext(compiled, {
  module: moduleBox,
  exports: moduleBox.exports,
  require,
});

const { parseReversiBoard, reversiMovePayload } = moduleBox.exports;
const board = parseReversiBoard([
  '    1 2 3 4 5 6 7 8',
  ' 1  . . . . . . . .',
  ' 2  . . . . . . . .',
  ' 3  . . . !# . . . .',
  ' 4  . . . # o . . .',
  ' 5  . . . o # . . .',
  ' 6  . . . . . . . .',
  ' 7  . . . . . . . .',
  ' 8  . . . . . . . .',
].join('\n'));

assert.equal(board.length, 64);
assert.equal(board[0].row, 1);
assert.equal(board[0].col, 1);
assert.equal(board[(3 - 1) * 8 + (4 - 1)].stone, '#');
assert.equal(board[(3 - 1) * 8 + (4 - 1)].last, true);
assert.equal(board[(4 - 1) * 8 + (5 - 1)].stone, 'o');
assert.equal(reversiMovePayload(8, 2), '8 2');

console.log('reversi panel tests passed');
