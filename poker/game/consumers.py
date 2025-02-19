import json
import redis
import random
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
        self.user = self.scope["user"]
        self.user_channel_name = f"user_{self.user.id}"


        # Join the public game WebSocket room
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)

        # Join a **private WebSocket group** for this specific user
        await self.channel_layer.group_add(self.user_channel_name, self.channel_name)
        
        await self.accept()

        # Retrieve game and broadcast state
        game = await sync_to_async(Game.objects.get)(id=self.game_id)
        await self.broadcast_game_state(game)
        await self.broadcast_private(game)

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
        await self.channel_layer.group_discard(self.user_channel_name, self.channel_name)


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
        amount = data.get("amount", 0)  # Only needed for bet/raise

        try:
            game = await sync_to_async(Game.objects.get)(id=self.game_id)
           
            # Handle "join" first, since player may not exist in the game yet
            if action == "join":
                await self.handle_join(game, player_username)
                return  # Exit early to avoid checking for player before they're added

            # Fetch the player *after* handling "join"
            player = await sync_to_async(lambda: Player.objects.filter(game=game, user__username=player_username).first())()

            if not player:
                print(f"Player {player_username} not found in game {game.id}")
                return 
           
            
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
            elif action == "raise":
                await self.handle_raise(game, player, amount)


             # Move to the next player after a valid action
            # next_turn = await sync_to_async(lambda: game.get_next_turn_after(player.position))()
            # if next_turn is not None:
            #     game.current_turn = next_turn
            #     await sync_to_async(game.save)()
            #     await self.broadcast_game_state(game)  # Broadcast updated game state

            # elif action in ["check", "fold"]:
            #     await self.handle_action(game, player_username, action)

        except Game.DoesNotExist:
            print(f" Game {self.game_id} not found. Ignoring action: {action}")
        # except Exception as e:
        #     print(f"Unexpected error in receive: {e}")

    # =======================================================================
    # WEBSOCKET ACTION HANDLING
    # =======================================================================

    async def handle_join(self, game, player_username: str):
        """
        Handles a player joining the game.

        Args:
            game (Game): The game instance.
            player_username (str): The username of the player joining.
        """
        user = await sync_to_async(User.objects.get)(username=player_username)
        user_profile = await sync_to_async(lambda: user.profile)()

        # Check if already sitting
        existing_player = await sync_to_async( 
            lambda: game.players.filter(user=user).exists()
        )()
        if existing_player:
            return  # Player is already at the table

        # Ensure the player has enough chips
        if user_profile.chips < game.buy_in:
            await self.send(text_data=json.dumps({"error": "Not enough chips to join!"}))
            return

        # Deduct buy-in from player’s total chips
        await sync_to_async(lambda: setattr(user_profile, "chips", user_profile.chips - game.buy_in))()
        await sync_to_async(user_profile.save)()

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
        await sync_to_async(Player.objects.create)(
            game=game, user=user, position=min(available_positions), chips=game.buy_in
        )

        # Notify all players : new user join table
        join_message = f" 🪑 {player_username} has join the table."
        await self.broadcast_messages(join_message)

        # Check if we should start the game
        player_count = await sync_to_async(lambda: game.players.count())()
        if game.game_type == "sit_and_go" and player_count == game.max_players:
            await self.start_game(game)

        # Notify all players about game state
        await self.broadcast_game_state(game)
        await self.broadcast_private(game)

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

        # Refund buy-in if game hasn't started
        if  game.game_type == "sit_and_go" and game.status == "Waiting":
            user = await sync_to_async(lambda: player.user)()
            user_profile = await sync_to_async(lambda: user.profile)()
            await sync_to_async(lambda: setattr(user_profile, "chips", user_profile.chips + game.buy_in))()
            await sync_to_async(user_profile.save)()
            await self.broadcast_private(game)
            
        # Remove player from the game
        await sync_to_async(player.delete)()

        remaining_players = await sync_to_async(
            lambda: list(game.players.order_by("position"))
        )()
        if len(remaining_players) < 2:
            if game.status == "Active":
                game.status = "Finished"
            else:
                game.status = "Waiting"
            game.dealer_position = None
            game.current_turn = None
            game.pot = 0
        else:
            if game.dealer_position == player.position:
                game.dealer_position = remaining_players[0].position
            if game.current_turn == player.position:
                # game.current_turn = await self.get_next_turn_after(
                #     game, game.dealer_position
                # )
                await self.next_player(game)

        await sync_to_async(game.save)()

        # Notify all players
        leave_message = f"⚠️ {player_username} has left the table."
        await self.broadcast_messages(leave_message)
        await self.broadcast_game_state(game)




    # async def handle_action(self, game, player_username: str, action: str):
    #     """
    #     Handles player actions such as 'check' and 'fold'.

    #     Args:
    #         game (Game): The current game instance.
    #         player_username (str): The username of the player performing the action.
    #         action (str): The action being performed ('check' or 'fold').
    #     """
    #     player = await sync_to_async(Player.objects.get)(
    #         game=game, user__username=player_username
    #     )

    #     if player and await sync_to_async(player.is_turn)():
    #         action_message = await sync_to_async(self.process_action)(
    #             game, player, action
    #         )
    #         await self.broadcast_messages(action_message)
    #         await self.broadcast_game_state(game)

   

    # -----------------------------------------------------------------------
    # def process_action(self, game, player, action: str) -> str:
    #     """
    #     Processes player action and advances the game.

    #     Args:
    #         game (Game): The game instance.
    #         player (Player): The player performing the action.
    #         action (str): The action ('check' or 'fold').

    #     Returns:
    #         str: The action message to broadcast.
    #     """

    #     username = player.user.username

    #     action_message = (
    #         f"✅ {username} checked." if action == "check" else f"🚫 {username} folded."
    #     )

    #     game.current_turn = game.get_next_turn_after(player.position)
    #     game.save()

    #     return action_message


    async def handle_fold(self, game, player):
        """Handles player folding their hand."""
        username = await sync_to_async(lambda: player.user.username, thread_sensitive=True)()
        await self.broadcast_messages(f"🚫 {username} folded.")

        player.has_folded = True
        await sync_to_async(player.save)()
        
        # Check if only one player remains
        active_players = await sync_to_async(lambda: list(game.players.filter(has_folded=False)), thread_sensitive=True)()

        if len(active_players) == 1:
            await self.end_betting_round(game, winner=active_players[0])
        else:
            await self.next_player(game)
        

        # # If only one player remains, they win
        # active_players = await sync_to_async(lambda: list(game.players.filter(has_folded=False)))()
        # # if len(active_players) == 1:
        # #     await self.end_round(game, winner=active_players[0])



    async def handle_check(self, game, player):
        """Handles a player checking (if no bets exist)."""
        highest_bet = await sync_to_async(lambda: max(game.players.values_list("current_bet", flat=True)))()
        if highest_bet > 0:
            await self.send(text_data=json.dumps({"error": "Cannot check when a bet is in play"}))
            return
        username = await sync_to_async(lambda: player.user.username, thread_sensitive=True)()
        await self.broadcast_messages(f"✅ {username} checked.")

        # Move to the next player
        await self.next_player(game)




    async def handle_call(self, game, player):
        """Handles a player calling the highest bet."""
        highest_bet = await sync_to_async(lambda: max(game.players.values_list("current_bet", flat=True)))()
        call_amount = highest_bet - player.current_bet

        if call_amount == 0:
            await self.send(text_data=json.dumps({"error": "Cannot call, please check, raise or fold."}))
            return  # Stop further execution

        # Handle all-in scenario
        if player.chips < call_amount:
            call_amount = player.chips  # All-in

        # Deduct chips and update current bet
        player.chips -= call_amount
        player.current_bet += call_amount
        await sync_to_async(player.save)()

        # Broadcast message
        username = await sync_to_async(lambda: player.user.username, thread_sensitive=True)()
        await self.broadcast_messages(f"📞 {username} called {call_amount} chips.")

        # Move to the next player
        # await self.next_player(game, player)

        # Check if the betting round is over
        active_players = await sync_to_async(lambda: list(game.players.exclude(current_bet=0)), thread_sensitive=True)()
        highest_bet = max(p.current_bet for p in active_players) if active_players else 0

        if all(p.current_bet == highest_bet or p.chips == 0 for p in active_players):
            await self.end_betting_round(game)
        else:
            await self.next_player(game)




    async def handle_bet(self, game, player, amount):
        """Handles a player making a bet."""
        if amount <= 0 or amount > player.chips:
            await self.send(text_data=json.dumps({"error": "Invalid bet amount."}))
            return  # Invalid bet amount

        # player.chips -= amount
        # player.current_bet = amount
        # await sync_to_async(player.save)()
        # username = await sync_to_async(lambda: player.user.username, thread_sensitive=True)()

        # await self.broadcast_messages(f"💰 {username} bet {amount} chips.")

        player.chips -= amount  # Deduct from player stack
        player.current_bet += amount  # Track how much player has bet
        game.pot += amount  # Add to the game pot
        await sync_to_async(player.save)()
        await sync_to_async(game.save)()

        username = await sync_to_async(lambda: player.user.username, thread_sensitive=True)()
        await self.broadcast_messages(f"💰 {username} bet {amount} chips. Total Pot: {game.pot} chips.")
        
        # Move to the next player
        await self.next_player(game)


    # async def handle_raise(self, game, player, amount):
    #     """Handles a player raising the current bet."""
    #     highest_bet = await sync_to_async(lambda: max(game.players.values_list("current_bet", flat=True)))()
    #     min_raise = highest_bet * 2  # At least double the current bet

    #     if amount < min_raise or amount > player.chips:
    #         return  # Invalid raise amount

    #     player.chips -= amount
    #     player.current_bet = amount
    #     await sync_to_async(player.save)()
    #     username = await sync_to_async(lambda: player.user.username, thread_sensitive=True)()
    #     await self.broadcast_messages(f"⬆️ {username} raised to {amount} chips.")

    async def handle_raise(self, game, player, amount):
        """Handles a player raising the bet."""
        if player.chips < amount:
            await self.send(text_data=json.dumps({"error": "Not enough chips to raise."}))
            return

        player.chips -= amount
        player.current_bet += amount
        await sync_to_async(player.save)()

        username = await sync_to_async(lambda: player.user.username, thread_sensitive=True)()
        await self.broadcast_messages(f"⬆️ {username} raised {amount} chips.")

        # Move to next player
        await self.next_player(game)


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
            game.status = "Waiting"
            game.current_turn = None
            game.dealer_position = None
            await sync_to_async(game.save)()
            return


        # Create a deck (52 cards)
        suits = ["♠", "♣", "♥", "♦"]
        ranks = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
        deck = [f"{rank}{suit}" for suit in suits for rank in ranks]  # List of all cards

        # Shuffle the deck
        random.shuffle(deck)

        # Save deck to the game
        game.deck = deck
       # await sync_to_async(game.save)()

        game.status = "Active"
        game.dealer_position = players[0].position
        await self.next_player(game)
        await self.shuffle_and_deal(game)
        await sync_to_async(game.save)()
        await self.broadcast_messages("🚀 Game can start.")

    # -----------------------------------------------------------------------
    # async def get_next_turn_after(self, game, position):
    #     """
    #     Gets the next player's position after the given position.
    #     Loops around if at the last position.
    #     """
    #     players = await sync_to_async(
    #         lambda: list(game.players.order_by("position")), thread_sensitive=True
    #     )()

    #     for i, player in enumerate(players):
    #         if player.position == position:
    #             return players[(i + 1) % len(players)].position

    #     return None

    # -----------------------------------------------------------------------
    # async def end_round(self, game, winner):
    #     """
    #     Ends the current round and moves the dealer to the next player.
    #     """
    #     players = await sync_to_async(list)(game.players.order_by("position"))

    #     if not players:
    #         print("No players left.")
    #         return

    #     # Move dealer to the next player
    #     game.dealer_position = self.get_next_turn_after(game, game.dealer_position)

    #     # New round starts, next turn goes to the player right after the new dealer
    #     game.current_turn = self.get_next_turn_after(game, game.dealer_position)

    #     await sync_to_async(game.save)()

    #     # Broadcast updated game state
    #     await self.broadcast_game_state(game)


 
    async def next_player(self, game):
        """
        Moves the turn to the next active player who has not folded.
        """
        active_players = await sync_to_async(lambda: list(game.players.filter(has_folded=False).order_by("position")), thread_sensitive=True)()
        
        if not active_players:
            return  # No active players left, stop execution

        # Find current turn index
        current_index = next((i for i, p in enumerate(active_players) if p.position == game.current_turn), -1)

        if current_index == -1:
            game.current_turn = active_players[0].position  # Default to first active player
        else:
            game.current_turn = active_players[(current_index + 1) % len(active_players)].position  # Move to next active player

        await sync_to_async(game.save)()
        await self.broadcast_game_state(game)  # Broadcast updated game state


    #     next_turn = await sync_to_async(lambda: game.get_next_turn_after(player.position))()
       
    #     if next_turn is not None:
    #         game.current_turn = next_turn
    #         await sync_to_async(game.save)()
    #         await self.broadcast_game_state(game)  # Broadcast updated game state
            

    # def get_next_turn_after(self, position):
    #     """
    #     Returns the next non-folded player's position after the given position.
    #     """
    #     active_players = list(self.players.filter(has_folded=False).order_by("position"))

    #     if not active_players:
    #         return None  # No players left in the game
        
    #     for i, player in enumerate(active_players):
    #         if player.position == position:
    #             return active_players[(i + 1) % len(active_players)].position  # Move to next active player
        
    #     return active_players[0].position  # Default to first active player


   # -----------------------------------------------------------------------
    # async def end_round(self, game, winner=None):
    #     """
    #     Ends the current betting round.
    #     - If there is only one active player, they win the pot.
    #     - Otherwise, move to the next round of betting or showdown.
    #     - Move the dealer to the next position.
    #     - Reset all bets and update chip stacks.
    #     """

    #     # If a winner is already determined (only 1 active player remains)
    #     if winner:
    #         winner.chips += game.pot  # Assign the pot to the winner
    #         game.pot = 0  # Reset the pot
    #         await sync_to_async(winner.save)()
    #         await sync_to_async(game.save)()
            
    #         await self.broadcast_messages(f"🏆 {winner.user.username} wins the pot!")
        
    #     else:
    #         # Otherwise, move to the next betting round (Flop, Turn, River, etc.)
    #         await self.advance_game_phase(game)

    #     # Reset all player bets
    #     players = await sync_to_async(lambda: list(game.players.all()), thread_sensitive=True)()
    #     for player in players:
    #         player.current_bet = 0
    #         await sync_to_async(player.save)()

    #     # Move dealer to the next player
    #     await self.rotate_dealer(game)

    #     # Start a new round or the next phase
    #     await self.start_new_betting_round(game)
        
    #     # Broadcast updated game state
    #     await self.broadcast_game_state(game)

    # async def end_betting_round(self, game, winner=None):
    #     """
    #     Ends the current betting round.
        
    #     - If only one active player remains, they win the pot.
    #     - Otherwise, move to the next game phase (Flop, Turn, River, Showdown).
    #     - Move the dealer to the next position.
    #     - Reset player bets and update chip stacks.
    #     - Start a new betting round if needed.
    #     """

    #     # Fetch all players once
    #     players = await sync_to_async(lambda: list(game.players.all()), thread_sensitive=True)()

    #     if winner:
    #         # Assign pot to the winner
    #         winner.chips += game.pot
    #         game.pot = 0
    #         await sync_to_async(winner.save)()
    #         await sync_to_async(game.save)()
            
    #         await self.broadcast_messages(f"🏆 {winner.user.username} wins the pot!")
            
    #         # Reset player bets and move dealer before restarting a new game
    #         for player in players:
    #             player.current_bet = 0
    #             await sync_to_async(player.save)()
            
    #         await self.rotate_dealer(game)
    #         await self.start_new_betting_round(game)  # Restart with a fresh betting round
    #         await self.broadcast_game_state(game)
    #         return  # Stop here since the round is over

    #     # Move to the next betting round (Flop, Turn, River, Showdown)
    #     await self.advance_game_phase(game)

    #     # Reset all player bets for the next phase
    #     for player in players:
    #         player.current_bet = 0
    #         await sync_to_async(player.save)()

    #     # Move dealer to the next position
    #     await self.rotate_dealer(game)

    #     # Start the next phase betting round
    #     await self.start_new_betting_round(game)

    #     # Broadcast updated game state
    #     await self.broadcast_game_state(game)


    async def start_new_betting_round(self, game):
        """
        Starts a new betting round after the dealer has been rotated.
        Resets player bets and assigns the next player.
        """
        # Reset all player bets
        players = await sync_to_async(lambda: list(game.players.all()), thread_sensitive=True)()
        for player in players:
            player.current_bet = 0
            await sync_to_async(player.save)()

        # Find the first player after the dealer who is still in the game
        # next_player = await self.get_next_turn_after(game, game.dealer_position)
        await self.next_player(game)

        # if next_player is None:
        #     await self.end_betting_round(game)  # End round if no valid player found
        #     return  

        # game.current_turn = next_player
        await sync_to_async(game.save)()
        await self.broadcast_messages("🃏 New betting round has started!")
        await self.broadcast_game_state(game)
    


    async def end_betting_round(self, game, winner=None):
        """Ends the current betting round and moves to the next phase if needed."""
        if winner:
            winner.chips += game.pot  # Assign the pot to the winner
            game.pot = 0
            await sync_to_async(winner.save)()
            await sync_to_async(game.save)()
            username = await sync_to_async(lambda: winner.user.username, thread_sensitive=True)()
            await self.broadcast_messages(f"🏆 {username} wins the pot!")
           # await self.broadcast_messages(f"🏆 {winner.user.username} wins the pot!")
        else:
            await self.advance_game_phase(game)


        # Reset all players for the new round
        players = await sync_to_async(lambda: list(game.players.all()), thread_sensitive=True)()
       
        for player in players:
            player.current_bet = 0
            player.has_folded = False  # Reset fold status for the next phase
            await sync_to_async(player.save)()
       
        await self.rotate_dealer(game)
        await self.start_new_betting_round(game)
        await self.broadcast_game_state(game)


    async def rotate_dealer(self, game):
        """
        Moves the dealer to the next active player.
        """
        players = await sync_to_async(lambda: list(game.players.order_by("position")), thread_sensitive=True)()
        
        if len(players) < 2:
            return  # No need to rotate if only one player remains

        # Find the current dealer's position in the list
        current_dealer_index = next((i for i, p in enumerate(players) if p.position == game.dealer_position), -1)

        # If the current dealer isn't found (new game or invalid position), start from player 0
        new_dealer_index = (current_dealer_index + 1) % len(players) if current_dealer_index != -1 else 0
        new_dealer = players[new_dealer_index]
        game.dealer_position = new_dealer.position
        await sync_to_async(game.save)()
        
        new_dealer_username = await sync_to_async(lambda: new_dealer.user.username, thread_sensitive=True)()
        await self.broadcast_messages(f"🔄 New dealer : {new_dealer_username}.")


   


    # async def advance_game_phase(self, game):
    #     """
    #     Moves the game to the next phase (Flop -> Turn -> River -> Showdown).
    #     """
    #     if game.current_phase == "preflop":
    #         game.current_phase = "flop"
    #     elif game.current_phase == "flop":
    #         game.current_phase = "turn"
    #     elif game.current_phase == "turn":
    #         game.current_phase = "river"
    #     elif game.current_phase == "river":
    #         game.current_phase = "showdown"

    #     await sync_to_async(game.save)()
    #     await self.broadcast_messages(f"📢 Game Phase: {game.current_phase.upper()}")

    async def advance_game_phase(self, game):
        """
        Moves the game to the next phase (Preflop -> Flop -> Turn -> River -> Showdown).
        Resets necessary game variables and ensures proper transitions.
        """
        if game.current_phase == "preflop":
            game.current_phase = "flop"
            await self.move_to_flop(game)
        elif game.current_phase == "flop":
            game.current_phase = "turn"
            await self.move_to_turn(game)
        elif game.current_phase == "turn":
            game.current_phase = "river"
            await self.move_to_river(game)
        elif game.current_phase == "river":
            game.current_phase = "showdown"
            await self.handle_showdown(game)

        # Reset necessary game variables (e.g., pot for the new round)
        game.pot = 0
        await sync_to_async(game.save)()

        await self.broadcast_messages(f"📢 Game Phase: {game.current_phase.upper()}")
        await self.broadcast_game_state(game)




    async def move_to_flop(self, game):
        """Deals 3 community cards for the Flop and burns 1 card."""
        await self.burn_card(game)  # Burn 1 card
        game.community_cards.extend(game.deck[:3])  # Deal 3 cards
        game.deck = game.deck[3:]  # Remove dealt cards from deck
        await sync_to_async(game.save)()
        await self.broadcast_messages("🃏 The Flop has been dealt!")


    async def move_to_turn(self, game):
        """Deals 1 community card for the Turn and burns 1 card."""
        await self.burn_card(game)  # Burn 1 card
        game.community_cards.append(game.deck.pop(0))  # Deal 1 card
        await sync_to_async(game.save)()
        await self.broadcast_messages("🃏 The Turn has been dealt!")


    async def move_to_river(self, game):
        """Deals 1 community card for the River and burns 1 card."""
        await self.burn_card(game)  # Burn 1 card
        game.community_cards.append(game.deck.pop(0))  # Deal 1 card
        await sync_to_async(game.save)()
        await self.broadcast_messages("🃏 The River has been dealt!")


    async def handle_showdown(self, game):
        """
        Determines the winner(s) and distributes the pot.
        """
        # Evaluate hands & determine winner
        winner = await self.determine_winner(game)
        
        # Award chips to the winner
        winner.chips += game.pot
        game.pot = 0  # Reset pot
        await sync_to_async(winner.save)()
        await sync_to_async(game.save)()

        await self.broadcast_messages(f"🏆 {winner.user.username} wins the pot!")

    # ===========================================
    # CARD & DEALING LOGIC
    # ===========================================

    # def create_deck(self):
    #     """Creates a standard deck of 52 cards."""
    #     suits = ["♠", "♥", "♦", "♣"]
    #     ranks = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
    #     return [f"{rank}{suit}" for suit in suits for rank in ranks]

    async def burn_card(self, game):
        """Burns (removes) the top card from the deck."""
        if game.deck:
            game.deck.pop(0)

    async def shuffle_and_deal(self, game):
        """
        Shuffles the deck and deals two hole cards to each player.
        """

        # Create and shuffle the deck
        # deck = self.create_deck()
        # random.shuffle(deck)
        dealt_cards = {}

        deck = game.deck

        # Fetch players in correct order
        players = await sync_to_async(
            lambda: list(game.players.order_by("position")), thread_sensitive=True
        )()
        if not players:
            print("No players in the game. Cannot deal cards.")
            return

        # Determine starting position (first player after the dealer)
        dealer_position = game.dealer_position
        start_index = next((i for i, p in enumerate(players) if p.position == dealer_position), -1)
        if start_index == -1:
            print("Dealer not found. Cannot proceed with dealing.")
            return
        
        # Deal cards in two rounds
        for _ in range(2):  # Two hole cards per player
            for i in range(len(players)):
                player = players[(start_index + i + 1) % len(players)]  # Next player after dealer
                card = deck.pop(0) 
                
                # Append card to player's hand
                username = await sync_to_async(
                        lambda: player.user.username, thread_sensitive=True
                    )()
                if username not in dealt_cards:
                    dealt_cards[username] = []
                dealt_cards[username].append(card)

                # Save hole cards to database
                for player in players:
                    username = await sync_to_async(
                        lambda: player.user.username, thread_sensitive=True
                    )()
                    if username in dealt_cards:
                        await sync_to_async(player.set_hole_cards)(dealt_cards[username])

                # print(f"Dealt cards: {dealt_cards}") # Debugging

        game.deck = deck
   
        # Save the updated game state
        await sync_to_async(game.save)()
        
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
        
        # Create a personal game state message for each player
        game_state_message = {
            "type": "update_game_state",
            "game_status": game.status,
            "current_phase": game.current_phase,
            "pot": game.pot,
            "dealer_position": game.dealer_position,
            "current_turn": game.current_turn,
            "current_username": current_username,
            "players": [
                {
                    "username": await sync_to_async(lambda: p.user.username, thread_sensitive=True)(),
                   # "total_user_chips": await sync_to_async(lambda: p.user.profile.chips, thread_sensitive=True)(),
                    "position": p.position,
                    "game_chips": p.chips,
                    "current_bet": p.current_bet,
                }
                for p in players
            ],
        }

        # Send this game state **privately** to the respective player
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "send_game_state",
                "data": game_state_message,
            },
        )

    async def send_game_state(self, event):
        """Sends game state updates to frontend"""
        await self.send(text_data=json.dumps(event["data"]))


    # -----------------------------------------------------------------------
    async def broadcast_private(self, game):
        """Sends updated game state to all connected players"""

        # Fetch all players asynchronously
        players = await sync_to_async(
            lambda: list(game.players.all()), thread_sensitive=True
        )()

        for player in players:
            id = await sync_to_async(lambda: player.user.id, thread_sensitive=True)()
            hole_cards = await sync_to_async(lambda: player.hole_cards, thread_sensitive=True)()
            total_user_chips = await sync_to_async(lambda: player.user.profile.chips, thread_sensitive=True)()

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
                    "type": "send_personal_game_state",
                    "data": private_data,
                },
            )

    async def send_personal_game_state(self, event):
        """
        Sends the player's own game state (including their hole cards).
        """
        await self.send(text_data=json.dumps(event["data"]))
