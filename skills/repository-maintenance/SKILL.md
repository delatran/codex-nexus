---
name: "repository-maintenance"
description: "Maintain this repository's skills, runtime, setup, manifests, and verification surfaces with source ownership and current receipts."
---

# Repository maintenance

Use this skill only when the requested change targets this repository's skills,
runtime contract, setup entrypoint, rules, manifests, or verification scripts.
It supplies repository-specific source ownership and gates.

Keep current source authoritative:

- Edit the source that owns the requested behavior; use the
  [source-to-verifier map](references/maintenance-gates.md) when ownership or
  the required gate is unclear.
- Treat generated manifests, reports, catalogs, and receipts as derived; regenerate them with their owning command.
- Inspect current status, callers, and the complete diff before and after a change.
- Preserve the owner's explicit feature selection. An advertised experiment is not a request to enable it or restore a deleted choice.
- Edit the repository's skill source exposed through `~/.agents/skills`; keep personal, system, and plugin skills separate without synchronized copies.
- A passing fixture proves only the exercised local behavior; it does not prove a live runtime or external state.

Use checks that match the changed behavior. A small skill prose edit needs
metadata and link checks plus an updated source inventory. Code, installation,
runtime, or coordinated catalog changes need the integrated verifier. A
documentation change alone does not require installation or a runtime probe.

Resolve the loaded `SKILL.md` path first. Let `codex_nexus_root` be
`SKILL.md.resolve().parents[2]`, verify that it contains `nexus/`, and use its
absolute helper paths. The Codex Nexus root is the maintenance target; do not
confuse it with a user's project root.

- Generate the source inventory with: `python -B "<codex_nexus_root>/nexus/__main__.py" inventory --root "<codex_nexus_root>" --write`
- Inspect runtime policy or client changes with: `python -B "<codex_nexus_root>/nexus/__main__.py" runtime --root "<codex_nexus_root>"`
- Check managed installation changes with: `python -B "<codex_nexus_root>/setup.py" --health --root "<codex_nexus_root>"`
- Use focused tests during implementation. For an integrated change,
  run `python -B "<codex_nexus_root>/nexus/__main__.py" verify --root "<codex_nexus_root>"`; add `--runtime` when runtime configuration or discovery changed.
  This command already runs the complete unit suite and packaging checks.
  Do not run the same suite again without a new change, failure, or concern.

Do not claim a manifest, runtime, or installation is current until the relevant
command output and final paths have been observed. A failed gate leaves that
claim incomplete; continue independent authorized work while resolving it.
