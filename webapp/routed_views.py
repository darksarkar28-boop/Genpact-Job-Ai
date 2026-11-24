"""Account-scoped workbench for explicitly authorized model operations."""
import json
import re

from django import forms
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods

from tools.model_router import RoutingError
from tools.routed_workflows import run
from .models import Profile


class WorkflowForm(forms.Form):
    action = forms.ChoiceField(choices=[("rank", "Rank postings"), ("deduplicate", "Find duplicate postings"),
                                       ("apply", "Draft and review application"), ("research", "Company research leads"), ("verify-research", "Recheck company research sources")])
    postings = forms.CharField(required=False, max_length=80000, widget=forms.Textarea(attrs={"rows": 10}),
                               help_text="Paste one posting per block, separated by a line containing ---.")
    employer_id = forms.CharField(required=False, max_length=169,
                                  help_text="Verified registry namespace and employer ID, not a company name.")
    consent = forms.BooleanField(required=False, label="Allow the configured external AI provider to process this input and (when needed) my saved profile.")

    def clean(self):
        data = super().clean()
        action = data.get("action")
        if action not in ("research", "verify-research") and not data.get("consent"):
            self.add_error("consent", "Explicit consent is required for AI requests.")
        if action in ("research", "verify-research") and not data.get("employer_id"):
            self.add_error("employer_id", "Provide a verified employer identifier.")
        blocks = [part.strip() for part in re.split(r"(?m)^---\s*$", data.get("postings", "")) if part.strip()]
        if action != "research" and not 1 <= len(blocks) <= 10:
            self.add_error("postings", "Provide one to ten postings.")
        if action == "apply" and len(blocks) != 1:
            self.add_error("postings", "Application drafting requires exactly one posting.")
        data["jobs"] = [{"key": str(i + 1), "text": text} for i, text in enumerate(blocks)]
        return data


@login_required
@never_cache
@sensitive_post_parameters()
@require_http_methods(["GET", "POST"])
def workbench(request):
    form = WorkflowForm(request.POST if request.method == "POST" else None)
    output = None
    status = 200
    if request.method == "POST":
        status = 422
        if form.is_valid():
            data = form.cleaned_data
            profile = Profile.objects.filter(user=request.user).first()
            action = data["action"]
            payload = {"profile": profile.data if profile and profile.revision else {}, "jobs": data["jobs"],
                       "employer_id": data["employer_id"]}
            if data["jobs"]:
                payload["job"] = data["jobs"][0]
            try:
                output = run(action, payload, settings.DATA_DIR)
                output = json.dumps(output, ensure_ascii=False, indent=2)
                status = 200
            except (RoutingError, ValueError, TypeError):
                form.add_error(None, "Unable to complete the requested workflow. Check inputs, confirmed profile and provider configuration.")
    return render(request, "webapp/workbench.html", {"form": form, "output": output}, status=status)
