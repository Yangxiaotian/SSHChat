#!/usr/bin/env python3
"""Room-owner game catalog: list off, on/off all."""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

tmpdir = tempfile.mkdtemp(prefix="sshchat_game_catalog_")
os.environ["SSHCHAT_GAME_SESSIONS"] = os.path.join(tmpdir, "sessions.json")
os.environ["SSHCHAT_DEFAULT_LOCALE"] = "zh"

import games  # noqa: E402
import server  # noqa: E402
from session_store import DisconnectedSeat  # noqa: E402


class _DummyConn:
    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def send(self, data: bytes) -> None:
        self.sent.append(data)

    def close(self) -> None:
        pass


class GameCatalogOwnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = _DummyConn()
        self.other = _DummyConn()
        server.clients.clear()
        server.room_owners.clear()
        server.room_enabled_games.clear()
        server.room_enabled_game_revs.clear()
        server.room_games.clear()
        server.rooms.clear()
        server.clients[self.conn] = {
            "name": "alice",
            "current_room": "default",
            "locale": "zh",
            "rooms": {"default"},
        }
        server.clients[self.other] = {
            "name": "bob",
            "current_room": "default",
            "locale": "zh",
            "rooms": {"default"},
        }
        server.rooms["default"] = {self.conn, self.other}
        server.room_owners["default"] = self.conn
        server.room_enabled_games["default"] = set(games.GAMES)

    def tearDown(self) -> None:
        server.clients.clear()
        server.room_owners.clear()
        server.room_enabled_games.clear()
        server.room_enabled_game_revs.clear()
        server.room_games.clear()
        server.rooms.clear()

    def _lines(self, conn, name: str, payload: str) -> list[str]:
        out: list[str] = []

        def capture(_c, line: str) -> None:
            out.append(line)

        with mock.patch.object(server, "send_line", side_effect=capture):
            with mock.patch.object(server, "_mark_sessions_dirty"):
                server._handle_game(conn, name, "default", payload)
        return out

    def test_list_off_owner_only(self) -> None:
        server.room_enabled_games["default"] = {"chess"}
        denied = self._lines(self.other, "bob", "/game list off")
        self.assertTrue(any("只有房主" in ln for ln in denied), denied)
        ok = self._lines(self.conn, "alice", "/game list off")
        blob = " ".join(ok)
        self.assertIn("已下线", blob)
        self.assertIn("gomoku", blob)
        # Whole-token check: "chess" must not appear as its own offline id.
        offline_ids = []
        for ln in ok:
            if "已下线" in ln and "：" in ln:
                offline_ids = [p.strip() for p in ln.split("：", 1)[1].split(",")]
        self.assertNotIn("chess", offline_ids)
        self.assertIn("darkchess", offline_ids)

    def test_on_off_all(self) -> None:
        server.room_enabled_games["default"] = {"chess"}
        on_lines = self._lines(self.conn, "alice", "/game on all")
        self.assertTrue(any("全部游戏" in ln for ln in on_lines), on_lines)
        self.assertEqual(server.room_enabled_games["default"], set(games.GAMES))

        off_lines = self._lines(self.conn, "alice", "/game off all")
        self.assertTrue(any("下线全部" in ln or "已下线全部" in ln for ln in off_lines), off_lines)
        self.assertEqual(server.room_enabled_games["default"], set())

    def test_off_all_keeps_active(self) -> None:
        class FakeGame:
            name = "gomoku"
            state = "playing"

        server.room_games["default"] = FakeGame()
        server.room_enabled_games["default"] = set(games.GAMES)
        lines = self._lines(self.conn, "alice", "/game off all")
        self.assertEqual(server.room_enabled_games["default"], {"gomoku"})
        self.assertTrue(any("gomoku" in ln and "保持上线" in ln for ln in lines), lines)

    def test_stale_disconnected_owner_heals_to_live_member(self) -> None:
        """Ghost DisconnectedSeat owner must not block /game on for everyone."""
        server.room_owners["default"] = DisconnectedSeat("ghost")
        server.room_enabled_games["default"] = {"chess"}
        # alice sorts before bob → alice becomes owner
        lines = self._lines(self.conn, "alice", "/game on all")
        self.assertTrue(any("全部游戏" in ln for ln in lines), lines)
        self.assertIs(server.room_owners["default"], self.conn)
        self.assertEqual(server.room_enabled_games["default"], set(games.GAMES))

    def test_disconnected_seat_same_nick_can_reclaim_catalog(self) -> None:
        server.room_owners["default"] = DisconnectedSeat("alice")
        server.room_enabled_games["default"] = set()
        lines = self._lines(self.conn, "alice", "/game on all")
        self.assertTrue(any("全部游戏" in ln for ln in lines), lines)
        self.assertIs(server.room_owners["default"], self.conn)

    def test_on_all_pushes_federation_catalog(self) -> None:
        server.room_enabled_games["default"] = {"chess"}
        pushed: list[str] = []
        with mock.patch.object(
            server, "_federation_push_game_catalog", side_effect=pushed.append
        ):
            lines = self._lines(self.conn, "alice", "/game on all")
        self.assertTrue(any("全部游戏" in ln for ln in lines), lines)
        self.assertEqual(pushed, ["default"])
        self.assertGreater(server.room_enabled_game_revs.get("default", 0), 0)

    def test_owner_disconnect_mid_game_hands_off_to_remaining(self) -> None:
        class FakeGame:
            name = "gomoku"
            state = "playing"
            players = None

            def __init__(self, seat, nick: str) -> None:
                self.players = [(seat, nick)]

            def on_player_leave(self, *_a, **_k):
                return [], [], False

        owner = self.conn
        peer = self.other
        game = FakeGame(owner, "alice")
        server.room_games["default"] = game
        server.room_owners["default"] = owner
        with mock.patch.object(server, "_safe_persist_sessions_now"):
            with mock.patch.object(server, "_mark_sessions_dirty"):
                server.remove_client(owner)
        self.assertIs(server.room_owners["default"], peer)
        self.assertIsInstance(game.players[0][0], DisconnectedSeat)
        # Remaining member can manage catalog
        server.room_enabled_games["default"] = set()
        lines = self._lines(peer, "bob", "/game on all")
        self.assertTrue(any("全部游戏" in ln for ln in lines), lines)


if __name__ == "__main__":
    unittest.main()
