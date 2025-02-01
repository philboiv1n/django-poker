"""
forms.py
========

This module defines Django forms used in the poker application.
Forms handle user input and validation, translating data into
model objects or updating existing records.

"""

from django import forms
from .models import Profile


class ProfileForm(forms.ModelForm):
    """
    A form for updating the user's profile with a nickname and color selection.

    Inherits from ModelForm to automatically map certain fields
    from the Profile model, simplifying validation and data handling.
    """

    # This field overrides the default widget to provide a color picker in the browser.
    # The `attrs` dictionary adds CSS classes and an HTML input type of "color".
    avatar_color = forms.CharField(
        widget=forms.TextInput(attrs={"type": "color", "class": "form-control"})
    )

    class Meta:
        """
        Meta class defines which model fields should be included
        and how they map to the form fields.
        """

        model = Profile
        fields = ["avatar_color"]  # The fields editable by the user.
