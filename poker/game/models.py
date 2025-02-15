"""
models.py
=========

This module defines the core data structures of the poker application:
- Profile: Extends the built-in User model with additional poker-related fields.
- Game: Represents a poker table/session with its settings and status.
- Player: Links a User to a specific Game and tracks their in-game state.

By using Django's ORM, each class corresponds to a database table where these
pieces of data are stored. This allows the application to store and manage
players, games, and poker statistics seamlessly.
"""

import redis
from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils.timezone import now
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings


# Connect to Redis
redis_client = redis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=0, decode_responses=True)


class Profile(models.Model):
    """
    Extends the default Django User model with additional fields for poker-specific data.
    Tracks statistics, chips, and other attributes associated with a player's account.
    """

    # Links this Profile to a single User (1-to-1 relationship).
    # When the User is deleted, the Profile is also removed.
    user = models.OneToOneField(User, on_delete=models.CASCADE)

    # Hex color code (e.g., "#FF5733") for the player's avatar or display.
    avatar_color = models.CharField(max_length=7, default="#000000")

    # The number of chips a user currently holds (fictional currency).
    chips = models.PositiveIntegerField(default=1000)

    # The cumulative total of chips the user has ever received (e.g., from admin or game buy-ins).
    total_chips_received = models.PositiveIntegerField(default=0)

    # Total chips the user has won overall across all games.
    total_chips_won = models.PositiveIntegerField(default=0)

    # Total chips the user has lost overall across all games.
    total_chips_lost = models.PositiveIntegerField(default=0)

    # Overall statistics:
    # - games_played: total number of complete games the user participated in
    # - games_won: total number of games the user finished in a winning position
    # - games_lost: total number of games the user lost
    games_played = models.PositiveIntegerField(default=0)
    games_won = models.PositiveIntegerField(default=0)
    games_lost = models.PositiveIntegerField(default=0)

    # Tracking hands at a more granular level:
    # - hands_played: how many poker hands dealt to this user
    # - hands_won: how many of those hands were winning hands
    hands_played = models.PositiveIntegerField(default=0)
    hands_won = models.PositiveIntegerField(default=0)

    # The largest pot or single-hand earning achieved in a single win.
    highest_win = models.PositiveIntegerField(default=0)

    # Streak tracking:
    # - longest_winning_streak: the highest consecutive wins in terms of hands/games
    # - longest_losing_streak: the highest consecutive losses
    longest_winning_streak = models.PositiveIntegerField(default=0)
    longest_losing_streak = models.PositiveIntegerField(default=0)

    # The average bet size the user typically makes (for analytics).
    average_bet = models.FloatField(default=0.0)

    # The player's ranking among other players (if applicable).
    ranking = models.PositiveIntegerField(null=True, blank=True)

    # Counters for specific rare or powerful hands:
    # - royal_flushes: how many times the user got a Royal Flush
    # - straight_flushes: how many times the user got a Straight Flush
    # - four_of_a_kinds: how many times the user got Four of a Kind
    # - full_houses: how many times the user got a Full House
    royal_flushes = models.PositiveIntegerField(default=0)
    straight_flushes = models.PositiveIntegerField(default=0)
    four_of_a_kinds = models.PositiveIntegerField(default=0)
    full_houses = models.PositiveIntegerField(default=0)


