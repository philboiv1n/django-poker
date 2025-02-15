"""
routing.py
==========

Defines the WebSocket URL routing for real-time communications via Django Channels.
Each path in `websocket_urlpatterns` maps to a specific Consumer class, handling
messages and events for multiplayer interactions (e.g., chat, game actions).
"""

from django.urls import path
from .consumers import GameConsumer

# A list of URL patterns for WebSocket connections.
# Each entry maps a URL path to an ASGI consumer.
websocket_urlpatterns = [
    path("ws/game/<int:game_id>/", GameConsumer.as_asgi()), # Game table
]