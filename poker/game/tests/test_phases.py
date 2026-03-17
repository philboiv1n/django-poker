"""
Tests for is_phase_over, end_phase, goto_next_phase, and handle_showdown.
"""
from asgiref.sync import sync_to_async
from game.models import Game, Player
from .base import AsyncTestCase, MockConsumer, make_user, make_game, make_player, setup_active_2p_game


class NoStartHandConsumer(MockConsumer):
    """MockConsumer variant that suppresses start_hand so new-hand state
    changes don't interfere with end_phase assertions."""
    async def start_hand(self, game):
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def get_player(p):
    return await sync_to_async(Player.objects.get)(pk=p.pk)

async def get_game(g):
    return await sync_to_async(Game.objects.get)(pk=g.pk)


# ---------------------------------------------------------------------------
# is_phase_over
# ---------------------------------------------------------------------------

class IsPhaseOverTests(AsyncTestCase):

    def setUp(self):
        self.game, self.p1, self.p2 = setup_active_2p_game()
        self.consumer = MockConsumer(self.p1.user, self.game.id)

    async def _active_players(self):
        return await sync_to_async(
            lambda: list(
                Player.objects.filter(game=self.game, has_folded=False).order_by("position")
            )
        )()

    async def test_phase_over_when_all_checked(self):
        await sync_to_async(Player.objects.filter(game=self.game).update)(
            has_checked=True, has_acted_this_round=True, current_bet=0,
            is_big_blind=False,
        )
        game = await get_game(self.game)
        game.current_phase = "flop"
        result = await self.consumer.is_phase_over(game)
        self.assertTrue(result)

    async def test_phase_over_when_all_matched_bet(self):
        await sync_to_async(Player.objects.filter(game=self.game).update)(
            has_acted_this_round=True, current_bet=50,
            is_big_blind=False,
        )
        game = await get_game(self.game)
        result = await self.consumer.is_phase_over(game)
        self.assertTrue(result)

    async def test_phase_not_over_when_player_has_not_acted(self):
        await sync_to_async(Player.objects.filter(pk=self.p1.pk).update)(
            has_acted_this_round=False, current_bet=10,
        )
        await sync_to_async(Player.objects.filter(pk=self.p2.pk).update)(
            has_acted_this_round=True, current_bet=20, is_big_blind=True,
        )
        game = await get_game(self.game)
        result = await self.consumer.is_phase_over(game)
        self.assertFalse(result)

    async def test_phase_not_over_preflop_bb_hasnt_acted(self):
        """Preflop: if BB hasn't acted, phase must not end even if bets match."""
        await sync_to_async(Player.objects.filter(pk=self.p1.pk).update)(
            has_acted_this_round=True, current_bet=20, is_big_blind=False,
        )
        await sync_to_async(Player.objects.filter(pk=self.p2.pk).update)(
            has_acted_this_round=False, current_bet=20, is_big_blind=True,
        )
        game = await get_game(self.game)
        game.current_phase = "preflop"
        result = await self.consumer.is_phase_over(game)
        self.assertFalse(result)

    async def test_phase_over_when_all_all_in(self):
        await sync_to_async(Player.objects.filter(game=self.game).update)(
            is_all_in=True, is_big_blind=False,
        )
        game = await get_game(self.game)
        result = await self.consumer.is_phase_over(game)
        self.assertTrue(result)

    async def test_phase_not_over_with_one_active_player(self):
        # Fold p2 → only p1 active
        await sync_to_async(Player.objects.filter(pk=self.p2.pk).update)(has_folded=True)
        game = await get_game(self.game)
        result = await self.consumer.is_phase_over(game)
        self.assertFalse(result)

    async def test_phase_not_over_mixed_bets(self):
        """p1 bet 50, p2 only bet 20 — not over."""
        await sync_to_async(Player.objects.filter(pk=self.p1.pk).update)(
            current_bet=50, has_acted_this_round=True, is_big_blind=False,
        )
        await sync_to_async(Player.objects.filter(pk=self.p2.pk).update)(
            current_bet=20, has_acted_this_round=True, is_big_blind=False,
        )
        game = await get_game(self.game)
        result = await self.consumer.is_phase_over(game)
        self.assertFalse(result)


