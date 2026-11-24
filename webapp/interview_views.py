import math
from datetime import timedelta

from django import forms
from django.contrib.auth.decorators import login_required
from django.db import transaction, IntegrityError
from django.http import HttpResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from .interview_practice import make_questions, review_answer
from .models import CareerContext, MockAnswer, MockInterview, Profile

QUESTION_SECONDS = 120
MAX_MEDIA = 8 * 1024 * 1024


class StartForm(forms.Form):
    mode = forms.ChoiceField(choices=[("text", "Text (no recording)"), ("voice", "Voice recording"), ("video", "Voice and video recording")])
    consent = forms.BooleanField(required=False, label="I consent to recording and storing my audio/video on this account.")
    def clean(self):
        data = super().clean()
        if data.get("mode") in ("voice", "video") and not data.get("consent"):
            self.add_error("consent", "Recording requires explicit consent.")
        return data


class AnswerForm(forms.Form):
    index = forms.IntegerField(min_value=0, max_value=2, widget=forms.HiddenInput)
    transcript = forms.CharField(required=False, max_length=8000, widget=forms.Textarea(attrs={"rows": 8}),
                                label="Answer / editable transcript")
    recording = forms.FileField(required=False, widget=forms.ClearableFileInput(attrs={"accept": "audio/webm,video/webm,audio/ogg"}))


def _media(upload, mode):
    if upload.size > MAX_MEDIA:
        raise ValueError("Recording exceeds 8 MiB. Use the text fallback.")
    data = upload.read(MAX_MEDIA + 1)
    if len(data) > MAX_MEDIA:
        raise ValueError("Recording exceeds 8 MiB.")
    mime = upload.content_type.split(";")[0].lower()
    if mime in ("audio/webm", "video/webm") and data.startswith(b"\x1a\x45\xdf\xa3"):
        if mime == ("video/webm" if mode == "video" else "audio/webm"):
            return data, mime
    if mode == "voice" and mime == "audio/ogg" and data.startswith(b"OggS"):
        return data, mime
    raise ValueError("Unsupported recording format. Use WebM or voice Ogg, or type your answer.")


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def home(request):
    profile = Profile.objects.filter(user=request.user, revision__gt=0).first()
    context = CareerContext.objects.filter(user=request.user).first()
    form = StartForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and profile and context and context.data.get("posting_description") and form.is_valid():
        attempt = MockInterview.objects.create(user=request.user, context=context.data,
            questions=make_questions(context.data, profile.data), profile_revision=profile.revision,
            deadline=timezone.now() + timedelta(seconds=QUESTION_SECONDS),
            mode=form.cleaned_data["mode"], recording_consent=form.cleaned_data["consent"] and form.cleaned_data["mode"] != "text")
        return redirect("interview-detail", pk=attempt.pk)
    return render(request, "webapp/interview_home.html", {"profile": profile, "context": context, "form": form,
        "attempts": MockInterview.objects.filter(user=request.user).order_by("-created_at")[:30]},
        status=422 if request.method == "POST" else 200)


def _render(request, attempt, form=None, status=200):
    question = None if attempt.completed else attempt.questions[attempt.question_index]
    answers = list(attempt.answers.order_by("question_index").defer("recording"))
    # Avoid loading the media binary into the result template.
    for answer in answers:
        answer.question = attempt.questions[answer.question_index]["text"]
    return render(request, "webapp/interview_detail.html", {"attempt": attempt,
        "question": question, "answers": answers, "form": form or AnswerForm(initial={"index": attempt.question_index}),
        "remaining": max(0, math.ceil((attempt.deadline-timezone.now()).total_seconds()))}, status=status)


@login_required
@never_cache
@sensitive_post_parameters()
@require_http_methods(["GET", "POST"])
def detail(request, pk):
    attempt = get_object_or_404(MockInterview, pk=pk, user=request.user)
    if request.method == "GET":
        return _render(request, attempt)
    form = AnswerForm(request.POST, request.FILES)
    if attempt.completed:
        return HttpResponse("Interview already completed.", status=409)
    if not form.is_valid():
        return _render(request, attempt, form, 422)
    d = form.cleaned_data
    if d["index"] != attempt.question_index:
        return HttpResponse("This question has already changed. Reload the interview.", status=409)
    now = timezone.now()
    expired = now >= attempt.deadline
    transcript, recording, media_type = d["transcript"], b"", ""
    if expired:
        transcript = ""
    elif d["recording"]:
        if not attempt.recording_consent or attempt.mode == "text":
            form.add_error("recording", "Recording is not authorized.")
            return _render(request, attempt, form, 422)
        try:
            recording, media_type = _media(d["recording"], attempt.mode)
        except ValueError as exc:
            form.add_error("recording", str(exc))
            return _render(request, attempt, form, 422)
    if not expired and not transcript and not recording:
        form.add_error("transcript", "Enter an answer or record one, or wait for the timer to expire.")
        return _render(request, attempt, form, 422)
    question = attempt.questions[attempt.question_index]
    feedback = review_answer(question, transcript)
    next_index = attempt.question_index + 1
    try:
        with transaction.atomic():
            updated = MockInterview.objects.filter(pk=pk, user=request.user, completed=False,
                question_index=d["index"], deadline=attempt.deadline).update(
                question_index=next_index, completed=next_index == len(attempt.questions),
                deadline=now+timedelta(seconds=QUESTION_SECONDS))
            if not updated:
                return HttpResponse("Question changed; reload.", status=409)
            MockAnswer.objects.create(interview=attempt, question_index=d["index"], transcript=transcript,
                feedback=feedback, recording=recording, media_type=media_type, timed_out=expired)
    except IntegrityError:
        return HttpResponse("Question already submitted.", status=409)
    return redirect("interview-detail", pk=pk)


@login_required
@never_cache
@require_GET
def download(request, pk, index):
    answer = get_object_or_404(MockAnswer, interview_id=pk, interview__user=request.user, question_index=index)
    if not answer.recording or not answer.media_type:
        raise Http404
    extension = "ogg" if answer.media_type == "audio/ogg" else "webm"
    response = HttpResponse(bytes(answer.recording), content_type="application/octet-stream")
    response["Content-Disposition"] = f'attachment; filename="interview-{index+1}.{extension}"'
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
@never_cache
@sensitive_post_parameters()
@require_POST
def erase(request, pk, index):
    answer = get_object_or_404(MockAnswer, interview_id=pk, interview__user=request.user, question_index=index)
    action = request.POST.get("action")
    if action == "recording":
        MockAnswer.objects.filter(pk=answer.pk).update(recording=b"", media_type="")
    elif action == "transcript":
        MockAnswer.objects.filter(pk=answer.pk).update(transcript="", feedback={})
    else:
        return HttpResponse("Unknown deletion operation.", status=400)
    return redirect("interview-detail", pk=pk)


@login_required
@never_cache
@require_POST
def delete(request, pk):
    get_object_or_404(MockInterview, pk=pk, user=request.user).delete()
    return redirect("interview-home")


@login_required
@never_cache
@require_POST
def clear_context(request):
    CareerContext.objects.filter(user=request.user).delete()
    return redirect("career-planning")
