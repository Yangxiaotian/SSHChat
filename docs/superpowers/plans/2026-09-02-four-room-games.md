# Four Room Games Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver four complete two-player room games with server-authoritative rules, private hidden information, usable client panels, and a scalable game lobby.

**Architecture:** Extend the existing `games.py` game-class interface and `server.py` room dispatcher. Add one focused renderer panel per game, reuse `GameWorkbench` for lifecycle and command routing, and move game creation into a searchable lobby so only the active game panel is mounted.

**Tech Stack:** Python 3 standard library, existing SSHChat server/federation layer, React 18, TypeScript, Vite, existing unittest and renderer TypeScript checks.

## Global Constraints

- All four games are two-player real-time room games.
- The room owner creates a game; the second player joins it.
- There is no robot opponent in this release.
- The server is the only authority for board state, turn order, legal moves, hidden information, and results.
- Hidden game state must be rendered per connection and never placed in broadcast lines.
- Existing games and current room/federation behavior must remain compatible.
- Core actions must be available through the client panels; raw commands remain a fallback only.
- Do not add runtime dependencies.
- Each batch must pass its rule, privacy, client, and regression checks before the next batch starts.

---

### Task 1: Add Shared Game Test Helpers

**Files:**
- Create: `tests/test_new_games_common.py`
- Modify: none

**Interfaces:**
- Produces `DummyConn` and shared assertions reused by later rule tests.

- [ ] **Step 1: Write the helper module and baseline registration test**

```python
import unittest

from games import GAMES, create_game


class DummyConn:
    def __init__(self, name):
        self.name = name


class NewGameRegistrationTests(unittest.TestCase):
    def test_new_games_are_registered_and_creatable(self):
        for name in ("reversi", "darkchess", "battleship", "junqi"):
            self.assertIn(name, GAMES)
            game = create_game(name, DummyConn("alice"), "alice")
            self.assertEqual(game.name, name)
            self.assertEqual(game.state, "waiting")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify the missing registrations fail**

Run: `python -m unittest tests.test_new_games_common -v`

Expected: FAIL because the four canonical game IDs are not yet registered.

- [ ] **Step 3: Keep the helper test as the shared red test**

Do not add production code in this task. Later tasks make this test green while adding each game.

---

### Task 2: Implement Reversi Server Rules

**Files:**
- Create: none
- Modify: `games.py` near the existing board-game classes and `GAMES` registration
- Create: `tests/test_reversi_game.py`

**Interfaces:**
- Produces `ReversiGame` with `name = "reversi"`, `try_join`, `try_move`, `show`, `seats`, `resign`, `abort`, and `on_player_leave`.
- Uses 1-based row/column payloads: `row col`; accepts `pass` only when no legal move exists.

- [ ] **Step 1: Write failing rule tests**

```python
import unittest
from games import create_game
from tests.test_new_games_common import DummyConn


class ReversiGameTests(unittest.TestCase):
    def setUp(self):
        self.black = DummyConn("black")
        self.white = DummyConn("white")
        self.game = create_game("reversi", self.black, "black")
        self.game.try_join(self.white, "white")

    def test_opening_flips_bracketed_piece_and_changes_turn(self):
        self.assertEqual(self.game.try_move(self.black, "3 4")[0], [])
        self.assertEqual(self.game.board[3][3], 1)
        self.assertEqual(self.game.board[3][4], 1)
        self.assertEqual(self.game.turn, 2)

    def test_occupied_or_non_flipping_move_is_rejected_without_mutation(self):
        before = [row[:] for row in self.game.board]
        error, _, _ = self.game.try_move(self.black, "4 4")
        self.assertTrue(error)
        self.assertEqual(self.game.board, before)

    def test_two_consecutive_passes_end_as_score_result(self):
        self.game.board = [[1] * 8 for _ in range(8)]
        self.game.turn = 1
        _, _, first_done = self.game.try_move(self.black, "pass")
        self.assertFalse(first_done)
        _, _, second_done = self.game.try_move(self.white, "pass")
        self.assertTrue(second_done)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the focused tests and confirm expected failures**

