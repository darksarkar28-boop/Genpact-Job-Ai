import hashlib
import io

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.db import IntegrityError, transaction
from django.db.models import F
from django.http import FileResponse, Http404, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .documents import (
    DocumentProblem, TEMPLATES, artifact_file, draft_preview, prepare_document_request,
    queue_document_job, read_review_pdf, release_approval_matches, retry_document_job, review_digest, review_pdf_file,
)
from .forms import EducationSet, ExperienceSet, ImportForm, ProfileForm
from .importers import ImportProblem, read_upload
from .models import DocumentJob, Profile, TemplateReview
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


def _document_page(
    request, *, status=200, form_data=None, export_error="", preview=None,
    release_report=None, release_form_data=None,
):
    profile = _owned_profile(request)
    jobs = list(DocumentJob.objects.filter(user=request.user).order_by("-created_at")[:25])
    for job in jobs:
        job.release_approved = release_approval_matches(job, request.user.pk, profile.revision)
    successful_cvs = [
        job for job in jobs
        if job.status == DocumentJob.Status.SUCCEEDED
        and job.document_type == DocumentJob.DocumentType.CV
        and artifact_file(job) and review_pdf_file(job)
    ]
    successful_covers = [
        job for job in jobs
        if job.status == DocumentJob.Status.SUCCEEDED
        and job.document_type == DocumentJob.DocumentType.COVER_LETTER
        and artifact_file(job) and review_pdf_file(job)
    ]
    approved = {
        review.template_key: review
        for review in TemplateReview.objects.filter(approved=True).exclude(feedback="")
    }
    templates = []
    for item in TEMPLATES:
        enriched = dict(item)
        enriched["review"] = approved.get(item["key"])
        templates.append(enriched)
    return render(request, "webapp/documents.html", {
        "profile": profile,
        "jobs": jobs,
        "templates": templates,
        "form_data": form_data or {},
        "export_error": export_error,
        "preview": preview,
        "release_report": release_report,
        "release_form_data": release_form_data or {},
        "default_headings": "Summary\nSkills\nExperience\nEducation",
        "successful_cvs": successful_cvs,
        "successful_covers": successful_covers,
        "has_active_jobs": any(job.status in (DocumentJob.Status.QUEUED, DocumentJob.Status.RUNNING) for job in jobs),
    }, status=status)


@login_required
@never_cache
@require_GET
def document_center(request):
    return _document_page(request)


@login_required
@never_cache
@require_POST
def preview_document(request):
    profile = _owned_profile(request)
    try:
        prepared = prepare_document_request(profile, request.POST)
    except DocumentProblem as exc:
        return _document_page(request, status=422, form_data=request.POST, export_error=str(exc))
    return _document_page(request, form_data=request.POST, preview=prepared)


@login_required
@never_cache
@require_POST
def queue_document(request):
    profile = _owned_profile(request)
    try:
        job, created = queue_document_job(request.user, profile, request.POST)
    except DocumentProblem as exc:
        return _document_page(request, status=422, form_data=request.POST, export_error=str(exc))
    if created:
        messages.success(request, f"Export #{job.pk} was queued.")
    else:
        messages.success(request, f"That export already exists as job #{job.pk}; no duplicate was created.")
    return redirect("documents")


@login_required
@never_cache
@require_POST
def retry_document(request, job_id):
    job = get_object_or_404(DocumentJob, pk=job_id, user=request.user)
    try:
        retry_document_job(job)
    except DocumentProblem as exc:
        return _document_page(request, status=409, export_error=str(exc))
    messages.success(request, f"Export #{job.pk} was queued again.")
    return redirect("documents")


@login_required
@never_cache
@require_GET
def document_status(request, job_id):
    job = get_object_or_404(DocumentJob, pk=job_id, user=request.user)
    payload = {
        "id": job.pk,
        "status": job.status,
        "attempt": job.attempt,
        "error": job.error,
        "verification": job.verification,
    }
    if job.status == DocumentJob.Status.SUCCEEDED and artifact_file(job):
        profile = _owned_profile(request)
        approved = release_approval_matches(job, request.user.pk, profile.revision)
        payload["release_approved"] = approved
        payload["review_required"] = not approved
        if approved:
            payload["download_url"] = reverse("document_download", args=[job.pk])
        if review_pdf_file(job):
            payload["preview_url"] = reverse("document_download", args=[job.pk]) + "?preview=1"
    return JsonResponse(payload)


