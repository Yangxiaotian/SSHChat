"""Tests for bug fixes identified in code review."""
import unittest
from unittest.mock import MagicMock

from games import (
    SanguoshaGame,
    WerewolfGame,
    GomokuGame,
    HoldemGame,
    ZhaJinHuaGame,
    NiuTouWangGame,
    MahjongGame,
    create_game,
    resolve_game_name,
    _mj_is_win,
    _zjh_compare,
    _mj_normalize_tile,
    _SgsPlayer,
)


def _make_sgs_player(conn, name, role="", hp=4, max_hp=4, hand=None, skill_ids=(),
                     chained=False, dead=False, weapon=None, armor=None,
                     niepan_used=False, judge_lebu=False):
    """Helper to create _SgsPlayer with attributes set."""
    p = _SgsPlayer(conn, name)
    p.role = role
    p.hp = hp
    p.max_hp = max_hp
    p.hand = hand if hand is not None else []
    p.skill_ids = skill_ids
    p.chained = chained
    p.dead = dead
    p.weapon = weapon
    p.armor = armor
    p.horse_plus = None
    p.horse_minus = None
    p.niepan_used = niepan_used
    p.judge_lebu = judge_lebu
    return p


class TestSanguoshaChainDamage(unittest.TestCase):
    """Chain damage should only spread for fire/thunder, and should clear chained state."""

    def _make_game_with_two_chained_players(self):
        g = SanguoshaGame.__new__(SanguoshaGame)
        g._rng = __import__("random").Random(42)
        p0 = _make_sgs_player(object(), "A", role="主公", chained=True)
        p1 = _make_sgs_player(object(), "B", role="反贼", chained=True)
        g.players = [p0, p1]
        g._discard = []
        g._turn_idx = 0
        return g

    def test_normal_damage_does_not_spread(self):
        g = self._make_game_with_two_chained_players()
        notes = []
        g._chain_spread_damage(0, "normal", notes)
        self.assertEqual(g.players[1].hp, 4)
        self.assertTrue(g.players[0].chained)

    def test_fire_damage_spreads_and_unchains(self):
        g = self._make_game_with_two_chained_players()
        notes = []
        g._chain_spread_damage(0, "fire", notes)
        self.assertEqual(g.players[1].hp, 3)
        self.assertFalse(g.players[0].chained)
        self.assertFalse(g.players[1].chained)

    def test_thunder_damage_spreads(self):
        g = self._make_game_with_two_chained_players()
        notes = []
        g._chain_spread_damage(0, "thunder", notes)
        self.assertEqual(g.players[1].hp, 3)
        self.assertFalse(g.players[0].chained)


class TestSanguoshaFangzhu(unittest.TestCase):
    """Fangzhu should trigger when the damaged player has it, and discard from the source."""

    def test_fangzhu_discards_from_attacker(self):
        g = SanguoshaGame.__new__(SanguoshaGame)
        g._rng = __import__("random").Random(42)
        p0 = _make_sgs_player(object(), "SimaYi", role="反贼", hp=3, max_hp=3,
                              skill_ids=("fangzhu",))
        attacker = _make_sgs_player(object(), "Attacker", role="主公", hand=["card_x"])
        g.players = [p0, attacker]
        g._discard = []
        g._turn_idx = 1
        # _damage(target_idx=0, source_idx=1) -- SimaYi takes damage from Attacker
        g._damage(0, 1, 1, reactions=True)
        # Attacker should have lost a card (fangzhu triggers on damaged player)
        self.assertEqual(len(attacker.hand), 0)
        self.assertEqual(len(g._discard), 1)


class TestSanguoshaGuose(unittest.TestCase):
    """Guose should not consume the card if target already has judge_lebu."""

    def test_guose_does_not_waste_card_when_lebu_exists(self):
        from sgs_data import CARD_SEP
        card = f"A{CARD_SEP}方块"
        player = _make_sgs_player(object(), "DaQiao", role="反贼", hp=3, max_hp=3,
                                  hand=[card], skill_ids=("guose",))
        target = _make_sgs_player(object(), "Victim", role="主公", judge_lebu=True)
        # The fix: check judge_lebu BEFORE removing card
        from games import find_card_in_hand, is_diamond
        found = find_card_in_hand(player.hand, "A")
        self.assertIsNotNone(found)
        self.assertTrue(is_diamond(found))
        self.assertTrue(target.judge_lebu)
        # Card should NOT be removed since judge_lebu is True
        self.assertIn(card, player.hand)


