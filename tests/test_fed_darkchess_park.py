#!/usr/bin/env python3
"""Unit coverage for darkchess-vs-doushou federation park/gend safety."""

from __future__ import annotations

import base64
import pickle
import unittest
from unittest import mock

import federation
import server


class _Playing:
    def __init__(self, name: str, *, face: int = 0, updated: float = 1.0):
        self.name = name
        self.state = "playing"
        self.face_up = set(range(face))
        self.session_updated_at = updated
        self.first_name = "a"
        self.second_name = "b"


class DarkchessDoushouFedTests(unittest.TestCase):
    def setUp(self) -> None:
        server.room_games.clear()
        server.room_games_parked.clear()
        server.room_game_authority.clear()
        server.room_game_tokens.clear()
        server.room_game_provisional.clear()
        server._greq_until.clear()

    def test_progressed_darkchess_beats_fresh_doushou_on_greq(self) -> None:
        dark = _Playing("darkchess", face=5, updated=10.0)
        dou = _Playing("doushou", face=0, updated=99.0)
        pick = server._game_sync_should_keep_local(
            local_game=dark,
            remote_game=dou,
            local_auth="node-a",
            authority="node-b",
            local_id="node-a",
            local_token="aaaa",
            conflict_token="bbbb",
            greq=True,
        )
        self.assertTrue(pick)

    def test_park_stacks_different_kinds(self) -> None:
        first = _Playing("darkchess", face=3)
        second = _Playing("doushou", face=0)
        server._park_room_game_locked("default", first)
        server._park_room_game_locked("default", second)
        parked = server.room_games_parked["default"]
        names = {getattr(g, "name", None) for g in server._parked_games_list(parked)}
        self.assertEqual(names, {"darkchess", "doushou"})
        best = server._best_parked_game(parked)
        self.assertEqual(getattr(best, "name", None), "darkchess")

    def test_gend_does_not_clear_parked(self) -> None:
        dark = _Playing("darkchess", face=2)
        dou = _Playing("doushou", face=0)
        server.room_games["default"] = dou
        server.room_game_authority["default"] = "node-b"
        server.room_game_tokens["default"] = "tok-dou"
        server.room_games_parked["default"] = dark

        class FakeHub:
            enabled = True
            node_id = "node-a"

        with mock.patch.object(federation, "get_hub", return_value=FakeHub()):
            server._fed_on_game_end("default", "node-b", "tok-dou")
        self.assertNotIn("default", server.room_games)
        self.assertIs(server.room_games_parked["default"], dark)

    def test_notify_end_keeps_parked(self) -> None:
        dark = _Playing("darkchess", face=1)
        server.room_games_parked["default"] = dark
        server.room_game_tokens["default"] = "tok"

        class FakeHub:
            enabled = True
            node_id = "node-a"

            def end_game(self, room, authority, token=""):
                return None

        with mock.patch.object(federation, "get_hub", return_value=FakeHub()):
            server._federation_notify_game_end("default")
        self.assertIs(server.room_games_parked["default"], dark)

    def test_swap_parked_promotes_darkchess(self) -> None:
        dark = _Playing("darkchess", face=4)
        dou = _Playing("doushou", face=0)
        server.room_games["default"] = dou
        server.room_games_parked["default"] = dark

        class FakeHub:
            enabled = True
            node_id = "node-a"

        with mock.patch.object(federation, "get_hub", return_value=FakeHub()):
            out = server._swap_parked_game_locked("default")
        self.assertIs(out, dark)
        self.assertIs(server.room_games["default"], dark)
        parked = server._parked_games_list(server.room_games_parked.get("default"))
        self.assertEqual([getattr(g, "name", None) for g in parked], ["doushou"])


if __name__ == "__main__":
    unittest.main()
