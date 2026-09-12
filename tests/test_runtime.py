from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from nexus import runtime


ROOT = Path(__file__).resolve().parents[1]
FEATURE_NAMES = (
    "apps",
    "fast_mode",
    "memories",
    "multi_agent_v2",
    "prevent_idle_sleep",
    "shell_tool",
    "shell_zsh_fork",
    "unified_exec",
    "context_management",
    "network_proxy",
)
STABLE_FEATURE_NAMES = ("apps", "fast_mode", "memories", "multi_agent_v2", "shell_tool", "unified_exec")
OPTIONAL_FEATURE_NAMES = ("code_mode", "rollout_budget", "transcript_v2")
COMPATIBILITY_EXCEPTION_FEATURE = "shell_zsh_fork"


def _config() -> dict:
    import tomllib

    with (ROOT / ".codex" / "config.toml").open("rb") as handle:
        return tomllib.load(handle)


def _optional_rollout_config() -> dict:
    config = _config()
    config["features"]["rollout_budget"] = {
        "enabled": True,
        "limit_tokens": 1000000000,
        "reminder_at_remaining_tokens": [1000000],
    }
    return config


def _catalog(
    *,
    slug: str = runtime.MODEL_SLUG,
    default_context: int = 272000,
    max_context: int = 872000,
) -> dict:
    return {
        "models": [
            {
                "slug": slug,
                "context_window": default_context,
                "max_context_window": max_context,
                "effective_context_window_percent": 95,
                "supported_reasoning_levels": [
                    {"effort": effort} for effort in runtime.SUPPORTED_EFFORTS
                ],
            }
        ]
    }


def _features(*, disabled: str | None = None, malformed: object = None) -> dict:
    result: dict[str, object] = {
        name: {
            "stage": "stable" if name in STABLE_FEATURE_NAMES else "experimental",
            "enabled": name != disabled and name != COMPATIBILITY_EXCEPTION_FEATURE,
        }
        for name in FEATURE_NAMES + OPTIONAL_FEATURE_NAMES
    }
    if malformed is not None:
        result["unified_exec"] = {"stage": "stable", "enabled": malformed}
    return {"features": result}


