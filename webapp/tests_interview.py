from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import CareerContext, MockInterview, MockAnswer, Profile


class InterviewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="mock-owner")
        self.other = get_user_model().objects.create_user(username="mock-other")
        Profile.objects.create(user=self.user, revision=1, data={"skills": ["Python"]})
        CareerContext.objects.create(user=self.user, data={"role": "Engineer", "employer": "Acme",
            "posting_description": "Python and SQL", "interview_skills": "Python, SQL"})
        self.client.force_login(self.user)

    def start(self, mode="text", consent=False):
        response = self.client.post(reverse("interview-home"), {"mode": mode, "consent": consent})
        self.assertEqual(response.status_code, 302)
        return MockInterview.objects.get(user=self.user)

    def answer(self, attempt, index, **fields):
        return self.client.post(reverse("interview-detail", args=[attempt.pk]), {"index": index, **fields})

    def test_complete_mock_and_replay(self):
        attempt = self.start()
        self.assertContains(self.client.get(reverse("interview-detail", args=[attempt.pk])), "Acme")
        for index in range(3):
            self.assertEqual(self.answer(attempt, index, transcript="I used Python because it is appropriate. I test and validate results.").status_code, 302)
            if index == 0:
                self.assertEqual(self.answer(attempt, index, transcript="duplicate").status_code, 409)
        attempt.refresh_from_db()
        self.assertTrue(attempt.completed)
        self.assertEqual(attempt.answers.count(), 3)
        self.assertIn("SQL", attempt.answers.get(question_index=1).feedback["missing"])
        self.assertContains(self.client.get(reverse("interview-detail", args=[attempt.pk])), "Interview complete")

    def test_expired_answer_is_not_saved(self):
        attempt = self.start("voice", True)
        MockInterview.objects.filter(pk=attempt.pk).update(deadline=timezone.now()-timedelta(seconds=1))
        media = SimpleUploadedFile("late.webm", b"\x1a\x45\xdf\xa3fixture", content_type="audio/webm")
        self.assertEqual(self.answer(attempt, 0, transcript="late", recording=media).status_code, 302)
        answer = attempt.answers.get()
        self.assertTrue(answer.timed_out)
        self.assertEqual(answer.transcript, "")
        self.assertEqual(answer.recording, b"")
        self.assertEqual(answer.media_type, "")
        self.assertEqual(answer.feedback, {})
        page = self.client.get(reverse("interview-detail", args=[attempt.pk]))
        self.assertContains(page, "Time expired; answer not saved.")
        self.assertNotContains(page, "Clarity:")
        self.assertNotContains(page, "Missing rubric points:")

    def test_recording_consent_privacy_and_deletion(self):
        self.assertEqual(self.client.post(reverse("interview-home"), {"mode": "voice"}).status_code, 422)
        attempt = self.start("voice", True)
        bad = SimpleUploadedFile("bad.webm", b"bad", content_type="audio/webm")
        self.assertEqual(self.answer(attempt, 0, recording=bad).status_code, 422)
        media = SimpleUploadedFile("test.webm", b"\x1a\x45\xdf\xa3fixture", content_type="audio/webm")
        self.assertEqual(self.answer(attempt, 0, transcript="My private answer", recording=media).status_code, 302)
        download = reverse("interview-download", args=[attempt.pk, 0])
        erase = reverse("interview-erase", args=[attempt.pk, 0])
        self.assertEqual(self.client.get(download).content, b"\x1a\x45\xdf\xa3fixture")
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(download).status_code, 404)
        self.assertEqual(self.client.post(erase, {"action": "recording"}).status_code, 404)
        self.assertEqual(self.client.get(reverse("interview-detail", args=[attempt.pk])).status_code, 404)
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(erase, {"action": "recording"}).status_code, 302)
        self.assertEqual(self.client.get(download).status_code, 404)
        self.client.post(erase, {"action": "transcript"})
        answer = MockAnswer.objects.get(interview=attempt)
        self.assertEqual((answer.transcript, answer.feedback), ("", {}))
        self.client.post(reverse("interview-delete", args=[attempt.pk]))
        self.assertFalse(MockInterview.objects.filter(pk=attempt.pk).exists())

    def test_text_mode_refuses_media(self):
        attempt = self.start()
        self.assertNotContains(self.client.get(reverse("interview-detail", args=[attempt.pk])), 'name="recording"')
        media = SimpleUploadedFile("test.webm", b"\x1a\x45\xdf\xa3fixture", content_type="audio/webm")
        self.assertEqual(self.answer(attempt, 0, recording=media).status_code, 422)
        self.assertEqual(attempt.answers.count(), 0)

    def test_context_snapshot_and_clear(self):
        attempt = self.start()
        CareerContext.objects.filter(user=self.user).update(data={"role": "Changed"})
        self.client.post(reverse("career-clear"))
        attempt.refresh_from_db()
        self.assertEqual(attempt.context["role"], "Engineer")
        self.assertFalse(CareerContext.objects.filter(user=self.user).exists())

    def test_media_only_has_no_transcript_score_and_video_is_private(self):
        attempt = self.start("video", True)
        media = SimpleUploadedFile("test.webm", b"\x1a\x45\xdf\xa3fixture", content_type="video/webm")
        self.assertEqual(self.answer(attempt, 0, recording=media).status_code, 302)
        answer = attempt.answers.get()
        self.assertEqual(answer.media_type, "video/webm")
        self.assertEqual(answer.feedback, {})
        self.assertEqual(self.client.get(reverse("interview-download", args=[attempt.pk, 0]))["Content-Type"],
                         "application/octet-stream")

    def test_mislabeled_audio_webm_is_accepted_but_video_is_not(self):
        attempt = self.start("voice", True)
        # Minimal EBML/Segment/Tracks/TrackEntry/TrackType metadata fixtures.
        def webm(track_type):
            return bytes.fromhex("1a45dfa380185380678a1654ae6b85ae838381") + bytes([track_type])
        video = SimpleUploadedFile("video.webm", webm(1), content_type="video/webm")
        self.assertEqual(self.answer(attempt, 0, recording=video).status_code, 422)
        audio = SimpleUploadedFile("audio.webm", webm(2), content_type="video/webm")
        self.assertEqual(self.answer(attempt, 0, recording=audio).status_code, 302)
        self.assertEqual(attempt.answers.get().media_type, "audio/webm")

    def test_attempt_cap_allows_delete_and_restart(self):
        from .interview_views import MAX_ATTEMPTS
        for _ in range(MAX_ATTEMPTS):
            self.assertEqual(self.client.post(reverse("interview-home"), {"mode": "text"}).status_code, 302)
        response = self.client.post(reverse("interview-home"), {"mode": "voice", "consent": True})
        self.assertContains(response, "Delete one before starting another", status_code=422)
        attempt = MockInterview.objects.filter(user=self.user).first()
        self.client.post(reverse("interview-delete", args=[attempt.pk]))
        self.assertEqual(self.client.post(reverse("interview-home"), {"mode": "text"}).status_code, 302)

    def test_recorded_technical_answer_can_be_reviewed_after_completion(self):
        attempt = self.start("voice", True)
        self.answer(attempt, 0, transcript="First answer")
        media = SimpleUploadedFile("technical.webm", b"\x1a\x45\xdf\xa3fixture", content_type="audio/webm")
        self.assertEqual(self.answer(attempt, 1, recording=media).status_code, 302)
        answer = attempt.answers.get(question_index=1)
        original = bytes(answer.recording)
        self.assertEqual((answer.transcript, answer.feedback), ("", {}))
        url = reverse("interview-transcript", args=[attempt.pk, 1])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.answer(attempt, 2, transcript="Last answer")
        attempt.refresh_from_db()
        self.assertTrue(attempt.completed)
        deadline = attempt.deadline
        self.assertContains(self.client.get(url), "Download original recording")
        transcript = "I chose Python because it is maintainable. I test and validate results."
        self.assertEqual(self.client.post(url, {"transcript": transcript}).status_code, 422)
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(url, {"transcript": transcript, "confirm": True}).status_code, 404)
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(url, {"transcript": transcript, "confirm": True}).status_code, 302)
        answer.refresh_from_db()
        attempt.refresh_from_db()
        self.assertEqual(bytes(answer.recording), original)
        self.assertEqual(attempt.deadline, deadline)
        self.assertEqual(answer.transcript, transcript)
        self.assertIsInstance(answer.feedback["clarity"], int)
        self.assertIsInstance(answer.feedback["technical_depth"], int)
        self.assertIn("SQL", answer.feedback["missing"])
        self.assertEqual(answer.feedback["excerpt"], transcript)
        page = self.client.get(reverse("interview-detail", args=[attempt.pk]))
        self.assertContains(page, "Missing rubric points: SQL")
        self.assertContains(page, transcript)
        erase = reverse("interview-erase", args=[attempt.pk, 1])
        self.client.post(erase, {"action": "transcript"})
        answer.refresh_from_db()
        self.assertEqual((answer.transcript, answer.feedback), ("", {}))
        self.client.post(erase, {"action": "recording"})
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, {"transcript": transcript, "confirm": True}).status_code, 404)
        self.assertEqual(self.client.get(reverse("interview-transcript", args=[attempt.pk, 0])).status_code, 404)
