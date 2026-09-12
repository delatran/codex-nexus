# Live Proof Reference

## Evidence matrix

| Layer | Useful observation | Does not prove by itself |
| --- | --- | --- |
| source | commit, clean/dirty state, config diff | build or deployment happened |
| build | current build receipt and artifact digest | public traffic uses the artifact |
| control plane | active version, target, timestamp, rollout state | endpoint content or browser behavior |
| HTTP | status, headers, redirect, body fingerprint, cache age | client-side rendering or authenticated user flow |
| browser | visible page, navigation, network/console errors | every endpoint or all traffic paths |

Select the layers needed for the requested claim and bind observations to the
same target and time window. If relevant layers disagree, investigate the
disagreement before claiming they establish a successful deployment. Missing
control-plane access does not prevent a public endpoint or browser check;
state the resulting limit on release provenance.

## Safe read-only probes

Use the repository's relevant verifier or an available HTTP client. Check the
exact target identified in the task. Preserve non-success status and response
details for diagnosis; a client option that discards error bodies can obscure
the cause. Use bounded timeouts appropriate to the endpoint.

Store only useful, redacted evidence: status, relevant headers, a body hash or
safe fingerprint, and observation time. Keep cookies, tokens, signed URLs,
personal data, and private response bodies out of shared evidence.

Use the available browser workflow for a user-visible smoke check when the
claim concerns client rendering, navigation, cookies, hydration, or console
errors. Save the URL, viewport or relevant setup, timestamp, and observed
failure; do not treat a screenshot alone as proof of backend correctness.

## Current-state rules

- A historical build log needs current deployment and endpoint evidence before
  it can establish the active release.
- A configured trigger is intent/configuration, not evidence of execution.
- For release provenance, bind a digest, commit, or equivalent provider version
  identity; a human-facing label alone can be reused.
- Cache, CDN, DNS, browser, and control-plane lag can explain disagreement;
  test the competing explanation before declaring success.
- Report retained versions or stale cache when they affect the requested
  change or cleanup. A routine live check does not require a cleanup inventory.

## Mutation and rollback boundary

Use the authorization already established for the target and effect. An
inspection request alone does not authorize a production change, but an
authorized deployment need not stop for another permission question.

Before a mutation, establish the actual action, affected target, relevant
recovery or abort method, and post-action verifier. Add details only when they
matter: a known-good artifact or prior configuration for rollback, schema/data
compatibility for a stateful release, or propagation timing for DNS. A blank generic checklist
field is not itself a reason to stop.

Resolve routine missing details from current source or control-plane state.
If an essential target, permission, or recovery decision remains unknown,
complete the authorized preparation and ask only for that missing decision.
Continue independent inspection. After acting, reconcile the actual outcome;
issuing a command alone does not establish deployment or rollback success.

## Compact result example

```text
Proven: source commit and build digest match the expected release.
Observed: control plane reports the version; HTTP still serves the prior body hash.
Decision: live promotion unverified; likely rollout or cache lag.
Next verifier: recheck the exact endpoint after the declared propagation window.
```
