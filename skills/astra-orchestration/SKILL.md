---
name: astra-orchestration
description: Coordinate independent workers and dependent tool operations for complex tasks, handoffs, and verified integration.
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

## Schedule the ready work

Keep the coordinator on a ready dependency while workers run. Batch independent
searches and reads through the active host's supported parallel tool interface;
parallel tool calls do not require extra agents. Keep dependent operations,
coupled writes, and actions awaiting authorization in order. Inspect every
result, including failures, before using it in later work.

Request or extract the fields and source ranges needed for the decision. A
compact result still needs its source identity, support locator, relevant exit
status, and unresolved errors. Keep raw logs outside the conversational context
when they are needed as artifacts. Do not silently omit a failed parallel call
or remove citation provenance when summarizing large results.

A pending question or delayed tool blocks only its dependents. Continue other
authorized work, then use the host's completion notifications or bounded waits.
Avoid polling unchanged state. Before replacing interrupted work, inspect its
recorded IDs and current state so a retry does not duplicate an active action.

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
- Independent documentation lookups: batch the initial searches, inspect their
  results, then open the selected sources. A new query that depends on an opened
  source runs after that observation; worker count is not a progress measure.

## Verification

Check the integrated result against the user's acceptance condition. A worker
handoff is evidence to assess, not proof by itself. Set time or cost bounds
when the operation needs them or the user provides them; do not invent a
fixed timeout for every worker. If a worker is unavailable, continue its work
locally when feasible. Report only gaps that affect the delivered result.
