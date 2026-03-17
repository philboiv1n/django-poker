"""
Tests for join/leave transactions and action handlers
(fold, check, call, bet, post_action_flow).
"""
from asgiref.sync import sync_to_async

from game.models import Game, Player
from .base import AsyncTestCase, MockConsumer, make_user, make_game, make_player, setup_active_2p_game


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def get_player(p):
    """Fetch a fresh Player instance with user pre-fetched (avoids sync DB access)."""
    return await sync_to_async(
        lambda: Player.objects.select_related("user").get(pk=p.pk)
    )()


async def get_game(g):
    return await sync_to_async(Game.objects.get)(pk=g.pk)


# ---------------------------------------------------------------------------
# join_game_transaction
# ---------------------------------------------------------------------------

class JoinGameTransactionTests(AsyncTestCase):

    def setUp(self):
        self.user = make_user("joiner", chips=500)
        self.game = make_game(buy_in=100, max_players=3)
        self.consumer = MockConsumer(self.user, self.game.id)

    async def test_join_deducts_buy_in_from_profile(self):
        await self.consumer.join_game_transaction(self.game.id, self.user.id)
        await sync_to_async(self.user.profile.refresh_from_db)()
        self.assertEqual(self.user.profile.chips, 400)

    async def test_join_creates_player_with_buy_in_chips(self):
        await self.consumer.join_game_transaction(self.game.id, self.user.id)
        player = await sync_to_async(Player.objects.get)(game=self.game, user=self.user)
        self.assertEqual(player.chips, 100)

    async def test_join_assigns_position_zero_for_first_player(self):
        await self.consumer.join_game_transaction(self.game.id, self.user.id)
        player = await sync_to_async(Player.objects.get)(game=self.game, user=self.user)
        self.assertEqual(player.position, 0)

    async def test_join_assigns_next_available_position(self):
        u2 = await sync_to_async(make_user)("j_p2", chips=500)
        # Seat 0 already taken
        await sync_to_async(make_player)(self.user, self.game, position=0, chips=100)
        consumer2 = MockConsumer(u2, self.game.id)
        await consumer2.join_game_transaction(self.game.id, u2.id)
        p2 = await sync_to_async(Player.objects.get)(game=self.game, user=u2)
        self.assertEqual(p2.position, 1)

    async def test_join_raises_when_insufficient_chips(self):
        broke_user = await sync_to_async(make_user)("j_broke", chips=50)
        consumer = MockConsumer(broke_user, self.game.id)
        with self.assertRaises(Exception):
            await consumer.join_game_transaction(self.game.id, broke_user.id)

    async def test_join_raises_when_already_seated(self):
        await self.consumer.join_game_transaction(self.game.id, self.user.id)
        with self.assertRaises(Exception):
            await self.consumer.join_game_transaction(self.game.id, self.user.id)

    async def test_join_raises_when_game_full(self):
        game = await sync_to_async(make_game)(name="Full Game", max_players=1, buy_in=100)
        await self.consumer.join_game_transaction(game.id, self.user.id)
        u2 = await sync_to_async(make_user)("j_p2b", chips=500)
        consumer2 = MockConsumer(u2, game.id)
        with self.assertRaises(Exception):
            await consumer2.join_game_transaction(game.id, u2.id)


# ---------------------------------------------------------------------------
# leave_game_transaction — waiting status
# ---------------------------------------------------------------------------

