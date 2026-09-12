"""Portable source checks, a hash inventory, and reproducible source packaging."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .workspace import _safe_root, source_files, sha256, write_json


GENERATED = {"SOURCE_MANIFEST.json"}
REQUIRED = {"AGENTS.md", ".codex/config.toml",
            "README.md", "LICENSE", "setup.py", "nexus/__main__.py"}
ALLOWED_ROOTS = {".codex", ".github", "docs", "nexus", "skills", "tests"}
ALLOWED_FILES = REQUIRED | GENERATED | {".gitignore", ".gitattributes", ".editorconfig",
                                      "CONTRIBUTING.md", "SECURITY.md"}


def inventory(root: Path) -> dict:
    root = _safe_root(root)
    entries = []
    for path in source_files(root):
        relative = path.relative_to(root).as_posix()
        if relative in GENERATED:
            continue
        entries.append({"path": relative, "sha256": sha256(path), "bytes": path.stat().st_size})
    return {"schema": "codex-nexus/source-inventory/v1", "files": entries,
            "file_count": len(entries), "source_bytes": sum(item["bytes"] for item in entries)}


def update_inventory(root: Path) -> dict:
    root = _safe_root(root)
    result = inventory(root)
    write_json(root / "SOURCE_MANIFEST.json", result)
    return result


def skill_metadata(text: str) -> dict:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise ValueError("missing YAML frontmatter")
    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise ValueError("unclosed YAML frontmatter") from exc
    result = {}
    nested_metadata = False
    metadata_keys = set()
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line == "metadata:" and "metadata" not in result:
            result["metadata"] = {}
            nested_metadata = True
            continue
        if line.startswith("  ") and nested_metadata:
            key, separator, value = line.strip().partition(":")
            if not separator or not key or key in metadata_keys or not value.strip():
                raise ValueError("invalid nested skill metadata")
            metadata_keys.add(key)
            result["metadata"][key] = value.strip()
            continue
        nested_metadata = False
        key, separator, value = line.partition(":")
        if not separator or key not in {"name", "description"} or key in result:
            raise ValueError("frontmatter uses duplicate or unsupported keys")
        value = value.strip()
        if value.startswith('"'):
            value = json.loads(value)
        elif value.startswith("'") and value.endswith("'"):
            value = value[1:-1].replace("''", "'")
        if not isinstance(value, str) or not value or value in {"|", ">"}:
            raise ValueError("name and description must be single-line strings")
        result[key] = value
    if not {"name", "description"}.issubset(result):
        raise ValueError("name and description are required")
    return result


def static_checks(root: Path) -> dict:
    root = _safe_root(root)
    errors, warnings = [], []
    for item in root.iterdir():
        if item.name not in ALLOWED_ROOTS | ALLOWED_FILES | {".git", "artifacts"}:
            errors.append(f"unowned root entry: {item.name}")
    for directory, children, _ in os.walk(root, followlinks=False):
        children[:] = [name for name in children if name not in {".git", "artifacts"}]
        if "__pycache__" in children:
            errors.append(f"cache in source tree: {Path(directory).relative_to(root).as_posix()}")
            children.remove("__pycache__")
    files = source_files(root)
    names = {path.relative_to(root).as_posix() for path in files}
    errors.extend(f"missing required source: {name}" for name in sorted(REQUIRED - names))
    for path in files:
        relative = path.relative_to(root).as_posix()
        parts = Path(relative).parts
        if parts[0] == ".codex" and relative != ".codex/config.toml":
            errors.append(f"unowned Codex source surface: {relative}")
        if (len(parts) > 1 and parts[0] not in ALLOWED_ROOTS) or (len(parts) == 1 and relative not in ALLOWED_FILES):
            errors.append(f"unowned source surface: {relative}")
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeError, OSError):
            errors.append(f"source is not readable UTF-8: {relative}")
            continue
        if any(ord(char) > 127 and char.isalpha() for char in text):
            errors.append(f"non-English alphabet requires review: {relative}")
        if chr(0x2014) in text:
            errors.append(f"em dash violates the owner's source style: {relative}")
        if re.search(r"\bsk-[A-Za-z0-9_-]{24,}\b", text):
            errors.append(f"possible credential: {relative}")
        if re.search(r"(?i)[a-z]:[/\\]users[/\\](?!<|%|\{)[a-z0-9_ -]+[/\\]", text):
            errors.append(f"private absolute home path: {relative}")
        try:
            if path.suffix == ".json":
                json.loads(text)
            elif path.suffix == ".toml":
                tomllib.loads(text)
            elif path.suffix == ".py":
                ast.parse(text, filename=relative)
        except (ValueError, SyntaxError) as exc:
            errors.append(f"invalid syntax: {relative}: {type(exc).__name__}")
        if path.suffix == ".md":
            for target in re.findall(r"\]\(([^)]+)\)", text):
                target = target.strip().strip("<>")
                parsed = urlsplit(target)
                if parsed.scheme or not parsed.path:
                    continue
                destination = (path.parent / unquote(parsed.path)).resolve()
                if not destination.is_relative_to(root.resolve()) or not destination.exists():
                    errors.append(f"broken or escaping local link: {relative}: {target}")
    skill_names = []
    descriptions = set()
    for path in sorted((root / "skills").glob("*/SKILL.md")):
        try:
            metadata = skill_metadata(path.read_text(encoding="utf-8"))
            name, description = metadata["name"], metadata["description"]
            if name != path.parent.name or not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", name) or len(name) > 64:
                errors.append(f"invalid skill name: {path.parent.name}")
            if not 24 <= len(description) <= 1024 or description in descriptions:
                errors.append(f"invalid or duplicate skill description: {name}")
            if len(path.read_text(encoding="utf-8").split()) > 2000:
                warnings.append(f"review instruction length: {name}")
            descriptions.add(description)
            skill_names.append(name)
        except (ValueError, OSError) as exc:
            errors.append(f"invalid skill metadata: {path.parent.name}: {exc}")
    for directory in (root / "skills").iterdir() if (root / "skills").is_dir() else []:
        if directory.is_dir() and directory.name != "__pycache__" and not (directory / "SKILL.md").is_file():
            errors.append(f"skill has no entrypoint: {directory.name}")
    if not skill_names:
        errors.append("skill catalog is empty")
    try:
        from .runtime import validate_configuration
        configuration = validate_configuration(root)
        errors.extend(configuration.get("errors", []))
    except ImportError:
        errors.append("runtime configuration verifier unavailable")
    except (OSError, ValueError, TypeError) as exc:
        errors.append(f"runtime configuration unreadable: {type(exc).__name__}")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "skills": skill_names,
            "limits": "Static scans check structure and explicit source markers, not semantic English, authorship, relevance, truth, or live model quality."}


def package_source(root: Path, output: Path) -> dict:
    root, output = _safe_root(root), output.absolute()
    if output.resolve().is_relative_to(root) or output.exists() or output.is_symlink():
        raise ValueError("package output must be new and outside the source root")
    if any(parent.is_symlink() or (callable(getattr(parent, "is_junction", None)) and parent.is_junction()) for parent in output.parents):
        raise ValueError("package parent redirects through a link")
    before = inventory(root)
    recorded = json.loads((root / "SOURCE_MANIFEST.json").read_text(encoding="utf-8"))
    if before != recorded:
        raise ValueError("source inventory is stale; regenerate and verify first")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".nexus-package-", suffix=".tmp", dir=output.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            with zipfile.ZipFile(handle, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
                for path in source_files(root):
                    relative = path.relative_to(root).as_posix()
                    info = zipfile.ZipInfo(relative, date_time=(2026, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o100644 << 16
                    info.create_system = 3
                    archive.writestr(info, path.read_bytes())
        if inventory(root) != before:
            raise ValueError("source changed while packaging; package is not validated")
        with zipfile.ZipFile(temporary) as archive:
            members = archive.namelist()
            expected = {path.relative_to(root).as_posix(): sha256(path) for path in source_files(root)}
            if len(members) != len(set(members)) or set(members) != set(expected):
                raise ValueError("package member inventory mismatch")
            for name, digest in expected.items():
                if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                    raise ValueError(f"package hash mismatch: {name}")
        result = {"ok": True, "file_count": len(expected), "sha256": sha256(temporary), "bytes": temporary.stat().st_size}
        # Publish complete validated bytes, refusing a destination created meanwhile.
        os.link(temporary, output)
        return result
    finally:
        temporary.unlink(missing_ok=True)


def verify(root: Path, *, runtime: bool = False, codex: str | None = None) -> dict:
    root = _safe_root(root)
    checks = []
    try:
        static = static_checks(root)
        checks.append({"name": "source-and-skills", **static})
        current = inventory(root)
        recorded = json.loads((root / "SOURCE_MANIFEST.json").read_text(encoding="utf-8"))
        checks.append({"name": "source-inventory", "ok": current == recorded, "file_count": current["file_count"]})
    except (OSError, ValueError, RuntimeError) as exc:
        checks.append({"name": "source-integrity", "ok": False, "error": str(exc)})
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    try:
        completed = subprocess.run(
            [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"],
            cwd=root, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=120, check=False,
        )
        report = completed.stdout + completed.stderr
        count = re.search(r"Ran (\d+) tests?", report)
        checks.append({"name": "unit-and-regression", "ok": completed.returncode == 0 and bool(count) and int(count[1]) > 0,
                       "test_count": int(count[1]) if count else 0, "output": report[-24000:]})
    except (OSError, subprocess.TimeoutExpired) as exc:
        checks.append({"name": "unit-and-regression", "ok": False, "error": type(exc).__name__})
    try:
        with tempfile.TemporaryDirectory(prefix="nexus-package-") as temporary:
            package_base = Path(temporary).resolve()
            first = package_source(root, package_base / "a.zip")
            second = package_source(root, package_base / "b.zip")
            checks.append({"name": "reproducible-package", **first, "ok": first["sha256"] == second["sha256"]})
    except (OSError, ValueError, RuntimeError) as exc:
        checks.append({"name": "reproducible-package", "ok": False, "error": str(exc)})
    if runtime:
        from .runtime import inspect_runtime
        try:
            checks.append({"name": "selected-runtime", **inspect_runtime(root, codex=codex)})
        except (OSError, ValueError, RuntimeError) as exc:
            checks.append({"name": "selected-runtime", "ok": False, "error": type(exc).__name__})
    return {"ok": all(check["ok"] for check in checks), "checks": checks,
            "live_model_call": False, "runtime_requested": runtime}
