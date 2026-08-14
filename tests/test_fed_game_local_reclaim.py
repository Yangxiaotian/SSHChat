"""Local-only seats should reclaim stale remote game authority."""

from __future__ import annotations

import unittest
from unittest import mock

import federation
import server
from games import GomokuGame
from session_store import DisconnectedSeat, FederatedSeat


class DummyConn:
    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def send(self, data: bytes) -> None:
        self.sent.append(data)


class FedGameLocalReclaimTests(unittest.TestCase):
    def setUp(self) -> None:
        server.clients.clear()
        server.rooms.clear()
        server.room_owners.clear()
        server.room_games.clear()
        server.room_game_authority.clear()
        server.room_game_tokens.clear()
        server.room_games_parked.clear()
        federation._hub = None
        server._fed_hub = None

    def test_should_not_forward_when_all_seats_local(self) -> None:
        room = "default"
        host = DummyConn()
        guest = DummyConn()
        game = GomokuGame(host, "alice")
        game.try_join(guest, "bob")
        server.room_games[room] = game
        server.room_game_authority[room] = "iPhone"
        server.clients[host] = {"name": "alice", "rooms": {room}, "current_room": room}
        server.clients[guest] = {"name": "bob", "rooms": {room}, "current_room": room}
        server.rooms[room] = {host, guest}

        hub = mock.Mock()
        hub.enabled = True
        hub.node_id = "Mathematics.local"
        hub.sync_game = mock.Mock()
        hub.forward_game_cmd = mock.Mock(return_value=True)

        with mock.patch.object(server, "_local_node_id", return_value="Mathematics.local"), mock.patch.object(
            federation, "get_hub", return_value=hub
        ):
            self.assertFalse(server._should_forward_game(room, "move"))
            self.assertEqual(server.room_game_authority[room], "Mathematics.local")
            hub.forward_game_cmd.assert_not_called()
            hub.sync_game.assert_called()

    def test_still_forwards_when_remote_seat_present(self) -> None:
        room = "default"
        host = DummyConn()
        game = GomokuGame(host, "alice")
        remote = FederatedSeat("iPhone", "bob")
        game.try_join(remote, "bob")
        server.room_games[room] = game
        server.room_game_authority[room] = "iPhone"
        server.clients[host] = {"name": "alice", "rooms": {room}, "current_room": room}
        server.rooms[room] = {host}

        hub = mock.Mock()
        hub.enabled = True
        hub.node_id = "Mathematics.local"

        with mock.patch.object(server, "_local_node_id", return_value="Mathematics.local"), mock.patch.object(
            federation, "get_hub", return_value=hub
        ):
            self.assertTrue(server._should_forward_game(room, "move"))
            self.assertEqual(server.room_game_authority[room], "iPhone")

    def test_still_forwards_when_opponent_only_disconnected(self) -> None:
        """Resume-only node must not reclaim while the other seat is offline here."""
        room = "default"
        host = DummyConn()
        guest = DummyConn()
        game = GomokuGame(host, "alice")
        game.try_join(guest, "bob")
        server._replace_conn_refs(game, guest, DisconnectedSeat("bob"))
        server.room_games[room] = game
        server.room_game_authority[room] = "Mathematics.local"
        server.clients[host] = {"name": "alice", "rooms": {room}, "current_room": room}
        server.rooms[room] = {host}

        hub = mock.Mock()
        hub.enabled = True
        hub.node_id = "iPhone"
        hub.forward_game_cmd = mock.Mock(return_value=True)

        with mock.patch.object(server, "_local_node_id", return_value="iPhone"), mock.patch.object(
            federation, "get_hub", return_value=hub
        ):
            self.assertTrue(server._should_forward_game(room, "move"))
            self.assertEqual(server.room_game_authority[room], "Mathematics.local")


