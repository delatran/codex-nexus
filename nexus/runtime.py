"""Select a local Codex client and validate its observed capabilities.

The project TOML is the only runtime intent source.  This module does not
maintain a second policy file, choose a replacement model, or make a model or
API request.  It reports local client advertisements as observations.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping


SCHEMA_VERSION = "codex-nexus/runtime-observation-v2"
MODEL_SLUG = "gpt-6-astra"
SUPPORTED_EFFORTS = ("low", "medium", "high", "xhigh", "max", "ultra")
CLI_TIMEOUT_SECONDS = 20
MAX_CLI_OUTPUT_CHARS = 1_000_000
_STAGES = ("stable", "experimental", "under development", "deprecated", "removed")
_I64_MAX = (1 << 63) - 1
_VERSION_RE = re.compile(r"\bcodex-cli\s+v?(\d+)\.(\d+)(?:\.(\d+))?\b", re.IGNORECASE)

# Only these portable sections are project runtime inputs.  Host-specific
# settings remain outside the project contract.  Native feature availability
# is checked separately for the names explicitly selected in source.
_CONFIG_KEYS = frozenset(
    {
        "model",
        "model_reasoning_effort",
        "plan_mode_reasoning_effort",
        "approval_policy",
        "sandbox_mode",
        "allow_login_shell",
        "web_search",
        "features",
        "agents",
        "apps",
    }
)
_CONFIG_REQUIRED_KEYS = _CONFIG_KEYS
_CONFIG_COMPATIBILITY_EXCEPTION_FEATURE_KEYS = frozenset({"shell_zsh_fork"})
# Boolean feature names are validated by the selected native client.  These
# structured values need additional local type checks when explicitly selected.
_CONFIG_ENABLED_TABLE_KEYS = frozenset(
    {
        "code_mode",
        "current_time_reminder",
        "guardianv2",
        "network_proxy",
        "non_prefixed_mcp_tool_names",
        "token_budget",
    }
)
_CONFIG_ENABLED_TABLE_FIELDS = frozenset({"enabled"})
_CONFIG_CONTEXT_MANAGEMENT_KEYS = frozenset({"experimental_mode"})
_CONFIG_ROLLOUT_BUDGET_KEYS = frozenset(
    {"enabled", "limit_tokens", "reminder_at_remaining_tokens"}
)
_CONFIG_AGENT_KEYS = frozenset(
    {
        "default_subagent_model",
        "default_subagent_reasoning_effort",
        "max_concurrent_threads_per_session",
    }
)
_CONFIG_APP_KEYS = frozenset({"_default"})
_CONFIG_DEFAULT_APP_KEYS = frozenset({"enabled", "destructive_enabled", "open_world_enabled"})

RuntimeSource = Literal["explicit", "desktop_managed", "path", "unavailable"]


@dataclass(frozen=True)
class Runtime:
    """Selected executable plus non-sensitive selection metadata."""

    executable: Path | None
    source: RuntimeSource
    requested: str | None
    desktop_configured: bool
    desktop_available: bool
    path_available: bool
    reason: str | None = None

    @property
    def command(self) -> str | None:
        return str(self.executable) if self.executable is not None else None

    def public_record(self) -> dict[str, object]:
        """Return a machine-path-free record suitable for receipts."""

        return {
            "source": self.source,
            "explicit_override": self.requested is not None,
            "desktop_configured": self.desktop_configured,
            "desktop_available": self.desktop_available,
            "path_available": self.path_available,
            "available": self.executable is not None,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class CommandResult:
    """Bounded local CLI observation."""

    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    output_truncated: bool = False


def _home(codex_home: Path | str | None) -> Path:
    if codex_home is not None:
        return Path(codex_home).expanduser().resolve()
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser().resolve() if configured else (Path.home() / ".codex").resolve()


def _which(name: str) -> Path | None:
    found = shutil.which(name)
    if not found:
        return None
    candidate = Path(found).expanduser()
    return candidate.resolve() if candidate.is_file() else None


def _desktop_path(codex_home: Path | str | None) -> Path | None:
    """Read only the configured desktop executable path."""

    config = _home(codex_home) / "config.toml"
    if not config.is_file():
        return None
    try:
        payload = tomllib.loads(config.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    current: Any = payload
    for key in ("mcp_servers", "node_repl", "env", "CODEX_CLI_PATH"):
        if not isinstance(current, Mapping) or key not in current:
            return None
        current = current[key]
    if not isinstance(current, str) or not current.strip():
        return None
    candidate = Path(current).expanduser()
    if not candidate.is_absolute():
        candidate = _home(codex_home) / candidate
    return candidate


def _explicit_path(value: str) -> Path | None:
    candidate = Path(value).expanduser()
    path_like = candidate.is_absolute() or "/" in value or "\\" in value
    if path_like:
        return candidate.resolve() if candidate.is_file() else None
    return _which(value)


def resolve_runtime(
    explicit: str | os.PathLike[str] | None = None,
    codex_home: Path | str | None = None,
) -> Runtime:
    """Resolve one executable with explicit, desktop, then PATH precedence."""

    desktop = _desktop_path(codex_home)
    path_candidate = _which("codex")
    desktop_available = desktop is not None and desktop.is_file()
    requested = None if explicit is None else str(explicit)

    if explicit is not None:
        candidate = _explicit_path(str(explicit))
        if candidate is None:
            return Runtime(
                None,
                "unavailable",
                requested,
                desktop is not None,
                desktop_available,
                path_candidate is not None,
                "explicit runtime override is unavailable",
            )
        return Runtime(candidate, "explicit", requested, desktop is not None, desktop_available, path_candidate is not None)

    if desktop_available:
        assert desktop is not None
        return Runtime(desktop.resolve(), "desktop_managed", None, True, True, path_candidate is not None)
    if path_candidate is not None:
        return Runtime(path_candidate, "path", None, desktop is not None, False, True)
    return Runtime(None, "unavailable", None, desktop is not None, False, False, "no usable Codex executable was found")


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _load_toml(path: Path) -> Mapping[str, Any]:
    with path.open("rb") as handle:
        return _mapping(tomllib.load(handle), str(path))


def _safe_project_root(root: Path | str) -> Path:
    """Canonicalize a project root only after the shared link checks."""

    from .workspace import _safe_root

    return _safe_root(root)


def _check(report: dict[str, Any], name: str, ok: bool, detail: str) -> None:
    report["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
    if not ok:
        report["errors"].append({"check": name, "detail": detail})


def _exact_int(value: Any) -> bool:
    return type(value) is int


def _positive_int(value: Any) -> bool:
    return _exact_int(value) and value > 0


def _positive_i64(value: Any) -> bool:
    return _exact_int(value) and 0 < value <= _I64_MAX


def _object_keys(
    report: dict[str, Any],
    name: str,
    value: Any,
    allowed: frozenset[str],
    required: frozenset[str],
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _check(report, f"{name}-shape", False, f"{name} must be an object")
        return {}
    unknown = sorted(str(key) for key in value if key not in allowed)
    missing = sorted(key for key in required if key not in value)
    _check(report, f"{name}-unknown-keys", not unknown, f"{name} contains unsupported keys: {unknown}")
    _check(report, f"{name}-required-keys", not missing, f"{name} is missing required keys: {missing}")
    return value


def _validate_feature_config(report: dict[str, Any], name: str, value: Any) -> bool:
    """Validate one source feature and return its explicit activation state."""

    if name in _CONFIG_ENABLED_TABLE_KEYS:
        if type(value) is bool:
            return value
        table = _object_keys(
            report,
            f"config-feature-{name}",
            value,
            _CONFIG_ENABLED_TABLE_FIELDS,
            _CONFIG_ENABLED_TABLE_FIELDS,
        )
        enabled = table.get("enabled")
        _check(
            report,
            f"config-feature-{name}-enabled-shape",
            type(enabled) is bool,
            f"config feature {name}.enabled must be a boolean",
        )
        return enabled is True

    if name == "context_management":
        table = _object_keys(
            report,
            "config-feature-context-management",
            value,
            _CONFIG_CONTEXT_MANAGEMENT_KEYS,
            _CONFIG_CONTEXT_MANAGEMENT_KEYS,
        )
        experimental_mode = table.get("experimental_mode")
        _check(
            report,
            "config-feature-context-management-experimental-mode",
            type(experimental_mode) is bool,
            "context management experimental_mode must be a boolean",
        )
        return experimental_mode is True

    if name == "rollout_budget":
        table = _object_keys(
            report,
            "config-feature-rollout-budget",
            value,
            _CONFIG_ROLLOUT_BUDGET_KEYS,
            _CONFIG_ROLLOUT_BUDGET_KEYS,
        )
        enabled = table.get("enabled")
        _check(
            report,
            "config-feature-rollout-budget-enabled",
            type(enabled) is bool,
            "rollout budget enabled must be a boolean",
        )
        limit = table.get("limit_tokens")
        limit_ok = _positive_i64(limit)
        _check(
            report,
            "config-feature-rollout-budget-limit-tokens",
            limit_ok,
            "rollout budget limit_tokens must be a positive integer",
        )
        reminders = table.get("reminder_at_remaining_tokens")
        reminders_ok = (
            isinstance(reminders, list)
            and all(_positive_i64(item) and (not limit_ok or item < limit) for item in reminders)
        )
        _check(
            report,
            "config-feature-rollout-budget-reminders",
            reminders_ok,
            "rollout budget reminders must be a list of positive integers below limit_tokens",
        )
        return enabled is True

    valid = type(value) is bool
    _check(report, f"config-feature-{name}", valid, f"config feature {name} must be a boolean")
    return valid and value


def _validate_configuration(report: dict[str, Any], config: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate the project TOML intent without invoking a client."""

    config_map = _object_keys(report, "config", config, _CONFIG_KEYS, _CONFIG_REQUIRED_KEYS)
    _check(report, "config-model", config_map.get("model") == MODEL_SLUG, "config model must be gpt-6-astra")
    for key in ("model_reasoning_effort", "plan_mode_reasoning_effort"):
        value = config_map.get(key)
        _check(report, f"config-{key}", value in SUPPORTED_EFFORTS, f"{key} must be a supported reasoning effort")
    _check(report, "config-approval", config_map.get("approval_policy") == "never", "approval policy must remain never")
    _check(report, "config-sandbox", config_map.get("sandbox_mode") == "danger-full-access", "sandbox mode must remain danger-full-access")
    _check(report, "config-login-shell", config_map.get("allow_login_shell") is False, "login shell must remain disabled")
    _check(report, "config-web-search", config_map.get("web_search") == "live", "web search must use live mode")

    features_value = config_map.get("features")
    _check(report, "config-features-shape", isinstance(features_value, Mapping), "config features must be an object")
    features = features_value if isinstance(features_value, Mapping) else {}
    for name in sorted(features, key=str):
        valid_name = isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_-]+", name) is not None
        _check(report, "config-feature-name", valid_name, "config feature names must be safe TOML keys")
        if not valid_name:
            continue
        _validate_feature_config(report, name, features[name])
        if name in _CONFIG_COMPATIBILITY_EXCEPTION_FEATURE_KEYS:
            value = features.get(name)
            _check(
                report,
                f"config-feature-{name}-compatibility",
                type(value) is bool and value is False,
                "shell_zsh_fork must remain explicitly false for Windows Codex CLI compatibility",
            )
    agents = _object_keys(report, "config-agents", config_map.get("agents"), _CONFIG_AGENT_KEYS, _CONFIG_AGENT_KEYS)
    _check(report, "config-worker-model", agents.get("default_subagent_model") == MODEL_SLUG, "worker model must be gpt-6-astra")
    _check(report, "config-worker-effort", agents.get("default_subagent_reasoning_effort") in SUPPORTED_EFFORTS, "worker effort must be supported")
    _check(report, "config-thread-cap", _positive_int(agents.get("max_concurrent_threads_per_session")), "thread cap must be a positive integer")

    app_sections = _object_keys(report, "config-apps", config_map.get("apps"), _CONFIG_APP_KEYS, _CONFIG_APP_KEYS)
    default_apps = _object_keys(report, "config-default-apps", app_sections.get("_default"), _CONFIG_DEFAULT_APP_KEYS, _CONFIG_DEFAULT_APP_KEYS)
    _check(report, "config-apps-enabled", default_apps.get("enabled") is True, "default apps must be enabled")
    _check(report, "config-apps-destructive", default_apps.get("destructive_enabled") is False, "config destructive apps must remain disabled")
    _check(report, "config-apps-open-world", default_apps.get("open_world_enabled") is False, "config open-world apps must remain disabled")
    return config_map


