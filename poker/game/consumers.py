import json
import redis
import random
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from asgiref.sync import sync_to_async
from treys import Evaluator, Card
from .models import Game, Player, User

# Connect to Redis
redis_client = redis.Redis(
    host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=0, decode_responses=True
)


class GameConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer to handle real-time updates for the poker games.
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

        # Retrieve game and send **private** updates only to this user
        game = await sync_to_async(Game.objects.get)(id=self.game_id)

        # Send private hole cards only to the reconnecting player, not broadcast
        await self.send_private_game_state(game, self.user)

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
        await self.channel_layer.group_discard(
            self.user_channel_name, self.channel_name
        )

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
            player = await sync_to_async(
                lambda: Player.objects.filter(
                    game=game, user__username=player_username
                ).first()
            )()

            # Check if player exist in this game
            if not player:
                await self.send(
                    text_data=json.dumps({"error": "You are not playing on this table"})
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
    # WEBSOCKET ACTION HANDLING
    # =======================================================================
    #
    # hande_join : This is the first function that the user will call
    # when he is on a table. When there is enough player, this function will
    # call start_hand.
    #

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
            await self.send(
                text_data=json.dumps({"error": "You are already playing on this table"})
            )
            return

        # Ensure the player has enough chips
        if user_profile.chips < game.buy_in:
            await self.send(
                text_data=json.dumps({"error": "Not enough chips to join!"})
            )
            return

        # Deduct buy-in from player’s total chips
        await sync_to_async(
            lambda: setattr(user_profile, "chips", user_profile.chips - game.buy_in)
        )()
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
        # currently only working for sit_and_go games
        player_count = await sync_to_async(lambda: game.players.count())()
        if game.game_type == "sit_and_go" and player_count == game.max_players:
            await self.start_hand(game)
            return

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
        if game.game_type == "sit_and_go" and game.status == "waiting":
            user = await sync_to_async(lambda: player.user)()
            user_profile = await sync_to_async(lambda: user.profile)()
            await sync_to_async(
                lambda: setattr(user_profile, "chips", user_profile.chips + game.buy_in)
            )()
            await sync_to_async(user_profile.save)()
            await self.broadcast_private(game)

        # Remove player from the game
        await sync_to_async(player.delete)()

        remaining_players = await sync_to_async(
            lambda: list(game.players.order_by("position"))
        )()
        if len(remaining_players) < 2:
            if game.status == "active":
                game.status = "finished"
            else:  # Player leave before the game start
                game.status = "waiting"

            await self.reset_hand(game)

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

        # Safety Check
        if player.is_all_in or player.has_folded:
            await self.send(text_data=json.dumps({"error": "You cannot fold."}))
            return
        
        username = await sync_to_async(
            lambda: player.user.username, thread_sensitive=True
        )()
        await self.broadcast_messages(f"🚫 {username} folded.")

        player.has_folded = True
        player.has_acted_this_round = True
        await sync_to_async(player.save)()

        # Move to the post action flow
        await self.post_action_flow(game)


    # -----------------------------------------------------------------------
    async def handle_check(self, game, player):
        """Handles a player checking (if no bets exist)."""

        # Safety Check
        if player.is_all_in or player.has_folded:
            await self.send(text_data=json.dumps({"error": "You cannot check."}))
            return
        
        highest_bet = await sync_to_async(
            lambda: max(game.players.values_list("current_bet", flat=True))
        )()


        difference = highest_bet - player.current_bet

        if difference > 0:
            await self.send(json.dumps({"error": "Cannot check when facing a bet. Must call or fold."}))
            return

        # Mark the player as checked
        player.has_checked = True
        player.has_acted_this_round = True
        await sync_to_async(player.save)()

        # Broadcast
        username = await sync_to_async(
            lambda: player.user.username, thread_sensitive=True
        )()
        await self.broadcast_messages(f"✅ {username} checked.")

        # Move to the post action flow
        await self.post_action_flow(game)




    # -----------------------------------------------------------------------
    async def handle_call(self, game, player):
        """Handles a player calling the highest bet."""

        # Safety Check
        if player.is_all_in or player.has_folded:
            await self.send(text_data=json.dumps({"error": "You cannot call."}))
            return
        
        # Get the highest bet currently on the table
        highest_bet = await sync_to_async(
            lambda: max(game.players.values_list("current_bet", flat=True), default=0)
        )()

        call_amount = highest_bet - player.current_bet

        if call_amount <= 0:
            await self.send(text_data=json.dumps({"error": "Cannot call, please check, raise or fold."}))
            return

        # Handle all-in scenario
        if player.chips <= call_amount:
            call_amount = player.chips  # All-in
            player.is_all_in = True  # Mark player as all-in

        # Deduct chips and update current bet
        player.chips -= call_amount
        player.current_bet += call_amount
        player.total_bet += call_amount
        player.has_acted_this_round = True
        await sync_to_async(player.save)()
     

        # Broadcast message
        username = await sync_to_async(
            lambda: player.user.username, thread_sensitive=True
        )()

        if player.is_all_in:
            await self.broadcast_messages(
                f"🔴 {username} goes ALL-IN with {call_amount} chips!"
            )
        else:
            await self.broadcast_messages(f"📞 {username} called {call_amount} chips.")

        # Move to the post action flow
        await self.post_action_flow(game)



    # -----------------------------------------------------------------------
    async def handle_bet(self, game, player, amount):
        """Handles a player making a bet."""

        # Safety Check
        if player.is_all_in or player.has_folded:
            await self.send(text_data=json.dumps({"error": "You cannot bet."}))
            return
        
        # Validate the bet amount
        if amount <= 0 or amount > player.chips:
            await self.send(text_data=json.dumps({"error": "Invalid bet amount."}))
            return
        
        highest_bet = await sync_to_async(
            lambda: max(game.players.values_list("current_bet", flat=True), default=0)
        )()

        big_blind = game.big_blind

        min_bet = big_blind if highest_bet == 0 else max(big_blind, highest_bet * 2)
        if amount < min_bet and player.chips > min_bet:
            await self.send(json.dumps({"error": f"Minimum raise is {min_bet} chips."}))
            return

        # All-in check
        if amount == player.chips:
            player.is_all_in = True


        # Deduct bet from player's chips
        player.chips -= amount
        player.current_bet += amount
        player.total_bet += amount
        player.has_acted_this_round = True
        await sync_to_async(player.save)()
       

        # Broadcast message
        username = await sync_to_async(
            lambda: player.user.username, thread_sensitive=True
        )()

        if player.is_all_in:
            await self.broadcast_messages(
                f"🔴 {username} goes ALL-IN with {amount} chips!"
            )
        else:
            await self.broadcast_messages(
                f"💰 {username} bet {amount} chips."
            )

      
        # Move to the post action flow
        await self.post_action_flow(game)

  

    # -----------------------------------------------------------------------
    async def post_action_flow(self, game):
        """
        Called after an action (fold, check, call, bet).
        Decides if the betting round is over or if we go to the next player.
        """

        print("* POST ACTION FLOW")

        # @TODO
        # This function could probably be optimized...

        active_players = await sync_to_async(lambda: list(game.players.filter(has_folded=False)), thread_sensitive=True)()

        if len(active_players) == 1:
            await self.end_phase(game, winner=active_players[0])
            return

        if len(active_players) == 2:
            p1, p2 = active_players

            print("* POST ACTION FLOW - 2 players remains!")
            # If both are all-in, automatically run out the board to showdown
            if p1.is_all_in and p2.is_all_in:
                while game.current_phase != "showdown":
                   await self.advance_hand_phase(game)
                await self.start_hand(game)   
                return

            # If exactly one is all-in and the other has matched that bet
            # (no further action possible), skip just one street
            if p1.is_all_in and p2.total_bet >= p1.total_bet:
                await self.end_phase(game)
                return
            if p2.is_all_in and p1.total_bet >= p2.total_bet:
                await self.end_phase(game)
                return

        # Check if the phase is over
        if await self.is_phase_over(game):
            await self.end_phase(game)
            return

        # Otherwise, proceed to next_player
        await self.next_player(game)


   

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

    async def start_hand(self, game):
        """
        Starts the hand
        Initializes and shuffles the deck, assigns the dealer, blinds, and starts preflop.
        """

        print("* START HAND")

        # Fetch active players
        players = await sync_to_async(
            lambda: list(game.players.order_by("position")), thread_sensitive=True
        )()

        # TESTING - Was at the end of the handle_showdown, moved here in 0.0.20
        for player in players:
            player.total_bet = 0
            player.has_folded = False
            player.is_all_in = False
            player.is_big_blind = False
            await sync_to_async(player.save)()

        # Get blind amounts
        big_blind = game.big_blind

        # Iterate over players and check chip status
        for player in players:
            if player.chips == 0:
                username = await sync_to_async(
                    lambda: player.user.username, thread_sensitive=True
                )()
                print(f"{username} has no chips left and will be removed from the game.")
                await self.handle_leave(game, username)  # Remove player from the game
            elif player.chips < big_blind:
                username = await sync_to_async(
                    lambda: player.user.username, thread_sensitive=True
                )()
                print(f"{username} does not have enough for blinds and will go all-in.")

        # Fetch active players again (updated)
        players = await sync_to_async(
            lambda: list(game.players.order_by("position")), thread_sensitive=True
        )()

        # If only 1 player remains, end the hand
        if len(players) < 2:
            print("*** Only 1 player left. Ending game and transferring chips.")
            await self.transfer_chips_to_profile(players[0])
            return

        # Reset and start the hand!
        await self.reset_hand(game)

        # Assign dealer
        await self.rotate_dealer(game)

        # Assign Small & Big Blinds
        await self.assign_blinds(game)

        # Create a deck (52 cards)
        suits = ["s", "c", "h", "d"]  # ["♠", "♣", "♥", "♦"]
        ranks = ["2", "3", "4", "5", "6", "7", "8", "9", "T", "J", "Q", "K", "A"]
        deck = [
            f"{rank}{suit}" for suit in suits for rank in ranks
        ]  # List of all cards

        # Shuffle and save the deck
        random.shuffle(deck)
        game.deck = deck

        # Deal Hole Cards
        await self.deal(game)

        # Update Game Status & Broadcast Start
        game.status = "active"
        await sync_to_async(game.save)()
        await self.broadcast_messages("🚀 Starting a new hand.")
        await self.broadcast_game_state(game)

    # -----------------------------------------------------------------------
    async def reset_hand(self, game):
        game.current_turn = None
        game.side_pots = []
        game.deck = []
        game.community_cards = []
        game.current_phase = "preflop"
        await sync_to_async(game.save)()

  
    # -----------------------------------------------------------------------
    async def assign_blinds(self, game):
        """
        Assigns small and big blinds at the start of each round.
        If a player cannot afford the blind, they are removed from the table.
        """

        # Fetch the sorted player list
        players = await sync_to_async(
            lambda: list(game.players.order_by("position")),
            thread_sensitive=True,
        )()

        if len(players) < 2:
            print("Not enough players to assign blinds.")
            return  # Safety check

        # Find the dealer index in the players list
        dealer_index = next(
            (i for i, p in enumerate(players) if p.position == game.dealer_position),
            -1
        )
        if dealer_index == -1:
            print("Dealer position not found – cannot assign blinds.")
            return

        small_blind = game.small_blind
        big_blind = game.big_blind

        if len(players) == 2:
            # =======================
            # HEADS-UP (2 players)
            # =======================
            # In heads-up, the dealer is also the small blind.
            small_blind_player = players[dealer_index]
            big_blind_player = players[(dealer_index + 1) % 2]

            # after posting blinds, the small blind acts first preflop
            game.current_turn = small_blind_player.position

        else:
            # ===========================
            # 3+ PLAYERS (RING GAME)
            # ===========================
            sb_index = (dealer_index + 1) % len(players)
            bb_index = (dealer_index + 2) % len(players)
            small_blind_player = players[sb_index]
            big_blind_player = players[bb_index]

            # First to act preflop is dealer_index + 3
            first_to_act_index = (dealer_index + 3) % len(players)
            game.current_turn = players[first_to_act_index].position

           
        # Deduct small blind
        small_blind_player.chips -= small_blind
        small_blind_player.current_bet = small_blind
        small_blind_player.total_bet += small_blind
        await sync_to_async(small_blind_player.save)()

        # Deduct big blind
        big_blind_player.chips -= big_blind
        big_blind_player.current_bet = big_blind
        big_blind_player.total_bet += big_blind
        big_blind_player.is_big_blind = True
        await sync_to_async(big_blind_player.save)()

        # Broadcast
        sb_username = await sync_to_async(lambda: small_blind_player.user.username)()
        bb_username = await sync_to_async(lambda: big_blind_player.user.username)()
        await self.broadcast_messages(f"💰 {sb_username} posts SMALL blind ({small_blind} chips).")
        await self.broadcast_messages(f"💰 {bb_username} posts BIG blind ({big_blind} chips).")

        # Save
        await sync_to_async(game.save)()

    # -----------------------------------------------------------------------
    async def next_player(self, game):
        """
        Moves the turn to the next active player who has not folded and is not all-in.
        If all active players are all-in, the round advances automatically.
        """
        print("* NEXT PLAYER")

        active_players = await sync_to_async(
            lambda: list(game.players.filter(has_folded=False).order_by("position")),
            thread_sensitive=True,
        )()

        # Safety check: No active players left
        if not active_players:
            return

        # Check if all active players are all-in
        all_all_in = all(p.is_all_in for p in active_players)
        
        if all_all_in:
            print("* ALL PLAYERS ALL-IN -> ADVANCING ROUND")
            await self.advance_hand_phase(game)
            return

        # Find the index of the current turn
        current_index = next((i for i, p in enumerate(active_players) if p.position == game.current_turn),-1,)

        # Determine the next player who is not all-in
        for _ in range(len(active_players)):  # Loop ensures we don't get stuck in an infinite loop
            if current_index == -1:
                game.current_turn = active_players[0].position  # Default to first active player
            else:
                current_index = (current_index + 1) % len(active_players)
                game.current_turn = active_players[current_index].position

            # If the chosen player is not all-in, break loop
            if not active_players[current_index].is_all_in:
                break

        await sync_to_async(game.save)()
        await self.broadcast_game_state(game)  # Broadcast updated game state


    # -----------------------------------------------------------------------
    async def get_first_player_after_dealer(self, game):
        """
        Returns the first active player (not folded and not all-in) after the dealer.
        """
   
        # Get list of players who haven't folded AND aren't all-in, sorted by position
        active_non_allin_players = await sync_to_async(
            lambda: list(
                game.players.filter(has_folded=False, is_all_in=False).order_by("position")
            ),
            thread_sensitive=True,
        )()
    
        if not active_non_allin_players:
            return None  # No eligible players

        # Find the dealer's position
        dealer_position = game.dealer_position

        # Find the first non-all-in player whose position is greater than dealer_position
        for player in active_non_allin_players:
            if player.position > dealer_position:
                return player.position  # Found the first player after dealer

        # If none found, wrap around to the first in the list
        return active_non_allin_players[0].position
    

   
    # -----------------------------------------------------------------------
    async def is_phase_over(self, game):
        """
        Determines if the phase should end.

        The phase ends when:
        - Only one player remains (they win by default).
        - All non-folded players have called the highest bet or gone all-in.
        - If no bets were made, all players must check before the round ends.
        - In heads-up preflop, ensure the big blind has had a chance to act 
        if the small blind just called the forced bet.
        """

        active_players = await sync_to_async(
            lambda: list(game.players.filter(has_folded=False).order_by("position")),
            thread_sensitive=True,
        )()

        # if no player or 1 player left (winner), stop
        if len(active_players) <= 1:
            return False

        highest_bet = await sync_to_async(
            lambda: max(game.players.values_list("current_bet", flat=True))
        )()

        # If all active players have checked with no bet
        all_players_checked = all(p.has_checked for p in active_players)
        all_players_matched_bet = all(p.current_bet == highest_bet for p in active_players)

        # If it's preflop and the big blind hasn't acted yet
        if len(active_players) == 2 and game.current_phase == "preflop":
            # Identify big blind player
            big_blind_player = None
            for p in active_players:
                if p.is_big_blind:
                    big_blind_player = p
                    break

            if big_blind_player is not None:
                if not big_blind_player.has_acted_this_round and not big_blind_player.is_all_in:
                    return False

        # Normal scenario #1: there's a bet, everyone matched
        if highest_bet > 0 and all_players_matched_bet:
            return True

        # Normal scenario #2: no bet, all checked
        if highest_bet == 0 and all_players_checked:
            return True

        return False


    # -----------------------------------------------------------------------
    async def end_phase(self, game, winner=None):
        """Ends the current phase."""

        print("* END PHASE")

        # Reset each player's current bet & checked status for the next phase/hand
        players = await sync_to_async(
            lambda: list(game.players.all()), thread_sensitive=True
        )()
        for player in players:
            player.current_bet = 0
            player.has_checked = False
            player.has_acted_this_round = False
            await sync_to_async(player.save)()

         # If there's a forced winner (1 player left after folds),
        if winner:
            # Get the current pot amount
            pot = await sync_to_async(lambda: game.get_pot(), thread_sensitive=True)()
            winner.chips += pot
            await sync_to_async(winner.save)()

            username = await sync_to_async(lambda: winner.user.username)()
            await self.broadcast_messages(
                f"🏆 {username} wins the pot of {pot} chips!"
            )

            # Start a fresh hand right away (since this hand ended by fold)
            await self.start_hand(game)
            return
        
        await self.advance_hand_phase(game)

        # if we reach the last round, start a new hand
        if game.current_phase == "showdown":
            await self.start_hand(game)
            return
        # else move current player after the dealer
        else:
            game.current_turn = await self.get_first_player_after_dealer(game)
            await sync_to_async(game.save)()
            await self.broadcast_game_state(game)


    # -----------------------------------------------------------------------
    async def rotate_dealer(self, game):
        """
        Moves the dealer to the next active player.
        """

        print("* ROTATE DEALER")

        # Get all players sorted by their 'position' field
        players = await sync_to_async(
            lambda: list(game.players.order_by("position")), thread_sensitive=True
        )()

        if len(players) < 2:
            return  # No need to rotate if only one player remains

        # If we have never set a dealer before, default to the first seat
        if game.dealer_position is None:
            game.dealer_position = players[0].position
            await sync_to_async(game.save)()
            first_dealer_username = await sync_to_async(
                lambda: players[0].user.username,
                thread_sensitive=True,
            )()
            await self.broadcast_messages(f"🔄 Dealer is set to: {first_dealer_username} (first hand).")
            return

        # Find the current dealer's position in the list
        current_dealer_index = next(
            (i for i, p in enumerate(players) if p.position == game.dealer_position), -1
        )

         # If we can't find them, default to seat 0
        if current_dealer_index == -1:
            new_dealer_index = 0
        else:
            # Move dealer to next seat in a circular fashion
            new_dealer_index = (current_dealer_index + 1) % len(players)

        new_dealer = players[new_dealer_index]
        game.dealer_position = new_dealer.position
        await sync_to_async(game.save)()

        new_dealer_username = await sync_to_async(
            lambda: new_dealer.user.username, thread_sensitive=True
        )()
        await self.broadcast_messages(f"🔄 New dealer : {new_dealer_username}.")


    # -----------------------------------------------------------------------
    async def advance_hand_phase(self, game):
        """
        Moves the game to the next phase (Preflop -> Flop -> Turn -> River -> Showdown).
        Resets necessary game variables and ensures proper transitions.
        """

        print("* ADVANCE HAND PHASE")

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

    # -----------------------------------------------------------------------
    async def move_to_flop(self, game):

        print("* FLOP")

        """Deals 3 community cards for the Flop and burns 1 card."""
        await self.burn_card(game)  # Burn 1 card
        game.community_cards.extend(game.deck[:3])  # Deal 3 cards
        game.deck = game.deck[3:]  # Remove dealt cards from deck
        await sync_to_async(game.save)()
        await self.broadcast_messages("📢 The Flop has been dealt!")

    # -----------------------------------------------------------------------
    async def move_to_turn(self, game):

        print("* TURN")

        """Deals 1 community card for the Turn and burns 1 card."""
        await self.burn_card(game)  # Burn 1 card
        game.community_cards.append(game.deck.pop(0))  # Deal 1 card
        await sync_to_async(game.save)()
        await self.broadcast_messages("📢 The Turn has been dealt!")

    # -----------------------------------------------------------------------
    async def move_to_river(self, game):

        print("* RIVER")

        """Deals 1 community card for the River and burns 1 card."""
        await self.burn_card(game)  # Burn 1 card
        game.community_cards.append(game.deck.pop(0))  # Deal 1 card
        await sync_to_async(game.save)()
        await self.broadcast_messages("📢 The River has been dealt!")

    # -----------------------------------------------------------------------
   
    async def handle_showdown(self, game):
        """Determines winners and distributes the pot."""

        print("* SHOWDOWN")

       
        active_players = await sync_to_async(
            lambda: list(game.players.filter(has_folded=False)), thread_sensitive=True
        )()

        if not active_players:
            return # Safety check

        # Sort players by their total bet
        active_players.sort(key=lambda p: p.total_bet)


        side_pots = []
        previous_bet = 0  # Tracks previous threshold to avoid double-counting


         # Carve out pots
        for i, player in enumerate(active_players):
            current_bet = player.total_bet
            if current_bet > previous_bet:
                # The difference from the previous bet level
                diff = current_bet - previous_bet

                # Everyone from i onward has contributed at least 'diff' more than previous_bet
                pot_size = diff * (len(active_players) - i)

                # The players from i onward are eligible for this pot
                eligible_players = [p.id for p in active_players[i:]]
                side_pots.append({"amount": pot_size, "eligible_ids": eligible_players})

                previous_bet = current_bet

        community_cards = game.community_cards
        player_hands = []
        for player in active_players:
            username = await sync_to_async(lambda: player.user.username)()
            combined_cards = community_cards + player.hole_cards
            score, hand_desc = await sync_to_async(self.evaluate_hand)(combined_cards)
            player_hands.append((score, hand_desc, player))
            print(score, hand_desc, player)

        # Sort from best to worst (lowest treys score = best hand)
        player_hands.sort(key=lambda x: x[0])

        # For each side pot, find best among eligible players
        for pot in side_pots:
            pot_amount = pot["amount"]
            eligible_ids = pot["eligible_ids"]

            # Among the active_players, find those with an ID in eligible_ids
            # Then find the best treys score
            # Note: filter from player_hands because that's already sorted
            in_contest = [(s, d, p) for (s, d, p) in player_hands if p.id in eligible_ids]
            if not in_contest:
                # No one is eligible? (shouldn't happen, but just in case)
                continue

            best_score = in_contest[0][0]
            # find all winners with that score (in case of ties)
            winners = [p for (s, d, p) in in_contest if s == best_score]

            share = pot_amount // len(winners)
            # remainder = pot_amount % len(winners)  # If integer division leftover

            for w in winners:
                w.chips += share
                await sync_to_async(w.save)()
                username = await sync_to_async(lambda: w.user.username)()
                await self.broadcast_messages(
                    f"🏆 {username} wins the hand and {share} chips! "
                )


    # -----------------------------------------------------------------------
    async def transfer_chips_to_profile(self, player):
        """
        Transfers the remaining chips from the game to the player's overall profile chips.
        """

        # Fetch user profile
        user = await sync_to_async(lambda: player.user, thread_sensitive=True)()
        user_profile = await sync_to_async(
            lambda: user.profile, thread_sensitive=True
        )()

        # Transfer chips
        user_profile.chips += player.chips  # Add game chips to total chips

        username = await sync_to_async(
            lambda: player.user.username, thread_sensitive=True
        )()
        await self.broadcast_messages(
            f"🎉 {username} wins the game and receives {player.chips} chips!"
        )

        player.chips = 0  # Reset game chips

        # @TODO 
        # private broadcast to update FE

        # Save changes
        await sync_to_async(user_profile.save)()
        await sync_to_async(player.save)()

        

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
        Deals two hole cards to each player.
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
        start_index = next(
            (i for i, p in enumerate(players) if p.position == dealer_position), -1
        )
        if start_index == -1:
            print("Dealer not found. Cannot proceed with dealing.")
            return

        # Deal cards in two rounds
        for _ in range(2):  # Two hole cards per player
            for i in range(len(players)):
                player = players[
                    (start_index + i + 1) % len(players)
                ]  # Next player after dealer
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
                        await sync_to_async(player.set_hole_cards)(
                            dealt_cards[username]
                        )

        game.deck = deck

        # Save the updated game state
        await sync_to_async(game.save)()

        # Update Front-End
        await self.broadcast_private(game)

    # -----------------------------------------------------------------------
    def evaluate_hand(self, hand):
        """Converts a hand into a numerical score using the Treys Evaluator."""
        evaluator = Evaluator()

        # Convert hand strings (e.g., "Ah", "Kd") into Treys Card objects
        treys_hand = [Card.new(card) for card in hand]

        # find score, rank class and rank class string
        score = evaluator.evaluate([], treys_hand)
        rank_class = evaluator.get_rank_class(score)
        rank_class_string = evaluator.class_to_string(rank_class)

        # Return the score and rank class string
        return score, rank_class_string

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
                    "position": p.position,
                    "game_chips": p.chips,
                    "current_bet": p.current_bet,
                    "total_bet": p.total_bet,
                    "has_folded": p.has_folded,
                    "has_checked": p.has_checked,
                    "has_acted_this_round": p.has_acted_this_round,
                    "is_big_blind": p.is_big_blind,
                    "is_all_in": p.is_all_in,
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
        """Sends updated game state privately to all connected players"""

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
                    "type": "send_personal_game_state",
                    "data": private_data,
                },
            )

    async def send_personal_game_state(self, event):
        """
        Sends the player's own game state (including their hole cards).
        """
        await self.send(text_data=json.dumps(event["data"]))


    # -----------------------------------------------------------------------
    async def send_private_game_state(self, game, user):
        """Sends private game state updates only to the reconnecting player."""

        # Get player's private hole cards
        player = await sync_to_async(lambda: game.players.filter(user=user).first())()

        if not player:
            return  # Player might have left or not be in the game

        hole_cards = player.hole_cards  # Assuming hole_cards is stored in the model
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
                "type": "send_private_update",
                "data": private_message,
            },
        )

    async def send_private_update(self, event):
        """Sends private hole cards update to the reconnecting player."""
        await self.send(text_data=json.dumps(event["data"]))