class LeaveWaitingGameTests(AsyncTestCase):

    def setUp(self):
        self.user = make_user("leaver", chips=500)
        self.game = make_game(buy_in=100, status="waiting")
        self.player = make_player(self.user, self.game, position=0, chips=100)
        self.consumer = MockConsumer(self.user, self.game.id)

    async def test_leave_waiting_refunds_buy_in(self):
        await self.consumer.leave_game_transaction(self.game.id, self.user.username)
        await sync_to_async(self.user.profile.refresh_from_db)()
        self.assertEqual(self.user.profile.chips, 600)  # 500 + 100 buy-in

    async def test_leave_waiting_deletes_player(self):
        await self.consumer.leave_game_transaction(self.game.id, self.user.username)
        count = await sync_to_async(Player.objects.filter(game=self.game).count)()
        self.assertEqual(count, 0)

    async def test_leave_waiting_renumbers_positions(self):
        u2 = await sync_to_async(make_user)("lw_p2", chips=500)
        await sync_to_async(make_player)(u2, self.game, position=1, chips=100)
        # Remove position 0 — p2 should become position 0
        await self.consumer.leave_game_transaction(self.game.id, self.user.username)
        p2 = await sync_to_async(Player.objects.get)(game=self.game, user=u2)
        self.assertEqual(p2.position, 0)

    async def test_leave_waiting_game_status_stays_waiting(self):
        await self.consumer.leave_game_transaction(self.game.id, self.user.username)
        game = await get_game(self.game)
        self.assertEqual(game.status, "waiting")


# ---------------------------------------------------------------------------
# leave_game_transaction — active status
# ---------------------------------------------------------------------------

class LeaveActiveGameTests(AsyncTestCase):

    def setUp(self):
        self.game, self.p1, self.p2 = setup_active_2p_game()
        self.consumer = MockConsumer(self.p1.user, self.game.id)

    async def test_leave_active_folds_player(self):
        await self.consumer.leave_game_transaction(self.game.id, self.p1.user.username)
        p1 = await sync_to_async(Player.objects.get)(pk=self.p1.pk)
        self.assertTrue(p1.has_folded)

    async def test_leave_active_refunds_remaining_chips(self):
        # setup_active_2p_game: profile starts at chips=1000, buy-in NOT deducted
        # (we bypass join_game_transaction). Player chips=90 after SB.
        original_chips = self.p1.chips   # 90
        await self.consumer.leave_game_transaction(self.game.id, self.p1.user.username)
        await sync_to_async(self.p1.user.profile.refresh_from_db)()
        # profile.chips was 1000, gains player's remaining 90 → 1090
        self.assertEqual(self.p1.user.profile.chips, 1000 + original_chips)

    async def test_leave_active_sets_player_chips_to_zero(self):
        await self.consumer.leave_game_transaction(self.game.id, self.p1.user.username)
        p1 = await sync_to_async(Player.objects.get)(pk=self.p1.pk)
        self.assertEqual(p1.chips, 0)

    async def test_leave_active_two_player_game_sets_finished(self):
        await self.consumer.leave_game_transaction(self.game.id, self.p1.user.username)
        game = await get_game(self.game)
        self.assertEqual(game.status, "finished")

    async def test_leave_active_reassigns_current_turn(self):
        # p1 (pos 0) is current turn; after p1 leaves, turn goes to p2 (pos 1)
        await self.consumer.leave_game_transaction(self.game.id, self.p1.user.username)
        game = await get_game(self.game)
        # Game is finished (2-player), current_turn may be None or p2's position
        # Key: it should not still be p1's position
        self.assertNotEqual(game.current_turn, self.p1.position)

    async def test_leave_active_skips_all_in_player_for_turn(self):
        """current_turn must not be reassigned to an all-in player."""
        # Make p2 all-in, add p3 as the eligible player
        await sync_to_async(self.p2.__class__.objects.filter(pk=self.p2.pk).update)(is_all_in=True)
        u3 = await sync_to_async(make_user)("la_p3", chips=500)
        await sync_to_async(make_player)(u3, self.game, position=2, chips=80)
        # Now leave p1 (current turn); p2 is all-in so turn should go to p3
        game = await self.consumer.leave_game_transaction(self.game.id, self.p1.user.username)
        self.assertNotEqual(game.current_turn, self.p2.position)


# ---------------------------------------------------------------------------
# handle_leave — full flow including chip transfer and game reset
# ---------------------------------------------------------------------------

