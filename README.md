<p align="center">
  <img src="assets/readme/hero.svg" alt="AI Job Search. Pip the courier bird carries an application along a flight path to its destination. The job search that runs on your machine." width="100%">
</p>

<p align="center">
  <a href="https://github.com/darksarkar28-boop/Genpact-Job-Ai/actions/workflows/ci.yml"><img src="https://github.com/darksarkar28-boop/Genpact-Job-Ai/actions/workflows/ci.yml/badge.svg?branch=master" alt="CI" height="20"></a>
  &nbsp;
  <a href="https://claude.com/claude-code"><img src="https://img.shields.io/badge/built_on-Claude_Code-2A9D8F?style=flat" alt="Built on Claude Code" height="20"></a>
  &nbsp;
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-2A9D8F?style=flat" alt="MIT license" height="20"></a>
</p>

<p align="center">
  <a href="https://trendshift.io/repositories/43622?utm_source=trendshift-badge&amp;utm_medium=badge&amp;utm_campaign=badge-trendshift-43622" target="_blank" rel="noopener noreferrer"><img src="https://trendshift.io/api/badge/trendshift/repositories/43622/daily" alt="MadsLorentzen%2Fai-job-search | Trendshift" width="250" height="55"/></a>
</p>

<p align="center">
  An AI-powered job application framework built on <a href="https://claude.com/claude-code">Claude Code</a>.<br>
  Fork it, fill in your profile, and let Claude evaluate job postings, tailor your CV,<br>
  write cover letters, and prepare you for interviews.
</p>

<p align="center">
  <a href="#web-profile-builder"><b>Web profile builder</b></a>
  &nbsp;&nbsp;&nbsp;
  <a href="#quick-start"><b>CLI quick start</b></a>
  &nbsp;&nbsp;&nbsp;
  <a href="#commands"><b>Commands</b></a>
  &nbsp;&nbsp;&nbsp;
  <a href="#how-apply-works"><b>How /apply works</b></a>
  &nbsp;&nbsp;&nbsp;
  <a href="#customization"><b>Customization</b></a>
  &nbsp;&nbsp;&nbsp;
  <a href="SETUP.md"><b>Setup guide</b></a>
</p>

> [!NOTE]
> This is an independent open-source project and is not affiliated with, endorsed by, sponsored by, or maintained by Anthropic. Anthropic and Claude Code are referenced only to describe the toolchain this workflow uses.
>
> This project has **no affiliated cryptocurrency, token, or paid sponsorship program**. Anything claiming otherwise is unauthorized and should be treated as a scam. The only ways to support the project are the Ko-fi link below and contributing on GitHub.

## Does it actually work?

I'm a geophysicist by training. When my position was cut in late 2025, I built this framework to run my own job search - the same `/scrape`, `/apply`, and `/interview` workflow in this repo, used weekly, on my own career. I was upfront about it with every employer I spoke to, and instead of counting against me, it usually sparked a genuine technical conversation.

<p align="center">
  <img src="assets/readme/letters.svg" alt="69 envelopes for 69 tailored applications. 20 of them light up as first interviews, and one is sealed as the signed contract." width="100%">
</p>

Sixty-nine tailored applications, twenty first interviews, and one signed contract later, I started as an AI engineer in June 2026. People kept asking whether this actually works. It got me hired. Now it's yours.