# ---------------------------------------------------------------------------
# end_phase — with explicit winner (all others folded)
# ---------------------------------------------------------------------------

class EndPhaseWithWinnerTests(AsyncTestCase):

    def setUp(self):
        self.game, self.p1, self.p2 = setup_active_2p_game()
        self.consumer = NoStartHandConsumer(self.p1.user, self.game.id)

    async def test_winner_receives_pot(self):
        game = await get_game(self.game)
        pot = await sync_to_async(game.get_pot)()
        winner = await sync_to_async(Player.objects.select_related("user").get)(pk=self.p2.pk)
        chips_before = winner.chips
        await self.consumer.end_phase(game, winner=winner)
        winner = await get_player(self.p2)
        self.assertEqual(winner.chips, chips_before + pot)

    async def test_winner_message_broadcast(self):
        game = await get_game(self.game)
        winner = await sync_to_async(Player.objects.select_related("user").get)(pk=self.p2.pk)
        await self.consumer.end_phase(game, winner=winner)
        self.assertTrue(any("wins" in m for m in self.consumer._broadcasts))

    async def test_player_states_reset_after_end_phase_with_winner(self):
        game = await get_game(self.game)
        winner = await sync_to_async(Player.objects.select_related("user").get)(pk=self.p2.pk)
        # Set some state to ensure reset
        await sync_to_async(Player.objects.filter(game=self.game).update)(
            current_bet=50, has_checked=True,
        )
        await self.consumer.end_phase(game, winner=winner)
        # All current_bets should be 0 now
        players = await sync_to_async(list)(Player.objects.filter(game=self.game))
        for p in players:
            self.assertEqual(p.current_bet, 0)


# ---------------------------------------------------------------------------
# end_phase — without winner (phase advances)
# ---------------------------------------------------------------------------

class EndPhaseAdvanceTests(AsyncTestCase):

    def setUp(self):
        self.game, self.p1, self.p2 = setup_active_2p_game()
        self.consumer = MockConsumer(self.p1.user, self.game.id)

    async def test_preflop_advances_to_flop(self):
        await sync_to_async(Game.objects.filter(pk=self.game.pk).update)(current_phase="preflop")
        game = await get_game(self.game)
        await self.consumer.end_phase(game)
        game = await get_game(self.game)
        self.assertEqual(game.current_phase, "flop")

    async def test_player_bets_reset_between_phases(self):
        await sync_to_async(Player.objects.filter(game=self.game).update)(
            current_bet=30, has_checked=True, has_acted_this_round=True,
        )
        game = await get_game(self.game)
        await self.consumer.end_phase(game)
        players = await sync_to_async(list)(Player.objects.filter(game=self.game))
        for p in players:
            self.assertEqual(p.current_bet, 0)
            self.assertFalse(p.has_checked)
            self.assertFalse(p.has_acted_this_round)

    async def test_last_raise_delta_reset(self):
        await sync_to_async(Game.objects.filter(pk=self.game.pk).update)(last_raise_delta=40)
        game = await get_game(self.game)
        await self.consumer.end_phase(game)
        game = await get_game(self.game)
        self.assertEqual(game.last_raise_delta, 0)


# ---------------------------------------------------------------------------
# goto_next_phase — community cards
# ---------------------------------------------------------------------------

