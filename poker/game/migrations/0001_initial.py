"""
Squashed initial migration representing the current state of all models.
"""

import django.core.validators
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Profile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("avatar_color", models.CharField(default="#000000", max_length=7)),
                ("chips", models.PositiveIntegerField(default=1000)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="Game",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(default="Nameless", max_length=25, unique=True)),
                (
                    "game_type",
                    models.CharField(
                        choices=[("sit_and_go", "Texas Hold'em - Sit & Go")],
                        default="sit_and_go",
                        max_length=20,
                    ),
                ),
                (
                    "betting_type",
                    models.CharField(
                        choices=[("no_limit", "No-Limit")],
                        default="no_limit",
                        max_length=10,
                    ),
                ),
                ("buy_in", models.PositiveIntegerField(default=1000)),
                ("small_blind", models.PositiveIntegerField(default=50)),
                ("big_blind", models.PositiveIntegerField(default=100)),
                (
                    "blind_timer",
                    models.PositiveIntegerField(
                        default=5,
                        validators=[
                            django.core.validators.MinValueValidator(0),
                            django.core.validators.MaxValueValidator(60),
                        ],
                    ),
                ),
                (
                    "max_players",
                    models.PositiveIntegerField(
                        default=4,
                        validators=[
                            django.core.validators.MinValueValidator(2),
                            django.core.validators.MaxValueValidator(10),
                        ],
                    ),
                ),
                ("status", models.CharField(default="waiting", max_length=20)),
                ("dealer_position", models.IntegerField(blank=True, null=True)),
                ("current_turn", models.IntegerField(blank=True, null=True)),
                (
                    "current_phase",
                    models.CharField(
                        choices=[
                            ("preflop", "Preflop"),
                            ("flop", "Flop"),
                            ("turn", "Turn"),
                            ("river", "River"),
                            ("showdown", "Showdown"),
                        ],
                        default="preflop",
                        max_length=10,
                    ),
                ),
                ("community_cards", models.JSONField(default=list)),
                ("deck", models.JSONField(default=list)),
                ("last_raise_delta", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.CreateModel(
            name="Player",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("chips", models.PositiveIntegerField(default=0)),
                ("current_bet", models.PositiveIntegerField(default=0)),
                ("total_bet", models.IntegerField(default=0)),
                ("has_folded", models.BooleanField(default=False)),
                ("has_checked", models.BooleanField(default=False)),
                ("is_all_in", models.BooleanField(default=False)),
                ("is_small_blind", models.BooleanField(default=False)),
                ("is_big_blind", models.BooleanField(default=False)),
                ("is_dealer", models.BooleanField(default=False)),
                ("has_acted_this_round", models.BooleanField(default=False)),
                ("can_reraise_this_round", models.BooleanField(default=True)),
                ("position", models.PositiveIntegerField(blank=True, null=True)),
                ("hole_cards", models.JSONField(default=list)),
                ("last_active", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "game",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="players",
                        to="game.game",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
    ]