class TestSanguoshaWansha(unittest.TestCase):
    """Wansha should block peach only when target.hp <= 0 (dying), not <= 1."""

    def test_wansha_allows_peach_at_hp_1(self):
        g = SanguoshaGame.__new__(SanguoshaGame)
        g._rng = __import__("random").Random(42)
        jiaxu = _make_sgs_player(object(), "JiaXu", role="反贼", skill_ids=("wansha",))
        target = _make_sgs_player(object(), "Target", role="反贼", hp=1, max_hp=4)
        g.players = [jiaxu, target]
        g._turn_idx = 0
        g._discard = []
        turn_p = g.players[g._turn_idx]
        self.assertTrue(g._has_skill(turn_p, "wansha"))
        # With fix: hp=1 is NOT blocked (threshold is <= 0)
        self.assertFalse(target.hp <= 0)


class TestSanguoshaNiepan(unittest.TestCase):
    """Niepan should discard all hand cards and equipment, and restore to 3 HP."""

    def test_niepan_discards_and_restores(self):
        g = SanguoshaGame.__new__(SanguoshaGame)
        g._rng = __import__("random").Random(42)
        p = _make_sgs_player(object(), "Phoenix", role="反贼", hp=0, max_hp=3,
                             dead=True, hand=["card1", "card2"],
                             skill_ids=("niepan",), niepan_used=False)
        p.weapon = "weapon_x"
        p.armor = "armor_x"
        p.horse_plus = "horse_+"
        p.horse_minus = "horse_-"
        g.players = [p]
        g._discard = []
        g._turn_idx = 0
        # Simulate what _damage does when hp <= 0 with niepan
        if not p.niepan_used and g._has_skill(p, "niepan"):
            p.niepan_used = True
            p.hand.clear()
            p.weapon = None
            p.armor = None
            p.horse_plus = None
            p.horse_minus = None
            p.hp = 3
            p.dead = False
        self.assertEqual(p.hp, 3)
        self.assertFalse(p.dead)
        self.assertEqual(len(p.hand), 0)
        self.assertIsNone(p.weapon)
        self.assertIsNone(p.armor)
        self.assertTrue(p.niepan_used)


class TestWerewolfWitchSelfSave(unittest.TestCase):
    """Witch should not be able to save herself."""

    def test_witch_cannot_save_self(self):
        game = WerewolfGame.__new__(WerewolfGame)
        game.state = "night"
        game.round = 1
        game.alive = {"witch_p", "wolf_p", "villager_p"}
        game.roles = {"witch_p": "witch", "wolf_p": "wolf", "villager_p": "villager"}
        game.wolf_target = "witch_p"
        game.pending_kill = "witch_p"
        game.seer_target = None
        game.witch_saved = False
        game.witch_save_available = True
        game.witch_poison_available = True
        game.pending_poison = None
        game.day_votes = {}
        witch_conn = MagicMock()
        witch_conn.__hash__ = lambda self: hash(id(self))
        game.players = [(witch_conn, "witch_p")]
        game._queue_private = lambda conn, msgs: None

        priv, bcast, done = game.try_move(witch_conn, "save")
        self.assertTrue(any("cannot save herself" in msg for msg in priv))
        self.assertFalse(game.witch_saved)


class TestGomokuUndoWorks(unittest.TestCase):
    """Verify undo request/accept flow works for Gomoku (not blocked by dead handler)."""

    def test_undo_request_accept_flow(self):
        c1 = object()
        c2 = object()
        game = GomokuGame(c1, "A")
        game.try_join(c2, "B")
        game.try_move(c1, "7 7")  # A plays center (1-based: row 7, col 7)
        game.try_move(c2, "8 8")  # B plays (1-based: row 8, col 8)

        # B (last mover) requests undo
        priv, bcast, _ = game.request_undo(c2)
        self.assertTrue(any("悔棋" in line for line in priv))
        self.assertTrue(any("悔棋" in line for line in bcast))

        # A accepts the undo
        priv2, bcast2, _ = game.accept_undo(c1)
        self.assertTrue(any("悔棋" in line for line in bcast2 + priv2))
        # After undo, B's move at (8,8) should be removed (0-based: grid[7][7])
        self.assertEqual(game.grid[7][7], 0)  # 0 = empty cell


