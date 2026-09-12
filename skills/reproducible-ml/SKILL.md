---
name: "reproducible-ml"
description: "Plan, run, and verify ML experiments with reproducible protocols, fair comparisons, and results tied to actual runs."
metadata:
  short-description: "Run reproducible ML and verify evidence"
---

# Reproducible ML

Use this skill for ML experiments, training or evaluation notebooks, benchmarks,
or reproductions where the protocol and execution evidence matter. Ordinary
code edits and literature-only research do not require an experiment workflow.

## Choose the experiment mode

- For a smoke test or exploratory run, capture the inputs and settings needed
  to understand the observation. Adapt the experiment as useful and label the
  result exploratory; do not present a tuned-on outcome as confirmatory.
- For a confirmatory comparison, freeze the question, data/version, split,
  evaluation, primary metric, relevant baseline or control, seed policy, budget,
  exclusions, and stopping rule before inspecting scored outcomes. Keep
  unintended differences between comparison arms fixed.
- For a reproduction or audit of existing results, inspect the run artifacts
  and their source identity first. Existing logs can establish execution when
  their provenance is adequate. Rerun only when the request or a specific
  evidence gap requires it.

A baseline measures the reference behavior; a control isolates a claimed
effect. Use the comparison the question needs rather than adding redundant
arms. A predictive improvement alone does not establish a causal effect.

## Execute and retain evidence

Inspect the code, data manifest, environment, and runtime state relevant to the
run. Record code identity including material uncommitted changes, data/version,
configuration, seeds, environment, command, and output locations to the extent
needed to reproduce the result. When model calls are part of the experiment,
record the provider/model identifier and configured runtime settings, along
with returned identity or usage observations when available.

Choose runs sufficient to answer the question. A cheap smoke test is useful
when it can expose invalid inputs or execution errors before a costly run.
Preserve raw metrics, the calculation that produced them, logs, and failed
runs before summarizing. Check relevant leakage and protocol drift before
interpreting a comparison.

A written cell or command is a plan. Execution needs an observed outcome tied
to its run, and reproduction needs a replay within a declared tolerance.
Configuration alone does not prove model behavior. Identify missing evidence
explicitly instead of filling it from memory.

## Shared GPU and notebook state

Before allocating a shared GPU, inspect current processes and available memory
with a read-only tool such as `nvidia-smi`. Preserve active workloads and saved
notebook state. Within the authorized runtime budget, use available capacity
when the run can coexist without disrupting existing work.

Occupied resources do not automatically block the task. Use another available
device or a justified diagnostic run when appropriate, and continue independent
source, data, or CPU work while a GPU run is unavailable. Record any resource
change that affects comparability.

Terminate a process, reset a GPU or runtime, or overwrite notebook state only
when the exact action and target are already authorized. Do not repeat a
permission request for authorization that remains valid.

## Supporting reference and output

Read [protocol and evidence guidance](references/protocol-and-evidence.md) when
designing a confirmatory comparison, investigating contamination, or preparing a
reproducibility artifact.

Report the result, what actually ran, the relevant comparison, and material
limits. Use a durable evidence bundle only when the task needs reproducibility,
publication, or handoff beyond the existing run artifacts.
