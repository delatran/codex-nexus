# Codex Nexus

A focused setup for GPT-6-Astra in Codex App and Codex CLI.

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Codex Nexus connects one shared `AGENTS.md`, ten task-specific skills, a
source-owned Codex configuration, and local verification tools. It is built
for Codex App and Codex CLI, with practical support for repository work,
source-backed research, reproducible experiments, evidence-bound documents,
production checks, and controlled delegation.

Codex remains responsible for model access, tools, permissions, conversation
state, compaction, and account limits. Codex Nexus supplies shared rules,
procedures, and checks that make those tasks easier to carry out consistently.

## Install

Use Python 3.11 or newer and a local Codex executable that accepts the current
configuration schema. Local verification needs no API key, Node runtime, or
model request.

Preview and apply the installation from the repository root:

```sh
python -B setup.py --dry-run
python -B setup.py
python -B setup.py --health
```

The default `setup.py` operation applies the package. `--dry-run` prints the
hash-bound plan without changing the Codex home. `--health` checks the managed
links and source-owned settings. Start a new Codex task after setup so the host
loads the current instructions and configuration.

Setup manages three Codex surfaces:

| Codex path | Source | Purpose |
| --- | --- | --- |
| `~/.codex/AGENTS.md` | `AGENTS.md` | Shared operating rules for Codex App and Codex CLI. |
| `~/.agents/skills` | `skills/` | A directory link to the ten-skill catalog. |
| `~/.codex/config.toml` | `.codex/config.toml` | Codex Nexus settings merged through Codex's native writer. |

The configuration merge preserves unrelated user settings and creates a
private backup of replaced bytes. Setup does not manage workspace lists,
trusted project entries, credentials, plugins, MCP servers, or other personal
integrations. Existing custom instruction files and links require an explicit
replacement plan. On Windows, the managed instruction link can be refreshed
after Git replaces the source file when its private ownership receipt still
matches the recorded content and identity.

The [official skills guidance](https://learn.chatgpt.com/docs/build-skills)
uses `~/.agents/skills` for user skills and supports linked skill folders.
The link reads this repository's source directly, so changes need no copied
catalog or synchronization job. Personal and system skills already supplied
through `~/.codex/skills`, and skills supplied by plugins, remain separate.
Setup does not replace or copy those skills. Check a new task's available skill
list to verify discovery; healthy filesystem links alone do not prove it.

## Defaults

The checked-in configuration selects GPT-6-Astra for lead and worker tasks,
Ultra for lead and planning work, Max for workers, and native V2 delegation.
The owner selects the feature keys explicitly: apps, fast-mode selection,
memories, idle-sleep prevention, shell tools, unified execution, experimental
context management, and the network proxy. `shell_zsh_fork = false` retains
the Windows compatibility choice. The source configuration is the authoritative
list of requested values. Client discovery checks that selection without
adding experiments or restoring features the owner removed.

Codex requires a finite positive worker ceiling, so the source uses `1_000_000`.
This is a ceiling, not unlimited concurrency or an account entitlement. Host
resources, service limits, task authority, and the active Codex session still
determine what runs.

Context-window, compaction, token-budget, output, and verbosity defaults remain
model and host controlled. The package does not set a numeric token budget.

See [the Astra runtime contract](docs/ASTRA.md) for the selected settings and
their evidence limits.

## Skills

Load a skill when its procedure changes the task. Ordinary coding, explanations,
and simple edits usually need no additional workflow.

| Skill | Use it for |
| --- | --- |
| [Astra orchestration](skills/astra-orchestration/SKILL.md) | Independent workers, merge checkpoints, and bounded delegation. |
| [Context checkpoint](skills/context-checkpoint/SKILL.md) | Source-bound handoff and resume state. |
| [Evidence research](skills/evidence-research/SKILL.md) | Current claims, source comparison, and traceable support. |
| [Reproducible ML](skills/reproducible-ml/SKILL.md) | Frozen experiments, controls, and result evidence. |
| [Evidence-bound documents](skills/evidence-bound-documents/SKILL.md) | Template-preserving reports, resumes, and rendered QA. |
| [Production verification](skills/production-verification/SKILL.md) | Current release claims checked against source and runtime evidence. |
| [Agent evaluation](skills/agent-evaluation/SKILL.md) | Holdout evaluations with exact outcome oracles. |
| [Security boundaries](skills/security-boundaries/SKILL.md) | Scope, authorization, trust, and passive security workflows. |
| [Astra API integration](skills/astra-api-integration/SKILL.md) | A separate Responses client that needs validated request handling. |
| [Repository maintenance](skills/repository-maintenance/SKILL.md) | Changes to Codex Nexus, its setup, manifest, or verification gates. |

Native first-party skills remain the execution tools for documents, PDFs,
spreadsheets, browsers, cloud applications, and other surfaces. Codex Nexus
adds only the owner-specific procedures and evidence contracts that those tasks
need.

## Verify

After changing source, regenerate the manifest and run the relevant checks:

```sh
python -B -m nexus inventory --write
python -B -m nexus verify
git diff --check
```

Add `--runtime` to the verification command when changing Codex settings,
runtime discovery, or feature interpretation. It adds local Codex probes to
the same verification run and does not generate a model response.

To export a source archive, run
`python -B -m nexus package --output ../codex-nexus-source.zip`.
The destination must be new and outside the repository. Save verification
receipts only when needed, using `--output` with an external path.

A passing local check establishes only the behavior that it exercises. It does
not prove server availability, account entitlement, effective session
precedence, deployment, or model quality.

## Documentation

- [Architecture](docs/ARCHITECTURE.md) explains source ownership and runtime
  boundaries.
- [Astra runtime contract](docs/ASTRA.md) explains defaults, setup precedence,
  and client inspection.
- [Verification boundaries](docs/VERIFICATION.md) explains what each local
  gate can and cannot establish.
- [Research record](docs/RESEARCH.md) records the evidence behind design
  choices.
- [Evaluation protocol](docs/EVALUATION.md) defines a fair holdout comparison.
- [Contributing](CONTRIBUTING.md) describes maintenance expectations.
- [Security](SECURITY.md) describes reporting and scope boundaries.

MIT licensed.
