import json, redis
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
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

        if action == "update_turn":
            game = await self.get_game()
            if game:
                current_player = await self.get_current_player(game)
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        "type": "send_turn_update",
                        "current_turn": game.current_turn,
                        "current_username": current_player.user.username if current_player else "Unknown",
                    },
                )


    async def send_turn_update(self, event):
        """
        Sends the updated turn to all connected clients.
        """
        await self.send(text_data=json.dumps({
            "current_turn": event["current_turn"],
            "current_username": event["current_username"],
        }))


    async def get_game(self):
        """
        Retrieves the game from the database asynchronously.
        """
        try:
            return await self.get_game_async()
        except Game.DoesNotExist:
            return None


    async def get_current_player(self, game):
        """
        Retrieves the current player asynchronously based on `current_turn`.
        """
        try:
            return await Player.objects.aget(game=game, position=game.current_turn)
        except Player.DoesNotExist:
            return None


    async def send_action_message(self, event):
        """ Sends the player's action (e.g., check, fold) to all players. """
        await self.send(text_data=json.dumps({
            "message": event["message"]
        }))