"""Source-bound, untrusted context checkpoint verification.

This module only reads source state and hashes files. It never resumes work,
executes a tool, or grants authority from a checkpoint.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
from typing import Any

from . import workspace


SCHEMA = "codex-nexus/context-checkpoint/v1"
VERSION = 1
DEFAULT_MAX_AGE_SECONDS = 24 * 60 * 60
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_ACTIVE = frozenset({"pending", "in_flight", "running", "review_required", "awaiting_review"})
_TERMINAL = frozenset({"completed", "failed", "cancelled", "rejected"})
_STATES = _ACTIVE | _TERMINAL
_VERIFIER_STATES = frozenset({"observed", "skipped", "failed", "pending"})
_QUESTION_OPEN = frozenset({"open", "unresolved", "pending"})
_QUESTION_RESOLVED = frozenset({"answered", "resolved", "closed"})
_QUESTION_STATES = _QUESTION_OPEN | _QUESTION_RESOLVED


class CheckpointError(ValueError):
    """Raised when a checkpoint cannot be constructed safely."""


def _issue(code: str, path: str, message: str, **extra: Any) -> dict[str, Any]:
    result = {"code": code, "path": path, "message": message}
    result.update(extra)
    return result


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        result = datetime.fromisoformat(text)
    except ValueError:
        return None
    return result.astimezone(timezone.utc) if result.tzinfo else None


def _time_text(value: datetime | str | None) -> str:
    value = datetime.now(timezone.utc) if value is None else value
    if isinstance(value, str):
        value = _parse_time(value)
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise CheckpointError("created_at must be timezone-aware ISO-8601 time")
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _safe_rel(value: Any) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise CheckpointError("path must be a non-empty string")
    if "\\" in value or value.startswith("/") or ":" in value:
        raise CheckpointError(f"path is not a relative POSIX path: {value!r}")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if posix.is_absolute() or windows.drive or windows.root:
        raise CheckpointError(f"path is not relative: {value!r}")
    if value != posix.as_posix() or not posix.parts:
        raise CheckpointError(f"path is not normalized: {value!r}")
    if any(part in {".", "..", ""} for part in posix.parts):
        raise CheckpointError(f"path contains an unsafe component: {value!r}")
    return value


def _inventory(root: Path) -> tuple[Path | None, dict[str, Path], list[dict[str, Any]]]:
    try:
        root_path = workspace._safe_root(root)
        files = workspace.source_files(root_path)
    except (CheckpointError, OSError, RuntimeError, ValueError, workspace.WorkspaceError) as exc:
        return None, {}, [_issue("unsafe-root", "root", str(exc))]
    return (
        root_path,
        {item.relative_to(root_path).as_posix(): item for item in files},
        [],
    )


def _requested_rel(root: Path, raw: os.PathLike[str] | str) -> str:
    if not isinstance(raw, (str, os.PathLike)):
        raise CheckpointError("file entry must be path-like")
    requested = Path(raw).expanduser()
    candidate = requested if requested.is_absolute() else root / requested
    lexical = candidate.absolute()
    try:
        lexical.relative_to(root.absolute())
        resolved = lexical.resolve(strict=True)
        return _safe_rel(resolved.relative_to(root.resolve(strict=True)).as_posix())
    except (OSError, RuntimeError, ValueError) as exc:
        raise CheckpointError(f"file path leaves root or is missing: {raw!s}") from exc


def _generation(value: Any, label: str) -> int:
    if not _integer(value) or value < 0:
        raise CheckpointError(f"{label} must be a non-negative integer")
    return value


def _work_items(
    values: Any, generation: int, label: str
) -> list[dict[str, Any]]:
    if values is None:
        return []
    if isinstance(values, (str, bytes, bytearray)) or not isinstance(values, Sequence):
        raise CheckpointError(f"{label} must be a list")
    output: list[dict[str, Any]] = []
    for index, value in enumerate(values):
        path = f"{label}[{index}]"
        if isinstance(value, str):
            item: dict[str, Any] = {"id": value, "state": "pending"}
        elif isinstance(value, Mapping):
            item = dict(value)
        else:
            raise CheckpointError(f"{path} must be an object or string ID")
        if not _text(item.get("id")):
            raise CheckpointError(f"{path}.id is required")
        item["id"] = item["id"].strip()
        item["state"] = item.get("state", "pending")
        if not isinstance(item["state"], str) or item["state"] not in _STATES:
            raise CheckpointError(f"{path}.state is unsupported")
        item["generation"] = _generation(item.get("generation", generation), f"{path}.generation")
        output.append(item)
    return output


def _questions(values: Any) -> list[dict[str, Any]]:
    if values is None:
        return []
    if isinstance(values, (str, bytes, bytearray)) or not isinstance(values, Sequence):
        raise CheckpointError("unresolved_questions must be a list")
    output: list[dict[str, Any]] = []
    for index, value in enumerate(values):
        if isinstance(value, str):
            item = {"question": value, "status": "open"}
        elif isinstance(value, Mapping):
            item = dict(value)
        else:
            item = {}
        if not _text(item.get("question")):
            raise CheckpointError(f"unresolved_questions[{index}] is invalid")
        item.setdefault("status", "open")
        if not isinstance(item["status"], str) or item["status"] not in _QUESTION_STATES:
            raise CheckpointError(f"unresolved_questions[{index}].status is unsupported")
        output.append(item)
    return output


def _verifiers(values: Any) -> list[dict[str, Any]]:
    if values is None:
        return []
    if isinstance(values, (str, bytes, bytearray)) or not isinstance(values, Sequence):
        raise CheckpointError("verifier_receipts must be a list")
    output: list[dict[str, Any]] = []
    for index, value in enumerate(values):
        if not isinstance(value, Mapping) or not _text(value.get("name")):
            raise CheckpointError(f"verifier_receipts[{index}] is invalid")
        item = dict(value)
        item.setdefault("state", "skipped")
        output.append(item)
    return output


def create_checkpoint(
    root: Path,
    files: Sequence[os.PathLike[str] | str] | None,
    goal: str | Mapping[str, Any],
    next_action: str,
    *,
    done_condition: str | None = None,
    generation: int = 0,
    pending_tools: Sequence[Mapping[str, Any] | str] | None = None,
    pending_delegations: Sequence[Mapping[str, Any] | str] | None = None,
    unresolved_questions: Sequence[Mapping[str, Any] | str] | None = None,
    verifier_receipts: Sequence[Mapping[str, Any]] | None = None,
    empty_source_reason: str | None = None,
    created_at: datetime | str | None = None,
    destination: Path | None = None,
) -> dict[str, Any]:
    """Create a hash-bound packet; this function performs no continuation."""

    root_path, source_map, root_errors = _inventory(Path(root))
    if root_errors or root_path is None:
        raise CheckpointError(root_errors[0]["message"])
    generation_value = _generation(generation, "generation")
    if isinstance(goal, Mapping):
        statement = goal.get("statement")
        condition = goal.get("done_condition", done_condition)
    else:
        statement, condition = goal, done_condition
    if not _text(statement) or not _text(condition):
        raise CheckpointError("goal statement and done_condition are required")
    if not _text(next_action):
        raise CheckpointError("next_action is required")
    selected = list(source_map)
    if files is not None:
        if isinstance(files, (str, bytes, bytearray)) or not isinstance(files, Sequence):
            raise CheckpointError("files must be a list or None")
        selected = [_requested_rel(root_path, value) for value in files]
        if any(value not in source_map for value in selected):
            raise CheckpointError("files must be current source files")
    if len(selected) != len(set(selected)):
        raise CheckpointError("files contains duplicate paths")
    selected.sort()
    source_entries = [
        {"path": value, "sha256": workspace.sha256(source_map[value]), "size": source_map[value].stat().st_size}
        for value in selected
    ]
    source = {"root_relative": ".", "files": source_entries}
    if not source_entries:
        if not _text(empty_source_reason):
            raise CheckpointError("empty source selection requires empty_source_reason")
        source["mode"] = "empty"
        source["empty_reason"] = empty_source_reason.strip()
    tools = _work_items(pending_tools, generation_value, "pending_tools")
    delegations = _work_items(pending_delegations, generation_value, "pending_delegations")
    identifiers: set[str] = set()
    for item in [*tools, *delegations]:
        if item["id"] in identifiers:
            raise CheckpointError(f"duplicate pending ID: {item['id']}")
        identifiers.add(item["id"])
    packet: dict[str, Any] = {
        "schema": SCHEMA,
        "version": VERSION,
        "created_at": _time_text(created_at),
        "goal": {"statement": statement.strip(), "done_condition": condition.strip()},
        "generation": generation_value,
        "source": source,
        "pending_tools": tools,
        "pending_delegations": delegations,
        "unresolved_questions": _questions(unresolved_questions),
        "next_action": next_action.strip(),
        "verifier_receipts": _verifiers(verifier_receipts),
        "authority": {
            "untrusted": True,
            "requires_current_user_recheck": True,
            "requires_current_environment_recheck": True,
        },
    }
    if destination is not None:
        target = Path(destination).expanduser().absolute()
        if os.path.lexists(target):
            raise CheckpointError("destination already exists")
        ancestor = target.parent
        while ancestor != ancestor.parent:
            is_junction = getattr(ancestor, "is_junction", None)
            if ancestor.is_symlink() or (callable(is_junction) and is_junction()):
                raise CheckpointError("destination parent must not redirect through a link")
            ancestor = ancestor.parent
        try:
            target.relative_to(root_path.absolute())
        except ValueError:
            try:
                target.resolve(strict=False).relative_to(root_path.resolve(strict=True))
            except (OSError, RuntimeError, ValueError):
                try:
                    workspace.write_json(target, packet, overwrite=False)
                except FileExistsError as exc:
                    raise CheckpointError("destination already exists") from exc
            else:
                raise CheckpointError("destination resolves inside source root")
        else:
            raise CheckpointError("destination must be outside source root")
    return packet


def _receipt_present(value: Any) -> bool:
    """Accept nonempty recorded output, never scalar success placeholders."""
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return bool(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return bool(value)
    return False


def validate_checkpoint(
    packet: Mapping[str, Any] | Any,
    root: Path,
    *,
    now: datetime | None = None,
    max_age_seconds: int | None = DEFAULT_MAX_AGE_SECONDS,
    expected_generation: int | None = None,
) -> dict[str, Any]:
    """Validate packet preconditions without resuming or proving completion."""

    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    stale_files: list[str] = []
    blockers: list[str] = []
    result: dict[str, Any] = {
        "ok": False,
        "errors": errors,
        "warnings": warnings,
        "stale_files": stale_files,
        "pending_blockers": blockers,
        "authority_granted": False,
        "requires_current_authority_recheck": True,
        "checked_files": 0,
        "resumed": False,
        "declared_receipts": 0,
        "observed_receipts": 0,
        "recorded_checks_complete": False,
        "completion_proven": False,
        "continuation_ready": False,
        "requires_current_verifier_recheck": True,
    }
    if not isinstance(packet, Mapping):
        errors.append(_issue("packet-type", "packet", "packet must be an object"))
        return result
    data = packet
    if data.get("schema") != SCHEMA:
        errors.append(_issue("schema", "schema", f"expected {SCHEMA}"))
    if data.get("version") != VERSION:
        errors.append(_issue("version", "version", f"expected {VERSION}"))
    created = _parse_time(data.get("created_at"))
    if created is None:
        errors.append(_issue("created-at", "created_at", "timezone-aware ISO-8601 time is required"))
    else:
        current = now.astimezone(timezone.utc) if isinstance(now, datetime) else datetime.now(timezone.utc)
        age = (current - created).total_seconds()
        if age < -60:
            errors.append(_issue("future-created-at", "created_at", "timestamp is in the future"))
        if max_age_seconds is not None:
            if not _integer(max_age_seconds) or max_age_seconds < 0:
                errors.append(_issue("max-age", "max_age_seconds", "must be non-negative"))
            elif age > max_age_seconds:
                errors.append(_issue("stale-checkpoint", "created_at", "checkpoint is outside freshness window"))
    generation = data.get("generation")
    generation_value = generation if _integer(generation) and generation >= 0 else None
    if generation_value is None:
        errors.append(_issue("generation", "generation", "must be a non-negative integer"))
    else:
        result["generation"] = generation_value
        if expected_generation is not None and generation_value != expected_generation:
            errors.append(_issue("generation-mismatch", "generation", "generation differs from caller state"))
    goal = data.get("goal")
    if not isinstance(goal, Mapping):
        errors.append(_issue("goal", "goal", "statement and done_condition are required"))
    else:
        for field in ("statement", "done_condition"):
            if not _text(goal.get(field)):
                errors.append(_issue("goal-field", f"goal.{field}", "must be non-empty text"))
    if not _text(data.get("next_action")):
        errors.append(_issue("next-action", "next_action", "must be non-empty text"))
    authority = data.get("authority")
    if not isinstance(authority, Mapping):
        errors.append(_issue("authority", "authority", "untrusted authority declaration is required"))
    else:
        for field in (
            "untrusted",
            "requires_current_user_recheck",
            "requires_current_environment_recheck",
        ):
            if authority.get(field) is not True:
                errors.append(_issue("authority-boundary", f"authority.{field}", "must remain true"))
    if data.get("authority_granted") is True:
        errors.append(_issue("authority-claim", "authority_granted", "checkpoint cannot grant authority"))

    root_path, source_map, root_errors = _inventory(Path(root))
    errors.extend(root_errors)
    source = data.get("source")
    entries = source.get("files") if isinstance(source, Mapping) else None
    if not isinstance(source, Mapping):
        errors.append(_issue("source", "source", "source.files is required"))
        entries = []
    if not isinstance(entries, list):
        errors.append(_issue("source-files", "source.files", "must be a list"))
        entries = []
    if not entries:
        if not isinstance(source, Mapping) or source.get("mode") != "empty" or not _text(source.get("empty_reason")):
            errors.append(
                _issue(
                    "empty-source-unexplained",
                    "source",
                    "empty source selection requires an explicit mode and reason",
                )
            )
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        label = f"source.files[{index}]"
        if not isinstance(entry, Mapping):
            errors.append(_issue("source-entry", label, "must be an object"))
            continue
        try:
            relative = _safe_rel(entry.get("path"))
        except CheckpointError as exc:
            errors.append(_issue("path-escape", f"{label}.path", str(exc)))
            continue
        if relative in seen:
            errors.append(_issue("duplicate-source-path", f"{label}.path", relative))
            continue
        seen.add(relative)
        digest = entry.get("sha256")
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            errors.append(_issue("source-hash", f"{label}.sha256", "must be 64 hex characters"))
            continue
        if root_path is None or relative not in source_map:
            errors.append(_issue("source-path-not-current", f"{label}.path", "not in safe source inventory"))
            continue
        try:
            current_digest = workspace.sha256(source_map[relative])
        except (workspace.WorkspaceError, OSError, ValueError) as exc:
            errors.append(_issue("source-read", f"{label}.path", str(exc)))
            continue
        result["checked_files"] += 1
        if current_digest.lower() != digest.lower():
            stale_files.append(relative)
            errors.append(
                _issue(
                    "stale-source-file",
                    f"{label}.sha256",
                    "current source hash differs from checkpoint",
                    source_path=relative,
                    expected=digest,
                    observed=current_digest,
                )
            )

    pending = data.get("pending")
    lists = {
        "pending_tools": data.get("pending_tools"),
        "pending_delegations": data.get("pending_delegations"),
    }
    if isinstance(pending, Mapping):
        lists["pending_tools"] = lists["pending_tools"] if lists["pending_tools"] is not None else pending.get("tools", [])
        lists["pending_delegations"] = lists["pending_delegations"] if lists["pending_delegations"] is not None else pending.get("delegations", [])
    identifiers: set[str] = set()
    for label, values in lists.items():
        values = [] if values is None else values
        if not isinstance(values, list):
            errors.append(_issue("pending-type", label, "must be a list"))
            continue
        for index, item in enumerate(values):
            path = f"{label}[{index}]"
            if not isinstance(item, Mapping):
                errors.append(_issue("pending-entry", path, "must be an object"))
                continue
            identifier = item.get("id")
            if not _text(identifier):
                errors.append(_issue("pending-id", f"{path}.id", "must be non-empty text"))
                continue
            identifier = identifier.strip()
            if identifier in identifiers:
                errors.append(_issue("duplicate-pending-id", f"{path}.id", identifier))
            identifiers.add(identifier)
            state = item.get("state")
            if not isinstance(state, str) or state not in _STATES:
                errors.append(_issue("pending-state", f"{path}.state", f"unsupported state: {state!r}"))
            item_generation = item.get("generation")
            if not _integer(item_generation) or item_generation < 0:
                errors.append(_issue("pending-generation", f"{path}.generation", "must be non-negative integer"))
            elif generation_value is not None and item_generation != generation_value:
                code = "late-result-generation" if (isinstance(state, str) and state in _TERMINAL) or item.get("result_generation") is not None else "stale-pending-generation"
                errors.append(_issue(code, f"{path}.generation", "work belongs to another goal generation", id=identifier))
            result_generation = item.get("result_generation")
            if result_generation is not None and (
                not _integer(result_generation) or generation_value is None or result_generation != generation_value
            ):
                errors.append(_issue("late-result-generation", f"{path}.result_generation", "result is not current", id=identifier))
            if isinstance(state, str) and state in _ACTIVE:
                blockers.append(identifier)
                errors.append(_issue("pending-work", f"{path}.state", "pending work will not be resumed", id=identifier))

    questions = data.get("unresolved_questions")
    if not isinstance(questions, list):
        errors.append(_issue("questions", "unresolved_questions", "must be a list"))
        questions = []
    for index, item in enumerate(questions):
        path = f"unresolved_questions[{index}]"
        if isinstance(item, str):
            question, status = item, "open"
        elif isinstance(item, Mapping):
            question, status = item.get("question"), item.get("status", "open")
        else:
            errors.append(_issue("question-entry", path, "must be text or object"))
            continue
        if not _text(question):
            errors.append(_issue("question", path, "question must be non-empty"))
        if not isinstance(status, str) or status not in _QUESTION_STATES:
            errors.append(_issue("question-status", f"{path}.status", "unsupported question status"))
        elif status in _QUESTION_OPEN:
            errors.append(_issue("unresolved-question", path, "question blocks safe continuation"))

    receipts = data.get("verifier_receipts", data.get("verifiers"))
    if receipts is None:
        receipts = []
    elif not isinstance(receipts, list):
        errors.append(_issue("verifiers", "verifier_receipts", "must be a list"))
        receipts = []
    result["declared_receipts"] = len(receipts)
    names: set[str] = set()
    observed_receipts = 0
    verifier_records_valid = True
    for index, receipt in enumerate(receipts):
        path = f"verifier_receipts[{index}]"
        if not isinstance(receipt, Mapping):
            errors.append(_issue("verifier-entry", path, "must be an object"))
            verifier_records_valid = False
            continue
        name, state = receipt.get("name"), receipt.get("state")
        if not _text(name):
            errors.append(_issue("verifier-name", f"{path}.name", "name is required"))
            verifier_records_valid = False
        elif name in names:
            errors.append(_issue("duplicate-verifier", f"{path}.name", name))
            verifier_records_valid = False
        else:
            names.add(name)
        if not isinstance(state, str) or state not in _VERIFIER_STATES:
            errors.append(_issue("verifier-state", f"{path}.state", f"unsupported state: {state!r}"))
            verifier_records_valid = False
        elif state == "observed" and not _receipt_present(receipt.get("receipt")):
            errors.append(_issue("verifier-receipt", f"{path}.receipt", "observed receipt is required"))
            verifier_records_valid = False
        elif state == "observed":
            observed_receipts += 1
        elif state == "skipped":
            errors.append(_issue("verifier-skipped", f"{path}.state", "skipped is not a pass"))
            verifier_records_valid = False
        elif state in {"failed", "pending"}:
            errors.append(_issue("verifier-incomplete", f"{path}.state", f"{state} is not observed success"))
            verifier_records_valid = False

    result["pending_blockers"] = sorted(set(blockers))
    result["observed_receipts"] = observed_receipts
    result["recorded_checks_complete"] = bool(
        receipts and verifier_records_valid and observed_receipts == len(receipts)
    )
    if not receipts:
        warnings.append(
            _issue(
                "verifier-not-run",
                "verifier_receipts",
                "no verifier was run; the checkpoint is a noncompletion record",
            )
        )
    # A local packet and a recorded receipt cannot establish that the goal was
    # completed.  Keep this permanently false; semantic completion requires a
    # current, independent verifier outside this offline helper.
    result["completion_proven"] = False
    # This is only an advisory handoff signal.  A caller must still perform the
    # current verifier and authority checks represented by the two requirements
    # above; any validation error or pending blocker keeps it false.
    result["continuation_ready"] = bool(
        not errors and result["recorded_checks_complete"] and not blockers
    )
    # `ok` describes packet preconditions (shape, freshness, hashes, and
    # blockers), not the task's semantic completion.
    result["ok"] = not errors
    return result


__all__ = [
    "CheckpointError",
    "DEFAULT_MAX_AGE_SECONDS",
    "SCHEMA",
    "VERSION",
    "create_checkpoint",
    "validate_checkpoint",
]
