import json
import redis
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from asgiref.sync import sync_to_async
from random import choice

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

        # ✅ Retrieve `current_username` safely
        redis_username_key = f"game_{self.game_id}_current_username"
        stored_username = redis_client.get(redis_username_key)
        current_username = stored_username if stored_username else ""

        # Send it to the frontend when the user connects
        await self.send(text_data=json.dumps({"current_username": current_username}))

        # ✅ Send current player list
        await self.send_player_list()

        # ✅ Retrieve & clean past messages from Redis
        redis_messages_key = f"game_{self.game_id}_messages"
        stored_messages = redis_client.lrange(
            redis_messages_key, -10, -1
        )  # Get last 10 messages

        clean_messages = []
        for msg in stored_messages:
            try:
                # ✅ Check if the message is already a valid JSON string
                if msg.startswith("{") and msg.endswith("}"):
                    json_msg = json.loads(msg)
                    if isinstance(json_msg, dict) and "message" in json_msg:
                        clean_messages.append(json_msg["message"])
                else:
                    # ✅ If it's a plain string (not JSON), store it as-is
                    clean_messages.append(msg)
            except (json.JSONDecodeError, AttributeError) as e:
                print(
                    f"❌ Skipping invalid JSON message from Redis: {msg}, Error: {e}"
                )  # Debugging
                continue  # Skip invalid entries

        # ✅ Send cleaned messages to the client
        await self.send(text_data=json.dumps({"messages": clean_messages}))






    async def receive(self, text_data):
        """
        Handles messages received from WebSocket clients.
        """
        data = json.loads(text_data)
        action = data.get("action")
        player_username = data.get("player")

        print(f"🟢 WebSocket Received Action: {action} from {player_username}")  # ✅ Debugging

        try:
            # Ensure game exists before using it
            game = await sync_to_async(Game.objects.get)(id=self.game_id)

            if action == "join":
                await self.handle_join(game, player_username)
                await self.send_player_list() 

            elif action == "leave":
                await self.handle_leave(game, player_username)
                await self.send_player_list() 

            elif action in ["check", "fold"]:

               # game = await sync_to_async(Game.objects.get)(id=self.game_id)
                player = await sync_to_async(Player.objects.get)(
                    game=game, user__username=player_username
                )

            # 🔍 Debugging: Print the game state
                print(f"🔍 Game State: Current Turn - {game.current_turn}")
                username = await sync_to_async(lambda: player.user.username)()
                position = await sync_to_async(lambda: player.position)()
                print(f"🔍 Player: {username}, Position: {position}")

                # 🔍 Debugging: Print what `player.is_turn()` returns
                is_turn = await sync_to_async(player.is_turn)()
                print(f"🧐 is_turn() Check: {is_turn}")

                if player and await sync_to_async(player.is_turn)():

                    action_message = await sync_to_async(self.process_action)(
                        game, player, action
                    )
                    current_player = await self.get_current_player(game)
                    current_username = (
                        await sync_to_async(lambda: current_player.user.username)()
                        if current_player
                        else "Unknown"
                    )

                    print(f"📝 Storing action in Redis: {action_message}")  # ✅ Debugging Redis Storage

                    # Store current turn's username in Redis
                    redis_client.set(
                        f"game_{self.game_id}_current_username", current_username
                    )

                    # Store action messages in Redis (limit last 10 messages)
                    redis_key = f"game_{self.game_id}_messages"
                    redis_client.rpush(
                        redis_key,
                        json.dumps(
                            {
                                "message": action_message,
                                "current_username": current_username,
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
                            "current_turn": game.current_turn,
                            "current_username": current_username,
                        },
                    )
        except Game.DoesNotExist:
            print(f"❌ Game {self.game_id} not found. Ignoring action: {action}")





    async def disconnect(self, close_code):
        """
        Disconnects the user from the WebSocket room.
        """
        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)




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




    async def send_action_message(self, event):
        """
        Sends action messages to the frontend, ensuring `current_turn` is present.
        """
        message_data = {
            "messages": event["messages"],
            "current_username": event.get("current_username", ""),
        }

        # Only add `current_turn` if it exists in the event payload
        if "current_turn" in event:
            message_data["current_turn"] = event["current_turn"]

        await self.send(text_data=json.dumps(message_data))






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






    async def send_player_list(self):
        """Sends the updated list of players to all connected users."""
        try:
            game = await sync_to_async(Game.objects.get)(id=self.game_id)
        except Game.DoesNotExist:
            return

       #  players = await sync_to_async(lambda: list(game.players.order_by("position")))()
        players = await sync_to_async(lambda: list(game.players.values("user__username", "position")))()

        player_list = [{"username": p["user__username"], "position": p["position"]} for p in players]

        # Reset current_username if game is waiting
        current_username = "" if game.status == "waiting" else redis_client.get(f"game_{self.game_id}_current_username") or ""


        # Broadcast the player list to all players
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "update_players",
                "players": player_list,
                "player_count": len(players),
                "max_players": game.max_players,  
                "current_username": current_username,
            },
        )





    async def update_players(self, event):
        """Sends the player list update to all clients."""

        players = event.get("players", [])

        await self.send(text_data=json.dumps({"players": players}))







    async def handle_join(self, game, player_username):
        """
        Handles player joining the table via WebSocket.
        """
        user = await sync_to_async(User.objects.get)(username=player_username)

        # Ensure player is not already in the game
        existing_player = await sync_to_async(lambda: game.players.filter(user=user).exists())()
        if existing_player:
            return  # Player is already at the table

        # Find the lowest available position
        taken_positions = await sync_to_async(lambda: list(game.players.values_list("position", flat=True)))()
        available_positions = [pos for pos in range(game.max_players) if pos not in taken_positions]

        if not available_positions:
            return  # No available positions

        # Create new Player
        new_player = await sync_to_async(Player.objects.create)(
            game=game, user=user, position=min(available_positions)
        )

        print(f"✅ {player_username} joined at position {new_player.position}")


         # Fetch the player count asynchronously
        player_count = await sync_to_async(lambda: game.players.count())()
        game_status = await sync_to_async(lambda: game.status)()

        # Start the game if at least 2 players
        # if game.players.count() >= 2 and game.status == "waiting":
        #     game.status = "active"
        #     game.dealer_position = min(taken_positions)  # Smallest position is dealer
        #     game.current_turn = game.get_next_turn_after(game.dealer_position)
        #     await sync_to_async(game.save)()

        if player_count >= 2 and game_status == "waiting":
            print(f"🚀 Starting game since {player_count} players have joined.")
        
            # Start the game asynchronously
            await self.start_game(game)


        # Broadcast updated player list
        # await self.send_player_list()


        # Notify all players
        join_message = f" ⭐️ {player_username} has join the table."
        await self.store_and_display_messages(game,player_username,join_message)

    




    async def handle_leave(self, game, player_username):
        """
        Handles player leaving the table via WebSocket.
        """

        print(f"🚪 {player_username} is leaving table...")

        # Get the player
        player = await sync_to_async(Player.objects.filter(game=game, user__username=player_username).first)()
       
        # Check if player exist
        if not player:
            print(f"❌ Player {player_username} not found in game {game.id}.")
            return
    
        # Remove player from the game
        await sync_to_async(player.delete)()

        # Check how many players are left
        remaining_players = await sync_to_async(game.players.count)()
        
        if remaining_players <= 1:
            print("❌ Not enough players left. Resetting game.")

            # Reset the game state
            game.status = "waiting"
            game.dealer_position = None
            game.current_turn = 0 
            await sync_to_async(game.save)()

            # Reset `current_username` in Redis
            redis_key = f"game_{self.game_id}_current_username"
            redis_client.set(redis_key, "")

            # redis_client.set(f"game_{self.game_id}_current_username", "allo")

        else:
            print(f"🔄 {remaining_players} players still in game.")
            # Broadcast updated player list
            await self.send_player_list()

        # Notify all players
        leave_message = f"🚪 {player_username} has left the table."
        await self.store_and_display_messages(game,"",leave_message)

       





    async def start_game(self, game):
        """
        Starts the game when at least 2 players have joined.
        Selects a dealer and assigns the first turn.
        """
        print(f"🚀 Starting game for {game.id}")

        # Fetch players asynchronously
        players = await sync_to_async(lambda: list(game.players.order_by("position")))()

        if len(players) >= 2 and game.status == "waiting":
            # Update game status
            game.status = "active"

            # Choose the dealer (first player in the sorted list)
            dealer = players[0]
            game.dealer_position = dealer.position

            # Determine the next player (first player after dealer)
            next_player = players[1] if len(players) > 1 else None
            game.current_turn = next_player.position if next_player else None

            # Save the updated game state
            await sync_to_async(game.save)()

             # Broadcast game start
            next_player = await sync_to_async(lambda: next_player.user.username)()
            start_message = f"🎲 {await sync_to_async(lambda: dealer.user.username)()} is the dealer. {next_player} starts."
            
            await self.store_and_display_messages(game,next_player,start_message)








    async def store_and_display_messages(self, game, current_username, message):

        # Store current turn's username in Redis
        redis_client.set(
            f"game_{self.game_id}_current_username", current_username
        )

        # Store action messages in Redis (limit last 10 messages)
        redis_key = f"game_{self.game_id}_messages"
        redis_client.rpush(
            redis_key,
            json.dumps(
                {
                    "message": message,
                    "current_username": current_username,
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
                "current_turn": game.current_turn,
                "current_username": current_username,
            },
        )