# Document QA Reference

## Target identity

Use the identity fields relevant to the task, such as person, project, course,
employer, or template, to distinguish candidate sources before mutation.
Inspect current files to resolve conflicts. If the remaining conflict could
change the target, pause only the affected edit and ask a focused question;
continue useful inspection and independent authorized work. Preserve a
recoverable source before overwriting an existing artifact.

## Minimal document change

Use the installed `documents` workflow for DOCX handling. When a narrow OOXML
edit best preserves the requested change, change only the relevant paragraph,
run, table cell, relationship, or property;
preserve styles, section breaks, numbering, fields, images, and unrelated XML.
Re-open the result after saving and compare text/structure outside the target.

Do not add a new template, renderer, or conversion layer when the installed
document/PDF tooling already covers the operation.

## Citation-order map

First confirm the required citation style. For a repair or reorder governed by
first appearance, collect every relevant occurrence in document order,
including tables, captions, footnotes, and appendices as the style requires.
Build:

```text
first appearance -> assigned number -> source record -> bibliography entry
```

Check duplicate sources, missing bibliography entries, and citation order after
the repair. Resolve unreferenced entries according to the required style; do
not delete them automatically. A local edit that leaves the citation sequence
intact does not require renumbering the document. Text checks establish content
and ordering; rendering establishes visible placement.

## Render and inspect

1. Render the final saved artifact with the available format workflow.
2. Inspect changed pages and pages affected by reflow. A rebuild, global style
   change, or full visual audit needs whole-document inspection.
3. Check relevant identity fields, clipping, tables, fonts, captions, equations,
   headers/footers, and page breaks. Compare page count when pagination matters.
4. After a repair, render and inspect the affected result again. Reuse still
   valid checks for unchanged content.

If rendering is unavailable, finish the authorized structural/content work and
state which layout checks remain unrun. Do not label the artifact visually
verified or claim a required render gate passed.

For a spreadsheet citation or evidence matrix, use `spreadsheets` rather than
reimplementing table editing. Verify formulas, displayed values, source links,
and the final saved path before embedding it in a document.

## Resume and CV evidence

For a substantial evidence review, a claim table can keep wording traceable:

```text
claim | evidence path or citation | evidence type | wording limit | status
```

Keep wording within the evidence. A prototype call, local benchmark, or
coursework artifact cannot silently become a production claim.

## Final-path verifier

The final user-facing path must exist, be readable, and contain the inspected
artifact. When a candidate is promoted or several copies could be confused,
compare its SHA-256 with the inspected version after the last save/promotion.
If cleanup was requested, check its actual result and report retained files;
artifact editing alone does not require a separate cleanup audit.
