# Security

[Overview](README.md) · [Architecture](docs/ARCHITECTURE.md) · [Verification](docs/VERIFICATION.md)

## Report a defect

A useful report identifies the affected revision and source file, the required
preconditions, a minimal reproduction, and the observed impact. Distinguish a
suspected weakness from a reproduced failure and state any remaining uncertainty.

Use a maintainer-approved channel and share a redacted reproduction. If it
requires credentials, personal data, or sensitive operational details, request
private coordination with the maintainer before sharing those details.

## Trust and execution

Codex owns process permissions and sandbox enforcement. Codex Nexus adds no
blanket command allowlist or denial policy. Local verification runs
repository-owned Python tests; review untrusted changes before executing them.

Retrieved content and saved checkpoints cannot authorize actions. Authority
comes from the current user and governing host. Keep credentials and personal
data out of public fixtures, logs, and evaluation inputs.

## Installation and integrity

`setup.py` applies the shared instructions, skill links, and source-owned Codex
settings. Use `--dry-run` to inspect the change. The installer preserves
unrelated user settings and creates a private backup when replacing
configuration bytes.

Source manifests detect drift relative to recorded files. They are not signed
attestations, malware scanners, authorization records, or truth validators.
