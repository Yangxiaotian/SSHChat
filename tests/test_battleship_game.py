import unittest

from games import create_game
from tests.test_new_games_common import DummyConn


def place_standard(game, conn):
    placements = (
        "place carrier 1 1 h",
        "place battleship 3 1 h",
        "place cruiser 5 1 h",
        "place submarine 7 1 h",
        "place destroyer 9 1 h",
    )
    for command in placements:
        assert game.try_move(conn, command)[0] == []


class BattleshipGameTests(unittest.TestCase):
    def setUp(self):
        self.black = DummyConn("black")
        self.white = DummyConn("white")
        self.game = create_game("battleship", self.black, "black")
        self.game.try_join(self.white, "white")

    def test_overlapping_and_touching_ships_are_rejected(self):
        self.assertEqual(self.game.try_move(self.black, "place carrier 1 1 h")[0], [])
        error, _, _ = self.game.try_move(self.black, "place destroyer 1 2 h")
        self.assertTrue(error)
        error, _, _ = self.game.try_move(self.black, "place destroyer 2 6 h")
        self.assertTrue(error)

    def test_both_players_must_ready_before_fire(self):
        place_standard(self.game, self.black)
        place_standard(self.game, self.white)
        error, _, _ = self.game.try_move(self.black, "fire 10 10")
        self.assertTrue(error)
        self.assertEqual(self.game.try_move(self.black, "ready")[0], [])
        self.assertEqual(self.game.try_move(self.white, "ready")[0], [])
        self.assertEqual(self.game.state, "playing")

    def test_duplicate_shot_is_rejected_without_extra_turn(self):
        place_standard(self.game, self.black)
        place_standard(self.game, self.white)
        self.game.try_move(self.black, "ready")
        self.game.try_move(self.white, "ready")
        self.assertEqual(self.game.try_move(self.black, "fire 10 10")[0], [])
        self.assertEqual(self.game.try_move(self.white, "fire 10 10")[0], [])
        error, _, _ = self.game.try_move(self.black, "fire 10 10")
        self.assertTrue(error)
        self.assertEqual(self.game.turn, 1)

    def test_private_show_does_not_expose_enemy_unsunk_ships(self):
        place_standard(self.game, self.black)
        place_standard(self.game, self.white)
        red_view = "\n".join(self.game.show(self.black))
        white_view = "\n".join(self.game.show(self.white))
        self.assertEqual(red_view.count(" S"), 17)
        self.assertEqual(white_view.count(" S"), 17)
        self.assertNotIn("enemy carrier", red_view.lower())
        self.assertNotIn("enemy carrier", white_view.lower())

    def test_show_marks_an_opponent_shot_on_my_fleet(self):
        self.game.state = "playing"
        self.game._last = (0, 0)
        self.game._last_player = 2
        row = next(line for line in self.game.show(self.black) if line.lstrip().startswith("1 "))
        self.assertIn("!.", row)


if __name__ == "__main__":
    unittest.main()
