import json
import redis
import random
import asyncio
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from asgiref.sync import sync_to_async
from treys import Evaluator, Card
from itertools import combinations
from typing import List, Tuple
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

    async def connect(self) -> None:
        """
        Handles a new WebSocket connection.
 
        - Retrieves game and user information from the connection scope.
        - Adds the connection to both a public game room and a private user group.
        - Sends the player's private game state (e.g., hole cards).
        - Retrieves and sends the most recent messages stored in Redis.
 
        Returns:
            None
        """

        print("### CONNECT")

        self.game_id = self.scope["url_route"]["kwargs"]["game_id"]
        self.room_group_name = f"game_{self.game_id}"
        self.user = self.scope["user"]
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

    # -----------------------------------------------------------------------
    async def disconnect(self, close_code) -> None:
        """
        Handles the WebSocket disconnection.
 
        Removes the user's connection from both the public game group and their private user group.
 
        Args:
            close_code: The WebSocket close code.
 
        Returns:
            None
        """

        print("### DISCONNECT")

        await self.channel_layer.group_discard(
            self.room_group_name, self.channel_name
        )
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

    async def receive(self, text_data: str) -> None:
        """
        Handles messages received from WebSocket clients.

        Parses the incoming JSON message to determine the action type (join, leave, fold, check, call, bet).
        Handles initial player joining before trying to fetch the player.
        Validates player existence before processing further actions.
        Dispatches the action to the appropriate handler function based on the action type.
 
        Args:
            text_data (str): JSON-formatted message sent from the WebSocket client.
 
        Returns:
            None
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
    # hande_join : This is the first "game" function that the user will call
    # to join a table. When there is enough player, this function will
    # call start_hand.
    #

    async def handle_join(self, game:Game, player_username: str) -> None:
        """
        Handles a player joining the game.
 
        This function checks if the player is already seated or has enough chips,
        assigns them a position, deducts their buy-in, and creates a Player instance.
        If the table is full after this join, it starts the game.
 
        Args:
            game (Game): The game instance.
            player_username (str): The username of the joining player.
 
        Returns:
            None
        """
        
        print("* HANDLE JOIN")

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
        await asyncio.gather(
            self.broadcast_game_state(game),
            self.broadcast_private(game),
        )


    # -----------------------------------------------------------------------
    async def handle_leave(self, game:Game, player_username: str) -> None:
        """
        Handles a player leaving the game.
 
        If the game hasn't started, refunds their buy-in. Updates game state accordingly.
        If this player was the dealer or currently active, reassigns those roles.
        Broadcasts updated game state and removes the player.
 
        Args:
            game (Game): The game instance.
            player_username (str): The username of the player leaving the table.
 
        Returns:
            None
        """

        print("* HANDLE LEAVE")

        # Get the player
        player = await sync_to_async(
            lambda: game.players.filter(user__username=player_username).first()
        )()
        if not player:
            return

        # Refund buy-in if game hasn't started
        if game.game_type == "sit_and_go" and game.status == "waiting":
            user_profile = await sync_to_async(lambda: player.user.profile)()
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
            game.status = "finished" if game.status == "active" else "waiting"
            await self.reset_hand(game)
        else:
            if game.dealer_position == player.position:
                # @TODO Currently defaulting to first player, should it be
                # next player instead ?
                game.dealer_position = remaining_players[0].position
            if game.current_turn == player.position:
                await self.next_player(game)

        await sync_to_async(game.save)()

        # Notify all players
        leave_message = f"⚠️ {player_username} has left the table."
        await asyncio.gather(
            self.broadcast_messages(leave_message),
            self.broadcast_game_state(game),
        )



    # -----------------------------------------------------------------------
    async def handle_fold(self, game: Game, player: Player) -> None:
        """
        Handles the fold action from a player.
 
        Marks the player as folded and updates their state. Broadcasts a message to all
        players and triggers the post-action flow to determine the next step in the hand.
 
        Args:
            game (Game): The current game instance.
            player (Player): The player folding their hand.
 
        Returns:
            None
        """

        print("* HANDLE FOLD")

        # Safety Check
        if player.is_all_in or player.has_folded:
            await self.send(text_data=json.dumps({"error": "You cannot fold."}))
            return
        
        username = await sync_to_async(
            lambda: player.user.username, thread_sensitive=True
        )()
        await self.broadcast_messages(f"🔴 {username} folded.")
        
        player.has_folded = True
        player.has_acted_this_round = True
        await sync_to_async(player.save)()

        # Check if only one active player remains
        active_players = await sync_to_async(
            lambda: list(game.players.filter(has_folded=False)), thread_sensitive=True
        )()
        if len(active_players) == 1:
            await self.end_phase(game, winner=active_players[0])
            return

        # If it was the folding player's turn, move to next player
      #  if game.current_turn == player.position:
       #     await self.next_player(game)
        await self.post_action_flow(game)


    # -----------------------------------------------------------------------
    async def handle_check(self, game: Game, player: Player) -> None:
        """
        Handles the check action from a player.
 
        Validates if checking is allowed (no outstanding bets). Updates player status
        and broadcasts the check to all players. Triggers the post-action flow.
 
        Args:
            game (Game): The current game instance.
            player (Player): The player choosing to check.
 
        Returns:
            None
        """

        print("* HANDLE CHECK")

        # Safety Check
        if player.is_all_in or player.has_folded:
            await self.send(text_data=json.dumps({"error": "You cannot check."}))
            return
        
        highest_bet = await sync_to_async(
            lambda: max(game.players.values_list("current_bet", flat=True))
        )()

        difference = highest_bet - player.current_bet

        if difference > 0:
            await self.send(json.dumps({"error": "Cannot check when facing a bet. Must call, fold or bet."}))
            return

        # Mark the player as checked
        player.has_checked = True
        player.has_acted_this_round = True
        await sync_to_async(player.save)()

        # Broadcast
        username = await sync_to_async(
            lambda: player.user.username, thread_sensitive=True
        )()
        await self.broadcast_messages(f"🔵 {username} checked.")

        # Move to the post action flow
        await self.post_action_flow(game)



    # -----------------------------------------------------------------------
    async def handle_call(self, game: Game, player: Player) -> None:
        """
        Handles the call action from a player.
 
        Validates if calling is possible, calculates the amount needed to match the
        current bet, and updates the player's chip and bet status. Handles all-in logic
        and triggers post-action flow.
 
        Args:
            game (Game): The current game instance.
            player (Player): The player calling a bet.
 
        Returns:
            None
        """

        print("* HANDLE CALL")

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
     
        # Broadcast
        username = await sync_to_async(
            lambda: player.user.username, thread_sensitive=True
        )()

        if player.is_all_in:
            await self.broadcast_messages(
                f"🟣 {username} goes ALL-IN with {call_amount} chips!"
            )
        else:
            await self.broadcast_messages(f"🟢 {username} called {call_amount} chips.")

        # Move to the post action flow
        await self.post_action_flow(game)



    # -----------------------------------------------------------------------
    async def handle_bet(self, game: Game, player: Player, amount: int) -> None:
        """
        Handles a player placing a bet.
 
        This method validates the bet amount, updates the player's chip count and current bet,
        determines if the player is going all-in, and broadcasts the action to all players.
        It then continues the hand progression via post-action flow.

        Args:
            game (Game): The game instance the player is betting in.
            player (Player): The player placing the bet.
            amount (int): The number of chips the player wants to bet.
 
        Returns:
            None
        """

        print("* HANDLE BET")
        
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
       

        # Broadcast
        username = await sync_to_async(
            lambda: player.user.username, thread_sensitive=True
        )()

        if player.is_all_in:
            await self.broadcast_messages(
                f"🟣 {username} goes ALL-IN with {amount} chips!"
            )
        else:
            await self.broadcast_messages(
                f"🟡 {username} bet {amount} chips."
            )

      
        # Move to the post action flow
        await self.post_action_flow(game)

  

    # -----------------------------------------------------------------------
    async def post_action_flow(self, game: Game) -> None:
        """
        Handles game progression after each player's action.

        Determines the next steps based on current player states. Ends the hand if only
        one player remains. If players are all-in, runs out the board. Otherwise, decides
        whether to end the current phase or move to the next player.

        Args:
            game (Game): The current game instance.

        Returns:
            None
        """

        print("* POST ACTION FLOW")

        # @TODO
        # This function could probably be optimized...

        active_players = await sync_to_async(lambda: list(game.players.filter(has_folded=False)), thread_sensitive=True)()

        # General all-in logic for 2+ players
        non_folded = [p for p in active_players if not p.has_folded]
        # all_in_players = [p for p in non_folded if p.is_all_in]
        not_all_in_players = [p for p in non_folded if not p.is_all_in]
 
        # If everyone is all-in, auto-run remaining board
        if len(not_all_in_players) == 0:
            while game.current_phase != "showdown":
                await self.find_next_phase(game)
            await self.start_hand(game)
            return
 
        # If only one player is not all-in and they’ve matched the highest total bet
        if len(not_all_in_players) == 1:
            max_bet = max(p.total_bet for p in non_folded)
            remaining = not_all_in_players[0]
            if remaining.total_bet >= max_bet:
               # await self.end_phase(game)
                while game.current_phase != "showdown":
                    await self.find_next_phase(game)
                await self.start_hand(game)
                return

        # Check if the phase is over
        if await self.is_phase_over(game):
            await self.end_phase(game)
        else:
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

    async def start_hand(self, game: Game) -> None:
        """
        Starts a new hand for the game.
 
        This method resets all players statuses, removes players without chips,
        reinitializes the hand (deck, dealer, blinds), and deals new hole cards.
        If only one player remains, transfers chips and ends the game.
        Finally, updates the game state and broadcasts the new hand.
 
        Args:
            game (Game): The current game instance.
 
        Returns:
            None
        """

        print("* START HAND")

        # Fetch active players
        players = await sync_to_async(
            lambda: list(game.players.order_by("position")), thread_sensitive=True
        )()

        # Reset and start the hand!
        await self.reset_hand(game)

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
        if len(players) == 1:
            print("*** Only 1 player left. Ending game and transferring chips.")
            await self.transfer_chips_to_profile(players[0])
            username = await sync_to_async(
                lambda: players[0].user.username, thread_sensitive=True
            )()
            
            await self.handle_leave(game, username)  # Remove player from the game
            await self.broadcast_private(game)
            return


        # Assign dealer
        await self.rotate_dealer(game)

        # Assign Small & Big Blinds
        await self.assign_blinds(game)

        # Create a deck (52 cards)
        suits = ["s", "c", "h", "d"]  # ["♠", "♣", "♥", "♦"]
        ranks = ["2", "3", "4", "5", "6", "7", "8", "9", "T", "J", "Q", "K", "A"]
        deck = [f"{rank}{suit}" for suit in suits for rank in ranks]

        # Shuffle and save the deck
        random.shuffle(deck)
        game.deck = deck

        # Deal Hole Cards
        await self.deal(game)

        # Update Game Status
        game.status = "active"
        
        # Save
        await sync_to_async(game.save)()
 
        # Broadcast
        await asyncio.gather(
            self.broadcast_messages("🚀 Starting a new hand."),
            self.broadcast_game_state(game),
        )


    # -----------------------------------------------------------------------
    async def reset_hand(self, game: Game) -> None:
        """
        Resets the hand state before a new hand begins.
    
        Clears the current turn, side pots, deck, community cards, and sets the current phase to preflop.
        Saves the updated game state.
    
        Args:
            game (Game): The current game instance.
    
        Returns:
            None
        """

        print("* RESET HAND")

        game.current_turn = None
        game.side_pots = []
        game.deck = []
        game.community_cards = []
        game.current_phase = "preflop"

        players = await sync_to_async(lambda: list(game.players.all()), thread_sensitive=True)()
        for player in players:
            player.total_bet = 0
            player.has_folded = False
            player.is_all_in = False
            player.is_small_blind = False
            player.is_big_blind = False
            player.has_checked = False
            player.has_acted_this_round = False
            await sync_to_async(player.save)()
        
        await sync_to_async(game.save)()


    # -----------------------------------------------------------------------
    async def rotate_dealer(self, game: Game) -> None:
        """
        Assigns the dealer position to the next player in order.
 
        If no dealer is set, assigns it to the first player. If a dealer is already set,
        rotates to the next player in circular order. Marks the new dealer and broadcasts the update.
 
        Args:
            game (Game): The current game instance.
 
        Returns:
            None
        """

        print("* ROTATE DEALER")

        # Get all players sorted by their 'position' field
        players = await sync_to_async(
            lambda: list(game.players.order_by("position")), thread_sensitive=True
        )()

        # Safety check
        if len(players) < 2:
            return 

        # If we have never set a dealer before, default to the first seat
        if game.dealer_position is None:
            new_dealer_index = players[0].position
        else :
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
      
        # Reset the is_dealer flag for all players and assign to new dealer
        await sync_to_async(lambda: Player.objects.filter(game=game).update(is_dealer=False))()
        new_dealer.is_dealer = True
        await sync_to_async(new_dealer.save)()

        # Update game
        game.dealer_position = new_dealer.position
        await sync_to_async(game.save)()

         # Broadcast
        new_dealer_username = await sync_to_async(
            lambda: new_dealer.user.username, thread_sensitive=True
        )()
        await self.broadcast_messages(f"⭐️ New dealer : {new_dealer_username}.")


    # -----------------------------------------------------------------------
    async def assign_blinds(self, game: Game) -> None:
        """
        Assigns small and big blinds to players.
 
        For heads-up, the dealer is the small blind. For 3+ players, assigns blinds clockwise.
        Deducts the blinds from each player's chips and updates their states. 
        Also sets the player who acts first preflop and broadcasts the blind information.
 
        Args:
            game (Game): The game instance in progress.
 
        Returns:
            None
        """

        print("* ASSIGN BLINDS")

        # Fetch the sorted player list
        players = await sync_to_async(
            lambda: list(game.players.order_by("position")),
            thread_sensitive=True,
        )()

        if len(players) < 2:
            return # Safety check

        # Find the dealer index in the players list
        dealer_index = next(
            (i for i, p in enumerate(players) if p.position == game.dealer_position),
            -1
        )
        if dealer_index == -1:
            return # Safety check

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
        small_blind_player.is_small_blind = True
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

        await self.broadcast_messages(f"✨ {sb_username} post SMALL blind ({small_blind} chips).")
        await self.broadcast_messages(f"✨ {bb_username} post BIG blind ({big_blind} chips).")

        # Save
        await sync_to_async(game.save)()

    # -----------------------------------------------------------------------
    # async def next_player(self, game: Game) -> None:
    #     """
    #     Advances the turn to the next active player who has not folded and is not all-in.

    #     If all active players are all-in, the function skips to the showdown by auto-advancing
    #     through remaining phases and starting a new hand. Otherwise, it rotates the turn to 
    #     the next eligible player in table order.

    #     Args:
    #         game (Game): The current game instance.

    #     Returns:
    #         None
    #     """

    #     print("* NEXT PLAYER")

    #     active_players = await sync_to_async(
    #         lambda: list(game.players.filter(has_folded=False).order_by("position")),
    #         thread_sensitive=True,
    #     )()

    #     # Safety check: No active players left
    #     if not active_players:
    #         return

    #     # Check if all active players are all-in
    #     all_all_in = all(p.is_all_in for p in active_players)
        
    #     if all_all_in:
    #         print("* ALL PLAYERS ALL-IN -> ADVANCING ROUND")

    #         #
    #         # @TODO - Need to test this
    #         # Should be into a function, also used by the post action flow
    #         while game.current_phase != "showdown":
    #             await self.advance_hand_phase(game)
    #         await self.start_hand(game)   
    #         return
        
    #         # Old code :
    #         # await self.advance_hand_phase(game)
    #         # return

    #     # Find the index of the current turn
    #     current_index = next((i for i, p in enumerate(active_players) if p.position == game.current_turn),-1,)

    #     # Determine the next player who is not all-in
    #     for p in range(len(active_players)):  # Loop ensures we don't get stuck in an infinite loop
    #         if current_index == -1:
    #             game.current_turn = active_players[0].position  # Default to first active player
    #         else:
    #             current_index = (current_index + 1) % len(active_players)
    #             game.current_turn = active_players[current_index].position

    #         # If the chosen player is not all-in, break loop
    #         if not active_players[current_index].is_all_in:
    #             break

    #     # Save
    #     await sync_to_async(game.save)()

    #     # Broadcast
    #     await self.broadcast_game_state(game) 


    # # -----------------------------------------------------------------------
    # async def get_first_player_after_dealer(self, game: Game) -> Player:
    #     """
    #     Returns the position of the first eligible player to act after the dealer.
 
    #     The eligible player must not have folded and must not be all-in.
    #     The search wraps around if no player is found after the dealer's position.
 
    #     Args:
    #         game (Game): The current game instance.
 
    #     Returns:
    #         int: The position of the first eligible player after the dealer,
    #              or None if no such player exists.
    #     """

    #     # Get list of players who haven't folded AND aren't all-in, sorted by position
    #     active_non_allin_players = await sync_to_async(
    #         lambda: list(
    #             game.players.filter(has_folded=False, is_all_in=False).order_by("position")
    #         ),
    #         thread_sensitive=True,
    #     )()
    
    #     if not active_non_allin_players:
    #         return None  # No eligible players

    #     # Find the dealer's position
    #     dealer_position = game.dealer_position

    #     # Find the first non-all-in player whose position is greater than dealer_position
    #     for player in active_non_allin_players:
    #         if player.position > dealer_position:
    #             return player.position  # Found the first player after dealer

    #     # If none found, wrap around to the first in the list
    #     return active_non_allin_players[0].position
    

    
    async def next_player(self, game: Game, start_position: int = None) -> None:
        """
        Advances the turn to the next active player who has not folded and is not all-in.
        Optionally starts the search from a given position (e.g., dealer + 1), replacing
        the need for a separate 'get_first_player_after_dealer' function.

        If all active players are all-in, the function skips to the showdown by auto-advancing
        through remaining phases and starting a new hand. Otherwise, it rotates the turn to 
        the next eligible player in table order.

        Args:
            game (Game): The current game instance.
            start_position (int, optional): The position to begin searching from. Defaults to current_turn.

        Returns:
            None
        """
        print("* NEXT PLAYER")

        active_players = await sync_to_async(
            lambda: list(game.players.filter(has_folded=False).order_by("position")),
            thread_sensitive=True,
        )()

        if not active_players:
            return

        # If all active players are all-in, move directly to showdown
        if all(p.is_all_in for p in active_players):
            print("* ALL PLAYERS ALL-IN -> ADVANCING ROUND")
            while game.current_phase != "showdown":
                await self.find_next_phase(game)
            await self.start_hand(game)
            return

        # Determine the position to start from
        if start_position is not None:
            start_pos = start_position
        elif game.current_turn is not None:
            start_pos = game.current_turn
        else:
            # If current_turn is not yet set (e.g., first action on a new phase), start after dealer
            dealer_position = game.dealer_position
            sorted_positions = [p.position for p in active_players]
            start_pos = next((p for p in sorted_positions if p > dealer_position), sorted_positions[0])
        current_index = next((i for i, p in enumerate(active_players) if p.position == start_pos), -1)

        for _ in range(len(active_players)):
            current_index = (current_index + 1) % len(active_players)
            candidate = active_players[current_index]
            if not candidate.is_all_in:
                game.current_turn = candidate.position
                break

        await sync_to_async(game.save)()
        await self.broadcast_game_state(game)
   
    #
    #
    #
    #
    #
    #
    #
    # =======================================================================
    # GAME PHASES HANDLING
    # =======================================================================

    # -----------------------------------------------------------------------
    async def is_phase_over(self, game: Game) -> bool:
        """
        Determines if the current betting phase should end.
 
        The phase ends under the following conditions:
        - All non-folded players have called the highest bet or gone all-in.
        - All active players have checked if no bets were placed.
        - In preflop phase, ensures the big blind has acted if the small blind just called.
        - The phase does not end if fewer than two players remain.
 
        Args:
            game (Game): The current game instance.
 
        Returns:
            bool: True if the phase should end, False otherwise.
        """

        print("* CHECK IF PHASE IS OVER")

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
        all_players_checked = all(p.has_checked for p in active_players if not p.has_folded)
        all_players_matched_bet = all(p.current_bet == highest_bet for p in active_players if not p.has_folded)

        print("** == PHASE CHECK START ==")
        print(f"** Phase: {game.current_phase}")
        print(f"** Highest bet: {highest_bet}")
        for p in active_players:
            print(f"** Player {p.position} - Folded: {p.has_folded}, Bet: {p.current_bet}, Acted: {p.has_acted_this_round}")
        print(f"** All matched bet: {all_players_matched_bet}")
        print(f"** All checked: {all_players_checked}")
        print("** == PHASE CHECK END ==")

        # If it's preflop and the big blind hasn't acted yet
        if game.current_phase == "preflop":
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
            phase_over = True
        # Normal scenario #2: no bet, all checked
        elif highest_bet == 0 and all_players_checked:
            phase_over = True
        else:
            phase_over = False

        # print("* Checking if phase is over...")
        # for p in active_players:
        #     print(f"Player {p.position}: bet={p.current_bet}, acted={p.has_acted_this_round}, folded={p.has_folded}, all_in={p.is_all_in}")
        # print(f"Highest bet: {highest_bet}")
        # print(f"All players checked: {all_players_checked}")
        # print(f"All players matched bet: {all_players_matched_bet}")
        return phase_over


    # -----------------------------------------------------------------------
    async def end_phase(self, game: Game, winner=None) -> None:
        """
        Ends the current betting phase and prepares for the next phase or starts a new hand.
 
        If a winner is specified (only one player left), awards the pot to that player
        and begins a new hand. Otherwise, advances to the next phase or starts a new
        hand if the showdown is reached. Resets player statuses accordingly.
 
        Args:
            game (Game): The current game instance.
            winner (Player, optional): The player who wins by default due to all others folding.
 
        Returns:
            None
        """

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
                f"🏆 {username} is the last player and wins the pot of {pot} chips!"
            )

            # Start a fresh hand right away (since this hand ended by fold)
            await self.start_hand(game)
            return
        
        await self.find_next_phase(game)

        # if we reach the last round, start a new hand
        if game.current_phase == "showdown":
            await self.start_hand(game)
            return
        
        # else move current player after the dealer
        else:
           # game.current_turn = await self.get_first_player_after_dealer(game)
            await self.next_player(game, game.dealer_position)
          
          #  game.current_turn = await self.next_player(game, game.dealer_position)
          #  await sync_to_async(game.save)()
          #  await self.broadcast_game_state(game) 

   
    # -----------------------------------------------------------------------
    async def find_next_phase(self, game: Game) -> None:
        """
        Find the game to the next phase in the hand.
        
        This function updates the current game phase in the following order:
        Preflop → Flop → Turn → River → Showdown. At each stage, the appropriate
        community cards are dealt and the game state is updated accordingly.
        
        Args:
            game (Game): The current game instance.
        
        Returns:
            None
        """

        print("* FIND NEXT PHASE")

        if game.current_phase == "preflop":
            game.current_phase = "flop"
            await self.move_to("flop",game)
        elif game.current_phase == "flop":
            game.current_phase = "turn"
            await self.move_to("turn",game)
        elif game.current_phase == "turn":
            game.current_phase = "river"
            await self.move_to("river",game)
        elif game.current_phase == "river":
            game.current_phase = "showdown"
            await self.handle_showdown(game)

        await sync_to_async(game.save)()



    # -----------------------------------------------------------------------
    async def move_to(self, phase: str, game):
        """
        Advances the game to the specified phase (flop, turn, or river),
        burns a card, deals community cards, and updates game state.
        
        Parameters:
        - phase (str): One of "flop", "turn", or "river"
        - game: Game instance to modify
        """

        print("* MOVE TO NEXT PHASE")

        phase = phase.lower()

        if phase not in {"flop", "turn", "river"}:
            return #Safety check

        # Burn one card
        await self.burn_card(game)

        # Determine how many cards to deal
        cards_to_deal = 3 if phase == "flop" else 1
        dealt_cards = game.deck[:cards_to_deal]
        game.community_cards.extend(dealt_cards)
        game.deck = game.deck[cards_to_deal:]

        # Save
        await sync_to_async(game.save)()

        # Broadcast
        cards_pretty = await sync_to_async(self.convert_treys_str_int_pretty)(game.community_cards)
        phase_label = f"📡 {phase.capitalize()} : {cards_pretty}"
        await self.broadcast_messages(phase_label)


    # -----------------------------------------------------------------------
    async def handle_showdown(self, game: Game) -> None:
        """
        Determines winners and distributes the pot among eligible players.
 
        Handles the showdown by evaluating each remaining player's best 5-card hand,
        constructing side pots based on total bets, and awarding chips to the winners
        of each side pot. Uses Treys hand evaluator for scoring. Players with equal
        best hands split the pot equally.
 
        Args:
            game (Game): The current game instance.
 
        Returns:
            None
        """

        print("* MOVE TO SHOWDOWN")

        active_players = await sync_to_async(
            lambda: list(game.players.filter(has_folded=False)), thread_sensitive=True
        )()

        if not active_players:
            return # Safety check

        # Sort players by total bet (all players, including folded)
        all_players = await sync_to_async(lambda: list(game.players.all()), thread_sensitive=True)()
        all_players.sort(key=lambda p: p.total_bet)
 
        # Build side pots including folded players' contributions
        side_pots = []
        previous_bet = 0
        for i, player in enumerate(all_players):
            current_bet = player.total_bet
            if current_bet > previous_bet:
                diff = current_bet - previous_bet
                pot_size = diff * (len(all_players) - i)
                eligible_players = [p.id for p in all_players[i:] if not p.has_folded]
                side_pots.append({"amount": pot_size, "eligible_ids": eligible_players})
                previous_bet = current_bet

        print(side_pots)
        
        # Evaluate each player's best 5-card hand
        player_hands = []
        for player in active_players:
            username = await sync_to_async(lambda: player.user.username)()
            combined_cards = game.community_cards + player.hole_cards
            score, rank, best_5_ints = await sync_to_async(self.find_best_five_cards)(combined_cards)
            player_hands.append((score, rank, best_5_ints, player))

        # Sort from best to worst (lowest treys score = best hand)
        player_hands.sort(key=lambda x: x[0])

        # Distribute side pots
        for pot in side_pots:
            pot_amount = pot["amount"]
            eligible_ids = pot["eligible_ids"]

            # Filter out the players who are eligible for this pot
            in_contest = [(s, d, b5, p) for (s, d, b5, p) in player_hands if p.id in eligible_ids]
            if not in_contest:
                continue

            best_score = in_contest[0][0]
            winning_records = [(s, d, b5, plyr) for (s, d, b5, plyr) in in_contest if s == best_score]

            share = pot_amount // len(winning_records)
          
            for (win_score, win_rank, win_5, win_player) in winning_records:
                win_player.chips += share
                await sync_to_async(win_player.save)()
                
                username = await sync_to_async(lambda: win_player.user.username)()
                pretty_str = Card.ints_to_pretty_str(win_5)
                
                # Broadcast
                await self.broadcast_messages(
                    f"🏆 {username} wins {share} with {pretty_str.replace(",", "")} ({win_rank})"
                )

        

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
    async def burn_card(self, game: Game) -> None:
        """
        Burns (removes) the top card from the deck.
    
        This is used in Texas Hold'em to discard a card before dealing
        community cards (Flop, Turn, River) to prevent cheating and ensure fairness.
    
        Args:
            game (Game): The current game instance.
    
        Returns:
            None
        """
        if game.deck:
            game.deck.pop(0)

    # -----------------------------------------------------------------------
    async def deal(self, game: Game) -> None:
        """
        Deals two hole cards to each player in proper order.
 
        Distributes one card at a time to each player, twice around the table,
        starting from the left of the dealer. Cards are stored in the database and
        broadcasted privately to each player.
 
        Args:
            game (Game): The current game instance.
 
        Returns:
            None
        """

        # Init
        dealt_cards = {}
        deck = game.deck

        # Fetch players in correct order
        players = await sync_to_async(
            lambda: list(game.players.order_by("position")), thread_sensitive=True
        )()

        # Safety check
        if not players:
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

        # Save
        await sync_to_async(game.save)()

        # Update Front-End
        await self.broadcast_private(game)


    # -----------------------------------------------------------------------
    def find_best_five_cards(self, seven_card_strings: List[str]) -> Tuple[int, str, Tuple[int, int, int, int, int]]:
 
        """
        Determines the best 5-card hand from a 7-card combination.
 
        Evaluates all 5-card combinations from the given 7 cards (hole + community),
        and returns the best score, hand rank, and the best 5-card hand as Treys integers.
 
        Args:
            seven_card_strings (List[str]): A list of 7 card strings, e.g., ['Ah', 'Kd', 'Qs', 'Jh', 'Tc', '9c', '8d'].
 
        Returns:
            Tuple[int, str, Tuple[int, int, int, int, int]]:
                - best_score (int): Treys score for the best hand (lower is better).
                - best_rank (str): Human-readable classification of the hand (e.g., "Straight", "Flush").
                - best_five_ints (Tuple[int, ...]): Tuple of Treys card integers representing the best hand.
        """
        evaluator = Evaluator()

        # 1. Convert to Treys "card int" objects
        all_seven_cards = [Card.new(c) for c in seven_card_strings]

        best_score = 7642 # 7642 distinctly ranked hands in poker.
        best_five_cards = None

        # 2. Enumerate all 5-card combos
        for combo in combinations(all_seven_cards, 5):
            score = evaluator.evaluate([], list(combo))
            if score <= best_score:
                best_score = score
                best_five_cards = combo

        # 3. Determine rank class
        rank_class = evaluator.get_rank_class(best_score)
        best_rank = evaluator.class_to_string(rank_class)

        # best_five_cards are already "card ints"
        return best_score, best_rank, best_five_cards

   

    # -----------------------------------------------------------------------
    def convert_treys_str_int_pretty(self, str_cards: List[str]) -> str:

        """  
        Converts a list of card strings to Treys pretty string format.

        This function converts string representations of cards (e.g., "Ah", "Kd")
        into Treys card integers, then returns a human-readable string using Treys'
        pretty string format.

        Args:
            str_cards (List[str]): List of card strings to convert.

        Returns:
            str: Treys-formatted pretty string of cards. 
        """
        cards_ints = [Card.new(c) for c in str_cards]
        cards_str = Card.ints_to_pretty_str(cards_ints)
        return cards_str.replace(",","")
    

    # -----------------------------------------------------------------------
    async def transfer_chips_to_profile(self, player: Player) -> None:
        """
        Transfers remaining in-game chips from a player to their profile.

        This function is typically used when a game ends and a player has won.
        It adds the player's remaining chips in the game to their profile's chip count,
        resets their in-game chip count to 0, saves both objects, and broadcasts a win message.

        Args:
            player (Player): The player whose chips are being transferred.

        Returns:
            None
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
    #
    # =======================================================================
    # WEBSOCKET BROADCASTING TO PLAYERS
    # =======================================================================

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
                "type": "send_action_message",
                "messages": [message], 
            },
        )

    async def send_action_message(self, event):
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



    # -----------------------------------------------------------------------
    async def broadcast_game_state(self, game:Game) -> None:
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
        """
        Sends the game state payload to the frontend.
 
        Triggered by `broadcast_game_state` to deliver real-time updates to the client.
 
        Args:
            event (dict): Contains the game state data.
 
        Returns:
            None
        """
        await self.send(text_data=json.dumps(event["data"]))


    # -----------------------------------------------------------------------
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
                    "type": "send_personal_game_state",
                    "data": private_data,
                },
            )

    async def send_personal_game_state(self, event):
        """
        Sends a player's private game data (e.g., hole cards).
 
        Triggered by `broadcast_private` to send only to one player's WebSocket channel.
 
        Args:
            event (dict): Contains private data like hole cards and chip count.
 
        Returns:
            None
        """
        await self.send(text_data=json.dumps(event["data"]))


    # -----------------------------------------------------------------------
    async def send_private_game_state(self, game: Game, user: User) -> None:
        """
        Sends private game state to a reconnecting player.
 
        When a player reconnects to a game, this sends their private game info
        (hole cards and chip count) only to them.
 
        Args:
            game (Game): The current game instance.
            user (User): The reconnecting user.
 
        Returns:
            None
        """

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
        """
        Sends private hole cards update to the reconnecting player.
 
        Triggered by `send_private_game_state`.
 
        Args:
            event (dict): Contains the private data payload.
 
        Returns:
            None
        """
        await self.send(text_data=json.dumps(event["data"]))
