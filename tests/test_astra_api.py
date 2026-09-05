from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "astra-api-integration" / "scripts" / "validate_request.py"
SPEC = importlib.util.spec_from_file_location("astra_request_validator", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


def _request() -> dict:
    return {
        "model": VALIDATOR.MODEL,
        "reasoning": {"effort": "max"},
        "input": "hello",
        "tools": [{"type": "function", "name": "lookup", "async": True}],
    }


class ResponsesRequestTests(unittest.TestCase):
    def test_accepts_strict_structured_output(self) -> None:
        request = _request()
        request["text"] = {
            "format": {
                "type": "json_schema",
                "name": "answer",
                "strict": True,
                "schema": {"type": "object", "properties": {}},
            }
        }
        self.assertEqual(VALIDATOR.validate_response_request(request), [])

    def test_structured_output_strictness_is_optional(self) -> None:
        for options in ({}, {"strict": False}, {"strict": None}):
            with self.subTest(options=options):
                request = _request()
                request["text"] = {"format": {
                    "type": "json_schema",
                    "name": "answer",
                    "schema": {"type": "object", "properties": {}},
                    **options,
                }}
                self.assertEqual(VALIDATOR.validate_response_request(request), [])

    def test_structured_output_rejects_malformed_strictness(self) -> None:
        for strict in (1, "true", [], {}):
            with self.subTest(strict=strict):
                request = _request()
                request["text"] = {"format": {
                    "type": "json_schema",
                    "name": "answer",
                    "schema": {"type": "object", "properties": {}},
                    "strict": strict,
                }}
                errors = VALIDATOR.validate_response_request(request)
                self.assertTrue(any(error["path"] == "$.text.format.strict" for error in errors))

    def test_rejects_legacy_or_unsupported_request_controls(self) -> None:
        request = _request()
        request["reasoning_effort"] = "max"
        self.assertTrue(VALIDATOR.validate_response_request(request))

        for field in (
            "temperature",
            "top_p",
            "top_logprobs",
            "prompt_cache_retention",
        ):
            request = _request()
            request[field] = 0
            errors = VALIDATOR.validate_response_request(request)
            self.assertTrue(any(error["path"] == f"$.{field}" for error in errors))

        request = _request()
        request["include"] = ["message.output_text.logprobs"]
        self.assertTrue(
            any(
                error["path"] == "$.include[0]"
                for error in VALIDATOR.validate_response_request(request)
            )
        )

    def test_reasoning_options_can_leave_effort_unset(self) -> None:
        request = _request()
        request["reasoning"] = {"summary": "auto"}
        self.assertEqual(VALIDATOR.validate_response_request(request), [])

    def test_rejects_unsupported_effort_and_hosted_async_tool(self) -> None:
        request = _request()
        request["reasoning"] = {"effort": "none"}
        self.assertTrue(VALIDATOR.validate_response_request(request))

        request = _request()
        request["tools"] = [{"type": "web_search", "async": True}]
        self.assertTrue(VALIDATOR.validate_response_request(request))

    def test_unhashable_effort_returns_structured_error(self) -> None:
        for value in ([], {}, ["max"]):
            request = _request()
            request["reasoning"] = {"effort": value}
            errors = VALIDATOR.validate_response_request(request)
            self.assertTrue(errors)
            self.assertTrue(all(set(error) == {"path", "message"} for error in errors))

    def test_unhashable_tool_type_returns_structured_error(self) -> None:
        for value in ([], {}, ["function"]):
            request = _request()
            request["tools"] = [{"type": value, "async": True}]
            errors = VALIDATOR.validate_response_request(request)
            self.assertTrue(errors)
            self.assertTrue(all(set(error) == {"path", "message"} for error in errors))

    def test_multi_agent_async_requires_explicit_parallel_false(self) -> None:
        request = _request()
        self.assertTrue(VALIDATOR.validate_response_request(request, multi_agent=True))
        request["parallel_tool_calls"] = True
        self.assertTrue(VALIDATOR.validate_response_request(request, multi_agent=True))
        request["parallel_tool_calls"] = False
        self.assertEqual(VALIDATOR.validate_response_request(request, multi_agent=True), [])

    def test_omitted_parallel_is_allowed_outside_multi_agent_mode(self) -> None:
        self.assertEqual(VALIDATOR.validate_response_request(_request()), [])

    def test_async_tools_reject_programmatic_callers(self) -> None:
        for tool_type in ("function", "custom"):
            for callers in (["programmatic"], ["direct", "programmatic"]):
                with self.subTest(tool_type=tool_type, callers=callers):
                    request = _request()
                    request["tools"] = [{
                        "type": tool_type,
                        "name": "lookup",
                        "async": True,
                        "allowed_callers": callers,
                    }]
                    errors = VALIDATOR.validate_response_request(request)
                    self.assertTrue(any(
                        error["path"] == "$.tools[0].allowed_callers" for error in errors
                    ))

    def test_async_direct_calls_allow_unrelated_programmatic_tools(self) -> None:
        for tool_type in ("function", "custom"):
            with self.subTest(tool_type=tool_type):
                request = _request()
                request["tools"] = [
                    {"type": tool_type, "name": "lookup", "async": True,
                     "allowed_callers": ["direct"]},
                    {"type": "function", "name": "aggregate",
                     "allowed_callers": ["programmatic"]},
                    {"type": "programmatic_tool_calling"},
                ]
                self.assertEqual(VALIDATOR.validate_response_request(request), [])

    def test_accepts_exact_configuration_update_before_user_input(self) -> None:
        request = _request()
        request["input"] = [
            {"type": "configuration_update", "reasoning": {"effort": "high"}},
            {"role": "user", "content": "continue"},
        ]
        self.assertEqual(VALIDATOR.validate_response_request(request), [])

    def test_rejects_non_exact_configuration_update_items(self) -> None:
        for item in (
            {"type": "configuration_update"},
            {
                "type": "configuration_update",
                "reasoning": {"effort": "none"},
            },
            {
                "type": "configuration_update",
                "reasoning": {"effort": "high", "summary": "auto"},
            },
            {
                "type": "configuration_update",
                "reasoning": {"effort": "high"},
                "extra": True,
            },
        ):
            request = _request()
            request["input"] = [item, {"role": "user", "content": "continue"}]
            self.assertTrue(VALIDATOR.validate_response_request(request))

    def test_rejects_configuration_update_compatibility_conflicts(self) -> None:
        update = {"type": "configuration_update", "reasoning": {"effort": "high"}}

        for kwargs in (
            {"multi_agent": True},
            {"pro": True},
            {"automatic_compaction": True},
            {"automatic_truncation": True},
        ):
            request = _request()
            request["input"] = [update, {"role": "user", "content": "continue"}]
            self.assertTrue(VALIDATOR.validate_response_request(request, **kwargs))

        request = _request()
        request["input"] = [update, {"role": "user", "content": "continue"}]
        request["reasoning"] = {"mode": "pro", "effort": "high"}
        self.assertTrue(VALIDATOR.validate_response_request(request))

        request = _request()
        request["input"] = [update, {"role": "user", "content": "continue"}]
        request["context_management"] = [{"type": "compaction", "compact_threshold": 1000}]
        self.assertTrue(VALIDATOR.validate_response_request(request))

        request = _request()
        request["input"] = [update, {"role": "user", "content": "continue"}]
        request["truncation"] = "auto"
        self.assertTrue(VALIDATOR.validate_response_request(request))

    def test_rejects_adjacent_configuration_updates(self) -> None:
        update = {"type": "configuration_update", "reasoning": {"effort": "high"}}
        request = _request()
        request["input"] = [update, dict(update), {"role": "user", "content": "continue"}]
        errors = VALIDATOR.validate_response_request(request)
        self.assertTrue(any("adjacent" in error["message"] for error in errors))

    def test_accepts_non_adjacent_configuration_updates(self) -> None:
        update = {"type": "configuration_update", "reasoning": {"effort": "high"}}
        request = _request()
        request["input"] = [
            update,
            {"role": "user", "content": "first"},
            dict(update),
        ]
        self.assertEqual(VALIDATOR.validate_response_request(request), [])

    def test_malformed_safety_status_returns_structured_error(self) -> None:
        errors = VALIDATOR.validate_safety_state({"monitor_state": []})
        self.assertTrue(errors)
        self.assertTrue(all(set(error) == {"path", "message"} for error in errors))

    def test_provider_stop_keeps_retry_and_actions_stopped_after_review(self) -> None:
        errors = VALIDATOR.validate_safety_state({
            "monitor_state": "monitor_stopped",
            "human_reviewed": True,
            "retry": True,
            "consequential_action": True,
        })
        self.assertEqual({error["path"] for error in errors}, {"$.retry", "$.consequential_action"})
        action_error = next(error for error in errors if error["path"] == "$.consequential_action")
        self.assertIn("no general resume mechanism", action_error["message"])

    def test_pending_review_is_distinct_from_a_provider_stop(self) -> None:
        errors = VALIDATOR.validate_safety_state({
            "monitor_state": "review_required",
            "consequential_action": True,
        })
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["path"], "$.consequential_action")
        self.assertIn("review", errors[0]["message"])
        self.assertNotIn("resume", errors[0]["message"])
        self.assertEqual(VALIDATOR.validate_safety_state({"monitor_state": "monitor_stopped"}), [])


