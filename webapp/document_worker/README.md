# Isolated document worker

This image compiles browser export jobs without giving the compiler network access or access to the Django data directory.

Build it from the repository root:

```bash
docker build -t genpact-job-ai-document-worker:local webapp/document_worker
```

Apply migrations and run the web process as usual, then run one or more queue workers:

```bash
python manage.py migrate
python manage.py run_document_worker
```

The Django worker launches one short-lived container per job with `--network=none`, a read-only root filesystem, dropped Linux capabilities, a PID limit, bounded memory/CPU, a 128 MiB temporary filesystem, and a host-side timeout. LuaLaTeX is used for CV PDFs and XeLaTeX for cover-letter PDFs. Python-docx generates editable Word files from the same snapshot. LibreOffice renders their private proof PDFs for review.

Useful environment variables:

- `JOBWEB_DOCUMENT_WORKER_IMAGE` — container image name.
- `JOBWEB_DOCUMENT_WORKER_RUNTIME` — container runtime executable, default `docker`.
- `JOBWEB_DOCUMENT_WORKER_TIMEOUT` — host-side timeout in seconds, default `90`.
- `JOBWEB_DOCUMENT_WORKER_MEMORY` — container memory bound, default `768m`.
- `JOBWEB_DOCUMENT_WORKER_CPUS` — CPU bound, default `1.0`.

Artifacts are written below `JOBWEB_DATA_DIR/exports`, not the Git checkout. Build directories live below a private staging directory and are deleted after every attempt. The web download endpoint always filters by the signed-in owner.

## Using an approved application file

The document studio offers visibly marked preview pages for both formats. Candidates submit the selected files and posting terms for review in the studio. The results show the coverage denominator, missing terms and any unsupported claims.

An authenticated consumer, including an extension attaching a candidate-selected document, should read `GET /documents/<job_id>/status/` using the candidate's session. Once `release_approved` is true, use the returned `download_url` to obtain the approved PDF or editable DOCX. The server checks the current profile revision and the exact file digest on final download. A locked or changed file returns HTTP 403. Another account's job returns HTTP 404. After an edit, select the newly reviewed file. The `preview_url` always returns a marked review copy.


## Retention and recruiter-review evidence

Generated files are private candidate artifacts and should not be retained forever. The job row is kept for audit, while the file itself can be removed after a retention window:

```bash
python manage.py cleanup_document_exports --older-than-days 30
```

Use `--dry-run` first when changing retention policy.

The gallery never labels a layout as **Recruiter-tested** merely because it exists. An operator must first record non-empty reviewer feedback and explicitly approve it:

```bash
python manage.py record_template_review \
  --template classic \
  --reviewer "Recruiter review panel" \
  --feedback "Single-column layout scanned cleanly; contact details and section order were easy to find." \
  --approved
```

Without an approved feedback record, the browser shows **Recruiter review pending**. This prevents unverified marketing claims about a layout.