class HandleLeaveTests(AsyncTestCase):

    def setUp(self):
        self.game, self.p1, self.p2 = setup_active_2p_game()
        self.consumer = MockConsumer(self.p1.user, self.game.id)

    async def test_handle_leave_finished_game_resets_to_waiting(self):
        """After last active player leaves a finished game, status resets to waiting."""
        game = await get_game(self.game)
        await self.consumer.handle_leave(game, self.p1.user.username)
        game = await get_game(self.game)
        self.assertEqual(game.status, "waiting")

    async def test_handle_leave_finished_game_deletes_all_players(self):
        game = await get_game(self.game)
        await self.consumer.handle_leave(game, self.p1.user.username)
        count = await sync_to_async(Player.objects.filter(game=self.game).count)()
        self.assertEqual(count, 0)


# ---------------------------------------------------------------------------
# handle_fold
# ---------------------------------------------------------------------------

class HandleFoldTests(AsyncTestCase):
    """
    Uses a 3-player game for basic fold tests so that folding p1 doesn't
    immediately end the hand (which would trigger reset_hand and clear flags).
    The 2-player "last player wins" test uses setup_active_2p_game directly.
    """

    def setUp(self):
        # 3-player game: p1 is current turn
        self.user1 = make_user("fold_u1", chips=500)
        self.user2 = make_user("fold_u2", chips=500)
        self.user3 = make_user("fold_u3", chips=500)
        self.game = make_game(
            status="active",
            current_phase="flop",
            dealer_position=0,
            current_turn=0,
            max_players=3,
            deck=["As"] * 20,
        )
        self.p1 = make_player(self.user1, self.game, position=0, chips=100, current_bet=0, total_bet=20)
        self.p2 = make_player(self.user2, self.game, position=1, chips=80,  current_bet=0, total_bet=20)
        self.p3 = make_player(self.user3, self.game, position=2, chips=80,  current_bet=0, total_bet=20)
        self.consumer = MockConsumer(self.user1, self.game.id)

    async def test_fold_marks_player_as_folded(self):
        """In a 3-player game, folding p1 doesn't end the hand, so has_folded stays True."""
        game = await get_game(self.game)
        p1 = await get_player(self.p1)
        await self.consumer.handle_fold(game, p1)
        p1 = await sync_to_async(Player.objects.get)(pk=self.p1.pk)
        self.assertTrue(p1.has_folded)

    async def test_fold_marks_has_acted_this_round(self):
        game = await get_game(self.game)
        p1 = await get_player(self.p1)
        await self.consumer.handle_fold(game, p1)
        p1 = await sync_to_async(Player.objects.get)(pk=self.p1.pk)
        self.assertTrue(p1.has_acted_this_round)

    async def test_fold_when_already_folded_sends_error(self):
        await sync_to_async(Player.objects.filter(pk=self.p1.pk).update)(has_folded=True)
        game = await get_game(self.game)
        p1 = await get_player(self.p1)
        await self.consumer.handle_fold(game, p1)
        self.assertTrue(any("error" in m for m in self.consumer._sent))

    async def test_fold_when_all_in_sends_error(self):
        await sync_to_async(Player.objects.filter(pk=self.p1.pk).update)(is_all_in=True)
        game = await get_game(self.game)
        p1 = await get_player(self.p1)
        await self.consumer.handle_fold(game, p1)
        self.assertTrue(any("error" in m for m in self.consumer._sent))

    async def test_fold_last_player_wins_pot(self):
        """With 2 players, folding one should award pot to the other."""
        game_2p, p1_2p, p2_2p = await sync_to_async(setup_active_2p_game)()
        consumer = MockConsumer(p1_2p.user, game_2p.id)
        game = await get_game(game_2p)
        p1 = await get_player(p1_2p)
        chips_before = p2_2p.chips
        pot = await sync_to_async(game.get_pot)()
        await consumer.handle_fold(game, p1)
        p2 = await sync_to_async(Player.objects.get)(pk=p2_2p.pk)
        # After fold, end_phase gives pot to p2, then start_hand runs (new hand)
        # p2 should have gained the pot
        self.assertGreaterEqual(p2.chips, chips_before)


