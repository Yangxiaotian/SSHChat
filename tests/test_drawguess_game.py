import unittest

from games import GAMES, create_game, resolve_game_name
from tests.test_new_games_common import DummyConn


class DrawGuessGameTests(unittest.TestCase):
    def test_registered_and_aliases(self) -> None:
        self.assertIn("drawguess", GAMES)
        self.assertEqual(resolve_game_name("你画我猜"), "drawguess")
        self.assertEqual(resolve_game_name("pictionary"), "drawguess")
        self.assertEqual(resolve_game_name("draw-guess"), "drawguess")

    def test_join_start_guess_and_score(self) -> None:
        host = DummyConn("host")
        guesser = DummyConn("bob")
        game = create_game("drawguess", host, "alice")
        self.assertEqual(game.state, "waiting")

        err, bcast, ended = game.try_join(guesser, "bob")
        self.assertEqual(err, [])
        self.assertFalse(ended)
        self.assertTrue(any("start" in line.lower() or "开始" in line for line in bcast))

        err, bcast, ended = game.try_move(host, "start")
        self.assertEqual(err, [])
        self.assertFalse(ended)
        self.assertEqual(game.state, "drawing")
        self.assertEqual(game.drawer, "alice")
        self.assertTrue(game.secret)
        self.assertIn("clear", game.drain_canvas_actions())

        privates = game.drain_extra_privates()
        self.assertTrue(any(game.secret in "\n".join(lines) for _conn, lines in privates))

        wrong, _b, _e = game.try_move(guesser, "guess __not_a_word__")
        self.assertTrue(wrong)
        self.assertEqual(game.scores["bob"], 0)

        game.secret = "猫"
        err, bcast, ended = game.try_move(guesser, "guess 猫")
        self.assertEqual(err, [])
        self.assertFalse(ended)
        self.assertEqual(game.scores["bob"], 2)
        self.assertEqual(game.scores["alice"], 1)
        self.assertTrue(any("猜对" in line or "猫" in line for line in bcast))
        self.assertEqual(game.round, 2)
        self.assertIn("clear", game.drain_canvas_actions())

    def test_drawer_cannot_guess_and_skip_reveals(self) -> None:
        host = DummyConn("host")
        other = DummyConn("other")
        game = create_game("drawguess", host, "alice")
        game.try_join(other, "bob")
        game.try_move(host, "start")
        game.drain_canvas_actions()
        game.drain_extra_privates()
        secret = game.secret
        self.assertIsNotNone(secret)

        err, _b, _e = game.try_move(host, f"guess {secret}")
        self.assertTrue(err)

        err, bcast, ended = game.try_move(host, "skip")
        self.assertEqual(err, [])
        self.assertFalse(ended)
        self.assertTrue(any(secret in line for line in bcast))
        self.assertEqual(game.round, 2)

    def test_min_two_players_to_start(self) -> None:
        host = DummyConn("host")
        game = create_game("drawguess", host, "alice")
        err, _b, _e = game.try_move(host, "start")
        self.assertTrue(any("2" in line for line in err))


if __name__ == "__main__":
    unittest.main()
