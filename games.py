"""Mini game framework: chess (python-chess) + gomoku + xiangqi + sanguo for SSHChat.

Each game class exposes the same surface used by ``server.py``:
``try_join``, ``try_move``, ``resign``, ``abort``, ``seats``, ``show``,
``on_player_leave`` → ``(private_lines, broadcast_lines, ended)``.
Optional: ``pgn_export()`` for PGN (chess only).
"""

from __future__ import annotations

import random
import re
import itertools
import unicodedata
from typing import Optional, TYPE_CHECKING

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


class BoardUndoMixin:
    """悔棋：上一步走子方 /game undo，对方 /game undo accept。"""

    supports_undo = True

    def _undo_clear_pending(self) -> None:
        self._undo_requester_conn = None

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

    def __init__(self, white_conn, white_name: str) -> None:
        if _chess is None:
            raise RuntimeError(
                "python-chess 未安装。请在服务端 venv 内 "
                "`pip install 'chess>=1.10'` 后重启服务。"
            )
        self.board = _chess.Board()
        self.white_conn = white_conn
        self.white_name = white_name
        self.black_conn = None
        self.black_name: Optional[str] = None
        self.state = "waiting"
        self._last_move = None
        self._result_header: Optional[str] = None  # PGN Result when not from board.outcome()
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

    def _viewer_flip(self, conn) -> bool:
        return conn is not None and conn is self.black_conn

    def _board_render(self, conn=None) -> list[str]:
        return _render_board(
            self.board, last_move=self._last_move, flip=self._viewer_flip(conn)
        )

    def try_join(self, conn, name: str) -> GameResult:
        if self.state == "ended":
            return (
                [f"对局已结束，请先 /game new {self.name} 开新局。"],
                [],
                False,
            )
        if conn is self.white_conn:
            return (["你已经是白方。"], [], False)
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
            if outcome.winner is True:
                self._result_header = "1-0"
            elif outcome.winner is False:
                self._result_header = "0-1"
            else:
                self._result_header = "1/2-1/2"
            bcast.append(_format_outcome(outcome))
            return ([], bcast, True)

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
            return ([], [f"白方 {name} 认负 — 黑胜 0-1"], True)
        self._result_header = "1-0"
        return ([], [f"黑方 {name} 认负 — 白胜 1-0"], True)

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
        return [
            f"chess 对局状态：{self.state}",
            f"  白方：{self.white_name}",
            f"  黑方：{self.black_name or '(空席, 可 /game join)'}",
        ]

    def show(self, conn=None) -> list[str]:
        lines = [
            f"chess 对局（{self.state}）  白：{self.white_name}   "
            f"黑：{self.black_name or '空席'}"
        ]
        lines.extend(self._board_render(conn))
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
                return ([], [f"白方 {name} 离开 — 黑胜 0-1"], True)
            self._result_header = "1-0"
            return ([], [f"黑方 {name} 离开 — 白胜 1-0"], True)
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


