import json

from django import forms
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods

from .career_planning import compensation_band, counter_offer, skill_gaps
from .models import Profile


class CareerForm(forms.Form):
    employer = forms.CharField(max_length=200)
    role = forms.CharField(max_length=200)
    location = forms.CharField(max_length=200)
    level = forms.CharField(max_length=100)
    currency = forms.RegexField(regex=r"^[A-Za-z]{3}$", max_length=3, initial="INR")
    since = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    offer = forms.DecimalField(required=False, min_value=0.01, max_value=10**12, decimal_places=2)
    target = forms.DecimalField(required=False, min_value=0.01, max_value=10**12, decimal_places=2)
    salaries = forms.CharField(required=False, max_length=80000, widget=forms.Textarea(attrs={"rows": 5}),
        help_text="JSON array: role, location, level, currency, annual_base, date (YYYY-MM-DD), source (URL).")
    postings = forms.CharField(required=False, max_length=80000, widget=forms.Textarea(attrs={"rows": 5}),
        help_text="JSON array: role, source (URL), required_skills (list of strings).")

    def clean(self):
        data = super().clean()
        if bool(data.get("offer")) != bool(data.get("target")):
            raise forms.ValidationError("Supply both your actual offer and proposed target, or neither.")
        if data.get("since") and data["since"] > timezone.localdate():
            self.add_error("since", "Choose a start date no later than today.")
        for key in ("salaries", "postings"):
            if key in data:
                try:
                    data[key] = json.loads(data[key] or "[]")
                except ValueError:
                    self.add_error(key, "Enter a valid JSON array.")
        return data


@login_required
@never_cache
@sensitive_post_parameters()
@require_http_methods(["GET", "POST"])
def career_planning(request):
    profile = Profile.objects.filter(user=request.user, revision__gt=0).first()
    form = CareerForm(request.POST if request.method == "POST" else None,
                      initial={"location": profile.data.get("location", "") if profile else "",
                               "since": timezone.localdate().replace(month=1, day=1)})
    context = {"profile": profile, "form": form}
    status = 200
    if request.method == "POST":
        status = 422
        if not profile:
            form.add_error(None, "Save your master profile first.") if form.is_bound and form.is_valid() else None
        elif form.is_valid():
            d = form.cleaned_data
            try:
                band = compensation_band(d["salaries"], role=d["role"], location=d["location"],
                    level=d["level"], currency=d["currency"], since=d["since"])
                gaps, posting_count = skill_gaps(d["postings"], profile.data.get("skills", []))
                draft = counter_offer(employer=d["employer"], role=d["role"], currency=d["currency"].upper(),
                    offer=d["offer"], target=d["target"], band=band["band"]) if d.get("offer") else ""
            except ValueError as exc:
                form.add_error(None, str(exc))
            else:
                context.update(band=band, gaps=gaps, posting_count=posting_count, draft=draft)
                status = 200
    return render(request, "webapp/career_planning.html", context, status=status)
