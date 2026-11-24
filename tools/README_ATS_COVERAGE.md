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
stuff missing terms or turn a synonym into a false claim. The full issue's
mandatory pre-export factual-evidence gate remains to be implemented.

Remove the temporary extracted text and terms JSON after the verification step.
