import os
import re
import socket
import threading
from collections import defaultdict
from typing import Optional

DEFAULT_ROOM = "default"
PORT = int(os.environ.get("SSHCHAT_PORT", "12345"))

# conn -> {"name": str, "room": str}
clients = {}
# room -> set of conn
rooms = defaultdict(set)
lock = threading.Lock()

ROOM_RE = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")


def normalize_room(name: str) -> Optional[str]:
    name = name.strip()
    if not name or not ROOM_RE.match(name):
        return None
    return name


def send_line(conn, text: str) -> None:
    try:
        conn.send(text.encode("utf-8"))
    except Exception:
        remove_client(conn)


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
        except Exception:
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
        room = info["room"]
        name = info["name"]
        rooms[room].discard(conn)
    leave_msg = f"[!] {name} left the chat\n".encode("utf-8")
    broadcast_room(room, leave_msg)
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
        room = info["room"]

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
        old_room = room
        if new_room == old_room:
            send_line(conn, f"[*] Already in #{new_room}\n")
            return
        with lock:
            if conn not in clients:
                return
            rooms[old_room].discard(conn)
            clients[conn]["room"] = new_room
            rooms[new_room].add(conn)
        broadcast_room(
            old_room,
            f"[!] {name} went to #{new_room}\n".encode("utf-8"),
        )
        broadcast_room(
            new_room,
            f"[+] {name} joined #{new_room}\n".encode("utf-8"),
            exclude_conn=conn,
        )
        send_line(conn, f"[*] You joined #{new_room}\n")
        return

    if cmd in ("/users", "/who"):
        with lock:
            r = clients[conn]["room"]
            members = sorted(
                clients[c]["name"] for c in rooms.get(r, ()) if c in clients
            )
        send_line(
            conn,
            f"[*] #{r} ({len(members)}): {', '.join(members) if members else '(empty)'}\n",
        )
        return

    if cmd == "/help":
        send_line(
            conn,
            "[*] /users — users in this room | /join <room> — switch room | /help\n",
        )
        return

    send_line(conn, "[*] Unknown command. Try /help\n")


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
        room = info["room"]
        name = info["name"]

    if payload.startswith("/"):
        handle_command(conn, payload)
        return

    line_out = f"[{name}] {payload}\n".encode("utf-8")
    broadcast_room(room, line_out)


def handle_client(conn, addr) -> None:
    buffer = b""
    try:
        while b"\n" not in buffer:
            chunk = conn.recv(1024)
            if not chunk:
                return
            buffer += chunk
        first, buffer = buffer.split(b"\n", 1)
        name = first.decode("utf-8").strip() or "Unknown"

        with lock:
            clients[conn] = {"name": name, "room": DEFAULT_ROOM}
            rooms[DEFAULT_ROOM].add(conn)

        print(f"{name} joined #{DEFAULT_ROOM} ({addr})")

        join_msg = f"[+] {name} joined #{DEFAULT_ROOM}\n".encode("utf-8")
        broadcast_room(DEFAULT_ROOM, join_msg, exclude_conn=conn)
        send_line(
            conn,
            f"[*] You are in #{DEFAULT_ROOM}. Commands: /users, /join <room>, /help\n",
        )

        while True:
            if not buffer:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if line:
                    process_client_line(conn, line)

    except Exception as e:
        print("connection error:", e)
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
