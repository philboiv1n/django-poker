from django.shortcuts import render
from django.http import HttpResponse
from django.contrib.auth.models import User


def home(request):
    return render(request, "home.html")


# def health_check(request):
#     return HttpResponse("OK")


def show_info(request):
    all_users = User.objects.values()
    return HttpResponse(all_users)
