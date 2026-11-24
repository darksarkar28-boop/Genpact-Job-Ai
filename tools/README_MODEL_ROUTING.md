# Workload routing and public research

The web import remains local by default. Selecting optional AI assistance on the upload form allows text from PDF/DOCX documents to be sent to an operator-configured external provider. Local extraction stays authoritative and AI additions must be literal phrases in the document; the candidate reviews before saving. LinkedIn ZIP remains deterministic.

## Configuration

Set JOB_AI_MODEL_CONFIG to a JSON object in the web service environment. Replace the illustrative identifiers and rates with the actual provider model IDs and verified prices. Set the named credential environment variable separately; never commit credentials. Example shape:

```json
{
  "providers": {
    "primary": {
      "endpoint": "https://example.com/v1/chat/completions",
      "credential_env": "JOB_AI_PRIMARY_KEY"
    }
  },
  "fast": [
    {"provider": "primary", "name": "fast-model", "input_rate": 1, "output_rate": 2, "timeout": 10, "max_output": 2048}
  ],
  "frontier": [
    {"provider": "primary", "name": "frontier-model", "input_rate": 5, "output_rate": 10, "timeout": 10, "max_output": 4096}
  ],
  "budgets_usd": {"extract": 0.25, "deduplicate": 0.1, "rank": 0.1, "draft": 1, "review": 1}
}
```

Rates are USD per million tokens. Route lists are ordered, same-tier fallbacks; temporary failures consume a conservative reservation. Each call uses a budget and an approximate conservative input token bound. This is not a monthly spending cap or a guarantee of provider billing. The HTTPS transport disables redirects, passes a network timeout and output limit, and checks returned usage. Web imports additionally run inside a time-limited subprocess. Logs contain route labels, status, token counts and estimated cost, not prompts or candidate identities.

The shared Python interface is tools.model_service.route_text(workload, prompt). Workloads are extract, deduplicate, rank, draft and review. Deterministic deduplication should remain deterministic. Claude Code's existing posting-scorer and application-reviewer agents select haiku and opus respectively; their runtime settings and billing are separate from the Python transport. The authenticated /workbench/ page runs the Python budget router for pasted posting batches, duplicate suggestions, and draft/review. The CLI /rank and /apply specifications support the same routed workflows when configured and consented, retaining the existing retrieval, grounding and export checks. Unconfigured installations retain their existing CLI runtime.

## Shared public research

Use an authoritative registry namespace and verified company identifier, never a company display name. For example, IN-MCA:REGISTRY-ID is an illustrative key. An operator, not a candidate, reviews the public sources and supplies a JSON array of URL/summary objects:

```sh
python -m tools.refresh_public_research IN-MCA:REGISTRY-ID < reviewed-public-sources.json
```

Run under the web service account and the same JOBWEB_DATA_DIR. The cache is in public_research.sqlite3, outside candidate profile storage. Fresh reads avoid duplicate refresh, expiry defaults to 30 days, and SQLite serializes refreshes. An authenticated GET /research/company/?employer_id=IN-MCA:REGISTRY-ID returns fresh public research or 404. Candidates have no write endpoint. Keep private candidate details, application-specific material and interview notes out of the ingestion document; review its public provenance before publishing. Cached statements always require independent verification before use in final materials. The workbench application action automatically reads fresh leads for a verified employer ID and displays them separately from draft text; /apply and /interview also support shared lookup. Never treat cache summaries as verified claims. Existing workspace-local cache is retained as a fallback.

## Routed CLI input

Run python -m tools.routed_workflows ACTION with JSON on stdin. Actions: extract (text), rank (profile and jobs), deduplicate (jobs), apply (profile, job, optional employer_id), research (employer_id only). All model actions require the explicit --consent flag. Each job contains key and text, not a URL. Use a confirmed candidate profile, and obtain permission before external processing. Results are drafts and suggestions, never automatic submissions. The rank result uses the existing weighted scoring and gate contract. Only operator-approved public ingestion writes the shared cache.

## Automatic public research refresh

Optionally set JOB_AI_PUBLIC_RESEARCH_FEEDS to a JSON map from verified registry identifiers to operator-approved HTTPS JSON feed endpoints. Each endpoint must return an object with employer_id (matching the exact key) and sources (array of public URL/summary objects). Only configured endpoints may be called, redirects are refused, responses are bounded, and no candidate data is transmitted. A fresh cache hit makes no feed call. A miss or expired row refreshes under the cache transaction; failed refresh returns no fresh research rather than stale information. Operators must ensure the feed is authorized and public-only. The manual ingestion command remains available. Cached leads are not final evidence.

## Tests

Run python -m unittest tests.test_model_router_research_cache tests.test_model_service -v and python manage.py test webapp. Test doubles do not contact real providers. Live provider smoke tests require operator-supplied credentials and reviewed configuration.
