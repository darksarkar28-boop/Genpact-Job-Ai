import datetime
import json

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from .career_planning import compensation_band, counter_offer, skill_gaps
from .models import CareerContext, Profile


def salary(source, amount, **changes):
    return dict(role="Engineer", location="Delhi", level="Junior", currency="INR",
                annual_base=amount, date="2025-01-01", source=source, **{}) | changes


class CareerEvidenceTests(SimpleTestCase):
    def band(self, rows):
        return compensation_band(rows, role="Engineer", location="Delhi", level="Junior",
                                 currency="INR", since=datetime.date(2025, 1, 1))

    def test_filter_deduplicate_and_estimate(self):
        rows = [salary("https://example.com/1", 100), salary("https://example.com/2", 200),
                salary("https://example.com/3", 300)]
        result = self.band(rows + [rows[0], salary("https://example.com/4", 900, currency="USD"),
                                  salary("https://example.com/5", 900, date="2024-01-01")])
        self.assertEqual(result["count"], 3)
        self.assertEqual(result["band"]["median"], 200)

    def test_sparse_and_invalid_evidence(self):
        self.assertIsNone(self.band([salary("https://example.com/1", 100)])["band"])
        with self.assertRaises(ValueError):
            self.band([salary("javascript:alert(1)", 100)])
        with self.assertRaises(ValueError):
            self.band([salary("https://example.com/1", "NaN")])

    def test_gap_coverage_and_sparse(self):
        postings = [{"role": "Engineer", "source": f"https://example.com/{n}",
                     "required_skills": ["Python", "SQL"]} for n in (1, 2)]
        gaps, total = skill_gaps(postings + postings, ["python"])
        self.assertEqual((total, gaps[0]["skill"], gaps[0]["count"]), (2, "SQL", 2))
        self.assertEqual(skill_gaps(postings[:1], ["Python"])[0], [])
        self.assertEqual((gaps[0]["baseline_match"], gaps[0]["estimated_match"], gaps[0]["match_gain"]), (50.0, 100.0, 50.0))

    def test_gap_rank_uses_weighted_match_gain_and_repeated_evidence(self):
        postings = [{"role": "Engineer", "source": f"https://example.com/{i}", "required_skills": skills}
                    for i, skills in enumerate([["Python", "SQL"], ["Python", "SQL"],
                       ["Python", "Docker", "A", "B", "C"], ["Python", "Docker", "D", "E", "F"],
                       ["Python", "Docker", "G", "H", "I"], ["Rare"]])]
        gaps, total = skill_gaps(postings, ["Python"])
        self.assertEqual(total, 6)
        self.assertEqual([g["skill"] for g in gaps], ["SQL", "Docker"])
        self.assertEqual([g["match_gain"] for g in gaps], [16.7, 10.0])
        self.assertEqual(gaps[0]["baseline_match"], 26.7)
        self.assertEqual(gaps[0]["estimated_match"], 43.3)

    def test_negotiation_requires_actual_offer_and_target(self):
        with self.assertRaises(ValueError):
            counter_offer(employer="Acme", role="Engineer", currency="INR", offer=500,
                          target=400, band=None)
        self.assertNotIn("supplied compensation", counter_offer(employer="Acme", role="Engineer",
                         currency="INR", offer=500, target=600, band=None))


class CareerWebTests(TestCase):
    def test_authentication_and_profile_isolation(self):
        user = get_user_model().objects.create_user(username="career-a")
        other = get_user_model().objects.create_user(username="career-b")
        Profile.objects.create(user=user, revision=1, data={"skills": ["SQL"]})
        Profile.objects.create(user=other, revision=1, data={"skills": ["Python"]})
        url = reverse("career-planning")
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(user)
        data = dict(employer="Acme", role="Engineer", location="Delhi", level="Junior", currency="INR",
                    since="2025-01-01", offer="500", target="600", salaries="[]",
                    postings=json.dumps([{"role": "Engineer", "source": f"https://example.com/{i}",
                                           "required_skills": ["SQL", "Python"]} for i in (1, 2)]))
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Insufficient evidence for a band")
        self.assertContains(response, "Python")
        self.assertContains(response, "Estimated match gain")
        self.assertEqual(CareerContext.objects.get(user=user).data["role"], "Engineer")
        self.assertNotContains(response, "<h3>SQL</h3>")
        self.client.force_login(other)
        response = self.client.post(url, data)
        self.assertContains(response, "SQL")
        self.assertNotContains(response, "<h3>Python</h3>")
        data["postings"] = "not json"
        self.assertEqual(self.client.post(url, data).status_code, 422)
