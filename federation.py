"""Inter-server federation: merge rooms and users across trusted SSHChat nodes.

Servers connect over TCP (direct or via SSH stdio forward). Topology is a
graph: only adjacent nodes exchange keys, but messages flood (with dedup) and
unicasts next-hop so A—B—C is enough for A and C to talk. Same nickname on
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
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Callable, Optional

PROTOCOL_VERSION = "1"
_DISCONNECT_ERRNOS = {32, 54, 57, 104}
_RECONNECT_DELAY = float(os.environ.get("SSHCHAT_FED_RECONNECT_SECONDS", "5"))
_PEERS_WATCH_SECONDS = float(os.environ.get("SSHCHAT_FED_PEERS_WATCH_SECONDS", "5"))
# Bound flood dedup memory (graph cycles / rebroadcast).
_SEEN_MAX = int(os.environ.get("SSHCHAT_FED_SEEN_MAX", "4096"))


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
        on_game_sync: Optional[Callable[..., None]] = None,
        on_game_end: Optional[Callable[[str, str], None]] = None,
        on_game_cmd: Optional[Callable[[str, str, str, str, str, str], None]] = None,
        on_game_priv: Optional[Callable[[str, str, list[str]], None]] = None,
        on_file_notice: Optional[Callable[[str, str, dict[str, Any]], None]] = None,
        on_peer_event: Optional[Callable[[str, str, str], None]] = None,
        on_game_request: Optional[Callable[[str, str], None]] = None,
        get_local_library: Optional[Callable[[], list[dict[str, Any]]]] = None,
        on_library_page_request: Optional[
            Callable[[str, str, str, int, str], None]
        ] = None,
        on_library_page_result: Optional[
            Callable[[str, str, dict[str, Any]], None]
        ] = None,
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
        self.on_file_notice = on_file_notice
        # event in {"up","down"}, peer_node, reporter_node
        self.on_peer_event = on_peer_event
        # peer_node, room — ask holders to re-push gsync for that room
        self.on_game_request = on_game_request
        self.get_local_library = get_local_library
        # owner_node, req_id, book_name, page, requester_node
        self.on_library_page_request = on_library_page_request
        # from_peer, req_id, payload(dict)
        self.on_library_page_result = on_library_page_result
        self.enabled = os.environ.get("SSHCHAT_FEDERATION_DISABLE", "").strip().lower() not in (
            "1",
            "true",
            "yes",
        )
        self._config_lock = threading.Lock()
        self._peer_configs: list[dict[str, Any]] = []
        self._peer_configs_by_id: dict[str, dict[str, Any]] = {}
        self._outbound_started: set[str] = set()
        self._peers: dict[str, _PeerLink] = {}
        # dest node_id -> next-hop peer (direct neighbor).
        self._routes: dict[str, str] = {}
        self._remote_users: dict[tuple[str, str], RemoteUser] = {}
        self._room_remotes: dict[str, set[tuple[str, str]]] = defaultdict(set)
        # node_id -> list of book metadata dicts
        self._remote_catalogs: dict[str, list[dict[str, Any]]] = {}
        self._seen_lock = threading.Lock()
        self._seen_keys: set[str] = set()
        self._seen_order: deque[str] = deque()
        self._listen_socket: Optional[socket.socket] = None
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._peers_mtime: Optional[float] = None
        # Load initial peer list (outbound threads start in start()).
        self._ingest_peer_configs(self._load_peers(), start_outbound=False)

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
        started = self._start_missing_outbound_loops()
        if _PEERS_WATCH_SECONDS > 0:
            wt = threading.Thread(
                target=self._peers_watch_loop, name="fed-peers-watch", daemon=True
            )
            wt.start()
            self._threads.append(wt)
        print(
            f"federation: node={self.node_id!r} listen=0.0.0.0:{self.port} "
            f"peers={len(self._peer_configs_by_id)} outbound_started={started}"
        )

    def stop(self) -> None:
        self._stop.set()
        sock = self._listen_socket
        if self._peers:
            for link in list(self._peers.values()):
                try:
                    link.close()
                except Exception:
                    pass
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def reload_peers(self) -> int:
        """Re-read peers.json: start new outbound peers and drop removed ones.

        Inbound trust is applied when admin-*-peer.sh updates authorized_keys;
        this picks up peers.json without a process restart. Removed peers are
        disconnected and announced (nodedown) when a live link existed.
        Returns how many new outbound loops were started.
        """
        if not self.enabled:
            return 0
        peers = self._load_peers()
        removed = self._ingest_peer_configs(peers, start_outbound=False)
        for nid in removed:
            self._drop_configured_peer(nid)
        started = self._start_missing_outbound_loops()
        path = _peers_path()
        try:
            self._peers_mtime = path.stat().st_mtime if path.is_file() else None
        except OSError:
            self._peers_mtime = None
        print(
            f"federation: reloaded peers.json "
            f"({len(self._peer_configs_by_id)} peer(s), "
            f"{started} new outbound, {len(removed)} removed)"
        )
        return started

    def _ingest_peer_configs(
        self, peers: list[dict[str, Any]], *, start_outbound: bool
    ) -> list[str]:
        """Replace peer config from peers.json. Returns node_ids that were dropped."""
        with self._config_lock:
            new_by_id: dict[str, dict[str, Any]] = {}
            for p in peers:
                if not isinstance(p, dict):
                    continue
                nid = str(p.get("node_id") or "").strip()
                if not nid or nid == self.node_id:
                    continue
                new_by_id[nid] = dict(p)
            removed = [nid for nid in self._peer_configs_by_id if nid not in new_by_id]
            self._peer_configs_by_id = new_by_id
            self._peer_configs = list(self._peer_configs_by_id.values())
        if start_outbound:
            self._start_missing_outbound_loops()
        return removed

    def _drop_configured_peer(self, node_id: str) -> None:
        """Admin removed a peer from peers.json: tear down link and announce."""
        node_id = str(node_id or "").strip()
        if not node_id:
            return
        link = self._peers.get(node_id)
        if link is not None:
            if self._unregister_peer(node_id, link):
                self._notify_peer_down(node_id)
            print(f"federation: peer {node_id!r} removed from config (link closed)")
            return
        # Not currently linked: still drop any presence learned for that origin.
        self._clear_origin(node_id)
        print(f"federation: peer {node_id!r} removed from config")

    def _start_missing_outbound_loops(self) -> int:
        """Spawn reconnect loops for peers this node should dial."""
        to_start: list[str] = []
        with self._config_lock:
            for nid, peer in self._peer_configs_by_id.items():
                if nid in self._outbound_started:
                    continue
                # Lower node_id initiates; higher waits for inbound.
                if self.node_id > nid:
                    self._outbound_started.add(nid)
                    print(
                        f"federation: peer {nid!r} registered "
                        f"(this node waits for inbound)"
                    )
                    continue
                self._outbound_started.add(nid)
                to_start.append(nid)
                # Keep a copy for the loop; it re-reads by id on each attempt.
                _ = peer
        started = 0
        for nid in to_start:
            ct = threading.Thread(
                target=self._outbound_loop,
                args=(nid,),
                name=f"fed-out-{nid}",
                daemon=True,
            )
            ct.start()
            self._threads.append(ct)
            started += 1
            print(f"federation: starting outbound loop toward {nid!r}")
        return started

    def _peers_watch_loop(self) -> None:
        """Pick up peers.json edits even if SIGHUP was not delivered."""
        path = _peers_path()
        try:
            self._peers_mtime = path.stat().st_mtime if path.is_file() else None
        except OSError:
            self._peers_mtime = None
        while not self._stop.is_set():
            time.sleep(max(1.0, _PEERS_WATCH_SECONDS))
            if self._stop.is_set():
                break
            try:
                mtime = path.stat().st_mtime if path.is_file() else None
            except OSError:
                continue
            if mtime is None:
                continue
            if self._peers_mtime is not None and mtime <= self._peers_mtime:
                continue
            self._peers_mtime = mtime
            try:
                self.reload_peers()
            except Exception as e:
                print(f"federation: peers watch reload error: {e!r}")

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

    def _remember_seen(self, key: str) -> bool:
        """Record a flood/unicast line. Returns True if it was already seen."""
        key = key.strip("\r\n")
        if not key:
            return True
        with self._seen_lock:
            if key in self._seen_keys:
                return True
            self._seen_keys.add(key)
            self._seen_order.append(key)
            while len(self._seen_order) > max(64, _SEEN_MAX):
                old = self._seen_order.popleft()
                self._seen_keys.discard(old)
            return False

    def _fanout(
        self,
        line: str,
        *,
        exclude_node: Optional[str] = None,
        exclude_nodes: Optional[set[str]] = None,
    ) -> None:
        """Send an already-formatted protocol line to all direct peers except exclusions."""
        if not self.enabled or not self._peers:
            return
        if not line.endswith("\n"):
            line = line + "\n"
        skip: set[str] = set()
        if exclude_node:
            skip.add(exclude_node)
        if exclude_nodes:
            skip.update(exclude_nodes)
        for node_id, link in list(self._peers.items()):
            if node_id in skip:
                continue
            link.send_line(line)

    def _learn_route(self, dest: str, via: str) -> None:
        dest = str(dest or "").strip()
        via = str(via or "").strip()
        if not dest or not via or dest == self.node_id:
            return
        # Prefer a direct edge when we have one.
        if dest in self._peers:
            self._routes[dest] = dest
            return
        self._routes[dest] = via

    def _link_toward(self, dest: str) -> Optional[_PeerLink]:
        dest = str(dest or "").strip()
        if not dest:
            return None
        direct = self._peers.get(dest)
        if direct is not None:
            return direct
        hop = self._routes.get(dest)
        if hop:
            return self._peers.get(hop)
        return None

    def _send_toward(
        self, dest: str, line: str, *, exclude_node: Optional[str] = None
    ) -> bool:
        link = self._link_toward(dest)
        if link is None:
            return False
        if exclude_node and link.node_id == exclude_node:
            return False
        if not line.endswith("\n"):
            line = line + "\n"
        link.send_line(line)
        return True

    def _clear_origin(self, origin: str) -> None:
        """Drop presence and routes for a destination node (multi-hop unreachable)."""
        origin = str(origin or "").strip()
        if not origin or origin == self.node_id:
            return
        self._routes.pop(origin, None)
        self._remote_catalogs.pop(origin, None)
        to_remove = [k for k in self._remote_users if k[0] == origin]
        for k in to_remove:
            user = self._remote_users.pop(k, None)
            if user:
                for room in list(user.rooms):
                    self._room_remotes[room].discard(k)
                    self.on_join_notice(
                        room,
                        f"[!] {user.name} left #{room} (node {origin} unreachable)\n".encode(
                            "utf-8"
                        ),
                    )

    def broadcast_room(self, room: str, msg: bytes, exclude_node: Optional[str] = None) -> None:
        if not self.enabled or not self._peers:
            return
        payload = base64.b64encode(msg).decode("ascii")
        line = f"msg\t{self.node_id}\t{room}\t{payload}\n"
        self._remember_seen(line)
        self._fanout(line, exclude_node=exclude_node)

    def notify_join(self, name: str, room: str) -> None:
        if not self.enabled or not self._peers:
            return
        line = f"join\t{self.node_id}\t{name}\t{room}\n"
        self._remember_seen(line)
        self._fanout(line)

    def notify_leave(self, name: str, room: str) -> None:
        if not self.enabled or not self._peers:
            return
        line = f"leave\t{self.node_id}\t{name}\t{room}\n"
        self._remember_seen(line)
        self._fanout(line)

    def notify_switch(self, name: str, room: str) -> None:
        if not self.enabled or not self._peers:
            return
        line = f"switch\t{self.node_id}\t{name}\t{room}\n"
        self._remember_seen(line)
        self._fanout(line)

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
            self._remember_seen(line)
            if self._send_toward(user.node_id, line):
                sent = True
        return sent

    def send_file_notice(
        self, to_nick: str, from_name: str, notice: dict[str, Any]
    ) -> bool:
        """Route a /sendfile download notice to remote user(s). Returns True if any sent.

        File bytes stay on the origin node's HTTP(S) endpoint; only the absolute
        download_url + key cross the federation link (same idea as PM).
        """
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
        try:
            blob = base64.b64encode(
                json.dumps(notice, ensure_ascii=False).encode("utf-8")
            ).decode("ascii")
        except (TypeError, ValueError):
            return False
        sent = False
        for user in targets:
            line = f"fnotice\t{self.node_id}\t{user.name}\t{from_name}\t{blob}\n"
            self._remember_seen(line)
            if self._send_toward(user.node_id, line):
                sent = True
        return sent

    def sync_game(
        self,
        room: str,
        authority: str,
        pickle_b64: str,
        conflict_token: str = "",
    ) -> None:
        if not self.enabled or not self._peers:
            return
        # Nonce so catch-up re-pushes are not dropped by ingress dedup when the
        # pickled state is unchanged (same room/authority/payload).
        nonce = str(time.time_ns())
        token = (conflict_token or "").strip() or authority
        line = (
            f"gsync\t{self.node_id}\t{room}\t{authority}\t{pickle_b64}\t{nonce}\t{token}\n"
        )
        self._remember_seen(line)
        self._fanout(line)

    def remote_library_catalogs(self) -> dict[str, list[dict[str, Any]]]:
        """Copy of peer catalogs for union listing."""
        return {node: list(rows) for node, rows in self._remote_catalogs.items()}

    def sync_library_catalog(self, books: Optional[list[dict[str, Any]]] = None) -> None:
        """Fan-out local library metadata (presence-style replace-by-origin)."""
        if not self.enabled or not self._peers:
            return
        if books is None:
            if self.get_local_library is None:
                return
            try:
                books = self.get_local_library()
            except Exception as e:
                print(f"federation: get_local_library error: {e!r}")
                return
        if not isinstance(books, list):
            return
        blob = json.dumps(books, ensure_ascii=False)
        b64 = base64.b64encode(blob.encode("utf-8")).decode("ascii")
        nonce = str(time.time_ns())
        line = f"lcatalog\t{self.node_id}\t{b64}\t{nonce}\n"
        self._remember_seen(line)
        self._fanout(line)

    def request_library_page(
        self, owner_node: str, req_id: str, book_name: str, page: int
    ) -> bool:
        """Ask owner_node for one page of book_name (0-based page)."""
        if not self.enabled:
            return False
        owner_node = str(owner_node or "").strip()
        req_id = str(req_id or "").strip()
        book_name = Path(str(book_name or "").strip()).name
        if not owner_node or not req_id or not book_name:
            return False
        try:
            page_i = int(page)
        except (TypeError, ValueError):
            return False
        line = (
            f"lpage\t{self.node_id}\t{owner_node}\t{req_id}\t"
            f"{book_name}\t{page_i}\n"
        )
        self._remember_seen(line)
        return self._send_toward(owner_node, line)

    def reply_library_page(
        self, requester_node: str, req_id: str, payload: dict[str, Any]
    ) -> bool:
        if not self.enabled:
            return False
        requester_node = str(requester_node or "").strip()
        req_id = str(req_id or "").strip()
        if not requester_node or not req_id or not isinstance(payload, dict):
            return False
        blob = base64.b64encode(
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
        ).decode("ascii")
        line = f"lpage_ok\t{self.node_id}\t{requester_node}\t{req_id}\t{blob}\n"
        self._remember_seen(line)
        return self._send_toward(requester_node, line)

    def request_game(self, room: str) -> None:
        """Ask peers that hold room's game to re-push a gsync snapshot."""
        if not self.enabled or not self._peers:
            return
        room = str(room or "").strip()
        if not room:
            return
        nonce = str(time.time_ns())
        line = f"greq\t{self.node_id}\t{room}\t{nonce}\n"
        self._remember_seen(line)
        self._fanout(line)

    def end_game(self, room: str, authority: str) -> None:
        if not self.enabled or not self._peers:
            return
        line = f"gend\t{self.node_id}\t{room}\t{authority}\n"
        self._remember_seen(line)
        self._fanout(line)

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
        safe_rest = rest.replace("\t", " ").replace("\n", " ")
        # Include authority so multi-hop relays can next-hop without flooding.
        line = (
            f"gcmd\t{self.node_id}\t{authority_node}\t{room}\t"
            f"{player_node}\t{name}\t{sub}\t{safe_rest}\n"
        )
        self._remember_seen(line)
        return self._send_toward(authority_node, line)

    def send_game_private_to(
        self, to_node: str, room: str, to_name: str, lines: list[str]
    ) -> None:
        if not self.enabled or not lines:
            return
        blob = base64.b64encode(
            json.dumps(lines, ensure_ascii=False).encode("utf-8")
        ).decode("ascii")
        # to_node kept in the line for multi-hop routing.
        line = f"gpriv\t{self.node_id}\t{to_node}\t{room}\t{to_name}\t{blob}\n"
        self._remember_seen(line)
        self._send_toward(to_node, line)

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

    def _register_peer(self, node_id: str, link: _PeerLink) -> bool:
        """Install link for node_id.

        Returns True when the peer newly became reachable (caller should announce
        up). Returns False when replacing an already-live link (silent swap), so
        reconnect races do not spam local users with down/up pairs.
        """
        old = self._peers.get(node_id)
        already_up = old is not None and old is not link and not old._closed
        # Install before closing the old link so a concurrent finally on the old
        # session sees the newer owner and skips tearing down presence/routes.
        self._peers[node_id] = link
        self._routes[node_id] = node_id
        if old is not None and old is not link:
            old.close()
        return not already_up

    def _notify_peer_up(self, peer_node: str) -> None:
        """Local peer just connected: tell local users and other online peers."""
        self._emit_peer_event("up", peer_node, reporter=self.node_id, relay=True)

    def _notify_peer_down(self, peer_node: str) -> None:
        """Local peer just disconnected: tell local users and other online peers."""
        self._emit_peer_event("down", peer_node, reporter=self.node_id, relay=True)

    def _emit_peer_event(
        self,
        event: str,
        peer_node: str,
        *,
        reporter: str,
        relay: bool,
        exclude_node: Optional[str] = None,
    ) -> None:
        peer_node = str(peer_node or "").strip()
        reporter = str(reporter or "").strip() or self.node_id
        if event not in ("up", "down") or not peer_node:
            return
        if self.on_peer_event is not None:
            try:
                self.on_peer_event(event, peer_node, reporter)
            except Exception as e:
                print(f"federation: on_peer_event error: {e!r}")
        if relay:
            self._broadcast_peer_event(
                event,
                peer_node,
                reporter,
                exclude_node=exclude_node if exclude_node is not None else peer_node,
            )

    def _broadcast_peer_event(
        self,
        event: str,
        peer_node: str,
        reporter: str,
        *,
        exclude_node: Optional[str] = None,
    ) -> None:
        if not self.enabled or not self._peers:
            return
        kind = "nodeup" if event == "up" else "nodedown"
        line = f"{kind}\t{reporter}\t{peer_node}\n"
        self._remember_seen(line)
        skip = {peer_node}
        if exclude_node:
            skip.add(exclude_node)
        self._fanout(line, exclude_nodes=skip)

    def _unregister_peer(
        self, node_id: str, link: Optional[_PeerLink] = None
    ) -> bool:
        # Only tear down if this session still owns the peer slot. Otherwise a
        # replaced/stale session would wipe presence learned on the newer link
        # (seen as one-way /names: initiator flaps, passive side looks fine).
        current = self._peers.get(node_id)
        if link is not None and current is not None and current is not link:
            return False
        old = self._peers.pop(node_id, None)
        if old is not None:
            old.close()
        # Drop this direct peer and every destination that was only reachable via it.
        lost = {node_id}
        for dest, hop in list(self._routes.items()):
            if hop == node_id or dest == node_id:
                lost.add(dest)
                self._routes.pop(dest, None)
        for origin in lost:
            self._remote_catalogs.pop(origin, None)
            to_remove = [k for k in self._remote_users if k[0] == origin]
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
        return old is not None

    def _push_presence(self, link: _PeerLink) -> None:
        """Send local + known remote online snapshots to a newly connected peer."""
        users = self.get_local_clients()
        blob = json.dumps(users, ensure_ascii=False)
        local_line = f"presence\t{self.node_id}\t{blob}\n"
        self._remember_seen(local_line)
        link.send_line(local_line)

        by_origin: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for u in self._remote_users.values():
            by_origin[u.node_id].append(
                {
                    "name": u.name,
                    "rooms": sorted(u.rooms),
                    "current_room": u.current_room,
                }
            )
        for origin, ulist in by_origin.items():
            if origin == link.node_id:
                continue
            remote_blob = json.dumps(ulist, ensure_ascii=False)
            line = f"presence\t{origin}\t{remote_blob}\n"
            self._remember_seen(line)
            link.send_line(line)
        self._push_library_catalog(link)

    def _push_library_catalog(self, link: _PeerLink) -> None:
        """Send local + known remote library catalogs to a newly connected peer."""
        books: list[dict[str, Any]] = []
        if self.get_local_library is not None:
            try:
                books = self.get_local_library() or []
            except Exception as e:
                print(f"federation: get_local_library error: {e!r}")
                books = []
        if not isinstance(books, list):
            books = []
        local_blob = base64.b64encode(
            json.dumps(books, ensure_ascii=False).encode("utf-8")
        ).decode("ascii")
        local_line = f"lcatalog\t{self.node_id}\t{local_blob}\t{time.time_ns()}\n"
        self._remember_seen(local_line)
        link.send_line(local_line)
        for origin, rows in self._remote_catalogs.items():
            if origin == link.node_id:
                continue
            remote_blob = base64.b64encode(
                json.dumps(rows, ensure_ascii=False).encode("utf-8")
            ).decode("ascii")
            line = f"lcatalog\t{origin}\t{remote_blob}\t{time.time_ns()}\n"
            self._remember_seen(line)
            link.send_line(line)

    def _forward_unicast_for_nick(
        self,
        line: str,
        nick: str,
        *,
        ingress: str,
        prefer_nodes: Optional[set[str]] = None,
    ) -> None:
        """Next-hop forward a unicast line toward remote users with this nick."""
        key = _nick_key(nick)
        targets = [
            u
            for u in self._remote_users.values()
            if _nick_key(u.name) == key
            and (prefer_nodes is None or u.node_id in prefer_nodes)
        ]
        sent_hops: set[str] = set()
        for user in targets:
            link = self._link_toward(user.node_id)
            if link is None or link.node_id == ingress:
                continue
            if link.node_id in sent_hops:
                continue
            sent_hops.add(link.node_id)
            link.send_line(line if line.endswith("\n") else line + "\n")

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
            if self._remember_seen(line):
                return
            origin, room, b64 = parts[1], parts[2], parts[3]
            if origin == self.node_id:
                return
            self._learn_route(origin, peer_node)
            try:
                msg = base64.b64decode(b64.encode("ascii"))
            except Exception:
                return
            self.on_room_msg(room, msg, peer_node)
            self._fanout(line + "\n", exclude_node=peer_node)
            return
        if kind == "join" and len(parts) >= 4:
            if self._remember_seen(line):
                return
            origin, name, room = parts[1], parts[2], parts[3]
            self._learn_route(origin, peer_node)
            self._remote_join(origin, name, room)
            self._fanout(line + "\n", exclude_node=peer_node)
            return
        if kind == "leave" and len(parts) >= 4:
            if self._remember_seen(line):
                return
            origin, name, room = parts[1], parts[2], parts[3]
            self._learn_route(origin, peer_node)
            self._remote_leave(origin, name, room)
            self._fanout(line + "\n", exclude_node=peer_node)
            return
        if kind == "switch" and len(parts) >= 4:
            if self._remember_seen(line):
                return
            origin, name, room = parts[1], parts[2], parts[3]
            self._learn_route(origin, peer_node)
            self._remote_switch(origin, name, room)
            self._fanout(line + "\n", exclude_node=peer_node)
            return
        if kind == "presence" and len(parts) >= 3:
            if self._remember_seen(line):
                return
            # Keep JSON blob intact even if it ever contains tabs.
            pres_parts = line.split("\t", 2)
            if len(pres_parts) >= 3:
                origin = pres_parts[1]
                self._learn_route(origin, peer_node)
                self._remote_presence_bulk(origin, pres_parts[2])
                self._fanout(line + "\n", exclude_node=peer_node)
            return
        if kind == "lcatalog" and len(parts) >= 3:
            if self._remember_seen(line):
                return
            cat_parts = line.split("\t", 3)
            if len(cat_parts) < 3:
                return
            origin, b64 = cat_parts[1], cat_parts[2]
            if origin == self.node_id:
                return
            self._learn_route(origin, peer_node)
            self._remote_library_bulk(origin, b64)
            self._fanout(line + "\n", exclude_node=peer_node)
            return
        if kind == "lpage":
            page_parts = line.split("\t", 5)
            if len(page_parts) < 6:
                return
            if self._remember_seen(line):
                return
            origin, owner, req_id, book_name, page_s = (
                page_parts[1],
                page_parts[2],
                page_parts[3],
                page_parts[4],
                page_parts[5],
            )
            if origin == self.node_id:
                return
            self._learn_route(origin, peer_node)
            try:
                page_i = int(str(page_s).strip())
            except ValueError:
                return
            if owner == self.node_id:
                if self.on_library_page_request is not None:
                    # Heavy book parses (large EPUB/PDF) must not block the
                    # federation I/O thread — that stalls the duplex link and
                    # SSH tunnels drop mid-request.
                    cb = self.on_library_page_request
                    args = (owner, req_id, book_name, page_i, origin)

                    def _run_library_page(
                        _cb=cb, _args=args, _req=req_id
                    ) -> None:
                        try:
                            _cb(*_args)
                        except Exception as e:
                            print(
                                f"federation: on_library_page_request "
                                f"error ({_req}): {e!r}"
                            )

                    threading.Thread(
                        target=_run_library_page,
                        name=f"fed-lpage-{req_id[:8]}",
                        daemon=True,
                    ).start()
            else:
                self._learn_route(owner, peer_node)
                self._send_toward(owner, line + "\n", exclude_node=peer_node)
            return
        if kind == "lpage_ok":
            ok_parts = line.split("\t", 4)
            if len(ok_parts) < 5:
                return
            if self._remember_seen(line):
                return
            origin, requester, req_id, b64 = (
                ok_parts[1],
                ok_parts[2],
                ok_parts[3],
                ok_parts[4],
            )
            if origin == self.node_id:
                return
            self._learn_route(origin, peer_node)
            if requester == self.node_id:
                try:
                    payload = json.loads(
                        base64.b64decode(b64.encode("ascii")).decode("utf-8")
                    )
                except Exception:
                    return
                if isinstance(payload, dict) and self.on_library_page_result is not None:
                    try:
                        self.on_library_page_result(origin, req_id, payload)
                    except Exception as e:
                        print(f"federation: on_library_page_result error: {e!r}")
            else:
                self._learn_route(requester, peer_node)
                self._send_toward(requester, line + "\n", exclude_node=peer_node)
            return
        if kind == "pm" and len(parts) >= 5:
            if self._remember_seen(line):
                return
            origin, to_name, from_name, b64 = parts[1], parts[2], parts[3], parts[4]
            if origin == self.node_id:
                return
            self._learn_route(origin, peer_node)
            try:
                text = base64.b64decode(b64.encode("ascii")).decode("utf-8")
            except Exception:
                return
            self.on_pm(to_name, from_name, text)
            self._forward_unicast_for_nick(line + "\n", to_name, ingress=peer_node)
            return
        if kind in ("nodeup", "nodedown") and len(parts) >= 3:
            if self._remember_seen(line):
                return
            reporter, subject = parts[1], parts[2]
            if reporter == self.node_id or subject == self.node_id:
                return
            self._learn_route(reporter, peer_node)
            event = "up" if kind == "nodeup" else "down"
            if event == "up":
                self._learn_route(subject, peer_node)
            else:
                # Only drop if this subject was reached via the ingress neighbor.
                if self._routes.get(subject) == peer_node and subject not in self._peers:
                    self._clear_origin(subject)
            self._emit_peer_event(
                event, subject, reporter=reporter, relay=False
            )
            self._fanout(
                line + "\n",
                exclude_nodes={peer_node, subject},
            )
            return
        if kind == "fnotice" and len(parts) >= 5 and self.on_file_notice:
            if self._remember_seen(line):
                return
            origin, to_name, from_name, b64 = parts[1], parts[2], parts[3], parts[4]
            if origin == self.node_id:
                return
            self._learn_route(origin, peer_node)
            try:
                notice = json.loads(
                    base64.b64decode(b64.encode("ascii")).decode("utf-8")
                )
            except Exception:
                return
            if isinstance(notice, dict):
                self.on_file_notice(to_name, from_name, notice)
            self._forward_unicast_for_nick(line + "\n", to_name, ingress=peer_node)
            return
        if kind == "gsync" and len(parts) >= 5 and self.on_game_sync:
            if self._remember_seen(line):
                return
            origin, room, authority, b64 = parts[1], parts[2], parts[3], parts[4]
            conflict_token = parts[6] if len(parts) >= 7 else authority
            if origin == self.node_id:
                return
            self._learn_route(origin, peer_node)
            if authority and authority != self.node_id:
                self._learn_route(authority, peer_node)
            self.on_game_sync(peer_node, room, authority, b64, conflict_token)
            self._fanout(line + "\n", exclude_node=peer_node)
            return
        if kind == "greq" and len(parts) >= 3:
            if self._remember_seen(line):
                return
            origin, room = parts[1], parts[2]
            if origin == self.node_id:
                return
            self._learn_route(origin, peer_node)
            if self.on_game_request is not None:
                try:
                    self.on_game_request(peer_node, room)
                except Exception as e:
                    print(f"federation: on_game_request error: {e!r}")
            self._fanout(line + "\n", exclude_node=peer_node)
            return
        if kind == "gend" and len(parts) >= 3 and self.on_game_end:
            if self._remember_seen(line):
                return
            origin, room, authority = parts[1], parts[2], parts[3]
            if origin == self.node_id:
                return
            self._learn_route(origin, peer_node)
            self.on_game_end(room, authority)
            self._fanout(line + "\n", exclude_node=peer_node)
            return

        # gcmd: new form includes authority for multi-hop next-hop.
        #   gcmd\torigin\tauthority\troom\tplayer_node\tname\tsub\trest
        # legacy (direct-only):
        #   gcmd\torigin\troom\tplayer_node\tname\tsub\trest
        parts7 = line.split("\t", 7)
        if parts7[0] == "gcmd" and self.on_game_cmd:
            if self._remember_seen(line):
                return
            if len(parts7) >= 8:
                origin, authority, room, player_node, pname, sub = (
                    parts7[1],
                    parts7[2],
                    parts7[3],
                    parts7[4],
                    parts7[5],
                    parts7[6],
                )
                rest = parts7[7] if len(parts7) > 7 else ""
            elif len(parts7) >= 6:
                # Legacy: treat room field as room; no authority → flood.
                origin, room, player_node, pname, sub = (
                    parts7[1],
                    parts7[2],
                    parts7[3],
                    parts7[4],
                    parts7[5],
                )
                rest = parts7[6] if len(parts7) > 6 else ""
                authority = ""
            else:
                return
            if origin == self.node_id:
                return
            self._learn_route(origin, peer_node)
            if authority == self.node_id or not authority:
                self.on_game_cmd(peer_node, room, player_node, pname, sub, rest)
            if authority and authority != self.node_id:
                self._learn_route(authority, peer_node)
                self._send_toward(authority, line + "\n", exclude_node=peer_node)
            elif not authority:
                self._fanout(line + "\n", exclude_node=peer_node)
            return

        # gpriv: new form gpriv\torigin\tto_node\troom\tto_name\tblob
        # legacy: gpriv\torigin\troom\tto_name\tblob
        parts5 = line.split("\t", 5)
        if parts5[0] == "gpriv" and self.on_game_priv:
            if self._remember_seen(line):
                return
            to_node = ""
            if len(parts5) >= 6:
                origin, to_node, room, pname, b64 = (
                    parts5[1],
                    parts5[2],
                    parts5[3],
                    parts5[4],
                    parts5[5],
                )
            elif len(parts5) >= 5:
                origin, room, pname, b64 = (
                    parts5[1],
                    parts5[2],
                    parts5[3],
                    parts5[4],
                )
            else:
                return
            if origin == self.node_id:
                return
            self._learn_route(origin, peer_node)
            deliver = (not to_node) or (to_node == self.node_id)
            if deliver:
                try:
                    lines = json.loads(
                        base64.b64decode(b64.encode("ascii")).decode("utf-8")
                    )
                except Exception:
                    return
                if isinstance(lines, list):
                    self.on_game_priv(room, pname, [str(x) for x in lines])
            if to_node and to_node != self.node_id:
                self._send_toward(to_node, line + "\n", exclude_node=peer_node)
            elif not to_node:
                self._forward_unicast_for_nick(line + "\n", pname, ingress=peer_node)
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
        print(
            f"federation: presence from {node_id}: "
            f"{len(users)} user(s) → tracking {sum(1 for k in self._remote_users if k[0] == node_id)}"
        )

    def _remote_library_bulk(self, node_id: str, b64: str) -> None:
        if node_id == self.node_id:
            return
        try:
            raw = base64.b64decode(b64.encode("ascii")).decode("utf-8")
            books = json.loads(raw)
        except Exception:
            return
        if not isinstance(books, list):
            return
        cleaned: list[dict[str, Any]] = []
        for item in books:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name or Path(name).name != name:
                continue
            ext = str(item.get("ext") or Path(name).suffix.lstrip(".")).lower()
            try:
                size = int(item.get("size_bytes") or 0)
            except (TypeError, ValueError):
                size = 0
            cleaned.append({"name": name, "ext": ext, "size_bytes": size})
        self._remote_catalogs[node_id] = cleaned
        print(
            f"federation: library from {node_id}: {len(cleaned)} book(s)"
        )

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
                is_new = self._register_peer(peer_node, link)
                _send(f"@fed-ok\t{self.node_id}\n".encode("utf-8"))
                self._push_presence(link)
                print(f"federation: peer {peer_node} connected from {addr[0]!r}:{addr[1]}")
                if is_new:
                    self._notify_peer_up(peer_node)

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
                current_before = self._peers.get(peer_node)
                removed = self._unregister_peer(peer_node, link)
                if link is None or current_before is link:
                    print(f"federation: peer {peer_node} disconnected")
                if removed:
                    self._notify_peer_down(peer_node)
            try:
                conn.close()
            except Exception:
                pass

    def _outbound_loop(self, node_id: str) -> None:
        node_id = str(node_id).strip()
        if not node_id or node_id == self.node_id:
            return
        # Avoid duplicate links when both nodes list each other: lower id initiates.
        if self.node_id > node_id:
            return
        while not self._stop.is_set():
            with self._config_lock:
                peer = dict(self._peer_configs_by_id.get(node_id) or {})
            if not peer:
                time.sleep(_RECONNECT_DELAY)
                continue
            existing = self._peers.get(node_id)
            if existing is not None and not existing._closed:
                # Already linked; do not open parallel outbound sessions.
                time.sleep(_RECONNECT_DELAY)
                continue
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
        # ssh -W requires the peer's authorized_keys to allow this port
        # (restrict,port-forwarding,permitopen="127.0.0.1:FED_PORT").
        cmd = [
            "ssh",
            "-i",
            key,
            "-p",
            str(ssh_port),
            "-T",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "ConnectTimeout=15",
            "-o",
            "ExitOnForwardFailure=yes",
            "-W",
            remote,
            target,
        ]
        try:
            return subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
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
        # proc.stdin is same socket for tcp mode; pipe for ssh forced-command.
        send_sock = proc.stdin

        def _send(data: bytes) -> None:
            if hasattr(send_sock, "sendall"):
                send_sock.sendall(data)
            else:
                send_sock.write(data)
                send_sock.flush()

        def _recv(n: int) -> bytes:
            if hasattr(conn, "recv"):
                return conn.recv(n)
            chunk = conn.read(n)
            return chunk if chunk is not None else b""

        hello = f"@fed\t{self.node_id}\n".encode("utf-8")
        _send(hello)
        buffer = b""
        link: Optional[_PeerLink] = None
        registered = False
        while not self._stop.is_set():
            if b"\n" not in buffer:
                try:
                    chunk = _recv(4096)
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
                is_new = self._register_peer(peer_node, link)
                registered = True
                # Apply any peer lines already buffered with @fed-ok (usually
                # their presence snapshot) BEFORE we announce ourselves. The
                # initiator otherwise often dies after pushing local presence
                # and never learns remote /names.
                while b"\n" in buffer:
                    line_b, buffer = buffer.split(b"\n", 1)
                    link.handle_line(
                        line_b.decode("utf-8", errors="replace")
                    )
                if not any(k[0] == peer_node for k in self._remote_users):
                    # Presence not buffered yet — wait briefly for it.
                    try:
                        try:
                            conn.settimeout(3.0)
                        except (OSError, AttributeError):
                            pass
                        while b"\n" not in buffer:
                            chunk = _recv(4096)
                            if not chunk:
                                break
                            buffer += chunk
                        if b"\n" in buffer:
                            line_b, buffer = buffer.split(b"\n", 1)
                            link.handle_line(
                                line_b.decode("utf-8", errors="replace")
                            )
                    except OSError as e:
                        print(
                            f"federation: waiting presence from {peer_node}: {e!r}"
                        )
                    finally:
                        try:
                            conn.settimeout(None)
                        except (OSError, AttributeError):
                            pass
                self._push_presence(link)
                print(f"federation: outbound connected to {peer_node}")
                if is_new:
                    self._notify_peer_up(peer_node)
                continue
            if link is not None:
                link.handle_line(line)
        if registered:
            if self._unregister_peer(peer_node, link):
                self._notify_peer_down(peer_node)
        # Surface ssh client errors (e.g. refused port forward) when the
        # session dies before a federation handshake completes.
        try:
            err = getattr(proc, "stderr", None)
            if err is not None and hasattr(err, "read"):
                try:
                    err_b = err.read() or b""
                except Exception:
                    err_b = b""
                if err_b and not registered:
                    msg = err_b.decode("utf-8", errors="replace").strip()
                    if msg:
                        print(f"federation: ssh stderr ({peer_node}): {msg[:500]}")
        except Exception:
            pass
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
    on_game_sync: Optional[Callable[..., None]] = None,
    on_game_end: Optional[Callable[[str, str], None]] = None,
    on_game_cmd: Optional[Callable[[str, str, str, str, str, str], None]] = None,
    on_game_priv: Optional[Callable[[str, str, list[str]], None]] = None,
    on_file_notice: Optional[Callable[[str, str, dict[str, Any]], None]] = None,
    on_peer_event: Optional[Callable[[str, str, str], None]] = None,
    on_game_request: Optional[Callable[[str, str], None]] = None,
    get_local_library: Optional[Callable[[], list[dict[str, Any]]]] = None,
    on_library_page_request: Optional[
        Callable[[str, str, str, int, str], None]
    ] = None,
    on_library_page_result: Optional[
        Callable[[str, str, dict[str, Any]], None]
    ] = None,
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
        on_file_notice,
        on_peer_event,
        on_game_request,
        get_local_library,
        on_library_page_request,
        on_library_page_result,
    )
    return _hub
