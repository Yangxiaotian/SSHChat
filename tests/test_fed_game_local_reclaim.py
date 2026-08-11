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
        server.room_games["lobby"] = LocalGame()
        server.room_game_authority["lobby"] = "node-a"
        server.room_game_tokens["lobby"] = "zzzz"  # would win token conflict
        with mock.patch.object(federation, "get_hub", return_value=FakeHub()):
            with mock.patch.object(server.pickle, "loads", return_value=remote):
                with mock.patch.object(server, "_rebind_game_services"):
                    with mock.patch.object(server, "_remap_local_game_seats_locked"):
                        with mock.patch.object(server, "broadcast_room") as br:
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
                                        br.assert_not_called()
        self.assertIs(server.room_games["lobby"], remote)
        self.assertEqual(server.room_game_authority["lobby"], "node-b")

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


if __name__ == "__main__":
    unittest.main()
