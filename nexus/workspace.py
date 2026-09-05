"""Scope-safe workspace inventory snapshots and reversible quarantine operations.

This module deliberately uses only the Python standard library.  A snapshot is
an inventory of file names, sizes, and SHA-256 digests; it never stores file
contents and is not a recoverable backup.  Quarantine plans bind every move to
those digests and validate the complete plan before moving the first source
path; their external backup paths are the recovery boundary.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_SNAPSHOT = "codex-nexus/workspace-snapshot/v1"
SCHEMA_QUARANTINE = "codex-nexus/quarantine-plan/v1"

# Dropbox Cloud Files can expose a reparse point while remaining a regular
# file.  Only actual symbolic-link and mount-point tags are treated as path
# redirection.  This keeps ordinary cloud-backed source files usable while
# refusing symlink and junction traversal.
_REPARSE_TAG_SYMLINK = 0xA000000C
_REPARSE_TAG_MOUNT_POINT = 0xA0000003
_EXCLUDED_SOURCE_DIRS = frozenset({".git", "artifacts", "__pycache__", ".pytest_cache"})
_RETRYABLE_WINERRORS = frozenset({5, 32, 33})


class WorkspaceError(RuntimeError):
    """Base error for scope, snapshot, and quarantine failures."""


class PathSafetyError(WorkspaceError):
    """Raised when a path leaves its declared boundary or redirects."""


class StalePlanError(WorkspaceError):
    """Raised when a source no longer matches a hash-bound plan."""


class QuarantineError(WorkspaceError):
    """Raised when a quarantine operation cannot complete safely."""


def _as_path(value: os.PathLike[str] | str, *, label: str) -> Path:
    if isinstance(value, (str, os.PathLike)):
        return Path(value).expanduser()
    raise TypeError(f"{label} must be a path-like value")


def _absolute(value: os.PathLike[str] | str, *, label: str) -> Path:
    return _as_path(value, label=label).absolute()


def _is_within(path: Path, parent: Path, *, allow_equal: bool = False) -> bool:
    try:
        relative = path.relative_to(parent)
    except ValueError:
        return False
    return allow_equal or relative != Path(".")


def _assert_outside(path: Path, boundary: Path, *, label: str) -> None:
    lexical = path.absolute()
    boundary_abs = boundary.absolute()
    if _is_within(lexical, boundary_abs, allow_equal=True):
        raise PathSafetyError(f"{label} must be outside the source root")
    resolved_path = path.resolve(strict=False)
    resolved_boundary = boundary.resolve(strict=False)
    if _is_within(resolved_path, resolved_boundary, allow_equal=True):
        raise PathSafetyError(f"{label} resolves inside the source root")


def _is_link_or_junction(path: os.PathLike[str] | str) -> bool:
    """Return true only for path-redirection reparse points.

    The Windows file-attribute flag by itself is intentionally insufficient:
    cloud-backed regular files may carry that flag.  Python's symlink check
    and the two Windows redirection tags are the narrow test used here.
    """

    try:
        if os.path.islink(path):
            return True
        result = os.lstat(path)
    except (FileNotFoundError, OSError):
        return False
    tag = int(getattr(result, "st_reparse_tag", 0) or 0)
    if tag in {_REPARSE_TAG_SYMLINK, _REPARSE_TAG_MOUNT_POINT}:
        return True
    # Some Python/Windows combinations expose only file attributes.  The
    # exact tag is preferred, but a symlink already caught above is enough to
    # avoid rejecting ordinary cloud files with unknown tags.
    return False


def _path_kind(path: Path) -> str:
    if _is_link_or_junction(path):
        return "link"
    try:
        result = os.lstat(path)
    except FileNotFoundError:
        return "missing"
    if stat.S_ISREG(result.st_mode):
        return "file"
    if stat.S_ISDIR(result.st_mode):
        return "directory"
    return "special"


def _safe_root(root: os.PathLike[str] | str) -> Path:
    root_path = _absolute(root, label="root")
    # The root itself may be an ordinary directory reached through a parent
    # junction.  Reject that redirect before resolving it, otherwise all later
    # child checks would silently operate in the redirected tree.
    _reject_link_components(root_path)
    if _is_link_or_junction(root_path):
        raise PathSafetyError("root must not be a symbolic link or junction")
    if not root_path.exists() or not root_path.is_dir():
        raise WorkspaceError(f"root directory does not exist: {root_path}")
    return root_path.resolve(strict=True)


def _reject_link_components(path: Path) -> None:
    """Reject a link/junction in existing components of *path*."""

    absolute = path.absolute()
    current = Path(absolute.anchor)
    parts = absolute.parts[1:]
    for part in parts:
        current = current / part
        if current.exists() or os.path.lexists(current):
            if _is_link_or_junction(current):
                raise PathSafetyError(f"symbolic link or junction is not allowed: {current}")


def _assert_safe_child(root: Path, candidate: Path, *, label: str) -> Path:
    lexical = candidate.absolute()
    if not _is_within(lexical, root.absolute()):
        raise PathSafetyError(f"{label} leaves the source root: {candidate}")
    # Inspect every existing component through the candidate.  Stopping at
    # root would miss a child junction such as ``root/foo/bar`` after ``foo``
    # had been replaced between planning and execution.
    _reject_link_components(lexical)
    resolved = lexical.resolve(strict=False)
    if not _is_within(resolved, root.resolve(strict=True)):
        raise PathSafetyError(f"{label} resolves outside the source root: {candidate}")
    return resolved


def sha256(path: os.PathLike[str] | str) -> str:
    """Return the SHA-256 digest of one regular file."""

    file_path = _as_path(path, label="path")
    if _path_kind(file_path) != "file":
        raise WorkspaceError(f"regular file required for hashing: {file_path}")
    before = file_path.stat()
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    after = file_path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise StalePlanError(f"file changed while hashing: {file_path}")
    return digest.hexdigest()


def _hash_and_size(path: Path) -> tuple[str, int]:
    result = path.stat()
    digest = sha256(path)
    after = path.stat()
    if (result.st_size, result.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise StalePlanError(f"file changed while hashing: {path}")
    return digest, int(after.st_size)


def _iter_regular_files(
    root: Path,
    *,
    exclude_dirs: frozenset[str] | set[str] | None = None,
) -> list[Path]:
    excluded = _EXCLUDED_SOURCE_DIRS if exclude_dirs is None else frozenset(exclude_dirs)
    files: list[Path] = []

    def visit(directory: Path) -> None:
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as exc:
            raise WorkspaceError(f"cannot enumerate {directory}: {exc}") from exc
        for entry in entries:
            path = Path(entry.path)
            if _is_link_or_junction(path):
                target = path.resolve(strict=False)
                if not _is_within(target, root.resolve(strict=True)):
                    raise PathSafetyError(f"link or junction escapes root: {path}")
                raise PathSafetyError(f"link or junction is not supported: {path}")
            if entry.is_dir(follow_symlinks=False):
                if entry.name in excluded:
                    continue
                visit(path)
                continue
            if entry.is_file(follow_symlinks=False):
                files.append(path)
                continue
            raise PathSafetyError(f"unsupported special path: {path}")

    visit(root)
    return files


def source_files(root: os.PathLike[str] | str) -> list[Path]:
    """Return deterministic source files under *root*.

    ``.git``, ``artifacts``, ``__pycache__``, and ``.pytest_cache`` directories
    are excluded at every depth.  Returned paths are absolute and sorted by
    POSIX relative path.  Symlinks and junctions are rejected, while regular
    cloud-backed files remain ordinary files.
    """

    root_path = _safe_root(root)
    files = _iter_regular_files(root_path)
    return sorted(files, key=lambda path: path.relative_to(root_path).as_posix())


def _inventory(root: Path, *, exclude_dirs: Iterable[str] | None = None) -> list[dict[str, Any]]:
    excluded = None if exclude_dirs is None else frozenset(exclude_dirs)
    files = _iter_regular_files(root, exclude_dirs=excluded)
    output: list[dict[str, Any]] = []
    for path in files:
        digest, size = _hash_and_size(path)
        output.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": digest,
                "size": size,
            }
        )
    output.sort(key=lambda item: item["path"])
    return output


def _manifest_destination(root: Path, destination: os.PathLike[str] | str) -> Path:
    requested = _absolute(destination, label="destination")
    if requested.exists() and requested.is_dir():
        requested = requested / "workspace-snapshot.json"
    _assert_outside(requested, root, label="snapshot destination")
    _reject_link_components(requested.parent)
    requested.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(requested):
        raise WorkspaceError(f"snapshot destination already exists: {requested}")
    return requested


def write_text_atomic(
    path: os.PathLike[str] | str,
    text: str,
    *,
    encoding: str = "utf-8",
    retries: int = 4,
    overwrite: bool = True,
) -> Path:
    """Publish complete text, optionally requiring an absent destination.

    Exclusive publication uses a hard link to the staged sibling file so a
    concurrently created destination cannot be replaced between check and write.
    """

    if not isinstance(text, str):
        raise TypeError("text must be a string")
    target = _absolute(path, label="path")
    if not overwrite:
        _reject_link_components(target.parent)
        if os.path.lexists(target):
            raise FileExistsError(f"destination already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not overwrite:
        _reject_link_components(target.parent)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    temporary = Path(temporary_name)
    os.close(fd)
    try:
        with temporary.open("w", encoding=encoding, newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        attempts = max(0, int(retries))
        for attempt in range(attempts + 1):
            try:
                if overwrite:
                    os.replace(temporary, target)
                else:
                    _reject_link_components(target.parent)
                    os.link(temporary, target)
                return target
            except PermissionError:
                if attempt >= attempts:
                    raise
                time.sleep(0.05 * (2**attempt))
            except OSError as exc:
                if getattr(exc, "winerror", None) not in _RETRYABLE_WINERRORS:
                    raise
                if attempt >= attempts:
                    raise
                time.sleep(0.05 * (2**attempt))
        raise AssertionError("unreachable atomic write path")
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def write_json(
    path: os.PathLike[str] | str,
    payload: Mapping[str, Any] | Sequence[Any],
    *,
    retries: int = 4,
    overwrite: bool = True,
) -> Path:
    """Write stable UTF-8 JSON; use overwrite=False for a new receipt."""

    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        separators=(",", ": "),
    )
    return write_text_atomic(path, serialized + "\n", retries=retries, overwrite=overwrite)


def snapshot(
    root: os.PathLike[str] | str,
    destination: os.PathLike[str] | str,
) -> dict[str, Any]:
    """Create a metadata-only JSON snapshot outside *root* and return it.

    The returned manifest is evidence of the observed source state, not a
    backup.  Use :func:`plan_quarantine` and :func:`apply_quarantine` when
    recovery of selected paths is required.
    """

    root_path = _safe_root(root)
    destination_path = _manifest_destination(root_path, destination)
    files = _inventory(root_path)
    manifest: dict[str, Any] = {
        "schema": SCHEMA_SNAPSHOT,
        "recoverable": False,
        "recovery_boundary": "quarantine_receipt_backup_paths",
        "source_root": str(root_path),
        "excluded_directories": sorted(_EXCLUDED_SOURCE_DIRS),
        "source_files": files,
        "source_file_count": len(files),
        "source_byte_count": sum(int(item["size"]) for item in files),
    }
    write_json(destination_path, manifest, overwrite=False)
    return manifest


def _relative_request(root: Path, raw: os.PathLike[str] | str) -> tuple[str, Path]:
    requested = _as_path(raw, label="quarantine path")
    candidate = requested if requested.is_absolute() else root / requested
    # Check the lexical path before resolving so ``..`` and an escaping link
    # cannot be hidden by a later normalization step.
    lexical = candidate.absolute()
    if not _is_within(lexical, root.absolute()):
        raise PathSafetyError(f"quarantine path leaves root: {raw}")
    safe = _assert_safe_child(root, lexical, label="quarantine path")
    if not os.path.lexists(safe):
        raise WorkspaceError(f"quarantine path does not exist: {raw}")
    relative = safe.relative_to(root).as_posix()
    if relative in {"", "."}:
        raise PathSafetyError("quarantining the source root is not allowed")
    return relative, safe


def _tree_inventory_relative(target: Path, root: Path) -> list[dict[str, Any]]:
    """Inventory a target while retaining paths relative to the workspace."""

    kind = _path_kind(target)
    if kind == "file":
        digest, size = _hash_and_size(target)
        return [{"path": target.relative_to(root).as_posix(), "sha256": digest, "size": size}]
    if kind != "directory":
        raise PathSafetyError(f"quarantine target is not a regular file or directory: {target}")
    # No exclusions apply to an explicit quarantine target.
    files = _iter_regular_files(target, exclude_dirs=set())
    output: list[dict[str, Any]] = []
    for file_path in files:
        digest, size = _hash_and_size(file_path)
        output.append(
            {"path": file_path.relative_to(root).as_posix(), "sha256": digest, "size": size}
        )
    return sorted(output, key=lambda item: item["path"])


def plan_quarantine(
    root: os.PathLike[str] | str,
    paths: Sequence[os.PathLike[str] | str],
    backup_root: os.PathLike[str] | str,
) -> dict[str, Any]:
    """Build an explicit, hash-bound, JSON-serializable quarantine preview."""

    root_path = _safe_root(root)
    backup_path = _absolute(backup_root, label="backup_root")
    _assert_outside(backup_path, root_path, label="backup root")
    _reject_link_components(backup_path.parent)
    if os.path.lexists(backup_path) and _is_link_or_junction(backup_path):
        raise PathSafetyError(f"backup root is a link or junction: {backup_path}")

    if isinstance(paths, (str, os.PathLike)):
        raise TypeError("paths must be a sequence, not one path string")
    requested: list[tuple[str, Path]] = [_relative_request(root_path, item) for item in paths]
    if not requested:
        raise WorkspaceError("at least one quarantine path is required")
    requested.sort(key=lambda item: item[0])
    relative_paths = [item[0] for item in requested]
    for index, current in enumerate(relative_paths):
        current_parts = Path(current).parts
        for ancestor in relative_paths[:index]:
            ancestor_parts = Path(ancestor).parts
            if current_parts[: len(ancestor_parts)] == ancestor_parts:
                raise WorkspaceError(f"overlapping quarantine paths: {ancestor} and {current}")

    targets: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    for relative, target in requested:
        kind = _path_kind(target)
        files = _tree_inventory_relative(target, root_path)
        target_backup = _quarantine_backup_target(root_path, backup_path, relative)
        if os.path.lexists(target_backup):
            raise QuarantineError(f"backup destination already exists: {target_backup}")
        targets.append(
            {
                "path": relative,
                "kind": kind,
                "backup_path": relative,
                "file_count": len(files),
            }
        )
        entries.extend(files)
    entries.sort(key=lambda item: item["path"])

    unsigned: dict[str, Any] = {
        "schema": SCHEMA_QUARANTINE,
        "root": str(root_path),
        "backup_root": str(backup_path),
        "targets": targets,
        "files": entries,
    }
    plan_digest = hashlib.sha256(
        json.dumps(unsigned, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return {**unsigned, "plan_sha256": plan_digest, "status": "planned"}


def _quarantine_backup_target(root: Path, backup_root: Path, relative: str) -> Path:
    """Keep every recovery destination below its backup root and outside source."""

    target = backup_root / Path(relative)
    if not _is_within(target.absolute(), backup_root.absolute()):
        raise PathSafetyError(f"quarantine backup path leaves backup root: {relative}")
    _reject_link_components(target)
    if not _is_within(target.resolve(strict=False), backup_root.resolve(strict=False)):
        raise PathSafetyError(f"quarantine backup path resolves outside backup root: {relative}")
    _assert_outside(target, root, label="quarantine backup destination")
    return target


def _quarantine_files(targets: Sequence[Path], root: Path) -> dict[str, dict[str, Any]]:
    observed: dict[str, dict[str, Any]] = {}
    for target in targets:
        _reject_link_components(target)
        for entry in _tree_inventory_relative(target, root):
            observed[entry["path"]] = entry
    return observed


def _verify_quarantine_files(
    targets: Sequence[Path],
    root: Path,
    expected: Mapping[str, Mapping[str, Any]],
    *,
    label: str,
) -> None:
    observed = _quarantine_files(targets, root)
    if set(observed) != set(expected):
        raise QuarantineError(f"{label} file set changed")
    for relative, entry in expected.items():
        current = observed[relative]
        if current["sha256"] != entry["sha256"] or current["size"] != entry["size"]:
            raise QuarantineError(f"{label} hash changed: {relative}")


def _copy_quarantine_target(source: Path, destination: Path) -> None:
    """Copy to absent paths without replacing concurrent owner destinations."""

    def copy_file(raw_source: str | Path, raw_destination: str | Path) -> str:
        source_file, destination_file = Path(raw_source), Path(raw_destination)
        _reject_link_components(source_file)
        _reject_link_components(destination_file.parent)
        if _path_kind(source_file) != "file":
            raise PathSafetyError(f"quarantine copy requires a regular file: {source_file}")
        # Create privately and exclusively, including during cross-volume copy.
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        with source_file.open("rb") as reader:
            descriptor = os.open(destination_file, flags, 0o600)
            with os.fdopen(descriptor, "wb") as writer:
                shutil.copyfileobj(reader, writer)
                writer.flush()
                os.fsync(writer.fileno())
        shutil.copystat(source_file, destination_file, follow_symlinks=False)
        return str(destination_file)

    def reject_redirects(directory: str, names: list[str]) -> list[str]:
        parent = Path(directory)
        _reject_link_components(parent)
        for name in names:
            if _is_link_or_junction(parent / name):
                raise PathSafetyError(f"quarantine copy does not follow redirects: {parent / name}")
        return []

    _reject_link_components(source)
    _reject_link_components(destination.parent)
    kind = _path_kind(source)
    if kind == "file":
        copy_file(source, destination)
    elif kind == "directory":
        shutil.copytree(
            source, destination, copy_function=copy_file,
            ignore=reject_redirects, symlinks=True,
        )
    else:
        raise PathSafetyError(f"quarantine copy requires a regular target: {source}")


def _cleanup_quarantine_capture(
    source_root: Path,
    capture_root: Path,
    expected: Mapping[str, Mapping[str, Any]],
    directories: set[Path],
    *,
    backup_root: Path,
    backup_targets: Sequence[Path],
) -> str | None:
    """Remove verified private copies only; retain changed or unexpected paths."""

    try:
        _assert_outside(capture_root, source_root, label="quarantine capture cleanup")
        _reject_link_components(capture_root)
        _verify_quarantine_files(
            backup_targets, backup_root, expected, label="backup before capture cleanup"
        )
        _verify_quarantine_files([capture_root], capture_root, expected, label="capture cleanup")
        for relative, entry in expected.items():
            target = _assert_safe_child(
                capture_root, capture_root / Path(relative), label="capture cleanup file"
            )
            digest, size = _hash_and_size(target)
            if digest != entry["sha256"] or size != entry["size"]:
                raise QuarantineError(f"capture changed before cleanup: {relative}")
            backup = _quarantine_backup_target(source_root, backup_root, relative)
            backup_digest, backup_size = _hash_and_size(backup)
            if backup_digest != entry["sha256"] or backup_size != entry["size"]:
                raise QuarantineError(f"backup changed before capture cleanup: {relative}")
            target.unlink()
        # These directories were recorded before backup publication.  A new
        # empty directory is not implicitly ours to delete, and a new file
        # keeps its containing directory nonempty.
        for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
            _reject_link_components(directory)
            if directory != capture_root:
                _assert_safe_child(capture_root, directory, label="capture cleanup directory")
            directory.rmdir()
    except (OSError, WorkspaceError) as exc:
        return str(exc)
    return None


def _load_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(plan, Mapping):
        raise TypeError("quarantine plan must be a mapping")
    result = dict(plan)
    if result.get("schema") != SCHEMA_QUARANTINE:
        raise QuarantineError("unsupported quarantine plan schema")
    required = ("root", "backup_root", "targets", "files", "plan_sha256")
    missing = [key for key in required if key not in result]
    if missing:
        raise QuarantineError(f"quarantine plan is missing required keys: {', '.join(missing)}")
    if not isinstance(result["root"], str) or not isinstance(result["backup_root"], str):
        raise QuarantineError("quarantine plan roots must be strings")
    if not isinstance(result["plan_sha256"], str) or not result["plan_sha256"]:
        raise QuarantineError("quarantine plan is missing plan_sha256")
    if not isinstance(result.get("targets"), list) or not isinstance(result.get("files"), list):
        raise QuarantineError("quarantine plan is missing targets or files")
    return result


def _validate_plan_digest(plan: Mapping[str, Any]) -> None:
    supplied = plan.get("plan_sha256")
    if not isinstance(supplied, str):
        raise QuarantineError("quarantine plan is missing plan_sha256")
    unsigned = {key: plan[key] for key in ("schema", "root", "backup_root", "targets", "files")}
    expected = hashlib.sha256(
        json.dumps(unsigned, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    if supplied != expected:
        raise QuarantineError("quarantine plan digest does not match its contents")


def _validate_plan_preconditions(plan: Mapping[str, Any]) -> tuple[Path, Path, list[tuple[Path, Path, dict[str, Any]]]]:
    _validate_plan_digest(plan)
    root_path = _safe_root(str(plan["root"]))
    backup_path = _absolute(str(plan["backup_root"]), label="backup_root")
    _assert_outside(backup_path, root_path, label="backup root")
    _reject_link_components(backup_path.parent)
    if os.path.lexists(backup_path) and _is_link_or_junction(backup_path):
        raise PathSafetyError(f"backup root is a link or junction: {backup_path}")

    targets_raw = plan["targets"]
    files_raw = plan["files"]
    if not all(isinstance(item, Mapping) for item in targets_raw) or not all(
        isinstance(item, Mapping) for item in files_raw
    ):
        raise QuarantineError("quarantine plan contains malformed entries")
    expected_files: dict[str, dict[str, Any]] = {}
    for entry in files_raw:
        relative = entry.get("path")
        digest = entry.get("sha256")
        size = entry.get("size")
        if not isinstance(relative, str) or not isinstance(digest, str) or not isinstance(size, int):
            raise QuarantineError("quarantine file entry is malformed")
        if relative in expected_files:
            raise QuarantineError(f"duplicate quarantine file entry: {relative}")
        expected_files[relative] = {"path": relative, "sha256": digest, "size": size}

    target_specs: list[tuple[Path, Path, dict[str, Any]]] = []
    relatives: list[str] = []
    for target_raw in targets_raw:
        relative = target_raw.get("path")
        kind = target_raw.get("kind")
        backup_relative = target_raw.get("backup_path")
        if not isinstance(relative, str) or not isinstance(kind, str) or backup_relative != relative:
            raise QuarantineError("quarantine target entry is malformed")
        if relative in relatives:
            raise QuarantineError(f"duplicate quarantine target: {relative}")
        relatives.append(relative)
        target = _assert_safe_child(root_path, root_path / Path(relative), label="quarantine target")
        actual_kind = _path_kind(target)
        if actual_kind != kind:
            raise StalePlanError(f"quarantine target kind changed: {relative}")
        target_backup = _quarantine_backup_target(root_path, backup_path, relative)
        if os.path.lexists(target_backup):
            raise QuarantineError(f"backup destination already exists: {relative}")
        _reject_link_components(target_backup.parent)
        target_specs.append((target, target_backup, dict(target_raw)))

    relatives.sort()
    for index, current in enumerate(relatives):
        current_parts = Path(current).parts
        for ancestor in relatives[:index]:
            ancestor_parts = Path(ancestor).parts
            if current_parts[: len(ancestor_parts)] == ancestor_parts:
                raise QuarantineError(f"overlapping quarantine targets: {ancestor} and {current}")

    observed: dict[str, dict[str, Any]] = {}
    for target, _target_backup, _spec in target_specs:
        for entry in _tree_inventory_relative(target, root_path):
            observed[entry["path"]] = entry
    if set(observed) != set(expected_files):
        missing = sorted(set(expected_files) - set(observed))
        added = sorted(set(observed) - set(expected_files))
        raise StalePlanError(f"quarantine file set changed: missing={missing}, added={added}")
    for relative, expected in expected_files.items():
        actual = observed[relative]
        if actual["sha256"] != expected["sha256"] or actual["size"] != expected["size"]:
            raise StalePlanError(f"quarantine file hash changed: {relative}")
    return root_path, backup_path, target_specs


def _restore_moved(
    moved: list[tuple[Path, Path]],
    *,
    root: Path,
    backup_root: Path,
    expected: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    """Recover attempted moves without consuming backups or replacing owners."""

    failures: list[str] = []
    for source, backup in reversed(moved):
        try:
            relative = source.relative_to(root).as_posix()
            required = {
                name: entry for name, entry in expected.items()
                if name == relative or name.startswith(relative + "/")
            }
            if os.path.lexists(source):
                if not os.path.lexists(backup):
                    _verify_quarantine_files([source], root, required, label="unmoved quarantine source")
                    continue
                raise QuarantineError(f"source path already exists; recovery remains at {backup}")
            if not os.path.lexists(backup):
                raise QuarantineError(f"recovery path is missing: {backup}")
            saved = _quarantine_files([backup], backup_root)
            # Preserve additions made during the original move, while refusing
            # to present a changed or missing planned file as recovered.
            for name, entry in required.items():
                current = saved.get(name)
                if current is None or current["sha256"] != entry["sha256"] or current["size"] != entry["size"]:
                    raise QuarantineError(f"recovery content differs from the plan: {name}")
            saved_kind = _path_kind(backup)
            safe_source = _assert_safe_child(root, source, label="quarantine rollback path")
            _reject_link_components(backup)
            if safe_source.parent == root:
                _reject_link_components(root)
            else:
                _assert_safe_child(root, safe_source.parent, label="quarantine rollback parent")
            source.parent.mkdir(parents=True, exist_ok=True)
            safe_source = _assert_safe_child(root, safe_source, label="quarantine rollback path")
            if os.path.lexists(safe_source):
                raise QuarantineError(f"source path appeared during rollback; recovery remains at {backup}")
            _reject_link_components(backup)
            _copy_quarantine_target(backup, safe_source)
            if _path_kind(safe_source) != saved_kind:
                raise QuarantineError("rollback copy did not restore the recorded path")
            _verify_quarantine_files([safe_source], root, saved, label="quarantine rollback")
            _verify_quarantine_files([backup], backup_root, saved, label="retained quarantine backup")
        except (OSError, WorkspaceError) as exc:
            failures.append(f"{source}: {exc}")
    return failures


def apply_quarantine(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Capture planned paths, publish exclusive backups, and retain recovery."""

    loaded = _load_plan(plan)
    root_path, backup_path, target_specs = _validate_plan_preconditions(loaded)
    _reject_link_components(backup_path)
    backup_path.mkdir(parents=True, exist_ok=True)
    _reject_link_components(backup_path)
    capture_root = Path(tempfile.mkdtemp(prefix=".capture-", dir=str(backup_path)))
    _assert_outside(capture_root, root_path, label="quarantine capture root")
    _reject_link_components(capture_root)
    capture_directories = {capture_root}
    captured: list[tuple[Path, Path]] = []
    published: list[tuple[Path, Path]] = []
    backup_attempts: list[tuple[str, Path]] = []
    expected = {entry["path"]: entry for entry in loaded["files"]}
    try:
        ordered_targets = sorted(
            target_specs,
            key=lambda item: item[0].relative_to(root_path).as_posix(),
        )
        for target, target_backup, spec in ordered_targets:
            safe_target = _assert_safe_child(root_path, target, label="quarantine target")
            _reject_link_components(target_backup)
            if os.path.lexists(target_backup):
                raise QuarantineError(f"backup destination appeared during quarantine: {target_backup}")
            relative = spec["path"]
            capture = _quarantine_backup_target(root_path, capture_root, relative)
            capture.parent.mkdir(parents=True, exist_ok=True)
            parent = capture.parent
            while parent != capture_root:
                capture_directories.add(parent)
                parent = parent.parent
            _reject_link_components(capture)
            if os.path.lexists(capture):
                raise QuarantineError(f"private capture path appeared during quarantine: {capture}")
            safe_target = _assert_safe_child(root_path, safe_target, label="quarantine target")
            # A cross-filesystem move can create a destination before raising.
            # Capture in a new private directory before publishing the final
            # backup, whose name may be claimed by another owner meanwhile.
            captured.append((safe_target, capture))
            shutil.move(str(safe_target), str(capture))
            required = {
                name: entry for name, entry in expected.items()
                if name == relative or name.startswith(relative + "/")
            }
            _verify_quarantine_files([capture], capture_root, required, label="quarantine capture")
            if spec["kind"] == "directory":
                for directory, children, _files in os.walk(capture, followlinks=False):
                    current = Path(directory)
                    _reject_link_components(current)
                    for name in children:
                        _reject_link_components(current / name)
                    capture_directories.add(current)
            target_backup.parent.mkdir(parents=True, exist_ok=True)
            _reject_link_components(target_backup)
            backup_attempts.append((relative, target_backup))
            _copy_quarantine_target(capture, target_backup)
            published.append((safe_target, target_backup))

        # Re-read all moved files from their backup locations before exposing a
        # successful receipt.  This catches a provider copy/replace mismatch.
        _verify_quarantine_files(
            [backup for _target, backup, _spec in target_specs],
            backup_path,
            expected,
            label="quarantine backup",
        )
        _verify_quarantine_files(
            [capture for _source, capture in captured],
            capture_root, expected, label="retained quarantine capture",
        )
        for target, _backup, _spec in target_specs:
            safe_target = _assert_safe_child(root_path, target, label="quarantine source postcheck")
            if os.path.lexists(safe_target):
                raise QuarantineError("a source path remained or appeared during quarantine")
        cleanup_problem = _cleanup_quarantine_capture(
            root_path, capture_root, expected, capture_directories,
            backup_root=backup_path,
            backup_targets=[backup for _target, backup, _spec in target_specs],
        )
        # A cleanup warning can describe a retained capture, but it cannot
        # make an invalid final backup into an applied quarantine.
        _verify_quarantine_files(
            [backup for _target, backup, _spec in target_specs],
            backup_path, expected, label="final quarantine backup",
        )
    except Exception as exc:
        failures = _restore_moved(
            captured,
            root=root_path,
            backup_root=capture_root,
            expected=expected,
        )
        retained = [relative for relative, path in backup_attempts if os.path.lexists(path)]
        captured_paths = [
            source.relative_to(root_path).as_posix()
            for source, capture in captured if os.path.lexists(capture)
        ]
        capture_state = (
            f"capture directory remains at {capture_root}; captured paths present={captured_paths}"
            if os.path.lexists(capture_root)
            else f"capture directory is absent at {capture_root}"
        )
        recovery = (
            f"{capture_state}; "
            f"backup paths retained for reconciliation={retained} under {backup_path}"
        )
        if failures:
            raise QuarantineError(
                f"quarantine failed and rollback was incomplete: {exc}; rollback={failures}; {recovery}"
            ) from exc
        raise QuarantineError(
            f"quarantine failed; all moved paths were restored; {recovery}: {exc}"
        ) from exc

    result = {
        "schema": "codex-nexus/quarantine-receipt/v1",
        "status": "applied",
        "moved_count": len(published),
        "moved": [
            {
                "source": source.relative_to(root_path).as_posix(),
                "backup": backup.relative_to(backup_path).as_posix(),
            }
            for source, backup in published
        ],
        "plan_sha256": loaded["plan_sha256"],
    }
    if cleanup_problem is not None:
        result["capture_retained"] = capture_root.relative_to(backup_path).as_posix()
        result["warnings"] = [f"capture cleanup incomplete: {cleanup_problem}"]
    return result


