import logging
from asgiref.sync import sync_to_async
from ..models import Game, Player

logger = logging.getLogger(__name__)


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

        # Fetch players in correct order with user pre-loaded
        players = await sync_to_async(
            lambda: list(game.players.select_related("user").order_by("position")),
            thread_sensitive=True,
        )()

        # Safety check
        if not players:
            return

        # Build username map once from in-memory data (no per-card DB hits)
        player_usernames = {p.id: p.user.username for p in players}

        # Determine starting position (first player after the dealer)
        dealer_position = game.dealer_position
        start_index = next(
            (i for i, p in enumerate(players) if p.position == dealer_position), -1
        )
        if start_index == -1:
            logger.warning("deal: dealer position %s not found in players", dealer_position)
            return

        # Deal cards in two rounds
        for _ in range(2):  # Two hole cards per player
            for i in range(len(players)):
                p = players[(start_index + i + 1) % len(players)]  # Next player after dealer
                card = deck.pop(0)
                username = player_usernames[p.id]
                if username not in dealt_cards:
                    dealt_cards[username] = []
                dealt_cards[username].append(card)

        # Save hole cards once after all cards are dealt
        for p in players:
            username = player_usernames[p.id]
            if username in dealt_cards:
                await sync_to_async(p.set_hole_cards)(dealt_cards[username])

        game.deck = deck

        # Save only the deck field — game.save() without update_fields would
        # overwrite every column with in-memory values, potentially clobbering
        # concurrent writes (e.g. blind timer updates to small_blind/big_blind).
        await sync_to_async(lambda: game.save(update_fields=["deck"]))()

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

        # Fetch user and profile in a single query
        player_full = await sync_to_async(
            lambda: Player.objects.select_related("user__profile").get(id=player.id),
            thread_sensitive=True,
        )()
        user_profile = player_full.user.profile
        username = player_full.user.username

        # Transfer chips
        user_profile.chips += player.chips  # Add game chips to total chips
        await self.broadcast_messages(
            f"🎉 {username} wins the game and receives {player.chips} chips!"
        )

        player.chips = 0  # Reset game chips

        # Save changes
        await sync_to_async(user_profile.save)()
        await sync_to_async(player.save)()
