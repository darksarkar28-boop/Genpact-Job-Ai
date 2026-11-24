"""Real integration tests for the built isolated document-worker image."""
from pathlib import Path
from tempfile import TemporaryDirectory
import zipfile

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

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

            staging = Path(temporary) / ".staging"
            self.assertEqual(list(staging.iterdir()), [])