Run: `python -m unittest tests.test_reversi_game -v`

Expected: FAIL because `ReversiGame` and its board rules do not exist.

- [ ] **Step 3: Implement the minimal authoritative rules**

Implement an 8x8 integer board, legal-move generation in eight directions, line flipping, automatic pass when the current player has no move, double-pass/full-board scoring, turn checks, and idempotent ended-state rejection. Register the game in `create_game`, `GAMES`, aliases, help text, and `ratings.py` with the same Elo configuration used by `doushou`.

- [ ] **Step 4: Run the focused tests**

Run: `python -m unittest tests.test_reversi_game tests.test_new_games_common -v`

Expected: PASS with no errors.

---

### Task 3: Add Reversi Client Panel and Command Catalog

**Files:**
- Create: `electron/src/renderer/components/games/ReversiPanel.tsx`
- Modify: `electron/src/renderer/components/GameWorkbench.tsx`
- Modify: `electron/src/renderer/components/games/types.ts`
- Modify: `electron/src/renderer/components/games/commandFactory.ts`
- Modify: `electron/src/renderer/i18n/messages/en.ts`
- Modify: `electron/src/renderer/i18n/messages/zh.ts`
- Modify: `electron/src/renderer/styles/vscode-dark.css`
- Create: `electron/scripts/test-reversi-panel.cjs`

**Interfaces:**
- `ReversiPanel({ disabled, nickname, boardText, onMove })` renders the server board and sends `row col`.
- `GameKind` includes `reversi`; the lobby/current-game resolver maps `reversi` and `黑白棋`.

- [ ] **Step 1: Write the parser and coordinate tests**

Test that an 8x8 server board maps row 1/column 1 to the first cell, `*`/`!` markers do not shift coordinates, and a click sends exactly the selected 1-based coordinate.

- [ ] **Step 2: Run the renderer test before implementation**

Run: `node electron/scripts/test-reversi-panel.cjs`

Expected: FAIL because the panel/parser is absent.

- [ ] **Step 3: Implement the panel and wire it into the workbench**

Render one accessible button per cell, legal markers when supplied by the server, current-turn text, pass status, and score. Mount it only when `game === "reversi"`; add localized create/join/help actions and game name/hint/tip entries.

- [ ] **Step 4: Run the renderer type and focused tests**

Run: `node electron/scripts/test-reversi-panel.cjs; npm exec -- tsc -p tsconfig.node.json --noEmit` from `electron`.

Expected: PASS.

---

### Task 4: Implement Dark Chess Server and Client

**Files:**
- Modify: `games.py`
- Create: `tests/test_darkchess_game.py`
- Create: `electron/src/renderer/components/games/DarkchessPanel.tsx`
- Modify: `electron/src/renderer/components/GameWorkbench.tsx`
- Modify: `electron/src/renderer/components/games/types.ts`
- Modify: `electron/src/renderer/components/games/commandFactory.ts`
- Modify: `electron/src/renderer/i18n/messages/en.ts`
- Modify: `electron/src/renderer/i18n/messages/zh.ts`
- Modify: `electron/src/renderer/styles/vscode-dark.css`

**Interfaces:**
- Server payloads are `flip row col` and `move from_row from_col to_row to_col`.
- `DarkchessGame.show(conn)` returns `?` for unrevealed pieces not owned by the viewer and reveals only permitted face-up information.
- `DarkchessPanel` sends only the above canonical actions.

- [ ] **Step 1: Write failing rule and privacy tests**

Cover first-flip side assignment, adjacent movement, rank capture, cannon one-screen capture, out-of-turn rejection, and the assertion that `show(red_conn)` and `show(black_conn)` do not expose the other side's unrevealed piece labels.

- [ ] **Step 2: Run the focused tests and confirm red**