def _feature_map(features: Mapping[str, Any]) -> Mapping[str, Any] | None:
    if "features" not in features:
        return features
    nested = features.get("features")
    return nested if isinstance(nested, Mapping) else None


def _feature_state(value: Any) -> tuple[bool, str | None, str | None]:
    """Read an advertised feature activation without coercing its state."""

    if type(value) is bool:
        return value, None, None
    if not isinstance(value, Mapping):
        return False, None, "feature record must be a boolean or object"
    if "enabled" not in value or type(value.get("enabled")) is not bool:
        return False, None, "feature enabled must be a boolean"
    stage = value.get("stage")
    if stage is not None and (not isinstance(stage, str) or stage not in _STAGES):
        return False, None, f"feature stage must be one of {_STAGES}"
    return value["enabled"], stage, None


def _configured_feature_enabled(name: str, configured_features: Mapping[str, Any]) -> bool:
    value = configured_features.get(name)
    if isinstance(value, Mapping):
        activation_key = "experimental_mode" if name == "context_management" else "enabled"
        return value.get(activation_key) is True
    return value is True


def _model_list(catalog: Mapping[str, Any]) -> tuple[list[Any], str | None]:
    models = catalog.get("models")
    if isinstance(models, list):
        return models, None
    if isinstance(catalog.get("model"), Mapping):
        return [catalog["model"]], None
    return [], "catalog models must be a list or model object"


