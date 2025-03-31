"""
views.py
========

Defines the core view functions for the Django poker application:
- Handles user-facing pages such as the dashboard, profile, stats, and game interactions.
- Utilizes decorators like @login_required to ensure only authenticated users access certain views.
- Includes real-time-related and table join/leave logic.
"""

import redis
import json
from django.conf import settings
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib import messages
# from django.conf import settings

from .forms import ProfileForm
from .models import Game


@login_required
def logout_validation(request):
    """
    Displays a logout confirmation or validation page.

    Args:
        request (HttpRequest): The incoming HTTP request.

    Returns:
        HttpResponse: Renders the logout confirmation template.
    """
    return render(request, "game/logout_validation.html")


@login_required
def dashboard(request):
    """
    Renders the user's main landing page after login.

    Displays:
      - List of available 'waiting' games (those not yet started).
      - The user's nickname (drawn from Profile).

    Args:
        request (HttpRequest): The incoming HTTP request.

    Returns:
        HttpResponse: Rendered dashboard template with available games.
    """
    available_games = Game.objects.all
    return render(
        request,
        "game/dashboard.html",
        {"games": available_games},
    )


@login_required
def profile(request):
    """
    Allows the user to view and edit their profile.

    Handles both profile updates and password changes. Uses POST to process updates,
    and GET to display the current information.

    Args:
        request (HttpRequest): The incoming HTTP request.

    Returns:
        HttpResponse: Rendered profile template with forms.
    """

    profile_form = ProfileForm(instance=request.user.profile)
    password_form = PasswordChangeForm(request.user)

    if request.method == "POST":
        if "profile_submit" in request.POST:
            profile_form = ProfileForm(request.POST, instance=request.user.profile)
            if profile_form.is_valid():
                profile_form.save()
                messages.success(request, "Your profile has been updated successfully!")
                return redirect("profile")

        elif "password_submit" in request.POST:
            password_form = PasswordChangeForm(request.user, request.POST)
            if password_form.is_valid():
                user = password_form.save()
                update_session_auth_hash(
                    request, user
                )  # Prevents logout after password change
                messages.success(
                    request, "Your password has been changed successfully!"
                )
                return redirect("profile")
            else:
                messages.error(request, "Please correct the errors below.")

    return render(
        request,
        "game/profile.html",
        {
            "profile_form": profile_form,
            "password_form": password_form,
        },
    )


@login_required
def stats(request):
    """
    Displays detailed statistics for the current user.

    Stats include number of games played, wins, losses, and other
    performance metrics from the user's profile.

    Args:
        request (HttpRequest): The incoming HTTP request.

    Returns:
        HttpResponse: Rendered stats page.
    """
    profile = request.user.profile
    return render(request, "game/stats.html", {"profile": profile})



@login_required
def table(request, game_id):
    """
    Renders the lobby or active game view for a given game.

    Shows:
      - All players currently in the game.
      - Whether the current user is part of the game (is_player).
      - JSON-encoded player data for use in frontend scripts.

    Args:
        request (HttpRequest): The incoming HTTP request.
        game_id (int): The ID of the game to load.

    Returns:
        HttpResponse: Rendered table view with game and player info.
    """

    # Connect to Redis
    redis_client = redis.Redis(
        host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=0, decode_responses=True
    )

    game = get_object_or_404(Game, id=game_id)
    players = game.players.all()
    current_turn_player = players.filter(position=game.current_turn).first() or players.first()
    current_turn_username = current_turn_player.user.username if current_turn_player else ""
    is_player = players.filter(user=request.user).exists()

    # Retrieve last 10 messages from Redis (or DB)
    redis_key = f"game_{game_id}_messages"
    stored_messages = redis_client.lrange(redis_key, -10, -1)  # list of JSON strings
    # parse each
    clean_messages = [json.loads(msg).get("message", "") for msg in stored_messages]


    players_data = []

    for p in players:
        players_data.append({
            "username": p.user.username,
            "game_chips": p.chips,
            "position": p.position,
            "is_dealer": p.is_dealer,
            "is_small_blind": p.is_small_blind,
            "is_big_blind": p.is_big_blind,
            "has_folded": p.has_folded,
            "avatar_color": p.user.profile.avatar_color,
            "current_bet": p.current_bet,
            "is_next_to_play": p.position == current_turn_player.position,
        })

    players_json = json.dumps(players_data)

    return render(
        request,
        "game/table.html",
        {
            "game": game,
            "players": players,
            "players_json": players_json,
            # "is_player": "true" if is_player else "false",
            "is_player": is_player,
            "current_turn_username": current_turn_username,
            "last_messages_json": json.dumps(clean_messages),
        },
    )