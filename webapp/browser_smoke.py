"""Optional real-browser acceptance test. Run with manage.py test webapp.browser_smoke."""
import os
from time import perf_counter

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
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


class BrowserBoard(StaticLiveServerTestCase):
    def test_stage_change_reaches_another_signed_in_session(self):
        username, password = "board_browser", "stage-test-only-29"
        get_user_model().objects.create_user(username, password=password)

        def open_board(context):
            page = context.new_page()
            page.goto(self.live_server_url + reverse("login"))
            page.locator("[name=username]").fill(username)
            page.locator("[name=password]").fill(password)
            page.get_by_role("button", name="Sign in").click()
            page.get_by_role("link", name="Applications", exact=True).click()
            return page

        with sync_playwright() as runtime:
            browser = runtime.chromium.launch(executable_path=os.environ.get("JOBWEB_TEST_CHROMIUM"))
            try:
                writer = open_board(browser.new_context())
                writer.get_by_label("Company", exact=True).fill("Example Labs")
                writer.get_by_label("Role", exact=True).fill("Data Engineer")
                writer.get_by_role("button", name="Add to board").click()
                reader = open_board(browser.new_context())
                drafted = reader.get_by_role("region", name="Drafted", exact=True)
                expect(drafted.get_by_role("heading", name="Data Engineer")).to_be_visible()

                writer.get_by_label("Move to stage").select_option("interview")
                writer.get_by_role("button", name="Save change").click()
                reader.bring_to_front()
                interview = reader.get_by_role("region", name="Interview", exact=True)
                expect(interview.get_by_role("heading", name="Data Engineer")).to_be_visible(timeout=18000)
                interview.get_by_text("History", exact=True).click()
                expect(interview.get_by_text("drafted → Interview", exact=False)).to_be_visible()
            finally:
                browser.close()
