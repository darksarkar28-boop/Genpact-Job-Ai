# Job feed provider foundation (Issue #4)

This first slice reads employer-configured public Greenhouse, Lever and Ashby
job boards. It is not a global search engine: operators must configure board
slugs and employer names, including Indian employers where applicable.

| Source | Access method | Current implementation |
| --- | --- | --- |
| Greenhouse | Public Job Board API | Configured board, jobs endpoint |
| Lever | Public Postings API | Configured board, bounded offset pagination |
| Ashby | Public Job Posting API | Configured board, listed jobs |
| LinkedIn | Authorized partner integration | Not connected |
| Indeed | Authorized partner integration | Not connected |
| Naukri | Authorized integration | Not connected |
| Hirist | Authorized integration | Not connected |
| Instahyre | Authorized integration | Not connected |
| Internshala | Authorized integration | Not connected |

Never scrape restricted endpoints or claim a provider is live merely because a
fixture passes. No credentialed provider integration is implemented yet.

`tools.job_feeds.collect` returns normalized records and per-board results
(`ok`, `partial`, `unavailable`). Missing source dates remain missing.
Expired jobs with explicit expiry are excluded. Public-board removal tracking,
persistent storage, ranking, account preferences, scheduler and delivery remain
to be implemented. The legacy job key is retained for interoperability but is
not a unique identifier because several openings can have the same job title.

Run fixture tests with:

```sh
python -m unittest tests.test_job_feeds -v
```

The existing GitHub Actions unittest discovery includes this module. Real
provider access and the web end-to-end digest are not verified by these tests.
