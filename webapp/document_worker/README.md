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

The Django worker launches one short-lived container per job with `--network=none`, a read-only root filesystem, dropped Linux capabilities, a PID limit, bounded memory/CPU, a 64 MiB temporary filesystem, and a host-side timeout. LuaLaTeX is used for CV PDFs and XeLaTeX for cover-letter PDFs. DOCX exports are generated from the same confirmed content snapshot with Pandoc.

Useful environment variables:

- `JOBWEB_DOCUMENT_WORKER_IMAGE` — container image name.
- `JOBWEB_DOCUMENT_WORKER_RUNTIME` — container runtime executable, default `docker`.
- `JOBWEB_DOCUMENT_WORKER_TIMEOUT` — host-side timeout in seconds, default `90`.
- `JOBWEB_DOCUMENT_WORKER_MEMORY` — container memory bound, default `768m`.
- `JOBWEB_DOCUMENT_WORKER_CPUS` — CPU bound, default `1.0`.

Artifacts are written below `JOBWEB_DATA_DIR/exports`, not the Git checkout. Build directories live below a private staging directory and are deleted after every attempt. The web download endpoint always filters by the signed-in owner.


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
