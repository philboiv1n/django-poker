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
        """

        print("* CONNECT")

        self.game_id = self.scope["url_route"]["kwargs"]["game_id"]
        self.room_group_name = f"game_{self.game_id}"
        self.user = self.scope["user"]
        self.user_channel_name = f"user_{self.user.id}"


        # Join the public game WebSocket room and a **private WebSocket group** 
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.channel_layer.group_add(self.user_channel_name, self.channel_name)
        await self.accept()

        # Retrieve game and broadcast private
        game = await sync_to_async(Game.objects.get)(id=self.game_id)
        await self.broadcast_private(game)

        # Retrieve & clean past messages from Redis
        redis_key = f"game_{self.game_id}_messages"
        stored_messages = redis_client.lrange(redis_key, -10, -1)
        clean_messages = [json.loads(msg).get("message", "") for msg in stored_messages]

        # Send cleaned messages to the client
        await self.send(text_data=json.dumps({"messages": clean_messages}))

    # -----------------------------------------------------------------------
    async def disconnect(self, close_code):
        """Disconnects the user from the WebSocket room."""

        print("* DISCONNECT")
        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)
        await self.channel_layer.group_discard(self.user_channel_name, self.channel_name)


    #
    #
    #
    #
    #
    #
    #
    #
    # =======================================================================
    # WEBSOCKET MESSAGE HANDLING
    # =======================================================================

    async def receive(self, text_data: str):
        """
        Handles messages received from WebSocket clients.
        """

        print("* RECEIVE")

        data = json.loads(text_data)
        action = data.get("action")
        player_username = data.get("player")
        amount = data.get("amount", 0)  # Only needed for bet/raise

        try:
            game = await sync_to_async(Game.objects.get)(id=self.game_id)
           
            # Handle "join" first, since player may not exist in the game yet
            if action == "join":
                await self.handle_join(game, player_username)
                return 

            # Fetch the player *after* handling "join"
            player = await sync_to_async(lambda: Player.objects.filter(game=game, user__username=player_username).first())()

            # Check if player exist in this game
            if not player:
                await self.send(text_data=json.dumps({"error": "You are not playing on this table"}))
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
          
            # Check if betting round is complete
            if await self.is_betting_round_over(game):
                await self.end_betting_round(game)

        except Game.DoesNotExist:
            print(f" Game {self.game_id} not found. Ignoring action: {action}")
        # except Exception as e:
        #     print(f"Unexpected error in receive: {e}")
       

    
    #
    #
    #
    #
    #
    #
    #
    #
    # =======================================================================
    # WEBSOCKET GAME STATE HANDLING
    # =======================================================================

    async def start_game(self, game):
        """
        Starts the game when the required number of players have joined.
        Initializes and shuffles the deck, assigns the dealer, blinds, and starts preflop.
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

        # Initialize for new game
        game.pot = 0
        game.current_phase = "preflop"
        game.community_cards = []
  
        # Create a deck (52 cards)
        suits = ["♠", "♣", "♥", "♦"]
        ranks = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
        deck = [f"{rank}{suit}" for suit in suits for rank in ranks]  # List of all cards

        # Shuffle and save the deck
        random.shuffle(deck)
        game.deck = deck
        
        # Assign dealer
        await self.rotate_dealer(game)
        
        # Assign Small & Big Blinds
        await self.assign_blinds(game)  # This will set the small blind, big blind & current_turn

        # Deal Hole Cards
        await self.deal(game)
     
        # Update Game Status & Broadcast Start
        game.status = "Active"
        await sync_to_async(game.save)()
        await self.broadcast_messages("🚀 Game can start.")

        print("! start_game")
        await self.broadcast_game_state(game)

    # -----------------------------------------------------------------------
    async def assign_blinds(self, game):
        """
        Assigns small and big blinds at the start of each round.
        Small blind is posted by the first player after the dealer.
        Big blind is posted by the second player after the dealer.
        """
       
        # Get sorted player list
        players = await sync_to_async(lambda: list(game.players.order_by("position")), thread_sensitive=True)()

        if len(players) < 2:
            print("Not enough players to assign blinds.")
            return

        # Determine small & big blind positions
        dealer_index = next((i for i, p in enumerate(players) if p.position == game.dealer_position), -1)
        if dealer_index == -1:
            print("Dealer position not found.")
            return

        small_blind_player = players[(dealer_index + 1) % len(players)]  # Next player after dealer
        big_blind_player = players[(dealer_index + 2) % len(players)]  # Second player after dealer

        # Force small & big blinds
        small_blind = game.small_blind
        big_blind = game.big_blind

        # Update the pot
        game.pot += small_blind + big_blind

        # Deduct blinds from players
        small_blind_player.chips -= small_blind
        small_blind_player.current_bet = small_blind
        await sync_to_async(small_blind_player.save)()
        big_blind_player.chips -= big_blind
        big_blind_player.current_bet = big_blind
        await sync_to_async(big_blind_player.save)()

        # Set the first player to act
        first_to_act_index = (dealer_index + 3) % len(players)  # Next player after big blind 
        first_to_act = players[first_to_act_index]
        game.current_turn = first_to_act.position
        await sync_to_async(game.save)()
        
        # Broadcast blinds
        small_blind_username = await sync_to_async(lambda: small_blind_player.user.username, thread_sensitive=True)()
        big_blind_username = await sync_to_async(lambda: big_blind_player.user.username, thread_sensitive=True)()
        await self.broadcast_messages(f"💰 {small_blind_username} posts SMALL blind ({small_blind} chips).")
        await self.broadcast_messages(f"💰 {big_blind_username} posts BIG blind ({big_blind} chips).")
      

 
    # -----------------------------------------------------------------------
    async def next_player(self, game):
        """
        Moves the turn to the next active player who has not folded.
        """
        print("* NEXT PLAYER")

        active_players = await sync_to_async(lambda: list(game.players.filter(has_folded=False).order_by("position")), thread_sensitive=True)()
        
        if not active_players:
            return  # No active players left, stop execution

        # Find current turn index
        current_index = next((i for i, p in enumerate(active_players) if p.position == game.current_turn), -1)

        print("next_player - current_index",current_index) # Debug
        if current_index == -1:
            game.current_turn = active_players[0].position  # Default to first active player
        else:
            game.current_turn = active_players[(current_index + 1) % len(active_players)].position  # Move to next active player

        await sync_to_async(game.save)()
        await self.broadcast_game_state(game)  # Broadcast updated game state


    # -----------------------------------------------------------------------
    async def get_first_player_after_dealer(self, game):
        """
        Returns the first active player (has not folded) after the dealer.
        """
        # Get list of active (non-folded) players, ordered by position
        active_players = await sync_to_async(
            lambda: list(game.players.filter(has_folded=False).order_by("position")),
            thread_sensitive=True
        )()

        if not active_players:
            return None  # No active players left

        # Find the dealer's position
        dealer_position = game.dealer_position

        # Iterate over players to find the first one after the dealer
        for player in active_players:
            if player.position > dealer_position:
                return player.position  # Found the first active player after dealer

        # If no player is found after the dealer, return the first active player (wrap-around)
        return active_players[0].position



    # -----------------------------------------------------------------------
    async def is_betting_round_over(self, game):
        """
        Determines if the betting round should end.

        The round ends when:
        - Only one player remains (they win by default).
        - All non-folded players have called the highest bet or gone all-in.
        - If no bets were made, all players must check before the round ends.
        """

        # Get the list of active (non-folded) players
        active_players = await sync_to_async(
            lambda: list(game.players.filter(has_folded=False).order_by("position")),
            thread_sensitive=True,
        )()

        if not active_players:
            return False  # Indicate that the round has ended

        if len(active_players) == 1:
            return False # stop

        # Find the highest bet placed in this round
        highest_bet = await sync_to_async(
            lambda: max(game.players.values_list("current_bet", flat=True))
        )()

        # Check if all active players have checked
        all_players_checked = all(player.has_checked for player in active_players)

        # Check if all players have **matched** the highest bet
        all_players_matched_bet = all(player.current_bet == highest_bet for player in active_players)

        # Scenario 1: If there was a bet, ensure all players have either **called or folded**
        if highest_bet > 0 and all_players_matched_bet:
            return True

        # Scenario 2: If no bets were made (highest_bet == 0), **ensure every player checked**
        if highest_bet == 0 and all_players_checked:
            return True  # Round ends if all players checked

        return False  # Otherwise, the betting round is still ongoing


    # -----------------------------------------------------------------------
    async def end_betting_round(self, game, winner=None):
        """Ends the current betting round and moves to the next phase if needed."""

        players = await sync_to_async(lambda: list(game.players.all()), thread_sensitive=True)()
        for player in players:
            player.current_bet = 0
            player.has_folded = False  # Reset fold status for the next phase
            player.has_checked = False
            await sync_to_async(player.save)()

        if winner:
            winner.chips += game.pot  # Assign the pot to the winner
            await sync_to_async(winner.save)()
          #  await sync_to_async(game.save)()
            username = await sync_to_async(lambda: winner.user.username, thread_sensitive=True)()
            await self.broadcast_messages(f"🏆 {username} wins the pot of {game.pot} chips!")
            await self.start_game(game)
        else:
            await self.advance_game_phase(game)        
            game.current_turn = await self.get_first_player_after_dealer(game)
            await self.broadcast_messages("🃏 New betting round has started!")
            print("! end_betting_round - Needed to update FE with cards + phase")
            await self.broadcast_game_state(game)

        await sync_to_async(game.save)()



    # -----------------------------------------------------------------------
    async def rotate_dealer(self, game):
        """
        Moves the dealer to the next active player.
        """

        print("* ROTATE DEALER")
        
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


    # -----------------------------------------------------------------------
    async def advance_game_phase(self, game):
        """
        Moves the game to the next phase (Preflop -> Flop -> Turn -> River -> Showdown).
        Resets necessary game variables and ensures proper transitions.
        """

        print("* ADVANCE GAME PHASE")

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

    
        await sync_to_async(game.save)()
        await self.broadcast_messages(f"📢 Game Phase: {game.current_phase.upper()}")
  

    # -----------------------------------------------------------------------
    async def move_to_flop(self, game):
        """Deals 3 community cards for the Flop and burns 1 card."""
        await self.burn_card(game)  # Burn 1 card
        game.community_cards.extend(game.deck[:3])  # Deal 3 cards
        game.deck = game.deck[3:]  # Remove dealt cards from deck
        await sync_to_async(game.save)()
        await self.broadcast_messages("🃏 The Flop has been dealt!")


    # -----------------------------------------------------------------------
    async def move_to_turn(self, game):
        """Deals 1 community card for the Turn and burns 1 card."""
        await self.burn_card(game)  # Burn 1 card
        game.community_cards.append(game.deck.pop(0))  # Deal 1 card
        await sync_to_async(game.save)()
        await self.broadcast_messages("🃏 The Turn has been dealt!")


    # -----------------------------------------------------------------------
    async def move_to_river(self, game):
        """Deals 1 community card for the River and burns 1 card."""
        await self.burn_card(game)  # Burn 1 card
        game.community_cards.append(game.deck.pop(0))  # Deal 1 card
        await sync_to_async(game.save)()
        await self.broadcast_messages("🃏 The River has been dealt!")


    # -----------------------------------------------------------------------
    async def handle_showdown(self, game):
        """
        Determines the winner(s) and distributes the pot.
        """
        # Evaluate hands & determine winner
        # @TODO - Empty for now
        print("* SHOWDOWN")

        # winner = await self.determine_winner(game)
        # Award chips to the winner
        # winner.chips += game.pot
        # await sync_to_async(winner.save)()
        # await sync_to_async(game.save)()
        # await self.broadcast_messages(f"🏆 {winner.user.username} wins the pot!")



    #
    #
    #
    #
    #
    #
    #
    #
    # ===========================================
    # CARD & DEALING LOGIC
    # ===========================================

    # -----------------------------------------------------------------------
    async def burn_card(self, game):
        """Burns (removes) the top card from the deck."""
        if game.deck:
            game.deck.pop(0)

    # -----------------------------------------------------------------------
    async def deal(self, game):
        """
        Shuffles the deck and deals two hole cards to each player.
        """

        # Init
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
    

    #
    #
    #
    #
    #
    #
    #
    #
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
            await self.send(text_data=json.dumps({"error": "You are already playing on this table"}))
            return  
        
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
            await self.send(text_data=json.dumps({"error": "Table is full!"}))
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
            game.deck = []
            game.community_cards = []
            game.current_phase = "preflop"
        else:
            if game.dealer_position == player.position:
                game.dealer_position = remaining_players[0].position
            if game.current_turn == player.position:
                await self.next_player(game)

        await sync_to_async(game.save)()

        # Notify all players
        leave_message = f"⚠️ {player_username} has left the table."
        await self.broadcast_messages(leave_message)
        await self.broadcast_game_state(game)


    # -----------------------------------------------------------------------
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
        

    # -----------------------------------------------------------------------
    async def handle_check(self, game, player):
        """Handles a player checking (if no bets exist)."""
        highest_bet = await sync_to_async(lambda: max(game.players.values_list("current_bet", flat=True)))()
        if highest_bet > 0:
            await self.send(text_data=json.dumps({"error": "Cannot check when a bet is in play"}))
            return
       
        # Mark the player as checked
        player.has_checked = True
        await sync_to_async(player.save)()

        # Broadcast
        username = await sync_to_async(lambda: player.user.username, thread_sensitive=True)()
        await self.broadcast_messages(f"✅ {username} checked.")

        # Move to the next player
        await self.next_player(game)



    # -----------------------------------------------------------------------
    async def handle_call(self, game, player):
        """Handles a player calling the highest bet."""
        print("CALL - handle_call - Open") #Debug
        #highest_bet = await sync_to_async(lambda: max(game.players.values_list("current_bet", flat=True)))()
        # Get the highest bet currently on the table
        highest_bet = await sync_to_async(
            lambda: max(game.players.values_list("current_bet", flat=True), default=0)
        )()

        call_amount = highest_bet - player.current_bet

        if call_amount <= 0:
            await self.send(text_data=json.dumps({"error": "Cannot call, please check, raise or fold."}))
            return 

        # Handle all-in scenario
        if player.chips < call_amount:
            call_amount = player.chips  # All-in

        # Deduct chips and update current bet
        player.chips -= call_amount
        player.current_bet += call_amount
        game.pot += call_amount
        await sync_to_async(player.save)()
        await sync_to_async(game.save)()

        # Broadcast message
        username = await sync_to_async(lambda: player.user.username, thread_sensitive=True)()
        await self.broadcast_messages(f"📞 {username} called {call_amount} chips.")

        # Move to the next player
        await self.next_player(game)



    # -----------------------------------------------------------------------
    async def handle_bet(self, game, player, amount):
        """Handles a player making a bet."""

        # Get the highest bet currently on the table
        highest_bet = await sync_to_async(
            lambda: max(game.players.values_list("current_bet", flat=True), default=0)
        )()

         # Fetch the big blind amount (minimum bet requirement)
        big_blind = game.big_blind

        # Validate the bet
        if amount <= 0 or amount > player.chips:
            await self.send(text_data=json.dumps({"error": "Invalid bet amount."}))
            return

        # Determine the minimum valid bet
        if highest_bet == 0:
            # If there's no active bet, bet must be at least the big blind
            min_bet = big_blind
        else:
            # If there's an active bet, raise must be at least the last bet amount
            min_bet = max(big_blind, highest_bet * 2)

        # Check if the player meets the minimum bet requirement
        if amount < min_bet and player.chips > min_bet:
            await self.send(text_data=json.dumps({"error": f"Minimum bet is {min_bet} chips."}))
            return

        # Deduct bet from player's chips
        player.chips -= amount
        player.current_bet += amount
        game.pot += amount

        # Save updated state
        await sync_to_async(player.save)()
        await sync_to_async(game.save)()

        # Broadcast message
        username = await sync_to_async(lambda: player.user.username, thread_sensitive=True)()
        await self.broadcast_messages(f"💰 {username} bet {amount} chips. Total Pot: {game.pot} chips.")
        
        # Move to the next player
        await self.next_player(game)


  

    #
    #
    #
    #
    #
    #
    #
    #
    #
    #    
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
            "community_cards": game.community_cards,
            "players": [
                {
                    "username": await sync_to_async(lambda: p.user.username, thread_sensitive=True)(),
                   # "total_user_chips": await sync_to_async(lambda: p.user.profile.chips, thread_sensitive=True)(),
                    "position": p.position,
                    "game_chips": p.chips,
                    "current_bet": p.current_bet,
                    "has_folded": p.has_folded,
                    "has_checked": p.has_checked,
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