Run: `python -m unittest tests.test_darkchess_game -v`

Expected: FAIL because `DarkchessGame` is absent.

- [ ] **Step 3: Implement server rules and registration**

Use a deterministic server-side shuffle, store piece ownership separately from visibility, enforce the standard 4x8 layout and move/capture rules, and settle wins once. Add aliases `darkchess`, `dark-chess`, `flipchess`, `暗棋`, and `翻翻棋`.

- [ ] **Step 4: Implement and wire the client panel**

Use a 4x8 responsive grid with face-down styling, selected state, valid action phase, current-turn message, and local error display. Never infer hidden labels from stale chat text.

- [ ] **Step 5: Run Batch 2 regression checks**

Run: `python -m unittest tests.test_darkchess_game tests.test_reversi_game tests.test_new_games_common -v` and `npm exec -- tsc -p tsconfig.node.json --noEmit` from `electron`.

Expected: PASS.

---

### Task 5: Implement Battleship Server and Client

**Files:**
- Modify: `games.py`
- Create: `tests/test_battleship_game.py`
- Create: `electron/src/renderer/components/games/BattleshipPanel.tsx`
- Modify: `electron/src/renderer/components/GameWorkbench.tsx`
- Modify: `electron/src/renderer/components/games/types.ts`
- Modify: `electron/src/renderer/components/games/commandFactory.ts`
- Modify: `electron/src/renderer/i18n/messages/en.ts`
- Modify: `electron/src/renderer/i18n/messages/zh.ts`
- Modify: `electron/src/renderer/styles/vscode-dark.css`

**Interfaces:**
- Setup payloads are `place <ship> <row> <col> <h|v>` and `ready`.
- Attack payload is `fire <row> <col>`.
- `BattleshipGame.show(conn)` exposes own ships to the owner, hit/miss results publicly, and never exposes unsunk enemy ships.

- [ ] **Step 1: Write failing placement, shot, and privacy tests**

Cover all five ship lengths, overlap/touch rejection including diagonals, readiness gating, duplicate-shot rejection, hit/sunk reporting, all-ships-sunk result, and per-player hidden ship views.

- [ ] **Step 2: Run focused tests and confirm red**

Run: `python -m unittest tests.test_battleship_game -v`

Expected: FAIL because `BattleshipGame` is absent.

- [ ] **Step 3: Implement authoritative setup and play**

Store each player's fleet and shots separately, validate complete legal deployment before `ready`, alternate turns, return only public shot outcomes, and settle once when all ship cells are hit.

- [ ] **Step 4: Implement client two-grid interaction**

Provide ship list, rotate/randomize/reset, setup validation message, own ocean grid, opponent targeting grid, hit/miss/sunk markers, and disabled states for setup/turn/end.

- [ ] **Step 5: Run Batch 2 tests**

Run: `python -m unittest tests.test_battleship_game tests.test_darkchess_game tests.test_reversi_game -v` and `npm exec -- tsc -p tsconfig.node.json --noEmit` from `electron`.

Expected: PASS.

---

### Task 6: Implement Army Chess Server and Client

**Files:**
- Modify: `games.py`
- Create: `tests/test_junqi_game.py`
- Create: `electron/src/renderer/components/games/JunqiPanel.tsx`
- Modify: `electron/src/renderer/components/GameWorkbench.tsx`
- Modify: `electron/src/renderer/components/games/types.ts`
- Modify: `electron/src/renderer/components/games/commandFactory.ts`
- Modify: `electron/src/renderer/i18n/messages/en.ts`
- Modify: `electron/src/renderer/i18n/messages/zh.ts`
- Modify: `electron/src/renderer/styles/vscode-dark.css`

**Interfaces:**
- Setup payloads are `setup <piece> <row> <col>` and `ready`.
- Play payload is `move from_row from_col to_row to_col`.
- `JunqiGame.show(conn)` reveals own pieces and public captured/revealed pieces only.

