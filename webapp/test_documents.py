import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .documents import (
    DocumentProblem,
    TEMPLATES,
    _worker_command,
    claim_next_job,
    process_claimed_job,
    retry_document_job,
    review_pdf_file,
)
from .models import DocumentJob, Profile, TemplateReview
from .tests import docx, pdf


PROFILE = {
    "full_name": "Mira Das",
    "headline": "Software Engineer",
    "email": "mira@example.test",
    "phone": "+91 70000 00001",
    "location": "Pune, India",
    "summary": "Builds reliable reporting tools.",
    "skills": ["Python", "SQL"],
    "experience": [{
        "title": "Data Engineer",
        "employer": "Example Labs",
        "dates": "2021 - Present",
        "details": "Built internal pipelines.",
    }],
    "education": [{
        "school": "Example University",
        "degree": "BSc Computer Science",
        "dates": "2015 - 2019",
        "details": "",
    }],
}

EXTRACTED = [
    "Mira Das",
    "mira@example.test | +91 70000 00001 | Pune, India",
    "Summary",
    "Builds reliable reporting tools.",
    "Skills",
    "Python, SQL",
    "Experience",
    "Data Engineer - Example Labs",
    "2021 - Present",
    "Built internal pipelines.",
    "Education",
    "Example University - BSc Computer Science",
    "2015 - 2019",
]


