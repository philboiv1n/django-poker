from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.contrib.auth.models import User
import uuid


class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    nickname = models.CharField(max_length=50, blank=True)
    avatar_color = models.CharField(max_length=7, default="#000000")
    chips = models.PositiveIntegerField(default=1000)
    total_chips_received = models.PositiveIntegerField(default=0)
    total_chips_won = models.PositiveIntegerField(default=0)
    total_chips_lost = models.PositiveIntegerField(default=0)
    games_played = models.PositiveIntegerField(default=0)
    games_won = models.PositiveIntegerField(default=0)
    games_lost = models.PositiveIntegerField(default=0)
    hands_played = models.PositiveIntegerField(default=0)
    hands_won = models.PositiveIntegerField(default=0)
    highest_win = models.PositiveIntegerField(default=0)
    longest_winning_streak = models.PositiveIntegerField(default=0)
    longest_losing_streak = models.PositiveIntegerField(default=0)
    average_bet = models.FloatField(default=0.0)
    ranking = models.PositiveIntegerField(null=True, blank=True)
    royal_flushes = models.PositiveIntegerField(default=0)
    straight_flushes = models.PositiveIntegerField(default=0)
    four_of_a_kinds = models.PositiveIntegerField(default=0)
    full_houses = models.PositiveIntegerField(default=0)


# class Game(models.Model):
#     host = models.ForeignKey(User, on_delete=models.CASCADE, related_name='hosted_games')
#     code = models.CharField(max_length=8, unique=True)  # Invitation code
#     buy_in = models.PositiveIntegerField(default=1000)
#     small_blind = models.PositiveIntegerField(default=50)
#     big_blind = models.PositiveIntegerField(default=100)
#     blind_timer = models.PositiveIntegerField(default=5)  # In minutes
#     max_players = models.PositiveIntegerField(default=4)
#     status = models.CharField(max_length=20, default='waiting')  # e.g., waiting, active, finished
#     created_at = models.DateTimeField(auto_now_add=True)


class Game(models.Model):
    code = models.CharField(max_length=8, unique=True, default=uuid.uuid4().hex[:8].upper())
    buy_in = models.PositiveIntegerField(default=1000)
    small_blind = models.PositiveIntegerField(default=50)
    big_blind = models.PositiveIntegerField(default=100)
    blind_timer = models.PositiveIntegerField(
        default=5,
        validators=[MinValueValidator(0), MaxValueValidator(60)]
    )
    max_players = models.PositiveIntegerField(
        default=4,
        validators=[MinValueValidator(2), MaxValueValidator(10)]
    )

    # Status fields
    status = models.CharField(max_length=20, default="waiting")  # Options: waiting, active, finished
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Table {self.code} ({self.max_players} players) - {self.status}"


# class Player(models.Model):
#     user = models.ForeignKey(User, on_delete=models.CASCADE)
#     game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name='players')
#     chips = models.PositiveIntegerField(default=0)
#     is_ready = models.BooleanField(default=False)



class Player(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="players")
    chips = models.PositiveIntegerField(default=0)
    is_ready = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.user.username} in {self.game.code}"