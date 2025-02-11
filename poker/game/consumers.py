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
    """

    async def connect(self):
        """
        Connects the user to the WebSocket room for a specific game.
        Retrieves past messages and the current username from Redis.
        """
        self.game_id = self.scope["url_route"]["kwargs"]["game_id"]
        self.room_group_name = f"game_{self.game_id}"

        # Join the WebSocket room
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

        # Get the game status
        game = await sync_to_async(Game.objects.get)(id=self.game_id)

        # Broadcast updated game state
        await self.broadcast_game_state(game)

        # Retrieve & clean past messages from Redis
        redis_messages_key = f"game_{self.game_id}_messages"
        stored_messages = redis_client.lrange(
            redis_messages_key, -10, -1
        )  # Get last 10 messages

        clean_messages = []
        for msg in stored_messages:
            try:
                # Check if the message is already a valid JSON string
                if msg.startswith("{") and msg.endswith("}"):
                    json_msg = json.loads(msg)
                    if isinstance(json_msg, dict) and "message" in json_msg:
                        clean_messages.append(json_msg["message"])
                else:
                    # If it's a plain string (not JSON), store it as-is
                    clean_messages.append(msg)
            except (json.JSONDecodeError, AttributeError) as e:
                print(
                    f"❌ Skipping invalid JSON message from Redis: {msg}, Error: {e}"
                )  # Debugging
                continue  # Skip invalid entries

        # Send cleaned messages to the client
        await self.send(text_data=json.dumps({"messages": clean_messages}))

    async def receive(self, text_data):
        """
        Handles messages received from WebSocket clients.
        """
        data = json.loads(text_data)
        action = data.get("action")
        player_username = data.get("player")

        try:
            # Ensure game exists before using it
            game = await sync_to_async(Game.objects.get)(id=self.game_id)

            if action == "join":
                await self.handle_join(game, player_username)

            elif action == "leave":
                await self.handle_leave(game, player_username)

            elif action in ["check", "fold"]:
                player = await sync_to_async(Player.objects.get)(
                    game=game, user__username=player_username
                )

                if player and await sync_to_async(player.is_turn)():

                    action_message = await sync_to_async(self.process_action)(
                        game, player, action
                    )

                    await self.broadcast_messages(action_message)
                    await self.broadcast_game_state(game)

        except Game.DoesNotExist:
            print(f"❌ Game {self.game_id} not found. Ignoring action: {action}")

    def process_action(self, game, player, action):
        """Processes the player action (check or fold)."""

        username = player.user.username

        if action == "check":
            action_message = f"✅ {username} checked."
        elif action == "fold":
            action_message = f"🚫 {username} folded."

        # Move turn to the next player
        game.current_turn = game.get_next_turn_after(player.position)
        game.save()

        return action_message

    async def disconnect(self, close_code):
        """Disconnects the user from the WebSocket room."""
        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    async def get_game(self):
        """Retrieves the game object."""
        return await sync_to_async(Game.objects.get)(id=self.game_id)

    async def get_player(self, game, username):
        """Retrieves the player object."""
        return await sync_to_async(Player.objects.get)(
            game=game, user__username=username
        )

    async def get_current_player(self, game):
        """Retrieves the player whose turn it is."""
        return await sync_to_async(Player.objects.get)(
            game=game, position=game.current_turn
        )

    async def handle_join(self, game, player_username):
        """
        Handles player joining the table via WebSocket.
        """
        user = await sync_to_async(User.objects.get)(username=player_username)

        # Ensure player is not already in the game
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
        game_status = await sync_to_async(lambda: game.status)()

        # Start the game if at least 2 players and not waiting
        if player_count > 1 and game_status == "waiting":
            print(f"🚀 Starting game since {player_count} players have joined.")
            await self.start_game(game)

        # Notify all players
        join_message = f" 🪑 {player_username} has join the table."
        await self.broadcast_messages(join_message)
        await self.broadcast_game_state(game)

    async def handle_leave(self, game, player_username):
        """
        Handles player leaving the table via WebSocket.
        """

        # Get the player
        player = await sync_to_async(
            Player.objects.filter(game=game, user__username=player_username).first
        )()

        # Check if player exist
        if not player:
            print(f"❌ Player {player_username} not found in game {game.id}.")
            return

        # Remove player from the game
        await sync_to_async(player.delete)()

        # Check how many players are left
        remaining_players = list(
            await sync_to_async(lambda: list(game.players.order_by("position")))()
        )

        # Stop the game if < 2 players
        if len(remaining_players) < 2:
            game.status = "waiting"
            game.dealer_position = None
            game.current_turn = None
        else:
            # Reassign dealer if the leaving player was the dealer
            if game.dealer_position == player.position:
                new_dealer = remaining_players[0]  # Get the next lowest position player
                game.dealer_position = new_dealer.position

            # Update current_turn to the next player if needed
            if game.current_turn == player.position:
                game.current_turn = self.get_next_turn_after(game, game.dealer_position)

        # Save game state
        await sync_to_async(game.save)()

        # Notify all players
        leave_message = f"⚠️ {player_username} has left the table."
        await self.broadcast_messages(leave_message)
        await self.broadcast_game_state(game)

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
                return players[(i + 1) % len(players)].position  # Loops back if needed

        return None  # This should never happen

    async def start_game(self, game):
        """
        Starts the game when at least 2 players have joined.
        Selects a dealer and assigns the first turn.
        """
        print(f"🚀 Starting game for {game.id}")

        # Fetch players asynchronously
        players = await sync_to_async(
            lambda: list(game.players.order_by("position")), thread_sensitive=True
        )()

        if len(players) < 2:
            print("Not enough players to start the game. Waiting...")
            game.status = "waiting"
            game.current_turn = None
            game.dealer_position = None
            await sync_to_async(game.save)()
            return

        # Update game status
        game.status = "active"

        # Choose the dealer (first player in the sorted list)
        dealer = players[0]
        game.dealer_position = dealer.position

        # Determine the next player (first player after dealer)
        game.current_turn = await self.get_next_turn_after(game, dealer.position)

        # Save the updated game state
        await sync_to_async(game.save)()

        # Broadcast updated game state
        await self.broadcast_game_state(game)

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

    ###################################################################
    #
    #  Broadcasting
    #
    #
    ###################################################################

    async def broadcast_messages(self, message):

        # Store current turn's username in Redis
        # redis_client.set(
        #     f"game_{self.game_id}_current_username", current_username
        # )

        # Store action messages in Redis (limit last 10 messages)
        redis_key = f"game_{self.game_id}_messages"
        redis_client.rpush(
            redis_key,
            json.dumps(
                {
                    "message": message,
                    #  "current_username": current_username,
                }
            ),
        )
        redis_client.ltrim(redis_key, -10, -1)

        # Retrieve all messages from Redis
        stored_messages = redis_client.lrange(redis_key, -10, -1)
        clean_messages = [json.loads(msg).get("message", "") for msg in stored_messages]

        # Broadcast message to all players
        print(f"📢 Broadcasting action to WebSocket clients...")
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "send_action_message",
                "messages": clean_messages,
            },
        )

    async def send_action_message(self, event):
        """
        Sends action messages to the frontend, ensuring `current_turn` is present.
        """
        message_data = {
            "messages": event["messages"],
        }
        await self.send(text_data=json.dumps(message_data))

    async def broadcast_game_state(self, game):
        """Sends updated game state to all connected players"""

        # Fetch all players asynchronously
        players = await sync_to_async(
            lambda: list(game.players.all()), thread_sensitive=True
        )()

        # Retrieve current player
        current_player = await sync_to_async(
            lambda: game.players.filter(position=game.current_turn).first(),
            thread_sensitive=True,
        )()
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

    async def send_game_state(self, event):
        """Sends updated dealer and turn info to frontend"""
        await self.send(text_data=json.dumps(event))
