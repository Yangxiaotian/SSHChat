import unittest

from games import GAMES, create_game


class DummyConn:
    def __init__(self, name):
        self.name = name


class NewGameRegistrationTests(unittest.TestCase):
    def test_new_games_are_registered_and_creatable(self):
        for name in ("reversi", "darkchess", "battleship", "junqi"):
            self.assertIn(name, GAMES)
            game = create_game(name, DummyConn("alice"), "alice")
            self.assertEqual(game.name, name)
            self.assertEqual(game.state, "waiting")


if __name__ == "__main__":
    unittest.main()
