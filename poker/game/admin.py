from django.contrib import admin
from django.contrib.auth.models import User
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import Profile

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
    "full_houses"
    )

class ProfileInline(admin.StackedInline):
    model = Profile
    can_delete = False
    verbose_name_plural = 'Profile'
    readonly_fields = READONLY_FIELDS


class UserAdmin(BaseUserAdmin):
    inlines = (ProfileInline,)

    def get_readonly_fields(self, request, obj=None):
        # Ensure User fields are editable but Profile fields remain read-only
        if obj:  # Editing an existing user
            return self.readonly_fields + ()
        return self.readonly_fields


class ProfileAdmin(admin.ModelAdmin):
    # Specify fields that should be read-only
    readonly_fields = READONLY_FIELDS

    # Optionally, customize which fields are displayed in the admin form
    # fields = (
    #     "user",
    #     "nickname",
    #     "avatar_color",
    # )

    # Control how fields are listed in the admin's list view
    list_display = ("user", "nickname", "chips", "games_played", "games_won")


admin.site.unregister(User)
admin.site.register(User, UserAdmin)
admin.site.register(Profile, ProfileAdmin)
