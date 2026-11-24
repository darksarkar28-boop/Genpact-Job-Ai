"""Issue #6: backend preflight and export enforcement, not just a UI warning."""
import io
import json
import zipfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from .application_gate import audit, candidate_findings, company_findings, section_order
from .models import Profile

PROFILE = {
    "full_name": "Mira Das", "email": "mira@example.test",
    "skills": ["Python", "SQL"],
    "experience": [{"title": "Data Engineer", "employer": "Example Labs",
                    "dates": "2019 - 2024", "details": "Improved throughput by 25% for 50 clients."}],
    "education": [{"degree": "BSc Computer Science", "school": "Example University", "dates": "2015 - 2019"}],
}
CV_LINES = [
    "Mira Das", "mira@example.test",
    "Experience", "Data Engineer at Example Labs",
    "2019 - 2024", "Improved throughput by 25% for 50 clients.",
    "Education", "BSc Computer Science", "Skills", "Python SQL",
]
TERMS = {"keywords": [
    {"term": "Python", "priority": "required"},
    {"term": "Kubernetes", "priority": "preferred"},
]}


def pdf(lines):
    """Build a real searchable PDF in memory without an external compiler."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({
            NameObject("/F1"): DictionaryObject({
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            })
        })
    })
    stream = DecodedStreamObject()
    encoded = ["<" + line.encode("ascii").hex() + "> Tj T*" for line in lines]
    stream.set_data(("BT /F1 12 Tf 14 TL 45 740 Td\n" + "\n".join(encoded) + "\nET").encode())
    page.replace_contents(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class BackendReviewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.user = users.objects.create_user("mira", password="this-is-test-only#37")
        cls.other = users.objects.create_user("other", password="this-is-test-only#37")
        Profile.objects.create(user=cls.user, revision=1, data=PROFILE)
        Profile.objects.create(user=cls.other, revision=1, data={
            **PROFILE, "full_name": "Other Person", "email": "other@example.test",
            "experience": [], "education": [],
        })

    def setUp(self):
        self.client.force_login(self.user)

    def post_data(self, *, lines=None, action="review", token="", revision=1, extra=None):
        fields = {
            "profile_revision": revision,
            "employer": "Example Labs",
            "visual_headings": "Experience\nEducation",
            "keywords": json.dumps(TERMS),
            "claims": "[]",
            "company_citations": "[]",
            "confirm_visual": "on",
            "cv_pdf": SimpleUploadedFile("final_cv.pdf", pdf(CV_LINES if lines is None else lines),
                                          content_type="application/pdf"),
            "action": action,
            "token": token,
        }
        fields.update(extra or {})
        return fields

    def test_valid_review_and_export_both_check_final_pdf(self):
        reviewed = self.client.post(reverse("application_review"), self.post_data())
        self.assertEqual(reviewed.status_code, 200, reviewed.content[:300])
        report = reviewed.context["report"]
        self.assertTrue(report["allowed"])
        self.assertEqual(report["coverage"]["summary"]["required"]["covered"], 1)
        self.assertEqual(report["coverage"]["keywords"][1]["status"], "missing (unverified)")
        token = reviewed.context["token"]
        self.assertTrue(token)
        downloaded = self.client.post(reverse("application_review"), self.post_data(action="export", token=token))
        self.assertEqual(downloaded.status_code, 200, downloaded.content[:200])
        self.assertEqual(downloaded["Content-Type"], "application/zip")
        with zipfile.ZipFile(io.BytesIO(downloaded.content)) as bundle:
            self.assertIn("validated_cv.pdf", bundle.namelist())
            self.assertTrue(bundle.read("validated_cv.pdf").startswith(b"%PDF-"))
            report = json.loads(bundle.read("verification_report.json"))
            self.assertTrue(report["allowed"])

    def test_export_without_approval_is_rejected_by_backend(self):
        response = self.client.post(reverse("application_review"), self.post_data(action="export"))
        self.assertEqual(response.status_code, 422)
        self.assertContains(response, "Export denied", status_code=422)

    def test_edited_document_requires_reapproval_and_rechecks_claim(self):
        reviewed = self.client.post(reverse("application_review"), self.post_data())
        modified = [line.replace("25%", "99%") for line in CV_LINES]
        response = self.client.post(reverse("application_review"),
            self.post_data(lines=modified, action="export", token=reviewed.context["token"]))
        self.assertEqual(response.status_code, 422)
        self.assertContains(response, "Unsupported metric", status_code=422)

    def test_harmless_edit_also_invalidates_signed_pdf_approval(self):
        reviewed = self.client.post(reverse("application_review"), self.post_data())
        changed = [*CV_LINES, "I use Python for internal analytics."]
        result = self.client.post(reverse("application_review"),
            self.post_data(lines=changed, action="export", token=reviewed.context["token"]))
        self.assertEqual(result.status_code, 422)
        self.assertContains(result, "current approved review", status_code=422)

    def test_changed_profile_invalidates_prior_approval(self):
        reviewed = self.client.post(reverse("application_review"), self.post_data())
        Profile.objects.filter(user=self.user).update(revision=2)
        result = self.client.post(reverse("application_review"),
                                  self.post_data(action="export", token=reviewed.context["token"]))
        self.assertEqual(result.status_code, 409)
        self.assertNotEqual(result.get("Content-Type"), "application/zip")

    def test_two_account_isolation_uses_logged_in_profile_not_uploaded_claims(self):
        self.client.force_login(self.other)
        result = self.client.post(reverse("application_review"), self.post_data())
        self.assertEqual(result.status_code, 422)
        self.assertContains(result, "confirmed candidate name", status_code=422)

    def test_invalid_or_scanned_pdf_blocks(self):
        for data in (b"not PDF at all", pdf([])):
            with self.subTest(size=len(data)):
                fields = self.post_data()
                fields["cv_pdf"] = SimpleUploadedFile("bad.pdf", data, content_type="application/pdf")
                response = self.client.post(reverse("application_review"), fields)
                self.assertEqual(response.status_code, 422)
                self.assertContains(response, "PDF", status_code=422)

    def test_unconfirmed_employer_degree_date_and_metric_block(self):
        tampered = [
            [s.replace("Example Labs", "Fake LLC") for s in CV_LINES],
            [s.replace("BSc Computer Science", "BSc Quantum Physics") for s in CV_LINES],
            [s.replace("2024", "2025") for s in CV_LINES],
            [s.replace("25%", "999%") for s in CV_LINES],
        ]
        for lines in tampered:
            with self.subTest(lines=lines):
                response = self.client.post(reverse("application_review"), self.post_data(lines=lines))
                self.assertEqual(response.status_code, 422)
                self.assertFalse(response.context["report"]["allowed"])

    def test_cv_headings_out_of_visual_order_block(self):
        lines = [line for line in CV_LINES if line not in ("Experience", "Education")]
        lines.insert(2, "Education")
        lines.insert(5, "Experience")
        response = self.client.post(reverse("application_review"), self.post_data(lines=lines))
        self.assertEqual(response.status_code, 422)
        self.assertContains(response, "heading order", status_code=422)

    def test_unsourced_target_company_news_blocks_cover_letter(self):
        cover = pdf(["Dear Hiring Manager", "Example Labs launched a satellite platform in 2024.",
                     "I would love to work on data quality at Example Labs."])
        fields = self.post_data()
        fields["cover_pdf"] = SimpleUploadedFile("cover.pdf", cover, content_type="application/pdf")
        response = self.client.post(reverse("application_review"), fields)
        self.assertEqual(response.status_code, 422)
        self.assertContains(response, "Unsourced employer-specific statement", status_code=422)

    def test_multipart_size_gate_rejects_large_final_upload_before_parsing(self):
        response = self.client.generic("POST", reverse("application_review"),
                                       CONTENT_LENGTH=str(17 * 1024 * 1024))
        self.assertEqual(response.status_code, 413)

    def test_visual_review_confirmation_is_mandatory(self):
        response = self.client.post(reverse("application_review"),
            self.post_data(extra={"confirm_visual": ""}))
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("application/zip", response.get("Content-Type", ""))

    def test_missing_csrf_and_unauthenticated_access_block(self):
        anonymous = Client()
        self.assertEqual(anonymous.get(reverse("application_review")).status_code, 302)
        guarded = Client(enforce_csrf_checks=True)
        guarded.force_login(self.user)
        self.assertEqual(guarded.post(reverse("application_review"), self.post_data()).status_code, 403)

    def test_invalid_manifest_blocks(self):
        response = self.client.post(reverse("application_review"), self.post_data(extra={"claims": "not JSON"}))
        self.assertEqual(response.status_code, 422)

    def test_declared_claim_must_be_backed_by_confirmed_profile(self):
        response = self.client.post(reverse("application_review"),
            self.post_data(extra={"claims": json.dumps([{"claim": "Python SQL", "evidence": "invented award"}])}))
        self.assertEqual(response.status_code, 422)
        self.assertContains(response, "Candidate claim", status_code=422)


class PureAuditTests(TestCase):
    def test_company_source_fetch_must_actually_support_the_claim(self):
        text = "Example Labs launched a satellite platform."
        citations = [{"claim": text, "url": "https://news.example.test/launch"}]
        failed, _ = company_findings(text, "Example Labs", citations,
                                     source_fetcher=lambda url: "No such launch")
        self.assertTrue(failed)
        passed, _ = company_findings(text, "Example Labs", citations,
                                     source_fetcher=lambda url: text)
        self.assertEqual(passed, [])

    def test_fabricated_role_and_claim_are_rejected(self):
        findings = candidate_findings("Position: Lead Astronaut\nSkills Python", PROFILE, [])
        self.assertTrue(any("Unsupported job title" in item for item in findings))

    def test_reordered_headings_detected(self):
        self.assertIsNotNone(section_order("Education\nExperience", ["Experience", "Education"]))
        self.assertIsNone(section_order("Experience\nEducation", ["Experience", "Education"]))

    def test_no_pdf_text_never_passes(self):
        report = audit(PROFILE, pdf([]), keywords=TERMS["keywords"], visual_headings=["Experience", "Education"])
        self.assertFalse(report["allowed"])
