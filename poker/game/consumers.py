import json
import redis
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from asgiref.sync import sync_to_async
from .models import Game, Player, User

# Connect to Redis
redis_client = redis.Redis(
    host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=0, decode_responses=True
)


class GameConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer to handle real-time updates for poker games.
    Manages player connections, actions, and game state updates.
    """

    # =======================================================================
    # WEBSOCKET CONNECTION HANDLING
    # =======================================================================

    async def connect(self):
        """
        Handles WebSocket connection.

        - Joins the WebSocket room.
        - Sends past messages from Redis.
        - Sends the current game state to the client.
        """

        self.game_id = self.scope["url_route"]["kwargs"]["game_id"]
        self.room_group_name = f"game_{self.game_id}"

        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

        # Retrieve game and broadcast state
        game = await sync_to_async(Game.objects.get)(id=self.game_id)
        await self.broadcast_game_state(game)

        # Retrieve & clean past messages from Redis
        redis_messages_key = f"game_{self.game_id}_messages"
        stored_messages = redis_client.lrange(
            redis_messages_key, -10, -1
        )  # Get last 10 messages

        clean_messages = []
        for msg in stored_messages:
            try:
                json_msg = json.loads(msg)
                if isinstance(json_msg, dict) and "message" in json_msg:
                    clean_messages.append(json_msg["message"])
            except json.JSONDecodeError:
                continue  # Skip invalid messages

        # Send cleaned messages to the client
        await self.send(text_data=json.dumps({"messages": clean_messages}))

    # -----------------------------------------------------------------------
    async def disconnect(self, close_code):
        """Disconnects the user from the WebSocket room."""
        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    # =======================================================================
    # WEBSOCKET MESSAGE HANDLING
    # =======================================================================

    async def receive(self, text_data: str):
        """
        Handles messages received from WebSocket clients.
        """
        data = json.loads(text_data)
        action = data.get("action")
        player_username = data.get("player")

        try:
            game = await sync_to_async(Game.objects.get)(id=self.game_id)

            if action == "join":
                await self.handle_join(game, player_username)

            elif action == "leave":
                await self.handle_leave(game, player_username)

            elif action in ["check", "fold"]:
                await self.handle_action(game, player_username, action)

        except Game.DoesNotExist:
            print(f" Game {self.game_id} not found. Ignoring action: {action}")

    # =======================================================================
    # WEBSOCKET ACTION HANDLING
    # =======================================================================

    async def handle_action(self, game, player_username: str, action: str):
        """
        Handles player actions such as 'check' and 'fold'.

        Args:
            game (Game): The current game instance.
            player_username (str): The username of the player performing the action.
            action (str): The action being performed ('check' or 'fold').
        """
        player = await sync_to_async(Player.objects.get)(
            game=game, user__username=player_username
        )

        if player and await sync_to_async(player.is_turn)():
            action_message = await sync_to_async(self.process_action)(
                game, player, action
            )
            await self.broadcast_messages(action_message)
            await self.broadcast_game_state(game)

    # -----------------------------------------------------------------------
    def process_action(self, game, player, action: str) -> str:
        """
        Processes player action and advances the game.

        Args:
            game (Game): The game instance.
            player (Player): The player performing the action.
            action (str): The action ('check' or 'fold').

        Returns:
            str: The action message to broadcast.
        """

        username = player.user.username

        action_message = (
            f"✅ {username} checked." if action == "check" else f"🚫 {username} folded."
        )

        game.current_turn = game.get_next_turn_after(player.position)
        game.save()

        return action_message

    # -----------------------------------------------------------------------
    async def handle_join(self, game, player_username: str):
        """
        Handles a player joining the game.

        Args:
            game (Game): The game instance.
            player_username (str): The username of the player joining.
        """
        user = await sync_to_async(User.objects.get)(username=player_username)
        existing_player = await sync_to_async(
            lambda: game.players.filter(user=user).exists()
        )()

        if existing_player:
            return  # Player is already at the table

        # Find the lowest available position
        taken_positions = await sync_to_async(
            lambda: list(game.players.values_list("position", flat=True))
        )()
        available_positions = [
            pos for pos in range(game.max_players) if pos not in taken_positions
        ]

        if not available_positions:
            return  # No available positions

        # Create new Player
        new_player = await sync_to_async(Player.objects.create)(
            game=game, user=user, position=min(available_positions)
        )

        # Fetch the player count asynchronously
        player_count = await sync_to_async(lambda: game.players.count())()
        # game_status = await sync_to_async(lambda: game.status)()

        # Start the game if at least 2 players and not waiting
        if player_count > 1 and game.status == "waiting":
            # print(f"🚀 Starting game since {player_count} players have joined.")
            await self.start_game(game)

        # Notify all players
        join_message = f" 🪑 {player_username} has join the table."
        await self.broadcast_messages(join_message)
        await self.broadcast_game_state(game)

    # -----------------------------------------------------------------------
    async def handle_leave(self, game, player_username: str):
        """
        Handles player leaving the table via WebSocket.
        """

        # Get the player
        player = await sync_to_async(
            lambda: game.players.filter(user__username=player_username).first()
        )()
        if not player:
            return

        # Remove player from the game
        await sync_to_async(player.delete)()

        remaining_players = await sync_to_async(
            lambda: list(game.players.order_by("position"))
        )()
        if len(remaining_players) < 2:
            game.status = "waiting"
            game.dealer_position = None
            game.current_turn = None
        else:
            if game.dealer_position == player.position:
                game.dealer_position = remaining_players[0].position
            if game.current_turn == player.position:
                game.current_turn = await self.get_next_turn_after(
                    game, game.dealer_position
                )

        await sync_to_async(game.save)()

        # Notify all players
        leave_message = f"⚠️ {player_username} has left the table."
        await self.broadcast_messages(leave_message)
        await self.broadcast_game_state(game)

    # =======================================================================
    # WEBSOCKET GAME STATE HANDLING
    # =======================================================================

    async def start_game(self, game):
        """
        Starts the game when at least 2 players have joined.
        Selects a dealer and assigns the first turn.
        """

        players = await sync_to_async(
            lambda: list(game.players.order_by("position")), thread_sensitive=True
        )()

        if len(players) < 2:
            game.status = "waiting"
            game.current_turn = None
            game.dealer_position = None
            await sync_to_async(game.save)()
            return

        game.status = "active"
        game.dealer_position = players[0].position
        game.current_turn = await self.get_next_turn_after(game, game.dealer_position)
        await sync_to_async(game.save)()
        await self.broadcast_game_state(game)

    # -----------------------------------------------------------------------
    async def get_next_turn_after(self, game, position):
        """
        Gets the next player's position after the given position.
        Loops around if at the last position.
        """
        players = await sync_to_async(
            lambda: list(game.players.order_by("position")), thread_sensitive=True
        )()

        for i, player in enumerate(players):
            if player.position == position:
                return players[(i + 1) % len(players)].position

        return None

    # -----------------------------------------------------------------------
    # To use later...
    async def end_round(self, game):
        """
        Ends the current round and moves the dealer to the next player.
        """
        players = await sync_to_async(list)(game.players.order_by("position"))

        if not players:
            print("❌ No players left.")
            return

        # Move dealer to the next player
        game.dealer_position = self.get_next_turn_after(game, game.dealer_position)

        # New round starts, next turn goes to the player right after the new dealer
        game.current_turn = self.get_next_turn_after(game, game.dealer_position)

        await sync_to_async(game.save)()

        # Broadcast updated game state
        await self.broadcast_game_state(game)

    # =======================================================================
    # WEBSOCKET BROADCASTING TO PLAYERS
    # =======================================================================

    async def broadcast_messages(self, message: str):
        """
        Stores and broadcasts game messages.
        """

        # Store action messages in Redis (limit last 10 messages)
        redis_key = f"game_{self.game_id}_messages"
        redis_client.rpush(redis_key, json.dumps({"message": message}))
        redis_client.ltrim(redis_key, -10, -1)
        stored_messages = redis_client.lrange(redis_key, -10, -1)
        clean_messages = [json.loads(msg).get("message", "") for msg in stored_messages]

        # Broadcast message to all players
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "send_action_message",
                "messages": clean_messages,
            },
        )

    # -----------------------------------------------------------------------
    async def send_action_message(self, event):
        """
        Sends action messages to the frontend
        """
        message_data = {
            "messages": event["messages"],
        }
        await self.send(text_data=json.dumps(message_data))

    # -----------------------------------------------------------------------
    async def broadcast_game_state(self, game):
        """Sends updated game state to all connected players"""

        # Fetch all players asynchronously
        players = await sync_to_async(
            lambda: list(game.players.all()), thread_sensitive=True
        )()

        # Find the current player in the list
        current_player = next(
            (p for p in players if p.position == game.current_turn), None
        )
        current_username = (
            await sync_to_async(
                lambda: current_player.user.username, thread_sensitive=True
            )()
            if current_player
            else ""
        )

        # Construct the game state message
        game_state_message = {
            "type": "update_game_state",
            "game_status": game.status,
            "dealer_position": game.dealer_position,
            "current_turn": game.current_turn,
            "current_username": current_username,
            "players": [
                {
                    "username": await sync_to_async(
                        lambda: player.user.username, thread_sensitive=True
                    )(),
                    "position": player.position,
                }
                for player in players
            ],
        }

        # Broadcast to all players
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "send_game_state",
                "data": game_state_message,
            },
        )

    # -----------------------------------------------------------------------
    async def send_game_state(self, event):
        """Sends game state updates to frontend"""
        await self.send(text_data=json.dumps(event))
