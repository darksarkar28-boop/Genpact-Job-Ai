"""Exercise the same account, import, review and save endpoints as the browser."""
import io
import zipfile
from xml.sax.saxutils import escape

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from .models import Profile

RESUME = [
    "Mira Das", "Software Engineer", "Pune, India", "mira@example.test | +91 70000 00001",
    "Summary", "Builds reporting tools.", "Work Experience", "Data Engineer at Example Labs",
    "2021 - Present", "Built internal pipelines.", "Engineer at Prior Labs", "2019 - 2021",
    "Education", "Example University", "BSc Computer Science", "2015 - 2019", "Skills: Python, SQL",
]
LINKEDIN_PDF = [
    "Contact", "mira@example.test", "Top Skills", "Python", "SQL", "Mira Das", "Software Engineer",
    "Pune, India", "Summary", "Builds reporting tools.", "Experience", "Example Labs",
    "Software Engineer", "June 2021 - Present (5 years 1 month)", "Built pipelines.", "Education",
    "Example University", "BSc Computer Science (2015 - 2019)",
]


def pdf(lines):
    writer = PdfWriter()
    page = writer.add_blank_page(612, 792)
    def names(**kwargs):
        return DictionaryObject({NameObject("/" + k): NameObject("/" + v) for k, v in kwargs.items()})
    font = names(Type="Font", Subtype="Type1", BaseFont="Helvetica")
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
    })
    stream = DecodedStreamObject()
    # Hex strings encode literal fixture text without PDF string escaping.
    lines = ["<" + line.encode("ascii").hex() + "> Tj T*" for line in lines]
    stream.set_data(("BT /F1 11 Tf 14 TL 45 740 Td\n" + "\n".join(lines) + "\nET").encode())
    page.replace_contents(stream)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def archive(parts):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as doc:
        for path, content in parts.items():
            doc.writestr(path, content)
    return out.getvalue()


def docx(lines):
    paragraphs = "".join("<w:p><w:r><w:t>" + escape(line) + "</w:t></w:r></w:p>" for line in lines)
    return archive({
        "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                               '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        "_rels/.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                       '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        "word/document.xml": '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                             '<w:body>' + paragraphs + "</w:body></w:document>",
    })


def linkedin_zip():
    return archive({
        "Export/Profile.csv": "\ufeffFirst Name,Last Name,Headline,Geo Location,Summary\nMira,Das,Data Engineer,Pune,Builds tools.",
        "Export/Email Addresses.csv": "Email Address,Primary\nold@example.test,No\nmira@example.test,Yes",
        "Export/Positions.csv": "Title,Company Name,Started On,Finished On,Description\nData Engineer,Example Labs,2021,2024,Built reports.",
        "Export/Education.csv": "School Name,Degree Name,Start Date,End Date\nExample University,BSc,2015,2019",
        "Export/Skills.csv": "Name\nPython\nSQL\npython",
        "Export/Messages.csv": "Subject,Body\nPrivate,Mailbox content is irrelevant",
    })


def form_values(response):
    form = response.context["form"]
    data = {field.html_name: field.value() for field in form if field.value() is not None}
    for name in ("experience", "education"):
        group = response.context[name]
        for record in [group.management_form, *group.forms]:
            for field in record:
                if field.value() is not None and field.name != "DELETE":
                    data[field.html_name] = field.value()
    return data


