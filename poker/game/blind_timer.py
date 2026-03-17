"""
blind_timer.py
==============

Manages per-game asyncio tasks that double the blinds on a configurable interval.

Usage:
    start_blind_timer(game_id, channel_layer)  — call when a hand starts
    cancel_blind_timer(game_id)                — call when a game finishes
"""

import asyncio
import json
import logging
from django.utils.timezone import now

logger = logging.getLogger(__name__)

# game_id → asyncio.Task
_blind_tasks: dict = {}


async def _blind_timer_loop(game_id: int, channel_layer) -> None:
    """
    Sleeps until the next blind increase, doubles the blinds, broadcasts the
    change to all clients in the game room, then reschedules itself.
    Stops when the game is no longer active or blind_timer is 0.
    """
    from game.models import Game  # local import to avoid circular import

    while True:
        try:
            game = await Game.objects.aget(id=game_id)
        except Game.DoesNotExist:
            logger.debug("blind_timer: game %s no longer exists, stopping", game_id)
            break

        if game.status != "active" or game.blind_timer == 0 or game.blinds_last_increased_at is None:
            logger.debug("blind_timer: game %s not active or timer disabled, stopping", game_id)
            break

        interval_seconds = game.blind_timer * 60
        elapsed = (now() - game.blinds_last_increased_at).total_seconds()
        wait = max(0.0, interval_seconds - elapsed)

        logger.debug("blind_timer: game %s waiting %.1fs for next increase", game_id, wait)
        await asyncio.sleep(wait)

        # Re-fetch after sleep to get the latest state
        try:
            game = await Game.objects.aget(id=game_id)
        except Game.DoesNotExist:
            break

        if game.status != "active" or game.blind_timer == 0:
            break

        game.small_blind *= 2
        game.big_blind *= 2
        game.blinds_last_increased_at = now()
        await game.asave(update_fields=["small_blind", "big_blind", "blinds_last_increased_at"])

        logger.debug(
            "blind_timer: game %s blinds increased → %s/%s",
            game_id, game.small_blind, game.big_blind,
        )

        await channel_layer.group_send(
            f"game_{game_id}",
            {
                "type": "broadcast_send_helper",
                "data": {
                    "type": "blind_increase",
                    "small_blind": game.small_blind,
                    "big_blind": game.big_blind,
                    "blind_timer": game.blind_timer,
                    "blinds_last_increased_at": game.blinds_last_increased_at.isoformat(),
                    "messages": [
                        f"⬆️ Blinds increased to {game.small_blind} / {game.big_blind}!"
                    ],
                },
            },
        )


def start_blind_timer(game_id: int, channel_layer) -> None:
    """Schedule a blind increase loop for the given game. Replaces any existing task."""
    cancel_blind_timer(game_id)
    task = asyncio.ensure_future(_blind_timer_loop(game_id, channel_layer))
    _blind_tasks[game_id] = task
    logger.debug("blind_timer: started for game %s", game_id)


def cancel_blind_timer(game_id: int) -> None:
    """Cancel the blind increase task for a game if one exists."""
    task = _blind_tasks.pop(game_id, None)
    if task and not task.done():
        task.cancel()
        logger.debug("blind_timer: cancelled for game %s", game_id)
