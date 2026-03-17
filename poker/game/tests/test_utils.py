"""
Unit tests for game/utils.py helper functions.
"""
from game.utils import (
    create_deck,
    get_next_phase,
    can_user_do_action,
    find_best_five_cards,
)
from game.models import Game, Player
from .base import SyncTestCase, make_user, make_game, make_player


# ---------------------------------------------------------------------------
# create_deck
# ---------------------------------------------------------------------------

class CreateDeckTests(SyncTestCase):

    def setUp(self):
        self.deck = create_deck()

    def test_deck_has_52_cards(self):
        self.assertEqual(len(self.deck), 52)

    def test_no_duplicate_cards(self):
        self.assertEqual(len(self.deck), len(set(self.deck)))

    def test_all_four_suits_present(self):
        for suit in ("s", "c", "h", "d"):
            self.assertTrue(any(c.endswith(suit) for c in self.deck))

    def test_all_thirteen_ranks_present(self):
        for rank in ("2", "3", "4", "5", "6", "7", "8", "9", "T", "J", "Q", "K", "A"):
            self.assertTrue(any(c.startswith(rank) for c in self.deck))

    def test_card_format(self):
        # Every card is rank (1-2 chars) + suit (1 char)
        for card in self.deck:
            self.assertGreaterEqual(len(card), 2)
            self.assertLessEqual(len(card), 3)
            self.assertIn(card[-1], "schd")

    def test_four_of_each_rank(self):
        for rank in ("2", "3", "4", "5", "6", "7", "8", "9", "T", "J", "Q", "K", "A"):
            count = sum(1 for c in self.deck if c[:-1] == rank)
            self.assertEqual(count, 4, f"Expected 4 {rank}s, got {count}")

    def test_thirteen_of_each_suit(self):
        for suit in ("s", "c", "h", "d"):
            count = sum(1 for c in self.deck if c[-1] == suit)
            self.assertEqual(count, 13, f"Expected 13 {suit}s, got {count}")


# ---------------------------------------------------------------------------
# get_next_phase
# ---------------------------------------------------------------------------

class GetNextPhaseTests(SyncTestCase):

    def test_preflop_to_flop(self):
        self.assertEqual(get_next_phase("preflop"), "flop")

    def test_flop_to_turn(self):
        self.assertEqual(get_next_phase("flop"), "turn")

    def test_turn_to_river(self):
        self.assertEqual(get_next_phase("turn"), "river")

    def test_river_to_showdown(self):
        self.assertEqual(get_next_phase("river"), "showdown")

    def test_showdown_stays_showdown(self):
        # get_next_phase("showdown") falls through to the default
        self.assertEqual(get_next_phase("showdown"), "showdown")

    def test_unknown_phase_returns_showdown(self):
        self.assertEqual(get_next_phase("unknown"), "showdown")


# ---------------------------------------------------------------------------
# can_user_do_action
# ---------------------------------------------------------------------------

class CanUserDoActionTests(SyncTestCase):

    def setUp(self):
        self.user = make_user("eve")
        self.game = make_game(status="active")

    def _player(self, **kwargs):
        defaults = dict(position=0, chips=100)
        defaults.update(kwargs)
        # clean up previous players on same position
        Player.objects.filter(game=self.game, position=defaults["position"]).delete()
        return make_player(self.user, self.game, **defaults)

    # -- check -------------------------------------------------------------

    def test_check_allowed_when_no_bet(self):
        p = self._player(current_bet=0)
        self.assertTrue(can_user_do_action(self.game, p, "check", highest_bet=0))

    def test_check_blocked_when_bet_exists(self):
        p = self._player(current_bet=0)
        self.assertFalse(can_user_do_action(self.game, p, "check", highest_bet=20))

    def test_check_allowed_when_player_matches_bet(self):
        # Player already bet the same as highest → difference = 0 → check is allowed
        p = self._player(current_bet=20)
        self.assertTrue(can_user_do_action(self.game, p, "check", highest_bet=20))

    def test_check_blocked_for_folded_player(self):
        p = self._player(has_folded=True, current_bet=0)
        self.assertFalse(can_user_do_action(self.game, p, "check", highest_bet=0))

    def test_check_blocked_for_all_in_player(self):
        p = self._player(is_all_in=True, current_bet=0)
        self.assertFalse(can_user_do_action(self.game, p, "check", highest_bet=0))

    # -- call --------------------------------------------------------------

    def test_call_allowed_when_bet_exists(self):
        p = self._player(current_bet=0)
        self.assertTrue(can_user_do_action(self.game, p, "call", highest_bet=20))

    def test_call_blocked_when_no_bet(self):
        p = self._player(current_bet=0)
        self.assertFalse(can_user_do_action(self.game, p, "call", highest_bet=0))

    def test_call_blocked_when_player_already_matched(self):
        p = self._player(current_bet=20)
        self.assertFalse(can_user_do_action(self.game, p, "call", highest_bet=20))

    def test_call_blocked_for_folded_player(self):
        p = self._player(has_folded=True, current_bet=0)
        self.assertFalse(can_user_do_action(self.game, p, "call", highest_bet=20))

    def test_call_blocked_for_all_in_player(self):
        p = self._player(is_all_in=True, current_bet=0)
        self.assertFalse(can_user_do_action(self.game, p, "call", highest_bet=20))

    def test_highest_bet_queried_from_db_when_not_provided(self):
        # When highest_bet is not supplied, it's read from DB; no players → default 0
        p = self._player(current_bet=0)
        # With no other players, highest_bet = 0, so check is allowed
        self.assertTrue(can_user_do_action(self.game, p, "check"))