class RuntimeValidationTests(unittest.TestCase):
    def test_valid_fixture_passes_without_external_calls(self) -> None:
        report = runtime.validate_runtime(_config(), _catalog(), _features())
        self.assertTrue(report["ok"], report["errors"])
        self.assertFalse(report["external_calls"])
        self.assertEqual(report["selected"]["lead_effort"], "ultra")
        self.assertEqual(report["selected"]["max_concurrent_threads_per_session"], 1000000)
        self.assertTrue(report["selected"]["context_management_experimental_mode"])
        self.assertEqual(report["observed"]["context"]["max_context_window"], 872000)

    def test_thread_ceiling_accepts_non_six_positive_integer_and_reports_it(self) -> None:
        config = _config()
        config["agents"]["max_concurrent_threads_per_session"] = 7
        report = runtime.validate_runtime(config, _catalog(), _features())
        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(report["selected"]["max_concurrent_threads_per_session"], 7)

    def test_thread_ceiling_rejects_non_positive_or_non_integer_values(self) -> None:
        for value in (0, -1, True, 1.5, "1000000", None):
            with self.subTest(value=value):
                config = _config()
                config["agents"]["max_concurrent_threads_per_session"] = value
                report = runtime.validate_runtime(config, _catalog(), _features())
                self.assertFalse(report["ok"])
                self.assertIn("config-thread-cap", {item["check"] for item in report["errors"]})

    def test_malformed_configuration_fails_structurally(self) -> None:
        config = _config()
        config["model"] = "unsupported-model"
        report = runtime.validate_runtime(config, _catalog(), _features())
        self.assertFalse(report["ok"])
        self.assertIn("config-model", {item["check"] for item in report["errors"]})

    def test_unavailable_model_fails_without_model_fallback(self) -> None:
        report = runtime.validate_runtime(_config(), _catalog(slug="unsupported-model"), _features())
        self.assertFalse(report["ok"])
        self.assertIn("catalog-primary-present", {item["check"] for item in report["errors"]})

    def test_missing_configured_effort_fails(self) -> None:
        catalog = _catalog()
        catalog["models"][0]["supported_reasoning_levels"] = [{"effort": "low"}]
        report = runtime.validate_runtime(_config(), catalog, _features())
        self.assertFalse(report["ok"])
        self.assertIn("catalog-lead-effort", {item["check"] for item in report["errors"]})

    def test_larger_context_is_observed_without_exact_pin(self) -> None:
        report = runtime.validate_runtime(
            _config(), _catalog(default_context=500000, max_context=1500000), _features()
        )
        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(report["observed"]["context"]["context_window"], 500000)
        self.assertEqual(report["observed"]["context"]["max_context_window"], 1500000)

    def test_disabled_requested_features_fail(self) -> None:
        for name in FEATURE_NAMES:
            if name == COMPATIBILITY_EXCEPTION_FEATURE:
                continue
            with self.subTest(name=name):
                report = runtime.validate_runtime(_config(), _catalog(), _features(disabled=name))
                self.assertFalse(report["ok"])
                self.assertIn(f"feature-{name}", {item["check"] for item in report["errors"]})

    def test_non_boolean_feature_values_fail_without_coercion(self) -> None:
        for malformed in ("false", 1, None):
            with self.subTest(malformed=malformed):
                observed = _features()
                observed["features"]["unified_exec"] = {"stage": "stable", "enabled": malformed}
                report = runtime.validate_runtime(_config(), _catalog(), observed)
                self.assertFalse(report["ok"])
                self.assertIn("feature-unified_exec-shape", {item["check"] for item in report["errors"]})

    def test_context_management_catalog_is_flat_while_config_is_nested(self) -> None:
        report = runtime.validate_runtime(_config(), _catalog(), _features())
        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(report["observed"]["features"]["context_management"]["stage"], "experimental")

    def test_context_management_requires_an_enabled_catalog_feature(self) -> None:
        observed = _features()
        del observed["features"]["context_management"]
        report = runtime.validate_runtime(_config(), _catalog(), observed)
        self.assertFalse(report["ok"])
        self.assertIn("feature-context_management", {item["check"] for item in report["errors"]})

    def test_context_management_config_rejects_malformed_values(self) -> None:
        for value in ("true", 1, None):
            with self.subTest(value=value):
                config = _config()
                config["features"]["context_management"]["experimental_mode"] = value
                report = runtime.validate_runtime(config, _catalog(), _features())
                self.assertFalse(report["ok"])
                self.assertIn(
                    "config-feature-context-management-experimental-mode",
                    {item["check"] for item in report["errors"]},
                )

    def test_context_management_config_rejects_unknown_nested_keys(self) -> None:
        config = _config()
        config["features"]["context_management"]["unused"] = True
        report = runtime.validate_runtime(config, _catalog(), _features())
        self.assertFalse(report["ok"])
        self.assertIn(
            "config-feature-context-management-unknown-keys",
            {item["check"] for item in report["errors"]},
        )

    def test_context_management_can_be_explicitly_disabled(self) -> None:
        config = _config()
        config["features"]["context_management"]["experimental_mode"] = False
        report = runtime.validate_runtime(config, _catalog(), _features(disabled="context_management"))
        self.assertTrue(report["ok"], report["errors"])
        self.assertFalse(report["selected"]["context_management_experimental_mode"])

    def test_rollout_budget_is_optional_and_accepts_an_explicit_native_table(self) -> None:
        self.assertNotIn("rollout_budget", _config()["features"])
        config = _optional_rollout_config()
        rollout = config["features"]["rollout_budget"]
        self.assertEqual(rollout, {
            "enabled": True,
            "limit_tokens": 1000000000,
            "reminder_at_remaining_tokens": [1000000],
        })
        report = runtime.validate_runtime(config, _catalog(), _features())
        self.assertTrue(report["ok"], report["errors"])

    def test_rollout_budget_rejects_invalid_reminder_values(self) -> None:
        for value in ([0], [-1], [1000000000], ["1000000"], [True]):
            with self.subTest(value=value):
                config = _optional_rollout_config()
                config["features"]["rollout_budget"]["reminder_at_remaining_tokens"] = value
                report = runtime.validate_runtime(config, _catalog(), _features())
                self.assertFalse(report["ok"])
                self.assertIn(
                    "config-feature-rollout-budget-reminders",
                    {item["check"] for item in report["errors"]},
                )

    def test_rollout_budget_uses_signed_i64_bounds(self) -> None:
        config = _optional_rollout_config()
        rollout = config["features"]["rollout_budget"]
        rollout["limit_tokens"] = runtime._I64_MAX
        rollout["reminder_at_remaining_tokens"] = [runtime._I64_MAX - 1]
        report = runtime.validate_runtime(config, _catalog(), _features())
        self.assertTrue(report["ok"], report["errors"])

        config = _optional_rollout_config()
        rollout = config["features"]["rollout_budget"]
        rollout["limit_tokens"] = runtime._I64_MAX + 1
        report = runtime.validate_runtime(config, _catalog(), _features())
        self.assertFalse(report["ok"])
        self.assertIn(
            "config-feature-rollout-budget-limit-tokens",
            {item["check"] for item in report["errors"]},
        )

        config = _optional_rollout_config()
        rollout = config["features"]["rollout_budget"]
        rollout["limit_tokens"] = runtime._I64_MAX
        rollout["reminder_at_remaining_tokens"] = [runtime._I64_MAX + 1]
        report = runtime.validate_runtime(config, _catalog(), _features())
        self.assertFalse(report["ok"])
        self.assertIn(
            "config-feature-rollout-budget-reminders",
            {item["check"] for item in report["errors"]},
        )

    def test_rollout_budget_requires_limit_and_reminders(self) -> None:
        config = _optional_rollout_config()
        config["features"]["rollout_budget"].pop("limit_tokens")
        report = runtime.validate_runtime(config, _catalog(), _features())
        self.assertFalse(report["ok"])
        self.assertIn(
            "config-feature-rollout-budget-required-keys",
            {item["check"] for item in report["errors"]},
        )

    def test_enabled_feature_tables_accept_a_leaf_activation(self) -> None:
        config = _config()
        config["features"]["code_mode"] = {"enabled": True}
        report = runtime.validate_runtime(config, _catalog(), _features())
        self.assertTrue(report["ok"], report["errors"])

    def test_enabled_feature_tables_reject_unknown_nested_values(self) -> None:
        config = _config()
        config["features"]["code_mode"] = {"enabled": True, "unused": True}
        report = runtime.validate_runtime(config, _catalog(), _features())
        self.assertFalse(report["ok"])
        self.assertIn(
            "config-feature-code_mode-unknown-keys",
            {item["check"] for item in report["errors"]},
        )

    def test_unselected_experimental_advertisement_remains_optional(self) -> None:
        observed = _features()
        observed["features"]["future_experiment"] = {
            "stage": "under development",
            "enabled": True,
        }
        report = runtime.validate_runtime(_config(), _catalog(), observed)
        self.assertTrue(report["ok"], report["errors"])
        self.assertTrue(report["observed"]["features"]["future_experiment"]["enabled"])

    def test_new_boolean_feature_is_validated_by_native_advertisement(self) -> None:
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                config = _config()
                config["features"]["future_selected"] = enabled
                observed = _features()
                report = runtime.validate_runtime(config, _catalog(), observed)
                self.assertFalse(report["ok"])
                self.assertIn("feature-future_selected", {item["check"] for item in report["errors"]})

                observed["features"]["future_selected"] = {
                    "stage": "under development", "enabled": enabled,
                }
                report = runtime.validate_runtime(config, _catalog(), observed)
                self.assertTrue(report["ok"], report["errors"])

    def test_feature_source_rejects_non_boolean_leaf_values(self) -> None:
        for value in (1, "true", [], {"enabled": True}, None):
            with self.subTest(value=value):
                config = _config()
                config["features"]["memories"] = value
                report = runtime.validate_runtime(config, _catalog(), _features())
                self.assertFalse(report["ok"])
                self.assertIn("config-feature-memories", {item["check"] for item in report["errors"]})

    def test_feature_source_rejects_unsafe_toml_keys(self) -> None:
        config = _config()
        config["features"]["nested.enabled"] = True
        report = runtime.validate_runtime(config, _catalog(), _features())
        self.assertFalse(report["ok"])
        self.assertIn("config-feature-name", {item["check"] for item in report["errors"]})
        with self.assertRaisesRegex(ValueError, "unsafe TOML key"):
            runtime._source_override_args(config)

    def test_no_optional_feature_set_is_required(self) -> None:
        config = _config()
        config["features"] = {}
        report = runtime.validate_runtime(config, _catalog(), _features())
        self.assertTrue(report["ok"], report["errors"])

    def test_explicitly_disabled_experimental_feature_must_match_live_state(self) -> None:
        config = _config()
        config["features"]["transcript_v2"] = False
        report = runtime.validate_runtime(config, _catalog(), _features())
        self.assertFalse(report["ok"])
        self.assertIn("feature-transcript_v2", {item["check"] for item in report["errors"]})

        report = runtime.validate_runtime(config, _catalog(), _features(disabled="transcript_v2"))
        self.assertTrue(report["ok"], report["errors"])

    def test_shell_zsh_fork_exception_requires_exact_false_source_value(self) -> None:
        for value in (True, "false", 1, None):
            with self.subTest(value=value):
                config = _config()
                config["features"][COMPATIBILITY_EXCEPTION_FEATURE] = value
                report = runtime.validate_runtime(config, _catalog(), _features())
                self.assertFalse(report["ok"])
                self.assertIn(
                    "config-feature-shell_zsh_fork-compatibility",
                    {item["check"] for item in report["errors"]},
                )

    def test_omitted_shell_zsh_fork_is_not_added_as_a_source_requirement(self) -> None:
        config = _config()
        config["features"].pop(COMPATIBILITY_EXCEPTION_FEATURE)
        report = runtime.validate_runtime(config, _catalog(), _features())
        self.assertTrue(report["ok"], report["errors"])
        self.assertNotIn("features.shell_zsh_fork=false", runtime._source_override_args(config))

    def test_shell_zsh_fork_exception_requires_false_live_state(self) -> None:
        for mode in ("true", "missing", "malformed", "removed"):
            with self.subTest(mode=mode):
                observed = _features()
                if mode == "true":
                    observed["features"][COMPATIBILITY_EXCEPTION_FEATURE] = {
                        "stage": "experimental",
                        "enabled": True,
                    }
                elif mode == "missing":
                    observed["features"].pop(COMPATIBILITY_EXCEPTION_FEATURE)
                elif mode == "removed":
                    observed["features"][COMPATIBILITY_EXCEPTION_FEATURE] = {
                        "stage": "removed",
                        "enabled": False,
                    }
                else:
                    observed["features"][COMPATIBILITY_EXCEPTION_FEATURE] = {
                        "stage": "experimental",
                        "available": True,
                    }
                report = runtime.validate_runtime(_config(), _catalog(), observed)
                self.assertFalse(report["ok"])
                self.assertIn(
                    "feature-shell_zsh_fork-compatibility",
                    {item["check"] for item in report["errors"]},
                )

    def test_unconfigured_stable_advertisement_remains_optional(self) -> None:
        observed = _features()
        observed["features"]["future_stable"] = {"stage": "stable", "enabled": True}
        report = runtime.validate_runtime(_config(), _catalog(), observed)
        self.assertTrue(report["ok"], report["errors"])

    def test_source_override_serializes_rollout_integer_list(self) -> None:
        args = runtime._source_override_args(_optional_rollout_config())
        self.assertIn("features.rollout_budget.reminder_at_remaining_tokens=[1000000]", args)

    def test_source_overrides_do_not_restore_omitted_features(self) -> None:
        args = runtime._source_override_args(_config())
        roots = {item.split("=", 1)[0].split(".")[1] for item in args if item.startswith("features.")}
        self.assertEqual(roots, set(FEATURE_NAMES))
        self.assertFalse(roots & set(OPTIONAL_FEATURE_NAMES))

    def test_available_only_feature_is_not_activation(self) -> None:
        observed = _features()
        observed["features"]["unified_exec"] = {"stage": "stable", "available": True}
        report = runtime.validate_runtime(_config(), _catalog(), observed)
        self.assertFalse(report["ok"])
        self.assertIn("feature-unified_exec-shape", {item["check"] for item in report["errors"]})

    def test_removed_enabled_feature_fails(self) -> None:
        observed = _features()
        observed["features"]["unified_exec"] = {"stage": "removed", "enabled": True}
        report = runtime.validate_runtime(_config(), _catalog(), observed)
        self.assertFalse(report["ok"])
        self.assertIn("feature-unified_exec-stage", {item["check"] for item in report["errors"]})

    def test_deprecated_enabled_feature_warns_but_remains_active(self) -> None:
        observed = _features()
        observed["features"]["unified_exec"] = {"stage": "deprecated", "enabled": True}
        report = runtime.validate_runtime(_config(), _catalog(), observed)
        self.assertTrue(report["ok"], report["errors"])
        self.assertIn("feature-unified_exec-stage", {item["check"] for item in report["warnings"]})

    def test_contradictory_context_is_rejected(self) -> None:
        report = runtime.validate_runtime(
            _config(), _catalog(default_context=872000, max_context=272000), _features()
        )
        self.assertFalse(report["ok"])
        self.assertIn("catalog-context-order", {item["check"] for item in report["errors"]})

    def test_missing_context_is_reported_as_unknown(self) -> None:
        catalog = _catalog()
        catalog["models"][0].pop("context_window")
        catalog["models"][0].pop("max_context_window")
        report = runtime.validate_runtime(_config(), catalog, _features())
        self.assertTrue(report["ok"], report["errors"])
        self.assertNotIn("context_window", report["observed"]["context"])
        self.assertNotIn("max_context_window", report["observed"]["context"])
        self.assertIn("catalog-context-unknown", {item["check"] for item in report["warnings"]})

    def test_experimental_feature_stage_is_observed_not_rejected(self) -> None:
        report = runtime.validate_runtime(_config(), _catalog(), _features())
        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(report["observed"]["features"]["transcript_v2"]["stage"], "experimental")

    def test_absent_unrequested_feature_is_not_a_failure(self) -> None:
        config = _config()
        config["features"].pop("fast_mode")
        observed = _features()
        del observed["features"]["fast_mode"]
        report = runtime.validate_runtime(config, _catalog(), observed)
        self.assertTrue(report["ok"], report["errors"])

    def test_v1_depth_setting_is_rejected_by_native_v2_contract(self) -> None:
        config = _config()
        config["agents"]["max_depth"] = 1
        report = runtime.validate_runtime(config, _catalog(), _features())
        self.assertFalse(report["ok"])
        self.assertIn("config-agents-unknown-keys", {item["check"] for item in report["errors"]})


