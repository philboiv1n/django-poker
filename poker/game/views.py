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
from django.conf import settings

from .forms import ProfileForm
from .models import Game


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
            "is_player": "true" if is_player else "false",
            "current_turn_username": current_turn_username,
        },
    )