import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import federation
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
        server.room_games.clear()
        server.room_game_authority.clear()
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
        federation._hub = None
        server._fed_hub = None

    def test_broadcast_forwards_to_hub(self) -> None:
        sent: list[tuple[str, bytes]] = []

        class FakeHub:
            enabled = True

            def broadcast_room(self, room, msg, exclude_node=None):
                sent.append((room, msg))

        with mock.patch.object(federation, "get_hub", return_value=FakeHub()):
            server.broadcast_room("general", b"hi\n")
        self.assertEqual(sent, [("general", b"hi\n")])


if __name__ == "__main__":
    unittest.main()
