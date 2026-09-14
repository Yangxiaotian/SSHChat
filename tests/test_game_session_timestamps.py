"""Session timestamps on game objects."""

from __future__ import annotations

import unittest

from games import create_game, game_session_updated_at, stamp_new_session, touch_session


class DummyConn:
    pass


class GameSessionTimestampTests(unittest.TestCase):
    def test_create_game_stamps_session(self) -> None:
        game = create_game("gomoku", DummyConn(), "alice")
        self.assertGreater(game.session_started_at, 0)
        self.assertEqual(game.session_started_at, game.session_updated_at)

    def test_touch_session_bumps_updated_at(self) -> None:
        game = create_game("gomoku", DummyConn(), "alice")
        started = game.session_started_at
        updated = game.session_updated_at
        touch_session(game)
        self.assertEqual(game.session_started_at, started)
        self.assertGreaterEqual(game.session_updated_at, updated)
        self.assertGreaterEqual(game_session_updated_at(game), started)


if __name__ == "__main__":
    unittest.main()
