import unittest

from games import GomokuGame, parse_undo_action


class TestUndoActionParse(unittest.TestCase):
    def test_acc_maps_to_accept(self) -> None:
        action, err = parse_undo_action("acc")
        self.assertIsNone(err)
        self.assertEqual(action, "accept")

    def test_partial_accept_prefix(self) -> None:
        action, err = parse_undo_action("acce")
        self.assertIsNone(err)
        self.assertEqual(action, "accept")

    def test_unknown_reports_hint(self) -> None:
        action, err = parse_undo_action("foobar")
        self.assertIsNone(action)
        self.assertIn("accept", err or "")


class TestUndoNotifications(unittest.TestCase):
    def test_gomoku_undo_request_notifies_opponent_privately(self):
        c1 = object()
        c2 = object()
        game = GomokuGame(c1, "A")
        game.try_join(c2, "B")
        game.try_move(c1, "7 7")
        game.try_move(c2, "8 8")

        _priv, _bcast, _ = game.request_undo(c2)
        queued = game.drain_extra_privates()

        self.assertEqual(len(queued), 1)
        self.assertIs(queued[0][0], c1)
        self.assertTrue(any("请求悔棋" in ln for ln in queued[0][1]))

    def test_gomoku_undo_accept_notifies_requester_privately(self):
        c1 = object()
        c2 = object()
        game = GomokuGame(c1, "A")
        game.try_join(c2, "B")
        game.try_move(c1, "7 7")
        game.try_move(c2, "8 8")

        game.request_undo(c2)
        game.drain_extra_privates()
        _priv, _bcast, _ = game.accept_undo(c1)
        queued = game.drain_extra_privates()

        self.assertEqual(len(queued), 1)
        self.assertIs(queued[0][0], c2)
        self.assertTrue(any("同意悔棋" in ln for ln in queued[0][1]))


if __name__ == "__main__":
    unittest.main()

