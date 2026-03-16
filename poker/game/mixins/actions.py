import json
import asyncio
import logging
from django.db import transaction
from django.utils.timezone import now
from asgiref.sync import sync_to_async
from ..models import Game, Player, User
from ..utils import can_user_do_action

logger = logging.getLogger(__name__)


class ActionsMixin:
    """Player action handlers: join, leave, fold, check, call, bet, and post-action flow."""

    async def handle_join(self, game: Game, player_username: str) -> None:
        """
        Handles a player joining the game, with transaction safety and better structure.
        """

        logger.debug("handle_join: %s", player_username)

        try:
            user = await sync_to_async(User.objects.get)(username=player_username)
        except User.DoesNotExist:
            await self.send(text_data=json.dumps({"error": "User not found"}))
            return

        try:
            await self.join_game_transaction(game.id, user.id)
        except Exception as e:
            await self.send(text_data=json.dumps({"error": str(e)}))
            return

        await self.broadcast_messages(f"🪑 {player_username} has joined the table.")

        # Check if game should start
        player_count = await sync_to_async(lambda: game.players.count())()
        if game.game_type == "sit_and_go" and player_count == game.max_players:
            await self.start_hand(game)
        else:
            await asyncio.gather(
                self.broadcast_game_state(game),
                self.broadcast_private(game),
            )

    @sync_to_async
    @transaction.atomic
    def join_game_transaction(self, game_id, user_id):
        game = Game.objects.select_for_update().get(id=game_id)
        user = User.objects.select_related("profile").get(id=user_id)
        profile = user.profile

        if game.players.filter(user=user).exists():
            raise Exception("You're already seated at this table")

        taken_positions = list(game.players.values_list("position", flat=True))
        available_positions = [pos for pos in range(game.max_players) if pos not in taken_positions]

        if not available_positions:
            raise Exception("Table is full")

        if profile.chips < game.buy_in:
            raise Exception("Not enough chips")

        profile.chips -= game.buy_in
        profile.save()

        Player.objects.create(
            game=game,
            user=user,
            position=min(available_positions),
            chips=game.buy_in
        )

    async def handle_leave(self, game: Game, player_username: str) -> None:
        """
        Handles a player leaving the game.

        If the game hasn't started, refunds their buy-in. Updates game state accordingly.
        If this player was the dealer or currently active, reassigns those roles.
        Broadcasts updated game state and removes the player.

        Args:
            game (Game): The game instance.
            player_username (str): The username of the player leaving the table.

        Returns:
            None
        """

        logger.debug("handle_leave: %s", player_username)

        game = await self.leave_game_transaction(game.id, player_username)

        if game.status == "finished":
            # Transfer any remaining in-game chips to profiles before resetting
            remaining_players = await sync_to_async(
                lambda: list(game.players.all()), thread_sensitive=True
            )()
            for p in remaining_players:
                if p.chips > 0:
                    await self.transfer_chips_to_profile(game, p)
            await self.reset_hand(game)
        elif game.status == "active" and await self.is_phase_over(game):
            await self.end_phase(game)
            return

        leave_message = f"⚠️ {player_username} has left the table."
        await asyncio.gather(
            self.broadcast_messages(leave_message),
            self.broadcast_game_state(game),
            self.send_private_to_user(self.user),
        )

    @sync_to_async
    @transaction.atomic
    def leave_game_transaction(self, game_id, username):
        game = Game.objects.select_for_update().get(id=game_id)
        player = game.players.select_related("user__profile").filter(user__username=username).first()

        if not player:
            raise Exception("Player not found")

        player_position = player.position
        profile = player.user.profile

        if game.status == "active":
            # Mid-hand: fold the player and immediately refund their un-bet chips.
            # Keep the Player record so total_bet remains in get_pot(); it is
            # cleaned up at the start of the next hand (chips == 0 path).
            profile.chips += player.chips
            profile.save()
            player.chips = 0
            player.has_folded = True
            player.has_acted_this_round = True
            player.save()

            # Fetch once; reuse for both turn reassignment and finish check
            active_remaining = list(
                game.players.filter(has_folded=False).order_by("position")
            )
            if game.current_turn == player_position and active_remaining:
                game.current_turn = active_remaining[0].position

            # End game if fewer than 2 players can still act
            if len(active_remaining) < 2:
                game.status = "finished"

        elif game.status == "waiting":
            # Pre-game: refund full buy-in and remove the player
            if game.game_type == "sit_and_go":
                profile.chips += game.buy_in
                profile.save()

            player.delete()

            remaining_players = list(game.players.order_by("position"))
            for new_pos, p in enumerate(remaining_players):
                p.position = new_pos
            Player.objects.bulk_update(remaining_players, ["position"])

            if len(remaining_players) < 2:
                game.status = "waiting"
            else:
                if game.dealer_position == player_position:
                    game.dealer_position = remaining_players[0].position
                if game.current_turn == player_position:
                    game.current_turn = remaining_players[0].position

        else:
            # Game already finished — just clean up
            player.delete()

        game.save()
        return game

    async def handle_fold(self, game: Game, player: Player) -> None:
        """
        Handles the fold action from a player.

        Marks the player as folded and updates their state. Broadcasts a message to all
        players and triggers the post-action flow to determine the next step in the hand.

        Args:
            game (Game): The current game instance.
            player (Player): The player folding their hand.

        Returns:
            None
        """

        logger.debug("handle_fold")

        # Safety Check
        if player.is_all_in or player.has_folded:
            await self.send(text_data=json.dumps({"error": "You cannot fold."}))
            return

        username = player.user.username
        await self.broadcast_messages(f"🔴 {username} folded.")

        player.has_folded = True
        player.has_acted_this_round = True
        player.last_active = now()
        await sync_to_async(
            lambda: player.save(update_fields=["has_folded", "has_acted_this_round", "last_active"])
        )()

        # Check if only one active player remains
        active_players = await sync_to_async(
            lambda: list(game.players.select_related("user").filter(has_folded=False)),
            thread_sensitive=True,
        )()
        if len(active_players) == 1:
            await self.end_phase(game, winner=active_players[0])
            return

        await self.post_action_flow(game)

    async def handle_check(self, game: Game, player: Player) -> None:
        """
        Handles the check action from a player.

        Validates if checking is allowed (no outstanding bets). Updates player status
        and broadcasts the check to all players. Triggers the post-action flow.

        Args:
            game (Game): The current game instance.
            player (Player): The player choosing to check.

        Returns:
            None
        """

        logger.debug("handle_check")

        # Safety Check
        if player.is_all_in or player.has_folded:
            await self.send(text_data=json.dumps({"error": "You cannot check."}))
            return

        can_check = await sync_to_async(
            lambda: can_user_do_action(game, player, "check")
        )()

        if can_check == True:
            # Mark the player as checked
            player.has_checked = True
            player.has_acted_this_round = True
            player.last_active = now()
            await sync_to_async(
                lambda: player.save(update_fields=["has_checked", "has_acted_this_round", "last_active"])
            )()

            # Broadcast
            username = player.user.username
            await self.broadcast_messages(f"🔵 {username} checked.")

        else:
            await self.send(json.dumps({"error": "Cannot check"}))
            return

        # Move to the post action flow
        await self.post_action_flow(game)

    async def handle_call(self, game: Game, player: Player) -> None:
        """
        Handles the call action from a player.

        Validates if calling is possible, calculates the amount needed to match the
        current bet, and updates the player's chip and bet status. Handles all-in logic
        and triggers post-action flow.

        Args:
            game (Game): The current game instance.
            player (Player): The player calling a bet.

        Returns:
            None
        """

        logger.debug("handle_call")

        # Safety Check
        if player.is_all_in or player.has_folded:
            await self.send(text_data=json.dumps({"error": "You cannot call."}))
            return

        # Get the highest bet currently on the table
        highest_bet = await sync_to_async(
            lambda: max(game.players.values_list("current_bet", flat=True), default=0)
        )()

        call_amount = highest_bet - player.current_bet

        if call_amount <= 0:
            await self.send(text_data=json.dumps({"error": "Cannot call, please check, raise or fold."}))
            return

        # Handle all-in scenario
        if player.chips <= call_amount:
            call_amount = player.chips  # All-in
            player.is_all_in = True  # Mark player as all-in

        # Deduct chips and update current bet
        player.chips -= call_amount
        player.current_bet += call_amount
        player.total_bet += call_amount
        player.has_acted_this_round = True
        player.last_active = now()
        await sync_to_async(
            lambda: player.save(
                update_fields=["chips", "current_bet", "total_bet", "has_acted_this_round", "is_all_in", "last_active"]
            )
        )()

        # Broadcast
        username = player.user.username

        if player.is_all_in:
            await self.broadcast_messages(
                f"🟣 {username} goes ALL-IN with {call_amount} chips!"
            )
        else:
            await self.broadcast_messages(f"🟢 {username} called {call_amount} chips.")

        # Move to the post action flow
        await self.post_action_flow(game)

    async def handle_bet(self, game: Game, player: Player, amount: int) -> None:
        """
        Handles a player placing a bet.

        This method validates the bet amount, updates the player's chip count and current bet,
        determines if the player is going all-in, and broadcasts the action to all players.
        It then continues the hand progression via post-action flow.

        Args:
            game (Game): The game instance the player is betting in.
            player (Player): The player placing the bet.
            amount (int): The number of chips the player wants to bet.

        Returns:
            None
        """

        logger.debug("handle_bet: amount=%s", amount)

        # Safety Check
        if player.is_all_in or player.has_folded:
            await self.send(text_data=json.dumps({"error": "You cannot bet."}))
            return

        # Validate the bet amount
        if amount <= 0 or amount > player.chips:
            await self.send(text_data=json.dumps({"error": "Invalid bet amount."}))
            return

        # Block a raise if a sub-minimum all-in has frozen this player's option
        if not player.can_reraise_this_round:
            await self.send(json.dumps({"error": "You can only call or fold."}))
            return

        highest_bet = await sync_to_async(
            lambda: max(game.players.values_list("current_bet", flat=True), default=0)
        )()

        big_blind = game.big_blind
        last_raise_delta = game.last_raise_delta

        # Correct minimum raise:
        #   - First bet of the round: must be at least big_blind
        #   - Re-raise: must increase the bet by at least the previous raise increment
        #     (or big_blind if no raise has happened yet in this round)
        min_increment = max(last_raise_delta, big_blind)
        min_raise_to = highest_bet + min_increment
        min_additional = max(0, min_raise_to - player.current_bet)

        is_all_in_attempt = (amount == player.chips)

        if amount < min_additional and not is_all_in_attempt:
            await self.send(json.dumps({"error": f"Minimum raise to {min_raise_to} chips."}))
            return

        # All-in check
        if is_all_in_attempt:
            player.is_all_in = True

        # Deduct bet from player's chips
        player.chips -= amount
        player.current_bet += amount
        player.total_bet += amount
        player.has_acted_this_round = True
        player.last_active = now()
        await sync_to_async(
            lambda: player.save(
                update_fields=["chips", "current_bet", "total_bet", "has_acted_this_round", "is_all_in", "last_active"]
            )
        )()

        # Update last_raise_delta and handle sub-minimum all-in betting freeze
        new_bet_level = player.current_bet  # after the bet
        raise_increment = new_bet_level - highest_bet

        if is_all_in_attempt and raise_increment < min_increment:
            # Sub-minimum all-in: freeze re-raise rights for players who already acted
            already_acted = await sync_to_async(
                lambda: list(game.players.filter(has_acted_this_round=True, has_folded=False)),
                thread_sensitive=True,
            )()
            for p in already_acted:
                p.can_reraise_this_round = False
            await sync_to_async(
                lambda: Player.objects.bulk_update(already_acted, ["can_reraise_this_round"])
            )()
            # last_raise_delta stays unchanged (sub-minimum raise doesn't update it)
        else:
            # Full raise: update the raise delta for the next re-raise calculation
            game.last_raise_delta = raise_increment
        await sync_to_async(lambda: game.save(update_fields=["last_raise_delta"]))()

        # Broadcast
        username = player.user.username

        if player.is_all_in:
            await self.broadcast_messages(
                f"🟣 {username} goes ALL-IN with {amount} chips!"
            )
        else:
            await self.broadcast_messages(
                f"🟡 {username} bet {amount} chips."
            )

        # Move to the post action flow
        await self.post_action_flow(game)

    async def post_action_flow(self, game: Game) -> None:
        """
        Handles game progression after each player's action.

        Determines the next steps based on current player states. Ends the hand if only
        one player remains. If players are all-in, runs out the board. Otherwise, decides
        whether to end the current phase or move to the next player.

        Args:
            game (Game): The current game instance.

        Returns:
            None
        """

        logger.debug("post_action_flow")

        active_players = await sync_to_async(
            lambda: list(game.players.filter(has_folded=False)), thread_sensitive=True
        )()
        not_all_in_players = [p for p in active_players if not p.is_all_in]

        # If everyone is all-in, auto-run remaining board
        if len(not_all_in_players) == 0:
            while game.current_phase != "showdown":
                await self.goto_next_phase(game)
            await self.start_hand(game)
            return

        # If only one player is not all-in and they've matched the highest total bet
        if len(not_all_in_players) == 1:
            max_bet = max(p.total_bet for p in active_players)
            remaining = not_all_in_players[0]
            if remaining.total_bet >= max_bet:
                while game.current_phase != "showdown":
                    await self.goto_next_phase(game)
                await self.start_hand(game)
                return

        # Check if the phase is over (reuse the already-fetched active_players list)
        if await self.is_phase_over(game, active_players):
            await self.end_phase(game)
        else:
            logger.debug("post_action_flow: advancing to next player (current_turn=%s)", game.current_turn)
            await self.next_player(game, game.current_turn)