def _toml_override_value(value: Any) -> str:
    """Encode one source TOML value for a native ``-c key=value`` override."""

    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is int:
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=True)
    if isinstance(value, list) and all(type(item) is int for item in value):
        return json.dumps(value, separators=(",", ":"))
    raise ValueError("source configuration contains a value unsupported by -c overrides")


def _source_override_args(config: Mapping[str, Any]) -> tuple[str, ...]:
    """Flatten the exact source TOML into deterministic native CLI overrides."""

    result: list[str] = []

    def visit(value: Mapping[str, Any], prefix: str = "") -> None:
        for key in sorted(value):
            if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", key):
                raise ValueError("source configuration contains an unsafe TOML key")
            path = f"{prefix}.{key}" if prefix else key
            item = value[key]
            if isinstance(item, Mapping):
                visit(item, path)
            else:
                result.extend(("-c", f"{path}={_toml_override_value(item)}"))

    visit(config)
    return tuple(result)


def _base_report() -> dict[str, Any]:
    return {
        "ok": False,
        "schema": SCHEMA_VERSION,
        "model": MODEL_SLUG,
        "selected": {},
        "observed": {"context": None, "features": {}},
        "checks": [],
        "errors": [],
        "warnings": [],
        "external_calls": False,
        "model_call": False,
        "network_requested": False,
        "network_observation": "not instrumented",
        "catalog_source": "none",
        "probe_mode": "none",
        "local_cli_calls": [],
        "external_calls_scope": "This module makes no direct API/model/network request; native Codex CLI probes are reported separately and may refresh managed configuration or local caches. Network activity by the native client is not instrumented.",
    }


