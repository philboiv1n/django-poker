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
    path("table/<int:game_id>/", views.table, name="table"),
    path(
        "password_change/",
        auth_views.PasswordChangeView.as_view(),
        name="password_change",
    ),
    path(
        "password_change_done/",
        auth_views.PasswordChangeDoneView.as_view(),
        name="password_change_done",
    ),
]
