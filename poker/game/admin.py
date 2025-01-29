from django.contrib import admin
from django.contrib.auth.models import User
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import Profile, Game

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

# Defining ProfileInline (To Show Profile Inside User Admin)
class ProfileInline(admin.StackedInline):
    model = Profile
    can_delete = False
    verbose_name_plural = 'Profile'
    readonly_fields = READONLY_FIELDS


# Customizing UserAdmin
class UserAdmin(BaseUserAdmin):
    inlines = (ProfileInline,)
    def get_readonly_fields(self, request, obj=None):
        return self.readonly_fields if obj else ()  # Ensure only Profile fields are read-only


# PROFILE ADMIN
@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    readonly_fields = READONLY_FIELDS
    list_display = ("user", "nickname", "chips", "games_played", "games_won")



# GAME ADMIN
@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    list_display = ("code", "buy_in", "small_blind", "big_blind", "blind_timer", "max_players", "status", "created_at")
    readonly_fields = ("code", "created_at") 


# admin.site.register(User, UserAdmin)
# admin.site.register(Profile, ProfileAdmin)
# admin.site.register(Game, GameAdmin)

# Unregister default User model and register the custom one
admin.site.unregister(User)
admin.site.register(User, UserAdmin)