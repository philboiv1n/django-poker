"""
views.py
========

Defines the core view functions for the Django poker application:
- Handles user-facing pages such as the dashboard, profile, stats, and game interactions.
- Utilizes decorators like @login_required to ensure only authenticated users access certain views.
- Includes real-time-related and table join/leave logic.
"""
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib import messages

from .forms import ProfileForm
from .models import Game, Player


@login_required
def websocket_test(request):
    """
    Renders a simple template to test WebSocket functionality.
    Typically used to confirm that real-time channels are working properly.
    """
    return render(request, "game/test.html")


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
    profile = request.user.profile
    available_games = Game.objects.filter(status="waiting")
    return render(
        request,
        "game/dashboard.html",
        {
            "games": available_games,
            "nickname": profile.nickname,
        },
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
                update_session_auth_hash(request, user)  # Prevents logout after password change
                messages.success(request, "Your password has been changed successfully!")
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
            return redirect("dashboard")  # Redirect if the game is full

        # If user is not already in the game, create a Player entry with default chips
        Player.objects.get_or_create(
            user=request.user, game=game, defaults={"chips": game.buy_in}
        )
        return redirect("table", game_id=game.id)

    # Redirect users who try to access this view via GET
    return redirect("dashboard")


@login_required
def leave_table(request, game_id):
    """
    Removes the current user from a specified game if accessed via POST.
    Optional: If the game becomes empty afterward, mark it as 'waiting'.
    Redirects back to the game table or the dashboard, depending on the flow.
    """
    game = get_object_or_404(Game, id=game_id)

    if request.method == "POST":
        # Remove the user from the game
        Player.objects.filter(user=request.user, game=game).delete()

        # If no players remain, you can mark the game as waiting or inactive
        if game.players.count() == 0:
            game.status = "waiting"
            game.save()

    # Redirect back to the table by default
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
    is_player = players.filter(user=request.user).exists()

    return render(
        request,
        "game/table.html",
        {
            "game": game,
            "players": players,
            "is_player": is_player,
        },
    )
