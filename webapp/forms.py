from django import forms
from django.forms import formset_factory

from .importers import MAX_UPLOAD_BYTES, SUPPORTED_TYPES
from .profile_data import MAX_ENTRIES


class ImportForm(forms.Form):
    document = forms.FileField(
        label="Résumé or LinkedIn export",
        widget=forms.ClearableFileInput(attrs={"accept": ".pdf,.docx,.zip", "aria-describedby": "file-types"}),
    )

    def clean_document(self):
        document = self.cleaned_data["document"]
        if document.size > MAX_UPLOAD_BYTES:
            raise forms.ValidationError("Choose a file up to 8 MiB.")
        if document.name.rsplit(".", 1)[-1].lower() not in SUPPORTED_TYPES:
            raise forms.ValidationError("Choose a PDF, DOCX or LinkedIn export ZIP.")
        return document


class ProfileForm(forms.Form):
    full_name = forms.CharField(label="Full name", max_length=250)
    headline = forms.CharField(required=False, max_length=500)
    email = forms.EmailField(label="Contact email", required=False)
    phone = forms.CharField(required=False, max_length=100)
    location = forms.CharField(required=False, max_length=300)
    summary = forms.CharField(required=False, max_length=12000, widget=forms.Textarea(attrs={"rows": 4}))
    skills = forms.CharField(
        label="Skills", required=False, max_length=12000,
        help_text="Separate skills with commas or new lines.",
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)


class ExperienceForm(forms.Form):
    title = forms.CharField(label="Job title", required=False, max_length=500)
    employer = forms.CharField(required=False, max_length=500)
    dates = forms.CharField(label="Dates", required=False, max_length=250)
    details = forms.CharField(required=False, max_length=12000, widget=forms.Textarea(attrs={"rows": 3}))


class EducationForm(forms.Form):
    school = forms.CharField(label="Institution", required=False, max_length=500)
    degree = forms.CharField(label="Degree or course", required=False, max_length=500)
    dates = forms.CharField(label="Dates", required=False, max_length=250)
    details = forms.CharField(required=False, max_length=12000, widget=forms.Textarea(attrs={"rows": 2}))


ExperienceSet = formset_factory(ExperienceForm, extra=0, max_num=MAX_ENTRIES, validate_max=True,
                                absolute_max=MAX_ENTRIES, can_delete=True)
EducationSet = formset_factory(EducationForm, extra=0, max_num=MAX_ENTRIES, validate_max=True,
                               absolute_max=MAX_ENTRIES, can_delete=True)



class ApplicationReviewForm(forms.Form):
    """Ephemeral uploads: PDFs and profile facts are never placed in the session."""
    profile_revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    employer = forms.CharField(max_length=200, required=True, label="Target employer")
    cv_pdf = forms.FileField(label="Final CV PDF")
    cover_pdf = forms.FileField(label="Final cover letter PDF (optional)", required=False)
    visual_headings = forms.CharField(
        label="CV section headings in their visible order",
        help_text="One heading per line; at least two, to compare with the PDF text extraction order.",
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    keywords = forms.CharField(
        label="Posting keywords (JSON)",
        help_text='{"keywords":[{"term":"Python","priority":"required"}]}',
        widget=forms.Textarea(attrs={"rows": 5}),
        max_length=32768,
    )
    claims = forms.CharField(
        label="Additional candidate claim-to-evidence pairs (JSON, optional)",
        initial="[]", required=False, max_length=32768,
        help_text='[{"claim":"exact CV wording","evidence":"exact quote from saved profile"}]',
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    company_citations = forms.CharField(
        label="Employer-specific factual claims and source URLs (JSON, optional)",
        initial="[]", required=False, max_length=32768,
        help_text='[{"claim":"verbatim published claim","url":"https://example.org/source"}]',
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    confirm_visual = forms.BooleanField(
        required=True, label="I inspected the final rendered PDF pages and confirmed their visual reading order"
    )

    def clean(self):
        cleaned = super().clean()
        from .application_gate import MAX_PDF
        for name in ("cv_pdf", "cover_pdf"):
            doc = cleaned.get(name)
            if doc and (doc.size > MAX_PDF or not doc.name.lower().endswith(".pdf")):
                self.add_error(name, "Choose a PDF file smaller than or equal to 8 MiB.")
        return cleaned
