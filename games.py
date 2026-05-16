"""Mini game framework: chess (python-chess) + gomoku + xiangqi + raid for SSHChat.

Each game class exposes the same surface used by ``server.py``:
``try_join``, ``try_move``, ``resign``, ``abort``, ``seats``, ``show``,
``on_player_leave`` → ``(private_lines, broadcast_lines, ended)``.
Optional: ``pgn_export()`` for PGN (chess only).
"""

from __future__ import annotations

import random
import re
import unicodedata
from typing import Optional, TYPE_CHECKING

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
            sym = piece.symbol() if piece else "."
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


class ChessGame:
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
        label = (GOMOKU_SIZE - r) if flip else (r + 1)
        lines.append(f"{label:>2} " + "".join(row_cells))
    lines.append(hdr)
    if last is not None:
        lines.append(f"  上一步：({last[0] + 1}, {last[1] + 1})  （行 列，1 起算，左上为 1,1）")
    return lines


class GomokuGame:
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
        return conn is not None and conn is self.white_conn

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


class XiangqiGame:
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


# --- 搜打撤（合作撤离）---

_RAID_MAX_PLAYERS = 8
_RAID_MIN_PLAYERS = 2
_RAID_TURN_LIMIT = 36
_RAID_TURN_PER_EXTRA = 6
_RAID_EXTRACT_HOLD = 2
_RAID_SCAV_SPAWN_EVERY = 5
_RAID_SCAV_HP = 28
_RAID_SCAV_DMG = (6, 14)
_RAID_PLAYER_HP = 100
_RAID_WIN_VALUE = 400
_RAID_WIN_PER_EXTRA = 120

_RAID_ROOMS: dict[str, dict] = {
    "spawn": {
        "label": "出生点",
        "neighbors": ("hall", "garage"),
        "loot": False,
    },
    "hall": {
        "label": "走廊",
        "neighbors": ("spawn", "wh", "dorm", "plaza"),
        "loot": True,
    },
    "wh": {
        "label": "仓库",
        "neighbors": ("hall",),
        "loot": True,
    },
    "dorm": {
        "label": "宿舍",
        "neighbors": ("hall",),
        "loot": True,
    },
    "garage": {
        "label": "车库",
        "neighbors": ("spawn", "plaza"),
        "loot": True,
    },
    "plaza": {
        "label": "广场",
        "neighbors": ("hall", "garage", "extract"),
        "loot": False,
    },
    "extract": {
        "label": "撤离点",
        "neighbors": ("plaza",),
        "loot": False,
    },
}

_RAID_LOOT_TABLE: list[tuple[str, str, int]] = [
    ("绷带", "heal", 22),
    ("医疗包", "heal", 38),
    ("三级甲", "armor", 3),
    ("冲锋枪", "weapon", 4),
    ("金条", "value", 280),
    ("弹药箱", "weapon", 2),
    ("电路板", "value", 90),
    ("咖啡", "heal", 12),
]

_RAID_MOVE_ALIASES: dict[str, str] = {
    "搜": "search",
    "搜索": "search",
    "loot": "search",
    "search": "search",
    "打": "fight",
    "战斗": "fight",
    "fight": "fight",
    "撤": "extract",
    "撤离": "extract",
    "extract": "extract",
    "去": "go",
    "走": "go",
    "go": "go",
    "move": "go",
}


def _raid_room_key(token: str) -> Optional[str]:
    t = token.strip().lower()
    if not t:
        return None
    if t in _RAID_ROOMS:
        return t
    for key, meta in _RAID_ROOMS.items():
        if t == meta["label"] or t in meta["label"]:
            return key
    return None


def _raid_parse_action(raw: str) -> tuple[str, Optional[str]]:
    text = raw.strip()
    if not text:
        return ("", None)
    parts = text.split(maxsplit=1)
    head = parts[0].lower()
    tail = parts[1].strip() if len(parts) > 1 else ""
    verb = _RAID_MOVE_ALIASES.get(head, head)
    if verb == "go":
        dest = _raid_room_key(tail) if tail else None
        return ("go", dest)
    if verb in ("search", "fight", "extract"):
        return (verb, None)
    # 允许「走廊」「去仓库」等省略动词
    dest = _raid_room_key(text)
    if dest is not None:
        return ("go", dest)
    return (verb, tail or None)


