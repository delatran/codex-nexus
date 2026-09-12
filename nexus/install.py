"""Codex-only managed-link installation and health inspection.

The installation surface is intentionally small: the shared skill directory
and the global Codex instruction file.  Health is read-only.  A normal apply
creates missing links.  Real user files, directories, and redirects remain
conflicts unless an explicit replacement plan has a hash-bound backup.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import workspace


SCHEMA_HEALTH = "codex-nexus/install-health/v1"
SCHEMA_INSTALL = "codex-nexus/install-plan/v1"
SCHEMA_OWNERSHIP = "codex-nexus/install-ownership/v1"
OWNERSHIP_RELATIVE = ".codex-nexus-quarantine/install-ownership.json"
_OWNERSHIP_KEYS = frozenset(
    {
        "schema",
        "source_root",
        "home",
        "source_relative",
        "installed_relative",
        "target_kind",
        "source_sha256",
        "target_sha256",
        "source_identity",
        "target_identity",
    }
)
_OWNERSHIP_KINDS = frozenset({"managed_hardlink", "managed_link"})

INSTALL_MATRIX: tuple[dict[str, str], ...] = (
    {
        "name": "skills",
        "installed_relative": ".agents/skills",
        "source_relative": "skills",
        "kind": "directory",
    },
    {
        "name": "global_instructions",
        "installed_relative": ".codex/AGENTS.md",
        "source_relative": "AGENTS.md",
        "kind": "file",
    },
)


class InstallError(RuntimeError):
    """Raised for unsafe or incomplete installation operations."""


@dataclass(frozen=True)
class _OwnershipState:
    path: Path
    raw: bytes | None
    sha256: str | None
    record: dict[str, Any] | None
    target_matches: bool


@dataclass(frozen=True)
class _OwnershipMutation:
    path: Path
    before_sha256: str | None
    after_sha256: str
    before_exists: bool
    backup_dir: Path | None
    receipt_backup: Path | None
    stale_backup: Path | None


@dataclass(frozen=True)
class _RefreshedTarget:
    installed: Path
    source: Path
    backup: Path
    expected_sha256: str
    expected_identity: tuple[int, int]


def _as_path(value: os.PathLike[str] | str, *, label: str) -> Path:
    if not isinstance(value, (str, os.PathLike)):
        raise TypeError(f"{label} must be a path-like value")
    return Path(value).expanduser()


def _absolute(value: os.PathLike[str] | str, *, label: str) -> Path:
    return _as_path(value, label=label).absolute()


def _is_within(path: Path, parent: Path, *, allow_equal: bool = False) -> bool:
    try:
        relative = path.relative_to(parent)
    except ValueError:
        return False
    return allow_equal or relative != Path(".")


def _reparse_tag(path: Path) -> int:
    try:
        result = os.lstat(path)
    except (FileNotFoundError, OSError):
        return 0
    return int(getattr(result, "st_reparse_tag", 0) or 0)


def _is_managed_link(path: Path) -> bool:
    if os.path.islink(path):
        return True
    return _reparse_tag(path) in {0xA000000C, 0xA0000003}


def _path_kind(path: Path) -> str:
    if _is_managed_link(path):
        return "link"
    try:
        result = os.lstat(path)
    except FileNotFoundError:
        return "missing"
    if stat.S_ISDIR(result.st_mode):
        return "directory"
    if stat.S_ISREG(result.st_mode):
        return "file"
    return "special"


def _safe_source(root: Path, relative: str, expected_kind: str) -> Path:
    source = root / Path(relative)
    lexical = source.absolute()
    if not _is_within(lexical, root.absolute()):
        raise InstallError(f"source path leaves root: {relative}")
    if _is_managed_link(root) or _is_managed_link(lexical):
        raise InstallError(f"source path is a link or junction: {relative}")
    _reject_link_ancestors(lexical.parent)
    resolved = lexical.resolve(strict=False)
    if not _is_within(resolved, root.resolve(strict=True)):
        raise InstallError(f"source path resolves outside root: {relative}")
    actual = _path_kind(resolved)
    if actual != expected_kind:
        raise InstallError(f"source kind mismatch for {relative}: expected {expected_kind}, got {actual}")
    return resolved


def _reject_link_ancestors(path: Path) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        if os.path.lexists(current) and _is_managed_link(current):
            raise InstallError(f"installation parent is a link or junction: {current}")


def _safe_home(home: os.PathLike[str] | str) -> Path:
    home_path = _absolute(home, label="home")
    # A regular child below a junction is still outside the requested home.
    # Inspect all existing ancestors before canonicalizing the path.
    _reject_link_ancestors(home_path)
    if _is_managed_link(home_path):
        raise InstallError("home must not be a symbolic link or junction")
    # Health and planning are read-only.  The parent may be created only by
    # the explicit link-creation path after all preconditions pass.
    return home_path.resolve(strict=False)


def _target_path(home: Path, relative: str) -> Path:
    target = home / Path(relative)
    if not _is_within(target.absolute(), home.absolute()):
        raise InstallError(f"installed path leaves home: {relative}")
    _reject_link_ancestors(target.parent)
    return target


def _target_status(source: Path, installed: Path, expected_kind: str) -> dict[str, Any]:
    source_resolved = source.resolve(strict=True)
    kind = _path_kind(installed)
    if kind == "missing":
        return {
            "status": "missing",
            "observed_kind": "missing",
            "source_sha256": _source_digest(source) if expected_kind == "file" else None,
        }
    if kind == "link":
        try:
            observed_target = installed.resolve(strict=False)
        except OSError:
            observed_target = Path("<unresolved>")
        if observed_target == source_resolved:
            return {"status": "correct", "observed_kind": "managed_link"}
        return {
            "status": "conflict",
            # A redirect is not evidence of Codex Nexus ownership.  Without
            # an ownership marker, an arbitrary symlink or junction is a
            # custom path and requires the explicit replacement workflow.
            "observed_kind": "custom_link",
            "observed_target": str(observed_target),
        }
    if expected_kind == "file" and kind == "file" and _same_file(source_resolved, installed):
        return {"status": "correct", "observed_kind": "managed_hardlink"}
    return {
        "status": "conflict",
        "observed_kind": kind,
        "observed_sha256": _source_digest(installed) if kind == "file" else None,
    }


def _source_digest(path: Path) -> str | None:
    if _path_kind(path) != "file":
        return None
    return workspace.sha256(path)


def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _same_file(left: Path, right: Path) -> bool:
    """Return true only when both paths resolve to the same file identity."""

    try:
        return os.path.samefile(left, right)
    except (FileNotFoundError, OSError):
        return False


def _file_identity(path: Path) -> list[int]:
    """Return the stable device and inode identity for one directory entry."""

    try:
        observed = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise InstallError(f"cannot inspect file identity: {path}") from exc
    return [int(observed.st_dev), int(observed.st_ino)]


def _valid_identity(value: object) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and all(type(item) is int and item >= 0 for item in value)
    )


def _ownership_path(home: Path) -> Path:
    path = home / Path(OWNERSHIP_RELATIVE)
    if not _is_within(path.absolute(), home.absolute()):
        raise InstallError("ownership receipt leaves the selected home")
    _reject_link_ancestors(path.parent)
    if os.path.lexists(path) and _is_managed_link(path):
        raise InstallError("ownership receipt is a redirect")
    return path


def _ownership_target_sha(source: Path, installed: Path) -> str:
    kind = _path_kind(installed)
    if kind == "file":
        return _source_digest(installed) or ""
    if kind == "link":
        try:
            observed = installed.resolve(strict=True)
        except OSError as exc:
            raise InstallError("managed instruction link cannot be resolved") from exc
        if observed != source.resolve(strict=True):
            raise InstallError("instruction link does not target the project source")
        return workspace.sha256(source)
    raise InstallError("managed instruction target is not a regular file or link")


def _read_ownership(root: Path, home: Path) -> _OwnershipState:
    """Read and validate the private receipt without trusting it as authority."""

    path = _ownership_path(home)
    if not os.path.lexists(path):
        return _OwnershipState(path, None, None, None, False)
    if not path.is_file() or _is_managed_link(path):
        raise InstallError("ownership receipt is not a regular file")
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InstallError("ownership receipt is malformed") from exc
    if not isinstance(value, dict) or set(value) != _OWNERSHIP_KEYS:
        raise InstallError("ownership receipt has an unsupported shape")
    record = dict(value)
    if record.get("schema") != SCHEMA_OWNERSHIP:
        raise InstallError("ownership receipt has an unsupported schema")
    if record.get("source_relative") != "AGENTS.md" or record.get("installed_relative") != ".codex/AGENTS.md":
        raise InstallError("ownership receipt targets an unsupported path")
    if record.get("target_kind") not in _OWNERSHIP_KINDS:
        raise InstallError("ownership receipt has an unsupported target kind")
    if any(
        not isinstance(record.get(name), str)
        or not re.fullmatch(r"[0-9a-fA-F]{64}", record[name])
        for name in ("source_sha256", "target_sha256")
    ):
        raise InstallError("ownership receipt contains an invalid digest")
    if not _valid_identity(record.get("source_identity")) or not _valid_identity(record.get("target_identity")):
        raise InstallError("ownership receipt contains an invalid file identity")
    # Source fields record the pre-replacement source state. They are evidence
    # that the receipt was internally coherent, not a current-source equality
    # check because atomic replacement intentionally changes that identity.
    if record["source_sha256"] != record["target_sha256"]:
        raise InstallError("ownership receipt contains inconsistent source evidence")
    if record["target_kind"] == "managed_hardlink" and record["source_identity"] != record["target_identity"]:
        raise InstallError("ownership receipt contains inconsistent hardlink evidence")
    try:
        receipt_root = Path(record["source_root"]).expanduser().resolve(strict=False)
        receipt_home = Path(record["home"]).expanduser().resolve(strict=False)
    except (KeyError, OSError, RuntimeError, TypeError) as exc:
        raise InstallError("ownership receipt contains invalid roots") from exc
    if receipt_root != root or receipt_home != home:
        raise InstallError("ownership receipt is bound to a different source or home")

    installed = _target_path(home, ".codex/AGENTS.md")
    target_matches = False
    if os.path.lexists(installed):
        try:
            current_sha = _ownership_target_sha(root / "AGENTS.md", installed)
            current_identity = _file_identity(installed)
        except InstallError:
            pass
        else:
            target_matches = (
                current_sha == record["target_sha256"]
                and current_identity == record["target_identity"]
            )
    return _OwnershipState(
        path,
        raw,
        _sha_bytes(raw),
        record,
        target_matches,
    )


def _ownership_record(root: Path, home: Path, installed: Path) -> dict[str, Any]:
    source = _safe_source(root, "AGENTS.md", "file")
    target_kind = "managed_link" if _is_managed_link(installed) else "managed_hardlink"
    target_sha = _ownership_target_sha(source, installed)
    if target_kind == "managed_hardlink" and not _same_file(source, installed):
        raise InstallError("instruction target is not the project file identity")
    return {
        "schema": SCHEMA_OWNERSHIP,
        "source_root": str(root),
        "home": str(home),
        "source_relative": "AGENTS.md",
        "installed_relative": ".codex/AGENTS.md",
        "target_kind": target_kind,
        "source_sha256": workspace.sha256(source),
        "target_sha256": target_sha,
        "source_identity": _file_identity(source),
        "target_identity": _file_identity(installed),
    }


def _ownership_bytes(record: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(record, ensure_ascii=True, indent=2, sort_keys=True, separators=(",", ": "))
        + "\n"
    ).encode("utf-8")


def _ownership_meta(state: _OwnershipState) -> dict[str, str | None]:
    return {"path": OWNERSHIP_RELATIVE, "sha256": state.sha256}


def _can_refresh_owned(target: Mapping[str, Any], state: _OwnershipState) -> bool:
    return bool(
        target.get("status") == "conflict"
        and target.get("observed_kind") == "file"
        and state.record is not None
        and state.record.get("target_kind") == "managed_hardlink"
        and state.target_matches
    )


def _new_backup_dir(home: Path) -> Path:
    parent = home / ".codex-nexus-quarantine" / "install-backups"
    _reject_link_ancestors(parent)
    if os.path.lexists(parent) and _is_managed_link(parent):
        raise InstallError("installation backup directory is a redirect")
    try:
        parent.mkdir(parents=True, exist_ok=True)
        _reject_link_ancestors(parent)
        return Path(tempfile.mkdtemp(prefix="operation-", dir=str(parent)))
    except OSError as exc:
        raise InstallError("installation backup directory is unavailable") from exc


def _write_private_backup(path: Path, raw: bytes) -> None:
    _reject_link_ancestors(path.parent)
    if os.path.lexists(path):
        raise InstallError("installation backup destination already exists")
    try:
        with path.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise InstallError("installation backup could not be written") from exc
    if _sha_bytes(path.read_bytes()) != _sha_bytes(raw):
        raise InstallError("installation backup integrity check failed")


def _backup_stale_target(
    installed: Path,
    state: _OwnershipState,
    backup_dir: Path,
) -> Path:
    if state.record is None or not state.target_matches:
        raise InstallError("stale instruction target is not owned by Codex Nexus")
    if _path_kind(installed) != "file":
        raise InstallError("stale instruction target is no longer a regular file")
    if _source_digest(installed) != state.record["target_sha256"]:
        raise InstallError("owned instruction target changed before refresh")
    if _file_identity(installed) != state.record["target_identity"]:
        raise InstallError("owned instruction target identity changed before refresh")
    backup = backup_dir / "AGENTS.md"
    _reject_link_ancestors(backup_dir)
    try:
        os.replace(installed, backup)
    except OSError as exc:
        raise InstallError("stale instruction target could not be backed up") from exc
    return backup


def _verify_stale_backup(
    installed: Path,
    backup: Path,
    state: _OwnershipState,
) -> None:
    if state.record is None:
        raise InstallError("stale instruction target is not owned by Codex Nexus")
    if _path_kind(backup) != "file":
        raise InstallError("stale instruction backup is not a regular file")
    if _source_digest(backup) != state.record["target_sha256"]:
        raise InstallError("stale instruction backup failed integrity verification")
    if _file_identity(backup) != state.record["target_identity"]:
        raise InstallError("stale instruction backup identity changed")
    if os.path.lexists(installed):
        raise InstallError("stale instruction target remained after backup")


def _relative_home_path(home: Path, path: Path, *, label: str) -> str:
    try:
        relative = path.absolute().relative_to(home.absolute())
    except ValueError as exc:
        raise InstallError(f"{label} leaves the selected home") from exc
    if relative == Path("."):
        raise InstallError(f"{label} cannot be the selected home")
    return relative.as_posix()


def _restore_ownership_mutation(mutation: _OwnershipMutation) -> list[str]:
    """Restore one receipt only while its post-write bytes remain untouched."""

    failures: list[str] = []
    try:
        path = _ownership_path(mutation.path.parent.parent)
    except InstallError as exc:
        return [f"inspect ownership receipt: {exc}"]
    try:
        if not os.path.lexists(path):
            if mutation.before_exists:
                failures.append("ownership receipt disappeared before rollback")
            return failures
        if _is_managed_link(path) or _path_kind(path) != "file":
            failures.append("ownership receipt became a redirect before rollback")
            return failures
        current = path.read_bytes()
        if _sha_bytes(current) != mutation.after_sha256:
            failures.append("ownership receipt changed before rollback")
            return failures
        if mutation.before_exists:
            if mutation.receipt_backup is None or not os.path.lexists(mutation.receipt_backup):
                failures.append("ownership receipt backup is unavailable")
                return failures
            previous = mutation.receipt_backup.read_bytes()
            if mutation.before_sha256 is None or _sha_bytes(previous) != mutation.before_sha256:
                failures.append("ownership receipt backup failed integrity verification")
                return failures
            workspace.write_text_atomic(path, previous.decode("utf-8"))
            if _sha_bytes(path.read_bytes()) != mutation.before_sha256:
                failures.append("ownership receipt restore failed integrity verification")
        else:
            path.unlink()
            if os.path.lexists(path):
                failures.append("ownership receipt remained after rollback")
    except (OSError, UnicodeDecodeError, InstallError) as exc:
        failures.append(f"restore ownership receipt: {exc}")
    return failures


def _restore_refreshed_targets(
    refreshed: Sequence[_RefreshedTarget],
) -> list[str]:
    failures: list[str] = []
    for item in reversed(refreshed):
        installed, source, backup = item.installed, item.source, item.backup
        if not os.path.lexists(backup):
            failures.append(f"stale instruction backup is unavailable: {backup}")
            continue
        try:
            if _path_kind(backup) != "file":
                raise InstallError("stale instruction backup is not a regular file")
            if _source_digest(backup) != item.expected_sha256:
                raise InstallError("stale instruction backup content changed")
            if tuple(_file_identity(backup)) != item.expected_identity:
                raise InstallError("stale instruction backup identity changed")
        except InstallError as exc:
            failures.append(f"validate stale instruction backup {backup}: {exc}")
            continue
        if os.path.lexists(installed):
            expected_link = False
            if _is_managed_link(installed):
                try:
                    expected_link = installed.resolve(strict=True) == source.resolve(strict=True)
                except OSError:
                    expected_link = False
            else:
                expected_link = _same_file(source, installed)
            if expected_link:
                try:
                    if _is_managed_link(installed):
                        _remove_managed_link(installed)
                    else:
                        installed.unlink()
                except OSError as exc:
                    failures.append(f"remove refreshed target {installed}: {exc}")
                    continue
            else:
                failures.append(f"refreshed target became custom: {installed}")
                continue
        try:
            os.replace(backup, installed)
        except OSError as exc:
            failures.append(f"restore stale instruction target {installed}: {exc}")
            continue
        try:
            if _path_kind(installed) != "file":
                raise InstallError("restored instruction target is not a file")
            if _source_digest(installed) != item.expected_sha256:
                raise InstallError("restored instruction target content changed")
            if tuple(_file_identity(installed)) != item.expected_identity:
                raise InstallError("restored instruction target identity changed")
        except InstallError as exc:
            failures.append(f"validate restored instruction target {installed}: {exc}")
    return failures


def _validate_ownership_backups(mutation: _OwnershipMutation) -> list[str]:
    """Check rollback bytes before removing any newly installed target."""

    failures: list[str] = []
    previous_record: Mapping[str, Any] | None = None
    if mutation.backup_dir is None:
        failures.append("ownership backup directory is missing")
    elif _path_kind(mutation.backup_dir) != "directory":
        failures.append("ownership backup directory is unavailable")
    # The directory is part of the receipt's recovery boundary. Both backup
    # files must remain in it before rollback can consume either one.
    if mutation.backup_dir is not None:
        for label, path in (
            ("ownership receipt", mutation.receipt_backup),
            ("stale instruction", mutation.stale_backup),
        ):
            if path is not None and not _is_within(path.absolute(), mutation.backup_dir.absolute()):
                failures.append(f"{label} backup leaves the operation directory")
    if mutation.before_exists:
        if mutation.receipt_backup is None or _path_kind(mutation.receipt_backup) != "file":
            failures.append("ownership receipt backup is unavailable")
        else:
            try:
                previous = mutation.receipt_backup.read_bytes()
                if mutation.before_sha256 is None or _sha_bytes(previous) != mutation.before_sha256:
                    failures.append("ownership receipt backup failed integrity verification")
                else:
                    value = json.loads(previous.decode("utf-8"))
                    if not isinstance(value, dict) or set(value) != _OWNERSHIP_KEYS:
                        failures.append("ownership receipt backup has an unsupported shape")
                    else:
                        previous_record = value
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                failures.append("ownership receipt backup is malformed")
    elif mutation.receipt_backup is not None:
        failures.append("ownership receipt backup has an unexpected prior state")

    if mutation.stale_backup is not None:
        if previous_record is None:
            failures.append("stale instruction backup has no validated ownership record")
        elif _path_kind(mutation.stale_backup) != "file":
            failures.append("stale instruction backup is unavailable")
        elif _source_digest(mutation.stale_backup) != previous_record.get("target_sha256"):
            failures.append("stale instruction backup failed integrity verification")
        elif not _valid_identity(previous_record.get("target_identity")):
            failures.append("stale instruction backup has an invalid identity")
        else:
            try:
                if _file_identity(mutation.stale_backup) != previous_record["target_identity"]:
                    failures.append("stale instruction backup identity failed integrity verification")
            except InstallError:
                failures.append("stale instruction backup identity is unavailable")
    return failures


def _ownership_mutation_receipt(
    mutation: _OwnershipMutation | None,
    home: Path,
) -> dict[str, Any] | None:
    if mutation is None:
        return None
    return {
        "path": _relative_home_path(home, mutation.path, label="ownership receipt"),
        "before_exists": mutation.before_exists,
        "before_sha256": mutation.before_sha256,
        "after_sha256": mutation.after_sha256,
        "backup_dir": (
            _relative_home_path(home, mutation.backup_dir, label="ownership backup directory")
            if mutation.backup_dir is not None
            else None
        ),
        "receipt_backup": (
            _relative_home_path(home, mutation.receipt_backup, label="ownership receipt backup")
            if mutation.receipt_backup is not None
            else None
        ),
        "stale_backup": (
            _relative_home_path(home, mutation.stale_backup, label="stale instruction backup")
            if mutation.stale_backup is not None
            else None
        ),
    }


def _write_ownership(
    root: Path,
    home: Path,
    expected: _OwnershipState,
    *,
    backup_dir: Path | None = None,
) -> _OwnershipMutation | None:
    """Publish the current instruction ownership record with rollback bytes."""

    current = _read_ownership(root, home)
    if current.raw != expected.raw or current.sha256 != expected.sha256:
        raise InstallError("ownership receipt changed after planning")
    installed = _target_path(home, ".codex/AGENTS.md")
    desired = _ownership_bytes(_ownership_record(root, home, installed))
    if current.raw == desired:
        return None
    operation_backup = backup_dir or _new_backup_dir(home)
    receipt_backup: Path | None = None
    if current.raw is not None:
        receipt_backup = operation_backup / "ownership.json"
        _write_private_backup(receipt_backup, current.raw)
    mutation = _OwnershipMutation(
        path=current.path,
        before_sha256=current.sha256,
        after_sha256=_sha_bytes(desired),
        before_exists=current.raw is not None,
        backup_dir=operation_backup,
        receipt_backup=receipt_backup,
        stale_backup=None,
    )
    try:
        workspace.write_text_atomic(current.path, desired.decode("utf-8"))
        written = current.path.read_bytes()
        if written != desired:
            raise InstallError("ownership receipt write failed integrity verification")
        verified = _read_ownership(root, home)
        if verified.raw != desired or not verified.target_matches:
            raise InstallError("ownership receipt failed post-write validation")
    except Exception:
        rollback_failures = _restore_ownership_mutation(mutation)
        if rollback_failures:
            raise InstallError(
                "ownership receipt write failed and rollback was incomplete: "
                + "; ".join(rollback_failures)
            )
        raise
    return mutation


def _symlink_privilege_error(error: OSError) -> bool:
    """Limit the hardlink fallback to Windows' missing-link privilege case."""

    return os.name == "nt" and getattr(error, "winerror", None) == 1314


