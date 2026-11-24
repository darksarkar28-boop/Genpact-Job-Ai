# ATS keyword coverage (issue #6, initial slice)

The existing `tools/verify_pdf.py` checks the extracted CV text layer. This tool
adds a deterministic **report** against the required and preferred posting terms
already identified in `/apply` Step 1. It does not pretend to prove qualifications,
validate claims, or enforce authorization for exporting documents.

1. Extract the final CV text with `tools/verify_pdf.py --dump-text`, retaining
   `--ascii-dates`. Use the same text-layer output that an ATS actually reads.
2. Save the exact terms from the posting to a **temporary** JSON file:

   ```json
   {
     "keywords": [
       {"term": "Python", "priority": "required"},
       {"term": "Continuous integration", "priority": "required", "aliases": ["CI"]},
       {"term": "Docker", "priority": "preferred"}
     ]
   }
   ```
3. Run:

   ```bash
   python3 tools/ats_coverage.py --cv-text cv/main_company_role.txt \
     --terms cv/main_company_role-terms.json \
     --profile-text .claude/skills/job-application-assistant/01-candidate-profile.md
   ```

   Add `--json` for a machine-readable report. `--profile-text` is optional
   and repeatable; its content is never printed or written by this tool.

The output reports `covered` (literal case-insensitive whole term),
`synonym-only` (an **explicitly supplied** alias is present, not a literal
ATS hit), `missing (have it)` (a term appears in provided profile text but
not in the CV), or `missing (unverified)` (not seen in either).

Literal coverage = **exact matched terms / total terms**, separately for
required and preferred terms. A zero denominator appears as `null` in JSON
and `N/A` in the text report. Aliases do not count as exact hits. Punctuation and typographic
characters are folded using the existing PDF-verifier comparison function.

**Important:** A profile-text hit is only a lead; a reviewer must verify the
underlying claim against confirmed evidence before suggesting a CV edit.
An unverified missing term is not proof that a person lacks the skill. Do not
stuff missing terms or turn a synonym into a false claim. Final application
PDFs go through the authenticated review described below.

Remove the temporary extracted text and terms JSON after the verification step.


## Authenticated final PDF pre-export gate

Start the Django web app documented in the root README and sign in with a saved,
confirmed master profile. From the profile workspace, choose **Review an
application** (`/applications/review/`).

- Upload the *final compiled* CV PDF and, when applicable, the final cover
  letter PDF. Each is limited to 8 MiB and must contain extractable text.
- Specify the target employer and at least two CV headings in the order they
  appear visually. The server checks the headings' order in PDF extraction.
  Human visual inspection remains necessary for complex/multi-column layouts.
- Paste the posting's exact required/preferred terms as the JSON above.
  Missing terms are shown separately from fabricated claims; they are not
  by themselves a blocker.
- For candidate claims beyond the automated high-risk metrics, employment,
  dates, and degree checks, supply an array of exact
  `{"claim":"draft text","evidence":"confirmed profile excerpt"}` records.
  The server requires the evidence excerpt to be present in the signed-in
  user's saved profile.
- For any target-company factual statements, supply direct public HTTPS
  citations with `{"claim":"exact published statement","url":"https://..."}`.
  The server independently re-fetches each source, blocks redirects and
  private IP targets, and requires the claim text to match the fetched page.
  Inaccessible, changed, or unsupported citations block release.

Choose **Check document** to review findings. If it passes, select the
**same PDFs again** and choose **Recheck and export ZIP**. Approval expires
after 15 minutes and is bound to the logged-in account, the profile's
revision, all submitted rule inputs, and the PDF bytes. The server runs the
checks **again** on the final submitted files; editing anything requires a
fresh review.

Only this export endpoint supplies the `verified_application.zip` archive
containing the checked PDFs and a JSON verification receipt. No browser/API
endpoint bypasses these checks. Upload bytes are not retained between
requests. Stale profile versions and attempts from other accounts are
rejected. Client-side review badges are never accepted as authorization.

**Scope and evidence:** This is conservative deterministic verification, not
an omniscient NLP fact-checker. It detects unsupported high-risk terms,
invalid claim/source declarations, unreadable PDFs and heading-order
mismatches. It cannot infer every possible factual statement or guarantee
visual line order across arbitrary PDF layouts; a human reviewer must still
check the final rendered pages. Cloud PDF/DOCX compilation and extension
release are separate workstreams; they must call this backend gate before
being enabled.

Local focused test commands:

```bash
python -m unittest discover -s tests -t .
python manage.py test webapp
```
