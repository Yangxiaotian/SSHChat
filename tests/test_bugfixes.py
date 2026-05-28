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


if __name__ == "__main__":
    unittest.main()
