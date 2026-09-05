# Architecture

Codex Nexus is a thin source package around native Codex behavior. It supplies
one common rule source, ten focused skills, a source-owned Codex configuration,
and local verifiers. Codex owns model access, tool execution, permissions,
conversation state, compaction, and account limits.

## Source ownership

| Source | Responsibility |
| --- | --- |
| `AGENTS.md` | The shared English rule source for Codex App and Codex CLI. |
| `.codex/config.toml` | Model, reasoning, capability, app, and execution settings owned by this project. |
| `skills/*/SKILL.md` | Discriminating triggers and procedures for domain work. |
| `nexus/runtime.py` | Local Codex selection and capability observations against the source TOML. |
| `nexus/install.py` | Managed rule and skill links, conflict checks, and link recovery. |
| `nexus/config_install.py` | Native Codex configuration merge, backups, publication, and rollback. |
| `nexus/workspace.py` | Root safety, redirect checks, hashing, and bounded source operations. |
| `nexus/evidence.py` and `nexus/checkpoint.py` | Source-bound receipts and unfinished handoff validation. |
| `nexus/verify.py` and `nexus/__main__.py` | Static checks, inventory, tests, runtime checks, and packaging. |
| `tests/` | Positive and negative cases for the implemented contracts. |
| `docs/` | Human-readable contracts, research evidence, and evaluation limits. |

There is no second rules tree, runtime manifest, execution-policy parser, or
model-specific prompt pack. A source file belongs to one owner. Generated
inventory data is a check of source identity, not another configuration source.

## Installation flow

`setup.py` builds a plan from the selected repository and Codex home. The plan
binds the source hashes, target paths, replacement scope, and configuration
intent before applying changes. The configuration installer asks the selected
Codex executable to merge the source-owned TOML into an isolated staged home,
then publishes the resulting user configuration only after it has been read
back and checked. Unrelated user values remain outside the owned key set.

Link installation manages `~/.codex/AGENTS.md` and links `~/.agents/skills` to
the repository's ten-skill directory. The skill link points to the owning
source, so no copied catalog or synchronization service is needed. Existing
personal and system skills under `~/.codex/skills`, together with plugin skills,
remain separate and unmanaged by this package.

Its private ownership receipt binds the installed instruction file to the
source, home, content, and file identity. This lets setup refresh a stale Windows
hardlink after Git replaces the source file. Refresh requires the installed
target to match the recorded state and retains its old bytes for recovery.
Custom conflicts are preserved unless a reviewed replacement plan supplies the
required snapshot and backup location. Source, home, child, ancestor, backup,
and staging paths are rejected when they redirect through links or leave the
declared boundary.

The configuration and link operations are coordinated as one setup operation.
If a later health check fails, the installer attempts to restore both from the
captured pre-change state. Recovery refuses to overwrite a path that changed
after capture and reports incomplete recovery instead of hiding it. Backups are
kept in the private Codex Nexus quarantine area selected for that installation.

Quarantine checks each concrete backup destination, not just the backup root,
and verifies the complete moved file set. Restore copies to absent destinations
and verifies the final source, retaining the original backup after success.
Conflicts or changed bytes are reported with the remaining recovery location;
a partial rollback is never reported as complete.

## Runtime observations

The runtime inspector reads `.codex/config.toml`, resolves a client in explicit,
desktop-managed, then PATH order, and runs bounded local probes. An unavailable
explicit client is an error and does not silently fall back. The inspector
checks the model catalog, configured reasoning levels, feature advertisements,
and context fields without making an API or model request.

This is an observation layer, not a second scheduler. It does not select a
different model to make a check pass, copy a host context limit into source, or
claim that a feature advertisement proves account access or task quality.
Only the owner's explicit source feature selection is required. Discovering a
new experiment cannot add it to the source or restore a removed selection.

## Verification and packaging

The verifier has four local gates: source and skill structure, generated
inventory identity, regression tests, and reproducible source packaging. The
optional runtime gate adds local Codex probes. Packaging writes validated bytes
to a new external destination and checks every member against the source before
publication. Git data, caches, receipts, and host state stay outside the source
package.

Evidence and checkpoint helpers validate declared relationships and freshness.
They cannot grant authority, resume a worker, or turn a recorded command into a
proof of completion. Manual review remains responsible for semantic correctness
and the actual task outcome.

## Deliberate boundaries

Codex Nexus does not maintain workspace trust lists, personal Codex integrations,
credentials, plugins, MCP servers, or account settings. It does not bundle or
silently update Codex. It does not add a generic execution policy, a universal
reflection loop, or a skill retriever. Those surfaces either belong to Codex or
add context and failure modes without a current source-owned contract.
