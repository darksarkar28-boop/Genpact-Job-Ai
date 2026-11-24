import os
import re

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.management import call_command
from django.urls import reverse
from playwright.sync_api import expect, sync_playwright

from webapp.tests import pdf


class BrowserCohortReview(StaticLiveServerTestCase):
    def test_admin_invites_new_student_mentor_comments_and_student_revises(self):
        password = "fixture-cohort-journey-98"
        users = get_user_model().objects
        users.create_user("placement_team", password=password)
        users.create_user("faculty_mentor", password=password)
        call_command("provision_campus", slug="college", name="Example College", seats=2, admin="placement_team")
        dashboard = self.live_server_url + reverse("campus:dashboard", args=["college"])

        def sign_in(page, username):
            page.locator("[name=username]").fill(username)
            page.locator("[name=password]").fill(password)
            page.get_by_role("button", name="Sign in").click()

        def submit_file(page, title):
            page.get_by_label("Résumé file").set_input_files({
                "name": title + ".pdf", "mimeType": "application/pdf", "buffer": pdf([title]),
            })
            page.get_by_label("Share this revision with the mentors assigned to my cohort").check()
            page.get_by_role("button", name="Share revision").click()

        with sync_playwright() as runtime:
            browser = runtime.chromium.launch(executable_path=os.environ.get("JOBWEB_TEST_CHROMIUM"))
            try:
                admin = browser.new_context().new_page()
                admin.goto(self.live_server_url + reverse("login"))
                sign_in(admin, "placement_team")
                admin.get_by_role("link", name="Campus", exact=True).click()
                admin.get_by_role("link", name="Example College").click()
                admin.get_by_label("New cohort name").fill("Engineering")
                admin.get_by_role("button", name="Create cohort").click()
                form = admin.locator("form").filter(has=admin.get_by_role("button", name="Create invitations"))
                for recipients, role in [("learner@example.test, spare@example.test", "Candidate"),
                                         ("mentor@example.test", "Mentor")]:
                    form.get_by_label("Recipient emails").fill(recipients)
                    form.get_by_role("combobox", name=re.compile(r"^Role\b")).select_option(label=role)
                    form.get_by_role("combobox", name=re.compile(r"^Cohort\b")).select_option(label="Engineering")
                    form.get_by_role("button", name="Create invitations").click()
                student_url = admin.get_by_role("link", name="Invitation for learner@example.test").get_attribute("href")
                mentor_url = admin.get_by_role("link", name="Invitation for mentor@example.test").get_attribute("href")

                student = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
                student.goto(student_url)
                student.get_by_role("link", name="Create an account", exact=True).click()
                student.locator("[name=username]").fill("campus_student")
                student.locator("[name=password1]").fill(password)
                student.locator("[name=password2]").fill(password)
                student.get_by_role("button", name="Create account").click()
                student.get_by_role("button", name="Accept invitation").click()
                expect(student.get_by_role("heading", name="Example College")).to_be_visible()
                student.get_by_role("link", name="Master profile", exact=True).click()
                student.get_by_role("link", name="Enter details manually").click()
                student.get_by_label("Full name").fill("Campus Student")
                student.get_by_role("button", name="Save profile").click()
                student.get_by_role("link", name="Campus", exact=True).click()
                student.get_by_role("link", name="Example College").click()
                student.get_by_role("spinbutton", name=re.compile(r"^Applications\b")).fill("2")
                student.get_by_role("spinbutton", name=re.compile(r"^Interviews\b")).fill("1")
                student.get_by_role("button", name="Save progress").click()
                submit_file(student, "Initial resume")
                expect(student.get_by_role("heading", name="Revision 1")).to_be_visible()

                mentor = browser.new_context().new_page()
                mentor.goto(mentor_url)
                mentor.get_by_role("link", name="Sign in", exact=True).last.click()
                sign_in(mentor, "faculty_mentor")
                mentor.get_by_role("button", name="Accept invitation").click()
                expect(mentor.get_by_text("1 of 1 have reported.", exact=False)).to_be_visible()
                mentor.get_by_role("link", name="campus_student’s résumé").click()
                with mentor.expect_download() as received:
                    mentor.get_by_role("link", name="Download Initial resume.pdf").click()
                self.assertEqual(received.value.suggested_filename, "Initial resume.pdf")
                mentor.get_by_label("Feedback on revision 1").fill("Quantify the result of your project.")
                mentor.get_by_role("button", name="Post feedback").click()
                student.reload()
                expect(student.get_by_text("Quantify the result of your project.")).to_be_visible()
                student.get_by_role("link", name="Back to Example College").click()
                submit_file(student, "Revised resume")
                expect(student.get_by_role("heading", name="Revision 2")).to_be_visible()
                expect(student.get_by_role("heading", name="Revision 1")).to_be_visible()
                student.get_by_role("button", name="Withdraw mentor access").click()
                self.assertEqual(mentor.reload().status, 404)
                admin.goto(dashboard)
                expect(admin.get_by_text("Withdrew résumé access", exact=True)).to_be_visible()
                self.assertTrue(student.evaluate("document.documentElement.scrollWidth <= window.innerWidth"))
            finally:
                browser.close()
