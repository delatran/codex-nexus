# GPT-6 Astra API contracts

Initial official documentation snapshot: 2026-09-05. Async, steering, schema
optionality, and monitoring were rechecked on 2026-09-06. Recheck the relevant
current contract when changing those API fields or diagnosing compatibility;
unrelated prose or local refactoring does not require refreshing every source.

## Model and effort

Source: https://developers.openai.com/api/docs/models/gpt-6-astra

The model page identifies gpt-6-astra as the model for difficult end-to-end
work and lists the API reasoning effort values low, medium, high, xhigh, and
max. It also lists Responses support for function calling, structured output,
hosted shell, apply patch, skills, computer use, MCP, and tool search.

Source: https://developers.openai.com/api/docs/guides/reasoning

The reasoning guide states that effort is model-dependent, that Astra does not
support effort none, and that Astra function calling uses the Responses API.
The guide describes max as the setting for the most complex tasks. These are
API facts; Codex host Ultra is a separate host control.

## Structured outputs

Source: https://developers.openai.com/api/docs/guides/structured-outputs

For Responses output schemas, use `text.format` with `type: "json_schema"`,
a schema name, and a JSON Schema object. Set `strict: true` when exact schema
adherence matters. Use function calling for application functionality and
`text.format` when response text needs structure.

The [official Python SDK type](https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/responses/response_format_text_json_schema_config_param.py)
marks `strict` as optional and nullable; `false` or omission is not itself an
invalid API shape. Strict adherence is an application requirement when selected,
and only the provider-supported JSON Schema subset is accepted in strict mode.

The exact provider fields above are distinct from local evidence fields such
as source_hash or verifier_status. Keep those fields in the local receipt.

## Unsupported generation and cache fields

Source: https://developers.openai.com/api/docs/guides/latest-model?model=gpt-6-astra

Remove `temperature`, `top_p`, and `top_logprobs` from Astra requests. For a
Responses request, do not include `message.output_text.logprobs` in `include`.
Replace the retired `prompt_cache_retention` field with
`prompt_cache_options.ttl`, using a value supported by the current prompt
caching contract. The validator checks these migration boundaries by field
presence and does not claim to validate the complete cache-options schema.

## Async tool calling

Source: https://developers.openai.com/api/docs/guides/async-tool-calling

Set async true only on an application-run function or custom tool. The
application still executes the work. When it finishes, return a
function_call_output or custom_tool_call_output in a later Responses request
with the original call_id. Async tools use direct calls; hosted built-in tools
and programmatic tool calling are excluded. The guide also says not to combine
async tools with parallel tool calls in multi-agent mode. Set
`parallel_tool_calls: false` explicitly in that mode; the local validator
requires this value without claiming an omitted provider default. These
incompatibilities apply to the API combination, not all host parallel work.

## Mid-turn steering

Source: https://developers.openai.com/api/docs/guides/steering
Reference: https://developers.openai.com/api/reference/cli/resources/beta/subresources/responses

Steering is available for Astra over a Responses WebSocket. Submit
response.steer on the same connection with previous_response_id. Record the
steer ID and failures. Acceptance queues the input; keep reading for its
continuation. Return any required tool results on the same connection without
resubmitting accepted steering. Steering does not rewrite output already delivered,
undo earlier actions, or cancel tools that already started. Pending steering
input is connection-scoped and must be reconciled after disconnect. The event
accepts only type, previous_response_id, and input. Input is a string or a
nonempty array of user messages. Steering messages use only the user role;
each message may contain type, role, and content, with content as a string or
input_text, input_image, and input_file parts.

## Configuration updates

Source: https://developers.openai.com/api/docs/guides/reasoning#change-reasoning-mid-conversation

To change reasoning effort between Responses turns, add this exact input item
before the next user message while keeping the request-level
`reasoning.effort` unchanged:

```json
{
  "type": "configuration_update",
  "reasoning": {
    "effort": "high"
  }
}
```

The update changes only effort. It is supported for GPT-6 Astra in standard,
single-agent mode. It is incompatible with pro reasoning mode, multi-agent
mode, adjacent configuration updates, automatic compaction, and automatic
truncation. Do not place two updates next to each other in replayed history.
The standalone compaction endpoint also rejects histories containing these
updates. An explicit `compaction_trigger` can be used when the flow supports
it; after compaction, send a fresh configuration update.

Preserve updates through `previous_response_id` or their original positions
in replayed history. The response's `reasoning.effort` still reports the
request-level value, so it is not evidence of the effective updated effort.

The validator checks the exact item shape and only the input array and request
flags supplied to it. It cannot prove compatibility with omitted or
server-side conversation history.

## Misalignment monitoring

Source: https://developers.openai.com/api/docs/guides/safety-checks/misalignment-monitoring

The provider reviews consequential contexts asynchronously and can stop a
conversation. An alert identifies work to review; it does not establish user
misconduct, prove that execution stopped, or undo completed actions. Reconcile
the referenced request/response IDs and application tool records.

Handle the API error code `misalignment_policy_violation`, including errors
received after streaming starts. Stop dispatching actions for that affected
conversation, preserve appropriate redacted records, and show the error to its
responsible user/operator. Do not automatically retry the blocked workflow.
The provider documents no general way to resume a stopped conversation.

Codex Nexus uses `review_required` for a local review state and
`monitor_stopped` for a confirmed provider stop. They are local records, not
provider switches. Human review can inform an allowed next action but cannot
clear a provider or host block. Keep unrelated work governed by its own scope;
never use a new task, transport, or request to evade the stopped workflow.

## Host boundary

This skill is API-client specific. It does not configure, inspect, or mutate
the Codex App or Codex CLI host. Host model, effort, skills, rules, and
permissions belong to the active host's configuration and governing
instructions. API compatibility rules do not impose host-wide restrictions on
delegation or effort; host labels do not create new API fields or permissions.

## Validator boundary

`scripts/validate_request.py` is a deterministic partial shape validator. It
does not reproduce the complete provider schema, SDK validation, transport,
authentication, or server behavior. Its explicit-false rule for
multi-agent async tools is a local safety decision derived from the documented
prohibition, not a claim about an omitted provider default. Its configuration
update checks cover only the request and input items supplied to the helper;
they cannot inspect or prove server-side history, automatic truncation state,
or an account's rollout configuration.
