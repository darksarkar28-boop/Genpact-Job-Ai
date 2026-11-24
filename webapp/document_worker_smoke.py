"""Real integration tests for the built isolated document-worker image."""
import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import zipfile

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from pypdf import PdfReader

from .documents import TEMPLATES, artifact_file, review_pdf_file
from .models import DocumentJob, Profile
from .test_documents import PROFILE


class DocumentWorkerSmokeTests(TestCase):
    def _process(self, owner, profile, payload):
        self.client.force_login(owner)
        queued = self.client.post(reverse("document_queue"), payload)
        self.assertRedirects(queued, reverse("documents"))
        job = DocumentJob.objects.get(user=owner, status=DocumentJob.Status.QUEUED)
        self.assertEqual(job.profile_revision, profile.revision)
        status_url = reverse("document_status", args=[job.pk])
        self.assertEqual(self.client.get(status_url).json()["status"], "queued")
        call_command("run_document_worker", "--once")
        job.refresh_from_db()
        self.assertEqual(job.status, DocumentJob.Status.SUCCEEDED, job.error)
        status = self.client.get(status_url).json()
        self.assertEqual(status["status"], "succeeded")
        self.assertFalse(status["release_approved"])
        self.assertNotIn("download_url", status)
        locked = self.client.get(reverse("document_download", args=[job.pk]))
        self.assertEqual(locked.status_code, 403)
        artifact = artifact_file(job)
        self.assertIsNotNone(artifact)
        data = artifact.read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), job.artifact_sha256)
        preview = self.client.get(status["preview_url"])
        self.assertEqual(preview["Content-Type"], "application/pdf")
        self.assertIn("inline", preview["Content-Disposition"])
        draft = b"".join(preview.streaming_content)
        self.assertNotEqual(draft, data)
        pages = PdfReader(io.BytesIO(draft)).pages
        self.assertEqual(len(pages), job.verification["pages"])
        for page in pages:
            self.assertIn("DRAFT - review copy", page.extract_text())
        if job.output_format == DocumentJob.OutputFormat.DOCX:
            with zipfile.ZipFile(io.BytesIO(data)) as docx:
                self.assertIn("word/document.xml", docx.namelist())
        self.assertEqual(job.verification["contact_fields_checked"], ["email", "phone"])
        self.assertGreaterEqual(job.verification["pages"], 1)
        return job

    def _review_generated(self, owner, profile, cv_job, cover_job=None):
        self.client.force_login(owner)
        return self.client.post(reverse("document_release_review"), {
            "cv_job_id": cv_job.pk,
            "cover_job_id": cover_job.pk if cover_job else "",
            "employer": "Example Labs",
            "profile_revision": profile.revision,
            "visual_headings": "Summary\nSkills\nExperience\nEducation",
            "keywords": json.dumps({"keywords": [{"term": "Python", "priority": "required"}]}),
            "claims": "[]",
            "company_citations": "[]",
            "confirm_visual": "on",
        })

    def _approve_generated(self, owner, profile, cv_job, cover_job=None):
        response = self._review_generated(owner, profile, cv_job, cover_job)
        if response.status_code != 200:
            report = response.context["release_report"]
            findings = report["errors"] if report else response.context["export_error"]
            fixtures = {
                job.document_type: [page.extract_text() for page in PdfReader(review_pdf_file(job)).pages]
                for job in (cv_job, cover_job) if job
            }
            self.fail(f"Expected approval for supported fixtures. Findings: {findings!r}. Rendered fixture text: {fixtures!r}")
        self.assertContains(response, "Posting keyword coverage")
        self.assertContains(response, "1/1 exact")
        cv_job.refresh_from_db()
        self.assertIn("release_approval", cv_job.verification)
        if cover_job:
            cover_job.refresh_from_db()
            self.assertIn("release_approval", cover_job.verification)

    def _assert_exact_final_download(self, job):
        expected = artifact_file(job).read_bytes()
        status = self.client.get(reverse("document_status", args=[job.pk])).json()
        self.assertTrue(status["release_approved"])
        response = self.client.get(status["download_url"])
        self.assertEqual(response.status_code, 200)
        actual = b"".join(response.streaming_content)
        self.assertEqual(actual, expected)
        self.assertEqual(hashlib.sha256(actual).hexdigest(), job.artifact_sha256)

    def test_every_template_real_pdf_docx_stock_engines_and_layout_switching(self):
        owner = get_user_model().objects.create_user("worker_smoke")
        profile = Profile.objects.create(user=owner, revision=1, data=PROFILE)
        letter = "Dear Hiring Team,\nI build reliable reporting tools."
        with TemporaryDirectory() as temporary, override_settings(DOCUMENT_EXPORT_ROOT=Path(temporary)):
            cv_pdfs = {}
            cv_docxs = {}
            for template in TEMPLATES:
                key = template["key"]
                cv_pdfs[key] = self._process(owner, profile, {
                    "document_type": "cv", "output_format": "pdf", "template_key": key,
                })
                cv_docxs[key] = self._process(owner, profile, {
                    "document_type": "cv", "output_format": "docx", "template_key": key,
                })

            cover_pdf = self._process(owner, profile, {
                "document_type": "cover_letter", "output_format": "pdf", "template_key": "classic",
                "cover_letter_body": letter, "company": "Example Labs", "role": "Engineer",
            })
            cover_docx = self._process(owner, profile, {
                "document_type": "cover_letter", "output_format": "docx", "template_key": "clean",
                "cover_letter_body": letter, "company": "Example Labs", "role": "Engineer",
            })

            expected_templates = {template["key"] for template in TEMPLATES}
            self.assertEqual(set(cv_pdfs), expected_templates)
            self.assertEqual(set(cv_docxs), expected_templates)
            for job in cv_pdfs.values():
                self.assertEqual(job.verification["pagination_source"], "generated PDF")
            for job in cv_docxs.values():
                self.assertTrue(job.verification["pagination_source"].startswith("headless LibreOffice"))
            self.assertEqual(cover_pdf.verification["pagination_source"], "generated PDF")
            self.assertTrue(cover_docx.verification["pagination_source"].startswith("headless LibreOffice"))

            with zipfile.ZipFile(artifact_file(cv_docxs["modern"])) as doc:
                modern_xml = doc.read("word/document.xml")
            with zipfile.ZipFile(artifact_file(cv_docxs["compact"])) as doc:
                compact_xml = doc.read("word/document.xml")
            self.assertNotEqual(modern_xml, compact_xml)
            self.assertIn(b"Aptos", modern_xml)
            self.assertIn(b"Calibri", compact_xml)

            # PDF final downloads stay locked until the exact compiled application is approved.
            self._approve_generated(owner, profile, cv_pdfs["classic"], cover_pdf)
            self._assert_exact_final_download(cv_pdfs["classic"])
            self._assert_exact_final_download(cover_pdf)

            # A different account cannot reuse the owner's approval.
            other = get_user_model().objects.create_user("worker_smoke_other")
            self.client.force_login(other)
            self.assertEqual(
                self.client.get(reverse("document_download", args=[cv_pdfs["classic"].pk])).status_code,
                404,
            )

            # Editable DOCX uses the retained LibreOffice proof for review but releases
            # the exact approved DOCX bytes.
            self._approve_generated(owner, profile, cv_docxs["modern"], cover_docx)
            self._assert_exact_final_download(cv_docxs["modern"])
            self._assert_exact_final_download(cover_docx)

            # Editing cover-letter text creates a distinct artifact and cannot inherit
            # the prior approval. This is checked for both final formats.
            edited_letter = letter + "\nI also use Python for internal analytics."
            edited_cover_pdf = self._process(owner, profile, {
                "document_type": "cover_letter", "output_format": "pdf", "template_key": "classic",
                "cover_letter_body": edited_letter, "company": "Example Labs", "role": "Engineer",
            })
            edited_cover_docx = self._process(owner, profile, {
                "document_type": "cover_letter", "output_format": "docx", "template_key": "clean",
                "cover_letter_body": edited_letter, "company": "Example Labs", "role": "Engineer",
            })
            self.assertNotIn("release_approval", edited_cover_pdf.verification)
            self.assertNotIn("release_approval", edited_cover_docx.verification)

            # The supported edited example can be approved, and the owner then receives
            # the exact generated files.
            self._approve_generated(owner, profile, cv_pdfs["classic"], edited_cover_pdf)
            self._assert_exact_final_download(edited_cover_pdf)
            self._approve_generated(owner, profile, cv_docxs["modern"], edited_cover_docx)
            self._assert_exact_final_download(edited_cover_docx)

            # The final document reviewer also checks the actual edited letter,
            # including the rendered proof for an editable Word document.
            for output_format, cv_job in (("pdf", cv_pdfs["classic"]), ("docx", cv_docxs["modern"])):
                with self.subTest(unsupported_letter=output_format):
                    unsupported = self._process(owner, profile, {
                        "document_type": "cover_letter", "output_format": output_format,
                        "template_key": "clean", "cover_letter_body": letter + "\nBSc Computer Science, BSc Physics",
                        "company": "Example Labs", "role": "Engineer",
                    })
                    result = self._review_generated(owner, profile, cv_job, unsupported)
                    self.assertContains(result, "Unsupported degree/course: BSc Physics", status_code=422)
                    self.assertContains(result, "Posting keyword coverage", status_code=422)
                    unsupported.refresh_from_db()
                    self.assertNotIn("release_approval", unsupported.verification)
                    self.assertEqual(self.client.get(reverse("document_download", args=[unsupported.pk])).status_code, 403)

            # Approval is also bound to the current saved profile revision.
            Profile.objects.filter(pk=profile.pk).update(revision=profile.revision + 1)
            profile.refresh_from_db()
            for approved_job in (
                cv_pdfs["classic"], cover_pdf, cv_docxs["modern"], cover_docx,
                edited_cover_pdf, edited_cover_docx,
            ):
                status = self.client.get(reverse("document_status", args=[approved_job.pk])).json()
                self.assertFalse(status["release_approved"])
                self.assertNotIn("download_url", status)
                blocked = self.client.get(reverse("document_download", args=[approved_job.pk]))
                self.assertEqual(blocked.status_code, 403)

            staging = Path(temporary) / ".staging"
            self.assertEqual(list(staging.iterdir()), [])