# ---------------------------------------------------------------------------
# find_best_five_cards
# ---------------------------------------------------------------------------

class FindBestFiveCardsTests(SyncTestCase):
    """
    Treys: lower score = better hand.
    Royal flush has score 1; worst high card has score 7462.
    """

    def _score(self, cards):
        score, rank, best5 = find_best_five_cards(cards)
        return score, rank

    def test_royal_flush(self):
        cards = ["As", "Ks", "Qs", "Js", "Ts", "2h", "3d"]
        score, rank = self._score(cards)
        self.assertEqual(rank, "Royal Flush")

    def test_straight_flush(self):
        cards = ["9s", "8s", "7s", "6s", "5s", "2h", "3d"]
        score, rank = self._score(cards)
        self.assertEqual(rank, "Straight Flush")

    def test_four_of_a_kind(self):
        cards = ["As", "Ah", "Ad", "Ac", "Ks", "2h", "3d"]
        score, rank = self._score(cards)
        self.assertEqual(rank, "Four of a Kind")

    def test_full_house(self):
        cards = ["As", "Ah", "Ad", "Ks", "Kh", "2c", "3d"]
        score, rank = self._score(cards)
        self.assertEqual(rank, "Full House")

    def test_flush(self):
        cards = ["As", "Ks", "Qs", "Js", "9s", "2h", "3d"]
        score, rank = self._score(cards)
        self.assertEqual(rank, "Flush")

    def test_straight(self):
        cards = ["As", "Kh", "Qd", "Jc", "Ts", "2h", "3d"]
        score, rank = self._score(cards)
        self.assertEqual(rank, "Straight")

    def test_three_of_a_kind(self):
        cards = ["As", "Ah", "Ad", "Ks", "Qh", "2c", "3d"]
        score, rank = self._score(cards)
        self.assertEqual(rank, "Three of a Kind")

    def test_two_pair(self):
        cards = ["As", "Ah", "Ks", "Kh", "Qd", "2c", "3d"]
        score, rank = self._score(cards)
        self.assertEqual(rank, "Two Pair")

    def test_one_pair(self):
        cards = ["As", "Ah", "Ks", "Qh", "Jd", "2c", "3d"]
        score, rank = self._score(cards)
        self.assertEqual(rank, "Pair")

    def test_high_card(self):
        cards = ["As", "Kh", "Qd", "Jc", "9s", "7h", "2d"]
        score, rank = self._score(cards)
        self.assertEqual(rank, "High Card")

    def test_better_hand_has_lower_score(self):
        royal = find_best_five_cards(["As", "Ks", "Qs", "Js", "Ts", "2h", "3d"])[0]
        pair  = find_best_five_cards(["As", "Ah", "Ks", "Qh", "Jd", "2c", "3d"])[0]
        self.assertLess(royal, pair)

    def test_returns_five_cards(self):
        _, _, best5 = find_best_five_cards(["As", "Ks", "Qs", "Js", "Ts", "2h", "3d"])
        self.assertEqual(len(best5), 5)

    def test_higher_kicker_wins_pair(self):
        # Pair of aces with K kicker vs pair of aces with Q kicker
        ak_score = find_best_five_cards(["As", "Ah", "Ks", "Qh", "Jd", "2c", "3d"])[0]
        aq_score = find_best_five_cards(["As", "Ah", "Qs", "Jh", "9d", "2c", "3d"])[0]
        self.assertLess(ak_score, aq_score)  # AK pair beats AQ pair

    def test_straight_ace_low(self):
        # A-2-3-4-5 wheel straight
        cards = ["As", "2h", "3d", "4c", "5s", "Kh", "Qd"]
        score, rank = self._score(cards)
        # A-2-3-4-5 is a straight (wheel), but A-K-Q beats it
        self.assertEqual(rank, "Straight")
