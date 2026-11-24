# Career preparation (issue #8)

Sign in, confirm a master profile and open Career preparation. Enter a company, role,
location, level, currency, a target job description and its source (if available).
Enter technical requirements separated by commas. Analyze evidence to save this
context; the interview uses the same role and the current confirmed profile. The
attempt preserves its original context and profile revision.

The salary planner accepts up to 100 user-supplied annual base salary observations
as JSON, with keys role, location, level, currency, annual_base, date and source.
All dimensions must match; at least three observations from two distinct URLs
are needed for a displayed range and median. The CLI salary index datasets do
not necessarily contain comparable annual currency values, so do not convert
index values into salaries. Sources are user-supplied, not independently verified.
The editable negotiation draft uses the actual offer and user-selected target.

The skill planner accepts up to 100 postings with role, source and required_skills.
It compares exact confirmed skills and counts distinct posting URLs; at least two
postings must support a gap. Results are coverage estimates, not salary predictions.

Timed interviews offer three company/role-specific questions with server-enforced
two-minute deadlines. Text works without a microphone. Voice/video uses browser
MediaRecorder on HTTPS or localhost, with explicit consent and manual review.
Enter or edit a transcript for heuristic clarity and technical depth feedback.
No automated speech-to-text, external media processor or claim of expert scoring.
Recording without a transcript is allowed but not scored. Unsupported browsers
can use the text fallback. Recording size is limited to 8 MiB per answer. Recordings
are stored in the private account database, downloaded as attachments, and may be
deleted independently of transcripts. Deleting a transcript also deletes its scores.
Delete an entire interview to remove its context snapshot and all answers; clearing
the saved career context does not erase previous interviews. Operators must protect
the database and backups, including enforcing a suitable backup retention policy.
No raw salary or posting observation lists are persisted.

Run:
`python manage.py test webapp.tests_career webapp.tests_interview`
and install Playwright Chromium to run `python manage.py test webapp.browser_career`.
CI runs both suites and browser acceptance under the existing aggregate gate.
Fake-device testing does not replace validation on real candidate hardware.
