import json
from asgiref.sync import sync_to_async
from ..models import Game, Player, User
from ..utils import can_user_do_action
from ..redis_client import redis_client


class BroadcastingMixin:
    """WebSocket broadcasting methods for sending game state to players."""

    async def broadcast_messages(self, message: str) -> None:
        """
        Stores (in Redis) and broadcasts only the *newly added* message to all players.

        Keeps the last 10 messages in Redis, but clients only receive this most recent one.

        Args:
            message (str): The message to store and broadcast.

        Returns:
            None
        """

        # Store the message in Redis (pushing to the end of the list)
        redis_key = f"game_{self.game_id}_messages"
        redis_client.rpush(redis_key, json.dumps({"message": message}))
        # Trim to last 10
        redis_client.ltrim(redis_key, -10, -1)

        # Broadcast *only* the newly-added message
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "broadcast_messages_helper",
                "messages": [message],
            },
        )

    async def broadcast_messages_helper(self, event):
        """
        Sends action messages to the frontend.

        This is triggered by the `broadcast_messages` method to deliver the list of
        recent game messages to the client.

        Args:
            event (dict): Contains the list of messages to send.

        Returns:
            None
        """
        message_data = {
            "messages": event["messages"],
        }
        await self.send(text_data=json.dumps(message_data))

    async def broadcast_game_state(self, game: Game) -> None:
        """
        Sends the complete game state to all connected players.

        Constructs and sends a detailed game state payload including each player's status,
        current phase, pot size, community cards, and the player whose turn it is.

        Args:
            game (Game): The current game instance.

        Returns:
            None
        """

        # Fetch all players asynchronously
        players = await sync_to_async(
            lambda: list(game.players.all()), thread_sensitive=True
        )()

        # Find the current player in the list
        current_player = next(
            (p for p in players if p.position == game.current_turn),
            players[0] if players else None
        )

        current_username = (
            await sync_to_async(
                lambda: current_player.user.username, thread_sensitive=True
            )()
            if current_player
            else ""
        )

        # Get the current pot amount
        pot = await sync_to_async(lambda: game.get_pot(), thread_sensitive=True)()

        # Create a personal game state message for each player
        game_state_message = {
            "type": "update_game_state",
            "game_status": game.status,
            "current_phase": game.current_phase,
            "pot": pot,
            "dealer_position": game.dealer_position,
            "current_turn": game.current_turn,
            "current_username": current_username,
            "community_cards": game.community_cards,
            "players": [
                {
                    "username": await sync_to_async(
                        lambda: p.user.username, thread_sensitive=True
                    )(),
                    "avatar_color": await sync_to_async(
                        lambda: p.user.profile.avatar_color, thread_sensitive=True
                    )(),
                    "position": p.position,
                    "game_chips": p.chips,
                    "current_bet": p.current_bet,
                    "total_bet": p.total_bet,
                    "has_folded": p.has_folded,
                    "has_checked": p.has_checked,
                    "has_acted_this_round": p.has_acted_this_round,
                    "is_small_blind": p.is_small_blind,
                    "is_big_blind": p.is_big_blind,
                    "is_dealer": p.is_dealer,
                    "is_all_in": p.is_all_in,
                    "is_next_to_play": p.position == current_player.position,
                    "user_can_check": await sync_to_async(
                       lambda: can_user_do_action(game, p, "check"), thread_sensitive=True
                    )(),
                    "user_can_call": await sync_to_async(
                       lambda: can_user_do_action(game, p, "call"), thread_sensitive=True
                    )(),
                }
                for p in players
            ],
        }

        # Send this game state to all players
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "broadcast_send_helper",
                "data": game_state_message,
            },
        )

    async def broadcast_send_helper(self, event):
        """
        Trigger that handles sending data.
        """
        await self.send(text_data=json.dumps(event["data"]))

    async def broadcast_private(self, game: Game) -> None:
        """
        Sends private game state updates (like hole cards) to each player.

        Sends user-specific information (e.g., hole cards and total chips)
        via their private WebSocket channel.

        Args:
            game (Game): The current game instance.

        Returns:
            None
        """

        # Fetch all players asynchronously
        players = await sync_to_async(
            lambda: list(game.players.all()), thread_sensitive=True
        )()

        for player in players:
            id = await sync_to_async(lambda: player.user.id, thread_sensitive=True)()
            hole_cards = await sync_to_async(
                lambda: player.hole_cards, thread_sensitive=True
            )()
            total_user_chips = await sync_to_async(
                lambda: player.user.profile.chips, thread_sensitive=True
            )()

            # Create a personal game state message for each player
            private_data = {
                "type": "update_private",
                "hole_cards": hole_cards,
                "total_user_chips": total_user_chips,
            }

            # Send this game state **privately** to the respective player
            await self.channel_layer.group_send(
                f"user_{id}",
                {
                    "type": "broadcast_send_helper",
                    "data": private_data,
                },
            )

    async def send_private_game_state(self, game: Game, user: User) -> None:
        """
        Sends private game state to a player.

        Args:
            game (Game): The current game instance.
            user (User): The user.

        Returns:
            None
        """

        player = await sync_to_async(lambda: game.players.filter(user=user).first())()
        if not player:
            return  # Safety check

        hole_cards = player.hole_cards
        total_user_chips = await sync_to_async(
            lambda: player.user.profile.chips, thread_sensitive=True
        )()

        private_message = {
            "type": "private_game_state",
            "hole_cards": hole_cards,
            "total_user_chips": total_user_chips,
        }

        # Send private message only to this user's private channel
        await self.channel_layer.group_send(
            self.user_channel_name,  # Only to the current user
            {
                "type": "broadcast_send_helper",
                "data": private_message,
            },
        )

    async def send_private_to_user(self, user: User) -> None:
        """
        Sends private user status to a user.

        Args:
            user (User): The user.

        Returns:
            None
        """

        total_user_chips = await sync_to_async(
            lambda: user.profile.chips, thread_sensitive=True
        )()

        private_message = {
            "type": "private_game_state",
            "total_user_chips": total_user_chips,
        }

        # Send private message only to this user's private channel
        await self.channel_layer.group_send(
            self.user_channel_name,  # Only to the current user
            {
                "type": "broadcast_send_helper",
                "data": private_message,
            },
        )
