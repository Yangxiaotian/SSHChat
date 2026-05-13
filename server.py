import os
import re
import socket
import threading
import traceback
from collections import defaultdict
from typing import Optional

import games

DEFAULT_ROOM = "default"
PORT = int(os.environ.get("SSHCHAT_PORT", "12345"))

# conn -> {"name", "rooms", "current_room"}
clients = {}
# room -> set of conn
rooms = defaultdict(set)
# room -> conn of owner (first joiner; default room = first TCP client in #default)
room_owners: dict[str, object] = {}
# room -> announcement text (shown to everyone entering the room)
room_announcements: dict[str, str] = {}
# room -> active game session (e.g. games.ChessGame); at most one per room
room_games: dict[str, object] = {}
lock = threading.Lock()

ROOM_RE = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")
MAX_ANNOUNCE_LEN = 400
_DISCONNECT_ERRNOS = {32, 54, 57, 104}

# VT100: clear display + cursor home; trailing \n so line-oriented clients flush it.
_CLEAR_SCREEN = "\x1b[2J\x1b[H\n"
_SCREEN_CLEARED_ACK = "[*] Screen cleared.\n"

HELP_LINES = (
    "[*] ---------- SSHChat 命令说明 ----------\n",
    "[*] 普通文字（不以 / 开头）发到「当前活跃房间」，房内在线用户都会收到。\n",
    "[*]\n",
    "[*] /join <房间>     加入房间并立刻切到该房；若已在房内则只切换当前房。\n",
    "[*]              房间名：1～32 字符，仅字母、数字、下划线、连字符。\n",
    "[*] /switch <房间>  只在已加入的房间之间切换；未加入会提示先用 /join。\n",
    "[*] /part <房间>    退出某房间；至少保留一间，不能退出最后一个。\n",
    "[*] /rooms         列出你已加入的房间；前面带 * 的是当前活跃房间。\n",
    "[*] /names 或 /users  列出当前活跃房间内的昵称（二者相同）。\n",
    "[*]\n",
    "[*] /msg #<房间> <文字>   不切换当前房，把一句话发到指定房间（# 开头表示房间）。\n",
    "[*] /msg <昵称> <文字>   私聊：发给该昵称的在线用户（大小写不敏感）。\n",
    "[*]              若有多人同昵称，会全部收到；发件人会收到汇总提示。\n",
    "[*]\n",
    "[*] /clear 或 /cls  清屏（终端会清空显示；图形客户端会清空当前房间记录）。\n",
    "[*] /announce      查看当前房间公告；房主可用 /announce <文字> 设置，/announce clear 清除。\n",
    "[*]              房主：#default 为第一个进服用户；其它房间为第一个 /join 该房的用户。\n",
    "[*]\n",
    "[*] /game ...      房间小游戏（chess、gomoku）。/game list /new /join /seats /show /move /pgn /resign /abort /end。\n",
    "[*]              详细用法用 /game help 查看。\n",
    "[*] /help          显示本说明。\n",
    "[*]\n",
    "[*] 发 /file … 会提示不支持：本项目不在 SSH 会话里做文件传输。\n",
)


def _parse_handshake_line(raw: str) -> str:
    """First line: nickname only (optional tab suffix from old clients is ignored)."""
    line = raw.strip()
    if not line:
        return "Unknown"
    return line.split("\t", 1)[0].strip() or "Unknown"


def normalize_room(name: str) -> Optional[str]:
    name = name.strip()
    if not name or not ROOM_RE.match(name):
        return None
    return name


def _reassign_room_owner_locked(room: str, departed: object) -> None:
    """Must hold lock. departed left this room or disconnected."""
    if room_owners.get(room) != departed:
        return
    rem = rooms.get(room, ())
    if rem:
        room_owners[room] = next(iter(rem))
    else:
        room_owners.pop(room, None)


def send_room_announcement_preview(conn, room: str) -> None:
    """If the room has an announcement, show it to this client (after join/switch)."""
    with lock:
        text = (room_announcements.get(room) or "").strip()
    if not text:
        return
    send_line(conn, f"[#{room}] [*] 公告：{text}\n")


def _format_game_lines(room: str, lines) -> bytes:
    """Wrap each game-line with the standard [#room] [*] prefix as one byte blob."""
    return "".join(f"[#{room}] [*] {ln}\n" for ln in lines).encode("utf-8")


