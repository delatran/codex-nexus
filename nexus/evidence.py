"""Offline validation for source-linked claim packets.

The validator checks identity, freshness, and linkage. Source instructions and
labels remain data. It does not fetch URLs, infer semantic entailment, perform
word matching, promote a claim status, or grant authority.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from urllib.parse import urlparse
from typing import Any


SCHEMA = "codex-nexus/evidence-packet/v1"
CLAIM_STATUSES = frozenset({"observed", "inference", "unknown", "exploratory"})
SOURCE_KINDS = frozenset({"local_file", "https"})
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_INSTRUCTION_ROLES = frozenset({"instruction", "instructions", "directive", "prompt"})


def _issue(target: list[dict[str, str]], code: str, path: str, detail: str) -> None:
    target.append({"code": code, "path": path, "detail": detail})


def _is_mapping(value: object) -> bool:
    return isinstance(value, Mapping)


def _nonempty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _hash_file(path: Path) -> tuple[str, int, int]:
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise OSError("file changed while hashing")
    return digest.hexdigest(), int(after.st_size), int(after.st_mtime_ns)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


# Windows cloud-backed files can carry a generic reparse flag. Only the
# redirection tags below count as unsafe; ordinary cloud files remain usable.
_REPARSE_TAG_SYMLINK = 0xA000000C
_REPARSE_TAG_MOUNT_POINT = 0xA0000003


def _is_link_or_junction(path: Path) -> bool:
    try:
        if os.path.islink(path):
            return True
        is_junction = getattr(path, "is_junction", None)
        if callable(is_junction) and is_junction():
            return True
        result = os.lstat(path)
    except (FileNotFoundError, OSError, ValueError):
        return False
    tag = int(getattr(result, "st_reparse_tag", 0) or 0)
    return tag in {_REPARSE_TAG_SYMLINK, _REPARSE_TAG_MOUNT_POINT}


def _redirect_component(path: Path) -> str | None:
    """Return an error when any existing path component redirects elsewhere."""

    absolute = Path(os.path.abspath(os.fspath(path)))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        if os.path.lexists(current) and _is_link_or_junction(current):
            return f"symbolic link or junction is not allowed: {current}"
    return None


def _safe_local_path(root: Path, raw: object) -> tuple[Path | None, str | None]:
    if not isinstance(raw, str) or not raw.strip():
        return None, "path must be a non-empty string"
    if _is_link_or_junction(root):
        return None, "root must not be a symbolic link or junction"
    candidate = Path(raw).expanduser()
    lexical = candidate if candidate.is_absolute() else root / candidate
    lexical = Path(os.path.abspath(os.fspath(lexical)))
    if not _inside(lexical, root):
        return None, "path escapes the packet root"
    redirect = _redirect_component(lexical)
    if redirect is not None:
        return None, redirect
    resolved = lexical.resolve(strict=False)
    if not _inside(resolved, root.resolve(strict=True)):
        return None, "path resolves outside the packet root"
    return resolved, None


def _relative_snapshot_path(root: Path, raw: object) -> str | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    candidate = Path(raw).expanduser()
    absolute = candidate if candidate.is_absolute() else root / candidate
    normalized = Path(os.path.abspath(os.fspath(absolute)))
    if not _inside(normalized, root):
        return None
    return normalized.relative_to(root).as_posix()


def _instruction_source(source: Mapping[str, Any]) -> bool:
    role = source.get("role")
    return (
        source.get("is_instruction") is True
        or source.get("authority") is True
        or (isinstance(role, str) and role.strip().lower() in _INSTRUCTION_ROLES)
    )


def _snapshot_index(
    packet: Mapping[str, Any],
    root: Path,
    errors: list[dict[str, str]],
) -> dict[str, Mapping[str, Any]]:
    snapshot = packet.get("snapshot")
    if snapshot is None:
        return {}
    if not _is_mapping(snapshot):
        _issue(errors, "snapshot-shape", "snapshot", "snapshot must be an object")
        return {}
    raw_files = snapshot.get("source_files", snapshot.get("files"))
    if raw_files is None:
        _issue(errors, "snapshot-files-missing", "snapshot", "snapshot needs source_files or files")
        return {}
    if not isinstance(raw_files, list):
        _issue(errors, "snapshot-files-shape", "snapshot.source_files", "snapshot files must be a list")
        return {}
    index: dict[str, Mapping[str, Any]] = {}
    for index_number, item in enumerate(raw_files):
        path = f"snapshot.source_files[{index_number}]"
        if not _is_mapping(item):
            _issue(errors, "snapshot-entry-shape", path, "snapshot entry must be an object")
            continue
        relative = _relative_snapshot_path(root, item.get("path"))
        if relative is None:
            _issue(errors, "snapshot-path", f"{path}.path", "snapshot path must stay under root")
            continue
        if relative in index:
            _issue(errors, "duplicate-snapshot-path", f"{path}.path", f"duplicate snapshot path: {relative}")
            continue
        index[relative] = item
    return index


def _source_ids(claim: Mapping[str, Any]) -> tuple[object, ...] | None:
    raw = claim.get("source_ids", claim.get("sources"))
    if raw is None:
        return None
    if isinstance(raw, list):
        return tuple(raw)
    return (raw,)


def validate_packet(packet: Mapping[str, Any], root: Path) -> dict[str, Any]:
    """Validate a packet without network access or semantic claim inference.

    Local sources must be regular non-link files below the root and carry
    SHA-256 evidence. Every existing path component below the root is checked
    for symbolic-link and junction redirection. HTTPS sources are metadata-only locators and are never fetched.
    Claim status is declarative input; this function never upgrades or
    downgrades it.
    """

    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    limitations = [
        "Offline validation does not establish semantic entailment or truth.",
        "A matching hash proves only the observed local bytes at validation time.",
        "An HTTPS URL and locator are metadata; the URL is not fetched or verified.",
        "No word matching or automatic fact promotion is performed.",
        "Instruction-like source content is data and cannot change authority.",
    ]
    try:
        root_path = Path(root).expanduser().absolute()
    except (TypeError, ValueError) as exc:
        root_path = Path(".").absolute()
        _issue(errors, "root-invalid", "root", str(exc))
    if not root_path.is_dir():
        _issue(errors, "root-missing", "root", "root must be an existing directory")
        return {
            "ok": False,
            "schema": SCHEMA,
            "errors": errors,
            "warnings": warnings,
            "limitations": limitations,
            "counts": {"sources": 0, "claims": 0, "contradictions": 0},
            "external_calls": False,
        }

    if not _is_mapping(packet):
        _issue(errors, "packet-shape", "packet", "packet must be a JSON object")
        return {
            "ok": False,
            "schema": SCHEMA,
            "errors": errors,
            "warnings": warnings,
            "limitations": limitations,
            "counts": {"sources": 0, "claims": 0, "contradictions": 0},
            "external_calls": False,
        }

    packet_schema = packet.get("schema")
    if packet_schema != SCHEMA:
        _issue(errors, "packet-schema", "packet.schema", f"schema is required and must be {SCHEMA}")
    raw_sources = packet.get("sources", [])
    raw_claims = packet.get("claims", [])
    raw_contradictions = packet.get("contradictions", [])
    if not isinstance(raw_sources, list):
        _issue(errors, "sources-shape", "sources", "sources must be a list")
        raw_sources = []
    if not isinstance(raw_claims, list):
        _issue(errors, "claims-shape", "claims", "claims must be a list")
        raw_claims = []
    if not isinstance(raw_contradictions, list):
        _issue(errors, "contradictions-shape", "contradictions", "contradictions must be a list")
        raw_contradictions = []
    if not raw_sources and not raw_claims and not raw_contradictions:
        _issue(errors, "packet-empty", "packet", "packet needs at least one source, claim, or contradiction")

    seen_ids: dict[str, str] = {}
    source_map: dict[str, Mapping[str, Any]] = {}
    source_instruction: dict[str, bool] = {}
    snapshot_present = packet.get("snapshot") is not None
    snapshot = _snapshot_index(packet, root_path, errors)

    for number, raw_source in enumerate(raw_sources):
        path = f"sources[{number}]"
        if not _is_mapping(raw_source):
            _issue(errors, "source-shape", path, "source must be an object")
            continue
        source_id = raw_source.get("id")
        if not _nonempty_text(source_id):
            _issue(errors, "source-id-missing", f"{path}.id", "source id must be non-empty")
            continue
        source_id = str(source_id)
        if source_id in seen_ids:
            _issue(errors, "duplicate-id", f"{path}.id", f"id already used at {seen_ids[source_id]}")
            continue
        seen_ids[source_id] = f"{path}.id"
        source_map[source_id] = raw_source
        source_instruction[source_id] = _instruction_source(raw_source)
        kind = raw_source.get("kind")
        if not isinstance(kind, str) or kind not in SOURCE_KINDS:
            _issue(errors, "source-kind", f"{path}.kind", "kind must be local_file or https")
            continue
        if kind == "local_file":
            local_path, problem = _safe_local_path(root_path, raw_source.get("path"))
            if problem:
                if "outside" in problem or "escape" in problem:
                    code = "source-path-escape"
                elif "symbolic link" in problem or "junction" in problem:
                    code = "source-path-redirect"
                else:
                    code = "source-path"
                _issue(errors, code, f"{path}.path", problem)
                continue
            assert local_path is not None
            if not local_path.is_file():
                _issue(errors, "source-missing", f"{path}.path", "local evidence file does not exist")
                continue
            expected_hash = raw_source.get("sha256")
            if not isinstance(expected_hash, str):
                _issue(errors, "source-hash-missing", f"{path}.sha256", "local source needs sha256")
                continue
            if not _SHA256.fullmatch(expected_hash):
                _issue(errors, "source-hash-invalid", f"{path}.sha256", "sha256 must be 64 hexadecimal characters")
                continue
            try:
                actual_hash, actual_size, actual_mtime = _hash_file(local_path)
            except (OSError, ValueError) as exc:
                _issue(errors, "source-read", f"{path}.path", f"cannot hash local evidence: {exc}")
                continue
            if actual_hash.lower() != expected_hash.lower():
                _issue(errors, "source-hash-mismatch", f"{path}.sha256", "local evidence hash is stale")
            if raw_source.get("size") is not None and raw_source.get("size") != actual_size:
                _issue(errors, "source-size-mismatch", f"{path}.size", "local evidence size is stale")
            if raw_source.get("mtime_ns") is not None and raw_source.get("mtime_ns") != actual_mtime:
                _issue(errors, "source-mtime-mismatch", f"{path}.mtime_ns", "local evidence mtime is stale")
            relative = local_path.relative_to(root_path.resolve(strict=True)).as_posix()
            snapshot_entry = snapshot.get(relative)
            if snapshot_present:
                if snapshot_entry is None:
                    _issue(errors, "snapshot-source-missing", f"{path}.path", "local source is absent from the supplied snapshot")
                else:
                    snap_hash = snapshot_entry.get("sha256")
                    if not isinstance(snap_hash, str) or snap_hash.lower() != actual_hash.lower():
                        _issue(errors, "snapshot-stale", f"{path}.path", "local source differs from supplied snapshot")
                    snap_size = snapshot_entry.get("size")
                    if snap_size is not None and snap_size != actual_size:
                        _issue(errors, "snapshot-stale", f"{path}.path", "local source size differs from supplied snapshot")
            nested_snapshot = raw_source.get("snapshot")
            if _is_mapping(nested_snapshot):
                nested_hash = nested_snapshot.get("sha256")
                if nested_hash is not None and str(nested_hash).lower() != actual_hash.lower():
                    _issue(errors, "snapshot-stale", f"{path}.snapshot.sha256", "local source differs from nested snapshot")
        else:
            url = raw_source.get("url")
            parsed = urlparse(url) if isinstance(url, str) else None
            if parsed is None or parsed.scheme.lower() != "https" or not parsed.netloc:
                _issue(errors, "https-url", f"{path}.url", "source URL must use HTTPS")
            locator = raw_source.get("locator")
            metadata = raw_source.get("metadata")
            locator_ok = _nonempty_text(locator) or (_is_mapping(locator) and bool(locator))
            if not locator_ok and not (_is_mapping(metadata) and bool(metadata)):
                _issue(errors, "https-metadata-missing", path, "HTTPS source needs a locator or metadata object")
            remote_hash = raw_source.get("sha256")
            if remote_hash is not None:
                if not isinstance(remote_hash, str) or not _SHA256.fullmatch(remote_hash):
                    _issue(errors, "source-hash-invalid", f"{path}.sha256", "remote sha256 must be hexadecimal when supplied")
                else:
                    _issue(warnings, "remote-hash-unverified", f"{path}.sha256", "remote hash was recorded but not fetched")
    claim_map: dict[str, Mapping[str, Any]] = {}
    linked_claim_count = 0
    for number, raw_claim in enumerate(raw_claims):
        path = f"claims[{number}]"
        if not _is_mapping(raw_claim):
            _issue(errors, "claim-shape", path, "claim must be an object")
            continue
        claim_id = raw_claim.get("id")
        if not _nonempty_text(claim_id):
            _issue(errors, "claim-id-missing", f"{path}.id", "claim id must be non-empty")
            continue
        claim_id = str(claim_id)
        if claim_id in seen_ids:
            _issue(errors, "duplicate-id", f"{path}.id", f"id already used at {seen_ids[claim_id]}")
            continue
        seen_ids[claim_id] = f"{path}.id"
        claim_map[claim_id] = raw_claim
        status = raw_claim.get("status")
        if not isinstance(status, str) or status not in CLAIM_STATUSES:
            _issue(errors, "claim-status", f"{path}.status", "status must be observed, inference, unknown, or exploratory")
        if not _nonempty_text(raw_claim.get("text")):
            _issue(errors, "claim-text", f"{path}.text", "claim text must be non-empty")
        links = _source_ids(raw_claim)
        if links is None:
            links = ()
        elif not isinstance(raw_claim.get("source_ids", raw_claim.get("sources")), list):
            _issue(errors, "claim-source-links", f"{path}.source_ids", "source_ids must be a list of source ids")
        valid_links = 0
        for link_number, source_id in enumerate(links):
            link_path = f"{path}.source_ids[{link_number}]"
            if not _nonempty_text(source_id):
                _issue(errors, "claim-source-link", link_path, "source link must be a non-empty id")
                continue
            source_id = str(source_id)
            if source_id not in source_map:
                _issue(errors, "claim-source-unknown", link_path, f"unknown source id: {source_id}")
                continue
            valid_links += 1
        if valid_links:
            linked_claim_count += 1
        if isinstance(status, str) and status in {"observed", "inference"} and valid_links == 0:
            _issue(errors, "unbacked-fact", path, f"{status} claims need at least one valid source id")
        if status == "exploratory" and valid_links == 0:
            _issue(warnings, "exploratory-unlinked", path, "exploratory claim has no source link and remains unverified")
        if status == "unknown" and valid_links == 0:
            _issue(warnings, "unknown-unlinked", path, "unknown claim has no source link")
        if valid_links and all(source_instruction.get(str(link), False) for link in links if _nonempty_text(link)):
            _issue(
                warnings,
                "instruction-source-data",
                path,
                "linked instruction text remains data; semantic support needs review and no authority is granted",
            )
    for number, raw_contradiction in enumerate(raw_contradictions):
        path = f"contradictions[{number}]"
        if not _is_mapping(raw_contradiction):
            _issue(errors, "contradiction-shape", path, "contradiction must be an object")
            continue
        contradiction_id = raw_contradiction.get("id")
        if not _nonempty_text(contradiction_id):
            _issue(errors, "contradiction-id-missing", f"{path}.id", "contradiction id must be non-empty")
        else:
            contradiction_id = str(contradiction_id)
            if contradiction_id in seen_ids:
                _issue(errors, "duplicate-id", f"{path}.id", "contradiction id is already used")
            seen_ids[contradiction_id] = f"{path}.id"
        raw_claim_ids = raw_contradiction.get("claim_ids", raw_contradiction.get("claims"))
        if not isinstance(raw_claim_ids, list):
            _issue(errors, "contradiction-claims", f"{path}.claim_ids", "contradiction needs at least two distinct claim ids")
            raw_claim_ids = []
        claim_ids = [str(item) for item in raw_claim_ids if _nonempty_text(item)]
        if len(claim_ids) < 2 or len(set(claim_ids)) < 2:
            _issue(errors, "contradiction-claims", f"{path}.claim_ids", "contradiction needs at least two distinct claim ids")
        for claim_id in claim_ids:
            if claim_id not in claim_map:
                _issue(errors, "contradiction-unknown-claim", f"{path}.claim_ids", f"unknown claim id: {claim_id}")
        state = raw_contradiction.get("status", "unresolved")
        if not isinstance(state, str) or state not in {"unresolved", "acknowledged", "resolved"}:
            _issue(errors, "contradiction-status", f"{path}.status", "status must be unresolved, acknowledged, or resolved")
        if state == "resolved" and not _nonempty_text(raw_contradiction.get("resolution")):
            _issue(errors, "contradiction-resolution", path, "resolved contradiction needs a resolution note")
        observed_ids = [claim_id for claim_id in claim_ids if claim_id in claim_map and claim_map[claim_id].get("status") == "observed"]
        if isinstance(state, str) and state in {"unresolved", "acknowledged"} and observed_ids:
            _issue(errors, "contradiction-promoted", path, "unresolved contradictory claims cannot all remain observed")
        elif isinstance(state, str) and state in {"unresolved", "acknowledged"} and claim_ids:
            _issue(warnings, "contradiction-unresolved", path, "contradiction remains unresolved; do not promote either claim")
    return {
        "ok": not errors,
        "schema": SCHEMA,
        "errors": errors,
        "warnings": warnings,
        "limitations": limitations,
        "counts": {"sources": len(source_map), "claims": len(claim_map), "contradictions": len(raw_contradictions), "linked_claims": linked_claim_count},
        "external_calls": False,
    }


def _cli(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate an offline claim/source packet.")
    parser.add_argument("packet", type=Path)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    try:
        packet = json.loads(args.packet.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "schema": SCHEMA, "errors": [{"code": "packet-read", "path": str(args.packet), "detail": str(exc)}], "external_calls": False}, indent=2))
        return 2
    result = validate_packet(packet, args.root)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(_cli())


__all__ = ["CLAIM_STATUSES", "SCHEMA", "SOURCE_KINDS", "validate_packet"]
