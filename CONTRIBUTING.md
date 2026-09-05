# Contributing to Codex Nexus

Codex Nexus is maintained for Codex App and Codex CLI. Read `AGENTS.md` and
the nearest owning source before editing. Keep repository instructions, code,
comments, configuration, and documentation in English. Never put credentials,
private machine paths, or user-specific configuration values in distributable
files.

## Make a focused change

Every change should have a concrete consumer and an observable verifier.

- A skill needs a discriminating trigger, a short entrypoint, and a task-level
  check. Add a reference only when it changes a decision or procedure.
- A configuration change must update the runtime contract and negative tests
  when it changes accepted keys, types, or behavior. Keep the owner's explicit
  feature selection; do not enable new experiments or restore deleted choices
  merely because a client catalog advertises them.
- A setup or installation change must cover preservation, conflicts, failure,
  and recovery at the boundary it changes.
- A verification change must explain what it establishes and what remains
  untested. Do not make an oracle weaker to obtain a pass.
- Documentation should describe current ownership and usage. Do not add a
  migration diary, generic model advice, or claims that were not measured.

Prefer the smallest coherent design that protects a real invariant. Keep
unrelated cleanup out of a functional change. Do not add a wrapper around a
native Codex capability unless the wrapper provides a concrete contract that
the host does not provide.

Edit skill source under `skills/`. Installation exposes that source through
`~/.agents/skills`; do not create synchronized copies under `~/.codex/skills`
or replace the personal, system, and plugin skills maintained there or by Codex.

## Local workflow

From the repository root:

```sh
python -B -m nexus inventory --write
python -B -m nexus verify
python -B setup.py --health
git diff --check
```

Add `--runtime` to the verification command when changing `.codex/config.toml`,
runtime discovery, or feature interpretation. The integrated command runs the
full suite once. Export a source package only when needed, to an external path:

```sh
python -B -m nexus package --output ../codex-nexus-source.zip
```

The source manifest is generated, not hand-edited. A package must be created
outside the repository and must not already exist. Do not report remote CI,
account entitlement, server behavior, or live model quality from a local run.

## Review before delivery

Read the complete diff after the checks pass. Confirm that the result:

- preserves unrelated owner changes and user configuration;
- keeps authority, trust boundaries, and target paths explicit;
- has tests for consequential success and failure paths;
- contains no unsupported model names, secrets, private absolute paths, or
  em dashes;
- links only to files that exist in the current source tree; and
- reports skipped, unavailable, and unmeasured behavior honestly.

If a check is unavailable, state the missing evidence and the next useful
verifier. A passing static scan proves source shape only. A passing runtime
probe proves local Codex advertisements only. Neither is a benchmark of model
quality.

See [the architecture](docs/ARCHITECTURE.md), [runtime contract](docs/ASTRA.md),
[verification boundaries](docs/VERIFICATION.md), [research record](docs/RESEARCH.md),
and [evaluation protocol](docs/EVALUATION.md) for the owning contracts.
