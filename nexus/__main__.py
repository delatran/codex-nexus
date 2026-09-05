"""One small command surface for source, runtime, and evidence verification."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    # Installed skills may call this file from an unrelated project directory.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "nexus"

from .workspace import _safe_root, write_json


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Verify the Codex-native Codex Nexus workspace.")
    commands = result.add_subparsers(dest="command", required=True)
    for name in ("verify", "runtime", "inventory", "package", "evidence", "checkpoint"):
        child = commands.add_parser(name)
        child.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
        if name == "verify":
            child.add_argument("--runtime", action="store_true", help="include local Codex probes, without a model request")
        if name in {"verify", "runtime"}:
            child.add_argument("--codex", help="explicit Codex executable; invalid selection fails without fallback")
        if name == "inventory":
            child.add_argument("--write", action="store_true", help="regenerate SOURCE_MANIFEST.json")
        if name in {"evidence", "checkpoint"}:
            child.add_argument("packet", type=Path)
        if name != "inventory":
            child.add_argument("--output", type=Path, required=name == "package")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        root = _safe_root(args.root)
        if args.command == "verify":
            from .verify import verify
            report = verify(root, runtime=args.runtime, codex=args.codex)
        elif args.command == "runtime":
            from .runtime import inspect_runtime
            report = inspect_runtime(root, codex=args.codex)
        elif args.command == "inventory":
            from .verify import inventory, update_inventory
            data = update_inventory(root) if args.write else inventory(root)
            report = {"ok": True, "written": args.write, "file_count": data["file_count"], "source_bytes": data["source_bytes"]}
        elif args.command == "package":
            from .verify import package_source
            report = package_source(root, args.output)
        else:
            data = json.loads(args.packet.read_text(encoding="utf-8"))
            if args.command == "evidence":
                from .evidence import validate_packet
                report = validate_packet(data, root)
            else:
                from .checkpoint import validate_checkpoint
                report = validate_checkpoint(data, root)
        output = getattr(args, "output", None)
        if output is not None and args.command != "package":
            resolved_output = output.resolve()
            # Receipts may go to artifacts or an external path, never over source or input.
            if resolved_output.is_relative_to(root) and not resolved_output.is_relative_to(root / "artifacts"):
                raise ValueError("receipt output inside the source root must be under artifacts/")
            packet = getattr(args, "packet", None)
            if packet is not None and packet.resolve() == resolved_output:
                raise ValueError("receipt must not overwrite its input packet")
            if resolved_output.exists():
                raise ValueError("receipt output already exists; select a new path")
            write_json(output, report, overwrite=False)
        sys.stdout.write(json.dumps(report, indent=2, ensure_ascii=True) + "\n")
        return 0 if report.get("ok") else 1
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        sys.stdout.write(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=True) + "\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
