import unittest

from games import HoldemGame, ZhaJinHuaGame


class PokerGamesFlowTests(unittest.TestCase):
    def test_zjh_compare_bot_by_name_and_seat(self):
        host_conn = object()
        bot_conn = object()

        def build_game() -> ZhaJinHuaGame:
            game = ZhaJinHuaGame(host_conn, "zouyu")
            game.players = [(host_conn, "zouyu"), (bot_conn, "R1")]
            game.bot_names = {"R1"}
            game.bot_conns = {"R1": bot_conn}
            game.state = "playing"
            game.folded = set()
            game.looked = set()
            game.stacks = {"zouyu": 1000, "R1": 1000}
            game.cards = {
                "zouyu": ["AS", "KS", "QS"],
                "R1": ["2C", "3D", "4H"],
            }
            game.pot = 0
            game.current_bet = 1
            game.turn_idx = 0
            return game

        game = build_game()
        err, bcast, _done = game.try_move(host_conn, "compare R1")
        self.assertEqual(err, [])
        self.assertTrue(any("比牌" in line for line in bcast))

        game = build_game()
        err2, bcast2, _done2 = game.try_move(host_conn, "compare #2")
        self.assertEqual(err2, [])
        self.assertTrue(any("比牌" in line for line in bcast2))

    def test_holdem_round_reaches_showdown(self):
        c1 = object()
        c2 = object()
        game = HoldemGame(c1, "A")
        game.rng.seed(7)
        err_join, b_join, _ = game.try_join(c2, "B")
        self.assertEqual(err_join, [])
        self.assertTrue(any("加入了德州扑克" in line for line in b_join))

        err_start, b_start, _ = game.try_move(c1, "start")
        self.assertEqual(err_start, [])
        self.assertTrue(any("德州扑克开始" in line for line in b_start))

        guard = 0
        while game.state == "playing" and guard < 80:
            guard += 1
            cur_name = game.players[game.turn_idx][1]
            cur_conn = c1 if cur_name == "A" else c2
            to_call = game._to_call(cur_name)  # noqa: SLF001 - tested gameplay flow
            cmd = "call" if to_call > 0 else "check"
            err, _b, _done = game.try_move(cur_conn, cmd)
            self.assertEqual(err, [])

        self.assertEqual(game.state, "ended")
        self.assertEqual(len(game.board), 5)
        self.assertEqual(sum(game.stacks.values()), len(game.players) * 1000)

    def test_holdem_accepts_chinese_move_aliases(self):
        host_conn = object()
        game = HoldemGame(host_conn, "房主")
        game.rng.seed(11)
        err_start, b_start, _ = game.try_move(host_conn, "开始")
        self.assertEqual(err_start, [])
        self.assertTrue(any("德州扑克开始" in line for line in b_start))
        self.assertEqual(game.state, "playing")
        self.assertEqual(game.players[game.turn_idx][1], "房主")

        err_check, b_check, _ = game.try_move(host_conn, "过牌")
        self.assertEqual(err_check, [])
        self.assertTrue(any("过牌" in line for line in b_check))

        game2 = HoldemGame(host_conn, "房主")
        game2.rng.seed(11)
        game2.try_move(host_conn, "start")
        err_call, b_call, _ = game2.try_move(host_conn, "跟注")
        self.assertEqual(err_call, [])
        self.assertTrue(any("过牌" in line for line in b_call))

        game3 = HoldemGame(host_conn, "房主")
        game3.rng.seed(11)
        game3.try_move(host_conn, "start")
        err_raise, _, _ = game3.try_move(host_conn, "加注 5")
        self.assertEqual(err_raise, [])

        game4 = HoldemGame(host_conn, "房主")
        game4.rng.seed(11)
        game4.try_move(host_conn, "start")
        err_allin, b_allin, _ = game4.try_move(host_conn, "全下")
        self.assertEqual(err_allin, [])
        self.assertTrue(any("全下" in line for line in b_allin))

    def test_holdem_start_auto_fills_at_least_three_bots(self):
        host_conn = object()
        peer_conn = object()
        game = HoldemGame(host_conn, "host")
        game.rng.seed(23)

        err_join, _b_join, _ = game.try_join(peer_conn, "peer")
        self.assertEqual(err_join, [])

        err_start, b_start, _ = game.try_move(host_conn, "start")
        self.assertEqual(err_start, [])
        self.assertEqual(game.state, "playing")

        bot_count = sum(1 for _c, n in game.players if n in game.bot_names)
        self.assertGreaterEqual(bot_count, 3)
        self.assertEqual(len(game.players), 5)
        self.assertTrue(any("机器人：" in line for line in b_start))

    def test_zjh_starts_blind_and_reveals_after_look(self):
        host_conn = object()
        peer_conn = object()
        game = ZhaJinHuaGame(host_conn, "host")
        game.rng.seed(5)
        game.try_join(peer_conn, "peer")

        err_start, _b_start, _ = game.try_move(host_conn, "start")
        self.assertEqual(err_start, [])
        lines = game.show(host_conn)
        self.assertTrue(any("闷牌中" in line for line in lines))

        err_look, _b_look, _ = game.try_move(host_conn, "look")
        self.assertEqual(err_look, [])
        lines2 = game.show(host_conn)
        self.assertTrue(any(line.startswith("你的手牌：") and "闷牌中" not in line for line in lines2))

    def test_zjh_two_humans_start_without_auto_bots(self):
        host_conn = object()
        peer_conn = object()
        game = ZhaJinHuaGame(host_conn, "host")
        game.try_join(peer_conn, "peer")

        err_start, b_start, _ = game.try_move(host_conn, "start")
        self.assertEqual(err_start, [])
        self.assertEqual(game.state, "playing")
        self.assertEqual(len(game.players), 2)
        self.assertEqual(len(game.bot_names), 0)
        self.assertFalse(any("机器人：" in line for line in b_start))

    def test_zjh_start_bot_adds_robots(self):
        host_conn = object()
        peer_conn = object()
        game = ZhaJinHuaGame(host_conn, "host")
        game.try_join(peer_conn, "peer")

        err_start, b_start, _ = game.try_move(host_conn, "start bot")
        self.assertEqual(err_start, [])
        self.assertGreaterEqual(len(game.bot_names), 1)
        self.assertTrue(any("机器人：" in line for line in b_start))

    def test_holdem_starts_blind_and_reveals_after_look(self):
        host_conn = object()
        peer_conn = object()
        game = HoldemGame(host_conn, "host")
        game.rng.seed(9)
        game.try_join(peer_conn, "peer")

        err_start, _b_start, _ = game.try_move(host_conn, "start")
        self.assertEqual(err_start, [])
        lines = game.show(host_conn)
        self.assertTrue(any("闷牌中" in line for line in lines))

        err_look, _b_look, _ = game.try_move(host_conn, "look")
        self.assertEqual(err_look, [])
        lines2 = game.show(host_conn)
        self.assertTrue(any(line.startswith("你的手牌：") and "闷牌中" not in line for line in lines2))


if __name__ == "__main__":
    unittest.main()