class ConfigurationValidationTests(unittest.TestCase):
    def _root(self, temp: str, *, config_text: str | None = None) -> Path:
        root = Path(temp).resolve()
        (root / ".codex").mkdir()
        (root / ".codex" / "config.toml").write_text(
            (ROOT / ".codex" / "config.toml").read_text(encoding="utf-8")
            if config_text is None
            else config_text,
            encoding="utf-8",
        )
        return root

    def test_valid_configuration_is_static_and_external_call_free(self) -> None:
        with (
            mock.patch.object(runtime, "_run", side_effect=AssertionError("must not run CLI")),
            mock.patch.object(runtime, "resolve_runtime", side_effect=AssertionError("must not resolve runtime")),
        ):
            report = runtime.validate_configuration(ROOT)
        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(report["mode"], "static_configuration")
        self.assertFalse(report["external_calls"])

    def test_runtime_json_is_not_required(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = self._root(temp)
            self.assertFalse((root / "codex" / "runtime.json").exists())
            report = runtime.validate_configuration(root)
        self.assertTrue(report["ok"], report["errors"])

    def test_changed_model_fails_static_validation(self) -> None:
        config = (ROOT / ".codex" / "config.toml").read_text(encoding="utf-8")
        config = config.replace('model = "gpt-6-astra"', 'model = "unsupported-model"', 1)
        with tempfile.TemporaryDirectory() as temp:
            report = runtime.validate_configuration(self._root(temp, config_text=config))
        self.assertFalse(report["ok"])
        self.assertIn("config-model", {item["check"] for item in report["errors"]})

    def test_changed_safety_setting_fails_static_validation(self) -> None:
        config = (ROOT / ".codex" / "config.toml").read_text(encoding="utf-8")
        config = config.replace('sandbox_mode = "danger-full-access"', 'sandbox_mode = "restricted"', 1)
        with tempfile.TemporaryDirectory() as temp:
            report = runtime.validate_configuration(self._root(temp, config_text=config))
        self.assertFalse(report["ok"])
        self.assertIn("config-sandbox", {item["check"] for item in report["errors"]})

    def test_unknown_config_key_is_rejected(self) -> None:
        config = 'removed_setting = true\n' + (ROOT / ".codex" / "config.toml").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as temp:
            report = runtime.validate_configuration(self._root(temp, config_text=config))
        self.assertFalse(report["ok"])
        self.assertIn("config-unknown-keys", {item["check"] for item in report["errors"]})

    def test_legacy_thread_alias_is_rejected(self) -> None:
        config = (ROOT / ".codex" / "config.toml").read_text(encoding="utf-8")
        config = config.replace("max_concurrent_threads_per_session = 1000000", "max_threads = 6")
        with tempfile.TemporaryDirectory() as temp:
            report = runtime.validate_configuration(self._root(temp, config_text=config))
        self.assertFalse(report["ok"])
        self.assertIn("config-agents-required-keys", {item["check"] for item in report["errors"]})
        self.assertIn("config-agents-unknown-keys", {item["check"] for item in report["errors"]})

    def test_configuration_receipt_is_deterministic_across_processes(self) -> None:
        code = (
            "import json, sys; from nexus.runtime import validate_configuration; "
            "print(json.dumps(validate_configuration(sys.argv[1]), sort_keys=True))"
        )
        outputs = []
        for _ in range(2):
            completed = subprocess.run(
                [sys.executable, "-B", "-c", code, str(ROOT)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            outputs.append(json.loads(completed.stdout))
        self.assertEqual(outputs[0], outputs[1])


class RuntimeResolutionTests(unittest.TestCase):
    def test_local_cli_timeout_is_structured(self) -> None:
        timeout = subprocess.TimeoutExpired(["codex"], runtime.CLI_TIMEOUT_SECONDS, output="partial")
        with mock.patch.object(runtime.subprocess, "run", side_effect=timeout):
            result = runtime._run("codex", "features", "list")
        self.assertTrue(result.timed_out)
        self.assertEqual(result.returncode, 124)
        self.assertIn("partial", result.stdout)

    def test_local_cli_output_is_bounded(self) -> None:
        completed = subprocess.CompletedProcess(
            ["codex"], 0, stdout="x" * (runtime.MAX_CLI_OUTPUT_CHARS + 1), stderr=""
        )
        with mock.patch.object(runtime.subprocess, "run", return_value=completed):
            result = runtime._run("codex", "debug", "models", "--bundled")
        self.assertTrue(result.output_truncated)
        self.assertEqual(len(result.stdout), runtime.MAX_CLI_OUTPUT_CHARS)

    def test_invalid_explicit_override_does_not_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp).resolve()
            (home / "config.toml").write_text(
                '[mcp_servers.node_repl.env]\nCODEX_CLI_PATH = "desktop.exe"\n', encoding="utf-8"
            )
            result = runtime.resolve_runtime(explicit=str(home / "missing.exe"), codex_home=home)
        self.assertIsNone(result.command)
        self.assertEqual(result.source, "unavailable")
        self.assertTrue(result.public_record()["explicit_override"])

    def test_desktop_path_precedes_path_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp).resolve()
            desktop = home / "desktop.exe"
            path = home / "path.exe"
            desktop.touch()
            path.touch()
            (home / "config.toml").write_text(
                f'[mcp_servers.node_repl.env]\nCODEX_CLI_PATH = "{desktop.as_posix()}"\n', encoding="utf-8"
            )
            with mock.patch.object(runtime, "_which", return_value=path):
                result = runtime.resolve_runtime(codex_home=home)
        self.assertEqual(result.source, "desktop_managed")
        self.assertEqual(Path(result.command), desktop.resolve())

    def test_missing_desktop_path_falls_back_to_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp).resolve()
            path = home / "path.exe"
            path.touch()
            (home / "config.toml").write_text(
                '[mcp_servers.node_repl.env]\nCODEX_CLI_PATH = "missing.exe"\n', encoding="utf-8"
            )
            with mock.patch.object(runtime, "_which", return_value=path):
                result = runtime.resolve_runtime(codex_home=home)
        self.assertEqual(result.source, "path")
        self.assertEqual(Path(result.command), path.resolve())

    def test_public_record_does_not_leak_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp).resolve() / "codex.exe"
            path.touch()
            result = runtime.resolve_runtime(explicit=str(path))
            encoded = json.dumps(result.public_record())
        self.assertNotIn(temp, encoded)
        self.assertNotIn("codex.exe", encoded)