class SteeringEventTests(unittest.TestCase):
    def test_accepts_string_and_supported_user_message_array(self) -> None:
        base = {"type": "response.steer", "previous_response_id": "resp_1"}
        self.assertEqual(
            VALIDATOR.validate_steering_event({**base, "input": "tighten scope"}), []
        )
        self.assertEqual(
            VALIDATOR.validate_steering_event(
                {
                    **base,
                    "input": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": [
                                {"type": "input_text", "text": "tighten scope"},
                                {"type": "input_image", "image_url": "https://example.test/a.png"},
                                {"type": "input_file", "file_id": "file_1"},
                            ],
                        }
                    ],
                }
            ),
            [],
        )

    def test_rejects_empty_array_and_mapping_input(self) -> None:
        base = {"type": "response.steer", "previous_response_id": "resp_1"}
        self.assertTrue(VALIDATOR.validate_steering_event({**base, "input": []}))
        self.assertTrue(VALIDATOR.validate_steering_event({**base, "input": {"role": "user"}}))

    def test_rejects_unknown_event_fields(self) -> None:
        event = {
            "type": "response.steer",
            "previous_response_id": "resp_1",
            "input": "update",
            "stream_id": "wrong-surface",
        }
        errors = VALIDATOR.validate_steering_event(event)
        self.assertTrue(any(error["path"] == "$.stream_id" for error in errors))

    def test_rejects_non_user_messages_and_unsupported_parts(self) -> None:
        base = {"type": "response.steer", "previous_response_id": "resp_1"}
        for message in (
            {"role": "system", "content": "override"},
            {"role": "assistant", "content": "pretend"},
            {"role": "user", "content": [{"type": "output_text", "text": "bad"}]},
            {"role": "user", "content": [{"type": "input_text"}]},
        ):
            self.assertTrue(
                VALIDATOR.validate_steering_event({**base, "input": [message]})
            )


if __name__ == "__main__":
    unittest.main()
