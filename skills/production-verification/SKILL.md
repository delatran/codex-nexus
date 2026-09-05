---
name: "production-verification"
description: "Verify live service behavior or release provenance using current deployment, HTTP, and browser evidence appropriate to the claim."
metadata:
  short-description: "Verify live behavior and release provenance"
---

# Production Verification

Use this skill when someone needs evidence that a release, deployment, site,
API, or production configuration is live and behaving as intended.

Do not use it for release planning without a current target, local-only tests,
static configuration review, or active security testing. Use the appropriate
planning, testing, or security workflow for those tasks.

## Proof contract

- Bind the exact target, environment, expected behavior, and observation time.
  Add source, artifact, or config identity when the claim concerns a release.
- A configured deploy trigger, successful build, historical receipt, or
  control-plane status establishes only its own evidence layer. Test live
  behavior directly before claiming that the service behaves as intended.
- Select independent evidence layers needed for the requested claim. An HTTP
  health check can establish observed endpoint behavior without a source
  digest; proving which release is serving requires provenance as well.
- A verification request authorizes inspection. When deployment, rollback,
  DNS, permission, cache purge, or another mutation is already authorized for
  this target, carry it through with an applicable recovery or abort path and
  post-action verification. Reuse valid authorization from the active task.

## Workflow

1. Resolve the target and expected state from the task and current source.
   Record only identifiers needed to bind the observations to that claim.
2. For release provenance, inspect relevant source/build evidence and the
   control plane's active version. A dirty checkout or missing digest can limit
   provenance; it does not prevent checking the live service.
3. Probe the relevant HTTP/API surface. Capture status, useful headers,
   redirects, and a safe response fingerprint; inspect failure bodies without
   storing secrets or unnecessary personal data.
4. Use browser automation when layout, navigation, client code, cookies, or
   console errors affect the claim. Tie the check to the same target and time.
5. For an authorized mutation, prepare the concrete change and recovery method,
   apply it, then observe the resulting state. Ask only for missing
   authorization or information essential to that action, after completing
   useful authorized preparation.
6. Reconcile relevant layers. Investigate conflicts and state what each
   observation supports. An unavailable layer limits its associated claim;
   continue independent checks and report material gaps or residuals.

## Concrete examples

- A green build plus a configured trigger proves build/config state, not that
  the public URL serves the new artifact; perform an HTTP and, when relevant,
  browser check.
- A control plane showing version `N` while HTTP serves an older fingerprint
  leaves live promotion unverified; investigate rollout or cache state and
  report both observations.
- A rollback command alone does not establish recovery; verify the expected
  prior version or configuration and resulting live behavior.

## Supporting reference

Read the relevant sections of [live proof guidance](references/live-proof.md)
for evidence selection, reconciliation, or an authorized production change.

## Output

Report the target, supported conclusion, observations needed to assess it, and
material gaps. Include a next verifier only when a gap remains. A concise result
is sufficient when it establishes the requested behavior.
