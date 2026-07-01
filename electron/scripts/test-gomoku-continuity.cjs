const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

const sourcePath = path.resolve(
  __dirname,
  '../src/renderer/components/games/gomokuContinuity.ts',
);
const source = fs.readFileSync(sourcePath, 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2020,
  },
  fileName: sourcePath,
}).outputText;
const moduleBox = { exports: {} };
vm.runInNewContext(compiled, {
  module: moduleBox,
  exports: moduleBox.exports,
  require,
  console,
  Date,
  Map,
  Set,
});

const {
  continuityCacheKey,
  freshPonderReply,
  matchContinuityBranch,
  mergeContinuityMoves,
} = moduleBox.exports;

assert.equal(continuityCacheKey('sig', '#', 'rapfi_external'), 'sig|#|1|rapfi_external');

assert.equal(
  JSON.stringify(mergeContinuityMoves(
    { row: 8, col: 8 },
    [{ row: 8, col: 8 }, { row: 7, col: 8 }, { row: 16, col: 1 }, { row: 8, col: 9 }],
    3,
  )),
  JSON.stringify([{ row: 8, col: 8 }, { row: 7, col: 8 }, { row: 8, col: 9 }]),
);

const cache = new Map();
cache.set('fresh', {
  row: 9,
  col: 9,
  ms: 1200,
  opponent: { row: 8, col: 8 },
  branchRank: 1,
  plannedAt: 10_000,
});
assert.equal(freshPonderReply(cache, 'fresh', 20_000, 15_000).row, 9);
assert.equal(freshPonderReply(cache, 'fresh', 30_001, 15_000), null);
assert.equal(cache.has('fresh'), false);

const plan = {
  baseSignature: 'before',
  mySide: 1,
  createdAt: 1,
  branches: [
    {
      predictedSignature: 'after-a',
      opponent: { row: 7, col: 7 },
      reply: { row: 8, col: 8 },
      rank: 1,
    },
  ],
};
assert.equal(matchContinuityBranch(plan, 'after-a').reply.row, 8);
assert.equal(matchContinuityBranch(plan, 'after-b'), null);

console.log('gomoku continuity tests passed');