def send_game_private(conn, room: str, lines) -> None:
    if not lines:
        return
    send_line(conn, _format_game_lines(room, lines).decode("utf-8"))


def broadcast_game(room: str, lines) -> None:
    if not lines:
        return
    broadcast_room(room, _format_game_lines(room, lines))


def _drop_game_if_room_empty_locked(room: str) -> None:
    """Caller holds lock; drop the game session when the room has no clients."""
    if not rooms.get(room):
        room_games.pop(room, None)


def send_line(conn, text: str) -> None:
    try:
        conn.send(text.encode("utf-8"))
    except Exception as e:
        print(f"send_line error: {e!r}")
        remove_client(conn)


def send_private_messages(conn, sender_name: str, target_nick: str, text: str) -> None:
    """Deliver a private message to all matching nicks; echo status to sender."""
    targets = find_clients_by_nickname(target_nick)
    if not targets:
        send_line(
            conn,
            f"[*] No one online named {target_nick!r} (match is case-insensitive)\n",
        )
        return
    for peer_conn, peer_name in targets:
        send_line(peer_conn, f"[PM from {sender_name}] {text}\n")
    if len(targets) == 1:
        only = targets[0][1]
        send_line(conn, f"[*] PM → {only}: {text}\n")
    else:
        n = len(targets)
        send_line(
            conn,
            f"[*] PM sent to {n} users matching {target_nick!r}: {text}\n",
        )


def find_clients_by_nickname(nick: str) -> list[tuple]:
    """Return [(conn, display_name), ...] for online users matching nick (case-insensitive)."""
    key = nick.strip().lower()
    if not key:
        return []
    with lock:
        return [
            (c, clients[c]["name"])
            for c in list(clients)
            if c in clients and clients[c]["name"].lower() == key
        ]


def broadcast_room(room: str, msg: bytes, exclude_conn=None) -> None:
    with lock:
        targets = [
            c
            for c in list(rooms.get(room, ()))
            if c is not exclude_conn and c in clients
        ]
    dead = []
    for c in targets:
        try:
            c.send(msg)
        except Exception as e:
            print(f"broadcast send error: {e!r}")
            dead.append(c)
    for c in dead:
        remove_client(c)


def remove_client(conn) -> None:
    with lock:
        info = clients.pop(conn, None)
        if not info:
            try:
                conn.close()
            except Exception:
                pass
            return
        name = info["name"]
        joined_rooms = list(info["rooms"])
        game_notices: list[tuple[str, list[str]]] = []
        for room in joined_rooms:
            rooms[room].discard(conn)
            _reassign_room_owner_locked(room, conn)
            game = room_games.get(room)
            if game is not None:
                _, bcast, _ended = game.on_player_leave(conn, name)
                if bcast:
                    game_notices.append((room, bcast))
            _drop_game_if_room_empty_locked(room)
    for room in joined_rooms:
        leave_msg = f"[!] {name} left #{room}\n".encode("utf-8")
        broadcast_room(room, leave_msg)
    for room, lines in game_notices:
        broadcast_game(room, lines)
    try:
        conn.close()
    except Exception:
        pass


