---
name: astra-orchestration
description: Coordinate complex work with independent Astra workstreams, targeted checkpoints, and verified integration.
---

# Astra orchestration

Use this skill when a task has independent workstreams or a multi-phase
workflow whose dependencies need coordination.
Do not load it for a small self-contained edit, a single read, or a tightly
coupled change where another worker cannot add evidence or reduce elapsed time.

## Decision

1. Choose single-agent execution when the work depends on one evolving state,
   edits the same files, needs serial observations, or has no useful parallel gain.
2. Delegate ready independent work when it adds evidence or reduces elapsed
   time. Assign a concrete deliverable, relevant context, write ownership, and
   an acceptance check. Shared read-only context is allowed; keep coupled
   writes with one owner. State integration dependencies when they matter.
3. Inherit the coordinator and worker model, effort, thread cap, and nesting
   cap from the active runtime unless the current user explicitly requests a
   supported override. Treat observed caps as ceilings, never staffing targets.
   Do not reserve workers for sequential phases. Keep dependent implementation
   with the coordinator when a worker would wait idle. A worker may delegate
   further only when the active instructions and runtime permit it. Independent
   final reviews may run together once source is stable.
4. Keep worker handoffs proportional to the work: report the result, changed
   files, relevant evidence, and unresolved issues. Add source identifiers or
   other fields when the coordinator needs them to reconcile concurrent work.
5. The coordinator owns synthesis. Merge evidence by claim and source, reject
   stale or conflicting results, reread changed files, and run the parent
   verifier before closure.

## Initiative and clarification

When the user has clearly requested action, continue the authorized local work
through inspection, implementation, and verification. Make a bounded
assumption when missing detail does not materially change the target,
permission, or acceptance condition. Reuse authorization already given for
the target and effect. Before any remaining approval, prepare the concrete
change and verifier. A pending question blocks only work that depends on its
answer; continue independent authorized work.

If a task repeatedly pauses on a question that the current sources already
answer, resolve it from those sources and identify the conflicting guidance.
Edit that guidance only when instruction maintenance is in scope; otherwise
report the issue without expanding the task. A local instruction cannot
override an active host restriction or approval decision.

## Checkpoints

Create a checkpoint only before an actual handoff, context loss, or delegated
state that cannot be reconstructed cheaply. A routine long phase or merge in
the current context does not require one. Record enough to resume correctly:
the goal, acceptance condition, source identity, pending work and tool IDs,
verification state, and next action. Use a structured source-bound packet
only when its validation adds value; see
[context-checkpoint](../context-checkpoint/SKILL.md) for that workflow.
A user steering update invalidates only plans and worker results
whose inputs, authority, or acceptance condition it changes; retain unrelated
branch results and revalidate shared merge assumptions.

## Executable examples

- One failing test in one module: stay single-agent and run the focused
  reproduction, root-cause check, fix, and regression gate.
- Two caller audits that inform one shared-contract edit: delegate the two
  read-only audits while the coordinator examines the shared contract and its
  tests. The coordinator integrates the findings and owns the coupled edit.
  Add a later independent review only when it can check the completed result.

## Verification

Check the integrated result against the user's acceptance condition. A worker
handoff is evidence to assess, not proof by itself. Set time or cost bounds
when the operation needs them or the user provides them; do not invent a
fixed timeout for every worker. If a worker is unavailable, continue its work
locally when feasible. Report only gaps that affect the delivered result.
