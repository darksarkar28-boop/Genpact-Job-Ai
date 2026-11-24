"""Real integration tests for the built isolated document-worker image."""
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import zipfile

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .documents import artifact_file, claim_next_job, process_claimed_job, queue_document_job
from .models import DocumentJob, Profile
from .test_documents import PROFILE


class DocumentWorkerSmokeTests(TestCase):
    def _process(self, owner, profile, payload):
        job, created = queue_document_job(owner, profile, payload)
        self.assertTrue(created)
        claimed = claim_next_job()
        self.assertEqual(claimed.pk, job.pk)
        process_claimed_job(claimed)
        job.refresh_from_db()
        self.assertEqual(job.status, DocumentJob.Status.SUCCEEDED, job.error)
        self.assertTrue(artifact_file(job).is_file())
        self.assertEqual(job.verification["contact_fields_checked"], ["email", "phone"])
        self.assertGreaterEqual(job.verification["pages"], 1)
        return job

    def _verify_application_download(self, owner, profile, cv, cover):
        self.client.force_login(owner)
        fields = {
            "employer": "Example Labs",
            "profile_revision": profile.revision,
            "visual_headings": "Skills\nExperience\nEducation",
            "keywords": json.dumps({"keywords": [{"term": "Python", "priority": "required"}]}),
            "confirm_visual": "on",
        }
        url = reverse("application_review")
        with cv.open("rb") as cv_file, cover.open("rb") as cover_file:
            reviewed = self.client.post(url, {**fields, "action": "review", "cv_pdf": cv_file, "cover_pdf": cover_file})
        self.assertEqual(reviewed.status_code, 200, reviewed.context["report"])
        self.assertTrue(reviewed.context["report"]["allowed"])
        with cv.open("rb") as cv_file, cover.open("rb") as cover_file:
            released = self.client.post(url, {
                **fields, "action": "export", "token": reviewed.context["token"], "cv_pdf": cv_file, "cover_pdf": cover_file,
            })
        self.assertEqual(released.status_code, 200)
        self.assertEqual(released["Content-Type"], "application/zip")
        with zipfile.ZipFile(io.BytesIO(released.content)) as package:
            self.assertEqual(package.read("validated_cv.pdf"), cv.read_bytes())
            self.assertEqual(package.read("validated_cover_letter.pdf"), cover.read_bytes())
            self.assertTrue(json.loads(package.read("verification_report.json"))["allowed"])

    def test_real_pdf_docx_stock_engines_and_layout_switching(self):
        owner = get_user_model().objects.create_user("worker_smoke")
        profile = Profile.objects.create(user=owner, revision=1, data=PROFILE)
        letter = "Dear Hiring Team,\nI build reliable reporting tools."
        with TemporaryDirectory() as temporary, override_settings(DOCUMENT_EXPORT_ROOT=Path(temporary)):
            cv_pdf = self._process(owner, profile, {
                "document_type": "cv", "output_format": "pdf", "template_key": "classic",
            })
            cover_pdf = self._process(owner, profile, {
                "document_type": "cover_letter", "output_format": "pdf", "template_key": "classic",
                "cover_letter_body": letter, "company": "Example Labs", "role": "Engineer",
            })
            modern_docx = self._process(owner, profile, {
                "document_type": "cv", "output_format": "docx", "template_key": "modern",
            })
            compact_docx = self._process(owner, profile, {
                "document_type": "cv", "output_format": "docx", "template_key": "compact",
            })
            cover_docx = self._process(owner, profile, {
                "document_type": "cover_letter", "output_format": "docx", "template_key": "clean",
                "cover_letter_body": letter, "company": "Example Labs", "role": "Engineer",
            })

            self.assertEqual(cv_pdf.verification["pagination_source"], "generated PDF")
            self.assertEqual(cover_pdf.verification["pagination_source"], "generated PDF")
            self.assertTrue(modern_docx.verification["pagination_source"].startswith("headless LibreOffice"))
            self.assertTrue(cover_docx.verification["pagination_source"].startswith("headless LibreOffice"))

            with zipfile.ZipFile(artifact_file(modern_docx)) as doc:
                modern_xml = doc.read("word/document.xml")
            with zipfile.ZipFile(artifact_file(compact_docx)) as doc:
                compact_xml = doc.read("word/document.xml")
            self.assertNotEqual(modern_xml, compact_xml)
            self.assertIn(b"Aptos", modern_xml)
            self.assertIn(b"Calibri", compact_xml)

            self._verify_application_download(owner, profile, artifact_file(cv_pdf), artifact_file(cover_pdf))

            staging = Path(temporary) / ".staging"
            self.assertEqual(list(staging.iterdir()), [])