def handle_command(conn, payload: str) -> None:
    with lock:
        info = clients.get(conn)
        if not info:
            return
        name = info["name"]
        current_room = info["current_room"]

    parts = payload.split(None, 1)
    cmd = parts[0].lower() if parts else ""

    if cmd == "/join":
        if len(parts) < 2 or not parts[1].strip():
            send_line(conn, "[*] Usage: /join <room>\n")
            return
        new_room = normalize_room(parts[1])
        if not new_room:
            send_line(
                conn,
                "[*] Invalid room name (1–32 chars: letters, digits, _ -)\n",
            )
            return

        newly_joined = False
        with lock:
            if conn not in clients:
                return
            joined = clients[conn]["rooms"]
            prev_room = clients[conn]["current_room"]
            if new_room not in joined:
                was_empty = len(rooms[new_room]) == 0
                joined.add(new_room)
                rooms[new_room].add(conn)
                if was_empty:
                    room_owners[new_room] = conn
                newly_joined = True
            clients[conn]["current_room"] = new_room

        if newly_joined:
            broadcast_room(
                new_room,
                f"[+] {name} joined #{new_room}\n".encode("utf-8"),
                exclude_conn=conn,
            )
            send_line(
                conn,
                f"[*] Joined #{new_room} and switched from #{prev_room} to #{new_room}\n",
            )
            send_room_announcement_preview(conn, new_room)
        elif new_room == current_room:
            send_line(conn, f"[*] Already active in #{new_room}\n")
        else:
            send_line(conn, f"[*] Switched from #{current_room} to #{new_room}\n")
            send_room_announcement_preview(conn, new_room)
        return

    if cmd == "/switch":
        if len(parts) < 2 or not parts[1].strip():
            send_line(conn, "[*] Usage: /switch <room>\n")
            return
        target_room = normalize_room(parts[1])
        if not target_room:
            send_line(
                conn,
                "[*] Invalid room name (1–32 chars: letters, digits, _ -)\n",
            )
            return
        with lock:
            if conn not in clients:
                return
            joined = clients[conn]["rooms"]
            active = clients[conn]["current_room"]
            if target_room not in joined:
                send_line(conn, f"[*] You are not in #{target_room}. Use /join first.\n")
                return
            if target_room == active:
                send_line(conn, f"[*] Already active in #{target_room}\n")
                return
            clients[conn]["current_room"] = target_room
        send_line(conn, f"[*] Switched from #{active} to #{target_room}\n")
        send_room_announcement_preview(conn, target_room)
        return

    if cmd == "/msg":
        parts3 = payload.split(None, 2)
        if len(parts3) < 3 or not parts3[1].strip() or not parts3[2].strip():
            send_line(
                conn,
                "[*] Usage: /msg #<room> <text>  |  /msg <nick> <text>\n"
                "[*] (Room only if target starts with #; otherwise nick — same as irssi.)\n",
            )
            return
        target = parts3[1].strip()
        text = parts3[2].strip()
        if target.startswith("#"):
            target_room = normalize_room(target[1:])
            if not target_room:
                send_line(
                    conn,
                    "[*] Invalid room name (1–32 chars: letters, digits, _ -)\n",
                )
                return
            with lock:
                if conn not in clients:
                    return
                joined = clients[conn]["rooms"]
                if target_room not in joined:
                    send_line(
                        conn,
                        f"[*] You are not in #{target_room}. Use /join first.\n",
                    )
                    return
            line_out = f"[#{target_room}] [{name}] {text}\n".encode("utf-8")
            broadcast_room(target_room, line_out)
            return
        send_private_messages(conn, name, target, text)
        return

    if cmd == "/part":
        if len(parts) < 2 or not parts[1].strip():
            send_line(conn, "[*] Usage: /part <room>\n")
            return
        target_room = normalize_room(parts[1])
        if not target_room:
            send_line(
                conn,
                "[*] Invalid room name (1–32 chars: letters, digits, _ -)\n",
            )
            return
        switched_to = None
        with lock:
            if conn not in clients:
                return
            joined = clients[conn]["rooms"]
            active = clients[conn]["current_room"]
            if target_room not in joined:
                send_line(conn, f"[*] You are not in #{target_room}\n")
                return
            if len(joined) == 1:
                send_line(conn, "[*] Cannot leave your last room\n")
                return
            joined.remove(target_room)
            rooms[target_room].discard(conn)
            _reassign_room_owner_locked(target_room, conn)
            game_bcast: list[str] = []
            game = room_games.get(target_room)
            if game is not None:
                _, game_bcast, _ended = game.on_player_leave(conn, name)
            _drop_game_if_room_empty_locked(target_room)
            if active == target_room:
                switched_to = sorted(joined)[0]
                clients[conn]["current_room"] = switched_to
        if game_bcast:
            broadcast_game(target_room, game_bcast)
        broadcast_room(
            target_room,
            f"[!] {name} left #{target_room}\n".encode("utf-8"),
        )
        if switched_to:
            send_line(conn, f"[*] Left #{target_room}, switched to #{switched_to}\n")
        else:
            send_line(conn, f"[*] Left #{target_room}\n")
        return

    if cmd == "/rooms":
        with lock:
            if conn not in clients:
                return
            active = clients[conn]["current_room"]
            joined = sorted(clients[conn]["rooms"])
        labels = [f"*#{r}" if r == active else f"#{r}" for r in joined]
        send_line(conn, f"[*] Rooms: {', '.join(labels)}\n")
        return

    if cmd in ("/names", "/users"):
        with lock:
            r = clients[conn]["current_room"]
            members = sorted(
                clients[c]["name"] for c in rooms.get(r, ()) if c in clients
            )
        send_line(
            conn,
            f"[*] #{r} ({len(members)}): {', '.join(members) if members else '(empty)'}\n",
        )
        return

    if cmd in ("/clear", "/cls"):
        send_line(conn, _CLEAR_SCREEN)
        send_line(conn, _SCREEN_CLEARED_ACK)
        return

    if cmd == "/help":
        for hline in HELP_LINES:
            send_line(conn, hline)
        return

    if cmd == "/announce":
        tail = payload[len("/announce") :].strip()
        with lock:
            if conn not in clients:
                return
            room = clients[conn]["current_room"]
            is_owner = room_owners.get(room) == conn
        if not tail:
            with lock:
                cur = (room_announcements.get(room) or "").strip()
            if cur:
                send_line(conn, f"[*] #{room} 当前公告：{cur}\n")
            else:
                send_line(conn, f"[*] #{room} 暂无公告。\n")
            return
        if not is_owner:
            send_line(conn, "[*] 只有房主可以修改公告（查看无需权限）。\n")
            return
        if tail.lower() == "clear":
            with lock:
                room_announcements.pop(room, None)
            broadcast_room(
                room,
                f"[#{room}] [*] 公告已清除。\n".encode("utf-8"),
            )
            send_line(conn, f"[*] 已清除 #{room} 的公告。\n")
            return
        one_line = " ".join(tail.split())
        if len(one_line) > MAX_ANNOUNCE_LEN:
            send_line(
                conn,
                f"[*] 公告过长（最多 {MAX_ANNOUNCE_LEN} 字符）。\n",
            )
            return
        with lock:
            room_announcements[room] = one_line
        broadcast_room(
            room,
            f"[#{room}] [*] 公告：{one_line}\n".encode("utf-8"),
        )
        send_line(conn, f"[*] 已更新 #{room} 的公告。\n")
        return

    if cmd == "/game":
        _handle_game(conn, name, current_room, payload)
        return

    send_line(conn, "[*] Unknown command. Try /help\n")


