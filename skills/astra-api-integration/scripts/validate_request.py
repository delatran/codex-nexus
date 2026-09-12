"""Partial, pure validation for GPT-6 Astra Responses request contracts.

This helper never imports an SDK, reads an API key, opens a socket, or calls a
provider. It checks only documented request fields plus local state-policy
envelopes used to prevent stale results and retry-after-stop mistakes.

It is deliberately a partial shape validator, not a generated or complete
provider JSON Schema validator. Server and SDK validation remain necessary.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping


MODEL = "gpt-6-astra"
SUPPORTED_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})
ASYNC_TOOL_TYPES = frozenset({"function", "custom"})
STEERING_EVENT_KEYS = frozenset({"type", "previous_response_id", "input"})
STEERING_MESSAGE_KEYS = frozenset({"type", "role", "content"})
STEERING_CONTENT_TYPES = frozenset({"input_text", "input_image", "input_file"})
CONFIGURATION_UPDATE_KEYS = frozenset({"type", "reasoning"})
CONFIGURATION_UPDATE_REASONING_KEYS = frozenset({"effort"})
UNSUPPORTED_REQUEST_FIELDS = {
    "temperature": "is not supported by GPT-6 Astra; remove it",
    "top_p": "is not supported by GPT-6 Astra; remove it",
    "top_logprobs": "is not supported by GPT-6 Astra; remove it",
    "prompt_cache_retention": "is not supported by GPT-6 Astra; use prompt_cache_options.ttl",
}
UNSUPPORTED_INCLUDE_ITEM = "message.output_text.logprobs"


def _error(errors: list[dict[str, str]], path: str, message: str) -> None:
    errors.append({"path": path, "message": message})


def _nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_schema_format(value: object, errors: list[dict[str, str]]) -> None:
    if not isinstance(value, Mapping):
        _error(errors, "$.text.format", "must be an object")
        return
    schema_type = value.get("type")
    if schema_type != "json_schema":
        return
    if not _nonempty_string(value.get("name")):
        _error(errors, "$.text.format.name", "is required for json_schema")
    strict = value.get("strict")
    if strict is not None and type(strict) is not bool:
        _error(errors, "$.text.format.strict", "must be a boolean or null when supplied")
    if not isinstance(value.get("schema"), Mapping):
        _error(errors, "$.text.format.schema", "must be a JSON Schema object")


def _validate_configuration_update(
    item: Mapping[object, object],
    path: str,
    errors: list[dict[str, str]],
) -> None:
    """Validate the documented, deliberately narrow update input item."""

    for key in item:
        if not isinstance(key, str) or key not in CONFIGURATION_UPDATE_KEYS:
            _error(errors, f"{path}.{key}", "is not supported on configuration_update")
    if item.get("type") != "configuration_update":
        _error(errors, f"{path}.type", "must be configuration_update")

    reasoning = item.get("reasoning")
    if not isinstance(reasoning, Mapping):
        _error(errors, f"{path}.reasoning", "must be an object with effort")
        return
    for key in reasoning:
        if not isinstance(key, str) or key not in CONFIGURATION_UPDATE_REASONING_KEYS:
            _error(errors, f"{path}.reasoning.{key}", "only effort can be updated")
    effort = reasoning.get("effort")
    if not isinstance(effort, str) or effort not in SUPPORTED_EFFORTS:
        _error(
            errors,
            f"{path}.reasoning.effort",
            "must be one of low, medium, high, xhigh, or max",
        )


def _validate_configuration_updates(
    input_value: object,
    errors: list[dict[str, str]],
    *,
    multi_agent: bool,
    pro: bool,
    automatic_compaction: bool,
    automatic_truncation: bool,
) -> None:
    """Validate visible configuration-update compatibility constraints.

    Conversation history outside this request is intentionally out of scope.
    The adjacent-item check therefore covers only the supplied input array.
    """

    if not isinstance(input_value, list):
        return

    update_indices: list[int] = []
    for index, item in enumerate(input_value):
        if isinstance(item, Mapping) and item.get("type") == "configuration_update":
            update_indices.append(index)
            _validate_configuration_update(item, f"$.input[{index}]", errors)

    if not update_indices:
        return

    if multi_agent:
        _error(
            errors,
            "$.input",
            "configuration_update is supported only in standard single-agent mode",
        )
    if pro:
        _error(
            errors,
            "$.input",
            "configuration_update is not supported in pro reasoning mode",
        )
    if automatic_compaction:
        _error(
            errors,
            "$.input",
            "configuration_update cannot be combined with automatic compaction",
        )
    if automatic_truncation:
        _error(
            errors,
            "$.input",
            "configuration_update cannot be combined with automatic truncation",
        )

    for left, right in zip(update_indices, update_indices[1:]):
        if right == left + 1:
            _error(
                errors,
                f"$.input[{right}]",
                "configuration_update items cannot be adjacent",
            )


def validate_response_request(
    request: Mapping[str, Any],
    *,
    multi_agent: bool = False,
    pro: bool = False,
    automatic_compaction: bool = False,
    automatic_truncation: bool = False,
) -> list[dict[str, str]]:
    """Return deterministic contract errors for one Responses request."""

    errors: list[dict[str, str]] = []
    if not isinstance(request, Mapping):
        return [{"path": "$", "message": "request must be a JSON object"}]

    if request.get("model") != MODEL:
        _error(errors, "$.model", "must be gpt-6-astra")

    for field, message in UNSUPPORTED_REQUEST_FIELDS.items():
        if field in request:
            _error(errors, f"$.{field}", message)

    include = request.get("include")
    if isinstance(include, list):
        for index, value in enumerate(include):
            if value == UNSUPPORTED_INCLUDE_ITEM:
                _error(
                    errors,
                    f"$.include[{index}]",
                    "is not supported by GPT-6 Astra; remove it",
                )
    elif include == UNSUPPORTED_INCLUDE_ITEM:
        _error(errors, "$.include", "is not supported by GPT-6 Astra; remove it")

    if "reasoning_effort" in request:
        _error(
            errors,
            "$.reasoning_effort",
            "is not the Responses field; use reasoning.effort",
        )

    reasoning = request.get("reasoning")
    request_is_pro = pro
    if reasoning is not None:
        if not isinstance(reasoning, Mapping):
            _error(errors, "$.reasoning", "must be an object")
        else:
            request_is_pro = request_is_pro or reasoning.get("mode") == "pro"
            if "effort" in reasoning:
                effort = reasoning.get("effort")
                if not isinstance(effort, str) or effort not in SUPPORTED_EFFORTS:
                    _error(
                        errors,
                        "$.reasoning.effort",
                        "must be one of low, medium, high, xhigh, or max",
                    )

    if "response_format" in request:
        _error(
            errors,
            "$.response_format",
            "is not the Responses structured-output field; use text.format",
        )

    text = request.get("text")
    if text is not None:
        if not isinstance(text, Mapping):
            _error(errors, "$.text", "must be an object")
        elif "format" in text:
            _validate_schema_format(text["format"], errors)

    tools = request.get("tools")
    async_tools: list[int] = []
    if tools is not None:
        if not isinstance(tools, list):
            _error(errors, "$.tools", "must be an array")
        else:
            for index, tool in enumerate(tools):
                path = f"$.tools[{index}]"
                if not isinstance(tool, Mapping):
                    _error(errors, path, "must be an object")
                    continue
                tool_type = tool.get("type")
                if tool.get("async") is True:
                    if not isinstance(tool_type, str) or tool_type not in ASYNC_TOOL_TYPES:
                        _error(
                            errors,
                            f"{path}.async",
                            "async is supported only for function and custom tools",
                        )
                    else:
                        async_tools.append(index)
                    callers = tool.get("allowed_callers")
                    if isinstance(callers, list) and "programmatic" in callers:
                        _error(
                            errors,
                            f"{path}.allowed_callers",
                            "async tools support direct calls only; remove the programmatic caller",
                        )
                if tool_type == "function" and not _nonempty_string(tool.get("name")):
                    _error(errors, f"{path}.name", "is required for a function tool")

    if multi_agent and async_tools and request.get("parallel_tool_calls") is not False:
        _error(
            errors,
            "$.parallel_tool_calls",
            "must be explicitly false with async tools in multi-agent mode; the provider guide forbids combining them and does not define an omitted default",
        )

    request_uses_automatic_compaction = automatic_compaction
    context_management = request.get("context_management")
    if isinstance(context_management, list):
        request_uses_automatic_compaction = request_uses_automatic_compaction or any(
            isinstance(item, Mapping) and item.get("type") == "compaction"
            for item in context_management
        )
    request_uses_automatic_truncation = automatic_truncation or request.get("truncation") == "auto"
    _validate_configuration_updates(
        request.get("input"),
        errors,
        multi_agent=multi_agent,
        pro=request_is_pro,
        automatic_compaction=request_uses_automatic_compaction,
        automatic_truncation=request_uses_automatic_truncation,
    )

    return errors


def _validate_steering_content(
    content: object,
    path: str,
    errors: list[dict[str, str]],
) -> None:
    if isinstance(content, str):
        return
    if not isinstance(content, list):
        _error(errors, path, "must be a string or an array of supported input parts")
        return
    for index, part in enumerate(content):
        part_path = f"{path}[{index}]"
        if not isinstance(part, Mapping):
            _error(errors, part_path, "must be an object")
            continue
        part_type = part.get("type")
        if not isinstance(part_type, str) or part_type not in STEERING_CONTENT_TYPES:
            _error(
                errors,
                f"{part_path}.type",
                "must be input_text, input_image, or input_file",
            )
            continue
        if part_type == "input_text":
            if not isinstance(part.get("text"), str):
                _error(errors, f"{part_path}.text", "is required for input_text")
        elif part_type == "input_image":
            if not any(
                _nonempty_string(part.get(field)) for field in ("file_id", "image_url")
            ):
                _error(
                    errors,
                    part_path,
                    "input_image needs a nonempty file_id or image_url",
                )
        elif not any(
            _nonempty_string(part.get(field))
            for field in ("file_id", "file_url", "file_data")
        ):
            _error(
                errors,
                part_path,
                "input_file needs a nonempty file_id, file_url, or file_data",
            )


def _validate_steering_message(
    message: object,
    path: str,
    errors: list[dict[str, str]],
) -> None:
    if not isinstance(message, Mapping):
        _error(errors, path, "must be a user message object")
        return
    for key in message:
        if not isinstance(key, str) or key not in STEERING_MESSAGE_KEYS:
            _error(errors, f"{path}.{key}", "is not supported on a steering user message")
    if "type" in message and message.get("type") != "message":
        _error(errors, f"{path}.type", "must be message when present")
    if message.get("role") != "user":
        _error(errors, f"{path}.role", "must be user")
    if "content" not in message:
        _error(errors, f"{path}.content", "is required")
    else:
        _validate_steering_content(message.get("content"), f"{path}.content", errors)


def validate_steering_event(event: Mapping[str, Any]) -> list[dict[str, str]]:
    """Validate the documented partial shape of one response.steer event."""

    errors: list[dict[str, str]] = []
    if not isinstance(event, Mapping):
        return [{"path": "$", "message": "steering event must be a JSON object"}]
    for key in event:
        if not isinstance(key, str) or key not in STEERING_EVENT_KEYS:
            _error(errors, f"$.{key}", "is not supported on response.steer")
    if event.get("type") != "response.steer":
        _error(errors, "$.type", "must be response.steer")
    if not _nonempty_string(event.get("previous_response_id")):
        _error(errors, "$.previous_response_id", "is required")
    if "input" not in event:
        _error(errors, "$.input", "is required")
    elif isinstance(event["input"], str):
        pass
    elif isinstance(event["input"], list):
        if not event["input"]:
            _error(errors, "$.input", "must be a nonempty array of user messages")
        else:
            for index, message in enumerate(event["input"]):
                _validate_steering_message(message, f"$.input[{index}]", errors)
    else:
        _error(
            errors,
            "$.input",
            "must be a string or a nonempty array of user messages",
        )
    return errors


def validate_safety_state(state: Mapping[str, Any]) -> list[dict[str, str]]:
    """Validate local retry policy after a provider safety signal."""

    errors: list[dict[str, str]] = []
    if not isinstance(state, Mapping):
        return [{"path": "$", "message": "safety state must be a JSON object"}]
    status = state.get("monitor_state", "clear")
    if not isinstance(status, str) or status not in {"clear", "review_required", "monitor_stopped"}:
        _error(errors, "$.monitor_state", "must be clear, review_required, or monitor_stopped")
    if isinstance(status, str) and status in {"review_required", "monitor_stopped"}:
        retry_count = state.get("retry_count", 0)
        if state.get("retry") is True or retry_count not in (0, None):
            _error(
                errors,
                "$.retry",
                "automatic retry is forbidden for a provider-stopped conversation"
                if status == "monitor_stopped"
                else "automatic retry must wait while the affected workflow requires review",
            )
        if state.get("consequential_action") is True:
            _error(
                errors,
                "$.consequential_action",
                "stop further actions for the affected conversation; the provider offers no general resume mechanism"
                if status == "monitor_stopped"
                else "requires review of the affected action before dispatch",
            )
    return errors


def _read_json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _result(errors: list[dict[str, str]], checks: list[str]) -> dict[str, Any]:
    return {
        "ok": not errors,
        "checks": checks,
        "errors": errors,
        "network_calls": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate Astra Responses request and local safety envelopes without network access."
    )
    parser.add_argument("request", type=Path, nargs="?")
    parser.add_argument("--multi-agent", action="store_true")
    parser.add_argument("--steering", type=Path)
    parser.add_argument("--safety-state", type=Path)
    args = parser.parse_args(argv)

    if args.request is None and args.steering is None and args.safety_state is None:
        parser.error("provide a request, --steering, or --safety-state")

    errors: list[dict[str, str]] = []
    checks: list[str] = []
    try:
        if args.request is not None:
            errors.extend(validate_response_request(_read_json(args.request), multi_agent=args.multi_agent))
            checks.append("responses_request")
        if args.steering is not None:
            errors.extend(validate_steering_event(_read_json(args.steering)))
            checks.append("steering_event")
        if args.safety_state is not None:
            errors.extend(validate_safety_state(_read_json(args.safety_state)))
            checks.append("safety_state")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        errors.append({"path": "$", "message": str(exc)})

    print(json.dumps(_result(errors, checks), ensure_ascii=False, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
