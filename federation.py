"""Inter-server federation: merge rooms and users across trusted SSHChat nodes.

Servers connect over TCP (direct or via SSH stdio forward). Same nickname on
different nodes is treated as one account; same room name shares messages.
"""

from __future__ import annotations

import base64
import json
import os
import re
import socket
import subprocess
import threading
import time
import traceback
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Optional

PROTOCOL_VERSION = "1"
_DISCONNECT_ERRNOS = {32, 54, 57, 104}
_RECONNECT_DELAY = float(os.environ.get("SSHCHAT_FED_RECONNECT_SECONDS", "5"))


def _node_id() -> str:
    raw = os.environ.get("SSHCHAT_NODE_ID", "").strip()
    if raw:
        return raw
    return socket.gethostname() or "sshchat-node"


def _federation_port(chat_port: int) -> int:
    raw = os.environ.get("SSHCHAT_FEDERATION_PORT", "").strip()
    if raw:
        return int(raw)
    return chat_port + 1


def _peers_path() -> Path:
    raw = os.environ.get("SSHCHAT_FEDERATION_PEERS", "").strip()
    if raw:
        return Path(raw)
    base = Path(__file__).resolve().parent
    return base / "federation" / "peers.json"


def _ssh_key_path() -> Path:
    raw = os.environ.get("SSHCHAT_FEDERATION_KEY", "").strip()
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parent / "federation" / "id_ed25519"


def _nick_key(name: str) -> str:
    return name.strip().lower()


class RemoteUser:
    """Presence for a user connected on a peer node."""

    __slots__ = ("node_id", "name", "rooms", "current_room")

    def __init__(
        self,
        node_id: str,
        name: str,
        rooms: Optional[set[str]] = None,
        current_room: str = "default",
    ) -> None:
        self.node_id = node_id
        self.name = name
        self.rooms = set(rooms or ())
        self.current_room = current_room


class _PeerLink:
    """One bidirectional federation link to a peer node."""

    def __init__(self, hub: FederationHub, node_id: str, send_fn: Callable[[bytes], None]) -> None:
        self.hub = hub
        self.node_id = node_id
        self._send_fn = send_fn
        self._closed = False

    def send_line(self, line: str) -> None:
        if self._closed:
            return
        try:
            self._send_fn(line.encode("utf-8"))
        except Exception as e:
            print(f"federation: send to {self.node_id} failed: {e!r}")
            self.close()

    def close(self) -> None:
        self._closed = True

    def handle_line(self, line: str) -> None:
        self.hub._on_peer_line(self.node_id, line)


