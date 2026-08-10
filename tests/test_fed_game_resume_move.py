"""Federation game resume: remote /game move must re-seat and return gpriv."""

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


class FedGameResumeMoveTests(unittest.TestCase):
    def setUp(self) -> None:
        server.clients.clear()
        server.rooms.clear()
        server.room_owners.clear()
        server.room_games.clear()
        server.room_game_authority.clear()
        server.room_game_tokens.clear()
        federation._hub = None
        server._fed_hub = None

    def test_fed_resolve_actor_reseats_disconnected_as_federated(self) -> None:
        room = "default"
        host = DummyConn()
        game = GomokuGame(host, "alice")
        # Simulate host disconnect on authority node.
        _updated, changed = server._replace_conn_refs(
            game, host, DisconnectedSeat("alice")
        )
        self.assertTrue(changed)
        server.room_games[room] = game
        server.room_game_authority[room] = "Mathematics.local"

        with mock.patch.object(server, "_local_node_id", return_value="Mathematics.local"):
            actor = server._fed_resolve_actor(room, game, "iPhone", "alice", "move")

        self.assertIsInstance(actor, FederatedSeat)
        assert isinstance(actor, FederatedSeat)
        self.assertEqual(actor.node_id, "iPhone")
        self.assertTrue(game.is_seated(actor))
        self.assertIs(server._game_seat_conn_by_name(game, "alice"), actor)

    def test_fed_move_routes_private_to_peer_node(self) -> None:
        room = "default"
        host = DummyConn()
        guest = DummyConn()
        game = GomokuGame(host, "alice")
        game.try_join(guest, "bob")
        server._replace_conn_refs(game, host, DisconnectedSeat("alice"))
        server.room_games[room] = game
        server.room_game_authority[room] = "Mathematics.local"
        server.rooms[room] = set()

        hub = mock.Mock()
        hub.enabled = True
        hub.node_id = "Mathematics.local"
        sent: list[tuple] = []

        def _send_priv(to_node, r, to_name, lines):
            sent.append((to_node, r, to_name, list(lines)))

        hub.send_game_private_to.side_effect = _send_priv
        hub.sync_game = mock.Mock()
        hub.broadcast_room = mock.Mock()

        with mock.patch.object(server, "_local_node_id", return_value="Mathematics.local"), mock.patch.object(
            federation, "get_hub", return_value=hub
        ):
            server._fed_execute_game_cmd("iPhone", room, "iPhone", "alice", "move", "8 8")

        seat = server._game_seat_conn_by_name(game, "alice")
        self.assertIsInstance(seat, FederatedSeat)
        assert isinstance(seat, FederatedSeat)
        self.assertEqual(seat.node_id, "iPhone")
        # Successful gomoku moves often have empty priv and rely on room broadcast + gsync.
        self.assertTrue(
            hub.sync_game.called or sent,
            "expected game sync and/or private reply after federated move",
        )

        # Illegal follow-up must gpriv an error to the remote node (not silent).
        sent.clear()
        with mock.patch.object(server, "_local_node_id", return_value="Mathematics.local"), mock.patch.object(
            federation, "get_hub", return_value=hub
        ):
            server._fed_execute_game_cmd("iPhone", room, "iPhone", "alice", "move", "8 8")
        self.assertTrue(sent, "expected gpriv error for illegal/out-of-turn move")
        self.assertEqual(sent[0][0], "iPhone")


if __name__ == "__main__":
    unittest.main()
