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

        # Peer B receives the relay and announces locally without re-flooding.
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
        peer_b._on_peer_line("node-a", "nodeup\tnode-a\tnode-c")
        self.assertEqual(events[-1], ("up", "node-c", "node-a"))
        self.assertEqual(other.lines, [])

        hub._notify_peer_down("node-c")
        self.assertEqual(events[-1], ("down", "node-c", "node-a"))
        self.assertTrue(any(l.startswith("nodedown\tnode-a\tnode-c") for l in link_b.lines))


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
