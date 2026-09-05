---
name: agent-evaluation
description: Compare agent workflows, skills, and routing with fair protocols, protected holdouts, and outcome evidence suited to the claim.
---

# Agent evaluation

Use this skill to evaluate an agent workflow, skill set, routing policy,
configuration, or delegation procedure. A focused regression case can validate
its covered behavior; it cannot establish general agent quality.

## Select and freeze the comparison

Choose the comparison that answers the user's question. Use a no-skill control
when measuring a skill's contribution; an evaluation of two workflows may need
different arms. Before inspecting scored outcomes, define the task population,
versions, intervention, success criteria, primary metric, exclusions, and
stopping rule.

Hold other relevant conditions constant, including input artifacts, model and
effort, tool permissions, context, delegation policy, and resource budgets,
unless one is an explicit part of the intervention. Record intended differences
so they are not mistaken for a skill effect. Development cases may be used to
refine the protocol, but they then cease to be blind holdouts.

Provide the executing agent with the actual task wording, task requirements,
and source artifacts needed for the work. Keep held-out expected answers,
evaluator-only failure labels, and scoring fixtures separate when their exposure
would leak the solution. Blind artifact scoring to the comparison arm when
practical. Holdout protection must not hide the real user's requirements.

## Define observable outcomes

Prefer a deterministic oracle when the outcome permits one, such as a focused
test with an observed exit status, a file/schema invariant, or a frozen metric
calculation. The oracle must test the requested behavior. A matching filename or
hash alone does not establish semantic correctness.

For qualitative work, define a rubric with observable criteria and use
independent or blinded review when it adds useful confidence. A rendered
document, production observation, or security finding needs checks appropriate
to that artifact and claim. Do not force every task through the same verifier.

Keep oracle failure, incomplete evidence, and evaluator disagreement separate.
A fluent answer, planned command, or generated receipt is not an observed
outcome. Inspect the underlying artifact and tool result.

## Metrics and resources

Report the primary outcome with its task count and relevant uncertainty.
Choose secondary metrics for the question, such as unsupported claims,
artifact correctness, unnecessary clarification, latency, cost, retries, or
side effects. Include consequential authorization or artifact-integrity
violations. Mark unavailable telemetry as unavailable rather than estimating it
from configuration.

When evaluating delegation, define harmful overdelegation before scoring:
unnecessary duplicated work, avoidable coordination cost, or workers that add
no useful independent contribution. Necessary overlap for independent
verification is not excessive merely because multiple agents inspect the same
artifact.

Configured model, effort, context limit, selected skills, and tool catalog are
static metadata. Observed prompts, artifacts, tool outputs, usage, errors, and
scores are run evidence. A configured capacity is neither a utilization result
nor proof of correctness.

## Evidence and limits

Run a comparison large enough for the claimed conclusion, using a pilot when it
can catch protocol faults before expensive evaluation. Preserve raw outcomes
and failure cases. Do not retune the task set, oracle, or metric after seeing
results without labeling that analysis exploratory and using a fresh holdout
for a confirmatory claim.

A local replay establishes only the behavior it covers. Explain limitations
that affect the conclusion, including missing observations and differences
between the evaluation and the intended live environment.
