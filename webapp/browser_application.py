"""Exercise the issue 6 release form and inspect what Chromium downloads."""
import json
import os
import zipfile

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from playwright.sync_api import expect, sync_playwright

from .models import Profile
from .tests_application import CV_LINES, PROFILE, TERMS, pdf


class BrowserApplicationReview(StaticLiveServerTestCase):
    def test_review_download_and_edit_after_approval(self):
        password = "fixture-browser-review-37"
        user = get_user_model().objects.create_user("release_candidate", password=password)
        Profile.objects.create(user=user, revision=1, data=PROFILE)
        cv = pdf(CV_LINES)
        cover = pdf(["Dear Hiring Team,", "I enjoy building reporting tools using Python and SQL."])

        def upload(page, cv_bytes):
            for label, name, content in [
                ("Final CV PDF", "cv.pdf", cv_bytes),
                ("Final cover letter PDF (optional)", "letter.pdf", cover),
            ]:
                page.get_by_label(label, exact=True).set_input_files(
                    {"name": name, "mimeType": "application/pdf", "buffer": content}
                )

        with sync_playwright() as runtime:
            browser = runtime.chromium.launch(executable_path=os.environ.get("JOBWEB_TEST_CHROMIUM"))
            try:
                page = browser.new_page(accept_downloads=True)
                page.goto(self.live_server_url + reverse("login"))
                page.locator("[name=username]").fill(user.username)
                page.locator("[name=password]").fill(password)
                page.get_by_role("button", name="Sign in").click()
                page.get_by_role("link", name="Review an application").click()
                page.get_by_label("Target employer").fill("Example Labs")
                page.get_by_label("CV section headings in their visible order").fill("Experience\nEducation")
                page.get_by_label("Posting keywords (JSON)", exact=True).fill(json.dumps(TERMS))
                page.get_by_role("checkbox").check()
                upload(page, cv)
                page.get_by_role("button", name="Check document").click()
                expect(page.get_by_role("row").filter(has_text="Kubernetes")).to_contain_text("missing")
                export = page.get_by_role("button", name="Recheck and export ZIP")
                expect(export).to_be_visible()
                upload(page, cv)
                with page.expect_download() as downloading:
                    export.click()
                download = downloading.value
                self.assertEqual(download.suggested_filename, "verified_application.zip")
                with zipfile.ZipFile(download.path()) as bundle:
                    self.assertEqual(bundle.read("validated_cv.pdf"), cv)
                    self.assertEqual(bundle.read("validated_cover_letter.pdf"), cover)
                    self.assertTrue(json.loads(bundle.read("verification_report.json"))["allowed"])

                edited = pdf([line.replace("25%", "99%") for line in CV_LINES])
                upload(page, edited)
                export.click()
                expect(page.get_by_text("Unsupported metric or date: 99%", exact=True)).to_be_visible()
                expect(export).to_have_count(0)
            finally:
                browser.close()
