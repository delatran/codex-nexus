"""Install Codex Nexus for Codex App and Codex CLI.

The default operation applies a hash-bound plan. ``--dry-run`` is the
explicit read-only mode. Link installation and global configuration updates
are staged independently; a configuration change is restored if a later link
operation fails.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from nexus import config_install, install


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect or install Codex Nexus for Codex App and Codex CLI."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--health",
        action="store_true",
        help="read link and global configuration health only",
    )
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="show the read-only link and configuration plan",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="allow replacement only when --snapshot and --backup-root are supplied",
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        help="existing JSON snapshot required for custom-path replacement",
    )
    parser.add_argument(
        "--backup-root",
        type=Path,
        help="external backup directory required for custom-path replacement",
    )
    parser.add_argument(
        "--codex",
        help="explicit compatible Codex executable for the native configuration writer",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="repository root fixture (default: this directory)",
    )
    parser.add_argument(
        "--home",
        type=Path,
        default=Path.home(),
        help="home fixture containing the managed links and .codex/config.toml",
    )
    return parser


def _dump(payload: object) -> None:
    sys.stdout.write(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    )


def _plan(args: argparse.Namespace) -> dict[str, Any]:
    links = install.plan_install(
        args.root,
        args.home,
        replace=args.replace,
        snapshot=args.snapshot,
        backup_root=args.backup_root,
    )
    configuration = config_install.plan_config(args.root, args.home, codex=args.codex)
    return {
        "schema": "codex-nexus/setup-plan/v1",
        "root": str(Path(args.root).expanduser().absolute()),
        "home": str(Path(args.home).expanduser().absolute()),
        "links": links,
        "configuration": configuration,
    }


def main(argv: list[str] | None = None) -> int:
    argument_parser = _parser()
    args = argument_parser.parse_args(argv)
    if args.health and any((args.replace, args.snapshot, args.backup_root, args.codex)):
        argument_parser.error("--health accepts only --root and --home; apply options are not used")
    if not args.replace and any((args.snapshot, args.backup_root)):
        argument_parser.error("--snapshot and --backup-root require --replace")
    try:
        if args.health:
            result = install.health(args.root, args.home, include_configuration=True)
            _dump(result)
            return 0 if result["ok"] else 1
        plan = _plan(args)
        if args.dry_run:
            _dump(plan)
            return 0
        applied_config = config_install.apply_config(
            plan["configuration"],
            args.root,
            args.home,
            codex=args.codex,
        )
        try:
            link_receipt = install.apply_install(plan["links"])
        except Exception as original:
            try:
                config_install.rollback_config(applied_config)
            except Exception as rollback_error:
                raise config_install.ConfigInstallError(
                    "setup failed and configuration rollback was incomplete"
                ) from rollback_error
            if applied_config.receipt.get("warnings"):
                raise config_install.ConfigInstallError(
                    "setup failed; configuration staging cleanup was incomplete"
                ) from original
            raise original
        try:
            final_health = install.health(args.root, args.home, include_configuration=True)
            if not final_health["ok"]:
                raise install.InstallError("setup completed without a healthy installation")
        except Exception as final_error:
            rollback_failures: list[str] = []
            try:
                install.rollback_install(plan["links"], link_receipt)
            except Exception:
                rollback_failures.append("links rollback was incomplete")
            try:
                config_install.rollback_config(applied_config)
            except Exception:
                rollback_failures.append("configuration rollback was incomplete")
            if applied_config.receipt.get("warnings"):
                rollback_failures.append("configuration staging cleanup was incomplete")
            if rollback_failures:
                raise install.InstallError(
                    "setup completed without a healthy installation; "
                    + "; ".join(rollback_failures)
                ) from final_error
            raise install.InstallError(
                "setup completed without a healthy installation; changes rolled back"
            ) from final_error
        _dump(
            {
                "schema": "codex-nexus/setup-receipt/v1",
                "status": "applied",
                "links": link_receipt,
                "configuration": applied_config.receipt,
                "health": final_health,
            }
        )
        return 0
    except (
        config_install.ConfigInstallError,
        install.InstallError,
        OSError,
        ValueError,
        TypeError,
    ) as exc:
        _dump({"status": "error", "error": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
