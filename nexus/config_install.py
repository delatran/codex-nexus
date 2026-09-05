"""Hash-bound installation of the project Codex configuration.

The project ``.codex/config.toml`` is the only source of owned settings.  The
user configuration is edited through the selected Codex app-server writer so
comments, plugins, MCP servers, accounts, and other unrelated settings remain
under the native writer's control.  A temporary sibling file is edited and
validated before it replaces the user file atomically.
"""

from __future__ import annotations

import copy
from contextlib import contextmanager
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Mapping

from . import runtime, workspace


SCHEMA_HEALTH = "codex-nexus/config-health/v1"
SCHEMA_PLAN = "codex-nexus/config-plan/v1"
SCHEMA_RECEIPT = "codex-nexus/config-receipt/v1"
CONFIG_RELATIVE = Path(".codex") / "config.toml"
LEGACY_KEY_PATHS = (
    "agents.max_threads",
    "agents.max_depth",
    "agents.job_max_runtime_seconds",
    "features.multi_agent",
    "features.enable_request_compression",
    "project_doc_fallback_filenames",
    "project_doc_max_bytes",
    "project_root_markers",
)
_NATIVE_TYPED_FEATURE_NAMES = frozenset(
    {
        "code_mode",
        "context_management",
        "current_time_reminder",
        "guardianv2",
        "network_proxy",
        "non_prefixed_mcp_tool_names",
        "rollout_budget",
        "token_budget",
    }
)
RPC_TIMEOUT_SECONDS = 30
MAX_RPC_LINE_CHARS = 1_000_000
LOCK_TIMEOUT_SECONDS = 10


class ConfigInstallError(RuntimeError):
    """Raised when a staged native configuration merge cannot be trusted."""


@dataclass(frozen=True)
class AppliedConfig:
    """Public receipt plus private rollback state."""

    receipt: dict[str, Any]
    target: Path
    backup: Path | None
    before_sha256: str | None
    after_sha256: str
    target_existed: bool


@contextmanager
def _target_lock(target: Path):
    """Serialize freshness checks and replacement for one user config."""

    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.parent / ".codex-nexus-config.lock"
    workspace._reject_link_components(target.parent)
    if os.path.lexists(lock_path) and workspace._is_link_or_junction(lock_path):
        raise ConfigInstallError("configuration lock is a redirect")
    try:
        handle = lock_path.open("a+b")
    except OSError as exc:
        raise ConfigInstallError("configuration lock is unavailable") from exc
    acquired = False
    try:
        handle.seek(0)
        handle.write(b"\0")
        handle.flush()
        handle.seek(0)
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except (BlockingIOError, OSError):
                time.sleep(0.05)
        if not acquired:
            raise ConfigInstallError("configuration is busy")
        yield
    finally:
        if acquired:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()


def _as_path(value: os.PathLike[str] | str, *, label: str) -> Path:
    if not isinstance(value, (str, os.PathLike)):
        raise TypeError(f"{label} must be a path-like value")
    return Path(value).expanduser()


def _safe_paths(root: os.PathLike[str] | str, home: os.PathLike[str] | str) -> tuple[Path, Path, Path]:
    """Validate roots before resolving any path below a possible redirect."""

    try:
        root_path = workspace._safe_root(root)
        home_candidate = _as_path(home, label="home").absolute()
        workspace._reject_link_components(home_candidate)
        if workspace._is_link_or_junction(home_candidate):
            raise ConfigInstallError("home must not be a symbolic link or junction")
        home_path = home_candidate.resolve(strict=False)
        target = home_candidate / CONFIG_RELATIVE
        if not workspace._is_within(target.absolute(), home_candidate.absolute()):
            raise ConfigInstallError("configuration target leaves home")
        workspace._reject_link_components(target.parent)
        workspace._reject_link_components(target)
        return root_path, home_path, target
    except workspace.WorkspaceError as exc:
        raise ConfigInstallError(str(exc)) from exc


def _source_path(root: Path) -> Path:
    source = root / CONFIG_RELATIVE
    try:
        workspace._reject_link_components(source)
    except workspace.WorkspaceError as exc:
        raise ConfigInstallError("project configuration contains a redirect") from exc
    if not source.is_file():
        raise ConfigInstallError("project configuration is missing")
    return source


