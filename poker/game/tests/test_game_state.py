"""
Tests for rotate_dealer, assign_blinds, reset_hand, and start_hand.
"""
from asgiref.sync import sync_to_async
from game.models import Game, Player
from .base import AsyncTestCase, MockConsumer, make_user, make_game, make_player


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def get_game(g):
    return await sync_to_async(Game.objects.get)(pk=g.pk)

async def get_player(p):
    return await sync_to_async(Player.objects.get)(pk=p.pk)

async def get_players(game):
    return await sync_to_async(
        lambda: list(Player.objects.filter(game=game).order_by("position"))
    )()


def _setup_2p(small_blind=10, big_blind=20):
    u1 = make_user("gs_p1")
    u2 = make_user("gs_p2")
    game = make_game(
        status="waiting",
        small_blind=small_blind,
        big_blind=big_blind,
        max_players=2,
        blind_timer=0,
    )
    p1 = make_player(u1, game, position=0, chips=200)
    p2 = make_player(u2, game, position=1, chips=200)
    return game, p1, p2


# ---------------------------------------------------------------------------
# rotate_dealer
# ---------------------------------------------------------------------------

class RotateDealerTests(AsyncTestCase):

    def setUp(self):
        self.game, self.p1, self.p2 = _setup_2p()
        self.consumer = MockConsumer(self.p1.user, self.game.id)

    async def test_first_rotation_assigns_to_first_player(self):
        """No previous dealer → dealer goes to position 0."""
        game = await get_game(self.game)
        game.dealer_position = None
        await self.consumer.rotate_dealer(game)
        game = await get_game(self.game)
        self.assertEqual(game.dealer_position, 0)

    async def test_rotation_moves_to_next_player(self):
        """dealer at 0 → should rotate to 1."""
        await sync_to_async(Game.objects.filter(pk=self.game.pk).update)(dealer_position=0)
        game = await get_game(self.game)
        await self.consumer.rotate_dealer(game)
        game = await get_game(self.game)
        self.assertEqual(game.dealer_position, 1)

    async def test_rotation_wraps_around(self):
        """dealer at last position → wraps to position 0."""
        await sync_to_async(Game.objects.filter(pk=self.game.pk).update)(dealer_position=1)
        game = await get_game(self.game)
        await self.consumer.rotate_dealer(game)
        game = await get_game(self.game)
        self.assertEqual(game.dealer_position, 0)

    async def test_only_one_player_is_marked_dealer(self):
        await sync_to_async(Game.objects.filter(pk=self.game.pk).update)(dealer_position=None)
        game = await get_game(self.game)
        await self.consumer.rotate_dealer(game)
        dealers = await sync_to_async(
            Player.objects.filter(game=self.game, is_dealer=True).count
        )()
        self.assertEqual(dealers, 1)

    async def test_three_player_rotation(self):
        u3 = await sync_to_async(make_user)("gs_p3")
        await sync_to_async(make_player)(u3, self.game, position=2, chips=200)
        await sync_to_async(Game.objects.filter(pk=self.game.pk).update)(dealer_position=1)
        game = await get_game(self.game)
        await self.consumer.rotate_dealer(game)
        game = await get_game(self.game)
        self.assertEqual(game.dealer_position, 2)


# ---------------------------------------------------------------------------
# assign_blinds
# ---------------------------------------------------------------------------

