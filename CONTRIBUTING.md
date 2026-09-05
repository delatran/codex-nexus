# Contributing to Codex Nexus

Codex Nexus is maintained for Codex App and Codex CLI. Read `AGENTS.md` and
the nearest owning source before editing. Keep repository instructions, code,
comments, configuration, and documentation in English. Never put credentials,
private machine paths, or user-specific configuration values in distributable
files.

## Branches and releases

`main` holds stable releases. `gpt-6-astra` is the persistent development
branch for Astra: commit and push focused changes there as often as needed.
Ordinary development does not advance `main` or create a release tag. Keep
GitHub's default branch on `main` and the development checkout on
`gpt-6-astra` between releases.

A release is ready when its intended scope is complete, the full diff has
been reviewed, relevant local checks pass, and all `verify` CI jobs pass for
the exact development commit. Commit count is not a release criterion.
Follow the local workflow below and add runtime or installation checks when
the changed behavior requires them. Commit the regenerated source manifest
with its owning changes. Remote publication remains subject to the owner's
authorization for the repository and effect.

This release cycle applies while `main` targets Astra. If `main` adopts
another model, keep any ongoing Astra maintenance separate and transfer only
applicable shared fixes; do not merge the two model branches wholesale.

For an authorized release:

1. Start with a clean working tree, fetch `origin`, and confirm that the local
   development branch matches its remote branch. Update local `main` with a
   fast-forward from `origin/main`. Bring any new `main` changes into
   `gpt-6-astra` before release review, resolve conflicts there, regenerate
   the manifest if source changed, and verify and push that candidate.
2. After the candidate's CI passes, switch to `main` and merge
   `gpt-6-astra` with `git merge --no-ff gpt-6-astra`. The release merge
   must have the same source tree as the reviewed candidate. If it does not,
   return to development, reconcile the changes, and validate the new result
   before publication.
3. Push `main` without forcing and wait for every `verify` job on the exact
   merge commit to succeed. If CI fails, fix and verify the failure before
   tagging; do not label a failed candidate as a completed release.
4. Create an annotated tag on that verified `main` commit using
   `git tag -a gpt-6-astra-vX.Y.Z main -m "Astra X.Y.Z"`, replacing the
   version placeholders, then push that exact tag. Check the remote tag's
   peeled commit against the verified release commit. Existing release tags
   are fixed: corrections receive a new version, never a moved tag.
5. Fast-forward `gpt-6-astra` to `main`, push the development branch, and
   leave it checked out for the next change. Keep both branches; release
   merges preserve development history.

The first release is `gpt-6-astra-v1.0.0`. Increment the patch version for
compatible fixes, the minor version for compatible additions, and the major
version for incompatible changes to the supported setup or runtime contract.
A tag identifies this repository's release, not a new OpenAI model version.

To inspect an older release without changing the active installation, use a
separate worktree at its tag. Installed skills read the linked checkout
directly; changing that checkout's branch can change the skills exposed to
new tasks. If a published release needs correction, use a reviewed follow-up
commit or revert and a new tag rather than rewriting shared history.

## Make a focused change

Every change should have a concrete consumer and an observable verifier.

- A skill needs a discriminating trigger, a short entrypoint, and a task-level
  check. Add a reference only when it changes a decision or procedure.
- A configuration change must update the runtime contract and negative tests
  when it changes accepted keys, types, or behavior. Keep the owner's explicit
  feature selection; do not enable new experiments or restore deleted choices
  merely because a client catalog advertises them.
- A setup or installation change must cover preservation, conflicts, failure,
  and recovery at the boundary it changes.
- A verification change must explain what it establishes and what remains
  untested. Do not make an oracle weaker to obtain a pass.
- Documentation should describe current ownership and usage. Do not add a
  migration diary, generic model advice, or claims that were not measured.

Prefer the smallest coherent design that protects a real invariant. Keep
unrelated cleanup out of a functional change. Do not add a wrapper around a
native Codex capability unless the wrapper provides a concrete contract that
the host does not provide.

Edit skill source under `skills/`. Installation exposes that source through
`~/.agents/skills`; do not create synchronized copies under `~/.codex/skills`
or replace the personal, system, and plugin skills maintained there or by Codex.

## Local workflow

From the repository root:

```sh
python -B -m nexus inventory --write
python -B -m nexus verify
python -B setup.py --health
git diff --check
```

Add `--runtime` to the verification command when changing `.codex/config.toml`,
runtime discovery, or feature interpretation. The integrated command runs the
full suite once. Export a source package only when needed, to an external path:

```sh
python -B -m nexus package --output ../codex-nexus-source.zip
```

The source manifest is generated, not hand-edited. A package must be created
outside the repository and must not already exist. Do not report remote CI,
account entitlement, server behavior, or live model quality from a local run.

## Review before delivery

Read the complete diff after the checks pass. Confirm that the result:

- preserves unrelated owner changes and user configuration;
- keeps authority, trust boundaries, and target paths explicit;
- has tests for consequential success and failure paths;
- contains no unsupported model names, secrets, private absolute paths, or
  em dashes;
- links only to files that exist in the current source tree; and
- reports skipped, unavailable, and unmeasured behavior honestly.

If a check is unavailable, state the missing evidence and the next useful
verifier. A passing static scan proves source shape only. A passing runtime
probe proves local Codex advertisements only. Neither is a benchmark of model
quality.

See [the architecture](docs/ARCHITECTURE.md), [runtime contract](docs/ASTRA.md),
[verification boundaries](docs/VERIFICATION.md), [research record](docs/RESEARCH.md),
and [evaluation protocol](docs/EVALUATION.md) for the owning contracts.
