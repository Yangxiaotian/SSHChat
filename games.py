"""Mini game framework: chess (python-chess) + gomoku + xiangqi + sanguo for SSHChat.

Each game class exposes the same surface used by ``server.py``:
``try_join``, ``try_move``, ``resign``, ``abort``, ``seats``, ``show``,
``on_player_leave`` → ``(private_lines, broadcast_lines, ended)``.
Optional: ``pgn_export()`` for PGN (chess only).
"""

from __future__ import annotations

import itertools
import random
import re
import time
import unicodedata
from typing import Optional, TYPE_CHECKING


def stamp_new_session(game) -> None:
    """Mark a freshly created room game (/game new)."""
    now = time.time()
    game.session_started_at = now
    game.session_updated_at = now


def touch_session(game) -> None:
    """Bump session_updated_at after a move or other state change."""
    now = time.time()
    started = getattr(game, "session_started_at", None)
    if not isinstance(started, (int, float)):
        game.session_started_at = now
    game.session_updated_at = now


def game_session_updated_at(game) -> float:
    """Best-effort monotonic age key for federation conflict resolution."""
    if game is None:
        return 0.0
    for attr in ("session_updated_at", "session_started_at"):
        val = getattr(game, attr, None)
        if isinstance(val, (int, float)):
            return float(val)
    return 0.0

from ratings import GameRatingStore, game_scheme_label, is_rated_game

from sgs_data import (
    ALL_SUITS,
    SHA_CARDS,
    SHAN_CARDS,
    SGS_GENERAL_POOL,
    TRICK_NAMES,
    build_junzheng_deck,
    card_base,
    card_label,
    card_suit,
    equip_slot,
    find_card_in_hand,
    format_general_list,
    format_skills,
    is_black,
    is_diamond,
    is_red,
    is_red_sha,
    card_pin_rank,
    general_gender,
    SGS_GENERAL_BY_NAME,
    weapon_range,
)

if TYPE_CHECKING:  # noqa: SIM108
    import chess as _chess_type  # noqa: F401  only for type hints in annotations

# python-chess is optional at runtime: server starts fine without it; only
# /game new chess will refuse and report the missing dep. Gomoku is pure stdlib.
try:
    import chess as _chess  # type: ignore[no-redef]
    from chess import pgn as _chess_pgn  # noqa: F401
    _CHESS_IMPORT_ERROR: Optional[str] = None
except Exception as _e:  # noqa: BLE001
    _chess = None  # type: ignore[assignment]
    _chess_pgn = None  # type: ignore[assignment]
    _CHESS_IMPORT_ERROR = f"{_e!r}"


def chess_available() -> bool:
    return _chess is not None


def chess_import_error() -> Optional[str]:
    return _CHESS_IMPORT_ERROR


GameResult = tuple[list[str], list[str], bool]

GOMOKU_SIZE = 15
_AI_LEVEL_LABELS = {
    "easy": "简单",
    "normal": "普通",
    "hard": "困难",
}
_AI_LEVEL_ALIASES = {
    "ai": "normal",
    "bot": "normal",
    "computer": "normal",
    "电脑": "normal",
    "机器人": "normal",
    "easy": "easy",
    "normal": "normal",
    "hard": "hard",
    "简单": "easy",
    "普通": "normal",
    "困难": "hard",
}


def _parse_ai_level(tokens: list[str]) -> Optional[str]:
    level: Optional[str] = None
    for raw in tokens:
        key = raw.strip().lower()
        if not key:
            continue
        mapped = _AI_LEVEL_ALIASES.get(key)
        if mapped is None:
            raise RuntimeError(
                f"未知开局参数 {raw!r}；棋类 AI 用法：/game new chess ai [easy|normal|hard]"
            )
        level = mapped
    return level


def _board_ai_name(level: str) -> str:
    return f"AI-{_AI_LEVEL_LABELS.get(level, level)}"


def _format_rating_profile_line(
    store: Optional[GameRatingStore],
    game: str,
    seat_no: int,
    player_name: str,
) -> str:
    if store is None or not is_rated_game(game):
        return f"#{seat_no} {player_name}"
    profile = store.profile(game, player_name)
    return (
        f"#{seat_no} {profile['name']}: 积分={profile['rating']} "
        f"等级={profile['level']} 战绩={profile['wins']}/{profile['losses']}/{profile['draws']}"
    )


def _format_rating_lines(
    store: Optional[GameRatingStore],
    game: str,
    player_names: list[Optional[str]],
    *,
    ai_name: Optional[str] = None,
) -> list[str]:
    if store is None or not is_rated_game(game):
        return []
    scheme = game_scheme_label(game)
    if ai_name:
        lines = [
            f"积分体系：{scheme}；AI 练习局不计入持久化积分，人人对局跨房间共享。"
        ]
    else:
        lines = [f"积分体系：{scheme}；积分跨房间共享。"]
    for seat_no, name in enumerate(player_names, start=1):
        if name:
            lines.append(_format_rating_profile_line(store, game, seat_no, name))
        else:
            lines.append(f"#{seat_no} 空席：加入后开始计分")
    if ai_name:
        lines[-1] = f"#{len(player_names)} {ai_name}: 练习对手（不计入持久化积分）"
    return lines


def _format_rating_result_lines(
    store: Optional[GameRatingStore],
    game: str,
    player_a: str,
    player_b: str,
    score_a: float,
    *,
    ranked: bool,
) -> list[str]:
    if not ranked:
        return ["练习局：本局不计入持久化积分。"]
    if store is None or not is_rated_game(game):
        return []
    prof_a, prof_b = store.record_result(game, player_a, player_b, score_a)
    return [
        "积分结算："
        f"{prof_a['name']} {prof_a['delta']:+d} -> {prof_a['rating']}（{prof_a['level']}）；"
        f"{prof_b['name']} {prof_b['delta']:+d} -> {prof_b['rating']}（{prof_b['level']}）"
    ]


_CHESS_AI_DEPTH = {"easy": 1, "normal": 2, "hard": 3}
_CHESS_PIECE_VALUES = {
    1: 100,
    2: 320,
    3: 330,
    4: 500,
    5: 900,
    6: 20000,
}


def _chess_evaluate(board) -> int:
    outcome = board.outcome(claim_draw=True)
    if outcome is not None:
        if outcome.winner is True:
            return 200000
        if outcome.winner is False:
            return -200000
        return 0
    score = 0
    for piece_type, value in _CHESS_PIECE_VALUES.items():
        score += len(board.pieces(piece_type, True)) * value
        score -= len(board.pieces(piece_type, False)) * value
    score += len(list(board.legal_moves)) * (6 if board.turn else -6)
    return score


def _chess_order_moves(board) -> list:
    moves = list(board.legal_moves)

    def move_key(move) -> tuple[int, int]:
        score = 0
        if board.is_capture(move):
            victim = board.piece_at(move.to_square)
            attacker = board.piece_at(move.from_square)
            victim_val = _CHESS_PIECE_VALUES.get(victim.piece_type, 0) if victim else 0
            attacker_val = _CHESS_PIECE_VALUES.get(attacker.piece_type, 0) if attacker else 0
            score += 1000 + victim_val - attacker_val
        if move.promotion:
            score += 800 + _CHESS_PIECE_VALUES.get(move.promotion, 0)
        if board.gives_check(move):
            score += 120
        return (score, -move.from_square)

    moves.sort(key=move_key, reverse=True)
    return moves


def _chess_negamax(board, depth: int, alpha: int, beta: int, root_white: bool) -> int:
    if depth <= 0 or board.is_game_over(claim_draw=True):
        score = _chess_evaluate(board)
        return score if root_white else -score
    best = -10**9
    for move in _chess_order_moves(board):
        board.push(move)
        score = -_chess_negamax(board, depth - 1, -beta, -alpha, root_white)
        board.pop()
        if score > best:
            best = score
        if best > alpha:
            alpha = best
        if alpha >= beta:
            break
    return best


def _choose_chess_ai_move(board, level: str):
    if _chess is None:
        return None
    depth = _CHESS_AI_DEPTH.get(level, 2)
    root_white = board.turn == _chess.WHITE
    best_move = None
    best_score = -10**9
    for move in _chess_order_moves(board):
        board.push(move)
        score = -_chess_negamax(board, depth - 1, -10**9, 10**9, root_white)
        board.pop()
        if score > best_score:
            best_score = score
            best_move = move
    return best_move


_XQ_AI_DEPTH = {"easy": 1, "normal": 3, "hard": 4}
_XQ_AI_WIDTH = {"easy": 6, "normal": 14, "hard": 24}


def _xq_piece_value(cell: int, row: int) -> int:
    pt = _xq_piece_type(cell)
    base = {
        _XQ_K: 20000,
        _XQ_R: 900,
        _XQ_N: 430,
        _XQ_C: 450,
        _XQ_B: 220,
        _XQ_A: 220,
        _XQ_P: 120,
    }.get(pt, 0)
    side = _xq_piece_side(cell)
    if pt == _XQ_P and side is not None:
        crossed = row >= 5 if side == _XQ_RED else row <= 4
        if crossed:
            base += 40
    return base


def _xq_evaluate(board: list[list[int]]) -> int:
    score = 0
    for row_idx, row in enumerate(board):
        for cell in row:
            if cell == 0:
                continue
            side = _xq_piece_side(cell)
            value = _xq_piece_value(cell, row_idx)
            score += value if side == _XQ_RED else -value
    return score


def _xq_terminal_score(board: list[list[int]], side: int, root_side: int) -> Optional[int]:
    legal = _xq_legal_moves(board, side)
    if legal:
        return None
    king = _xq_king_pos(board, side)
    in_check = king is not None and _xq_is_attacked(board, king[0], king[1], -side)
    if in_check:
        return -200000
    return -120000


def _xq_order_moves(board: list[list[int]], moves: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    def key(move: tuple[int, int, int, int]) -> tuple[int, int]:
        fr, fc, tr, tc = move
        attacker = board[fr][fc]
        captured = board[tr][tc]
        score = 0
        if captured != 0:
            score += 1000 + _xq_piece_value(captured, tr) - _xq_piece_value(attacker, fr)
        if _xq_gives_check(board, move, _xq_piece_side(attacker) or _XQ_RED):
            score += 700
        return (score, -fr)

    return sorted(moves, key=key, reverse=True)


def _xq_gives_check(
    board: list[list[int]],
    move: tuple[int, int, int, int],
    side: int,
) -> bool:
    nb = _xq_apply_copy(board, move)
    opp = -side
    king = _xq_king_pos(nb, opp)
    return king is not None and _xq_is_attacked(nb, king[0], king[1], side)


def _xq_is_mate_move(
    board: list[list[int]],
    move: tuple[int, int, int, int],
    side: int,
) -> bool:
    nb = _xq_apply_copy(board, move)
    opp = -side
    king = _xq_king_pos(nb, opp)
    return king is not None and _xq_is_attacked(nb, king[0], king[1], side) and not _xq_legal_moves(nb, opp)


def _xq_tactical_moves(
    board: list[list[int]],
    side: int,
) -> list[tuple[int, int, int, int]]:
    moves = [
        m
        for m in _xq_legal_moves(board, side)
        if board[m[2]][m[3]] != 0 or _xq_gives_check(board, m, side)
    ]
    return _xq_order_moves(board, moves)[:14]


def _xq_quiescence(
    board: list[list[int]],
    side: int,
    alpha: int,
    beta: int,
    root_side: int,
    qdepth: int,
) -> int:
    score = _xq_evaluate(board)
    stand = score if side == _XQ_RED else -score
    if stand >= beta:
        return beta
    if stand > alpha:
        alpha = stand
    if qdepth <= 0:
        return alpha
    for move in _xq_tactical_moves(board, side):
        value = -_xq_quiescence(
            _xq_apply_copy(board, move),
            -side,
            -beta,
            -alpha,
            root_side,
            qdepth - 1,
        )
        if value >= beta:
            return beta
        if value > alpha:
            alpha = value
    return alpha


def _xq_apply_copy(
    board: list[list[int]],
    move: tuple[int, int, int, int],
) -> list[list[int]]:
    clone = [row[:] for row in board]
    _xq_apply(clone, *move)
    return clone


def _xq_negamax(
    board: list[list[int]],
    side: int,
    depth: int,
    alpha: int,
    beta: int,
    root_side: int,
    width: int,
) -> int:
    terminal = _xq_terminal_score(board, side, root_side)
    if terminal is not None:
        return terminal
    if depth <= 0:
        return _xq_quiescence(board, side, alpha, beta, root_side, 3)
    best = -10**9
    legal = _xq_order_moves(board, _xq_legal_moves(board, side))[:width]
    for move in legal:
        score = -_xq_negamax(
            _xq_apply_copy(board, move),
            -side,
            depth - 1,
            -beta,
            -alpha,
            root_side,
            width,
        )
        if score > best:
            best = score
        if best > alpha:
            alpha = best
        if alpha >= beta:
            break
    return best


def _choose_xq_ai_move(
    board: list[list[int]],
    side: int,
    level: str,
    ply_log: Optional[list[dict]] = None,
):
    depth = _XQ_AI_DEPTH.get(level, 2)
    width = _XQ_AI_WIDTH.get(level, 10)
    legal = _xq_order_moves(board, _xq_legal_moves(board, side))[:width]
    if ply_log is not None:
        legal = [
            m
            for m in legal
            if not _xq_would_lose_on_repetition(board, ply_log, side, *m)
        ] or legal
    best_move = None
    best_score = -10**9
    for move in legal:
        if _xq_is_mate_move(board, move, side):
            score = 950000
        else:
            score = -_xq_negamax(
                _xq_apply_copy(board, move),
                -side,
                depth - 1,
                -10**9,
                10**9,
                side,
                width,
            )
        if score > best_score:
            best_score = score
            best_move = move
    return best_move


_GOMOKU_AI_DEPTH = {"easy": 1, "normal": 2, "hard": 3}
_GOMOKU_AI_WIDTH = {"easy": 6, "normal": 8, "hard": 10}


def _gomoku_center_bias(row: int, col: int) -> int:
    center = GOMOKU_SIZE // 2
    return 20 - (abs(row - center) + abs(col - center))


def _gomoku_candidate_cells(grid: list[list[int]]) -> list[tuple[int, int]]:
    occupied = [
        (r, c)
        for r in range(GOMOKU_SIZE)
        for c in range(GOMOKU_SIZE)
        if grid[r][c] != 0
    ]
    if not occupied:
        center = GOMOKU_SIZE // 2
        return [(center, center)]
    cand: set[tuple[int, int]] = set()
    for r, c in occupied:
        for dr in range(-2, 3):
            for dc in range(-2, 3):
                nr = r + dr
                nc = c + dc
                if (
                    0 <= nr < GOMOKU_SIZE
                    and 0 <= nc < GOMOKU_SIZE
                    and grid[nr][nc] == 0
                ):
                    cand.add((nr, nc))
    return sorted(
        cand,
        key=lambda pos: (-_gomoku_center_bias(pos[0], pos[1]), pos[0], pos[1]),
    )


def _gomoku_pattern_score(length: int, open_ends: int) -> int:
    if length >= 5:
        return 1_000_000
    if length == 4 and open_ends == 2:
        return 120_000
    if length == 4 and open_ends == 1:
        return 20_000
    if length == 3 and open_ends == 2:
        return 8_000
    if length == 3 and open_ends == 1:
        return 1_500
    if length == 2 and open_ends == 2:
        return 500
    if length == 2 and open_ends == 1:
        return 120
    return 20 if open_ends == 2 else 0


def _gomoku_move_score(
    grid: list[list[int]],
    row: int,
    col: int,
    who: int,
) -> int:
    if grid[row][col] != 0:
        return -10**9
    total = 0
    for dr, dc in ((1, 0), (0, 1), (1, 1), (1, -1)):
        length = 1
        open_ends = 0
        for sign in (-1, 1):
            step = 1
            while True:
                nr = row + dr * step * sign
                nc = col + dc * step * sign
                if not (0 <= nr < GOMOKU_SIZE and 0 <= nc < GOMOKU_SIZE):
                    break
                if grid[nr][nc] == who:
                    length += 1
                    step += 1
                    continue
                if grid[nr][nc] == 0:
                    open_ends += 1
                break
        total += _gomoku_pattern_score(length, open_ends)
    return total + _gomoku_center_bias(row, col)


def _gomoku_eval(grid: list[list[int]], root: int) -> int:
    cand = _gomoku_candidate_cells(grid)
    best_root = max((_gomoku_move_score(grid, r, c, root) for r, c in cand), default=0)
    opp = 3 - root
    best_opp = max((_gomoku_move_score(grid, r, c, opp) for r, c in cand), default=0)
    score = best_root - int(best_opp * 0.92)
    return score if root == 1 else -score


def _gomoku_sorted_candidates(
    grid: list[list[int]],
    who: int,
    width: int,
) -> list[tuple[int, int]]:
    opp = 3 - who
    ranked = []
    for r, c in _gomoku_candidate_cells(grid):
        attack = _gomoku_move_score(grid, r, c, who)
        defend = _gomoku_move_score(grid, r, c, opp)
        ranked.append((attack * 2 + int(defend * 1.7), r, c))
    ranked.sort(reverse=True)
    return [(r, c) for _score, r, c in ranked[:width]]


def _gomoku_negamax(
    grid: list[list[int]],
    who: int,
    depth: int,
    alpha: int,
    beta: int,
    root: int,
    width: int,
    last: Optional[tuple[int, int]] = None,
) -> int:
    if last is not None and _gomoku_winner_at(grid, last[0], last[1], 3 - who):
        return -1_000_000 if root == who else 1_000_000
    if depth <= 0:
        return _gomoku_eval(grid, root)
    best = -10**9
    for row, col in _gomoku_sorted_candidates(grid, who, width):
        grid[row][col] = who
        score = -_gomoku_negamax(
            grid,
            3 - who,
            depth - 1,
            -beta,
            -alpha,
            root,
            width,
            (row, col),
        )
        grid[row][col] = 0
        if score > best:
            best = score
        if best > alpha:
            alpha = best
        if alpha >= beta:
            break
    return best


def _choose_gomoku_ai_move(grid: list[list[int]], who: int, level: str) -> tuple[int, int]:
    depth = _GOMOKU_AI_DEPTH.get(level, 2)
    width = _GOMOKU_AI_WIDTH.get(level, 8)
    best_move = None
    best_score = -10**9
    for row, col in _gomoku_sorted_candidates(grid, who, width):
        grid[row][col] = who
        if _gomoku_winner_at(grid, row, col, who):
            grid[row][col] = 0
            return (row, col)
        score = -_gomoku_negamax(
            grid,
            3 - who,
            depth - 1,
            -10**9,
            10**9,
            who,
            width,
            (row, col),
        )
        grid[row][col] = 0
        if score > best_score:
            best_score = score
            best_move = (row, col)
    return best_move or (GOMOKU_SIZE // 2, GOMOKU_SIZE // 2)


_UNDO_ACTION_ALIASES: dict[str, str] = {
    "accept": "accept",
    "同意": "accept",
    "ok": "accept",
    "yes": "accept",
    "acc": "accept",
    "y": "accept",
    "reject": "reject",
    "拒绝": "reject",
    "no": "reject",
    "rej": "reject",
    "n": "reject",
    "cancel": "cancel",
    "取消": "cancel",
    "can": "cancel",
}

_UNDO_ACTION_HINT = (
    "悔棋子命令用法：/game undo accept | reject | cancel"
    "（简写 acc / rej / can）"
)


def parse_undo_action(rest: str) -> tuple[Optional[str], Optional[str]]:
    """Map /game undo 后的参数；返回 (action, error)。

    action 为 request | accept | reject | cancel；error 非空时表示无法识别。
    """
    token = (rest or "").strip().lower()
    if not token:
        return "request", None
    if token in _UNDO_ACTION_ALIASES:
        return _UNDO_ACTION_ALIASES[token], None
    matches = {
        action
        for key, action in _UNDO_ACTION_ALIASES.items()
        if key.startswith(token) or token.startswith(key)
    }
    if len(matches) == 1:
        return matches.pop(), None
    return None, _UNDO_ACTION_HINT


class BoardUndoMixin:
    """悔棋：上一步走子方 /game undo，对方 /game undo accept。"""

    supports_undo = True

    def _undo_clear_pending(self) -> None:
        self._undo_requester_conn = None

    def _undo_queue_private(self, conn, lines: list[str]) -> None:
        if conn is None or not lines:
            return
        if not hasattr(self, "_extra_privates"):
            self._extra_privates = []
        self._extra_privates.append((conn, lines))

    def drain_extra_privates(self):
        out = getattr(self, "_extra_privates", [])
        self._extra_privates = []
        return out

    def _undo_last_mover_conn(self):
        raise NotImplementedError

    def _undo_has_moves(self) -> bool:
        raise NotImplementedError

    def _undo_pop_last_move(self) -> bool:
        raise NotImplementedError

    def _undo_opponent_conn(self, conn):
        raise NotImplementedError

    def _undo_player_name(self, conn) -> str:
        raise NotImplementedError

    def _undo_turn_line(self) -> str:
        raise NotImplementedError

    def request_undo(self, conn) -> GameResult:
        if self.state != "playing":
            return (["对局未进行中，无法悔棋。"], [], False)
        if not self.is_seated(conn):
            return (["你不是对局双方，无法悔棋。"], [], False)
        if not self._undo_has_moves():
            return (["尚无走子，无法悔棋。"], [], False)
        last_conn = self._undo_last_mover_conn()
        if last_conn is None or conn is not last_conn:
            return (["只有上一步的走子方可以请求悔棋。"], [], False)
        if self._undo_requester_conn is not None:
            if self._undo_requester_conn is conn:
                return (
                    [
                        "你已发起悔棋请求，等对方 "
                        "/game undo accept 或 /game undo reject。"
                    ],
                    [],
                    False,
                )
            return (["已有悔棋请求待对方处理。"], [], False)
        self._undo_requester_conn = conn
        opp = self._undo_opponent_conn(conn)
        opp_name = self._undo_player_name(opp) if opp else "对方"
        req_name = self._undo_player_name(conn)
        if opp is not None:
            self._undo_queue_private(
                opp,
                [
                    f"{req_name} 请求悔棋（撤销上一步）。",
                    "请用 /game undo accept 同意，或 /game undo reject 拒绝。",
                ],
            )
        return (
            [f"已向 {opp_name} 发起悔棋请求，等对方同意或拒绝。"],
            [
                f"{req_name} 请求悔棋（撤销上一步），"
                f"请 {opp_name} 执行 /game undo accept 同意，"
                "或 /game undo reject 拒绝。"
            ],
            False,
        )

    def accept_undo(self, conn) -> GameResult:
        if self.state != "playing":
            return (["对局未进行中。"], [], False)
        if not self.is_seated(conn):
            return (["你不是对局双方。"], [], False)
        if self._undo_requester_conn is None:
            return (["当前没有待处理的悔棋请求。"], [], False)
        if conn is self._undo_requester_conn:
            return (
                [
                    "你是悔棋请求方，请等对方 /game undo accept，"
                    "或 /game undo cancel 取消请求。"
                ],
                [],
                False,
            )
        requester = self._undo_requester_conn
        req_name = self._undo_player_name(requester)
        ac_name = self._undo_player_name(conn)
        if not self._undo_pop_last_move():
            self._undo_clear_pending()
            return (["无法撤销（棋盘状态异常）。"], [], False)
        self._undo_clear_pending()
        self._undo_queue_private(requester, [f"{ac_name} 已同意悔棋，已撤销你的上一步。"])
        bcast = [
            f"{ac_name} 同意悔棋，已撤销 {req_name} 的上一步。",
            self._undo_turn_line(),
        ]
        return ([f"悔棋成功，已撤销你的上一步。"], bcast, False)

    def reject_undo(self, conn) -> GameResult:
        if self._undo_requester_conn is None:
            return (["当前没有待处理的悔棋请求。"], [], False)
        if not self.is_seated(conn):
            return (["你不是对局双方。"], [], False)
        if conn is self._undo_requester_conn:
            return (
                ["对方尚未回应；可用 /game undo cancel 取消你的悔棋请求。"],
                [],
                False,
            )
        req_name = self._undo_player_name(self._undo_requester_conn)
        ac_name = self._undo_player_name(conn)
        self._undo_queue_private(self._undo_requester_conn, [f"{ac_name} 已拒绝你的悔棋请求。"])
        self._undo_clear_pending()
        return (
            [],
            [f"{ac_name} 拒绝了 {req_name} 的悔棋请求。"],
            False,
        )

    def cancel_undo(self, conn) -> GameResult:
        if self._undo_requester_conn is None:
            return (["当前没有悔棋请求可取消。"], [], False)
        if conn is not self._undo_requester_conn:
            return (["只有悔棋请求方可以 /game undo cancel 取消。"], [], False)
        name = self._undo_player_name(conn)
        opp = self._undo_opponent_conn(conn)
        if opp is not None:
            self._undo_queue_private(opp, [f"{name} 已取消悔棋请求。"])
        self._undo_clear_pending()
        return ([], [f"{name} 取消了悔棋请求。"], False)


def _color_label(color: bool) -> str:
    return "白" if color == _chess.WHITE else "黑"


def _squares_of_last_move(move):
    if move is None:
        return set()
    s = {move.from_square, move.to_square}
    # Castling: rook also moved — highlight rook from/to as well.
    if move.from_square == _chess.E1 and move.to_square == _chess.G1:
        s.update((_chess.H1, _chess.F1))
    elif move.from_square == _chess.E1 and move.to_square == _chess.C1:
        s.update((_chess.A1, _chess.D1))
    elif move.from_square == _chess.E8 and move.to_square == _chess.G8:
        s.update((_chess.H8, _chess.F8))
    elif move.from_square == _chess.E8 and move.to_square == _chess.C8:
        s.update((_chess.A8, _chess.D8))
    return s


_CHESS_EMPTY = "·"


def _chess_piece_glyph(piece) -> str:
    if piece is None:
        return _CHESS_EMPTY
    return piece.unicode_symbol()


def _render_board(board, *, last_move=None, flip: bool = False):
    hi = _squares_of_last_move(last_move)

    def col_label(ch: str) -> str:
        return f" {ch} "

    files = "hgfedcba" if flip else "abcdefgh"
    file_row = "".join(col_label(c) for c in files)
    ranks = range(1, 9) if flip else range(8, 0, -1)
    lines = ["   " + file_row]
    if flip:
        lines.append("  （己方在下方）")
    for rank in ranks:
        cells = []
        file_range = range(7, -1, -1) if flip else range(8)
        for f in file_range:
            sq = _chess.square(f, rank - 1)
            piece = board.piece_at(sq)
            sym = _chess_piece_glyph(piece)
            cells.append(f"({sym})" if sq in hi else f" {sym} ")
        lines.append(f"{rank:>2} " + "".join(cells))
    lines.append("   " + file_row)
    if last_move is not None:
        lines.append(
            f"  上一步：{board.fullmove_number} "
            f"{_chess.square_name(last_move.from_square)}→"
            f"{_chess.square_name(last_move.to_square)}"
        )
    return lines


def _format_outcome(outcome) -> str:
    term_map = {
        _chess.Termination.CHECKMATE: "将杀",
        _chess.Termination.STALEMATE: "逼和",
        _chess.Termination.INSUFFICIENT_MATERIAL: "兵力不足",
        _chess.Termination.FIFTY_MOVES: "50 回合规则",
        _chess.Termination.SEVENTYFIVE_MOVES: "75 回合规则",
        _chess.Termination.THREEFOLD_REPETITION: "三次重复",
        _chess.Termination.FIVEFOLD_REPETITION: "五次重复",
    }
    reason = term_map.get(outcome.termination, "对局结束")
    if outcome.winner is True:
        return f"对局结束：白胜（{reason}） 1-0"
    if outcome.winner is False:
        return f"对局结束：黑胜（{reason}） 0-1"
    return f"对局结束：和棋（{reason}） 1/2-1/2"


class ChessGame(BoardUndoMixin):
    """Two-seat chess. Creator = white; joiner = black."""

    name = "chess"
    first_seat_desc = "白方"
    second_seat_desc = "黑方"
    # Chess boards are compact enough to refresh every move in terminal clients.
    send_view_on_move = True

    def __init__(
        self,
        white_conn,
        white_name: str,
        *,
        rating_store: Optional[GameRatingStore] = None,
        ai_level: Optional[str] = None,
    ) -> None:
        if _chess is None:
            raise RuntimeError(
                "python-chess 未安装。请在服务端 venv 内 "
                "`pip install 'chess>=1.10'` 后重启服务。"
            )
        self.board = _chess.Board()
        self.white_conn = white_conn
        self.white_name = white_name
        self.rating_store = rating_store
        self.ai_level = ai_level
        self.ai_name = _board_ai_name(ai_level) if ai_level else None
        self.black_conn = object() if ai_level else None
        self.black_name: Optional[str] = self.ai_name if ai_level else None
        self.state = "playing" if ai_level else "waiting"
        self._last_move = None
        self._result_header: Optional[str] = None  # PGN Result when not from board.outcome()
        self.join_blurb = (
            f"{self.ai_name} 执黑，练习局立即开始；本局不计入持久化积分。"
            if ai_level
            else "等另一位玩家用 /game join 加入。"
        )
        self._undo_clear_pending()

    def _undo_has_moves(self) -> bool:
        return bool(self.board.move_stack)

    def _undo_last_mover_conn(self):
        if not self.board.move_stack:
            return None
        last_color = not self.board.turn
        return self.white_conn if last_color == _chess.WHITE else self.black_conn

    def _undo_opponent_conn(self, conn):
        side = self.color_of(conn)
        if side is None:
            return None
        return self.black_conn if side == _chess.WHITE else self.white_conn

    def _undo_player_name(self, conn) -> str:
        side = self.color_of(conn)
        if side == _chess.WHITE:
            return self.white_name
        if side == _chess.BLACK:
            return self.black_name or "黑方"
        return "?"

    def _undo_pop_last_move(self) -> bool:
        if not self.board.move_stack:
            return False
        self.board.pop()
        if self.board.move_stack:
            self._last_move = self.board.peek()
        else:
            self._last_move = None
        return True

    def _undo_turn_line(self) -> str:
        color = self.board.turn
        who = self.white_name if color == _chess.WHITE else self.black_name
        suffix = "（将军）" if self.board.is_check() else ""
        return (
            f"轮到 {_color_label(color)}方 {who}"
            f"（第 {self.board.fullmove_number} 手）{suffix}"
        )

    def color_of(self, conn) -> Optional[bool]:
        if conn is self.white_conn:
            return _chess.WHITE
        if conn is self.black_conn:
            return _chess.BLACK
        return None

    def is_seated(self, conn) -> bool:
        return self.color_of(conn) is not None

    def _viewer_flip(self, conn=None, *, viewer_name: Optional[str] = None) -> bool:
        side = self.color_of(conn)
        if side is None and viewer_name:
            vn = viewer_name.strip()
            if vn == self.white_name:
                side = _chess.WHITE
            elif vn == self.black_name:
                side = _chess.BLACK
        return side == _chess.BLACK

    def _board_render(self, conn=None, *, viewer_name: Optional[str] = None) -> list[str]:
        return _render_board(
            self.board,
            last_move=self._last_move,
            flip=self._viewer_flip(conn, viewer_name=viewer_name),
        )

    def _is_ai_game(self) -> bool:
        return self.ai_level is not None

    def _is_ai_turn(self) -> bool:
        return self._is_ai_game() and self.board.turn == _chess.BLACK

    def _rating_lines(self) -> list[str]:
        return _format_rating_lines(
            self.rating_store,
            self.name,
            [self.white_name, self.black_name],
            ai_name=self.ai_name,
        )

    def _settle_ratings(self, score_white: float) -> list[str]:
        if not self.black_name:
            return []
        return _format_rating_result_lines(
            self.rating_store,
            self.name,
            self.white_name,
            self.black_name,
            score_white,
            ranked=not self._is_ai_game(),
        )

    def _finish_outcome(self, outcome) -> list[str]:
        if outcome.winner is True:
            self._result_header = "1-0"
            return self._settle_ratings(1.0)
        if outcome.winner is False:
            self._result_header = "0-1"
            return self._settle_ratings(0.0)
        self._result_header = "1/2-1/2"
        return self._settle_ratings(0.5)

    def _run_ai_turn(self) -> list[str]:
        move = _choose_chess_ai_move(self.board, self.ai_level or "normal")
        if move is None:
            self.state = "ended"
            self._result_header = "1/2-1/2"
            return ["对局结束：AI 无合法着法。", *self._settle_ratings(0.5)]
        san = self.board.san(move)
        self.board.push(move)
        self._last_move = move
        bcast = [f"黑方 {self.black_name} 走 {san}"]
        outcome = self.board.outcome()
        if outcome is not None:
            self.state = "ended"
            bcast.append(_format_outcome(outcome))
            bcast.extend(self._finish_outcome(outcome))
            return bcast
        suffix = "（将军）" if self.board.is_check() else ""
        bcast.append(f"轮到 白方 {self.white_name}（第 {self.board.fullmove_number} 手）{suffix}")
        return bcast

    def nudge_bots(self) -> list[str]:
        """Resume AI practice after reconnect (/game show)."""
        if self.state != "playing" or not self._is_ai_turn():
            return []
        return self._run_ai_turn()

    def try_join(self, conn, name: str) -> GameResult:
        if self.state == "ended":
            return (
                [f"对局已结束，请先 /game new {self.name} 开新局。"],
                [],
                False,
            )
        if conn is self.white_conn:
            return (["你已经是白方。"], [], False)
        if self._is_ai_game():
            return (["当前为 AI 练习局，不能加入执黑；可 /game show 围观。"], [], False)
        if self.black_conn is not None:
            return (
                [f"黑方席位已被 {self.black_name} 占。"],
                [],
                False,
            )
        self.black_conn = conn
        self.black_name = name
        self.state = "playing"
        bcast = [
            f"{name} 加入为黑方，对局开始！",
            f"白：{self.white_name}    黑：{self.black_name}",
            f"轮到 白方 {self.white_name}（第 1 手）",
        ]
        return ([], bcast, False)

    def try_move(self, conn, raw: str) -> GameResult:
        if self.state == "waiting":
            return (["对局尚未开始，等黑方 /game join 后再走子。"], [], False)
        if self.state != "playing":
            return (["对局已结束。"], [], False)
        side = self.color_of(conn)
        if side is None:
            return (["你不是对局双方，无法走子（可 /game show 围观）。"], [], False)
        if side != self.board.turn:
            return (["不是你的回合。"], [], False)

        self._undo_clear_pending()
        text = raw.strip()
        if not text:
            return (["用法：/game move <走法>，如 e4 / Nf3 / O-O / e2e4。"], [], False)

        move = self._parse_move(text)
        if move is None:
            return (
                [
                    f"无法识别走法 {text!r}。支持 SAN（e4、Nf3、O-O、exd5、e8=Q）"
                    "或 UCI（e2e4、e7e8q）。"
                ],
                [],
                False,
            )

        san = self.board.san(move)
        self.board.push(move)
        self._last_move = move

        mover = self.white_name if side == _chess.WHITE else self.black_name
        bcast = [f"{_color_label(side)}方 {mover} 走 {san}"]

        outcome = self.board.outcome()
        if outcome is not None:
            self.state = "ended"
            bcast.append(_format_outcome(outcome))
            bcast.extend(self._finish_outcome(outcome))
            return ([], bcast, True)

        if self._is_ai_turn():
            bcast.extend(self._run_ai_turn())
            return ([], bcast, self.state == "ended")

        next_color = self.board.turn
        next_name = (
            self.white_name if next_color == _chess.WHITE else self.black_name
        )
        suffix = ""
        if self.board.is_check():
            suffix = "（将军）"
        bcast.append(
            f"轮到 {_color_label(next_color)}方 {next_name}"
            f"（第 {self.board.fullmove_number} 手）{suffix}"
        )
        return ([], bcast, False)

    def _parse_move(self, text: str):
        try:
            return self.board.parse_san(text)
        except (
            _chess.InvalidMoveError,
            _chess.IllegalMoveError,
            _chess.AmbiguousMoveError,
        ):
            pass
        try:
            mv = _chess.Move.from_uci(text.lower())
        except ValueError:
            return None
        if mv in self.board.legal_moves:
            return mv
        return None

    def resign(self, conn, name: str) -> GameResult:
        if self.state != "playing":
            return (["对局尚未开始或已结束，无需认负。"], [], False)
        side = self.color_of(conn)
        if side is None:
            return (["你不是对局双方。"], [], False)
        self.state = "ended"
        if side == _chess.WHITE:
            self._result_header = "0-1"
            return ([], [f"白方 {name} 认负 — 黑胜 0-1", *self._settle_ratings(0.0)], True)
        self._result_header = "1-0"
        return ([], [f"黑方 {name} 认负 — 白胜 1-0", *self._settle_ratings(1.0)], True)

    def abort(self, conn, name: str) -> GameResult:
        if self.state == "ended":
            return (["对局已结束。"], [], False)
        if self.color_of(conn) is None:
            return (["你不是对局双方，无法终止。"], [], False)
        if self.state == "playing":
            return (
                ["已开始的对局请用 /game resign 认负，不能 /game abort。"],
                [],
                False,
            )
        self.state = "ended"
        return ([], [f"{name} 终止了对局（未开始）。"], True)

    def seats(self) -> list[str]:
        lines = [
            f"chess 对局状态：{self.state}",
            f"  白方：{self.white_name}",
            f"  黑方：{self.black_name or '(空席, 可 /game join)'}",
        ]
        lines.extend(self._rating_lines())
        return lines

    def show(self, conn=None, *, viewer_name: Optional[str] = None) -> list[str]:
        lines = [
            f"chess 对局（{self.state}）  白：{self.white_name}   "
            f"黑：{self.black_name or '空席'}"
        ]
        lines.extend(self._rating_lines())
        lines.extend(self._board_render(conn, viewer_name=viewer_name))
        if self.state == "playing":
            color = self.board.turn
            who = self.white_name if color == _chess.WHITE else self.black_name
            suffix = "（将军）" if self.board.is_check() else ""
            lines.append(
                f"轮到 {_color_label(color)}方 {who}"
                f"（第 {self.board.fullmove_number} 手）{suffix}"
            )
        return lines

    def pgn_export(self) -> list[str]:
        """Multi-line PGN for the current or finished game."""
        game = _chess_pgn.Game()
        game.headers["Event"] = "SSHChat"
        game.headers["Site"] = "?"
        game.headers["White"] = self.white_name
        game.headers["Black"] = self.black_name or "?"
        if self.state == "playing":
            game.headers["Result"] = "*"
        elif self._result_header is not None:
            game.headers["Result"] = self._result_header
        else:
            outcome = self.board.outcome()
            if outcome is None:
                game.headers["Result"] = "*"
            elif outcome.winner is True:
                game.headers["Result"] = "1-0"
            elif outcome.winner is False:
                game.headers["Result"] = "0-1"
            else:
                game.headers["Result"] = "1/2-1/2"
        node = game
        for mv in self.board.move_stack:
            node = node.add_variation(mv)
        exporter = _chess_pgn.StringExporter(columns=70)
        text = game.accept(exporter).strip()
        return text.splitlines() if text else ["(empty game)"]

    def on_player_leave(self, conn, name: str) -> GameResult:
        side = self.color_of(conn)
        if side is None:
            return ([], [], False)
        if conn is self.white_conn:
            self.white_conn = None
        if conn is self.black_conn:
            self.black_conn = None
        if self.state == "waiting":
            self.state = "ended"
            self._result_header = "*"
            return ([], [f"{name} 离开，对局取消。"], True)
        if self.state == "playing":
            self.state = "ended"
            if side == _chess.WHITE:
                self._result_header = "0-1"
                return ([], [f"白方 {name} 离开 — 黑胜 0-1", *self._settle_ratings(0.0)], True)
            self._result_header = "1-0"
            return ([], [f"黑方 {name} 离开 — 白胜 1-0", *self._settle_ratings(1.0)], True)
        return ([], [], False)


def _gomoku_parse_move(raw: str) -> Optional[tuple[int, int]]:
    """Return 0-based (row, col) or None. Accepts '8 8', '8,8', '8-8'."""
    t = raw.strip().replace(",", " ").replace("-", " ")
    parts = t.split()
    if len(parts) != 2:
        return None
    try:
        r = int(parts[0])
        c = int(parts[1])
    except ValueError:
        return None
    if not (1 <= r <= GOMOKU_SIZE and 1 <= c <= GOMOKU_SIZE):
        return None
    return (r - 1, c - 1)


def _gomoku_line_length(
    grid: list[list[int]], row: int, col: int, who: int, dr: int, dc: int
) -> int:
    cnt = 1
    for sign in (-1, 1):
        r, c = row, col
        while True:
            r += dr * sign
            c += dc * sign
            if (
                r < 0
                or r >= GOMOKU_SIZE
                or c < 0
                or c >= GOMOKU_SIZE
                or grid[r][c] != who
            ):
                break
            cnt += 1
    return cnt


def _gomoku_winner_at(
    grid: list[list[int]], row: int, col: int, who: int
) -> bool:
    """Black (Renju): exactly five; white: five or more."""
    need_exact = who == 1
    dirs = ((1, 0), (0, 1), (1, 1), (1, -1))
    for dr, dc in dirs:
        cnt = _gomoku_line_length(grid, row, col, who, dr, dc)
        if need_exact:
            if cnt == 5:
                return True
        elif cnt >= 5:
            return True
    return False


def _gomoku_axis_line(
    grid: list[list[int]], row: int, col: int, dr: int, dc: int
) -> str:
    """9-cell line through (row,col); center index 4 is the last move."""
    out: list[str] = []
    for i in range(-4, 5):
        r, c = row + dr * i, col + dc * i
        if r < 0 or r >= GOMOKU_SIZE or c < 0 or c >= GOMOKU_SIZE:
            out.append("#")
        elif grid[r][c] == 1:
            out.append("X")
        elif grid[r][c] == 2:
            out.append("O")
        else:
            out.append(".")
    return "".join(out)


def _gomoku_line_max_run(line: str, ch: str = "X") -> int:
    best = 0
    i = 0
    while i < len(line):
        if line[i] != ch:
            i += 1
            continue
        j = i
        while j < len(line) and line[j] == ch:
            j += 1
        best = max(best, j - i)
        i = j
    return best


def _gomoku_line_five_threat_count(line: str) -> int:
    """Empty cells where X would complete five in a row on this line."""
    count = 0
    for pos in range(len(line)):
        if line[pos] != ".":
            continue
        trial = list(line)
        trial[pos] = "X"
        if _gomoku_line_max_run("".join(trial)) >= 5:
            count += 1
    return count


def _gomoku_axis_has_four(line: str) -> bool:
    """One four-threat on an axis through center X (活四/冲四/跳四, not dead four).

    Renju 四 = there is an empty cell where placing X makes five. Shapes that only
    become a 冲四 after one more move (e.g. OXX.X → OXXXX) are 三-level, not 四;
    counting those caused false 四四 (e.g. zouyu/yxt at 11,5).
    """
    if line[4] != "X":
        return False
    if _gomoku_line_max_run(line) >= 5:
        return False
    return _gomoku_line_five_threat_count(line) >= 1


def _gomoku_axis_open_three(line: str) -> bool:
    """One open-three (活三) on an axis through center X."""
    if line[4] != "X":
        return False
    s = line

    # Straight open three
    if (
        s[2] == "X"
        and s[3] == "X"
        and s[4] == "X"
        and s[1] == "."
        and s[5] == "."
        and s[6] != "X"
    ):
        return True
    if (
        s[3] == "X"
        and s[4] == "X"
        and s[5] == "X"
        and s[2] == "."
        and s[6] == "."
        and s[1] != "X"
    ):
        return True
    if (
        s[4] == "X"
        and s[5] == "X"
        and s[6] == "X"
        and s[3] == "."
        and s[7] == "."
        and s[8] != "X"
    ):
        return True
    # Jump open three
    if (
        s[2] == "X"
        and s[4] == "X"
        and s[5] == "X"
        and s[3] == "."
        and s[1] in ".#"
        and s[6] == "."
    ):
        return True
    if (
        s[3] == "X"
        and s[4] == "X"
        and s[6] == "X"
        and s[5] == "."
        and s[2] == "."
        and s[7] in ".#"
    ):
        return True
    # Jump open three: X.XX / XX.X through center X
    if (
        s[1] == "X"
        and s[3] == "X"
        and s[4] == "X"
        and s[2] == "."
        and s[0] in ".#"
        and s[5] == "."
    ):
        return True
    if (
        s[1] == "X"
        and s[2] == "X"
        and s[4] == "X"
        and s[3] == "."
        and s[0] in ".#"
        and s[5] == "."
    ):
        return True
    return False


def _gomoku_renju_forbidden(
    grid: list[list[int]], row: int, col: int
) -> list[str]:
    """Renju forbidden moves for black at (row,col). Stone must already be placed."""
    reasons: list[str] = []
    dirs = ((1, 0), (0, 1), (1, 1), (1, -1))
    if any(
        _gomoku_line_length(grid, row, col, 1, dr, dc) >= 6 for dr, dc in dirs
    ):
        reasons.append("长连")
    open_threes = 0
    fours = 0
    for dr, dc in dirs:
        line = _gomoku_axis_line(grid, row, col, dr, dc)
        if _gomoku_axis_has_four(line):
            fours += 1
        elif _gomoku_axis_open_three(line):
            open_threes += 1
    if open_threes >= 2:
        reasons.append("三三")
    if fours >= 2:
        reasons.append("四四")
    return reasons


def _gomoku_render(
    grid: list[list[int]],
    *,
    last: Optional[tuple[int, int]] = None,
    flip: bool = False,
) -> list[str]:
    """ASCII board: # = black (first), o = white. last move cell in parens."""
    col_nums = (
        list(range(GOMOKU_SIZE, 0, -1))
        if flip
        else list(range(1, GOMOKU_SIZE + 1))
    )
    hdr = "   " + "".join(f"{i:>2} " for i in col_nums)
    lines = [hdr]
    if flip:
        lines.append("  （己方在下方；坐标仍按全局 1,1 左上）")
    sym = {0: ".", 1: "#", 2: "o"}
    rows = range(GOMOKU_SIZE - 1, -1, -1) if flip else range(GOMOKU_SIZE)
    cols = range(GOMOKU_SIZE - 1, -1, -1) if flip else range(GOMOKU_SIZE)
    for r in rows:
        row_cells = []
        for c in cols:
            ch = sym[grid[r][c]]
            if last is not None and (r, c) == last:
                row_cells.append(f"({ch})")
            else:
                row_cells.append(f" {ch} ")
        label = r + 1  # global row; board may flip for 己方在下, coords stay 1,1 top-left
        lines.append(f"{label:>2} " + "".join(row_cells))
    lines.append(hdr)
    if last is not None:
        lines.append(f"  上一步：({last[0] + 1}, {last[1] + 1})  （行 列，1 起算，左上为 1,1）")
    return lines


class GomokuGame(BoardUndoMixin):
    """15×15 Renju-style gomoku. Creator = black (先手); joiner = white.

    Black: 长连 / 四四 / 三三 禁手，且仅「恰好五连」取胜；白方无禁手。
    """

    name = "gomoku"
    first_seat_desc = "黑方（先手）"
    second_seat_desc = "白方"
    # 每步都向房间广播最新棋盘，保证前端棋盘与原始局面文本实时同步。
    send_view_on_move = True

    def __init__(
        self,
        black_conn,
        black_name: str,
        *,
        rating_store: Optional[GameRatingStore] = None,
        ai_level: Optional[str] = None,
    ) -> None:
        self.grid: list[list[int]] = [
            [0 for _ in range(GOMOKU_SIZE)] for _ in range(GOMOKU_SIZE)
        ]
        self.black_conn = black_conn
        self.black_name = black_name
        self.rating_store = rating_store
        self.ai_level = ai_level
        self.ai_name = _board_ai_name(ai_level) if ai_level else None
        self.white_conn = object() if ai_level else None
        self.white_name: Optional[str] = self.ai_name if ai_level else None
        self.state = "playing" if ai_level else "waiting"
        self._turn = 1  # 1=black, 2=white
        self._last: Optional[tuple[int, int]] = None
        self._history: list[tuple[int, int, int]] = []  # row, col, player
        self.join_blurb = (
            f"{self.ai_name} 执白，练习局立即开始；本局不计入持久化积分。"
            if ai_level
            else "等另一位玩家用 /game join 加入。"
        )
        self._undo_clear_pending()

    def _undo_has_moves(self) -> bool:
        return bool(self._history)

    def _undo_last_mover_conn(self):
        if not self._history:
            return None
        player = self._history[-1][2]
        return self._seat_conn(player)

    def _undo_opponent_conn(self, conn):
        who = self.who_of(conn)
        if who is None:
            return None
        return self._seat_conn(3 - who)

    def _undo_player_name(self, conn) -> str:
        who = self.who_of(conn)
        if who == 1:
            return self.black_name
        if who == 2:
            return self.white_name or "白方"
        return "?"

    def _undo_pop_last_move(self) -> bool:
        if not self._history:
            return False
        row, col, player = self._history.pop()
        self.grid[row][col] = 0
        self._turn = player
        if self._history:
            lr, lc, _ = self._history[-1]
            self._last = (lr, lc)
        else:
            self._last = None
        return True

    def _undo_turn_line(self) -> str:
        next_is_black = self._turn == 1
        nm = self.black_name if next_is_black else self.white_name
        return f"轮到 {'黑' if next_is_black else '白'}方 {nm} 落子"

    def _seat_conn(self, who: int):
        return self.black_conn if who == 1 else self.white_conn

    def who_of(self, conn) -> Optional[int]:
        if conn is self.black_conn:
            return 1
        if conn is self.white_conn:
            return 2
        return None

    def is_seated(self, conn) -> bool:
        return self.who_of(conn) is not None

    def _viewer_flip(self, conn) -> bool:
        return False

    def _board_render(self, conn=None) -> list[str]:
        return _gomoku_render(self.grid, last=self._last, flip=self._viewer_flip(conn))

    def _is_ai_game(self) -> bool:
        return self.ai_level is not None

    def _is_ai_turn(self) -> bool:
        return self._is_ai_game() and self._turn == 2

    def _rating_lines(self) -> list[str]:
        return _format_rating_lines(
            self.rating_store,
            self.name,
            [self.black_name, self.white_name],
            ai_name=self.ai_name,
        )

    def _settle_ratings(self, score_black: float) -> list[str]:
        if not self.white_name:
            return []
        return _format_rating_result_lines(
            self.rating_store,
            self.name,
            self.black_name,
            self.white_name,
            score_black,
            ranked=not self._is_ai_game(),
        )

    def _run_ai_turn(self) -> list[str]:
        row, col = _choose_gomoku_ai_move(self.grid, 2, self.ai_level or "normal")
        self.grid[row][col] = 2
        self._last = (row, col)
        self._history.append((row, col, 2))
        bcast = [f"白方 {self.white_name} 落子 ({row + 1}, {col + 1})"]
        if _gomoku_winner_at(self.grid, row, col, 2):
            self.state = "ended"
            bcast.append(f"对局结束：白方 {self.white_name} 连五获胜！")
            bcast.extend(self._settle_ratings(0.0))
            return bcast
        if all(self.grid[r][c] != 0 for r in range(GOMOKU_SIZE) for c in range(GOMOKU_SIZE)):
            self.state = "ended"
            bcast.append("对局结束：棋盘已满，和棋。")
            bcast.extend(self._settle_ratings(0.5))
            return bcast
        self._turn = 1
        bcast.append(f"轮到 黑方 {self.black_name} 落子")
        return bcast

    def nudge_bots(self) -> list[str]:
        """Resume AI practice after reconnect (/game show)."""
        if self.state != "playing" or not self._is_ai_turn():
            return []
        return self._run_ai_turn()

    def try_join(self, conn, name: str) -> GameResult:
        if self.state == "ended":
            return (
                [f"对局已结束，请先 /game new {self.name} 开新局。"],
                [],
                False,
            )
        if conn is self.black_conn:
            return (["你已经是黑方。"], [], False)
        if self._is_ai_game():
            return (["当前为 AI 练习局，不能加入执白；可 /game show 围观。"], [], False)
        if self.white_conn is not None:
            return (
                [f"白方席位已被 {self.white_name} 占。"],
                [],
                False,
            )
        self.white_conn = conn
        self.white_name = name
        self.state = "playing"
        bcast = [
            f"{name} 加入为白方，对局开始！",
            f"黑（先手）：{self.black_name}    白：{self.white_name}",
            "落子：/game move <行> <列>  例：8 8  或  8,8  （1～15，左上为 1,1）",
            "规则：连珠禁手 — 黑方禁 长连、四四、三三；黑仅恰好五连胜，白五连及以上胜。",
            f"轮到 黑方 {self.black_name} 落子",
        ]
        return ([], bcast, False)

    def try_move(self, conn, raw: str) -> GameResult:
        if self.state == "waiting":
            return (["对局尚未开始，等白方 /game join。"], [], False)
        if self.state != "playing":
            return (["对局已结束。"], [], False)
        player = self.who_of(conn)
        if player is None:
            return (["你不是对局双方。"], [], False)
        if player != self._turn:
            return (["不是你的回合。"], [], False)

        self._undo_clear_pending()
        pos = _gomoku_parse_move(raw)
        if pos is None:
            return (
                [
                    "用法：/game move <行> <列>  例：8 8  或  8,8"
                    f"（1～{GOMOKU_SIZE}）"
                ],
                [],
                False,
            )
        row, col = pos
        if self.grid[row][col] != 0:
            return (["该点已有子，请换位置。"], [], False)

        self.grid[row][col] = player
        if player == 1:
            forbidden = _gomoku_renju_forbidden(self.grid, row, col)
            if forbidden:
                self.grid[row][col] = 0
                kinds = "、".join(forbidden)
                return (
                    [
                        f"黑方禁手（{kinds}），此着无效，请改下他处。",
                        "黑方仅可「恰好五连」取胜；长连、双四、双活三为禁手。",
                    ],
                    [],
                    False,
                )

        self._last = (row, col)
        self._history.append((row, col, player))

        bname = self.black_name if player == 1 else self.white_name
        stone = "黑" if player == 1 else "白"
        bcast = [f"{stone}方 {bname} 落子 ({row + 1}, {col + 1})"]

        if _gomoku_winner_at(self.grid, row, col, player):
            self.state = "ended"
            bcast.append(f"对局结束：{stone}方 {bname} 连五获胜！")
            bcast.extend(self._settle_ratings(1.0 if player == 1 else 0.0))
            return ([], bcast, True)

        if all(self.grid[r][c] != 0 for r in range(GOMOKU_SIZE) for c in range(GOMOKU_SIZE)):
            self.state = "ended"
            bcast.append("对局结束：棋盘已满，和棋。")
            bcast.extend(self._settle_ratings(0.5))
            return ([], bcast, True)

        self._turn = 3 - player
        if self._is_ai_turn():
            bcast.extend(self._run_ai_turn())
            return ([], bcast, self.state == "ended")
        next_is_black = self._turn == 1
        next_name = self.black_name if next_is_black else self.white_name
        bcast.append(f"轮到 {'黑' if next_is_black else '白'}方 {next_name} 落子")
        return ([], bcast, False)

    def resign(self, conn, name: str) -> GameResult:
        if self.state != "playing":
            return (["对局尚未开始或已结束，无需认负。"], [], False)
        player = self.who_of(conn)
        if player is None:
            return (["你不是对局双方。"], [], False)
        self.state = "ended"
        if player == 1:
            return ([], [f"黑方 {name} 认负 — 白胜", *self._settle_ratings(0.0)], True)
        return ([], [f"白方 {name} 认负 — 黑胜", *self._settle_ratings(1.0)], True)

    def abort(self, conn, name: str) -> GameResult:
        if self.state == "ended":
            return (["对局已结束。"], [], False)
        if self.who_of(conn) is None:
            return (["你不是对局双方，无法终止。"], [], False)
        if self.state == "playing":
            return (
                ["已开始的对局请用 /game resign 认负，不能 /game abort。"],
                [],
                False,
            )
        self.state = "ended"
        return ([], [f"{name} 终止了对局（未开始）。"], True)

    def seats(self) -> list[str]:
        lines = [
            f"gomoku 对局状态：{self.state}",
            f"  黑方（先手）：{self.black_name}",
            f"  白方：{self.white_name or '(空席, 可 /game join)'}",
        ]
        lines.extend(self._rating_lines())
        return lines

    def show(self, conn=None) -> list[str]:
        lines = [
            f"gomoku 对局（{self.state}）  黑：{self.black_name}   "
            f"白：{self.white_name or '空席'}",
            f"黑方（先手）：{self.black_name}",
            f"白方：{self.white_name or '(空席, 可 /game join)'}",
        ]
        lines.extend(self._rating_lines())
        lines.extend(self._board_render(conn))
        if self.state == "playing":
            next_is_black = self._turn == 1
            nm = self.black_name if next_is_black else self.white_name
            lines.append(f"轮到 {'黑' if next_is_black else '白'}方 {nm} 落子")
        return lines

    def on_player_leave(self, conn, name: str) -> GameResult:
        player = self.who_of(conn)
        if player is None:
            return ([], [], False)
        if conn is self.black_conn:
            self.black_conn = None
        if conn is self.white_conn:
            self.white_conn = None
        if self.state == "waiting":
            self.state = "ended"
            return ([], [f"{name} 离开，对局取消。"], True)
        if self.state == "playing":
            self.state = "ended"
            if player == 1:
                return ([], [f"黑方 {name} 离开 — 白胜", *self._settle_ratings(0.0)], True)
            return ([], [f"白方 {name} 离开 — 黑胜", *self._settle_ratings(1.0)], True)
        return ([], [], False)


GO_SIZE = 19
GO_KOMI = 6.5
GO_COLUMNS = "ABCDEFGHJKLMNOPQRST"


def _go_parse_move(raw: str) -> Optional[tuple[int, int]]:
    """Return 0-based (row, col). Accepts '4 4', '4,4', '4-4'."""
    t = raw.strip().replace(",", " ").replace("-", " ")
    parts = t.split()
    if len(parts) != 2:
        return None
    try:
        r = int(parts[0])
        c = int(parts[1])
    except ValueError:
        return None
    if not (1 <= r <= GO_SIZE and 1 <= c <= GO_SIZE):
        return None
    return (r - 1, c - 1)


def _go_to_gtp(row: int, col: int) -> str:
    return f"{GO_COLUMNS[col]}{GO_SIZE - row}"


def _go_neighbors(row: int, col: int):
    for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nr, nc = row + dr, col + dc
        if 0 <= nr < GO_SIZE and 0 <= nc < GO_SIZE:
            yield nr, nc


def _go_group_and_liberties(
    grid: list[list[int]], row: int, col: int
) -> tuple[set[tuple[int, int]], set[tuple[int, int]]]:
    color = grid[row][col]
    if color == 0:
        return set(), {(row, col)}
    group: set[tuple[int, int]] = set()
    liberties: set[tuple[int, int]] = set()
    stack = [(row, col)]
    while stack:
        cur = stack.pop()
        if cur in group:
            continue
        group.add(cur)
        for nr, nc in _go_neighbors(cur[0], cur[1]):
            v = grid[nr][nc]
            if v == 0:
                liberties.add((nr, nc))
            elif v == color and (nr, nc) not in group:
                stack.append((nr, nc))
    return group, liberties


def _go_try_play(
    grid: list[list[int]],
    row: int,
    col: int,
    player: int,
    ko_point: Optional[tuple[int, int]],
) -> tuple[bool, str, list[tuple[int, int]], Optional[tuple[int, int]]]:
    if grid[row][col] != 0:
        return False, "该点已有棋子，请换位置。", [], ko_point
    if ko_point is not None and (row, col) == ko_point:
        return False, "此处为劫点，不能立刻回提。", [], ko_point

    opp = 3 - player
    grid[row][col] = player
    captured: list[tuple[int, int]] = []
    seen_groups: set[frozenset[tuple[int, int]]] = set()
    for nr, nc in _go_neighbors(row, col):
        if grid[nr][nc] != opp:
            continue
        group, libs = _go_group_and_liberties(grid, nr, nc)
        key = frozenset(group)
        if key in seen_groups:
            continue
        seen_groups.add(key)
        if not libs:
            for gr, gc in group:
                grid[gr][gc] = 0
            captured.extend(sorted(group))

    own_group, own_libs = _go_group_and_liberties(grid, row, col)
    if not own_libs:
        grid[row][col] = 0
        for cr, cc in captured:
            grid[cr][cc] = opp
        return False, "禁入点：该手为自杀手。", [], ko_point

    next_ko = (
        captured[0]
        if len(captured) == 1 and len(own_group) == 1 and len(own_libs) == 1
        else None
    )
    return True, "", captured, next_ko


def _go_score(grid: list[list[int]]) -> tuple[float, float, int, int]:
    visited: set[tuple[int, int]] = set()
    black_stones = 0
    white_stones = 0
    black_territory = 0
    white_territory = 0
    for r in range(GO_SIZE):
        for c in range(GO_SIZE):
            v = grid[r][c]
            if v == 1:
                black_stones += 1
                continue
            if v == 2:
                white_stones += 1
                continue
            if (r, c) in visited:
                continue
            region: set[tuple[int, int]] = set()
            borders: set[int] = set()
            stack = [(r, c)]
            while stack:
                cur = stack.pop()
                if cur in visited:
                    continue
                visited.add(cur)
                region.add(cur)
                for nr, nc in _go_neighbors(cur[0], cur[1]):
                    nv = grid[nr][nc]
                    if nv == 0 and (nr, nc) not in visited:
                        stack.append((nr, nc))
                    elif nv in (1, 2):
                        borders.add(nv)
            if borders == {1}:
                black_territory += len(region)
            elif borders == {2}:
                white_territory += len(region)
    return (
        float(black_stones + black_territory),
        float(white_stones + white_territory) + GO_KOMI,
        black_territory,
        white_territory,
    )


def _go_render(
    grid: list[list[int]],
    *,
    last: Optional[tuple[int, int]] = None,
) -> list[str]:
    hdr = "   " + "".join(f"{i:>2} " for i in range(1, GO_SIZE + 1))
    lines = [hdr]
    sym = {0: ".", 1: "#", 2: "o"}
    for r in range(GO_SIZE):
        row_cells = []
        for c in range(GO_SIZE):
            ch = sym[grid[r][c]]
            if last is not None and (r, c) == last:
                row_cells.append(f"({ch})")
            else:
                row_cells.append(f" {ch} ")
        lines.append(f"{r + 1:>2} " + "".join(row_cells))
    lines.append(hdr)
    if last is not None:
        lines.append(f"  上一步：({last[0] + 1}, {last[1] + 1})  （行 列，1 起算，左上为 1,1）")
    lines.append("  图例：# 黑棋  o 白棋  . 空点；连续两次停一手自动数子。")
    return lines


class GoGame(BoardUndoMixin):
    """19×19 Go. Creator = black; joiner = white."""

    name = "go"
    first_seat_desc = "黑方（先手）"
    second_seat_desc = "白方"
    send_view_on_move = True

    def __init__(
        self,
        black_conn,
        black_name: str,
        *,
        rating_store: Optional[GameRatingStore] = None,
    ) -> None:
        self.grid: list[list[int]] = [[0 for _ in range(GO_SIZE)] for _ in range(GO_SIZE)]
        self.black_conn = black_conn
        self.black_name = black_name
        self.white_conn = None
        self.white_name: Optional[str] = None
        self.rating_store = rating_store
        self.state = "waiting"
        self._turn = 1
        self._last: Optional[tuple[int, int]] = None
        self._ko_point: Optional[tuple[int, int]] = None
        self._passes = 0
        self._captures = {1: 0, 2: 0}
        self._history: list[dict] = []
        self.join_blurb = "等另一位玩家用 /game join 加入。"
        self._undo_clear_pending()

    def _snapshot(
        self,
        player: int,
        action: str,
        row: Optional[int] = None,
        col: Optional[int] = None,
    ) -> dict:
        snap = {
            "player": player,
            "action": action,
            "grid": [row[:] for row in self.grid],
            "turn": self._turn,
            "last": self._last,
            "ko": self._ko_point,
            "passes": self._passes,
            "captures": dict(self._captures),
        }
        if row is not None and col is not None:
            snap["row"] = row
            snap["col"] = col
        return snap

    def _katago_moves_line(self) -> Optional[str]:
        moves: list[str] = []
        for snap in self._history:
            player = "B" if snap.get("player") == 1 else "W"
            if snap.get("action") == "pass":
                moves.append(f"{player} pass")
                continue
            if snap.get("action") != "move":
                continue
            row = snap.get("row")
            col = snap.get("col")
            if isinstance(row, int) and isinstance(col, int):
                moves.append(f"{player} {_go_to_gtp(row, col)}")
        if not moves:
            return None
        return "KataGo手顺：" + "; ".join(moves)

    def _can_show_katago_moves_line(self, conn) -> bool:
        if conn is self.black_conn:
            return self.black_name == "zouyu"
        if conn is self.white_conn:
            return self.white_name == "zouyu"
        return False

    def _undo_has_moves(self) -> bool:
        return bool(self._history)

    def _undo_last_mover_conn(self):
        if not self._history:
            return None
        return self._seat_conn(self._history[-1]["player"])

    def _undo_opponent_conn(self, conn):
        who = self.who_of(conn)
        if who is None:
            return None
        return self._seat_conn(3 - who)

    def _undo_player_name(self, conn) -> str:
        who = self.who_of(conn)
        if who == 1:
            return self.black_name
        if who == 2:
            return self.white_name or "白方"
        return "?"

    def _undo_pop_last_move(self) -> bool:
        if not self._history:
            return False
        snap = self._history.pop()
        self.grid = [row[:] for row in snap["grid"]]
        self._turn = snap["turn"]
        self._last = snap["last"]
        self._ko_point = snap["ko"]
        self._passes = snap["passes"]
        self._captures = dict(snap["captures"])
        return True

    def _undo_turn_line(self) -> str:
        return self._turn_line()

    def _seat_conn(self, who: int):
        return self.black_conn if who == 1 else self.white_conn

    def who_of(self, conn) -> Optional[int]:
        if conn is self.black_conn:
            return 1
        if conn is self.white_conn:
            return 2
        return None

    def is_seated(self, conn) -> bool:
        return self.who_of(conn) is not None

    def _rating_lines(self) -> list[str]:
        return _format_rating_lines(self.rating_store, self.name, [self.black_name, self.white_name])

    def _settle_ratings(self, score_black: float) -> list[str]:
        if not self.white_name:
            return []
        return _format_rating_result_lines(
            self.rating_store,
            self.name,
            self.black_name,
            self.white_name,
            score_black,
            ranked=True,
        )

    def _turn_line(self) -> str:
        next_is_black = self._turn == 1
        nm = self.black_name if next_is_black else self.white_name
        return f"轮到 {'黑' if next_is_black else '白'}方 {nm} 落子"

    def _finish_by_score(self) -> list[str]:
        self.state = "ended"
        black_score, white_score, black_territory, white_territory = _go_score(self.grid)
        diff = abs(black_score - white_score)
        bcast = [
            "双方连续停一手，对局结束，开始数子。",
            f"黑方：{black_score:g}（含空 {black_territory}）  白方：{white_score:g}（含贴目 {GO_KOMI:g}，空 {white_territory}）",
        ]
        if black_score > white_score:
            bcast.append(f"结果：黑方 {self.black_name} 胜 {diff:g} 子。")
            bcast.extend(self._settle_ratings(1.0))
        elif white_score > black_score:
            bcast.append(f"结果：白方 {self.white_name} 胜 {diff:g} 子。")
            bcast.extend(self._settle_ratings(0.0))
        else:
            bcast.append("结果：和棋。")
            bcast.extend(self._settle_ratings(0.5))
        return bcast

    def try_join(self, conn, name: str) -> GameResult:
        if self.state == "ended":
            return ([f"对局已结束，请先 /game new {self.name} 开新局。"], [], False)
        if conn is self.black_conn:
            return (["你已经是黑方。"], [], False)
        if conn is self.white_conn:
            return (["你已经是白方。"], [], False)
        if self.white_conn is not None:
            return ([f"白方席位已被 {self.white_name} 占。"], [], False)
        self.white_conn = conn
        self.white_name = name
        self.state = "playing"
        bcast = [
            f"{name} 加入为白方，围棋对局开始！",
            f"黑（先手）：{self.black_name}    白：{self.white_name}",
            "落子：/game move <行> <列>；停一手：/game move pass；连续两次停一手终局数子。",
            self._turn_line(),
        ]
        return ([], bcast, False)

    def try_move(self, conn, raw: str) -> GameResult:
        if self.state == "waiting":
            return (["对局尚未开始，等白方 /game join。"], [], False)
        if self.state != "playing":
            return (["对局已结束。"], [], False)
        player = self.who_of(conn)
        if player is None:
            return (["你不是对局双方。"], [], False)
        if player != self._turn:
            return (["不是你的回合。"], [], False)

        t = raw.strip().lower()
        if t in ("pass", "停", "停一手", "跳过", "过"):
            self._undo_clear_pending()
            self._history.append(self._snapshot(player, "pass"))
            self._passes += 1
            self._ko_point = None
            name = self.black_name if player == 1 else self.white_name
            stone = "黑" if player == 1 else "白"
            bcast = [f"{stone}方 {name} 停一手。"]
            if self._passes >= 2:
                bcast.extend(self._finish_by_score())
                return ([], bcast, True)
            self._turn = 3 - player
            bcast.append(self._turn_line())
            return ([], bcast, False)

        pos = _go_parse_move(raw)
        if pos is None:
            return (
                [
                    "用法：/game move <行> <列> 例：4 4；停一手：/game move pass"
                    f"（1～{GO_SIZE}）"
                ],
                [],
                False,
            )
        row, col = pos
        snap = self._snapshot(player, "move", row, col)
        ok, err, captured, next_ko = _go_try_play(self.grid, row, col, player, self._ko_point)
        if not ok:
            return ([err], [], False)

        self._undo_clear_pending()
        self._history.append(snap)
        self._last = (row, col)
        self._ko_point = next_ko
        self._passes = 0
        self._captures[player] += len(captured)
        name = self.black_name if player == 1 else self.white_name
        stone = "黑" if player == 1 else "白"
        bcast = [f"{stone}方 {name} 落子 ({row + 1}, {col + 1})"]
        if captured:
            bcast.append(f"{stone}方提子 {len(captured)} 枚。")
        self._turn = 3 - player
        bcast.append(self._turn_line())
        return ([], bcast, False)

    def resign(self, conn, name: str) -> GameResult:
        if self.state != "playing":
            return (["对局尚未开始或已结束，无需认负。"], [], False)
        player = self.who_of(conn)
        if player is None:
            return (["你不是对局双方。"], [], False)
        self.state = "ended"
        if player == 1:
            return ([], [f"黑方 {name} 认负 — 白胜", *self._settle_ratings(0.0)], True)
        return ([], [f"白方 {name} 认负 — 黑胜", *self._settle_ratings(1.0)], True)

    def abort(self, conn, name: str) -> GameResult:
        if self.state == "ended":
            return (["对局已结束。"], [], False)
        if self.who_of(conn) is None:
            return (["你不是对局双方，无法终止。"], [], False)
        if self.state == "playing":
            return (["已开始的对局请用 /game resign 认负，不能 /game abort。"], [], False)
        self.state = "ended"
        return ([], [f"{name} 终止了围棋对局（未开始）。"], True)

    def seats(self) -> list[str]:
        lines = [
            f"go 对局状态：{self.state}",
            f"  黑方（先手）：{self.black_name}",
            f"  白方：{self.white_name or '(空席, 可 /game join)'}",
            f"  提子：黑 {self._captures[1]}，白 {self._captures[2]}",
        ]
        lines.extend(self._rating_lines())
        return lines

    def show(self, conn=None) -> list[str]:
        lines = [
            f"go 对局（{self.state}）  黑：{self.black_name}   白：{self.white_name or '空席'}",
            f"贴目：白 {GO_KOMI:g}；提子：黑 {self._captures[1]}，白 {self._captures[2]}",
        ]
        lines.extend(self._rating_lines())
        lines.extend(_go_render(self.grid, last=self._last))
        if self._ko_point is not None:
            kr, kc = self._ko_point
            lines.append(f"劫点：第 {kr + 1} 行，第 {kc + 1} 列，不能立刻回提。")
        moves_line = self._katago_moves_line()
        if moves_line and self._can_show_katago_moves_line(conn):
            lines.append(moves_line)
        if self.state == "playing":
            lines.append(self._turn_line())
        elif self.state == "waiting":
            lines.append("等待白方 /game join 加入。")
        return lines

    def on_player_leave(self, conn, name: str) -> GameResult:
        player = self.who_of(conn)
        if player is None:
            return ([], [], False)
        if conn is self.black_conn:
            self.black_conn = None
        if conn is self.white_conn:
            self.white_conn = None
        if self.state == "waiting":
            self.state = "ended"
            return ([], [f"{name} 离开，围棋对局取消。"], True)
        if self.state == "playing":
            self.state = "ended"
            if player == 1:
                return ([], [f"黑方 {name} 离开 — 白胜", *self._settle_ratings(0.0)], True)
            return ([], [f"白方 {name} 离开 — 黑胜", *self._settle_ratings(1.0)], True)
        return ([], [], False)


REVERSI_SIZE = 8
_REVERSI_DIRS = tuple(
    (dr, dc)
    for dr in (-1, 0, 1)
    for dc in (-1, 0, 1)
    if dr or dc
)


def _reversi_flips(
    board: list[list[int]], row: int, col: int, player: int
) -> list[tuple[int, int]]:
    if not (0 <= row < REVERSI_SIZE and 0 <= col < REVERSI_SIZE):
        return []
    if board[row][col] != 0:
        return []
    other = 3 - player
    flips: list[tuple[int, int]] = []
    for dr, dc in _REVERSI_DIRS:
        nr, nc = row + dr, col + dc
        line: list[tuple[int, int]] = []
        while 0 <= nr < REVERSI_SIZE and 0 <= nc < REVERSI_SIZE:
            cell = board[nr][nc]
            if cell != other:
                if cell == player:
                    flips.extend(line)
                break
            line.append((nr, nc))
            nr += dr
            nc += dc
    return flips


def _reversi_legal_moves(board: list[list[int]], player: int) -> list[tuple[int, int]]:
    return [
        (row, col)
        for row in range(REVERSI_SIZE)
        for col in range(REVERSI_SIZE)
        if _reversi_flips(board, row, col, player)
    ]


def _reversi_render(
    board: list[list[int]], *, last: Optional[tuple[int, int]] = None
) -> list[str]:
    lines = ["    " + " ".join(str(i) for i in range(1, REVERSI_SIZE + 1))]
    for row, cells in enumerate(board):
        tokens = []
        for col, cell in enumerate(cells):
            token = "#" if cell == 1 else "o" if cell == 2 else "."
            if last == (row, col):
                token = f"!{token}"
            tokens.append(token)
        lines.append(f"{row + 1:>2}  " + " ".join(f"{token:>2}" for token in tokens))
    lines.append("Legend: # Black  o White  . Empty  ! opponent last")
    return lines


class ReversiGame:
    """Standard 8x8 Reversi. Creator is black; joiner is white."""

    name = "reversi"
    first_seat_desc = "Black (first)"
    second_seat_desc = "White"
    send_view_on_move = True

    def __init__(
        self,
        black_conn,
        black_name: str,
        *,
        rating_store: Optional[GameRatingStore] = None,
    ) -> None:
        self.board: list[list[int]] = [[0] * REVERSI_SIZE for _ in range(REVERSI_SIZE)]
        self.board[3][3] = 2
        self.board[3][4] = 1
        self.board[4][3] = 1
        self.board[4][4] = 2
        self.black_conn = black_conn
        self.black_name = black_name
        self.white_conn = None
        self.white_name: Optional[str] = None
        self.rating_store = rating_store
        self.state = "waiting"
        self.turn = 1
        self._passes = 0
        self._last: Optional[tuple[int, int]] = None
        self._last_player: Optional[int] = None
        self.join_blurb = "Waiting for another player to join with /game join."

    def who_of(self, conn) -> Optional[int]:
        if conn is self.black_conn:
            return 1
        if conn is self.white_conn:
            return 2
        return None

    def is_seated(self, conn) -> bool:
        return self.who_of(conn) is not None

    def _name_of(self, player: int) -> str:
        return self.black_name if player == 1 else self.white_name or "White"

    def _rating_lines(self) -> list[str]:
        return _format_rating_lines(
            self.rating_store, self.name, [self.black_name, self.white_name]
        )

    def _settle_ratings(self, score_black: float) -> list[str]:
        if not self.white_name:
            return []
        return _format_rating_result_lines(
            self.rating_store,
            self.name,
            self.black_name,
            self.white_name,
            score_black,
            ranked=True,
        )

    def _turn_line(self) -> str:
        return f"Turn: {'Black' if self.turn == 1 else 'White'} {self._name_of(self.turn)}"

    def _score(self) -> tuple[int, int]:
        black = sum(cell == 1 for row in self.board for cell in row)
        white = sum(cell == 2 for row in self.board for cell in row)
        return black, white

    def _finish(self) -> list[str]:
        self.state = "ended"
        black, white = self._score()
        lines = [f"Reversi game over: Black {black}, White {white}."]
        if black > white:
            lines.append(f"Result: Black {self.black_name} wins.")
            lines.extend(self._settle_ratings(1.0))
        elif white > black:
            lines.append(f"Result: White {self.white_name} wins.")
            lines.extend(self._settle_ratings(0.0))
        else:
            lines.append("Result: draw.")
            lines.extend(self._settle_ratings(0.5))
        return lines

    def try_join(self, conn, name: str) -> GameResult:
        if self.state == "ended":
            return ([f"Game ended; start a new {self.name} game."], [], False)
        if conn is self.black_conn:
            return (["You are already Black."], [], False)
        if conn is self.white_conn:
            return (["You are already White."], [], False)
        if self.white_conn is not None:
            return ([f"White seat is occupied by {self.white_name}."], [], False)
        self.white_conn = conn
        self.white_name = name
        self.state = "playing"
        return (
            [],
            [
                f"{name} joined Reversi as White; game started.",
                f"Black: {self.black_name}  White: {self.white_name}",
                "Move with /game move <row> <col>; pass only when no legal move exists.",
                self._turn_line(),
            ],
            False,
        )

    def try_move(self, conn, raw: str) -> GameResult:
        if self.state == "waiting":
            return (["Game has not started; wait for White to join."], [], False)
        if self.state != "playing":
            return (["Game has ended."], [], False)
        player = self.who_of(conn)
        if player is None:
            return (["You are not one of the players."], [], False)
        if player != self.turn:
            return (["It is not your turn."], [], False)

        token = raw.strip().lower()
        legal = _reversi_legal_moves(self.board, player)
        if token in {"pass", "skip", "过", "停", "停一手"}:
            if legal:
                return (["You have a legal move; passing is not allowed."], [], False)
            self._passes += 1
            self._last = None
            self._last_player = None
            name = self._name_of(player)
            lines = [f"{name} passes."]
            if self._passes >= 2:
                lines.extend(self._finish())
                return ([], lines, True)
            self.turn = 3 - player
            lines.append(self._turn_line())
            return ([], lines, False)

        match = re.fullmatch(r"(\d+)\s*[, ]\s*(\d+)", token)
        if not match:
            return (["Usage: /game move <row> <col> (1-8), or pass when blocked."], [], False)
        row, col = int(match.group(1)) - 1, int(match.group(2)) - 1
        flips = _reversi_flips(self.board, row, col, player)
        if not flips:
            return (["Illegal Reversi move: the move must flip at least one piece."], [], False)

        self.board[row][col] = player
        for fr, fc in flips:
            self.board[fr][fc] = player
        self._last = (row, col)
        self._last_player = player
        self._passes = 0
        self.turn = 3 - player
        lines = [
            f"{self._name_of(player)} plays ({row + 1}, {col + 1}) and flips {len(flips)}.",
        ]
        if not _reversi_legal_moves(self.board, self.turn):
            lines.append(f"{self._name_of(self.turn)} has no legal move and must pass.")
        if not any(cell == 0 for row_cells in self.board for cell in row_cells):
            lines.extend(self._finish())
            return ([], lines, True)
        lines.append(self._turn_line())
        return ([], lines, False)

    def resign(self, conn, name: str) -> GameResult:
        if self.state != "playing":
            return (["Game has not started or has already ended."], [], False)
        player = self.who_of(conn)
        if player is None:
            return (["You are not one of the players."], [], False)
        self.state = "ended"
        winner = 3 - player
        return (
            [],
            [
                f"{name} resigns; {self._name_of(winner)} wins.",
                *self._settle_ratings(1.0 if winner == 1 else 0.0),
            ],
            True,
        )

    def abort(self, conn, name: str) -> GameResult:
        if self.state == "ended":
            return (["Game has ended."], [], False)
        if self.who_of(conn) is None:
            return (["You are not one of the players."], [], False)
        if self.state == "playing":
            return (["A started game must be resigned, not aborted."], [], False)
        self.state = "ended"
        return ([], [f"{name} aborted the Reversi game."], True)

    def seats(self) -> list[str]:
        black, white = self._score()
        return [
            f"reversi game state: {self.state}",
            f"Black: {self.black_name}",
            f"White: {self.white_name or '(empty; /game join)'}",
            f"Score: Black {black}, White {white}",
            *self._rating_lines(),
        ]

    def show(self, conn=None) -> list[str]:
        black, white = self._score()
        viewer = self.who_of(conn)
        last = self._last if self._last is not None and (viewer is None or self._last_player != viewer) else None
        lines = [
            f"reversi game ({self.state})  Black: {self.black_name}  White: {self.white_name or 'empty'}",
            f"Score: Black {black}, White {white}",
            *self._rating_lines(),
            *_reversi_render(self.board, last=last),
        ]
        if self.state == "playing":
            lines.append(self._turn_line())
        elif self.state == "waiting":
            lines.append("Waiting for White: /game join")
        return lines

    def on_player_leave(self, conn, name: str) -> GameResult:
        player = self.who_of(conn)
        if player is None:
            return ([], [], False)
        if conn is self.black_conn:
            self.black_conn = None
        if conn is self.white_conn:
            self.white_conn = None
        if self.state == "waiting":
            self.state = "ended"
            return ([], [f"{name} left; Reversi game cancelled."], True)
        if self.state == "playing":
            self.state = "ended"
            winner = 3 - player
            return (
                [],
                [
                    f"{name} left; {self._name_of(winner)} wins.",
                    *self._settle_ratings(1.0 if winner == 1 else 0.0),
                ],
                True,
            )
        return ([], [], False)


DARKCHESS_ROWS = 4
DARKCHESS_COLS = 8
_DARKCHESS_PIECES = (
    ("red", 1, "G"), ("red", 2, "A"), ("red", 2, "A"),
    ("red", 3, "E"), ("red", 3, "E"), ("red", 4, "R"),
    ("red", 4, "R"), ("red", 5, "H"), ("red", 5, "H"),
    ("red", 6, "C"), ("red", 6, "C"), ("red", 7, "S"),
    ("red", 7, "S"), ("red", 7, "S"), ("red", 7, "S"),
    ("red", 7, "S"), ("black", 1, "G"), ("black", 2, "A"),
    ("black", 2, "A"), ("black", 3, "E"), ("black", 3, "E"),
    ("black", 4, "R"), ("black", 4, "R"), ("black", 5, "H"),
    ("black", 5, "H"), ("black", 6, "C"), ("black", 6, "C"),
    ("black", 7, "S"), ("black", 7, "S"), ("black", 7, "S"),
    ("black", 7, "S"), ("black", 7, "S"),
)
_DARKCHESS_DIRS = ((-1, 0), (1, 0), (0, -1), (0, 1))


def _darkchess_index(row: int, col: int) -> int:
    return (row - 1) * DARKCHESS_COLS + col - 1


class DarkchessGame:
    """Two-player Chinese Dark Chess with private face-down pieces."""

    name = "darkchess"
    first_seat_desc = "Player 1"
    second_seat_desc = "Player 2"
    send_view_on_move = True

    def __init__(
        self,
        first_conn,
        first_name: str,
        *,
        rating_store: Optional[GameRatingStore] = None,
    ) -> None:
        self.first_conn = first_conn
        self.first_name = first_name
        self.second_conn = None
        self.second_name: Optional[str] = None
        self.rating_store = rating_store
        self.state = "waiting"
        self.turn = 1
        self.board: list[Optional[int]] = list(range(32))
        self.pieces = [
            {"side": side, "rank": rank, "label": label}
            for side, rank, label in _DARKCHESS_PIECES
        ]
        random.shuffle(self.board)
        self.face_up: set[int] = set()
        self.player_side: dict[int, Optional[str]] = {1: None, 2: None}
        self._last: Optional[tuple[int, int]] = None
        self._last_player: Optional[int] = None
        self.join_blurb = "Waiting for another player to join with /game join."

    def who_of(self, conn) -> Optional[int]:
        if conn is self.first_conn:
            return 1
        if conn is self.second_conn:
            return 2
        return None

    def is_seated(self, conn) -> bool:
        return self.who_of(conn) is not None

    def _player_name(self, player: int) -> str:
        return self.first_name if player == 1 else self.second_name or "Player 2"

    def _piece(self, cell: int) -> dict:
        return self.pieces[cell]

    def _side_for_player(self, player: int) -> Optional[str]:
        return self.player_side.get(player)

    def _rating_lines(self) -> list[str]:
        return _format_rating_lines(
            self.rating_store, self.name, [self.first_name, self.second_name]
        )

    def _settle_ratings(self, winner: int) -> list[str]:
        if not self.second_name:
            return []
        return _format_rating_result_lines(
            self.rating_store,
            self.name,
            self.first_name,
            self.second_name,
            1.0 if winner == 1 else 0.0,
            ranked=True,
        )

    def _turn_line(self) -> str:
        return f"Turn: {self._player_name(self.turn)} (player {self.turn})"

    def _adjacent(self, fr: int, fc: int, tr: int, tc: int) -> bool:
        return abs(fr - tr) + abs(fc - tc) == 1

    def _can_capture(self, attacker: dict, defender: dict) -> bool:
        if attacker["label"] == "C":
            return False
        if attacker["label"] == "S" and defender["label"] == "G":
            return True
        if attacker["label"] == "G" and defender["label"] == "S":
            return False
        return attacker["rank"] <= defender["rank"]

    def _cannon_screen(self, fr: int, fc: int, tr: int, tc: int) -> Optional[int]:
        if fr != tr and fc != tc:
            return None
        step_r = 0 if fr == tr else (1 if tr > fr else -1)
        step_c = 0 if fc == tc else (1 if tc > fc else -1)
        r, c = fr + step_r, fc + step_c
        screen = None
        while (r, c) != (tr, tc):
            cell = self.board[_darkchess_index(r, c)]
            if cell is not None:
                if screen is not None:
                    return None
                screen = cell
            r += step_r
            c += step_c
        return screen

    def _has_side_piece(self, side: str) -> bool:
        return any(cell is not None and self._piece(cell)["side"] == side for cell in self.board)

    def _finish_if_needed(self) -> Optional[tuple[int, list[str]]]:
        if self.player_side[1] is None or self.player_side[2] is None:
            return None
        next_side = self.player_side[self.turn]
        if next_side and self._has_side_piece(next_side):
            return None
        winner = 3 - self.turn
        self.state = "ended"
        return winner, [
            f"{self._player_name(winner)} wins: the opponent has no pieces left.",
            *self._settle_ratings(winner),
        ]

    def try_join(self, conn, name: str) -> GameResult:
        if self.state == "ended":
            return (["Game ended; start a new darkchess game."], [], False)
        if conn is self.first_conn:
            return (["You are already Player 1."], [], False)
        if conn is self.second_conn:
            return (["You are already Player 2."], [], False)
        if self.second_conn is not None:
            return ([f"Player 2 seat is occupied by {self.second_name}."], [], False)
        self.second_conn = conn
        self.second_name = name
        self.state = "playing"
        return (
            [],
            [
                f"{name} joined darkchess; flip a piece to determine sides.",
                f"Player 1: {self.first_name}  Player 2: {self.second_name}",
                "Use /game move flip <row> <col> or /game move move <from row> <from col> <to row> <to col>.",
                self._turn_line(),
            ],
            False,
        )

    def try_move(self, conn, raw: str) -> GameResult:
        if self.state == "waiting":
            return (["Game has not started; wait for Player 2 to join."], [], False)
        if self.state != "playing":
            return (["Game has ended."], [], False)
        player = self.who_of(conn)
        if player is None:
            return (["You are not one of the players."], [], False)
        if player != self.turn:
            return (["It is not your turn."], [], False)

        parts = raw.strip().split()
        if not parts:
            return (["Usage: flip row col or move from_row from_col to_row to_col."], [], False)
        verb = parts[0].lower()
        if verb in {"flip", "翻", "翻子"}:
            if len(parts) != 3 or not all(part.isdigit() for part in parts[1:]):
                return (["Usage: /game move flip <row> <col> (1-4, 1-8)."], [], False)
            row, col = int(parts[1]), int(parts[2])
            if not (1 <= row <= DARKCHESS_ROWS and 1 <= col <= DARKCHESS_COLS):
                return (["Coordinates must be row 1-4 and column 1-8."], [], False)
            pos = _darkchess_index(row, col)
            cell = self.board[pos]
            if cell is None or cell in self.face_up:
                return (["That square has no face-down piece."], [], False)
            self.face_up.add(cell)
            if self.player_side[1] is None:
                self.player_side[player] = self._piece(cell)["side"]
                self.player_side[3 - player] = "black" if self.player_side[player] == "red" else "red"
            self._last = (row, col)
            self._last_player = player
            self.turn = 3 - player
            p = self._piece(cell)
            lines = [f"{self._player_name(player)} flips {'+' if p['side'] == 'red' else '-'}{p['label']}." ]
            lines.append(self._turn_line())
            return ([], lines, False)

        if verb not in {"move", "走", "移动"} or len(parts) != 5 or not all(part.isdigit() for part in parts[1:]):
            return (["Usage: /game move move <from row> <from col> <to row> <to col>."], [], False)
        fr, fc, tr, tc = (int(value) for value in parts[1:])
        if not (1 <= fr <= 4 and 1 <= tr <= 4 and 1 <= fc <= 8 and 1 <= tc <= 8):
            return (["Coordinates must be row 1-4 and column 1-8."], [], False)
        source_pos, target_pos = _darkchess_index(fr, fc), _darkchess_index(tr, tc)
        source, target = self.board[source_pos], self.board[target_pos]
        side = self._side_for_player(player)
        if side is None:
            return (["Flip the first piece before moving."], [], False)
        if source is None or source not in self.face_up or self._piece(source)["side"] != side:
            return (["You can move only your own face-up piece."], [], False)
        if target is not None and target not in self.face_up:
            return (["A face-down piece must be flipped before it can be captured."], [], False)
        if target is None:
            if not self._adjacent(fr, fc, tr, tc):
                return (["A normal piece moves one adjacent square."], [], False)
        else:
            attacker, defender = self._piece(source), self._piece(target)
            if attacker["side"] == defender["side"]:
                return (["You cannot capture your own piece."], [], False)
            if attacker["label"] == "C":
                if self._cannon_screen(fr, fc, tr, tc) is None:
                    return (["A cannon capture needs exactly one screen in a row or column."], [], False)
            elif not self._adjacent(fr, fc, tr, tc) or not self._can_capture(attacker, defender):
                return (["Illegal capture under darkchess rank rules."], [], False)
        self.board[source_pos] = None
        self.board[target_pos] = source
        self._last = (tr, tc)
        self._last_player = player
        self.turn = 3 - player
        lines = [f"{self._player_name(player)} moves from ({fr}, {fc}) to ({tr}, {tc})."]
        finished = self._finish_if_needed()
        if finished:
            _, finish_lines = finished
            lines.extend(finish_lines)
            return ([], lines, True)
        lines.append(self._turn_line())
        return ([], lines, False)

    def resign(self, conn, name: str) -> GameResult:
        if self.state != "playing":
            return (["Game has not started or has already ended."], [], False)
        player = self.who_of(conn)
        if player is None:
            return (["You are not one of the players."], [], False)
        winner = 3 - player
        self.state = "ended"
        return ([], [f"{name} resigns; {self._player_name(winner)} wins.", *self._settle_ratings(winner)], True)

    def abort(self, conn, name: str) -> GameResult:
        if self.state == "ended":
            return (["Game has ended."], [], False)
        if self.who_of(conn) is None:
            return (["You are not one of the players."], [], False)
        if self.state == "playing":
            return (["A started game must be resigned, not aborted."], [], False)
        self.state = "ended"
        return ([], [f"{name} aborted the darkchess game."], True)

    def seats(self) -> list[str]:
        return [
            f"darkchess game state: {self.state}",
            f"Player 1: {self.first_name} side={self.player_side[1] or 'unknown'}",
            f"Player 2: {self.second_name or '(empty; /game join)'} side={self.player_side[2] or 'unknown'}",
            *self._rating_lines(),
        ]

    def show(self, conn=None) -> list[str]:
        viewer = self.who_of(conn)
        last = self._last if self._last is not None and (viewer is None or self._last_player != viewer) else None
        lines = [
            f"darkchess game ({self.state})  Player 1: {self.first_name}  Player 2: {self.second_name or 'empty'}",
            f"Sides: P1 {self.player_side[1] or 'unknown'}  P2 {self.player_side[2] or 'unknown'}",
            *self._rating_lines(),
        ]
        for row in range(1, DARKCHESS_ROWS + 1):
            tokens = []
            for col in range(1, DARKCHESS_COLS + 1):
                cell = self.board[_darkchess_index(row, col)]
                if cell is None:
                    tokens.append(".")
                elif cell not in self.face_up:
                    tokens.append("?")
                else:
                    piece = self._piece(cell)
                    tokens.append(f"{'+' if piece['side'] == 'red' else '-'}{piece['label']}")
                if last == (row, col):
                    tokens[-1] = "!" + tokens[-1]
            # Keep hidden and revealed pieces at the same width so terminal
            # columns stay aligned after a flip.
            lines.append(f"{row:>2}  " + " ".join(f"{token:>3}" for token in tokens))
        lines.append("Legend: + red  - black  ! opponent last  ? face-down  . empty")
        if self.state == "playing":
            lines.append(self._turn_line())
        return lines

    def on_player_leave(self, conn, name: str) -> GameResult:
        player = self.who_of(conn)
        if player is None:
            return ([], [], False)
        if conn is self.first_conn:
            self.first_conn = None
        if conn is self.second_conn:
            self.second_conn = None
        if self.state == "waiting":
            self.state = "ended"
            return ([], [f"{name} left; darkchess game cancelled."], True)
        if self.state == "playing":
            self.state = "ended"
            winner = 3 - player
            return ([], [f"{name} left; {self._player_name(winner)} wins.", *self._settle_ratings(winner)], True)
        return ([], [], False)


BATTLESHIP_SIZE = 10
_BATTLESHIP_FLEET = {
    "carrier": 5,
    "battleship": 4,
    "cruiser": 3,
    "submarine": 3,
    "destroyer": 2,
}


class BattleshipGame:
    """Two-player Battleship with private fleet layouts."""

    name = "battleship"
    first_seat_desc = "Player 1"
    second_seat_desc = "Player 2"
    send_view_on_move = True

    def __init__(
        self,
        first_conn,
        first_name: str,
        *,
        rating_store: Optional[GameRatingStore] = None,
    ) -> None:
        self.first_conn = first_conn
        self.first_name = first_name
        self.second_conn = None
        self.second_name: Optional[str] = None
        self.rating_store = rating_store
        self.state = "waiting"
        self.turn = 1
        self.fleets: dict[int, dict[str, set[tuple[int, int]]]] = {1: {}, 2: {}}
        self.shots: dict[int, set[tuple[int, int]]] = {1: set(), 2: set()}
        self.hit_shots: dict[int, set[tuple[int, int]]] = {1: set(), 2: set()}
        self.incoming_hits: dict[int, set[tuple[int, int]]] = {1: set(), 2: set()}
        self.ready: set[int] = set()
        self._last: Optional[tuple[int, int]] = None
        self._last_player: Optional[int] = None
        self.join_blurb = "Waiting for another player to join with /game join."

    def who_of(self, conn) -> Optional[int]:
        if conn is self.first_conn:
            return 1
        if conn is self.second_conn:
            return 2
        return None

    def is_seated(self, conn) -> bool:
        return self.who_of(conn) is not None

    def _player_name(self, player: int) -> str:
        return self.first_name if player == 1 else self.second_name or "Player 2"

    def _rating_lines(self) -> list[str]:
        return _format_rating_lines(
            self.rating_store, self.name, [self.first_name, self.second_name]
        )

    def _settle_ratings(self, winner: int) -> list[str]:
        if not self.second_name:
            return []
        return _format_rating_result_lines(
            self.rating_store,
            self.name,
            self.first_name,
            self.second_name,
            1.0 if winner == 1 else 0.0,
            ranked=True,
        )

    def _all_ship_cells(self, player: int) -> set[tuple[int, int]]:
        return set().union(*(cells for cells in self.fleets[player].values())) if self.fleets[player] else set()

    def _ship_cells(self, row: int, col: int, length: int, orientation: str) -> set[tuple[int, int]]:
        dr, dc = (0, 1) if orientation == "h" else (1, 0)
        return {(row + dr * offset, col + dc * offset) for offset in range(length)}

    def _fleet_complete(self, player: int) -> bool:
        return set(self.fleets[player]) == set(_BATTLESHIP_FLEET)

    def _sunk_ship(self, player: int, cell: tuple[int, int]) -> Optional[str]:
        for name, cells in self.fleets[player].items():
            if cell in cells and cells <= self.incoming_hits[player]:
                return name
        return None

    def _finish(self, winner: int, reason: str) -> list[str]:
        self.state = "ended"
        return [
            f"{self._player_name(winner)} wins Battleship ({reason}).",
            *self._settle_ratings(winner),
        ]

    def try_join(self, conn, name: str) -> GameResult:
        if self.state == "ended":
            return (["Game ended; start a new Battleship game."], [], False)
        if conn is self.first_conn:
            return (["You are already Player 1."], [], False)
        if conn is self.second_conn:
            return (["You are already Player 2."], [], False)
        if self.second_conn is not None:
            return ([f"Player 2 seat is occupied by {self.second_name}."], [], False)
        self.second_conn = conn
        self.second_name = name
        self.state = "setup"
        return (
            [],
            [
                f"{name} joined Battleship; both players must place their fleet.",
                "Use /game move place <ship> <row> <col> <h|v>, then /game move ready.",
            ],
            False,
        )

    def try_move(self, conn, raw: str) -> GameResult:
        if self.state == "waiting":
            return (["Game has not started; wait for Player 2 to join."], [], False)
        if self.state == "ended":
            return (["Game has ended."], [], False)
        player = self.who_of(conn)
        if player is None:
            return (["You are not one of the players."], [], False)
        parts = raw.strip().lower().split()
        if not parts:
            return (["Usage: place <ship> <row> <col> <h|v>, ready, or fire <row> <col>."], [], False)

        if parts[0] == "place":
            if self.state != "setup":
                return (["Fleet placement is over."], [], False)
            if len(parts) != 5 or parts[1] not in _BATTLESHIP_FLEET or parts[4] not in {"h", "v"}:
                return (["Usage: place carrier|battleship|cruiser|submarine|destroyer row col h|v."], [], False)
            ship, row_raw, col_raw, orientation = parts[1:]
            if not row_raw.isdigit() or not col_raw.isdigit():
                return (["Ship coordinates must be numbers from 1 to 10."], [], False)
            if ship in self.fleets[player]:
                return ([f"You already placed the {ship}."], [], False)
            row, col = int(row_raw) - 1, int(col_raw) - 1
            cells = self._ship_cells(row, col, _BATTLESHIP_FLEET[ship], orientation)
            if any(r < 0 or r >= BATTLESHIP_SIZE or c < 0 or c >= BATTLESHIP_SIZE for r, c in cells):
                return (["The ship must fit inside the 10x10 board."], [], False)
            occupied = self._all_ship_cells(player)
            adjacent = {
                (r + dr, c + dc)
                for r, c in cells
                for dr in (-1, 0, 1)
                for dc in (-1, 0, 1)
                if dr or dc
            }
            if cells & occupied or adjacent & occupied:
                return (["Ships may not overlap or touch, including diagonally."], [], False)
            self.fleets[player][ship] = cells
            return ([], [f"{self._player_name(player)} placed {ship}."], False)

        if parts[0] == "ready":
            if self.state != "setup":
                return (["The game is already playing."], [], False)
            if not self._fleet_complete(player):
                return (["Place all five ships before ready."], [], False)
            self.ready.add(player)
            if self.ready != {1, 2}:
                return ([], [f"{self._player_name(player)} is ready; waiting for the other fleet."], False)
            self.state = "playing"
            self.turn = 1
            return ([], ["Both fleets are ready. Battleship begins.", f"Turn: {self._player_name(self.turn)}"], False)

        if parts[0] != "fire" or len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
            return (["Usage: fire <row> <col> (1-10)."], [], False)
        if self.state != "playing":
            return (["Both players must be ready before firing."], [], False)
        if player != self.turn:
            return (["It is not your turn."], [], False)
        row, col = int(parts[1]) - 1, int(parts[2]) - 1
        if not (0 <= row < BATTLESHIP_SIZE and 0 <= col < BATTLESHIP_SIZE):
            return (["Firing coordinates must be from 1 to 10."], [], False)
        shot = (row, col)
        if shot in self.shots[player]:
            return (["You already fired at that coordinate."], [], False)
        self.shots[player].add(shot)
        opponent = 3 - player
        target_ship = next((name for name, cells in self.fleets[opponent].items() if shot in cells), None)
        lines = [f"{self._player_name(player)} fires at ({row + 1}, {col + 1}): {'HIT' if target_ship else 'MISS'}." ]
        self._last = (row, col)
        self._last_player = player
        if target_ship:
            self.hit_shots[player].add(shot)
            self.incoming_hits[opponent].add(shot)
            sunk = self._sunk_ship(opponent, shot)
            if sunk:
                lines.append(f"Sunk: {sunk}.")
            if self._all_ship_cells(opponent) <= self.incoming_hits[opponent]:
                lines.extend(self._finish(player, "all enemy ships sunk"))
                return ([], lines, True)
        self.turn = opponent
        lines.append(f"Turn: {self._player_name(self.turn)}")
        return ([], lines, False)

    def resign(self, conn, name: str) -> GameResult:
        if self.state not in {"setup", "playing"}:
            return (["Game has not started or has already ended."], [], False)
        player = self.who_of(conn)
        if player is None:
            return (["You are not one of the players."], [], False)
        winner = 3 - player
        self.state = "ended"
        return ([], [f"{name} resigns; {self._player_name(winner)} wins.", *self._settle_ratings(winner)], True)

    def abort(self, conn, name: str) -> GameResult:
        if self.state == "ended":
            return (["Game has ended."], [], False)
        if self.who_of(conn) is None:
            return (["You are not one of the players."], [], False)
        if self.state == "playing":
            return (["A started game must be resigned, not aborted."], [], False)
        self.state = "ended"
        return ([], [f"{name} aborted the Battleship game."], True)

    def seats(self) -> list[str]:
        return [
            f"battleship game state: {self.state}",
            f"Player 1: {self.first_name} {'ready' if 1 in self.ready else 'not ready'}",
            f"Player 2: {self.second_name or '(empty; /game join)'} {'ready' if 2 in self.ready else 'not ready'}",
            *self._rating_lines(),
        ]

    def _render_grid(self, player: Optional[int], opponent: Optional[int]) -> list[str]:
        own_cells = self._all_ship_cells(player) if player else set()
        own_hits = self.incoming_hits[player] if player else set()
        fired = self.shots[player] if player else set()
        hit = self.hit_shots[player] if player else set()
        opponent_last = self._last if player is not None and self._last_player not in {None, player} else None
        lines = []
        for row in range(BATTLESHIP_SIZE):
            own_tokens = []
            enemy_tokens = []
            for col in range(BATTLESHIP_SIZE):
                cell = (row, col)
                own_token = "X" if cell in own_hits else "S" if cell in own_cells else "."
                if cell == opponent_last:
                    own_token = "!" + own_token
                own_tokens.append(own_token)
                enemy_tokens.append("X" if cell in hit else "o" if cell in fired else "?")
            lines.append(
                f"{row + 1:>2} "
                + " ".join(f"{token:>2}" for token in own_tokens)
                + "    "
                + " ".join(f"{token:>2}" for token in enemy_tokens)
            )
        return lines

    def show(self, conn=None) -> list[str]:
        player = self.who_of(conn)
        opponent = 3 - player if player else None
        lines = [
            f"battleship game ({self.state})  Player 1: {self.first_name}  Player 2: {self.second_name or 'empty'}",
            "Own fleet / opponent waters (S=ship, X=hit, o=miss, ?=unknown).",
            *self._rating_lines(),
        ]
        lines.extend(self._render_grid(player, opponent))
        if self.state == "playing":
            lines.append(f"Turn: {self._player_name(self.turn)}")
        return lines

    def on_player_leave(self, conn, name: str) -> GameResult:
        player = self.who_of(conn)
        if player is None:
            return ([], [], False)
        if conn is self.first_conn:
            self.first_conn = None
        if conn is self.second_conn:
            self.second_conn = None
        if self.state == "waiting":
            self.state = "ended"
            return ([], [f"{name} left; Battleship game cancelled."], True)
        if self.state in {"setup", "playing"}:
            self.state = "ended"
            winner = 3 - player
            return ([], [f"{name} left; {self._player_name(winner)} wins.", *self._settle_ratings(winner)], True)
        return ([], [], False)


JUNQI_ROWS = 12
JUNQI_COLS = 5
_JUNQI_PIECE_COUNTS = {
    "flag": 1,
    "commander": 1,
    "army": 1,
    "division": 2,
    "brigade": 2,
    "regiment": 2,
    "battalion": 2,
    "company": 3,
    "platoon": 3,
    "engineer": 3,
    "mine": 3,
    "bomb": 2,
}
_JUNQI_PIECE_CODES = {
    "flag": "F",
    "commander": "C",
    "army": "A",
    "division": "D",
    "brigade": "B",
    "regiment": "R",
    "battalion": "T",
    "company": "N",
    "platoon": "P",
    "engineer": "E",
    "mine": "M",
    "bomb": "O",
}
_JUNQI_RANKS = {
    "commander": 10,
    "army": 9,
    "division": 8,
    "brigade": 7,
    "regiment": 6,
    "battalion": 5,
    "company": 4,
    "platoon": 3,
    "engineer": 2,
}
_JUNQI_CAMPS = {
    (0, 1), (0, 3), (1, 2),
    (4, 1), (4, 3), (5, 2),
    (6, 2), (7, 1), (7, 3),
    (10, 1), (10, 3), (11, 2),
}
_JUNQI_RAIL_ROWS = {0, 4, 5, 7, 11}
_JUNQI_RAIL_COLS = {0, 2, 4}


class JunqiGame:
    """Two-player Chinese Army Chess with private piece identities."""

    name = "junqi"
    first_seat_desc = "Red"
    second_seat_desc = "Blue"
    send_view_on_move = True

    def __init__(
        self,
        first_conn,
        first_name: str,
        *,
        rating_store: Optional[GameRatingStore] = None,
    ) -> None:
        self.first_conn = first_conn
        self.first_name = first_name
        self.second_conn = None
        self.second_name: Optional[str] = None
        self.rating_store = rating_store
        self.state = "waiting"
        self.turn = 1
        self.board: list[list[Optional[dict[str, object]]]] = [
            [None] * JUNQI_COLS for _ in range(JUNQI_ROWS)
        ]
        self.ready: set[int] = set()
        self._last: Optional[tuple[tuple[int, int], tuple[int, int]]] = None
        self._last_player: Optional[int] = None
        self.join_blurb = "Waiting for another player to join with /game join."

    def who_of(self, conn) -> Optional[int]:
        if conn is self.first_conn:
            return 1
        if conn is self.second_conn:
            return 2
        return None

    def is_seated(self, conn) -> bool:
        return self.who_of(conn) is not None

    def _player_name(self, player: int) -> str:
        return self.first_name if player == 1 else self.second_name or "Blue"

    def _rating_lines(self) -> list[str]:
        return _format_rating_lines(
            self.rating_store, self.name, [self.first_name, self.second_name]
        )

    def _settle_ratings(self, winner: int) -> list[str]:
        if not self.second_name:
            return []
        return _format_rating_result_lines(
            self.rating_store,
            self.name,
            self.first_name,
            self.second_name,
            1.0 if winner == 1 else 0.0,
            ranked=True,
        )

    def _side_rows(self, player: int) -> range:
        return range(0, 5) if player == 1 else range(7, 12)

    def _in_bounds(self, row: int, col: int) -> bool:
        return 0 <= row < JUNQI_ROWS and 0 <= col < JUNQI_COLS

    def _parse_position(self, raw_row: str, raw_col: str) -> Optional[tuple[int, int]]:
        if not raw_row.isdigit() or not raw_col.isdigit():
            return None
        row, col = int(raw_row) - 1, int(raw_col) - 1
        return (row, col) if self._in_bounds(row, col) else None

    def _side_complete(self, player: int) -> bool:
        counts = {kind: 0 for kind in _JUNQI_PIECE_COUNTS}
        for row in self.board:
            for piece in row:
                if piece and piece["side"] == player:
                    counts[str(piece["kind"])] += 1
        return counts == _JUNQI_PIECE_COUNTS

    def _side_piece_count(self, player: int, kind: str) -> int:
        return sum(
            1
            for row in self.board
            for piece in row
            if piece and piece["side"] == player and piece["kind"] == kind
        )

    def _has_flag(self, player: int) -> bool:
        return any(
            piece and piece["side"] == player and piece["kind"] == "flag"
            for row in self.board
            for piece in row
        )

    def _rail_neighbours(self, row: int, col: int) -> list[tuple[int, int]]:
        result = []
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nr, nc = row + dr, col + dc
            if not self._in_bounds(nr, nc):
                continue
            if (row == nr and row not in _JUNQI_RAIL_ROWS) or (
                col == nc and col not in _JUNQI_RAIL_COLS
            ):
                continue
            result.append((nr, nc))
        return result

    def _can_reach(self, source: tuple[int, int], target: tuple[int, int], piece: dict) -> bool:
        sr, sc = source
        tr, tc = target
        distance = abs(sr - tr) + abs(sc - tc)
        if distance == 1:
            return True
        if source in _JUNQI_CAMPS or target in _JUNQI_CAMPS:
            return distance == 1 or (abs(sr - tr) == 1 and abs(sc - tc) == 1)
        if sr == tr or sc == tc:
            step_r = 0 if sr == tr else (1 if tr > sr else -1)
            step_c = 0 if sc == tc else (1 if tc > sc else -1)
            row, col = sr + step_r, sc + step_c
            if any(self.board[row][col] for _ in [0] if (row, col) != (tr, tc)):
                return False
            while (row, col) != (tr, tc):
                if self.board[row][col] is not None:
                    return False
                row += step_r
                col += step_c
            return sr in _JUNQI_RAIL_ROWS if sr == tr else sc in _JUNQI_RAIL_COLS
        if piece["kind"] != "engineer":
            return False
        queue = [source]
        seen = {source}
        while queue:
            current = queue.pop(0)
            for neighbour in self._rail_neighbours(*current):
                if neighbour in seen or neighbour == target:
                    if neighbour == target:
                        return True
                    continue
                if self.board[neighbour[0]][neighbour[1]] is None:
                    seen.add(neighbour)
                    queue.append(neighbour)
        return False

    def _capture(self, attacker: dict, target: dict) -> tuple[str, Optional[int]]:
        attacker_kind = str(attacker["kind"])
        target_kind = str(target["kind"])
        target_side = int(target["side"])
        if target_kind == "flag":
            return "flag", int(attacker["side"])
        if attacker_kind == "bomb" or target_kind == "bomb":
            return "both", None
        if target_kind == "mine":
            return ("attacker", None) if attacker_kind == "engineer" else ("target", None)
        if _JUNQI_RANKS.get(attacker_kind, 0) >= _JUNQI_RANKS.get(target_kind, 0):
            return "attacker", None
        return "target", None

    def try_join(self, conn, name: str) -> GameResult:
        if self.state == "ended":
            return (["Game ended; start a new Junqi game."], [], False)
        if conn is self.first_conn:
            return (["You are already Red."], [], False)
        if conn is self.second_conn:
            return (["You are already Blue."], [], False)
        if self.second_conn is not None:
            return ([f"Blue seat is occupied by {self.second_name}."], [], False)
        self.second_conn = conn
        self.second_name = name
        self.state = "setup"
        return (
            [],
            [
                f"{name} joined Junqi; both players must place 25 pieces.",
                "Use /game move setup <piece> <row> <col>, then /game move ready.",
            ],
            False,
        )

    def try_move(self, conn, raw: str) -> GameResult:
        if self.state == "waiting":
            return (["Game has not started; wait for Blue to join."], [], False)
        if self.state == "ended":
            return (["Game has ended."], [], False)
        player = self.who_of(conn)
        if player is None:
            return (["You are not one of the players."], [], False)
        parts = raw.strip().lower().split()
        if not parts:
            return (["Usage: setup <piece> <row> <col>, ready, or move <fr> <fc> <tr> <tc>."], [], False)

        if parts[0] == "setup":
            if self.state != "setup":
                return (["Setup is over."], [], False)
            if len(parts) != 4 or parts[1] not in _JUNQI_PIECE_COUNTS:
                return (["Usage: setup flag|commander|... <row> <col>."], [], False)
            kind = parts[1]
            position = self._parse_position(parts[2], parts[3])
            if position is None or position[0] not in self._side_rows(player):
                return (["Your pieces must be placed in your five setup rows."], [], False)
            row, col = position
            if self.board[row][col] is not None:
                return (["That position is occupied."], [], False)
            if self._side_piece_count(player, kind) >= _JUNQI_PIECE_COUNTS[kind]:
                return ([f"You already placed all {kind} pieces."], [], False)
            if kind == "flag" and position not in (
                {(0, 1), (0, 3)} if player == 1 else {(11, 1), (11, 3)}
            ):
                return (["The flag must be placed in headquarters."], [], False)
            if kind == "mine" and row not in ((3, 4) if player == 1 else (7, 8)):
                return (["Mines must be placed in the last two rows of your camp."], [], False)
            if kind == "bomb" and row == (0 if player == 1 else 11):
                return (["Bombs cannot be placed in the first row."], [], False)
            self.board[row][col] = {"side": player, "kind": kind, "revealed": False}
            return ([], [f"{self._player_name(player)} placed {kind} at {row + 1},{col + 1}."], False)

        if parts[0] == "ready":
            if self.state != "setup":
                return (["The game is already playing."], [], False)
            if not self._side_complete(player):
                return (["Place exactly all 25 pieces before ready."], [], False)
            self.ready.add(player)
            if self.ready != {1, 2}:
                return ([], [f"{self._player_name(player)} is ready; waiting for the other army."], False)
            self.state = "playing"
            self.turn = 1
            return ([], ["Both armies are ready. Junqi begins.", f"Turn: {self._player_name(self.turn)}"], False)

        if parts[0] != "move" or len(parts) != 5:
            return (["Usage: move <from row> <from col> <to row> <to col>."], [], False)
        if self.state != "playing":
            return (["Both players must be ready before moving."], [], False)
        if player != self.turn:
            return (["It is not your turn."], [], False)
        source = self._parse_position(parts[1], parts[2])
        target = self._parse_position(parts[3], parts[4])
        if source is None or target is None or source == target:
            return (["Coordinates must be two different board positions from 1-based rows and columns."], [], False)
        attacker = self.board[source[0]][source[1]]
        target_piece = self.board[target[0]][target[1]]
        if attacker is None or attacker["side"] != player:
            return (["起点无己方棋子。"], [], False)
        if attacker["kind"] == "flag" or attacker["kind"] == "mine":
            return (["Flags and mines cannot move."], [], False)
        if target_piece is not None and target_piece["side"] == player:
            return (["You cannot capture your own piece."], [], False)
        if not self._can_reach(source, target, attacker):
            return (["That piece cannot reach the destination."], [], False)

        message = f"{self._player_name(player)} moved {source[0] + 1},{source[1] + 1} to {target[0] + 1},{target[1] + 1}."
        if target_piece is not None:
            attacker["revealed"] = True
            target_piece["revealed"] = True
            result, winner = self._capture(attacker, target_piece)
            if result == "flag":
                self.board[source[0]][source[1]] = None
                self.board[target[0]][target[1]] = attacker
                self._last = (source, target)
                self._last_player = player
                self.state = "ended"
                return ([], [message, f"{self._player_name(winner or player)} captured the flag and wins.", *self._settle_ratings(winner or player)], True)
            if result == "both":
                self.board[source[0]][source[1]] = None
                self.board[target[0]][target[1]] = None
                message += " Bombs exploded; both pieces were removed."
            elif result == "attacker":
                self.board[source[0]][source[1]] = None
                self.board[target[0]][target[1]] = attacker
                message += " Capture succeeded."
            else:
                self.board[source[0]][source[1]] = None
                message += " The attacker was lost."
        else:
            self.board[source[0]][source[1]] = None
            self.board[target[0]][target[1]] = attacker
        opponent = 3 - player
        if not self._has_flag(opponent) or not any(
            piece and piece["side"] == opponent
            for row in self.board
            for piece in row
        ):
            self.state = "ended"
            return ([], [message, f"{self._player_name(player)} wins Junqi.", *self._settle_ratings(player)], True)
        self._last = (source, target)
        self._last_player = player
        self.turn = opponent
        return ([], [message, f"Turn: {self._player_name(self.turn)}"], False)

    def resign(self, conn, name: str) -> GameResult:
        if self.state not in {"setup", "playing"}:
            return (["Game has not started or has already ended."], [], False)
        player = self.who_of(conn)
        if player is None:
            return (["You are not one of the players."], [], False)
        winner = 3 - player
        self.state = "ended"
        return ([], [f"{name} resigns; {self._player_name(winner)} wins.", *self._settle_ratings(winner)], True)

    def abort(self, conn, name: str) -> GameResult:
        if self.state == "ended":
            return (["Game has ended."], [], False)
        if self.who_of(conn) is None:
            return (["You are not one of the players."], [], False)
        if self.state == "playing":
            return (["A started game must be resigned, not aborted."], [], False)
        self.state = "ended"
        return ([], [f"{name} aborted the Junqi game."], True)

    def seats(self) -> list[str]:
        return [
            f"junqi game state: {self.state}",
            f"Red: {self.first_name} {'ready' if 1 in self.ready else 'not ready'}",
            f"Blue: {self.second_name or '(empty; /game join)'} {'ready' if 2 in self.ready else 'not ready'}",
            *self._rating_lines(),
        ]

    def show(self, conn=None) -> list[str]:
        player = self.who_of(conn)
        show_last = self._last is not None and (player is None or self._last_player != player)
        lines = [
            f"junqi game ({self.state})  Red: {self.first_name}  Blue: {self.second_name or 'empty'}",
            "Your pieces are shown; opponent pieces remain hidden until revealed by capture.",
            "F flag C commander A army D division B brigade R regiment T battalion N company P platoon E engineer M mine O bomb.",
            *self._rating_lines(),
        ]
        for row in range(JUNQI_ROWS):
            tokens = []
            for col in range(JUNQI_COLS):
                piece = self.board[row][col]
                if piece is None:
                    token = "."
                elif player is not None and piece["side"] == player:
                    token = ("+" if player == 1 else "-") + _JUNQI_PIECE_CODES[str(piece["kind"])]
                elif piece.get("revealed"):
                    token = ("+" if piece["side"] == 1 else "-") + _JUNQI_PIECE_CODES[str(piece["kind"])]
                elif self.state == "setup":
                    token = "?"
                else:
                    token = "?"
                if show_last and self._last and (row, col) in self._last:
                    token = "!" + token
                tokens.append(token)
            lines.append(f"{row + 1:>2} " + " ".join(f"{token:>3}" for token in tokens))
        lines.append("Legend: + red  - blue  ! opponent last  ? hidden  . empty")
        if self.state == "playing":
            lines.append(f"Turn: {self._player_name(self.turn)}")
        return lines

    def on_player_leave(self, conn, name: str) -> GameResult:
        player = self.who_of(conn)
        if player is None:
            return ([], [], False)
        if conn is self.first_conn:
            self.first_conn = None
        if conn is self.second_conn:
            self.second_conn = None
        if self.state == "waiting":
            self.state = "ended"
            return ([], [f"{name} left; Junqi game cancelled."], True)
        if self.state in {"setup", "playing"}:
            self.state = "ended"
            winner = 3 - player
            return ([], [f"{name} left; {self._player_name(winner)} wins.", *self._settle_ratings(winner)], True)
        return ([], [], False)


XIANGQI_ROWS = 10
XIANGQI_COLS = 9
_XQ_RED = 1
_XQ_BLACK = -1
_XQ_K, _XQ_A, _XQ_B, _XQ_N, _XQ_R, _XQ_C, _XQ_P = 1, 2, 3, 4, 5, 6, 7
_XQ_SYM_RED = ("", "帅", "仕", "相", "马", "车", "炮", "兵")
_XQ_SYM_BLACK = ("", "将", "士", "象", "马", "车", "炮", "卒")
_XQ_CN_FILE = "一二三四五六七八九"  # 红方纵线：右=一 … 左=九
_XQ_CN_RANK = "一二三四五六七八九"  # 进/退步数 1～9
_XQ_CHAR_TO_TYPE = {
    "帅": _XQ_K,
    "将": _XQ_K,
    "仕": _XQ_A,
    "士": _XQ_A,
    "相": _XQ_B,
    "象": _XQ_B,
    "马": _XQ_N,
    "车": _XQ_R,
    "炮": _XQ_C,
    "兵": _XQ_P,
    "卒": _XQ_P,
}
_XQ_LINE_PIECES = frozenset({_XQ_R, _XQ_C, _XQ_P, _XQ_K})
_XQ_NOTATION_RE = re.compile(
    r"^(?:(前|后))?"
    r"([车马炮相仕帅将士象兵卒])"
    r"([一二三四五六七八九1-9])?"
    r"([进退平])"
    r"([一二三四五六七八九1-9]+)$"
)
_XQ_CELL_W = 4  # 每格显示宽度；+车 / -车 / !车（上一步）适配 SSH 等宽字体
_XQ_MARK_RE = re.compile(r"\{\{/?[RB]\}\}")


def _xq_disp_width(text: str) -> int:
    plain = _XQ_MARK_RE.sub("", text)
    w = 0
    for ch in plain:
        if unicodedata.east_asian_width(ch) in ("F", "W"):
            w += 2
        else:
            w += 1
    return w


def _xq_pad(text: str, width: int) -> str:
    pad = width - _xq_disp_width(text)
    return text if pad <= 0 else text + (" " * pad)


def _xq_cell_body(cell: int, *, highlight: bool) -> str:
    if cell == 0:
        return "*" if highlight else "·"
    pt = _xq_piece_type(cell)
    side = _xq_piece_side(cell)
    assert side is not None
    sym = _XQ_SYM_RED[pt] if side == _XQ_RED else _XQ_SYM_BLACK[pt]
    if highlight:
        return "!" + sym
    return ("+" if side == _XQ_RED else "-") + sym


def _xq_col_label(col: int, side: int) -> str:
    """Traditional file: red 九..一 (right→left); black 1..9 (left→right)."""
    if side == _XQ_RED:
        return _XQ_CN_FILE[8 - col]
    return str(col + 1)


def _xq_digit_token(token: str) -> Optional[int]:
    t = token.strip()
    if not t:
        return None
    if t.isdigit():
        n = int(t)
    elif len(t) == 1 and t in _XQ_CN_FILE:
        n = _XQ_CN_FILE.index(t) + 1
    else:
        return None
    if 1 <= n <= 9:
        return n
    return None


def _xq_col_from_token(token: str, side: int) -> Optional[int]:
    n = _xq_digit_token(token)
    if n is None:
        return None
    if side == _XQ_RED:
        return XIANGQI_COLS - n
    return n - 1


def _xq_file_num(col: int, side: int) -> int:
    if side == _XQ_RED:
        return XIANGQI_COLS - col
    return col + 1


def _xq_rank_label(steps: int, side: int) -> str:
    if side == _XQ_RED:
        return _XQ_CN_RANK[steps - 1]
    return str(steps)


def _xq_is_forward(side: int, fr: int, tr: int) -> bool:
    if side == _XQ_RED:
        return tr < fr
    return tr > fr


def _xq_piece_char(pt: int, side: int) -> str:
    if pt == _XQ_K:
        return "帅" if side == _XQ_RED else "将"
    if pt == _XQ_A:
        return "仕" if side == _XQ_RED else "士"
    if pt == _XQ_B:
        return "相" if side == _XQ_RED else "象"
    if pt == _XQ_P:
        return "兵" if side == _XQ_RED else "卒"
    return { _XQ_N: "马", _XQ_R: "车", _XQ_C: "炮" }[pt]


def _xq_front_row(side: int, r: int, r2: int) -> int:
    """Row index of the '前' piece when two same-type pieces share a file."""
    if side == _XQ_RED:
        return min(r, r2)
    return max(r, r2)


def _xq_match_notation_move(
    board: list[list[int]],
    side: int,
    *,
    pt: int,
    prefix: Optional[str],
    from_file_tok: Optional[str],
    dir_char: str,
    dest_tok: str,
) -> Optional[tuple[int, int, int, int]]:
    dest_num = _xq_digit_token(dest_tok)
    if dest_num is None:
        return None

    matches: list[tuple[int, int, int, int]] = []
    for fr, fc, tr, tc in _xq_legal_moves(board, side):
        if _xq_piece_type(board[fr][fc]) != pt:
            continue
        if from_file_tok is not None:
            want = _xq_digit_token(from_file_tok)
            if want is None or _xq_file_num(fc, side) != want:
                continue

        same_file: dict[int, list[int]] = {}
        for r in range(XIANGQI_ROWS):
            cell = board[r][fc]
            if _xq_piece_side(cell) == side and _xq_piece_type(cell) == pt:
                same_file.setdefault(fc, []).append(r)
        rows_on_file = same_file.get(fc, [fr])
        if prefix and len(rows_on_file) >= 2:
            other = [x for x in rows_on_file if x != fr][0]
            front = _xq_front_row(side, fr, other)
            if prefix == "前" and fr != front:
                continue
            if prefix == "后" and fr == front:
                continue

        dr, dc = tr - fr, tc - fc
        if pt in _XQ_LINE_PIECES:
            if dir_char == "平":
                if dr != 0 or dc == 0:
                    continue
                if _xq_file_num(tc, side) != dest_num:
                    continue
            elif dir_char == "进":
                if not _xq_is_forward(side, fr, tr):
                    continue
                if dc == 0:
                    if abs(dr) != dest_num:
                        continue
                elif pt == _XQ_P and _xq_file_num(tc, side) != dest_num:
                    continue
                elif dc != 0:
                    continue
            elif dir_char == "退":
                if _xq_is_forward(side, fr, tr):
                    continue
                if dc == 0:
                    if abs(dr) != dest_num:
                        continue
                elif pt == _XQ_P and _xq_file_num(tc, side) != dest_num:
                    continue
                elif dc != 0:
                    continue
            else:
                continue
        else:
            if dir_char == "平":
                continue
            if dir_char == "进":
                if not _xq_is_forward(side, fr, tr):
                    continue
            elif dir_char == "退":
                if _xq_is_forward(side, fr, tr):
                    continue
            else:
                continue
            if _xq_file_num(tc, side) != dest_num:
                continue

        matches.append((fr, fc, tr, tc))

    if len(matches) == 1:
        return matches[0]
    return None


def _xq_parse_notation(
    raw: str, side: int, board: list[list[int]]
) -> Optional[tuple[int, int, int, int]]:
    s = raw.strip().replace(" ", "")
    m = _XQ_NOTATION_RE.match(s)
    if not m:
        return None
    prefix, pchar, ftoken, dir_char, dest_tok = m.groups()
    pt = _XQ_CHAR_TO_TYPE.get(pchar)
    if pt is None:
        return None
    return _xq_match_notation_move(
        board,
        side,
        pt=pt,
        prefix=prefix or None,
        from_file_tok=ftoken or None,
        dir_char=dir_char,
        dest_tok=dest_tok,
    )


def _xq_format_notation(
    board: list[list[int]], fr: int, fc: int, tr: int, tc: int, side: int
) -> str:
    pt = _xq_piece_type(board[fr][fc])
    sym = _xq_piece_char(pt, side)
    from_file = _xq_col_label(fc, side)

    same_file_rows = [
        r
        for r in range(XIANGQI_ROWS)
        if _xq_piece_side(board[r][fc]) == side and _xq_piece_type(board[r][fc]) == pt
    ]
    prefix = ""
    if len(same_file_rows) >= 2:
        front = _xq_front_row(side, same_file_rows[0], same_file_rows[1])
        prefix = "前" if fr == front else "后"

    dr, dc = tr - fr, tc - fc
    if pt in _XQ_LINE_PIECES:
        if dc == 0:
            steps = abs(dr)
            if _xq_is_forward(side, fr, tr):
                dir_char = "进"
            else:
                dir_char = "退"
            dest = _xq_rank_label(steps, side)
        else:
            dir_char = "平"
            dest = _xq_col_label(tc, side)
    else:
        dir_char = "进" if _xq_is_forward(side, fr, tr) else "退"
        dest = _xq_col_label(tc, side)

    return f"{prefix}{sym}{from_file}{dir_char}{dest}"


def _xq_pos_label(row: int, col: int, side: int) -> str:
    return f"{_xq_col_label(col, side)}{row + 1}"


def _xq_format_cell(cell: int, *, highlight: bool) -> str:
    return _xq_pad(_xq_cell_body(cell, highlight=highlight), _XQ_CELL_W)


def _xq_file_header_for_cols(col_ix: list[int], side: int) -> str:
    """表头与同行格子顺序一致：须传入与棋盘相同的 col_ix。"""
    labels = [_xq_col_label(c, side) for c in col_ix]
    return "   " + "".join(_xq_pad(lb, _XQ_CELL_W) for lb in labels)


def _xq_piece_side(cell: int) -> Optional[int]:
    if cell > 0:
        return _XQ_RED
    if cell < 0:
        return _XQ_BLACK
    return None


def _xq_piece_type(cell: int) -> int:
    return abs(cell)


def _xq_in_palace(row: int, col: int, side: int) -> bool:
    if not (3 <= col <= 5):
        return False
    if side == _XQ_RED:
        return 7 <= row <= 9
    return 0 <= row <= 2


def _xq_king_pos(board: list[list[int]], side: int) -> Optional[tuple[int, int]]:
    target = side * _XQ_K
    for r in range(XIANGQI_ROWS):
        for c in range(XIANGQI_COLS):
            if board[r][c] == target:
                return (r, c)
    return None


def _xq_flying_kings(board: list[list[int]]) -> bool:
    rk = _xq_king_pos(board, _XQ_RED)
    bk = _xq_king_pos(board, _XQ_BLACK)
    if rk is None or bk is None:
        return False
    if rk[1] != bk[1]:
        return False
    lo, hi = sorted((rk[0], bk[0]))
    for r in range(lo + 1, hi):
        if board[r][rk[1]] != 0:
            return False
    return True


def _xq_initial_board() -> list[list[int]]:
    back = [_XQ_R, _XQ_N, _XQ_B, _XQ_A, _XQ_K, _XQ_A, _XQ_B, _XQ_N, _XQ_R]
    board = [[0 for _ in range(XIANGQI_COLS)] for _ in range(XIANGQI_ROWS)]
    for c, p in enumerate(back):
        board[0][c] = -p
        board[9][c] = p
    board[2][1] = board[2][7] = -_XQ_C
    board[7][1] = board[7][7] = _XQ_C
    for c in range(0, XIANGQI_COLS, 2):
        board[3][c] = -_XQ_P
        board[6][c] = _XQ_P
    return board


def _xq_copy(board: list[list[int]]) -> list[list[int]]:
    return [row[:] for row in board]


def _xq_apply(board: list[list[int]], fr: int, fc: int, tr: int, tc: int) -> None:
    board[tr][tc] = board[fr][fc]
    board[fr][fc] = 0


def _xq_gen_pseudo(
    board: list[list[int]], row: int, col: int, *, captures_only: bool = False
) -> list[tuple[int, int]]:
    cell = board[row][col]
    if cell == 0:
        return []
    side = _xq_piece_side(cell)
    assert side is not None
    pt = _xq_piece_type(cell)
    out: list[tuple[int, int]] = []

    def add(tr: int, tc: int) -> None:
        if not (0 <= tr < XIANGQI_ROWS and 0 <= tc < XIANGQI_COLS):
            return
        target = board[tr][tc]
        if target != 0 and _xq_piece_side(target) == side:
            return
        if captures_only and target == 0:
            return
        out.append((tr, tc))

    if pt == _XQ_K:
        for dr, dc in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            tr, tc = row + dr, col + dc
            if _xq_in_palace(tr, tc, side):
                add(tr, tc)
    elif pt == _XQ_A:
        for dr, dc in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
            tr, tc = row + dr, col + dc
            if _xq_in_palace(tr, tc, side):
                add(tr, tc)
    elif pt == _XQ_B:
        for dr, dc in ((2, 2), (2, -2), (-2, 2), (-2, -2)):
            tr, tc = row + dr, col + dc
            eye_r, eye_c = row + dr // 2, col + dc // 2
            if not (0 <= tr < XIANGQI_ROWS and 0 <= tc < XIANGQI_COLS):
                continue
            if board[eye_r][eye_c] != 0:
                continue
            if side == _XQ_RED and tr < 5:
                continue
            if side == _XQ_BLACK and tr > 4:
                continue
            add(tr, tc)
    elif pt == _XQ_N:
        legs = (
            (-2, -1, -1, 0),
            (-2, 1, -1, 0),
            (2, -1, 1, 0),
            (2, 1, 1, 0),
            (-1, -2, 0, -1),
            (-1, 2, 0, 1),
            (1, -2, 0, -1),
            (1, 2, 0, 1),
        )
        for dr, dc, lr, lc in legs:
            lr_r, lc_c = row + lr, col + lc
            if not (0 <= lr_r < XIANGQI_ROWS and 0 <= lc_c < XIANGQI_COLS):
                continue
            if board[lr_r][lc_c] != 0:
                continue
            add(row + dr, col + dc)
    elif pt == _XQ_R:
        for dr, dc in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            r, c = row + dr, col + dc
            while 0 <= r < XIANGQI_ROWS and 0 <= c < XIANGQI_COLS:
                if board[r][c] == 0:
                    if not captures_only:
                        out.append((r, c))
                else:
                    if _xq_piece_side(board[r][c]) != side:
                        out.append((r, c))
                    break
                r += dr
                c += dc
    elif pt == _XQ_C:
        for dr, dc in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            r, c = row + dr, col + dc
            jumped = False
            while 0 <= r < XIANGQI_ROWS and 0 <= c < XIANGQI_COLS:
                if board[r][c] == 0:
                    if not captures_only and not jumped:
                        out.append((r, c))
                elif not jumped:
                    jumped = True
                else:
                    if _xq_piece_side(board[r][c]) != side:
                        out.append((r, c))
                    break
                r += dr
                c += dc
    elif pt == _XQ_P:
        if side == _XQ_RED:
            add(row - 1, col)
            if row <= 4:
                add(row, col - 1)
                add(row, col + 1)
        else:
            add(row + 1, col)
            if row >= 5:
                add(row, col - 1)
                add(row, col + 1)
    return out


def _xq_is_attacked(
    board: list[list[int]], row: int, col: int, by_side: int
) -> bool:
    for r in range(XIANGQI_ROWS):
        for c in range(XIANGQI_COLS):
            if _xq_piece_side(board[r][c]) != by_side:
                continue
            if (row, col) in _xq_gen_pseudo(board, r, c, captures_only=True):
                return True
            if _xq_piece_type(board[r][c]) == _XQ_K:
                # 将帅对脸时，同列无子隔的对方将/帅视为被“照面”。
                if (
                    board[row][col] == -by_side * _XQ_K
                    and c == col
                    and abs(r - row) > 1
                ):
                    lo, hi = sorted((r, row))
                    blocked = False
                    for rr in range(lo + 1, hi):
                        if board[rr][col] != 0:
                            blocked = True
                            break
                    if not blocked:
                        return True
    return False


def _xq_legal_moves(board: list[list[int]], side: int) -> list[tuple[int, int, int, int]]:
    moves: list[tuple[int, int, int, int]] = []
    for r in range(XIANGQI_ROWS):
        for c in range(XIANGQI_COLS):
            if _xq_piece_side(board[r][c]) != side:
                continue
            for tr, tc in _xq_gen_pseudo(board, r, c):
                nb = _xq_copy(board)
                _xq_apply(nb, r, c, tr, tc)
                if _xq_flying_kings(nb):
                    continue
                kpos = _xq_king_pos(nb, side)
                if kpos is not None and _xq_is_attacked(
                    nb, kpos[0], kpos[1], -side
                ):
                    continue
                moves.append((r, c, tr, tc))
    return moves


def _xq_parse_coord_move(
    raw: str, side: int
) -> Optional[tuple[int, int, int, int]]:
    t = raw.strip().replace(",", " ")
    parts = t.split()
    if len(parts) != 4:
        return None
    try:
        fr = int(parts[0])
        tr = int(parts[2])
    except ValueError:
        return None
    fc = _xq_col_from_token(parts[1], side)
    tc = _xq_col_from_token(parts[3], side)
    if fc is None or tc is None:
        return None
    if not (1 <= fr <= XIANGQI_ROWS and 1 <= tr <= XIANGQI_ROWS):
        return None
    return (fr - 1, fc, tr - 1, tc)


def _xq_parse_absolute_coord_move(raw: str) -> Optional[tuple[int, int, int, int]]:
    t = raw.strip().lower().replace(",", " ")
    if not t.startswith(("coord ", "坐标 ")):
        return None
    parts = t.split()
    if len(parts) != 5:
        return None
    try:
        fr, fc, tr, tc = (int(x) for x in parts[1:])
    except ValueError:
        return None
    if not (
        1 <= fr <= XIANGQI_ROWS
        and 1 <= tr <= XIANGQI_ROWS
        and 1 <= fc <= XIANGQI_COLS
        and 1 <= tc <= XIANGQI_COLS
    ):
        return None
    return (fr - 1, fc - 1, tr - 1, tc - 1)


def _xq_parse_move(
    raw: str, side: int, board: list[list[int]]
) -> Optional[tuple[int, int, int, int]]:
    t = raw.strip()
    if not t:
        return None
    coord_abs = _xq_parse_absolute_coord_move(t)
    if coord_abs is not None:
        return coord_abs
    coord = _xq_parse_coord_move(t, side)
    if coord is not None:
        return coord
    return _xq_parse_notation(t, side, board)


def _xq_position_key(
    board: list[list[int]], turn: int
) -> tuple[tuple[tuple[int, ...], ...], int]:
    return (tuple(tuple(row) for row in board), turn)


def _xq_in_check(board: list[list[int]], defender: int) -> bool:
    k = _xq_king_pos(board, defender)
    return k is not None and _xq_is_attacked(board, k[0], k[1], -defender)


def _xq_chased_squares(board: list[list[int]], attacker: int) -> frozenset[tuple[int, int]]:
    out: set[tuple[int, int]] = set()
    for r in range(XIANGQI_ROWS):
        for c in range(XIANGQI_COLS):
            cell = board[r][c]
            if cell == 0 or _xq_piece_side(cell) == attacker:
                continue
            if _xq_piece_type(cell) == _XQ_K:
                continue
            if _xq_is_attacked(board, r, c, attacker):
                out.add((r, c))
    return frozenset(out)


def _xq_analyze_ply(
    board_before: list[list[int]],
    board_after: list[list[int]],
    mover: int,
) -> dict[str, object]:
    opp = -mover
    check = _xq_in_check(board_after, opp)
    chase_targets: frozenset[tuple[int, int]] = frozenset()
    if not check:
        chase_targets = _xq_chased_squares(board_after, mover)
    return {"check": check, "chase_targets": chase_targets}


def _xq_is_perpetual_chase(side_plies: list[dict]) -> bool:
    if not side_plies:
        return False
    if any(p["check"] or not p["chase_targets"] for p in side_plies):
        return False
    tracked: Optional[tuple[int, int]] = None
    for ply in side_plies:
        bb = ply["board_before"]
        mover = ply["mover"]
        if tracked is None:
            tracked = next(iter(ply["chase_targets"]))
            continue
        tr, tc = tracked
        if (
            bb[tr][tc] != 0
            and _xq_piece_side(bb[tr][tc]) == -mover
            and (tr, tc) in ply["chase_targets"]
        ):
            continue
        found = False
        for r, c in ply["chase_targets"]:
            if bb[r][c] != 0 and _xq_piece_side(bb[r][c]) == -mover:
                tracked = (r, c)
                found = True
                break
        if not found:
            return False
    return True


def _xq_classify_side_cycle(side_plies: list[dict]) -> str:
    if not side_plies:
        return "idle"
    if all(p["check"] for p in side_plies):
        return "perpetual_check"
    if _xq_is_perpetual_chase(side_plies):
        return "perpetual_chase"
    if all(not p["check"] and not p["chase_targets"] for p in side_plies):
        return "idle"
    return "mixed"


def _xq_side_plies_in_cycle(
    ply_log: list[dict], start_idx: int, end_idx: int, side: int
) -> list[dict]:
    return [
        ply_log[i]
        for i in range(start_idx + 1, end_idx + 1)
        if ply_log[i]["mover"] == side
    ]


def _xq_adjudicate_repetition(
    ply_log: list[dict], i_first: int, i_last: int
) -> tuple[str, float]:
    """Return (broadcast line, score_red) when a position repeats for the 3rd time."""
    red_plies = _xq_side_plies_in_cycle(ply_log, i_first, i_last, _XQ_RED)
    black_plies = _xq_side_plies_in_cycle(ply_log, i_first, i_last, _XQ_BLACK)
    rpat = _xq_classify_side_cycle(red_plies)
    bpat = _xq_classify_side_cycle(black_plies)

    def lose(side: int, reason: str) -> tuple[str, float]:
        color = "红" if side == _XQ_RED else "黑"
        score = 0.0 if side == _XQ_RED else 1.0
        return (f"对局结束：{color}方{reason}，三次循环局面不变着判负。", score)

    if rpat == "perpetual_check" and bpat != "perpetual_check":
        return lose(_XQ_RED, "长将")
    if bpat == "perpetual_check" and rpat != "perpetual_check":
        return lose(_XQ_BLACK, "长将")
    if rpat == "perpetual_check" and bpat == "perpetual_check":
        return ("对局结束：双方长将，和棋。", 0.5)
    if rpat == "perpetual_check" and bpat == "perpetual_chase":
        return lose(_XQ_RED, "长将")
    if bpat == "perpetual_check" and rpat == "perpetual_chase":
        return lose(_XQ_BLACK, "长将")
    if rpat == "perpetual_chase" and bpat == "idle":
        return lose(_XQ_RED, "长捉")
    if bpat == "perpetual_chase" and rpat == "idle":
        return lose(_XQ_BLACK, "长捉")
    if rpat == "perpetual_chase" and bpat == "perpetual_chase":
        return ("对局结束：双方长捉，和棋。", 0.5)
    if rpat == "idle" and bpat == "idle":
        return ("对局结束：循环局面双方无照打，和棋。", 0.5)
    if rpat in ("perpetual_check", "perpetual_chase") and bpat == "mixed":
        return lose(_XQ_RED, "长将" if rpat == "perpetual_check" else "长捉")
    if bpat in ("perpetual_check", "perpetual_chase") and rpat == "mixed":
        return lose(_XQ_BLACK, "长将" if bpat == "perpetual_check" else "长捉")
    return ("对局结束：循环局面双方须变着，和棋。", 0.5)


def _xq_repetition_verdict(ply_log: list[dict]) -> Optional[tuple[str, float]]:
    if not ply_log:
        return None
    key = ply_log[-1]["key"]
    indices = [i for i, p in enumerate(ply_log) if p["key"] == key]
    if len(indices) < 3:
        return None
    return _xq_adjudicate_repetition(ply_log, indices[0], indices[-1])


def _xq_record_ply(
    ply_log: list[dict],
    board_before: list[list[int]],
    board_after: list[list[int]],
    mover: int,
) -> Optional[tuple[str, float]]:
    info = _xq_analyze_ply(board_before, board_after, mover)
    ply_log.append(
        {
            "key": _xq_position_key(board_after, -mover),
            "mover": mover,
            "check": info["check"],
            "chase_targets": info["chase_targets"],
            "board_before": board_before,
        }
    )
    return _xq_repetition_verdict(ply_log)


def _xq_would_lose_on_repetition(
    board: list[list[int]],
    ply_log: list[dict],
    side: int,
    fr: int,
    fc: int,
    tr: int,
    tc: int,
) -> bool:
    board_before = _xq_copy(board)
    board_after = _xq_copy(board)
    _xq_apply(board_after, fr, fc, tr, tc)
    trial = list(ply_log)
    verdict = _xq_record_ply(trial, board_before, board_after, side)
    if verdict is None:
        return False
    _msg, score_red = verdict
    if side == _XQ_RED:
        return score_red == 0.0
    return score_red == 1.0


def _xq_move_label(
    board: list[list[int]], fr: int, fc: int, tr: int, tc: int, side: int
) -> str:
    return _xq_format_notation(board, fr, fc, tr, tc, side)


def _xq_render(
    board: list[list[int]],
    *,
    last_from: Optional[tuple[int, int]] = None,
    last_to: Optional[tuple[int, int]] = None,
    last_notation: Optional[str] = None,
    flip: bool = False,
) -> list[str]:
    hi: set[tuple[int, int]] = set()
    if last_from is not None:
        hi.add(last_from)
    if last_to is not None:
        hi.add(last_to)

    row_ix = list(range(XIANGQI_ROWS - 1, -1, -1)) if flip else list(range(XIANGQI_ROWS))
    col_ix = list(range(XIANGQI_COLS - 1, -1, -1)) if flip else list(range(XIANGQI_COLS))

    board_w = _xq_disp_width("   ") + XIANGQI_COLS * _XQ_CELL_W
    if flip:
        # 黑方视角：顶为对方（红）一…九，底为己方 9…1（屏幕从左到右，即卷轴从右往左读 1…9）
        top_hdr = _xq_file_header_for_cols(col_ix, _XQ_RED) + "  ← 红方纵线 一…九"
        bot_hdr = (
            _xq_file_header_for_cols(col_ix, _XQ_BLACK)
            + "  ← 黑方纵线 9…1（从右向左为 1～9）"
        )
    else:
        top_hdr = _xq_file_header_for_cols(col_ix, _XQ_BLACK) + "  ← 黑方 1～9"
        bot_hdr = (
            _xq_file_header_for_cols(col_ix, _XQ_RED)
            + "  ← 红方纵线 九…一（右为一）"
        )
    lines = [
        top_hdr,
        "  图例：+红  -黑  !上一步  ·空  （请用等宽字体）",
    ]
    if flip:
        lines.append("  （己方在下方）")
    for r in row_ix:
        cells = [
            _xq_format_cell(board[r][c], highlight=(r, c) in hi) for c in col_ix
        ]
        # 传统记谱不标横线号；与顶/底「   +纵线」表头同宽缩进以便对齐
        lines.append("   " + "".join(cells))
        # 河界在棋盘上介于内部第 4、5 行之间（对应纵坐标第 5、6 行）；须在画出上行后再插入，
        # 否则会变成夹在显示的第 6、7 行之间。
        if (flip and r == 5) or ((not flip) and r == 4):
            river = "楚河汉界"
            pad = max(0, board_w - _xq_disp_width(river))
            lines.append(" " * (pad // 2) + river)
    lines.append(bot_hdr)
    if last_notation:
        lines.append(f"  上一步：{last_notation}")
    return lines


class XiangqiGame(BoardUndoMixin):
    """Chinese chess (xiangqi). Creator = red (先手); joiner = black."""

    name = "xiangqi"
    first_seat_desc = "红方（先手）"
    second_seat_desc = "黑方"
    # 每步向双方私信最新棋盘，保证 Electron 工作台与终端局面同步。
    send_view_on_move = True

    def __init__(
        self,
        red_conn,
        red_name: str,
        *,
        rating_store: Optional[GameRatingStore] = None,
        ai_level: Optional[str] = None,
    ) -> None:
        self.board = _xq_initial_board()
        self.red_conn = red_conn
        self.red_name = red_name
        self.rating_store = rating_store
        self.ai_level = ai_level
        self.ai_name = _board_ai_name(ai_level) if ai_level else None
        self.black_conn = object() if ai_level else None
        self.black_name: Optional[str] = self.ai_name if ai_level else None
        self.state = "playing" if ai_level else "waiting"
        self._turn = _XQ_RED
        self._last_from: Optional[tuple[int, int]] = None
        self._last_to: Optional[tuple[int, int]] = None
        self._last_mover_side: Optional[int] = None
        self._last_notation: Optional[str] = None
        self._history: list[
            tuple[
                list[list[int]],
                int,
                Optional[tuple[int, int]],
                Optional[tuple[int, int]],
                Optional[int],
                Optional[str],
            ]
        ] = []
        self._xq_ply_log: list[dict] = []
        self.join_blurb = (
            f"{self.ai_name} 执黑，练习局立即开始；本局不计入持久化积分。"
            if ai_level
            else "等另一位玩家用 /game join 加入。"
        )
        self._undo_clear_pending()

    def __setstate__(self, state: dict) -> None:
        self.__dict__.update(state)
        self._ensure_compat_state()

    def _ensure_compat_state(self) -> None:
        """Keep active Xiangqi games saved by older builds compatible."""
        if not hasattr(self, "_xq_ply_log") or not isinstance(self._xq_ply_log, list):
            self._xq_ply_log = []
        elif any(not isinstance(item, dict) for item in self._xq_ply_log):
            self._xq_ply_log = []
        if not hasattr(self, "_history") or not isinstance(self._history, list):
            self._history = []
        if len(self._xq_ply_log) > len(self._history):
            self._xq_ply_log = self._xq_ply_log[: len(self._history)]

    def _undo_has_moves(self) -> bool:
        self._ensure_compat_state()
        return bool(self._history)

    def _undo_last_mover_conn(self):
        if self._last_mover_side is None:
            return None
        return self.red_conn if self._last_mover_side == _XQ_RED else self.black_conn

    def _undo_opponent_conn(self, conn):
        side = self._side_of(conn)
        if side is None:
            return None
        return self.black_conn if side == _XQ_RED else self.red_conn

    def _undo_player_name(self, conn) -> str:
        side = self._side_of(conn)
        if side == _XQ_RED:
            return self.red_name
        if side == _XQ_BLACK:
            return self.black_name or "黑方"
        return "?"

    def _undo_pop_last_move(self) -> bool:
        self._ensure_compat_state()
        if not self._history:
            return False
        self._history.pop()
        if self._xq_ply_log:
            self._xq_ply_log.pop()
        if self._history:
            snap = self._history[-1]
            self.board = [row[:] for row in snap[0]]
            self._turn = snap[1]
            self._last_from = snap[2]
            self._last_to = snap[3]
            self._last_mover_side = snap[4]
            self._last_notation = snap[5]
        else:
            self.board = _xq_initial_board()
            self._turn = _XQ_RED
            self._last_from = None
            self._last_to = None
            self._last_mover_side = None
            self._last_notation = None
            self._xq_ply_log.clear()
        return True

    def _xq_commit_move(
        self, side: int, fr: int, fc: int, tr: int, tc: int, label: str
    ) -> tuple[list[str], bool]:
        """Apply a half-move; return (broadcast lines, ended)."""
        self._ensure_compat_state()
        board_before = _xq_copy(self.board)
        captured = self.board[tr][tc]
        _xq_apply(self.board, fr, fc, tr, tc)
        self._last_from = (fr, fc)
        self._last_to = (tr, tc)
        self._last_mover_side = side
        self._last_notation = label
        self._turn = -side
        self._history.append(self._xq_snapshot())

        mover = self.red_name if side == _XQ_RED else self.black_name
        color = "红" if side == _XQ_RED else "黑"
        bcast = [f"{color}方 {mover} 走 {label}"]

        verdict = _xq_record_ply(self._xq_ply_log, board_before, self.board, side)
        if verdict is not None:
            self.state = "ended"
            msg, score_red = verdict
            bcast.append(msg)
            bcast.extend(self._settle_ratings(score_red))
            return bcast, True

        if captured != 0 and _xq_piece_type(captured) == _XQ_K:
            self.state = "ended"
            bcast.append(f"对局结束：{color}方 {mover} 获胜（将死）！")
            bcast.extend(self._settle_ratings(1.0 if side == _XQ_RED else 0.0))
            return bcast, True

        opp_moves = _xq_legal_moves(self.board, self._turn)
        opp_k = _xq_king_pos(self.board, self._turn)
        in_check = (
            opp_k is not None
            and _xq_is_attacked(self.board, opp_k[0], opp_k[1], side)
        )
        if not opp_moves:
            self.state = "ended"
            if in_check:
                bcast.append(f"对局结束：{color}方 {mover} 将死获胜！")
                bcast.extend(self._settle_ratings(1.0 if side == _XQ_RED else 0.0))
            else:
                loser = self.red_name if self._turn == _XQ_RED else self.black_name
                loser_color = "红" if self._turn == _XQ_RED else "黑"
                bcast.append(
                    f"对局结束：{loser_color}方 {loser} 无合法着法，{color}方困毙获胜！"
                )
                bcast.extend(self._settle_ratings(1.0 if side == _XQ_RED else 0.0))
            return bcast, True

        next_name = self.red_name if self._turn == _XQ_RED else self.black_name
        next_color = "红" if self._turn == _XQ_RED else "黑"
        suffix = "（将军）" if in_check else ""
        bcast.append(f"轮到 {next_color}方 {next_name} 走子{suffix}")
        return bcast, False

    def _undo_turn_line(self) -> str:
        nm = self.red_name if self._turn == _XQ_RED else self.black_name
        color = "红" if self._turn == _XQ_RED else "黑"
        kpos = _xq_king_pos(self.board, self._turn)
        suffix = ""
        if kpos is not None and _xq_is_attacked(
            self.board, kpos[0], kpos[1], -self._turn
        ):
            suffix = "（被将军）"
        return f"轮到 {color}方 {nm} 走子{suffix}"

    def _xq_snapshot(self) -> tuple:
        self._ensure_compat_state()
        return (
            [row[:] for row in self.board],
            self._turn,
            self._last_from,
            self._last_to,
            self._last_mover_side,
            self._last_notation,
        )

    def _side_of(self, conn) -> Optional[int]:
        if conn is self.red_conn:
            return _XQ_RED
        if conn is self.black_conn:
            return _XQ_BLACK
        return None

    def is_seated(self, conn) -> bool:
        return self._side_of(conn) is not None

    def _viewer_flip(self, conn=None, *, viewer_name: Optional[str] = None) -> bool:
        side = self._side_of(conn)
        if side is None and viewer_name:
            vn = viewer_name.strip()
            if vn == self.red_name:
                side = _XQ_RED
            elif vn == self.black_name:
                side = _XQ_BLACK
        return side == _XQ_BLACK

    def _board_render(self, conn=None, *, viewer_name: Optional[str] = None) -> list[str]:
        self._ensure_compat_state()
        return _xq_render(
            self.board,
            last_from=self._last_from,
            last_to=self._last_to,
            last_notation=self._last_notation,
            flip=self._viewer_flip(conn, viewer_name=viewer_name),
        )

    def _is_ai_game(self) -> bool:
        return self.ai_level is not None

    def _is_ai_turn(self) -> bool:
        return self._is_ai_game() and self._turn == _XQ_BLACK

    def _rating_lines(self) -> list[str]:
        return _format_rating_lines(
            self.rating_store,
            self.name,
            [self.red_name, self.black_name],
            ai_name=self.ai_name,
        )

    def _settle_ratings(self, score_red: float) -> list[str]:
        if not self.black_name:
            return []
        return _format_rating_result_lines(
            self.rating_store,
            self.name,
            self.red_name,
            self.black_name,
            score_red,
            ranked=not self._is_ai_game(),
        )

    def _run_ai_turn(self) -> list[str]:
        self._ensure_compat_state()
        move = _choose_xq_ai_move(
            self.board,
            _XQ_BLACK,
            self.ai_level or "normal",
            self._xq_ply_log,
        )
        if move is None:
            self.state = "ended"
            return ["对局结束：AI 无合法着法。", *self._settle_ratings(0.5)]
        fr, fc, tr, tc = move
        label = _xq_move_label(self.board, fr, fc, tr, tc, _XQ_BLACK)
        bcast, ended = self._xq_commit_move(_XQ_BLACK, fr, fc, tr, tc, label)
        return bcast

    def nudge_bots(self) -> list[str]:
        """Resume AI practice after reconnect (/game show)."""
        if self.state != "playing" or not self._is_ai_turn():
            return []
        return self._run_ai_turn()

    def try_join(self, conn, name: str) -> GameResult:
        self._ensure_compat_state()
        if self.state == "ended":
            return (
                [f"对局已结束，请先 /game new {self.name} 开新局。"],
                [],
                False,
            )
        if conn is self.red_conn:
            return (["你已经是红方。"], [], False)
        if self._is_ai_game():
            return (["当前为 AI 练习局，不能加入执黑；可 /game show 围观。"], [], False)
        if self.black_conn is not None:
            return (
                [f"黑方席位已被 {self.black_name} 占。"],
                [],
                False,
            )
        self.black_conn = conn
        self.black_name = name
        self.state = "playing"
        bcast = [
            f"{name} 加入为黑方，对局开始！",
            f"红（先手）：{self.red_name}    黑：{self.black_name}",
            "走子：/game move <棋谱>  例：炮二平五、马2进3",
            "  也可用坐标：/game move 8 二 8 五（行 1～10；红列 九…一/黑列 1～9）",
            "  同线双子用 前/后；棋盘 +红 -黑 !上一步",
            "  循环局面：三次重复时，长将/长捉不变着判负（竞赛规则）",
            f"轮到 红方 {self.red_name} 走子",
        ]
        return ([], bcast, False)

    def try_move(self, conn, raw: str) -> GameResult:
        self._ensure_compat_state()
        if self.state == "waiting":
            return (["对局尚未开始，等黑方 /game join。"], [], False)
        if self.state != "playing":
            return (["对局已结束。"], [], False)
        side = self._side_of(conn)
        if side is None:
            return (["你不是对局双方（可 /game show 围观）。"], [], False)
        if side != self._turn:
            return (["不是你的回合。"], [], False)

        self._undo_clear_pending()
        parsed = _xq_parse_move(raw, side, self.board)
        if parsed is None:
            hint = ""
            compact = raw.strip().replace(" ", "")
            if _XQ_NOTATION_RE.match(compact):
                hint = "（棋谱格式已识别，但无合法着法或同型子歧义，试加 前/后）"
            return (
                [
                    "用法：/game move <棋谱>  例：炮二平五、马二进三、马2进3",
                    "  同线双子加 前/后；或坐标四元组（红列 九…一，黑列 1…9）",
                    hint,
                ],
                [],
                False,
            )
        fr, fc, tr, tc = parsed
        legal = _xq_legal_moves(self.board, side)
        if (fr, fc, tr, tc) not in legal:
            if self.board[fr][fc] == 0:
                print(
                    "xiangqi invalid move: empty source "
                    f"user_side={side} raw={raw!r} parsed={(fr + 1, fc + 1, tr + 1, tc + 1)}"
                )
                return (["起点无子。"], [], False)
            if _xq_piece_side(self.board[fr][fc]) != side:
                print(
                    "xiangqi invalid move: opponent source "
                    f"user_side={side} raw={raw!r} parsed={(fr + 1, fc + 1, tr + 1, tc + 1)} "
                    f"cell={self.board[fr][fc]}"
                )
                return (["不能移动对方的棋子。"], [], False)
            return (["该走法不合法（蹩马腿、塞象眼、出九宫、照面等）。"], [], False)

        label = _xq_move_label(self.board, fr, fc, tr, tc, side)
        if _xq_would_lose_on_repetition(self.board, self._xq_ply_log, side, fr, fc, tr, tc):
            color = "红" if side == _XQ_RED else "黑"
            return (
                [
                    f"此着会形成第三次循环局面且{color}方须变着"
                    "（长将/长捉不变着按竞赛规则判负），请改走他处。"
                ],
                [],
                False,
            )

        bcast, ended = self._xq_commit_move(side, fr, fc, tr, tc, label)
        if ended:
            return ([], bcast, True)

        if self._is_ai_turn():
            bcast.extend(self._run_ai_turn())
            return ([], bcast, self.state == "ended")

        return ([], bcast, False)

    def resign(self, conn, name: str) -> GameResult:
        if self.state != "playing":
            return (["对局尚未开始或已结束，无需认负。"], [], False)
        side = self._side_of(conn)
        if side is None:
            return (["你不是对局双方。"], [], False)
        self.state = "ended"
        if side == _XQ_RED:
            return ([], [f"红方 {name} 认负 — 黑胜", *self._settle_ratings(0.0)], True)
        return ([], [f"黑方 {name} 认负 — 红胜", *self._settle_ratings(1.0)], True)

    def abort(self, conn, name: str) -> GameResult:
        if self.state == "ended":
            return (["对局已结束。"], [], False)
        if self._side_of(conn) is None:
            return (["你不是对局双方，无法终止。"], [], False)
        if self.state == "playing":
            return (
                ["已开始的对局请用 /game resign 认负，不能 /game abort。"],
                [],
                False,
            )
        self.state = "ended"
        return ([], [f"{name} 终止了对局（未开始）。"], True)

    def seats(self) -> list[str]:
        lines = [
            f"xiangqi 对局状态：{self.state}",
            f"  红方（先手）：{self.red_name}",
            f"  黑方：{self.black_name or '(空席, 可 /game join)'}",
        ]
        lines.extend(self._rating_lines())
        return lines

    def show(self, conn=None, *, viewer_name: Optional[str] = None) -> list[str]:
        lines = [
            f"xiangqi 对局（{self.state}）  红：{self.red_name}   "
            f"黑：{self.black_name or '空席'}"
        ]
        lines.extend(self._rating_lines())
        lines.extend(self._board_render(conn, viewer_name=viewer_name))
        if self.state == "playing":
            nm = self.red_name if self._turn == _XQ_RED else self.black_name
            color = "红" if self._turn == _XQ_RED else "黑"
            kpos = _xq_king_pos(self.board, self._turn)
            suffix = ""
            if kpos is not None and _xq_is_attacked(
                self.board, kpos[0], kpos[1], -self._turn
            ):
                suffix = "（被将军）"
            lines.append(f"轮到 {color}方 {nm} 走子{suffix}")
        return lines

    def on_player_leave(self, conn, name: str) -> GameResult:
        side = self._side_of(conn)
        if side is None:
            return ([], [], False)
        if conn is self.red_conn:
            self.red_conn = None
        if conn is self.black_conn:
            self.black_conn = None
        if self.state == "waiting":
            self.state = "ended"
            return ([], [f"{name} 离开，对局取消。"], True)
        if self.state == "playing":
            self.state = "ended"
            if side == _XQ_RED:
                return ([], [f"红方 {name} 离开 — 黑胜", *self._settle_ratings(0.0)], True)
            return ([], [f"黑方 {name} 离开 — 红胜", *self._settle_ratings(1.0)], True)
        return ([], [], False)


# --- 三国杀军争版（简化身份局，武将池见 sgs_data.py）---

_SGS_MAX_PLAYERS = 6
_SGS_MIN_PLAYERS = 2
_SGS_INITIAL_HAND = 4
_SGS_DRAW_PER_TURN = 2

_SGS_ROLE_TABLE: dict[int, list[str]] = {
    2: ["主公", "反贼"],
    3: ["主公", "反贼", "反贼"],
    4: ["主公", "忠臣", "反贼", "内奸"],
    5: ["主公", "忠臣", "忠臣", "反贼", "反贼"],
    6: ["主公", "忠臣", "忠臣", "反贼", "反贼", "内奸"],
}

def _normalize_declared_suit(tok: str) -> Optional[str]:
    """反间等：将玩家输入的花色声明规范为 红桃/方块/黑桃/梅花。"""
    t = tok.strip()
    if t in ALL_SUITS:
        return t
    return {
        "红": "红桃",
        "方": "方块",
        "黑": "黑桃",
        "梅": "梅花",
    }.get(t)


_SGS_MOVE_ALIASES: dict[str, str] = {
    "杀": "sha",
    "sha": "sha",
    "火杀": "sha",
    "雷杀": "sha",
    "闪": "shan",
    "shan": "shan",
    "桃": "tao",
    "tao": "tao",
    "酒": "jiu",
    "jiu": "jiu",
    "决斗": "duel",
    "duel": "duel",
    "拆": "dismantle",
    "过河拆桥": "dismantle",
    "dismantle": "dismantle",
    "无中生有": "draw2",
    "draw2": "draw2",
    "南蛮": "nanman",
    "南蛮入侵": "nanman",
    "nanman": "nanman",
    "万箭": "wanjian",
    "万箭齐发": "wanjian",
    "wanjian": "wanjian",
    "顺手": "shunshou",
    "顺手牵羊": "shunshou",
    "顺": "shunshou",
    "牵": "shunshou",
    "兵粮": "bingliang",
    "兵粮寸断": "bingliang",
    "铁索": "tiesuo",
    "铁索连环": "tiesuo",
    "五谷": "wugu",
    "五谷丰登": "wugu",
    "桃园": "taoyuan",
    "桃园结义": "taoyuan",
    "火攻": "huogong",
    "huogong": "huogong",
    "出示": "chushi",
    "展示": "chushi",
    "chushi": "chushi",
    "仁德": "rende",
    "rende": "rende",
    "制衡": "zhiheng",
    "zhiheng": "zhiheng",
    "裸衣": "luoyi",
    "luoyi": "luoyi",
    "突袭": "tuxi",
    "tuxi": "tuxi",
    "强袭": "qiangxi",
    "qiangxi": "qiangxi",
    "青囊": "qingnang",
    "qingnang": "qingnang",
    "巧变": "qiaobian",
    "qiaobian": "qiaobian",
    "结姻": "jieyin",
    "jieyin": "jieyin",
    "国色": "guose",
    "guose": "guose",
    "流离": "liuli",
    "liuli": "liuli",
    "奇袭": "qixi",
    "qixi": "qixi",
    "武将": "generals",
    "generals": "generals",
    "天妒": "tiandu",
    "tiandu": "tiandu",
    "雷击": "leiji",
    "leiji": "leiji",
    "天香": "tianxiang",
    "tianxiang": "tianxiang",
    "享乐": "xiangle",
    "xiangle": "xiangle",
    "英魂": "yinghun",
    "yinghun": "yinghun",
    "激昂": "jiang",
    "jiang": "jiang",
    "魂姿": "hunzi",
    "hunzi": "hunzi",
    "制霸": "zhiba",
    "zhiba": "zhiba",
    "拒制霸": "拒制霸",
    "拼点": "拼点",
    "反间": "fanjian",
    "fanjian": "fanjian",
    "乱击": "luanji",
    "luanji": "luanji",
    "双雄": "shuangxiong",
    "shuangxiong": "shuangxiong",
    "蛊惑": "guhuo",
    "guhuo": "guhuo",
    "观星": "guanxing",
    "guanxing": "guanxing",
    "断粮": "duanliang",
    "duanliang": "duanliang",
    "装备": "equip",
    "equip": "equip",
    "卸": "unequip",
    "unequip": "unequip",
    "过": "pass",
    "pass": "pass",
    "结束": "pass",
    "不出": "pass",
    "受击": "hurt",
    "hurt": "hurt",
    "雌雄弃": "cixiong_discard",
    "cixiong_discard": "cixiong_discard",
    "雌雄摸": "cixiong_draw",
    "cixiong_draw": "cixiong_draw",
    "开始": "start",
    "开局": "start",
    "start": "start",
}


def _sgs_parse_action(raw: str) -> tuple[str, list[str], Optional[str]]:
    """返回 (verb, args, sha_kind)。首词为 杀/火杀/雷杀 时 verb=sha 且 sha_kind 约束牌名。"""
    text = raw.strip()
    if not text:
        return ("", [], None)
    parts = text.split()
    head = parts[0]
    if head in SHA_CARDS:
        sha_kind = None if head == "杀" else head
        return ("sha", parts[1:], sha_kind)
    verb = _SGS_MOVE_ALIASES.get(head, _SGS_MOVE_ALIASES.get(head.lower(), head.lower()))
    sha_kind = head if verb == "sha" and head in SHA_CARDS and head != "杀" else None
    return (verb, parts[1:], sha_kind)


def _peel_hand_tokens_from_args(
    player: _SgsPlayer, args: list[str]
) -> tuple[list[str], list[str]]:
    """从参数末尾剥离手牌 token，至少保留一个目标 token。"""
    rest = list(args)
    peeled: list[str] = []
    while len(rest) > 1:
        tok = rest[-1]
        if find_card_in_hand(player.hand, tok) is not None:
            peeled.insert(0, rest.pop())
        else:
            break
    return rest, peeled


def _sgs_sha_target_and_card(
    player: _SgsPlayer,
    args: list[str],
    *,
    sha_kind: Optional[str],
    has_zhangba: bool = False,
) -> tuple[
    Optional[list[str]],
    Optional[str],
    Optional[tuple[str, str]],
    Optional[str],
]:
    """返回 (目标参数, 单张杀牌, 丈八两牌, 错误)。"""
    if not args:
        extra = ""
        if has_zhangba:
            extra = "；装备【丈八蛇矛】时：/game move 杀 <目标> <牌1> <牌2>"
        return (
            None,
            None,
            None,
            f"用法：/game move 杀|火杀|雷杀 <#序号|昵称> [牌名]{extra}",
        )
    target_args, peeled = _peel_hand_tokens_from_args(player, args)
    if not target_args:
        return (None, None, None, "请指定目标。")
    if len(peeled) > 2:
        return (None, None, None, "指定牌过多。")
    if len(peeled) == 2:
        if not has_zhangba:
            return (None, None, None, "只能指定一张【杀】类牌。")
        if sha_kind and sha_kind != "杀":
            return (
                None,
                None,
                None,
                "【丈八蛇矛】只能将两张手牌当普通【杀】，不能当火杀/雷杀。",
            )
        return (target_args, None, (peeled[0], peeled[1]), None)
    if len(peeled) == 1:
        found = find_card_in_hand(player.hand, peeled[0])
        assert found is not None
        if card_base(found) not in SHA_CARDS:
            if has_zhangba:
                return (
                    None,
                    None,
                    None,
                    f"【{peeled[0]}】不是【杀】。"
                    "使用丈八：/game move 杀 <目标> <牌1> <牌2>",
                )
            return (None, None, None, f"【{peeled[0]}】不是【杀】类牌。")
        if sha_kind and card_base(found) != sha_kind:
            return (
                None,
                None,
                None,
                f"请使用【{sha_kind}】（你指定了【{card_label(found)}】）。",
            )
        return (target_args, peeled[0], None, None)
    return (target_args, None, None, None)


class _SgsPlayer:
    __slots__ = (
        "conn",
        "name",
        "general",
        "kingdom",
        "skill_ids",
        "max_hp",
        "hp",
        "hand",
        "role",
        "dead",
        "sha_used",
        "jiu_buff",
        "luoyi_buff",
        "skip_play",
        "skip_draw",
        "judge_lebu",
        "judge_bingliang",
        "drew_this_turn",
        "chained",
        "niepan_used",
        "fanjian_used",
        "shuangxiong_color",
        "zaoxian_awakened",
        "hunzi_awakened",
        "zhiba_used",
        "shuangxiong_duel_used",
        "weapon",
        "armor",
        "horse_plus",
        "horse_minus",
    )

    def __init__(self, conn, name: str) -> None:
        self.conn = conn
        self.name = name
        self.general = ""
        self.kingdom = ""
        self.skill_ids: tuple[str, ...] = ()
        self.max_hp = 4
        self.hp = 4
        self.hand: list[str] = []
        self.role = ""
        self.dead = False
        self.sha_used = 0
        self.jiu_buff = False
        self.luoyi_buff = False
        self.skip_play = False
        self.skip_draw = False
        self.judge_lebu = False
        self.judge_bingliang = False
        self.drew_this_turn = False
        self.chained = False
        self.niepan_used = False
        self.fanjian_used = False
        self.shuangxiong_color: Optional[str] = None
        self.zaoxian_awakened = False
        self.hunzi_awakened = False
        self.zhiba_used = False
        self.shuangxiong_duel_used = False
        self.weapon: Optional[str] = None
        self.armor: Optional[str] = None
        self.horse_plus: Optional[str] = None
        self.horse_minus: Optional[str] = None


class SanguoshaGame:
    """三国杀军争版简化身份局（2–6 人，武将池见 sgs_data）。"""

    name = "sanguo"
    # 出牌后不再向全员推送整页 /game show（棋盘类对局仍用 send_oriented_boards）
    send_view_on_move = False
    first_seat_desc = "房主"
    second_seat_desc = "玩家"
    join_blurb = (
        f"其它玩家可 /game join 入座（{_SGS_MIN_PLAYERS}～{_SGS_MAX_PLAYERS} 人）；"
        f"人满或就绪后由房主 /game move 开始 开局。"
    )

    def __init__(self, host_conn, host_name: str) -> None:
        self.players: list[_SgsPlayer] = [_SgsPlayer(host_conn, host_name)]
        self.state = "waiting"
        self._turn_idx = 0
        self._deck: list[str] = []
        self._discard: list[str] = []
        self._tiandu_offer: dict[int, str] = {}
        self._pending: Optional[dict] = None
        self._extra_privates: list[tuple[object, list[str]]] = []
        self._rng = random.Random()

    def drain_extra_privates(self) -> list[tuple[object, list[str]]]:
        out = self._extra_privates
        self._extra_privates = []
        return out

    def _queue_private(self, who_idx: int, lines: list[str]) -> None:
        if lines and 0 <= who_idx < len(self.players):
            self._extra_privates.append((self.players[who_idx].conn, lines))

    def _who_of(self, conn) -> Optional[int]:
        for i, p in enumerate(self.players):
            if conn is p.conn:
                return i
        return None

    def is_seated(self, conn) -> bool:
        return self._who_of(conn) is not None

    def _is_alive(self, p: _SgsPlayer) -> bool:
        return not p.dead and p.hp > 0

    def _alive_indices(self) -> list[int]:
        return [i for i, p in enumerate(self.players) if self._is_alive(p)]

    def _current(self) -> _SgsPlayer:
        return self.players[self._turn_idx]

    def _roster_names(self) -> str:
        return "、".join(p.name for p in self.players)

    def _lord_index(self) -> int:
        for i, p in enumerate(self.players):
            if p.role == "主公":
                return i
        return 0

    def _draw_cards(self, player: _SgsPlayer, n: int) -> int:
        drawn = 0
        for _ in range(n):
            if not self._deck:
                if not self._discard:
                    break
                self._rng.shuffle(self._discard)
                self._deck = self._discard[:]
                self._discard = []
            if not self._deck:
                break
            player.hand.append(self._deck.pop())
            drawn += 1
        return drawn

    def _remove_card(self, player: _SgsPlayer, card: str) -> bool:
        found = find_card_in_hand(player.hand, card)
        if found is None:
            return False
        player.hand.remove(found)
        self._discard.append(found)
        return True

    def _find_trick_in_hand(
        self, player: _SgsPlayer, trick_name: str
    ) -> Optional[str]:
        for c in player.hand:
            if card_base(c) == trick_name:
                return c
        return None

    def _has_skill(self, player: _SgsPlayer, skill: str) -> bool:
        return skill in player.skill_ids

    def _grant_skills(self, player: _SgsPlayer, *skills: str) -> None:
        merged = list(player.skill_ids)
        for s in skills:
            if s not in merged:
                merged.append(s)
        player.skill_ids = tuple(merged)

    def _jiang_try_draw(self, player: _SgsPlayer, msgs: list[str]) -> None:
        if not self._has_skill(player, "jiang"):
            return
        n = self._draw_cards(player, 1)
        if n:
            msgs.append(f"{player.name}（激昂）摸 1 张")

    def _offer_tiandu(self, who_idx: int, card: str, msgs: list[str]) -> None:
        """判定牌进入弃牌堆后，郭嘉可择机领取。"""
        p = self.players[who_idx]
        if not self._has_skill(p, "tiandu"):
            return
        self._tiandu_offer[who_idx] = card
        msgs.append(
            f"{p.name} 可 /game move 天妒 获得判定牌【{card_label(card)}】"
        )
        self._queue_private(
            who_idx,
            [f"天妒：/game move 天妒 获得【{card_label(card)}】"],
        )

    def _do_tiandu_take(self, who: int) -> GameResult:
        player = self.players[who]
        if not self._has_skill(player, "tiandu"):
            return (["你没有天妒技能。"], [], False)
        card = self._tiandu_offer.pop(who, None)
        if card is None:
            return (["当前没有可天妒的判定牌。"], [], False)
        if card not in self._discard:
            return (["该判定牌已不可取。"], [], False)
        self._discard.remove(card)
        player.hand.append(card)
        return (
            [],
            [f"{player.name}（天妒）获得【{card_label(card)}】"],
            False,
        )

    def _yinghun_loss(self, player: _SgsPlayer) -> int:
        return max(0, player.max_hp - player.hp)

    def _prepare_phase(self, who_idx: int) -> list[str]:
        """准备阶段：魂姿觉醒、英魂（手动在当回合 /game move 英魂）。"""
        p = self.players[who_idx]
        msgs: list[str] = []
        if (
            self._has_skill(p, "hunzi")
            and not p.hunzi_awakened
            and p.hp == 1
        ):
            p.hunzi_awakened = True
            if p.max_hp > 1:
                p.max_hp -= 1
                if p.hp > p.max_hp:
                    p.hp = p.max_hp
            self._grant_skills(p, "yingzi", "yinghun")
            msgs.append(
                f"{p.name}（魂姿）觉醒，体力上限 {p.max_hp}，"
                f"获得【英姿】【英魂】"
            )
        return msgs

    def _lord_for_zhiba(self) -> Optional[int]:
        lord_i = self._lord_index()
        lord = self.players[lord_i]
        if lord.dead or not self._has_skill(lord, "zhiba"):
            return None
        return lord_i

    def _do_yinghun(
        self, who: int, mode: str, target_idx: int
    ) -> GameResult:
        player = self.players[who]
        if not self._has_skill(player, "yinghun"):
            return (["你没有英魂技能。"], [], False)
        loss = self._yinghun_loss(player)
        if loss <= 0:
            return (["英魂：你未受伤，无法发动。"], [], False)
        if who != self._turn_idx:
            return (["英魂：仅可在你的回合发动。"], [], False)
        tgt_p = self.players[target_idx]
        if tgt_p.dead:
            return (["目标已阵亡。"], [], False)
        if mode == "1":
            drew = self._draw_cards(tgt_p, loss)
            if drew <= 0:
                return (["牌堆已空。"], [], False)
            msgs = [
                f"{player.name}（英魂）令 {tgt_p.name} 摸 {drew} 张"
            ]
            if tgt_p.hand:
                card = tgt_p.hand.pop()
                self._discard.append(card)
                msgs.append(f"{tgt_p.name} 弃【{card_label(card)}】")
            else:
                msgs.append(f"{tgt_p.name} 无手牌可弃")
            return ([], msgs, False)
        if mode == "2":
            drew = self._draw_cards(tgt_p, 1)
            if drew <= 0:
                return (["牌堆已空。"], [], False)
            msgs = [f"{player.name}（英魂）令 {tgt_p.name} 摸 1 张"]
            drop = min(loss, len(tgt_p.hand))
            for _ in range(drop):
                card = tgt_p.hand.pop()
                self._discard.append(card)
            if drop:
                msgs.append(f"{tgt_p.name} 弃 {drop} 张")
            else:
                msgs.append(f"{tgt_p.name} 无手牌可弃")
            return ([], msgs, False)
        return (
            ["用法：/game move 英魂 1|2 <目标>（1摸X弃1，2摸1弃X）"],
            [],
            False,
        )

    def _resolve_zhiba_pin(
        self, initiator_idx: int, lord_idx: int, init_card: str, lord_card: str
    ) -> list[str]:
        init_p = self.players[initiator_idx]
        lord_p = self.players[lord_idx]
        lord_p.hand.remove(lord_card)
        r_init = card_pin_rank(init_card)
        r_lord = card_pin_rank(lord_card)
        msgs = [
            f"{init_p.name}（制霸）拼【{card_label(init_card)}】"
            f" vs {lord_p.name}【{card_label(lord_card)}】"
        ]
        if r_init > r_lord:
            init_p.hand.append(init_card)
            init_p.hand.append(lord_card)
            msgs.append(f"{init_p.name} 拼点赢，收回两张牌")
        else:
            lord_p.hand.append(init_card)
            lord_p.hand.append(lord_card)
            msgs.append(f"{lord_p.name} 拼点赢，获得两张牌")
        return msgs

    def _resolve_zhiba_pending(
        self, who: int, verb: str, args: list[str]
    ) -> GameResult:
        pend = self._pending
        if not pend or pend.get("kind") != "zhiba":
            return (["当前没有制霸拼点待处理。"], [], False)
        lord_i = int(pend["lord"])
        if who != lord_i:
            lord = self.players[lord_i]
            return (
                [f"请等待主公 {lord.name} 响应制霸拼点。"],
                [],
                False,
            )
        lord = self.players[lord_i]
        init_i = int(pend["initiator"])
        init_card = pend["init_card"]
        if verb in ("拒制霸", "拒", "拒绝"):
            if not lord.hunzi_awakened:
                return (["主公未觉醒，不能拒绝制霸拼点。"], [], False)
            self._pending = None
            self.players[init_i].hand.append(init_card)
            return (
                [],
                [
                    f"{lord.name} 拒绝与 {self.players[init_i].name} 制霸拼点，"
                    f"【{card_label(init_card)}】退回"
                ],
                False,
            )
        if verb in ("拼点", "应", "制霸"):
            if not args:
                return (["用法：/game move 拼点 <手牌>"], [], False)
            found = find_card_in_hand(lord.hand, args[0])
            if found is None:
                return ([f"你没有【{args[0]}】。"], [], False)
            self._pending = None
            msgs = self._resolve_zhiba_pin(init_i, lord_i, init_card, found)
            return ([], msgs, False)
        hint = "请 /game move 拼点 <牌>"
        if lord.hunzi_awakened:
            hint += " 或 /game move 拒制霸"
        return ([hint], [], False)

    def _seat_distance(self, a: int, b: int) -> int:
        """固定座次上的最短步数（含阵亡位，与存活环距离可能不同）。"""
        n = len(self.players)
        d = abs(a - b)
        return min(d, n - d)

    def _alive_distance(self, a: int, b: int) -> int:
        """存活角色间的最短座次距离（锦囊距离用）。"""
        if a == b:
            return 0
        alive = self._alive_indices()
        if a not in alive or b not in alive:
            return 99
        ai, bi = alive.index(a), alive.index(b)
        n = len(alive)
        return min(abs(ai - bi), n - abs(ai - bi))

    def _calc_distance(self, from_idx: int, to_idx: int) -> int:
        """from 到 to 的距离：座次环距；目标 +1 马 +1；双方 -1 马各 -1。"""
        d = self._alive_distance(from_idx, to_idx)
        to_p = self.players[to_idx]
        from_p = self.players[from_idx]
        if to_p.horse_plus:
            d += 1
        if from_p.horse_minus:
            d -= 1
        if to_p.horse_minus:
            d -= 1
        return max(1, d)

    def _attack_range(self, actor_idx: int) -> int:
        """攻击范围 = 武器距离 + 进攻马(-1)。"""
        actor = self.players[actor_idx]
        r = weapon_range(actor.weapon)
        if actor.horse_minus:
            r += 1
        return r

    def _in_attack_range(self, actor_idx: int, target_idx: int) -> bool:
        return self._calc_distance(actor_idx, target_idx) <= self._attack_range(
            actor_idx
        )

    def _equip_slots(self) -> tuple[str, ...]:
        return ("weapon", "armor", "horse_plus", "horse_minus")

    def _get_equip(self, player: _SgsPlayer, slot: str) -> Optional[str]:
        return getattr(player, slot)

    def _set_equip(
        self, player: _SgsPlayer, slot: str, card: Optional[str]
    ) -> None:
        setattr(player, slot, card)

    def _discard_equip(self, card: str) -> None:
        self._discard.append(card)

    def _seat_order_line(self) -> str:
        parts: list[str] = []
        for i, p in enumerate(self.players):
            mark = ""
            if self.state == "playing" and i == self._turn_idx and not p.dead:
                mark = "▸"
            dead = "×" if p.dead else ""
            parts.append(f"#{i + 1}{mark}{p.name}{dead}")
        return "  座次（顺时针）：" + " → ".join(parts)

    def _private_hand_view(self, who: int) -> list[str]:
        """当前玩家私信：手牌与装备（不必 /game show）。"""
        p = self.players[who]
        labels = [f"{i + 1}.{card_label(c)}" for i, c in enumerate(p.hand)]
        lines = [
            f"── 你的手牌（{len(p.hand)}张）──",
            "  " + ("、".join(labels) if labels else "（空）"),
            "── 你的装备 ──",
            "  "
            + "  ".join(
                (
                    f"武器：{card_label(p.weapon) if p.weapon else '—'}",
                    f"防具：{card_label(p.armor) if p.armor else '—'}",
                    f"+1马：{card_label(p.horse_plus) if p.horse_plus else '—'}",
                    f"-1马：{card_label(p.horse_minus) if p.horse_minus else '—'}",
                )
            ),
        ]
        lines.extend(self._distance_lines(who))
        pend = self._pending
        if (
            who == self._turn_idx
            and not pend
            and not p.skip_play
            and not p.dead
        ):
            lines.append(
                f"  ▸ 轮到你出牌  杀距{self._attack_range(who)}"
            )
            lines.append("  /game show 帮助  查看完整指令")
        elif pend and pend.get("kind") == "guanxing" and pend.get("who") == who:
            n = len(pend.get("cards", []))
            lines.append(
                f"  ▸ 观星：/game move 观星 <1～{n} 排列> 或 观星 过"
            )
            for i, c in enumerate(pend.get("cards", []), 1):
                lines.append(f"    牌顶 #{i} 【{card_label(c)}】")
        elif pend:
            hint = self._pending_hint().strip()
            if hint:
                lines.append(f"  ▸ {hint}")
        return lines

    def push_hand_views(self) -> list[tuple[object, list[str]]]:
        """返回需私信手牌摘要的 (conn, lines)。"""
        if self.state != "playing":
            return []
        out: list[tuple[object, list[str]]] = []
        seen: set[int] = set()

        def add(who: int) -> None:
            if who in seen or who < 0 or who >= len(self.players):
                return
            p = self.players[who]
            if p.dead:
                return
            seen.add(who)
            lines = self._private_hand_view(who)
            if lines:
                out.append((p.conn, lines))

        pend = self._pending
        if pend:
            kind = pend.get("kind")
            if kind == "guanxing":
                add(int(pend["who"]))
            elif kind == "sha":
                add(int(pend["target"]))
            elif kind in ("duel", "nanman", "wanjian"):
                add(int(pend["turn"]))
            elif kind == "huogong":
                phase = pend.get("phase", "show")
                if phase == "show":
                    add(int(pend["target"]))
                else:
                    add(int(pend["source"]))
            elif kind in ("shunshou", "dismantle"):
                add(int(pend["source"]))
            elif kind == "cixiong":
                add(int(pend["target"]))
            elif kind == "zhiba":
                add(int(pend["lord"]))
            return out

        who = self._turn_idx
        p = self.players[who]
        if not p.dead and not p.skip_play:
            add(who)
        return out

    def _distance_lines(self, viewer: Optional[int]) -> list[str]:
        if viewer is None or self.state != "playing":
            return []
        lines = [f"  距离（相对你 #{viewer + 1}，杀距={self._attack_range(viewer)}）："]
        for i, p in enumerate(self.players):
            if i == viewer:
                continue
            if p.dead:
                lines.append(f"    #{i + 1} {p.name}  阵亡")
                continue
            d = self._calc_distance(viewer, i)
            tag = "可杀" if self._in_attack_range(viewer, i) else "不可杀"
            lines.append(f"    #{i + 1} {p.name}  距离{d}  ({tag})")
        return lines

    def _ignore_target_armor(self, actor_idx: int, target_idx: int) -> bool:
        actor = self.players[actor_idx]
        return actor.weapon is not None and card_base(actor.weapon) == "青釭剑"

    def _has_armor(self, player: _SgsPlayer, name: str) -> bool:
        return (
            player.armor is not None and card_base(player.armor) == name
        )

    def _can_bagua_for_target(self, actor_idx: int, target_idx: int) -> bool:
        if self._ignore_target_armor(actor_idx, target_idx):
            return False
        return self._has_armor(self.players[target_idx], "八卦阵")

    def _try_bagua_shan(self, target_idx: int) -> tuple[bool, list[str]]:
        """八卦阵判定：红色视为出闪。返回 (是否视为出闪, 战报)。"""
        target = self.players[target_idx]
        card = self._flip_judge_card()
        lines: list[str] = []
        if card is None:
            lines.append(f"{target.name}【八卦阵】判定：牌堆空")
            return False, lines
        self._discard.append(card)
        lines.append(
            f"{target.name}【八卦阵】判定【{card_label(card)}】"
        )
        if is_red(card):
            lines.append(f"  → 红色，视为出【闪】")
            self._offer_tiandu(target_idx, card, lines)
            return True, lines
        lines.append(f"  → 非红色，仍需【闪】或受击")
        self._offer_tiandu(target_idx, card, lines)
        return False, lines

    def _bagua_try_for_need(
        self,
        actor_idx: int,
        target_idx: int,
        got_shan: int,
        need_shan: int,
    ) -> tuple[int, list[str], bool]:
        """依次判定八卦直至无效、满足需闪数或无法发动。返回 (got_shan, 战报, 是否已抵消)."""
        lines: list[str] = []
        while got_shan < need_shan and self._can_bagua_for_target(
            actor_idx, target_idx
        ):
            prev = got_shan
            ok, one = self._try_bagua_shan(target_idx)
            lines.extend(one)
            if not ok:
                break
            got_shan += 1
            if got_shan >= need_shan:
                return got_shan, lines, True
            lines.append(
                f"  → 视为出【闪】（{got_shan}/{need_shan}），"
                f"可继续判定【八卦阵】或打出【闪】"
            )
        return got_shan, lines, False

    def _qilin_discard_horse(self, target_idx: int, notes: list[str]) -> None:
        victim = self.players[target_idx]
        for slot in ("horse_plus", "horse_minus"):
            card = self._get_equip(victim, slot)
            if card:
                self._set_equip(victim, slot, None)
                self._discard_equip(card)
                notes.append(
                    f"{victim.name}麒麟弓弃【{card_label(card)}】"
                )
                return

    def _jizhi_draw(self, player: _SgsPlayer, notes: list[str]) -> None:
        if self._has_skill(player, "jizhi"):
            if self._draw_cards(player, 1):
                notes.append(f"{player.name}集智+1牌")

    def _status_line(self) -> str:
        """单行局面摘要，附在每次出牌广播末尾。"""
        hint = self._pending_hint().strip()
        if hint:
            return hint.lstrip()
        if self.state == "playing" and not self._pending:
            cur = self._current()
            if not cur.dead:
                return f"▸ #{self._turn_idx + 1} {cur.name} 回合"
        return ""

    def finalize_broadcast(self, lines: list[str]) -> list[str]:
        if not lines:
            return lines
        foot = self._status_line()
        if foot and lines[-1] != foot:
            lines.append(foot)
        return lines

    def _can_trick_target(self, target_idx: int) -> bool:
        p = self.players[target_idx]
        if self._has_skill(p, "qianxun") and not p.hand:
            return False
        return True

    def _trick_target_err(self, name: str) -> GameResult:
        return ([f"{name}（谦逊）无手牌，不能成为锦囊目标。"], [], False)

    def _shangshi_draw(self, player: _SgsPlayer, notes: list[str]) -> None:
        while (
            self._has_skill(player, "shangshi")
            and not player.dead
            and len(player.hand) <= player.hp
            and player.hp > 0
        ):
            if not self._draw_cards(player, 1):
                break
            notes.append(f"{player.name}伤逝+1牌")

    def _kuanggu_check(self, damaged_idx: int, notes: list[str]) -> None:
        for i, p in enumerate(self.players):
            if not self._has_skill(p, "kuanggu") or p.dead:
                continue
            if self._alive_distance(i, damaged_idx) != 1:
                continue
            if p.hp < p.max_hp:
                p.hp += 1
                notes.append(f"{p.name}狂骨+1体力")

    def _lieren_steal(
        self, actor_idx: int, target_idx: int, notes: list[str]
    ) -> None:
        actor = self.players[actor_idx]
        victim = self.players[target_idx]
        if not self._has_skill(actor, "lieren") or not victim.hand:
            return
        card = self._rng.choice(victim.hand)
        victim.hand.remove(card)
        actor.hand.append(card)
        notes.append(f"{actor.name}烈刃得【{card_label(card)}】")

    def _player_gender(self, player: _SgsPlayer) -> str:
        g = SGS_GENERAL_BY_NAME.get(player.general or "")
        if g:
            return g.get("gender", general_gender(player.general))
        return general_gender(player.general or "")

    def _opposite_gender(self, a_idx: int, b_idx: int) -> bool:
        return self._player_gender(self.players[a_idx]) != self._player_gender(
            self.players[b_idx]
        )

    def _start_cixiong_pending(
        self, source_idx: int, target_idx: int
    ) -> list[str]:
        """【杀】造成伤害后：雌雄双股剑令异性目标选择弃牌或令使用者摸牌。"""
        src = self.players[source_idx]
        tgt = self.players[target_idx]
        if tgt.dead or src.dead:
            return []
        if not (
            src.weapon is not None
            and card_base(src.weapon) == "雌雄双股剑"
        ):
            return []
        if not self._opposite_gender(source_idx, target_idx):
            return []
        if not tgt.hand:
            n = self._draw_cards(src, 1)
            return [f"【雌雄双股剑】{tgt.name}无手牌，{src.name}摸{n}张"]
        self._pending = {
            "kind": "cixiong",
            "source": source_idx,
            "target": target_idx,
        }
        return [
            f"【雌雄双股剑】{tgt.name}请选择："
            f"/game move 雌雄弃 <牌> 或 /game move 雌雄摸"
        ]

    def _sha_damage_followup(
        self,
        source_idx: int,
        target_idx: int,
        bcast: list[str],
        notes: list[str],
    ) -> GameResult:
        self._lieren_steal(source_idx, target_idx, notes)
        if notes:
            bcast.append("  " + "；".join(notes))
        cixiong_msgs = self._start_cixiong_pending(source_idx, target_idx)
        if cixiong_msgs:
            bcast.extend(cixiong_msgs)
        if self._pending and self._pending.get("kind") == "cixiong":
            return ([], bcast, False)
        return self._maybe_end(bcast)

    def _resolve_cixiong(
        self, who: int, verb: str, args: Optional[list[str]]
    ) -> GameResult:
        pend = self._pending
        if not pend or pend.get("kind") != "cixiong":
            return (["当前没有【雌雄双股剑】待结算。"], [], False)
        if who != pend["target"]:
            tgt = self.players[pend["target"]]
            return ([f"请 {tgt.name} 选择雌雄双股剑效果。"], [], False)
        src = self.players[pend["source"]]
        tgt = self.players[pend["target"]]
        if verb == "cixiong_discard":
            if not args:
                return (
                    ["用法：/game move 雌雄弃 <牌名>  或  /game move 雌雄摸"],
                    [],
                    False,
                )
            found = find_card_in_hand(tgt.hand, args[0])
            if found is None:
                return ([f"你没有【{args[0]}】。"], [], False)
            tgt.hand.remove(found)
            self._discard.append(found)
            self._pending = None
            bcast = [
                f"【雌雄双股剑】{tgt.name}弃【{card_label(found)}】"
                f"（{src.name} 未摸牌）"
            ]
            return self._maybe_end(bcast)
        if verb == "cixiong_draw":
            self._pending = None
            n = self._draw_cards(src, 1)
            bcast = [f"【雌雄双股剑】{tgt.name}选择令{src.name}摸{n}张"]
            return self._maybe_end(bcast)
        return (
            ["请 /game move 雌雄弃 <牌> 或 /game move 雌雄摸"],
            [],
            False,
        )

    def _huoshou_kill_draw(
        self, source_idx: Optional[int], notes: list[str]
    ) -> None:
        if source_idx is None:
            return
        src = self.players[source_idx]
        if self._has_skill(src, "huoshou"):
            n = self._draw_cards(src, 2)
            if n:
                notes.append(f"{src.name}祸首+{n}牌")

    def _has_zhangba(self, player: _SgsPlayer) -> bool:
        return (
            player.weapon is not None
            and card_base(player.weapon) == "丈八蛇矛"
        )

    def _format_sha_label(
        self, card: str, zhangba_labels: Optional[list[str]] = None
    ) -> str:
        if zhangba_labels:
            return f"【{'】【'.join(zhangba_labels)}】当【杀】"
        return f"【{card_label(card)}】"

    def _consume_zhangba_sha(
        self, player: _SgsPlayer, tok1: str, tok2: str
    ) -> Optional[tuple[str, list[str], tuple[str, str]]]:
        """弃两张手牌，视为出【杀】。返回 (虚拟杀, 展示用标签, 实际两牌)。"""
        c1 = find_card_in_hand(player.hand, tok1)
        if c1 is None:
            return None
        player.hand.remove(c1)
        c2 = find_card_in_hand(player.hand, tok2)
        if c2 is None:
            player.hand.append(c1)
            return None
        player.hand.remove(c2)
        self._discard.extend([c1, c2])
        return ("杀", [card_label(c1), card_label(c2)], (c1, c2))

    def _is_sha_card(self, card: str) -> bool:
        return card_base(card) in SHA_CARDS

    def _is_shan_card(self, card: str) -> bool:
        return card_base(card) in SHAN_CARDS

    def _has_sha(self, player: _SgsPlayer) -> bool:
        for c in player.hand:
            if self._is_sha_card(c):
                return True
        if self._has_skill(player, "wusheng"):
            if any(is_red(c) for c in player.hand):
                return True
        if self._has_skill(player, "longdan"):
            if any(self._is_shan_card(c) for c in player.hand):
                return True
        return False

    def _consume_sha(
        self,
        player: _SgsPlayer,
        *,
        token: Optional[str] = None,
        sha_kind: Optional[str] = None,
    ) -> Optional[str]:
        if token is not None:
            found = find_card_in_hand(player.hand, token)
            if found is None or not self._is_sha_card(found):
                return None
            if sha_kind and card_base(found) != sha_kind:
                return None
            player.hand.remove(found)
            self._discard.append(found)
            return found
        for c in list(player.hand):
            if self._is_sha_card(c) and (not sha_kind or card_base(c) == sha_kind):
                player.hand.remove(c)
                self._discard.append(c)
                return c
        if sha_kind:
            return None
        if self._has_skill(player, "wusheng"):
            for c in list(player.hand):
                if is_red(c):
                    player.hand.remove(c)
                    self._discard.append(c)
                    return c
        if self._has_skill(player, "longdan"):
            for c in list(player.hand):
                if self._is_shan_card(c):
                    player.hand.remove(c)
                    self._discard.append(c)
                    return c
        return None

    def _consume_shan(self, player: _SgsPlayer) -> bool:
        for c in list(player.hand):
            if self._is_shan_card(c):
                player.hand.remove(c)
                self._discard.append(c)
                return True
        if self._has_skill(player, "longdan"):
            for c in list(player.hand):
                if self._is_sha_card(c):
                    player.hand.remove(c)
                    self._discard.append(c)
                    return True
        return False

    def _consume_card(self, player: _SgsPlayer, card: str) -> bool:
        if card in SHA_CARDS:
            return self._consume_sha(player) is not None
        found = find_card_in_hand(player.hand, card)
        if found is None:
            return False
        player.hand.remove(found)
        self._discard.append(found)
        return True

    def _resolve_target(
        self,
        actor_idx: int,
        tokens: list[str],
        *,
        allow_self: bool = False,
    ) -> Optional[int]:
        if not tokens:
            return None
        token = " ".join(tokens).strip()
        if token.startswith("#"):
            try:
                slot = int(token[1:]) - 1
            except ValueError:
                return None
            if 0 <= slot < len(self.players) and (allow_self or slot != actor_idx):
                return slot
            return None
        try:
            slot = int(token) - 1
            if 0 <= slot < len(self.players) and (allow_self or slot != actor_idx):
                return slot
        except ValueError:
            pass
        for i, p in enumerate(self.players):
            if (allow_self or i != actor_idx) and token in p.name:
                return i
        return None

    def _public_status(self, p: _SgsPlayer, slot: int, viewer: Optional[int]) -> str:
        dead = " [阵亡]" if p.dead else ""
        role = ""
        if p.role == "主公" and not p.dead:
            role = " 身份=主公"
        elif viewer == slot - 1 or p.dead:
            role = f" 身份={p.role}"
        hand = f" 手牌={len(p.hand)}张"
        if viewer is not None and viewer == slot - 1 and not p.dead:
            labels = [f"{i + 1}.{card_label(c)}" for i, c in enumerate(p.hand)]
            hand = f" 手牌={'、'.join(labels) if labels else '（空）'}"
        gen = ""
        if p.general:
            k = f"·{p.kingdom}" if p.kingdom else ""
            gen = f" {p.general}{k}"
        chain = " 铁索" if p.chained else ""
        judge = ""
        if p.judge_lebu:
            judge += " 乐"
        if p.judge_bingliang:
            judge += " 兵粮"
        equip = ""
        if self.state == "playing" and (
            p.weapon or p.armor or p.horse_plus or p.horse_minus
        ):
            bits: list[str] = []
            if p.weapon:
                bits.append(f"武={card_label(p.weapon)}")
            if p.armor:
                bits.append(f"防={card_label(p.armor)}")
            if p.horse_plus:
                bits.append(f"+1马={card_label(p.horse_plus)}")
            if p.horse_minus:
                bits.append(f"-1马={card_label(p.horse_minus)}")
            equip = "  " + " ".join(bits)
        return (
            f"  #{slot} {p.name}{dead}{gen}{role}{chain}{judge}{equip}  "
            f"体力 {max(0, p.hp)}/{p.max_hp}{hand}"
        )

    def _pending_hint(self) -> str:
        if not self._pending:
            return ""
        kind = self._pending.get("kind", "")
        if kind == "sha":
            tgt = self.players[self._pending["target"]]
            need = self._pending.get("need_shan", 1)
            extra = ""
            if self._has_skill(tgt, "liuli"):
                extra = "；可 /game move 流离 <牌> <转移目标>"
            return (
                f"  【待响应】{tgt.name} 需 {need} 张【闪】或受击{extra}"
            )
        if kind == "duel":
            who = self.players[self._pending["turn"]]
            return f"  【决斗】轮到 {who.name} 出【杀】或 /game move 受击"
        if kind == "zhiba":
            lord = self.players[self._pending["lord"]]
            init = self.players[self._pending["initiator"]]
            extra = (
                " 或 /game move 拒制霸"
                if lord.hunzi_awakened
                else ""
            )
            return (
                f"  【制霸】{init.name} 向主公 {lord.name} 拼点："
                f"/game move 拼点 <牌>{extra}"
            )
        if kind in ("nanman", "wanjian"):
            who = self.players[self._pending["turn"]]
            label = self._pending.get("label", "")
            need = "杀" if kind == "nanman" else "闪"
            return f"  【{label}】轮到 {who.name} 出【{need}】或 /game move 受击"
        if kind == "guanxing":
            who = self.players[self._pending["who"]]
            n = len(self._pending.get("cards", []))
            return (
                f"  【观星】{who.name}：/game move 观星 <1～{n} 的排列> 或 观星 过"
            )
        if kind == "huogong":
            phase = self._pending.get("phase", "show")
            src = self.players[self._pending["source"]]
            tgt = self.players[self._pending["target"]]
            if phase == "show":
                return (
                    f"  【火攻】{tgt.name} 请出示一张手牌："
                    "/game move 出示 <牌名>"
                )
            suit = self._pending.get("shown_suit", "")
            return (
                f"  【火攻】{src.name} 可弃一张【{suit}】牌造成 1 点火焰伤害："
                "/game move 火攻 <牌名>  或  /game move 过"
            )
        if kind in ("shunshou", "dismantle"):
            who = self.players[self._pending["source"]]
            verb = "顺手" if kind == "shunshou" else "拆"
            name = "顺手牵羊" if kind == "shunshou" else "过河拆桥"
            return (
                f"  【{name}】{who.name} 选择区域："
                f"/game move {verb} <手牌|武器|防具|+1马|-1马>"
            )
        if kind == "cixiong":
            tgt = self.players[self._pending["target"]]
            return (
                f"  【雌雄双股剑】{tgt.name}："
                f"/game move 雌雄弃 <牌> 或 /game move 雌雄摸"
            )
        return ""

    def show(self, conn=None, *, full: bool = False) -> list[str]:
        viewer = self._who_of(conn) if conn is not None else None
        lines = [
            f"三国杀·军争 {self.state}  "
            f"{len(self.players)}人  牌堆{len(self._deck)}  弃{len(self._discard)}",
        ]
        lines.append(self._seat_order_line())
        for i, p in enumerate(self.players, 1):
            lines.append(self._public_status(p, i, viewer))
        lines.extend(self._distance_lines(viewer))
        if self.state == "waiting":
            host = self.players[0].name
            n = len(self.players)
            if n < _SGS_MAX_PLAYERS:
                lines.append(f"  空席：/game join（当前 {n}/{_SGS_MAX_PLAYERS}）")
            if n < _SGS_MIN_PLAYERS:
                lines.append(
                    f"  至少 {_SGS_MIN_PLAYERS} 人后可开局"
                    f"（还差 {_SGS_MIN_PLAYERS - n} 人）"
                )
            else:
                lines.append(
                    f"  房主 {host} 执行 /game move 开始 即可开局"
                    f"（满员前仍可 join）"
                )
            lines.append("  /game move 武将  查看军争武将池")
            return lines
        hint = self._pending_hint()
        if hint:
            lines.append(hint)
        if self.state == "playing" and not self._pending:
            cur = self._current()
            if not cur.dead:
                lines.append(f"  当前回合：#{self._turn_idx + 1} {cur.name}")
        if viewer is not None and self.state == "playing":
            vp = self.players[viewer]
            if vp.role and not vp.dead:
                lines.append(f"  你的身份：{vp.role}")
            if vp.skill_ids and not vp.dead:
                lines.append(f"  你的技能：{format_skills(vp.skill_ids)}")
                ginfo = SGS_GENERAL_BY_NAME.get(vp.general or "")
                if ginfo and ginfo.get("desc"):
                    lines.append(f"    {ginfo['desc']}")
        if (
            self._pending
            and self._pending.get("kind") == "guanxing"
            and viewer == self._pending.get("who")
        ):
            for i, c in enumerate(self._pending.get("cards", []), 1):
                lines.append(f"  观星 #{i} 【{card_label(c)}】")
        if full:
            lines.append(
                "  出牌：杀/火杀/雷杀 <目标> [牌名|丈八:<牌1> <牌2>] | 桃 | 闪 | 酒 | 决斗 | "
                "拆/过河拆桥 <目标> [区域] | 顺手/顺手牵羊 <目标> [区域] | 无中生有 | 南蛮 | "
                "万箭 | 兵粮/铁索 <目标1> <目标2> | 铁索 重铸 | "
                "五谷/桃园/火攻 <目标> | 装备 <牌名> | "
                "反间 <目标> <花色> <牌> | 蛊惑 | 观星 | 武将 | 过"
            )
        else:
            lines.append("  指令详情：/game show 帮助")
        return lines

    def _guanxing_start(self, actor_idx: int) -> list[str]:
        actor = self.players[actor_idx]
        n = min(5, len(self._deck))
        if n == 0:
            return []
        cards = [self._deck.pop() for _ in range(n)]
        self._pending = {
            "kind": "guanxing",
            "cards": cards,
            "who": actor_idx,
        }
        return [
            f"{actor.name}（观星）观看牌堆顶 {n} 张，"
            "/game show 查看，/game move 观星 <序号…> 调整（例：观星 3 1 2 5 4）"
        ]

    def _guanxing_resolve(self, who: int, args: list[str]) -> GameResult:
        pend = self._pending
        if not pend or pend.get("kind") != "guanxing":
            return (["当前没有观星。"], [], False)
        if who != pend["who"]:
            return (["不是你的观星。"], [], False)
        cards: list[str] = list(pend["cards"])
        if not args or (len(args) == 1 and args[0] in ("过", "pass")):
            ordered = cards
        else:
            if len(args) != len(cards):
                return (
                    [f"请给出 {len(cards)} 个序号（1～{len(cards)}），或 观星 过"],
                    [],
                    False,
                )
            try:
                idxs = [int(x) - 1 for x in args]
            except ValueError:
                return (["序号须为数字。"], [], False)
            if sorted(idxs) != list(range(len(cards))):
                return (["序号须为 1～N 的不重复排列。"], [], False)
            ordered = [cards[i] for i in idxs]
        for c in reversed(ordered):
            self._deck.append(c)
        self._pending = None
        actor = self.players[who]
        bcast = [f"{actor.name}（观星）已将 {len(ordered)} 张放回牌堆顶"]
        bcast.extend(self._turn_draw_core())
        actor.drew_this_turn = True
        bcast.extend(self._auto_skip_play_phase(who))
        return ([], bcast, False)

    def _turn_draw_core(self) -> list[str]:
        actor = self.players[self._turn_idx]
        msgs: list[str] = []
        n = _SGS_DRAW_PER_TURN + self._draw_phase_extra(actor)
        got = self._draw_cards(actor, n)
        if self._has_skill(actor, "luoshen"):
            luoshen_n = 0
            while self._deck:
                c = self._deck.pop()
                actor.hand.append(c)
                luoshen_n += 1
                if is_red(c):
                    break
            if luoshen_n:
                msgs.append(f"{actor.name}（洛神）翻牌入手 {luoshen_n} 张")
        if self._has_skill(actor, "haoshi") and len(actor.hand) >= 2:
            others = [
                i
                for i in self._alive_indices()
                if i != self._turn_idx
            ]
            if others:
                min_len = min(len(self.players[i].hand) for i in others)
                targets = [
                    i
                    for i in others
                    if len(self.players[i].hand) == min_len
                ]
                for t in targets[:2]:
                    if len(actor.hand) < 2:
                        break
                    c = actor.hand.pop()
                    self.players[t].hand.append(c)
                    msgs.append(
                        f"{actor.name}（好施）将【{card_label(c)}】"
                        f"交给 {self.players[t].name}"
                    )
        if got:
            msgs.append(f"{actor.name} 摸 {got} 张")
        return msgs

    def _draw_phase_extra(self, actor: _SgsPlayer) -> int:
        extra = 0
        if self._has_skill(actor, "yingzi"):
            extra += 1
        if self._has_skill(actor, "zaoxian") and actor.zaoxian_awakened:
            extra += 1
        if self._has_skill(actor, "haoshi"):
            extra += 2
        if self._has_skill(actor, "yicong"):
            alive = self._alive_indices()
            if alive:
                dists = [
                    self._calc_distance(self._turn_idx, i)
                    for i in alive
                    if i != self._turn_idx
                ]
                if dists and min(dists) >= 2:
                    extra += 1
        return extra

    def _assign_setup(self) -> list[str]:
        n = len(self.players)
        roles = list(_SGS_ROLE_TABLE[n])
        self._rng.shuffle(roles)
        pool = list(SGS_GENERAL_POOL)
        self._rng.shuffle(pool)
        self._deck = build_junzheng_deck()
        self._rng.shuffle(self._deck)
        self._discard = []
        self._tiandu_offer = {}
        lines = [f"三国杀·军争开始！玩家：{self._roster_names()}"]
        for i, p in enumerate(self.players):
            p.role = roles[i]
            g = pool[i]
            p.general = g["name"]
            p.kingdom = g["kingdom"]
            p.skill_ids = g["skills"]
            p.max_hp = g["hp"]
            if self._has_skill(p, "buqu"):
                p.max_hp += 1
            if p.role == "主公":
                p.max_hp += 1
            p.hp = p.max_hp
            p.dead = False
            p.sha_used = 0
            p.jiu_buff = False
            p.luoyi_buff = False
            p.skip_play = False
            p.skip_draw = False
            p.judge_lebu = False
            p.judge_bingliang = False
            p.drew_this_turn = False
            p.chained = False
            p.niepan_used = False
            p.fanjian_used = False
            p.shuangxiong_color = None
            p.zaoxian_awakened = False
            p.hunzi_awakened = False
            p.zhiba_used = False
            p.shuangxiong_duel_used = False
            p.weapon = None
            p.armor = None
            p.horse_plus = None
            p.horse_minus = None
            if self._has_skill(p, "huashen"):
                donors = [g for g in pool if g["name"] != p.general and g["skills"]]
                if donors:
                    d = self._rng.choice(donors)
                    extra_s = self._rng.choice(d["skills"])
                    p.skill_ids = p.skill_ids + (extra_s,)
            init = _SGS_INITIAL_HAND
            drawn = self._draw_cards(p, init)
            lines.append(
                f"  #{i + 1} {p.name}：{p.general}·{p.kingdom} "
                f"（体力 {p.max_hp}）— {g['desc']}"
            )
            if drawn < init:
                lines.append(f"    （牌堆不足，仅发到 {drawn} 张）")
        lord_idx = self._lord_index()
        self._turn_idx = lord_idx
        lord = self.players[lord_idx]
        lines.append(f"主公：#{lord_idx + 1} {lord.name}（身份公开）")
        lines.append("其余身份请各玩家 /game show 私下查看；/game move 武将 查看武将池。")
        lines.append(f"轮到 #{lord_idx + 1} {lord.name} 的回合")
        return lines

    def _flip_judge_card(self) -> Optional[str]:
        if not self._deck:
            if not self._discard:
                return None
            self._rng.shuffle(self._discard)
            self._deck = self._discard[:]
            self._discard = []
        if not self._deck:
            return None
        return self._deck.pop()

    def _run_judge(
        self, who_idx: int, label: str, *, escape_suit: str
    ) -> tuple[bool, list[str]]:
        """判定：escape_suit 则锦囊无效。返回 (是否生效, 战报行)。"""
        actor = self.players[who_idx]
        card = self._flip_judge_card()
        lines: list[str] = []
        if card is None:
            lines.append(f"{actor.name}【{label}】判定：牌堆空，视为不生效")
            return False, lines
        self._discard.append(card)
        suit = card_suit(card)
        lines.append(
            f"{actor.name}【{label}】判定【{card_label(card)}】"
            f"（需非{escape_suit}才生效）"
        )
        if suit == escape_suit:
            lines.append(f"  → 判定为{escape_suit}，【{label}】无效并弃置")
            self._offer_tiandu(who_idx, card, lines)
            return False, lines
        lines.append(f"  → 【{label}】生效")
        self._offer_tiandu(who_idx, card, lines)
        return True, lines

    def _judge_phase(self, who_idx: int) -> list[str]:
        """回合开始判定阶段：兵粮寸断、乐不思蜀。"""
        p = self.players[who_idx]
        msgs: list[str] = []
        if p.judge_bingliang:
            p.judge_bingliang = False
            ok, part = self._run_judge(who_idx, "兵粮寸断", escape_suit="梅花")
            msgs.extend(part)
            if ok:
                p.skip_draw = True
                msgs.append(
                    f"{p.name} 本回合跳过摸牌阶段（兵粮寸断判定生效）"
                )
        if p.judge_lebu:
            p.judge_lebu = False
            ok, part = self._run_judge(who_idx, "乐不思蜀", escape_suit="红桃")
            msgs.extend(part)
            if ok:
                p.skip_play = True
                msgs.append(
                    f"{p.name} 本回合跳过出牌阶段（乐不思蜀判定生效）"
                )
        return msgs

    def _auto_skip_play_phase(self, who_idx: int) -> list[str]:
        """乐不思蜀生效后自动结束出牌阶段，无需玩家输入「过」。"""
        p = self.players[who_idx]
        if not p.skip_play or who_idx != self._turn_idx:
            return []
        p.skip_play = False
        msgs = [f"{p.name} 出牌阶段自动跳过（乐不思蜀判定生效）"]
        msgs.extend(self._finish_turn(who_idx))
        return msgs

    def _catchup_turn_start(self, who_idx: int) -> list[str]:
        """若回合开始判定/摸牌未结算（不应依赖玩家输入），在此补跑。"""
        p = self.players[who_idx]
        msgs: list[str] = []
        if p.judge_lebu or p.judge_bingliang:
            msgs.extend(self._judge_phase(who_idx))
        if p.skip_draw and not p.drew_this_turn:
            p.skip_draw = False
            msgs.append(f"{p.name} 摸牌阶段已跳过（兵粮寸断）")
        elif not p.drew_this_turn and not self._pending:
            msgs.extend(self._turn_draw_core())
            p.drew_this_turn = True
        msgs.extend(self._auto_skip_play_phase(who_idx))
        return msgs

    def _begin_turn_draw(self) -> list[str]:
        who = self._turn_idx
        actor = self.players[who]
        actor.sha_used = 0
        actor.jiu_buff = False
        actor.luoyi_buff = False
        actor.fanjian_used = False
        actor.shuangxiong_duel_used = False
        actor.zhiba_used = False
        actor.drew_this_turn = False
        msgs: list[str] = []
        msgs.extend(self._prepare_phase(who))
        msgs.extend(self._judge_phase(who))
        if actor.skip_draw:
            actor.skip_draw = False
            msgs.append(f"{actor.name} 摸牌阶段已跳过（兵粮寸断）")
        elif self._has_skill(actor, "guanxing"):
            msgs.extend(self._guanxing_start(who))
            return msgs
        msgs.extend(self._turn_draw_core())
        actor.drew_this_turn = True
        msgs.extend(self._auto_skip_play_phase(who))
        return msgs

    def _end_turn_discard(
        self, actor: _SgsPlayer, discard_indices: Optional[list[int]] = None
    ) -> list[str]:
        msgs: list[str] = []
        dropped = 0
        need = max(0, len(actor.hand) - actor.hp)
        if need > 0 and discard_indices:
            picked = sorted(discard_indices, reverse=True)
            for idx in picked:
                if 0 <= idx < len(actor.hand):
                    card = actor.hand.pop(idx)
                    self._discard.append(card)
                    dropped += 1
        while dropped < need and len(actor.hand) > actor.hp:
            card = actor.hand.pop()
            self._discard.append(card)
            dropped += 1
        if dropped:
            msgs.append(
                f"{actor.name} 弃 {dropped} 张（上限 {actor.hp}）"
            )
        if self._has_skill(actor, "biyue") and not actor.hand:
            if self._draw_cards(actor, 1):
                msgs.append(f"{actor.name} 闭月+1牌")
        if self._has_skill(actor, "zaoxian") and not actor.zaoxian_awakened:
            if not actor.hand:
                actor.zaoxian_awakened = True
                actor.max_hp += 1
                actor.hp += 1
                msgs.append(f"{actor.name} 凿险觉醒+1上限")
        return msgs

    def _next_alive_turn(self) -> tuple[_SgsPlayer, list[str]]:
        n = len(self.players)
        for _ in range(n):
            self._turn_idx = (self._turn_idx + 1) % n
            p = self.players[self._turn_idx]
            if not p.dead:
                return p, self._begin_turn_draw()
        return self.players[self._turn_idx], []

    def _damage(
        self,
        target_idx: int,
        source_idx: Optional[int],
        amount: int = 1,
        *,
        reactions: bool = True,
        damage_card: Optional[str] = None,
        element: str = "normal",
        from_sha: bool = False,
    ) -> list[str]:
        p = self.players[target_idx]
        if p.dead or amount <= 0:
            return []
        if amount > 1 and self._has_armor(p, "白银狮子"):
            amount = 1
        if self._has_armor(p, "藤甲"):
            if element == "fire":
                amount += 1
            elif element == "normal":
                amount = max(1, amount - 1)
        notes: list[str] = []
        dealt = 0
        for _ in range(amount):
            if p.dead:
                break
            p.hp -= 1
            dealt += 1
            if reactions:
                self._shangshi_draw(p, notes)
            if p.hp <= 0:
                p.hp = 0
                if not p.niepan_used and self._has_skill(p, "niepan"):
                    p.niepan_used = True
                    p.hand.clear()
                    p.weapon = None
                    p.armor = None
                    p.horse_plus = None
                    p.horse_minus = None
                    p.hp = 3
                    p.dead = False
                    notes.append(f"{p.name}涅槃弃全部牌至3体力")
                else:
                    p.dead = True
                    notes.append(f"{p.name}阵亡（{p.role}）")
                    if source_idx is not None and self._has_skill(p, "benggu"):
                        killer = self.players[source_idx]
                        killer.skill_ids = ()
                        notes.append(f"{killer.name}断肠失技能")
                    self._huoshou_kill_draw(source_idx, notes)
        lines: list[str] = []
        if dealt:
            lines.append(
                f"{p.name} -{dealt} → {max(0, p.hp)}/{p.max_hp}"
            )
        if (
            from_sha
            and source_idx is not None
            and not p.dead
            and dealt > 0
        ):
            src = self.players[source_idx]
            if src.weapon and card_base(src.weapon) == "麒麟弓":
                self._qilin_discard_horse(target_idx, notes)
        if reactions and dealt > 0 and not p.dead and self._has_skill(p, "yiji"):
            n = self._draw_cards(p, 2)
            if n:
                notes.append(f"{p.name}遗计+{n}牌")
        if reactions and source_idx is not None and not p.dead:
            src = self.players[source_idx]
            if self._has_skill(p, "ganglie"):
                lines.extend(
                    self._damage(source_idx, None, 1, reactions=False)
                )
            if self._has_skill(p, "jianxiong"):
                taken = False
                if damage_card and damage_card in self._discard:
                    self._discard.remove(damage_card)
                    p.hand.append(damage_card)
                    notes.append(
                        f"{p.name}奸雄得【{card_label(damage_card)}】"
                    )
                    taken = True
                if not taken and self._draw_cards(p, 1):
                    notes.append(f"{p.name}奸雄+1牌")
            if self._has_skill(p, "fankui") and src.hand:
                stolen = self._rng.choice(src.hand)
                src.hand.remove(stolen)
                p.hand.append(stolen)
                notes.append(
                    f"{p.name}反馈得【{card_label(stolen)}】"
                )
            if self._has_skill(p, "fangzhu") and src.hand:
                c = src.hand.pop()
                self._discard.append(c)
                notes.append(f"{src.name}被放逐弃【{card_label(c)}】")
        if reactions:
            self._kuanggu_check(target_idx, notes)
            self._chain_spread_damage(target_idx, element, notes)
        if notes:
            lines.append("  " + "；".join(notes))
        return lines

    def _tiesuo_toggle_player(self, idx: int) -> str:
        """横置/重置武将牌：已在连环则解除，否则进入连环。"""
        p = self.players[idx]
        if p.chained:
            p.chained = False
            return f"{p.name} 解除连环"
        p.chained = True
        return f"{p.name} 进入连环"

    def _chain_spread_damage(self, origin: int, element: str, notes: list[str]) -> None:
        if not self.players[origin].chained:
            return
        if element not in ("fire", "thunder"):
            return
        self.players[origin].chained = False
        hit: list[str] = []
        for i, other in enumerate(self.players):
            if i != origin and other.chained and not other.dead:
                other.chained = False
                hit.append(other.name)
                self._damage(i, None, 1, reactions=True)
        if hit:
            notes.append(f"铁索连环传导：{'、'.join(hit)}各受1点伤害")

    def _check_win(self) -> Optional[str]:
        alive = self._alive_indices()
        if not alive:
            return "无人存活，平局"
        lord_alive = any(
            self._is_alive(self.players[i]) and self.players[i].role == "主公"
            for i in range(len(self.players))
        )
        rebels_alive = any(
            self._is_alive(self.players[i]) and self.players[i].role == "反贼"
            for i in range(len(self.players))
        )
        traitor_alive = any(
            self._is_alive(self.players[i]) and self.players[i].role == "内奸"
            for i in range(len(self.players))
        )
        if not lord_alive:
            if len(alive) == 1 and self.players[alive[0]].role == "内奸":
                return "内奸胜利！"
            return "反贼阵营胜利！"
        if not rebels_alive and not traitor_alive:
            return "主公阵营胜利！"
        if len(alive) == 1 and traitor_alive:
            return "内奸胜利！"
        return None

    def _maybe_end(self, bcast: list[str]) -> GameResult:
        win = self._check_win()
        if win:
            self.state = "ended"
            bcast.append(f"对局结束：{win}")
            return ([], bcast, True)
        return ([], bcast, False)

    def _start_playing(self) -> list[str]:
        self.state = "playing"
        bcast = self._assign_setup()
        bcast.extend(self._begin_turn_draw())
        return bcast

    def try_join(self, conn, name: str) -> GameResult:
        if self.state == "ended":
            return (
                [f"对局已结束，请先 /game new {self.name} 开新局。"],
                [],
                False,
            )
        if self._who_of(conn) is not None:
            return (["你已经在座位上。"], [], False)
        if self.state != "waiting":
            return (["对局已开始，无法中途加入。"], [], False)
        if len(self.players) >= _SGS_MAX_PLAYERS:
            return ([f"座位已满（最多 {_SGS_MAX_PLAYERS} 人）。"], [], False)
        self.players.append(_SgsPlayer(conn, name))
        n = len(self.players)
        priv = [
            f"{name} 入座（{n}/{_SGS_MAX_PLAYERS}）。",
            "你的身份将在开局后通过 /game show 查看。",
        ]
        if n < _SGS_MIN_PLAYERS:
            priv.append(
                f"至少 {_SGS_MIN_PLAYERS} 人后可由房主 /game move 开始 开局。"
            )
        elif n < _SGS_MAX_PLAYERS:
            priv.append("房主可随时 /game move 开始；座位未满仍可 join。")
        else:
            priv.append("人数已满，请房主 /game move 开始。")
        bcast = [f"{name} 加入，当前：{self._roster_names()}（{n}/{_SGS_MAX_PLAYERS}）"]
        if n >= _SGS_MIN_PLAYERS:
            host = self.players[0].name
            bcast.append(f"房主 {host} 可 /game move 开始 开局")
        return (priv, bcast, False)

    def _finish_turn(
        self, actor_idx: int, discard_indices: Optional[list[int]] = None
    ) -> list[str]:
        actor = self.players[actor_idx]
        msgs = self._end_turn_discard(actor, discard_indices=discard_indices)
        nxt, draw_msgs = self._next_alive_turn()
        msgs.extend(draw_msgs)
        return msgs

    def _sha_limit(self, actor: _SgsPlayer) -> int:
        if self._has_skill(actor, "paoxiao"):
            return 99
        if actor.weapon and card_base(actor.weapon) == "诸葛连弩":
            return 99
        if self._has_skill(actor, "tianyi"):
            return 2
        return 1

    def _sha_element(self, actor: _SgsPlayer, sha_card: str) -> str:
        base = card_base(sha_card)
        if base == "火杀":
            return "fire"
        if base == "雷杀":
            return "thunder"
        if (
            actor.weapon
            and card_base(actor.weapon) == "朱雀羽扇"
            and base == "杀"
        ):
            return "fire"
        return "normal"

    def _renwang_blocks_sha(
        self,
        sha_card: str,
        target: _SgsPlayer,
        actor_idx: int,
        target_idx: int,
        *,
        zhangba_cards: Optional[tuple[str, str]] = None,
    ) -> bool:
        if self._ignore_target_armor(actor_idx, target_idx):
            return False
        if not self._has_armor(target, "仁王盾"):
            return False
        if card_base(sha_card) in ("火杀", "雷杀"):
            return False
        if zhangba_cards is not None:
            return all(is_black(c) for c in zhangba_cards)
        if card_base(sha_card) == "杀":
            return is_black(sha_card)
        return is_black(sha_card)

    def _sha_limit_ok(self, actor: _SgsPlayer) -> bool:
        return actor.sha_used < self._sha_limit(actor)

    def _sha_base_damage(
        self, actor: _SgsPlayer, target: _SgsPlayer, *, sha_card: str
    ) -> int:
        dmg = 1
        if actor.jiu_buff or actor.luoyi_buff:
            dmg += 1
            actor.jiu_buff = False
            actor.luoyi_buff = False
        if self._has_skill(actor, "liegong") and target.hp >= actor.hp:
            dmg += 1
        if (
            actor.weapon
            and card_base(actor.weapon) == "古锭刀"
            and not target.hand
        ):
            dmg += 1
        return dmg

    def _do_sha(
        self,
        actor_idx: int,
        target_idx: int,
        *,
        card_token: Optional[str] = None,
        zhangba_tokens: Optional[tuple[str, str]] = None,
        sha_kind: Optional[str] = None,
    ) -> GameResult:
        actor = self.players[actor_idx]
        target = self.players[target_idx]
        if target.dead:
            return (["目标已阵亡。"], [], False)
        if not self._in_attack_range(actor_idx, target_idx):
            dist = self._calc_distance(actor_idx, target_idx)
            ar = self._attack_range(actor_idx)
            return (
                [
                    f"目标超出攻击范围（距离{dist}，你的杀距{ar}）。"
                    "  /game show 查看座次与距离。"
                ],
                [],
                False,
            )
        if not self._sha_limit_ok(actor):
            return (["本回合【杀】次数已用完。"], [], False)
        zhangba_labels: Optional[list[str]] = None
        zhangba_cards: Optional[tuple[str, str]] = None
        if zhangba_tokens:
            if not self._has_zhangba(actor):
                return (["你没有装备【丈八蛇矛】。"], [], False)
            consumed = self._consume_zhangba_sha(
                actor, zhangba_tokens[0], zhangba_tokens[1]
            )
            if consumed is None:
                return (
                    [
                        "请将两张不同的手牌作为【杀】打出"
                        "（/game move 杀 <目标> <牌1> <牌2>）"
                    ],
                    [],
                    False,
                )
            card, zhangba_labels, zhangba_cards = consumed
        else:
            card = self._consume_sha(actor, token=card_token, sha_kind=sha_kind)
            if card is None:
                if sha_kind:
                    return ([f"你没有【{sha_kind}】。"], [], False)
                if card_token:
                    return ([f"你没有【{card_token}】。"], [], False)
                hint = "你没有【杀】。"
                if self._has_zhangba(actor) and len(actor.hand) >= 2:
                    hint += (
                        " 装备【丈八蛇矛】时可用两张手牌："
                        "/game move 杀 <目标> <牌1> <牌2>"
                    )
                return ([hint], [], False)
        sha_label = self._format_sha_label(card, zhangba_labels)
        if self._renwang_blocks_sha(
            card,
            target,
            actor_idx,
            target_idx,
            zhangba_cards=zhangba_cards,
        ):
            actor.sha_used += 1
            return (
                [],
                [
                    f"{actor.name}{sha_label}→{target.name}，"
                    f"【仁王盾】无效（黑色【杀】）"
                ],
                False,
            )
        actor.sha_used += 1
        element = self._sha_element(actor, card)
        if self._has_skill(actor, "tieqi"):
            need_shan = 0
        elif self._has_skill(target, "liegong") and target.hp <= actor.hp:
            need_shan = 0
        elif self._has_skill(target, "wushuang"):
            need_shan = 2
        else:
            need_shan = 1
        self._pending = {
            "kind": "sha",
            "source": actor_idx,
            "target": target_idx,
            "need_shan": need_shan,
            "got_shan": 0,
            "damage": self._sha_base_damage(actor, target, sha_card=card),
            "sha_card": card,
            "sha_label": sha_label,
            "element": element,
            "xiangle": self._has_skill(target, "xiangle"),
        }
        bagua_prefix: list[str] = []
        if need_shan > 0 and self._can_bagua_for_target(actor_idx, target_idx):
            got, bagua_lines, dodged = self._bagua_try_for_need(
                actor_idx, target_idx, 0, need_shan
            )
            if bagua_lines:
                bagua_prefix = bagua_lines
            self._pending["got_shan"] = got
            if dodged:
                self._pending = None
                bcast = [
                    f"{actor.name}{sha_label}→{target.name}"
                ] + bagua_prefix + [f"{target.name} 闪避【杀】（八卦阵）。"]
                return ([], bcast, False)
        tags: list[str] = []
        if self._pending["xiangle"]:
            tags.append(f"{target.name}可享乐")
        if self._has_skill(target, "liuli"):
            tags.append(f"{target.name}可流离")
        if need_shan == 0:
            tags.append("不可闪")
        elif need_shan > 1:
            tags.append(f"需{need_shan}闪")
        else:
            tags.append(f"{target.name}出闪或受击")
        tag_s = f"（{'，'.join(tags)}）" if tags else ""
        bcast = [f"{actor.name}{sha_label}→{target.name}{tag_s}"]
        if bagua_prefix:
            bcast = bagua_prefix + bcast
        if is_red_sha(card):
            self._jiang_try_draw(actor, bcast)
            self._jiang_try_draw(target, bcast)
        if need_shan == 0 and not self._pending["xiangle"]:
            dmg = self._pending["damage"]
            card_used = self._pending["sha_card"]
            elem = self._pending.get("element", "normal")
            self._pending = None
            notes: list[str] = []
            bcast.extend(
                self._damage(
                    target_idx,
                    actor_idx,
                    dmg,
                    damage_card=card_used,
                    element=elem,
                    from_sha=True,
                )
            )
            return self._sha_damage_followup(
                actor_idx, target_idx, bcast, notes
            )
        return ([], bcast, False)

    def _resolve_sha_response(
        self, who: int, verb: str, args: Optional[list[str]] = None
    ) -> GameResult:
        pend = self._pending
        if not pend or pend["kind"] != "sha":
            return (["当前没有待响应的【杀】。"], [], False)
        if who != pend["target"]:
            return (["不是你受到的【杀】。"], [], False)
        target = self.players[who]
        if verb == "xiangle" and pend.get("xiangle"):
            if not args or len(args) < 2:
                return (
                    ["用法：/game move 享乐 <牌1> <牌2> 弃2张抵消【杀】"],
                    [],
                    False,
                )
            removed: list[str] = []
            for token in args[:2]:
                found = find_card_in_hand(target.hand, token)
                if found is None:
                    return ([f"你没有【{token}】。"], [], False)
                target.hand.remove(found)
                self._discard.append(found)
                removed.append(card_label(found))
            self._pending = None
            return (
                [],
                [f"{target.name}（享乐）弃置【{'】【'.join(removed)}】，【杀】无效"],
                False,
            )
        if verb == "liuli" and self._has_skill(target, "liuli"):
            src_idx = pend["source"]
            if len(args) < 2:
                return (
                    [
                        "用法：/game move 流离 <弃牌> <转移目标> "
                        "（不能转给出杀者或自己）"
                    ],
                    [],
                    False,
                )
            card_tok = args[0]
            found = find_card_in_hand(target.hand, card_tok)
            if found is None:
                return ([f"你没有【{card_tok}】。"], [], False)
            redir = self._resolve_target(who, args[1:])
            if redir is None or redir == who or redir == src_idx:
                return (["无效转移目标（不能为自己或出【杀】者）。"], [], False)
            if not self._is_alive(self.players[redir]):
                return (["转移目标已阵亡。"], [], False)
            target.hand.remove(found)
            self._discard.append(found)
            new_tgt = self.players[redir]
            pend["target"] = redir
            pend["xiangle"] = self._has_skill(new_tgt, "xiangle")
            pend["got_shan"] = 0
            need = pend.get("need_shan", 1)
            tags = [f"{new_tgt.name}出闪或受击"]
            if pend["xiangle"]:
                tags.insert(0, f"{new_tgt.name}可享乐")
            bcast = [
                f"{target.name}（流离）弃【{card_label(found)}】，"
                f"【杀】→{new_tgt.name}（{'，'.join(tags)}）"
            ]
            if need > 0 and self._can_bagua_for_target(src_idx, redir):
                got, bagua_lines, dodged = self._bagua_try_for_need(
                    src_idx, redir, 0, need
                )
                if bagua_lines:
                    bcast = bagua_lines + bcast
                pend["got_shan"] = got
                if dodged:
                    self._pending = None
                    bcast.append(f"{new_tgt.name} 闪避【杀】（八卦阵）。")
                    return ([], bcast, False)
            return ([], bcast, False)
        if verb == "tianxiang" and self._has_skill(target, "tianxiang"):
            if len(args) < 2:
                return (
                    [
                        "用法：/game move 天香 <牌> <转移目标> "
                        "（将伤害转移给该角色）"
                    ],
                    [],
                    False,
                )
            card_tok = args[0]
            found = find_card_in_hand(target.hand, card_tok)
            if found is None:
                return ([f"你没有【{card_tok}】。"], [], False)
            redir = self._resolve_target(who, args[1:])
            if redir is None or redir == who:
                return (["无效转移目标。"], [], False)
            target.hand.remove(found)
            self._discard.append(found)
            src = pend["source"]
            dmg = pend.get("damage", 1)
            card_used = pend.get("sha_card")
            elem = pend.get("element", "normal")
            self._pending = None
            bcast = [
                f"{target.name}（天香）弃【{card_label(found)}】，"
                f"伤害转移给 {self.players[redir].name}"
            ]
            notes: list[str] = []
            bcast.extend(
                self._damage(
                    redir,
                    src,
                    dmg,
                    damage_card=card_used,
                    element=elem,
                    from_sha=True,
                )
            )
            return self._sha_damage_followup(src, redir, bcast, notes)
        if verb == "xiangle":
            return (["当前不能用享乐。"], [], False)
        if verb == "shan":
            src_idx = pend["source"]
            need = pend["need_shan"]
            if not self._consume_shan(target):
                return (["你没有【闪】。"], [], False)
            got = pend.get("got_shan", 0) + 1
            pend["got_shan"] = got
            if got >= need:
                self._pending = None
                return ([], [f"{target.name} 闪避【杀】。"], False)
            bcast = [f"{target.name} 打出【闪】（{got}/{need}）"]
            got, bagua_lines, dodged = self._bagua_try_for_need(
                src_idx, who, got, need
            )
            pend["got_shan"] = got
            if bagua_lines:
                bcast.extend(bagua_lines)
            if dodged:
                self._pending = None
                bcast.append(f"{target.name} 闪避【杀】（八卦阵）。")
                return ([], bcast, False)
            return ([], bcast, False)
        if verb in ("pass", "hurt"):
            self._pending = None
            src = pend["source"]
            dmg = pend.get("damage", 1)
            card_used = pend.get("sha_card")
            elem = pend.get("element", "normal")
            notes: list[str] = []
            bcast = [f"{target.name}未闪【杀】"]
            bcast.extend(
                self._damage(
                    who,
                    src,
                    dmg,
                    damage_card=card_used,
                    element=elem,
                    from_sha=True,
                )
            )
            return self._sha_damage_followup(src, who, bcast, notes)
        opts = ["闪", "受击"]
        if pend.get("xiangle"):
            opts.insert(0, "享乐")
        if self._has_skill(target, "tianxiang"):
            opts.insert(0, "天香")
        if self._has_skill(target, "liuli"):
            opts.insert(0, "流离")
        return ([f"请 {' / '.join(opts)}。"], [], False)

    def _duel_need_sha(self, player_idx: int) -> int:
        if self._has_skill(self.players[player_idx], "wushuang"):
            return 2
        return 1

    def _play_draw2(self, who: int, *, guhuo: bool = False) -> GameResult:
        player = self.players[who]
        if not guhuo and not self._remove_card(player, "无中生有"):
            return (["你没有【无中生有】。"], [], False)
        n = self._draw_cards(player, 2)
        tag = "蛊惑" if guhuo else ""
        pre = f"{player.name}{tag}" if tag else player.name
        notes: list[str] = []
        bcast = [f"{pre}【无中生有】+{n}牌"]
        self._jizhi_draw(player, notes)
        if notes:
            bcast.append("  " + "；".join(notes))
        return ([], bcast, False)

    def _random_from_hand(
        self, victim: _SgsPlayer
    ) -> tuple[Optional[str], Optional[str]]:
        """对方手牌对出牌者不可见，只能随机选一张。"""
        if not victim.hand:
            return None, f"{victim.name} 手牌为空。"
        return self._rng.choice(victim.hand), None

    _ZONE_SLOT_CN: dict[str, str] = {
        "weapon": "武器",
        "armor": "防具",
        "horse_plus": "+1马",
        "horse_minus": "-1马",
    }

    def _parse_zone_pick(self, token: str) -> Optional[str]:
        t = token.strip()
        aliases = {
            "手牌": "hand",
            "hand": "hand",
            "武器": "weapon",
            "weapon": "weapon",
            "防具": "armor",
            "armor": "armor",
            "+1马": "horse_plus",
            "防御马": "horse_plus",
            "horse_plus": "horse_plus",
            "-1马": "horse_minus",
            "进攻马": "horse_minus",
            "horse_minus": "horse_minus",
        }
        return aliases.get(t, aliases.get(t.lower()))

    def _available_victim_zones(
        self, victim: _SgsPlayer
    ) -> dict[str, Optional[str]]:
        """可选区域：hand→None（随机一张），装备槽→牌。"""
        zones: dict[str, Optional[str]] = {}
        if victim.hand:
            zones["hand"] = None
        for slot in self._equip_slots():
            card = self._get_equip(victim, slot)
            if card:
                zones[slot] = card
        return zones

    def _format_zone_pick_menu(self, zones: dict[str, Optional[str]]) -> list[str]:
        lines: list[str] = []
        if "hand" in zones:
            lines.append("  · 手牌（随机 1 张，牌面对他人保密）")
        for slot in self._equip_slots():
            card = zones.get(slot)
            if card:
                lines.append(
                    f"  · {self._ZONE_SLOT_CN[slot]}【{card_label(card)}】"
                )
        return lines

    def _take_victim_zone(
        self, victim: _SgsPlayer, zone: str, card: str
    ) -> None:
        if zone == "hand":
            victim.hand.remove(card)
        else:
            self._set_equip(victim, zone, None)

    def _trick_distance_err(
        self, who: int, tgt: int, *, max_dist: int = 1
    ) -> Optional[str]:
        d = self._calc_distance(who, tgt)
        if d > max_dist:
            return (
                f"与 {self.players[tgt].name} 距离为 {d}，"
                f"该锦囊需距离≤{max_dist}。"
            )
        return None

    def _play_equip(self, who: int, card_tok: str) -> GameResult:
        player = self.players[who]
        if not card_tok:
            return (
                ["用法：/game move 装备 <牌名>  （武器/防具/+1马/-1马）"],
                [],
                False,
            )
        found = find_card_in_hand(player.hand, card_tok)
        if found is None:
            return ([f"你没有【{card_tok}】。"], [], False)
        slot = equip_slot(found)
        if slot is None:
            return ([f"【{card_label(found)}】不是装备牌。"], [], False)
        player.hand.remove(found)
        old = self._get_equip(player, slot)
        if old:
            self._discard_equip(old)
        self._set_equip(player, slot, found)
        slot_cn = {
            "weapon": "武器",
            "armor": "防具",
            "horse_plus": "+1马(防御)",
            "horse_minus": "-1马(进攻)",
        }[slot]
        msg = f"{player.name} 装备【{card_label(found)}】→{slot_cn}"
        if old:
            msg += f"（换下【{card_label(old)}】）"
        ar = self._attack_range(who)
        return ([], [msg + f"  当前杀距{ar}"], False)

    def _finish_trick_zone_pick(
        self,
        kind: str,
        who: int,
        tgt: int,
        zone: str,
        *,
        guhuo: bool = False,
    ) -> GameResult:
        player = self.players[who]
        victim = self.players[tgt]
        zones = self._available_victim_zones(victim)
        if zone not in zones:
            slot_cn = "手牌" if zone == "hand" else self._ZONE_SLOT_CN.get(zone, zone)
            return ([f"{victim.name} 的{slot_cn}已无法选择。"], [], False)
        if zone == "hand":
            if not victim.hand:
                return ([f"{victim.name} 手牌为空。"], [], False)
            found = self._rng.choice(victim.hand)
        else:
            found = zones.get(zone) or self._get_equip(victim, zone)
            if not found:
                return ([f"{victim.name} 的{self._ZONE_SLOT_CN[zone]}已空。"], [], False)
        self._take_victim_zone(victim, zone, found)
        label = card_label(found)
        zone_cn = "手牌" if zone == "hand" else self._ZONE_SLOT_CN[zone]
        tag = "（蛊惑）" if guhuo else ""
        trick_name = "顺手牵羊" if kind == "shunshou" else "过河拆桥"
        notes: list[str] = []
        if kind == "shunshou":
            player.hand.append(found)
            if zone == "hand":
                bcast = [
                    f"{player.name}{tag}【{trick_name}】→{victim.name} "
                    "获得其一张手牌"
                ]
                self._queue_private(who, [f"你获得了【{label}】"])
                self._queue_private(tgt, [f"你失去了手牌【{label}】"])
            else:
                bcast = [
                    f"{player.name}{tag}【{trick_name}】→{victim.name} "
                    f"获得其{zone_cn}【{label}】"
                ]
                self._queue_private(tgt, [f"你失去了{zone_cn}【{label}】"])
        else:
            self._discard.append(found)
            bcast = [
                f"{player.name}{tag}【{trick_name}】→{victim.name} "
                f"弃掉其{zone_cn}【{label}】"
            ]
            if zone == "hand":
                self._queue_private(tgt, [f"你失去了手牌【{label}】"])
            else:
                self._queue_private(tgt, [f"你失去了{zone_cn}【{label}】"])
        self._jizhi_draw(player, notes)
        if notes:
            bcast.append("  " + "；".join(notes))
        return ([], bcast, False)

    def _begin_trick_zone_pick(
        self,
        kind: str,
        who: int,
        tgt: int,
        *,
        guhuo: bool = False,
        zone_arg: Optional[str] = None,
    ) -> GameResult:
        player = self.players[who]
        victim = self.players[tgt]
        if not self._can_trick_target(tgt):
            return self._trick_target_err(victim.name)
        trick_name = "顺手牵羊" if kind == "shunshou" else "过河拆桥"
        trick: Optional[str] = None
        if not guhuo:
            trick = self._find_trick_in_hand(player, trick_name)
            if trick is None:
                return ([f"你没有【{trick_name}】。"], [], False)
        dist_err = self._trick_distance_err(who, tgt)
        if dist_err:
            return ([dist_err], [], False)
        zones = self._available_victim_zones(victim)
        if not zones:
            return ([f"{victim.name} 无手牌且无装备。"], [], False)

        def consume_trick() -> None:
            if guhuo or trick is None:
                return
            player.hand.remove(trick)
            self._discard.append(trick)

        if zone_arg:
            zone = self._parse_zone_pick(zone_arg)
            if zone is None:
                return (
                    [
                        "无效区域，可选：手牌、武器、防具、+1马、-1马"
                    ],
                    [],
                    False,
                )
            if zone not in zones:
                want = "手牌" if zone == "hand" else self._ZONE_SLOT_CN.get(zone, zone)
                return ([f"{victim.name} 没有可选的{want}。"], [], False)
            consume_trick()
            return self._finish_trick_zone_pick(
                kind, who, tgt, zone, guhuo=guhuo
            )

        if len(zones) == 1:
            consume_trick()
            only = next(iter(zones))
            return self._finish_trick_zone_pick(
                kind, who, tgt, only, guhuo=guhuo
            )

        consume_trick()
        self._pending = {
            "kind": kind,
            "source": who,
            "target": tgt,
            "guhuo": guhuo,
        }
        verb_cn = "顺手" if kind == "shunshou" else "拆"
        menu = self._format_zone_pick_menu(zones)
        priv = [
            f"【{trick_name}】请选择 {victim.name} 的区域：",
            *menu,
            f"  /game move {verb_cn} <手牌|武器|防具|+1马|-1马>",
        ]
        tag = "（蛊惑）" if guhuo else ""
        bcast = [
            f"{player.name}{tag}对 {victim.name} 使用【{trick_name}】，"
            "等待选择区域…"
        ]
        return (priv, bcast, False)

    def _resolve_trick_zone_pick(
        self, who: int, verb: str, args: list[str]
    ) -> GameResult:
        pend = self._pending
        if not pend or pend.get("kind") not in ("shunshou", "dismantle"):
            return (["当前没有待选区域的锦囊。"], [], False)
        kind = pend["kind"]
        if who != pend["source"]:
            actor = self.players[pend["source"]]
            return ([f"等待 {actor.name} 选择区域。"], [], False)
        if verb != kind:
            verb_cn = "顺手" if kind == "shunshou" else "拆"
            return (
                [f"请用 /game move {verb_cn} <区域> 完成选择。"],
                [],
                False,
            )
        victim = self.players[pend["target"]]
        zones = self._available_victim_zones(victim)
        if not args:
            menu = self._format_zone_pick_menu(zones)
            verb_cn = "顺手" if kind == "shunshou" else "拆"
            return (
                [
                    "请选择区域：",
                    *menu,
                    f"  /game move {verb_cn} <手牌|武器|防具|+1马|-1马>",
                ],
                [],
                False,
            )
        zone = self._parse_zone_pick(args[0])
        if zone is None:
            return (
                ["无效区域，可选：手牌、武器、防具、+1马、-1马"],
                [],
                False,
            )
        if zone not in zones:
            want = "手牌" if zone == "hand" else self._ZONE_SLOT_CN.get(zone, zone)
            return ([f"{victim.name} 没有可选的{want}。"], [], False)
        guhuo = bool(pend.get("guhuo"))
        self._pending = None
        return self._finish_trick_zone_pick(
            kind, who, pend["target"], zone, guhuo=guhuo
        )

    def _play_dismantle(
        self,
        who: int,
        tgt: int,
        *,
        guhuo: bool = False,
        zone_arg: Optional[str] = None,
    ) -> GameResult:
        return self._begin_trick_zone_pick(
            "dismantle", who, tgt, guhuo=guhuo, zone_arg=zone_arg
        )

    def _play_shunshou(
        self,
        who: int,
        tgt: int,
        *,
        guhuo: bool = False,
        zone_arg: Optional[str] = None,
    ) -> GameResult:
        return self._begin_trick_zone_pick(
            "shunshou", who, tgt, guhuo=guhuo, zone_arg=zone_arg
        )

    def _play_bingliang(
        self,
        who: int,
        tgt: int,
        *,
        guhuo: bool = False,
        via_duanliang: bool = False,
    ) -> GameResult:
        player = self.players[who]
        if not self._can_trick_target(tgt):
            return self._trick_target_err(self.players[tgt].name)
        dist_err = self._trick_distance_err(who, tgt)
        if dist_err:
            return ([dist_err], [], False)
        if not guhuo and not via_duanliang:
            if not self._remove_card(player, "兵粮寸断"):
                return (["你没有【兵粮寸断】。"], [], False)
        victim = self.players[tgt]
        if victim.judge_bingliang:
            return ([f"{victim.name} 判定区已有【兵粮寸断】。"], [], False)
        victim.judge_bingliang = True
        src = "断粮" if via_duanliang else ("蛊惑" if guhuo else "兵粮寸断")
        notes: list[str] = []
        bcast = [
            f"{player.name} 对 {victim.name} 使用【{src}】，"
            "置于其判定区（回合开始时判定：梅花则无效，否则跳过摸牌）"
        ]
        self._jizhi_draw(player, notes)
        if notes:
            bcast.append("  " + "；".join(notes))
        return ([], bcast, False)

    def _do_guhuo(self, who: int, args: list[str]) -> GameResult:
        player = self.players[who]
        if len(args) < 2:
            return (
                [
                    "用法：/game move 蛊惑 <锦囊> [参数…] <牌>",
                    "  例：蛊惑 无中生有 红桃杀",
                    "      蛊惑 过河拆桥 yxt 黑桃3",
                    "      蛊惑 决斗 3 梅花杀",
                    "      蛊惑 南蛮 方块2",
                ],
                [],
                False,
            )
        card_tok = args[-1]
        found = find_card_in_hand(player.hand, card_tok)
        if found is None:
            return ([f"你没有【{card_tok}】。"], [], False)
        player.hand.remove(found)
        self._discard.append(found)
        trick_raw = args[0]
        mid = args[1:-1]
        trick = _SGS_MOVE_ALIASES.get(trick_raw, trick_raw.lower())

        if trick in ("draw2", "无中生有"):
            return self._play_draw2(who, guhuo=True)
        if trick in ("dismantle", "拆", "过河拆桥"):
            if not mid:
                return (
                    ["用法：/game move 蛊惑 过河拆桥 <目标> <你的牌>"],
                    [],
                    False,
                )
            tgt = self._resolve_target(who, mid)
            if tgt is None:
                return (["无效目标。"], [], False)
            return self._play_dismantle(who, tgt, guhuo=True)
        if trick in ("shunshou", "顺手", "顺手牵羊"):
            if not mid:
                return (
                    ["用法：/game move 蛊惑 顺手 <目标> <你的牌>"],
                    [],
                    False,
                )
            tgt = self._resolve_target(who, mid)
            if tgt is None:
                return (["无效目标。"], [], False)
            return self._play_shunshou(who, tgt, guhuo=True)
        if trick == "duel":
            if not mid:
                return (["用法：/game move 蛊惑 决斗 <目标> <牌>"], [], False)
            tgt = self._resolve_target(who, mid)
            if tgt is None:
                return (["无效目标。"], [], False)
            return self._do_duel(who, tgt, consume_card=False)
        if trick in ("nanman", "南蛮", "南蛮入侵"):
            priv, bcast, ended = self._start_area(
                who, "nanman", "南蛮入侵（蛊惑）", "南蛮入侵", consume_card=False
            )
            self._jizhi_draw(player, bcast)
            return (priv, bcast, ended)
        if trick in ("wanjian", "万箭", "万箭齐发"):
            priv, bcast, ended = self._start_area(
                who, "wanjian", "万箭齐发（蛊惑）", "万箭齐发", consume_card=False
            )
            self._jizhi_draw(player, bcast)
            return (priv, bcast, ended)
        if trick in ("wugu", "五谷", "五谷丰登"):
            bcast = [f"{player.name}（蛊惑）【五谷丰登】"]
            for p in self.players:
                if not p.dead and self._draw_cards(p, 1):
                    bcast.append(f"{p.name} 摸 1 张")
            self._jizhi_draw(player, bcast)
            return ([], bcast, False)
        if trick in ("taoyuan", "桃园", "桃园结义"):
            bcast = [f"{player.name}（蛊惑）【桃园结义】"]
            for p in self.players:
                if not p.dead and p.hp < p.max_hp:
                    p.hp += 1
                    bcast.append(f"{p.name} 回复 1 点体力")
            self._jizhi_draw(player, bcast)
            return ([], bcast, False)
        if trick in ("huogong", "火攻"):
            if not mid:
                return (
                    ["用法：/game move 蛊惑 火攻 <目标> <蛊惑牌>"],
                    [],
                    False,
                )
            tgt = self._resolve_target(who, mid)
            if tgt is None:
                return (["无效目标。"], [], False)
            return self._do_huogong(who, tgt, consume_card=False, guhuo=True)
        if trick in ("bingliang", "兵粮", "兵粮寸断"):
            if not mid:
                return (["用法：/game move 蛊惑 兵粮 <目标> <牌>"], [], False)
            tgt = self._resolve_target(who, mid)
            if tgt is None:
                return (["无效目标。"], [], False)
            return self._play_bingliang(who, tgt, guhuo=True)
        if trick in ("tiesuo", "铁索", "铁索连环"):
            if len(mid) == 1 and mid[0] in ("重铸", "recast", "chongzhu"):
                n = self._draw_cards(player, 1)
                bcast = [f"{player.name}（蛊惑）重铸【铁索连环】，摸 {n} 张"]
                return ([], bcast, False)
            if len(mid) < 2:
                return (
                    [
                        "用法：/game move 蛊惑 铁索 <目标1> <目标2> <牌> | "
                        "蛊惑 铁索 重铸 <牌>"
                    ],
                    [],
                    False,
                )
            tgt1 = self._resolve_target(who, mid[:1], allow_self=True)
            tgt2 = self._resolve_target(who, mid[1:2], allow_self=True)
            if tgt1 is None or tgt2 is None:
                return (["无效目标。"], [], False)
            if tgt1 == tgt2:
                return (["两名目标不能相同。"], [], False)
            for t in (tgt1, tgt2):
                if self.players[t].dead:
                    return (["不能对阵亡角色使用铁索连环。"], [], False)
            parts = [
                self._tiesuo_toggle_player(tgt1),
                self._tiesuo_toggle_player(tgt2),
            ]
            bcast = [
                f"{player.name}（蛊惑）【铁索连环】" + "；".join(parts)
            ]
            self._jizhi_draw(player, bcast)
            return ([], bcast, False)
        return (
            [f"蛊惑不支持宣称【{trick_raw}】。"],
            [],
            False,
        )

    def _do_duel(
        self, actor_idx: int, target_idx: int, *, consume_card: bool = True
    ) -> GameResult:
        actor = self.players[actor_idx]
        if consume_card and not self._remove_card(actor, "决斗"):
            return (["你没有【决斗】。"], [], False)
        need = self._duel_need_sha(target_idx)
        self._pending = {
            "kind": "duel",
            "source": actor_idx,
            "target": target_idx,
            "turn": target_idx,
            "need_sha": need,
            "got_sha": 0,
        }
        tgt_name = self.players[target_idx].name
        extra = f"（需{need}杀）" if need > 1 else f"（{tgt_name}出杀或受击）"
        bcast = [f"{actor.name}【决斗】→{tgt_name}{extra}"]
        self._jiang_try_draw(actor, bcast)
        self._jiang_try_draw(self.players[target_idx], bcast)
        return ([], bcast, False)

    def _resolve_duel_step(self, who: int, verb: str) -> GameResult:
        pend = self._pending
        if not pend or pend["kind"] != "duel":
            return (["当前没有【决斗】。"], [], False)
        if who != pend["turn"]:
            cur = self.players[pend["turn"]]
            return ([f"【决斗】轮到 {cur.name}。"], [], False)
        player = self.players[who]
        need = pend.get("need_sha", 1)
        if verb == "sha":
            if self._consume_sha(player) is None:
                return (["请出【杀】或 受击。"], [], False)
            pend["got_sha"] = pend.get("got_sha", 0) + 1
            if pend["got_sha"] < need:
                return (
                    [],
                    [
                        f"{player.name} 打出【杀】"
                        f"（{pend['got_sha']}/{need}）"
                    ],
                    False,
                )
            pend["got_sha"] = 0
            other = (
                pend["target"] if who == pend["source"] else pend["source"]
            )
            pend["turn"] = other
            pend["need_sha"] = self._duel_need_sha(other)
            pend["got_sha"] = 0
            msg = f"{player.name} 打出【杀】，轮到 {self.players[other].name}"
            if pend["need_sha"] > 1:
                msg += f"（无双需 {pend['need_sha']} 张【杀】）"
            return ([], [msg], False)
        if verb in ("pass", "hurt"):
            other = (
                pend["target"] if who == pend["source"] else pend["source"]
            )
            self._pending = None
            bcast = [f"{player.name} 不出【杀】，【决斗】失败"]
            bcast.extend(self._damage(who, other, 1))
            return self._maybe_end(bcast)
        return (["请出 杀 或 受击。"], [], False)

    def _hand_has_suit(self, player: _SgsPlayer, suit: str) -> bool:
        return any(card_suit(c) == suit for c in player.hand)

    def _do_huogong(
        self,
        actor_idx: int,
        target_idx: int,
        *,
        consume_card: bool = True,
        guhuo: bool = False,
    ) -> GameResult:
        actor = self.players[actor_idx]
        target = self.players[target_idx]
        if target_idx == actor_idx:
            return (["不能对自己使用【火攻】。"], [], False)
        if target.dead:
            return (["不能对阵亡角色使用【火攻】。"], [], False)
        if not self._can_trick_target(target_idx):
            return self._trick_target_err(target.name)
        if consume_card and not self._remove_card(actor, "火攻"):
            return (["你没有【火攻】。"], [], False)
        tag = "（蛊惑）" if guhuo else ""
        notes: list[str] = []
        self._jizhi_draw(actor, notes)
        if not target.hand:
            bcast = [
                f"{actor.name}{tag}对 {target.name} 使用【火攻】，"
                f"{target.name} 无手牌，结算结束"
            ]
            if notes:
                bcast.append("  " + "；".join(notes))
            return ([], bcast, False)
        self._pending = {
            "kind": "huogong",
            "source": actor_idx,
            "target": target_idx,
            "phase": "show",
            "guhuo": guhuo,
        }
        bcast = [
            f"{actor.name}{tag}对 {target.name} 使用【火攻】，"
            f"请 {target.name} 出示一张手牌（/game move 出示 <牌>）"
        ]
        if notes:
            bcast.append("  " + "；".join(notes))
        return ([], bcast, False)

    def _resolve_huogong_step(
        self, who: int, verb: str, args: list[str]
    ) -> GameResult:
        pend = self._pending
        if not pend or pend["kind"] != "huogong":
            return (["当前没有【火攻】待结算。"], [], False)
        source = int(pend["source"])
        target = int(pend["target"])
        actor = self.players[source]
        victim = self.players[target]
        phase = pend.get("phase", "show")

        if phase == "show":
            if who != target:
                return ([f"请 {victim.name} 出示手牌。"], [], False)
            if verb not in ("chushi",):
                return (
                    ["用法：/game move 出示 <牌名>  （须为你手牌中的一张）"],
                    [],
                    False,
                )
            if not args:
                return (["用法：/game move 出示 <牌名>"], [], False)
            shown = find_card_in_hand(victim.hand, args[0])
            if shown is None:
                return ([f"你没有【{args[0]}】可出示。"], [], False)
            suit = card_suit(shown)
            label = card_label(shown)
            bcast = [f"{victim.name} 出示【{label}】"]
            if not self._hand_has_suit(actor, suit):
                self._pending = None
                bcast.append(
                    f"{actor.name} 无【{suit}】手牌，【火攻】结算结束"
                )
                return ([], bcast, False)
            pend["phase"] = "play"
            pend["shown_suit"] = suit
            pend["shown_label"] = label
            self._pending = pend
            bcast.append(
                f"{actor.name} 可弃一张【{suit}】牌造成 1 点火焰伤害"
                "（/game move 火攻 <牌> 或 过）"
            )
            return ([], bcast, False)

        if who != source:
            return (
                [f"轮到 {actor.name} 决定是否弃【{pend.get('shown_suit', '')}】牌。"],
                [],
                False,
            )
        if verb in ("pass",):
            self._pending = None
            return (
                [],
                [f"{actor.name} 不弃牌，【火攻】结算结束"],
                False,
            )
        if verb != "huogong":
            suit = pend.get("shown_suit", "")
            return (
                [f"请 /game move 火攻 <{suit}牌> 造成伤害，或 /game move 过"],
                [],
                False,
            )
        if not args:
            return (
                ["用法：/game move 火攻 <与出示牌同花色的牌>  或  /game move 过"],
                [],
                False,
            )
        need_suit = pend.get("shown_suit", "")
        found = find_card_in_hand(actor.hand, args[0])
        if found is None:
            return ([f"你没有【{args[0]}】。"], [], False)
        if card_suit(found) != need_suit:
            return (
                [
                    f"须弃【{need_suit}】牌（对方出示【{pend.get('shown_label', '')}】）"
                ],
                [],
                False,
            )
        actor.hand.remove(found)
        self._discard.append(found)
        shown_label = pend.get("shown_label", "")
        self._pending = None
        bcast = [
            f"{actor.name} 弃【{card_label(found)}】，"
            f"对 {victim.name} 造成 1 点火焰伤害"
            f"（对方曾出示【{shown_label}】）"
        ]
        bcast.extend(
            self._damage(target, source, 1, element="fire", damage_card=found)
        )
        return self._maybe_end(bcast)

    def _area_order(self, actor_idx: int, *, kind: str = "") -> list[int]:
        n = len(self.players)
        order = [(actor_idx + 1 + i) % n for i in range(n)]
        out = [i for i in order if not self.players[i].dead and i != actor_idx]
        if kind == "nanman":
            out = [
                i
                for i in out
                if not self._has_skill(self.players[i], "huoshou")
            ]
        return out

    def _advance_area(self, pend: dict, who: int, msg: str) -> GameResult:
        order = pend["order"]
        pos = order.index(who)
        for j in range(pos + 1, len(order)):
            if not self.players[order[j]].dead:
                pend["turn"] = order[j]
                self._pending = pend
                nxt = self.players[order[j]]
                return (
                    [],
                    [f"{msg} → {nxt.name}（{pend['label']}）"],
                    False,
                )
        self._pending = None
        return ([], [f"{msg}（{pend['label']}完）"], False)

    def _continue_area_after_damage(
        self, pend: dict, who: int, bcast: list[str]
    ) -> GameResult:
        order = pend["order"]
        pos = order.index(who)
        for j in range(pos + 1, len(order)):
            if not self.players[order[j]].dead:
                self._pending = {
                    "kind": pend["kind"],
                    "source": pend.get("source"),
                    "order": order,
                    "turn": order[j],
                    "label": pend["label"],
                }
                nxt = self.players[order[j]]
                bcast.append(f"→ {nxt.name}（{pend['label']}）")
                return ([], bcast, False)
        self._pending = None
        bcast.append(f"{pend['label']} 结算完")
        return self._maybe_end(bcast)

    def _resolve_area_response(self, who: int, verb: str) -> GameResult:
        pend = self._pending
        if not pend or pend["kind"] not in ("nanman", "wanjian"):
            return (["当前没有群体锦囊待响应。"], [], False)
        if who != pend["turn"]:
            return ([f"轮到 {self.players[pend['turn']].name}。"], [], False)
        need_card = "杀" if pend["kind"] == "nanman" else "闪"
        player = self.players[who]
        if verb == "sha" and need_card == "杀":
            if self._consume_sha(player) is None:
                return (["你没有【杀】。"], [], False)
            return self._advance_area(pend, who, f"{player.name} 打出【杀】")
        if verb == "shan" and need_card == "闪":
            src_idx = int(pend["source"])
            if self._can_bagua_for_target(src_idx, who):
                _, bagua_lines, dodged = self._bagua_try_for_need(
                    src_idx, who, 0, 1
                )
                if dodged:
                    tail = (
                        f"{player.name} 闪避【{pend.get('label', '万箭')}】"
                        "（八卦阵）"
                    )
                    msg = "；".join(bagua_lines + [tail]) if bagua_lines else tail
                    return self._advance_area(pend, who, msg)
            if not self._consume_shan(player):
                return (["你没有【闪】。"], [], False)
            return self._advance_area(pend, who, f"{player.name} 打出【闪】")
        if verb in ("sha", "shan"):
            return ([f"请出【{need_card}】或 受击。"], [], False)
        if verb in ("pass", "hurt"):
            saved = dict(pend)
            self._pending = None
            bcast = [f"{player.name} 未出【{need_card}】，受到 1 点伤害"]
            bcast.extend(self._damage(who, saved.get("source"), 1))
            ended = self._maybe_end(bcast)
            if ended[2]:
                return ended
            return self._continue_area_after_damage(saved, who, bcast)
        return ([f"请出【{need_card}】或 受击。"], [], False)

    def _start_area(
        self,
        actor_idx: int,
        kind: str,
        label: str,
        card: str,
        *,
        consume_card: bool = True,
    ) -> GameResult:
        actor = self.players[actor_idx]
        if consume_card and not self._remove_card(actor, card):
            return ([f"你没有【{card}】。"], [], False)
        order = self._area_order(actor_idx, kind=kind)
        if not order:
            extra = ""
            if kind == "nanman":
                extra = "（祸首：南蛮无效）"
            return (
                [],
                [f"{actor.name} 使用【{label}】{extra}，无人需响应"],
                False,
            )
        self._pending = {
            "kind": kind,
            "source": actor_idx,
            "order": order,
            "turn": order[0],
            "label": label,
        }
        return (
            [],
            [
                f"{actor.name} 使用【{label}】！",
                f"轮到 {self.players[order[0]].name}",
            ],
            False,
        )

    def _try_start(self, conn) -> GameResult:
        who = self._who_of(conn)
        if who is None:
            return (["你不是玩家。"], [], False)
        if who != 0:
            host = self.players[0].name
            return ([f"只有房主 {host} 可以开局。"], [], False)
        n = len(self.players)
        if n < _SGS_MIN_PLAYERS:
            need = _SGS_MIN_PLAYERS - n
            return (
                [
                    f"至少 {_SGS_MIN_PLAYERS} 人才能开局，还需 {need} 人 /game join"
                    f"（当前 {n}/{_SGS_MAX_PLAYERS}）。"
                ],
                [],
                False,
            )
        priv = ["你的身份见 /game show。"]
        return (priv, self._start_playing(), False)

    def try_move(self, conn, raw: str) -> GameResult:
        if self.state == "waiting":
            verb, _args, _sha_kind = _sgs_parse_action(raw)
            if verb == "generals":
                return (format_general_list(), [], False)
            if verb == "start":
                return self._try_start(conn)
            n = len(self.players)
            host = self.players[0].name
            lines = [
                f"对局尚未开始（{n}/{_SGS_MAX_PLAYERS} 人）。",
                "其它玩家可 /game join 入座。",
            ]
            if n < _SGS_MIN_PLAYERS:
                lines.append(
                    f"至少 {_SGS_MIN_PLAYERS} 人后可由房主 /game move 开始 开局。"
                )
            else:
                lines.append(f"房主 {host} 执行 /game move 开始 即可开局。")
            return (lines, [], False)
        if self.state != "playing":
            return (["对局已结束。"], [], False)
        who = self._who_of(conn)
        if who is None:
            return (["你不是玩家（可 /game show 围观）。"], [], False)
        player = self.players[who]
        if player.dead:
            return (["你已阵亡。"], [], False)

        verb, args, sha_kind = _sgs_parse_action(raw)

        if verb == "tiandu":
            return self._do_tiandu_take(who)

        if self._pending and self._pending.get("kind") == "guanxing":
            if who != self._pending["who"]:
                return (["等待观星结算。"], [], False)
            if verb not in ("guanxing", "pass"):
                return (
                    ["观星未完成：/game move 观星 <序号…> 或 观星 过"],
                    [],
                    False,
                )
            use_args = args if verb == "guanxing" else ["过"]
            return self._guanxing_resolve(who, use_args)

        if self._pending:
            kind = self._pending["kind"]
            if kind == "zhiba":
                return self._resolve_zhiba_pending(who, verb, args)
            if kind == "cixiong":
                return self._resolve_cixiong(who, verb, args)
            if kind in ("shunshou", "dismantle"):
                return self._resolve_trick_zone_pick(who, verb, args)
            if kind == "huogong":
                return self._resolve_huogong_step(who, verb, args)
            if verb in (
                "shan",
                "sha",
                "hurt",
                "pass",
                "xiangle",
                "tianxiang",
                "liuli",
            ):
                if kind == "sha":
                    return self._resolve_sha_response(who, verb, args)
                if kind == "duel":
                    return self._resolve_duel_step(who, verb)
                return self._resolve_area_response(who, verb)
            hint = self._pending_hint().strip()
            return ([hint or "请先响应当前锦囊/杀。"], [], False)

        if who != self._turn_idx:
            cur = self._current()
            return (
                [f"还没轮到你，当前 #{self._turn_idx + 1} {cur.name} 的回合。"],
                [],
                False,
            )

        if (
            who == self._turn_idx
            and not self._pending
            and (player.judge_lebu or player.judge_bingliang)
        ):
            catch = self._catchup_turn_start(who)
            if catch:
                return ([], catch, False)

        if player.skip_play and who == self._turn_idx and not self._pending:
            bcast = self._auto_skip_play_phase(who)
            return ([], bcast, False)

        if verb == "generals":
            return (format_general_list(), [], False)

        if not verb:
            return (
                [
                    "用法：/game move 杀|火杀|雷杀 <目标> [牌名] | 桃 [目标] | "
                    "决斗 <目标> | 拆 <目标> [区域] | 顺手 <目标> [区域] | "
                    "装备 <牌名> | 无中生有 | 南蛮 | 万箭 | 酒 | 过 [手牌序号...]",
                ],
                [],
                False,
            )

        if verb == "pass":
            overflow = max(0, len(player.hand) - player.hp)
            discard_indices: Optional[list[int]] = None
            if args:
                if overflow <= 0:
                    return (["当前无需弃牌，直接 /game move 过 即可。"], [], False)
                picks: list[int] = []
                for tok in args:
                    if not tok.isdigit():
                        return (["弃牌序号须为数字。"], [], False)
                    idx = int(tok)
                    if idx < 1 or idx > len(player.hand):
                        return (
                            [f"弃牌序号须在 1～{len(player.hand)} 之间。"],
                            [],
                            False,
                        )
                    picks.append(idx - 1)
                if len(set(picks)) != len(picks):
                    return (["弃牌序号不能重复。"], [], False)
                if len(picks) != overflow:
                    return (
                        [f"你需要弃 {overflow} 张，请给出 {overflow} 个手牌序号。"],
                        [],
                        False,
                    )
                discard_indices = picks
            bcast = [f"{player.name} 结束出牌阶段"]
            bcast.extend(self._finish_turn(who, discard_indices=discard_indices))
            return ([], bcast, False)

        if verb == "jiu":
            used: Optional[str] = None
            for c in list(player.hand):
                if card_base(c) == "酒":
                    player.hand.remove(c)
                    self._discard.append(c)
                    used = c
                    break
            if used is None and self._has_skill(player, "jiuchi"):
                for c in list(player.hand):
                    if card_suit(c) == "梅花":
                        player.hand.remove(c)
                        self._discard.append(c)
                        used = c
                        break
            if used is None:
                return (["你没有【酒】（酒池可用【梅花】牌）。"], [], False)
            player.jiu_buff = True
            msg = f"{player.name} 喝酒，下一张【杀】伤害 +1"
            if card_base(used) != "酒":
                msg = (
                    f"{player.name}（酒池）将【{card_label(used)}】当【酒】，"
                    "下一张【杀】伤害 +1"
                )
            return ([], [msg], False)

        if verb == "luoyi":
            if not self._has_skill(player, "luoyi"):
                return (["你没有裸衣技能。"], [], False)
            player.luoyi_buff = True
            return ([], [f"{player.name}（裸衣）下一张【杀】伤害 +1"], False)

        if verb == "tao":
            tgt = who
            if args:
                t = self._resolve_target(who, args)
                if t is None:
                    return (["无效目标。"], [], False)
                tgt = t
            target = self.players[tgt]
            if target.dead:
                return (["不能救阵亡角色。"], [], False)
            if target.hp >= target.max_hp:
                return (["目标体力已满。"], [], False)
            turn_p = self.players[self._turn_idx]
            if (
                self._has_skill(turn_p, "wansha")
                and target.hp <= 0
                and who != target
                and who != self._turn_idx
            ):
                return (
                    ["完杀：贾诩回合内，体力为 1 的角色只能由贾诩使用【桃】。"],
                    [],
                    False,
                )
            if not self._remove_card(player, "桃"):
                return (["你没有【桃】。"], [], False)
            target.hp += 1
            return (
                [],
                [
                    f"{player.name} 对 {target.name} 使用【桃】，"
                    f"体力 {target.hp}/{target.max_hp}"
                ],
                False,
            )

        if verb == "draw2":
            return self._play_draw2(who)

        if verb == "equip":
            card_tok = " ".join(args).strip() if args else ""
            return self._play_equip(who, card_tok)

        if verb == "sha":
            target_args, card_tok, zhangba_tok, err = _sgs_sha_target_and_card(
                player,
                args,
                sha_kind=sha_kind,
                has_zhangba=self._has_zhangba(player),
            )
            if err:
                return ([err], [], False)
            assert target_args is not None
            tgt = self._resolve_target(who, target_args)
            if tgt is None:
                return (["无效目标。"], [], False)
            return self._do_sha(
                who,
                tgt,
                card_token=card_tok,
                zhangba_tokens=zhangba_tok,
                sha_kind=sha_kind,
            )

        if verb == "duel":
            if not args:
                return (["用法：/game move 决斗 <目标>"], [], False)
            if (
                self._has_skill(player, "shuangxiong")
                and player.shuangxiong_color
                and len(args) >= 2
                and not player.shuangxiong_duel_used
            ):
                tgt = self._resolve_target(who, args[:1])
                card_tok = args[-1]
                if tgt is None:
                    return (["无效目标。"], [], False)
                found = find_card_in_hand(player.hand, card_tok)
                if found is None:
                    return ([f"你没有【{card_tok}】。"], [], False)
                if player.shuangxiong_color == "red" and not is_black(found):
                    return (["双雄：需使用黑色牌当【决斗】。"], [], False)
                if player.shuangxiong_color == "black" and not is_red(found):
                    return (["双雄：需使用红色牌当【决斗】。"], [], False)
                player.hand.remove(found)
                self._discard.append(found)
                player.shuangxiong_duel_used = True
                return self._do_duel(who, tgt)
            tgt = self._resolve_target(who, args)
            if tgt is None:
                return (["无效目标。"], [], False)
            return self._do_duel(who, tgt)

        if verb == "dismantle":
            if not args:
                return (
                    [
                        "用法：/game move 拆 <目标> [区域]  或  /game move 过河拆桥 <目标> [区域]",
                        "  区域：手牌 | 武器 | 防具 | +1马 | -1马（多选时须指定；仅一项可省略）",
                    ],
                    [],
                    False,
                )
            tgt = self._resolve_target(who, args[:1])
            if tgt is None:
                return (["无效目标。"], [], False)
            zone_arg = args[1] if len(args) > 1 else None
            return self._play_dismantle(who, tgt, zone_arg=zone_arg)

        if verb == "nanman":
            if not self._remove_card(player, "南蛮入侵"):
                return (["你没有【南蛮入侵】。"], [], False)
            priv, bcast, ended = self._start_area(
                who, "nanman", "南蛮入侵", "南蛮入侵"
            )
            self._jizhi_draw(player, bcast)
            return (priv, bcast, ended)

        if verb == "wanjian":
            if not self._remove_card(player, "万箭齐发"):
                return (["你没有【万箭齐发】。"], [], False)
            priv, bcast, ended = self._start_area(
                who, "wanjian", "万箭齐发", "万箭齐发"
            )
            self._jizhi_draw(player, bcast)
            return (priv, bcast, ended)

        if verb == "shunshou":
            if not args:
                return (
                    [
                        "用法：/game move 顺手 <目标> [区域]  或  顺手牵羊 <目标> [区域]",
                        "  区域：手牌 | 武器 | 防具 | +1马 | -1马（多选时须指定；仅一项可省略）",
                    ],
                    [],
                    False,
                )
            tgt = self._resolve_target(who, args[:1])
            if tgt is None:
                return (["无效目标。"], [], False)
            zone_arg = args[1] if len(args) > 1 else None
            return self._play_shunshou(who, tgt, zone_arg=zone_arg)

        if verb == "bingliang":
            if not args:
                return (["用法：/game move 兵粮 <目标>"], [], False)
            tgt = self._resolve_target(who, args)
            if tgt is None:
                return (["无效目标。"], [], False)
            return self._play_bingliang(who, tgt)

        if verb == "duanliang":
            if not self._has_skill(player, "duanliang"):
                return (["你没有断粮技能。"], [], False)
            if len(args) < 2:
                return (["用法：/game move 断粮 <目标> <黑色牌>"], [], False)
            tgt = self._resolve_target(who, args[:1])
            card_tok = args[-1]
            if tgt is None:
                return (["无效目标。"], [], False)
            found = find_card_in_hand(player.hand, card_tok)
            if found is None or not is_black(found):
                return (["请使用黑色牌（黑桃/梅花）。"], [], False)
            player.hand.remove(found)
            self._discard.append(found)
            return self._play_bingliang(who, tgt, via_duanliang=True)

        if verb == "wugu":
            if not self._remove_card(player, "五谷丰登"):
                return (["你没有【五谷丰登】。"], [], False)
            bcast = [f"{player.name} 使用【五谷丰登】"]
            for p in self.players:
                if not p.dead and self._draw_cards(p, 1):
                    bcast.append(f"{p.name} 摸 1 张")
            self._jizhi_draw(player, bcast)
            return ([], bcast, False)

        if verb == "taoyuan":
            if not self._remove_card(player, "桃园结义"):
                return (["你没有【桃园结义】。"], [], False)
            bcast = [f"{player.name} 使用【桃园结义】"]
            for p in self.players:
                if not p.dead and p.hp < p.max_hp:
                    p.hp += 1
                    bcast.append(f"{p.name} 回复 1 点体力")
            self._jizhi_draw(player, bcast)
            return ([], bcast, False)

        if verb == "huogong":
            if not args:
                return (["用法：/game move 火攻 <目标>"], [], False)
            tgt = self._resolve_target(who, args[:1])
            if tgt is None:
                return (["无效目标。"], [], False)
            return self._do_huogong(who, tgt)

        if verb == "tiesuo":
            if args and args[0] in ("重铸", "recast", "chongzhu"):
                if len(args) != 1:
                    return (["用法：/game move 铁索 重铸"], [], False)
                if not self._remove_card(player, "铁索连环"):
                    return (["你没有【铁索连环】。"], [], False)
                n = self._draw_cards(player, 1)
                return (
                    [],
                    [f"{player.name} 重铸【铁索连环】，摸 {n} 张"],
                    False,
                )
            if len(args) < 2:
                return (
                    [
                        "用法：/game move 铁索 <目标1> <目标2> | /game move 铁索 重铸"
                    ],
                    [],
                    False,
                )
            tgt1 = self._resolve_target(who, args[:1], allow_self=True)
            tgt2 = self._resolve_target(who, args[1:2], allow_self=True)
            if tgt1 is None or tgt2 is None:
                return (["无效目标。"], [], False)
            if tgt1 == tgt2:
                return (["两名目标不能相同。"], [], False)
            for t in (tgt1, tgt2):
                if self.players[t].dead:
                    return (["不能对阵亡角色使用铁索连环。"], [], False)
            if not self._remove_card(player, "铁索连环"):
                return (["你没有【铁索连环】。"], [], False)
            parts = [
                self._tiesuo_toggle_player(tgt1),
                self._tiesuo_toggle_player(tgt2),
            ]
            bcast = [
                f"{player.name} 使用【铁索连环】：" + "；".join(parts)
            ]
            self._jizhi_draw(player, bcast)
            return ([], bcast, False)

        if verb == "rende":
            if len(args) < 2:
                return (
                    ["用法：/game move 仁德 <目标> <牌名>"],
                    [],
                    False,
                )
            if not self._has_skill(player, "rende"):
                return (["你没有仁德技能。"], [], False)
            tgt = self._resolve_target(who, args[:1])
            card = args[-1]
            if tgt is None:
                return (["无效目标。"], [], False)
            found = find_card_in_hand(player.hand, card)
            if found is None:
                return ([f"你没有【{card}】。"], [], False)
            player.hand.remove(found)
            self.players[tgt].hand.append(found)
            return (
                [],
                [
                    f"{player.name}（仁德）将【{card_label(found)}】"
                    f"交给 {self.players[tgt].name}"
                ],
                False,
            )

        if verb == "zhiheng":
            if not self._has_skill(player, "zhiheng"):
                return (["你没有制衡技能。"], [], False)
            if not args:
                return (["用法：/game move 制衡 <牌名>"], [], False)
            card = args[0]
            if not self._remove_card(player, card):
                return ([f"你没有【{card}】。"], [], False)
            n = self._draw_cards(player, 1)
            return ([], [f"{player.name}（制衡）弃【{card}】摸 {n} 张"], False)

        if verb == "qiaobian":
            if not self._has_skill(player, "qiaobian"):
                return (["你没有巧变技能。"], [], False)
            if not args:
                return (["用法：/game move 巧变 <牌名>"], [], False)
            card = args[0]
            if not self._remove_card(player, card):
                return ([f"你没有【{card}】。"], [], False)
            n = self._draw_cards(player, 1)
            return ([], [f"{player.name}（巧变）弃【{card}】摸 {n} 张"], False)

        if verb == "tuxi":
            if not self._has_skill(player, "tuxi"):
                return (["你没有突袭技能。"], [], False)
            if not args:
                return (["用法：/game move 突袭 <目标>"], [], False)
            tgt = self._resolve_target(who, args)
            if tgt is None:
                return (["无效目标。"], [], False)
            victim = self.players[tgt]
            if not victim.hand:
                return ([f"{victim.name} 没有手牌。"], [], False)
            card = self._rng.choice(victim.hand)
            victim.hand.remove(card)
            player.hand.append(card)
            return (
                [],
                [f"{player.name}（突袭）获得 {victim.name} 的【{card}】"],
                False,
            )

        if verb == "qiangxi":
            if not self._has_skill(player, "qiangxi"):
                return (["你没有强袭技能。"], [], False)
            if len(args) < 2:
                return (["用法：/game move 强袭 <目标> <弃牌>"], [], False)
            tgt = self._resolve_target(who, args[:1])
            card = args[-1]
            if tgt is None:
                return (["无效目标。"], [], False)
            if not self._remove_card(player, card):
                return ([f"你没有【{card}】。"], [], False)
            bcast = [f"{player.name}（强袭）对 {self.players[tgt].name} 造成伤害"]
            bcast.extend(self._damage(tgt, who, 1))
            return self._maybe_end(bcast)

        if verb == "fanjian":
            if not self._has_skill(player, "fanjian"):
                return (["你没有反间技能。"], [], False)
            if player.fanjian_used:
                return (["本回合已使用过【反间】。"], [], False)
            if len(args) < 3:
                return (
                    [
                        "用法：/game move 反间 <目标> <其声明花色> <你交出的牌>",
                        "  花色：红桃|方块|黑桃|梅花（可简写 红/方/黑/梅）",
                    ],
                    [],
                    False,
                )
            tgt = self._resolve_target(who, args[:1])
            if tgt is None:
                return (["无效目标。"], [], False)
            declared = _normalize_declared_suit(args[1])
            if declared is None:
                return (
                    [f"无效花色「{args[1]}」。请用：红桃/方块/黑桃/梅花。"],
                    [],
                    False,
                )
            card_tok = args[2]
            found = find_card_in_hand(player.hand, card_tok)
            if found is None:
                return ([f"你没有【{card_tok}】。"], [], False)
            victim = self.players[tgt]
            if victim.dead:
                return (["不能对阵亡角色反间。"], [], False)
            player.hand.remove(found)
            victim.hand.append(found)
            player.fanjian_used = True
            suit_shown = card_suit(found)
            head = (
                f"{player.name}（反间）{victim.name} 声明【{declared}】，"
                f"获得并展示【{card_label(found)}】"
            )
            if suit_shown:
                head += f"（{suit_shown}）"
            if suit_shown != declared:
                bcast = [head + "，花色不符"]
                bcast.extend(self._damage(tgt, who, 1))
                return self._maybe_end(bcast)
            return ([], [head + "，花色相符"], False)

        if verb == "qingnang":
            if not self._has_skill(player, "qingnang"):
                return (["你没有青囊技能。"], [], False)
            if len(args) < 2:
                return (["用法：/game move 青囊 <目标> <弃牌>"], [], False)
            tgt = self._resolve_target(who, args[:1])
            card = args[-1]
            if tgt is None:
                return (["无效目标。"], [], False)
            target = self.players[tgt]
            if target.hp >= target.max_hp:
                return (["目标体力已满。"], [], False)
            if not self._remove_card(player, card):
                return ([f"你没有【{card}】。"], [], False)
            target.hp += 1
            return (
                [],
                [
                    f"{player.name}（青囊）令 {target.name} 回复 1 点"
                    f"（{target.hp}/{target.max_hp}）"
                ],
                False,
            )

        if verb == "jieyin":
            if not self._has_skill(player, "jieyin"):
                return (["你没有结姻技能。"], [], False)
            if not args:
                return (["用法：/game move 结姻 <目标>"], [], False)
            tgt = self._resolve_target(who, args)
            if tgt is None:
                return (["无效目标。"], [], False)
            other = self.players[tgt]
            if player.hp > 2 or other.hp > 2:
                return (["双方体力均须≤2。"], [], False)
            healed = []
            if player.hp < player.max_hp:
                player.hp += 1
                healed.append(player.name)
            if other.hp < other.max_hp:
                other.hp += 1
                healed.append(other.name)
            if not healed:
                return (["双方体力均已满。"], [], False)
            return ([], [f"结姻：{'、'.join(healed)} 各回复 1 点"], False)

        if verb == "guose":
            if not self._has_skill(player, "guose"):
                return (["你没有国色技能。"], [], False)
            if len(args) < 2:
                return (
                    ["用法：/game move 国色 <目标> <方块牌>（当【乐不思蜀】）"],
                    [],
                    False,
                )
            tgt = self._resolve_target(who, args[:1])
            card_tok = args[-1]
            if tgt is None:
                return (["无效目标。"], [], False)
            if not self._can_trick_target(tgt):
                return self._trick_target_err(self.players[tgt].name)
            dist_err = self._trick_distance_err(who, tgt)
            if dist_err:
                return ([dist_err], [], False)
            found = find_card_in_hand(player.hand, card_tok)
            if found is None or not is_diamond(found):
                return (["国色须使用【方块】花色的牌。"], [], False)
            victim = self.players[tgt]
            if victim.judge_lebu:
                return ([f"{victim.name} 判定区已有【乐不思蜀】。"], [], False)
            player.hand.remove(found)
            self._discard.append(found)
            victim.judge_lebu = True
            return (
                [],
                [
                    f"{player.name}（国色）将【{card_label(found)}】当【乐不思蜀】"
                    f"→{victim.name} 判定区"
                    "（其回合判定：红桃则无效，否则跳过出牌）",
                ],
                False,
            )

        if verb == "liuli":
            return (
                ["流离仅在成为【杀】的目标时，于响应阶段使用。"],
                [],
                False,
            )

        if verb == "qixi":
            if not self._has_skill(player, "qixi"):
                return (["你没有奇袭技能。"], [], False)
            if len(args) < 2:
                return (["用法：/game move 奇袭 <目标> <黑色牌>"], [], False)
            tgt = self._resolve_target(who, args[:1])
            card_tok = args[-1]
            if tgt is None:
                return (["无效目标。"], [], False)
            if not self._can_trick_target(tgt):
                return self._trick_target_err(self.players[tgt].name)
            found = find_card_in_hand(player.hand, card_tok)
            if found is None or not is_black(found):
                return (["请使用黑色牌（黑桃/梅花）。"], [], False)
            player.hand.remove(found)
            self._discard.append(found)
            victim = self.players[tgt]
            if not victim.hand:
                return ([f"{victim.name} 没有手牌。"], [], False)
            taken = self._rng.choice(victim.hand)
            victim.hand.remove(taken)
            self._discard.append(taken)
            return (
                [],
                [
                    f"{player.name}（奇袭）将【{card_label(found)}】当【过河拆桥】，"
                    f"拆掉 {victim.name} 的【{card_label(taken)}】"
                ],
                False,
            )

        if verb == "leiji":
            if not self._has_skill(player, "leiji"):
                return (["你没有雷击技能。"], [], False)
            if not args:
                return (["用法：/game move 雷击 <目标>"], [], False)
            tgt = self._resolve_target(who, args)
            if tgt is None:
                return (["无效目标。"], [], False)
            if not self._deck:
                return (["牌堆已空，无法判定。"], [], False)
            judge = self._deck.pop()
            self._discard.append(judge)
            dmg = 2 if is_black(judge) else 1
            bcast = [
                f"{player.name}（雷击）判定【{card_label(judge)}】，"
                f"{self.players[tgt].name} 受到 {dmg} 点雷电伤害"
            ]
            bcast.extend(self._damage(tgt, who, dmg))
            return self._maybe_end(bcast)

        if verb == "shuangxiong":
            if not self._has_skill(player, "shuangxiong"):
                return (["你没有双雄技能。"], [], False)
            if not args:
                return (["用法：/game move 双雄 红|黑"], [], False)
            color = args[0]
            if color in ("红", "红色"):
                player.shuangxiong_color = "red"
            elif color in ("黑", "黑色"):
                player.shuangxiong_color = "black"
            else:
                return (["请指定 红 或 黑。"], [], False)
            label = "红色" if player.shuangxiong_color == "red" else "黑色"
            return (
                [],
                [
                    f"{player.name}（双雄）展示{label}，"
                    "可将异色手牌当【决斗】（/game move 决斗 <目标> <牌>）"
                ],
                False,
            )

        if verb == "luanji":
            if not self._has_skill(player, "luanji"):
                return (["你没有乱击技能。"], [], False)
            if len(args) < 2:
                return (["用法：/game move 乱击 <牌1> <牌2>"], [], False)
            removed: list[str] = []
            for tok in args[:2]:
                found = find_card_in_hand(player.hand, tok)
                if found is None:
                    return ([f"你没有【{tok}】。"], [], False)
                player.hand.remove(found)
                self._discard.append(found)
                removed.append(card_label(found))
            priv, bcast, ended = self._start_area(
                who,
                "wanjian",
                "万箭齐发（乱击）",
                "万箭齐发",
                consume_card=False,
            )
            bcast.insert(
                0,
                f"{player.name}（乱击）弃【{'】【'.join(removed)}】发动【万箭齐发】",
            )
            return (priv, bcast, ended)

        if verb == "guhuo":
            if not self._has_skill(player, "guhuo"):
                return (["你没有蛊惑技能。"], [], False)
            return self._do_guhuo(who, args)

        if verb == "yinghun":
            if not args or len(args) < 2:
                return (
                    ["用法：/game move 英魂 1|2 <目标>（1摸X弃1，2摸1弃X）"],
                    [],
                    False,
                )
            mode = args[0]
            if mode not in ("1", "2"):
                return (
                    ["用法：/game move 英魂 1|2 <目标>（1摸X弃1，2摸1弃X）"],
                    [],
                    False,
                )
            tgt = self._resolve_target(who, args[1:], allow_self=True)
            if tgt is None:
                return (["无效目标。"], [], False)
            return self._do_yinghun(who, mode, tgt)

        if verb == "zhiba":
            lord_i = self._lord_for_zhiba()
            if lord_i is None:
                return (["场上没有拥有【制霸】的主公。"], [], False)
            if who == lord_i:
                return (["主公不能对自己发动制霸。"], [], False)
            if player.kingdom != "吴":
                return (["制霸：仅吴势力角色可发动。"], [], False)
            if player.zhiba_used:
                return (["本回合已发动过制霸。"], [], False)
            if not args:
                return (["用法：/game move 制霸 <拼点牌>"], [], False)
            found = find_card_in_hand(player.hand, args[0])
            if found is None:
                return ([f"你没有【{args[0]}】。"], [], False)
            player.hand.remove(found)
            player.zhiba_used = True
            lord = self.players[lord_i]
            self._pending = {
                "kind": "zhiba",
                "initiator": who,
                "lord": lord_i,
                "init_card": found,
            }
            priv = [
                f"{lord.name}：{player.name} 向你发起制霸拼点，"
                "请 /game move 拼点 <牌>"
            ]
            if lord.hunzi_awakened:
                priv.append("（已觉醒可 /game move 拒制霸）")
            self._queue_private(lord_i, priv)
            bcast = [
                f"{player.name}（制霸）向主公 {lord.name} 发起拼点"
                f"【{card_label(found)}】"
            ]
            return ([], bcast, False)

        head_tok = raw.strip().split()[0] if raw.strip() else verb
        hint = "输入 /game show 帮助 查看可用命令。"
        if head_tok in ("顺", "牵", "顺手"):
            hint += " 顺手牵羊：/game move 顺手 <目标> 或 /game move 顺 <目标>"
        return ([f"无法识别指令「{head_tok}」。{hint}"], [], False)

    def resign(self, conn, name: str) -> GameResult:
        if self.state != "playing":
            return (["对局尚未开始或已结束。"], [], False)
        who = self._who_of(conn)
        if who is None:
            return (["你不是玩家。"], [], False)
        self.players[who].dead = True
        self.players[who].hp = 0
        bcast = [f"{name} 认输阵亡，身份：{self.players[who].role}"]
        ended = self._maybe_end(bcast)
        if not ended[2]:
            bcast.append("对局继续。")
        return ended

    def abort(self, conn, name: str) -> GameResult:
        if self.state == "ended":
            return (["对局已结束。"], [], False)
        if self._who_of(conn) is None:
            return (["你不是玩家，无法终止。"], [], False)
        if self.state == "playing":
            return (["对局已开始，请用 /game resign 认输。"], [], False)
        self.state = "ended"
        return ([], [f"{name} 取消了对局（未开始）。"], True)

    def seats(self) -> list[str]:
        lines = [
            f"sanguo 状态：{self.state}  "
            f"玩家 {len(self.players)}/{_SGS_MAX_PLAYERS}",
        ]
        for i, p in enumerate(self.players, 1):
            dead = "（阵亡）" if p.dead else ""
            lines.append(f"  #{i}：{p.name}{dead}")
        if self.state == "waiting":
            n = len(self.players)
            if n < _SGS_MAX_PLAYERS:
                lines.append(f"  空席：/game join（{n}/{_SGS_MAX_PLAYERS}）")
            if n >= _SGS_MIN_PLAYERS:
                lines.append(
                    f"  房主 {self.players[0].name}：/game move 开始"
                )
        return lines

    def on_player_leave(self, conn, name: str) -> GameResult:
        who = self._who_of(conn)
        if who is None:
            return ([], [], False)
        if self.state == "waiting":
            self.players.pop(who)
            if not self.players:
                self.state = "ended"
                return ([], [f"{name} 离开，对局取消。"], True)
            return ([], [f"{name} 离开，当前：{self._roster_names()}"], False)
        self.players[who].dead = True
        self.players[who].hp = 0
        bcast = [f"{name} 断线，视为阵亡（身份：{self.players[who].role}）"]
        return self._maybe_end(bcast)


class WerewolfGame:
    name = "werewolf"
    first_seat_desc = "host"
    second_seat_desc = "player"
    send_view_on_move = False

    def __init__(self, owner_conn, owner_name: str) -> None:
        self.players: list[tuple[object, str]] = [(owner_conn, owner_name)]
        self.state = "waiting"
        self.round = 0
        self.roles: dict[str, str] = {}
        self.alive: set[str] = {owner_name}
        self.day_votes: dict[str, str] = {}
        self.wolf_target: Optional[str] = None
        self.seer_target: Optional[str] = None
        self.pending_kill: Optional[str] = None
        self.pending_poison: Optional[str] = None
        self.witch_saved = False
        self.witch_save_available = True
        self.witch_poison_available = True
        self._extra_privates: list[tuple[object, list[str]]] = []

    def _norm(self, s: str) -> str:
        return s.strip().lower()

    def _name_of(self, conn) -> Optional[str]:
        for c, n in self.players:
            if c is conn:
                return n
        return None

    def _conn_of(self, name: str):
        for c, n in self.players:
            if n == name:
                return c
        return None

    def _find_player(self, token: str, *, alive_only: bool = True) -> Optional[str]:
        q = self._norm(token)
        for _c, n in self.players:
            if alive_only and n not in self.alive:
                continue
            if self._norm(n) == q:
                return n
        return None

    def _queue_private(self, conn, lines: list[str]) -> None:
        if conn is not None and lines:
            self._extra_privates.append((conn, lines))

    def drain_extra_privates(self):
        out = self._extra_privates
        self._extra_privates = []
        return out

    def _wolves_alive(self) -> list[str]:
        return [n for n in self.alive if self.roles.get(n) == "wolf"]

    def _villagers_alive(self) -> list[str]:
        return [n for n in self.alive if self.roles.get(n) != "wolf"]

    def _alive_line(self) -> str:
        return "Alive: " + ", ".join(sorted(self.alive))

    def _check_win(self) -> Optional[str]:
        wolves = len(self._wolves_alive())
        villagers = len(self._villagers_alive())
        if wolves <= 0:
            self.state = "ended"
            return "Villagers win."
        if wolves >= villagers:
            self.state = "ended"
            return "Wolves win."
        return None

    def _assign_roles(self) -> None:
        n = len(self.players)
        wolf_n = 2 if n <= 7 else 3
        villager_n = n - wolf_n - 2
        deck = (["wolf"] * wolf_n) + ["seer", "witch"] + (["villager"] * villager_n)
        random.shuffle(deck)
        self.roles = {}
        for i, (_c, name) in enumerate(self.players):
            self.roles[name] = deck[i]

    def try_join(self, conn, name: str) -> GameResult:
        if self.state != "waiting":
            return (["Game already started."], [], False)
        if any(c is conn for c, _ in self.players):
            return (["You already joined."], [], False)
        if any(self._norm(n) == self._norm(name) for _c, n in self.players):
            return (["Nickname already used in this game."], [], False)
        if len(self.players) >= 12:
            return (["Room is full for werewolf (max 12)."], [], False)
        self.players.append((conn, name))
        self.alive.add(name)
        msg = [f"{name} joined werewolf ({len(self.players)} players)."]
        if len(self.players) >= 5:
            msg.append("Host can start: /game move start")
        return ([], msg, False)

    def _start_game(self) -> GameResult:
        if len(self.players) < 5:
            return (["Need at least 5 players."], [], False)
        self._assign_roles()
        self.state = "night"
        self.round = 1
        self.day_votes = {}
        self.wolf_target = None
        self.seer_target = None
        self.pending_kill = None
        self.pending_poison = None
        self.witch_saved = False
        self.witch_save_available = True
        self.witch_poison_available = True

        for conn, name in self.players:
            role = self.roles[name]
            self._queue_private(conn, [f"Your role: {role}"])
            if role == "wolf":
                mates = [n for n, r in self.roles.items() if r == "wolf" and n != name]
                self._queue_private(conn, ["Wolf mates: " + (", ".join(mates) if mates else "(none)")])
                self._queue_private(conn, ["Night cmd: /game move kill <name>"])
            elif role == "seer":
                self._queue_private(conn, ["Night cmd: /game move check <name>"])
            elif role == "witch":
                self._queue_private(conn, ["Night cmd: /game move save | poison <name> | pass"])
        return ([], [f"Werewolf started. Night {self.round}.", "Use /game show for commands."], False)

    def _resolve_night_if_ready(self) -> list[str]:
        wolves_done = (not self._wolves_alive()) or (self.wolf_target is not None)
        seer_done = (not any(r == "seer" and n in self.alive for n, r in self.roles.items())) or (self.seer_target is not None)
        witch_alive = any(r == "witch" and n in self.alive for n, r in self.roles.items())
        witch_done = (not witch_alive) or (self.witch_saved or self.pending_poison is not None or self.pending_kill is None)
        if not (wolves_done and seer_done and witch_done):
            return []

        dead: set[str] = set()
        if self.pending_kill and not self.witch_saved:
            dead.add(self.pending_kill)
        if self.pending_poison:
            dead.add(self.pending_poison)
        for n in dead:
            self.alive.discard(n)

        self.state = "day"
        self.day_votes = {}
        out = [f"Day {self.round} begins."]
        out.append("Night deaths: " + (", ".join(sorted(dead)) if dead else "none"))
        win = self._check_win()
        if win:
            out.append(win)
            return out
        out.append(self._alive_line())
        out.append("Day cmd: /game move vote <name>")
        return out

    def _resolve_day_if_ready(self) -> list[str]:
        alive = sorted(self.alive)
        if any(n not in self.day_votes for n in alive):
            return []
        counts: dict[str, int] = {}
        for _v, t in self.day_votes.items():
            counts[t] = counts.get(t, 0) + 1
        top = max(counts.values())
        winners = [k for k, v in counts.items() if v == top]
        out: list[str] = []
        if len(winners) == 1:
            kicked = winners[0]
            self.alive.discard(kicked)
            out.append(f"Voted out: {kicked}")
        else:
            out.append("Vote tie, no one is out.")
        win = self._check_win()
        if win:
            out.append(win)
            return out
        self.round += 1
        self.state = "night"
        self.day_votes = {}
        self.wolf_target = None
        self.seer_target = None
        self.pending_kill = None
        self.pending_poison = None
        self.witch_saved = False
        out.append(f"Night {self.round} begins.")
        return out

    def try_move(self, conn, raw: str) -> GameResult:
        actor = self._name_of(conn)
        if actor is None:
            return (["You are not in this game."], [], False)
        text = raw.strip()
        if not text:
            return (["Usage: /game move <cmd>"], [], False)
        parts = text.split()
        cmd = self._norm(parts[0])
        arg = " ".join(parts[1:]).strip()

        if cmd == "start":
            if self.state != "waiting":
                return (["Already started."], [], False)
            if self.players[0][0] is not conn:
                return (["Only host can start."], [], False)
            return self._start_game()

        if self.state == "waiting":
            return (["Not started. Host: /game move start"], [], False)
        if self.state == "ended":
            return (["Game ended."], [], False)
        if actor not in self.alive:
            return (["You are out. Use /game show to spectate."], [], False)

        if self.state == "night":
            role = self.roles.get(actor, "villager")
            priv: list[str] = []
            if cmd == "kill":
                if role != "wolf":
                    return (["Only wolf can kill."], [], False)
                target = self._find_player(arg, alive_only=True) if arg else None
                if not target or target == actor:
                    return (["Invalid target."], [], False)
                self.wolf_target = target
                self.pending_kill = target
                priv.append(f"Kill target set: {target}")
            elif cmd == "check":
                if role != "seer":
                    return (["Only seer can check."], [], False)
                target = self._find_player(arg, alive_only=True) if arg else None
                if not target or target == actor:
                    return (["Invalid target."], [], False)
                self.seer_target = target
                team = "wolf" if self.roles.get(target) == "wolf" else "villager"
                priv.append(f"Check result: {target} is {team}.")
            elif cmd == "save":
                if role != "witch":
                    return (["Only witch can save."], [], False)
                if not self.witch_save_available:
                    return (["Save potion already used."], [], False)
                if not self.pending_kill:
                    return (["No kill target yet."], [], False)
                if self.pending_kill == actor:
                    return (["Witch cannot save herself."], [], False)
                if self.pending_poison is not None:
                    return (["Already poisoned this night."], [], False)
                self.witch_saved = True
                self.witch_save_available = False
                priv.append(f"Saved: {self.pending_kill}")
            elif cmd == "poison":
                if role != "witch":
                    return (["Only witch can poison."], [], False)
                if not self.witch_poison_available:
                    return (["Poison already used."], [], False)
                if self.witch_saved:
                    return (["Already saved this night."], [], False)
                target = self._find_player(arg, alive_only=True) if arg else None
                if not target or target == actor:
                    return (["Invalid target."], [], False)
                self.pending_poison = target
                self.witch_poison_available = False
                priv.append(f"Poison target set: {target}")
            elif cmd == "pass":
                if role != "witch":
                    return (["Only witch can pass."], [], False)
                if self.pending_poison is None:
                    self.pending_poison = ""
                if self.pending_kill is None:
                    self.witch_saved = True
                priv.append("Witch skipped.")
            else:
                return (["Night cmds: kill/check/save/poison/pass"], [], False)
            return (priv, self._resolve_night_if_ready(), self.state == "ended")

        if self.state == "day":
            if cmd != "vote":
                return (["Day cmd: /game move vote <name>"], [], False)
            target = self._find_player(arg, alive_only=True) if arg else None
            if not target or target == actor:
                return (["Invalid vote target."], [], False)
            self.day_votes[actor] = target
            bcast = [f"{actor} voted {target} ({len(self.day_votes)}/{len(self.alive)})"]
            bcast.extend(self._resolve_day_if_ready())
            return ([], bcast, self.state == "ended")

        return (["Invalid state."], [], False)

    def resign(self, conn, name: str) -> GameResult:
        actor = self._name_of(conn)
        if actor is None or actor not in self.alive:
            return (["You are not alive in this game."], [], False)
        self.alive.discard(actor)
        out = [f"{name} resigned and is out."]
        win = self._check_win()
        if win:
            out.append(win)
            return ([], out, True)
        out.append(self._alive_line())
        return ([], out, False)

    def abort(self, conn, name: str) -> GameResult:
        if self.players[0][0] is not conn:
            return (["Only host can abort."], [], False)
        if self.state == "ended":
            return (["Game already ended."], [], False)
        self.state = "ended"
        return ([], [f"{name} aborted the werewolf game."], True)

    def seats(self) -> list[str]:
        lines = [f"werewolf state: {self.state}", f"players: {len(self.players)} (min 5)"]
        for _c, n in self.players:
            mark = "alive" if n in self.alive else "out"
            lines.append(f" - {n} ({mark})")
        if self.state == "waiting":
            lines.append("Host start cmd: /game move start")
        return lines

    def show(self, conn=None, full: bool = False) -> list[str]:
        lines = [f"werewolf state: {self.state}", f"round: {self.round}", self._alive_line()]
        if self.state == "waiting":
            lines.append("waiting for players, then host /game move start")
        elif self.state == "night":
            lines.append("night cmds: wolf kill, seer check, witch save/poison/pass")
        elif self.state == "day":
            lines.append("day cmd: /game move vote <name>")
            lines.append(f"votes: {len(self.day_votes)}/{len(self.alive)}")
        return lines

    def on_player_leave(self, conn, name: str) -> GameResult:
        idx = None
        for i, (c, _n) in enumerate(self.players):
            if c is conn:
                idx = i
                break
        if idx is None:
            return ([], [], False)
        _c, pname = self.players.pop(idx)
        self.alive.discard(pname)
        self.roles.pop(pname, None)
        if not self.players:
            self.state = "ended"
            return ([], [f"{name} left. No players left, game ended."], True)
        if self.state == "waiting":
            return ([], [f"{name} left. waiting players: {len(self.players)}"], False)
        out = [f"{name} disconnected and is out."]
        win = self._check_win()
        if win:
            out.append(win)
            return ([], out, True)
        out.append(self._alive_line())
        return ([], out, False)


_ZJH_RANKS = "23456789TJQKA"
_ZJH_VALUES = {r: i + 2 for i, r in enumerate(_ZJH_RANKS)}
_ZJH_SUITS = ["S", "H", "D", "C"]
_POKER_SUIT_ZH = {"S": "黑桃", "H": "红桃", "D": "方块", "C": "梅花"}
_POKER_RANK_ZH = {"A": "A", "K": "K", "Q": "Q", "J": "J", "T": "10"}

def _fmt_poker_cards(cards: list[str]) -> str:
    out: list[str] = []
    for c in cards:
        if len(c) < 2:
            out.append(c)
            continue
        r, s = c[0], c[1]
        out.append(f"{_POKER_SUIT_ZH.get(s, s)}{_POKER_RANK_ZH.get(r, r)}")
    return " ".join(out)

_ZJH_TYPE_ZH = {6: "豹子", 5: "顺金", 4: "金花", 3: "顺子", 2: "对子", 1: "单张"}


def _zjh_is_special_235(cards: list[str]) -> bool:
    vals = sorted(_ZJH_VALUES[c[0]] for c in cards)
    if vals != [2, 3, 5]:
        return False
    return len({c[1] for c in cards}) == 3


def _zjh_eval3(cards: list[str]) -> tuple[int, list[int]]:
    vals = sorted((_ZJH_VALUES[c[0]] for c in cards), reverse=True)
    suits = [c[1] for c in cards]
    counts = {v: vals.count(v) for v in set(vals)}
    ordered = sorted(counts.items(), key=lambda x: (x[1], x[0]), reverse=True)
    is_flush = len(set(suits)) == 1
    uniq = sorted(set(vals))
    is_straight = len(uniq) == 3 and uniq[2] - uniq[0] == 2 and uniq[1] - uniq[0] == 1
    if sorted(vals) == [2, 3, 14]:
        is_straight = True
        vals = [3, 2, 1]
    if ordered[0][1] == 3:
        return (6, [ordered[0][0]])
    if is_straight and is_flush:
        return (5, vals)
    if is_flush:
        return (4, vals)
    if is_straight:
        return (3, vals)
    if ordered[0][1] == 2:
        pair = ordered[0][0]
        kick = max(v for v in vals if v != pair)
        return (2, [pair, kick])
    return (1, vals)


def _zjh_compare(cards_a: list[str], cards_b: list[str]) -> int:
    """Return positive if a wins, negative if b wins, 0 if tie."""
    ev_a = _zjh_eval3(cards_a)
    ev_b = _zjh_eval3(cards_b)
    if _zjh_is_special_235(cards_a) and ev_b[0] == 6:
        return 1
    if _zjh_is_special_235(cards_b) and ev_a[0] == 6:
        return -1
    if ev_a > ev_b:
        return 1
    if ev_a < ev_b:
        return -1
    return 0


def _zjh_describe(cards: list[str]) -> str:
    if _zjh_is_special_235(cards):
        return f"{_fmt_poker_cards(cards)}（235特殊）"
    cat, _tie = _zjh_eval3(cards)
    return f"{_fmt_poker_cards(cards)}（{_ZJH_TYPE_ZH.get(cat, '单张')}）"



def _game_state_zh(state: str) -> str:
    return {
        "waiting": "等待开始",
        "playing": "进行中",
        "ended": "已结束",
        "await_row": "等待选行",
    }.get(state, state)


def _street_zh(street: str) -> str:
    return {
        "preflop": "翻牌前",
        "flop": "翻牌",
        "turn": "转牌",
        "river": "河牌",
    }.get(street, street)


def _bot_level_zh(level: str) -> str:
    return {
        "easy": "简单",
        "hard": "困难",
        "pro": "专家",
    }.get(level, level)


class ZhaJinHuaGame:
    name = "zjh"
    first_seat_desc = "房主"
    join_blurb = "其他玩家可用 /game join 加入，房主用 /game move start 开始；需机器人用 start bot 或 bot add"
    def __init__(self, host_conn, host_name: str) -> None:
        self.players: list[tuple[object, str]] = [(host_conn, host_name)]
        self.bot_names: set[str] = set()
        self.bot_conns: dict[str, object] = {}
        self.bot_level = "hard"
        self._bot_running = False
        self.state = "waiting"
        self.folded: set[str] = set()
        self.looked: set[str] = set()
        self.cards: dict[str, list[str]] = {}
        self.stacks: dict[str, int] = {host_name: 1000}
        self.pot = 0
        self.current_bet = 1
        self.turn_idx = 0
        self.rng = random.Random()
    def _is_bot(self, name: str) -> bool:
        return name in self.bot_names

    def _add_bots(self, count: int) -> list[str]:
        added: list[str] = []
        for _ in range(max(0, count)):
            if len(self.players) >= 6:
                break
            idx = len(self.bot_names) + 1
            bn = f"R{idx}"
            while any(n == bn for _c, n in self.players):
                idx += 1
                bn = f"R{idx}"
            bc = object()
            self.players.append((bc, bn))
            self.bot_names.add(bn)
            self.bot_conns[bn] = bc
            self.stacks.setdefault(bn, 1000)
            added.append(bn)
        return added

    def _auto_add_bots(self) -> list[str]:
        human = sum(1 for c, n in self.players if c is not None and n not in self.bot_names)
        # 仅在使用 bot/机器人 参数开局时调用：尽量补到 3 个机器人（最多 6 人）。
        target_bots = min(3, max(0, 6 - human))
        seated_bots = sum(1 for _c, n in self.players if n in self.bot_names)
        add_n = min(max(0, target_bots - seated_bots), 6 - len(self.players))
        return self._add_bots(add_n)

    def _bot_action(self, name: str) -> str:
        cat, tie = _zjh_eval3(self.cards[name])
        high = tie[0] if tie else 0
        looked = name in self.looked
        mult = 2 if looked else 1
        stack = max(0, self.stacks.get(name, 0))
        to_call = self.current_bet * mult
        compare_cost = self.current_bet * mult * 2
        # 加注是“加到当前注 + add”，支付金额是 new_bet * mult。
        max_add = max(0, (stack // mult) - self.current_bet)
        can_raise = max_add > 0
        can_call = stack >= to_call
        can_compare = stack > 0 and len([n for n in self._alive() if n != name]) > 0
        if not can_call:
            return "fold"

        # 风险强度：需要支付的筹码占自己剩余积分的比例。
        risk = to_call / max(1, stack)
        targets = [n for n in self._alive() if n != name]

        def _raise_cmd(base_add: int) -> str:
            if not can_raise:
                return "follow"
            add = max(1, min(base_add, max_add))
            return f"raise {add}"

        # 牌型：6豹子 > 5同花顺 > 4同花 > 3顺子 > 2对子 > 1高牌
        if self.bot_level == "easy":
            if cat >= 4 and can_raise and self.rng.random() < 0.35:
                return _raise_cmd(1)
            if cat == 1 and risk >= 0.18 and self.rng.random() < 0.55:
                return "fold"
            if not looked and self.rng.random() < 0.35:
                return "look"
            return self.rng.choice(["follow", "follow", "fold"])

        if self.bot_level == "hard":
            if cat >= 5:
                if can_raise and self.rng.random() < 0.55:
                    return _raise_cmd(2)
                if can_compare and self.rng.random() < 0.20:
                    return f"compare {self.rng.choice(targets)}"
                return "follow"
            if cat == 4:
                if can_raise and self.rng.random() < 0.35:
                    return _raise_cmd(1)
                if can_compare and self.rng.random() < 0.15:
                    return f"compare {self.rng.choice(targets)}"
                return "follow"
            if cat == 3:
                if risk < 0.20:
                    return "follow"
                return "fold" if self.rng.random() < 0.35 else "follow"
            if cat == 2:
                # 对子不应被误判为“必弃牌”
                if high >= 11 and can_raise and risk < 0.15 and self.rng.random() < 0.25:
                    return _raise_cmd(1)
                if risk < 0.22:
                    return "follow"
                return "fold" if self.rng.random() < 0.28 else "follow"
            # 高牌
            if high >= 13 and risk < 0.16:
                return "follow"
            if not looked and risk < 0.10 and self.rng.random() < 0.20:
                return "look"
            return "fold" if risk >= 0.14 else "follow"

        # pro: 更激进且会控制风险；强牌更会主动制造压力。
        if cat >= 5:
            if can_raise and self.rng.random() < 0.70:
                return _raise_cmd(3)
            if can_compare and self.rng.random() < 0.35:
                return f"compare {self.rng.choice(targets)}"
            return "follow"
        if cat == 4:
            if can_raise and self.rng.random() < 0.50:
                return _raise_cmd(2)
            if can_compare and self.rng.random() < 0.22:
                return f"compare {self.rng.choice(targets)}"
            return "follow"
        if cat == 3:
            if can_raise and risk < 0.18 and self.rng.random() < 0.22:
                return _raise_cmd(1)
            return "follow" if risk < 0.28 else ("fold" if self.rng.random() < 0.20 else "follow")
        if cat == 2:
            # 对子在 pro 难度下通常会继续，尤其是高对子。
            if high >= 12 and can_raise and risk < 0.20 and self.rng.random() < 0.30:
                return _raise_cmd(2)
            if risk < 0.30:
                return "follow"
            return "fold" if self.rng.random() < 0.18 else "follow"
        # 高牌：根据 kicker 与风险决定，保留少量诈唬/探测。
        if high >= 13 and risk < 0.24:
            if can_raise and self.rng.random() < 0.10:
                return _raise_cmd(1)
            return "follow"
        if not looked and risk < 0.12 and self.rng.random() < 0.25:
            return "look"
        return "fold" if risk >= 0.20 else ("follow" if self.rng.random() < 0.80 else "look")
    def _bot_turn(self) -> bool:
        if self.state != "playing" or not self.players:
            return False
        return self.players[self.turn_idx][1] in self.bot_names

    def nudge_bots(self) -> list[str]:
        """Resume bot turns after reconnect, deploy, or idle human seats."""
        if not self._bot_turn():
            return []
        return self._run_bots()

    def _run_bots(self) -> list[str]:
        if self._bot_running:
            return []
        out: list[str] = []
        guard = 0
        self._bot_running = True
        try:
            while self.state == "playing" and guard < 32:
                guard += 1
                cur = self.players[self.turn_idx][1]
                if cur not in self._alive():
                    if cur in self._not_folded() and self.stacks.get(cur, 0) <= 0:
                        self.folded.add(cur)
                        out.append(f"{cur} 积分耗尽，自动弃牌")
                        done = self._finish_if_one()
                        if done:
                            out.extend(done)
                            break
                        self._advance()
                        continue
                    done = self._finish_if_one()
                    if done:
                        out.extend(done)
                        break
                    self._advance()
                    continue
                if cur not in self.bot_names:
                    break
                bot_conn = self.bot_conns.get(cur)
                if bot_conn is None:
                    break
                action = self._bot_action(cur)
                _err, b, _done = self.try_move(bot_conn, action)
                if _err:
                    _err2, b2, _done2 = self.try_move(bot_conn, "fold")
                    out.extend(b2)
                    if _err2:
                        break
                else:
                    out.extend(b)
                if self.state == "ended":
                    break
        finally:
            self._bot_running = False
        return out
    def _name_of(self, conn) -> Optional[str]:
        for c, n in self.players:
            if c is conn:
                return n
        return None
    def _not_folded(self) -> list[str]:
        return [n for _c, n in self.players if n not in self.folded]

    def _alive(self) -> list[str]:
        return [n for n in self._not_folded() if self.stacks.get(n, 0) > 0]
    def _pick_next_actor_from_start(self) -> bool:
        alive = set(self._alive())
        for i, (_c, n) in enumerate(self.players):
            if n in alive:
                self.turn_idx = i
                return True
        return False
    def _resolve_target_name(self, token: str) -> Optional[str]:
        t = token.strip()
        if not t:
            return None
        if t.startswith("#") and t[1:].isdigit():
            idx = int(t[1:]) - 1
            if 0 <= idx < len(self.players):
                return self.players[idx][1]
            return None
        for _c, n in self.players:
            if n == t:
                return n
        for _c, n in self.players:
            if n.lower() == t.lower():
                return n
        return None
    def _advance(self):
        alive = self._alive()
        if len(alive) <= 1:
            return
        for _ in range(len(self.players)):
            self.turn_idx = (self.turn_idx + 1) % len(self.players)
            if self.players[self.turn_idx][1] in alive:
                return
    def _can_continue_session(self) -> bool:
        if any(self.stacks.get(n, 0) <= 0 for _c, n in self.players):
            return False
        return len([n for _c, n in self.players if self.stacks.get(n, 0) > 0]) >= 2

    def _deal_hand(self) -> list[str]:
        self.state = "playing"
        self.folded.clear()
        self.looked.clear()
        self.cards.clear()
        self.turn_idx = 0
        self.pot = 0
        self.current_bet = 1
        deck = [f"{r}{s}" for r in _ZJH_RANKS for s in _ZJH_SUITS]
        self.rng.shuffle(deck)
        for _c, n in self.players:
            if self.stacks.get(n, 0) <= 0:
                continue
            self.cards[n] = [deck.pop(), deck.pop(), deck.pop()]
            self.stacks.setdefault(n, 1000)
            if self.stacks[n] > 0:
                self.stacks[n] -= 1
                self.pot += 1
        self._pick_next_actor_from_start()
        out = ["炸金花已开始，默认闷牌；先看牌后可见手牌。", f"底池={self.pot}", f"轮到：{self.players[self.turn_idx][1]}"]
        if self.bot_names:
            out.append(f"机器人：{', '.join(sorted(self.bot_names))}（难度={_bot_level_zh(self.bot_level)}）")
        out.extend(self._run_bots())
        return out

    def _begin_next_hand(self) -> list[str]:
        if not self._can_continue_session():
            self.state = "ended"
            return ["有玩家积分已耗尽或人数不足，炸金花对局结束。"]
        out = ["—— 自动开始下一局 ——"]
        out.extend(self._deal_hand())
        return out

    def _finish_if_one(self) -> Optional[list[str]]:
        remaining = self._not_folded()
        if len(remaining) != 1:
            return None
        w = remaining[0]
        gain = self.pot
        self.stacks[w] += gain
        self.pot = 0
        self.current_bet = 1
        out = [f"{w} 因其他玩家弃牌获胜，底池 +{gain}"]
        out.extend(self._begin_next_hand())
        return out

    def _start(self, with_bots: bool = False) -> list[str]:
        if with_bots:
            self._auto_add_bots()
        if len(self.players) < 2:
            return ["至少需要 2 名玩家才能开始。"]
        if any(self.stacks.get(n, 0) <= 0 for _c, n in self.players):
            return ["有玩家积分已耗尽。请重开游戏重置为1000积分。"]
        return self._deal_hand()
    def try_join(self, conn, name: str) -> GameResult:
        if self.state != "waiting": return (["对局已开始，无法加入。"], [], False)
        if len(self.players) >= 6: return (["炸金花最多 6 人。"], [], False)
        if any(n == name for _c, n in self.players): return (["该昵称已在席位中。"], [], False)
        self.players.append((conn, name)); self.stacks.setdefault(name, 1000)
        return ([], [f"{name} 加入了炸金花（{len(self.players)}/6）"], False)
    def try_move(self, conn, raw: str) -> GameResult:
        actor = self._name_of(conn)
        if actor is None: return (["你不在本局中。"], [], False)
        parts = raw.strip().split()
        if not parts: return (["用法：/game move <start/look/follow/raise/fold/compare>"], [], False)
        cmd = parts[0].lower()
        cmd = {
            "开始": "start",
            "看牌": "look",
            "跟注": "follow",
            "加注": "raise",
            "弃牌": "fold",
            "比牌": "compare",
            "机器人": "bot",
        }.get(cmd, cmd)
        if cmd == "bot":
            if conn is not self.players[0][0]:
                return (["只有房主可以设置机器人。"], [], False)
            if len(parts) < 2:
                return (["用法：/game move bot <easy|hard|pro>  或  bot add [人数]"], [], False)
            sub = parts[1].lower()
            if sub == "add":
                if self.state != "waiting":
                    return (["只能在等待开始阶段添加机器人。"], [], False)
                n = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else 1
                if n <= 0:
                    return (["添加人数必须大于 0。"], [], False)
                added = self._add_bots(n)
                if not added:
                    return (["无法添加更多机器人（已满 6 人）。"], [], False)
                return ([], [f"已添加机器人：{', '.join(added)}（难度={_bot_level_zh(self.bot_level)}）"], False)
            if sub not in ("easy", "hard", "pro"):
                return (["用法：/game move bot <easy|hard|pro>  或  bot add [人数]"], [], False)
            self.bot_level = sub
            return ([], [f"机器人难度已设为：{_bot_level_zh(self.bot_level)}"], False)
        if cmd == "start":
            if self.state == "playing": return (["对局已经开始。"], [], False)
            if conn is not self.players[0][0]: return (["只有房主可以开始对局。"], [], False)
            with_bots = len(parts) >= 2 and parts[1].lower() in ("bot", "bots", "withbots", "机器人")
            return ([], self._start(with_bots=with_bots), False)
        if self.state != "playing": return (["当前不是进行中状态。"], [], False)
        if actor in self.folded:
            bcast = self.nudge_bots()
            return (["你已经弃牌。"], bcast, self.state == "ended")
        done = self._finish_if_one()
        if done:
            return ([], done, self.state == "ended")
        if self.stacks.get(actor, 0) <= 0: return (["你已无可用积分，无法继续操作。"], [], False)
        current = self.players[self.turn_idx][1]
        if actor != current: return ([f"还没轮到你，当前轮到：{current}"], [], False)
        mult = 2 if actor in self.looked else 1; cost = self.current_bet * mult; bcast: list[str] = []
        if cmd == "look": self.looked.add(actor); bcast.append(f"{actor} 选择了看牌"); self._advance()
        elif cmd in ("follow", "call"):
            if self.stacks[actor] < cost: return ([f"积分不足，需要 {cost}"], [], False)
            self.stacks[actor] -= cost; self.pot += cost; bcast.append(f"{actor} 跟注 {cost}"); self._advance()
        elif cmd == "raise":
            if len(parts) < 2 or not parts[1].isdigit(): return (["用法：/game move raise <amount>"], [], False)
            add = int(parts[1])
            if add <= 0: return (["加注金额必须大于 0。"], [], False)
            new_bet = self.current_bet + add; pay = new_bet * mult
            if self.stacks[actor] < pay: return ([f"积分不足，需要 {pay}"], [], False)
            self.current_bet = new_bet; self.stacks[actor] -= pay; self.pot += pay; bcast.append(f"{actor} 加注到 {self.current_bet}（支付 {pay}）"); self._advance()
        elif cmd == "fold": self.folded.add(actor); bcast.append(f"{actor} 选择弃牌"); self._advance()
        elif cmd == "compare":
            if len(parts) < 2: return (["用法：/game move compare <name>"], [], False)
            target = self._resolve_target_name(parts[1])
            if not target: return (["目标不存在。"], [], False)
            if target == actor: return (["不能和自己比牌。"], [], False)
            if target not in self._alive(): return (["目标玩家当前不可比牌。"], [], False)
            compare_cost = self.current_bet * mult * 2
            pay = min(self.stacks[actor], compare_cost)
            if pay <= 0:
                return (["你已无可用积分，无法继续操作。"], [], False)
            self.stacks[actor] -= pay; self.pot += pay
            cmp = _zjh_compare(self.cards[actor], self.cards[target])
            if cmp > 0:
                winner, loser = actor, target
            elif cmp < 0:
                winner, loser = target, actor
            else:
                winner, loser = target, actor
            self.folded.add(loser)
            if pay < compare_cost:
                bcast.append(f"{actor} 积分不足（需 {compare_cost}，实付 {pay}）发起全压比牌")
            bcast.append(
                f"{actor} 与 {target} 比牌："
                f"{actor} {_zjh_describe(self.cards[actor])} vs "
                f"{target} {_zjh_describe(self.cards[target])}，"
                f"{winner} 胜出，{loser} 弃牌"
            )
            self._advance()
        else: return (["可用操作：开始、看牌、跟注、加注、弃牌、比牌。"], [], False)
        done = self._finish_if_one()
        if done: return ([], bcast + done, self.state == "ended")
        if not self._bot_running:
            bcast.extend(self._run_bots())
        bcast.append(f"底池={self.pot}，当前注={self.current_bet}，轮到：{self.players[self.turn_idx][1]}")
        return ([], bcast, False)
    def resign(self, conn, name: str) -> GameResult: return self.try_move(conn, "fold")
    def abort(self, conn, name: str) -> GameResult:
        if conn is not self.players[0][0]: return (["只有房主可以终止对局。"], [], False)
        self.state = "ended"; return ([], [f"{name} 终止了炸金花对局"], True)
    def seats(self) -> list[str]:
        lines = [f"炸金花 状态：{_game_state_zh(self.state)}", f"底池={self.pot}", f"当前注={self.current_bet}"]
        for i, (_c, n) in enumerate(self.players, start=1):
            tag = "已弃牌" if n in self.folded else "存活"; looked = "，已看牌" if n in self.looked else ""
            lines.append(f"#{i} {n}：积分={self.stacks.get(n, 0)} {tag}{looked}")
        if self.state == "waiting": lines.append("房主可用 /game move start 开始；需机器人时用 start bot 或 bot add")
        if self.state == "playing": lines.append(f"轮到：{self.players[self.turn_idx][1]}")
        return lines
    def show(self, conn=None, full: bool = False) -> list[str]:
        lines = self.seats(); me = self._name_of(conn) if conn is not None else None
        if me and me in self.cards:
            if me in self.looked:
                lines.append(f"你的手牌：{_fmt_poker_cards(self.cards[me])}")
            else:
                lines.append("你当前闷牌中（先看牌后可见）")
        return lines
    def on_player_leave(self, conn, name: str) -> GameResult:
        removed_idx = None
        for i, (c, n) in enumerate(self.players):
            if c is conn:
                removed_idx = i
                self.players.pop(i)
                self.folded.add(n)
                self.cards.pop(n, None)
                self.stacks.pop(n, None)
                break
        if not self.players: self.state = "ended"; return ([], [f"{name} 离开，炸金花对局已结束"], True)
        if removed_idx is not None:
            if removed_idx < self.turn_idx:
                self.turn_idx -= 1
            if self.turn_idx >= len(self.players):
                self.turn_idx = 0
            if self.state == "playing":
                alive = self._alive()
                if alive and self.players[self.turn_idx][1] not in alive:
                    self._advance()
        done = self._finish_if_one() if self.state == "playing" else None
        if done: return ([], [f"{name} 离开"] + done, self.state == "ended")
        return ([], [f"{name} 离开了炸金花对局"], False)


_HOLDEM_MOVE_HELP = (
    "德州扑克 /game move 指令（中文与英文等价，任选一种）：",
    "  开始 start                 房主开局",
    "  看牌 look                  查看自己的底牌（仅自己可见，不占行动轮次）",
    "  过牌 check                 当前无需跟注时过牌",
    "  跟注 call                  跟平当前注（无需跟注时等同过牌）",
    "  加注 <额> raise <额>       在现有注额上再加",
    "  弃牌 fold",
    "  全下 allin",
    "  机器人 <难度> bot <easy|hard|pro>   房主设置机器人",
    "示例：/game move 跟注  或  /game move call  ；/game move 加注 10  或  raise 10",
)


class HoldemGame:
    name = "holdem"
    first_seat_desc = "房主"
    join_blurb = (
        "其他玩家 /game join；房主 /game move 开始（或 start）开局；"
        "/game show 帮助 查看完整中英指令"
    )

    def __init__(self, host_conn, host_name: str) -> None:
        self.players: list[tuple[object, str]] = [(host_conn, host_name)]
        self.bot_names: set[str] = set()
        self.bot_conns: dict[str, object] = {}
        self.bot_level = "hard"
        self._bot_running = False

        self.state = "waiting"
        self.folded: set[str] = set()
        self.looked: set[str] = set()
        self.hands: dict[str, list[str]] = {}
        self.stacks: dict[str, int] = {host_name: 1000}

        self.board: list[str] = []
        self.pot = 0
        self.street = "preflop"
        self.turn_idx = 0

        # 当前街下注状态
        self.current_bet = 0
        self.round_bet: dict[str, int] = {}
        self.acted: set[str] = set()

        self.rng = random.Random()

    def _name_of(self, conn) -> Optional[str]:
        for c, n in self.players:
            if c is conn:
                return n
        return None

    def _is_alive(self, name: str) -> bool:
        return any(n == name for _c, n in self.players) and name not in self.folded

    def _alive(self) -> list[str]:
        return [n for _c, n in self.players if self._is_alive(n)]

    def _can_act(self, name: str) -> bool:
        return self._is_alive(name) and self.stacks.get(name, 0) > 0

    def _can_act_names(self) -> list[str]:
        return [n for _c, n in self.players if self._can_act(n)]

    def _auto_add_bots(self) -> list[str]:
        human = sum(1 for c, n in self.players if c is not None and n not in self.bot_names)
        # 目标：尽量保证至少 3 个机器人（受 6 人总席位限制）。
        # 例如：2真人 -> 3机器人；4真人 -> 2机器人（已满 6 人上限）。
        target_bots = min(3, max(0, 6 - human))
        seated_bots = sum(1 for _c, n in self.players if n in self.bot_names)
        add_n = min(max(0, target_bots - seated_bots), 6 - len(self.players))
        added: list[str] = []
        for _ in range(add_n):
            idx = len(self.bot_names) + 1
            bn = f"R{idx}"
            while any(n == bn for _c, n in self.players):
                idx += 1
                bn = f"R{idx}"
            bc = object()
            self.players.append((bc, bn))
            self.bot_names.add(bn)
            self.bot_conns[bn] = bc
            self.stacks.setdefault(bn, 1000)
            added.append(bn)
        return added

    def _fmt(self, cards: list[str]) -> str:
        return _fmt_poker_cards(cards)

    def _to_call(self, name: str) -> int:
        return max(0, self.current_bet - self.round_bet.get(name, 0))

    def _pick_next_actor_from_start(self) -> bool:
        for i, (_c, n) in enumerate(self.players):
            if self._can_act(n):
                self.turn_idx = i
                return True
        return False

    def _advance(self) -> None:
        if len(self._can_act_names()) <= 1:
            return
        for _ in range(len(self.players)):
            self.turn_idx = (self.turn_idx + 1) % len(self.players)
            if self._can_act(self.players[self.turn_idx][1]):
                return

    def _reset_street_bets(self) -> None:
        self.current_bet = 0
        self.acted.clear()
        self.round_bet = {n: 0 for _c, n in self.players if self._is_alive(n)}
        self._pick_next_actor_from_start()

    def _street_complete(self) -> bool:
        active = self._can_act_names()
        if len(active) <= 1:
            return True
        for n in active:
            if self.round_bet.get(n, 0) != self.current_bet:
                return False
            if n not in self.acted:
                return False
        return True

    def _eval5(self, cards: list[str]) -> tuple[int, list[int]]:
        vals = sorted((_ZJH_VALUES[c[0]] for c in cards), reverse=True)
        suits = [c[1] for c in cards]
        counts = {v: vals.count(v) for v in set(vals)}
        ordered = sorted(counts.items(), key=lambda x: (x[1], x[0]), reverse=True)
        uniq = sorted(set(vals))
        straight = len(uniq) == 5 and uniq[-1] - uniq[0] == 4
        if sorted(vals) == [2, 3, 4, 5, 14]:
            straight = True
            vals = [5, 4, 3, 2, 1]
        flush = len(set(suits)) == 1
        if straight and flush:
            return (9, vals)
        if ordered[0][1] == 4:
            return (8, [ordered[0][0]])
        if ordered[0][1] == 3 and ordered[1][1] == 2:
            return (7, [ordered[0][0], ordered[1][0]])
        if flush:
            return (6, vals)
        if straight:
            return (5, vals)
        if ordered[0][1] == 3:
            return (4, [ordered[0][0]])
        if ordered[0][1] == 2 and ordered[1][1] == 2:
            return (3, sorted([ordered[0][0], ordered[1][0]], reverse=True))
        if ordered[0][1] == 2:
            return (2, [ordered[0][0]])
        return (1, vals)

    def _best7(self, cards: list[str]) -> tuple[int, list[int]]:
        best = None
        for c5 in itertools.combinations(cards, 5):
            sc = self._eval5(list(c5))
            if best is None or sc > best:
                best = sc
        return best if best is not None else (0, [])

    def _finish_if_one(self) -> Optional[list[str]]:
        alive = self._alive()
        if len(alive) != 1:
            return None
        w = alive[0]
        gain = self.pot
        self.stacks[w] += gain
        self.pot = 0
        self.state = "ended"
        return [f"{w} 因其他玩家弃牌获胜，底池 +{gain}"]

    def _showdown(self) -> list[str]:
        alive = self._alive()
        if not alive:
            self.state = "ended"
            return ["对局已结束：无存活玩家。"]
        scored = sorted(
            [(n, self._best7(self.hands[n] + self.board)) for n in alive],
            key=lambda kv: kv[1],
            reverse=True,
        )
        best_score = scored[0][1]
        winners = [n for n, sc in scored if sc == best_score]
        share = self.pot // len(winners)
        remainder = self.pot % len(winners)
        for i, w in enumerate(winners):
            self.stacks[w] += share + (1 if i < remainder else 0)
        self.pot = 0
        self.state = "ended"
        lines = ["摊牌：", f"公共牌：{self._fmt(self.board)}"]
        for n, _sc in scored:
            lines.append(f"- {n}: {self._fmt(self.hands[n])}")
        if len(winners) == 1:
            lines.append(f"获胜者：{winners[0]}，底池 +{share}")
        else:
            lines.append(f"平局：{'、'.join(winners)}，各 +{share}")
        return lines

    def _next_street(self) -> list[str]:
        if self.street == "preflop":
            self.street = "flop"
            self.board.extend([self.deck.pop(), self.deck.pop(), self.deck.pop()])
        elif self.street == "flop":
            self.street = "turn"
            self.board.append(self.deck.pop())
        elif self.street == "turn":
            self.street = "river"
            self.board.append(self.deck.pop())
        else:
            return self._showdown()

        self._reset_street_bets()
        out = [f"阶段：{_street_zh(self.street)}", f"公共牌：{self._fmt(self.board)}", f"底池={self.pot}"]
        actors = self._can_act_names()
        if actors:
            out.append(f"轮到：{self.players[self.turn_idx][1]}")
        else:
            out.append("本阶段无人可下注。")
        return out

    def _auto_finish_allin_streets(self) -> list[str]:
        out: list[str] = []
        while self.state == "playing" and len(self._can_act_names()) <= 1:
            out.append("无人可继续下注，自动推进公共牌。")
            out.extend(self._next_street())
            if self.state == "ended":
                break
        return out

    def _start(self) -> list[str]:
        self._auto_add_bots()
        if len(self.players) < 2:
            return ["至少需要 2 名玩家才能开始。"]
        if any(self.stacks.get(n, 0) <= 0 for _c, n in self.players):
            return ["有玩家积分已耗尽。请重开游戏重置为1000积分。"]

        self.state = "playing"
        self.folded.clear()
        self.looked.clear()
        self.hands.clear()
        self.board = []
        self.pot = 0
        self.street = "preflop"

        self.deck = [f"{r}{s}" for r in _ZJH_RANKS for s in _ZJH_SUITS]
        self.rng.shuffle(self.deck)

        for _c, n in self.players:
            self.hands[n] = [self.deck.pop(), self.deck.pop()]
            self.stacks.setdefault(n, 1000)
            ante = min(1, self.stacks[n])
            self.stacks[n] -= ante
            self.pot += ante

        self._reset_street_bets()

        out = ["德州扑克开始", f"底池={self.pot}", "公共牌：未发", f"轮到：{self.players[self.turn_idx][1]}"]
        if self.bot_names:
            out.append(f"机器人：{', '.join(sorted(self.bot_names))}（难度={_bot_level_zh(self.bot_level)}）")
        out.extend(self._auto_finish_allin_streets())
        out.extend(self._run_bots())
        return out

    def try_join(self, conn, name: str) -> GameResult:
        if self.state != "waiting":
            return (["对局已开始，无法加入。"], [], False)
        if len(self.players) >= 6:
            return (["最多 6 名玩家。"], [], False)
        if any(n == name for _c, n in self.players):
            return (["该昵称已被占用。"], [], False)
        self.players.append((conn, name))
        self.stacks.setdefault(name, 1000)
        return ([], [f"{name} 加入了德州扑克（{len(self.players)}/6）"], False)

    def _bot_action(self, name: str) -> str:
        to_call = self._to_call(name)
        if to_call <= 0:
            # 可过牌时，机器人按牌力少量加注，更多选择过牌
            if self.street == "preflop":
                a, b = self.hands[name]
                va = _ZJH_VALUES[a[0]]
                vb = _ZJH_VALUES[b[0]]
                pair = a[0] == b[0]
                high = max(va, vb)
                if self.bot_level == "pro" and (pair or high >= 13) and self.rng.random() < 0.45:
                    return "raise 4"
                if self.bot_level == "hard" and (pair or high >= 12) and self.rng.random() < 0.25:
                    return "raise 2"
            else:
                score = self._best7(self.hands[name] + self.board)[0]
                if self.bot_level == "pro" and score >= 6 and self.rng.random() < 0.45:
                    return "raise 6"
                if self.bot_level == "hard" and score >= 4 and self.rng.random() < 0.25:
                    return "raise 3"
            return "check"

        # 需要跟注时
        if self.street == "preflop":
            a, b = self.hands[name]
            va = _ZJH_VALUES[a[0]]
            vb = _ZJH_VALUES[b[0]]
            pair = a[0] == b[0]
            high = max(va, vb)
            if self.bot_level == "easy":
                return self.rng.choice(["call", "call", "fold"])
            if self.bot_level == "pro":
                if pair and high >= 9 and self.rng.random() < 0.35:
                    return "raise 4"
                if pair or high >= 12:
                    return "call"
                return "fold" if to_call >= 8 else "call"
            if pair or high >= 11:
                return "call"
            return "fold" if to_call >= 8 else "call"

        score = self._best7(self.hands[name] + self.board)[0]
        if self.bot_level == "easy":
            return self.rng.choice(["call", "call", "fold"])
        if self.bot_level == "pro":
            if score >= 7 and self.rng.random() < 0.35:
                return "raise 6"
            if score >= 3:
                return "call"
            return "fold" if to_call >= 10 else "call"
        if score >= 4:
            return "call"
        return "fold" if to_call >= 10 else "call"

    def _run_bots(self) -> list[str]:
        if self._bot_running:
            return []
        out: list[str] = []
        guard = 0
        self._bot_running = True
        try:
            while self.state == "playing" and guard < 80:
                guard += 1
                if not self.players:
                    break
                cur = self.players[self.turn_idx][1]
                if not self._can_act(cur):
                    self._advance()
                    continue
                if cur not in self.bot_names:
                    break
                bot_conn = self.bot_conns.get(cur)
                if bot_conn is None:
                    break
                _err, b, _done = self.try_move(bot_conn, self._bot_action(cur))
                out.extend(b)
                if self.state == "ended":
                    break
        finally:
            self._bot_running = False
        return out

    def try_move(self, conn, raw: str) -> GameResult:
        name = self._name_of(conn)
        if name is None:
            return (["你不在本局中。"], [], False)
        parts = raw.strip().split()
        if not parts:
            return (list(_HOLDEM_MOVE_HELP), [], False)
        cmd = {
            "开始": "start",
            "看牌": "look",
            "过牌": "check",
            "过": "check",
            "跟注": "call",
            "跟": "call",
            "加注": "raise",
            "弃牌": "fold",
            "全下": "allin",
            "机器人": "bot",
        }.get(parts[0], parts[0].lower())

        if cmd == "bot":
            if conn is not self.players[0][0]:
                return (["只有房主可以设置机器人难度。"], [], False)
            if len(parts) < 2 or parts[1].lower() not in ("easy", "hard", "pro"):
                return (
                    ["用法：/game move 机器人 <easy|hard|pro>  或  bot <easy|hard|pro>"],
                    [],
                    False,
                )
            self.bot_level = parts[1].lower()
            return ([], [f"机器人难度已设为：{_bot_level_zh(self.bot_level)}"], False)

        if cmd == "start":
            if self.state == "playing":
                return (["对局已经开始。"], [], False)
            if conn is not self.players[0][0]:
                return (["只有房主可以开始对局。"], [], False)
            return ([], self._start(), False)

        if self.state != "playing":
            return (["当前不是进行中状态。"], [], False)
        if name in self.folded:
            return (["你已经弃牌。"], [], False)
        if self.stacks.get(name, 0) <= 0:
            return (["你已全下，等待本轮结算。"], [], False)
        if cmd == "look":
            if name in self.looked:
                return ([], [f"{name} 已经看过牌。"], False)
            self.looked.add(name)
            return ([], [f"{name} 选择了看牌"], False)

        cur = self.players[self.turn_idx][1]
        if name != cur:
            return ([f"还没轮到你，当前轮到：{cur}"], [], False)

        to_call = self._to_call(name)
        b: list[str] = []

        if cmd == "fold":
            self.folded.add(name)
            self.acted.add(name)
            b.append(f"{name} 选择弃牌")

        elif cmd == "check":
            if to_call > 0:
                return ([f"当前需跟注 {to_call}，不能过牌。"], [], False)
            self.acted.add(name)
            b.append(f"{name} 过牌")

        elif cmd == "call":
            if to_call <= 0:
                self.acted.add(name)
                b.append(f"{name} 过牌")
            else:
                pay = min(to_call, self.stacks[name])
                self.stacks[name] -= pay
                self.pot += pay
                self.round_bet[name] = self.round_bet.get(name, 0) + pay
                self.acted.add(name)
                if pay < to_call:
                    b.append(f"{name} 跟注未满并全下 {pay}")
                else:
                    b.append(f"{name} 跟注 {pay}")

        elif cmd == "raise":
            if len(parts) < 2 or not parts[1].isdigit():
                return (
                    ["用法：/game move 加注 <金额>  或  /game move raise <amount>"],
                    [],
                    False,
                )
            add = int(parts[1])
            if add <= 0:
                return (["加注金额必须大于 0。"], [], False)
            target = self.current_bet + add
            need = target - self.round_bet.get(name, 0)
            if need <= to_call:
                return (["加注金额过小。"], [], False)
            if need > self.stacks[name]:
                return ([f"积分不足，需要 {need}；可用 allin。"], [], False)
            self.stacks[name] -= need
            self.pot += need
            self.round_bet[name] = self.round_bet.get(name, 0) + need
            self.current_bet = self.round_bet[name]
            self.acted = {name}
            b.append(f"{name} 加注到 {self.current_bet}（支付 {need}）")

        elif cmd == "allin":
            pay = self.stacks[name]
            if pay <= 0:
                return (["你已无可下注积分。"], [], False)
            self.stacks[name] = 0
            self.pot += pay
            self.round_bet[name] = self.round_bet.get(name, 0) + pay
            if self.round_bet[name] > self.current_bet:
                self.current_bet = self.round_bet[name]
                self.acted = {name}
                b.append(f"{name} 全下并把当前注抬到 {self.current_bet}")
            else:
                self.acted.add(name)
                b.append(f"{name} 全下 {pay}")

        else:
            return (["未知指令。"] + list(_HOLDEM_MOVE_HELP), [], False)

        done = self._finish_if_one()
        if done:
            return ([], b + done, True)

        if self._street_complete():
            b.extend(self._next_street())
            b.extend(self._auto_finish_allin_streets())
        else:
            self._advance()

        if not self._bot_running:
            b.extend(self._run_bots())

        if self.state == "playing":
            b.append(f"底池={self.pot}")
            b.append(f"公共牌：{self._fmt(self.board) if self.board else '未发'}")
            if self.players:
                b.append(f"轮到：{self.players[self.turn_idx][1]}")

        return ([], b, self.state == "ended")

    def resign(self, conn, name: str) -> GameResult:
        return self.try_move(conn, "fold")

    def abort(self, conn, name: str) -> GameResult:
        if conn is not self.players[0][0]:
            return (["只有房主可以终止对局。"], [], False)
        self.state = "ended"
        return ([], [f"{name} 终止了德州扑克对局"], True)

    def seats(self) -> list[str]:
        lines = [
            f"德州扑克 状态：{_game_state_zh(self.state)}",
            f"阶段：{_street_zh(self.street)}",
            f"底池={self.pot}",
            f"当前注={self.current_bet}",
            f"公共牌：{self._fmt(self.board) if self.board else '未发'}",
        ]
        if self.players:
            lines.append(f"房主：{self.players[0][1]}")
        current = self.players[self.turn_idx][1] if self.players else ""
        for i, (_c, n) in enumerate(self.players, start=1):
            if n in self.folded:
                tag = "已弃牌"
            elif self.stacks.get(n, 0) <= 0 and self.state == "playing":
                tag = "全下"
            else:
                tag = "存活"
            mark = "（行动中）" if self.state == "playing" and n == current and self._can_act(n) else ""
            looked = "，已看牌" if n in self.looked else ""
            lines.append(f"#{i} {n}：积分={self.stacks.get(n, 0)} {tag}{looked}{mark}")
        return lines

    def show(self, conn=None, full: bool = False) -> list[str]:
        lines = self.seats()
        me = self._name_of(conn) if conn is not None else None
        if me and me in self.hands:
            if me in self.looked:
                lines.append(f"你的手牌：{self._fmt(self.hands[me])}")
            else:
                lines.append("你当前闷牌中（先看牌后可见）")
        if full:
            lines.extend(_HOLDEM_MOVE_HELP)
        else:
            lines.append(
                "行牌（中英均可）：开始 start | 看牌 look | 过牌 check | 跟注 call | "
                "加注 raise <额> | 弃牌 fold | 全下 allin"
            )
            if self.state == "waiting":
                lines.append("房主 /game move 开始 发牌；人数不足会自动补机器人")
            lines.append("完整对照：/game show 帮助")
        return lines

    def on_player_leave(self, conn, name: str) -> GameResult:
        idx = None
        for i, (c, _n) in enumerate(self.players):
            if c is conn:
                idx = i
                break
        if idx is None:
            return ([], [], False)

        _c, pname = self.players.pop(idx)
        self.folded.add(pname)
        self.hands.pop(pname, None)
        self.round_bet.pop(pname, None)
        self.acted.discard(pname)
        self.stacks.pop(pname, None)

        if not self.players:
            self.state = "ended"
            return ([], [f"{name} 离开，德州扑克对局已结束"], True)

        if idx < self.turn_idx:
            self.turn_idx -= 1
        if self.turn_idx >= len(self.players):
            self.turn_idx = 0

        if self.state == "playing":
            done = self._finish_if_one()
            if done:
                return ([], [f"{name} 离开"] + done, True)
            if self.players and not self._can_act(self.players[self.turn_idx][1]):
                self._pick_next_actor_from_start()

        return ([], [f"{name} 离开了德州扑克对局"], False)


def _nt_bulls(card: int) -> int:
    if card == 55:
        return 7
    if card % 11 == 0:
        return 5
    if card % 10 == 0:
        return 3
    if card % 5 == 0:
        return 2
    return 1


class NiuTouWangGame:
    name = "niutou"
    first_seat_desc = "房主"
    join_blurb = "其他玩家可用 /game join 加入，房主用 /game move start 开始"

    def __init__(self, host_conn, host_name: str) -> None:
        self.players: list[tuple[object, str]] = [(host_conn, host_name)]
        self.bot_names: set[str] = set()
        self.bot_conns: dict[str, object] = {}
        self.bot_level = "hard"
        self.state = "waiting"
        self.rng = random.Random()
        self.hands: dict[str, list[int]] = {}
        self.rows: list[list[int]] = []
        self.penalty: dict[str, int] = {host_name: 0}
        self.picks: dict[str, int] = {}
        self.resolve_queue: list[tuple[str, int]] = []
        self.await_player: Optional[str] = None
        self.await_card: Optional[int] = None
        self.turn = 0
    def _auto_add_bots(self) -> list[str]:
        human = sum(1 for c, _n in self.players if c is not None)
        if human >= 2:
            return []
        add_n = min(5, max(3, self.rng.randint(3, 5)), 10 - len(self.players))
        added: list[str] = []
        for _ in range(add_n):
            idx = len(self.bot_names) + 1
            bn = f"R{idx}"
            while any(n == bn for _c, n in self.players):
                idx += 1
                bn = f"R{idx}"
            bc = object()
            self.players.append((bc, bn))
            self.bot_names.add(bn)
            self.bot_conns[bn] = bc
            self.penalty.setdefault(bn, 0)
            added.append(bn)
        return added
    def _bot_pick(self, name: str) -> int:
        hand = sorted(self.hands.get(name, []))
        if not hand:
            return -1
        tails = sorted(row[-1] for row in self.rows)
        if self.bot_level == "easy":
            return self.rng.choice(hand)
        safe = [c for c in hand if c > tails[0]]
        if self.bot_level == "pro":
            best = min(hand, key=lambda c: min(abs(c - t) for t in tails))
            return best if best > tails[0] else min(hand)
        return min(safe) if safe else min(hand)
    def _bot_choose_row(self, name: str) -> int:
        if self.bot_level == "easy":
            return self.rng.randint(0, 3)
        scores = [self._row_bulls(r) for r in self.rows]
        return min(range(4), key=lambda i: scores[i])
    def _auto_bot_pick_until_resolve(self) -> list[str]:
        out: list[str] = []
        if self.state != "playing":
            return out
        for _c, n in self.players:
            if n in self.bot_names and n not in self.picks and self.hands.get(n):
                c = self._bot_pick(n)
                if c in self.hands[n]:
                    self.hands[n].remove(c)
                    self.picks[n] = c
                    out.append(f"{n} 已选牌（{len(self.picks)}/{len(self.players)}）")
        if len(self.picks) == len(self.players):
            self.resolve_queue = sorted(self.picks.items(), key=lambda kv: kv[1])
            out.append("所有玩家已选牌，按牌面从小到大结算")
            out.extend(self._resolve_queue_until_pause_or_end())
        return out

    def _name_of(self, conn) -> Optional[str]:
        for c, n in self.players:
            if c is conn:
                return n
        return None

    def _row_bulls(self, row: list[int]) -> int:
        return sum(_nt_bulls(x) for x in row)

    def _render_rows(self) -> list[str]:
        return [f"第{i}行：{' '.join(str(x) for x in row)}（牛头={self._row_bulls(row)}）" for i, row in enumerate(self.rows, start=1)]

    def _start(self) -> list[str]:
        if len(self.players) < 2:
            self._auto_add_bots()
        if len(self.players) < 2:
            return ["至少需要 2 名玩家才能开始。"]
        if len(self.players) > 10:
            return ["最多 10 名玩家。"]
        deck = list(range(1, 105))
        self.rng.shuffle(deck)
        self.hands.clear()
        self.picks.clear()
        self.penalty = {name: 0 for _c, name in self.players}
        self.rows = [[deck.pop()] for _ in range(4)]
        for _c, name in self.players:
            self.hands[name] = sorted([deck.pop() for _ in range(10)])
        self.turn = 1
        self.state = "playing"
        out = ["牛头王开始：每回合用 pick 选一张牌；若小于所有行尾，必须用 row 1~4 选择吃行。"] + self._render_rows()
        if self.bot_names:
            out.append(f"机器人：{', '.join(sorted(self.bot_names))}（难度={_bot_level_zh(self.bot_level)}）")
        return out

    def try_join(self, conn, name: str) -> GameResult:
        if self.state != "waiting":
            return (["对局已开始，无法加入。"], [], False)
        if any(n == name for _c, n in self.players):
            return (["该昵称已被占用。"], [], False)
        if len(self.players) >= 10:
            return (["房间人数已满。"], [], False)
        self.players.append((conn, name))
        self.penalty.setdefault(name, 0)
        return ([], [f"{name} 加入了牛头王（{len(self.players)}/10）"], False)

    def _need_row_choice(self, card: int) -> bool:
        return card < min(row[-1] for row in self.rows)

    def _apply_card(self, name: str, card: int) -> tuple[list[str], bool]:
        b = [f"{name} 打出了 {card}"]
        if self._need_row_choice(card):
            self.state = "await_row"
            self.await_player = name
            self.await_card = card
            b.append(f"{name} 需要选行：/game move row 1~4")
            return (b, True)
        best_idx = max([i for i, row in enumerate(self.rows) if row[-1] <= card], key=lambda i: self.rows[i][-1])
        row = self.rows[best_idx]
        row.append(card)
        if len(row) > 5:
            taken = row[:-1]
            bulls = self._row_bulls(taken)
            self.penalty[name] += bulls
            self.rows[best_idx] = [row[-1]]
            b.append(f"{name} 吃了第 {best_idx+1} 行，+{bulls} 牛头")
        return (b, False)

    def _finish_turn_or_next(self) -> list[str]:
        out = self._render_rows()
        if all(len(self.hands[n]) == 0 for _c, n in self.players):
            self.state = "ended"
            rank = sorted(self.penalty.items(), key=lambda kv: kv[1])
            out.append("对局结束（牛头越少排名越高）：")
            for i, (n, p) in enumerate(rank, start=1):
                out.append(f"{i}. {n} - {p}")
            return out
        self.turn += 1
        self.picks.clear()
        self.resolve_queue.clear()
        out.append(f"第 {self.turn} 回合：请使用 /game move pick <card> 选牌")
        return out

    def _resolve_queue_until_pause_or_end(self) -> list[str]:
        out: list[str] = []
        while self.resolve_queue:
            n, c = self.resolve_queue.pop(0)
            b, paused = self._apply_card(n, c)
            out.extend(b)
            if paused:
                if self.await_player in self.bot_names:
                    if self.await_card is None:
                        self.state = "ended"
                        out.append("内部错误：待处理牌缺失，牛头王对局已结束")
                        return out
                    idx = self._bot_choose_row(self.await_player)
                    taken = self.rows[idx]
                    bulls = self._row_bulls(taken)
                    self.penalty[self.await_player] += bulls
                    self.rows[idx] = [self.await_card]
                    out.append(f"{self.await_player} 选择了第 {idx+1} 行，+{bulls} 牛头")
                    self.await_card = None
                    self.await_player = None
                    self.state = "playing"
                    continue
                out.extend(self._render_rows())
                return out
        out.extend(self._finish_turn_or_next())
        return out

    def try_move(self, conn, raw: str) -> GameResult:
        name = self._name_of(conn)
        if name is None:
            return (["你不在本局中。"], [], False)
        parts = raw.strip().split()
        if not parts:
            return (["用法：/game move start | pick <n> | row <1-4>"], [], False)
        cmd = parts[0].lower()
        if cmd == "bot":
            if conn is not self.players[0][0]:
                return (["只有房主可以设置机器人难度。"], [], False)
            if len(parts) < 2 or parts[1].lower() not in ("easy", "hard", "pro"):
                return (["用法：/game move bot <easy|hard|pro>"], [], False)
            self.bot_level = parts[1].lower()
            return ([], [f"机器人难度已设为：{_bot_level_zh(self.bot_level)}"], False)
        if cmd == "start":
            if self.state == "playing":
                return (["对局已经开始。"], [], False)
            if conn is not self.players[0][0]:
                return (["只有房主可以开始对局。"], [], False)
            return ([], self._start(), False)
        if self.state == "waiting":
            return (["对局尚未开始。"], [], False)
        if self.state == "ended":
            return (["对局已结束，请房主点击“发牌开始”开启下一局。"], [], False)
        if self.state == "await_row":
            if name != self.await_player:
                return ([f"请等待 {self.await_player} 选择吃哪一行"], [], False)
            if cmd != "row" or len(parts) < 2 or not parts[1].isdigit():
                return (["用法：/game move row 1~4"], [], False)
            idx = int(parts[1]) - 1
            if idx < 0 or idx >= 4:
                return (["行号超出范围，请输入 1~4。"], [], False)
            if self.await_player in self.bot_names:
                idx = self._bot_choose_row(self.await_player)
            if self.await_card is None:
                self.state = "ended"
                return (["内部错误：待处理牌缺失，牛头王对局已结束"], [], True)
            taken = self.rows[idx]
            bulls = self._row_bulls(taken)
            self.penalty[name] += bulls
            self.rows[idx] = [self.await_card]
            self.await_card = None
            self.await_player = None
            self.state = "playing"
            out = [f"{name} 选择了第 {idx+1} 行，+{bulls} 牛头"]
            out.extend(self._resolve_queue_until_pause_or_end())
            out.extend(self._auto_bot_pick_until_resolve())
            return ([], out, self.state == "ended")
        if cmd != "pick" or len(parts) < 2 or not parts[1].isdigit():
            return (["用法：/game move pick <card>"], [], False)
        card = int(parts[1])
        hand = self.hands.get(name, [])
        if card not in hand:
            return ([f"你的手牌中没有 {card}"], [], False)
        if name in self.picks:
            return (["本回合你已经选过牌。"], [], False)
        hand.remove(card)
        self.picks[name] = card
        bcast = [f"{name} 已选牌（{len(self.picks)}/{len(self.players)}）"]
        bcast.extend(self._auto_bot_pick_until_resolve())
        if len(self.picks) < len(self.players):
            return ([], bcast, False)
        self.resolve_queue = sorted(self.picks.items(), key=lambda kv: kv[1])
        bcast.append("所有玩家已选牌，按牌面从小到大结算")
        bcast.extend(self._resolve_queue_until_pause_or_end())
        return ([], bcast, self.state == "ended")

    def resign(self, conn, name: str) -> GameResult:
        return (["牛头王不支持认输，请使用 /game abort 终止。"], [], False)

    def abort(self, conn, name: str) -> GameResult:
        if conn is not self.players[0][0]:
            return (["只有房主可以终止对局。"], [], False)
        self.state = "ended"
        return ([], [f"{name} 终止了牛头王对局"], True)

    def seats(self) -> list[str]:
        lines = [f"牛头王 状态：{_game_state_zh(self.state)}", f"回合：{self.turn}"]
        if self.players:
            lines.append(f"房主：{self.players[0][1]}")
        for _c, n in self.players:
            lines.append(f"- {n}：牛头={self.penalty.get(n, 0)}，手牌数={len(self.hands.get(n, []))}")
        lines.extend(self._render_rows() if self.rows else [])
        return lines

    def show(self, conn=None, full: bool = False) -> list[str]:
        lines = self.seats()
        me = self._name_of(conn) if conn is not None else None
        if me and me in self.hands:
            lines.append(f"你的手牌：{' '.join(str(x) for x in sorted(self.hands[me]))}")
        if self.state == "await_row" and self.await_player == me:
            lines.append("你必须选择一行：/game move row 1~4")
        return lines

    def on_player_leave(self, conn, name: str) -> GameResult:
        idx = None
        for i, (c, _n) in enumerate(self.players):
            if c is conn:
                idx = i
                break
        if idx is None:
            return ([], [], False)
        _c, pname = self.players.pop(idx)
        self.hands.pop(pname, None)
        self.picks.pop(pname, None)
        self.penalty.pop(pname, None)
        if not self.players:
            self.state = "ended"
            return ([], [f"{name} 离开，牛头王对局已结束"], True)
        if self.state in ("playing", "await_row"):
            self.state = "ended"
            return ([], [f"{name} 离开，牛头王对局已结束"], True)
        return ([], [f"{name} 离开了牛头王对局"], False)


def _mj_tile_sort_key(tile: str) -> tuple[int, int]:
    suit = tile[0]
    rank = int(tile[1:])
    suit_order = {"m": 0, "p": 1, "s": 2, "z": 3}
    return (suit_order.get(suit, 9), rank)


def _mj_normalize_tile(raw: str) -> Optional[str]:
    t = raw.strip().lower()
    if not t:
        return None
    if len(t) == 2 and t[0] in ("m", "p", "s", "z") and t[1].isdigit():
        suit = t[0]
        rank = int(t[1])
        if suit == "z":
            return f"{suit}{rank}" if 1 <= rank <= 7 else None
        return f"{suit}{rank}" if 1 <= rank <= 9 else None
    cn = raw.strip().replace(" ", "")
    cn = cn.replace("筒子", "筒").replace("条子", "条")
    digit_map = {
        "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
        "六": 6, "七": 7, "八": 8, "九": 9,
        "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9,
    }
    if len(cn) == 2 and cn[0] in digit_map:
        d = digit_map[cn[0]]
        suit_ch = cn[1]
        if suit_ch in ("万", "萬"):
            return f"m{d}"
        if suit_ch in ("筒", "饼", "餅"):
            return f"p{d}"
        if suit_ch in ("条", "條", "索"):
            return f"s{d}"
    honor_map = {
        "东": "z1", "东风": "z1",
        "南": "z2", "南风": "z2",
        "西": "z3", "西风": "z3",
        "北": "z4", "北风": "z4",
        "中": "z5", "红中": "z5",
        "发": "z6", "发财": "z6",
        "白": "z7", "白板": "z7",
    }
    return honor_map.get(cn)


def _mj_can_form_sets(counts: dict[str, int]) -> bool:
    while True:
        active = [t for t, c in counts.items() if c > 0]
        if not active:
            return True
        tile = min(active, key=_mj_tile_sort_key)
        c = counts[tile]
        if c >= 3:
            counts[tile] -= 3
            if _mj_can_form_sets(counts):
                counts[tile] += 3
                return True
            counts[tile] += 3
        suit = tile[0]
        rank = int(tile[1:])
        if suit in ("m", "p", "s"):
            t2 = f"{suit}{rank+1}"
            t3 = f"{suit}{rank+2}"
            if rank <= 7 and counts.get(t2, 0) > 0 and counts.get(t3, 0) > 0:
                counts[tile] -= 1
                counts[t2] -= 1
                counts[t3] -= 1
                if _mj_can_form_sets(counts):
                    counts[tile] += 1
                    counts[t2] += 1
                    counts[t3] += 1
                    return True
                counts[tile] += 1
                counts[t2] += 1
                counts[t3] += 1
        return False


def _mj_is_win(hand: list[str]) -> bool:
    if len(hand) != 14:
        return False
    counts: dict[str, int] = {}
    for t in hand:
        counts[t] = counts.get(t, 0) + 1
        if counts[t] > 4:
            return False
    for pair_tile, c in list(counts.items()):
        if c < 2:
            continue
        counts[pair_tile] -= 2
        if _mj_can_form_sets(counts):
            counts[pair_tile] += 2
            return True
        counts[pair_tile] += 2
    return False


def _mj_is_seven_pairs(hand: list[str]) -> bool:
    if len(hand) != 14:
        return False
    counts: dict[str, int] = {}
    for t in hand:
        counts[t] = counts.get(t, 0) + 1
        if counts[t] > 4:
            return False
    return len(counts) == 7 and all(v in (2, 4) for v in counts.values())


def _mj_is_win_with_melds(hand: list[str], meld_count: int) -> bool:
    need = 14 - meld_count * 3
    if len(hand) != need:
        return False
    if need == 14:
        return _mj_is_win(hand) or _mj_is_seven_pairs(hand)
    if need < 2 or (need - 2) % 3 != 0:
        return False
    counts: dict[str, int] = {}
    for t in hand:
        counts[t] = counts.get(t, 0) + 1
        if counts[t] > 4:
            return False
    for pair_tile, c in list(counts.items()):
        if c < 2:
            continue
        counts[pair_tile] -= 2
        if _mj_can_form_sets(counts):
            counts[pair_tile] += 2
            return True
        counts[pair_tile] += 2
    return False


class MahjongGame:
    name = "mahjong"
    first_seat_desc = "东家（房主）"
    join_blurb = (
        "凑齐 4 人（不足时 start 自动补 AI）后房主 /game move start 开始；"
        "轮到你时用 /game move discard <牌> 出牌。"
    )

    def __init__(self, host_conn, host_name: str) -> None:
        self.players: list[tuple[object, str]] = [(host_conn, host_name)]
        self.bot_names: set[str] = set()
        self.bot_conns: dict[str, object] = {}
        self.bot_level = "hard"
        self._bot_running = False
        self._bot_resume_needed = False
        self.state = "waiting"
        self.rng = random.Random()
        self.hands: dict[str, list[str]] = {}
        self.wall: list[str] = []
        self.discards: list[str] = []
        self.turn_idx = 0
        self.winner: Optional[str] = None
        self.melds: dict[str, list[tuple[str, list[str]]]] = {}
        self.last_discard: Optional[tuple[str, str]] = None
        self.claim_phase = False
        self.claim_passed: set[str] = set()

    def _is_bot(self, name: str) -> bool:
        return name in self.bot_names

    def _auto_add_bots(self) -> list[str]:
        add_n = 4 - len(self.players)
        if add_n <= 0:
            return []
        added: list[str] = []
        for _ in range(add_n):
            idx = len(self.bot_names) + 1
            bn = f"R{idx}"
            while any(n == bn for _c, n in self.players):
                idx += 1
                bn = f"R{idx}"
            bc = object()
            self.players.append((bc, bn))
            self.bot_names.add(bn)
            self.bot_conns[bn] = bc
            added.append(bn)
        return added

    def _bot_pick_discard(self, name: str) -> str:
        hand = list(self.hands.get(name, []))
        if not hand:
            return "m1"
        if self.bot_level == "easy":
            return self.rng.choice(hand)
        counts: dict[str, int] = {}
        for t in hand:
            counts[t] = counts.get(t, 0) + 1
        lonely = [t for t in hand if t[0] == "z" or counts[t] == 1]
        if lonely:
            return self.rng.choice(lonely)
        return self.rng.choice(hand)

    def _bot_claim_action(self, name: str, disc: str) -> str:
        hand = self.hands.get(name, [])
        meld_count = len(self.melds.get(name, []))
        test = sorted(hand + [disc], key=_mj_tile_sort_key)
        if _mj_is_win_with_melds(test, meld_count):
            if self.bot_level == "easy" and self.rng.random() < 0.25:
                return "pass"
            return "hu"
        if self.bot_level != "easy" and self._can_peng(name, disc):
            chance = 0.55 if self.bot_level == "pro" else 0.30
            if self.rng.random() < chance:
                return "peng"
        return "pass"

    def _bot_turn_action(self, name: str) -> str:
        hand = self.hands.get(name, [])
        meld_count = len(self.melds.get(name, []))
        if _mj_is_win_with_melds(hand, meld_count):
            return "hu"
        return f"discard {self._bot_pick_discard(name)}"

    def _claim_eligible_names(self) -> set[str]:
        if not self.claim_phase or self.last_discard is None:
            return set()
        from_name, _disc = self.last_discard
        return {n for _c, n in self.players if n != from_name}

    def _resolve_claim_if_all_passed(self) -> list[str]:
        """End claim window when everyone eligible has passed (e.g. bots done, humans already passed)."""
        if not self.claim_phase or self.last_discard is None:
            return []
        eligible = self._claim_eligible_names()
        if not eligible or self.claim_passed < eligible:
            return []
        self.claim_phase = False
        self.last_discard = None
        out = ["无人吃碰杠胡。"]
        out.extend(self._next_turn_draw())
        return out

    def _run_bots(self) -> list[str]:
        if self._bot_running:
            return []
        out: list[str] = []
        guard = 0
        self._bot_running = True
        try:
            while self.state == "playing" and guard < 80:
                guard += 1
                if self.claim_phase and self.last_discard is not None:
                    from_name, disc = self.last_discard
                    pending = [
                        n for _c, n in self.players
                        if n != from_name and n not in self.claim_passed and n in self.bot_names
                    ]
                    if not pending:
                        # In games with bots, auto-pass human players who have
                        # no valid action so they aren't stuck waiting to /game
                        # move pass when they have nothing to do.  In all-human
                        # games each player should still respond explicitly.
                        if self.bot_names:
                            human_pending = [
                                n for _c, n in self.players
                                if n != from_name
                                and n not in self.claim_passed
                                and n not in self.bot_names
                            ]
                            for hn in human_pending:
                                if not self._human_can_act(hn, disc):
                                    self.claim_passed.add(hn)
                                    out.append(f"{hn} 无可用操作，自动过。")
                        resolved = self._resolve_claim_if_all_passed()
                        if resolved:
                            out.extend(resolved)
                            continue
                        break
                    bn = pending[0]
                    bc = self.bot_conns.get(bn)
                    if bc is None:
                        break
                    _e, b, _d = self.try_move(bc, self._bot_claim_action(bn, disc))
                    out.extend(b)
                    continue
                cur = self._current_name()
                if cur is None or cur not in self.bot_names or self.claim_phase:
                    break
                bc = self.bot_conns.get(cur)
                if bc is None:
                    break
                _e, b, _d = self.try_move(bc, self._bot_turn_action(cur))
                out.extend(b)
                if self.state == "ended":
                    break
        finally:
            self._bot_running = False
            if self._bot_resume_needed and self.state == "playing":
                self._bot_resume_needed = False
                out.extend(self._run_bots())
        return out

    def nudge_bots(self) -> list[str]:
        """Advance AI after reconnect or idle claim window (safe to call from /game show)."""
        if self.state != "playing":
            return []
        return self._run_bots()

    def _finish_move(self, priv: list[str], bcast: list[str], ended: bool) -> GameResult:
        if not ended and self.state == "playing":
            if self._bot_running:
                self._bot_resume_needed = True
            else:
                extra = self._run_bots()
                if extra:
                    bcast = list(bcast) + extra
        return (priv, bcast, ended)

    def _name_of(self, conn) -> Optional[str]:
        for c, n in self.players:
            if c is conn:
                return n
        return None

    def _current_name(self) -> Optional[str]:
        if not self.players:
            return None
        return self.players[self.turn_idx % len(self.players)][1]

    def _build_wall(self) -> list[str]:
        wall: list[str] = []
        for suit in ("m", "p", "s"):
            for rank in range(1, 10):
                wall.extend([f"{suit}{rank}"] * 4)
        for rank in range(1, 8):
            wall.extend([f"z{rank}"] * 4)
        self.rng.shuffle(wall)
        return wall

    def _draw_one(self, name: str) -> Optional[str]:
        if not self.wall:
            self.state = "ended"
            return None
        tile = self.wall.pop()
        self.hands[name].append(tile)
        self.hands[name].sort(key=_mj_tile_sort_key)
        return tile

    def _start(self) -> list[str]:
        added = self._auto_add_bots()
        if len(self.players) != 4:
            return ["麻将需要 4 人开局。"]
        self.wall = self._build_wall()
        self.hands = {}
        self.melds = {}
        self.discards = []
        self.winner = None
        self.last_discard = None
        self.claim_phase = False
        self.claim_passed = set()
        for i, (_c, n) in enumerate(self.players):
            need = 14 if i == 0 else 13
            self.hands[n] = []
            self.melds[n] = []
            for _ in range(need):
                tile = self.wall.pop()
                self.hands[n].append(tile)
            self.hands[n].sort(key=_mj_tile_sort_key)
        self.turn_idx = 0
        self.state = "playing"
        cur = self._current_name()
        out = [
            "麻将开始：支持吃/碰/杠/胡；轮到你可 /game move discard <牌> 或 hu。",
            f"当前：{cur} 出牌。",
        ]
        if added:
            out.append(f"已自动补 AI：{', '.join(added)}（难度={_bot_level_zh(self.bot_level)}）")
        elif self.bot_names:
            out.append(f"AI 玩家：{', '.join(sorted(self.bot_names))}（难度={_bot_level_zh(self.bot_level)}）")
        out.extend(self._run_bots())
        return out

    def _seat_distance(self, from_name: str, to_name: str) -> int:
        names = [n for _c, n in self.players]
        i = names.index(from_name)
        j = names.index(to_name)
        return (j - i) % len(names)

    def _next_turn_draw(self) -> list[str]:
        cur = self._current_name()
        if cur is None:
            return []
        tile = self._draw_one(cur)
        if self.state == "ended":
            return ["牌墙摸完，荒牌流局。"]
        # Don't reveal the drawn tile to other players; the drawing player sees
        # it in their hand via show(). Bots' tiles are always hidden.
        return [f"{cur} 摸牌，请出牌。"]

    def _human_can_act(self, name: str, disc: str) -> bool:
        """Return True if the human player has at least one valid response to disc."""
        return (
            self._can_chi(name, disc)
            or self._can_peng(name, disc)
            or self._can_ming_gang(name, disc)
            or _mj_is_win_with_melds(
                sorted(self.hands.get(name, []) + [disc], key=_mj_tile_sort_key),
                len(self.melds.get(name, [])),
            )
        )

    def _can_peng(self, name: str, tile: str) -> bool:
        return self.hands.get(name, []).count(tile) >= 2

    def _can_ming_gang(self, name: str, tile: str) -> bool:
        return self.hands.get(name, []).count(tile) >= 3

    def _can_an_gang(self, name: str, tile: str) -> bool:
        return self.hands.get(name, []).count(tile) >= 4

    def _can_bu_gang(self, name: str, tile: str) -> bool:
        if self.hands.get(name, []).count(tile) < 1:
            return False
        for mtype, mts in self.melds.get(name, []):
            if mtype == "peng" and mts[0] == tile:
                return True
        return False

    def _can_chi(self, name: str, tile: str) -> bool:
        if tile[0] not in ("m", "p", "s"):
            return False
        if self.last_discard is None:
            return False
        from_name, _t = self.last_discard
        if self._seat_distance(from_name, name) != 1:
            return False
        hand = self.hands.get(name, [])
        r = int(tile[1])
        for a, b in ((r - 2, r - 1), (r - 1, r + 1), (r + 1, r + 2)):
            if 1 <= a <= 9 and 1 <= b <= 9:
                ta = f"{tile[0]}{a}"
                tb = f"{tile[0]}{b}"
                if ta in hand and tb in hand:
                    return True
        return False

    def _consume_chi(self, name: str, tile: str, ta: str, tb: str) -> None:
        self.hands[name].remove(ta)
        self.hands[name].remove(tb)
        self.melds[name].append(("chi", sorted([tile, ta, tb], key=_mj_tile_sort_key)))

    def _consume_peng(self, name: str, tile: str) -> None:
        self.hands[name].remove(tile)
        self.hands[name].remove(tile)
        self.melds[name].append(("peng", [tile, tile, tile]))

    def _consume_ming_gang(self, name: str, tile: str) -> None:
        for _ in range(3):
            self.hands[name].remove(tile)
        self.melds[name].append(("gang_ming", [tile, tile, tile, tile]))

    def _consume_an_gang(self, name: str, tile: str) -> None:
        for _ in range(4):
            self.hands[name].remove(tile)
        self.melds[name].append(("gang_an", [tile, tile, tile, tile]))

    def _consume_bu_gang(self, name: str, tile: str) -> bool:
        for idx, (mtype, mts) in enumerate(self.melds[name]):
            if mtype == "peng" and mts[0] == tile:
                self.melds[name][idx] = ("gang_bu", [tile, tile, tile, tile])
                self.hands[name].remove(tile)
                return True
        return False

    def try_join(self, conn, name: str) -> GameResult:
        if self.state != "waiting":
            return (["对局已开始，无法加入。"], [], False)
        if any(n == name for _c, n in self.players):
            return (["该昵称已被占用。"], [], False)
        if len(self.players) >= 4:
            return (["麻将满员（4 人）。"], [], False)
        self.players.append((conn, name))
        return ([], [f"{name} 加入了麻将（{len(self.players)}/4）"], False)

    def try_move(self, conn, raw: str) -> GameResult:
        name = self._name_of(conn)
        if name is None:
            return (["你不在本局中。"], [], False)
        parts = raw.strip().split()
        if not parts:
            return (["用法：/game move start | discard <牌> | chi <x> <y> | peng | gang [牌] | hu"], [], False)
        cmd = parts[0].lower()
        cmd = {
            "开始": "start",
            "出牌": "discard",
            "过": "pass",
            "胡": "hu",
            "碰": "peng",
            "杠": "gang",
            "吃": "chi",
            "机器人": "bot",
        }.get(parts[0], cmd)
        if cmd == "bot":
            if conn is not self.players[0][0]:
                return (["只有房主可以设置机器人难度。"], [], False)
            if len(parts) < 2 or parts[1].lower() not in ("easy", "hard", "pro"):
                return (["用法：/game move bot <easy|hard|pro>"], [], False)
            self.bot_level = parts[1].lower()
            return ([], [f"机器人难度已设为：{_bot_level_zh(self.bot_level)}"], False)
        if cmd == "start":
            if conn is not self.players[0][0]:
                return (["只有房主可以开始。"], [], False)
            if self.state == "playing":
                return (["对局已经开始。"], [], False)
            return ([], self._start(), False)
        if self.state == "waiting":
            return (["对局尚未开始。"], [], False)
        if self.state == "ended":
            return (["对局已结束，请房主重新 /game new mahjong 开新局。"], [], False)
        cur = self._current_name()
        hand = self.hands.get(name, [])
        meld_count = len(self.melds.get(name, []))

        if self.claim_phase:
            if self.last_discard is None:
                self.claim_phase = False
                return (["内部状态错误，请继续。"], [], False)
            from_name, disc = self.last_discard
            if name == from_name:
                return (["出牌者不能在本轮响应自己的弃牌。"], [], False)
            if cmd == "pass":
                self.claim_passed.add(name)
                eligible = {n for _c, n in self.players if n != from_name}
                if self.claim_passed >= eligible:
                    self.claim_phase = False
                    self.last_discard = None
                    out = ["无人吃碰杠胡。"]
                    out.extend(self._next_turn_draw())
                    return self._finish_move([], out, self.state == "ended")
                return self._finish_move([], [f"{name} 选择过"], False)
            if cmd == "hu":
                test = sorted(hand + [disc], key=_mj_tile_sort_key)
                if _mj_is_win_with_melds(test, meld_count):
                    self.state = "ended"
                    self.winner = name
                    return ([], [f"{name} 点炮胡 {from_name} 的 {disc}，本局结束。"], True)
                return (["你当前不能点炮胡。"], [], False)
            if cmd == "peng":
                if not self._can_peng(name, disc):
                    return (["你当前不能碰这张牌。"], [], False)
                self._consume_peng(name, disc)
                self.turn_idx = [n for _c, n in self.players].index(name)
                self.claim_phase = False
                self.last_discard = None
                self.claim_passed = set()
                return self._finish_move([], [f"{name} 碰了 {disc}，请出牌。"], False)
            if cmd == "gang":
                if not self._can_ming_gang(name, disc):
                    return (["你当前不能明杠这张牌。"], [], False)
                self._consume_ming_gang(name, disc)
                self.turn_idx = [n for _c, n in self.players].index(name)
                self.claim_phase = False
                self.last_discard = None
                self.claim_passed = set()
                out = [f"{name} 明杠 {disc}"]
                out.extend(self._next_turn_draw())
                return self._finish_move([], out, self.state == "ended")
            if cmd == "chi":
                if len(parts) < 3:
                    return (["用法：/game move chi <牌1> <牌2>"], [], False)
                if not self._can_chi(name, disc):
                    return (["你当前不能吃这张牌。"], [], False)
                t1 = _mj_normalize_tile(parts[1])
                t2 = _mj_normalize_tile(parts[2])
                if t1 is None or t2 is None:
                    return (["吃牌参数格式错误。"], [], False)
                seq = sorted([disc, t1, t2], key=_mj_tile_sort_key)
                if seq[0][0] != seq[1][0] or seq[1][0] != seq[2][0]:
                    return (["吃牌必须同花色顺子。"], [], False)
                rs = [int(x[1]) for x in seq]
                if rs[0] + 1 != rs[1] or rs[1] + 1 != rs[2]:
                    return (["吃牌必须是连续三张。"], [], False)
                if t1 not in hand or t2 not in hand:
                    return (["你手里没有这两张吃牌。"], [], False)
                self._consume_chi(name, disc, t1, t2)
                self.turn_idx = [n for _c, n in self.players].index(name)
                self.claim_phase = False
                self.last_discard = None
                self.claim_passed = set()
                return self._finish_move([], [f"{name} 吃了 {disc}，请出牌。"], False)
            return (["当前是吃碰杠胡阶段，可用：chi/peng/gang/hu/pass"], [], False)

        if cur != name:
            return ([f"当前轮到 {cur}，请等待。"], [], False)

        if cmd == "hu":
            if _mj_is_win_with_melds(hand, meld_count):
                self.state = "ended"
                self.winner = name
                return ([], [f"{name} 自摸胡牌！本局结束。"], True)
            return (["当前牌型不能胡。"], [], False)

        if cmd == "gang":
            gang_tile = _mj_normalize_tile(parts[1]) if len(parts) > 1 else None
            cand = gang_tile
            if cand is None and len(parts) > 1:
                return (["杠牌参数错误。"], [], False)
            if cand and self._can_an_gang(name, cand):
                self._consume_an_gang(name, cand)
                out = [f"{name} 暗杠 {cand}"]
                out.extend(self._next_turn_draw())
                return self._finish_move([], out, self.state == "ended")
            if cand and self._can_bu_gang(name, cand):
                if not self._consume_bu_gang(name, cand):
                    return (["补杠失败，请重试。"], [], False)
                out = [f"{name} 补杠 {cand}"]
                out.extend(self._next_turn_draw())
                return self._finish_move([], out, self.state == "ended")
            if cand is None:
                for t in sorted(set(hand), key=_mj_tile_sort_key):
                    if self._can_an_gang(name, t):
                        self._consume_an_gang(name, t)
                        out = [f"{name} 暗杠 {t}"]
                        out.extend(self._next_turn_draw())
                        return self._finish_move([], out, self.state == "ended")
                    if self._can_bu_gang(name, t):
                        self._consume_bu_gang(name, t)
                        out = [f"{name} 补杠 {t}"]
                        out.extend(self._next_turn_draw())
                        return self._finish_move([], out, self.state == "ended")
            return (["当前没有可执行的暗杠/补杠。"], [], False)

        if cmd != "discard" or len(parts) < 2:
            return (["用法：/game move discard <牌>（支持 二万/九筒/东风/红中 等）"], [], False)
        tile = _mj_normalize_tile(parts[1])
        if tile is None:
            return (["牌面格式错误：可用 m1~m9/p1~p9/s1~s9/z1~z7，或中文牌名"], [], False)
        if tile not in hand:
            return ([f"你手里没有 {tile}"], [], False)
        hand.remove(tile)
        self.discards.append(tile)
        self.last_discard = (name, tile)
        self.claim_phase = True
        self.claim_passed = set()
        self.turn_idx = (self.turn_idx + 1) % len(self.players)
        next_name = self._current_name()
        return self._finish_move(
            [],
            [
                f"{name} 打出 {tile}（可写中文如 二万/东风）",
                f"其余玩家可吃碰杠胡：/game move chi|peng|gang|hu|pass；若无人操作，轮到 {next_name} 摸牌。",
            ],
            False,
        )

    def resign(self, conn, name: str) -> GameResult:
        if self.state != "playing":
            return (["当前没有进行中的麻将对局。"], [], False)
        self.state = "ended"
        return ([], [f"{name} 认输，麻将对局结束。"], True)

    def abort(self, conn, name: str) -> GameResult:
        if conn is not self.players[0][0]:
            return (["只有房主可以终止对局。"], [], False)
        self.state = "ended"
        return ([], [f"{name} 终止了麻将对局"], True)

    def seats(self) -> list[str]:
        lines = [f"麻将 状态：{_game_state_zh(self.state)}"]
        seat_labels = ["东", "南", "西", "北"]
        eligible = self._claim_eligible_names() if self.claim_phase else set()
        for i, (_c, n) in enumerate(self.players):
            marker = ""
            if self.state == "playing":
                if self.claim_phase and n in eligible and n not in self.claim_passed:
                    marker = " <- 待响应"
                elif not self.claim_phase and i == self.turn_idx:
                    marker = " <- 当前"
            bot_tag = " [AI]" if n in self.bot_names else ""
            lines.append(f"{seat_labels[i]}家：{n}{bot_tag}{marker}")
        lines.append(f"牌墙剩余：{len(self.wall)}")
        if self.claim_phase and self.last_discard is not None:
            lines.append(f"待响应弃牌：{self.last_discard[0]} -> {self.last_discard[1]}")
            waiting = sorted(eligible - self.claim_passed)
            if waiting:
                lines.append(f"尚未响应：{', '.join(waiting)}")
        if self.discards:
            lines.append(f"最近弃牌：{' '.join(self.discards[-8:])}")
        return lines

    def show(self, conn=None, full: bool = False) -> list[str]:
        lines = self.seats()
        me = self._name_of(conn) if conn is not None else None
        if me and me in self.hands:
            hand = " ".join(sorted(self.hands[me], key=_mj_tile_sort_key))
            lines.append(f"你的手牌：{hand}")
            meld_txt = []
            for mtype, mts in self.melds.get(me, []):
                label = {"chi": "吃", "peng": "碰", "gang_ming": "明杠", "gang_an": "暗杠", "gang_bu": "补杠"}.get(mtype, mtype)
                meld_txt.append(f"{label}({''.join(mts)})")
            if meld_txt:
                lines.append(f"你的副露：{' '.join(meld_txt)}")
            if self.state == "playing" and self.claim_phase and self.last_discard is not None:
                from_name, disc = self.last_discard
                if me == from_name:
                    lines.append(f"你已打出 {disc}，等待他人吃碰杠胡或过。")
                elif me not in self.claim_passed:
                    lines.append("当前可用：/game move chi <牌1> <牌2> | peng | gang | hu | pass")
                else:
                    waiting = sorted(self._claim_eligible_names() - self.claim_passed)
                    lines.append(
                        f"你已选择过，等待：{', '.join(waiting) if waiting else '结算'}"
                    )
            elif self.state == "playing" and self._current_name() == me:
                lines.append("你可用：/game move discard <牌> | gang [牌] | hu")
            else:
                cur = self._current_name()
                if self.claim_phase:
                    lines.append("当前为响应弃牌阶段，请等待他人或过牌。")
                else:
                    lines.append(f"当前轮到：{cur}")
        return lines

    def on_player_leave(self, conn, name: str) -> GameResult:
        idx = None
        for i, (c, _n) in enumerate(self.players):
            if c is conn:
                idx = i
                break
        if idx is None:
            return ([], [], False)
        _c, pname = self.players.pop(idx)
        self.hands.pop(pname, None)
        if not self.players:
            self.state = "ended"
            return ([], [f"{name} 离开，麻将对局已结束"], True)
        self.state = "ended"
        return ([], [f"{name} 离开，人数不足，麻将对局结束"], True)



DOUSHOU_ROWS = 9
DOUSHOU_COLS = 7
DOUSHOU_RIVER = {(r, c) for r in range(3, 6) for c in (1, 2, 4, 5)}
DOUSHOU_DENS = {"black": (0, 3), "red": (8, 3)}
DOUSHOU_TRAPS = {
    "black": {(0, 2), (0, 4), (1, 3)},
    "red": {(8, 2), (8, 4), (7, 3)},
}
DOUSHOU_RANKS = {
    "rat": 1,
    "cat": 2,
    "dog": 3,
    "wolf": 4,
    "leopard": 5,
    "tiger": 6,
    "lion": 7,
    "elephant": 8,
}
DOUSHOU_CN = {
    "rat": "鼠",
    "cat": "猫",
    "dog": "狗",
    "wolf": "狼",
    "leopard": "豹",
    "tiger": "虎",
    "lion": "狮",
    "elephant": "象",
}
DOUSHOU_ALIASES = {
    "鼠": "rat", "老鼠": "rat", "rat": "rat", "r": "rat",
    "猫": "cat", "cat": "cat", "c": "cat",
    "狗": "dog", "犬": "dog", "dog": "dog", "d": "dog",
    "狼": "wolf", "wolf": "wolf", "w": "wolf",
    "豹": "leopard", "leopard": "leopard", "p": "leopard",
    "虎": "tiger", "tiger": "tiger", "t": "tiger",
    "狮": "lion", "lion": "lion", "l": "lion",
    "象": "elephant", "elephant": "elephant", "e": "elephant",
}
DOUSHOU_INITIAL = [
    ("black", "lion", 0, 0),
    ("black", "tiger", 0, 6),
    ("black", "dog", 1, 1),
    ("black", "cat", 1, 5),
    ("black", "rat", 2, 0),
    ("black", "leopard", 2, 2),
    ("black", "wolf", 2, 4),
    ("black", "elephant", 2, 6),
    ("red", "elephant", 6, 0),
    ("red", "wolf", 6, 2),
    ("red", "leopard", 6, 4),
    ("red", "rat", 6, 6),
    ("red", "cat", 7, 1),
    ("red", "dog", 7, 5),
    ("red", "tiger", 8, 0),
    ("red", "lion", 8, 6),
]


def _doushou_opponent(side: str) -> str:
    return "black" if side == "red" else "red"


def _doushou_side_zh(side: str) -> str:
    return "红方" if side == "red" else "黑方"


def _doushou_parse_coord(a: str, b: str | None = None) -> Optional[tuple[int, int]]:
    raw = a.strip()
    if b is None:
        m = re.match(r"^\s*(\d+)\s*[,，:]\s*(\d+)\s*$", raw)
        if not m:
            return None
        row, col = int(m.group(1)), int(m.group(2))
    else:
        if not a.strip().isdigit() or not b.strip().isdigit():
            return None
        row, col = int(a), int(b)
    if 1 <= row <= DOUSHOU_ROWS and 1 <= col <= DOUSHOU_COLS:
        return row - 1, col - 1
    return None


def _doushou_parse_move(raw: str) -> Optional[tuple[tuple[int, int] | None, tuple[int, int]]]:
    parts = raw.replace("，", ",").split()
    if len(parts) == 1:
        # a compact target such as 4,3 is allowed only when selected by piece name is absent.
        target = _doushou_parse_coord(parts[0])
        if target:
            return None, target
    if len(parts) == 2:
        target = _doushou_parse_coord(parts[0], parts[1])
        if target:
            return None, target
        piece = DOUSHOU_ALIASES.get(parts[0].lower()) or DOUSHOU_ALIASES.get(parts[0])
        target = _doushou_parse_coord(parts[1])
        if piece and target:
            return None, target
    if len(parts) == 3:
        piece = DOUSHOU_ALIASES.get(parts[0].lower()) or DOUSHOU_ALIASES.get(parts[0])
        target = _doushou_parse_coord(parts[1], parts[2])
        if piece and target:
            return None, target
    if len(parts) == 4:
        src = _doushou_parse_coord(parts[0], parts[1])
        dst = _doushou_parse_coord(parts[2], parts[3])
        if src and dst:
            return src, dst
    if len(parts) == 2 and re.match(r"^\d+[,，]\d+$", parts[0]) and re.match(r"^\d+[,，]\d+$", parts[1]):
        src = _doushou_parse_coord(parts[0])
        dst = _doushou_parse_coord(parts[1])
        if src and dst:
            return src, dst
    return None


def _doushou_piece_token(piece: Optional[dict[str, str]], last: bool = False) -> str:
    if piece is None:
        return "!" if last else "·"
    prefix = "+" if piece["side"] == "red" else "-"
    return prefix + DOUSHOU_CN[piece["kind"]]


def _doushou_terrain(row: int, col: int) -> str:
    pos = (row, col)
    if pos == DOUSHOU_DENS["black"]:
        return "黑穴"
    if pos == DOUSHOU_DENS["red"]:
        return "红穴"
    if pos in DOUSHOU_TRAPS["black"]:
        return "黑陷"
    if pos in DOUSHOU_TRAPS["red"]:
        return "红陷"
    if pos in DOUSHOU_RIVER:
        return "河"
    return ""


class DoushouGame(BoardUndoMixin):
    """斗兽棋：7x9，红方先手，无机器人。"""

    name = "doushou"
    first_seat_desc = "红方（先手）"
    second_seat_desc = "黑方"
    send_view_on_move = True

    def __init__(self, red_conn, red_name: str, *, rating_store: Optional[GameRatingStore] = None) -> None:
        self.red_conn = red_conn
        self.red_name = red_name
        self.black_conn = None
        self.black_name: Optional[str] = None
        self.rating_store = rating_store
        self.state = "waiting"
        self._turn = "red"
        self._last: Optional[tuple[int, int]] = None
        self._history: list[tuple[tuple[int, int], tuple[int, int], dict[str, str], Optional[dict[str, str]], str, Optional[tuple[int, int]]]] = []
        self.board: list[list[Optional[dict[str, str]]]] = [[None for _ in range(DOUSHOU_COLS)] for _ in range(DOUSHOU_ROWS)]
        self._reset_board()
        self.join_blurb = "等另一位玩家用 /game join 加入；斗兽棋无机器人。"
        self._undo_clear_pending()

    def _reset_board(self) -> None:
        self.board = [[None for _ in range(DOUSHOU_COLS)] for _ in range(DOUSHOU_ROWS)]
        for side, kind, row, col in DOUSHOU_INITIAL:
            self.board[row][col] = {"side": side, "kind": kind}
        self._turn = "red"
        self._last = None
        self._history.clear()

    def _seat_conn(self, side: str):
        return self.red_conn if side == "red" else self.black_conn

    def who_of(self, conn) -> Optional[str]:
        if conn is self.red_conn:
            return "red"
        if conn is self.black_conn:
            return "black"
        return None

    def is_seated(self, conn) -> bool:
        return self.who_of(conn) is not None

    def _name_of_side(self, side: str) -> str:
        return self.red_name if side == "red" else (self.black_name or "黑方")

    def _undo_has_moves(self) -> bool:
        return bool(self._history)

    def _undo_last_mover_conn(self):
        if not self._history:
            return None
        return self._seat_conn(self._history[-1][2]["side"])

    def _undo_opponent_conn(self, conn):
        side = self.who_of(conn)
        if side is None:
            return None
        return self._seat_conn(_doushou_opponent(side))

    def _undo_player_name(self, conn) -> str:
        side = self.who_of(conn)
        if side == "red":
            return self.red_name
        if side == "black":
            return self.black_name or "黑方"
        return "?"

    def _undo_pop_last_move(self) -> bool:
        if not self._history:
            return False
        src, dst, piece, captured, prev_turn, prev_last = self._history.pop()
        sr, sc = src
        dr, dc = dst
        self.board[sr][sc] = piece
        self.board[dr][dc] = captured
        self._turn = prev_turn
        self._last = prev_last
        return True

    def _undo_turn_line(self) -> str:
        return f"轮到 {_doushou_side_zh(self._turn)} {self._name_of_side(self._turn)} 行棋"

    def _rating_lines(self) -> list[str]:
        return _format_rating_lines(self.rating_store, self.name, [self.red_name, self.black_name])

    def _settle_ratings(self, score_red: float) -> list[str]:
        if not self.black_name:
            return []
        return _format_rating_result_lines(self.rating_store, self.name, self.red_name, self.black_name, score_red, ranked=True)

    def try_join(self, conn, name: str) -> GameResult:
        if self.state == "ended":
            return (["对局已结束，请先 /game new doushou 开新局。"], [], False)
        if conn is self.red_conn:
            return (["你已经是红方。"], [], False)
        if self.black_conn is not None:
            return ([f"黑方席位已被 {self.black_name} 占。"], [], False)
        self.black_conn = conn
        self.black_name = name
        self.state = "playing"
        return ([], [
            f"{name} 加入为黑方，斗兽棋开始！",
            f"红方（先手）：{self.red_name}    黑方：{self.black_name}",
            "走法：先点己方棋子再点目标格；也可 /game move <起行> <起列> <终行> <终列>。",
            "规则：大吃小，同级互吃；鼠吃象、象不能吃鼠；狮虎可跳河但河中有鼠会被挡；进对方兽穴获胜。",
            self._undo_turn_line(),
        ], False)

    def _piece_can_capture(self, attacker: dict[str, str], target: dict[str, str], dst: tuple[int, int]) -> bool:
        if attacker["side"] == target["side"]:
            return False
        ar = DOUSHOU_RANKS[attacker["kind"]]
        tr = DOUSHOU_RANKS[target["kind"]]
        if dst in DOUSHOU_TRAPS[attacker["side"]]:
            tr = 0
        if attacker["kind"] == "rat" and target["kind"] == "elephant":
            return True
        if attacker["kind"] == "elephant" and target["kind"] == "rat":
            return False
        # 河里的鼠不能无风险偷吃岸上的象。
        if attacker["kind"] == "rat" and target["kind"] == "elephant" and dst not in DOUSHOU_RIVER:
            return True
        return ar >= tr

    def _jump_target(self, src: tuple[int, int], dr: int, dc: int) -> Optional[tuple[int, int]]:
        r, c = src[0] + dr, src[1] + dc
        if not (0 <= r < DOUSHOU_ROWS and 0 <= c < DOUSHOU_COLS) or (r, c) not in DOUSHOU_RIVER:
            return None
        path = []
        while 0 <= r < DOUSHOU_ROWS and 0 <= c < DOUSHOU_COLS and (r, c) in DOUSHOU_RIVER:
            path.append((r, c))
            r += dr
            c += dc
        if not (0 <= r < DOUSHOU_ROWS and 0 <= c < DOUSHOU_COLS):
            return None
        for pr, pc in path:
            p = self.board[pr][pc]
            if p is not None and p["kind"] == "rat":
                return None
        return r, c

    def _legal_move_reason(self, side: str, src: tuple[int, int], dst: tuple[int, int]) -> tuple[bool, str]:
        sr, sc = src
        dr, dc = dst
        if not (0 <= sr < DOUSHOU_ROWS and 0 <= sc < DOUSHOU_COLS and 0 <= dr < DOUSHOU_ROWS and 0 <= dc < DOUSHOU_COLS):
            return False, "坐标超出棋盘。"
        piece = self.board[sr][sc]
        if piece is None:
            return False, "起点没有棋子。"
        if piece["side"] != side:
            return False, "只能移动自己的棋子。"
        if dst == DOUSHOU_DENS[side]:
            return False, "不能进入自己的兽穴。"
        target = self.board[dr][dc]
        if target is not None and target["side"] == side:
            return False, "目标格已有己方棋子。"
        manhattan = abs(dr - sr) + abs(dc - sc)
        is_jump = False
        if manhattan != 1:
            if piece["kind"] not in {"lion", "tiger"}:
                return False, "普通动物每步只能上下左右走一格。"
            if sr != dr and sc != dc:
                return False, "狮虎只能横向或纵向跳河。"
            step_r = 0 if sr == dr else (1 if dr > sr else -1)
            step_c = 0 if sc == dc else (1 if dc > sc else -1)
            jump = self._jump_target(src, step_r, step_c)
            if jump != dst:
                return False, "狮虎只有隔河直跳，且河中不能有鼠阻挡。"
            is_jump = True
        if dst in DOUSHOU_RIVER and piece["kind"] != "rat":
            return False, "只有鼠可以进入河流。"
        if is_jump and dst in DOUSHOU_RIVER:
            return False, "狮虎跳河必须落到岸上。"
        if target is not None and not self._piece_can_capture(piece, target, dst):
            return False, f"{DOUSHOU_CN[piece['kind']]}不能吃{DOUSHOU_CN[target['kind']]}。"
        return True, ""

    def _find_unique_piece_move(self, side: str, kind: str, dst: tuple[int, int]) -> tuple[Optional[tuple[int, int]], str]:
        matches = []
        for r in range(DOUSHOU_ROWS):
            for c in range(DOUSHOU_COLS):
                p = self.board[r][c]
                if p and p["side"] == side and p["kind"] == kind:
                    ok, _ = self._legal_move_reason(side, (r, c), dst)
                    if ok:
                        matches.append((r, c))
        if len(matches) == 1:
            return matches[0], ""
        if not matches:
            return None, f"没有可移动到 ({dst[0] + 1},{dst[1] + 1}) 的{DOUSHOU_CN[kind]}。"
        return None, "有多个同类棋子可到达，请改用起点+终点坐标。"

    def try_move(self, conn, raw: str) -> GameResult:
        if self.state == "waiting":
            return (["对局尚未开始，等黑方 /game join。"], [], False)
        if self.state != "playing":
            return (["对局已结束。"], [], False)
        side = self.who_of(conn)
        if side is None:
            return (["你不是对局双方。"], [], False)
        if side != self._turn:
            return (["不是你的回合。"], [], False)
        self._undo_clear_pending()

        parts = raw.replace("，", ",").split()
        parsed = _doushou_parse_move(raw)
        src: Optional[tuple[int, int]] = None
        dst: Optional[tuple[int, int]] = None
        if parsed and parsed[0] is not None:
            src, dst = parsed
        elif len(parts) in {2, 3}:
            piece_kind = DOUSHOU_ALIASES.get(parts[0].lower()) or DOUSHOU_ALIASES.get(parts[0])
            target = _doushou_parse_coord(parts[1], parts[2]) if len(parts) == 3 else _doushou_parse_coord(parts[1])
            if piece_kind and target:
                src, msg = self._find_unique_piece_move(side, piece_kind, target)
                if src is None:
                    return ([msg], [], False)
                dst = target
        if src is None or dst is None:
            return (["用法：/game move <起行> <起列> <终行> <终列>；例：7 7 6 7。也可先点棋子再点目标格。"], [], False)

        ok, reason = self._legal_move_reason(side, src, dst)
        if not ok:
            return ([reason], [], False)
        sr, sc = src
        dr, dc = dst
        piece = self.board[sr][sc]
        assert piece is not None
        captured = self.board[dr][dc]
        prev_turn = self._turn
        prev_last = self._last
        self._history.append((src, dst, dict(piece), dict(captured) if captured else None, prev_turn, prev_last))
        self.board[dr][dc] = piece
        self.board[sr][sc] = None
        self._last = dst

        mover = self._name_of_side(side)
        action = f"{_doushou_side_zh(side)} {mover} 走 {DOUSHOU_CN[piece['kind']]}：({sr + 1},{sc + 1}) -> ({dr + 1},{dc + 1})"
        if captured:
            action += f"，吃掉{_doushou_side_zh(captured['side'])}{DOUSHOU_CN[captured['kind']]}"
        bcast = [action]

        if dst == DOUSHOU_DENS[_doushou_opponent(side)]:
            self.state = "ended"
            bcast.append(f"对局结束：{_doushou_side_zh(side)} {mover} 攻入对方兽穴获胜！")
            bcast.extend(self._settle_ratings(1.0 if side == "red" else 0.0))
            return ([], bcast, True)

        opponent = _doushou_opponent(side)
        if not any(p and p["side"] == opponent for row in self.board for p in row):
            self.state = "ended"
            bcast.append(f"对局结束：{_doushou_side_zh(side)} {mover} 吃光对方棋子获胜！")
            bcast.extend(self._settle_ratings(1.0 if side == "red" else 0.0))
            return ([], bcast, True)

        self._turn = opponent
        bcast.append(self._undo_turn_line())
        return ([], bcast, False)

    def resign(self, conn, name: str) -> GameResult:
        if self.state != "playing":
            return (["对局尚未开始或已结束，无需认负。"], [], False)
        side = self.who_of(conn)
        if side is None:
            return (["你不是对局双方。"], [], False)
        self.state = "ended"
        red_score = 0.0 if side == "red" else 1.0
        winner = _doushou_opponent(side)
        return ([], [f"{_doushou_side_zh(side)} {name} 认负 — {_doushou_side_zh(winner)}胜", *self._settle_ratings(red_score)], True)

    def abort(self, conn, name: str) -> GameResult:
        if self.state == "ended":
            return (["对局已结束。"], [], False)
        if self.who_of(conn) is None:
            return (["你不是对局双方，无法终止。"], [], False)
        if self.state == "playing":
            return (["已开始的对局请用 /game resign 认负，不能 /game abort。"], [], False)
        self.state = "ended"
        return ([], [f"{name} 终止了斗兽棋对局（未开始）。"], True)

    def seats(self) -> list[str]:
        lines = [
            f"doushou 对局状态：{self.state}",
            f"  红方（先手）：{self.red_name}",
            f"  黑方：{self.black_name or '(空席, 可 /game join)'}",
        ]
        lines.extend(self._rating_lines())
        return lines

    def _board_render(self) -> list[str]:
        lines = ["斗兽棋棋盘（7列×9行，+红 -黑，!上一步）"]
        lines.append("    1    2    3    4    5    6    7")
        for r in range(DOUSHOU_ROWS):
            cells = []
            for c in range(DOUSHOU_COLS):
                piece = self.board[r][c]
                token = _doushou_piece_token(piece, self._last == (r, c))
                terrain = _doushou_terrain(r, c)
                if piece is None and terrain:
                    token = terrain
                cells.append(f"{token:^4}")
            lines.append(f"{r + 1:>2} " + "".join(cells))
        lines.append("图例：红穴/黑穴=兽穴；红陷/黑陷=陷阱；河=河流。坐标为 行 列，左上为 1,1。")
        if self._last is not None:
            lines.append(f"上一步：({self._last[0] + 1}, {self._last[1] + 1})")
        return lines

    def show(self, conn=None) -> list[str]:
        lines = [f"doushou 对局（{self.state}）  红：{self.red_name}   黑：{self.black_name or '空席'}"]
        lines.extend(self._rating_lines())
        lines.extend(self._board_render())
        if self.state == "playing":
            lines.append(self._undo_turn_line())
        elif self.state == "waiting":
            lines.append("等待黑方加入：/game join")
        return lines

    def on_player_leave(self, conn, name: str) -> GameResult:
        side = self.who_of(conn)
        if side is None:
            return ([], [], False)
        if conn is self.red_conn:
            self.red_conn = None
        if conn is self.black_conn:
            self.black_conn = None
        if self.state == "waiting":
            self.state = "ended"
            return ([], [f"{name} 离开，斗兽棋对局取消。"], True)
        if self.state == "playing":
            self.state = "ended"
            winner = _doushou_opponent(side)
            red_score = 1.0 if winner == "red" else 0.0
            return ([], [f"{_doushou_side_zh(side)} {name} 离开 — {_doushou_side_zh(winner)}胜", *self._settle_ratings(red_score)], True)
        return ([], [], False)

def create_game(
    game_name: str,
    creator_conn,
    creator_name: str,
    *,
    options: Optional[list[str]] = None,
    rating_store: Optional[GameRatingStore] = None,
):
    options = options or []
    ai_level = _parse_ai_level(options) if game_name in {"chess", "gomoku", "xiangqi"} else None
    if options and game_name not in {"chess", "gomoku", "xiangqi"}:
        raise RuntimeError(f"{game_name} 暂不支持额外开局参数。")
    if game_name == ChessGame.name:
        game = ChessGame(
            creator_conn,
            creator_name,
            rating_store=rating_store,
            ai_level=ai_level,
        )
    elif game_name == GomokuGame.name:
        game = GomokuGame(
            creator_conn,
            creator_name,
            rating_store=rating_store,
            ai_level=ai_level,
        )
    elif game_name == GoGame.name:
        if options:
            raise RuntimeError("go 暂不支持 AI 或额外开局参数。")
        game = GoGame(
            creator_conn,
            creator_name,
            rating_store=rating_store,
        )
    elif game_name == ReversiGame.name:
        if options:
            raise RuntimeError("reversi does not support opening options.")
        game = ReversiGame(
            creator_conn,
            creator_name,
            rating_store=rating_store,
        )
    elif game_name == DarkchessGame.name:
        if options:
            raise RuntimeError("darkchess does not support opening options.")
        game = DarkchessGame(
            creator_conn,
            creator_name,
            rating_store=rating_store,
        )
    elif game_name == BattleshipGame.name:
        if options:
            raise RuntimeError("battleship does not support opening options.")
        game = BattleshipGame(
            creator_conn,
            creator_name,
            rating_store=rating_store,
        )
    elif game_name == JunqiGame.name:
        if options:
            raise RuntimeError("junqi does not support opening options.")
        game = JunqiGame(
            creator_conn,
            creator_name,
            rating_store=rating_store,
        )
    elif game_name == XiangqiGame.name:
        game = XiangqiGame(
            creator_conn,
            creator_name,
            rating_store=rating_store,
            ai_level=ai_level,
        )
    elif game_name == DoushouGame.name:
        if options:
            raise RuntimeError("doushou 暂不支持 AI 或额外开局参数。")
        game = DoushouGame(
            creator_conn,
            creator_name,
            rating_store=rating_store,
        )
    else:
        cls = GAMES.get(game_name)
        if cls is None:
            raise RuntimeError(f"未知游戏：{game_name}")
        game = cls(creator_conn, creator_name)
    stamp_new_session(game)
    return game


GAMES = {
    ChessGame.name: ChessGame,
    GomokuGame.name: GomokuGame,
    GoGame.name: GoGame,
    ReversiGame.name: ReversiGame,
    DarkchessGame.name: DarkchessGame,
    BattleshipGame.name: BattleshipGame,
    JunqiGame.name: JunqiGame,
    XiangqiGame.name: XiangqiGame,
    DoushouGame.name: DoushouGame,
    SanguoshaGame.name: SanguoshaGame,
    WerewolfGame.name: WerewolfGame,
    HoldemGame.name: HoldemGame,
    ZhaJinHuaGame.name: ZhaJinHuaGame,
    NiuTouWangGame.name: NiuTouWangGame,
    MahjongGame.name: MahjongGame,
}
GAME_ALIASES = {
    "cchess": XiangqiGame.name,
    "weiqi": GoGame.name,
    "baduk": GoGame.name,
    "围棋": GoGame.name,
    "黑白棋": ReversiGame.name,
    "othello": ReversiGame.name,
    "reversi": ReversiGame.name,
    "othello": ReversiGame.name,
    "dark-chess": DarkchessGame.name,
    "flipchess": DarkchessGame.name,
    "暗棋": DarkchessGame.name,
    "翻翻棋": DarkchessGame.name,
    "battleship": BattleshipGame.name,
    "战舰": BattleshipGame.name,
    "海战棋": BattleshipGame.name,
    "junqi": JunqiGame.name,
    "army": JunqiGame.name,
    "landbattle": JunqiGame.name,
    "军棋": JunqiGame.name,
    "sgs": SanguoshaGame.name,
    "langrensha": WerewolfGame.name,
    "were-wolf": WerewolfGame.name,
    "poker": HoldemGame.name,
    "texas": HoldemGame.name,
    "texasholdem": HoldemGame.name,
    "holdem": HoldemGame.name,
    "dezhou": HoldemGame.name,
    "德州": HoldemGame.name,
    "德州扑克": HoldemGame.name,
    "zhajinhua": ZhaJinHuaGame.name,
    "zjh": ZhaJinHuaGame.name,
    "炸金花": ZhaJinHuaGame.name,
    "niutou": NiuTouWangGame.name,
    "niutouwang": NiuTouWangGame.name,
    "ntw": NiuTouWangGame.name,
    "牛头王": NiuTouWangGame.name,
    "mj": MahjongGame.name,
    "majiang": MahjongGame.name,
    "mahjong": MahjongGame.name,
    "麻将": MahjongGame.name,
    "三国杀": SanguoshaGame.name,
    "jungle": DoushouGame.name,
    "junglechess": DoushouGame.name,
    "doushouqi": DoushouGame.name,
    "斗兽棋": DoushouGame.name,
    "斗兽": DoushouGame.name,
}


def resolve_game_name(name: str) -> str:
    """Map alias (e.g. cchess) to canonical game id."""
    key = name.lower()
    return GAME_ALIASES.get(key, key)


def all_game_names() -> list[str]:
    """All registered game ids (for owner catalog / defaults)."""
    return sorted(GAMES)


def terminal_hint(name: str) -> str:
    """Give terminal players the first legal command after a game starts."""
    hints = {
        "reversi": "Terminal: /game move <row> <col>; use /game move pass only when no legal move exists.",
        "darkchess": "Terminal: /game move flip <row> <col>, then /game move move <fr> <fc> <tr> <tc>.",
        "battleship": "Terminal: place all five ships with /game move place <ship> <row> <col> <h|v>, then ready and fire <row> <col>.",
        "junqi": "Terminal: place pieces with /game move setup <piece> <row> <col>, then ready and move <fr> <fc> <tr> <tc>.",
    }
    return hints.get(name, "")


def game_rule_notice(name: str) -> list[str]:
    """Short rules shown whenever one of the newer board games is opened."""
    notices = {
        "reversi": "游戏须知：黑白棋轮流落子，必须夹住并翻转对方棋子；无合法位置时停一手，双方连续停手结束。",
        "darkchess": "游戏须知：暗棋按 将 > 士 > 象 > 车 > 马 > 卒；炮隔一子吃子，翻子决定阵营，轮到你时再翻或走。",
        "battleship": "游戏须知：海战棋先布置五艘舰船且舰船不可重叠或相邻；双方准备后轮流开火，击沉全部舰船获胜。",
        "junqi": "游戏须知：军棋先布阵再轮流行棋；军旗、地雷不能移动，炸弹同归于尽，工兵可排雷，吃掉军旗获胜。",
    }
    notice = notices.get(name)
    return [notice] if notice else []


def list_game_names(enabled: Optional[set[str]] = None) -> list[str]:
    """Canonical game ids for /game list; optional room filter (online only)."""
    if enabled is None:
        return all_game_names()
    return sorted(n for n in GAMES if n in enabled)


HELP_LINES = (
    # Canonical Chinese copy kept for imports/tests; runtime /game help uses
    # i18n.game_help_lines(locale) from locales/{en,zh}.py.
    "[*] /game list             列出本房已上线、可玩的游戏。",
    "[*] /game new <名称>       在当前房间开一局；发起人坐第一席"
    "（chess: 白；gomoku/go/xiangqi/doushou: 黑/黑/红/红先手；sanguo: 房主）。",
    "[*] /game new <名称> ai [easy|normal|hard]  棋类开启 AI 练习局（仅 chess/gomoku/xiangqi）；"
    "练习局不计入持久化积分。",
    "[*] /game join             加入对局（chess/gomoku/go/xiangqi/doushou 为第二席；"
    "sanguo 可 2～6 人 join，房主 /game move 开始 开局）。",
    "[*] /game seats            显示双方与对局状态。",
    "[*] /game show             重新显示棋盘（己方在下，对手视角自动翻转）。",
    "[*] /game rating [游戏] [昵称]  查看棋类持久化积分/等级；积分跨房间共享。",
    "[*] reversi（黑白棋）终端：/game move <行> <列>；无合法落点时 /game move pass。",
    "[*] darkchess（暗棋/翻翻棋）终端：先 /game move flip <行> <列>，再 /game move move <起行> <起列> <终行> <终列>。",
    "[*] battleship（海战棋）终端：双方 place 五艘舰船后 ready，再 /game move fire <行> <列>。",
    "[*] junqi（军棋）终端：双方 setup 棋子后 ready，再 /game move move <起行> <起列> <终行> <终列>。",
    "[*] chess 棋盘用 Unicode 棋子（♔♟ 等）；空位为 ·，上一步格子用括号标出。"
    "请用等宽字体；深色背景下黑子若看不清可换浅色终端主题。",
    "[*] /game move …           chess: SAN/UCI；gomoku/go: 行 列；go 可 pass 停一手；"
    "xiangqi: 棋谱（炮二平五、马2进3）或坐标四元组；"
    "doushou: 坐标四元组（起行 起列 终行 终列）；"
    "sanguo: 军争版；等待时房主 开始；/game move 武将 查武将池；"
    "观星/蛊惑/断粮等技能见 /game show（别名 sgs/三国杀）。",
    "[*] xiangqi 也可用别名 cchess 开局。",
    "[*] 棋盘 +红 -黑 !上一步；马/象/士进退按纵线朝棋盘中线为进。",
    "[*] 象棋按竞赛规则：三次循环局面时，长将/长捉不变着判负；双方无照打可和棋。",
    "[*] /game pgn              导出当前/已结束棋局的 PGN（仅 chess）。",
    "[*] /game undo             悔棋：上一步走子方发起，对方 /game undo accept 同意后撤销一步"
    "（chess/gomoku/go/xiangqi/doushou；简写 acc / rej / can；reject 拒绝，cancel 取消请求）。",
    "[*] /game resign           认负（仅对局进行中）。",
    "[*] /game abort            终止未开始的对局。",
    "[*] /game end              房主可强制结束当前对局。",
    "[*] /game on <名称>        房主在本房上线某游戏（别名同 new）。",
    "[*] /game off <名称>       房主在本房下线某游戏（进行中的该局不受影响）。",
    "[*] holdem（德州扑克）中英指令对照：",
    "[*]   开始 start | 看牌 look | 过牌 check | 跟注 call | 加注 <额> raise <额> | 弃牌 fold | 全下 allin",
    "[*]   机器人 bot <easy|hard|pro>；开局后 /game show 帮助 可再看完整说明。",
    "[*] zjh（炸金花）中英对照：开始 start | 看牌 look | 跟注 follow | 加注 raise <额> | "
    "比牌 compare <昵称> | 弃牌 fold；比牌费用为当前单注两倍（看牌后再翻倍）；"
    "牌型：豹子>顺金>金花>顺子>对子>单张，花色不同235可胜豹子；"
    "同牌型相等时主动比牌者负；每局结束自动发下一局；"
    "需机器人时用 start bot 或 bot add [人数]；bot <easy|hard|pro> 设难度。",
    "[*] mahjong（麻将）4 人局：人数不足时 start 自动补 AI；房主可 bot <easy|hard|pro> 调难度。",
    "[*] 支持吃/碰/杠/点炮胡/自摸胡；轮到你时 discard <牌>，可 gang/hu；他人弃牌后可 chi/peng/gang/hu/pass。",
    "[*] 麻将编码说明：m=万（man），p=筒/饼（pin），s=条/索（sou），z=字牌（东南西北中发白）。",
    "[*] 麻将支持中文出牌：二万、九筒、五条、东风、红中、发财、白板（也支持 m1/p9/s5/z3）。",
)