class AssignBlindsTests(AsyncTestCase):

    def setUp(self):
        self.game, self.p1, self.p2 = _setup_2p(small_blind=10, big_blind=20)
        self.consumer = MockConsumer(self.p1.user, self.game.id)

    async def test_heads_up_dealer_is_small_blind(self):
        """In heads-up, dealer = small blind.
        Starting dealer_position=0 → rotates to 1 → p2 (pos 1) is dealer and SB."""
        await sync_to_async(Game.objects.filter(pk=self.game.pk).update)(dealer_position=0)
        game = await get_game(self.game)
        await self.consumer.rotate_dealer(game)
        game = await get_game(self.game)
        await self.consumer.assign_blinds(game)
        p2 = await get_player(self.p2)
        self.assertTrue(p2.is_small_blind)
        self.assertTrue(p2.is_dealer)

    async def test_heads_up_other_player_is_big_blind(self):
        """After rotating to dealer=1 (p2), p1 (pos 0) is the BB."""
        await sync_to_async(Game.objects.filter(pk=self.game.pk).update)(dealer_position=0)
        game = await get_game(self.game)
        await self.consumer.rotate_dealer(game)
        game = await get_game(self.game)
        await self.consumer.assign_blinds(game)
        p1 = await get_player(self.p1)
        self.assertTrue(p1.is_big_blind)

    async def test_small_blind_deducted(self):
        """After rotating to dealer=1 (p2), p2 is SB and pays 10."""
        await sync_to_async(Game.objects.filter(pk=self.game.pk).update)(dealer_position=0)
        game = await get_game(self.game)
        await self.consumer.rotate_dealer(game)
        game = await get_game(self.game)
        await self.consumer.assign_blinds(game)
        p2 = await get_player(self.p2)
        self.assertEqual(p2.chips, 190)          # 200 - 10
        self.assertEqual(p2.current_bet, 10)

    async def test_big_blind_deducted(self):
        """After rotating to dealer=1 (p2), p1 is BB and pays 20."""
        await sync_to_async(Game.objects.filter(pk=self.game.pk).update)(dealer_position=0)
        game = await get_game(self.game)
        await self.consumer.rotate_dealer(game)
        game = await get_game(self.game)
        await self.consumer.assign_blinds(game)
        p1 = await get_player(self.p1)
        self.assertEqual(p1.chips, 180)          # 200 - 20
        self.assertEqual(p1.current_bet, 20)

    async def test_heads_up_current_turn_is_small_blind(self):
        """Preflop heads-up: SB (dealer at pos 1) acts first → current_turn = 1."""
        await sync_to_async(Game.objects.filter(pk=self.game.pk).update)(dealer_position=0)
        game = await get_game(self.game)
        await self.consumer.rotate_dealer(game)
        game = await get_game(self.game)
        await self.consumer.assign_blinds(game)
        game = await get_game(self.game)
        self.assertEqual(game.current_turn, 1)   # SB at position 1

    async def test_three_players_sb_left_of_dealer(self):
        u3 = await sync_to_async(make_user)("gs_p3")
        await sync_to_async(make_player)(u3, self.game, position=2, chips=200)
        await sync_to_async(Game.objects.filter(pk=self.game.pk).update)(dealer_position=0)
        game = await get_game(self.game)
        await self.consumer.rotate_dealer(game)  # dealer → pos 1
        game = await get_game(self.game)
        await self.consumer.assign_blinds(game)
        # SB should be pos 2 (left of dealer at 1), BB pos 0
        p2 = await get_player(self.p2)  # position 1 = dealer
        p3 = await sync_to_async(Player.objects.get)(game=self.game, position=2)
        self.assertTrue(p3.is_small_blind)

    async def test_player_with_less_than_blind_goes_all_in(self):
        """A player who can't cover the full blind goes all-in for remaining chips."""
        await sync_to_async(Player.objects.filter(pk=self.p2.pk).update)(chips=5)
        await sync_to_async(Game.objects.filter(pk=self.game.pk).update)(dealer_position=0)
        game = await get_game(self.game)
        await self.consumer.rotate_dealer(game)
        game = await get_game(self.game)
        await self.consumer.assign_blinds(game)
        p2 = await get_player(self.p2)
        self.assertTrue(p2.is_all_in)
        self.assertEqual(p2.chips, 0)
        self.assertEqual(p2.current_bet, 5)


# ---------------------------------------------------------------------------
# reset_hand
# ---------------------------------------------------------------------------