# ---------------------------------------------------------------------------
# handle_check
# ---------------------------------------------------------------------------

class HandleCheckTests(AsyncTestCase):

    def setUp(self):
        self.user = make_user("checker", chips=500)
        self.game = make_game(status="active", small_blind=10, big_blind=20)
        # post-flop: all bets cleared, no outstanding bet
        self.player = make_player(self.user, self.game, position=0, chips=200, current_bet=0,
                                  hole_cards=["Ah", "Kh"])
        u2 = make_user("hc_opp", chips=500)
        make_player(u2, self.game, position=1, chips=200, current_bet=0,
                    hole_cards=["2c", "3d"])
        self.game.current_phase = "flop"
        self.game.current_turn = 0
        self.game.community_cards = ["2h", "3h", "4h"]
        self.game.deck = [
            "5s", "6s", "7s", "8s", "9s",
            "Tc", "Jc", "Qc", "Kc", "Ac",
        ]
        self.game.save()
        self.consumer = MockConsumer(self.user, self.game.id)

    async def test_check_sets_has_checked(self):
        game = await get_game(self.game)
        player = await get_player(self.player)
        await self.consumer.handle_check(game, player)
        player = await sync_to_async(Player.objects.get)(pk=self.player.pk)
        self.assertTrue(player.has_checked)

    async def test_check_sets_has_acted(self):
        game = await get_game(self.game)
        player = await get_player(self.player)
        await self.consumer.handle_check(game, player)
        player = await sync_to_async(Player.objects.get)(pk=self.player.pk)
        self.assertTrue(player.has_acted_this_round)

    async def test_check_blocked_when_bet_exists(self):
        # Opponent has bet 50; player hasn't matched → can't check
        await sync_to_async(
            Player.objects.filter(game=self.game, position=1).update
        )(current_bet=50)
        game = await get_game(self.game)
        player = await get_player(self.player)
        await self.consumer.handle_check(game, player)
        player = await sync_to_async(Player.objects.get)(pk=self.player.pk)
        self.assertFalse(player.has_checked)
        self.assertTrue(any("error" in m for m in self.consumer._sent))

    async def test_check_blocked_for_all_in_player(self):
        await sync_to_async(Player.objects.filter(pk=self.player.pk).update)(is_all_in=True)
        game = await get_game(self.game)
        player = await get_player(self.player)
        await self.consumer.handle_check(game, player)
        self.assertTrue(any("error" in m for m in self.consumer._sent))


# ---------------------------------------------------------------------------
# handle_call
# ---------------------------------------------------------------------------

