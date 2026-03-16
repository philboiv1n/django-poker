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

from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils.timezone import now


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


class Game(models.Model):
    """
    Represents a poker table or session that players can join.
    Stores the game configuration (name, blinds, buy-in) and status.
    """

    # Available game types (can be extended with more variants in the future).
    GAME_TYPES = [
        ("sit_and_go", "Texas Hold'em - Sit & Go"),
    ]

    # Betting structures: Limit or No-Limit.
    BETTING_TYPES = [
        ("no_limit", "No-Limit"),
    ]

    # Custom name of the table. Each table must have a unique name.
    name = models.CharField(max_length=25, unique=True, blank=False, default="Nameless")

    # The variant of poker being played, e.g., Texas Hold'em.
    game_type = models.CharField(
        max_length=20, choices=GAME_TYPES, default="sit_and_go"
    )

    # The betting style used for this game: Limit or No-Limit.
    betting_type = models.CharField(
        max_length=10, choices=BETTING_TYPES, default="no_limit"
    )

    # Store the side pots
    # side_pots = models.JSONField(default=list)

    # Amount of chips required to join this table.
    buy_in = models.PositiveIntegerField(default=1000)

    # The blinds (forced bets) in the game:
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
    # - "ctive": game in progress
    # - "Finished": game has ended
    status = models.CharField(max_length=20, default="waiting")

    # Tracks dealer position (where dealing starts)
    dealer_position = models.IntegerField(null=True, blank=True)

    # Tracks which player's turn it is (position in game)
    current_turn = models.IntegerField(null=True, blank=True)

    # Track the current game phase
    current_phase = models.CharField(
        max_length=10,
        choices=[
            ("preflop", "Preflop"),
            ("flop", "Flop"),
            ("turn", "Turn"),
            ("river", "River"),
            ("showdown", "Showdown"),
        ],
        default="preflop",
    )

    # Stores the Flop, Turn, River
    community_cards = models.JSONField(default=list)

    # Stores the deck as a list of strings
    deck = models.JSONField(default=list)

    # The size of the last raise in the current betting round, used to enforce
    # the correct minimum re-raise rule (min raise = last raise increment).
    # Reset to 0 at the start of each betting round.
    last_raise_delta = models.PositiveIntegerField(default=0)

    # Timestamp of when the game was created.
    created_at = models.DateTimeField(auto_now_add=True)

    def get_pot(self) -> int:
        """
        Returns the total amount of chips across all players.
        """
        players = list(self.players.order_by("position"))
        return sum(player.total_bet for player in players)
       
    def burn_card(self) -> None:
        """
        Burns (removes) the top card from the deck.
    
        This is used in Texas Hold'em to discard a card before dealing
        community cards (Flop, Turn, River) to prevent cheating and ensure fairness.
    
        Args:
            game (Game): The current game instance.
    
        Returns:
            None
        """
        if self.deck:
            self.deck.pop(0)
    

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

    # Amount bet in this round
    current_bet = models.PositiveIntegerField(default=0)

    # Cumulative bet in the game
    total_bet = models.IntegerField(default=0)

    # Whether the player folded
    has_folded = models.BooleanField(default=False)

    # Whether the player has checked
    has_checked = models.BooleanField(default=False)

    # Whether the player is all-in
    is_all_in = models.BooleanField(default=False)

    # Player is small blind
    is_small_blind = models.BooleanField(default=False)

    # Player is big blind
    is_big_blind = models.BooleanField(default=False)

    # Player is dealing
    is_dealer = models.BooleanField(default=False)

    # Whether the player has acted
    has_acted_this_round = models.BooleanField(default=False)

    # False when a sub-minimum all-in has occurred and this player has already
    # acted at the previous bet level — they may only call, not re-raise.
    can_reraise_this_round = models.BooleanField(default=True)

    # Assigns a seat in the game
    position = models.PositiveIntegerField(null=True, blank=True)

    # Store hole cards (two private cards per player)
    hole_cards = models.JSONField(default=list)  # Stores ["4♥︎", "K♠︎"]

    # Tracks the last time the player performed an action (e.g., to detect inactivity).
    last_active = models.DateTimeField(default=now)

    def set_hole_cards(self, cards):
        """Save hole cards as JSON."""
        self.hole_cards = cards
        self.save()

    def clear_hole_cards(self):
        """Clears hole cards at the end of the round."""
        self.hole_cards = []
        self.save()

    def __str__(self):
        """
        Returns a simple string with the player's username
        and the name of the game they are in with their position.
        """
        return f"{self.user.username} in {self.game.name} (Position {self.position})"
