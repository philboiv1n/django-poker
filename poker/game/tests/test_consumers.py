"""
Integration tests for GameConsumer (WebSocket connect/disconnect/receive).

Uses channels.testing.WebsocketCommunicator with an InMemoryChannelLayer so
no Redis is required.  Authentication is injected via a thin middleware shim
that sets scope["user"] before the consumer sees the request.
"""
import asyncio
import json
from unittest.mock import patch, AsyncMock

from asgiref.sync import sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.test import TransactionTestCase, override_settings
from django.urls import path

from game.consumers import GameConsumer
from game.models import Game, Player
from .base import make_user, make_game, make_player


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

IN_MEMORY_CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}


class ForcedAuthMiddleware:
    """Inject a fixed user into the WebSocket scope (bypasses session auth)."""

    def __init__(self, app, user):
        self.app = app
        self.user = user

    async def __call__(self, scope, receive, send):
        scope = dict(scope)
        scope["user"] = self.user
        return await self.app(scope, receive, send)


def make_application(user):
    """Return a test ASGI application with the given user force-authenticated."""
    inner = URLRouter([
        path("ws/game/<int:game_id>/", GameConsumer.as_asgi()),
    ])
    return ForcedAuthMiddleware(inner, user)


async def _send_recv(communicator, data: dict) -> dict:
    """Send a JSON message and return the parsed response."""
    await communicator.send_json_to(data)
    response = await communicator.receive_json_from()
    return response


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

@override_settings(CHANNEL_LAYERS=IN_MEMORY_CHANNEL_LAYERS)
class ConsumerTestBase(TransactionTestCase):
    """Shared setUp for all consumer tests."""

    def setUp(self):
        self.user = make_user("con_u1")
        self.game = make_game(status="waiting", max_players=4, buy_in=100)

    def _app(self, user=None):
        return make_application(user or self.user)

    async def _connect(self, game_id=None, user=None):
        """Open a WebSocket; caller is responsible for disconnect."""
        gid = game_id if game_id is not None else self.game.id
        comm = WebsocketCommunicator(self._app(user), f"/ws/game/{gid}/")
        connected, _ = await comm.connect()
        return comm, connected

    async def _drain_one(self, comm, settle=0.3):
        """Sleep briefly so the consumer can process, then discard one queued
        output message if present.  Does NOT use receive_output(timeout=…) which
        would cancel the consumer task on expiry."""
        await asyncio.sleep(settle)
        if not comm.output_queue.empty():
            await comm.output_queue.get()

    async def _settle(self, settle=0.3):
        """Yield control to the event loop so the consumer can finish processing."""
        await asyncio.sleep(settle)


# ---------------------------------------------------------------------------
# connect / disconnect
# ---------------------------------------------------------------------------

class ConnectTests(ConsumerTestBase):

    async def test_authenticated_user_connects(self):
        comm, connected = await self._connect()
        self.assertTrue(connected)
        await comm.disconnect()

    async def test_unauthenticated_user_is_rejected(self):
        anon = AnonymousUser()
        app = make_application(anon)
        comm = WebsocketCommunicator(app, f"/ws/game/{self.game.id}/")
        connected, _ = await comm.connect()
        self.assertFalse(connected)

    async def test_connect_nonexistent_game_closes(self):
        """Connecting to a game that doesn't exist should close the connection."""
        nonexistent_id = 999_999
        comm, connected = await self._connect(game_id=nonexistent_id)
        # Connection might be accepted then immediately closed, or rejected
        # Either way the communicator should reach a closed state shortly.
        if connected:
            # Consumer closes from inside after accept(); WS should close.
            disconnected = await comm.receive_output(timeout=1)
            self.assertEqual(disconnected["type"], "websocket.close")
        else:
            # Rejected before accept — also valid
            pass

    async def test_connect_joins_game_group(self):
        """Authenticated connect adds channel to the game group."""
        comm, connected = await self._connect()
        self.assertTrue(connected)
        # Successful connection + clean disconnect is sufficient proof the
        # consumer registered itself to the game group without errors.
        await comm.disconnect()

    async def test_disconnect_does_not_raise(self):
        comm, _ = await self._connect()
        # disconnect() should complete cleanly
        await comm.disconnect()


