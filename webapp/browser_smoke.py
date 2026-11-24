"""Optional real-browser acceptance test. Run with manage.py test webapp.browser_smoke."""
import os
from time import perf_counter

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from playwright.sync_api import expect, sync_playwright

from .tests import RESUME, docx


class BrowserOnboarding(StaticLiveServerTestCase):
    def test_signup_to_saved_profile_on_desktop_and_mobile(self):
        with sync_playwright() as runtime:
            browser = runtime.chromium.launch(executable_path=os.environ.get("JOBWEB_TEST_CHROMIUM"))
            try:
                for width, height in ((1280, 840), (390, 844)):
                    with self.subTest(viewport=width):
                        context = browser.new_context(viewport={"width": width, "height": height})
                        try:
                            self.walkthrough(context.new_page(), width)
                        finally:
                            context.close()
            finally:
                browser.close()

    def walkthrough(self, page, width):
        username = f"browser_candidate_{width}"
        password = "violet-28-fixture-only"
        document = {"name": "resume.docx", "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "buffer": docx(RESUME)}
        start = perf_counter()
        page.goto(self.live_server_url)
        page.locator("[name=username]").fill(username)
        page.locator("[name=password1]").fill(password)
        page.locator("[name=password2]").fill(password)
        page.get_by_role("button", name="Create account").click()
        page.get_by_label("Résumé or LinkedIn export").set_input_files(document)
        page.get_by_role("button", name="Review import").click()
        expect(page.get_by_label("Full name")).to_have_value("Mira Das")
        page.get_by_label("Full name").fill("Mira R. Das")
        page.get_by_role("button", name="Add another role").click()
        page.locator('[name="experience-2-title"]').fill("Mentor")
        page.get_by_role("button", name="Save profile").click()
        expect(page.get_by_role("heading", name="Mira R. Das")).to_be_visible()
        elapsed = perf_counter() - start
        print(f"Automated {width}px signup → confirmed DOCX profile: {elapsed:.2f} seconds")
        self.assertLess(elapsed, 120)
        self.assertTrue(page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"))
        page.get_by_role("button", name="Sign out").click()
        page.locator("[name=username]").fill(username)
        page.locator("[name=password]").fill(password)
        page.get_by_role("button", name="Sign in").click()
        expect(page.get_by_role("heading", name="Mentor")).to_be_visible()
