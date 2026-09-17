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


class GameCatalogOwnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = object()
        self.other = object()
        server.clients.clear()
        server.room_owners.clear()
        server.room_enabled_games.clear()
        server.room_games.clear()
        server.clients[self.conn] = {
            "name": "alice",
            "current_room": "default",
            "locale": "zh",
        }
        server.clients[self.other] = {
            "name": "bob",
            "current_room": "default",
            "locale": "zh",
        }
        server.room_owners["default"] = self.conn
        server.room_enabled_games["default"] = set(games.GAMES)

    def tearDown(self) -> None:
        server.clients.clear()
        server.room_owners.clear()
        server.room_enabled_games.clear()
        server.room_games.clear()

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


if __name__ == "__main__":
    unittest.main()
