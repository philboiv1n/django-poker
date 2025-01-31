"""
apps.py
=======

Configures the 'game' Django application. Each Django app can have an AppConfig
class that Django uses to identify and initialize the app. This file is also
commonly used to register signals or perform one-time startup logic.
"""

from django.apps import AppConfig


class GameConfig(AppConfig):
    """
    The AppConfig subclass for the 'game' app. Django uses this class to set
    application-level configurations, like the default primary key field type
    or name of the app. You can override the ready() method here if you need
    to run startup code (e.g., loading signals or scheduling tasks).
    """

    # Specifies the type of auto-generated primary key fields for models in this app.
    default_auto_field = "django.db.models.BigAutoField"

    # The name attribute must match the app folder ("game") for Django to load it properly.
    name = "game"
