from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from .forms import ProfileForm


@login_required
def websocket_test(request):
    return render(request, "game/test.html")


@login_required
def dashboard(request):
    profile = request.user.profile  # Access the Profile model via the User
    nickname = profile.nickname
    return render(request, 'game/dashboard.html', {'nickname': nickname})
    #return render(request, "game/dashboard.html")


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