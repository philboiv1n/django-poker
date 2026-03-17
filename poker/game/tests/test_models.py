"""
Unit tests for Game, Player, and Profile models.
"""
from django.core.exceptions import ValidationError

from game.models import Game, Player
from .base import SyncTestCase, make_user, make_game, make_player


class ProfileModelTests(SyncTestCase):

    def setUp(self):
        self.user = make_user("alice", chips=500)

    def test_str(self):
        self.assertEqual(str(self.user.profile), "alice's Profile")

    def test_default_chips_on_creation(self):
        new_user = make_user("bob")
        # make_user sets chips=1000; profile is created by signal with default 1000
        self.assertEqual(new_user.profile.chips, 1000)

    def test_chips_set_correctly(self):
        self.assertEqual(self.user.profile.chips, 500)

    def test_avatar_color_default(self):
        self.assertEqual(self.user.profile.avatar_color, "#000000")

    def test_avatar_color_valid_hex(self):
        self.user.profile.avatar_color = "#1a2b3c"
        self.user.profile.full_clean()  # should not raise

    def test_avatar_color_invalid_hex_raises(self):
        self.user.profile.avatar_color = "not-a-color"
        with self.assertRaises(ValidationError):
            self.user.profile.full_clean()

    def test_avatar_color_too_short_raises(self):
        self.user.profile.avatar_color = "#abc"
        with self.assertRaises(ValidationError):
            self.user.profile.full_clean()


class GameModelTests(SyncTestCase):

    def setUp(self):
        self.game = make_game(name="Poker Night")

    def test_str_contains_name(self):
        self.assertIn("Poker Night", str(self.game))

    def test_default_status_is_waiting(self):
        self.assertEqual(self.game.status, "waiting")

    def test_default_phase_is_preflop(self):
        self.assertEqual(self.game.current_phase, "preflop")

    def test_default_community_cards_empty(self):
        self.assertEqual(self.game.community_cards, [])

    def test_default_deck_empty(self):
        self.assertEqual(self.game.deck, [])

    # -- get_pot -----------------------------------------------------------

    def test_get_pot_no_players(self):
        self.assertEqual(self.game.get_pot(), 0)

    def test_get_pot_single_player(self):
        u = make_user("u1")
        make_player(u, self.game, total_bet=50)
        self.assertEqual(self.game.get_pot(), 50)

    def test_get_pot_multiple_players(self):
        u1 = make_user("u1")
        u2 = make_user("u2")
        u3 = make_user("u3")
        make_player(u1, self.game, position=0, total_bet=100)
        make_player(u2, self.game, position=1, total_bet=75)
        make_player(u3, self.game, position=2, total_bet=50)
        self.assertEqual(self.game.get_pot(), 225)

    def test_get_pot_includes_folded_players(self):
        u1 = make_user("u1")
        u2 = make_user("u2")
        make_player(u1, self.game, position=0, total_bet=100, has_folded=True)
        make_player(u2, self.game, position=1, total_bet=100)
        self.assertEqual(self.game.get_pot(), 200)

    def test_get_pot_all_zero(self):
        u1 = make_user("u1")
        u2 = make_user("u2")
        make_player(u1, self.game, position=0, total_bet=0)
        make_player(u2, self.game, position=1, total_bet=0)
        self.assertEqual(self.game.get_pot(), 0)

    # -- burn_card ---------------------------------------------------------

    def test_burn_card_removes_top_card(self):
        self.game.deck = ["As", "Ks", "Qs"]
        self.game.burn_card()
        self.assertEqual(self.game.deck, ["Ks", "Qs"])

    def test_burn_card_empty_deck_does_nothing(self):
        self.game.deck = []
        self.game.burn_card()   # should not raise
        self.assertEqual(self.game.deck, [])

    def test_burn_card_single_card(self):
        self.game.deck = ["As"]
        self.game.burn_card()
        self.assertEqual(self.game.deck, [])

    # -- status choices ----------------------------------------------------

    def test_valid_statuses(self):
        for status in ("waiting", "active", "finished"):
            self.game.status = status
            # Exclude JSONField defaults (community_cards/deck=[]) which are not blank=True
            self.game.full_clean(exclude=["community_cards", "deck"])  # should not raise

    # -- dealer / turn -----------------------------------------------------

    def test_dealer_position_nullable(self):
        self.assertIsNone(self.game.dealer_position)

    def test_current_turn_nullable(self):
        self.assertIsNone(self.game.current_turn)


class PlayerModelTests(SyncTestCase):

    def setUp(self):
        self.user = make_user("carol")
        self.game = make_game()
        self.player = make_player(self.user, self.game, position=0, chips=500)

    def test_str_contains_username(self):
        self.assertIn("carol", str(self.player))

    def test_default_booleans_all_false(self):
        p = self.player
        self.assertFalse(p.has_folded)
        self.assertFalse(p.has_checked)
        self.assertFalse(p.is_all_in)
        self.assertFalse(p.is_small_blind)
        self.assertFalse(p.is_big_blind)
        self.assertFalse(p.is_dealer)
        self.assertFalse(p.has_acted_this_round)

    def test_can_reraise_default_true(self):
        self.assertTrue(self.player.can_reraise_this_round)

    def test_hole_cards_default_empty(self):
        self.assertEqual(self.player.hole_cards, [])

    # -- set_hole_cards ----------------------------------------------------

    def test_set_hole_cards_stores_cards(self):
        self.player.set_hole_cards(["As", "Kh"])
        self.player.refresh_from_db()
        self.assertEqual(self.player.hole_cards, ["As", "Kh"])

    def test_clear_hole_cards(self):
        self.player.set_hole_cards(["As", "Kh"])
        self.player.clear_hole_cards()
        self.player.refresh_from_db()
        self.assertEqual(self.player.hole_cards, [])

    def test_set_hole_cards_overwrites_previous(self):
        self.player.set_hole_cards(["As", "Kh"])
        self.player.set_hole_cards(["2c", "3d"])
        self.player.refresh_from_db()
        self.assertEqual(self.player.hole_cards, ["2c", "3d"])

    # -- bets / chips ------------------------------------------------------

    def test_chips_and_bets_default_zero(self):
        p = make_player(make_user("dave"), self.game, position=1)
        self.assertEqual(p.chips, 100)   # make_player default
        self.assertEqual(p.current_bet, 0)
        self.assertEqual(p.total_bet, 0)
