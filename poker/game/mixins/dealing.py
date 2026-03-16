from asgiref.sync import sync_to_async
from ..models import Game, Player


class DealingMixin:
    """Card dealing and chip transfer methods."""

    async def deal(self, game: Game) -> None:
        """
        Deals two hole cards to each player in proper order.

        Distributes one card at a time to each player, twice around the table,
        starting from the left of the dealer. Cards are stored in the database and
        broadcasted privately to each player.

        Args:
            game (Game): The current game instance.

        Returns:
            None
        """

        # Init
        dealt_cards = {}
        deck = game.deck

        # Fetch players in correct order
        players = await sync_to_async(
            lambda: list(game.players.order_by("position")), thread_sensitive=True
        )()

        # Safety check
        if not players:
            return

        # Determine starting position (first player after the dealer)
        dealer_position = game.dealer_position
        start_index = next(
            (i for i, p in enumerate(players) if p.position == dealer_position), -1
        )
        if start_index == -1:
            print("Dealer not found. Cannot proceed with dealing.")
            return

        # Deal cards in two rounds
        for _ in range(2):  # Two hole cards per player
            for i in range(len(players)):
                p = players[(start_index + i + 1) % len(players)]  # Next player after dealer
                card = deck.pop(0)
                username = await sync_to_async(
                    lambda: p.user.username, thread_sensitive=True
                )()
                if username not in dealt_cards:
                    dealt_cards[username] = []
                dealt_cards[username].append(card)

        # Save hole cards once after all cards are dealt
        for p in players:
            username = await sync_to_async(
                lambda: p.user.username, thread_sensitive=True
            )()
            if username in dealt_cards:
                await sync_to_async(p.set_hole_cards)(dealt_cards[username])

        game.deck = deck

        # Save
        await sync_to_async(game.save)()

        # Update Front-End
        await self.broadcast_private(game)

    async def transfer_chips_to_profile(self, game: Game, player: Player) -> None:
        """
        Transfers remaining in-game chips from a player to their profile.

        This function is typically used when a game ends and a player has won.
        It adds the player's remaining chips in the game to their profile's chip count,
        resets their in-game chip count to 0, saves both objects, and broadcasts a win message.

        Args:
            game (Game): The game instance.
            player (Player): The player whose chips are being transferred.

        Returns:
            None
        """

        # Fetch user profile
        user = await sync_to_async(lambda: player.user, thread_sensitive=True)()
        user_profile = await sync_to_async(
            lambda: user.profile, thread_sensitive=True
        )()

        # Transfer chips
        user_profile.chips += player.chips  # Add game chips to total chips

        username = await sync_to_async(
            lambda: player.user.username, thread_sensitive=True
        )()
        await self.broadcast_messages(
            f"🎉 {username} wins the game and receives {player.chips} chips!"
        )

        player.chips = 0  # Reset game chips

        # Save changes
        await sync_to_async(user_profile.save)()
        await sync_to_async(player.save)()
