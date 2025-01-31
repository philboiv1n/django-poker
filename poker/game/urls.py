"""
urls.py
=======

Defines the main URL routes for the Django poker application. Each path connects
a URL endpoint to its corresponding view, handling actions like user login,
profile management, real-time testing, and table interactions (creating, joining,
leaving a game).
"""

from django.urls import path
from django.contrib.auth import views as auth_views
from game.views import websocket_test
from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="game/login.html"),
        name="login",
    ),
    path("profile/", views.profile, name="profile"),
    path("stats/", views.stats, name="stats"),
    path("logout_validation", views.logout_validation, name="logout_validation"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("test/", websocket_test, name="websocket_test"),
    path("table/<int:game_id>/", views.table, name="table"),
    path("join/<int:game_id>/", views.join_table, name="join_table"),
    path("leave/<int:game_id>/", views.leave_table, name="leave_table"),
]
