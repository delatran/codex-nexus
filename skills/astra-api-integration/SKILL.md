---
name: astra-api-integration
description: Build or debug GPT-6 Astra Responses clients, request schemas, async tool loops, and WebSocket steering.
---

# Astra API integration

Use this skill when building, reviewing, or debugging an actual GPT-6 Astra
Responses API client, request builder, tool loop, WebSocket steering flow, or
structured-output contract. Do not load it for ordinary Codex work, prompt
writing, host configuration, or a task that never constructs an API request.

Build and test locally when live access is unavailable. Use provider calls
within the authorization already established for the task; missing credentials
or optional metadata need not prevent implementation or offline validation.

## Surface boundary

Keep these surfaces separate:

- The Responses API uses model gpt-6-astra and reasoning.effort values low,
  medium, high, xhigh, and max. API effort none is invalid for Astra.
- Codex Ultra is a host intelligence setting. It is not an API effort value.
  Do not translate host Ultra into an API request or silently substitute a
  different model.
- Async tool calling is for direct application-run function or custom tools.
  Hosted tools and programmatic tool calling follow different contracts.
- Mid-turn steering requires the same Responses WebSocket connection. A normal
  HTTP continuation is not a steering event.

## Workflow

Identify the actual surface and current client before changing its contract:
Responses HTTP, Responses WebSocket, or a local adapter. Preserve the requested
model and effort. Implement only the modes needed by the application; async,
steering, and local state envelopes are not prerequisites for a simple request.

- **Request shape:** Use Responses function calling for application functions.
  Use `text.format` for structured response text; choose `json_schema` with
  `strict: true` when exact schema adherence is required. Separate API validity
  from the application's chosen strictness and evidence conventions.
- **Async tools:** Preserve the original `call_id`, execute the tool in the
  application, and return its output in a later Responses request. Use direct
  calls. In multi-agent mode, set `parallel_tool_calls` explicitly to `false`
  with async tools to satisfy their documented incompatibility. Keep enough
  pending-call state to reconcile results with the current task; reuse existing
  IDs/status tracking and add generation or hashes only when needed. Continue
  independent work while a tool runs and wait when a result is needed.
- **Steering:** Send `response.steer` on the same WebSocket with the current
  `previous_response_id`. Its only fields are `type`, `previous_response_id`,
  and `input`; input is a string or a nonempty array of user messages with
  supported content. Track accepted/failed submissions by steer ID. Acceptance
  queues input; keep reading the continuation and return required tool results
  on that connection. Reconcile pending input after disconnect before replay.
  Steering does not undo output or cancel tools that already started.
- **Provider stop:** Recognize `misalignment_policy_violation` and stop
  dispatching actions for the affected conversation. Preserve redacted records
  for operator review and reconcile actions already started. Do not retry or
  reroute the stopped workflow to bypass the stop. The API has no general resume
  mechanism; new local authorization does not override the provider decision.
  For an alert without a confirmed stop, review its referenced request/actions
  and actual application state. Respect the scope of any resulting restriction.

## Exact contract versus recommendation

The [official contract reference](references/official-contracts.md) identifies
provider-defined fields and behavior. Read its relevant sections for the mode
being implemented. Local fields such as goal_generation, connection_id,
input_hash, monitor_state, and
retry policy are Codex Nexus state records; they are not sent to the provider
unless a documented API field accepts them. Do not invent a provider endpoint,
event, retry behavior, or feature switch to make a fixture pass.

## Verification

For changed request builders or tool loops, run relevant success and failure
cases using existing tests where they cover the behavior. Resolve `skill_root`
from this loaded `SKILL.md`; its `scripts/validate_request.py` can check partial
request, steering, and local safety-state shapes without network access.

The helper is a partial validator, not the complete provider schema. A passing
offline check proves only its checked shape and local state conventions.
Run a scoped integration check when authorized and needed; report unavailable
account access, SDK/server verification, or runtime evidence without claiming
that local fixtures prove them.