- [ ] **Step 1: Write failing deployment and capture tests**

Cover standard piece counts, legal setup cells, duplicate placement rejection, flag immobility, rank captures, bomb/mine/engineer exceptions, railway movement, camp occupancy, flag capture, no-move loss, privacy, and repeated end settlement.

- [ ] **Step 2: Run focused tests and confirm red**

Run: `python -m unittest tests.test_junqi_game -v`

Expected: FAIL because `JunqiGame` is absent.

- [ ] **Step 3: Implement the complete server rules**

Use the fixed standard two-player board map, validate each side's full deployment, keep opponent pieces hidden, implement road/rail/camp/headquarters constraints, and reject illegal moves before any mutation. Add aliases `junqi`, `army`, `landbattle`, `军棋`.

- [ ] **Step 4: Implement the client setup and play panel**

Provide a piece tray, drag/reorder or randomize controls, placement validation, ready state, private opponent rendering, selection and destination controls, capture result, and clear turn/phase messages.

- [ ] **Step 5: Run Batch 3 rule and privacy tests**

Run: `python -m unittest tests.test_junqi_game tests.test_battleship_game tests.test_darkchess_game tests.test_reversi_game -v`.

Expected: PASS.

---

### Task 7: Add Searchable Game Lobby and Final Integration

**Files:**
- Create: `electron/src/renderer/components/games/GameLobby.tsx`
- Modify: `electron/src/renderer/components/GameWorkbench.tsx`
- Modify: `electron/src/renderer/components/games/types.ts`
- Modify: `electron/src/renderer/components/games/commandFactory.ts`
- Modify: `electron/src/renderer/i18n/messages/en.ts`
- Modify: `electron/src/renderer/i18n/messages/zh.ts`
- Modify: `electron/src/renderer/styles/vscode-dark.css`
- Create: `electron/scripts/test-game-lobby.cjs`
- Modify: `server.py` only if the existing `/game list` payload needs a stable localized catalog field; preserve legacy list parsing

**Interfaces:**
- `GameLobby({ disabled, activeGame, availableGames, locale, onCommand })` emits existing `/game new`, `/game join`, `/game show`, and `/game list` commands.
- `GameKind` and localized metadata are the single source for game labels, categories, player counts, and short rules.

- [ ] **Step 1: Write failing lobby filtering tests**

Test that searching `军棋`, `junqi`, and `Army` returns the same card, category filters do not mutate the game list, and the current game is not mounted twice.

- [ ] **Step 2: Run the lobby test and confirm red**

Run: `node electron/scripts/test-game-lobby.cjs`

Expected: FAIL because `GameLobby` and the four new catalog entries are absent.

- [ ] **Step 3: Implement the lobby and replace the long quick-create list**

Keep the active-game panel in place, add a compact lobby toggle, render searchable categorized cards, and mount no inactive game panel. Preserve existing game commands and localized labels.

- [ ] **Step 4: Run renderer and server regression suites**

Run: `python -m unittest discover -s tests -p 'test_*.py' -v`; from `electron`, run `npm exec -- tsc -p tsconfig.node.json --noEmit`, `npm run test:rapfi-output`, and `npm run test:gomoku-continuity`.

Expected: all tests pass with zero TypeScript errors.

- [ ] **Step 5: Run formatting and integration checks**

Run: `python -m py_compile games.py server.py`; `git diff --check`; then run a two-client smoke test for each new game covering create, join, setup (if applicable), at least one legal action, one illegal action, reconnect/show, and end.

Expected: Python compilation succeeds, `git diff --check` has no whitespace errors, and each smoke test reaches a valid ended or still-playing state without duplicate moves or hidden-state leaks.

- [ ] **Step 6: Build only after all gates pass**

Run from `electron`: `npm run build:portable`.

Expected: Vite, TypeScript, electron-builder, and portable packaging all exit 0. Record the generated package path and do not include engine binaries unless the existing packaging policy requires them.