*The longer version, including the full application funnel, is on [LinkedIn](https://www.linkedin.com/in/mads-lorentzen/).*

<p align="center">
  <i>Did this save you a Sunday of cover-letter writing? Consider a coffee.<br>
  Did it land you the job? Maybe two.</i> ☕
</p>

<p align="center">
  <a href="https://ko-fi.com/madslorentzen">
    <img src="https://storage.ko-fi.com/cdn/kofi3.png?v=6" alt="Buy me a coffee at ko-fi.com" height="40">
  </a>
</p>

## What this is

A structured workflow that turns Claude Code into a full-stack job application assistant. Six commands carry you from an empty profile to a tracked result:

<p align="center">
  <img src="assets/readme/route.svg" alt="The workflow in order: /setup builds your profile, /scrape finds new postings, /rank shortlists the fits, /apply tailors a CV and cover letter, /interview preps every round, /outcome tracks each result." width="100%">
</p>

The core workflow (self-profiling, fit evaluation, and the drafter-reviewer application pipeline) is **language- and country-agnostic**. Job portal search skills live in [community-portals](https://github.com/ai-job-search-dev/community-portals), one folder per job board, owned by the people who use it: copy in the ones for your market, or build your own with `/add-portal`.

The framework encodes career guidance best practices, including structured evaluation criteria, forward-looking cover letter framing, and optional salary benchmarking.

## Web profile builder

Open the web app, create an account, and choose a résumé PDF, DOCX, LinkedIn profile PDF, or LinkedIn data-export ZIP. Review the extracted details and select **Save profile**. Returning users can sign in, edit their saved profile, or import another file to fill gaps. The browser flow needs no CLI, LaTeX, API key or model subscription.

The importer recognizes English résumé headings and English LinkedIn CSV headers. Export a text-based PDF or use DOCX for scanned documents. Check multi-column layouts and unusual headings in the review screen. Only confirmed fields are stored. Raw documents are discarded after parsing.

### Start the web app

From the repository root, in a Python 3.10+ virtual environment, run:

```sh
python -m pip install -r webapp/requirements.txt
python manage.py migrate
python manage.py runserver
```

Open <http://127.0.0.1:8000/>. The application stores accounts, sessions and profiles in SQLite under `~/.genpact-job-ai/`. Set `JOBWEB_DATA_DIR` before migration to use a different persistent directory. The CLI quick start below is independent.

For a single-host production deployment, mount that directory on a persistent volume and set `JOBWEB_PRODUCTION=1`, `JOBWEB_ALLOWED_HOSTS` to your host name, and `JOBWEB_SECRET_KEY` to a persistent random secret. Terminate HTTPS at a trusted proxy that limits request bodies to 9 MiB and rate-limits account creation and login. If it strips incoming forwarded-protocol headers and supplies its own, set `JOBWEB_TRUST_PROXY_TLS=1`. After migration, run `python manage.py collectstatic --noinput` and serve `gunicorn webapp.wsgi:application` behind the proxy. Development uses a temporary signing key, so set a persistent key to keep sessions through restarts.

Run the onboarding tests with `python manage.py test webapp`. To reproduce the timed desktop and mobile browser checks, install `playwright`, run `python -m playwright install chromium`, then run `python manage.py test webapp.browser_smoke`.

### Campus and bootcamp portal

An operator can provision a license for an existing account after migration.

```sh
python manage.py provision_campus --slug placement --name "Placement Office" --seats 100 --admin placement_admin
```

The admin opens **Campus**, creates cohorts and issues private invitations in batches of up to 50. Send each recipient their individual link. To enable the **Send invitations by email** option, set `CAMPUS_SMTP_HOST`, `CAMPUS_FROM_EMAIL`, and, when required, `CAMPUS_SMTP_USER` and `CAMPUS_SMTP_PASSWORD`. STARTTLS on port 587 is the default. Use `CAMPUS_SMTP_PORT` and `CAMPUS_SMTP_TLS` for your provider. Use a stable `JOBWEB_SECRET_KEY` across worker processes and restarts. Treat invitation URLs as credentials and redact the `/campus/invite/` path from access logs. A recipient signs in or creates an account and explicitly accepts. A reserved seat expires after seven days.

Active candidates and outstanding candidate invitations occupy licensed seats. Staff accounts do not consume seats. Deactivate a candidate or revoke an invitation to reassign capacity. Rerun the provisioning command to change the license. Its limit cannot go below existing allocations.

The dashboard counts distinct active candidates in the selected cohorts. Onboarding uses confirmed profile completion. Applications, interviews and offers use the candidate’s own reported counts, with reporting coverage shown separately. A candidate can share PDF or DOCX revisions up to 4 MiB and withdraw access. Moving a candidate to another cohort ends the existing share. Mentors can read and comment on the revisions explicitly shared with their assigned cohorts. Organization admins see the roster, aggregate progress and access history. Private document bytes and revision comments live in the persistent application database.

The **Issue 9 / licensed seats, cohort privacy and mentor review** GitHub Actions job runs the browser workflow, cross-organization checks and a concurrent last-seat test on a file-backed SQLite database.

## Quick start

> 🎥 **Prefer to see it in action first?** [The Next New Thing did a hands-on walkthrough](https://www.youtube.com/watch?v=HoVxjMNFYv4) of how the workflow is actually used, from setup to a finished application (recorded August 2026 - commands may have evolved since).

### What you need

| | |
|---|---|
| **[Claude Code](https://claude.com/claude-code)** | The CLI. No free tier: a Claude Pro/Max/Team subscription or Anthropic API credits (pay-per-token, usually cheaper for occasional use). |
| **Python 3.10+** | For the tools in `tools/`. |
| **[Bun](https://bun.sh)** | Runs the job portal CLI tools you copy in from community-portals. |
| **LaTeX** | A minimal install with `lualatex` and `xelatex`: [TinyTeX](https://yihui.org/tinytex/) on macOS/Linux or [Basic MiKTeX](https://miktex.org/) on Windows, plus the packages in [SETUP.md](SETUP.md#latex-for-compiling-cvs-and-cover-letters). A full TeX Live, MacTeX or MiKTeX also works. |
| **pypdf** *(optional)* | `pip install pypdf` for `/apply`'s ATS parseability check. |

<details>
<summary>Fine print: other agents, LaTeX engines, PDF text extraction</summary>

- **Using a different agent tool** (Codex, Antigravity, Gemini CLI)? Start at [`AGENTS.md`](AGENTS.md) - portal search skills you copy into `.agents/skills/` work there out of the box, and [community forks](https://github.com/ai-job-search-dev/ai-job-search/discussions/78) adapt the full workflow.
- **LaTeX engines.** The CV compiles with `lualatex` (pdflatex often fails on modern MiKTeX installs with `fontawesome5` font-expansion errors); the cover letter compiles with `xelatex` because `cover.cls` requires `fontspec`. The minimal TinyTeX/Basic MiKTeX install and its package list are in [SETUP.md](SETUP.md#latex-for-compiling-cvs-and-cover-letters).
- **PDF text extraction.** `pypdf` is BSD-licensed and needs no Poppler. Poppler `pdftotext` remains a fallback (macOS: `brew install poppler`, Debian/Ubuntu: `apt install poppler-utils`, Windows: `choco install poppler`). If both are missing, the check degrades to a visual keyword review.

</details>

### 1. Clone Genpact-Job-Ai

```bash
git clone --branch master https://github.com/darksarkar28-boop/Genpact-Job-Ai.git
cd Genpact-Job-Ai
```

> [!IMPORTANT]
> `/setup` writes personal data such as your contact details and employment history
> into tracked files. Keep profile commits local or push them to a private repository.
> See [SETUP.md section 8](SETUP.md#8-keeping-your-profile-private) before using a public remote.

### 2. Add job portals for your market (optional)

AI Job Search ships no job-board CLIs; without one, `/scrape` searches with WebSearch. Pick your boards in [community-portals](https://github.com/ai-job-search-dev/community-portals) (`portals/<country>/`, or `portals/global/` for LinkedIn and multi-market boards), and ask your agent, swapping in your board's folder:

> Copy https://github.com/ai-job-search-dev/community-portals/tree/main/portals/dk/jobindex-search into `.agents/skills/` and show me its code and what it does. Once I confirm, run `python3 tools/install_portal_clis.py` (Windows: `python tools\install_portal_clis.py`).

`/setup` prints this prompt for the boards you name.

<details>
<summary>What the installer does</summary>

It runs `bun install` in every portal CLI under `.agents/skills/*/cli/` - discovered, not listed, so a portal you copy from community-portals or add with `/add-portal` is included on a fresh clone. With no portal installed it does nothing. Every install runs with `--ignore-scripts`, and a CLI whose `package.json` declares an install-time lifecycle script, `trustedDependencies` or `workspaces`, or that has a `bunfig.toml` or `.npmrc` beside it, is skipped and reported rather than run. `--dry-run` shows what it would install.

For zero-dependency portals such as `linkedin-search` and `freehire-search` the install is optional: they run with plain `bun`; `bun install` only pulls TypeScript dev types.

</details>

### 3. Set up your profile

```bash
claude
# Then inside Claude Code:
/setup
```

`/setup` offers three paths: read your `documents/` folder if you have one populated (CV PDF, LinkedIn export, diplomas, reference letters, past applications), import a single CV pasted in chat, or walk through an interview. It auto-detects what you have and asks. Documents-folder mode is idempotent and safe to re-run as you add more material; see `documents/README.md` for the layout.

### 4. Search for jobs

```bash
/scrape
```

This searches multiple job portals for positions matching your profile, deduplicates results, and presents them sorted by fit. Pick a match to run `/apply` on it directly — or, when a scrape returns more jobs than you want to eyeball, run `/rank` to batch-score them all against the fit framework and get a ranked shortlist first.

### 5. Apply to a job

```bash
/apply https://boards.greenhouse.io/acme/jobs/1234567
```

If the URL can't be fetched (some job portals block automated access), you can paste the job description directly instead:

```bash
/apply <paste the full job description here>
```

This runs the full workflow: evaluate fit, draft CV + cover letter, review with a second agent, revise, and present the final output. The posting passes a deterministic integrity gate first (hidden text, invisible characters, instruction-shaped lines, hidden links), and its one-line verdict opens the evaluation; the agents that fetch and review read-only.

> [!WARNING]
> Postings are treated as untrusted input (the workflow follows no instructions embedded in them and fetches no links from their body), but agentic defenses are instruction-level, not a sandbox - on an unfamiliar job board, skim what was fetched and written before you hit send. Details in [SECURITY.md](SECURITY.md).

## Commands

`/setup`, `/scrape`, and `/apply` form the core workflow. Ten more commands extend it once your profile is in place.

| Command | What it does |
|---|---|
| **`/setup`** | Builds your profile from your documents, a pasted CV, or an interview |
| **`/scrape`** | Searches job portals for postings that match your profile |
| **`/apply`** | Evaluates fit, then drafts, reviews, and compiles a tailored CV and cover letter |
| `/rank` | Batch-scores a scrape into a ranked shortlist |
| `/interview` | Builds a stage-specific prep pack and runs a mock interview |
| `/outcome` | Records results, archives what you sent, and drafts follow-ups |
| `/gmail-sync` | Proposes application status updates from your Gmail |
| `/notion-sync` | Publishes a read-only view of your pipeline to Notion |
| `/html-report` | Generates an offline dashboard of your applications |
| `/expand` | Adds competencies found in your public profiles and course syllabi |
| `/upskill` | Maps your skill gaps and builds a learning plan |
| `/add-template` | Registers your own CV or cover letter template |
| `/add-portal` | Generates a search skill for a job board in your market |
| `/reset` | Wipes profile data or documents - see [Starting over](#starting-over) |

<details>
<summary><b>Full command reference</b></summary>

- **`/interview`** preps you for a scheduled interview on a tracked application. It builds a stage-specific prep pack from the application's archive (the exact posting, the CV and cover letter the interviewer actually read, feedback recorded from earlier rounds), researches the company and interviewers with a verify-before-use rule, maps likely questions to your STAR examples, and offers a mock interview following the roleplay protocol in `07-interview-prep.md`. Gaps get honest bridge answers, never invented experience.
- **`/outcome`** records what happened to an application - interview stages, offers, rejections, silence. It archives the submitted CV, cover letter, and posting text into `documents/applications/<company>_<role>/`, keeps `outcome.md` in the format `/setup` Path A parses, and updates the tracker. It also owns the stretch before there is an outcome to record: `/outcome followup` surfaces open applications that have gone quiet (default 10 days), drafts a short channel-appropriate follow-up in your writing style using only claims from the materials you already submitted (drafts only, never sends; at most twice per application), and offers a thank-you note in the same turn an interview stage is recorded. Once a few applications resolve, it points you back to `/setup` to calibrate the fit framework from what actually got interviews.
- **`/notion-sync`** publishes a one-way, read-only view of the pipeline into a Notion database via the official Notion MCP server (OAuth, no API keys) - one row per ranked job plus every tracked application, with a write-once briefing page per row. The repo files stay the system of record: nothing syncs back, and documents sync as filenames only. Complements `/html-report`: that is the deep offline dashboard you regenerate at your desk; this is the glanceable live view from anywhere Notion runs (desktop, web, phone).
- **`/gmail-sync`** reads your Gmail (via the Gmail connector) for status signals on your open applications - interview invites, assessment links, offers, rejections - and proposes them as a batch for you to approve before anything is written to the tracker or `outcome.md`, citing the source email on every proposed change. Offers stop short of proposing `hired`/`offer_declined` since that's your call; conflicting or unmatched signals get flagged for a manual `/outcome` pass instead of guessed.
- **`/rank`** bridges `/scrape` and `/apply`: it batch-scores all newly scraped postings against the fit framework (parallel agents fetch each posting and score the five evaluation dimensions) and returns a ranked shortlist with honest per-job strengths and gaps. Deal-breakers veto, deadlines get urgency flags, dead postings get marked expired. Pick a number and it hands off to the full `/apply` workflow.
- **`/expand`** enriches your profile by scanning public sources you've already linked in it (GitHub repos, portfolio site, Kaggle, Google Scholar) and looking up syllabi for named courses and certifications. Discovered competencies are added to your profile with a source tag. Useful right after `/setup` to surface skills that documents alone don't make explicit.
- **`/upskill`** analyzes the gap between your profile, your tracked job postings, and your ranked-but-untracked postings (`/rank`'s recorded gaps in `job_scraper/seen_jobs.json`) — or a single posting via `/upskill <URL>`. Produces a prioritized heatmap of skill gaps and a learning plan with web-searched study resources and time estimates. Useful for career planning between applications.
- **`/html-report`** generates a self-contained HTML dashboard from `job_search_tracker.csv` and the application archives — stat cards, status/sector/channel/funnel charts (inline SVG, no external dependencies), and a filterable applications table. Opens directly in a browser, fully offline. Re-run it any time after `/apply` or `/outcome` adds new entries.
- **`/add-template`** registers your own CV or cover letter template (LaTeX, Typst, or another toolchain) in place of the stock ones. It captures the template's instructions (source extension, compile command, fonts, style rules, page limit), runs a mandatory test compile, and wires the template into `/apply`. See [Custom templates](#custom-templates) below.
- **`/add-portal`** generates a job-portal search skill for a job board in your market. It investigates the portal (search URL pattern, result structure, access rules), scaffolds the CLI skill from the same structure as the community portals, and test-runs a live query before registering. See [Job search tools](#job-search-tools) below.

</details>

## How `/apply` works

<p align="center">
  <img src="assets/readme/apply.svg" alt="A job posting passes an integrity gate and fit evaluation, then loops through draft, review, revise, and compile plus inspect until the CV is exactly two pages, the cover letter one page, and the ATS text layer is checked." width="100%">
</p>

The `/apply` command runs a **drafter-reviewer workflow** with mandatory PDF compilation:

1. **Parse** the job posting (URL or text)
2. **Evaluate fit** against your profile (skills, experience, culture, location, career alignment)
3. **Draft** a tailored CV and cover letter in LaTeX
4. **Spawn a reviewer agent** that researches the company and critiques the drafts
5. **Revise** based on the reviewer's feedback
6. **Compile and inspect** both PDFs: lualatex for the CV, xelatex for the cover letter. Claude reads the rendered pages and iterates on the LaTeX until the CV is exactly 2 pages with no orphaned entry titles, and the cover letter is exactly 1 page with the signature visible and fonts consistent.
7. **ATS-check the CV**: extract the PDF's text layer (`pdftotext`, optional dependency) and verify it the way an ATS parser sees it — contact details present as literal text, no garbled glyphs, sane reading order — then score the posting's keyword coverage against the extraction. Keywords the profile genuinely supports get added; genuine gaps stay visible, never stuffed.
8. **Present** the final output with a verification checklist

All claims in the CV and cover letter are verified against your actual profile. The system never fabricates skills or experience.

<details>
<summary><b>What makes this workflow different</b></summary>

- **PDF verification loop.** Most LaTeX-resume templates produce "looks fine in the .tex" output that breaks in the PDF: job titles orphan to the next page, cover letters spill onto page 2, bullet fonts silently fall back to the body font. The `/apply` command compiles and visually inspects every PDF and applies targeted fixes (`\needspace`, `\enlargethispage`, font-matching wrappers for list items) until the layout is clean. This runs automatically on every application.
- **ATS verification on the PDF text layer.** An ATS reads the PDF's embedded text, not the rendered page — and LaTeX can silently produce PDFs whose text extracts as garbage (icon glyphs where the email should be, interleaved lines from multi-column layouts). `/apply` extracts the compiled CV's text layer with `pdftotext` and verifies contact details, reading order, and the posting's keyword coverage against what a parser actually sees. Honesty rule enforced: a keyword the profile doesn't support is acknowledged as a gap, never stuffed in.
- **Relevance-weighted CV cutting.** When a CV overflows 2 pages, the workflow does not cut mechanically from the "oldest" section. It scores each candidate line by (a) relevance to the target posting, (b) uniqueness in the document, and (c) whether the cover letter depends on it, and cuts the lowest-total-score line first. An older-role bullet that hits posting keywords survives ahead of a recent-role bullet that does not.
- **Drafter-reviewer separation.** The drafter writes; a second Claude agent, spawned with a fresh context, researches the company and critiques the drafts. The drafter then revises. This catches missed keywords, weak framing, and generic language that a single pass often leaves in.
- **Token-efficient reviewer dispatch.** The reviewer agent receives drafts inline rather than re-reading them, and the verification checklist runs once at the end of the workflow rather than being duplicated by both agents. Note: the new compile-and-inspect step in Step 5 spends some of those savings on PDF rendering and layout iteration — the workflow trades some end-to-end token cost for a real reduction in broken PDFs reaching the user.

</details>

## Customization

### Which files to edit manually

If you prefer editing files directly instead of using `/setup`:

| File | What to change |
|------|---------------|
| `CLAUDE.md` | Your full profile (name, education, experience, skills, goals) |
| `01-candidate-profile.md` | Structured version of your CV data |
| `02-behavioral-profile.md` | Your behavioral assessment or self-assessment |
| `04-job-evaluation.md` | Skill match areas, career goals, motivation filters |
| `05-cv-templates.md` | Profile statement templates for different role types |
| `07-interview-prep.md` | Your STAR examples from actual experience |
| `search-queries.md` | Job search queries for your skills and location |

### Updating your search queries

As your priorities evolve, you can reconfigure just the job search without re-running the full profile setup:

```
/setup --section search
```

This re-runs the search configuration interview: which roles to target, which skills to search for, which locations, and which portals. It also suggests role types you may not have considered based on your profile.

### Custom templates

The CV uses [moderncv](https://ctan.org/pkg/moderncv) (banking style). The cover letter uses a custom `cover.cls` with Lato/Raleway fonts. Both are LaTeX — the reference engine this repo ships and maintains.

To use your own template instead — LaTeX, [Typst](https://typst.app/), or any other toolchain that compiles to PDF from the command line — run:

```
/add-template
```

Point it at your source file (a `.tex` file plus any `.cls`/`.sty` files or bundled fonts; a `.typ` file plus any local packages; or an equivalent for another toolchain). The command interviews you for the template's instructions — source extension, compile command, fonts and where they live, style rules to preserve, hard page limit — stores everything under `templates/`, runs a mandatory test compile, and activates the template so `/apply` drafts and compiles from it. Templates are stored with `[PLACEHOLDER]` tokens instead of personal data, so they're safe to commit and share.

- `/add-template --list` shows registered templates
- `/add-template --use <name>` switches between them
- `/add-template --use default` reverts to the stock moderncv / cover.cls templates

If you prefer doing it by hand, the manual route still works: update the guidance in `05-cv-templates.md` and `06-cover-letter-templates.md`.

### Job search tools

Job portal CLIs live in [community-portals](https://github.com/ai-job-search-dev/community-portals), not here: Danish boards under `portals/dk/`, and country-agnostic `linkedin-search` (LinkedIn's public listings, personal use only) and `freehire-search` (the [freehire.me](https://freehire.me) tech aggregator) under `portals/global/`. Each portal is owned by its users through CODEOWNERS. Copy the ones you need into `.agents/skills/` (see [step 2](#2-add-job-portals-for-your-market-optional)). If your board has no portal yet, run:

```
/add-portal
```

Give it your local job board's URL. The command investigates the portal (search-URL pattern, result-page structure, robots.txt/access rules), scaffolds a CLI skill with the same structure, commands, and output contract as the community portals, and test-runs a live query before registering anything. Auth-walled portals are declined, and portals with restrictive terms get a prominent personal-use-only warning in the generated skill. The generated skill is market-specific, so contribute it to [community-portals](https://github.com/ai-job-search-dev/community-portals) rather than here; the generator itself is the universal part.

Maintaining a fork adapted to your market or language? Add it to the [Community forks & adaptations](https://github.com/ai-job-search-dev/ai-job-search/discussions/78) thread so others can find it.

### Extending the framework: portals, templates, criteria - and borrowing from other forks

Everything above adds up to an extension model, so here it is stated plainly. The framework has three extension points, and none of them require touching upstream:

1. **Portal skills** - the module system for job boards. Every `*-search` skill is a self-contained folder under `.agents/skills/` with the same contract (a `search`/`detail` CLI, `--format json|table|plain` output, an `enabled:` flag in its `SKILL.md`, its own tests). `/scrape` auto-discovers any installed skill that follows the contract - nothing to register, nothing to wire up. [community-portals](https://github.com/ai-job-search-dev/community-portals) collects them, and `/add-portal` generates new ones.
2. **Document templates** - `/add-template` registers any CV or cover-letter toolchain that compiles to PDF from the command line, LaTeX or otherwise.
3. **Evaluation criteria** - deal-breakers and preferences in your profile are free-form, and the evaluation rubric scores against whatever you put there. "Strong parental-leave terms", "minimum salary X per my union's scale", "no on-call" - each is one profile line, no code, and it carries real weight in `/rank` and `/apply` fit evaluations.

<details>
<summary>How the Language Gate handles language deal-breakers</summary>

Language is the one deal-breaker type with dedicated, structured handling: `/setup` captures every language you work in and your level (asked directly, or inferred from your CV/LinkedIn export) into a `Languages` table, and the Language Gate (`04-job-evaluation.md`) hard-rejects a posting that requires a language you haven't declared at all, while flagging - not auto-rejecting - one that asks for a higher level than you declared in a language you do work in, so a borderline case (a strict "fluent" bar against your own B1/B2, say) gets your judgment instead of a silent drop.

</details>

**Copying a portal skill in** is the intended way to get a job board: find it in [community-portals](https://github.com/ai-job-search-dev/community-portals) (or in a fork from the [community index](https://github.com/ai-job-search-dev/ai-job-search/discussions/78)) and copy the one folder into your own `.agents/skills/`. Before you run it:

- **Read the code.** All of it - these CLIs run on your machine against your career data, and its skill's own `allowed-tools` lets it run without asking while that skill runs; once you add its grant (see `/add-portal`'s pre-approval step), `/scrape`, `/apply` and `/rank` run it without asking too. Check that the only network calls go to the job board it claims to search, that `package.json` has no `dependencies` and no lifecycle scripts (`postinstall` etc.), and that nothing reads or writes outside its own folder.
- **Run its tests offline** (`bun test` in the skill's `cli/` directory) - a well-built skill's tests pass with no network access.
- Check the `enabled:` flag and the skill's own ToS notes.

The copy step is manual on purpose. An installer that fetched portals from other repos and ran them for you would skip the one check that matters: you, reading the code first. There isn't one, and that's a security decision rather than a missing feature.

Market-specific *data sources* (a national salary database, local award-rate tables) follow the same pattern as portals: they belong in a market fork, shared via [#78](https://github.com/ai-job-search-dev/ai-job-search/discussions/78), not upstream.

### Salary benchmarking

The salary tool works with any salary data you provide (union statistics, Glassdoor exports, personal research, etc.). See `tools/README_SALARY_TOOL.md` for the expected format and setup. If you don't have salary data, the salary step is simply skipped.

`tools/convert_salary_excel.py` turns an Excel export into that format. When the company column also holds the city, as in the IDA workbook's "Firmanavn og by" column (`Ørsted A/S,Fredericia`), the converter splits each cell on its last comma and strips both parts, so `salary_lookup.py --city` can filter. A company name that itself contains ", " stays intact.

### Starting over

To wipe your profile data and start fresh:

```
/reset profile    # clears skill files, preserves framework rules
/reset documents  # deletes files from documents/ folder
/reset all        # both
```

`/reset` shows exactly what will be deleted and requires you to type `RESET` to confirm. Nothing is deleted until you do.

### Staying up to date

Run `git pull --ff-only origin master` from a clean `master` checkout to update from Genpact-Job-Ai. The [tags](../../tags) include the original project's version history.

## Tips for better results

### Profile depth matters

The single biggest factor in output quality is how much detail you put into your profile. A thin profile produces generic applications; a detailed one enables genuinely tailored results.

- **Role descriptions:** Don't just list job titles. Describe what you actually did in each position: specific projects, tools used, responsibilities, and measurable achievements. The more material you provide, the more precisely the system can reframe your experience for different roles.
- **Skills in context:** Instead of listing "Python" or "project management," describe how and where you applied them. "Built ML pipelines for customer churn prediction in Python using scikit-learn" gives the system far more to work with than "Python, machine learning."
- **All onboarding paths work:** Whether you point `/setup` at your `documents/` folder, paste a single CV, or walk through the interview, the principle is the same: richer input produces sharper output.

### Career path discovery

The framework supports two distinct modes of job searching:

- **Explicit targeting:** You know which roles or sectors you want. The system helps refine and prioritize based on fit.
- **Latent opportunity discovery:** By analyzing your full history (not just job titles, but the actual work you did), the system can surface career paths you haven't considered. Transferable skills that map to unexpected industries, patterns in what you enjoyed or excelled at, or emerging roles that combine your domain expertise with new technology.

To get the most from this, invest time during `/setup` in describing not just your experience, but what energized you, what drained you, and what you'd want more of. This context directly shapes how the system evaluates fit and which roles it surfaces during `/scrape`.

## File structure

<details>
<summary>Show the repository layout</summary>

```
ai-job-search/
├── CLAUDE.md                          # Main candidate profile + workflow rules
├── .claude/
│   ├── commands/
│   │   ├── apply.md                   # /apply workflow (drafter-reviewer)
│   │   ├── setup.md                   # /setup onboarding (documents folder, CV import, or interview)
│   │   ├── expand.md                  # /expand competency enrichment from documents and online presence
│   │   ├── add-template.md            # /add-template register custom templates (LaTeX, Typst, ...)
│   │   ├── add-portal.md              # /add-portal generate a job-portal search skill for your market
│   │   ├── rank.md                    # /rank triage scraped jobs into a ranked shortlist
│   │   ├── outcome.md                 # /outcome record application results, archive materials
│   │   ├── gmail-sync.md              # /gmail-sync auto-detect application status from Gmail
│   │   ├── interview.md               # /interview stage-specific prep pack + mock interview
│   │   ├── html-report.md             # /html-report generate application tracker dashboard
│   │   ├── notion-sync.md             # /notion-sync one-way pipeline view in a Notion database
│   │   └── reset.md                   # /reset wipe profile data or documents folder
│   ├── skills/
│   │   ├── job-application-assistant/  # Core application skill
│   │   │   ├── SKILL.md               # Skill definition
│   │   │   ├── 01-candidate-profile.md # Your education, experience, skills
│   │   │   ├── 02-behavioral-profile.md# PI/DISC/personality assessment
│   │   │   ├── 03-writing-style.md    # Tone, structure, do's and don'ts
│   │   │   ├── 04-job-evaluation.md   # Scoring framework for job fit
│   │   │   ├── 05-cv-templates.md     # LaTeX CV structure + tailoring rules
│   │   │   ├── 06-cover-letter-templates.md # LaTeX cover letter templates
│   │   │   └── 07-interview-prep.md   # STAR examples + interview framework
│   │   ├── job-scraper/               # Job search orchestration
│   │   └── upskill/                   # /upskill skill gap analysis and learning plan
│   └── settings.json                  # Claude Code permissions (shared, scoped)
├── .agents/skills/                    # Job portal CLIs you copy in from community-portals (none shipped)
├── assets/                            # README artwork (Pip the mascot, animated SVG diagrams)
├── cv/
│   └── main_example.tex               # moderncv LaTeX template
├── cover_letters/
│   ├── cover.cls                      # Custom cover letter LaTeX class
│   ├── cover_example.tex              # Example cover letter (structural reference + CI smoke test)
│   └── OpenFonts/                     # Lato + Raleway fonts
├── templates/                         # Custom templates registered via /add-template
│   └── README.md                      # Folder layout instructions
├── documents/                         # Career source materials for /setup Path A and /expand
│   ├── README.md                      # Folder layout instructions
│   ├── cv/                            # Master CV (PDF or .tex)
│   ├── linkedin/                      # LinkedIn profile export (PDF)
│   ├── diplomas/                      # Degree certificates and transcripts
│   ├── references/                    # Reference letters
│   └── applications/                  # Past application records (<company>_<role>/)
├── .github/workflows/ci.yml           # CI: LaTeX smoke compiles, skill lint, CLI typechecks
├── .github/workflows/release.yml      # Manually publish a Genpact-Job-Ai release
├── salary_lookup.py                   # Salary benchmarking tool (BYO data)
├── tools/
│   ├── check_framework_version.py     # CI check: framework_version bumped when skill files change
│   ├── check_upstream_updates.py      # Preview which personalized files an upstream update touches
│   ├── convert_salary_excel.py        # Convert salary Excel to JSON
│   ├── fetch_page.py                  # GET-only browser-header retry for 403s (robots.txt enforced)
│   ├── install_portal_clis.py         # Install every discovered portal CLI (bun install)
│   ├── job_key.py                     # Canonical job key (company + title slug) shared by /scrape and /rank
│   ├── lint_skills.py                 # CI lint for skills, commands, settings.json
│   ├── page_decode.py                 # Decode a saved page by its declared charset (EUC-KR, Shift_JIS, ...)
│   ├── posting_guard.py               # Integrity gate: strip hidden text, flag injection-shaped lines
│   ├── rank_state.py                  # /rank state: candidates, deadline sweep, apply scores to seen_jobs.json
│   ├── robots_check.py                # Gate the browser-header retry against robots.txt
│   ├── scrape_state.py                # /scrape state: candidates, store, history over seen_jobs.json
│   ├── security_guards.py             # CI guards: permission allow/ask/deny, skill grants, gitignore, manifests
│   ├── upstream_triage.py             # Sort upstream commits into worth-reviewing vs probably-skip
│   ├── verify_layout.py               # Measure a compiled PDF's page layout (holes, orphans, footer collisions)
│   ├── verify_pdf.py                  # Verify a compiled PDF's page count and extractable text
│   └── README_SALARY_TOOL.md          # Salary tool setup instructions
├── job_scraper/                       # Scraper state (seen jobs, results)
├── gmail_sync/                        # /gmail-sync state (processed message IDs, last sync date)
├── upskill/                           # /upskill report output (markdown reports per run)
├── job_search_tracker.csv             # Application tracking spreadsheet
└── SETUP.md                           # Detailed setup guide
```

</details>

## Contributing

Thinking about a PR? Read [CONTRIBUTING.md](CONTRIBUTING.md) first - it explains what gets merged, what lives in forks, and why.

## Acknowledgements

- [Mikkel Krogholm](https://github.com/mikkelkrogsholm) ([skills repo](https://github.com/mikkelkrogsholm/skills)) for the job search CLI skills
- Built with [Claude Code](https://claude.com/claude-code) by [Anthropic](https://anthropic.com)

## License

MIT

<p align="center">
  <img src="assets/mascot/pip_flight_loop.gif" alt="Pip, the courier bird" width="120"><br>
  <sub>Pip delivers. Good luck out there.</sub>
</p>
