"""NiuTou persistent Elo + /game move pick/row/bot completions."""

from __future__ import annotations

import tempfile
import unittest

from games import NiuTouWangGame, create_game
from ratings import GameRatingStore, is_rated_game
from sshchat_gui import _command_completions


class TestNiuTouRatings(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = GameRatingStore(f"{self.tmp.name}/ratings.json")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_niutou_is_rated_game(self) -> None:
        self.assertTrue(is_rated_game("niutou"))

    def test_create_game_wires_rating_store(self) -> None:
        game = create_game("niutou", object(), "Alice", rating_store=self.store)
        self.assertIs(game.rating_store, self.store)

    def test_legacy_pickle_without_rating_store_rebinds(self) -> None:
        """Pre-rating niutou sessions omit rating_store; rebind must still attach."""
        import server

        game = NiuTouWangGame(object(), "Alice", rating_store=self.store)
        # Simulate an older pickle that never had these attrs:
        game.__dict__.pop("rating_store", None)
        game.__dict__.pop("_ratings_settled", None)
        self.assertFalse(hasattr(game, "rating_store"))
        prev = server.rating_store
        server.rating_store = self.store
        try:
            server._rebind_game_services(game)
        finally:
            server.rating_store = prev
        self.assertIs(game.rating_store, self.store)
        # show/seats must not AttributeError
        lines = game.seats()
        self.assertTrue(lines)

    def test_two_humans_settle_persistent_rating(self) -> None:
        a, b = object(), object()
        game = NiuTouWangGame(a, "Alice", rating_store=self.store)
        game.try_join(b, "Bob")
        game.bot_names.clear()
        game.bot_conns.clear()
        # Force a finished ranking without playing 10 turns.
        game.state = "ended"
        game.penalty = {"Alice": 3, "Bob": 12}
        game.hands = {"Alice": [], "Bob": []}
        lines = game._settle_ratings()
        self.assertTrue(any("积分结算" in line for line in lines))
        alice = self.store.profile("niutou", "Alice")
        bob = self.store.profile("niutou", "Bob")
        self.assertGreater(alice["rating"], bob["rating"])
        self.assertEqual(alice["wins"], 1)
        self.assertEqual(bob["losses"], 1)

    def test_solo_with_bots_is_practice(self) -> None:
        host = object()
        game = NiuTouWangGame(host, "Alice", rating_store=self.store)
        _, start_lines, _ = game.try_move(host, "start")
        self.assertTrue(game.bot_names)
        game.state = "ended"
        game.penalty = {n: i for i, (_c, n) in enumerate(game.players)}
        game.hands = {n: [] for _c, n in game.players}
        lines = game._settle_ratings()
        self.assertEqual(lines, ["练习局：本局不计入持久化积分。"])
        self.assertEqual(self.store.profile("niutou", "Alice")["games"], 0)
        self.assertTrue(any("机器人" in line for line in start_lines) or game.bot_names)


class TestNiuTouMoveCompletions(unittest.TestCase):
    def test_pick_row_start_bot_suggested(self) -> None:
        items = _command_completions("/game move p")
        self.assertIn("/game move pick", items)
        self.assertIn("/game move play", items)
        items = _command_completions("/game move ")
        for verb in ("pick", "play", "row", "start", "bot"):
            self.assertIn(f"/game move {verb}", items)

    def test_bot_and_row_args(self) -> None:
        self.assertEqual(
            _command_completions("/game move bot "),
            [
                "/game move bot easy",
                "/game move bot hard",
                "/game move bot pro",
            ],
        )
        self.assertEqual(
            _command_completions("/game move row "),
            [
                "/game move row 1",
                "/game move row 2",
                "/game move row 3",
                "/game move row 4",
            ],
        )


if __name__ == "__main__":
    unittest.main()
