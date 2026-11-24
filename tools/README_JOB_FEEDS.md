# Job feeds and morning digest (Issue #4)

## Source access

| Source | Candidate-side ingestion path |
| --- | --- |
| Greenhouse | Public Job Board API, configured employer board |
| Lever | Public Postings API, configured employer board, offset pagination |
| Ashby | Public Job Posting API, configured employer board |
| LinkedIn | Licensed read-only feed/relay, operator approval required |
| Indeed | Licensed read-only feed/relay, operator approval required |
| Naukri | Licensed read-only feed/relay, operator approval required |
| Hirist | Licensed read-only feed/relay, operator approval required |
| Instahyre | Licensed read-only feed/relay, operator approval required |
| Internshala | Licensed read-only feed/relay, operator approval required |

The latter six integrations consume the explicitly specified v1 normalized
relay contract. They are not native vendor API clients and no live vendor
agreement or relay is bundled. The published LinkedIn Job Posting and Indeed
Job Sync APIs are primarily for publishing/managing employer postings, not
unrestricted candidate search. Do not use them as an assumed public search API.
Obtain lawful feed access and configure an operator-controlled adapter or
licensed aggregator that implements the relay contract before enabling a source.
Missing credentials/configuration are shown as unavailable, not successful.
No scraping, credential guessing, or login bypass is implemented.

Public API references:
- https://developers.greenhouse.io/job-board.html
- https://github.com/lever/postings-api
- https://developers.ashbyhq.com/docs/public-job-posting-api

Partner publishing API references (not job search endpoints):
- https://learn.microsoft.com/en-us/linkedin/talent/job-postings/api/overview
- https://docs.indeed.com/job-sync-api/

Set JOBFEEDS_CONFIG in the server's private environment to JSON like:

    {"boards":[{"provider":"greenhouse","slug":"your-board","employer":"Your Company","region":"india"}],
     "partners":[{"provider":"naukri","endpoint":"https://your-authorized-relay.example/jobs","token":"private-token"}]}

Examples are placeholders, not actual provider credentials or URLs.
A relay accepts GET with Authorization: Bearer <token>, with optional cursor
query parameter; it returns JSON:

    {"version":1,"provider":"naukri","jobs":[
      {"source_id":"stable-id","employer":"Employer","title":"Engineer",
       "location":"Bengaluru","region":"india","application_url":"https://employer.example/job/1",
       "posted_at":"2026-11-24T06:00:00Z","updated_at":null,"expires_at":null,
       "description":"Python and Django"}],"next_cursor":null}

Use global or india region, ISO timestamps (or null), and at most 500 rows per
page. Only return a complete snapshot if all active jobs for that feed are
included. No redirect or arbitrary next URL is accepted. An opaque cursor is
encoded against the fixed operator endpoint. The server retries bounded
429/5xx responses. Invalid or incomplete snapshots retain previous records;
complete snapshots deactivate disappeared rows. Explicit expiry and a seven-day
freshness limit exclude stale records. Source identity and canonical application
URL deduplicate records; separate vacancies with the same title are preserved.
Cross-platform records with different application URLs cannot reliably be
identified as identical; no speculative merging is performed.

## Web and scheduling

Run python manage.py migrate and visit /jobs/ after signing in. Source status,
market, role, location, IANA time zone, morning hour and email digest controls
are available. Rank uses exact confirmed-profile keyword coverage, not a
probability or model-generated assessment. Unconfirmed profiles have no score.

Configure JOBFEEDS_SMTP_HOST, JOBFEEDS_SMTP_PORT (587), JOBFEEDS_SMTP_USER,
JOBFEEDS_SMTP_PASSWORD, JOBFEEDS_SMTP_TLS (1), JOBFEEDS_FROM_EMAIL in the private
environment. Save and verify the email from the account, then separately opt in.
Email is the supported delivery channel. WhatsApp and Telegram are not required
by the issue's alternative-channel wording and are not included.

Run these commands via the deployment's cron or scheduler (for example, refresh
hourly and run digest every five minutes). They are deliberately not executed
in HTTP requests or GitHub Actions against real providers.

    python manage.py refresh_job_feeds
    python manage.py send_job_digests

Use the same persistent database and environment as the web app. Run only
one refresh scheduler per deployment. Digest attempts have a unique account
and local-date constraint. After the chosen morning hour and before noon, a
due account receives at most one attempt for that day. SMTP failure or process
crash is recorded as failed/claimed rather than automatically retried because
remote acceptance may have already occurred. Inspect the attempt and reconcile
with the provider before manual recovery. Opt-out stops future delivery; it
cannot recall a message already being sent. No automatic delivery is enabled
until configured and verified opt-in is saved.

## Verification

    python -m unittest tests.test_job_feeds tests.test_partner_job_feeds -v
    python manage.py test jobfeeds --verbosity 2

Issue #4 has a dedicated GitHub Actions acceptance matrix entry and contributes
to CI gate. Tests use fake relay tokens, public fixtures and in-memory email.
Production source licenses, native relay integration, SMTP credentials, external
scheduler setup and live provider delivery require deployment validation.
