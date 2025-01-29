from django.urls import path
from django.contrib.auth import views as auth_views
from game.views import websocket_test
from . import views

urlpatterns = [
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="game/login.html"),
        name="login",
    ),
    path("profile/", views.profile, name="profile"),
    path("", views.dashboard, name="dashboard"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("test/", websocket_test, name="websocket_test"),
]
