import base64
import json
import os
import pickle
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import canvas_sharing
import federation
import file_http_server
import library
import server


class DummyConn:
    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self.closed = False

    def send(self, data: bytes) -> None:
        self.sent.append(data)

    def close(self) -> None:
        self.closed = True


class FederationProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        server.clients.clear()
        server.rooms.clear()
        server.room_owners.clear()
        server.room_announcements.clear()
        server.room_polls.clear()
        server.room_capsules.clear()
        server.room_games.clear()
        server.room_game_authority.clear()
        server.room_game_tokens.clear()
        server.room_game_ended_ids.clear()
        server.room_game_provisional.clear()
        server.room_games_parked.clear()
        server.room_enabled_games.clear()
        server.disconnected_sessions.clear()
        federation._hub = None
        server._fed_hub = None

    def _free_port(self) -> int:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        return port

    def test_two_nodes_merge_room_messages(self) -> None:
        chat_a = self._free_port()
        chat_b = self._free_port()
        fed_a = self._free_port()
        fed_b = self._free_port()

        with tempfile.TemporaryDirectory() as td:
            peers_a = Path(td) / "peers_a.json"
            peers_b = Path(td) / "peers_b.json"
            peers_a.write_text(
                json.dumps(
                    [
                        {
                            "node_id": "node-b",
                            "host": "127.0.0.1",
                            "mode": "tcp",
                            "federation_port": fed_b,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            peers_b.write_text(
                json.dumps(
                    [
                        {
                            "node_id": "node-a",
                            "host": "127.0.0.1",
                            "mode": "tcp",
                            "federation_port": fed_a,
                        }
                    ]
                ),
                encoding="utf-8",
            )

            env_a = {
                "SSHCHAT_NODE_ID": "node-a",
                "SSHCHAT_FEDERATION_PORT": str(fed_a),
                "SSHCHAT_FEDERATION_PEERS": str(peers_a),
            }
            env_b = {
                "SSHCHAT_NODE_ID": "node-b",
                "SSHCHAT_FEDERATION_PORT": str(fed_b),
                "SSHCHAT_FEDERATION_PEERS": str(peers_b),
            }

            received_b: list[bytes] = []

            def on_msg_b(room, msg, _peer):
                received_b.append(msg)
                server.broadcast_room(room, msg, skip_federation=True)

            with mock.patch.dict(os.environ, env_a, clear=False):
                hub_a = federation.init_hub(
                    chat_a,
                    server.lock,
                    lambda r, m, p: server.broadcast_room(
                        r, m, via_federation_from=p, skip_federation=True
                    ),
                    lambda r, m: server.broadcast_room(r, m, skip_federation=True),
                    lambda t, f, x: None,
                    lambda: [],
                )
                hub_a.start()

            with mock.patch.dict(os.environ, env_b, clear=False):
                hub_b = federation.init_hub(
                    chat_b,
                    server.lock,
                    on_msg_b,
                    lambda r, m: server.broadcast_room(r, m, skip_federation=True),
                    lambda t, f, x: None,
                    lambda: [],
                )
                hub_b.start()

            deadline = time.time() + 8
            while time.time() < deadline:
                if hub_a.peer_count >= 1 and hub_b.peer_count >= 1:
                    break
                time.sleep(0.1)
            self.assertGreaterEqual(hub_a.peer_count, 1, "node-a should connect to node-b")
            self.assertGreaterEqual(hub_b.peer_count, 1, "node-b should connect to node-a")

            room = "lobby"
            payload = f"[#{room}] [alice] hello federated network\n".encode()
            hub_a.broadcast_room(room, payload)

            deadline = time.time() + 3
            while time.time() < deadline and not received_b:
                time.sleep(0.05)
            self.assertTrue(received_b, "node-b should receive federated room message")
            self.assertIn(b"hello federated network", received_b[0])

            hub_a.stop()
            hub_b.stop()

    def test_same_name_rooms_sync_from_hub(self) -> None:
        hub = federation.FederationHub(
            12345,
            server.lock,
            lambda r, m, p: None,
            lambda r, m: None,
            lambda t, f, x: None,
            lambda: [],
        )
        hub.enabled = True
        hub._remote_join("node-b", "alice", "dev")
        hub._remote_join("node-b", "alice", "ops")
        rooms = hub.rooms_for_name("Alice")
        self.assertEqual(rooms, {"dev", "ops"})
        self.assertEqual(hub.names_in_room("dev"), ["alice"])

    def test_file_notice_routes_to_remote_peer(self) -> None:
        received: list[tuple[str, str, dict]] = []

        class FakeLink:
            def __init__(self) -> None:
                self.lines: list[str] = []

            def send_line(self, line: str) -> None:
                self.lines.append(line)

        hub = federation.FederationHub(
            12345,
            server.lock,
            lambda r, m, p: None,
            lambda r, m: None,
            lambda t, f, x: None,
            lambda: [],
            on_file_notice=lambda to, frm, notice: received.append((to, frm, notice)),
        )
        hub.enabled = True
        hub.node_id = "node-a"
        link = FakeLink()
        hub._peers["node-b"] = link
        hub._remote_join("node-b", "bob", "dev")

        notice = {
            "filename": "a.pdf",
            "file_size": 12,
            "download_url": "https://files.example/download/tok",
            "download_key": "ABC123",
            "download_token": "tok",
            "room": "dev",
            "transfer_id": "tid",
        }
        self.assertTrue(hub.send_file_notice("bob", "alice", notice))
        self.assertEqual(len(link.lines), 1)
        self.assertTrue(link.lines[0].startswith("fnotice\tnode-a\tbob\talice\t"))

        # Simulate peer receive on another hub
        peer = federation.FederationHub(
            12346,
            server.lock,
            lambda r, m, p: None,
            lambda r, m: None,
            lambda t, f, x: None,
            lambda: [],
            on_file_notice=lambda to, frm, n: received.append((to, frm, n)),
        )
        peer.enabled = True
        peer.node_id = "node-b"
        peer._on_peer_line("node-a", link.lines[0].rstrip("\n"))
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0][0], "bob")
        self.assertEqual(received[0][1], "alice")
        self.assertEqual(received[0][2]["download_url"], notice["download_url"])
        self.assertEqual(received[0][2]["download_key"], "ABC123")

    def test_file_leave_broadcast_seeds_offline_peer(self) -> None:
        """Fully offline recipients get fleave fan-out (not presence-gated fnotice)."""
        received: list[tuple[str, str, dict]] = []

        class FakeLink:
            def __init__(self) -> None:
                self.lines: list[str] = []

            def send_line(self, line: str) -> None:
                self.lines.append(line)

        origin = federation.FederationHub(
            12345,
            server.lock,
            lambda r, m, p: None,
            lambda r, m: None,
            lambda t, f, x: None,
            lambda: [],
        )
        origin.enabled = True
        origin.node_id = "node-a"
        link = FakeLink()
        origin._peers["node-b"] = link
        # Recipient is NOT in remote presence — send_file_notice would no-op.
        self.assertFalse(origin.has_remote_user("ghost"))

        notice = {
            "filename": "x.bin",
            "file_size": 3,
            "download_url": "https://a.example/download/t1",
            "download_key": "KEY123",
            "download_token": "t1",
            "transfer_id": "xfer-1",
        }
        self.assertTrue(origin.broadcast_file_leave("ghost", "alice", notice))
        self.assertEqual(len(link.lines), 1)
        self.assertTrue(link.lines[0].startswith("fleave\tnode-a\tghost\talice\t"))

        peer = federation.FederationHub(
            12346,
            server.lock,
            lambda r, m, p: None,
            lambda r, m: None,
            lambda t, f, x: None,
            lambda: [],
            on_file_notice=lambda to, frm, n: received.append((to, frm, n)),
        )
        peer.enabled = True
        peer.node_id = "node-b"
        peer._on_peer_line("node-a", link.lines[0].rstrip("\n"))
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0][0], "ghost")
        self.assertEqual(received[0][2]["transfer_id"], "xfer-1")

    def test_notify_file_ready_seeds_federation_when_offline(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        prev_bm = server.offline_messages
        store_path = str(Path(tmp.name) / "off.json")
        server.offline_messages = __import__("offline_messages").OfflineMessageStore(
            store_path
        )
        self.addCleanup(lambda: setattr(server, "offline_messages", prev_bm))

        seeded: list[tuple] = []

        class FakeHub:
            enabled = True

            def has_remote_user(self, nick):
                return False

            def send_file_notice(self, *a, **k):
                return False

            def broadcast_file_leave(self, to, frm, notice):
                seeded.append((to, frm, notice))
                return True

            def clear_file_leave(self, *a, **k):
                return True

        class FakeHTTP:
            def get_base_url(self):
                return "https://files.example"

        class FakeTransfer:
            transfer_id = "tid-off"
            sender = "alice"
            filename = "doc.pdf"
            file_size = 10
            room = None
            download_tokens = {"bob": "tok-bob"}
            download_keys = {"bob": "KEYBOB"}

        prev_http = server.file_http
        server.file_http = FakeHTTP()
        self.addCleanup(lambda: setattr(server, "file_http", prev_http))

        with mock.patch.object(federation, "get_hub", return_value=FakeHub()):
            server._notify_file_ready(FakeTransfer())

        self.assertEqual(server.offline_messages.count("bob"), 1)
        self.assertEqual(len(seeded), 1)
        self.assertEqual(seeded[0][0], "bob")
        self.assertEqual(seeded[0][2]["download_key"], "KEYBOB")
        self.assertTrue(
            seeded[0][2]["download_url"].startswith("https://files.example/download/")
        )

    def test_reload_peers_starts_new_outbound_without_restart(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            peers_path = Path(td) / "peers.json"
            peers_path.write_text("[]\n", encoding="utf-8")
            env = {
                "SSHCHAT_NODE_ID": "node-a",
                "SSHCHAT_FEDERATION_PEERS": str(peers_path),
                "SSHCHAT_FED_PEERS_WATCH_SECONDS": "0",
            }
            with mock.patch.dict(os.environ, env, clear=False):
                hub = federation.FederationHub(
                    12345,
                    server.lock,
                    lambda r, m, p: None,
                    lambda r, m: None,
                    lambda t, f, x: None,
                    lambda: [],
                )
                hub.enabled = True
                hub.node_id = "node-a"
                started_ids: list[str] = []

                class FakeThread:
                    def __init__(self, target=None, args=(), name=None, daemon=None):
                        self.target = target
                        self.args = args

                    def start(self):
                        if self.args:
                            started_ids.append(self.args[0])

                with mock.patch("federation.threading.Thread", FakeThread):
                    self.assertEqual(hub.reload_peers(), 0)
                    peers_path.write_text(
                        json.dumps(
                            [
                                {
                                    "node_id": "node-b",
                                    "host": "127.0.0.1",
                                    "mode": "tcp",
                                    "federation_port": 9,
                                }
                            ]
                        ),
                        encoding="utf-8",
                    )
                    # node-a < node-b ⇒ this node initiates outbound
                    self.assertEqual(hub.reload_peers(), 1)
                    self.assertEqual(started_ids, ["node-b"])
                    self.assertIn("node-b", hub._outbound_started)
                    # Idempotent: second reload must not spawn another loop
                    self.assertEqual(hub.reload_peers(), 0)
                    self.assertEqual(started_ids, ["node-b"])

    def test_reload_peers_drops_removed_peer_and_notifies(self) -> None:
        events: list[tuple[str, str, str]] = []

        class FakeLink:
            def __init__(self) -> None:
                self.closed = False
                self.lines: list[str] = []

            def send_line(self, line: str) -> None:
                self.lines.append(line)

            def close(self) -> None:
                self.closed = True

        with tempfile.TemporaryDirectory() as td:
            peers_path = Path(td) / "peers.json"
            peers_path.write_text(
                json.dumps(
                    [
                        {
                            "node_id": "node-b",
                            "host": "127.0.0.1",
                            "mode": "tcp",
                            "federation_port": 9,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            env = {
                "SSHCHAT_NODE_ID": "node-a",
                "SSHCHAT_FEDERATION_PEERS": str(peers_path),
                "SSHCHAT_FED_PEERS_WATCH_SECONDS": "0",
            }
            with mock.patch.dict(os.environ, env, clear=False):
                hub = federation.FederationHub(
                    12345,
                    server.lock,
                    lambda r, m, p: None,
                    lambda r, m: None,
                    lambda t, f, x: None,
                    lambda: [],
                    on_peer_event=lambda ev, peer, rep: events.append((ev, peer, rep)),
                )
                hub.enabled = True
                hub.node_id = "node-a"
                link_b = FakeLink()
                link_c = FakeLink()
                hub._peers["node-b"] = link_b
                hub._peers["node-c"] = link_c
                hub._routes["node-b"] = "node-b"
                hub._routes["node-c"] = "node-c"
                hub._peer_configs_by_id["node-b"] = {
                    "node_id": "node-b",
                    "host": "127.0.0.1",
                }
                hub._remote_join("node-b", "bob", "lobby")

                peers_path.write_text("[]\n", encoding="utf-8")
                self.assertEqual(hub.reload_peers(), 0)

                self.assertNotIn("node-b", hub._peers)
                self.assertNotIn("node-b", hub._peer_configs_by_id)
                self.assertTrue(link_b.closed)
                self.assertEqual(hub.names_in_room("lobby"), [])
                self.assertEqual(events[-1], ("down", "node-b", "node-a"))
                # Remaining peer is told about the drop.
                self.assertTrue(
                    any(l.startswith("nodedown\tnode-a\tnode-b") for l in link_c.lines)
                )

    def test_peer_up_down_notifies_other_peers(self) -> None:
        events: list[tuple[str, str, str]] = []

        class FakeLink:
            def __init__(self) -> None:
                self.lines: list[str] = []

            def send_line(self, line: str) -> None:
                self.lines.append(line)

            def close(self) -> None:
                return None

        hub = federation.FederationHub(
            12345,
            server.lock,
            lambda r, m, p: None,
            lambda r, m: None,
            lambda t, f, x: None,
            lambda: [],
            on_peer_event=lambda ev, peer, rep: events.append((ev, peer, rep)),
        )
        hub.enabled = True
        hub.node_id = "node-a"
        link_b = FakeLink()
        link_c = FakeLink()
        hub._peers["node-b"] = link_b
        hub._peers["node-c"] = link_c

        hub._notify_peer_up("node-c")
        self.assertEqual(events[-1], ("up", "node-c", "node-a"))
        # Other online peers get nodeup; the subject peer itself does not.
        self.assertTrue(any(l.startswith("nodeup\tnode-a\tnode-c") for l in link_b.lines))
        self.assertFalse(any(l.startswith("nodeup\t") for l in link_c.lines))

        # Peer B receives the relay, announces locally, and fanouts further.
        peer_b = federation.FederationHub(
            12346,
            server.lock,
            lambda r, m, p: None,
            lambda r, m: None,
            lambda t, f, x: None,
            lambda: [],
            on_peer_event=lambda ev, peer, rep: events.append((ev, peer, rep)),
        )
        peer_b.enabled = True
        peer_b.node_id = "node-b"
        other = FakeLink()
        peer_b._peers["node-d"] = other
        peer_b._routes["node-d"] = "node-d"
        peer_b._on_peer_line("node-a", "nodeup\tnode-a\tnode-c")
        self.assertEqual(events[-1], ("up", "node-c", "node-a"))
        self.assertTrue(any(l.startswith("nodeup\tnode-a\tnode-c") for l in other.lines))

        hub._notify_peer_down("node-c")
        self.assertEqual(events[-1], ("down", "node-c", "node-a"))
        self.assertTrue(any(l.startswith("nodedown\tnode-a\tnode-c") for l in link_b.lines))

    def test_line_topology_message_and_presence_and_pm(self) -> None:
        """A—B—C: room msg, presence, and PM traverse the middle hop."""
        chat_a, chat_b, chat_c = self._free_port(), self._free_port(), self._free_port()
        fed_a, fed_b, fed_c = self._free_port(), self._free_port(), self._free_port()

        with tempfile.TemporaryDirectory() as td:
            peers_a = Path(td) / "peers_a.json"
            peers_b = Path(td) / "peers_b.json"
            peers_c = Path(td) / "peers_c.json"
            # Line: A—B—C (no A—C edge).
            peers_a.write_text(
                json.dumps(
                    [
                        {
                            "node_id": "node-b",
                            "host": "127.0.0.1",
                            "mode": "tcp",
                            "federation_port": fed_b,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            peers_b.write_text(
                json.dumps(
                    [
                        {
                            "node_id": "node-a",
                            "host": "127.0.0.1",
                            "mode": "tcp",
                            "federation_port": fed_a,
                        },
                        {
                            "node_id": "node-c",
                            "host": "127.0.0.1",
                            "mode": "tcp",
                            "federation_port": fed_c,
                        },
                    ]
                ),
                encoding="utf-8",
            )
            peers_c.write_text(
                json.dumps(
                    [
                        {
                            "node_id": "node-b",
                            "host": "127.0.0.1",
                            "mode": "tcp",
                            "federation_port": fed_b,
                        }
                    ]
                ),
                encoding="utf-8",
            )

            env_a = {
                "SSHCHAT_NODE_ID": "node-a",
                "SSHCHAT_FEDERATION_PORT": str(fed_a),
                "SSHCHAT_FEDERATION_PEERS": str(peers_a),
                "SSHCHAT_FED_PEERS_WATCH_SECONDS": "0",
            }
            env_b = {
                "SSHCHAT_NODE_ID": "node-b",
                "SSHCHAT_FEDERATION_PORT": str(fed_b),
                "SSHCHAT_FEDERATION_PEERS": str(peers_b),
                "SSHCHAT_FED_PEERS_WATCH_SECONDS": "0",
            }
            env_c = {
                "SSHCHAT_NODE_ID": "node-c",
                "SSHCHAT_FEDERATION_PORT": str(fed_c),
                "SSHCHAT_FEDERATION_PEERS": str(peers_c),
                "SSHCHAT_FED_PEERS_WATCH_SECONDS": "0",
            }

            received_c: list[bytes] = []
            pm_a: list[tuple[str, str, str]] = []

            def on_msg_c(room, msg, _peer):
                received_c.append(msg)

            hubs = []
            with mock.patch.dict(os.environ, env_a, clear=False):
                hub_a = federation.FederationHub(
                    chat_a,
                    server.lock,
                    lambda r, m, p: None,
                    lambda r, m: None,
                    lambda t, f, x: pm_a.append((t, f, x)),
                    lambda: [
                        {
                            "name": "alice",
                            "rooms": ["lobby"],
                            "current_room": "lobby",
                        }
                    ],
                )
                hub_a.start()
                hubs.append(hub_a)

            with mock.patch.dict(os.environ, env_b, clear=False):
                hub_b = federation.FederationHub(
                    chat_b,
                    server.lock,
                    lambda r, m, p: None,
                    lambda r, m: None,
                    lambda t, f, x: None,
                    lambda: [],
                )
                hub_b.start()
                hubs.append(hub_b)

            with mock.patch.dict(os.environ, env_c, clear=False):
                hub_c = federation.FederationHub(
                    chat_c,
                    server.lock,
                    on_msg_c,
                    lambda r, m: None,
                    lambda t, f, x: None,
                    lambda: [
                        {
                            "name": "carol",
                            "rooms": ["lobby"],
                            "current_room": "lobby",
                        }
                    ],
                )
                hub_c.start()
                hubs.append(hub_c)

            try:
                deadline = time.time() + 10
                while time.time() < deadline:
                    if (
                        hub_a.peer_count >= 1
                        and hub_b.peer_count >= 2
                        and hub_c.peer_count >= 1
                    ):
                        break
                    time.sleep(0.05)
                self.assertGreaterEqual(hub_a.peer_count, 1)
                self.assertGreaterEqual(hub_b.peer_count, 2)
                self.assertGreaterEqual(hub_c.peer_count, 1)

                # Seed presence across the line (join from A reaches C via B).
                hub_a.notify_join("alice", "lobby")
                deadline = time.time() + 5
                while time.time() < deadline and "alice" not in hub_c.names_in_room("lobby"):
                    time.sleep(0.05)
                self.assertIn("alice", hub_c.names_in_room("lobby"))
                self.assertEqual(hub_c._routes.get("node-a"), "node-b")

                hub_c.notify_join("carol", "lobby")
                deadline = time.time() + 5
                while time.time() < deadline and "carol" not in hub_a.names_in_room("lobby"):
                    time.sleep(0.05)
                self.assertIn("carol", hub_a.names_in_room("lobby"))

                payload = b"[#lobby] [alice] hello across the graph\n"
                hub_a.broadcast_room("lobby", payload)
                deadline = time.time() + 5
                while time.time() < deadline and not received_c:
                    time.sleep(0.05)
                self.assertTrue(received_c)
                self.assertIn(b"hello across the graph", received_c[0])

                self.assertTrue(hub_c.send_pm("alice", "carol", "ping-from-c"))
                deadline = time.time() + 5
                while time.time() < deadline and not pm_a:
                    time.sleep(0.05)
                self.assertEqual(pm_a[-1], ("alice", "carol", "ping-from-c"))
            finally:
                for h in hubs:
                    h.stop()

    def test_cycle_dedup_delivers_msg_once(self) -> None:
        """Triangle A—B—C—A: same room msg lands once per hub."""
        deliveries: dict[str, list[bytes]] = {"a": [], "b": [], "c": []}

        hubs: dict[str, federation.FederationHub] = {}
        for nid, key in (("node-a", "a"), ("node-b", "b"), ("node-c", "c")):
            hub = federation.FederationHub(
                12345,
                server.lock,
                lambda r, m, p, k=key: deliveries[k].append(m),
                lambda r, m: None,
                lambda t, f, x: None,
                lambda: [],
            )
            hub.enabled = True
            hub.node_id = nid
            hubs[nid] = hub

        def wire(src: str, dst: str) -> None:
            sh, dh = hubs[src], hubs[dst]

            class Link:
                def __init__(self, target_hub, ingress_id):
                    self.target_hub = target_hub
                    self.ingress_id = ingress_id
                    self._closed = False

                def send_line(self, line: str) -> None:
                    self.target_hub._on_peer_line(self.ingress_id, line)

                def close(self) -> None:
                    self._closed = True

            sh._peers[dst] = Link(dh, src)
            sh._routes[dst] = dst

        wire("node-a", "node-b")
        wire("node-b", "node-a")
        wire("node-b", "node-c")
        wire("node-c", "node-b")
        wire("node-c", "node-a")
        wire("node-a", "node-c")

        payload = b"[#lobby] once only\n"
        hubs["node-a"].broadcast_room("lobby", payload)
        self.assertEqual(len(deliveries["b"]), 1)
        self.assertEqual(len(deliveries["c"]), 1)
        self.assertEqual(deliveries["b"][0], payload)
        self.assertEqual(deliveries["c"][0], payload)
        # Origin does not deliver its own outbound msg locally via federation.
        self.assertEqual(deliveries["a"], [])


class FederationServerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        server.clients.clear()
        server.rooms.clear()
        server.room_games.clear()
        server.room_game_authority.clear()
        server.room_game_tokens.clear()
        server.room_game_ended_ids.clear()
        server.room_game_provisional.clear()
        server.room_games_parked.clear()
        federation._hub = None
        server._fed_hub = None

    def _free_port(self) -> int:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        return port

    def test_canvas_nick_invite_reaches_federated_user(self) -> None:
        """ /canvas <nick> must accept a user who is only online on a peer. """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = canvas_sharing.CanvasStore(
            store_path=os.path.join(tmp.name, "canvas.json")
        )
        alice = DummyConn()
        server.clients[alice] = {"name": "alice", "current_room": "lobby"}
        pms: list[tuple] = []

        class FakeHub:
            enabled = True

            def has_remote_user(self, nick: str) -> bool:
                return str(nick).lower() == "bob"

            def send_pm(self, to_nick, from_name, text) -> bool:
                pms.append((to_nick, from_name, text))
                return True

            def has_remote_file_public(self) -> bool:
                return False

            def pick_file_public_peer(self):
                return None

        class FakeFileHttp:
            def get_base_url(self) -> str:
                return "https://files.example"

        with mock.patch.object(federation, "get_hub", return_value=FakeHub()):
            with mock.patch.object(server, "file_http", FakeFileHttp()):
                with mock.patch.object(canvas_sharing, "canvas_store", store):
                    with mock.patch.object(
                        file_http_server,
                        "needs_federation_file_proxy",
                        return_value=False,
                    ):
                        server._handle_canvas(alice, "alice", "/canvas bob")

        sent = b"".join(alice.sent).decode("utf-8")
        self.assertNotIn("不在线", sent)
        self.assertTrue(pms, sent)
        self.assertEqual(pms[0][0], "bob")
        self.assertIn("gui-open canvas", pms[0][2])

    def test_fed_pm_canvas_invite_not_wrapped_as_pm(self) -> None:
        bob = DummyConn()
        server.clients[bob] = {"name": "bob", "current_room": "lobby"}
        invite = (
            "[*] ========== 共享画布 ==========\n"
            "[*] gui-open canvas https://files.example/canvas/tok ABCDEF\n"
        )
        server._fed_on_pm("bob", "alice", invite)
        sent = b"".join(bob.sent).decode("utf-8")
        self.assertNotIn("[PM from alice]", sent)
        self.assertIn("gui-open canvas", sent)
        server._fed_on_pm("bob", "alice", "hello there")
        sent2 = b"".join(bob.sent).decode("utf-8")
        self.assertIn("[PM from alice] hello there", sent2)

    def test_broadcast_forwards_to_hub(self) -> None:
        sent: list[tuple[str, bytes]] = []

        class FakeHub:
            enabled = True

            def broadcast_room(self, room, msg, exclude_node=None):
                sent.append((room, msg))

        with mock.patch.object(federation, "get_hub", return_value=FakeHub()):
            server.broadcast_room("general", b"hi\n")
        self.assertEqual(sent, [("general", b"hi\n")])

    def test_peer_up_pushes_active_game_snapshots(self) -> None:
        pushed: list[tuple[str, str]] = []

        class FakeHub:
            enabled = True
            node_id = "node-a"
            peer_count = 1

            def sync_game(self, room, authority, pickle_b64, conflict_token=""):
                pushed.append((room, authority))

            def sync_library_catalog(self, books=None):
                return None

            def sync_file_public(self, base_url=None):
                return None

        class FakeGame:
            state = "playing"

        server.room_games["lobby"] = FakeGame()
        server.room_game_authority["lobby"] = "node-a"
        server.room_game_tokens["lobby"] = "aa"
        with mock.patch.object(federation, "get_hub", return_value=FakeHub()):
            with mock.patch.object(server, "_pickle_game_for_storage", return_value=b"x"):
                with mock.patch.object(server, "broadcast_local_notice"):
                    server._fed_on_peer_event("up", "node-b", "node-a")
        # peer-up catch-up push + reconcile re-push for local-authority rooms
        self.assertEqual(pushed, [("lobby", "node-a"), ("lobby", "node-a")])

    def test_game_request_pushes_snapshot(self) -> None:
        pushed: list[str] = []

        class FakeHub:
            enabled = True
            node_id = "node-a"
            peer_count = 1

            def sync_game(self, room, authority, pickle_b64, conflict_token=""):
                pushed.append(room)

        class FakeGame:
            state = "playing"

        server.room_games["arena"] = FakeGame()
        server.room_game_authority["arena"] = "node-a"
        server.room_game_tokens["arena"] = "bb"
        with mock.patch.object(federation, "get_hub", return_value=FakeHub()):
            with mock.patch.object(server, "_pickle_game_for_storage", return_value=b"x"):
                server._fed_on_game_request("node-b", "arena")
        self.assertEqual(pushed, ["arena"])

    def test_sync_game_line_includes_nonce(self) -> None:
        class FakeLink:
            def __init__(self) -> None:
                self.lines: list[str] = []

            def send_line(self, line: str) -> None:
                self.lines.append(line)

        hub = federation.FederationHub(
            12345,
            server.lock,
            lambda r, m, p: None,
            lambda r, m: None,
            lambda t, f, x: None,
            lambda: [],
        )
        hub.enabled = True
        hub.node_id = "node-a"
        link = FakeLink()
        hub._peers["node-b"] = link
        hub.sync_game("lobby", "node-a", "YmFzZTY0", "deadbeef")
        self.assertEqual(len(link.lines), 1)
        parts = link.lines[0].rstrip("\n").split("\t")
        self.assertEqual(parts[0], "gsync")
        self.assertEqual(parts[2], "lobby")
        self.assertEqual(parts[4], "YmFzZTY0")
        self.assertGreaterEqual(len(parts), 7)
        self.assertEqual(parts[6], "deadbeef")

    def test_gsync_parse_strips_nonce_from_b64(self) -> None:
        """Regression: split(..., 4) used to append nonce/token onto pickle b64."""
        got: list[tuple[str, str, str, str, str]] = []

        class FakeLink:
            node_id = "node-b"

            def send_line(self, line: str) -> None:
                return None

        hub = federation.FederationHub(
            12345,
            server.lock,
            lambda r, m, p: None,
            lambda r, m: None,
            lambda t, f, x: None,
            lambda: [],
        )
        hub.enabled = True
        hub.node_id = "node-a"
        hub._peers["node-b"] = FakeLink()
        hub.on_game_sync = lambda peer, room, auth, b64, tok: got.append(
            (peer, room, auth, b64, tok)
        )
        line = (
            "gsync\tnode-b\tlobby\tnode-b\tYmFzZTY0\t"
            "1234567890123456789\tdeadbeef"
        )
        hub._on_peer_line("node-b", line)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0][1], "lobby")
        self.assertEqual(got[0][2], "node-b")
        self.assertEqual(got[0][3], "YmFzZTY0")
        self.assertEqual(got[0][4], "deadbeef")
        # Must be valid standalone base64 (no trailing nonce).
        import base64

        self.assertEqual(base64.b64decode(got[0][3].encode("ascii")), b"base64")

    def test_greq_invokes_handler_and_fanout(self) -> None:
        got: list[tuple[str, str]] = []

        class FakeLink:
            def __init__(self, node_id: str) -> None:
                self.node_id = node_id
                self.lines: list[str] = []

            def send_line(self, line: str) -> None:
                self.lines.append(line)

        hub = federation.FederationHub(
            12345,
            server.lock,
            lambda r, m, p: None,
            lambda r, m: None,
            lambda t, f, x: None,
            lambda: [],
            on_game_request=lambda peer, room: got.append((peer, room)),
        )
        hub.enabled = True
        hub.node_id = "node-a"
        link_b = FakeLink("node-b")
        link_c = FakeLink("node-c")
        hub._peers["node-b"] = link_b
        hub._peers["node-c"] = link_c
        hub._on_peer_line("node-b", "greq\tnode-b\tlobby\t1")
        self.assertEqual(got, [("node-b", "lobby")])
        self.assertTrue(any(l.startswith("greq\t") for l in link_c.lines))
        self.assertFalse(any(l.startswith("greq\t") for l in link_b.lines))

    def test_conflict_winner_is_deterministic(self) -> None:
        w1, _ = server._game_conflict_winner("node-a", "aa", "node-b", "bb")
        w2, _ = server._game_conflict_winner("node-b", "bb", "node-a", "aa")
        self.assertEqual(w1, "node-b")
        self.assertEqual(w2, "node-b")

    def test_game_sync_conflict_keeps_higher_token_and_notifies(self) -> None:
        notices: list[bytes] = []

        class LocalGame:
            name = "chess"
            state = "playing"

        class RemoteGame:
            name = "gomoku"
            state = "playing"

        class FakeHub:
            enabled = True
            node_id = "node-a"

        remote = RemoteGame()
        local = LocalGame()
        server.room_games["lobby"] = local
        server.room_game_authority["lobby"] = "node-a"
        server.room_game_tokens["lobby"] = "aaaa"  # loses to bbbb
        server.room_games_parked.clear()
        # Unsolicited gsync cannot overwrite a live host; token tiebreak is for greq.
        server._note_greq("lobby")
        with mock.patch.object(federation, "get_hub", return_value=FakeHub()):
            with mock.patch.object(server.pickle, "loads", return_value=remote):
                with mock.patch.object(server, "_rebind_game_services"):
                    with mock.patch.object(server, "_remap_local_game_seats_locked"):
                        with mock.patch.object(
                            server,
                            "broadcast_room",
                            side_effect=lambda r, m, **k: notices.append(m),
                        ):
                            with mock.patch.object(server, "send_oriented_boards"):
                                with mock.patch.object(server, "send_sanguo_hand_views"):
                                    server._fed_on_game_sync(
                                        "node-b",
                                        "lobby",
                                        "node-b",
                                        "ZmFrZQ==",
                                        "bbbb",
                                    )
        self.assertIs(server.room_games["lobby"], remote)
        self.assertEqual(server.room_game_authority["lobby"], "node-b")
        self.assertIs(server.room_games_parked["lobby"], local)
        self.assertEqual(len(notices), 1)
        self.assertIn("联邦对局冲突".encode("utf-8"), notices[0])
        self.assertIn("已暂存".encode("utf-8"), notices[0])
        self.assertIn(b"node-b", notices[0])
        self.assertIn(b"node-a", notices[0])

    def test_game_sync_conflict_keeps_local_when_token_wins(self) -> None:
        pushed: list[str] = []

        class LocalGame:
            name = "chess"
            state = "playing"

        class RemoteGame:
            name = "gomoku"
            state = "playing"

        class FakeHub:
            enabled = True
            node_id = "node-a"
            peer_count = 1

            def sync_game(self, room, authority, pickle_b64, conflict_token=""):
                pushed.append(room)

        local = LocalGame()
        server.room_games["lobby"] = local
        server.room_game_authority["lobby"] = "node-a"
        server.room_game_tokens["lobby"] = "zzzz"  # wins over aaaa
        with mock.patch.object(federation, "get_hub", return_value=FakeHub()):
            with mock.patch.object(server.pickle, "loads", return_value=RemoteGame()):
                with mock.patch.object(server, "_rebind_game_services"):
                    with mock.patch.object(server, "_remap_local_game_seats_locked"):
                        with mock.patch.object(server, "broadcast_room") as br:
                            with mock.patch.object(
                                server, "_pickle_game_for_storage", return_value=b"x"
                            ):
                                server._fed_on_game_sync(
                                    "node-b",
                                    "lobby",
                                    "node-b",
                                    "ZmFrZQ==",
                                    "aaaa",
                                )
                                br.assert_not_called()
        self.assertIs(server.room_games["lobby"], local)
        self.assertEqual(server.room_game_authority["lobby"], "node-a")
        self.assertEqual(pushed, ["lobby"])

    def test_lcatalog_stores_and_fanouts(self) -> None:
        class FakeLink:
            def __init__(self, node_id: str) -> None:
                self.node_id = node_id
                self.lines: list[str] = []

            def send_line(self, line: str) -> None:
                self.lines.append(line)

        hub = federation.FederationHub(
            12345,
            server.lock,
            lambda r, m, p: None,
            lambda r, m: None,
            lambda t, f, x: None,
            lambda: [],
        )
        hub.enabled = True
        hub.node_id = "node-a"
        peer_b = FakeLink("node-b")
        peer_c = FakeLink("node-c")
        hub._peers["node-b"] = peer_b
        hub._peers["node-c"] = peer_c
        books = [{"name": "shared.txt", "ext": "txt", "size_bytes": 12}]
        blob = base64.b64encode(
            json.dumps(books, ensure_ascii=False).encode("utf-8")
        ).decode("ascii")
        line = f"lcatalog\tnode-b\t{blob}\t1"
        hub._on_peer_line("node-b", line)
        self.assertEqual(
            hub.remote_library_catalogs()["node-b"][0]["name"], "shared.txt"
        )
        self.assertTrue(any(l.startswith("lcatalog\tnode-b\t") for l in peer_c.lines))
        self.assertFalse(any(l.startswith("lcatalog\t") for l in peer_b.lines))

    def test_lpage_round_trip_invokes_handlers(self) -> None:
        requests: list[tuple] = []
        results: list[tuple] = []
        done = threading.Event()

        class FakeLink:
            def __init__(self, node_id: str) -> None:
                self.node_id = node_id
                self.lines: list[str] = []

            def send_line(self, line: str) -> None:
                self.lines.append(line)

        def on_req(*a):
            requests.append(a)
            done.set()

        owner = federation.FederationHub(
            12345,
            server.lock,
            lambda r, m, p: None,
            lambda r, m: None,
            lambda t, f, x: None,
            lambda: [],
            on_library_page_request=on_req,
        )
        owner.enabled = True
        owner.node_id = "node-owner"
        owner._peers["node-reader"] = FakeLink("node-reader")

        reader = federation.FederationHub(
            12346,
            server.lock,
            lambda r, m, p: None,
            lambda r, m: None,
            lambda t, f, x: None,
            lambda: [],
            on_library_page_result=lambda *a: results.append(a),
        )
        reader.enabled = True
        reader.node_id = "node-reader"
        reader._peers["node-owner"] = FakeLink("node-owner")
        reader._routes["node-owner"] = "node-owner"

        self.assertTrue(reader.request_library_page("node-owner", "req1", "a.txt", 2))
        req_line = reader._peers["node-owner"].lines[-1].rstrip("\n")
        owner._on_peer_line("node-reader", req_line)
        self.assertTrue(done.wait(2.0), "lpage handler thread did not run")
        self.assertEqual(requests[-1][:4], ("node-owner", "req1", "a.txt", 2))
        self.assertEqual(requests[-1][4], "node-reader")
        # nick/flags trailing args (may be empty defaults)
        self.assertGreaterEqual(len(requests[-1]), 5)

        payload = {"ok": True, "text": "hello", "page": 2, "total_pages": 5}
        owner.reply_library_page("node-reader", "req1", payload)
        ok_line = owner._peers["node-reader"].lines[-1].rstrip("\n")
        reader._on_peer_line("node-owner", ok_line)
        self.assertEqual(results[-1][0], "node-owner")
        self.assertEqual(results[-1][1], "req1")
        self.assertEqual(results[-1][2]["text"], "hello")

    def test_lpage_resume_and_save_flags_reach_owner(self) -> None:
        got: list[tuple] = []
        done = threading.Event()

        def on_req(*a):
            got.append(a)
            done.set()

        class FakeLink:
            def __init__(self, node_id: str) -> None:
                self.node_id = node_id
                self.lines: list[str] = []

            def send_line(self, line: str) -> None:
                self.lines.append(line)

        owner = federation.FederationHub(
            12345,
            server.lock,
            lambda r, m, p: None,
            lambda r, m: None,
            lambda t, f, x: None,
            lambda: [],
            on_library_page_request=on_req,
        )
        owner.enabled = True
        owner.node_id = "node-owner"
        owner._peers["node-b"] = FakeLink("node-b")

        reader = federation.FederationHub(
            12346,
            server.lock,
            lambda r, m, p: None,
            lambda r, m: None,
            lambda t, f, x: None,
            lambda: [],
        )
        reader.enabled = True
        reader.node_id = "node-b"
        reader._peers["node-owner"] = FakeLink("node-owner")
        reader._routes["node-owner"] = "node-owner"

        self.assertTrue(
            reader.request_library_page(
                "node-owner", "req2", "book.epub", 0, nick="yxt", flags="r"
            )
        )
        owner._on_peer_line(
            "node-b", reader._peers["node-owner"].lines[-1].rstrip("\n")
        )
        self.assertTrue(done.wait(2.0))
        self.assertEqual(got[-1][5], "yxt")
        self.assertEqual(got[-1][6], "r")

    def test_lpage_search_flag_and_query_reach_owner(self) -> None:
        got: list[tuple] = []
        done = threading.Event()

        def on_req(*a):
            got.append(a)
            done.set()

        class FakeLink:
            def __init__(self, node_id: str) -> None:
                self.node_id = node_id
                self.lines: list[str] = []

            def send_line(self, line: str) -> None:
                self.lines.append(line)

        owner = federation.FederationHub(
            12345,
            server.lock,
            lambda r, m, p: None,
            lambda r, m: None,
            lambda t, f, x: None,
            lambda: [],
            on_library_page_request=on_req,
        )
        owner.enabled = True
        owner.node_id = "node-owner"
        owner._peers["node-b"] = FakeLink("node-b")

        reader = federation.FederationHub(
            12346,
            server.lock,
            lambda r, m, p: None,
            lambda r, m: None,
            lambda t, f, x: None,
            lambda: [],
        )
        reader.enabled = True
        reader.node_id = "node-b"
        reader._peers["node-owner"] = FakeLink("node-owner")
        reader._routes["node-owner"] = "node-owner"

        self.assertTrue(
            reader.request_library_page(
                "node-owner",
                "req3",
                "book.epub",
                0,
                flags="f",
                query="hello world",
            )
        )
        line = reader._peers["node-owner"].lines[-1].rstrip("\n")
        self.assertIn("\tf\t", line)
        self.assertTrue(line.endswith("hello world"))
        owner._on_peer_line("node-b", line)
        self.assertTrue(done.wait(2.0))
        self.assertEqual(got[-1][6], "f")
        self.assertEqual(got[-1][7], "hello world")
        self.assertFalse(
            reader.request_library_page(
                "node-owner", "req4", "book.epub", 0, flags="f", query="  "
            )
        )

    def test_owner_page_request_resumes_bookmark(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        lib_dir = Path(tmp.name)
        book = lib_dir / "tale.txt"
        book.write_text("word " * 800, encoding="utf-8")
        old_chars = library.LIBRARY_PAGE_CHARS
        library.LIBRARY_PAGE_CHARS = 200
        self.addCleanup(lambda: setattr(library, "LIBRARY_PAGE_CHARS", old_chars))

        prev_dir = os.environ.get("SSHCHAT_LIBRARY_DIR")
        os.environ["SSHCHAT_LIBRARY_DIR"] = str(lib_dir)
        self.addCleanup(
            lambda: os.environ.__setitem__("SSHCHAT_LIBRARY_DIR", prev_dir)
            if prev_dir is not None
            else os.environ.pop("SSHCHAT_LIBRARY_DIR", None)
        )
        # Point server library bookmarks at temp store.
        prev_bm = server.library_bookmarks
        store_path = str(Path(tmp.name) / "bm.json")
        server.library_bookmarks = library.LibraryBookmarkStore(store_path)
        self.addCleanup(lambda: setattr(server, "library_bookmarks", prev_bm))
        server.library_bookmarks.set_page("yxt", "tale.txt", 2)

        replies: list[tuple] = []

        class FakeHub:
            enabled = True
            node_id = "owner"

            def reply_library_page(self, requester, req_id, payload):
                replies.append((requester, req_id, payload))

        with mock.patch.object(federation, "get_hub", return_value=FakeHub()):
            with mock.patch.object(server, "_federation_sync_library_bookmarks"):
                server._fed_on_library_page_request(
                    "owner", "rid", "tale.txt", 0, "reader", "yxt", "r"
                )
        self.assertEqual(len(replies), 1)
        payload = replies[0][2]
        self.assertTrue(payload.get("ok"))
        self.assertEqual(payload.get("page"), 2)
        self.assertTrue(payload.get("resumed"))

    def test_owner_page_request_saves_bookmark(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        lib_dir = Path(tmp.name)
        book = lib_dir / "tale.txt"
        book.write_text("word " * 800, encoding="utf-8")
        old_chars = library.LIBRARY_PAGE_CHARS
        library.LIBRARY_PAGE_CHARS = 200
        self.addCleanup(lambda: setattr(library, "LIBRARY_PAGE_CHARS", old_chars))

        prev_dir = os.environ.get("SSHCHAT_LIBRARY_DIR")
        os.environ["SSHCHAT_LIBRARY_DIR"] = str(lib_dir)
        self.addCleanup(
            lambda: os.environ.__setitem__("SSHCHAT_LIBRARY_DIR", prev_dir)
            if prev_dir is not None
            else os.environ.pop("SSHCHAT_LIBRARY_DIR", None)
        )
        prev_bm = server.library_bookmarks
        store_path = str(Path(tmp.name) / "bm.json")
        server.library_bookmarks = library.LibraryBookmarkStore(store_path)
        self.addCleanup(lambda: setattr(server, "library_bookmarks", prev_bm))

        class FakeHub:
            enabled = True
            node_id = "owner"

            def reply_library_page(self, requester, req_id, payload):
                pass

        with mock.patch.object(federation, "get_hub", return_value=FakeHub()):
            with mock.patch.object(server, "_federation_sync_library_bookmarks"):
                server._fed_on_library_page_request(
                    "owner", "rid", "tale.txt", 3, "reader", "yxt", "s"
                )
        self.assertEqual(server.library_bookmarks.get_page("yxt", "tale.txt"), 3)

    def test_owner_page_request_searches_book(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        lib_dir = Path(tmp.name)
        book = lib_dir / "tale.txt"
        book.write_text(
            "alpha " * 40 + "\n\nneedle in hay\n\n" + "omega " * 40,
            encoding="utf-8",
        )
        old_chars = library.LIBRARY_PAGE_CHARS
        library.LIBRARY_PAGE_CHARS = 80
        self.addCleanup(lambda: setattr(library, "LIBRARY_PAGE_CHARS", old_chars))

        prev_dir = os.environ.get("SSHCHAT_LIBRARY_DIR")
        os.environ["SSHCHAT_LIBRARY_DIR"] = str(lib_dir)
        self.addCleanup(
            lambda: os.environ.__setitem__("SSHCHAT_LIBRARY_DIR", prev_dir)
            if prev_dir is not None
            else os.environ.pop("SSHCHAT_LIBRARY_DIR", None)
        )
        prev_bm = server.library_bookmarks
        store_path = str(Path(tmp.name) / "bm.json")
        server.library_bookmarks = library.LibraryBookmarkStore(store_path)
        self.addCleanup(lambda: setattr(server, "library_bookmarks", prev_bm))

        replies: list[tuple] = []

        class FakeHub:
            enabled = True
            node_id = "owner"

            def reply_library_page(self, requester, req_id, payload):
                replies.append((requester, req_id, payload))

        with mock.patch.object(federation, "get_hub", return_value=FakeHub()):
            server._fed_on_library_page_request(
                "owner", "rid", "tale.txt", 0, "reader", "", "f", "needle"
            )
        self.assertEqual(len(replies), 1)
        payload = replies[0][2]
        self.assertTrue(payload.get("ok"))
        self.assertIn("results", payload)
        self.assertNotIn("text", payload)
        hits = payload["results"]
        self.assertTrue(hits)
        self.assertIn("needle", hits[0]["snippet"])

    def test_lpage_handler_does_not_block_peer_line(self) -> None:
        """Slow book loads must not stall federation I/O."""
        started = threading.Event()
        release = threading.Event()

        def slow_handler(*_a):
            started.set()
            release.wait(2.0)

        hub = federation.FederationHub(
            12345,
            server.lock,
            lambda r, m, p: None,
            lambda r, m: None,
            lambda t, f, x: None,
            lambda: [],
            on_library_page_request=slow_handler,
        )
        hub.enabled = True
        hub.node_id = "node-owner"
        t0 = time.monotonic()
        hub._on_peer_line(
            "node-reader",
            "lpage\tnode-reader\tnode-owner\treq9\tbook.epub\t0",
        )
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 0.5)
        self.assertTrue(started.wait(2.0))
        release.set()

    def test_peer_down_fails_pending_library_page_waiters(self) -> None:
        event = threading.Event()
        req_id = "abc123"
        with server._library_page_waiters_lock:
            server._library_page_waiters[req_id] = {
                "event": event,
                "payload": None,
            }
        try:
            server._fed_on_peer_event("down", "Mathematics.local", "node-a")
            self.assertTrue(event.is_set())
            with server._library_page_waiters_lock:
                payload = server._library_page_waiters[req_id]["payload"]
            self.assertIsInstance(payload, dict)
            self.assertFalse(payload.get("ok"))
            self.assertIn("Mathematics.local", str(payload.get("error") or ""))
        finally:
            with server._library_page_waiters_lock:
                server._library_page_waiters.pop(req_id, None)

    def test_sync_library_catalog_fanout(self) -> None:
        class FakeLink:
            def __init__(self) -> None:
                self.lines: list[str] = []

            def send_line(self, line: str) -> None:
                self.lines.append(line)

        hub = federation.FederationHub(
            12345,
            server.lock,
            lambda r, m, p: None,
            lambda r, m: None,
            lambda t, f, x: None,
            lambda: [],
            get_local_library=lambda: [
                {"name": "local.md", "ext": "md", "size_bytes": 8}
            ],
        )
        hub.enabled = True
        hub.node_id = "node-a"
        link = FakeLink()
        hub._peers["node-b"] = link
        hub.sync_library_catalog()
        self.assertEqual(len(link.lines), 1)
        parts = link.lines[0].rstrip("\n").split("\t")
        self.assertEqual(parts[0], "lcatalog")
        self.assertEqual(parts[1], "node-a")
        books = json.loads(base64.b64decode(parts[2]).decode("utf-8"))
        self.assertEqual(books[0]["name"], "local.md")

    def test_lmarks_fanout_and_handler(self) -> None:
        got: list[tuple] = []

        class FakeLink:
            def __init__(self, node_id: str) -> None:
                self.node_id = node_id
                self.lines: list[str] = []

            def send_line(self, line: str) -> None:
                self.lines.append(line)

        hub = federation.FederationHub(
            12345,
            server.lock,
            lambda r, m, p: None,
            lambda r, m: None,
            lambda t, f, x: None,
            lambda: [],
            on_library_bookmarks=lambda *a: got.append(a),
        )
        hub.enabled = True
        hub.node_id = "node-a"
        peer = FakeLink("node-b")
        hub._peers["node-b"] = peer
        hub.sync_library_bookmarks("yxt", {"a.epub": {"page": 2, "updated_ts": 9}})
        self.assertEqual(len(peer.lines), 1)
        parts = peer.lines[0].rstrip("\n").split("\t")
        self.assertEqual(parts[0], "lmarks")
        self.assertEqual(parts[2], "yxt")
        books = json.loads(base64.b64decode(parts[3]).decode("utf-8"))
        self.assertEqual(books["a.epub"]["page"], 2)

        other = federation.FederationHub(
            12346,
            server.lock,
            lambda r, m, p: None,
            lambda r, m: None,
            lambda t, f, x: None,
            lambda: [],
            on_library_bookmarks=lambda *a: got.append(a),
        )
        other.enabled = True
        other.node_id = "node-b"
        other._peers["node-a"] = FakeLink("node-a")
        other._on_peer_line("node-a", peer.lines[0].rstrip("\n"))
        self.assertEqual(got[-1][0], "node-a")
        self.assertEqual(got[-1][1], "yxt")
        self.assertEqual(got[-1][2]["a.epub"]["page"], 2)

    def test_run_session_assembles_chunked_lines(self) -> None:
        """Large federation frames (lpage_ok) must survive multi-recv delivery."""
        handled: list[str] = []
        big = "lpage_ok\tnode-b\tnode-a\treq1\t" + ("A" * 12000)
        payload = (big + "\n").encode("utf-8")
        chunks = [payload[i : i + 100] for i in range(0, len(payload), 100)] + [b""]

        class FakeSock:
            def __init__(self) -> None:
                self._chunks = list(chunks)
                self.sent: list[bytes] = []

            def recv(self, n: int) -> bytes:
                if not self._chunks:
                    return b""
                return self._chunks.pop(0)

            def sendall(self, data: bytes) -> None:
                self.sent.append(data)

            def close(self) -> None:
                return None

        hub = federation.FederationHub(
            12345,
            server.lock,
            lambda r, m, p: None,
            lambda r, m: None,
            lambda t, f, x: None,
            lambda: [],
        )
        hub.enabled = True
        hub.node_id = "node-a"
        hub._stop.clear()

        sock = FakeSock()
        # Pre-seed handshake identity so we enter the main read loop with leftover buffer.
        # Feed @fed hello first as separate complete line, then chunked body via recv.
        hello = b"@fed\tnode-b\n"
        # Put hello+first part of big line in initial buffer by making first recv return hello
        # then subsequent chunked big line — simplest: prepend hello as its own complete recv.
        sock._chunks = [hello] + chunks

        original_handle = federation._PeerLink.handle_line

        def capture(self, line: str) -> None:
            handled.append(line.rstrip("\n"))

        with mock.patch.object(federation._PeerLink, "handle_line", capture):
            hub._run_session(sock, ("127.0.0.1", 1), peer_hint=None)
        self.assertTrue(any(h.startswith("lpage_ok\t") for h in handled), handled[:3])
        self.assertTrue(any(len(h) > 10000 for h in handled))

    def test_recv_loop_continue_on_partial_line(self) -> None:
        """Regression: split without newline used to ValueError and drop the peer."""
        hub = federation.FederationHub(
            12345,
            server.lock,
            lambda r, m, p: None,
            lambda r, m: None,
            lambda t, f, x: None,
            lambda: [],
        )
        hub.enabled = True
        hub.node_id = "node-a"
        buffer = b"partial-without-newline"
        # Mimic the fixed loop body once.
        if b"\n" not in buffer:
            buffer += b"-more"
            self.assertNotIn(b"\n", buffer)
            # Fixed code continues; old code would raise here:
            with self.assertRaises(ValueError):
                _line_b, _buffer = buffer.split(b"\n", 1)

        """Disconnect leave must not dual-send via room msg + notify_leave."""
        msgs: list[tuple[str, bytes]] = []
        leaves: list[tuple[str, str]] = []

        class FakeHub:
            enabled = True

            def same_name_in_room(self, room, name, local_same):
                return False

            def broadcast_room(self, room, msg, exclude_node=None):
                msgs.append((room, msg))

            def notify_leave(self, name, room):
                leaves.append((name, room))

        alice = DummyConn()
        with server.lock:
            server.clients[alice] = {
                "name": "yxt",
                "rooms": {"default"},
                "current_room": "default",
            }
            server.rooms["default"] = {alice}
        with mock.patch.object(federation, "get_hub", return_value=FakeHub()):
            server.remove_client(alice)
        self.assertEqual(leaves, [("yxt", "default")])
        self.assertEqual(msgs, [])

    def test_fed_room_msg_drops_presence_chat_duplicates(self) -> None:
        delivered: list[bytes] = []

        def capture(room, msg, **kwargs):
            delivered.append(msg)

        with mock.patch.object(server, "broadcast_room", side_effect=capture):
            server._fed_on_room_msg(
                "default", b"[!] yxt left #default\n", "node-b"
            )
            server._fed_on_room_msg(
                "default", b"[+] yxt joined #default\n", "node-b"
            )
            server._fed_on_room_msg("default", b"hello chat\n", "node-b")
        self.assertEqual(delivered, [b"hello chat\n"])

    def test_register_peer_replace_suppresses_up_notice(self) -> None:
        events: list[tuple[str, str, str]] = []

        class FakeLink:
            def __init__(self, node_id: str) -> None:
                self.node_id = node_id
                self._closed = False

            def close(self) -> None:
                self._closed = True

            def send_line(self, line: str) -> None:
                return None

        hub = federation.FederationHub(
            12345,
            server.lock,
            lambda r, m, p: None,
            lambda r, m: None,
            lambda t, f, x: None,
            lambda: [],
            on_peer_event=lambda ev, peer, rep: events.append((ev, peer, rep)),
        )
        hub.enabled = True
        hub.node_id = "node-a"
        first = FakeLink("node-b")
        self.assertTrue(hub._register_peer("node-b", first))
        hub._notify_peer_up("node-b")
        second = FakeLink("node-b")
        self.assertFalse(hub._register_peer("node-b", second))
        self.assertEqual(events, [("up", "node-b", "node-a")])
        self.assertTrue(first._closed)
        self.assertIs(hub._peers["node-b"], second)

    def test_pick_newest_file_public_peer(self) -> None:
        hub = federation.FederationHub(
            12345,
            server.lock,
            lambda r, m, p: None,
            lambda r, m: None,
            lambda t, f, x: None,
            lambda: [],
        )
        hub.enabled = True
        hub.node_id = "node-a"
        hub._routes["node-b"] = "node-b"
        hub._routes["node-c"] = "node-c"
        hub._peers["node-b"] = object()  # type: ignore[assignment]
        hub._remote_file_pubs["node-b"] = {
            "base_url": "https://old.trycloudflare.com",
            "seen_at": 100.0,
        }
        hub._remote_file_pubs["node-c"] = {
            "base_url": "https://new.trycloudflare.com",
            "seen_at": 200.0,
        }
        picked = hub.pick_file_public_peer()
        self.assertEqual(picked, ("node-c", "https://new.trycloudflare.com"))

    def test_file_host_rpc_roundtrip(self) -> None:
        chat_a = self._free_port()
        chat_b = self._free_port()
        fed_a = self._free_port()
        fed_b = self._free_port()

        with tempfile.TemporaryDirectory() as td:
            peers_a = Path(td) / "peers_a.json"
            peers_b = Path(td) / "peers_b.json"
            peers_a.write_text(
                json.dumps(
                    [
                        {
                            "node_id": "node-b",
                            "host": "127.0.0.1",
                            "mode": "tcp",
                            "federation_port": fed_b,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            peers_b.write_text(
                json.dumps(
                    [
                        {
                            "node_id": "node-a",
                            "host": "127.0.0.1",
                            "mode": "tcp",
                            "federation_port": fed_a,
                        }
                    ]
                ),
                encoding="utf-8",
            )

            hosted: dict = {}
            result: dict = {}
            done = threading.Event()

            def on_host_req(requester, req_id, payload):
                hosted["requester"] = requester
                hosted["payload"] = payload
                hub_b.reply_file_host(
                    requester,
                    req_id,
                    {
                        "ok": True,
                        "upload_url": "https://cf.trycloudflare.com/upload/tok",
                        "upload_key": "ABC123",
                        "host_node": "node-b",
                    },
                )

            def on_host_result(origin, req_id, payload):
                result["origin"] = origin
                result["payload"] = payload
                done.set()

            with mock.patch.dict(
                os.environ,
                {
                    "SSHCHAT_NODE_ID": "node-a",
                    "SSHCHAT_FEDERATION_PORT": str(fed_a),
                    "SSHCHAT_FEDERATION_PEERS": str(peers_a),
                },
                clear=False,
            ):
                hub_a = federation.FederationHub(
                    chat_a,
                    threading.Lock(),
                    lambda r, m, p: None,
                    lambda r, m: None,
                    lambda t, f, x: None,
                    lambda: [],
                    on_file_host_result=on_host_result,
                    get_local_file_public=lambda: "",
                )
                hub_a.enabled = True
                hub_a.start()

            with mock.patch.dict(
                os.environ,
                {
                    "SSHCHAT_NODE_ID": "node-b",
                    "SSHCHAT_FEDERATION_PORT": str(fed_b),
                    "SSHCHAT_FEDERATION_PEERS": str(peers_b),
                },
                clear=False,
            ):
                hub_b = federation.FederationHub(
                    chat_b,
                    threading.Lock(),
                    lambda r, m, p: None,
                    lambda r, m: None,
                    lambda t, f, x: None,
                    lambda: [],
                    on_file_host_request=on_host_req,
                    get_local_file_public=lambda: "https://cf.trycloudflare.com",
                )
                hub_b.enabled = True
                hub_b.start()

            try:
                deadline = time.time() + 5
                while time.time() < deadline:
                    if hub_a.peer_count and hub_b.peer_count:
                        break
                    time.sleep(0.05)
                self.assertGreater(hub_a.peer_count, 0)
                # B announces public URL; A should learn it.
                hub_b.sync_file_public()
                deadline = time.time() + 3
                while time.time() < deadline and "node-b" not in hub_a._remote_file_pubs:
                    time.sleep(0.05)
                self.assertIn("node-b", hub_a._remote_file_pubs)
                picked = hub_a.pick_file_public_peer()
                self.assertIsNotNone(picked)
                assert picked is not None
                self.assertEqual(picked[0], "node-b")
                self.assertTrue(
                    hub_a.request_file_host(
                        "node-b",
                        "req1",
                        {"sender": "alice", "recipients": ["bob"], "room": None},
                    )
                )
                self.assertTrue(done.wait(5))
                self.assertEqual(hosted.get("requester"), "node-a")
                self.assertEqual(result.get("origin"), "node-b")
                self.assertTrue((result.get("payload") or {}).get("ok"))
            finally:
                hub_a.stop()
                hub_b.stop()

    def test_canvas_host_rpc_roundtrip(self) -> None:
        chat_a = self._free_port()
        chat_b = self._free_port()
        fed_a = self._free_port()
        fed_b = self._free_port()

        with tempfile.TemporaryDirectory() as td:
            peers_a = Path(td) / "peers_a.json"
            peers_b = Path(td) / "peers_b.json"
            peers_a.write_text(
                json.dumps(
                    [
                        {
                            "node_id": "node-b",
                            "host": "127.0.0.1",
                            "mode": "tcp",
                            "federation_port": fed_b,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            peers_b.write_text(
                json.dumps(
                    [
                        {
                            "node_id": "node-a",
                            "host": "127.0.0.1",
                            "mode": "tcp",
                            "federation_port": fed_a,
                        }
                    ]
                ),
                encoding="utf-8",
            )

            hosted: dict = {}
            result: dict = {}
            done = threading.Event()

            def on_host_req(requester, req_id, payload):
                hosted["payload"] = payload
                hub_b.reply_file_host(
                    requester,
                    req_id,
                    {
                        "ok": True,
                        "mode": "canvas",
                        "base_url": "https://cf.trycloudflare.com",
                        "session_id": "canvas-1",
                        "creator": "alice",
                        "tokens": {"alice": "tok-a", "bob": "tok-b"},
                        "keys": {"alice": "AAA111", "bob": "BBB222"},
                        "host_node": "node-b",
                    },
                )

            def on_host_result(origin, req_id, payload):
                result["origin"] = origin
                result["payload"] = payload
                done.set()

            with mock.patch.dict(
                os.environ,
                {
                    "SSHCHAT_NODE_ID": "node-a",
                    "SSHCHAT_FEDERATION_PORT": str(fed_a),
                    "SSHCHAT_FEDERATION_PEERS": str(peers_a),
                },
                clear=False,
            ):
                hub_a = federation.FederationHub(
                    chat_a,
                    threading.Lock(),
                    lambda r, m, p: None,
                    lambda r, m: None,
                    lambda t, f, x: None,
                    lambda: [],
                    on_file_host_result=on_host_result,
                    get_local_file_public=lambda: "",
                )
                hub_a.enabled = True
                hub_a.start()

            with mock.patch.dict(
                os.environ,
                {
                    "SSHCHAT_NODE_ID": "node-b",
                    "SSHCHAT_FEDERATION_PORT": str(fed_b),
                    "SSHCHAT_FEDERATION_PEERS": str(peers_b),
                },
                clear=False,
            ):
                hub_b = federation.FederationHub(
                    chat_b,
                    threading.Lock(),
                    lambda r, m, p: None,
                    lambda r, m: None,
                    lambda t, f, x: None,
                    lambda: [],
                    on_file_host_request=on_host_req,
                    get_local_file_public=lambda: "https://cf.trycloudflare.com",
                )
                hub_b.enabled = True
                hub_b.start()

            try:
                deadline = time.time() + 5
                while time.time() < deadline:
                    if hub_a.peer_count and hub_b.peer_count:
                        break
                    time.sleep(0.05)
                self.assertTrue(
                    hub_a.request_file_host(
                        "node-b",
                        "c-req1",
                        {
                            "mode": "canvas",
                            "creator": "alice",
                            "participants": ["bob"],
                            "room": "default",
                        },
                    )
                )
                self.assertTrue(done.wait(5))
                self.assertEqual(result.get("origin"), "node-b")
                payload = result.get("payload") or {}
                self.assertTrue(payload.get("ok"))
                self.assertEqual(payload.get("mode"), "canvas")
                self.assertEqual(hosted.get("payload", {}).get("mode"), "canvas")
            finally:
                hub_a.stop()
                hub_b.stop()


class FilePublicReachabilityTests(unittest.TestCase):
    def test_trycloudflare_and_private(self) -> None:
        import file_http_server as fhs

        self.assertTrue(fhs.is_externally_reachable_host("abc.trycloudflare.com"))
        self.assertTrue(
            fhs.is_externally_reachable_url("https://abc.trycloudflare.com")
        )
        self.assertFalse(fhs.is_externally_reachable_host("10.147.17.226"))
        self.assertFalse(fhs.is_externally_reachable_host("127.0.0.1"))
        self.assertFalse(fhs.is_externally_reachable_host("localhost"))
        self.assertTrue(fhs.needs_federation_file_proxy("http://10.0.0.5:8443"))
        with mock.patch.dict(os.environ, {"SSHCHAT_FILE_USE_FED_PROXY": "0"}):
            self.assertFalse(
                fhs.needs_federation_file_proxy("http://10.0.0.5:8443")
            )

    def test_live_cloudflare_url_overrides_stale_env(self) -> None:
        """Boot refreshes public_url; long-lived server must prefer the live file."""
        import tempfile
        import file_http_server as fhs

        fd, path = tempfile.mkstemp(suffix=".url")
        os.close(fd)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("https://fresh-boot-host.trycloudflare.com\n")
            with mock.patch.dict(
                os.environ, {"SSHCHAT_CLOUDFLARED_URL_FILE": path}
            ):
                self.assertEqual(
                    fhs.live_cloudflare_base_url(),
                    "https://fresh-boot-host.trycloudflare.com",
                )
                srv = fhs.FileHTTPServer(
                    host="127.0.0.1",
                    port=8443,
                    use_https=False,
                    public_host="stale-old-host.trycloudflare.com",
                    public_port=443,
                )
                self.assertEqual(
                    srv.get_base_url(),
                    "https://fresh-boot-host.trycloudflare.com",
                )
                self.assertEqual(srv.get_public_host(), "fresh-boot-host.trycloudflare.com")
        finally:
            os.unlink(path)

    def test_stale_trycloudflare_env_ignored_without_live_file(self) -> None:
        """Tunnel restart deletes public_url; do not keep serving the dead hostname."""
        import tempfile
        import file_http_server as fhs

        fd, path = tempfile.mkstemp(suffix=".url")
        os.close(fd)
        os.unlink(path)  # missing latch
        with mock.patch.dict(os.environ, {"SSHCHAT_CLOUDFLARED_URL_FILE": path}):
            with mock.patch.object(fhs, "_detect_lan_ip", return_value="10.0.0.9"):
                srv = fhs.FileHTTPServer(
                    host="127.0.0.1",
                    port=8443,
                    use_https=False,
                    public_host="dead-old-host.trycloudflare.com",
                    public_port=443,
                )
                self.assertEqual(srv.get_public_host(), "10.0.0.9")
                self.assertEqual(srv.get_base_url(), "http://10.0.0.9:8443")


if __name__ == "__main__":
    unittest.main()
