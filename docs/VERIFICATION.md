# Verification boundaries

Codex Nexus reports the behavior its local checks actually observe. It does
not turn configuration, effort, fluency, or a generated receipt into proof of
model quality.

## Routine checks

From the repository root:

```sh
python -B -m nexus inventory --write
python -B -m nexus verify
python -B setup.py --dry-run
python -B setup.py --health
```

`verify` runs four independent gates:

1. Source, skill metadata, links, configuration shape, English markers,
   credential patterns, and redirect-sensitive paths are checked.
2. Every inventoried source file is compared by path, byte count, and SHA-256
   with `SOURCE_MANIFEST.json`.
3. The repository's unit and regression tests run with their actual skips and
   failures visible.
4. Two temporary source packages are built, compared for reproducibility, and
   read back member by member against the source.

`--runtime` adds bounded local probes for the selected Codex executable. It
checks the local version output, bundled model catalog, feature list, and the
configuration contract. It never generates a model response.
The source's declared feature values define the check. Additional client
features remain observations; the verifier does not require every available
experiment to be selected or enabled.

Add `--runtime` when the change affects Codex configuration or compatibility.
For a durable receipt, add `--output` with a new path outside the source tree.
The distributable source does not need a receipt directory. Keep private
reports outside the repository when the result must not be packaged.

## Installation checks

`setup.py --dry-run` shows the link and configuration plan without mutation.
The default `setup.py` applies the plan, preserves unrelated user settings,
backs up replaced configuration bytes, and performs a final health check.
`setup.py --health` checks the managed links and source-owned configuration.
The skill link is `~/.agents/skills`; personal and system skills under
`~/.codex/skills` remain outside the managed catalog. Health confirms filesystem
state, while a new task's available skill list confirms host discovery.

If a link operation or final health check fails, setup attempts to restore the
captured configuration and links. Recovery is conservative: it refuses to
overwrite a path that changed after capture and reports incomplete recovery.
Existing custom link targets are never silently replaced. A replacement needs
an explicit snapshot and an external backup root.

Windows instruction hardlinks are refreshed only when the private ownership
receipt matches the installed file's content and identity. Regression tests
cover source replacement, user edits, invalid receipts, and recovery failures.

## What each gate proves

| Observation | Supports | Does not support |
| --- | --- | --- |
| Static source pass | Current source shape and explicit marker checks | Semantic English, authorship, relevance, or truth |
| Manifest pass | The source matches its generated inventory | A correct design or a clean remote checkout |
| Test pass | The exercised local contracts passed | Untested platforms, live services, or untested inputs |
| Runtime pass | A local Codex client advertises the requested schema and capabilities | Account access, effective precedence, server behavior, or model quality |
| Setup health pass | Managed links and owned settings are healthy now | Future host rewrites or user changes |
| Reproducible package pass | The current source packages deterministically | Deployment, publication, or runtime behavior |

The language scan rejects non-English alphabet markers, em dashes, likely
credentials, and private absolute home paths. Model selection is validated in
the configuration; mentions in comparisons, tests, or documentation do not
select a model. The scan cannot judge writing quality or identify an author.
The skill scan checks
entrypoints and local references; it cannot prove that a running task chose a
skill or that the skill improved the result.

## Reporting limits

Report commands that ran, their observed result, skipped or unavailable checks,
and the boundary that remains untested. Do not infer a current production state
from a historical receipt. Do not infer quality from a larger context window,
higher effort, more workers, a successful static scan, or a self-review.

For model or workflow comparisons, freeze the task population, arms, resources,
oracles, and stopping rule before observing outcomes. Use the exact holdout
protocol in [EVALUATION.md](EVALUATION.md). For the design evidence and limits
behind the current checks, read [RESEARCH.md](RESEARCH.md).
