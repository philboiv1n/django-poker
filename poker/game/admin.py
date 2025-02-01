"""
admin.py
========

Manages how the Django admin panel handles the custom models for the poker app:
- Integrates a ProfileInline with the User model.
- Displays and configures Profile, Game, and Player models.
- Provides actions to remove or manage players from games.
"""

from django.contrib import admin
from django.contrib.auth.models import User
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import Profile, Game, Player

# List of fields related to user statistics that should be read-only in the admin interface
READONLY_FIELDS = (
    "total_chips_received",
    "total_chips_won",
    "total_chips_lost",
    "games_played",
    "games_won",
    "games_lost",
    "hands_played",
    "hands_won",
    "highest_win",
    "longest_winning_streak",
    "longest_losing_streak",
    "average_bet",
    "ranking",
    "royal_flushes",
    "straight_flushes",
    "four_of_a_kinds",
    "full_houses",
)


class ProfileInline(admin.StackedInline):
    """
    An inline admin descriptor for Profile objects.
    This allows the Profile to appear (and be edited) within
    the Django User admin page.
    """

    model = Profile
    can_delete = False
    verbose_name_plural = "Profile"
    readonly_fields = READONLY_FIELDS


class UserAdmin(BaseUserAdmin):
    """
    Custom admin configuration for the Django User model.
    Attaches the ProfileInline so that admins can see and edit user Profiles
    directly on the User admin page.
    """

    inlines = (ProfileInline,)

    def get_readonly_fields(self, request, obj=None):
        """
        Determine which fields are read-only.
        If obj is None (meaning a new user is being created),
        we don't set read-only fields.
        """
        return self.readonly_fields if obj else ()

    def save_model(self, request, obj, form, change):
        """
        Ensures that a Profile is created when a new User is added via the Admin Panel.
        """
        is_new_user = (
            not obj.pk
        )  # Check if the user is being created for the first time

        super().save_model(request, obj, form, change)  # Save the user first

        if is_new_user:
            # Create a Profile if it does not already exist
            Profile.objects.get_or_create(user=obj)


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    """
    Custom admin configuration for Profiles.
    Prevents editing of read-only stat fields, but allows editing basic profile info.
    """

    readonly_fields = READONLY_FIELDS
    list_display = ("user", "chips", "games_played", "games_won")


@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    """
    Admin configuration for the Game model.
    Displays key game properties in list view and allows filtering/search.
    """

    list_display = (
        "name",
        "game_type",
        "betting_type",
        "max_players",
        "status",
        "created_at",
    )
    list_filter = ("game_type", "betting_type", "status")
    search_fields = ("name",)
    ordering = ("-created_at",)


@admin.register(Player)
class PlayerAdmin(admin.ModelAdmin):
    """
    Admin configuration for the Player model.
    Allows quick viewing of which user is in which game, their chip count,
    and readiness status.
    Provides custom actions for removing players from games.
    """

    list_display = ("user", "game", "chips", "is_ready", "last_active")
    list_filter = ("game", "is_ready")
    search_fields = ("user__username", "game__name")
    actions = ["remove_from_game", "remove_inactive_players"]

    def remove_from_game(self, request, queryset):
        """
        Custom admin action:
        Removes the selected players from their respective games.
        """
        queryset.delete()
        self.message_user(
            request, "Selected players have been removed from their games."
        )

    remove_from_game.short_description = "Remove selected players from games"


# Unregister default User model and register the custom one
admin.site.unregister(User)
admin.site.register(User, UserAdmin)
