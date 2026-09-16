import unittest

from games import GomokuGame, parse_draw_action


class TestDrawActionParse(unittest.TestCase):
    def test_empty_is_request(self) -> None:
        action, err = parse_draw_action("")
        self.assertIsNone(err)
        self.assertEqual(action, "request")

    def test_acc_maps_to_accept(self) -> None:
        action, err = parse_draw_action("acc")
        self.assertIsNone(err)
        self.assertEqual(action, "accept")

    def test_unknown_reports_hint(self) -> None:
        action, err = parse_draw_action("foobar")
        self.assertIsNone(action)
        self.assertIn("accept", err or "")


class TestDrawOffer(unittest.TestCase):
    def test_gomoku_draw_request_notifies_opponent(self) -> None:
        c1 = object()
        c2 = object()
        game = GomokuGame(c1, "A")
        game.try_join(c2, "B")
        game.try_move(c1, "7 7")

        priv, bcast, ended = game.request_draw(c1)
        self.assertFalse(ended)
        self.assertTrue(any("求和" in ln for ln in priv))
        self.assertTrue(any("求和" in ln for ln in bcast))
        queued = game.drain_extra_privates()
        self.assertEqual(len(queued), 1)
        self.assertIs(queued[0][0], c2)
        self.assertTrue(any("请求求和" in ln for ln in queued[0][1]))

    def test_gomoku_draw_accept_ends_as_draw(self) -> None:
        c1 = object()
        c2 = object()
        game = GomokuGame(c1, "A")
        game.try_join(c2, "B")
        game.try_move(c1, "7 7")
        game.request_draw(c1)
        game.drain_extra_privates()

        priv, bcast, ended = game.accept_draw(c2)
        self.assertTrue(ended)
        self.assertEqual(game.state, "ended")
        self.assertTrue(any("平" in ln for ln in bcast + priv))
        queued = game.drain_extra_privates()
        self.assertEqual(len(queued), 1)
        self.assertIs(queued[0][0], c1)

    def test_ai_game_rejects_draw(self) -> None:
        human = object()
        game = GomokuGame(human, "A", ai_level="normal")
        priv, bcast, ended = game.request_draw(human)
        self.assertFalse(ended)
        self.assertEqual(bcast, [])
        self.assertTrue(any("AI" in ln for ln in priv))

    def test_draw_blocked_while_undo_pending(self) -> None:
        c1 = object()
        c2 = object()
        game = GomokuGame(c1, "A")
        game.try_join(c2, "B")
        game.try_move(c1, "7 7")
        game.try_move(c2, "8 8")
        game.request_undo(c2)

        priv, _bcast, ended = game.request_draw(c1)
        self.assertFalse(ended)
        self.assertTrue(any("悔棋" in ln for ln in priv))


if __name__ == "__main__":
    unittest.main()