def _handle_game(conn, name: str, room: str, payload: str) -> None:
    """All /game subcommands. Mutates room_games under the global lock."""
    raw = payload[len("/game") :].strip()
    if not raw or raw.lower() == "help":
        send_line(conn, "[*] /game 用法：\n")
        for ln in games.HELP_LINES:
            send_line(conn, ln + "\n")
        send_line(
            conn,
            "[*] 当前支持的游戏：" + ", ".join(sorted(games.GAMES)) + "\n",
        )
        return

    sub, _, rest = raw.partition(" ")
    sub = sub.lower()
    rest = rest.strip()

    if sub == "list":
        send_line(
            conn,
            "[*] 可玩游戏：" + ", ".join(sorted(games.GAMES)) + "\n",
        )
        return

    if sub == "new":
        game_name = rest.lower() or "chess"
        cls = games.GAMES.get(game_name)
        if cls is None:
            send_line(
                conn,
                f"[*] 未知游戏 {game_name!r}；/game list 查看可用。\n",
            )
            return
        with lock:
            existing = room_games.get(room)
            if existing is not None and existing.state != "ended":
                send_line(
                    conn,
                    f"[*] 本房已有进行中的对局（{existing.name}/"
                    f"{existing.state}）；/game end 由房主结束或先等当前局结束。\n",
                )
                return
            try:
                new_game = cls(conn, name)
            except RuntimeError as e:
                send_line(conn, f"[*] 无法开局：{e}\n")
                return
            room_games[room] = new_game
        broadcast_game(
            room,
            [
                f"{name} 开了一局 {game_name}（作为白方），"
                "等另一位玩家用 /game join 加入。",
            ]
            + new_game.show(),
        )
        return

    if sub == "join":
        with lock:
            game = room_games.get(room)
            if game is None:
                send_line(conn, "[*] 本房没有进行中的对局；用 /game new chess 开局。\n")
                return
            priv, bcast, _ = game.try_join(conn, name)
        send_game_private(conn, room, priv)
        broadcast_game(room, bcast)
        return

    if sub == "seats":
        with lock:
            game = room_games.get(room)
            lines = game.seats() if game else ["本房没有进行中的对局。"]
        send_game_private(conn, room, lines)
        return

    if sub == "show":
        with lock:
            game = room_games.get(room)
            lines = game.show() if game else ["本房没有进行中的对局。"]
        send_game_private(conn, room, lines)
        return

    if sub == "move":
        with lock:
            game = room_games.get(room)
            if game is None:
                send_line(conn, "[*] 本房没有进行中的对局。\n")
                return
            priv, bcast, ended = game.try_move(conn, rest)
        send_game_private(conn, room, priv)
        broadcast_game(room, bcast)
        return

    if sub == "resign":
        with lock:
            game = room_games.get(room)
            if game is None:
                send_line(conn, "[*] 本房没有进行中的对局。\n")
                return
            priv, bcast, _ = game.resign(conn, name)
        send_game_private(conn, room, priv)
        broadcast_game(room, bcast)
        return

    if sub == "abort":
        with lock:
            game = room_games.get(room)
            if game is None:
                send_line(conn, "[*] 本房没有进行中的对局。\n")
                return
            priv, bcast, _ = game.abort(conn, name)
        send_game_private(conn, room, priv)
        broadcast_game(room, bcast)
        return

    if sub == "pgn":
        with lock:
            game = room_games.get(room)
            if game is None or not hasattr(game, "pgn_export"):
                lines = ["本房没有可导出 PGN 的对局（仅 chess 支持）。"]
            else:
                lines = game.pgn_export()
        send_game_private(conn, room, lines)
        return

    if sub == "end":
        with lock:
            game = room_games.get(room)
            is_owner = room_owners.get(room) is conn
            if game is None:
                send_line(conn, "[*] 本房没有进行中的对局。\n")
                return
            if not is_owner:
                send_line(conn, "[*] 只有房主可以 /game end。\n")
                return
            room_games.pop(room, None)
        broadcast_game(room, [f"{name}（房主）结束了本房的对局。"])
        return

    send_line(conn, f"[*] 未知子命令 /game {sub}；用 /game help 查看。\n")


