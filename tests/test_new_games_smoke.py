import unittest

from games import create_game
from tests.test_battleship_game import place_standard
from tests.test_junqi_game import place_side
from tests.test_new_games_common import DummyConn


class NewGamesSmokeTests(unittest.TestCase):
    def test_reversi_create_join_and_first_move(self) -> None:
        black = DummyConn("black")
        white = DummyConn("white")
        game = create_game("reversi", black, "black")

        error, _broadcast, ended = game.try_join(white, "white")
        self.assertEqual(error, [])
        self.assertFalse(ended)
        self.assertEqual(game.state, "playing")
        self.assertEqual(game.try_move(black, "3 4")[0], [])
        self.assertEqual(game.turn, 2)

    def test_darkchess_create_join_and_flip_sequence(self) -> None:
        first = DummyConn("first")
        second = DummyConn("second")
        game = create_game("darkchess", first, "first")

        error, _broadcast, ended = game.try_join(second, "second")
        self.assertEqual(error, [])
        self.assertFalse(ended)
        self.assertEqual(game.state, "playing")
        self.assertEqual(game.try_move(first, "flip 1 1")[0], [])
        self.assertEqual(game.try_move(second, "flip 1 2")[0], [])
        self.assertEqual(game.turn, 1)

    def test_battleship_create_join_ready_and_fire(self) -> None:
        first = DummyConn("first")
        second = DummyConn("second")
        game = create_game("battleship", first, "first")
        self.assertEqual(game.try_join(second, "second")[0], [])
        place_standard(game, first)
        place_standard(game, second)
        self.assertEqual(game.try_move(first, "ready")[0], [])
        self.assertEqual(game.try_move(second, "ready")[0], [])
        self.assertEqual(game.state, "playing")
        self.assertEqual(game.try_move(first, "fire 10 10")[0], [])
        self.assertEqual(game.turn, 2)

    def test_junqi_create_join_setup_and_ready(self) -> None:
        red = DummyConn("red")
        blue = DummyConn("blue")
        game = create_game("junqi", red, "red")
        self.assertEqual(game.try_join(blue, "blue")[0], [])
        place_side(game, red, range(1, 6), 2)
        place_side(game, blue, range(8, 13), 2)
        self.assertEqual(game.try_move(red, "ready")[0], [])
        self.assertEqual(game.try_move(blue, "ready")[0], [])
        self.assertEqual(game.state, "playing")


if __name__ == "__main__":
    unittest.main()