def health(
    root: os.PathLike[str] | str,
    home: os.PathLike[str] | str,
    *,
    include_configuration: bool = False,
) -> dict[str, Any]:
    """Inspect the managed-link matrix without mutation.

    ``include_configuration`` adds the hash-only global Codex configuration
    drift report.  It is opt-in for the link API so callers that only manage
    links do not need a Codex executable; the setup entrypoint always enables
    it for its health command and final verification.
    """

    root_candidate = _absolute(root, label="root")
    _reject_link_ancestors(root_candidate)
    if _is_managed_link(root_candidate):
        raise InstallError("root must be a regular directory")
    root_path = root_candidate.resolve(strict=True)
    if not root_path.is_dir():
        raise InstallError("root must be a regular directory")
    home_path = _safe_home(home)
    _read_ownership(root_path, home_path)
    targets: list[dict[str, Any]] = []
    for spec in INSTALL_MATRIX:
        source = _safe_source(root_path, spec["source_relative"], spec["kind"])
        installed = _target_path(home_path, spec["installed_relative"])
        observation = _target_status(source, installed, spec["kind"])
        targets.append(
            {
                "name": spec["name"],
                "installed_relative": spec["installed_relative"],
                "source_relative": spec["source_relative"],
                "expected_kind": spec["kind"],
                **observation,
            }
        )
    ok = all(target["status"] == "correct" for target in targets)
    result: dict[str, Any] = {
        "schema": SCHEMA_HEALTH,
        "ok": ok,
        "root": str(root_path),
        "targets": targets,
        "summary": {
            "correct": sum(target["status"] == "correct" for target in targets),
            "missing": sum(target["status"] == "missing" for target in targets),
            "conflict": sum(target["status"] == "conflict" for target in targets),
        },
    }
    if include_configuration:
        from . import config_install

        configuration = config_install.health(root_path, home_path)
        result["configuration"] = configuration
        result["ok"] = bool(result["ok"] and configuration.get("ok"))
    return result