class FedGameSyncProgressTests(unittest.TestCase):
    def setUp(self) -> None:
        server.clients.clear()
        server.rooms.clear()
        server.room_games.clear()
        server.room_game_authority.clear()
        server.room_game_tokens.clear()
        server.room_games_parked.clear()
        federation._hub = None

    def test_gsync_prefers_remote_with_more_progress(self) -> None:
        class LocalGame:
            name = "gomoku"
            state = "playing"
            _history = [(1, 1, 1)]

        class RemoteGame:
            name = "gomoku"
            state = "playing"
            _history = [(1, 1, 1), (2, 2, 2)]

        class FakeHub:
            enabled = True
            node_id = "node-a"

        remote = RemoteGame()
        local = LocalGame()
        server.room_games["lobby"] = local
        server.room_game_authority["lobby"] = "node-a"
        server.room_game_tokens["lobby"] = "zzzz"  # would win token conflict
        notices: list[bytes] = []
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
                                    with mock.patch.object(
                                        server, "_persist_after_game_change"
                                    ):
                                        server._fed_on_game_sync(
                                            "node-b",
                                            "lobby",
                                            "node-b",
                                            "ZmFrZQ==",
                                            "aaaa",
                                        )
        self.assertIs(server.room_games["lobby"], remote)
        self.assertEqual(server.room_game_authority["lobby"], "node-b")
        self.assertIs(server.room_games_parked["lobby"], local)
        self.assertEqual(len(notices), 1)
        self.assertIn("已暂存".encode("utf-8"), notices[0])

    def test_gsync_ignores_stale_lower_progress(self) -> None:
        class LocalGame:
            name = "gomoku"
            state = "playing"
            _history = [(1, 1, 1), (2, 2, 2)]

        class RemoteGame:
            name = "gomoku"
            state = "playing"
            _history = [(1, 1, 1)]

        class FakeHub:
            enabled = True
            node_id = "node-b"

        local = LocalGame()
        server.room_games["lobby"] = local
        server.room_game_authority["lobby"] = "node-a"
        with mock.patch.object(federation, "get_hub", return_value=FakeHub()):
            with mock.patch.object(server.pickle, "loads", return_value=RemoteGame()):
                with mock.patch.object(server, "_rebind_game_services"):
                    with mock.patch.object(server, "_remap_local_game_seats_locked"):
                        server._fed_on_game_sync(
                            "node-a",
                            "lobby",
                            "node-a",
                            "ZmFrZQ==",
                            "tok",
                        )
        self.assertIs(server.room_games["lobby"], local)

    def test_gsync_authority_new_session_beats_stale_replica_fork(self) -> None:
        """After /game new on the authority, a partitioned replica must not win by ply count."""

        class LocalGame:
            name = "gomoku"
            state = "playing"
            _history = [(i, i % 15, i % 15) for i in range(19)]

        class RemoteGame:
            name = "gomoku"
            state = "playing"
            _history = [(i, i % 15, i % 15) for i in range(53)]

        class FakeHub:
            enabled = True
            node_id = "Mathematics.local"

        server.room_games["default"] = LocalGame()
        server.room_game_authority["default"] = "Mathematics.local"
        server.room_game_tokens["default"] = "ffff" + "0" * 28
        with mock.patch.object(federation, "get_hub", return_value=FakeHub()):
            with mock.patch.object(server.pickle, "loads", return_value=RemoteGame()):
                with mock.patch.object(server, "_rebind_game_services"):
                    with mock.patch.object(server, "_remap_local_game_seats_locked"):
                        with mock.patch.object(
                            server, "_federation_push_game_snapshot"
                        ) as push:
                            server._fed_on_game_sync(
                                "iPhone",
                                "default",
                                "iPhone",
                                "ZmFrZQ==",
                                "aaaa" + "0" * 28,
                            )
                            push.assert_called_once()
        self.assertIs(server.room_games["default"].__class__, LocalGame)

    def test_gsync_replica_accepts_authority_new_session_with_fewer_plies(self) -> None:
        """Replica must not ignore a newer authority snapshot just because local has more plies."""

        class LocalGame:
            name = "gomoku"
            state = "playing"
            _history = [(1, 1, 1), (2, 2, 2), (3, 3, 3), (4, 4, 4)]

        class RemoteGame:
            name = "gomoku"
            state = "playing"
            _history = [(1, 1, 1)]

        class FakeHub:
            enabled = True
            node_id = "iPhone"

        remote = RemoteGame()
        server.room_games["default"] = LocalGame()
        server.room_game_authority["default"] = "Mathematics.local"
        server.room_game_tokens["default"] = "aaaa" + "0" * 28
        with mock.patch.object(federation, "get_hub", return_value=FakeHub()):
            with mock.patch.object(server.pickle, "loads", return_value=remote):
                with mock.patch.object(server, "_rebind_game_services"):
                    with mock.patch.object(server, "_remap_local_game_seats_locked"):
                        with mock.patch.object(server, "_persist_after_game_change"):
                            server._fed_on_game_sync(
                                "Mathematics.local",
                                "default",
                                "Mathematics.local",
                                "ZmFrZQ==",
                                "ffff" + "0" * 28,
                            )
        self.assertIs(server.room_games["default"], remote)

    def test_replica_ignores_greq(self) -> None:
        class LocalGame:
            name = "gomoku"
            state = "playing"

        class FakeHub:
            enabled = True
            node_id = "node-b"

        server.room_games["lobby"] = LocalGame()
        server.room_game_authority["lobby"] = "node-a"
        with mock.patch.object(federation, "get_hub", return_value=FakeHub()):
            with mock.patch.object(server, "_federation_push_game_snapshot") as push:
                server._fed_on_game_request("node-c", "lobby")
                push.assert_not_called()

    def test_authority_answers_greq(self) -> None:
        class LocalGame:
            name = "gomoku"
            state = "playing"

        class FakeHub:
            enabled = True
            node_id = "node-a"

        server.room_games["lobby"] = LocalGame()
        server.room_game_authority["lobby"] = "node-a"
        with mock.patch.object(federation, "get_hub", return_value=FakeHub()):
            with mock.patch.object(server, "_federation_push_game_snapshot") as push:
                server._fed_on_game_request("node-b", "lobby")
                push.assert_called_once_with("lobby")

    def test_empty_auth_does_not_answer_greq(self) -> None:
        class LocalGame:
            name = "gomoku"
            state = "playing"

        class FakeHub:
            enabled = True
            node_id = "node-b"

        server.room_games["lobby"] = LocalGame()
        # Missing authority after old session restore must not claim hostship.
        with mock.patch.object(federation, "get_hub", return_value=FakeHub()):
            with mock.patch.object(server, "_federation_push_game_snapshot") as push:
                server._fed_on_game_request("node-a", "lobby")
                push.assert_not_called()

    def test_gsync_persists_immediately(self) -> None:
        class RemoteGame:
            name = "gomoku"
            state = "playing"
            _history = [(1, 1, 1), (2, 2, 2)]

        class FakeHub:
            enabled = True
            node_id = "node-b"

        with mock.patch.object(federation, "get_hub", return_value=FakeHub()):
            with mock.patch.object(server.pickle, "loads", return_value=RemoteGame()):
                with mock.patch.object(server, "_rebind_game_services"):
                    with mock.patch.object(server, "_remap_local_game_seats_locked"):
                        with mock.patch.object(server, "send_oriented_boards"):
                            with mock.patch.object(server, "send_sanguo_hand_views"):
                                with mock.patch.object(
                                    server, "_persist_after_game_change"
                                ) as persist:
                                    server._fed_on_game_sync(
                                        "node-a",
                                        "lobby",
                                        "node-a",
                                        "ZmFrZQ==",
                                        "tok",
                                    )
                                    persist.assert_called_once()


