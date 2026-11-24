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
- `JOBWEB_DOCUMENT_WORKER_TIMEOUT` — host-side timeout in seconds, default `60`.
- `JOBWEB_DOCUMENT_WORKER_MEMORY` — container memory bound, default `768m`.
- `JOBWEB_DOCUMENT_WORKER_CPUS` — CPU bound, default `1.0`.

Artifacts are written below `JOBWEB_DATA_DIR/exports`, not the Git checkout. Build directories live below a private staging directory and are deleted after every attempt. The web download endpoint always filters by the signed-in owner.
