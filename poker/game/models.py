from django.db import models
from django.contrib.auth.models import User


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