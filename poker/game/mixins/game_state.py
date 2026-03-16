import random
import asyncio
from asgiref.sync import sync_to_async
from ..models import Game, Player
from ..utils import create_deck


class GameStateMixin:
    """Hand lifecycle methods: start_hand, reset_hand, rotate_dealer, assign_blinds, next_player."""

    async def start_hand(self, game: Game) -> None:
        """
        Starts a new hand for the game.

        This method resets all players statuses, removes players without chips,
        reinitializes the hand (deck, dealer, blinds), and deals new hole cards.
        If only one player remains, transfers chips and ends the game.
        Finally, updates the game state and broadcasts the new hand.

        Args:
            game (Game): The current game instance.

        Returns:
            None
        """

        print("* START HAND")

        # Fetch active players
        players = await sync_to_async(
            lambda: list(game.players.order_by("position")), thread_sensitive=True
        )()

        # Reset and start the hand!
        await self.reset_hand(game)

        # Get blind amounts
        big_blind = game.big_blind

        # Iterate over players and check chip status
        for player in players:
            if player.chips == 0:
                username = await sync_to_async(
                    lambda: player.user.username, thread_sensitive=True
                )()
                print(f"{username} has no chips left and will be removed from the game.")
                await self.handle_leave(game, username)  # Remove player from the game
            elif player.chips < big_blind:
                username = await sync_to_async(
                    lambda: player.user.username, thread_sensitive=True
                )()
                print(f"{username} does not have enough for blinds and will go all-in.")

        # Fetch active players again (updated)
        players = await sync_to_async(
            lambda: list(game.players.order_by("position")), thread_sensitive=True
        )()

        # If only 1 player remains, end the hand
        if len(players) == 1:
            print("*** Only 1 player left. Ending game and transferring chips.")
            await self.transfer_chips_to_profile(game, players[0])
            username = await sync_to_async(
                lambda: players[0].user.username, thread_sensitive=True
            )()
            await self.broadcast_private(game)
            await self.handle_leave(game, username)  # Remove player from the game
            return

        # Assign dealer
        await self.rotate_dealer(game)

        # Assign Small & Big Blinds
        await self.assign_blinds(game)

        # Create a deck (52 cards)
        deck = await sync_to_async(
            lambda: create_deck()
        )()

        # Shuffle and save the deck
        random.shuffle(deck)
        game.deck = deck

        # Deal Hole Cards
        await self.deal(game)

        # Update Game Status
        game.status = "active"

        # Save
        await sync_to_async(game.save)()

        # Broadcast
        await asyncio.gather(
            self.broadcast_messages("🚀 Starting a new hand."),
            self.broadcast_game_state(game),
        )

    async def reset_hand(self, game: Game) -> None:
        """
        Resets the hand state before a new hand begins.

        Clears the current turn, deck, community cards, and sets the current phase to preflop.
        Saves the updated game state.

        Args:
            game (Game): The current game instance.

        Returns:
            None
        """

        print("* RESET HAND")

        game.current_turn = None
        game.deck = []
        game.community_cards = []
        game.current_phase = "preflop"
        game.last_raise_delta = 0

        players = await sync_to_async(lambda: list(game.players.all()), thread_sensitive=True)()
        for player in players:
            player.current_bet = 0
            player.total_bet = 0
            player.has_folded = False
            player.is_all_in = False
            player.is_small_blind = False
            player.is_big_blind = False
            player.has_checked = False
            player.has_acted_this_round = False
            player.can_reraise_this_round = True
            await sync_to_async(player.save)()

        await sync_to_async(game.save)()

    async def rotate_dealer(self, game: Game) -> None:
        """
        Assigns the dealer position to the next player in order.

        If no dealer is set, assigns it to the first player. If a dealer is already set,
        rotates to the next player in circular order. Marks the new dealer and broadcasts the update.

        Args:
            game (Game): The current game instance.

        Returns:
            None
        """

        print("* ROTATE DEALER")

        # Get all players sorted by their 'position' field
        players = await sync_to_async(
            lambda: list(game.players.order_by("position")), thread_sensitive=True
        )()

        # Safety check
        if len(players) < 2:
            return

        # If we have never set a dealer before, default to the first seat
        if game.dealer_position is None:
            new_dealer_index = 0
        else:
            # Find the current dealer's position in the list
            current_dealer_index = next(
                (i for i, p in enumerate(players) if p.position == game.dealer_position), -1
            )

            # If we can't find them, default to seat 0
            if current_dealer_index == -1:
                new_dealer_index = 0
            else:
                # Move dealer to next seat in a circular fashion
                new_dealer_index = (current_dealer_index + 1) % len(players)

        new_dealer = players[new_dealer_index]

        # Reset the is_dealer flag for all players and assign to new dealer
        await sync_to_async(lambda: Player.objects.filter(game=game).update(is_dealer=False))()
        new_dealer.is_dealer = True
        await sync_to_async(new_dealer.save)()

        # Update game
        game.dealer_position = new_dealer.position
        await sync_to_async(game.save)()

    async def assign_blinds(self, game: Game) -> None:
        """
        Assigns small and big blinds to players.

        For heads-up, the dealer is the small blind. For 3+ players, assigns blinds clockwise.
        Deducts the blinds from each player's chips and updates their states.
        Also sets the player who acts first preflop and broadcasts the blind information.

        Args:
            game (Game): The game instance in progress.

        Returns:
            None
        """

        print("* ASSIGN BLINDS")

        # Fetch the sorted player list
        players = await sync_to_async(
            lambda: list(game.players.order_by("position")),
            thread_sensitive=True,
        )()

        if len(players) < 2:
            return  # Safety check

        # Find the dealer index in the players list
        dealer_index = next(
            (i for i, p in enumerate(players) if p.position == game.dealer_position),
            -1
        )
        if dealer_index == -1:
            return  # Safety check

        small_blind = game.small_blind
        big_blind = game.big_blind

        if len(players) == 2:
            # =======================
            # HEADS-UP (2 players)
            # =======================
            # In heads-up, the dealer is also the small blind.
            small_blind_player = players[dealer_index]
            big_blind_player = players[(dealer_index + 1) % 2]

            # after posting blinds, the small blind acts first preflop
            game.current_turn = small_blind_player.position

        else:
            # ===========================
            # 3+ PLAYERS (RING GAME)
            # ===========================
            sb_index = (dealer_index + 1) % len(players)
            bb_index = (dealer_index + 2) % len(players)
            small_blind_player = players[sb_index]
            big_blind_player = players[bb_index]

            # First to act preflop is dealer_index + 3
            first_to_act_index = (dealer_index + 3) % len(players)
            game.current_turn = players[first_to_act_index].position

        # Deduct small blind (go all-in if not enough chips)
        sb_amount = min(small_blind, small_blind_player.chips)
        small_blind_player.chips -= sb_amount
        small_blind_player.current_bet = sb_amount
        small_blind_player.total_bet += sb_amount
        small_blind_player.is_small_blind = True
        if small_blind_player.chips == 0:
            small_blind_player.is_all_in = True
        await sync_to_async(small_blind_player.save)()

        # Deduct big blind (go all-in if not enough chips)
        bb_amount = min(big_blind, big_blind_player.chips)
        big_blind_player.chips -= bb_amount
        big_blind_player.current_bet = bb_amount
        big_blind_player.total_bet += bb_amount
        big_blind_player.is_big_blind = True
        if big_blind_player.chips == 0:
            big_blind_player.is_all_in = True
        await sync_to_async(big_blind_player.save)()

        # Save
        await sync_to_async(game.save)()

    async def next_player(self, game: Game, start_position: int) -> int:
        """
        Determines and sets the next player to act based on current game state.
        Skips players who are folded or all-in. If no player needs to act, ends the betting round.

        Args:
            game (Game): The current game instance.
            start_position (int): The seat number of the last acting player.

        Returns:
            int: The seat number of the next player, or None if the betting round is complete.
        """
        print("* NEXT PLAYER")
        print("*** Provided start_position (seat):", start_position)

        # Fetch all players who have not folded
        all_active_players = await sync_to_async(
            lambda: list(game.players.filter(has_folded=False).order_by("position")),
            thread_sensitive=True
        )()

        # Split into those who can act (not all-in) and all for highest_bet calculation
        eligible_players = [p for p in all_active_players if not p.is_all_in]

        if not eligible_players:
            print("*** No eligible players found. Advancing to showdown.")
            while game.current_phase != "showdown":
                await self.goto_next_phase(game)
            await self.start_hand(game)
            return None

        # Compute highest bet among all (including all-ins) to fairly assess who needs to act
        highest_bet = max(p.current_bet for p in all_active_players)
        print("*** Highest bet among all active players:", highest_bet)

        # Build circular player order after start_position
        after = [p for p in eligible_players if p.position > start_position]
        before = [p for p in eligible_players if p.position <= start_position]
        circular_order = after + before

        print("*** Circular order of eligible players (by seat):", [p.position for p in circular_order])

        candidate = None
        for p in circular_order:
            if p.current_bet < highest_bet or not p.has_acted_this_round:
                candidate = p
                break

        if candidate is None:
            print("*** No player needs to act. Betting round is complete.")
            return None

        print("*** Next candidate seat:", candidate.position)
        game.current_turn = candidate.position
        await sync_to_async(game.save)()
        await self.broadcast_game_state(game)
        return candidate.position
