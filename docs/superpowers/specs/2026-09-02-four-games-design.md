# Four Room Games Design

**Date:** 2026-09-02

## Goal

Add four complete two-player real-time room games to SSHChat: Chinese Army Chess (`junqi`), Battleship (`battleship`), Reversi (`reversi`), and Chinese Dark Chess / Flip Chess (`darkchess`). Each game must be playable from the client without relying on raw commands for core actions, while preserving the existing server-authoritative room model.

## Product Decisions

- All four games are two-player real-time room games.
- The room owner creates a game; the second player joins it.
- There is no robot opponent in this release.
- The server is the only authority for board state, turn order, legal moves, hidden information, and results.
- Existing games remain available and use the same game lobby.
- The main workbench renders only the current game's panel. Other games are selected from a searchable, categorized game lobby.

## Rule Baselines

### Chinese Army Chess

- Two-player hidden-information Army Chess on the existing standard two-player board.
- Each player deploys their pieces during `setup` and confirms before play starts.
- Piece hierarchy is: marshal, general, colonel, major, captain, lieutenant, sergeant, engineer; bombs can capture any non-flag piece; mines can only be captured by engineers; flags cannot move.
- The board includes roads, railways, camps, headquarters, mines, bombs, and flags according to the standard two-player layout.
- A capture is legal only when the attacker can capture the defender under the hierarchy and special-piece rules.
- Capturing the opponent flag, eliminating all opponent movable pieces, or leaving the opponent without a legal move wins.

### Battleship

- Two private 10x10 grids.
- Fleet: carrier (5), battleship (4), cruiser (3), submarine (3), destroyer (2).
- Ships are placed horizontally or vertically without overlap. Ships may not touch, including diagonally.
- Both players must confirm placement before turns begin.
- Players alternate firing at one opponent coordinate. A hit is reported without exposing unsunk ship positions; a sunk ship is named only when fully destroyed.
- All ships sunk loses; the opponent wins. Duplicate shots and out-of-range shots are rejected without changing the turn.

### Reversi

- Standard 8x8 board and four-center opening.
- A move must bracket at least one contiguous opponent line in one or more directions and flips all bracketed pieces.
- If a player has no legal move, that player passes automatically and the turn changes.
- Two consecutive passes, or a full board, ends the game.
- The player with more pieces wins; an equal count is a draw.

### Chinese Dark Chess / Flip Chess

- Standard 4x8 board with 32 face-down pieces.
- On a turn, a player may flip one face-down piece or move/capture a face-up piece according to dark-chess rules.
- The first flipped piece determines the player's color/side for the game.
- Same-color adjacent movement is allowed; a capture follows piece rank rules. Cannons capture across exactly one intervening piece along a row or column.
- Face-down pieces are never exposed to the opponent before being flipped.
- A player wins when the opponent has no remaining playable piece or no legal move.

## Server Architecture

Each game is a focused class in `games.py` implementing the existing game surface:

```python
try_join(conn, name) -> GameResult
try_move(conn, raw) -> GameResult
show(conn=None) -> list[str]
seats() -> list[str]
resign(conn) -> GameResult
abort(conn) -> GameResult
on_player_leave(conn, name) -> GameResult
```

The classes are registered in `GAMES`, created through `create_game`, and resolved through `GAME_ALIASES`. The canonical IDs are `junqi`, `battleship`, `reversi`, and `darkchess`.

The state machine is:

```text
waiting -> setup -> playing -> ended
```

- `waiting`: creator has the first seat; the second seat is open.
- `setup`: both players are seated and private setup is incomplete.
- `playing`: setup is complete and turns are enforced.
- `ended`: no moves or joins can mutate the game.

For hidden-information games, `show(conn)` produces a private view. The server sends each player's view through the existing oriented/private board delivery path. A spectator receives only public information and never receives an opponent's deployment, ship positions, or unrevealed dark-chess pieces.

Every accepted action produces one state update and one board refresh. Rejected actions produce only a private error. The game object stores a monotonic move/setup revision so duplicate forwarded commands cannot apply twice.

## Command Contract

The UI uses these canonical commands; English aliases can be added through the existing command builder without changing server semantics:

```text
/game new junqi
/game new battleship
/game new reversi
/game new darkchess
/game join
/game show
/game seats
/game move setup <payload>
/game move ready
/game move pass
/game move <coordinates>
/game resign
/game abort
/game end
```

The server validates payload shape, coordinate range, phase, seat ownership, turn ownership, and game-specific legality before mutating state.

## Client Architecture

`GameWorkbench` remains the only owner of current-game selection and panel mounting. `GameKind` gains the four canonical IDs. `commandFactory.ts` gains quick actions and coordinate builders for each game.

The current-game area mounts exactly one specialized panel:

- `JunqiPanel`: drag/reorder or randomize a private deployment, confirm setup, then select a piece and destination.
- `BattleshipPanel`: place and rotate ships, randomize/reset, confirm, then show own ocean and opponent targeting grid.
- `ReversiPanel`: render an 8x8 board with legal-move markers and clickable moves.
- `DarkchessPanel`: render hidden pieces, flipped pieces, selection, legal destinations, and capture feedback.

All panels receive `disabled`, `nickname`, `boardText`, and a command callback, matching existing panel conventions. Core interactions are disabled when disconnected, not seated, not ready, not the player's turn, or when the game is ended; the panel explains the reason rather than silently ignoring clicks.

## Game Lobby

The lobby is opened from the workbench header and replaces the current long list of quick-create buttons. It contains:

- Search by localized game name or canonical ID.
- Categories: board games, strategy games, and card games.
- One card per game with localized name, player count, short rules, current availability, and create/join action.
- A compact current-game summary when a game is already active.

Only the active game's interactive panel is mounted, so adding games does not increase the height or event work of the chat view.

## Error Handling and Recovery

- Invalid moves never mutate board state or advance the turn.
- A stale client action receives a localized error and a fresh private board view.
- Disconnecting a seated player follows the existing game leave policy and settles the game exactly once.
- Rejoining with the same account rebinds the seat through the existing session recovery path.
- A game ending through win, resign, abort, or leave is idempotent and cannot be scored twice.
- Hidden state is never included in broadcast lines; only per-connection `show(conn)` output can reveal a player's private state.

## Test and Release Gates

Each batch must pass before the next batch begins:

1. **Batch 1: Reversi.** Rule tests, complete two-client game, pass/end handling, reconnect, and client coordinate tests.
2. **Batch 2: Dark Chess and Battleship.** Hidden-information privacy tests, setup validation, special captures/shots, complete two-client games, reconnect, and client interaction tests.
3. **Batch 3: Army Chess and Lobby.** Deployment validation, railway/camp/headquarters rules, capture hierarchy, complete game, privacy, reconnect, lobby search/filter, and regression tests for all existing games.

Required checks include Python compilation, server rule tests, TypeScript compilation, renderer tests, `git diff --check`, and a manual two-client room smoke test for each game. A batch is not considered complete when only its panel renders; the server must reject illegal actions and finish a full game correctly.