@login_required
@never_cache
@require_GET
def document_download(request, job_id):
    job = get_object_or_404(DocumentJob, pk=job_id, user=request.user)
    path = artifact_file(job)
    if job.status != DocumentJob.Status.SUCCEEDED or path is None:
        raise Http404("Document artifact is unavailable.")
    if request.GET.get("preview") == "1":
        proof = review_pdf_file(job)
        if proof is None:
            raise Http404("Document preview is unavailable.")
        return FileResponse(draft_preview(proof), filename=f"draft-{path.stem}.pdf", content_type="application/pdf")

    profile = _owned_profile(request)
    if not release_approval_matches(job, request.user.pk, profile.revision):
        return HttpResponseForbidden(
            "Final download is locked until these exact generated files pass the application review."
        )
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != job.artifact_sha256:
        return HttpResponseForbidden("Final download is locked because the approved artifact changed.")
    content_type = {
        DocumentJob.OutputFormat.PDF: "application/pdf",
        DocumentJob.OutputFormat.DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }[job.output_format]
    return FileResponse(io.BytesIO(data), as_attachment=True, filename=path.name, content_type=content_type)


@login_required
@never_cache
@require_POST
def approve_document_release(request):
    """Approve exact generated artifacts for final download using Issue #6's audit."""
    import json

    from .application_gate import GateError, audit, parse_json_list

    profile = _owned_profile(request)
    try:
        cv_job = get_object_or_404(
            DocumentJob,
            pk=int(request.POST.get("cv_job_id") or 0),
            user=request.user,
            document_type=DocumentJob.DocumentType.CV,
        )
        cover_id = int(request.POST.get("cover_job_id") or 0)
        cover_job = None
        if cover_id:
            cover_job = get_object_or_404(
                DocumentJob,
                pk=cover_id,
                user=request.user,
                document_type=DocumentJob.DocumentType.COVER_LETTER,
            )
        jobs = [cv_job] + ([cover_job] if cover_job else [])
        for job in jobs:
            if (
                job.status != DocumentJob.Status.SUCCEEDED
                or job.profile_revision != profile.revision
                or not artifact_file(job)
            ):
                raise DocumentProblem(
                    "Choose completed generated files from the current saved profile revision."
                )
        if request.POST.get("confirm_visual") != "on":
            raise DocumentProblem("Confirm that you visually reviewed the generated documents.")
        terms_obj = json.loads(request.POST.get("keywords") or "{}")
        if not isinstance(terms_obj, dict) or not isinstance(terms_obj.get("keywords"), list):
            raise GateError("Posting keywords must be a JSON object with a keywords list")
        keywords = terms_obj["keywords"]
        claims = parse_json_list(request.POST.get("claims") or "[]", "Candidate claims", maximum=60)
        citations = parse_json_list(
            request.POST.get("company_citations") or "[]", "Company citations", maximum=8
        )
        headings = [
            line.strip()
            for line in (request.POST.get("visual_headings") or "").splitlines()
            if line.strip()
        ]
        cv_bytes = read_review_pdf(cv_job)
        cover_bytes = read_review_pdf(cover_job) if cover_job else b""
    except (DocumentProblem, GateError, ValueError, TypeError) as exc:
        return _document_page(
            request,
            status=422,
            export_error=str(exc),
            release_form_data=request.POST,
        )

    report = audit(
        profile.data,
        cv_bytes,
        cover_pdf=cover_bytes,
        keywords=keywords,
        claims=claims,
        company_citations=citations,
        employer=(request.POST.get("employer") or "").strip(),
        visual_headings=headings,
    )
    if not report["allowed"]:
        return _document_page(
            request,
            status=422,
            export_error="Final download remains locked. Correct the review findings and try again.",
            release_report=report,
            release_form_data=request.POST,
        )

    profile.refresh_from_db()
    if any(job.profile_revision != profile.revision for job in jobs):
        return _document_page(
            request,
            status=409,
            export_error="Your profile changed during review. Review the generated files again.",
            release_report=report,
            release_form_data=request.POST,
        )

    approved_at = timezone.now().isoformat()
    application_jobs = [job.pk for job in jobs]
    with transaction.atomic():
        for job in jobs:
            verification = dict(job.verification or {})
            verification["release_approval"] = {
                "user_id": request.user.pk,
                "profile_revision": profile.revision,
                "artifact_sha256": job.artifact_sha256,
                "review_pdf_sha256": review_digest(job),
                "verified_sources": report["verified_sources"],
                "approved_at": approved_at,
                "application_jobs": application_jobs,
            }
            job.verification = verification
            job.save(update_fields=["verification", "updated_at"])

    messages.success(
        request,
        "Application review passed. Final downloads are unlocked for the exact approved files.",
    )
    return _document_page(request, release_report=report, release_form_data=request.POST)


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