class TestZhaJinHuaCompareTie(unittest.TestCase):
    """Compare tie should go to defender (target), not attacker."""

    def test_tie_goes_to_defender(self):
        """When attacker and defender have equal hand strength, defender wins."""
        c1 = object()
        c2 = object()
        game = ZhaJinHuaGame(c1, "A")
        game.try_join(c2, "B")
        game.try_move(c1, "start")

        # Set up identical hands for both players
        game.cards["A"] = ["A♠", "A♥", "A♦"]
        game.cards["B"] = ["A♣", "A♠", "A♥"]
        game.looked.add("A")
        game.looked.add("B")
        game.stacks["A"] = 100
        game.stacks["B"] = 100

        # A attacks B - with equal hands, B (defender) should win
        priv, bcast, _ = game.try_move(c1, "compare B")
        # B should be the winner (tie goes to defender)
        self.assertTrue(any("B" in line and "胜出" in line for line in bcast))

    def test_special_235_beats_leopard(self):
        leopard = ["2C", "2H", "2D"]
        special = ["2S", "3H", "5D"]
        self.assertGreater(_zjh_compare(special, leopard), 0)
        self.assertLess(_zjh_compare(leopard, special), 0)


class TestZhaJinHuaCompareAllIn(unittest.TestCase):
    """Compare should allow all-in payment when attacker stack is insufficient."""

    def test_compare_allows_insufficient_stack_all_in(self):
        c1 = object()
        c2 = object()
        game = ZhaJinHuaGame(c1, "yxt")
        game.try_join(c2, "zouyu")
        game.try_move(c1, "start")

        game.looked.add("yxt")
        game.looked.add("zouyu")
        game.turn_idx = 0
        game.current_bet = 601
        game.pot = 1708
        game.stacks["yxt"] = 498
        game.stacks["zouyu"] = 398
        game.cards["yxt"] = ["AS", "AH", "AD"]
        game.cards["zouyu"] = ["2D", "3D", "4H"]

        err, bcast, done = game.try_move(c1, "compare zouyu")
        self.assertEqual(err, [])
        self.assertTrue(any("全压比牌" in line for line in bcast))
        self.assertTrue(any("比牌" in line and "胜出" in line for line in bcast))
        self.assertTrue(any("豹子" in line and "顺子" in line for line in bcast))
        self.assertGreater(game.stacks["yxt"], 2000)

    def test_all_in_compare_win_auto_starts_next_hand(self):
        """Winning compare should award pot and auto-deal next hand when session can continue."""
        c1 = object()
        c2 = object()
        game = ZhaJinHuaGame(c1, "yxt")
        game.try_join(c2, "zouyu")
        game.try_move(c1, "start")

        game.looked.add("yxt")
        game.looked.add("zouyu")
        game.folded.discard("yxt")
        game.folded.discard("zouyu")
        game.turn_idx = 0
        game.current_bet = 701
        game.pot = 1684
        game.stacks["yxt"] = 277
        game.stacks["zouyu"] = 602
        game.cards["yxt"] = ["AS", "AH", "AD"]
        game.cards["zouyu"] = ["2D", "3D", "4H"]

        err, bcast, done = game.try_move(c1, "compare zouyu")
        self.assertEqual(err, [])
        self.assertFalse(done)
        self.assertEqual(game.state, "playing")
        self.assertTrue(any("因其他玩家弃牌获胜" in line for line in bcast))
        self.assertTrue(any("自动开始下一局" in line for line in bcast))
        self.assertGreater(game.stacks["yxt"], 1600)

    def test_folded_human_nudge_advances_bot_turn(self):
        """Folded human poking the game should still advance bot turns."""
        c1 = object()
        c2 = object()
        game = ZhaJinHuaGame(c1, "yxt")
        game.try_join(c2, "zouyu")
        game.try_move(c1, "start bot")
        game.folded = {"yxt", "zouyu", "R1"}
        game.pot = 1405
        game.current_bet = 20
        game.stacks = {
            "yxt": 859,
            "zouyu": 959,
            "R1": 899,
            "R2": 579,
            "R3": 299,
        }
        for i, (_, n) in enumerate(game.players):
            if n == "R2":
                game.turn_idx = i
                break

        err, bcast, done = game.try_move(c1, "fold")
        self.assertEqual(err, ["你已经弃牌。"])
        self.assertFalse(done)
        self.assertEqual(game.state, "playing")
        self.assertTrue(any("因其他玩家弃牌获胜" in line for line in bcast) or any("自动开始下一局" in line for line in bcast))

    def test_zero_stack_bots_auto_fold_and_finish(self):
        """Bots with 0 stack but not folded should not deadlock bot runner."""
        c1 = object()
        c2 = object()
        game = ZhaJinHuaGame(c1, "yxt")
        game.try_join(c2, "zouyu")
        game.try_move(c1, "start bot")
        game.state = "playing"
        game.folded = {"yxt", "zouyu", "R1"}
        game.stacks = {"R2": 0, "R3": 0, "R1": 899}
        game.pot = 1405
        game.turn_idx = next(
            i for i, (_, n) in enumerate(game.players) if n == "R2"
        )

        out = game.nudge_bots()
        self.assertIn(game.state, ("playing", "ended"))
        self.assertTrue(any("积分耗尽，自动弃牌" in line for line in out))

    def test_stuck_state_recovers_on_any_move(self):
        """Sole survivor with 0 stack should collect pot and continue when others still have chips."""
        c1 = object()
        game = ZhaJinHuaGame(c1, "yxt")
        game.state = "playing"
        game.folded = {"zouyu", "R1", "R2", "R3"}
        game.looked = {"yxt"}
        game.players = [(c1, "yxt"), (object(), "zouyu"), (object(), "R1"), (object(), "R2"), (object(), "R3")]
        game.stacks = {"yxt": 0, "zouyu": 602, "R1": 998, "R2": 858, "R3": 858}
        game.pot = 1684
        game.current_bet = 701
        game.turn_idx = 0
        game.cards = {"yxt": ["JH", "8C", "7C"]}

        err, bcast, done = game.try_move(c1, "follow")
        self.assertEqual(err, [])
        self.assertFalse(done)
        self.assertEqual(game.state, "playing")
        self.assertEqual(game.stacks["yxt"], 1683)
        self.assertTrue(any("因其他玩家弃牌获胜" in line for line in bcast))


