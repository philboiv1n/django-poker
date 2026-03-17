"""
consumers.py
============

WebSocket consumer for real-time poker game updates.
Game logic is split into mixins under poker/game/mixins/.
"""

import json
import logging
from channels.generic.websocket import AsyncWebsocketConsumer
from asgiref.sync import sync_to_async
from .models import Game, Player
from .mixins import (
    BroadcastingMixin,
    ActionsMixin,
    GameStateMixin,
    PhasesMixin,
    DealingMixin,
)

logger = logging.getLogger(__name__)


class GameConsumer(
    BroadcastingMixin,
    ActionsMixin,
    GameStateMixin,
    PhasesMixin,
    DealingMixin,
    AsyncWebsocketConsumer,
):
    """
    WebSocket consumer to handle real-time updates for the poker games.
    Manages player connections, actions, and game state updates.
    """

    # =======================================================================
    # WEBSOCKET CONNECTION HANDLING
    # =======================================================================

    async def connect(self) -> None:
        """
        Handles a new WebSocket connection.

        - Rejects unauthenticated connections immediately.
        - Retrieves game and user information from the connection scope.
        - Adds the connection to both a public game room and a private user group.
        - Sends the player's private game state (e.g., hole cards).

        Returns:
            None
        """

        logger.debug("WebSocket connect")

        self.game_id = self.scope["url_route"]["kwargs"]["game_id"]
        self.room_group_name = f"game_{self.game_id}"
        self.user = self.scope["user"]

        # Reject unauthenticated connections before accepting
        if not self.user.is_authenticated:
            logger.warning("Unauthenticated WebSocket connection rejected")
            await self.close()
            return

        self.user_channel_name = f"user_{self.user.id}"

        # Join the public game WebSocket room and a **private WebSocket group**
        await self.channel_layer.group_add(
            self.room_group_name, self.channel_name
        )
        await self.channel_layer.group_add(
            self.user_channel_name, self.channel_name
        )
        await self.accept()

        # Retrieve game and send **private** updates only to this user
        game = await sync_to_async(Game.objects.get)(id=self.game_id)

        # Send private hole cards only to the reconnecting player, not broadcast
        await self.send_private_game_state(game, self.user)

    async def disconnect(self, close_code) -> None:
        """
        Handles the WebSocket disconnection.

        Removes the user's connection from both the public game group and their private user group.

        Args:
            close_code: The WebSocket close code.

        Returns:
            None
        """

        logger.debug("WebSocket disconnect (close_code=%s)", close_code)

        await self.channel_layer.group_discard(
            self.room_group_name, self.channel_name
        )
        await self.channel_layer.group_discard(
            self.user_channel_name, self.channel_name
        )

    # =======================================================================
    # WEBSOCKET MESSAGE HANDLING
    # =======================================================================

    async def receive(self, text_data: str) -> None:
        """
        Handles messages received from WebSocket clients.

        Parses the incoming JSON message to determine the action type (join, leave, fold, check, call, bet).
        Validates player existence and turn order before processing further actions.
        Dispatches the action to the appropriate handler function.

        Args:
            text_data (str): JSON-formatted message sent from the WebSocket client.

        Returns:
            None
        """

        logger.debug("WebSocket receive")

        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            await self.send(text_data=json.dumps({"error": "Invalid message format"}))
            return

        action = data.get("action")
        # Always derive the player identity from the authenticated session —
        # never trust a username supplied by the client.
        player_username = self.user.username

        # Coerce amount to int early so downstream handlers can trust the type.
        # Floats, strings, and missing values are all normalised here.
        try:
            amount = int(data.get("amount", 0))
        except (TypeError, ValueError):
            await self.send(text_data=json.dumps({"error": "Invalid bet amount."}))
            return

        try:
            game = await sync_to_async(Game.objects.get)(id=self.game_id)

            # Handle "join" first, since player may not exist in the game yet
            if action == "join":
                await self.handle_join(game, player_username)
                return

            # Fetch the player *after* handling "join"
            player = await sync_to_async(
                lambda: Player.objects.select_related("user").filter(
                    game=game, user__username=player_username
                ).first()
            )()

            # Check if player exists in this game
            if not player:
                await self.send(
                    text_data=json.dumps({"error": "You are not playing on this table"})
                )
                return

            # Enforce turn order for all game actions
            if action in ("fold", "check", "call", "bet"):
                if game.status != "active" or game.current_turn != player.position:
                    await self.send(
                        text_data=json.dumps({"error": "It's not your turn."})
                    )
                    return

            # Handle possible actions from player
            if action == "leave":
                await self.handle_leave(game, player_username)
            elif action == "fold":
                await self.handle_fold(game, player)
            elif action == "check":
                await self.handle_check(game, player)
            elif action == "call":
                await self.handle_call(game, player)
            elif action == "bet":
                await self.handle_bet(game, player, amount)

        except Game.DoesNotExist:
            logger.warning("Game %s not found. Ignoring action: %s", self.game_id, action)
