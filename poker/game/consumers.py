import json
import redis
import random
import asyncio
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from django.db import transaction
from asgiref.sync import sync_to_async
from treys import Card
# from itertools import combinations
# from typing import List, Tuple
from collections import defaultdict
from .models import Game, Player, User
from .utils import get_next_phase, find_best_five_cards, convert_treys_str_int_pretty, can_user_do_action, create_deck

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

    # async def handle_join(self, game:Game, player_username: str) -> None:
    #     """
    #     Handles a player joining the game.
 
    #     This function checks if the player is already seated or has enough chips,
    #     assigns them a position, deducts their buy-in, and creates a Player instance.
    #     If the table is full after this join, it starts the game.
 
    #     Args:
    #         game (Game): The game instance.
    #         player_username (str): The username of the joining player.
 
    #     Returns:
    #         None
    #     """
        
    #     print("* HANDLE JOIN")

    #     user = await sync_to_async(User.objects.get)(username=player_username)
    #     user_profile = await sync_to_async(lambda: user.profile)()

    #     # Check if already sitting
    #     existing_player = await sync_to_async(
    #         lambda: game.players.filter(user=user).exists()
    #     )()
    #     if existing_player:
    #         await self.send(
    #             text_data=json.dumps({"error": "You are already playing on this table"})
    #         )
    #         return

    #     # Find the lowest available position
    #     taken_positions = await sync_to_async(
    #         lambda: list(game.players.values_list("position", flat=True))
    #     )()
    #     available_positions = [
    #         pos for pos in range(game.max_players) if pos not in taken_positions
    #     ]
    #     if not available_positions:
    #         await self.send(text_data=json.dumps({"error": "Table is full!"}))
    #         return  # No available positions


    #     # Ensure the player has enough chips
    #     if user_profile.chips < game.buy_in:
    #         await self.send(
    #             text_data=json.dumps({"error": "Not enough chips to join!"})
    #         )
    #         return

    #     # Deduct buy-in from player’s total chips
    #     await sync_to_async(
    #         lambda: setattr(user_profile, "chips", user_profile.chips - game.buy_in)
    #     )()
    #     await sync_to_async(user_profile.save)()

    #     # Create new Player
    #     await sync_to_async(Player.objects.create)(
    #         game=game, user=user, position=min(available_positions), chips=game.buy_in
    #     )

    #     # Notify all players : new user join table
    #     join_message = f" 🪑 {player_username} has join the table."
    #     await self.broadcast_messages(join_message)

    #     # Check if we should start the game
    #     # currently only working for sit_and_go games
    #     player_count = await sync_to_async(lambda: game.players.count())()
    #     if game.game_type == "sit_and_go" and player_count == game.max_players:
    #         await self.start_hand(game)
    #         return

    #     # Notify all players about game state
    #     await asyncio.gather(
    #         self.broadcast_game_state(game),
    #         self.broadcast_private(game),
    #     )

    async def handle_join(self, game: Game, player_username: str) -> None:
        """
        Handles a player joining the game, with transaction safety and better structure.
        """

        print("* HANDLE JOIN")

        try:
            user = await sync_to_async(User.objects.get)(username=player_username)
        except User.DoesNotExist:
            await self.send(text_data=json.dumps({"error": "User not found"}))
            return

        try:
            await self.join_game_transaction(game.id, user.id)
        except Exception as e:
            await self.send(text_data=json.dumps({"error": str(e)}))
            return

        await self.broadcast_messages(f"🪑 {player_username} has joined the table.")

        # Check if game should start
        player_count = await sync_to_async(lambda: game.players.count())()
        if game.game_type == "sit_and_go" and player_count == game.max_players:
            await self.start_hand(game)
        else:
            await asyncio.gather(
                self.broadcast_game_state(game),
                self.broadcast_private(game),
            )

    @sync_to_async
    @transaction.atomic
    def join_game_transaction(self, game_id, user_id):
        game = Game.objects.select_for_update().get(id=game_id)
        user = User.objects.select_related("profile").get(id=user_id)
        profile = user.profile

        if game.players.filter(user=user).exists():
            raise Exception("You're already seated at this table")

        taken_positions = list(game.players.values_list("position", flat=True))
        available_positions = [pos for pos in range(game.max_players) if pos not in taken_positions]

        if not available_positions:
            raise Exception("Table is full")

        if profile.chips < game.buy_in:
            raise Exception("Not enough chips")

        profile.chips -= game.buy_in
        profile.save()

        Player.objects.create(
            game=game,
            user=user,
            position=min(available_positions),
            chips=game.buy_in
        )

    # -----------------------------------------------------------------------
    # async def handle_leave(self, game:Game, player_username: str) -> None:
    #     """
    #     Handles a player leaving the game.
 
    #     If the game hasn't started, refunds their buy-in. Updates game state accordingly.
    #     If this player was the dealer or currently active, reassigns those roles.
    #     Broadcasts updated game state and removes the player.
 
    #     Args:
    #         game (Game): The game instance.
    #         player_username (str): The username of the player leaving the table.
 
    #     Returns:
    #         None
    #     """

    #     print("* HANDLE LEAVE")

    #     # Get the player
    #     player = await sync_to_async(
    #         lambda: game.players.filter(user__username=player_username).first()
    #     )()
    #     if not player:
    #         return

    #     # Refund buy-in if game hasn't started
    #     if game.game_type == "sit_and_go" and game.status == "waiting":
    #         user_profile = await sync_to_async(lambda: player.user.profile)()
    #         await sync_to_async(
    #             lambda: setattr(user_profile, "chips", user_profile.chips + game.buy_in)
    #         )()
    #         await sync_to_async(user_profile.save)()
    #         await self.broadcast_private(game)

    #     # Remove player from the game
    #     await sync_to_async(player.delete)()

    #     # Renumber the positions of the remaining players.
    #     remaining_players = await sync_to_async(
    #         lambda: list(game.players.order_by("position"))
    #     )()

    #     # Reassign positions sequentially starting at 0.
    #     for new_position, p in enumerate(remaining_players):
    #         p.position = new_position
    #         await sync_to_async(p.save)()

    #     # Update dealer_position and current_turn if needed.
    #     # For example, if the leaving player was the dealer or the current turn,
    #     # we reassign these roles to the first player.
    #     if len(remaining_players) < 2:
    #         game.status = "finished" if game.status == "active" else "waiting"
    #         await self.reset_hand(game)
    #     else:
    #         if game.dealer_position == player.position:
    #             game.dealer_position = remaining_players[0].position
    #         if game.current_turn == player.position:
    #            game.current_turn = remaining_players[0].position

    #     await sync_to_async(game.save)()
        
    #     # After updating positions and roles, check if the current phase is complete.
    #     if await self.is_phase_over(game):
    #         await self.end_phase(game)
    #         return

    #     # Notify all players
    #     leave_message = f"⚠️ {player_username} has left the table."
    #     await asyncio.gather(
    #         self.broadcast_messages(leave_message),
    #         self.broadcast_game_state(game),
    #     )



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
    
        game = await self.leave_game_transaction(game.id, player_username)
        # game = await sync_to_async(Game.objects.get)(id=game.id) #re-fetch after transaction
 
        if game.status == "finished":
            await self.reset_hand(game)
        elif game.status == "active" and await self.is_phase_over(game):
            await self.end_phase(game)
            return
    
        leave_message = f"⚠️ {player_username} has left the table."
        await asyncio.gather(
            self.broadcast_messages(leave_message),
            self.broadcast_game_state(game),
            self.send_private_to_user(self.user),
        )

    @sync_to_async
    @transaction.atomic
    def leave_game_transaction(self, game_id, username):
        game = Game.objects.select_for_update().get(id=game_id)
        player = game.players.select_related("user__profile").filter(user__username=username).first()

        if not player:
            raise Exception("Player not found")

        player_position = player.position

        # Refund buy-in if game hasn't started
        if game.game_type == "sit_and_go" and game.status == "waiting":
            profile = player.user.profile
            profile.chips += game.buy_in
            profile.save()

        # Delete the player
        player.delete()

        # Renumber positions
        remaining_players = list(game.players.order_by("position"))
        for new_pos, p in enumerate(remaining_players):
            p.position = new_pos
            p.save()

        # Update game state if necessary
        if len(remaining_players) < 2:
            game.status = "finished" if game.status == "active" else "waiting"
        else:
            if game.dealer_position == player_position:
                game.dealer_position = remaining_players[0].position
            if game.current_turn == player_position:
                game.current_turn = remaining_players[0].position

        game.save()
        return game
    



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

        can_check = await sync_to_async(
            lambda: can_user_do_action(game, player, "check")
        )()

        if can_check == True :
            print("YES CAN CHECK")
            # Mark the player as checked
            player.has_checked = True
            player.has_acted_this_round = True
            await sync_to_async(player.save)()

            # Broadcast
            username = await sync_to_async(
                lambda: player.user.username, thread_sensitive=True
            )()
            await self.broadcast_messages(f"🔵 {username} checked.")
        
        else :
            await self.send(json.dumps({"error": "Cannot check"}))
            return

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

        active_players = await sync_to_async(lambda: list(game.players.filter(has_folded=False)), thread_sensitive=True)()

        # General all-in logic for 2+ players
        non_folded = [p for p in active_players if not p.has_folded]
        not_all_in_players = [p for p in non_folded if not p.is_all_in]
 
        # If everyone is all-in, auto-run remaining board
        if len(not_all_in_players) == 0:
            while game.current_phase != "showdown":
                await self.goto_next_phase(game)
            await self.start_hand(game)
            return
 
        # If only one player is not all-in and they’ve matched the highest total bet
        if len(not_all_in_players) == 1:
            max_bet = max(p.total_bet for p in non_folded)
            remaining = not_all_in_players[0]
            if remaining.total_bet >= max_bet:
                while game.current_phase != "showdown":
                    await self.goto_next_phase(game)
                await self.start_hand(game)
                return

        # Check if the phase is over
        if await self.is_phase_over(game):
            await self.end_phase(game)
        else:
            print("-----------------")
            print(game.current_turn)
            print("-----------------")
            await self.next_player(game, game.current_turn)


   

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
            await self.transfer_chips_to_profile(game, players[0])
            username = await sync_to_async(
                lambda: players[0].user.username, thread_sensitive=True
            )()
            await self.broadcast_private(game)
            await self.handle_leave(game, username)  # Remove player from the game
            return


        # Assign dealer
        await self.rotate_dealer(game)

        # Assign Small & Big Blinds
        await self.assign_blinds(game)

        # Create a deck (52 cards)
        deck = await sync_to_async(
            lambda: create_deck()
        )()

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
        # new_dealer_username = await sync_to_async(
        #     lambda: new_dealer.user.username, thread_sensitive=True
        # )()
        # await self.broadcast_messages(f"⭐️ New dealer : {new_dealer_username}.")


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

        # Save
        await sync_to_async(game.save)()



    # async def next_player(self, game: Game, start_position: int) -> int:
    #     """
    #     Advances the turn to the next active player (i.e. one who has not folded and is not all-in)
    #     based on the provided start_position (a seat number). It uses a circular ordering of all players
    #     who are eligible to act. If the full circle is completed (i.e. the candidate to act
    #     would be the same as the provided start_position), it returns None to signal that all players have acted,
    #     and the betting phase should end.
        
    #     In an ongoing betting round (highest_bet > 0), it selects the first player in circular order
    #     who either hasn't acted this round or hasn't matched the highest bet.
    #     In a new betting round (highest_bet == 0), it selects the first active player in the circular order.
        
    #     Args:
    #         game (Game): The current game instance.
    #         start_position (int): The seat number of the last acting player.
        
    #     Returns:
    #         int: The seat number of the next active player, or None if a full circle has been completed.
    #     """
    #     print("* NEXT PLAYER")
    #     print("*** Provided start_position (seat):", start_position)

    #     # Get active players: those who have not folded and are not all-in.
    #     active_players = await sync_to_async(
    #         lambda: list(game.players.filter(has_folded=False, is_all_in=False).order_by("position"))
    #     )()
    #     if not active_players:
    #         print("*** No active players; advancing to showdown.")
    #         while game.current_phase != "showdown":
    #             await self.goto_next_phase(game)
    #         await self.start_hand(game)
    #         return None

    #     # Determine the highest current bet among active players.
    #     highest_bet = max(p.current_bet for p in active_players)
    #     print("*** Highest bet among active players:", highest_bet)

    #     # Build the circular order of active players based on their seat numbers.
    #     # That is, all players with a seat number greater than start_position, followed by those with <= start_position.
    #     players_after = [p for p in active_players if p.position > start_position]
    #     players_before_or_equal = [p for p in active_players if p.position <= start_position]
    #     circular_order = players_after + players_before_or_equal
    #     print("*** Circular order of active players (by seat):", [p.position for p in circular_order])

    #     candidate = None

    #     # Evaluate who should act next
    #     for p in circular_order:
    #         if highest_bet == 0:
    #             candidate = p
    #             break
    #         if p.current_bet < highest_bet or not p.has_acted_this_round:
    #             candidate = p
    #             break

    #     # No one to act, round is over
    #     if candidate is None:
    #         print("*** No eligible next player found; betting round over.")
    #         return None

    #     # Assign turn to the candidate
    #     print("*** Next candidate seat:", candidate.position)
    #     game.current_turn = candidate.position
    #     await sync_to_async(game.save)()
    #     await self.broadcast_game_state(game)

    #     return game.current_turn

    async def next_player(self, game: Game, start_position: int) -> int:
        """
        Determines and sets the next player to act based on current game state.
        Skips players who are folded or all-in. If no player needs to act, ends the betting round.

        Args:
            game (Game): The current game instance.
            start_position (int): The seat number of the last acting player.

        Returns:
            int: The seat number of the next player, or None if the betting round is complete.
        """
        print("* NEXT PLAYER")
        print("*** Provided start_position (seat):", start_position)

        # Fetch all players who have not folded
        all_active_players = await sync_to_async(
            lambda: list(game.players.filter(has_folded=False).order_by("position")),
            thread_sensitive=True
        )()

        # Split into those who can act (not all-in) and all for highest_bet calculation
        eligible_players = [p for p in all_active_players if not p.is_all_in]

        if not eligible_players:
            print("*** No eligible players found. Advancing to showdown.")
            while game.current_phase != "showdown":
                await self.goto_next_phase(game)
            await self.start_hand(game)
            return None

        # Compute highest bet among all (including all-ins) to fairly assess who needs to act
        highest_bet = max(p.current_bet for p in all_active_players)
        print("*** Highest bet among all active players:", highest_bet)

        # Build circular player order after start_position
        after = [p for p in eligible_players if p.position > start_position]
        before = [p for p in eligible_players if p.position <= start_position]
        circular_order = after + before

        print("*** Circular order of eligible players (by seat):", [p.position for p in circular_order])

        candidate = None
        for p in circular_order:
            if p.current_bet < highest_bet or not p.has_acted_this_round:
                candidate = p
                break

        if candidate is None:
            print("*** No player needs to act. Betting round is complete.")
            return None

        print("*** Next candidate seat:", candidate.position)
        game.current_turn = candidate.position
        await sync_to_async(game.save)()
        await self.broadcast_game_state(game)
        return candidate.position




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

        print("* Checking if phase is over...")
        for p in active_players:
            print(f"Player {p.position}: bet={p.current_bet}, acted={p.has_acted_this_round}, folded={p.has_folded}, all_in={p.is_all_in}")
        print(f"Highest bet: {highest_bet}")
        print(f"All players checked: {all_players_checked}")
        print(f"All players matched bet: {all_players_matched_bet}")
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
        
        await self.goto_next_phase(game)

        # if we reach the last round, start a new hand
        if game.current_phase == "showdown":
            await self.start_hand(game)
            return
        
        # else move current player after the dealer
        else:
            await self.next_player(game, game.dealer_position)


   
    # -----------------------------------------------------------------------
    async def goto_next_phase(self, game: Game) -> None:
        """
        Find and move to the next phase. At each stage, the appropriate
        community cards are dealt and the game state is updated accordingly.
        
        Args:
            game (Game): The current game instance.
        
        Returns:
            None
        """

        print("* GOTO NEXT PHASE")
        next_phase = get_next_phase(game.current_phase)
        game.current_phase = next_phase
        await sync_to_async(game.save)()


        print("** NEXT PHASE :",next_phase)
        if next_phase not in {"flop", "turn", "river", "showdown"}:
            return #Safety check

        if next_phase == "showdown":
            await self.handle_showdown(game)
            return
        
        # Burn one card
        Game.burn_card(game)

        # Determine how many cards to deal
        cards_to_deal = 3 if next_phase == "flop" else 1
        dealt_cards = game.deck[:cards_to_deal]
        game.community_cards.extend(dealt_cards)
        game.deck = game.deck[cards_to_deal:]

        # Save
       #  game.current_phase = next_phase
        await sync_to_async(game.save)()

        # Broadcast
        cards_pretty = await sync_to_async(convert_treys_str_int_pretty)(game.community_cards)
        phase_label = f"📡 {next_phase.capitalize()} : {cards_pretty}"
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
            score, rank, best_5_ints = await sync_to_async(find_best_five_cards)(combined_cards)
            player_hands.append((score, rank, best_5_ints, player))

        # Sort from best to worst (lowest treys score = best hand)
        player_hands.sort(key=lambda x: x[0])

        winnings = defaultdict(lambda: {
            "chips_won": 0,
            "best_score": None,
            "best_rank": "",
            "best_five": None,
        })
        

        # Distribute side pots
        for pot in side_pots:
            pot_amount = pot["amount"]
            eligible_ids = pot["eligible_ids"]

            # Filter out the players who are eligible for this pot
            in_contest = [(s, r, b5, p) for (s, r, b5, p) in player_hands if p.id in eligible_ids]
            if not in_contest:
                continue

            best_score = in_contest[0][0]
            winners = [(s, r, b5, p) for (s, r, b5, p) in in_contest if s == best_score]
            share = pot_amount // len(winners)

            # @TODO - Modify this to broadcast each player only once...
          
            for (win_score, win_rank, win_5, win_player) in winners:
                winnings[win_player]["chips_won"] += share
                winnings[win_player]["best_score"] = score
                winnings[win_player]["best_rank"] = rank
                winnings[win_player]["best_five"] = win_5
                win_player.chips += share
                await sync_to_async(win_player.save)()

        
        # Now broadcast once per winning player
        for win_player, info in winnings.items():
            username = await sync_to_async(lambda: win_player.user.username)()
            best_five_str = Card.ints_to_pretty_str(info["best_five"]).replace(",", "")
            rank_desc = info["best_rank"]
            total_chips = info["chips_won"]

            await self.broadcast_messages(
                f"🏆 {username} wins {total_chips} with {best_five_str} ({rank_desc})"
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
    async def transfer_chips_to_profile(self, game: Game, player: Player) -> None:
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
                "type": "broadcast_messages_helper",
                "messages": [message], 
            },
        )

    async def broadcast_messages_helper(self, event):
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
                    "user_can_check": await sync_to_async(
                       lambda: can_user_do_action(game, p, "check"), thread_sensitive=True
                    )(),
                    "user_can_call": await sync_to_async(
                       lambda: can_user_do_action(game, p, "call"), thread_sensitive=True
                    )(),
                }
                for p in players
            ],
        }

        # Send this game state **privately** to the respective player
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "broadcast_send_helper",
                "data": game_state_message,
            },
        )

   
    # -----------------------------------------------------------------------
    async def broadcast_send_helper(self, event):
        """
        Trigger that handles sending data.
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
                    "type": "broadcast_send_helper",
                    "data": private_data,
                },
            )


    # -----------------------------------------------------------------------
    async def send_private_game_state(self, game: Game, user: User) -> None:
        """
        Sends private game state to a player.
 
        Args:
            game (Game): The current game instance.
            user (User): The user.
 
        Returns:
            None
        """

        player = await sync_to_async(lambda: game.players.filter(user=user).first())()
        if not player:
            return  # Safety check

        hole_cards = player.hole_cards 
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
                "type": "broadcast_send_helper",
                "data": private_message,
            },
        )


    # -----------------------------------------------------------------------
    async def send_private_to_user(self, user: User) -> None:
        """
        Sends private user status to a user.
 
        Args:
            user (User): The user.
 
        Returns:
            None
        """ 

        total_user_chips = await sync_to_async(
            lambda: user.profile.chips, thread_sensitive=True
        )()

        private_message = {
            "type": "private_game_state",
            "total_user_chips": total_user_chips,
        }

        # Send private message only to this user's private channel
        await self.channel_layer.group_send(
            self.user_channel_name,  # Only to the current user
            {
                "type": "broadcast_send_helper",
                "data": private_message,
            },
        )
