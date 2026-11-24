from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django import forms


class PreferencesForm(forms.Form):
    market = forms.ChoiceField(choices=[("all", "All markets"), ("global", "Global"), ("india", "India")])
    role = forms.CharField(max_length=200, required=False, help_text="Title contains this text")
    location = forms.CharField(max_length=200, required=False, help_text="Location contains this text")
    zone = forms.CharField(max_length=80, help_text="IANA time zone, e.g. Asia/Kolkata or Europe/London")
    hour = forms.IntegerField(min_value=5, max_value=11, label="Morning digest local hour (5–11)")
    email = forms.EmailField(required=False)
    opted_in = forms.BooleanField(required=False, label="Send my daily job digest by email")

    def clean_zone(self):
        value = self.cleaned_data["zone"]
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise forms.ValidationError("Enter a valid IANA time zone.")
        return value