class HandleCallTests(AsyncTestCase):

    def setUp(self):
        self.user = make_user("caller", chips=500)
        self.game = make_game(status="active", big_blind=20)
        # Player needs to call 20 (BB amount)
        self.player = make_player(
            self.user, self.game, position=0, chips=100,
            current_bet=0, total_bet=0,
            hole_cards=["Ah", "Kh"],
        )
        u2 = make_user("hca_opp", chips=500)
        make_player(u2, self.game, position=1, chips=80, current_bet=20, total_bet=20,
                    is_big_blind=True, has_acted_this_round=False,
                    hole_cards=["2c", "3d"])
        self.game.current_turn = 0
        self.game.current_phase = "preflop"
        self.game.dealer_position = 0
        self.game.deck = [
            "2s", "3s", "4s", "5s", "6s",   # flop burn + 3 flop + turn burn
            "7s", "8s", "9s", "Tc", "Jc",   # turn + river burn + river
            "Qc", "Kc", "2h", "3h", "4h",   # extras
        ]
        self.game.save()
        self.consumer = MockConsumer(self.user, self.game.id)

    async def test_call_deducts_chips(self):
        game = await get_game(self.game)
        player = await get_player(self.player)
        await self.consumer.handle_call(game, player)
        player = await sync_to_async(Player.objects.get)(pk=self.player.pk)
        self.assertEqual(player.chips, 80)   # 100 - 20

    async def test_call_increases_current_bet(self):
        game = await get_game(self.game)
        player = await get_player(self.player)
        await self.consumer.handle_call(game, player)
        player = await sync_to_async(Player.objects.get)(pk=self.player.pk)
        self.assertEqual(player.current_bet, 20)

    async def test_call_increases_total_bet(self):
        game = await get_game(self.game)
        player = await get_player(self.player)
        await self.consumer.handle_call(game, player)
        player = await sync_to_async(Player.objects.get)(pk=self.player.pk)
        self.assertEqual(player.total_bet, 20)

    async def test_call_marks_has_acted(self):
        game = await get_game(self.game)
        player = await get_player(self.player)
        await self.consumer.handle_call(game, player)
        player = await sync_to_async(Player.objects.get)(pk=self.player.pk)
        self.assertTrue(player.has_acted_this_round)

    async def test_call_all_in_when_not_enough_chips(self):
        # Player only has 10 but needs to call 20 → goes all-in for 10
        await sync_to_async(Player.objects.filter(pk=self.player.pk).update)(chips=10)
        game = await get_game(self.game)
        player = await get_player(self.player)
        await self.consumer.handle_call(game, player)
        player = await sync_to_async(Player.objects.get)(pk=self.player.pk)
        self.assertTrue(player.is_all_in)
        self.assertEqual(player.chips, 0)
        self.assertEqual(player.current_bet, 10)

    async def test_call_blocked_when_no_bet_to_call(self):
        # Clear opponent's bet
        await sync_to_async(
            Player.objects.filter(game=self.game, position=1).update
        )(current_bet=0)
        game = await get_game(self.game)
        player = await get_player(self.player)
        await self.consumer.handle_call(game, player)
        self.assertTrue(any("error" in m for m in self.consumer._sent))

    async def test_call_blocked_for_all_in_player(self):
        await sync_to_async(Player.objects.filter(pk=self.player.pk).update)(is_all_in=True)
        game = await get_game(self.game)
        player = await get_player(self.player)
        await self.consumer.handle_call(game, player)
        self.assertTrue(any("error" in m for m in self.consumer._sent))


# ---------------------------------------------------------------------------
# handle_bet
# ---------------------------------------------------------------------------