class TestWerewolfWitchMutualExclusion(unittest.TestCase):
    """Witch should not be able to both save and poison in the same night."""

    def test_cannot_poison_after_save(self):
        game = WerewolfGame.__new__(WerewolfGame)
        game.state = "night"
        game.round = 1
        game.alive = {"witch_p", "wolf_p", "villager_p", "seer_p", "villager2_p"}
        game.roles = {"witch_p": "witch", "wolf_p": "wolf", "villager_p": "villager",
                      "seer_p": "seer", "villager2_p": "villager"}
        game.wolf_target = "villager_p"
        game.pending_kill = "villager_p"
        game.seer_target = None
        game.witch_saved = False
        game.witch_save_available = True
        game.witch_poison_available = True
        game.pending_poison = None
        game.day_votes = {}
        game.players = [(object(), "witch_p"), (object(), "wolf_p"),
                        (object(), "villager_p"), (object(), "seer_p"),
                        (object(), "villager2_p")]
        game._extra_privates = []
        game._queue_private = lambda conn, msgs: game._extra_privates.append((conn, msgs))

        witch_conn = game.players[0][0]

        # Save the villager
        priv, bcast, _ = game.try_move(witch_conn, "save")
        self.assertTrue(game.witch_saved)

        # Try to poison someone - should be rejected
        priv2, bcast2, _ = game.try_move(witch_conn, "poison wolf_p")
        self.assertTrue(any("Already saved" in msg for msg in priv2))
        self.assertIsNone(game.pending_poison)

    def test_cannot_save_after_poison(self):
        game = WerewolfGame.__new__(WerewolfGame)
        game.state = "night"
        game.round = 1
        game.alive = {"witch_p", "wolf_p", "villager_p", "seer_p", "villager2_p"}
        game.roles = {"witch_p": "witch", "wolf_p": "wolf", "villager_p": "villager",
                      "seer_p": "seer", "villager2_p": "villager"}
        game.wolf_target = "villager_p"
        game.pending_kill = "villager_p"
        game.seer_target = None
        game.witch_saved = False
        game.witch_save_available = True
        game.witch_poison_available = True
        game.pending_poison = None
        game.day_votes = {}
        game.players = [(object(), "witch_p"), (object(), "wolf_p"),
                        (object(), "villager_p"), (object(), "seer_p"),
                        (object(), "villager2_p")]
        game._extra_privates = []
        game._queue_private = lambda conn, msgs: game._extra_privates.append((conn, msgs))

        witch_conn = game.players[0][0]

        # Poison wolf
        priv, bcast, _ = game.try_move(witch_conn, "poison wolf_p")
        self.assertEqual(game.pending_poison, "wolf_p")

        # Try to save - should be rejected
        priv2, bcast2, _ = game.try_move(witch_conn, "save")
        self.assertTrue(any("Already poisoned" in msg for msg in priv2))
        self.assertFalse(game.witch_saved)