def _gomoku_winner_at(
    grid: list[list[int]], row: int, col: int, who: int
) -> bool:
    dirs = ((1, 0), (0, 1), (1, 1), (1, -1))
    for dr, dc in dirs:
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
        if cnt >= 5:
            return True
    return False


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
    """15×15 gomoku. Creator = black (先手); joiner = white."""

    name = "gomoku"
    first_seat_desc = "黑方（先手）"
    second_seat_desc = "白方"

    def __init__(self, black_conn, black_name: str) -> None:
        self.grid: list[list[int]] = [
            [0 for _ in range(GOMOKU_SIZE)] for _ in range(GOMOKU_SIZE)
        ]
        self.black_conn = black_conn
        self.black_name = black_name
        self.white_conn = None
        self.white_name: Optional[str] = None
        self.state = "waiting"
        self._turn = 1  # 1=black, 2=white
        self._last: Optional[tuple[int, int]] = None
        self._history: list[tuple[int, int, int]] = []  # row, col, player
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

    def try_join(self, conn, name: str) -> GameResult:
        if self.state == "ended":
            return (
                [f"对局已结束，请先 /game new {self.name} 开新局。"],
                [],
                False,
            )
        if conn is self.black_conn:
            return (["你已经是黑方。"], [], False)
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
        self._last = (row, col)
        self._history.append((row, col, player))

        bname = self.black_name if player == 1 else self.white_name
        stone = "黑" if player == 1 else "白"
        bcast = [f"{stone}方 {bname} 落子 ({row + 1}, {col + 1})"]

        if _gomoku_winner_at(self.grid, row, col, player):
            self.state = "ended"
            bcast.append(f"对局结束：{stone}方 {bname} 连五获胜！")
            return ([], bcast, True)

        if all(self.grid[r][c] != 0 for r in range(GOMOKU_SIZE) for c in range(GOMOKU_SIZE)):
            self.state = "ended"
            bcast.append("对局结束：棋盘已满，和棋。")
            return ([], bcast, True)

        self._turn = 3 - player
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
            return ([], [f"黑方 {name} 认负 — 白胜"], True)
        return ([], [f"白方 {name} 认负 — 黑胜"], True)

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
        return [
            f"gomoku 对局状态：{self.state}",
            f"  黑方（先手）：{self.black_name}",
            f"  白方：{self.white_name or '(空席, 可 /game join)'}",
        ]

    def show(self, conn=None) -> list[str]:
        lines = [
            f"gomoku 对局（{self.state}）  黑：{self.black_name}   "
            f"白：{self.white_name or '空席'}"
        ]
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
                return ([], [f"黑方 {name} 离开 — 白胜"], True)
            return ([], [f"白方 {name} 离开 — 黑胜"], True)
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
                # 将帅对脸时同列无子隔也可视为被“照面”
                if (
                    board[row][col] == by_side * _XQ_K
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


def _xq_parse_move(
    raw: str, side: int, board: list[list[int]]
) -> Optional[tuple[int, int, int, int]]:
    t = raw.strip()
    if not t:
        return None
    coord = _xq_parse_coord_move(t, side)
    if coord is not None:
        return coord
    return _xq_parse_notation(t, side, board)


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

    def __init__(self, red_conn, red_name: str) -> None:
        self.board = _xq_initial_board()
        self.red_conn = red_conn
        self.red_name = red_name
        self.black_conn = None
        self.black_name: Optional[str] = None
        self.state = "waiting"
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
        self._undo_clear_pending()

    def _undo_has_moves(self) -> bool:
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
        if not self._history:
            return False
        self._history.pop()
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
        return True

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

    def _viewer_flip(self, conn) -> bool:
        return conn is not None and conn is self.black_conn

    def _board_render(self, conn=None) -> list[str]:
        return _xq_render(
            self.board,
            last_from=self._last_from,
            last_to=self._last_to,
            last_notation=self._last_notation,
            flip=self._viewer_flip(conn),
        )

    def try_join(self, conn, name: str) -> GameResult:
        if self.state == "ended":
            return (
                [f"对局已结束，请先 /game new {self.name} 开新局。"],
                [],
                False,
            )
        if conn is self.red_conn:
            return (["你已经是红方。"], [], False)
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
            f"轮到 红方 {self.red_name} 走子",
        ]
        return ([], bcast, False)

    def try_move(self, conn, raw: str) -> GameResult:
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
                return (["起点无子。"], [], False)
            if _xq_piece_side(self.board[fr][fc]) != side:
                return (["不能移动对方的棋子。"], [], False)
            return (["该走法不合法（蹩马腿、塞象眼、出九宫、照面等）。"], [], False)

        label = _xq_move_label(self.board, fr, fc, tr, tc, side)
        captured = self.board[tr][tc]
        _xq_apply(self.board, fr, fc, tr, tc)
        self._last_from = (fr, fc)
        self._last_to = (tr, tc)
        self._last_mover_side = side
        self._last_notation = label
        self._history.append(self._xq_snapshot())

        mover = self.red_name if side == _XQ_RED else self.black_name
        color = "红" if side == _XQ_RED else "黑"
        bcast = [f"{color}方 {mover} 走 {label}"]

        if captured != 0 and _xq_piece_type(captured) == _XQ_K:
            self.state = "ended"
            bcast.append(f"对局结束：{color}方 {mover} 获胜（将死）！")
            return ([], bcast, True)

        self._turn = -side
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
            else:
                bcast.append("对局结束：双方无合法着法，和棋。")
            return ([], bcast, True)

        next_name = self.red_name if self._turn == _XQ_RED else self.black_name
        next_color = "红" if self._turn == _XQ_RED else "黑"
        suffix = "（将军）" if in_check else ""
        bcast.append(f"轮到 {next_color}方 {next_name} 走子{suffix}")
        return ([], bcast, False)

    def resign(self, conn, name: str) -> GameResult:
        if self.state != "playing":
            return (["对局尚未开始或已结束，无需认负。"], [], False)
        side = self._side_of(conn)
        if side is None:
            return (["你不是对局双方。"], [], False)
        self.state = "ended"
        if side == _XQ_RED:
            return ([], [f"红方 {name} 认负 — 黑胜"], True)
        return ([], [f"黑方 {name} 认负 — 红胜"], True)

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
        return [
            f"xiangqi 对局状态：{self.state}",
            f"  红方（先手）：{self.red_name}",
            f"  黑方：{self.black_name or '(空席, 可 /game join)'}",
        ]

    def show(self, conn=None) -> list[str]:
        lines = [
            f"xiangqi 对局（{self.state}）  红：{self.red_name}   "
            f"黑：{self.black_name or '空席'}"
        ]
        lines.extend(self._board_render(conn))
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
                return ([], [f"红方 {name} 离开 — 黑胜"], True)
            return ([], [f"黑方 {name} 离开 — 红胜"], True)
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
    "雷击": "leiji",
    "leiji": "leiji",
    "天香": "tianxiang",
    "tianxiang": "tianxiang",
    "享乐": "xiangle",
    "xiangle": "xiangle",
    "英魂": "yinghun",
    "yinghun": "yinghun",
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
        labels = [card_label(c) for c in p.hand]
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
            return True, lines
        lines.append(f"  → 非红色，仍需【闪】或受击")
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
            labels = [card_label(c) for c in p.hand]
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
            return False, lines
        lines.append(f"  → 【{label}】生效")
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
        actor.drew_this_turn = False
        msgs: list[str] = []
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

    def _end_turn_discard(self, actor: _SgsPlayer) -> list[str]:
        msgs: list[str] = []
        dropped = 0
        while len(actor.hand) > actor.hp:
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
                    p.hp = 1
                    p.dead = False
                    notes.append(f"{p.name}涅槃至1体力")
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
            if self._has_skill(src, "fangzhu") and p.hand:
                c = p.hand.pop()
                self._discard.append(c)
                notes.append(f"{p.name}放逐弃【{card_label(c)}】")
        if reactions:
            self._kuanggu_check(target_idx, notes)
            self._chain_spread_damage(target_idx, notes)
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

    def _chain_spread_damage(self, origin: int, notes: list[str]) -> None:
        if not self.players[origin].chained:
            return
        hit: list[str] = []
        for i, other in enumerate(self.players):
            if i != origin and other.chained and not other.dead:
                other.hp -= 1
                hit.append(other.name)
                if other.hp <= 0:
                    other.dead = True
                    notes.append(f"{other.name}阵亡（{other.role}）")
        if hit:
            notes.append(f"铁索：{'、'.join(hit)}各-1")

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

    def _finish_turn(self, actor_idx: int) -> list[str]:
        actor = self.players[actor_idx]
        msgs = self._end_turn_discard(actor)
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
        return ([], [f"{actor.name}【决斗】→{tgt_name}{extra}"], False)

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
                    "装备 <牌名> | 无中生有 | 南蛮 | 万箭 | 酒 | 过",
                ],
                [],
                False,
            )

        if verb == "pass":
            bcast = [f"{player.name} 结束出牌阶段"]
            bcast.extend(self._finish_turn(who))
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
                and target.hp <= 1
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
            player.hand.remove(found)
            self._discard.append(found)
            victim = self.players[tgt]
            if victim.judge_lebu:
                return ([f"{victim.name} 判定区已有【乐不思蜀】。"], [], False)
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
            if not self._has_skill(player, "yinghun"):
                return (["你没有英魂技能。"], [], False)
            if not args:
                return (
                    ["用法：/game move 英魂 己 | 英魂 他 <目标>"],
                    [],
                    False,
                )
            mode = args[0]
            if mode in ("己", "自己"):
                if self._draw_cards(player, 1):
                    return ([], [f"{player.name}（英魂）摸 1 张"], False)
                return (["牌堆已空。"], [], False)
            if mode in ("他",) and len(args) >= 2:
                tgt = self._resolve_target(who, args[1:])
                if tgt is None:
                    return (["无效目标。"], [], False)
                if self._draw_cards(self.players[tgt], 1):
                    return (
                        [],
                        [f"{player.name}（英魂）令 {self.players[tgt].name} 摸 1 张"],
                        False,
                    )
                return (["牌堆已空。"], [], False)
            return (["用法：/game move 英魂 己 | 英魂 他 <目标>"], [], False)

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
                self.witch_saved = True
                self.witch_save_available = False
                priv.append(f"Saved: {self.pending_kill}")
            elif cmd == "poison":
                if role != "witch":
                    return (["Only witch can poison."], [], False)
                if not self.witch_poison_available:
                    return (["Poison already used."], [], False)
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
    join_blurb = "其他玩家可用 /game join 加入，房主用 /game move start 开始"
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
    def _auto_add_bots(self) -> list[str]:
        human = sum(1 for c, _n in self.players if c is not None)
        if human >= 2:
            return []
        add_n = min(5, max(3, self.rng.randint(3, 5)), 6 - len(self.players))
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
    def _bot_action(self, name: str) -> str:
        cat, tie = _zjh_eval3(self.cards[name])
        score = cat * 100 + (tie[0] if tie else 0)
        if self.bot_level == "easy":
            return self.rng.choice(["follow", "follow", "fold", "look"])
        if self.bot_level == "pro":
            if score >= 500:
                return "raise 2"
            if score < 220 and self.current_bet >= 3:
                return "fold"
            targets = [n for n in self._alive() if n != name]
            if score >= 350 and len(targets) >= 1 and self.rng.random() < 0.35:
                return f"compare {self.rng.choice(targets)}"
            return "follow"
        if score >= 450:
            return "raise 1"
        if score < 220 and self.current_bet >= 4:
            return "fold"
        return self.rng.choice(["follow", "follow", "look"])
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
    def _name_of(self, conn) -> Optional[str]:
        for c, n in self.players:
            if c is conn:
                return n
        return None
    def _alive(self) -> list[str]:
        return [n for _c, n in self.players if n not in self.folded and self.stacks.get(n, 0) > 0]
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
    def _start(self) -> list[str]:
        if len(self.players) < 2:
            self._auto_add_bots()
        if len(self.players) < 2:
            return ["至少需要 2 名玩家才能开始。"]
        if any(self.stacks.get(n, 0) <= 0 for _c, n in self.players):
            return ["有玩家积分已耗尽。请重开游戏重置为1000积分。"]
        self.state = "playing"
        self.folded.clear(); self.looked.clear(); self.cards.clear()
        self.turn_idx = 0; self.pot = 0; self.current_bet = 1
        deck = [f"{r}{s}" for r in _ZJH_RANKS for s in _ZJH_SUITS]
        self.rng.shuffle(deck)
        for _c, n in self.players:
            self.cards[n] = [deck.pop(), deck.pop(), deck.pop()]
            self.stacks.setdefault(n, 1000)
            if self.stacks[n] > 0:
                self.stacks[n] -= 1; self.pot += 1
        self._pick_next_actor_from_start()
        out = ["炸金花已开始，可用 /game show 查看手牌。", f"底池={self.pot}", f"轮到：{self.players[self.turn_idx][1]}"]
        if self.bot_names:
            out.append(f"机器人：{', '.join(sorted(self.bot_names))}（难度={_bot_level_zh(self.bot_level)}）")
        out.extend(self._run_bots())
        return out
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
                return (["只有房主可以设置机器人难度。"], [], False)
            if len(parts) < 2 or parts[1].lower() not in ("easy", "hard", "pro"):
                return (["用法：/game move bot <easy|hard|pro>"], [], False)
            self.bot_level = parts[1].lower()
            return ([], [f"机器人难度已设为：{_bot_level_zh(self.bot_level)}"], False)
        if cmd == "start":
            if self.state == "playing": return (["对局已经开始。"], [], False)
            if conn is not self.players[0][0]: return (["只有房主可以开始对局。"], [], False)
            return ([], self._start(), False)
        if self.state != "playing": return (["当前不是进行中状态。"], [], False)
        if actor in self.folded: return (["你已经弃牌。"], [], False)
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
            if self.stacks[actor] < cost: return ([f"积分不足，需要 {cost}"], [], False)
            self.stacks[actor] -= cost; self.pot += cost
            me = _zjh_eval3(self.cards[actor]); tg = _zjh_eval3(self.cards[target])
            loser = target if me >= tg else actor; winner = actor if me >= tg else target
            self.folded.add(loser); bcast.append(f"{actor} 与 {target} 比牌：{winner} 胜出，{loser} 弃牌"); self._advance()
        else: return (["可用操作：开始、看牌、跟注、加注、弃牌、比牌。"], [], False)
        done = self._finish_if_one()
        if done: return ([], bcast + done, True)
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
        if self.state == "waiting": lines.append("房主可用 /game move start 开始")
        if self.state == "playing": lines.append(f"轮到：{self.players[self.turn_idx][1]}")
        return lines
    def show(self, conn=None, full: bool = False) -> list[str]:
        lines = self.seats(); me = self._name_of(conn) if conn is not None else None
        if me and me in self.cards: lines.append(f"你的手牌：{_fmt_poker_cards(self.cards[me])}")
        return lines
    def on_player_leave(self, conn, name: str) -> GameResult:
        removed_idx = None
        for i, (c, n) in enumerate(self.players):
            if c is conn:
                removed_idx = i
                self.players.pop(i)
                self.folded.add(n)
                self.cards.pop(n, None)
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
        if done: return ([], [f"{name} 离开"] + done, True)
        return ([], [f"{name} 离开了炸金花对局"], False)