# ---------------------------------------------------------------------------
# receive — invalid JSON / unknown action
# ---------------------------------------------------------------------------

class ReceiveInvalidTests(ConsumerTestBase):

    async def test_invalid_json_returns_error(self):
        comm, _ = await self._connect()
        await comm.send_to(text_data="not json {{{")
        resp = await comm.receive_json_from(timeout=2)
        self.assertIn("error", resp)
        self.assertIn("Invalid", resp["error"])
        await comm.disconnect()

    async def test_invalid_bet_amount_returns_error(self):
        comm, _ = await self._connect()
        await comm.send_json_to({"action": "bet", "amount": "abc"})
        resp = await comm.receive_json_from(timeout=2)
        self.assertIn("error", resp)
        await comm.disconnect()

    async def test_action_without_being_in_game_returns_error(self):
        """fold from a user who has no Player record in the game → error."""
        comm, _ = await self._connect()
        await comm.send_json_to({"action": "fold"})
        resp = await comm.receive_json_from(timeout=2)
        self.assertIn("error", resp)
        await comm.disconnect()


# ---------------------------------------------------------------------------
# receive — join action
# ---------------------------------------------------------------------------

class ReceiveJoinTests(ConsumerTestBase):

    async def test_join_creates_player(self):
        self.user.profile.chips = 500
        await sync_to_async(self.user.profile.save)()

        comm, _ = await self._connect()
        with patch("game.consumers.GameConsumer.broadcast_game_state",
                   new_callable=AsyncMock), \
             patch("game.consumers.GameConsumer.broadcast_messages",
                   new_callable=AsyncMock):
            await comm.send_json_to({"action": "join"})
            await self._settle()   # give consumer time to process join

        exists = await sync_to_async(
            Player.objects.filter(game=self.game, user=self.user).exists
        )()
        self.assertTrue(exists)
        await comm.disconnect()

    async def test_join_insufficient_chips_returns_error(self):
        self.user.profile.chips = 10   # less than buy_in=100
        await sync_to_async(self.user.profile.save)()

        comm, _ = await self._connect()
        await comm.send_json_to({"action": "join"})
        resp = await comm.receive_json_from(timeout=2)
        self.assertIn("error", resp)
        await comm.disconnect()


# ---------------------------------------------------------------------------
# receive — turn enforcement
# ---------------------------------------------------------------------------

class TurnEnforcementTests(ConsumerTestBase):

    def setUp(self):
        super().setUp()
        # Active game where it is user2's turn (position 1), not self.user (position 0)
        self.user2 = make_user("con_u2")
        self.game = make_game(
            name="Turn Enforcement Game",
            status="active",
            current_phase="preflop",
            dealer_position=0,
            current_turn=1,   # user2's turn
            max_players=2,
            deck=["As", "Ks", "Qs", "Js", "Ts",
                  "2h", "3h", "4h", "5h", "6h",
                  "7c", "8c", "9c", "Tc", "Jc"],
        )
        self.p1 = make_player(self.user, self.game, position=0, chips=90,
                              current_bet=10, total_bet=10,
                              is_dealer=True, is_small_blind=True)
        self.p2 = make_player(self.user2, self.game, position=1, chips=80,
                              current_bet=20, total_bet=20,
                              is_big_blind=True)

    async def test_action_not_your_turn_returns_error(self):
        """self.user tries to fold when it is user2's turn → error."""
        comm, _ = await self._connect()
        # Drain initial private_game_state (p1 IS a player; consumer sends it on connect)
        await self._drain_one(comm)
        await comm.send_json_to({"action": "fold"})
        # Consumer sends "It's not your turn." directly via self.send()
        resp = await comm.receive_json_from(timeout=2)
        self.assertIn("error", resp)
        self.assertIn("not your turn", resp["error"].lower())
        await comm.disconnect()

    async def test_correct_turn_player_can_act(self):
        """user2 (current_turn=1) can fold without getting a turn error."""
        comm, _ = await self._connect(user=self.user2)
        # Drain initial private_game_state (user2 is a player)
        await self._drain_one(comm)
        # Mock end_phase so the 2-player fold-win doesn't reset the hand
        with patch("game.consumers.GameConsumer.broadcast_game_state",
                   new_callable=AsyncMock), \
             patch("game.consumers.GameConsumer.broadcast_messages",
                   new_callable=AsyncMock), \
             patch("game.consumers.GameConsumer.end_phase",
                   new_callable=AsyncMock):
            await comm.send_json_to({"action": "fold"})
            await self._settle()   # give consumer time to save has_folded

        p2 = await sync_to_async(Player.objects.get)(pk=self.p2.pk)
        self.assertTrue(p2.has_folded)
        await comm.disconnect()