class _RaidPlayer:
    __slots__ = ("conn", "name", "room", "hp", "armor", "weapon", "value")

    def __init__(self, conn, name: str) -> None:
        self.conn = conn
        self.name = name
        self.room = "spawn"
        self.hp = _RAID_PLAYER_HP
        self.armor = 0
        self.weapon = 1
        self.value = 0


class RaidGame:
    """Co-op extraction mini-game (搜-打-撤). Multiple raiders, round-robin turns."""

    name = "raid"
    first_seat_desc = "队长"
    second_seat_desc = "队员"
    join_blurb = (
        f"其它玩家可 /game join 加入（{_RAID_MIN_PLAYERS}～{_RAID_MAX_PLAYERS} 人，"
        f"满 {_RAID_MIN_PLAYERS} 人后开始行动，进行中仍可加入）。"
    )

    def __init__(self, leader_conn, leader_name: str) -> None:
        self.players: list[_RaidPlayer] = [_RaidPlayer(leader_conn, leader_name)]
        self.state = "waiting"
        self._turn_idx = 0
        self._ticks = 0
        self._looted: set[str] = set()
        self._scavs: list[dict] = []  # {room, hp}
        self._extract_hold = 0
        self._rng = random.Random()

    def _turn_limit(self) -> int:
        extra = max(0, len(self.players) - _RAID_MIN_PLAYERS)
        return _RAID_TURN_LIMIT + extra * _RAID_TURN_PER_EXTRA

    def _win_target(self) -> int:
        extra = max(0, len(self.players) - _RAID_MIN_PLAYERS)
        return _RAID_WIN_VALUE + extra * _RAID_WIN_PER_EXTRA

    def _who_of(self, conn) -> Optional[int]:
        for i, p in enumerate(self.players):
            if conn is p.conn:
                return i
        return None

    def is_seated(self, conn) -> bool:
        return self._who_of(conn) is not None

    def _alive(self) -> list[_RaidPlayer]:
        return [p for p in self.players if p.hp > 0]

    def _current(self) -> _RaidPlayer:
        return self.players[self._turn_idx]

    def _scavs_in(self, room: str) -> list[dict]:
        return [s for s in self._scavs if s["room"] == room]

    def _combined_value(self) -> int:
        return sum(p.value for p in self.players)

    def _all_alive_at_extract(self) -> bool:
        alive = self._alive()
        return bool(alive) and all(p.room == "extract" for p in alive)

    def _roster_names(self) -> str:
        return "、".join(p.name for p in self.players)

    def _render_map(self) -> list[str]:
        by_room: dict[str, list[str]] = {}
        for p in self.players:
            if p.hp <= 0:
                continue
            by_room.setdefault(p.room, []).append(p.name)
        lines = ["  区域连通："]
        for key, meta in _RAID_ROOMS.items():
            marks = []
            if key in self._looted:
                marks.append("已搜")
            if self._scavs_in(key):
                marks.append(f"敌×{len(self._scavs_in(key))}")
            if key in by_room:
                marks.append("、".join(by_room[key]))
            tag = f" [{', '.join(marks)}]" if marks else ""
            nbs = "、".join(_RAID_ROOMS[n]["label"] for n in meta["neighbors"])
            lines.append(f"    {meta['label']}({key}) → {nbs}{tag}")
        return lines

    def _status_line(self, p: _RaidPlayer, slot: int) -> str:
        dead = " [阵亡]" if p.hp <= 0 else ""
        return (
            f"  #{slot} {p.name}{dead}：{max(0, p.hp)}HP  "
            f"位置={_RAID_ROOMS[p.room]['label']}  "
            f"甲+{p.armor} 武+{p.weapon}  战利品价值={p.value}"
        )

    def show(self, conn=None) -> list[str]:
        target = self._win_target()
        lines = [
            f"raid 搜打撤（{self.state}）  队员 {len(self.players)}/{_RAID_MAX_PLAYERS}  "
            f"回合 {self._ticks}/{self._turn_limit()}  "
            f"全队价值 {self._combined_value()}（目标 {target} 撤离加分）",
        ]
        for i, p in enumerate(self.players, 1):
            lines.append(self._status_line(p, i))
        if len(self.players) < _RAID_MAX_PLAYERS and self.state != "ended":
            lines.append(
                f"  空席：还可 /game join（至少 {_RAID_MIN_PLAYERS} 人开始）"
            )
        lines.extend(self._render_map())
        if self.state == "playing":
            cur = self._current()
            lines.append(f"  当前行动：#{self._turn_idx + 1} {cur.name}")
            if self._extract_hold:
                lines.append(
                    f"  撤离读条：{self._extract_hold}/{_RAID_EXTRACT_HOLD} "
                    "（所有存活队员须在撤离点；继续 /game move 撤）"
                )
            lines.append(
                "  指令：/game move 搜 | 打 | 撤 | 去 <区域>"
                "（区域可用 走廊/仓库/撤离点 等）"
            )
        return lines

    def _start_playing(self) -> list[str]:
        self.state = "playing"
        self._turn_idx = 0
        self._spawn_scav()
        limit = self._turn_limit()
        target = self._win_target()
        first = self.players[0]
        return [
            f"raid 开始！队员：{self._roster_names()}",
            "合作搜刮、清敌；所有存活队员到撤离点后 /game move 撤（读条 2 次）。",
            f"风暴 {limit} 回合后封闭；全队战利品价值 ≥ {target} 为肥撤。",
            f"轮到 #{1} {first.name} 行动",
        ]

    def try_join(self, conn, name: str) -> GameResult:
        if self.state == "ended":
            return (
                [f"对局已结束，请先 /game new {self.name} 开新局。"],
                [],
                False,
            )
        if self._who_of(conn) is not None:
            return (["你已经在小队里。"], [], False)
        if len(self.players) >= _RAID_MAX_PLAYERS:
            return (
                [f"小队已满（最多 {_RAID_MAX_PLAYERS} 人）。"],
                [],
                False,
            )
        self.players.append(_RaidPlayer(conn, name))
        if self.state == "waiting":
            if len(self.players) < _RAID_MIN_PLAYERS:
                priv = [
                    f"{name} 加入小队（{len(self.players)}/{_RAID_MAX_PLAYERS}），"
                    f"再等 {_RAID_MIN_PLAYERS - len(self.players)} 人即可开始。",
                ]
                bcast = [f"{name} 加入，当前队员：{self._roster_names()}"]
                return (priv, bcast, False)
            return ([], self._start_playing(), False)
        bcast = [
            f"{name} 中途加入（{len(self.players)} 人），出生点集结。",
            f"当前队员：{self._roster_names()}",
        ]
        return ([], bcast, False)

    def _next_turn(self) -> _RaidPlayer:
        n = len(self.players)
        for _ in range(n):
            self._turn_idx = (self._turn_idx + 1) % n
            p = self.players[self._turn_idx]
            if p.hp > 0:
                return p
        return self.players[self._turn_idx]

    def _tick_storm(self) -> Optional[str]:
        self._ticks += 1
        if self._ticks % _RAID_SCAV_SPAWN_EVERY == 0:
            self._spawn_scav()
        if self._ticks >= self._turn_limit():
            return "风暴封闭，未能撤离 — 行动失败"
        if not self._alive():
            return "全队阵亡 — 行动失败"
        return None

    def _spawn_scav(self) -> None:
        rooms = [k for k in _RAID_ROOMS if k not in ("spawn", "extract")]
        if not rooms:
            return
        room = self._rng.choice(rooms)
        self._scavs.append({"room": room, "hp": _RAID_SCAV_HP})

    def _apply_loot(self, player: _RaidPlayer, item: tuple[str, str, int]) -> str:
        name, kind, val = item
        if kind == "heal":
            before = player.hp
            player.hp = min(_RAID_PLAYER_HP, player.hp + val)
            return f"使用 {name}，回复 {player.hp - before} HP"
        if kind == "armor":
            player.armor += val
            return f"装备 {name}，护甲 +{val}"
        if kind == "weapon":
            player.weapon += val
            return f"装备 {name}，火力 +{val}"
        player.value += val
        return f"获得 {name}，价值 +{val}"

    def _damage_player(self, player: _RaidPlayer, dmg: int) -> int:
        reduced = max(1, dmg - player.armor)
        player.hp -= reduced
        if self._extract_hold and player.hp > 0:
            self._extract_hold = 0
        return reduced

    def _do_search(self, actor: _RaidPlayer) -> GameResult:
        meta = _RAID_ROOMS[actor.room]
        if not meta["loot"]:
            return (["此处无物资可搜。"], [], False)
        if actor.room in self._looted:
            return (["这里已经搜刮干净了。"], [], False)
        roll = self._rng.random()
        if roll < 0.22:
            self._looted.add(actor.room)
            return (["翻找一番，只有空箱子和弹壳。"], [], False)
        item = self._rng.choice(_RAID_LOOT_TABLE)
        detail = self._apply_loot(actor, item)
        if roll > 0.55:
            self._looted.add(actor.room)
        bcast = [f"{actor.name} 搜刮 {_RAID_ROOMS[actor.room]['label']}：{detail}"]
        return ([], bcast, False)

    def _do_fight(self, actor: _RaidPlayer) -> GameResult:
        foes = self._scavs_in(actor.room)
        if not foes:
            return (["附近没有敌人。"], [], False)
        foe = foes[0]
        atk = actor.weapon + self._rng.randint(2, 8)
        foe["hp"] -= atk
        bcast = [
            f"{actor.name} 交火！造成 {atk} 伤害（敌人剩余 {max(0, foe['hp'])} HP）"
        ]
        if foe["hp"] <= 0:
            self._scavs.remove(foe)
            loot = self._rng.randint(40, 120)
            actor.value += loot
            bcast.append(f"击毙敌人，搜到战利品价值 +{loot}")
            return ([], bcast, False)
        dmg = self._rng.randint(*_RAID_SCAV_DMG)
        taken = self._damage_player(actor, dmg)
        bcast.append(f"敌人反击！{actor.name} 受到 {taken} 伤害（剩余 {actor.hp} HP）")
        if actor.hp <= 0:
            bcast.append(f"{actor.name} 阵亡出局。")
            if not self._alive():
                self.state = "ended"
                bcast.append("对局结束：全队阵亡，行动失败。")
                return ([], bcast, True)
        return ([], bcast, False)

    def _do_extract(self, actor: _RaidPlayer) -> GameResult:
        if actor.room != "extract":
            return (["不在撤离点（需先到达 撤离点/广场 一侧）。"], [], False)
        if not self._all_alive_at_extract():
            missing = [
                p.name
                for p in self._alive()
                if p.room != "extract"
            ]
            return (
                [
                    "还有存活队员不在撤离点，无法撤离。"
                    + (f"（未到：{'、'.join(missing)}）" if missing else "")
                ],
                [],
                False,
            )
        self._extract_hold += 1
        bcast = [
            f"{actor.name} 掩护撤离读条 {self._extract_hold}/{_RAID_EXTRACT_HOLD}…"
        ]
        if self._extract_hold < _RAID_EXTRACT_HOLD:
            return ([], bcast, False)
        total = self._combined_value()
        target = self._win_target()
        self.state = "ended"
        if total >= target:
            bcast.append(
                f"撤离成功！全队战利品价值 {total}（≥ {target}）— 肥撤！"
            )
        else:
            bcast.append(
                f"撤离成功但物资偏少（{total} < {target}）— 勉强活下来。"
            )
        return ([], bcast, True)

    def _do_go(self, actor: _RaidPlayer, dest: Optional[str]) -> GameResult:
        if dest is None:
            return (
                [
                    "用法：/game move 去 <区域>  例：去 走廊 / 去 hall / 仓库",
                    f"可选：{', '.join(m['label'] for m in _RAID_ROOMS.values())}",
                ],
                [],
                False,
            )
        if dest not in _RAID_ROOMS:
            return ([f"未知区域 {dest!r}。"], [], False)
        if dest not in _RAID_ROOMS[actor.room]["neighbors"]:
            here = _RAID_ROOMS[actor.room]["label"]
            there = _RAID_ROOMS[dest]["label"]
            return ([f"无法从 {here} 直接走到 {there}。"], [], False)
        actor.room = dest
        label = _RAID_ROOMS[dest]["label"]
        bcast = [f"{actor.name} 抵达 {label}"]
        # 进入新区域有小概率踩雷
        if dest != "extract" and self._scavs_in(dest) and self._rng.random() < 0.35:
            dmg = self._rng.randint(4, 10)
            taken = self._damage_player(actor, dmg)
            bcast.append(f"遭遇伏击！受到 {taken} 伤害（剩余 {actor.hp} HP）")
            if actor.hp <= 0:
                bcast.append(f"{actor.name} 阵亡出局。")
                if not self._alive():
                    self.state = "ended"
                    bcast.append("对局结束：全队阵亡，行动失败。")
                    return ([], bcast, True)
        return ([], bcast, False)

    def try_move(self, conn, raw: str) -> GameResult:
        if self.state == "waiting":
            need = _RAID_MIN_PLAYERS - len(self.players)
            return (
                [
                    f"行动尚未开始，还需 {need} 名队员 /game join"
                    f"（当前 {len(self.players)}/{_RAID_MAX_PLAYERS}）。"
                ],
                [],
                False,
            )
        if self.state != "playing":
            return (["对局已结束。"], [], False)
        who = self._who_of(conn)
        if who is None:
            return (["你不是行动队员（可 /game show 围观）。"], [], False)
        if who != self._turn_idx:
            cur = self._current()
            return (
                [f"还没轮到你，当前由 #{self._turn_idx + 1} {cur.name} 行动。"],
                [],
                False,
            )

        actor = self.players[who]
        if actor.hp <= 0:
            return (["你已阵亡，无法行动。"], [], False)

        verb, arg = _raid_parse_action(raw)
        if not verb:
            return (
                [
                    "用法：/game move 搜 | 打 | 撤 | 去 <区域>",
                    "  例：/game move 搜  /game move 打  /game move 去 走廊",
                ],
                [],
                False,
            )

        if verb == "search":
            priv, bcast, ended = self._do_search(actor)
        elif verb == "fight":
            priv, bcast, ended = self._do_fight(actor)
        elif verb == "extract":
            priv, bcast, ended = self._do_extract(actor)
        elif verb == "go":
            priv, bcast, ended = self._do_go(actor, arg)
        else:
            return ([f"未知指令 {verb!r}，请用 搜/打/撤/去。"], [], False)

        if ended:
            return (priv, bcast, True)

        storm = self._tick_storm()
        if storm is not None:
            self.state = "ended"
            bcast = list(bcast) + [storm]
            return (priv, bcast, True)

        if not ended and self.state == "playing":
            nxt = self._next_turn()
            slot = self._turn_idx + 1
            bcast = list(bcast) + [
                f"轮到 #{slot} {nxt.name} 行动（回合 {self._ticks}/{self._turn_limit()}）"
            ]

        return (priv, bcast, False)

    def resign(self, conn, name: str) -> GameResult:
        if self.state != "playing":
            return (["行动尚未开始或已结束。"], [], False)
        if self._who_of(conn) is None:
            return (["你不是行动队员。"], [], False)
        self.state = "ended"
        return ([], [f"{name} 放弃任务 — 小队撤离失败。"], True)

    def abort(self, conn, name: str) -> GameResult:
        if self.state == "ended":
            return (["对局已结束。"], [], False)
        if self._who_of(conn) is None:
            return (["你不是行动队员，无法终止。"], [], False)
        if self.state == "playing":
            return (
                ["任务已开始，请用 /game resign 放弃。"],
                [],
                False,
            )
        self.state = "ended"
        return ([], [f"{name} 取消了任务（未开始）。"], True)

    def seats(self) -> list[str]:
        lines = [
            f"raid 状态：{self.state}  "
            f"队员 {len(self.players)}/{_RAID_MAX_PLAYERS}  "
            f"回合 {self._ticks}/{self._turn_limit()}",
        ]
        for i, p in enumerate(self.players, 1):
            lines.append(f"  #{i}：{p.name}")
        if len(self.players) < _RAID_MAX_PLAYERS and self.state != "ended":
            lines.append("  空席：/game join")
        return lines

    def on_player_leave(self, conn, name: str) -> GameResult:
        who = self._who_of(conn)
        if who is None:
            return ([], [], False)
        if self.state == "waiting":
            self.players.pop(who)
            if not self.players:
                self.state = "ended"
                return ([], [f"{name} 离开，任务取消。"], True)
            return ([], [f"{name} 离开，当前队员：{self._roster_names()}"], False)
        if self.state == "playing":
            self.players.pop(who)
            if who < self._turn_idx:
                self._turn_idx -= 1
            elif who == self._turn_idx:
                self._turn_idx %= max(1, len(self.players))
            if not self._alive():
                self.state = "ended"
                return ([], [f"{name} 离开 — 全队失联，行动失败。"], True)
            return (
                [],
                [f"{name} 离开，剩余队员：{self._roster_names()}"],
                False,
            )
        return ([], [], False)


