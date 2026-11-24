from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.db import IntegrityError, transaction
from django.db.models import F
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .documents import DocumentProblem, TEMPLATES, artifact_file, queue_document_job, retry_document_job
from .forms import EducationSet, ExperienceSet, ImportForm, ProfileForm
from .importers import ImportProblem, read_upload
from .models import DocumentJob, Profile
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
            draft = merge_profiles(profile.data, read_upload(upload.cleaned_data["document"]))
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


def _document_page(request, *, status=200, form_data=None, export_error=""):
    profile = _owned_profile(request)
    jobs = list(DocumentJob.objects.filter(user=request.user).order_by("-created_at")[:25])
    return render(request, "webapp/documents.html", {
        "profile": profile,
        "jobs": jobs,
        "templates": TEMPLATES,
        "form_data": form_data or {},
        "export_error": export_error,
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
        payload["download_url"] = reverse("document_download", args=[job.pk])
        if job.output_format == DocumentJob.OutputFormat.PDF:
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
    content_type = {
        DocumentJob.OutputFormat.PDF: "application/pdf",
        DocumentJob.OutputFormat.DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }[job.output_format]
    inline = job.output_format == DocumentJob.OutputFormat.PDF and request.GET.get("preview") == "1"
    return FileResponse(
        path.open("rb"),
        as_attachment=not inline,
        filename=path.name,
        content_type=content_type,
    )