class GotoNextPhaseTests(AsyncTestCase):

    def setUp(self):
        # Stock the deck with enough unique cards
        self.game, self.p1, self.p2 = setup_active_2p_game()
        self.consumer = MockConsumer(self.p1.user, self.game.id)

    async def test_flop_deals_three_community_cards(self):
        game = await get_game(self.game)
        game.current_phase = "preflop"
        await self.consumer.goto_next_phase(game)
        game = await get_game(self.game)
        self.assertEqual(len(game.community_cards), 3)
        self.assertEqual(game.current_phase, "flop")

    async def test_turn_deals_one_community_card(self):
        await sync_to_async(Game.objects.filter(pk=self.game.pk).update)(
            current_phase="flop",
            community_cards=["As", "Ks", "Qs"],
        )
        game = await get_game(self.game)
        await self.consumer.goto_next_phase(game)
        game = await get_game(self.game)
        self.assertEqual(len(game.community_cards), 4)
        self.assertEqual(game.current_phase, "turn")

    async def test_river_deals_one_community_card(self):
        await sync_to_async(Game.objects.filter(pk=self.game.pk).update)(
            current_phase="turn",
            community_cards=["As", "Ks", "Qs", "Js"],
        )
        game = await get_game(self.game)
        await self.consumer.goto_next_phase(game)
        game = await get_game(self.game)
        self.assertEqual(len(game.community_cards), 5)
        self.assertEqual(game.current_phase, "river")

    async def test_river_to_showdown_updates_phase(self):
        await sync_to_async(Game.objects.filter(pk=self.game.pk).update)(
            current_phase="river",
            community_cards=["As", "Ks", "Qs", "Js", "Ts"],
        )
        # Give players hole cards so showdown doesn't crash
        await sync_to_async(Player.objects.filter(pk=self.p1.pk).update)(hole_cards=["2h", "3d"])
        await sync_to_async(Player.objects.filter(pk=self.p2.pk).update)(hole_cards=["4h", "5d"])
        game = await get_game(self.game)
        await self.consumer.goto_next_phase(game)
        game = await get_game(self.game)
        self.assertEqual(game.current_phase, "showdown")

    async def test_burn_card_removed_from_deck(self):
        original_deck_size = len(self.game.deck)
        game = await get_game(self.game)
        game.current_phase = "preflop"
        await self.consumer.goto_next_phase(game)
        game = await get_game(self.game)
        # 1 burned + 3 dealt = 4 cards removed
        self.assertEqual(len(game.deck), original_deck_size - 4)


# ---------------------------------------------------------------------------
# handle_showdown — pot distribution
# ---------------------------------------------------------------------------

