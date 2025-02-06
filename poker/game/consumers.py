import json
import redis
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from asgiref.sync import sync_to_async

from .models import Game, Player

# Connect to Redis
redis_client = redis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=0, decode_responses=True)


class GameConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer to handle real-time updates for poker games.
    """

    async def connect(self):
        """
        Connects the user to the WebSocket room for a specific game.
        """
        self.game_id = self.scope["url_route"]["kwargs"]["game_id"]
        self.room_group_name = f"game_{self.game_id}"

        # Join the WebSocket room
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

        # Send current player list when a user connects
        await self.send_player_list()

        # Send past messages from Redis on connection
        redis_key = f"game_{self.game_id}_messages"
        stored_messages = redis_client.lrange(redis_key, -10, -1)

        for message in stored_messages:
            await self.send(text_data=json.dumps({"message": message}))


    async def disconnect(self, close_code):
        """
        Disconnects the user from the WebSocket room.
        """
        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)


    async def receive(self, text_data):
        """
        Handles messages received from WebSocket clients.
        """
        data = json.loads(text_data)
        action = data.get("action")
        player_username = data.get("player")

        if action in ["join", "leave"]:
            await self.send_player_list() 

        if action in ["check", "fold"]:
            game = await sync_to_async(Game.objects.get)(id=self.game_id)
            player = await sync_to_async(Player.objects.get)(game=game, user__username=player_username)

            if player and await sync_to_async(player.is_turn)():

                action_message = await sync_to_async(self.process_action)(game, player, action)
                current_player = await self.get_current_player(game)
                current_username = await sync_to_async(lambda: current_player.user.username)() if current_player else "Unknown"

                # Store message in Redis (List)
                redis_key = f"game_{self.game_id}_messages"
                redis_client.rpush(redis_key, action_message)

                # Limit storage to last 10 messages
                redis_client.ltrim(redis_key, -10, -1)

                # Broadcast message to all players
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        "type": "send_action_message",
                        "message": action_message,
                        "current_turn": game.current_turn,
                        "current_username": current_username,
                    },
                )

    
    def process_action(self, game, player, action):
        """ Processes the player action (check or fold). """

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
        """ Sends the action message and turn update to all players. """
        await self.send(text_data=json.dumps({
            "message": event["message"],
            "current_turn": event["current_turn"],
            "current_username": event["current_username"]
        }))


    async def get_game(self):
        """ Retrieves the game object. """
        return await sync_to_async(Game.objects.get)(id=self.game_id)


    async def get_player(self, game, username):
        """ Retrieves the player object. """
        return await sync_to_async(Player.objects.get)(game=game, user__username=username)


    async def get_current_player(self, game):
        """ Retrieves the player whose turn it is. """
        return await sync_to_async(Player.objects.get)(game=game, position=game.current_turn)
    

    async def send_player_list(self):
        """ Sends the updated list of players to all connected users. """
        try:
            game = await sync_to_async(Game.objects.get)(id=self.game_id)
        except Game.DoesNotExist:
            return

        players = await sync_to_async(lambda: list(game.players.order_by("position")))()

        # Format the player list for broadcasting
        player_data = await sync_to_async(lambda: [
            {"username": player.user.username, "position": player.position}
            for player in players
        ])()
        
        # Broadcast the player list to all players
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "update_players",
                "players": player_data,
            },
        )

    async def update_players(self, event):
        """ Sends the player list update to all clients. """

        players = event.get("players", [])

        await self.send(text_data=json.dumps({
            "players": players
        }))
    