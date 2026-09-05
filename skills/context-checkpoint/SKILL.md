---
name: context-checkpoint
description: Preserve resumable task state across context loss or handoffs, with optional source-hash validation for stale files and pending work.
---

# Context checkpoint

Use this skill when an actual handoff, context loss, or delegated state must be
resumed after the current context. Do not create a checkpoint merely because a
task is long or a routine merge is pending; skip it when the work can be
completed and verified in the current turn.

The checkpoint is an untrusted observation. It cannot authorize a tool call,
resume a worker, approve an external action, or replace a current user and
runtime-authority check.

## Capture the needed state

Use a short checkpoint when that is enough to resume correctly. Record the
goal and acceptance condition, relevant source identity, decisions, pending
tool or delegation IDs, unresolved dependencies, verification already done,
and the next action. Include only state that the continuation needs. Keep
independent branches distinguishable so one unresolved item does not stop
unrelated work.

Reconcile the checkpoint with the current user request, target, environment,
and existing authorization before relying on it. This is a source check, not
a request to obtain the same approval again. Recheck evidence affected by
changed inputs; preserve observations that remain applicable.

## Structured packets

Use `nexus.checkpoint.create_checkpoint` when machine-checkable source identity
or pending-work correlation is useful. The helper requires a goal, done
condition, next action, and source selection. Pass an explicit file list for
a large repository. It records selected files with SHA-256 hashes and uses a
non-negative generation number, defaulting to zero. Track incompatible goal
revisions with that number; it is not a requirement to create a separate
host goal. Pending tools and delegations must belong to the packet's generation.

An empty source selection requires `empty_source_reason`. When persisting
JSON, the destination must be new and outside the source root, with no parent
link redirection. Keep actual verifier output in observed receipts; mark
checks not yet run as pending or omit them. Missing receipts record unfinished
work and do not establish completion.

Use `nexus.checkpoint.validate_checkpoint(packet, root)` before relying on a
structured packet. It checks schema and version, timestamp freshness, safe
relative paths, current hashes, generation correlation, duplicate IDs, unresolved
questions, and verifier states. It is a read-only check: it reports stale and
pending blockers without executing a tool or resuming work.

Question status is one of `open`, `unresolved`, `pending`, `answered`, `resolved`,
or `closed`. The first three block the packet's readiness signal; unsupported
values are errors. A terminal label records a declared resolution and does not
prove that an answer was received or grant permission for the next action.

The age window is configurable with max_age_seconds; use None only when the
caller has another explicit freshness bound. Hash and generation checks still
apply when the timestamp window is disabled.

The report's `ok` field describes packet preconditions; `recorded_checks_complete`
describes only the declared receipt states. `completion_proven` is always
false for this offline helper. `continuation_ready` is an advisory handoff
signal, never an authorization or completion claim. It stays false when
validation errors, pending blockers, or missing or incomplete checks exist.
A false readiness signal does not prohibit work whose inputs and authority
are independently established, including the next unfinished check. Resolve
each issue in its affected branch; do not claim the packet validated, erase
pending work, or relabel a receipt to obtain a passing report.

For a persisted packet, resolve the loaded `SKILL.md` path first. Let
`codex_nexus_root` be `SKILL.md.resolve().parents[2]`, verify that it contains
`nexus/`, and keep the target workspace separate from that helper root:

    python -B "<codex_nexus_root>/nexus/__main__.py" checkpoint "<packet>" --root "<target-root>"

For an unfinished parser repair, bind the next check honestly:

    import sys
    from pathlib import Path
    codex_nexus_root = Path("<loaded SKILL.md path>").resolve().parents[2]
    sys.path.insert(0, str(codex_nexus_root))
    from nexus.checkpoint import create_checkpoint
    packet = create_checkpoint(
        root, ["src/main.py"], "repair the failing parser",
        "run the focused regression", done_condition="focused parser regression passes",
        verifier_receipts=[{"name": "parser regression", "state": "pending"}],
    )

Read the full packet only when its task is in scope. Preserve unresolved
questions and stale-result errors in the handoff. A pending-check example
intentionally has no readiness claim. Do not silently refresh a changed hash
or accept an earlier-generation result as current. Reuse an unaffected
branch's work only after checking its inputs and recording the reconciliation.