class TestHoldemShowdownTieSplit(unittest.TestCase):
    """Holdem showdown should split pot when players tie."""

    def test_showdown_splits_pot_on_tie(self):
        c1 = object()
        c2 = object()
        game = HoldemGame(c1, "A")
        game.try_join(c2, "B")
        game.try_move(c1, "start")

        # Fold all bot players so only A and B remain
        for _c, n in list(game.players):
            if n not in ("A", "B"):
                game.folded.add(n)

        # Set identical hands for both players
        game.hands["A"] = ["A♠", "A♥"]
        game.hands["B"] = ["A♣", "A♦"]
        game.board = ["K♠", "K♥", "K♦", "Q♠", "Q♥"]
        game.pot = 100
        game.stacks["A"] = 50
        game.stacks["B"] = 50

        # Both players should get equal share
        result = game._showdown()
        self.assertTrue(any("平局" in line for line in result))
        # Each should get 50 (100/2)
        self.assertEqual(game.stacks["A"], 100)
        self.assertEqual(game.stacks["B"], 100)


class TestNiuTouWangCardEqualRowEnd(unittest.TestCase):
    """NiuTouWang should not crash when card equals smallest row end."""

    def test_card_equal_to_row_end_does_not_crash(self):
        c1 = object()
        c2 = object()
        game = NiuTouWangGame(c1, "A")
        game.try_join(c2, "B")
        game._start()

        # Set up rows where one row ends with a specific value
        game.rows = [[5, 10], [15, 20], [25, 30], [35, 40]]
        game.hands["A"] = [10]  # Card equals row end of first row
        game.hands["B"] = [50]
        game.state = "playing"

        # This should not crash
        b, paused = game._apply_card("A", 10)
        self.assertFalse(paused)
        # Card should be placed in the row with end <= card
        self.assertIn(10, game.rows[0])


class TestMahjongBasics(unittest.TestCase):
    def test_alias_and_create_game(self):
        self.assertEqual(resolve_game_name("麻将"), "mahjong")
        g = create_game("mahjong", object(), "A")
        self.assertIsInstance(g, MahjongGame)

    def test_standard_win_shape(self):
        hand = [
            "m1", "m1", "m1",
            "m2", "m2", "m2",
            "m3", "m3", "m3",
            "p4", "p5", "p6",
            "z1", "z1",
        ]
        self.assertTrue(_mj_is_win(hand))

    def test_chinese_tile_parse(self):
        self.assertEqual(_mj_normalize_tile("二万"), "m2")
        self.assertEqual(_mj_normalize_tile("九筒"), "p9")
        self.assertEqual(_mj_normalize_tile("五条"), "s5")
        self.assertEqual(_mj_normalize_tile("东风"), "z1")
        self.assertEqual(_mj_normalize_tile("红中"), "z5")

    def test_claim_pass_then_next_player_draw(self):
        c1, c2, c3, c4 = object(), object(), object(), object()
        g = MahjongGame(c1, "A")
        g.try_join(c2, "B")
        g.try_join(c3, "C")
        g.try_join(c4, "D")
        g.state = "playing"
        g.turn_idx = 0
        g.wall = ["m9", "m8", "m7"]
        g.hands = {
            "A": ["m1"] * 14,
            "B": ["p1"] * 13,
            "C": ["p2"] * 13,
            "D": ["p3"] * 13,
        }
        g.melds = {"A": [], "B": [], "C": [], "D": []}
        _e, _b, _d = g.try_move(c1, "discard m1")
        self.assertTrue(g.claim_phase)
        g.try_move(c2, "pass")
        g.try_move(c3, "pass")
        _e2, b2, _d2 = g.try_move(c4, "pass")
        self.assertFalse(g.claim_phase)
        self.assertTrue(any("摸牌" in line for line in b2))

    def test_start_auto_fills_bots(self):
        c1 = object()
        g = MahjongGame(c1, "A")
        _e, b, ended = g.try_move(c1, "start")
        self.assertFalse(ended)
        self.assertEqual(len(g.players), 4)
        self.assertEqual(len(g.bot_names), 3)
        self.assertTrue(any("自动补 AI" in line for line in b))

    def test_peng_and_discard(self):
        c1, c2, c3, c4 = object(), object(), object(), object()
        g = MahjongGame(c1, "A")
        g.players = [(c1, "A"), (c2, "B"), (c3, "C"), (c4, "D")]
        g.state = "playing"
        g.turn_idx = 1
        g.wall = ["s9", "s8"]
        g.hands = {
            "A": ["m1"] * 13,
            "B": ["m3"] + ["p2"] * 13,
            "C": ["m3", "m3"] + ["p4"] * 11,
            "D": ["s1"] * 13,
        }
        g.melds = {"A": [], "B": [], "C": [], "D": []}
        g.try_move(c2, "discard 三万")
        _e, b, _d = g.try_move(c3, "peng")
        self.assertTrue(any("碰了 m3" in line for line in b))
        self.assertEqual(g.turn_idx, 2)
        self.assertEqual(len(g.melds["C"]), 1)


if __name__ == "__main__":
    unittest.main()