class OnboardingTests(TestCase):
    password = "example-only-29-violet!"

    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user("fixture_owner", password=cls.password)

    def setUp(self):
        self.client.force_login(self.owner)

    def upload(self, filename, content, client=None):
        return (client or self.client).post(reverse("import"), {"document": SimpleUploadedFile(filename, content)})

    def test_signup_import_edit_save_and_return(self):
        visitor = Client()
        self.assertRedirects(visitor.get("/"), reverse("signup"))
        signup = visitor.post(reverse("signup"), {"username": "new_candidate", "password1": self.password,
                                                   "password2": self.password}, follow=True)
        self.assertContains(signup, "Import your details")
        review = self.upload("resume.docx", docx(RESUME), visitor)
        self.assertContains(review, "Review your profile")
        self.assertFalse(Profile.objects.get(user__username="new_candidate").data)
        data = form_values(review)
        data.update(full_name="Mira R. Das", **{"experience-0-title": "Reporting Engineer"})
        saved = visitor.post(reverse("edit"), data, follow=True)
        self.assertContains(saved, "Your master profile has been saved.")
        self.assertContains(saved, "Reporting Engineer")
        self.assertRedirects(visitor.post(reverse("logout")), reverse("login"))
        self.assertEqual(visitor.get(reverse("workspace")).status_code, 302)
        opened = visitor.post(reverse("login"), {"username": "new_candidate", "password": self.password}, follow=True)
        self.assertContains(opened, "Mira R. Das")
        self.assertContains(opened, "Example University")

    def test_real_files_produce_structured_review_fields(self):
        for name, content, title in [
            ("resume.pdf", pdf(RESUME), "Data Engineer"), ("RESUME.DOCX", docx(RESUME), "Data Engineer"),
            ("LinkedIn Profile.pdf", pdf(LINKEDIN_PDF), "Software Engineer"),
            ("Basic_LinkedInDataExport.zip", linkedin_zip(), "Data Engineer"),
        ]:
            with self.subTest(name=name):
                review = self.upload(name, content)
                self.assertEqual(review.status_code, 200)
                values = form_values(review)
                self.assertEqual(values["full_name"], "Mira Das")
                self.assertEqual(values["email"], "mira@example.test")
                self.assertEqual(values["skills"], "Python, SQL")
                self.assertEqual(values["experience-0-title"], title)
                self.assertEqual(values["experience-0-employer"], "Example Labs")
                self.assertEqual(values["education-0-school"], "Example University")
                self.assertTrue(values["education-0-degree"].startswith("BSc"))
                self.assertNotContains(review, "Mailbox content")
                self.assertFalse(Profile.objects.get(user=self.owner).data)

    def test_failed_cancelled_and_repeated_imports_preserve_confirmed_data(self):
        original = form_values(self.upload("resume.docx", docx(RESUME)))
        original["headline"] = "Confirmed by candidate"
        self.client.post(reverse("edit"), original)
        profile = Profile.objects.get(user=self.owner)
        for name, content in [("scan.pdf", pdf([])), ("bad.docx", b"unreadable"), ("wrong.txt", b"text"),
                              ("unrelated.zip", archive({"notes.csv": "a,b"}))]:
            with self.subTest(name=name):
                self.assertEqual(self.upload(name, content).status_code, 422)
        self.upload("data.zip", linkedin_zip())
        self.client.get(reverse("workspace"))  # Cancel the unsaved import.
        self.assertEqual(Profile.objects.get(pk=profile.pk).data, profile.data)
        duplicate = form_values(self.upload("resume.docx", docx(RESUME)))
        self.assertEqual(duplicate["headline"], "Confirmed by candidate")
        self.assertEqual(duplicate["experience-TOTAL_FORMS"], 2)
        self.assertEqual(duplicate["education-TOTAL_FORMS"], 1)

    def test_account_isolation_applies_to_reads_imports_and_writes(self):
        first = form_values(self.upload("data.zip", linkedin_zip()))
        self.client.post(reverse("edit"), first)
        private = Profile.objects.get(user=self.owner)
        other = Client()
        other.force_login(get_user_model().objects.create_user("second_candidate"))
        page = other.get(reverse("workspace"), {"user": self.owner.pk})
        self.assertNotContains(page, "Mira")
        own_review = self.upload("own.docx", docx(["Nila Roy", "Skills: Design"]), other)
        self.assertNotContains(own_review, "Example Labs")
        own = form_values(own_review)
        own.update(user=self.owner.pk, profile_id=private.pk, full_name="Nila Roy")
        self.assertRedirects(other.post(reverse("edit"), own), reverse("workspace"))
        self.assertEqual(Profile.objects.get(user=self.owner).data, private.data)
        self.assertNotContains(self.client.get(reverse("workspace")), "Nila Roy")

    def test_manual_entry_validation_and_stale_edit(self):
        review = self.client.get(reverse("edit"))
        data = form_values(review)
        invalid = self.client.post(reverse("edit"), {**data, "full_name": ""})
        self.assertContains(invalid, "This field is required", status_code=422)
        self.assertRedirects(self.client.post(reverse("edit"), {**data, "full_name": "First edit"}), reverse("workspace"))
        conflict = self.client.post(reverse("edit"), {**data, "full_name": "Stale edit"})
        self.assertContains(conflict, "changed in another tab", status_code=409)
        self.assertEqual(Profile.objects.get(user=self.owner).data["full_name"], "First edit")

    def test_oversized_upload_is_rejected_before_multipart_processing(self):
        response = self.client.generic("POST", reverse("import"), CONTENT_LENGTH=str(9 * 1024 * 1024))
        self.assertEqual(response.status_code, 413)

    def test_private_operations_require_session_and_csrf(self):
        public = Client()
        for name in ("workspace", "edit", "import"):
            self.assertEqual(public.get(reverse(name)).status_code, 302)
        guarded = Client(enforce_csrf_checks=True)
        guarded.force_login(self.owner)
        self.assertEqual(guarded.post(reverse("edit"), {"full_name": "unsaved"}).status_code, 403)