def _selected_from_config(config: Mapping[str, Any]) -> dict[str, Any]:
    agents = config.get("agents") if isinstance(config.get("agents"), Mapping) else {}
    features = config.get("features") if isinstance(config.get("features"), Mapping) else {}
    context_management = features.get("context_management") if isinstance(features.get("context_management"), Mapping) else {}
    return {
        "model": config.get("model"),
        "lead_effort": config.get("model_reasoning_effort"),
        "plan_effort": config.get("plan_mode_reasoning_effort"),
        "worker_effort": agents.get("default_subagent_reasoning_effort"),
        "max_concurrent_threads_per_session": agents.get("max_concurrent_threads_per_session"),
        "context_management_experimental_mode": context_management.get("experimental_mode"),
    }


def validate_runtime(
    config: Mapping[str, Any],
    catalog: Mapping[str, Any],
    features: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate local catalog observations against project TOML intent."""

    report = _base_report()
    try:
        config = _mapping(config, "config")
        catalog = _mapping(catalog, "catalog")
        features = _mapping(features, "features")
    except ValueError as exc:
        report["errors"].append({"check": "input-shape", "detail": str(exc)})
        return report

    config_map = _validate_configuration(report, config)
    report["selected"] = _selected_from_config(config_map)

    models, model_error = _model_list(catalog)
    _check(report, "catalog-models-shape", model_error is None, model_error or "catalog models are valid")
    invalid_models = [index for index, item in enumerate(models) if not isinstance(item, Mapping)]
    _check(report, "catalog-model-entry-shape", not invalid_models, f"catalog model entries are invalid: {invalid_models}")
    selected = next((item for item in models if isinstance(item, Mapping) and item.get("slug") == MODEL_SLUG), None)
    _check(report, "catalog-primary-present", selected is not None, "catalog must advertise gpt-6-astra")
    if selected is not None:
        levels = selected.get("supported_reasoning_levels")
        _check(report, "catalog-effort-shape", isinstance(levels, list), "supported reasoning levels must be a list")
        efforts: set[str] = set()
        if isinstance(levels, list):
            invalid_levels = []
            for index, item in enumerate(levels):
                if isinstance(item, str):
                    efforts.add(item)
                elif isinstance(item, Mapping) and isinstance(item.get("effort"), str):
                    efforts.add(item["effort"])
                else:
                    invalid_levels.append(index)
            _check(report, "catalog-effort-entry-shape", not invalid_levels, f"invalid reasoning level entries: {invalid_levels}")
        agents = config_map.get("agents") if isinstance(config_map.get("agents"), Mapping) else {}
        for name, effort in (
            ("lead", config_map.get("model_reasoning_effort")),
            ("plan", config_map.get("plan_mode_reasoning_effort")),
            ("worker", agents.get("default_subagent_reasoning_effort")),
        ):
            _check(report, f"catalog-{name}-effort", effort in efforts, f"catalog must advertise configured {name} effort")

        context: dict[str, Any] = {}
        valid_context: dict[str, int] = {}
        for key in ("context_window", "max_context_window"):
            value = selected.get(key)
            if value is not None:
                valid = _positive_int(value)
                _check(report, f"catalog-context-{key}", valid, f"catalog {key} must be a positive integer")
                if valid:
                    context[key] = value
                    valid_context[key] = value
        percent = selected.get("effective_context_window_percent")
        if percent is not None:
            percent_ok = isinstance(percent, (int, float)) and not isinstance(percent, bool) and 0 < percent <= 100
            _check(report, "catalog-context-effective-percent", percent_ok, "catalog effective context percent must be between 0 and 100")
            context["effective_context_window_percent"] = percent
        report["observed"]["context"] = context
        if not {"context_window", "max_context_window"} <= set(valid_context):
            report["warnings"].append(
                {
                    "check": "catalog-context-unknown",
                    "detail": "the client did not advertise both context window values",
                }
            )
        elif valid_context["context_window"] <= valid_context["max_context_window"]:
            _check(report, "catalog-context-order", True, "catalog context windows are ordered")
        else:
            _check(report, "catalog-context-order", False, "catalog context_window must not exceed max_context_window")
        report["observed"]["model"] = {"slug": selected.get("slug"), "context": context}

    actual_features = _feature_map(features)
    _check(report, "feature-catalog-shape", actual_features is not None, "feature catalog must contain an object")
    if actual_features is not None:
        configured_features = config_map.get("features") if isinstance(config_map.get("features"), Mapping) else {}
        observed_states: dict[str, tuple[bool, str | None, str | None]] = {}
        for raw_name in sorted(actual_features, key=str):
            name = str(raw_name)
            enabled, stage, shape_error = _feature_state(actual_features[raw_name])
            observed_states[name] = (enabled, stage, shape_error)
            report["observed"]["features"][name] = {"enabled": enabled, "stage": stage}
            if shape_error:
                _check(report, f"feature-{name}-shape", False, shape_error)

        for name in sorted(configured_features, key=str):
            if not isinstance(name, str) or re.fullmatch(r"[A-Za-z0-9_-]+", name) is None:
                continue
            state = observed_states.get(name)
            if state is None:
                enabled, stage, shape_error = False, None, "feature is absent from the observed catalog"
            else:
                enabled, stage, shape_error = state
            if name in _CONFIG_COMPATIBILITY_EXCEPTION_FEATURE_KEYS:
                source_disabled = type(configured_features.get(name)) is bool and configured_features.get(name) is False
                observed_disabled = (
                    state is not None
                    and shape_error is None
                    and enabled is False
                    and stage in _STAGES
                    and stage != "removed"
                )
                if stage == "deprecated" and observed_disabled:
                    report["warnings"].append(
                        {
                            "check": f"feature-{name}-stage",
                            "detail": f"feature {name} is disabled by compatibility exception but deprecated",
                        }
                    )
                if stage == "removed":
                    _check(report, f"feature-{name}-stage", False, f"feature {name} is removed and its compatibility exception requires review")
                _check(
                    report,
                    f"feature-{name}-compatibility",
                    source_disabled and observed_disabled,
                    "shell_zsh_fork must be explicitly false in source and live feature observations for Windows Codex CLI compatibility",
                )
                continue
            requested_enabled = _configured_feature_enabled(name, configured_features)
            if stage == "removed":
                _check(report, f"feature-{name}-stage", False, f"source-selected feature {name} is removed")
            elif stage == "deprecated":
                report["warnings"].append(
                    {
                        "check": f"feature-{name}-stage",
                        "detail": f"source-selected feature {name} is deprecated",
                    }
                )
            _check(
                report,
                f"feature-{name}",
                shape_error is None and enabled is requested_enabled,
                f"feature {name} must advertise the source activation {str(requested_enabled).lower()}",
            )

    report["ok"] = not report["errors"]
    return report


def validate_configuration(root: Path | str) -> dict[str, Any]:
    """Validate only the project TOML configuration without external calls."""

    report = _base_report()
    report["mode"] = "static_configuration"
    try:
        root_path = _safe_project_root(root)
    except (OSError, ValueError, RuntimeError) as exc:
        report["errors"].append({"check": "configuration-root", "detail": f"project root cannot be used: {type(exc).__name__}"})
        return report
    try:
        config = _load_toml(root_path / ".codex" / "config.toml")
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        report["errors"].append({"check": "configuration-load", "detail": f"configuration cannot be loaded: {type(exc).__name__}"})
        return report
    _validate_configuration(report, config)
    report["selected"] = _selected_from_config(config)
    report["ok"] = not report["errors"]
    return report


def _bounded_text(value: str | bytes | None) -> tuple[str, bool]:
    if value is None:
        return "", False
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    truncated = len(value) > MAX_CLI_OUTPUT_CHARS
    return value[:MAX_CLI_OUTPUT_CHARS], truncated


def _run(command: str, *args: str, cwd: Path | str | None = None) -> CommandResult:
    try:
        run_kwargs: dict[str, Any] = {
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "check": False,
            "timeout": CLI_TIMEOUT_SECONDS,
        }
        if cwd is not None:
            run_kwargs["cwd"] = str(Path(cwd).expanduser().resolve())
        completed = subprocess.run([command, *args], **run_kwargs)
    except subprocess.TimeoutExpired as exc:
        stdout, stdout_truncated = _bounded_text(exc.stdout)
        stderr, stderr_truncated = _bounded_text(exc.stderr)
        return CommandResult(124, stdout, stderr or "runtime command timed out", True, stdout_truncated or stderr_truncated)
    stdout, stdout_truncated = _bounded_text(completed.stdout)
    stderr, stderr_truncated = _bounded_text(completed.stderr)
    return CommandResult(completed.returncode, stdout, stderr, False, stdout_truncated or stderr_truncated)


def _parse_catalog(output: str) -> Mapping[str, Any]:
    return _mapping(json.loads(output), "catalog output")


def _parse_features(output: str) -> Mapping[str, Any]:
    stripped = output.strip()
    if not stripped:
        raise ValueError("feature output is empty")
    if stripped.startswith("{"):
        return _mapping(json.loads(stripped), "feature output")
    result: dict[str, Any] = {}
    for line in stripped.splitlines():
        parts = line.split()
        if len(parts) < 3 or parts[-1].lower() not in {"true", "false"}:
            continue
        stage = " ".join(parts[1:-1])
        if stage in _STAGES:
            result[parts[0]] = {"stage": stage, "enabled": parts[-1].lower() == "true"}
    if not result:
        raise ValueError("feature output contained no parseable features")
    return {"features": result}


def _parse_version(output: str) -> tuple[str | None, tuple[int, int] | None]:
    match = _VERSION_RE.search(output)
    if not match:
        return None, None
    major, minor, patch = match.groups()
    version = f"{int(major)}.{int(minor)}" + (f".{int(patch)}" if patch is not None else "")
    return version, (int(major), int(minor))


def _failure(
    message: str,
    runtime: Runtime | None = None,
    *,
    check: str = "runtime-inspection",
    local_cli_calls: list[str] | None = None,
    catalog_source: str = "none",
    client_version: Mapping[str, Any] | None = None,
    diagnosis: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "schema": SCHEMA_VERSION,
        "model": MODEL_SLUG,
        "selected": {},
        "observed": {"context": None, "features": {}},
        "runtime": runtime.public_record() if runtime is not None else None,
        "client_version": dict(client_version or {}),
        "diagnosis": dict(diagnosis or {}),
        "errors": [{"check": check, "detail": message}],
        "checks": [],
        "warnings": [],
        "external_calls": False,
        "model_call": False,
        "network_requested": False,
        "network_observation": "not instrumented",
        "catalog_source": catalog_source,
        "probe_mode": (
            "bundled_catalog_and_source_features"
            if catalog_source == "bundled_catalog"
            else "offline_fixture"
            if catalog_source == "offline_fixture"
            else "none"
        ),
        "local_cli_calls": list(local_cli_calls or []),
        "external_calls_scope": "This module makes no direct API/model/network request; native Codex CLI probes are reported separately and may refresh managed configuration or local caches. Network activity by the native client is not instrumented.",
    }


def _probe_rejected_configuration(result: CommandResult) -> bool:
    """Recognize a client-side config-shape rejection without exposing output."""

    text = f"{result.stdout}\n{result.stderr}".lower()
    return any(
        marker in text
        for marker in (
            "invalid type",
            "expected struct",
            "failed to parse",
            "unknown field",
            "unknown key",
            "unrecognized field",
        )
    )


def _probe_failure(
    stage: str,
    result: CommandResult,
    runtime: Runtime,
    calls: list[str],
    catalog_source: str,
    client_version: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a bounded, actionable diagnosis for a failed local probe."""

    if _probe_rejected_configuration(result):
        observed = dict(client_version)
        observed["status"] = "incompatible_client"
        return _failure(
            f"selected Codex client rejected the project configuration during {stage}; its config schema is incompatible",
            runtime,
            check="incompatible-client",
            local_cli_calls=calls,
            catalog_source=catalog_source,
            client_version=observed,
            diagnosis={
                "kind": "config_schema_rejection",
                "stage": stage,
                "action": "select a Codex client that accepts the project config schema",
            },
        )
    return _failure(
        f"{stage} command failed",
        runtime,
        check=f"{stage}-command",
        local_cli_calls=calls,
        catalog_source=catalog_source,
        client_version=client_version,
    )


def inspect_runtime(root: Path | str, codex: str | os.PathLike[str] | Runtime | Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Inspect project TOML, local model catalog, and feature state."""

    try:
        root_path = _safe_project_root(root)
    except (OSError, ValueError, RuntimeError) as exc:
        return _failure(f"project root cannot be used: {type(exc).__name__}")
    try:
        config = _load_toml(root_path / ".codex" / "config.toml")
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        return _failure(f"configuration cannot be loaded: {type(exc).__name__}")

    source_report = _base_report()
    _validate_configuration(source_report, config)
    if source_report["errors"]:
        failure = _failure(
            "project configuration failed static validation",
            check="configuration-contract",
        )
        failure["selected"] = _selected_from_config(config)
        failure["configuration_errors"] = source_report["errors"]
        return failure

    fixture: Mapping[str, Any] | None = codex if isinstance(codex, Mapping) else None
    if isinstance(codex, Runtime):
        runtime = codex
    else:
        runtime = resolve_runtime(explicit=None if fixture is not None else codex)
    if runtime.executable is None and fixture is None:
        return _failure(runtime.reason or "runtime executable is unavailable", runtime)

    attempted_cli_calls: list[str] = []
    catalog_source = "offline_fixture" if fixture is not None else "bundled_catalog"
    source_override_args: tuple[str, ...] = ()
    if fixture is None:
        try:
            source_override_args = _source_override_args(config)
        except ValueError as exc:
            return _failure(f"source configuration overrides are invalid: {exc}", runtime, check="configuration-overrides")
    client_version: dict[str, Any] = {"status": "not_probed"}
    try:
        if fixture is not None:
            client_version = {"status": "not_probed", "reason": "offline_fixture"}
            catalog = _mapping(fixture.get("catalog"), "catalog fixture")
            features = _mapping(fixture.get("features"), "feature fixture")
        else:
            assert runtime.command is not None
            attempted_cli_calls.append("--version")
            version_result = _run(runtime.command, "--version", cwd=root_path)
            if version_result.timed_out:
                return _failure(
                    "Codex client version probe timed out",
                    runtime,
                    check="client-version",
                    local_cli_calls=attempted_cli_calls,
                    catalog_source=catalog_source,
                    client_version={"status": "unavailable"},
                )
            if version_result.output_truncated:
                return _failure(
                    "Codex client version output exceeded the bounded limit",
                    runtime,
                    check="client-version",
                    local_cli_calls=attempted_cli_calls,
                    catalog_source=catalog_source,
                    client_version={"status": "unavailable"},
                )
            version, parsed = _parse_version(version_result.stdout or version_result.stderr)
            if version_result.returncode != 0:
                return _failure(
                    "Codex client version probe failed",
                    runtime,
                    check="client-version",
                    local_cli_calls=attempted_cli_calls,
                    catalog_source=catalog_source,
                    client_version={"status": "unavailable", "version": version},
                )
            client_version = {"status": "observed" if parsed is not None else "unrecognized", "version": version}

            attempted_cli_calls.append("debug models --bundled")
            models = _run(
                runtime.command,
                "debug",
                "models",
                "--bundled",
                cwd=root_path,
            )
            if models.timed_out:
                return _failure("model catalog command timed out", runtime, local_cli_calls=attempted_cli_calls, catalog_source=catalog_source, client_version=client_version)
            if models.output_truncated:
                return _failure("model catalog command output exceeded the bounded limit", runtime, local_cli_calls=attempted_cli_calls, catalog_source=catalog_source, client_version=client_version)
            if models.returncode != 0:
                return _probe_failure("model catalog", models, runtime, attempted_cli_calls, catalog_source, client_version)
            attempted_cli_calls.append("features list")
            feature_list = _run(
                runtime.command,
                *source_override_args,
                "features",
                "list",
                cwd=root_path,
            )
            if feature_list.timed_out:
                return _failure("feature catalog command timed out", runtime, local_cli_calls=attempted_cli_calls, catalog_source=catalog_source, client_version=client_version)
            if feature_list.output_truncated:
                return _failure("feature catalog command output exceeded the bounded limit", runtime, local_cli_calls=attempted_cli_calls, catalog_source=catalog_source, client_version=client_version)
            if feature_list.returncode != 0:
                return _probe_failure("feature catalog", feature_list, runtime, attempted_cli_calls, catalog_source, client_version)
            catalog = _parse_catalog(models.stdout)
            features = _parse_features(feature_list.stdout)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return _failure(f"runtime catalog is malformed: {type(exc).__name__}", runtime, local_cli_calls=attempted_cli_calls, catalog_source=catalog_source, client_version=client_version)

    report = validate_runtime(config, catalog, features)
    report["runtime"] = runtime.public_record()
    report["client_version"] = client_version
    if (
        sys.platform == "win32"
        and client_version.get("version") == "0.153.4"
        and config["features"].get("shell_zsh_fork") is False
    ):
        report["warnings"].append(
            {
                "check": "feature-shell_zsh_fork-compatibility",
                "detail": "Windows Codex CLI 0.153.4 has a startup incompatibility when shell_zsh_fork is enabled; this project intentionally keeps it false and verifies that live state",
            }
        )
    if client_version.get("status") == "unrecognized":
        report["warnings"].append(
            {
                "check": "client-version",
                "detail": "Codex client returned an unrecognized version string; catalog and feature observations determine compatibility",
            }
        )
    report["catalog_source"] = catalog_source
    report["probe_mode"] = "offline_fixture" if fixture is not None else "bundled_catalog_and_source_features"
    report["local_cli_calls"] = attempted_cli_calls
    report["external_calls_scope"] = "This module makes no direct API/model/network request; native Codex CLI probes are reported separately and may refresh managed configuration or local caches. Network activity by the native client is not instrumented."
    return report


__all__ = [
    "CLI_TIMEOUT_SECONDS",
    "CommandResult",
    "MAX_CLI_OUTPUT_CHARS",
    "MODEL_SLUG",
    "Runtime",
    "SCHEMA_VERSION",
    "SUPPORTED_EFFORTS",
    "inspect_runtime",
    "resolve_runtime",
    "validate_configuration",
    "validate_runtime",
]
