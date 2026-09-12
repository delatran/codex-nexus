# Maintenance gates

Source ownership:

- skills/*/SKILL.md and their references own skill behavior and routing.
- nexus owns offline inventory, evidence, runtime inspection, and installation logic.
- AGENTS.md owns the common rules and .codex/config.toml owns installed runtime defaults.
- The source feature selection is explicit. Runtime discovery must not require every advertised experiment or restore a feature the owner removed.
- ~/.agents/skills links the source catalog; personal and system skills under ~/.codex/skills and plugin skills remain separately maintained.
- setup.py owns complete Codex installation, configuration merge, and health inspection.
- tests own executable regression evidence.

Match the check to the changed surface:

| Change | Verification |
| --- | --- |
| Small skill prose or reference edit | Validate frontmatter, local links, and the updated inventory. |
| Coordinated skills, catalog, Python, or packaging change | Run focused checks during editing, then the integrated verifier. |
| Runtime configuration or discovery change | Add `--runtime` to integrated verification; inspect the current runtime output. |
| Installer or managed installation change | Run the relevant installer tests and read-only health check; inspect the actual final paths. |

Derived surfaces must be regenerated, not hand-edited. Resolve the loaded
`SKILL.md` path first, derive `codex_nexus_root` from
`SKILL.md.resolve().parents[2]`, and verify that it contains `nexus/`. Use
absolute helper paths and keep the Codex Nexus root separate from any target
workspace:

    python -B "<codex_nexus_root>/nexus/__main__.py" inventory --root "<codex_nexus_root>" --write

Use `python -B "<codex_nexus_root>/nexus/__main__.py" runtime --root
"<codex_nexus_root>"` for focused runtime inspection. Integrated verification is
`python -B "<codex_nexus_root>/nexus/__main__.py" verify --root
"<codex_nexus_root>"`; it already includes the unit suite, source checks,
inventory comparison, and packaging. Add `--runtime` after runtime or
discovery changes. A documentation-only change does not by itself require
a local Codex probe or installation operation.

After installation changes, the health check is read-only:

    python -B "<codex_nexus_root>/setup.py" --health --root "<codex_nexus_root>"

Before closing, compare the command output with the requested target, inspect
the complete diff, and identify material checks that were skipped or only run
against fixtures. Do not use a report, catalog, or configured link as proof
of live behavior without a runtime observation. Preserve existing authorization
for the named installation target; prepare all local changes and verification
before any remaining approval for an external effect.
