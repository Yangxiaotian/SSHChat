"""Mini game framework: chess (python-chess) + gomoku for SSHChat.

Each game class exposes the same surface used by ``server.py``:
``try_join``, ``try_move``, ``resign``, ``abort``, ``seats``, ``show``,
``on_player_leave`` → ``(private_lines, broadcast_lines, ended)``.
Optional: ``pgn_export()`` for PGN (chess only).
"""

from __future__ import annotations

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


def _render_board(board, *, last_move=None):
    hi = _squares_of_last_move(last_move)
    # Each cell is 3 chars wide: " P " for plain or "(P)" for last-move
    # highlight. Header uses the same per-cell format so files line up with
    # piece symbols even when highlights are present.
    # Left/right rank gutters are both 3 chars (same idea as gomoku's
    # ``f"{n:>2} "`` / ``f" {n:>2}"``), and the header adds matching blank
    # gutters so every row has identical width — avoids jagged top/bottom edges.
    def col_label(ch: str) -> str:
        return f" {ch} "

    file_row = "".join(col_label(c) for c in "abcdefgh")
    header = "   " + file_row + "   "
    lines = [header]
    for rank in range(8, 0, -1):
        cells = []
        for f in range(8):
            sq = _chess.square(f, rank - 1)
            piece = board.piece_at(sq)
            sym = piece.symbol() if piece else "."
            cells.append(f"({sym})" if sq in hi else f" {sym} ")
        lines.append(f"{rank:>2} " + "".join(cells) + f" {rank:>2}")
    lines.append(header)
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
        ]
        bcast.extend(_render_board(self.board, last_move=self._last_move))
        bcast.append(f"轮到 白方 {self.white_name}（第 1 手）")
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
        bcast.extend(_render_board(self.board, last_move=self._last_move))

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

    def show(self) -> list[str]:
        lines = [
            f"chess 对局（{self.state}）  白：{self.white_name}   "
            f"黑：{self.black_name or '空席'}"
        ]
        lines.extend(_render_board(self.board, last_move=self._last_move))
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
) -> list[str]:
    """ASCII board: # = black (first), o = white. last move cell in parens."""
    # 3-char cell ("(x)" or " x "), 3-char left prefix matching the column
    # header offset, so stones sit directly under their column number.
    hdr = "   " + "".join(f"{i:>2} " for i in range(1, GOMOKU_SIZE + 1))
    lines = [hdr]
    sym = {0: ".", 1: "#", 2: "o"}
    for r in range(GOMOKU_SIZE):
        row_cells = []
        for c in range(GOMOKU_SIZE):
            ch = sym[grid[r][c]]
            if last is not None and (r, c) == last:
                row_cells.append(f"({ch})")
            else:
                row_cells.append(f" {ch} ")
        lines.append(f"{r + 1:>2} " + "".join(row_cells) + f" {r + 1:>2}")
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
        ]
        bcast.extend(_gomoku_render(self.grid, last=self._last))
        bcast.append(f"轮到 黑方 {self.black_name} 落子")
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
        bcast.extend(_gomoku_render(self.grid, last=self._last))

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

    def show(self) -> list[str]:
        lines = [
            f"gomoku 对局（{self.state}）  黑：{self.black_name}   "
            f"白：{self.white_name or '空席'}"
        ]
        lines.extend(_gomoku_render(self.grid, last=self._last))
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


GAMES = {ChessGame.name: ChessGame, GomokuGame.name: GomokuGame}


HELP_LINES = (
    "[*] /game list             列出可玩游戏。",
    "[*] /game new <名称>       在当前房间开一局；发起人坐第一席（chess: 白；gomoku: 黑先手）。",
    "[*] /game join             加入空第二席。",
    "[*] /game seats            显示双方与对局状态。",
    "[*] /game show             重新显示棋盘。",
    "[*] /game move …           chess: SAN/UCI；gomoku: 行 列（1～15），如 8 8。",
    "[*] /game pgn              导出当前/已结束棋局的 PGN（仅 chess）。",
    "[*] /game resign           认负（仅对局进行中）。",
    "[*] /game abort            终止未开始的对局。",
    "[*] /game end              房主可强制结束当前对局。",
)
