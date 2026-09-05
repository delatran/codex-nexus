# ML protocol and evidence

Use this reference for a confirmatory comparison, a reproducibility audit, or a
result intended for a report. Capture the fields that affect the claim; a
diagnostic run does not need the full protocol of a publication experiment.

## Freeze before scored outcomes

Specify the decision target, hypothesis or effect being estimated, dataset and
version, relevant population, split or cross-validation rule, preprocessing,
model or algorithm, baseline or control, metric, uncertainty method, budget,
seed policy, stopping rule, and exclusions. Record the code and environment
needed to recreate the run.

A baseline should expose whether the proposed method adds value. A control is
needed when isolating a change or attributing an effect; it can coincide with
the baseline when that comparison answers the question. Keep nuisance
conditions equal. If resource use, model identity, or a workflow difference is
the intended intervention, declare it and choose budgets consistent with that
comparison.

Exploratory diagnostics may precede the frozen comparison. If tuning exposed
the final holdout, use fresh independent evaluation or limit the conclusion;
renaming the same holdout does not restore independence.

For causal claims, identify treatment, outcome, comparison, time window,
confounders, selection risks, and identification limits before evaluating the
effect.

## Evidence levels

| Label | Minimum evidence |
| --- | --- |
| planned | A protocol or command exists without an observed run outcome. |
| executed | The command or cell ran, and its outcome is tied to code, data, and run identity; record success or failure separately. |
| reproduced | A replay matches a declared tolerance under the stated conditions. |
| exploratory | The protocol or sample was adapted after inspecting outcomes; it is not a confirmatory comparison. |
| unverified | An artifact or fact needed to substantiate the claim is missing. |

Saved logs and artifacts can support execution when their provenance and
identity are established. A copied log without a reliable link to the claimed
run cannot. Do not rerun an experiment solely to replace valid evidence with
a newer timestamp.

## Reproducibility artifacts

Reuse the project's manifests and run outputs. Add missing evidence when it
affects replay or interpretation:

- data identity, split, preprocessing fit scope, and content/version fingerprint;
- code revision or content identity, including material working-tree changes;
- relevant dependency/runtime versions, hardware, configuration, and seeds;
- command, raw output, metric calculation, timestamp, and artifact locations;
- comparison, uncertainty, failed runs, and exclusions.

Hash artifacts when immutable identity or a later freshness check matters.
Exclude secrets, credentials, and unnecessary personal data from a shared
bundle. A bundle is an index to evidence, not proof that an unobserved run
succeeded.

## Shared GPU execution

Inspect processes and free memory before consuming shared GPU capacity. Record
the observation when occupancy changes the chosen run or affects comparison.
Prefer available capacity that will not disrupt an existing workload; a busy
device alone does not require stopping independent work.

Do not reset a runtime or terminate another workload without authorization for
that action and target. An unavailable run remains unexecuted. Report the
resource limitation without inventing a performance result or changing a frozen
comparison silently.

## Contamination and interpretation

Check the leakage paths relevant to the task: train/evaluation overlap,
preprocessing fit scope, duplicate records, temporal leakage, benchmark or
prompt exposure, selection effects, and tuning on the holdout. Report open
risks that limit the conclusion; do not imply every possible contamination
check was performed.

Distinguish association, predictive comparison, and causal effect. State the
strongest conclusion supported by the design and observations, with uncertainty
and population limits. For example, a higher score after changing the split is
an exploratory observation until evaluated under a valid common comparison.
