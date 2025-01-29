from django import forms
from .models import Profile, Game


class ProfileForm(forms.ModelForm):

    avatar_color = forms.CharField(
        widget=forms.TextInput(attrs={'type': 'color', 'class': 'form-control'})
    )

    class Meta:
        model = Profile
        fields = ["nickname", "avatar_color"]


# This should be removed
class GameCreationForm(forms.ModelForm):
    class Meta:
        model = Game
        fields = ['buy_in', 'small_blind', 'big_blind', 'blind_timer', 'max_players']

class GameJoinForm(forms.Form):
    code = forms.CharField(max_length=8, label="Enter Game Code")