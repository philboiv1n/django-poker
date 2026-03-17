"""
Shared test infrastructure: MockConsumer, helper factories, base test case.
"""
import json
from asgiref.sync import sync_to_async
from django.contrib.auth.models import User
from django.test import TransactionTestCase, TestCase

from game.models import Game, Player, Profile
from game.mixins.actions import ActionsMixin
from game.mixins.phases import PhasesMixin
from game.mixins.game_state import GameStateMixin
from game.mixins.dealing import DealingMixin
from game.mixins.broadcasting import BroadcastingMixin


# ---------------------------------------------------------------------------
# Mock consumer — replaces channel_layer / Redis with no-ops so that
# mixin logic can be tested in isolation.
# ---------------------------------------------------------------------------

class MockConsumer(BroadcastingMixin, ActionsMixin, GameStateMixin, PhasesMixin, DealingMixin):
    """Minimal consumer stub for unit-testing game-logic mixins."""

    def __init__(self, user, game_id):
        self.user = user
        self.game_id = game_id
        self.channel_name = f"test_{user.id}"
        self.room_group_name = f"game_{game_id}"
        self.user_channel_name = f"user_{user.id}"
        self.channel_layer = None   # not needed; broadcasts are mocked below
        self._sent = []             # messages sent via self.send()
        self._broadcasts = []       # messages passed to broadcast_messages()

    # -- WebSocket send --------------------------------------------------
    async def send(self, text_data=None, bytes_data=None):
        if text_data:
            self._sent.append(json.loads(text_data))

    # -- Broadcasting overrides (suppress Redis / channel-layer calls) ---
    async def broadcast_game_state(self, game):
        pass

    async def broadcast_messages(self, message):
        self._broadcasts.append(message)

    async def broadcast_private(self, game):
        pass

    async def send_private_to_user(self, user):
        pass

    async def send_private_game_state(self, game, user):
        pass


# ---------------------------------------------------------------------------
# DB factories
# ---------------------------------------------------------------------------

def make_user(username, chips=1000):
    user = User.objects.create_user(username, password="testpass")
    Profile.objects.get_or_create(user=user, defaults={"chips": chips})
    user.profile.chips = chips
    user.profile.save()
    return user


def make_game(**kwargs):
    defaults = dict(
        name="Test Game",
        buy_in=100,
        small_blind=10,
        big_blind=20,
        max_players=4,
        blind_timer=0,          # disable blind timer in all tests
        status="waiting",
    )
    defaults.update(kwargs)
    return Game.objects.create(**defaults)


def make_player(user, game, position=0, chips=100, **kwargs):
    return Player.objects.create(
        user=user, game=game, position=position, chips=chips, **kwargs
    )


def setup_active_2p_game(small_blind=10, big_blind=20, name="Active 2P Game"):
    """
    Return (game, p1, p2) with a fully-active 2-player preflop state.
    p1 = dealer + SB, p2 = BB.  current_turn = 0 (SB acts first heads-up).
    Deck is stocked with enough cards for a full hand.
    """
    import uuid
    u1 = make_user(f"act_p1_{uuid.uuid4().hex[:6]}")
    u2 = make_user(f"act_p2_{uuid.uuid4().hex[:6]}")
    game = make_game(
        name=f"{name}_{uuid.uuid4().hex[:8]}",
        small_blind=small_blind,
        big_blind=big_blind,
        status="active",
        max_players=2,
        current_phase="preflop",
        dealer_position=0,
        current_turn=0,
        deck=[
            "As", "Ks", "Qs", "Js", "Ts",
            "2h", "3h", "4h", "5h", "6h",
            "7c", "8c", "9c", "Tc", "Jc",
            "2d", "3d", "4d", "5d", "6d",
        ],
    )
    p1 = make_player(
        u1, game, position=0, chips=90,
        is_dealer=True, is_small_blind=True,
        current_bet=small_blind, total_bet=small_blind,
        hole_cards=["Ah", "Kh"],
    )
    p2 = make_player(
        u2, game, position=1, chips=big_blind * 4 - big_blind,
        is_big_blind=True,
        current_bet=big_blind, total_bet=big_blind,
        hole_cards=["2c", "3d"],
    )
    return game, p1, p2


# ---------------------------------------------------------------------------
# Base test cases
# ---------------------------------------------------------------------------

class SyncTestCase(TestCase):
    """For purely synchronous tests (models, utils)."""


class AsyncTestCase(TransactionTestCase):
    """
    For async tests that exercise consumer mixins.
    TransactionTestCase avoids select_for_update deadlocks that TestCase
    can cause when sync_to_async spawns threads with separate connections.
    """