class HandleShowdownTests(AsyncTestCase):

    def setUp(self):
        self.consumer = None  # set per test

    async def _setup_game_async(self, community_cards=None, name="Showdown Game"):
        """Create a showdown game asynchronously."""
        import uuid
        game = await sync_to_async(make_game)(
            name=f"{name}_{uuid.uuid4().hex[:8]}",
            status="active",
            current_phase="showdown",
            community_cards=community_cards or ["As", "Ks", "Qs", "Js", "Ts"],
        )
        return game

    async def test_winner_takes_full_pot_two_players(self):
        """The player with the best hand takes the whole pot."""
        u1 = await sync_to_async(make_user)("winner_p1")
        u2 = await sync_to_async(make_user)("loser_p2")
        # Community gives p1 (Ah Kh) a Royal Flush; p2 (2c 3d) gets Two Pair at best.
        # Board deliberately NOT a royal flush by itself so p1's hole cards matter.
        game = await self._setup_game_async(
            community_cards=["Qh", "Jh", "Th", "2s", "7c"]
        )
        p1 = await sync_to_async(make_player)(u1, game, position=0, chips=0, total_bet=100,
                                               hole_cards=["Ah", "Kh"])   # Royal Flush
        p2 = await sync_to_async(make_player)(u2, game, position=1, chips=0, total_bet=100,
                                               hole_cards=["2c", "3d"])   # Two Pair
        consumer = MockConsumer(u1, game.id)
        game = await get_game(game)
        await consumer.handle_showdown(game)
        p1 = await get_player(p1)
        p2 = await get_player(p2)
        total_chips = p1.chips + p2.chips
        self.assertEqual(total_chips, 200)   # full pot redistributed
        self.assertGreater(p1.chips, p2.chips)  # p1 won with Royal Flush

    async def test_split_pot_equal_hands(self):
        """Equal hands → pot split evenly."""
        u1 = await sync_to_async(make_user)("split_p1")
        u2 = await sync_to_async(make_user)("split_p2")
        game = await self._setup_game_async(community_cards=["As", "Ks", "Qs", "Js", "Ts"],
                                             name="Split Game")
        # Both players use the board (royal flush) → tie
        p1 = await sync_to_async(make_player)(u1, game, position=0, chips=0, total_bet=100,
                                               hole_cards=["2h", "3d"])
        p2 = await sync_to_async(make_player)(u2, game, position=1, chips=0, total_bet=100,
                                               hole_cards=["2c", "4c"])
        consumer = MockConsumer(u1, game.id)
        game = await get_game(game)
        await consumer.handle_showdown(game)
        p1 = await get_player(p1)
        p2 = await get_player(p2)
        self.assertEqual(p1.chips, p2.chips)   # equal split

    async def test_side_pot_all_in_player_cannot_win_more_than_contributed(self):
        """
        3 players: A all-in 50, B all-in 100, C calls 100.
        A can only win from the first pot (50*3=150).
        B and C compete for the remainder (50*2=100).
        """
        u1 = await sync_to_async(make_user)("allin_50")
        u2 = await sync_to_async(make_user)("allin_100")
        u3 = await sync_to_async(make_user)("caller")
        game = await self._setup_game_async(community_cards=["2h", "3h", "4h", "5h", "6h"],
                                             name="Side Pot Game")
        # A has the best hand (straight flush 2-6 of hearts with hole cards)
        p1 = await sync_to_async(make_player)(u1, game, position=0, chips=0, total_bet=50,
                                               is_all_in=True, hole_cards=["7h", "8h"])
        p2 = await sync_to_async(make_player)(u2, game, position=1, chips=0, total_bet=100,
                                               is_all_in=True, hole_cards=["As", "Kd"])
        p3 = await sync_to_async(make_player)(u3, game, position=2, chips=0, total_bet=100,
                                               hole_cards=["Ah", "Ks"])
        game_obj = await get_game(game)
        game_obj.dealer_position = 2
        consumer = MockConsumer(u1, game.id)
        await consumer.handle_showdown(game_obj)
        p1 = await get_player(p1)
        p2 = await get_player(p2)
        p3 = await get_player(p3)
        total = p1.chips + p2.chips + p3.chips
        self.assertEqual(total, 250)    # all chips redistributed
        # p1 (best hand) wins main pot; can win at most 50*3=150
        self.assertLessEqual(p1.chips, 150)

    async def test_winner_announcement_broadcast(self):
        u1 = await sync_to_async(make_user)("showdown_w")
        u2 = await sync_to_async(make_user)("showdown_l")
        game = await self._setup_game_async(name="Broadcast Game")
        await sync_to_async(make_player)(u1, game, position=0, chips=0, total_bet=100,
                                         hole_cards=["Ah", "Kh"])
        await sync_to_async(make_player)(u2, game, position=1, chips=0, total_bet=100,
                                         hole_cards=["2c", "3d"])
        consumer = MockConsumer(u1, game.id)
        game = await get_game(game)
        await consumer.handle_showdown(game)
        self.assertTrue(any("wins" in m for m in consumer._broadcasts))

    async def test_folded_players_chips_go_to_winner(self):
        """Folded player's chips end up in the pot, won by active player."""
        u1 = await sync_to_async(make_user)("active_hw")
        u2 = await sync_to_async(make_user)("folded_hw")
        game = await self._setup_game_async(name="Folded Game")
        p1 = await sync_to_async(make_player)(u1, game, position=0, chips=0, total_bet=100,
                                               hole_cards=["Ah", "Kh"])
        await sync_to_async(make_player)(u2, game, position=1, chips=0, total_bet=100,
                                         has_folded=True, hole_cards=[])
        consumer = MockConsumer(u1, game.id)
        game = await get_game(game)
        await consumer.handle_showdown(game)
        p1 = await get_player(p1)
        self.assertEqual(p1.chips, 200)  # wins all
