from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.http import Http404
from .forms import ProfileForm
from .models import Game, Player


@login_required
def websocket_test(request):
    return render(request, "game/test.html")


# @login_required
# def dashboard(request):
#     profile = request.user.profile  # Access the Profile model via the User
#     nickname = profile.nickname
#     return render(request, 'game/dashboard.html', {'nickname': nickname})
#     #return render(request, "game/dashboard.html")


@login_required
def dashboard(request):
    profile = request.user.profile
    available_games = Game.objects.filter(status="waiting")  # Only show games that haven't started
    return render(request, "game/dashboard.html", {"games": available_games, "nickname": profile.nickname})


@login_required
def profile(request):

    # Access the current user's profile
    # profile = request.user.profile

    # Handle form submission for updating the profile
    if request.method == "POST":
        form = ProfileForm(request.POST, instance=request.user.profile)
        if form.is_valid():
            form.save()
            return redirect("profile")
    else:
        form = ProfileForm(instance=request.user.profile)
    
    # Pass the profile and form to the template
    return render(request, "game/profile.html", {
        "form": form,
       # "profile": profile, 
    })


# @login_required
# def join_game(request):
#     if request.method == "POST":
#         code = request.POST.get("code", "").upper()
#         try:
#             game = Game.objects.get(code=code)
#             if game.players.count() >= game.max_players:
#                 return render(request, "game/join_game.html", {"error": "Game is full."})
            
#             # Add the user as a player if they're not already in the game
#             Player.objects.get_or_create(user=request.user, game=game, defaults={"chips": game.buy_in})
#             return redirect("lobby", code=game.code)  # Redirect to the game lobby
#         except Game.DoesNotExist:
#             return render(request, "game/join_game.html", {"error": "Invalid game code."})
#     return render(request, "game/join_game.html")


@login_required
def join_game(request, code):
    game = get_object_or_404(Game, code=code)

    # Ensure the game isn't full
    if game.players.count() >= game.max_players:
        return render(request, "game/dashboard.html", {"error": "Game is full.", "games": Game.objects.filter(status="waiting")})

    # Add the user as a player if they're not already in the game
    Player.objects.get_or_create(user=request.user, game=game, defaults={"chips": game.buy_in})
    
    return redirect("lobby", code=game.code)  # Redirect to game lobby




@login_required
def lobby(request, code):
    try:
        game = Game.objects.get(code=code)
        players = game.players.all()
        return render(request, "game/lobby.html", {"game": game, "players": players})
    except Game.DoesNotExist:
        raise Http404("Game does not exist")