class Game(models.Model):
    """
    Represents a poker table or session that players can join.
    Stores the game configuration (name, blinds, buy-in) and status.
    """

    # Available game types (can be extended with more variants in the future).
    GAME_TYPES = [
        ("texas_holdem", "Texas Hold'em"),
    ]

    # Betting structures: Limit or No-Limit.
    BETTING_TYPES = [
        ("limit", "Limit"),
        ("no_limit", "No-Limit"),
    ]

    # Custom name of the table. Each table must have a unique name.
    name = models.CharField(max_length=25, unique=True, blank=False, default="Nameless")

    # The variant of poker being played, e.g., Texas Hold'em.
    game_type = models.CharField(
        max_length=20, choices=GAME_TYPES, default="texas_holdem"
    )

    # The betting style used for this game: Limit or No-Limit.
    betting_type = models.CharField(
        max_length=10, choices=BETTING_TYPES, default="no_limit"
    )

    # Amount of chips required to join this table.
    buy_in = models.PositiveIntegerField(default=1000)

    # The blinds (forced bets) in the game:
    # - small_blind
    # - big_blind
    small_blind = models.PositiveIntegerField(default=50)
    big_blind = models.PositiveIntegerField(default=100)

    # The timer for increasing blinds, in minutes (0 means no increase).
    blind_timer = models.PositiveIntegerField(
        default=5, validators=[MinValueValidator(0), MaxValueValidator(60)]
    )

    # The maximum number of players allowed to join this table.
    max_players = models.PositiveIntegerField(
        default=4, validators=[MinValueValidator(2), MaxValueValidator(10)]
    )

    # Current status of the game:
    # - "waiting": waiting for players
    # - "active": game in progress
    # - "finished": game has ended
    status = models.CharField(max_length=20, default="waiting")

    # Tracks dealer position (where dealing starts)
    # dealer_position = models.PositiveIntegerField(default=0)
    dealer_position = models.IntegerField(null=True, blank=True)

    # Tracks which player's turn it is (position in game)
    #current_turn = models.PositiveIntegerField(null=True, blank=True)
    # current_turn = models.IntegerField(default=0) 
    current_turn = models.IntegerField(null=True, blank=True) 


    # Timestamp of when the game was created.
    created_at = models.DateTimeField(auto_now_add=True)

   
    def get_next_position(self):
        """
        Returns the next available seat position for a new player.
        """
        existing_positions = list(self.players.values_list("position", flat=True))
        for pos in range(self.max_players):  # Find the first empty position
            if pos not in existing_positions:
                return pos
        return None  # No available positions
    

    def get_next_turn_after(self, position):
        """
        Returns the next player's position after the given position.
        Ensures a valid player is always assigned.
        """
        players = list(self.players.order_by("position"))

        if not players:
            return None  # No players left
        
        for i, player in enumerate(players):
            if player.position == position:
                return players[(i + 1) % len(players)].position  # Ensure a valid player is returned
    
        return players[0].position  # Default to first player if something goes wrong
        

    
    def rotate_dealer(self):
        """
        Moves the dealer to the next player after a full betting round.
        """
        players = list(self.players.order_by("position"))
        current_dealer_index = next((i for i, p in enumerate(players) if p.position == self.dealer_position), 0)

        # Move the dealer to the next player
        new_dealer_index = (current_dealer_index + 1) % len(players)
        self.dealer_position = players[new_dealer_index].position

        # First turn goes to the player after the dealer
        self.current_turn = self.get_next_turn_after(self.dealer_position)
        self.save()

        # Notify players via WebSocket
        self.broadcast_new_dealer(players[new_dealer_index].user.username)



    def broadcast_game_start(self, dealer_username):
        """ Broadcasts the game start message via WebSockets. """
        message = f"{dealer_username} is the first dealer! Game has started."
        self.broadcast_websocket_message(message)



    def broadcast_new_dealer(self, dealer_username):
        """ Broadcasts when the dealer rotates. """
        message = f"{dealer_username} is the new dealer!"
        self.broadcast_websocket_message(message)



    def broadcast_websocket_message(self, message):
        """ Sends a WebSocket message to all players in the game. """

        # Store message in Redis (list)
        redis_key = f"game_{self.id}_messages"
        redis_client.rpush(redis_key, message)

        # Limit storage to last 10 messages
        redis_client.ltrim(redis_key, -10, -1)

        channel_layer = get_channel_layer()

        async_to_sync(channel_layer.group_send)(
            f"game_{self.id}",
            {
                "type": "send_action_message",
                "message": message,
            },
)

    def __str__(self):
        """
        Returns a string representation of the game, including the name, game type,
        and betting type for easy identification in admin or logs.
        """
        return f"{self.name} ({self.get_game_type_display()} - {self.get_betting_type_display()})"


class Player(models.Model):
    """
    An intermediary model representing a specific user's participation in a game.
    Stores each player's state within that game (e.g., chips, readiness, last active).
    """

    # The user who is playing in the game.
    user = models.ForeignKey("auth.User", on_delete=models.CASCADE)

    # The game in which the user is participating.
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="players")

    # The number of chips this user has specifically for this game.
    chips = models.PositiveIntegerField(default=0)

    # Indicates if the player is ready to start the game (often used in the lobby).
    is_ready = models.BooleanField(default=False)

    # Tracks the last time the player performed an action (e.g., to detect inactivity).
    last_active = models.DateTimeField(default=now)

    # Assigns a seat in the game
    position = models.PositiveIntegerField(null=True, blank=True)

    # Store hole cards (two private cards per player)
    hole_cards = models.JSONField(default=list)  # Stores ["4♥︎", "K♠︎"]

    def set_hole_cards(self, cards):
        """Save hole cards as JSON."""
        self.hole_cards = cards
        self.save()

    def clear_hole_cards(self):
        """Clears hole cards at the end of the round."""
        self.hole_cards = []
        self.save()

    def save(self, *args, **kwargs):
        """
        Automatically assigns the next available position when a player joins a game.
        """
        if self.position is None:  # Only assign position if not already set
            self.position = self.game.get_next_position()
        super().save(*args, **kwargs)

    def is_turn(self):
        """Returns True if it's this player's turn to act."""
        return self.game.current_turn == self.position

    def __str__(self):
        """
        Returns a simple string with the player's username
        and the name of the game they are in with their position.
        """
        return f"{self.user.username} in {self.game.name} (Position {self.position})"