class DocumentExportTests(TestCase):
    password = "export-only-29-violet!"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(DOCUMENT_EXPORT_ROOT=Path(self.temp.name))
        self.settings_override.enable()
        self.user = get_user_model().objects.create_user("export_owner", password=self.password)
        self.profile = Profile.objects.create(user=self.user, data=PROFILE, revision=3)
        self.client.force_login(self.user)

    def tearDown(self):
        self.settings_override.disable()
        self.temp.cleanup()

    def queue(self, *, output_format="pdf", template_key="classic", document_type="cv", **extra):
        payload = {
            "document_type": document_type,
            "output_format": output_format,
            "template_key": template_key,
            **extra,
        }
        return self.client.post(reverse("document_queue"), payload)

    @staticmethod
    def fake_worker(workdir, job):
        output = workdir / f"output.{job.output_format}"
        if job.output_format == DocumentJob.OutputFormat.PDF:
            output.write_bytes(pdf(EXTRACTED))
        else:
            output.write_bytes(docx(EXTRACTED))
            (workdir / "output.pdf").write_bytes(pdf(EXTRACTED))
        return output

    def finish_next(self):
        job = claim_next_job()
        self.assertIsNotNone(job)
        with patch("webapp.documents._run_isolated_worker", side_effect=self.fake_worker):
            process_claimed_job(job)
        job.refresh_from_db()
        return job

    def approve(self, cv_job, cover_job=None):
        payload = {
            "cv_job_id": cv_job.pk,
            "cover_job_id": cover_job.pk if cover_job else "",
            "employer": "Example Labs",
            "visual_headings": "Summary\nSkills\nExperience\nEducation",
            "keywords": '{"keywords":[{"term":"Python","priority":"required"}]}',
            "claims": "[]",
            "company_citations": "[]",
            "confirm_visual": "on",
        }
        response = self.client.post(reverse("document_release_review"), payload)
        self.assertRedirects(response, reverse("documents"))
        cv_job.refresh_from_db()
        if cover_job:
            cover_job.refresh_from_db()
        return response

    def test_source_preview_preserves_content_and_recruiter_badge_requires_evidence(self):
        letter = "Dear Hiring Team,\nThis is the exact content I want to keep."
        preview = self.client.post(reverse("document_preview"), {
            "document_type": "cover_letter",
            "output_format": "docx",
            "template_key": "modern",
            "role": "Engineer",
            "company": "Example Co",
            "cover_letter_body": letter,
        })
        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, "Pre-download preview")
        self.assertContains(preview, "This is the exact content I want to keep.")
        self.assertContains(preview, "Recruiter review pending")
        self.assertFalse(DocumentJob.objects.exists())

        TemplateReview.objects.create(
            template_key="modern",
            reviewer_label="External recruiter",
            feedback="Readable and easy to scan.",
            approved=True,
        )
        reviewed = self.client.get(reverse("documents"))
        self.assertContains(reviewed, "Recruiter-tested · External recruiter")

    def test_gallery_queue_deduplicates_and_pdf_can_be_previewed(self):
        page = self.client.get(reverse("documents"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, reverse("application_review"))
        self.assertEqual(len(TEMPLATES), 8)
        for template in TEMPLATES:
            self.assertContains(page, template["label"])

        self.assertRedirects(self.queue(), reverse("documents"))
        self.assertRedirects(self.queue(), reverse("documents"))
        self.assertEqual(DocumentJob.objects.filter(user=self.user).count(), 1)

        job = self.finish_next()
        self.assertEqual(job.status, DocumentJob.Status.SUCCEEDED)
        self.assertTrue(job.verification["readable"])
        self.assertEqual(job.verification["reading_order"], "verified against the single-column source sequence")
        self.assertEqual(len(job.artifact_sha256), 64)

        status = self.client.get(reverse("document_status", args=[job.pk]))
        self.assertEqual(status.json()["status"], "succeeded")
        self.assertIn("preview_url", status.json())
        self.assertNotIn("download_url", status.json())
        self.assertTrue(status.json()["review_required"])
        preview = self.client.get(reverse("document_download", args=[job.pk]) + "?preview=1")
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview["Content-Type"], "application/pdf")
        self.assertIn("inline", preview["Content-Disposition"])

    def test_docx_download_is_editable_format_and_owner_scoped(self):
        self.assertRedirects(self.queue(output_format="docx", template_key="modern"), reverse("documents"))
        job = self.finish_next()
        self.assertEqual(job.output_format, "docx")
        self.assertEqual(job.status, "succeeded")
        self.assertEqual(job.verification["pages"], 1)
        self.assertEqual(
            job.verification["pagination_source"],
            "headless LibreOffice rendering of generated DOCX",
        )

        other = Client()
        other.force_login(get_user_model().objects.create_user("different_owner"))
        self.assertEqual(other.get(reverse("document_download", args=[job.pk])).status_code, 404)

        locked = self.client.get(reverse("document_download", args=[job.pk]))
        self.assertEqual(locked.status_code, 403)
        self.assertContains(locked, "Final download is locked", status_code=403)

        self.approve(job)
        status = self.client.get(reverse("document_status", args=[job.pk])).json()
        self.assertTrue(status["release_approved"])
        self.assertIn("download_url", status)

        download = self.client.get(reverse("document_download", args=[job.pk]))
        self.assertEqual(download.status_code, 200)
        self.assertEqual(
            download["Content-Type"],
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        payload = b"".join(download.streaming_content)
        self.assertTrue(payload.startswith(b"PK"))

    def test_retention_cleanup_removes_artifact_and_private_review_proof_but_keeps_audit_record(self):
        self.queue(output_format="docx", template_key="modern")
        job = self.finish_next()
        path = Path(self.temp.name) / job.artifact_name
        proof = review_pdf_file(job)
        self.assertTrue(path.is_file())
        self.assertIsNotNone(proof)
        self.assertTrue(proof.is_file())
        old = timezone.now() - timedelta(days=45)
        DocumentJob.objects.filter(pk=job.pk).update(finished_at=old)

        call_command("cleanup_document_exports", older_than_days=30)
        job.refresh_from_db()
        self.assertFalse(path.exists())
        self.assertFalse(proof.exists())
        self.assertEqual(job.artifact_name, "")
        self.assertEqual(job.artifact_sha256, "")
        self.assertNotIn("review_pdf_name", job.verification)
        self.assertNotIn("release_approval", job.verification)
        self.assertIn("artifact_cleaned_at", job.verification)
        self.assertEqual(DocumentJob.objects.filter(pk=job.pk).count(), 1)
        self.assertEqual(self.client.get(reverse("document_download", args=[job.pk])).status_code, 404)

    def test_failed_compile_is_cleaned_and_can_be_retried(self):
        self.queue()
        job = claim_next_job()
        with patch(
            "webapp.documents._run_isolated_worker",
            side_effect=DocumentProblem("The isolated document worker could not compile this export."),
        ):
            process_claimed_job(job)
        job.refresh_from_db()
        self.assertEqual(job.status, DocumentJob.Status.FAILED)
        self.assertFalse(job.artifact_name)
        staging = Path(self.temp.name) / ".staging"
        self.assertEqual(list(staging.iterdir()), [])

        self.assertRedirects(
            self.client.post(reverse("document_retry", args=[job.pk])),
            reverse("documents"),
        )
        job.refresh_from_db()
        self.assertEqual(job.status, DocumentJob.Status.QUEUED)
        self.assertEqual(job.error, "")

        job = self.finish_next()
        self.assertEqual(job.status, DocumentJob.Status.SUCCEEDED)
        self.assertEqual(job.attempt, 2)

    def test_duplicate_retry_does_not_reset_an_already_claimed_job(self):
        self.queue()
        first = claim_next_job()
        with patch("webapp.documents._run_isolated_worker", side_effect=DocumentProblem("Compile failed")):
            process_claimed_job(first)
        # Two requests have each loaded the same failed attempt. A worker
        # claims the first retry before the second request completes.
        first.refresh_from_db()
        second_request = DocumentJob.objects.get(pk=first.pk)
        retry_document_job(first)
        active = claim_next_job()
        self.assertEqual((active.pk, active.attempt), (first.pk, 2))
        try:
            retry_document_job(second_request)
        except DocumentProblem:
            pass  # A conflict or an idempotent no-op preserves the active attempt.
        active.refresh_from_db()
        self.assertEqual(active.status, DocumentJob.Status.RUNNING)
        self.assertIsNone(claim_next_job())
        with patch("webapp.documents._run_isolated_worker", side_effect=DocumentProblem("Another failure")):
            process_claimed_job(active)
        with self.assertRaises(DocumentProblem):
            retry_document_job(second_request)
        active.refresh_from_db()
        self.assertEqual((active.status, active.attempt), (DocumentJob.Status.FAILED, 2))

    def test_profile_revision_invalidates_generated_release_approval(self):
        self.queue()
        job = self.finish_next()
        self.approve(job)
        self.assertEqual(self.client.get(reverse("document_download", args=[job.pk])).status_code, 200)

        Profile.objects.filter(pk=self.profile.pk).update(revision=self.profile.revision + 1)
        self.profile.refresh_from_db()
        blocked = self.client.get(reverse("document_download", args=[job.pk]))
        self.assertEqual(blocked.status_code, 403)
        self.assertContains(blocked, "Final download is locked", status_code=403)

    def test_cover_letter_requires_text_and_template_switch_preserves_content(self):
        invalid = self.queue(document_type="cover_letter")
        self.assertEqual(invalid.status_code, 422)
        self.assertContains(invalid, "Add the cover-letter text", status_code=422)
        self.assertFalse(DocumentJob.objects.exists())

        letter = "Dear Hiring Team,\nI am applying because this role matches my confirmed experience."
        self.queue(document_type="cover_letter", cover_letter_body=letter, company="Example Co", role="Engineer")
        self.queue(
            document_type="cover_letter",
            output_format="docx",
            template_key="clean",
            cover_letter_body=letter,
            company="Example Co",
            role="Engineer",
        )
        jobs = list(DocumentJob.objects.order_by("pk"))
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0].content_snapshot["extra"]["body"], letter)
        self.assertEqual(jobs[1].content_snapshot["extra"]["body"], letter)

    def test_unsaved_profile_cannot_queue_and_worker_command_is_locked_down(self):
        other = Client()
        blank_user = get_user_model().objects.create_user("blank_profile")
        other.force_login(blank_user)
        response = other.post(reverse("document_queue"), {
            "document_type": "cv", "output_format": "pdf", "template_key": "classic",
        })
        self.assertEqual(response.status_code, 422)
        self.assertFalse(DocumentJob.objects.filter(user=blank_user).exists())

        self.queue()
        job = claim_next_job()
        command = _worker_command(Path("/tmp/document-job"), job)
        joined = " ".join(command)
        self.assertIn("--network=none", command)
        self.assertIn("--read-only", command)
        self.assertIn("--cap-drop=ALL", command)
        self.assertIn("--security-opt=no-new-privileges", command)
        self.assertIn("--pids-limit=128", command)
        self.assertIn("--memory=", joined)
        self.assertIn("--cpus=", joined)
        self.assertEqual(command[-1], "lualatex")

        cover = DocumentJob.objects.create(
            user=self.user,
            profile_revision=self.profile.revision,
            document_type="cover_letter",
            output_format="pdf",
            template_key="classic",
            fingerprint="f" * 64,
            content_snapshot={
                "profile": PROFILE,
                "extra": {"body": "Dear Hiring Team,\nThank you.", "company": "Example", "role": "Engineer"},
            },
            status="running",
        )
        self.assertEqual(_worker_command(Path("/tmp/document-job-2"), cover)[-1], "xelatex")