class HoldemGame:
    name = "holdem"
    first_seat_desc = "房主"
    join_blurb = "其他玩家可用 /game join 加入，房主用 /game move start 开始"

    def __init__(self, host_conn, host_name: str) -> None:
        self.players: list[tuple[object, str]] = [(host_conn, host_name)]
        self.bot_names: set[str] = set()
        self.bot_conns: dict[str, object] = {}
        self.bot_level = "hard"
        self._bot_running = False

        self.state = "waiting"
        self.folded: set[str] = set()
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
        human = sum(1 for c, _n in self.players if c is not None)
        if human >= 2:
            return []
        add_n = min(5, max(3, self.rng.randint(3, 5)), 6 - len(self.players))
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
        winner = scored[0][0]
        gain = self.pot
        self.stacks[winner] += gain
        self.pot = 0
        self.state = "ended"
        lines = ["摊牌：", f"公共牌：{self._fmt(self.board)}"]
        for n, _sc in scored:
            lines.append(f"- {n}: {self._fmt(self.hands[n])}")
        lines.append(f"获胜者：{winner}，底池 +{gain}")
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
        if len(self.players) < 2:
            self._auto_add_bots()
        if len(self.players) < 2:
            return ["至少需要 2 名玩家才能开始。"]
        if any(self.stacks.get(n, 0) <= 0 for _c, n in self.players):
            return ["有玩家积分已耗尽。请重开游戏重置为1000积分。"]

        self.state = "playing"
        self.folded.clear()
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
            return (["用法：/game move <start/check/call/raise/fold/allin>"], [], False)
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

        if self.state != "playing":
            return (["当前不是进行中状态。"], [], False)
        if name in self.folded:
            return (["你已经弃牌。"], [], False)
        if self.stacks.get(name, 0) <= 0:
            return (["你已全下，等待本轮结算。"], [], False)

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
                return (["用法：/game move raise <amount>"], [], False)
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
            return (["可用操作：过牌、跟注、加注、弃牌、全下。"], [], False)

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
            lines.append(f"#{i} {n}：积分={self.stacks.get(n, 0)} {tag}{mark}")
        return lines

    def show(self, conn=None, full: bool = False) -> list[str]:
        lines = self.seats()
        me = self._name_of(conn) if conn is not None else None
        if me and me in self.hands:
            lines.append(f"你的手牌：{self._fmt(self.hands[me])}")
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
        best_idx = max([i for i, row in enumerate(self.rows) if row[-1] < card], key=lambda i: self.rows[i][-1])
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


