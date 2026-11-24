from django import forms
from django.forms import formset_factory

from .importers import MAX_UPLOAD_BYTES, SUPPORTED_TYPES
from .profile_data import MAX_ENTRIES


class ImportForm(forms.Form):
    document = forms.FileField(
        label="Résumé or LinkedIn export",
        widget=forms.ClearableFileInput(attrs={"accept": ".pdf,.docx,.zip", "aria-describedby": "file-types"}),
    )

    ai_assist = forms.BooleanField(required=False, label="Optional AI assistance: send the document text to the configured model provider")

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
