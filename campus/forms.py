import io
import re
import zipfile
from pathlib import PurePath

from django import forms
from django.conf import settings
from django.core.validators import validate_email

from .models import Cohort, Role


class BulkInviteForm(forms.Form):
    emails = forms.CharField(label="Recipient emails", max_length=16000, widget=forms.Textarea(attrs={"rows": 3}),
                             help_text="Use a new line or a comma between addresses. Up to 50 per batch.")
    role = forms.ChoiceField(choices=Role.choices, initial=Role.CANDIDATE)
    cohort = forms.ModelChoiceField(queryset=Cohort.objects.none(), required=False)
    deliver = forms.BooleanField(required=False, label="Send invitations by email")

    def __init__(self, org, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cohort"].queryset = org.cohorts.all()
        if not settings.CAMPUS_MAIL_ENABLED:
            del self.fields["deliver"]

    def clean_emails(self):
        emails = list(dict.fromkeys(x.casefold() for x in re.split(r"[\s,;]+", self.cleaned_data["emails"]) if x))
        if not 1 <= len(emails) <= 50:
            raise forms.ValidationError("Enter between 1 and 50 different email addresses.")
        for email in emails:
            if len(email) > 254:
                raise forms.ValidationError("Email addresses must be at most 254 characters.")
            validate_email(email)
        return emails

    def clean(self):
        values = super().clean()
        if values.get("role") in (Role.CANDIDATE, Role.MENTOR) and not values.get("cohort"):
            self.add_error("cohort", "Choose the candidate or mentor's initial cohort.")
        return values


class ProgressForm(forms.Form):
    applications = forms.IntegerField(min_value=0, max_value=100000)
    interviews = forms.IntegerField(min_value=0, max_value=100000)
    offers = forms.IntegerField(min_value=0, max_value=100000)


class ResumeForm(forms.Form):
    document = forms.FileField(label="Résumé file", widget=forms.FileInput(attrs={"accept": ".pdf,.docx"}),
                               help_text="PDF or DOCX, up to 4 MiB.")
    consent = forms.BooleanField(label="Share this revision with the mentors assigned to my cohort")
    current_review = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    current_revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    current_cohort = forms.IntegerField(min_value=1, widget=forms.HiddenInput)

    def clean_document(self):
        upload = self.cleaned_data["document"]
        if len(self.files.getlist("document")) != 1 or len(self.files) != 1:
            raise forms.ValidationError("Select one résumé.")
        limit = 4 * 1024 * 1024
        if upload.size > limit:
            raise forms.ValidationError("Select a résumé of 4 MiB or less.")
        name = PurePath(upload.name.replace("\\", "/")).name
        if len(name) > 150:
            raise forms.ValidationError("Shorten the filename to 150 characters.")
        data = upload.read(limit + 1)
        if len(data) > limit:
            raise forms.ValidationError("Select a résumé of 4 MiB or less.")
        ext = PurePath(name).suffix.lower()
        if ext == ".pdf" and data.startswith(b"%PDF-") and b"%%EOF" in data[-1024:]:
            return name, data
        if ext == ".docx":
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    if {"word/document.xml", "[Content_Types].xml"}.issubset(archive.namelist()):
                        return name, data
            except (zipfile.BadZipFile, ValueError):
                pass
        raise forms.ValidationError("Choose a PDF or Word DOCX document matching its filename.")


class FeedbackForm(forms.Form):
    comment = forms.CharField(label="Feedback on this revision", max_length=4000, widget=forms.Textarea(attrs={"rows": 3}))