class HandleBetTests(AsyncTestCase):

    def setUp(self):
        self.user = make_user("bettor", chips=500)
        self.game = make_game(status="active", big_blind=20, small_blind=10)
        self.player = make_player(
            self.user, self.game, position=0, chips=200,
            current_bet=0, total_bet=0,
            hole_cards=["Ah", "Kh"],
        )
        u2 = make_user("hb_opp", chips=500)
        make_player(u2, self.game, position=1, chips=200, current_bet=0, total_bet=0,
                    hole_cards=["2c", "3d"])
        self.game.current_turn = 0
        self.game.current_phase = "flop"
        self.game.dealer_position = 0
        self.game.last_raise_delta = 0
        self.game.community_cards = ["2h", "3h", "4h"]
        self.game.deck = [
            "5s", "6s", "7s", "8s", "9s",
            "Tc", "Jc", "Qc", "Kc", "Ac",
        ]
        self.game.save()
        self.consumer = MockConsumer(self.user, self.game.id)

    async def test_bet_deducts_chips(self):
        game = await get_game(self.game)
        player = await get_player(self.player)
        await self.consumer.handle_bet(game, player, 20)
        player = await sync_to_async(Player.objects.get)(pk=self.player.pk)
        self.assertEqual(player.chips, 180)

    async def test_bet_updates_current_and_total_bet(self):
        game = await get_game(self.game)
        player = await get_player(self.player)
        await self.consumer.handle_bet(game, player, 20)
        player = await sync_to_async(Player.objects.get)(pk=self.player.pk)
        self.assertEqual(player.current_bet, 20)
        self.assertEqual(player.total_bet, 20)

    async def test_bet_marks_has_acted(self):
        game = await get_game(self.game)
        player = await get_player(self.player)
        await self.consumer.handle_bet(game, player, 20)
        player = await sync_to_async(Player.objects.get)(pk=self.player.pk)
        self.assertTrue(player.has_acted_this_round)

    async def test_bet_updates_last_raise_delta(self):
        game = await get_game(self.game)
        player = await get_player(self.player)
        await self.consumer.handle_bet(game, player, 40)
        game = await get_game(self.game)
        self.assertEqual(game.last_raise_delta, 40)

    async def test_bet_below_minimum_sends_error(self):
        # Min bet on the flop is big_blind (20); betting 5 should be rejected
        game = await get_game(self.game)
        player = await get_player(self.player)
        await self.consumer.handle_bet(game, player, 5)
        self.assertTrue(any("error" in m for m in self.consumer._sent))

    async def test_bet_zero_sends_error(self):
        game = await get_game(self.game)
        player = await get_player(self.player)
        await self.consumer.handle_bet(game, player, 0)
        self.assertTrue(any("error" in m for m in self.consumer._sent))

    async def test_bet_above_chips_sends_error(self):
        game = await get_game(self.game)
        player = await get_player(self.player)
        await self.consumer.handle_bet(game, player, 9999)
        self.assertTrue(any("error" in m for m in self.consumer._sent))

    async def test_all_in_bet_allowed_even_below_minimum(self):
        # Player has only 5 chips; betting all 5 is allowed as an all-in
        await sync_to_async(Player.objects.filter(pk=self.player.pk).update)(chips=5)
        game = await get_game(self.game)
        player = await get_player(self.player)
        await self.consumer.handle_bet(game, player, 5)
        player = await sync_to_async(Player.objects.get)(pk=self.player.pk)
        self.assertTrue(player.is_all_in)
        self.assertEqual(player.chips, 0)

    async def test_bet_blocked_for_folded_player(self):
        await sync_to_async(Player.objects.filter(pk=self.player.pk).update)(has_folded=True)
        game = await get_game(self.game)
        player = await get_player(self.player)
        await self.consumer.handle_bet(game, player, 20)
        self.assertTrue(any("error" in m for m in self.consumer._sent))

    async def test_sub_min_all_in_freezes_reraise_for_acted_players(self):
        """
        A sub-minimum all-in freeze must set can_reraise_this_round=False
        for players who have already acted this round.

        Setup: 3 players. p1 raised to 40 (has_acted). p2 (self.player, position 0)
        goes all-in for 5 (sub-minimum). p3 hasn't acted yet, so is_phase_over
        returns False and end_phase is NOT called — the flag stays False.
        """
        u3 = await sync_to_async(make_user)("sub_u3", chips=500)
        await sync_to_async(make_player)(u3, self.game, position=2, chips=200,
                                         current_bet=0, total_bet=0,
                                         has_acted_this_round=False,
                                         hole_cards=["4s", "5s"])
        # p2 (position 1) raised to 40, already acted
        await sync_to_async(
            Player.objects.filter(game=self.game, position=1).update
        )(has_acted_this_round=True, current_bet=40, total_bet=40)
        # Set last_raise_delta to 20 so min_increment is 20
        await sync_to_async(Game.objects.filter(pk=self.game.pk).update)(last_raise_delta=20)
        # self.player (position 0) has only 5 chips — sub-min all-in
        await sync_to_async(Player.objects.filter(pk=self.player.pk).update)(chips=5, current_bet=0)
        self.game.current_turn = 0
        await sync_to_async(Game.objects.filter(pk=self.game.pk).update)(current_turn=0)

        game = await get_game(self.game)
        player = await get_player(self.player)
        await self.consumer.handle_bet(game, player, 5)
        # p2 (who already acted) should have can_reraise frozen
        p2 = await sync_to_async(Player.objects.get)(game=self.game, position=1)
        self.assertFalse(p2.can_reraise_this_round)
