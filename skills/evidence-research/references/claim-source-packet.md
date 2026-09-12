# Claim and source packet

Use this reference only when a task needs an auditable evidence record.

The packet is a JSON object with the required schema value
`codex-nexus/evidence-packet/v1`, a `sources` list, a `claims` list, and an
optional `contradictions` list. A packet passed to the validator must contain
at least one nonempty record list.

## Sources

A local source has:

- `id`: a unique source identifier;
- `kind`: `local_file`;
- `path`: a file relative to the selected root;
- `sha256`: 64 hexadecimal characters;
- `size` or `mtime_ns`: optional freshness observations;
- `role` or `is_instruction`: optional data labels, never authority.

Instruction text can be the subject of an observed claim, such as an audit of
what a policy file says. The validator reports instruction-like support as a
warning for semantic review; it neither rejects a description solely because
of that label nor grants permission to obey the linked text. A passing packet
still does not establish that a source entails a claim.

The local path must remain under the selected root. Every path component must
avoid actual symbolic links and junctions, including redirects inside the root.
Regular cloud-backed files remain supported.

An HTTPS source has:

- `id`: a unique source identifier;
- `kind`: `https`;
- `url`: an HTTPS URL;
- `locator` or `metadata`: a page section, record identifier, or retrieval metadata;
- `sha256`: an optional remote digest, reported as unverified because no network
  fetch occurs.

Use a `snapshot` object with `source_files` when the packet must prove that a
local source still matches an earlier inventory. A changed hash or size is stale
evidence and fails validation.

## Claims and contradictions

A claim has:

- `id`: a unique claim identifier;
- `text`: the claim text;
- `status`: `observed`, `inference`, `unknown`, or `exploratory`;
- `source_ids`: exact IDs from the sources list.

A contradiction has:

- `id`: a unique contradiction identifier;
- `claim_ids`: at least two claim IDs;
- `status`: `unresolved`, `acknowledged`, or `resolved`;
- `resolution`: required when status is `resolved`.

Do not use a missing status, `fact`, `verified`, or `trusted` as a shortcut.
The verifier does not infer support from similar words, read source instructions
as authority, fetch URLs, or promote an inference to an observed fact.