class FederationHub:
    """Manages peer links and remote user presence."""

    def __init__(
        self,
        chat_port: int,
        lock: threading.Lock,
        on_room_msg: Callable[[str, bytes, str], None],
        on_join_notice: Callable[[str, bytes], None],
        on_pm: Callable[[str, str, str], None],
        get_local_clients: Callable[[], list[dict[str, Any]]],
        on_game_sync: Optional[Callable[[str, str, str, str], None]] = None,
        on_game_end: Optional[Callable[[str, str], None]] = None,
        on_game_cmd: Optional[Callable[[str, str, str, str, str, str], None]] = None,
        on_game_priv: Optional[Callable[[str, str, list[str]], None]] = None,
    ) -> None:
        self.node_id = _node_id()
        self.chat_port = chat_port
        self.port = _federation_port(chat_port)
        self.lock = lock
        self.on_room_msg = on_room_msg
        self.on_join_notice = on_join_notice
        self.on_pm = on_pm
        self.get_local_clients = get_local_clients
        self.on_game_sync = on_game_sync
        self.on_game_end = on_game_end
        self.on_game_cmd = on_game_cmd
        self.on_game_priv = on_game_priv
        self.enabled = os.environ.get("SSHCHAT_FEDERATION_DISABLE", "").strip().lower() not in (
            "1",
            "true",
            "yes",
        )
        self._peer_configs = self._load_peers()
        self._peers: dict[str, _PeerLink] = {}
        self._remote_users: dict[tuple[str, str], RemoteUser] = {}
        self._room_remotes: dict[str, set[tuple[str, str]]] = defaultdict(set)
        self._listen_socket: Optional[socket.socket] = None
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    @property
    def peer_count(self) -> int:
        return len(self._peers)

    def start(self) -> None:
        if not self.enabled:
            print("federation: disabled (SSHCHAT_FEDERATION_DISABLE=1)")
            return
        t = threading.Thread(target=self._listen_loop, name="fed-listen", daemon=True)
        t.start()
        self._threads.append(t)
        for peer in self._peer_configs:
            ct = threading.Thread(
                target=self._outbound_loop,
                args=(peer,),
                name=f"fed-out-{peer.get('node_id', '?')}",
                daemon=True,
            )
            ct.start()
            self._threads.append(ct)
        print(
            f"federation: node={self.node_id!r} listen=0.0.0.0:{self.port} "
            f"peers={len(self._peer_configs)}"
        )

    def stop(self) -> None:
        self._stop.set()
        sock = self._listen_socket
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _load_peers(self) -> list[dict[str, Any]]:
        path = _peers_path()
        if not path.is_file():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"federation: cannot read {path}: {e!r}")
            return []
        if not isinstance(data, list):
            return []
        return [p for p in data if isinstance(p, dict) and p.get("node_id")]

    def broadcast_room(self, room: str, msg: bytes, exclude_node: Optional[str] = None) -> None:
        if not self.enabled or not self._peers:
            return
        payload = base64.b64encode(msg).decode("ascii")
        line = f"msg\t{self.node_id}\t{room}\t{payload}\n"
        for node_id, link in list(self._peers.items()):
            if node_id != exclude_node:
                link.send_line(line)

    def notify_join(self, name: str, room: str) -> None:
        if not self.enabled or not self._peers:
            return
        line = f"join\t{self.node_id}\t{name}\t{room}\n"
        for link in self._peers.values():
            link.send_line(line)

    def notify_leave(self, name: str, room: str) -> None:
        if not self.enabled or not self._peers:
            return
        line = f"leave\t{self.node_id}\t{name}\t{room}\n"
        for link in self._peers.values():
            link.send_line(line)

    def notify_switch(self, name: str, room: str) -> None:
        if not self.enabled or not self._peers:
            return
        line = f"switch\t{self.node_id}\t{name}\t{room}\n"
        for link in self._peers.values():
            link.send_line(line)

    def send_pm(self, to_nick: str, from_name: str, text: str) -> bool:
        """Route PM to remote user(s) on peer nodes. Returns True if any sent."""
        if not self.enabled or not self._peers:
            return False
        key = _nick_key(to_nick)
        targets = [
            u
            for u in self._remote_users.values()
            if _nick_key(u.name) == key
        ]
        if not targets:
            return False
        payload = base64.b64encode(text.encode("utf-8")).decode("ascii")
        sent = False
        for user in targets:
            line = f"pm\t{self.node_id}\t{user.name}\t{from_name}\t{payload}\n"
            link = self._peers.get(user.node_id)
            if link is not None:
                link.send_line(line)
                sent = True
        return sent

    def sync_game(self, room: str, authority: str, pickle_b64: str) -> None:
        if not self.enabled or not self._peers:
            return
        line = f"gsync\t{self.node_id}\t{room}\t{authority}\t{pickle_b64}\n"
        for link in self._peers.values():
            link.send_line(line)

    def end_game(self, room: str, authority: str) -> None:
        if not self.enabled or not self._peers:
            return
        line = f"gend\t{self.node_id}\t{room}\t{authority}\n"
        for link in self._peers.values():
            link.send_line(line)

    def forward_game_cmd(
        self,
        authority_node: str,
        room: str,
        player_node: str,
        name: str,
        sub: str,
        rest: str,
    ) -> bool:
        if not self.enabled:
            return False
        link = self._peers.get(authority_node)
        if link is None:
            return False
        safe_rest = rest.replace("\t", " ").replace("\n", " ")
        line = f"gcmd\t{self.node_id}\t{room}\t{player_node}\t{name}\t{sub}\t{safe_rest}\n"
        link.send_line(line)
        return True

    def send_game_private_to(
        self, to_node: str, room: str, to_name: str, lines: list[str]
    ) -> None:
        if not self.enabled or not lines:
            return
        link = self._peers.get(to_node)
        if link is None:
            return
        blob = base64.b64encode(
            json.dumps(lines, ensure_ascii=False).encode("utf-8")
        ).decode("ascii")
        line = f"gpriv\t{self.node_id}\t{room}\t{to_name}\t{blob}\n"
        link.send_line(line)

    def rooms_for_name(self, name: str) -> set[str]:
        """Rooms occupied by same nickname on peer nodes (for session sync)."""
        key = _nick_key(name)
        rooms: set[str] = set()
        for u in self._remote_users.values():
            if _nick_key(u.name) == key:
                rooms.update(u.rooms)
        return rooms

    def active_room_for_name(self, name: str) -> Optional[str]:
        key = _nick_key(name)
        for u in self._remote_users.values():
            if _nick_key(u.name) == key:
                return u.current_room
        return None

    def has_remote_user(self, nick: str) -> bool:
        key = _nick_key(nick)
        return any(_nick_key(u.name) == key for u in self._remote_users.values())

    def names_in_room(self, room: str) -> list[str]:
        keys = self._room_remotes.get(room, ())
        return sorted({self._remote_users[k].name for k in keys if k in self._remote_users})

    def same_name_in_room(self, room: str, name: str, local_has_other: bool) -> bool:
        """True if same nickname exists on a peer in this room."""
        if local_has_other:
            return True
        key = _nick_key(name)
        for rk in self._room_remotes.get(room, ()):
            u = self._remote_users.get(rk)
            if u and _nick_key(u.name) == key:
                return True
        return False

    def _register_peer(self, node_id: str, link: _PeerLink) -> None:
        # Replace any existing link for this node. Closing the newcomer caused
        # reconnect flaps when a stale peer entry briefly overlapped a retry
        # (common over SSH tunnels / unstable paths).
        old = self._peers.get(node_id)
        if old is not None and old is not link:
            old.close()
        self._peers[node_id] = link

    def _unregister_peer(self, node_id: str) -> None:
        link = self._peers.pop(node_id, None)
        if link is not None:
            link.close()
        to_remove = [k for k in self._remote_users if k[0] == node_id]
        for k in to_remove:
            user = self._remote_users.pop(k, None)
            if user:
                for room in list(user.rooms):
                    self._room_remotes[room].discard(k)
                    self.on_join_notice(
                        room,
                        f"[!] {user.name} left #{room} (peer {node_id} disconnected)\n".encode(
                            "utf-8"
                        ),
                    )

    def _push_presence(self, link: _PeerLink) -> None:
        """Send local online users snapshot to a newly connected peer."""
        users = self.get_local_clients()
        blob = json.dumps(users, ensure_ascii=False)
        link.send_line(f"presence\t{self.node_id}\t{blob}\n")

    def _on_peer_line(self, peer_node: str, line: str) -> None:
        line = line.strip("\r\n")
        if not line or line.startswith("#"):
            return
        if line == "ping":
            link = self._peers.get(peer_node)
            if link:
                link.send_line("pong\n")
            return
        if line == "pong":
            return
        parts = line.split("\t", 4)
        if not parts:
            return
        kind = parts[0]
        if kind == "msg" and len(parts) >= 4:
            origin, room, b64 = parts[1], parts[2], parts[3]
            if origin == self.node_id:
                return
            try:
                msg = base64.b64decode(b64.encode("ascii"))
            except Exception:
                return
            self.on_room_msg(room, msg, peer_node)
            return
        if kind == "join" and len(parts) >= 4:
            self._remote_join(parts[1], parts[2], parts[3])
            return
        if kind == "leave" and len(parts) >= 4:
            self._remote_leave(parts[1], parts[2], parts[3])
            return
        if kind == "switch" and len(parts) >= 4:
            self._remote_switch(parts[1], parts[2], parts[3])
            return
        if kind == "presence" and len(parts) >= 3:
            self._remote_presence_bulk(parts[1], parts[2])
            return
        if kind == "pm" and len(parts) >= 5:
            origin, to_name, from_name, b64 = parts[1], parts[2], parts[3], parts[4]
            if origin == self.node_id:
                return
            try:
                text = base64.b64decode(b64.encode("ascii")).decode("utf-8")
            except Exception:
                return
            self.on_pm(to_name, from_name, text)
            return
        if kind == "gsync" and len(parts) >= 5 and self.on_game_sync:
            origin, room, authority, b64 = parts[1], parts[2], parts[3], parts[4]
            if origin == self.node_id:
                return
            self.on_game_sync(peer_node, room, authority, b64)
            return
        if kind == "gend" and len(parts) >= 3 and self.on_game_end:
            origin, room, authority = parts[1], parts[2], parts[3]
            if origin == self.node_id:
                return
            self.on_game_end(room, authority)
            return

        parts6 = line.split("\t", 6)
        if parts6[0] == "gcmd" and len(parts6) >= 6 and self.on_game_cmd:
            origin, room, player_node, pname, sub = (
                parts6[1],
                parts6[2],
                parts6[3],
                parts6[4],
                parts6[5],
            )
            rest = parts6[6] if len(parts6) > 6 else ""
            if origin == self.node_id:
                return
            self.on_game_cmd(peer_node, room, player_node, pname, sub, rest)
            return
        if parts6[0] == "gpriv" and len(parts6) >= 5 and self.on_game_priv:
            origin, room, pname, b64 = parts6[1], parts6[2], parts6[3], parts6[4]
            if origin == self.node_id:
                return
            try:
                lines = json.loads(base64.b64decode(b64.encode("ascii")).decode("utf-8"))
            except Exception:
                return
            if isinstance(lines, list):
                self.on_game_priv(room, pname, [str(x) for x in lines])
            return

    def _remote_key(self, node_id: str, name: str) -> tuple[str, str]:
        return (node_id, _nick_key(name))

    def _remote_join(self, node_id: str, name: str, room: str) -> None:
        if node_id == self.node_id:
            return
        rk = self._remote_key(node_id, name)
        user = self._remote_users.get(rk)
        if user is None:
            user = RemoteUser(node_id, name)
            self._remote_users[rk] = user
        was_in = room in user.rooms
        user.rooms.add(room)
        user.current_room = room
        self._room_remotes[room].add(rk)
        if not was_in:
            self.on_join_notice(
                room,
                f"[+] {name} joined #{room}\n".encode("utf-8"),
            )

    def _remote_leave(self, node_id: str, name: str, room: str) -> None:
        if node_id == self.node_id:
            return
        rk = self._remote_key(node_id, name)
        user = self._remote_users.get(rk)
        if user is None:
            return
        user.rooms.discard(room)
        self._room_remotes[room].discard(rk)
        if not user.rooms:
            self._remote_users.pop(rk, None)
        self.on_join_notice(
            room,
            f"[!] {name} left #{room}\n".encode("utf-8"),
        )

    def _remote_switch(self, node_id: str, name: str, room: str) -> None:
        if node_id == self.node_id:
            return
        rk = self._remote_key(node_id, name)
        user = self._remote_users.get(rk)
        if user is not None:
            user.current_room = room

    def _remote_presence_bulk(self, node_id: str, blob: str) -> None:
        if node_id == self.node_id:
            return
        try:
            users = json.loads(blob)
        except json.JSONDecodeError:
            return
        if not isinstance(users, list):
            return
        stale = [k for k in self._remote_users if k[0] == node_id]
        for k in stale:
            user = self._remote_users.pop(k, None)
            if user:
                for room in user.rooms:
                    self._room_remotes[room].discard(k)
        for item in users:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            rooms_raw = item.get("rooms") or []
            if not isinstance(rooms_raw, list):
                continue
            rooms = {str(r) for r in rooms_raw if r}
            current = str(item.get("current_room") or "default")
            rk = self._remote_key(node_id, name)
            user = RemoteUser(node_id, name, rooms, current)
            self._remote_users[rk] = user
            for room in rooms:
                self._room_remotes[room].add(rk)

    def _listen_loop(self) -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", self.port))
            s.listen(32)
            self._listen_socket = s
        except OSError as e:
            print(f"federation: cannot listen on port {self.port}: {e!r}")
            return
        while not self._stop.is_set():
            try:
                conn, addr = s.accept()
            except OSError:
                break
            threading.Thread(
                target=self._serve_inbound,
                args=(conn, addr),
                name=f"fed-in-{addr[0]}",
                daemon=True,
            ).start()

    def _serve_inbound(self, conn: socket.socket, addr) -> None:
        self._run_session(conn, addr, peer_hint=None)

    def _run_session(self, conn, addr, peer_hint: Optional[str]) -> None:
        buffer = b""
        peer_node: Optional[str] = peer_hint
        link: Optional[_PeerLink] = None
        try:
            while peer_node is None:
                if b"\n" not in buffer:
                    chunk = conn.recv(4096)
                    if not chunk:
                        return
                    buffer += chunk
                    if len(buffer) > 65536:
                        return
                line_b, buffer = buffer.split(b"\n", 1)
                line = line_b.decode("utf-8", errors="replace").strip()
                if not line.startswith("@fed"):
                    return
                parts = line.split("\t")
                if len(parts) < 2:
                    return
                remote_id = parts[1].strip()
                if not remote_id or remote_id == self.node_id:
                    return
                peer_node = remote_id

                def _send(data: bytes, _c=conn) -> None:
                    _c.sendall(data)

                link = _PeerLink(self, peer_node, _send)
                self._register_peer(peer_node, link)
                _send(f"@fed-ok\t{self.node_id}\n".encode("utf-8"))
                self._push_presence(link)
                print(f"federation: peer {peer_node} connected from {addr[0]!r}:{addr[1]}")

            assert link is not None and peer_node is not None
            while not self._stop.is_set():
                if b"\n" not in buffer:
                    try:
                        chunk = conn.recv(4096)
                    except OSError as e:
                        if getattr(e, "errno", None) in _DISCONNECT_ERRNOS:
                            break
                        raise
                    if not chunk:
                        break
                    buffer += chunk
                    if len(buffer) > 1048576:
                        break
                line_b, buffer = buffer.split(b"\n", 1)
                line = line_b.decode("utf-8", errors="replace")
                link.handle_line(line)
        except Exception as e:
            print(f"federation: session error ({peer_node}): {e!r}")
            traceback.print_exc()
        finally:
            if peer_node:
                self._unregister_peer(peer_node)
                print(f"federation: peer {peer_node} disconnected")
            try:
                conn.close()
            except Exception:
                pass

    def _outbound_loop(self, peer: dict[str, Any]) -> None:
        node_id = str(peer["node_id"]).strip()
        if not node_id or node_id == self.node_id:
            return
        # Avoid duplicate links when both nodes list each other: lower id initiates.
        if self.node_id > node_id:
            return
        while not self._stop.is_set():
            try:
                proc = self._open_outbound(peer)
                if proc is None:
                    time.sleep(_RECONNECT_DELAY)
                    continue
                self._run_stdio_session(proc, node_id)
            except Exception as e:
                print(f"federation: outbound to {node_id} error: {e!r}")
            if not self._stop.is_set():
                time.sleep(_RECONNECT_DELAY)

    def _open_outbound(self, peer: dict[str, Any]) -> Optional[subprocess.Popen]:
        node_id = str(peer["node_id"]).strip()
        host = str(peer.get("host") or "").strip()
        if not host:
            return None
        fed_port = int(peer.get("federation_port") or self.port)
        mode = str(peer.get("mode") or "ssh").strip().lower()

        if mode == "tcp":
            sock = socket.create_connection((host, fed_port), timeout=15)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            return self._wrap_socket_proc(sock)

        ssh_port = int(peer.get("ssh_port") or 22)
        ssh_user = str(peer.get("ssh_user") or "sshchat-federation").strip()
        key = str(peer.get("ssh_key") or _ssh_key_path())
        target = f"{ssh_user}@{host}"
        remote = f"127.0.0.1:{fed_port}"
        cmd = [
            "ssh",
            "-i",
            key,
            "-p",
            str(ssh_port),
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "ConnectTimeout=15",
            "-W",
            remote,
            target,
        ]
        try:
            return subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError as e:
            print(f"federation: ssh to {node_id} ({target}) failed: {e!r}")
            return None

    def _wrap_socket_proc(self, sock: socket.socket) -> subprocess.Popen:
        """Adapt a connected socket to Popen-like stdin/stdout for _run_stdio_session."""

        class _SockProc:
            stdin = sock
            stdout = sock

            def poll(self):
                return None

            def wait(self, timeout=None):
                return 0

            def terminate(self):
                try:
                    sock.close()
                except OSError:
                    pass

            def kill(self):
                self.terminate()

        return _SockProc()  # type: ignore[return-value]

    def _run_stdio_session(self, proc, peer_node: str) -> None:
        assert proc.stdin and proc.stdout
        conn = proc.stdout
        # proc.stdin is same socket for tcp mode
        send_sock = proc.stdin

        def _send(data: bytes) -> None:
            send_sock.sendall(data)

        hello = f"@fed\t{self.node_id}\n".encode("utf-8")
        _send(hello)
        buffer = b""
        link: Optional[_PeerLink] = None
        registered = False
        while not self._stop.is_set():
            if b"\n" not in buffer:
                try:
                    chunk = conn.recv(4096)
                except OSError as e:
                    if getattr(e, "errno", None) in _DISCONNECT_ERRNOS:
                        break
                    raise
                if not chunk:
                    break
                buffer += chunk
                if len(buffer) > 1048576:
                    break
            line_b, buffer = buffer.split(b"\n", 1)
            line = line_b.decode("utf-8", errors="replace").strip()
            if not registered:
                if not line.startswith("@fed-ok"):
                    break
                parts = line.split("\t")
                link = _PeerLink(self, peer_node, _send)
                self._register_peer(peer_node, link)
                registered = True
                self._push_presence(link)
                print(f"federation: outbound connected to {peer_node}")
                continue
            if link is not None:
                link.handle_line(line)
        if registered:
            self._unregister_peer(peer_node)
        try:
            proc.terminate()
        except Exception:
            pass


_hub: Optional[FederationHub] = None


def get_hub() -> Optional[FederationHub]:
    return _hub


def init_hub(
    chat_port: int,
    lock: threading.Lock,
    on_room_msg: Callable[[str, bytes, str], None],
    on_join_notice: Callable[[str, bytes], None],
    on_pm: Callable[[str, str, str], None],
    get_local_clients: Callable[[], list[dict[str, Any]]],
    on_game_sync: Optional[Callable[[str, str, str, str], None]] = None,
    on_game_end: Optional[Callable[[str, str], None]] = None,
    on_game_cmd: Optional[Callable[[str, str, str, str, str, str], None]] = None,
    on_game_priv: Optional[Callable[[str, str, list[str]], None]] = None,
) -> FederationHub:
    global _hub
    _hub = FederationHub(
        chat_port,
        lock,
        on_room_msg,
        on_join_notice,
        on_pm,
        get_local_clients,
        on_game_sync,
        on_game_end,
        on_game_cmd,
        on_game_priv,
    )
    return _hub
