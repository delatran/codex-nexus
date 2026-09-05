---
name: "evidence-bound-documents"
description: "Create or edit reports, proposals, resumes, and PDFs while preserving identity, templates, citations, rendering, and evidence-bounded claims."
metadata:
  short-description: "Preserve document identity, claims, and layout"
---

# Evidence-Bound Documents

Use this skill when a document, report, proposal, thesis, resume, or PDF must
preserve a named person/project identity, an existing template, citation order,
rendered layout, or evidence-bounded claims.

For ordinary prose with no artifact or identity risk, work directly. Use the
installed `documents`, `pdf`, or `spreadsheets` workflow when its format-specific
handling is relevant. This skill adds source and artifact checks; it does not
require loading every format skill or creating an evidence packet.

## Identity and source contract

- Inspect candidate files to resolve the exact person, project, and requested
  artifact from current evidence. Similar names call for comparison before
  mutation; ask only if the remaining ambiguity could select the wrong target.
- Inspect the actual template or rubric when one governs the deliverable.
  Preserve unrelated content, styles, numbering, headers, and metadata. Follow
  the user's scope when they request a redesign or rebuild.
- Keep a recoverable source before overwriting an existing artifact, using
  version history or an exact copy. Prefer a local edit when it satisfies the
  request. Record a hash when it helps prove source identity or final promotion.
- Keep material factual claims within the supplied or verified evidence.
  Resolve a claim gap without freezing unrelated edits or inventing facts.

## Workflow

1. Establish the requested artifact, relevant identity, and destination. Infer
   routine output naming from the task and existing files.
2. Apply the edit with the appropriate document workflow. When a narrow OOXML
   edit best preserves an existing DOCX, change only the relevant nodes and
   re-open the result; preserve unrelated package parts.
3. Follow the required citation style. When first-appearance numbering applies
   and the edit changes citation order, build a map across all relevant document
   parts, then update citations and bibliography together. Preserve unaffected
   numbering for a local edit that does not change the citation sequence.
4. For formatted artifacts, render the final saved version and inspect the
   affected layout. Check changed pages, pagination effects, and identity fields;
   inspect the whole document for a rebuild or global layout change.
5. Verify that the delivered path contains the inspected version. After a
   repair or promotion, repeat the affected checks. State any missing render or
   evidence explicitly instead of claiming full verification.

## Evidence-bounded claims

For a resume or portfolio, ground material claims in supplied career facts,
project paths, commits, experiment outputs, publications, or other evidence.
Use a claim map when the review is substantial or a claim is disputed. Preserve
the distinction between development and production, prototype and deployment,
coursework and employment, and observed results and planned work. Do not infer
years of experience, audited savings, production systems, or tools from a job
description alone.

For academic work, inspect the actual template or rubric before claiming
compliance. Follow the user's requested language for the deliverable.

## Concrete examples

- Compare project/course evidence when two files share a student name; opening
  candidate files to identify the target is useful preparation.
- In a first-appearance citation style, a reference first appearing in a table
  participates in that ordering; a prose-only map can miss it.
- A DOCX that passes text extraction but clips a table in the rendered PDF is
  not ready for delivery.

## Supporting reference

Read the relevant sections of [document QA guidance](references/document-qa.md)
for identity conflicts, OOXML edits, citation repair, or artifact promotion.

## Output

Return the artifact path, checks actually run, and material gaps. Include a
source/claim mapping when requested or needed to assess the result. Keep the
delivery proportional to the edit.