def restore_quarantine(
    plan: Mapping[str, Any],
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Restore every path recorded by an applied quarantine receipt.

    Restoration is conservative: an existing source path is treated as a
    conflict and is never overwritten.  Exclusive copies preserve the original
    backup, including after success.  Final source and backup hashes are both
    checked.  A failed restore retains partial copies and owner edits in place
    and identifies the affected source paths for reconciliation.
    """

    loaded = _load_plan(plan)
    _validate_plan_digest(loaded)
    if not isinstance(receipt, Mapping) or receipt.get("schema") != "codex-nexus/quarantine-receipt/v1":
        raise QuarantineError("invalid quarantine receipt")
    if receipt.get("status") != "applied" or receipt.get("plan_sha256") != loaded["plan_sha256"]:
        raise QuarantineError("quarantine receipt does not match the plan")

    root_path = _safe_root(str(loaded["root"]))
    backup_path = _absolute(str(loaded["backup_root"]), label="backup_root")
    _assert_outside(backup_path, root_path, label="backup root")
    _reject_link_components(backup_path)
    if not backup_path.exists() or not backup_path.is_dir() or _is_link_or_junction(backup_path):
        raise QuarantineError("quarantine backup root is unavailable")

    targets_raw = loaded["targets"]
    files_raw = loaded["files"]
    expected_files: dict[str, dict[str, Any]] = {}
    for entry in files_raw:
        if not isinstance(entry, Mapping):
            raise QuarantineError("quarantine plan contains malformed file entries")
        relative, digest, size = entry.get("path"), entry.get("sha256"), entry.get("size")
        if not isinstance(relative, str) or not isinstance(digest, str) or not isinstance(size, int):
            raise QuarantineError("quarantine plan contains malformed file entries")
        if relative in expected_files:
            raise QuarantineError(f"duplicate quarantine file entry: {relative}")
        expected_files[relative] = {"path": relative, "sha256": digest, "size": size}

    target_specs: list[tuple[Path, Path, str]] = []
    target_relatives: list[str] = []
    for target_raw in targets_raw:
        if not isinstance(target_raw, Mapping):
            raise QuarantineError("quarantine plan contains malformed target entries")
        relative, kind, backup_relative = (
            target_raw.get("path"),
            target_raw.get("kind"),
            target_raw.get("backup_path"),
        )
        if not isinstance(relative, str) or not isinstance(kind, str) or backup_relative != relative:
            raise QuarantineError("quarantine plan contains malformed target entries")
        if relative in target_relatives:
            raise QuarantineError(f"duplicate quarantine target: {relative}")
        target_relatives.append(relative)
        source = _assert_safe_child(
            root_path,
            root_path / Path(relative),
            label="quarantine restore path",
        )
        if os.path.lexists(source):
            raise QuarantineError(f"restore source path already exists: {relative}")
        backup = _quarantine_backup_target(root_path, backup_path, relative)
        if not os.path.lexists(backup) or _path_kind(backup) != kind:
            raise QuarantineError(f"quarantine backup target is unavailable: {relative}")
        target_specs.append((source, backup, relative))

    moved_raw = receipt.get("moved")
    if not isinstance(moved_raw, list):
        raise QuarantineError("quarantine receipt is missing moved paths")
    receipt_pairs = {
        (item.get("source"), item.get("backup"))
        for item in moved_raw
        if isinstance(item, Mapping)
    }
    expected_pairs = {(relative, relative) for _source, _backup, relative in target_specs}
    if receipt_pairs != expected_pairs:
        raise QuarantineError("quarantine receipt does not cover the planned targets")

    backups = [backup for _source, backup, _relative in target_specs]
    _verify_quarantine_files(backups, backup_path, expected_files, label="quarantine backup")
    attempted: list[tuple[Path, str]] = []
    try:
        for source, backup, relative in sorted(target_specs, key=lambda item: item[2], reverse=True):
            safe_source = _assert_safe_child(root_path, source, label="quarantine restore path")
            if os.path.lexists(safe_source):
                raise QuarantineError(f"restore source path appeared during restore: {source}")
            _reject_link_components(backup)
            if safe_source.parent == root_path:
                _reject_link_components(root_path)
            else:
                _assert_safe_child(root_path, safe_source.parent, label="quarantine restore parent")
            source.parent.mkdir(parents=True, exist_ok=True)
            # Recheck both sides before copying.  Exclusive creation still
            # refuses a destination that appears after the existence check.
            safe_source = _assert_safe_child(root_path, safe_source, label="quarantine restore path")
            if os.path.lexists(safe_source):
                raise QuarantineError(f"restore source path appeared during restore: {source}")
            _reject_link_components(backup)
            attempted.append((safe_source, relative))
            _copy_quarantine_target(backup, safe_source)
        _verify_quarantine_files(
            [source for source, _backup, _relative in target_specs],
            root_path, expected_files, label="quarantine restored source",
        )
        _verify_quarantine_files(backups, backup_path, expected_files, label="retained quarantine backup")
    except Exception as exc:
        partial = [relative for source, relative in attempted if os.path.lexists(source)]
        if partial:
            raise QuarantineError(
                f"quarantine restore was incomplete: {exc}; "
                f"source paths retained for reconciliation={partial}; backup retained at {backup_path}"
            ) from exc
        raise QuarantineError(f"quarantine restore failed; backup retained at {backup_path}: {exc}") from exc

    return {
        "schema": "codex-nexus/quarantine-restore-receipt/v1",
        "status": "restored",
        "restored_count": len(target_specs),
        "backup_retained": True,
        "restored": [
            {"source": source.relative_to(root_path).as_posix(), "backup": backup.relative_to(backup_path).as_posix()}
            for source, backup, _relative in target_specs
        ],
        "plan_sha256": loaded["plan_sha256"],
    }


__all__ = [
    "PathSafetyError",
    "QuarantineError",
    "SCHEMA_QUARANTINE",
    "SCHEMA_SNAPSHOT",
    "StalePlanError",
    "WorkspaceError",
    "apply_quarantine",
    "plan_quarantine",
    "restore_quarantine",
    "sha256",
    "snapshot",
    "source_files",
    "write_json",
    "write_text_atomic",
]
