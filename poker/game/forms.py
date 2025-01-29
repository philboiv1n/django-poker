from django import forms
from .models import Profile


class ProfileForm(forms.ModelForm):

    avatar_color = forms.CharField(
        widget=forms.TextInput(attrs={'type': 'color', 'class': 'form-control'})
    )

    class Meta:
        model = Profile
        fields = ["nickname", "avatar_color"]
