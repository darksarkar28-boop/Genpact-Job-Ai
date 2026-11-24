"""Browser acceptance with fake local microphone/camera devices."""
import os
import re
from concurrent.futures import ThreadPoolExecutor

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.db import connections
from playwright.sync_api import expect, sync_playwright

from .models import CareerContext, MockInterview, Profile


def database_value(operation):
    """Playwright sync API runs an event loop in this thread; use an isolated ORM worker."""
    def read():
        try:
            return operation()
        finally:
            connections.close_all()
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(read).result(timeout=15)


class BrowserCareer(StaticLiveServerTestCase):
    def test_career_voice_mock_and_privacy(self):
        user = get_user_model().objects.create_user(username="career-browser", password="fixture-violet-198")
        Profile.objects.create(user=user, revision=1, data={"full_name": "Fixture", "skills": ["Python"]})
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=os.environ.get("JOBWEB_TEST_CHROMIUM"),
                args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"])
            context = browser.new_context(permissions=["microphone", "camera"])
            try:
                page = context.new_page()
                page.goto(self.live_server_url + "/accounts/login/")
                page.locator("[name=username]").fill("career-browser")
                page.locator("[name=password]").fill("fixture-violet-198")
                page.get_by_role("button", name="Sign in").click()
                page.wait_for_url("**/profile/")
                page.goto(self.live_server_url + "/career/")
                for field, value in {"employer": "Acme", "role": "Engineer", "location": "Delhi",
                    "level": "Junior", "currency": "INR", "since": "2025-01-01", "posting_description": "Python and SQL",
                    "interview_skills": "Python, SQL", "offer": "500000", "target": "600000"}.items():
                    page.locator(f"[name={field}]").fill(value)
                page.get_by_role("button", name="Analyze evidence").click()
                expect(page.get_by_text("Insufficient evidence for a band")).to_be_visible()
                expect(page.get_by_text("Insufficient repeated evidence for a priority skill gap")).to_be_visible()
                expect(page.locator("#counter-script")).to_have_value(re.compile("600,000.00"))
                page.get_by_role("link", name="Open timed mock interviews").click()
                expect(page.get_by_role("heading", name="Engineer at Acme")).to_be_visible()
                page.locator("[name=mode]").select_option("voice")
                page.locator("[name=consent]").check()
                page.get_by_role("button", name="Start timed interview").click()
                page.get_by_role("button", name="Start recording", exact=True).click()
                expect(page.locator("#recording-status")).to_have_text("Recording…")
                page.wait_for_timeout(1100)
                page.get_by_role("button", name="Stop recording", exact=True).click()
                expect(page.locator("#recording-status")).to_have_text("Recording ready; review before submitting.")
                for i in range(3):
                    expect(page.get_by_role("heading", name=f"Question {i+1} of 3", exact=True)).to_be_visible()
                    page.locator("[name=transcript]").fill("I used Python because it worked. I test and validate results.")
                    page.get_by_role("button", name="Submit answer / continue").click()
                expect(page.get_by_role("heading", name="Interview complete")).to_be_visible()
                expect(page.get_by_role("link", name="Download recording")).to_be_visible()
                self.assertTrue(database_value(lambda: bool(MockInterview.objects.get(user_id=user.pk).answers.get(question_index=0).recording)))
                page.get_by_role("button", name="Delete recording", exact=True).click()
                expect(page.get_by_role("link", name="Download recording")).to_have_count(0)
                page.get_by_role("button", name="Delete entire interview and all recordings").click()
                expect(page.get_by_text("No interviews yet.")).to_be_visible()
                self.assertFalse(database_value(lambda: MockInterview.objects.filter(user_id=user.pk).exists()))
                self.assertEqual(database_value(lambda: CareerContext.objects.get(user_id=user.pk).data["role"]), "Engineer")
                # Exercise actual browser video capture, discard, and text fallback.
                page.locator("[name=mode]").select_option("video")
                page.locator("[name=consent]").check()
                page.get_by_role("button", name="Start timed interview").click()
                page.get_by_role("button", name="Start recording", exact=True).click()
                expect(page.locator("#recording-status")).to_have_text("Recording…")
                page.wait_for_timeout(1100)
                page.get_by_role("button", name="Stop recording", exact=True).click()
                expect(page.locator("#recording-status")).to_have_text("Recording ready; review before submitting.")
                page.get_by_role("button", name="Discard recording").click()
                expect(page.locator("#recording-status")).to_have_text("Recording discarded.")
                page.get_by_role("button", name="Start recording", exact=True).click()
                expect(page.locator("#recording-status")).to_have_text("Recording…")
                page.wait_for_timeout(1100)
                page.get_by_role("button", name="Stop recording", exact=True).click()
                expect(page.locator("#recording-status")).to_have_text("Recording ready; review before submitting.")
                page.locator("[name=transcript]").fill("My recorded video answer.")
                page.get_by_role("button", name="Submit answer / continue").click()
                expect(page.get_by_role("heading", name="Question 2 of 3", exact=True)).to_be_visible()
                self.assertEqual(database_value(lambda: MockInterview.objects.get(user_id=user.pk).answers.get(question_index=0).media_type), "video/webm")
                page.locator("[name=transcript]").fill("My text fallback answer.")
                page.get_by_role("button", name="Submit answer / continue").click()
                expect(page.get_by_role("heading", name="Question 3 of 3", exact=True)).to_be_visible()
                self.assertFalse(database_value(lambda: bool(MockInterview.objects.get(user_id=user.pk).answers.get(question_index=1).recording)))
                page.get_by_role("button", name="Delete entire interview and all recordings").click()
                expect(page.get_by_text("No interviews yet.")).to_be_visible()
                page.locator("[name=mode]").select_option("text")
                page.get_by_role("button", name="Start timed interview").click()
                expect(page.get_by_role("heading", name="Question 1 of 3", exact=True)).to_be_visible()
                expect(page.locator("[name=recording]")).to_have_count(0)
                page.locator("[name=transcript]").fill("Text-only practice.")
                page.get_by_role("button", name="Submit answer / continue").click()
                expect(page.get_by_role("heading", name="Question 2 of 3", exact=True)).to_be_visible()
                page.get_by_role("button", name="Delete entire interview and all recordings").click()
                expect(page.get_by_text("No interviews yet.")).to_be_visible()

            finally:
                context.close()
                browser.close()
        self.assertFalse(MockInterview.objects.filter(user=user).exists())
