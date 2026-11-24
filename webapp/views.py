from django.conf import settings
from django.http import JsonResponse
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.db import IntegrityError, transaction
from django.db.models import F
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
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
    next_url = request.POST.get("next") or request.GET.get("next", "")
    if not next_url.startswith("/") or not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        next_url = ""
    if request.user.is_authenticated:
        return redirect(next_url or "workspace")
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
            return redirect(next_url or "workspace")
    return render(request, "webapp/signup.html", {"form": form, "next": next_url})


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



@login_required
@sensitive_post_parameters("claims", "company_citations", "token")
@never_cache
@require_http_methods(["GET", "POST"])
def application_review(request):
    """Review then export the actual uploaded PDF bytes; never trust a prior report."""
    import hashlib
    import io
    import json
    import zipfile
    from django.core import signing
    from django.http import HttpResponse
    from .application_gate import GateError, audit, parse_json_list
    from .forms import ApplicationReviewForm

    profile = _owned_profile(request)
    if request.method == "GET":
        return render(request, "webapp/application_review.html", {
            "form": ApplicationReviewForm(initial={
                "profile_revision": profile.revision,
                "visual_headings": "Experience\nEducation",
                "keywords": '{"keywords":[{"term":"Python","priority":"required"}]}',
                "claims": "[]", "company_citations": "[]",
            }),
        })

    if request.POST.get("action") not in ("review", "export"):
        return HttpResponse("Unknown review operation", status=400)
    form = ApplicationReviewForm(request.POST, request.FILES)
    if not form.is_valid():
        return render(request, "webapp/application_review.html", {"form": form}, status=422)
    values = form.cleaned_data
    if values["profile_revision"] != profile.revision or not profile.revision:
        form.add_error(None, "Your master profile has changed or has not been saved. Reload and review again.")
        return render(request, "webapp/application_review.html", {"form": form}, status=409)
    try:
        terms_obj = json.loads(values["keywords"])
        if not isinstance(terms_obj, dict):
            raise GateError("Posting keywords must be a JSON object")
        keywords = terms_obj.get("keywords")
        claims = parse_json_list(values["claims"], "Candidate claims", maximum=60)
        company_citations = parse_json_list(values["company_citations"], "Company citations", maximum=8)
        cv_data = values["cv_pdf"].read()
        cover_data = values["cover_pdf"].read() if values.get("cover_pdf") else b""
        headings = [line.strip() for line in values["visual_headings"].splitlines() if line.strip()]
    except (ValueError, TypeError) as exc:
        form.add_error(None, f"Cannot check the submission: {exc}")
        return render(request, "webapp/application_review.html", {"form": form}, status=422)

    report = audit(
        profile.data, cv_data, cover_pdf=cover_data, keywords=keywords, claims=claims,
        company_citations=company_citations, employer=values["employer"],
        visual_headings=headings,
    )
    # Bind approval to the owner, profile version, actual PDF bytes, and every
    # input governing the audit. The signed approval cannot be replayed for edits.
    specification = {
        "keywords": keywords, "claims": claims, "citations": company_citations,
        "employer": values["employer"], "headings": headings,
    }
    spec_hash = hashlib.sha256(json.dumps(specification, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    approved = {"user": request.user.pk, "revision": profile.revision,
                "pdf": report.get("pdf_sha256"), "spec": spec_hash}
    token = signing.dumps(approved, salt="job-ai-application-release-v1") if report["allowed"] else None

    # Another tab may have changed the profile while PDFs and citations were checked.
    profile.refresh_from_db()
    if values["profile_revision"] != profile.revision:
        report["allowed"] = False
        report["errors"].append("Profile updated during review; upload and review again.")
        token = None

    if request.POST["action"] == "export":
        try:
            previous = signing.loads(request.POST.get("token", ""), salt="job-ai-application-release-v1", max_age=900)
        except signing.BadSignature:
            previous = None
        if previous != approved or not report["allowed"] or values["profile_revision"] != profile.revision:
            report["allowed"] = False
            report["errors"].append("Export denied: a current approved review of these exact files and inputs is required.")
        else:
            out = io.BytesIO()
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("validated_cv.pdf", cv_data)
                if cover_data:
                    archive.writestr("validated_cover_letter.pdf", cover_data)
                public_report = {"allowed": True, "profile_revision": profile.revision,
                                 "checked_at": timezone.now().isoformat(),
                                 "coverage": report.get("coverage"),
                                 "verified_sources": report.get("verified_sources", []),
                                 "pdf_sha256": report.get("pdf_sha256"),
                                 "note": report.get("note")}
                archive.writestr("verification_report.json", json.dumps(public_report, ensure_ascii=False, indent=2))
            response = HttpResponse(out.getvalue(), content_type="application/zip")
            response["Content-Disposition"] = 'attachment; filename="verified_application.zip"'
            response["Cache-Control"] = "no-store"
            return response
    return render(request, "webapp/application_review.html", {
        "form": form, "report": report, "token": token if request.POST["action"] == "review" else None,
        "requires_reupload": True,
    }, status=200 if report["allowed"] else 422)


@login_required
@never_cache
@require_GET
def company_research(request):
    from tools.routed_workflows import research
    try:
        employer_id = request.GET.get("employer_id")
        if not employer_id:
            raise ValueError("missing employer identifier")
        result = research(employer_id, settings.DATA_DIR)
    except ValueError:
        return JsonResponse({"error": "Use a verified registry namespace and employer identifier."}, status=400)
    if result is None:
        return JsonResponse({"error": "No fresh public research is available."}, status=404)
    return JsonResponse(result)