class RuntimeInspectionTests(unittest.TestCase):
    def _root(self, temp: str) -> Path:
        root = Path(temp).resolve()
        (root / ".codex").mkdir()
        (root / ".codex" / "config.toml").write_text(
            (ROOT / ".codex" / "config.toml").read_text(encoding="utf-8"), encoding="utf-8"
        )
        return root

    def test_invalid_source_stops_before_cli_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = self._root(temp)
            config = root / ".codex" / "config.toml"
            config.write_text(
                config.read_text(encoding="utf-8").replace(
                    "\n[features]\n", "\nremoved_setting = true\n\n[features]\n", 1
                ),
                encoding="utf-8",
            )
            selected = runtime.Runtime(Path(temp).resolve() / "codex.exe", "explicit", "codex.exe", False, False, False)
            with mock.patch.object(runtime, "_run", side_effect=AssertionError("CLI must not run")) as probe:
                result = runtime.inspect_runtime(root, codex=selected)
        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0]["check"], "configuration-contract")
        self.assertTrue(any(item["check"] == "config-unknown-keys" for item in result["configuration_errors"]))
        probe.assert_not_called()

    def test_source_overrides_bind_capability_probes_to_project_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = self._root(temp)
            config_path = root / ".codex" / "config.toml"
            config_path.write_text(
                config_path.read_text(encoding="utf-8").replace(
                    'model_reasoning_effort = "ultra"', 'model_reasoning_effort = "low"', 1
                ),
                encoding="utf-8",
            )
            selected = runtime.Runtime(Path(temp).resolve() / "codex.exe", "explicit", "codex.exe", False, False, False)
            calls: list[tuple[str, ...]] = []

            def run(_command: str, *args: str, **_kwargs: object) -> runtime.CommandResult:
                calls.append(args)
                if args == ("--version",):
                    return runtime.CommandResult(0, "codex-cli 0.153.4", "")
                if args == ("debug", "models", "--bundled"):
                    return runtime.CommandResult(0, json.dumps(_catalog()), "")
                self.assertEqual(args[-2:], ("features", "list"))
                self.assertIn("-c", args)
                self.assertIn('model_reasoning_effort="low"', args)
                return runtime.CommandResult(0, json.dumps(_features()), "")

            with mock.patch.object(runtime, "_run", side_effect=run):
                result = runtime.inspect_runtime(root, codex=selected)
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["probe_mode"], "bundled_catalog_and_source_features")
        self.assertEqual(calls[1], ("debug", "models", "--bundled"))
        self.assertNotIn("-c", calls[1])
        self.assertEqual(calls[2][-2:], ("features", "list"))
        self.assertIn('model_reasoning_effort="low"', calls[2])
        self.assertNotIn('model_reasoning_effort="ultra"', calls[2])
        self.assertIn("managed configuration", result["external_calls_scope"])
        self.assertIn("not instrumented", result["external_calls_scope"])
        self.assertEqual(calls[0], ("--version",))

    def test_missing_runtime_fails_without_fixture_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = self._root(temp)
            missing = runtime.Runtime(None, "unavailable", None, False, False, False, "missing")
            result = runtime.inspect_runtime(root, codex=missing)
        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0]["check"], "runtime-inspection")

    def test_inspect_runtime_uses_offline_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = self._root(temp)
            result = runtime.inspect_runtime(root, codex={"catalog": _catalog(), "features": _features()})
        self.assertTrue(result["ok"], result["errors"])
        self.assertFalse(result["external_calls"])
        self.assertEqual(result["client_version"]["status"], "not_probed")
        self.assertEqual(result["probe_mode"], "offline_fixture")

    def test_old_client_is_classified_before_catalog_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = self._root(temp)
            selected = runtime.Runtime(Path(temp).resolve() / "codex.exe", "explicit", "codex.exe", False, False, False)
            def run(_command: str, *args: str, **_kwargs: object) -> runtime.CommandResult:
                if args == ("--version",):
                    return runtime.CommandResult(0, "codex-cli 0.144.6", "")
                if args == ("debug", "models", "--bundled"):
                    return runtime.CommandResult(0, json.dumps(_catalog(slug="unsupported-model")), "")
                return runtime.CommandResult(1, "", "invalid type: expected struct AgentRoleToml")

            with mock.patch.object(runtime, "_run", side_effect=run) as mocked:
                result = runtime.inspect_runtime(root, codex=selected)
        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0]["check"], "incompatible-client")
        self.assertEqual(result["client_version"]["status"], "incompatible_client")
        self.assertEqual(result["diagnosis"]["kind"], "config_schema_rejection")
        self.assertEqual(mocked.call_args_list[0].args[1:], ("--version",))
        self.assertEqual(result["local_cli_calls"], ["--version", "debug models --bundled", "features list"])

    def test_supported_client_probe_order_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = self._root(temp)
            selected = runtime.Runtime(Path(temp).resolve() / "codex.exe", "explicit", "codex.exe", False, False, False)

            def run(command: str, *args: str, **kwargs: object) -> runtime.CommandResult:
                if args == ("--version",):
                    return runtime.CommandResult(0, "codex-cli 0.153.4", "")
                if args == ("debug", "models", "--bundled"):
                    return runtime.CommandResult(0, json.dumps(_catalog()), "")
                self.assertEqual(args[-2:], ("features", "list"))
                self.assertIn("-c", args)
                return runtime.CommandResult(0, json.dumps(_features()), "")

            with (
                mock.patch.object(runtime, "_run", side_effect=run),
                mock.patch.object(runtime.sys, "platform", "win32"),
            ):
                result = runtime.inspect_runtime(root, codex=selected)
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["client_version"]["version"], "0.153.4")
        self.assertEqual(result["probe_mode"], "bundled_catalog_and_source_features")
        self.assertEqual(result["local_cli_calls"], ["--version", "debug models --bundled", "features list"])
        self.assertTrue(
            any(
                item["check"] == "feature-shell_zsh_fork-compatibility"
                and "0.153.4" in item["detail"]
                for item in result["warnings"]
            )
        )

    def test_unrecognized_version_is_explicitly_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = self._root(temp)
            selected = runtime.Runtime(Path(temp).resolve() / "codex.exe", "explicit", "codex.exe", False, False, False)

            def run(_command: str, *args: str, **_kwargs: object) -> runtime.CommandResult:
                if args == ("--version",):
                    return runtime.CommandResult(0, "Codex development build", "")
                if args == ("debug", "models", "--bundled"):
                    return runtime.CommandResult(0, json.dumps(_catalog()), "")
                self.assertEqual(args[-2:], ("features", "list"))
                return runtime.CommandResult(0, json.dumps(_features()), "")

            with mock.patch.object(runtime, "_run", side_effect=run):
                result = runtime.inspect_runtime(root, codex=selected)
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["client_version"]["status"], "unrecognized")
        self.assertTrue(any(item["check"] == "client-version" for item in result["warnings"]))

    def test_failed_bundled_probe_reports_attempted_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = self._root(temp)
            selected = runtime.Runtime(Path(temp).resolve() / "codex.exe", "explicit", "codex.exe", False, False, False)
            timeout = runtime.CommandResult(124, "", "timed out", timed_out=True)

            def run(_command: str, *args: str, **_kwargs: object) -> runtime.CommandResult:
                if args == ("--version",):
                    return runtime.CommandResult(0, "codex-cli 0.153.4", "")
                return timeout

            with mock.patch.object(runtime, "_run", side_effect=run):
                result = runtime.inspect_runtime(root, codex=selected)
        self.assertFalse(result["ok"])
        self.assertEqual(result["catalog_source"], "bundled_catalog")
        self.assertEqual(result["local_cli_calls"], ["--version", "debug models --bundled"])


if __name__ == "__main__":
    unittest.main()