class FedGameParkRestoreTests(unittest.TestCase):
    def setUp(self) -> None:
        server.clients.clear()
        server.rooms.clear()
        server.room_games.clear()
        server.room_game_authority.clear()
        server.room_game_tokens.clear()
        server.room_games_parked.clear()
        federation._hub = None

    def test_unreachable_authority_parks_and_frees_room(self) -> None:
        class ActiveGame:
            name = "chess"
            state = "playing"

        class FakeHub:
            enabled = True
            node_id = "node-a"

            def _link_toward(self, dest):
                return None

        game = ActiveGame()
        server.room_games["lobby"] = game
        server.room_game_authority["lobby"] = "node-b"
        notices: list[bytes] = []
        with mock.patch.object(federation, "get_hub", return_value=FakeHub()):
            with mock.patch.object(
                server,
                "broadcast_room",
                side_effect=lambda r, m, **k: notices.append(m),
            ):
                with mock.patch.object(server, "_persist_after_game_change"):
                    server._fed_handle_unreachable_game_authority("node-b")
        self.assertNotIn("lobby", server.room_games)
        self.assertIs(server.room_games_parked["lobby"], game)
        self.assertEqual(len(notices), 1)
        self.assertIn(b"/game new", notices[0])

    def test_unreachable_restores_parked_over_remote_active(self) -> None:
        class RemoteActive:
            name = "gomoku"
            state = "playing"

        class ParkedLocal:
            name = "chess"
            state = "playing"

        class FakeHub:
            enabled = True
            node_id = "node-a"

            def _link_toward(self, dest):
                return None

        parked = ParkedLocal()
        server.room_games["lobby"] = RemoteActive()
        server.room_game_authority["lobby"] = "node-b"
        server.room_games_parked["lobby"] = parked
        notices: list[bytes] = []
        with mock.patch.object(federation, "get_hub", return_value=FakeHub()):
            with mock.patch.object(server, "_remap_local_game_seats_locked"):
                with mock.patch.object(server, "_rebind_game_services"):
                    with mock.patch.object(
                        server,
                        "broadcast_room",
                        side_effect=lambda r, m, **k: notices.append(m),
                    ):
                        with mock.patch.object(server, "send_oriented_boards"):
                            with mock.patch.object(server, "send_sanguo_hand_views"):
                                with mock.patch.object(
                                    server, "_persist_after_game_change"
                                ):
                                    server._fed_handle_unreachable_game_authority(
                                        "node-b"
                                    )
        self.assertIs(server.room_games["lobby"], parked)
        self.assertNotIn("lobby", server.room_games_parked)
        self.assertEqual(server.room_game_authority["lobby"], "node-a")
        self.assertEqual(len(notices), 1)
        self.assertIn("已恢复".encode("utf-8"), notices[0])

    def test_reconcile_local_auth_pulls_before_push(self) -> None:
        """Stale local authority must greq on link-up, not immediately fan-out."""

        class LocalGame:
            name = "gomoku"
            state = "playing"
            _history = [(1, 1, 1)]

        class RemoteGame:
            name = "gomoku"
            state = "playing"
            _history = [(1, 1, 1), (2, 2, 2), (3, 3, 3)]

        class FakeHub:
            enabled = True
            node_id = "wsl-node"
            peer_count = 1

            def request_game(self, room: str) -> None:
                server._fed_on_game_sync(
                    "mac-node",
                    room,
                    "mac-node",
                    "ZmFrZQ==",
                    "bbbb" + "0" * 28,
                )

            def _link_toward(self, _dest):
                return object()

        remote = RemoteGame()
        server.room_games["default"] = LocalGame()
        server.room_game_authority["default"] = "wsl-node"
        server.room_game_tokens["default"] = "aaaa" + "0" * 28
        pushed: list[str] = []
        with mock.patch.object(federation, "get_hub", return_value=FakeHub()):
            with mock.patch.object(server.pickle, "loads", return_value=remote):
                with mock.patch.object(server, "_rebind_game_services"):
                    with mock.patch.object(server, "_remap_local_game_seats_locked"):
                        with mock.patch.object(server, "broadcast_room"):
                            with mock.patch.object(server, "send_oriented_boards"):
                                with mock.patch.object(server, "send_sanguo_hand_views"):
                                    with mock.patch.object(
                                        server, "_persist_after_game_change"
                                    ):
                                        with mock.patch.object(
                                            server,
                                            "_federation_push_game_snapshot",
                                            side_effect=lambda r: pushed.append(r),
                                        ):
                                            server._federation_reconcile_restored_games()
        self.assertIs(server.room_games["default"], remote)
        self.assertEqual(server.room_game_authority["default"], "mac-node")
        self.assertEqual(pushed, [])

    def test_greq_from_ended_authority_sends_gend(self) -> None:
        class FakeHub:
            enabled = True
            node_id = "mac-node"
            ended: list[tuple[str, str]] = []

            def end_game(self, room: str, authority: str) -> None:
                self.ended.append((room, authority))

        hub = FakeHub()
        server.room_game_authority["default"] = "mac-node"
        with mock.patch.object(federation, "get_hub", return_value=hub):
            with mock.patch.object(server, "_federation_push_game_snapshot") as push:
                server._fed_on_game_request("wsl-node", "default")
                push.assert_not_called()
        self.assertEqual(hub.ended, [("default", "mac-node")])

    def test_reconcile_clears_stale_game_when_peer_ended(self) -> None:
        """WSL reconnect must drop a stale board when Mac already ended the game."""

        class StaleGame:
            name = "chess"
            state = "playing"
            _history = [(1, 1)]

        class FakeHub:
            enabled = True
            node_id = "wsl-node"
            peer_count = 1

            def request_game(self, room: str) -> None:
                server._fed_on_game_end(room, "mac-node")

            def _link_toward(self, _dest):
                return object()

        server.room_games["default"] = StaleGame()
        server.room_game_authority["default"] = "wsl-node"
        server.room_game_tokens["default"] = "aaaa" + "0" * 28
        pushed: list[str] = []
        with mock.patch.object(federation, "get_hub", return_value=FakeHub()):
            with mock.patch.object(server, "_persist_after_game_change"):
                with mock.patch.object(
                    server,
                    "_federation_push_game_snapshot",
                    side_effect=lambda r: pushed.append(r),
                ):
                    server._federation_reconcile_restored_games()
        self.assertNotIn("default", server.room_games)
        self.assertEqual(pushed, [])

    def test_gsync_rejects_stale_revival_after_local_end(self) -> None:
        class StaleRemote:
            name = "chess"
            state = "playing"
            _history = [(1, 1), (2, 2)]

        class FakeHub:
            enabled = True
            node_id = "mac-node"

        server.room_game_authority["default"] = "mac-node"
        with mock.patch.object(federation, "get_hub", return_value=FakeHub()):
            with mock.patch.object(server.pickle, "loads", return_value=StaleRemote()):
                with mock.patch.object(server, "_rebind_game_services"):
                    with mock.patch.object(server, "_persist_after_game_change") as persist:
                        server._fed_on_game_sync(
                            "wsl-node",
                            "default",
                            "wsl-node",
                            "ZmFrZQ==",
                            "aaaa" + "0" * 28,
                        )
                        persist.assert_not_called()
        self.assertNotIn("default", server.room_games)

    def test_reconcile_with_no_peers_parks_remote_auth(self) -> None:
        """Restart while partitioned must free the room without waiting for peer-down."""

        class ActiveGame:
            name = "chess"
            state = "playing"

        class FakeHub:
            enabled = True
            node_id = "node-a"
            peer_count = 0

            def _link_toward(self, dest):
                return None

        game = ActiveGame()
        server.room_games["lobby"] = game
        server.room_game_authority["lobby"] = "node-b"
        with mock.patch.object(federation, "get_hub", return_value=FakeHub()):
            with mock.patch.object(server, "broadcast_room"):
                with mock.patch.object(server, "_persist_after_game_change"):
                    server._federation_reconcile_restored_games()
        self.assertNotIn("lobby", server.room_games)
        self.assertIs(server.room_games_parked["lobby"], game)


if __name__ == "__main__":
    unittest.main()