GAMES = {
    ChessGame.name: ChessGame,
    GomokuGame.name: GomokuGame,
    XiangqiGame.name: XiangqiGame,
    SanguoshaGame.name: SanguoshaGame,
    WerewolfGame.name: WerewolfGame,
    HoldemGame.name: HoldemGame,
    ZhaJinHuaGame.name: ZhaJinHuaGame,
    NiuTouWangGame.name: NiuTouWangGame,
}
GAME_ALIASES = {
    "cchess": XiangqiGame.name,
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
    "三国杀": SanguoshaGame.name,
}


def resolve_game_name(name: str) -> str:
    """Map alias (e.g. cchess) to canonical game id."""
    key = name.lower()
    return GAME_ALIASES.get(key, key)


def all_game_names() -> list[str]:
    """All registered game ids (for owner catalog / defaults)."""
    return sorted(GAMES)


def list_game_names(enabled: Optional[set[str]] = None) -> list[str]:
    """Canonical game ids for /game list; optional room filter (online only)."""
    if enabled is None:
        return all_game_names()
    return sorted(n for n in GAMES if n in enabled)


HELP_LINES = (
    "[*] /game list             列出本房已上线、可玩的游戏。",
    "[*] /game new <名称>       在当前房间开一局；发起人坐第一席"
    "（chess: 白；gomoku/xiangqi: 黑/红先手；sanguo: 房主）。",
    "[*] /game join             加入对局（chess/gomoku/xiangqi 为第二席；"
    "sanguo 可 2～6 人 join，房主 /game move 开始 开局）。",
    "[*] /game seats            显示双方与对局状态。",
    "[*] /game show             重新显示棋盘（己方在下，对手视角自动翻转）。",
    "[*] chess 棋盘用 Unicode 棋子（♔♟ 等）；空位为 ·，上一步格子用括号标出。"
    "请用等宽字体；深色背景下黑子若看不清可换浅色终端主题。",
    "[*] /game move …           chess: SAN/UCI；gomoku: 行 列；"
    "xiangqi: 棋谱（炮二平五、马2进3）或坐标四元组；"
    "sanguo: 军争版；等待时房主 开始；/game move 武将 查武将池；"
    "观星/蛊惑/断粮等技能见 /game show（别名 sgs/三国杀）。",
    "[*] xiangqi 也可用别名 cchess 开局。",
    "[*] 棋盘 +红 -黑 !上一步；马/象/士进退按纵线朝棋盘中线为进。",
    "[*] /game pgn              导出当前/已结束棋局的 PGN（仅 chess）。",
    "[*] /game undo             悔棋：上一步走子方发起，对方 /game undo accept 同意后撤销一步"
    "（chess/gomoku/xiangqi；reject 拒绝，cancel 取消请求）。",
    "[*] /game resign           认负（仅对局进行中）。",
    "[*] /game abort            终止未开始的对局。",
    "[*] /game end              房主可强制结束当前对局。",
    "[*] /game on <名称>        房主在本房上线某游戏（别名同 new）。",
    "[*] /game off <名称>       房主在本房下线某游戏（进行中的该局不受影响）。",
)
