# Research and design evidence

[Overview](../README.md) · [Runtime](ASTRA.md) · [Evaluation](EVALUATION.md)

**Evidence reviewed through September 5, 2026.**

Codex Nexus draws on official Codex contracts, OpenAI employee guidance, and
primary research on instructions, context, feedback, and collaboration. Each
source below connects a design decision to the evidence and its limits.
These references support maintenance decisions; they are loaded only when
relevant to a task.

## Astra launch and employee guidance

The [Astra launch article](https://openai.com/index/gpt-6-astra/) and
[OpenAI release notes](https://openai.com/products/release-notes/) establish
the September 3, 2026 launch. This review includes relevant guidance published
through September 5. Rollout announcements describe availability over time;
they do not establish an individual account's current entitlement.

[Eric Provencher's article](https://x.com/pvncher/status/2095991462416490862)
recommends focused skill triggers, progressive disclosure, purposeful reading
and testing, and explicit completion boundaries. Codex Nexus applies this
guidance through short skill entrypoints, conditional references, and
verification proportional to the change.

[Dominik Kundel's article](https://x.com/dkundel/status/2095972046014673156)
favors native applications, useful references, clear finished states, and
task-level effort calibration. Codex Nexus uses existing application tools
before adding skills and keeps effort settings in the Codex configuration.
The article recommends trying low or medium effort before increasing it.
Codex Nexus retains the owner's requested higher defaults while allowing task
overrides. Neither the article's anecdotes nor these defaults prove an effort
level is optimal for every task.

The launch article introduces notes and searchable earlier context in Codex.
The [configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
defines `features.context_management.experimental_mode`. This exact setting
is enabled in the source and checked against the installed client. It requires
an eligible ChatGPT sign-in and remains experimental. An additional
[Provencher post](https://x.com/pvncher/status/2096190663310078360)
says the setting applies to new chats. Native context handling
does not replace a checkpoint that binds an external handoff to source hashes
and observed results. A passing feature check does not prove retrieval after
a real context boundary.

The [official Astra guide](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-6-astra)
addresses instruction sensitivity, clarification pauses, recurring prose,
selective delegation, and excessive testing on small tasks. The shared
AGENTS.md translates this guidance into authorized follow-through, concrete
writing, coordinated worker edits, and relevant verification. Skills keep
conditional procedures separate from universal requirements: a smoke run does
not require a confirmatory study, shared reads do not imply conflicting edits,
and an optional record does not block an otherwise authorized action. These
are instruction-design choices, not measured Astra performance gains. API mechanics
have one owner in
[the API skill reference](../skills/astra-api-integration/references/official-contracts.md).
They do not activate features in Codex App or CLI.

## Direct studies of instructions and skills

These are original studies. Preprints are labeled separately from published
conference or journal work. Versions matter because methods and results change.

| Source and version | Relevant evidence and limitation | Repository decision |
| --- | --- | --- |
| [SkillsBench](https://arxiv.org/html/2602.12670), v4, June 2026, preprint, Sections 3-4 | Curated procedures improve aggregate task success, but some tasks regress; generated skills and comprehensive packs can underperform. Mostly containerized terminal tasks, with no Astra measurement. | Retain distinct procedures and verifiers; avoid bulk skill generation and generic advice. Locally generated skills still need independent task checks. |
| [How Well Do Agentic Skills Work in the Wild](https://arxiv.org/html/2604.04323), v1, April 2026, preprint, Sections 3-4 | Discovery and distractor skills reduce the benefit of a curated pack. Query-specific refinement has mixed outcomes and substantial cost. | Keep triggers discriminating; do not add a skill retriever or automatic refinement service. |
| [Evaluating AGENTS.md](https://arxiv.org/html/2602.11988), v2, June 2026, preprint, Sections 3-5 | Generated context has no significant resolution benefit in the tested populations and increases work. Developer context has a small, nonsignificant average benefit. Python and a few model/harness combinations limit transfer. | Remove broad repository summaries and repeated generic instructions; keep actual owner constraints and commands. |
| [On the Impact of AGENTS.md Files](https://arxiv.org/html/2601.20404), v2, March 2026, preprint in the ICSE JAWS workshop context, Sections 3-5 | One paired Codex study reports lower runtime and output tokens, but does not assess semantic correctness or maintainability. | Efficiency and correctness need separate measures. Do not infer quality from a shorter run. |
| [Do Context Files Help Coding Agents?](https://arxiv.org/html/2607.27250), v1, July 2026, preprint, Sections 3-5 | A small replication finds no significant context-strategy effect. Its sample and asymmetric injection methods limit conclusions. | Keep no-context controls and disclose statistical uncertainty. Do not claim all context files help or hurt. |
| [Agent READMEs](https://arxiv.org/html/2511.12884), v2, August 2026, preprint | Describes how repository instructions are maintained. Frequency of a practice is not causal evidence that it helps. | Give every instruction an owner and check drift when its consumer changes. |

## Interfaces, context, feedback, and collaboration

| Primary source | Finding that changes a decision | Transfer boundary |
| --- | --- | --- |
| [SWE-agent](https://arxiv.org/pdf/2405.15793), NeurIPS 2024, Sections 2-3 and 5.1 | Tool-interface and context-management ablations favor useful actions and concise environment feedback over indiscriminate history. | Keep native Codex tools; do not copy an experimental shell harness. |
| [Lost in the Middle](https://arxiv.org/pdf/2307.03172), TACL 2024, Sections 2.3 and 4-5 | Relevant information is not used equally well at every position in a long context. | A checkpoint retains current goal, evidence, and next action; it does not replay the transcript. |
| [Context Length Alone Hurts Performance](https://aclanthology.org/2025.findings-emnlp.1264/), Findings of EMNLP 2025 | Retrieval success alone does not remove degradation from longer inputs in the studied tasks. | Report available context as capacity, not a target to fill or a fixed reliability threshold. |
| [Irrelevant Context](https://proceedings.mlr.press/v202/shi23a.html), ICML 2023, Sections 4-5 | Distractor information changes answers in controlled arithmetic tasks. | Load relevant skill references progressively; the measured rates do not transfer to coding. |
| [Reflexion](https://proceedings.neurips.cc/paper_files/paper/2023/hash/1b44b878bb782e6954cd888628510e90-Abstract-Conference.html), NeurIPS 2023, Appendix C | Feedback and retained lessons help in the tested agent setups; evaluator quality is consequential. | Record concrete failed assumptions after observed checks, not an unconditional reflection ritual. |
| [Self-Refine](https://proceedings.neurips.cc/paper_files/paper/2023/hash/91edff07232fb1b55a505a9e9f6c0ff3-Abstract-Conference.html), NeurIPS 2023, Sections 3.3, 4, and 6 | Specific feedback can help, but refinement has diminishing and sometimes non-monotonic returns. | Stop repeated review when evidence is unchanged; self-approval does not prove correctness. |
| [SWE-bench](https://arxiv.org/pdf/2310.06770), ICLR 2024, Section 2 | Repository tasks require navigation, edits, and execution against tests. | Keep artifact-based regression checks; a plausible patch is insufficient evidence. |
| [AI Agents That Matter](https://openreview.net/pdf?id=Zy4uFzMviZ), TMLR 2025, Sections 1-3 and 5-6 | Simple baselines, matched resources, holdouts, and reproducibility are necessary to assess an agent design. | Compare against the existing workflow and measure cost or work, not success alone. |
| [More Agents Is All You Need](https://mlanthology.org/tmlr/2024/li2024tmlr-more/), TMLR 2024 | Independent samples with aggregation can improve tested tasks. | Candidate diversity needs a reliable selector; it does not justify overlapping writes or a staffing quota. |
| [Capable Models Can Outgrow Collaboration](https://www.nature.com/articles/s42256-026-01268-y), Nature Machine Intelligence 2026 | Controlled comparisons show task-dependent gains, saturation, and coordination costs. | Delegate independent work and verify the merge; do not encode the study's thresholds as Astra constants. |

The apparently conflicting results are useful. Independent proposals with a
reliable selector differ from multi-turn collaboration over shared state.
Curated task procedures differ from broad, automatically discovered context.
None justifies deleting a useful safety boundary or adding a universal recipe.

## Codex contracts and limits

Current official [skills](https://learn.chatgpt.com/docs/build-skills),
[AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md),
[subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents), and
[configuration](https://learn.chatgpt.com/docs/config-file/config-reference)
guidance informs discovery, precedence, optional metadata, and supported keys.
Local probes still decide whether an installed executable accepts this config.

The skills guidance names `~/.agents/skills` as the user location and supports
symlinked skill folders. Codex Nexus links its source catalog there and keeps
personal, system, and plugin skills separate. The link removes the need for
synchronized copies; its filesystem health does not establish discovery in a
running task. Feature catalogs likewise inform compatibility checks without
requiring every experiment to be enabled. The source records the owner's
explicit selection.

The [Codex execution-policy implementation](https://github.com/openai/codex/blob/main/codex-rs/core/src/exec_policy.rs)
turns explicit forbidden prefixes into rejections and can let an explicit allow
skip approval or sandbox escalation. Codex Nexus leaves execution policy with
Codex instead of adding a broad prefix list over full-access behavior. Task
authority still comes from the user's request and the host's governing rules.

The project has one rule source, AGENTS.md, and one Codex settings source,
.codex/config.toml. Skills add only domain-specific decisions and helpers.
Context and feature observations are reported by the runtime inspector instead
of copied into a second policy manifest. No generic reasoning, writing-style,
provider-adapter, or self-reflection skill is added.

English and no-em-dash source checks enforce the owner's explicit preferences.
They do not detect authorship or judge semantic writing quality. Final review
assesses clarity, evidence, relevance, and whether the artifact does the job.

These sources justify design choices with limits. The [evaluation procedure](EVALUATION.md)
defines the next evidence needed for any claim that the workflow improves Astra
quality, latency, or resource use. Local regression and discovery checks establish
only their observed behavior. No benchmark effect from another model is claimed
as a Codex Nexus result.