def _read_toml(path: Path, *, source: bool) -> tuple[bytes, dict[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ConfigInstallError("configuration cannot be read") from exc
    try:
        parsed = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        label = "project" if source else "user"
        raise ConfigInstallError(f"{label} configuration is malformed TOML") from exc
    if not isinstance(parsed, dict):
        raise ConfigInstallError("configuration must contain a TOML table")
    return raw, parsed


def _source_intent(root: Path) -> tuple[bytes, dict[str, Any]]:
    source = _source_path(root)
    raw, parsed = _read_toml(source, source=True)
    # Keep the existing runtime validator as the source contract.  It rejects
    # accidental second policy files and catches unsupported model settings
    # before any user configuration is touched.
    report = runtime.validate_configuration(root)
    if not report.get("ok"):
        raise ConfigInstallError("project configuration does not satisfy the runtime contract")
    return raw, parsed


def _flatten(value: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key or "\n" in key or "\r" in key:
            raise ConfigInstallError("configuration contains an invalid key")
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(item, Mapping):
            result.update(_flatten(item, path))
        else:
            # TOML values are JSON-compatible for the plan/receipt surface.
            try:
                json.dumps(item, ensure_ascii=True)
            except (TypeError, ValueError) as exc:
                raise ConfigInstallError("configuration contains an unsupported value") from exc
            result[path] = copy.deepcopy(item)
    return result


def _get(mapping: Mapping[str, Any], path: str) -> tuple[bool, Any]:
    current: Any = mapping
    for component in path.split("."):
        if not isinstance(current, Mapping) or component not in current:
            return False, None
        current = current[component]
    return True, current


def _same_value(actual: Any, desired: Any) -> bool:
    """Compare TOML values without treating booleans as integers."""

    if type(actual) is not type(desired):
        return False
    if isinstance(actual, Mapping):
        if set(actual) != set(desired):
            return False
        return all(_same_value(actual[key], desired[key]) for key in actual)
    if isinstance(actual, list):
        return len(actual) == len(desired) and all(
            _same_value(left, right) for left, right in zip(actual, desired)
        )
    return actual == desired


def _remove(mapping: Mapping[str, Any], path: str) -> None:
    parts = path.split(".")
    current: Any = mapping
    for component in parts[:-1]:
        if not isinstance(current, dict) or component not in current:
            return
        current = current[component]
    if isinstance(current, dict):
        current.pop(parts[-1], None)


def _unowned(mapping: Mapping[str, Any], excluded: set[str]) -> dict[str, Any]:
    result = copy.deepcopy(dict(mapping))
    for path in excluded:
        _remove(result, path)
        parts = path.split(".")
        activation = "experimental_mode" if len(parts) == 3 and parts[1] == "context_management" else "enabled"
        if (
            len(parts) == 3
            and parts[0] == "features"
            and parts[1] in _NATIVE_TYPED_FEATURE_NAMES
            and parts[2] == activation
        ):
            parent = f"features.{parts[1]}"
            present, value = _get(result, parent)
            if present and type(value) is bool:
                _remove(result, parent)

    def prune(value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            pruned = prune(item)
            if not isinstance(pruned, dict) or pruned:
                cleaned[key] = pruned
        return cleaned

    return prune(result)


def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _target_state(target: Path) -> tuple[bytes | None, dict[str, Any] | None, str | None]:
    if not os.path.lexists(target):
        return None, None, None
    if workspace._is_link_or_junction(target):
        raise ConfigInstallError("user configuration target is a redirect")
    if not target.is_file():
        raise ConfigInstallError("user configuration target is not a regular file")
    raw, parsed = _read_toml(target, source=False)
    return raw, parsed, _sha_bytes(raw)


def _digest(unsigned: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(unsigned, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _unsigned_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "schema",
        "target",
        "source_sha256",
        "target_sha256",
        "owned",
        "remove",
        "needs_write",
        "runtime",
    )
    try:
        return {key: plan[key] for key in keys}
    except KeyError as exc:
        raise ConfigInstallError("configuration plan is missing required fields") from exc


def _validate_plan_digest(plan: Mapping[str, Any]) -> None:
    if plan.get("schema") != SCHEMA_PLAN or not isinstance(plan.get("plan_sha256"), str):
        raise ConfigInstallError("unsupported configuration plan or missing content digest")
    if _digest(_unsigned_plan(plan)) != plan["plan_sha256"]:
        raise ConfigInstallError("configuration plan digest does not match its contents")


def _owned_edits(owned: Mapping[str, Any], remove: list[str]) -> list[dict[str, Any]]:
    edits = [
        {"keyPath": path, "mergeStrategy": "replace", "value": copy.deepcopy(value)}
        for path, value in sorted(owned.items())
    ]
    edits.extend(
        {"keyPath": path, "mergeStrategy": "replace", "value": None}
        for path in sorted(remove)
    )
    return edits


def _runtime_record(codex: str | os.PathLike[str] | runtime.Runtime | None, home: Path) -> dict[str, Any]:
    selected = codex if isinstance(codex, runtime.Runtime) else runtime.resolve_runtime(codex, codex_home=home / ".codex")
    return selected.public_record()


def _configuration_delta(source_config: Mapping[str, Any], target_config: Mapping[str, Any] | None) -> tuple[dict[str, Any], list[str], bool]:
    owned = _flatten(source_config)
    remove = [path for path in LEGACY_KEY_PATHS if target_config is not None and _get(target_config, path)[0]]
    needs_write = target_config is None or any(
        not _get(target_config, path)[0] or not _same_value(_get(target_config, path)[1], desired)
        for path, desired in owned.items()
    ) or bool(remove)
    return owned, remove, needs_write


def plan_config(
    root: os.PathLike[str] | str,
    home: os.PathLike[str] | str,
    *,
    codex: str | os.PathLike[str] | runtime.Runtime | None = None,
) -> dict[str, Any]:
    """Build a sanitized, hash-bound plan without writing or starting a client."""

    root_path, home_path, target = _safe_paths(root, home)
    source_raw, source_config = _source_intent(root_path)
    _, target_config, target_sha = _target_state(target)
    owned, remove, needs_write = _configuration_delta(source_config, target_config)
    unsigned: dict[str, Any] = {
        "schema": SCHEMA_PLAN,
        "target": "user-config",
        "source_sha256": _sha_bytes(source_raw),
        "target_sha256": target_sha,
        "owned": owned,
        "remove": remove,
        "needs_write": needs_write,
        "runtime": _runtime_record(codex, home_path),
    }
    return {**unsigned, "plan_sha256": _digest(unsigned), "status": "planned"}


def health(
    root: os.PathLike[str] | str,
    home: os.PathLike[str] | str,
) -> dict[str, Any]:
    """Inspect owned global settings without returning private values."""

    try:
        root_path, _home_path, target = _safe_paths(root, home)
        source_raw, source_config = _source_intent(root_path)
        _, target_config, target_sha = _target_state(target)
        owned = _flatten(source_config)
        if target_config is None:
            drift = sorted(set(owned) | set(LEGACY_KEY_PATHS))
            return {
                "schema": SCHEMA_HEALTH,
                "ok": False,
                "status": "missing",
                "target": "user-config",
                "source_sha256": _sha_bytes(source_raw),
                "target_sha256": None,
                "owned_keys": sorted(owned),
                "drift_keys": drift,
            }
        drift: set[str] = set()
        for path, desired in owned.items():
            present, actual = _get(target_config, path)
            if not present or not _same_value(actual, desired):
                drift.add(path)
        for path in LEGACY_KEY_PATHS:
            if _get(target_config, path)[0]:
                drift.add(path)
        return {
            "schema": SCHEMA_HEALTH,
            "ok": not drift,
            "status": "correct" if not drift else "drift",
            "target": "user-config",
            "source_sha256": _sha_bytes(source_raw),
            "target_sha256": target_sha,
            "owned_keys": sorted(owned),
            "drift_keys": sorted(drift),
        }
    except ConfigInstallError as exc:
        return {
            "schema": SCHEMA_HEALTH,
            "ok": False,
            "status": "invalid",
            "target": "user-config",
            "error": str(exc),
        }


def _rpc_line(stream: Any, payload: Mapping[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n"
    stream.write(encoded.encode("utf-8"))
    stream.flush()


def _reader_queue(stream: Any) -> Queue[bytes | None]:
    queue: Queue[bytes | None] = Queue()

    def read() -> None:
        try:
            read_chunk = getattr(stream, "read1", stream.read)
            while True:
                chunk = read_chunk(4096)
                if not chunk:
                    queue.put(None)
                    return
                queue.put(chunk)
        except Exception:
            queue.put(None)

    threading.Thread(target=read, daemon=True).start()
    return queue


class _RpcReader:
    def __init__(self, queue: Queue[bytes | None]) -> None:
        self.queue = queue
        self.buffer = bytearray()

    def line(self, deadline: float) -> bytes | None:
        while b"\n" not in self.buffer:
            remaining = max(0.0, deadline - time.monotonic())
            if remaining <= 0:
                raise ConfigInstallError("Codex app-server response timed out")
            try:
                chunk = self.queue.get(timeout=remaining)
            except Empty as exc:
                raise ConfigInstallError("Codex app-server response timed out") from exc
            if chunk is None:
                return None
            self.buffer.extend(chunk)
            if len(self.buffer) > MAX_RPC_LINE_CHARS:
                raise ConfigInstallError("Codex app-server response exceeded the bounded limit")
        line, _, remaining = self.buffer.partition(b"\n")
        self.buffer = bytearray(remaining)
        return bytes(line)


def _rpc_response(reader: _RpcReader, request_id: int, deadline: float) -> Mapping[str, Any]:
    while time.monotonic() < deadline:
        line = reader.line(deadline)
        if line is None:
            break
        if len(line) > MAX_RPC_LINE_CHARS:
            raise ConfigInstallError("Codex app-server response exceeded the bounded limit")
        try:
            payload = json.loads(line.decode("utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, Mapping) and payload.get("id") == request_id:
            return payload
    raise ConfigInstallError("Codex app-server did not return a bounded response")


def _native_user_version(response: Mapping[str, Any]) -> str | None:
    result = response.get("result")
    if not isinstance(result, Mapping):
        return None
    origins = result.get("origins")
    if isinstance(origins, Mapping):
        for origin in origins.values():
            if not isinstance(origin, Mapping) or not isinstance(origin.get("version"), str):
                continue
            name = origin.get("name")
            if isinstance(name, Mapping) and name.get("type") == "user":
                return origin["version"]
    layers = result.get("layers")
    if isinstance(layers, list):
        for layer in layers:
            if not isinstance(layer, Mapping) or not isinstance(layer.get("version"), str):
                continue
            name = layer.get("name")
            if isinstance(name, Mapping) and name.get("type") == "user":
                return layer["version"]
    return None


def _native_batch_write(
    executable: Path,
    stage_home: Path,
    stage: Path,
    edits: list[dict[str, Any]],
) -> None:
    """Use the V2 app-server native TOML writer in an isolated home."""

    env = os.environ.copy()
    # The V2 writer deliberately refuses arbitrary file paths.  A temporary
    # isolated home makes the staged file the native user-config layer while
    # keeping the real user file untouched until the final os.replace.
    env["CODEX_HOME"] = str(stage_home / ".codex")
    for secret_name in ("OPENAI_API_KEY", "CODEX_API_KEY", "CHATGPT_API_KEY"):
        env.pop(secret_name, None)
    for proxy_name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(proxy_name, None)
    env["NO_PROXY"] = "*"
    env["no_proxy"] = "*"
    try:
        process = subprocess.Popen(
            [str(executable), "app-server", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            cwd=str(stage_home),
        )
    except OSError as exc:
        raise ConfigInstallError("selected Codex app-server is unavailable") from exc
    try:
        assert process.stdin is not None and process.stdout is not None
        reader = _RpcReader(_reader_queue(process.stdout))
        _rpc_line(
            process.stdin,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "codex-nexus", "version": "1"},
                    "capabilities": {},
                },
            },
        )
        initialize = _rpc_response(reader, 1, time.monotonic() + RPC_TIMEOUT_SECONDS)
        if "error" in initialize:
            raise ConfigInstallError("Codex app-server initialization failed")
        _rpc_line(process.stdin, {"jsonrpc": "2.0", "method": "initialized", "params": {}})
        _rpc_line(
            process.stdin,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "config/read",
                "params": {"includeLayers": True},
            },
        )
        read_response = _rpc_response(reader, 2, time.monotonic() + RPC_TIMEOUT_SECONDS)
        if "error" in read_response:
            raise ConfigInstallError("Codex app-server configuration read failed")
        native_version = _native_user_version(read_response)
        params: dict[str, Any] = {
            "filePath": str(stage),
            "reloadUserConfig": False,
            "edits": edits,
        }
        if native_version is not None:
            params["expectedVersion"] = native_version
        _rpc_line(process.stdin, {"jsonrpc": "2.0", "id": 3, "method": "config/batchWrite", "params": params})
        response = _rpc_response(reader, 3, time.monotonic() + RPC_TIMEOUT_SECONDS)
        if "error" in response or not isinstance(response.get("result"), Mapping):
            raise ConfigInstallError("Codex app-server configuration merge failed")
        status = response["result"].get("status")
        if status not in {"ok", "okOverridden"}:
            raise ConfigInstallError("Codex app-server returned an unknown configuration status")
    finally:
        try:
            process.stdin.close() if process.stdin is not None else None
        except OSError:
            pass
        try:
            process.terminate()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass
            try:
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass


def _backup_file(home: Path, target: Path, raw: bytes | None) -> Path | None:
    if raw is None:
        return None
    backup_root = home / ".codex-nexus-quarantine" / "config"
    try:
        workspace._reject_link_components(backup_root.parent)
        backup_root.mkdir(parents=True, exist_ok=True)
        workspace._reject_link_components(backup_root)
        directory = Path(tempfile.mkdtemp(prefix="backup-", dir=str(backup_root)))
        backup = directory / "config.toml"
        with backup.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        workspace._reject_link_components(backup)
        if workspace._is_link_or_junction(backup):
            raise ConfigInstallError("private configuration backup became a redirect")
        if _sha_bytes(backup.read_bytes()) != _sha_bytes(raw):
            raise ConfigInstallError("private configuration backup integrity check failed")
        return backup
    except (OSError, workspace.WorkspaceError) as exc:
        raise ConfigInstallError("private configuration backup could not be created") from exc


def _assert_target_fresh(target: Path, expected_sha: str | None) -> tuple[bytes | None, dict[str, Any] | None, str | None]:
    raw, parsed, observed_sha = _target_state(target)
    if observed_sha != expected_sha:
        raise ConfigInstallError("user configuration changed after planning")
    return raw, parsed, observed_sha


def _assert_regular_stage(stage: Path) -> None:
    try:
        workspace._reject_link_components(stage)
        if not os.path.lexists(stage) or workspace._is_link_or_junction(stage) or not stage.is_file():
            raise ConfigInstallError("staged configuration must be a regular file")
    except workspace.WorkspaceError as exc:
        raise ConfigInstallError("staged configuration contains a redirect") from exc


def _verify_stage(
    stage: Path,
    before: Mapping[str, Any] | None,
    owned: Mapping[str, Any],
    remove: list[str],
) -> tuple[bytes, dict[str, Any]]:
    _assert_regular_stage(stage)
    raw, after = _read_toml(stage, source=False)
    for path, desired in owned.items():
        present, actual = _get(after, path)
        if not present or not _same_value(actual, desired):
            raise ConfigInstallError("native writer did not produce the requested owned settings")
    for path in remove:
        if _get(after, path)[0]:
            raise ConfigInstallError("native writer did not remove an obsolete setting")
    excluded = set(owned) | set(remove)
    if before is not None and not _same_value(_unowned(before, excluded), _unowned(after, excluded)):
        raise ConfigInstallError("native writer changed an unrelated user setting")
    return raw, after


def _replace_from_backup(
    target: Path,
    backup: Path | None,
    target_existed: bool,
    expected_sha: str,
    before_sha: str | None,
) -> None:
    try:
        workspace._reject_link_components(target.parent)
        if target_existed:
            if backup is None or before_sha is None or not backup.is_file():
                raise ConfigInstallError("rollback backup is unavailable")
            workspace._reject_link_components(backup)
            if workspace._is_link_or_junction(backup):
                raise ConfigInstallError("rollback backup is a redirect")
            backup_raw = backup.read_bytes()
            if _sha_bytes(backup_raw) != before_sha:
                raise ConfigInstallError("rollback backup integrity check failed")
            current = _target_state(target)[2]
            if current != expected_sha:
                raise ConfigInstallError("refusing rollback after an unrelated target change")
            fd, name = tempfile.mkstemp(prefix=f".{target.name}.rollback-", dir=str(target.parent))
            os.close(fd)
            stage = Path(name)
            try:
                stage.write_bytes(backup_raw)
                if _sha_bytes(stage.read_bytes()) != before_sha:
                    raise ConfigInstallError("rollback staging integrity check failed")
                os.replace(stage, target)
            finally:
                if stage.exists():
                    stage.unlink()
        else:
            current = _target_state(target)[2]
            if current != expected_sha:
                raise ConfigInstallError("refusing rollback after an unrelated target change")
            if os.path.lexists(target):
                target.unlink()
    except (OSError, workspace.WorkspaceError) as exc:
        if isinstance(exc, ConfigInstallError):
            raise
        raise ConfigInstallError("configuration rollback failed") from exc


def _publish_exclusive(target: Path, stage: Path, expected_sha: str | None) -> None:
    """Publish a staged file without overwriting a target that appears late."""

    _assert_regular_stage(stage)
    capture: Path | None = None
    try:
        workspace._reject_link_components(target.parent)
        current_sha = _target_state(target)[2]
        if current_sha != expected_sha:
            raise ConfigInstallError("user configuration changed before publication")
        if current_sha is not None:
            fd, capture_name = tempfile.mkstemp(prefix=f".{target.name}.capture-", dir=str(target.parent))
            os.close(fd)
            capture = Path(capture_name)
            capture.unlink()
            workspace._reject_link_components(target)
            os.replace(target, capture)
            if workspace._is_link_or_junction(capture):
                raise ConfigInstallError("user configuration became a redirect before publication")
            captured_raw = capture.read_bytes()
            if _sha_bytes(captured_raw) != expected_sha:
                raise ConfigInstallError("user configuration changed before publication")
        if os.path.lexists(target):
            raise ConfigInstallError("user configuration appeared during publication")
        # Hard-link creation is an exclusive create on both supported hosts.
        # It cannot overwrite a file created after the freshness check.
        os.link(stage, target)
        try:
            stage.unlink()
        except OSError:
            # Publication is already committed. The bounded cleanup pass will
            # report a warning if the private stage cannot be removed.
            pass
        if capture is not None:
            try:
                capture.unlink()
            except OSError:
                pass
    except Exception as exc:
        if capture is not None and capture.exists():
            try:
                if not os.path.lexists(target):
                    os.replace(capture, target)
                else:
                    raise ConfigInstallError(
                        "concurrent configuration target appeared; private recovery backup retained"
                    ) from exc
            except ConfigInstallError:
                raise
            except OSError as restore_error:
                raise ConfigInstallError(
                    "exclusive publication failed and the original target could not be restored"
                ) from restore_error
        if isinstance(exc, ConfigInstallError):
            raise
        raise ConfigInstallError("exclusive configuration publication failed") from exc


def _cleanup_stage_home(stage_home: Path) -> BaseException | None:
    if not stage_home.exists() and not os.path.lexists(stage_home):
        return None
    try:
        workspace._reject_link_components(stage_home)
        if workspace._is_link_or_junction(stage_home):
            raise ConfigInstallError("staging directory became a redirect")
        shutil.rmtree(stage_home)
        if stage_home.exists() or os.path.lexists(stage_home):
            raise ConfigInstallError("staging directory cleanup is incomplete")
    except Exception as exc:
        return exc
    return None


def _apply_config_locked(
    plan: Mapping[str, Any],
    root: os.PathLike[str] | str,
    home: os.PathLike[str] | str,
    *,
    codex: str | os.PathLike[str] | runtime.Runtime | None = None,
) -> AppliedConfig:
    """Apply one plan through a staged native writer and return rollback state."""

    _validate_plan_digest(plan)
    root_path, home_path, target = _safe_paths(root, home)
    source_raw, source_config = _source_intent(root_path)
    source_sha = _sha_bytes(source_raw)
    if plan.get("source_sha256") != source_sha:
        raise ConfigInstallError("project configuration changed after planning")
    target_raw, target_config, target_sha = _assert_target_fresh(target, plan.get("target_sha256"))
    owned, remove, needs_write = _configuration_delta(source_config, target_config)
    if plan.get("owned") != owned or plan.get("remove") != remove or plan.get("needs_write") != needs_write:
        raise ConfigInstallError("configuration plan no longer matches the source intent")
    if not needs_write:
        return AppliedConfig(
            {
                "schema": SCHEMA_RECEIPT,
                "status": "unchanged",
                "target": "user-config",
                "before_sha256": target_sha,
                "after_sha256": target_sha,
                "changed_keys": [],
                "removed_keys": [],
                "source_sha256": source_sha,
                "plan_sha256": plan["plan_sha256"],
            },
            target,
            None,
            target_sha,
            target_sha or "",
            target_raw is not None,
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    workspace._reject_link_components(target.parent)
    backup = _backup_file(home_path, target, target_raw)
    stage_home = Path(tempfile.mkdtemp(prefix=".codex-nexus-stage-", dir=str(home_path)))
    stage_config_dir = stage_home / ".codex"
    stage_config_dir.mkdir()
    stage = stage_config_dir / "config.toml"
    published = False
    result: AppliedConfig | None = None
    failure: BaseException | None = None
    staged_raw: bytes | None = None
    try:
        if target_raw is not None:
            stage.write_bytes(target_raw)
        edits = _owned_edits(owned, remove)
        selected = codex if isinstance(codex, runtime.Runtime) else runtime.resolve_runtime(codex, codex_home=home_path / ".codex")
        if selected.executable is None:
            raise ConfigInstallError("a compatible Codex executable is required for configuration apply")
        _native_batch_write(selected.executable, stage_home, stage, edits)
        staged_raw, _ = _verify_stage(stage, target_config, owned, remove)
        _assert_target_fresh(target, target_sha)
        _publish_exclusive(target, stage, target_sha)
        published = True
        after_raw, _ = _read_toml(target, source=False)
        after_sha = _sha_bytes(after_raw)
        _verify_stage(target, target_config, owned, remove)
        receipt = {
            "schema": SCHEMA_RECEIPT,
            "status": "applied",
            "target": "user-config",
            "before_sha256": target_sha,
            "after_sha256": after_sha,
            "backup_relative": backup.relative_to(home_path).as_posix() if backup else None,
            "changed_keys": sorted(owned),
            "removed_keys": sorted(remove),
            "source_sha256": source_sha,
            "plan_sha256": plan["plan_sha256"],
        }
        result = AppliedConfig(receipt, target, backup, target_sha, after_sha, target_raw is not None)
    except Exception as original:
        failure = original
        if published:
            try:
                current_sha = _target_state(target)[2]
                if current_sha is None:
                    raise ConfigInstallError("configuration target disappeared before rollback")
                if staged_raw is None:
                    raise ConfigInstallError("staged configuration bytes are unavailable for rollback")
                _replace_from_backup(
                    target,
                    backup,
                    target_raw is not None,
                    _sha_bytes(staged_raw),
                    target_sha,
                )
            except Exception as rollback_error:
                failure = ConfigInstallError(
                    "configuration apply failed and rollback was incomplete"
                )
                failure.__cause__ = rollback_error
    finally:
        cleanup_failure = _cleanup_stage_home(stage_home)
        if cleanup_failure is not None:
            if failure is not None:
                failure = ConfigInstallError(
                    "configuration operation failed and staging cleanup was incomplete"
                )
                failure.__cause__ = cleanup_failure
            elif result is not None:
                result.receipt["warnings"] = ["staging cleanup was incomplete"]
            else:
                failure = ConfigInstallError("staging cleanup was incomplete")
                failure.__cause__ = cleanup_failure
    if failure is not None:
        raise failure
    if result is None:
        raise ConfigInstallError("configuration operation produced no result")
    return result


def apply_config(
    plan: Mapping[str, Any],
    root: os.PathLike[str] | str,
    home: os.PathLike[str] | str,
    *,
    codex: str | os.PathLike[str] | runtime.Runtime | None = None,
) -> AppliedConfig:
    """Apply one plan while serializing the target freshness boundary."""

    root_path, _home_path, target = _safe_paths(root, home)
    # Reject malformed source intent before creating the private lock file.
    _source_intent(root_path)
    with _target_lock(target):
        return _apply_config_locked(plan, root, home, codex=codex)


def rollback_config(applied: AppliedConfig) -> None:
    """Restore the exact pre-apply bytes after a later setup step fails."""

    if applied.receipt.get("status") == "unchanged":
        return
    with _target_lock(applied.target):
        _replace_from_backup(
            applied.target,
            applied.backup,
            applied.target_existed,
            applied.after_sha256,
            applied.before_sha256,
        )


__all__ = [
    "AppliedConfig",
    "ConfigInstallError",
    "LEGACY_KEY_PATHS",
    "SCHEMA_HEALTH",
    "SCHEMA_PLAN",
    "SCHEMA_RECEIPT",
    "apply_config",
    "health",
    "plan_config",
    "rollback_config",
]