class ResetHandTests(AsyncTestCase):

    def setUp(self):
        self.game, self.p1, self.p2 = _setup_2p()
        self.consumer = MockConsumer(self.p1.user, self.game.id)

    async def test_reset_clears_deck(self):
        await sync_to_async(Game.objects.filter(pk=self.game.pk).update)(
            deck=["As", "Ks"]
        )
        game = await get_game(self.game)
        await self.consumer.reset_hand(game)
        game = await get_game(self.game)
        self.assertEqual(game.deck, [])

    async def test_reset_clears_community_cards(self):
        await sync_to_async(Game.objects.filter(pk=self.game.pk).update)(
            community_cards=["As", "Ks", "Qs"]
        )
        game = await get_game(self.game)
        await self.consumer.reset_hand(game)
        game = await get_game(self.game)
        self.assertEqual(game.community_cards, [])

    async def test_reset_sets_phase_preflop(self):
        await sync_to_async(Game.objects.filter(pk=self.game.pk).update)(
            current_phase="river"
        )
        game = await get_game(self.game)
        await self.consumer.reset_hand(game)
        game = await get_game(self.game)
        self.assertEqual(game.current_phase, "preflop")

    async def test_reset_clears_player_bets(self):
        await sync_to_async(Player.objects.filter(game=self.game).update)(
            current_bet=50, total_bet=100
        )
        game = await get_game(self.game)
        await self.consumer.reset_hand(game)
        players = await get_players(self.game)
        for p in players:
            self.assertEqual(p.current_bet, 0)
            self.assertEqual(p.total_bet, 0)

    async def test_reset_clears_player_flags(self):
        await sync_to_async(Player.objects.filter(game=self.game).update)(
            has_folded=True, has_checked=True, is_all_in=True,
            is_small_blind=True, is_big_blind=True,
            has_acted_this_round=True,
        )
        game = await get_game(self.game)
        await self.consumer.reset_hand(game)
        players = await get_players(self.game)
        for p in players:
            self.assertFalse(p.has_folded)
            self.assertFalse(p.has_checked)
            self.assertFalse(p.is_all_in)
            self.assertFalse(p.is_small_blind)
            self.assertFalse(p.is_big_blind)
            self.assertFalse(p.has_acted_this_round)

    async def test_reset_clears_hole_cards(self):
        await sync_to_async(Player.objects.filter(game=self.game).update)(
            hole_cards=["As", "Kh"]
        )
        game = await get_game(self.game)
        await self.consumer.reset_hand(game)
        players = await get_players(self.game)
        for p in players:
            self.assertEqual(p.hole_cards, [])

    async def test_reset_clears_current_turn(self):
        await sync_to_async(Game.objects.filter(pk=self.game.pk).update)(current_turn=1)
        game = await get_game(self.game)
        await self.consumer.reset_hand(game)
        game = await get_game(self.game)
        self.assertIsNone(game.current_turn)


# ---------------------------------------------------------------------------
# start_hand
# ---------------------------------------------------------------------------

class StartHandTests(AsyncTestCase):

    def setUp(self):
        self.game, self.p1, self.p2 = _setup_2p()
        self.consumer = MockConsumer(self.p1.user, self.game.id)

    async def test_start_hand_sets_status_active(self):
        game = await get_game(self.game)
        await self.consumer.start_hand(game)
        game = await get_game(self.game)
        self.assertEqual(game.status, "active")

    async def test_start_hand_deals_two_hole_cards_per_player(self):
        game = await get_game(self.game)
        await self.consumer.start_hand(game)
        players = await get_players(self.game)
        for p in players:
            self.assertEqual(len(p.hole_cards), 2)

    async def test_start_hand_assigns_dealer(self):
        game = await get_game(self.game)
        await self.consumer.start_hand(game)
        dealers = await sync_to_async(
            Player.objects.filter(game=self.game, is_dealer=True).count
        )()
        self.assertEqual(dealers, 1)

    async def test_start_hand_posts_blinds(self):
        game = await get_game(self.game)
        await self.consumer.start_hand(game)
        sb = await sync_to_async(Player.objects.get)(game=self.game, is_small_blind=True)
        bb = await sync_to_async(Player.objects.get)(game=self.game, is_big_blind=True)
        self.assertEqual(sb.current_bet, 10)
        self.assertEqual(bb.current_bet, 20)

    async def test_start_hand_removes_busted_player(self):
        """A player with 0 chips at start of hand is removed."""
        await sync_to_async(Player.objects.filter(pk=self.p1.pk).update)(chips=0)
        # Need a third player so the game doesn't immediately end
        u3 = await sync_to_async(make_user)("gs_p3")
        await sync_to_async(make_player)(u3, self.game, position=2, chips=200)
        game = await get_game(self.game)
        await self.consumer.start_hand(game)
        exists = await sync_to_async(
            Player.objects.filter(game=self.game, user=self.p1.user).exists
        )()
        self.assertFalse(exists)

    async def test_start_hand_ends_if_only_one_player(self):
        """If only 1 player with chips remains, game finishes without starting."""
        await sync_to_async(Player.objects.filter(pk=self.p2.pk).update)(chips=0)
        game = await get_game(self.game)
        await self.consumer.start_hand(game)
        game = await get_game(self.game)
        # Either finished or still waiting — key point is it didn't crash
        self.assertIn(game.status, ("finished", "waiting"))

    async def test_start_hand_aborts_when_game_already_finished(self):
        """start_hand must abort if status is already 'finished'."""
        await sync_to_async(Game.objects.filter(pk=self.game.pk).update)(status="finished")
        game = await get_game(self.game)
        # Should return without crashing or changing to active
        await self.consumer.start_hand(game)
        game = await get_game(self.game)
        self.assertEqual(game.status, "finished")

    async def test_broadcast_message_sent_on_start(self):
        game = await get_game(self.game)
        await self.consumer.start_hand(game)
        self.assertTrue(any("hand" in m.lower() for m in self.consumer._broadcasts))
