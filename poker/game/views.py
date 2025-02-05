"""
views.py
========

Defines the core view functions for the Django poker application:
- Handles user-facing pages such as the dashboard, profile, stats, and game interactions.
- Utilizes decorators like @login_required to ensure only authenticated users access certain views.
- Includes real-time-related and table join/leave logic.
"""

import redis
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib import messages
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from django.conf import settings

from .forms import ProfileForm
from .models import Game, Player

# Connect to Redis
redis_client = redis.Redis(host="redis", port=6379, db=0, decode_responses=True)

@login_required
def logout_validation(request):
    """
    Displays a logout confirmation or validation page.
    Users might be prompted here to confirm before fully logging out.
    """
    return render(request, "game/logout_validation.html")


@login_required
def dashboard(request):
    """
    Renders the user's main landing page after login.
    Displays:
      - List of available 'waiting' games (those not yet started).
      - The user's nickname (drawn from Profile).
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
    Allows the user to view and edit their profile via a ProfileForm.
    - On POST: Saves form changes (nickname, avatar color, etc.).
    - On GET: Renders the form with current profile info.
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
    Displays detailed statistics for the current user (e.g., games played, wins, etc.).
    The data is gathered from request.user.profile and displayed read-only.
    """
    profile = request.user.profile
    return render(request, "game/stats.html", {"profile": profile})


@login_required
def join_table(request, game_id):
    """
    Joins the user to a specified game if:
      - The game is not already full.
      - The user is not already part of the game.
    Handled via POST to ensure a proper action submission.
    Redirects to the game lobby upon success.
    """
    game = get_object_or_404(Game, id=game_id)

    if request.method == "POST":
        # Ensure the game isn't full
        if game.players.count() >= game.max_players:
            messages.error(request, "This table is full.")
            return redirect("table", game_id=game.id)  

        # Ensure the player isn't already in the game
        player, created = Player.objects.get_or_create(
            user=request.user, game=game, defaults={"chips": game.buy_in}
        )

        if created:
            game.start_game_if_ready()  # Start game if at least 2 players joined
        return redirect("table", game_id=game.id)



@login_required
def leave_table(request, game_id):
    """
    Removes the player from the game.
    - If the player was the current turn, pass the turn to the next player.
    - If 1 or 0 players remain, stop the game.
    """

    game = get_object_or_404(Game, id=game_id)
    player = get_object_or_404(Player, user=request.user, game=game)

    if request.method == "POST":
        # Get the player's position before leaving
        leaving_position = player.position

        # Remove the player from the game
        player.delete()

        # Check remaining players
        remaining_players = list(game.players.order_by("position"))

        if len(remaining_players) == 0:
            # If no players remain, stop the game
            game.status = "waiting"
            game.current_turn = None
            messages.info(request, "The game has been stopped due to no players remaining.")
        
        elif len(remaining_players) == 1:
            # If only one player remains, stop the game
            game.status = "waiting"
            game.current_turn = None
            messages.info(request, "The game has been stopped because only one player is left.")
        
        else:
            # If the leaving player had the turn, pass it to the next player
            if game.current_turn == leaving_position:
                game.current_turn = game.get_next_turn_after(leaving_position)

        game.save()

    return redirect("table", game_id=game.id)



@login_required
def table(request, game_id):
    """
    Renders the lobby or active game view for a given game.
    Shows:
      - All players currently in the game.
      - Whether the current user is part of the game (is_player).
    """
    game = get_object_or_404(Game, id=game_id)
    players = game.players.all()
    current_turn_player = players.filter(position=game.current_turn).first()
    current_turn_username = current_turn_player.user.username if current_turn_player else ""
    is_player = players.filter(user=request.user).exists()

    return render(
        request,
        "game/table.html",
        {
            "game": game,
            "players": players,
            "is_player": is_player,
            "current_turn_username": current_turn_username,
        },
    )


def update_turn_and_notify(game):
    """
    Updates the game turn and sends a WebSocket message to all players.
    """

    # Get the current player object
    current_player = Player.objects.filter(game=game, position=game.current_turn).first()

    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        f"game_{game.id}",
        {
            "type": "send_turn_update",
            "current_turn": game.current_turn,
            "current_username": current_player.user.username if current_player else "Unknown",
        },
    )

@login_required
def player_action(request, game_id):
    """
    Allows a player to perform an action and updates the turn in real-time.
    """

    game = get_object_or_404(Game, id=game_id)
    player = get_object_or_404(Player, user=request.user, game=game)

    if not player.is_turn():
        return redirect("table", game_id=game.id)

    action_message = ""  # Message to broadcast

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "check":
            action_message = f"✅ {player.user.username} checked."
        elif action == "fold":
            action_message = f"🚫 {player.user.username} folded."

        # Move turn to the next player
        game.current_turn = game.get_next_turn_after(player.position)
        game.save()

         # Store message in Redis (List)
        redis_key = f"game_{game_id}_messages"
        redis_client.rpush(redis_key, action_message)

        # Limit storage to last 10 messages
        redis_client.ltrim(redis_key, -10, -1)
        
        # Broadcast the action message to all players via WebSockets
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f"game_{game.id}",
            {
                "type": "send_action_message",
                "message": action_message,
            },
        )

        # Send WebSocket update to all players
        update_turn_and_notify(game)

    return redirect("table", game_id=game.id)