def _load_snapshot(snapshot: Mapping[str, Any] | os.PathLike[str] | str) -> Mapping[str, Any]:
    if isinstance(snapshot, Mapping):
        return snapshot
    path = _as_path(snapshot, label="snapshot")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InstallError(f"cannot read replacement snapshot: {path}") from exc
    if not isinstance(payload, Mapping):
        raise InstallError("replacement snapshot must contain a JSON object")
    return payload


def plan_install(
    root: os.PathLike[str] | str,
    home: os.PathLike[str] | str,
    *,
    replace: bool = False,
    snapshot: Mapping[str, Any] | os.PathLike[str] | str | None = None,
    backup_root: os.PathLike[str] | str | None = None,
) -> dict[str, Any]:
    """Build a dry-run install plan for the exact managed-link matrix.

    ``replace=True`` is intentionally incomplete without both a supplied
    snapshot and backup destination.  The caller must opt into preservation
    before a real custom file or directory can be moved.
    """

    root_candidate = _absolute(root, label="root")
    _reject_link_ancestors(root_candidate)
    if _is_managed_link(root_candidate):
        raise InstallError("root must be a regular directory")
    root_path = root_candidate.resolve(strict=True)
    home_path = _safe_home(home)
    observed = health(root_path, home_path)
    ownership = _read_ownership(root_path, home_path)
    operations: list[dict[str, Any]] = []
    conflicts: list[str] = []
    for target in observed["targets"]:
        status = target["status"]
        if status == "correct":
            action = "keep"
        elif status == "missing":
            action = "create_link"
        elif target["name"] == "global_instructions" and _can_refresh_owned(target, ownership):
            action = "refresh_owned"
        elif replace:
            action = "replace_custom"
            conflicts.append(target["installed_relative"])
        else:
            action = "conflict"
            conflicts.append(target["installed_relative"])
        operations.append({**target, "action": action})

    quarantine_plan: Mapping[str, Any] | None = None
    snapshot_meta: dict[str, Any] | None = None
    if conflicts and replace:
        if snapshot is None or backup_root is None:
            raise InstallError(
                "replacing a custom installation requires an explicit snapshot and backup_root"
            )
        snapshot_payload = _load_snapshot(snapshot)
        if snapshot_payload.get("schema") != workspace.SCHEMA_SNAPSHOT:
            raise InstallError("replacement snapshot has an unsupported schema")
        snapshot_root = snapshot_payload.get("source_root")
        if not isinstance(snapshot_root, str) or Path(snapshot_root).resolve(strict=False) != home_path:
            raise InstallError("replacement snapshot does not describe the selected home")
        snapshot_files = snapshot_payload.get("source_files")
        if not isinstance(snapshot_files, list):
            raise InstallError("replacement snapshot is missing source_files")
        snapshot_paths = {
            item.get("path")
            for item in snapshot_files
            if isinstance(item, Mapping) and isinstance(item.get("path"), str)
        }
        snapshot_meta = {
            "schema": snapshot_payload.get("schema"),
            "source_root": snapshot_payload.get("source_root"),
            "source_file_count": snapshot_payload.get("source_file_count"),
        }
        relative_paths = [
            target["installed_relative"]
            for target in observed["targets"]
            if target["status"] == "conflict"
        ]
        for relative in relative_paths:
            if relative not in snapshot_paths and not any(
                path.startswith(relative.rstrip("/") + "/") for path in snapshot_paths
            ):
                raise InstallError(f"replacement snapshot does not cover custom path: {relative}")
        quarantine_plan = workspace.plan_quarantine(home_path, relative_paths, backup_root)

    unsigned: dict[str, Any] = {
        "schema": SCHEMA_INSTALL,
        "root": str(root_path),
        "home": str(home_path),
        "replace": bool(replace),
        "operations": operations,
        "quarantine_plan": quarantine_plan,
        "snapshot": snapshot_meta,
        "ownership": _ownership_meta(ownership),
    }
    digest = hashlib.sha256(
        json.dumps(unsigned, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return {**unsigned, "plan_sha256": digest, "status": "planned"}


def _validate_install_digest(plan: Mapping[str, Any]) -> None:
    if plan.get("schema") != SCHEMA_INSTALL or not isinstance(plan.get("plan_sha256"), str):
        raise InstallError("unsupported install plan or missing content digest")
    unsigned = {
        key: plan[key]
        for key in (
            "schema",
            "root",
            "home",
            "replace",
            "operations",
            "quarantine_plan",
            "snapshot",
            "ownership",
        )
    }
    expected = hashlib.sha256(
        json.dumps(unsigned, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    if expected != plan["plan_sha256"]:
        raise InstallError("install plan digest does not match its contents")


def _quarantine_target_paths(quarantine_plan: Mapping[str, Any]) -> set[str]:
    targets = quarantine_plan.get("targets")
    if not isinstance(targets, list):
        raise InstallError("replacement quarantine plan is missing targets")
    paths: set[str] = set()
    for target in targets:
        if not isinstance(target, Mapping) or not isinstance(target.get("path"), str):
            raise InstallError("replacement quarantine plan contains malformed targets")
        relative = target["path"]
        if relative in paths:
            raise InstallError(f"replacement quarantine plan contains duplicate target: {relative}")
        paths.add(relative)
    return paths


def _validate_quarantine_binding(
    plan: Mapping[str, Any],
    quarantine_plan: Mapping[str, Any],
    home_path: Path,
    replace_targets: set[str],
) -> None:
    """Bind a nested quarantine operation to this install plan before moving anything."""

    if plan.get("replace") is not True:
        raise InstallError("replacement quarantine plan requires replace=true")
    if not replace_targets:
        raise InstallError("replacement quarantine plan has no replace_custom operation")

    nested_root = quarantine_plan.get("root")
    if not isinstance(nested_root, str):
        raise InstallError("replacement quarantine plan is missing root")
    try:
        expected_root = home_path.resolve(strict=True)
        observed_root = _absolute(nested_root, label="quarantine root").resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise InstallError("replacement quarantine plan root is unavailable") from exc
    if observed_root != expected_root:
        raise InstallError("replacement quarantine plan root does not match the selected home")

    if _quarantine_target_paths(quarantine_plan) != replace_targets:
        raise InstallError(
            "replacement quarantine targets do not match the install plan's replace_custom targets"
        )

    # Recompute the nested plan's own hash, path, kind, and file invariants
    # before the first mutation.  apply_quarantine repeats this check after the
    # binding is established to close the final race window.
    try:
        workspace._validate_plan_preconditions(quarantine_plan)
    except (workspace.WorkspaceError, KeyError, TypeError, ValueError) as exc:
        raise InstallError("replacement quarantine plan preflight failed") from exc


def _remove_managed_link(path: Path) -> None:
    if not _is_managed_link(path):
        raise InstallError(f"refusing to remove a non-managed path: {path}")
    # A directory junction is reported as a link by ``_path_kind`` but must
    # be removed with ``rmdir`` on Windows.  A directory symlink generally
    # accepts ``unlink``; try that first and use the junction-safe fallback.
    if path.is_dir():
        try:
            path.unlink()
        except (PermissionError, IsADirectoryError, OSError):
            os.rmdir(path)
    else:
        path.unlink()


def _safe_command_path(path: Path) -> str:
    value = str(path)
    if any(char in value for char in '\r\n"&|<>^%!'):
        raise InstallError("path contains characters unsafe for the native directory-link fallback")
    return value


def _create_managed_link(source: Path, installed: Path, expected_kind: str) -> None:
    if os.path.lexists(installed):
        raise InstallError(f"installation destination already exists: {installed}")
    installed.parent.mkdir(parents=True, exist_ok=True)
    _reject_link_ancestors(installed.parent)
    if expected_kind == "directory":
        try:
            os.symlink(str(source), str(installed), target_is_directory=True)
            _verify_link_target(source, installed)
            return
        except (OSError, NotImplementedError) as symlink_error:
            if os.name != "nt":
                raise InstallError(f"cannot create directory link: {installed}") from symlink_error
            command = [
                os.environ.get("COMSPEC", "cmd.exe"),
                "/d",
                "/c",
                "mklink",
                "/J",
                _safe_command_path(installed),
                _safe_command_path(source),
            ]
            try:
                subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except (OSError, subprocess.CalledProcessError) as fallback_error:
                raise InstallError(f"cannot create directory junction: {installed}") from fallback_error
            _verify_link_target(source, installed)
            return
    try:
        os.symlink(str(source), str(installed), target_is_directory=False)
        _verify_link_target(source, installed)
    except (OSError, NotImplementedError) as symlink_error:
        if isinstance(symlink_error, OSError) and _symlink_privilege_error(symlink_error):
            try:
                os.link(str(source), str(installed))
                if not _same_file(source, installed):
                    raise InstallError(f"created hardlink identity mismatch: {installed}")
                return
            except (OSError, InstallError) as hardlink_error:
                if os.path.lexists(installed) and _path_kind(installed) == "file":
                    try:
                        installed.unlink()
                    except OSError:
                        pass
                raise InstallError(f"cannot create file hardlink: {installed}") from hardlink_error
        if os.path.lexists(installed) and _is_managed_link(installed):
            try:
                _remove_managed_link(installed)
            except OSError:
                pass
        raise InstallError(f"cannot create file link: {installed}") from symlink_error


def _verify_link_target(source: Path, installed: Path) -> None:
    try:
        observed = installed.resolve(strict=True)
    except OSError as exc:
        if os.path.lexists(installed) and _is_managed_link(installed):
            try:
                _remove_managed_link(installed)
            except OSError:
                pass
        raise InstallError(f"created link target cannot be resolved: {installed}") from exc
    if not _is_managed_link(installed) or observed != source.resolve(strict=True):
        if os.path.lexists(installed) and _is_managed_link(installed):
            try:
                _remove_managed_link(installed)
            except OSError:
                pass
        raise InstallError(f"created link target mismatch: {installed}")


def _remove_created_links(paths: Sequence[tuple[Path, Path]]) -> list[str]:
    failures: list[str] = []
    for path, source in reversed(paths):
        if not os.path.lexists(path):
            continue
        if not _is_managed_link(path) and not _same_file(source, path):
            failures.append(f"created path became custom: {path}")
            continue
        try:
            if _is_managed_link(path):
                _remove_managed_link(path)
            else:
                path.unlink()
        except OSError as exc:
            failures.append(f"remove {path}: {exc}")
    return failures


def _rollback_install(
    created: Sequence[tuple[Path, Path]],
    quarantine_plan: Mapping[str, Any] | None,
    quarantine_receipt: Mapping[str, Any] | None,
    *,
    refreshed: Sequence[_RefreshedTarget] = (),
    ownership_mutation: _OwnershipMutation | None = None,
) -> list[str]:
    if ownership_mutation is not None:
        backup_failures = _validate_ownership_backups(ownership_mutation)
        if backup_failures:
            return backup_failures
    refreshed_pairs = {(item.installed, item.source) for item in refreshed}
    ordinary_created = [
        pair for pair in created if pair not in refreshed_pairs
    ]
    failures = _remove_created_links(ordinary_created)
    refresh_failures = _restore_refreshed_targets(refreshed) if refreshed else []
    failures.extend(refresh_failures)
    if not refresh_failures and ownership_mutation is not None:
        failures.extend(_restore_ownership_mutation(ownership_mutation))
    if quarantine_plan is not None and quarantine_receipt is not None:
        try:
            workspace.restore_quarantine(quarantine_plan, quarantine_receipt)
        except Exception as exc:
            failures.append(f"restore custom paths: {exc}")
    return failures


def apply_install(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Apply a hash-bound install plan after validating every source and target."""

    _validate_install_digest(plan)
    root_candidate = _absolute(str(plan["root"]), label="root")
    _reject_link_ancestors(root_candidate)
    if _is_managed_link(root_candidate):
        raise InstallError("root must be a regular directory")
    root_path = root_candidate.resolve(strict=True)
    home_path = _safe_home(str(plan["home"]))
    observed = health(root_path, home_path)
    ownership = _read_ownership(root_path, home_path)
    planned_ownership = plan.get("ownership")
    if planned_ownership != _ownership_meta(ownership):
        raise InstallError("ownership receipt changed after planning")
    observed_by_name = {item["name"]: item for item in observed["targets"]}
    operations = plan.get("operations")
    if not isinstance(operations, list):
        raise InstallError("install plan is missing operations")

    # Complete preflight before touching a path.  This protects against a
    # personal file appearing or a managed link changing after dry-run.
    actions: list[tuple[dict[str, Any], Path, Path]] = []
    seen_names: set[str] = set()
    for operation in operations:
        if not isinstance(operation, Mapping):
            raise InstallError("install plan contains a malformed operation")
        name = operation.get("name")
        if name not in observed_by_name:
            raise InstallError(f"unknown installation target: {name}")
        if name in seen_names:
            raise InstallError(f"duplicate installation target: {name}")
        seen_names.add(name)
        current = observed_by_name[name]
        if (
            current["status"] != operation.get("status")
            or current.get("observed_kind") != operation.get("observed_kind")
            or current.get("observed_target") != operation.get("observed_target")
            or current.get("observed_sha256") != operation.get("observed_sha256")
            or current.get("source_sha256") != operation.get("source_sha256")
        ):
            raise InstallError(f"installation target changed after planning: {name}")
        spec = next(item for item in INSTALL_MATRIX if item["name"] == name)
        if (
            operation.get("installed_relative") != spec["installed_relative"]
            or operation.get("source_relative") != spec["source_relative"]
        ):
            raise InstallError(f"installation target paths changed after planning: {name}")
        if operation.get("expected_kind") != spec["kind"]:
            raise InstallError(f"installation kind changed after planning: {name}")
        source = _safe_source(root_path, spec["source_relative"], spec["kind"])
        installed = _target_path(home_path, spec["installed_relative"])
        action = operation.get("action")
        if action in {"create_link", "replace_custom", "refresh_owned"}:
            if action == "create_link" and _path_kind(installed) != "missing":
                raise InstallError(f"destination is no longer missing: {name}")
            if action == "refresh_owned" and (
                name != "global_instructions" or not _can_refresh_owned(current, ownership)
            ):
                raise InstallError(f"owned instruction target changed after planning: {name}")
            if action == "replace_custom" and plan.get("replace") is not True:
                raise InstallError("custom replacement requires replace=true")
            actions.append((dict(operation), source, installed))
        elif action == "conflict":
            raise InstallError(f"custom installation conflict requires explicit replacement: {name}")
        elif action != "keep":
            raise InstallError(f"unsupported installation action: {action}")

    expected_names = {item["name"] for item in INSTALL_MATRIX}
    if seen_names != expected_names:
        raise InstallError("install plan does not cover the complete managed-link matrix")

    quarantine_receipt: Mapping[str, Any] | None = None
    quarantine_plan = plan.get("quarantine_plan")
    replace_targets = {
        operation["installed_relative"]
        for operation, _source, _installed in actions
        if operation["action"] == "replace_custom"
    }
    if quarantine_plan is not None:
        if not isinstance(quarantine_plan, Mapping):
            raise InstallError("replacement quarantine plan is malformed")
        _validate_quarantine_binding(plan, quarantine_plan, home_path, replace_targets)
        quarantine_receipt = workspace.apply_quarantine(quarantine_plan)
    elif replace_targets:
        raise InstallError("replace_custom operations require a replacement quarantine plan")

    created: list[tuple[Path, Path]] = []
    refreshed: list[_RefreshedTarget] = []
    refresh_backup_dir: Path | None = None
    ownership_mutation: _OwnershipMutation | None = None
    try:
        for operation, source, installed in actions:
            action = operation["action"]
            if action == "refresh_owned":
                refresh_backup_dir = refresh_backup_dir or _new_backup_dir(home_path)
                stale_backup = _backup_stale_target(
                    installed,
                    ownership,
                    refresh_backup_dir,
                )
                assert ownership.record is not None
                refreshed.append(
                    _RefreshedTarget(
                        installed=installed,
                        source=source,
                        backup=stale_backup,
                        expected_sha256=ownership.record["target_sha256"],
                        expected_identity=tuple(ownership.record["target_identity"]),
                    )
                )
                _verify_stale_backup(installed, stale_backup, ownership)
            elif action not in {"create_link", "replace_custom"}:
                raise InstallError(f"unsupported installation action: {action}")
            _create_managed_link(source, installed, operation["expected_kind"])
            created.append((installed, source))
        ownership_mutation = _write_ownership(
            root_path,
            home_path,
            ownership,
            backup_dir=refresh_backup_dir,
        )
        if ownership_mutation is not None and refreshed:
            ownership_mutation = _OwnershipMutation(
                path=ownership_mutation.path,
                before_sha256=ownership_mutation.before_sha256,
                after_sha256=ownership_mutation.after_sha256,
                before_exists=ownership_mutation.before_exists,
                backup_dir=ownership_mutation.backup_dir,
                receipt_backup=ownership_mutation.receipt_backup,
                stale_backup=refreshed[-1].backup,
            )
    except Exception as exc:
        rollback_failures = _rollback_install(
            created,
            quarantine_plan,
            quarantine_receipt,
            refreshed=refreshed,
            ownership_mutation=ownership_mutation,
        )
        if rollback_failures:
            raise InstallError(
                f"installation failed and rollback was incomplete: {exc}; "
                f"rollback={rollback_failures}"
            ) from exc
        raise InstallError(f"installation failed; all changes were rolled back: {exc}") from exc

    try:
        final_health = health(root_path, home_path)
        if not final_health["ok"]:
            raise InstallError("installation completed without a healthy managed-link matrix")
        final_ownership = _read_ownership(root_path, home_path)
        if final_ownership.record is None or not final_ownership.target_matches:
            raise InstallError("installation completed without a healthy ownership receipt")
    except Exception as exc:
        rollback_failures = _rollback_install(
            created,
            quarantine_plan,
            quarantine_receipt,
            refreshed=refreshed,
            ownership_mutation=ownership_mutation,
        )
        if rollback_failures:
            raise InstallError(
                f"post-install health failed and rollback was incomplete: {exc}; "
                f"rollback={rollback_failures}"
            ) from exc
        raise InstallError(f"post-install health failed; all changes were rolled back: {exc}") from exc
    return {
        "schema": "codex-nexus/install-receipt/v1",
        "status": "applied",
        "created": [
            operation["installed_relative"]
            for operation, _source, _installed in actions
            if operation["action"] == "create_link"
        ],
        "replaced": [
            operation["installed_relative"]
            for operation, _source, _installed in actions
            if operation["action"] == "replace_custom"
        ],
        "refreshed": [
            operation["installed_relative"]
            for operation, _source, _installed in actions
            if operation["action"] == "refresh_owned"
        ],
        "ownership": _ownership_mutation_receipt(ownership_mutation, home_path),
        "quarantine": dict(quarantine_receipt) if quarantine_receipt else None,
        "health": final_health,
        "plan_sha256": plan["plan_sha256"],
    }


def rollback_install(plan: Mapping[str, Any], receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Compensate one successful install receipt without touching new custom files."""

    _validate_install_digest(plan)
    if not isinstance(receipt, Mapping) or receipt.get("status") != "applied":
        raise InstallError("install receipt is not rollbackable")
    root_candidate = _absolute(str(plan["root"]), label="root")
    _reject_link_ancestors(root_candidate)
    if _is_managed_link(root_candidate):
        raise InstallError("root must be a regular directory")
    root_path = root_candidate.resolve(strict=True)
    home_path = _safe_home(str(plan["home"]))
    operations = plan.get("operations")
    if not isinstance(operations, list):
        raise InstallError("install plan is missing operations")
    created_names = receipt.get("created")
    replaced_names = receipt.get("replaced")
    refreshed_names = receipt.get("refreshed")
    if (
        not isinstance(created_names, list)
        or not isinstance(replaced_names, list)
        or not isinstance(refreshed_names, list)
    ):
        raise InstallError("install receipt is missing rollback paths")
    expected_created = {
        operation.get("installed_relative")
        for operation in operations
        if isinstance(operation, Mapping) and operation.get("action") == "create_link"
    }
    expected_replaced = {
        operation.get("installed_relative")
        for operation in operations
        if isinstance(operation, Mapping) and operation.get("action") == "replace_custom"
    }
    expected_refreshed = {
        operation.get("installed_relative")
        for operation in operations
        if isinstance(operation, Mapping) and operation.get("action") == "refresh_owned"
    }
    if (
        set(created_names) != expected_created
        or set(replaced_names) != expected_replaced
        or set(refreshed_names) != expected_refreshed
    ):
        raise InstallError("install receipt does not match the planned changes")

    ownership_mutation: _OwnershipMutation | None = None
    ownership_receipt = receipt.get("ownership")
    if ownership_receipt is not None:
        if not isinstance(ownership_receipt, Mapping):
            raise InstallError("ownership rollback receipt is malformed")
        if ownership_receipt.get("path") != OWNERSHIP_RELATIVE:
            raise InstallError("ownership rollback receipt targets an unsupported path")
        before_exists = ownership_receipt.get("before_exists")
        before_sha256 = ownership_receipt.get("before_sha256")
        after_sha256 = ownership_receipt.get("after_sha256")
        if not isinstance(before_exists, bool) or not isinstance(after_sha256, str):
            raise InstallError("ownership rollback receipt is malformed")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", after_sha256):
            raise InstallError("ownership rollback receipt contains an invalid digest")
        if before_exists:
            if not isinstance(before_sha256, str) or not re.fullmatch(
                r"[0-9a-fA-F]{64}", before_sha256
            ):
                raise InstallError("ownership rollback receipt contains an invalid prior digest")
        elif before_sha256 is not None:
            raise InstallError("ownership rollback receipt contains an unexpected prior digest")

        def resolve_home_relative(value: object, label: str) -> Path | None:
            if value is None:
                return None
            if not isinstance(value, str):
                raise InstallError(f"{label} is malformed")
            candidate = home_path / Path(value)
            resolved = candidate.resolve(strict=False)
            if not _is_within(resolved, home_path):
                raise InstallError(f"{label} leaves the selected home")
            _reject_link_ancestors(candidate.parent)
            if _is_managed_link(candidate):
                raise InstallError(f"{label} is a redirect")
            return resolved

        ownership_path = _ownership_path(home_path)
        backup_dir = resolve_home_relative(ownership_receipt.get("backup_dir"), "ownership backup directory")
        receipt_backup = resolve_home_relative(
            ownership_receipt.get("receipt_backup"), "ownership receipt backup"
        )
        stale_backup = resolve_home_relative(
            ownership_receipt.get("stale_backup"), "stale instruction backup"
        )
        if backup_dir is None:
            raise InstallError("ownership rollback receipt is missing the backup directory")
        for label, path in (
            ("ownership receipt", receipt_backup),
            ("stale instruction", stale_backup),
        ):
            if path is not None and path.parent != backup_dir:
                raise InstallError(f"{label} backup is outside the operation directory")
        if before_exists != (receipt_backup is not None):
            raise InstallError("ownership rollback receipt does not match prior receipt state")
        if expected_refreshed != {".codex/AGENTS.md"}:
            if stale_backup is not None:
                raise InstallError("ownership rollback receipt has an unexpected stale backup")
        elif stale_backup is None:
            raise InstallError("ownership rollback receipt is missing the stale backup")
        ownership_mutation = _OwnershipMutation(
            path=ownership_path,
            before_sha256=before_sha256,
            after_sha256=after_sha256,
            before_exists=before_exists,
            backup_dir=backup_dir,
            receipt_backup=receipt_backup,
            stale_backup=stale_backup,
        )
    elif expected_refreshed or expected_created or expected_replaced:
        raise InstallError("install receipt is missing the ownership rollback state")

    created: list[tuple[Path, Path]] = []
    for relative in sorted(expected_created | expected_replaced | expected_refreshed):
        spec = next(
            (item for item in INSTALL_MATRIX if item["installed_relative"] == relative),
            None,
        )
        if spec is None:
            raise InstallError("install receipt contains an unknown rollback path")
        source = _safe_source(root_path, spec["source_relative"], spec["kind"])
        installed = _target_path(home_path, spec["installed_relative"])
        created.append((installed, source))

    quarantine_plan = plan.get("quarantine_plan")
    quarantine_receipt = receipt.get("quarantine")
    if quarantine_plan is not None and not isinstance(quarantine_plan, Mapping):
        raise InstallError("replacement quarantine plan is malformed")
    if quarantine_receipt is not None and not isinstance(quarantine_receipt, Mapping):
        raise InstallError("replacement quarantine receipt is malformed")
    refreshed: list[_RefreshedTarget] = []
    if expected_refreshed:
        assert ownership_mutation is not None
        assert ownership_mutation.stale_backup is not None
        assert ownership_mutation.receipt_backup is not None
        source = _safe_source(root_path, "AGENTS.md", "file")
        installed = _target_path(home_path, ".codex/AGENTS.md")
        previous_record = json.loads(ownership_mutation.receipt_backup.read_text(encoding="utf-8"))
        if not isinstance(previous_record, Mapping):
            raise InstallError("ownership receipt backup is malformed")
        refreshed.append(
            _RefreshedTarget(
                installed=installed,
                source=source,
                backup=ownership_mutation.stale_backup,
                expected_sha256=str(previous_record["target_sha256"]),
                expected_identity=tuple(previous_record["target_identity"]),
            )
        )
    failures = _rollback_install(
        created,
        quarantine_plan,
        quarantine_receipt,
        refreshed=refreshed,
        ownership_mutation=ownership_mutation,
    )
    if failures:
        raise InstallError(f"install rollback was incomplete: rollback={failures}")
    return {
        "schema": "codex-nexus/install-rollback/v1",
        "status": "rolled_back",
        "removed": sorted(expected_created | expected_replaced | expected_refreshed),
        "restored_custom": sorted(expected_replaced),
        "plan_sha256": plan["plan_sha256"],
    }


__all__ = [
    "INSTALL_MATRIX",
    "InstallError",
    "SCHEMA_HEALTH",
    "SCHEMA_INSTALL",
    "SCHEMA_OWNERSHIP",
    "apply_install",
    "health",
    "plan_install",
    "rollback_install",
]
