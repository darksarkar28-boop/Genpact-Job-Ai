from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.db import IntegrityError, transaction
from django.db.models import F
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .forms import EducationSet, ExperienceSet, ImportForm, ProfileForm
from .importers import ImportProblem, read_upload
from .models import Profile
from .profile_data import MAX_ENTRIES, SCALARS, merge_profiles, skill_list


@require_GET
def home(request):
    return redirect("workspace" if request.user.is_authenticated else "signup")


@sensitive_post_parameters()
@never_cache
@require_http_methods(["GET", "POST"])
def signup(request):
    if request.user.is_authenticated:
        return redirect("workspace")
    form = UserCreationForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                user = form.save()
        except IntegrityError:
            if not form._meta.model.objects.filter(username=form.cleaned_data["username"]).exists():
                raise
            form.add_error("username", "This username is already in use.")
        else:
            login(request, user)
            return redirect("workspace")
    return render(request, "webapp/signup.html", {"form": form})


def _owned_profile(request):
    return Profile.objects.get_or_create(user=request.user)[0]


def _workspace(request, profile, upload, status=200):
    return render(request, "webapp/workspace.html", {"profile": profile, "upload": upload}, status=status)


@login_required
@never_cache
@require_GET
def workspace(request):
    return _workspace(request, _owned_profile(request), ImportForm())


def _editor(request, profile, draft, *, imported=False, submitted=None):
    initial = {**draft, "skills": ", ".join(draft.get("skills", [])), "revision": profile.revision}
    form = ProfileForm(submitted, initial=initial)
    experience = ExperienceSet(submitted, initial=draft.get("experience") or [{}], prefix="experience")
    education = EducationSet(submitted, initial=draft.get("education") or [{}], prefix="education")
    status = 200
    if submitted is not None:
        if all([form.is_valid(), experience.is_valid(), education.is_valid()]):
            data = {field: form.cleaned_data[field] for field in SCALARS}
            data["skills"] = skill_list(form.cleaned_data["skills"])
            for group in (experience, education):
                records = []
                for item in group.cleaned_data:
                    record = {key: value for key, value in item.items() if key != "DELETE"}
                    if not item.get("DELETE") and any(record.values()):
                        records.append(record)
                data[group.prefix] = records
            # Tie the update to both the owner and the version that the user actually reviewed.
            saved = Profile.objects.filter(user=request.user, revision=form.cleaned_data["revision"]).update(
                data=data, revision=F("revision") + 1, updated_at=timezone.now(),
            )
            if saved:
                messages.success(request, "Your master profile has been saved.")
                return redirect("workspace")
            form.add_error(None, "Your saved profile changed in another tab. Open it and review your changes again.")
            status = 409
        else:
            status = 422
    return render(request, "webapp/editor.html", {
        "form": form, "experience": experience, "education": education, "imported": imported,
    }, status=status)


@login_required
@never_cache
@require_POST
def import_profile(request):
    profile = _owned_profile(request)
    upload = ImportForm(request.POST, request.FILES)
    if upload.is_valid():
        try:
            draft = merge_profiles(profile.data, read_upload(upload.cleaned_data["document"], ai=upload.cleaned_data["ai_assist"]))
            if any(len(draft[key]) > MAX_ENTRIES for key in ("education", "experience")):
                raise ImportProblem("This import would exceed 50 entries per section. Choose a shorter version.")
        except ImportProblem as exc:
            upload.add_error("document", str(exc))
        else:
            return _editor(request, profile, draft, imported=True)
    return _workspace(request, profile, upload, status=422)


@login_required
@sensitive_post_parameters()
@never_cache
@require_http_methods(["GET", "POST"])
def edit_profile(request):
    profile = _owned_profile(request)
    return _editor(request, profile, profile.data, submitted=request.POST if request.method == "POST" else None)
