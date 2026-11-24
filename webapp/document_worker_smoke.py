"""Run against the built document-worker image with manage.py test webapp.document_worker_smoke."""
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from .documents import artifact_file, claim_next_job, process_claimed_job, queue_document_job
from .models import DocumentJob, Profile
from .test_documents import PROFILE


class DocumentWorkerSmokeTests(TestCase):
    def test_pdf_and_docx_for_both_document_types(self):
        owner = get_user_model().objects.create_user("worker_smoke")
        profile = Profile.objects.create(user=owner, revision=1, data=PROFILE)
        letter = "Dear Hiring Team,\nI build reliable reporting tools."
        with TemporaryDirectory() as temporary, override_settings(DOCUMENT_EXPORT_ROOT=Path(temporary)):
            for kind in DocumentJob.DocumentType.values:
                for fmt in DocumentJob.OutputFormat.values:
                    with self.subTest(document_type=kind, output_format=fmt):
                        job, created = queue_document_job(owner, profile, {
                            "document_type": kind, "output_format": fmt, "template_key": "classic",
                            "cover_letter_body": letter, "company": "Example Labs", "role": "Engineer",
                        })
                        self.assertTrue(created)
                        claimed = claim_next_job()
                        self.assertEqual(claimed.pk, job.pk)
                        process_claimed_job(claimed)
                        job.refresh_from_db()
                        self.assertEqual(job.status, DocumentJob.Status.SUCCEEDED, job.error)
                        self.assertTrue(artifact_file(job).is_file())
                        self.assertEqual(job.verification["contact_fields_checked"], ["email", "phone"])
                        if fmt == DocumentJob.OutputFormat.PDF:
                            self.assertEqual(job.verification["pages"], 1)