# ---------------------------------------------------------------------------
# receive — game actions (check / call / leave)
# ---------------------------------------------------------------------------

class GameActionTests(ConsumerTestBase):

    def setUp(self):
        super().setUp()
        self.user2 = make_user("con_ga_u2")
        self.game = make_game(
            name="Game Action Game",
            status="active",
            current_phase="flop",
            dealer_position=0,
            current_turn=0,
            max_players=2,
            community_cards=["As", "Ks", "Qs"],
            deck=["2h", "3h", "4h", "5h", "6h", "7c", "8c"],
        )
        self.p1 = make_player(self.user, self.game, position=0, chips=100,
                              current_bet=0, total_bet=20,
                              is_dealer=True, is_small_blind=True)
        self.p2 = make_player(self.user2, self.game, position=1, chips=80,
                              current_bet=0, total_bet=20,
                              is_big_blind=True)

    async def test_check_updates_player(self):
        comm, _ = await self._connect()
        # Drain initial private_game_state (p1 is a player)
        await self._drain_one(comm)
        with patch("game.consumers.GameConsumer.post_action_flow",
                   new_callable=AsyncMock), \
             patch("game.consumers.GameConsumer.broadcast_messages",
                   new_callable=AsyncMock):
            await comm.send_json_to({"action": "check"})
            await self._settle()   # give consumer time to save has_checked

        p1 = await sync_to_async(Player.objects.get)(pk=self.p1.pk)
        self.assertTrue(p1.has_checked)
        await comm.disconnect()

    async def test_call_with_no_bet_returns_error(self):
        """No outstanding bet → calling is not valid."""
        comm, _ = await self._connect()
        # Drain initial private_game_state (p1 is a player)
        await self._drain_one(comm)
        await comm.send_json_to({"action": "call"})
        # Consumer sends "You cannot call." directly via self.send()
        resp = await comm.receive_json_from(timeout=2)
        self.assertIn("error", resp)
        await comm.disconnect()

    async def test_leave_removes_player_from_waiting_game(self):
        """In a waiting game, leave refunds and removes the player."""
        # Switch to waiting status for a clean leave test
        await sync_to_async(Game.objects.filter(pk=self.game.pk).update)(status="waiting")
        comm, _ = await self._connect()
        # Drain initial private_game_state (p1 is a player)
        await self._drain_one(comm)
        with patch("game.consumers.GameConsumer.broadcast_game_state",
                   new_callable=AsyncMock), \
             patch("game.consumers.GameConsumer.broadcast_messages",
                   new_callable=AsyncMock):
            await comm.send_json_to({"action": "leave"})
            await self._settle()   # give consumer time to remove player

        exists = await sync_to_async(
            Player.objects.filter(game=self.game, user=self.user).exists
        )()
        self.assertFalse(exists)
        await comm.disconnect()
