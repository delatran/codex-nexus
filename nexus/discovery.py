"""Observe native instruction and skill discovery without retaining prompts."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from . import runtime, workspace
from .verify import skill_metadata


SCHEMA_VERSION = "codex-nexus/native-discovery/v1"
_ROOT_LINE = re.compile(r"^- `(r\d+)` = `(.+)`$")
_SKILL_LINE = re.compile(r"^- ([a-z][a-z0-9-]*): (.*) \(file: (.+)\)$")
_INSTRUCTION_BLOCK = re.compile(
    r"\A# AGENTS\.md instructions for ([^\n]+)\n\s*<INSTRUCTIONS>\n(.*)\n</INSTRUCTIONS>\s*\Z",
    re.DOTALL,
)


def _report() -> dict[str, Any]:
    return {
        "schema": SCHEMA_VERSION,
        "ok": False,
        "observation_scope": "native_prompt_discovery",
        "effective_session_verified": False,
        "skill_bodies_loaded": False,
        "prompt_retained": False,
        "model_call": False,
        "local_cli_calls": [],
        "observed": {},
        "errors": [],
        "warnings": [],
    }


def _error(report: dict[str, Any], check: str, detail: str) -> dict[str, Any]:
    report["errors"].append({"check": check, "detail": detail})
    return report


def _sources(root: Path) -> dict[Path, tuple[bytes, dict[str, str] | None]]:
    paths = [root / "AGENTS.md"]
    workspace._reject_link_components(root / "skills")
    paths.extend(sorted((root / "skills").glob("*/SKILL.md")))
    if len(paths) == 1:
        raise ValueError("source skill catalog is empty")
    result = {}
    for path in paths:
        workspace._reject_link_components(path)
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        if not text.strip():
            raise ValueError("source text is empty")
        metadata = skill_metadata(text) if path.name == "SKILL.md" else None
        if metadata is not None and (
            re.fullmatch(r"[a-z][a-z0-9-]*", metadata["name"]) is None
            or metadata["name"] != path.parent.name
        ):
            raise ValueError("source skill identity is invalid")
        result[path] = (raw, metadata)
    return result


def _prompt_texts(payload: Any) -> tuple[list[str], list[str]]:
    if not isinstance(payload, list) or not payload:
        raise ValueError("prompt must be a nonempty message list")
    developer, user = [], []
    for item in payload:
        if not isinstance(item, Mapping) or item.get("type") != "message":
            raise ValueError("prompt entry must be a message")
        role = item.get("role")
        if not isinstance(role, str) or role not in {"system", "developer", "user", "assistant"}:
            raise ValueError("prompt role is unsupported")
        content = item.get("content")
        if not isinstance(content, list) or not content:
            raise ValueError("prompt message content must be nonempty")
        for part in content:
            if (
                not isinstance(part, Mapping)
                or part.get("type") != "input_text"
                or not isinstance(part.get("text"), str)
            ):
                raise ValueError("prompt content must be text")
            if role == "developer":
                developer.append(part["text"])
            elif role == "user":
                user.append(part["text"])
    if not developer or not user:
        raise ValueError("prompt must contain developer and user context")
    return developer, user


def _catalog(texts: list[str]) -> list[tuple[str, str, Path]]:
    blocks = []
    for text in texts:
        block = re.fullmatch(
            r"<skills_instructions>\n(.*?)\n</skills_instructions>", _normalized(text), re.DOTALL
        )
        if block:
            blocks.append(block[1])
    if len(blocks) != 1:
        raise ValueError("native skill catalog block is absent or ambiguous")
    roots: dict[str, Path] = {}
    records: list[tuple[str, str, str]] = []
    section = None
    fence: tuple[str, int] | None = None
    for line in blocks[0].splitlines():
        if fence is not None:
            stripped = line.strip()
            if stripped and set(stripped) == {fence[0]} and len(stripped) >= fence[1]:
                fence = None
            continue
        fence_match = re.match(r"^\s*(`{3,}|~{3,})", line)
        if fence_match:
            marker = fence_match[1]
            fence = (marker[0], len(marker))
            continue
        if line.startswith("#"):
            section = line
        root_match = _ROOT_LINE.fullmatch(line) if section == "### Skill roots" else None
        if root_match:
            alias, value = root_match.groups()
            path = Path(value)
            if not path.is_absolute() or (alias in roots and roots[alias] != path):
                raise ValueError("skill root alias is invalid or ambiguous")
            roots[alias] = path
        skill_match = _SKILL_LINE.fullmatch(line) if section == "### Available skills" else None
        if skill_match:
            records.append(skill_match.groups())
    resolved = []
    for name, description, value in records:
        alias, separator, remainder = value.partition("/")
        path = roots[alias] / remainder if separator and alias in roots else Path(value)
        if path.is_absolute():
            resolved.append((name, description, path.resolve()))
    return resolved


def _normalized(text: str) -> str:
    return text.replace("\r\n", "\n").strip()


def _contains_block(text: str, expected: str) -> bool:
    return f"\n{_normalized(expected)}\n" in f"\n{_normalized(text)}\n"


def _evaluate(
    root: Path,
    sources: dict[Path, tuple[bytes, dict[str, str] | None]],
    payload: Any,
    report: dict[str, Any],
) -> dict[str, Any]:
    try:
        developer, user = _prompt_texts(payload)
        catalog = _catalog(developer)
    except (OSError, ValueError, RuntimeError):
        return _error(report, "prompt-schema", "native prompt output has an unsupported or ambiguous structure")

    instruction_raw = sources[root / "AGENTS.md"][0]
    instruction_text = instruction_raw.decode("utf-8")
    visible = False
    for text in user:
        block = _INSTRUCTION_BLOCK.fullmatch(_normalized(text))
        if block:
            try:
                same_root = Path(block[1]).resolve() == root
            except (OSError, ValueError, RuntimeError):
                same_root = False
            if same_root and _contains_block(block[2], instruction_text):
                visible = True
    report["observed"]["instructions"] = {
        "source_sha256": hashlib.sha256(instruction_raw).hexdigest(),
        "source_text_visible": visible,
        "scope": "workspace_instruction_block",
        "global_origin_verified": False,
    }
    if not visible:
        _error(report, "source-instructions", "exact source instructions were not found in the workspace instruction block")

    skills = []
    for path, (raw, metadata) in sources.items():
        if metadata is None:
            continue
        matches = [entry for entry in catalog if entry[0] == metadata["name"] and entry[2] == path]
        description_status = "absent"
        if len(matches) == 1:
            rendered = matches[0][1]
            expected = metadata["description"]
            description_status = "exact" if rendered == expected else "shortened" if expected.startswith(rendered.rstrip(".\u2026")) else "different"
        skills.append({
            "name": metadata["name"],
            "source_sha256": hashlib.sha256(raw).hexdigest(),
            "cataloged_at_source": len(matches) == 1,
            "description_status": description_status,
        })
        if len(matches) != 1:
            _error(report, "source-skill", f"skill {metadata['name']} was absent or ambiguous at its exact source path")
    report["observed"]["skills"] = skills

    global_file: dict[str, Any] = {"status": "not_observed", "same_file_as_source": False}
    try:
        installed = runtime._home(None) / "AGENTS.md"
        installed_raw = installed.read_bytes()
        global_file.update({
            "status": "matches_source" if installed_raw == instruction_raw else "differs_from_source",
            "sha256": hashlib.sha256(installed_raw).hexdigest(),
            "same_file_as_source": installed.samefile(root / "AGENTS.md"),
        })
    except FileNotFoundError:
        global_file["status"] = "missing"
    except (OSError, ValueError):
        global_file["status"] = "unavailable"
    report["observed"]["global_instruction_file"] = global_file
    if global_file["status"] != "matches_source" or not global_file["same_file_as_source"]:
        report["warnings"].append({
            "check": "global-instruction-file",
            "detail": "workspace instruction visibility does not establish the managed global installation; inspect setup health separately",
        })
    try:
        unchanged = _sources(root) == sources
    except (OSError, UnicodeError, ValueError, RuntimeError):
        unchanged = False
    if not unchanged:
        _error(report, "source-freshness", "source instructions or skills changed during discovery")
    report["ok"] = not report["errors"]
    return report


def inspect_discovery(root: Path, codex: runtime.Runtime | Mapping[str, Any]) -> dict[str, Any]:
    """Use local prompt rendering; raw prompt data never enters the receipt."""

    report = _report()
    if isinstance(codex, Mapping):
        return _error(report, "native-runtime", "native discovery cannot be established by a capability fixture")
    if codex.command is None:
        return _error(report, "native-runtime", "selected native client is unavailable")
    try:
        root = workspace._safe_root(root)
        sources = _sources(root)
    except (OSError, UnicodeError, ValueError, RuntimeError):
        return _error(report, "discovery-source", "source instructions or skill catalog cannot be read safely")
    report["local_cli_calls"].append("debug prompt-input")
    try:
        result = runtime._run(codex.command, "debug", "prompt-input", cwd=root)
    except OSError:
        return _error(report, "discovery-command", "native prompt command could not be started")
    if result.timed_out:
        return _error(report, "discovery-timeout", "native prompt command timed out")
    if result.output_truncated:
        return _error(report, "discovery-output-limit", "native prompt output exceeded the bounded limit")
    if result.returncode != 0:
        return _error(report, "discovery-command", "native prompt command failed; its private output was omitted")
    try:
        payload = json.loads(result.stdout)
    except (ValueError, RecursionError):
        return _error(report, "prompt-json", "native prompt output is not valid JSON")
    return _evaluate(root, sources, payload, report)
