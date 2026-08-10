"""Local-only seats should reclaim stale remote game authority."""

from __future__ import annotations

import unittest
from unittest import mock

import federation
import server
from games import GomokuGame
from session_store import FederatedSeat


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


if __name__ == "__main__":
    unittest.main()
