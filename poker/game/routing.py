"""
routing.py
==========

Defines the WebSocket URL routing for real-time communications via Django Channels.
Each path in `websocket_urlpatterns` maps to a specific Consumer class, handling
messages and events for multiplayer interactions (e.g., chat, game actions).
"""

from django.urls import path
from . import consumers

# A list of URL patterns for WebSocket connections.
# Each entry maps a URL path to an ASGI consumer.
websocket_urlpatterns = [
    # 'ws/game/<str:room_code>/' is the WebSocket endpoint
    # that connects to the GameConsumer for real-time
    # game interactions in a specific room identified by `room_code`.
    path("ws/game/<str:room_code>/", consumers.GameConsumer.as_asgi()),
]