def process_client_line(conn, raw_line: bytes) -> None:
    text = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
    if not text:
        return
    if text.startswith("[") and "] " in text:
        _, _, payload = text.partition("] ")
    else:
        payload = text
    if not payload:
        return

    with lock:
        info = clients.get(conn)
        if not info:
            return
        room = info["current_room"]
        name = info["name"]

    if payload.startswith("/file "):
        send_line(conn, "[*] File transfer is not supported.\n")
        return

    if payload.startswith("/"):
        handle_command(conn, payload)
        return

    line_out = f"[#{room}] [{name}] {payload}\n".encode("utf-8")
    broadcast_room(room, line_out)


def handle_client(conn, addr) -> None:
    buffer = b""
    try:
        while b"\n" not in buffer:
            try:
                chunk = conn.recv(1024)
            except OSError as e:
                if getattr(e, "errno", None) in _DISCONNECT_ERRNOS:
                    return
                raise
            if not chunk:
                return
            buffer += chunk
        first, buffer = buffer.split(b"\n", 1)
        name = _parse_handshake_line(first.decode("utf-8", errors="replace"))

        with lock:
            was_empty_default = len(rooms[DEFAULT_ROOM]) == 0
            clients[conn] = {
                "name": name,
                "rooms": {DEFAULT_ROOM},
                "current_room": DEFAULT_ROOM,
            }
            rooms[DEFAULT_ROOM].add(conn)
            if was_empty_default:
                room_owners[DEFAULT_ROOM] = conn

        print(f"{name} joined #{DEFAULT_ROOM} (tcp_peer={addr[0]!r}:{addr[1]})")

        join_msg = f"[+] {name} joined #{DEFAULT_ROOM}\n".encode("utf-8")
        broadcast_room(DEFAULT_ROOM, join_msg, exclude_conn=conn)
        send_line(
            conn,
            f"[*] Active room #{DEFAULT_ROOM}. "
            f"/names /rooms /join /switch /msg /part /announce /game /clear /help\n",
        )
        send_room_announcement_preview(conn, DEFAULT_ROOM)

        while True:
            if not buffer:
                try:
                    chunk = conn.recv(4096)
                except OSError as e:
                    if getattr(e, "errno", None) in _DISCONNECT_ERRNOS:
                        break
                    raise
                if not chunk:
                    break
                buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if line:
                    process_client_line(conn, line)

    except Exception as e:
        print("connection error:", e)
        traceback.print_exc()
    finally:
        remove_client(conn)


def main():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", PORT))
    s.listen()
    print(f"chat server started on port {PORT} (default room #{DEFAULT_ROOM})")

    while True:
        conn, addr = s.accept()
        threading.Thread(
            target=handle_client,
            args=(conn, addr),
            daemon=True,
        ).start()


if __name__ == "__main__":
    main()