GAMES = {
    ChessGame.name: ChessGame,
    GomokuGame.name: GomokuGame,
    XiangqiGame.name: XiangqiGame,
    RaidGame.name: RaidGame,
}
GAME_ALIASES = {
    "cchess": XiangqiGame.name,
    "sdc": RaidGame.name,
    "extract": RaidGame.name,
    "搜打撤": RaidGame.name,
}


def resolve_game_name(name: str) -> str:
    """Map alias (e.g. cchess) to canonical game id."""
    key = name.lower()
    return GAME_ALIASES.get(key, key)


def list_game_names() -> list[str]:
    """Canonical game ids for /game list (aliases documented in help)."""
    return sorted(GAMES)


HELP_LINES = (
    "[*] /game list             列出可玩游戏。",
    "[*] /game new <名称>       在当前房间开一局；发起人坐第一席"
    "（chess: 白；gomoku/xiangqi: 黑/红先手；raid: 队长）。",
    "[*] /game join             加入对局（chess/gomoku/xiangqi 为第二席；"
    "raid 可多人，最多 8 人）。",
    "[*] /game seats            显示双方与对局状态。",
    "[*] /game show             重新显示棋盘（己方在下，对手视角自动翻转）。",
    "[*] chess 棋盘上下坐标行两端的 8/1 与邻行相同，用于列对齐；"
    "若仍错位请用等宽字体或 SSH 终端查看。",
    "[*] /game move …           chess: SAN/UCI；gomoku: 行 列；"
    "xiangqi: 棋谱（炮二平五、马2进3）或坐标四元组；"
    "raid: 搜 | 打 | 撤 | 去 <区域>（2～8 人合作撤离，别名 sdc/搜打撤）。",
    "[*] xiangqi 也可用别名 cchess 开局。",
    "[*] 棋盘 +红 -黑 !上一步；马/象/士进退按纵线朝棋盘中线为进。",
    "[*] /game pgn              导出当前/已结束棋局的 PGN（仅 chess）。",
    "[*] /game resign           认负（仅对局进行中）。",
    "[*] /game abort            终止未开始的对局。",
    "[*] /game end              房主可强制结束当前对局。",
)
