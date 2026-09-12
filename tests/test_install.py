from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import importlib.util
from unittest import mock
from pathlib import Path

from nexus import config_install, install


class _PrivilegeDenied(OSError):
    winerror = 1314


def _make_directory_redirect(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError) as exc:
        if os.name != "nt":
            raise unittest.SkipTest(f"directory redirects unavailable: {exc}")
    subprocess.run(
        [
            os.environ.get("COMSPEC", "cmd.exe"),
            "/d",
            "/c",
            "mklink",
            "/J",
            str(link),
            str(target),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def _populate_fixture(root: Path, home: Path) -> tuple[Path, Path]:
    root = root.resolve()
    home = home.resolve()
    (root / "skills").mkdir(parents=True)
    (root / "skills" / "example.md").write_text("example", encoding="utf-8")
    (root / "AGENTS.md").write_text("English instructions", encoding="utf-8")
    return root, home


def _fixture(base: Path) -> tuple[Path, Path]:
    base = base.resolve()
    root, home = _populate_fixture(base / "project", base / "home")
    source_config = Path(__file__).resolve().parents[1] / ".codex" / "config.toml"
    config_dir = root / ".codex"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(source_config.read_text(encoding="utf-8"), encoding="utf-8")
    return root, home


def _redigest_install_plan(plan: dict[str, object]) -> dict[str, object]:
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
    plan["plan_sha256"] = hashlib.sha256(
        json.dumps(unsigned, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return plan


def _seed_owned_hardlink(root: Path, home: Path) -> Path:
    source = root / "AGENTS.md"
    installed = home / ".codex" / "AGENTS.md"
    installed.parent.mkdir(parents=True, exist_ok=True)
    os.link(source, installed)
    receipt = install._ownership_path(home)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_bytes(
        install._ownership_bytes(install._ownership_record(root, home, installed))
    )
    return installed


def _replace_source_atomically(source: Path, content: str) -> None:
    staged = source.with_name(f".{source.name}.replacement")
    staged.write_text(content, encoding="utf-8")
    os.replace(staged, source)


class InstallHealthTests(unittest.TestCase):
    def test_setup_modes_are_mutually_exclusive(self) -> None:
        setup_path = Path(__file__).resolve().parents[1] / "setup.py"
        spec = importlib.util.spec_from_file_location("staged_setup", setup_path)
        self.assertIsNotNone(spec)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with self.assertRaises(SystemExit) as raised:
            module._parser().parse_args(["--dry-run", "--health"])
        self.assertEqual(raised.exception.code, 2)

    def test_setup_apply_full_fixture_reaches_healthy_state(self) -> None:
        setup_path = Path(__file__).resolve().parents[1] / "setup.py"
        spec = importlib.util.spec_from_file_location("staged_setup_apply", setup_path)
        self.assertIsNotNone(spec)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            root, home = _fixture(Path(temporary))
            def fake_native(_executable, _home, stage, _edits):
                stage.write_text((root / ".codex" / "config.toml").read_text(encoding="utf-8"), encoding="utf-8")

            with mock.patch.object(config_install, "_native_batch_write", side_effect=fake_native):
                result = module.main(
                    [
                        "--root",
                        str(root),
                        "--home",
                        str(home),
                        "--codex",
                        str(Path(sys.executable).resolve()),
                    ]
                )
            self.assertEqual(result, 0)
            self.assertTrue(install.health(root, home)["ok"])

    def test_health_is_read_only_and_reports_missing_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, home = _fixture(Path(temporary))
            result = install.health(root, home)
            self.assertFalse(result["ok"])
            self.assertEqual(result["summary"]["missing"], 2)
            self.assertFalse(home.exists())

    def test_source_root_below_a_junction_is_rejected_by_all_install_entry_points(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            physical = base / "physical"
            actual_root, home = _populate_fixture(physical / "project", base / "home")
            redirect = base / "redirect"
            _make_directory_redirect(redirect, physical)
            redirected_root = redirect / "project"

            with self.assertRaisesRegex(install.InstallError, "link or junction"):
                install.health(redirected_root, home)
            with self.assertRaisesRegex(install.InstallError, "link or junction"):
                install.plan_install(redirected_root, home)

            plan = install.plan_install(actual_root, home)
            plan["root"] = str(redirected_root)
            _redigest_install_plan(plan)
            with self.assertRaisesRegex(install.InstallError, "link or junction"):
                install.apply_install(plan)
            self.assertFalse(home.exists())

    def test_dry_run_has_no_side_effects_and_preserves_custom_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, home = _fixture(Path(temporary))
            custom = home / ".codex" / "AGENTS.md"
            custom.parent.mkdir(parents=True)
            custom.write_text("owner content", encoding="utf-8")
            plan = install.plan_install(root, home)
            actions = {item["name"]: item["action"] for item in plan["operations"]}
            self.assertEqual(actions["skills"], "create_link")
            self.assertEqual(actions["global_instructions"], "conflict")
            self.assertEqual(custom.read_text(encoding="utf-8"), "owner content")

    def test_custom_replacement_requires_a_hash_bound_snapshot_and_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, home = _fixture(Path(temporary))
            custom = home / ".codex" / "AGENTS.md"
            custom.parent.mkdir(parents=True)
            custom.write_text("owner content", encoding="utf-8")
            with self.assertRaises(install.InstallError):
                install.plan_install(root, home, replace=True)
            self.assertEqual(custom.read_text(encoding="utf-8"), "owner content")

    def test_apply_rejects_a_quarantine_plan_for_an_unrelated_root_before_moving(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, home = _fixture(base)
            evil = base / "evil"
            evil.mkdir()
            danger = evil / "danger.txt"
            danger.write_text("must remain", encoding="utf-8")
            plan = install.plan_install(root, home)
            plan["quarantine_plan"] = install.workspace.plan_quarantine(
                evil,
                ["danger.txt"],
                base / "evil-backup",
            )
            _redigest_install_plan(plan)
            with self.assertRaisesRegex(install.InstallError, "replacement quarantine plan"):
                install.apply_install(plan)
            self.assertEqual(danger.read_text(encoding="utf-8"), "must remain")
            self.assertFalse((base / "evil-backup").exists())
            self.assertFalse(home.exists())

    def test_apply_rejects_quarantine_targets_that_do_not_match_replacements(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, home = _fixture(base)
            agents = home / ".codex" / "AGENTS.md"
            agents.parent.mkdir(parents=True)
            agents.write_text("owner instructions", encoding="utf-8")
            skills = home / ".agents" / "skills"
            skills.mkdir(parents=True)
            (skills / "owner.md").write_text("owner skills", encoding="utf-8")
            snapshot = install.workspace.snapshot(home, base / "home-snapshot.json")
            plan = install.plan_install(
                root,
                home,
                replace=True,
                snapshot=snapshot,
                backup_root=base / "backup",
            )
            plan["quarantine_plan"] = install.workspace.plan_quarantine(
                home,
                [".codex/AGENTS.md"],
                base / "mismatched-backup",
            )
            _redigest_install_plan(plan)
            with self.assertRaisesRegex(install.InstallError, "do not match"):
                install.apply_install(plan)
            self.assertEqual(agents.read_text(encoding="utf-8"), "owner instructions")
            self.assertEqual((skills / "owner.md").read_text(encoding="utf-8"), "owner skills")
            self.assertFalse((base / "mismatched-backup").exists())

    def test_apply_translates_a_malformed_nested_quarantine_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, home = _fixture(base)
            custom = home / ".codex" / "AGENTS.md"
            custom.parent.mkdir(parents=True)
            custom.write_text("owner instructions", encoding="utf-8")
            snapshot = install.workspace.snapshot(home, base / "home-snapshot.json")
            plan = install.plan_install(
                root,
                home,
                replace=True,
                snapshot=snapshot,
                backup_root=base / "backup",
            )
            malformed = dict(plan["quarantine_plan"])
            malformed.pop("backup_root")
            plan["quarantine_plan"] = malformed
            _redigest_install_plan(plan)
            with self.assertRaisesRegex(install.InstallError, "preflight failed"):
                install.apply_install(plan)
            self.assertEqual(custom.read_text(encoding="utf-8"), "owner instructions")
            self.assertFalse((base / "backup").exists())

    def test_home_below_a_junction_is_rejected_before_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, _home = _fixture(base)
            outside = base / "outside"
            outside.mkdir()
            redirect = base / "redirect"
            _make_directory_redirect(redirect, outside)
            with self.assertRaisesRegex(install.InstallError, "link or junction"):
                install.health(root, redirect / "nested")

    def test_target_parent_junction_is_rejected_before_health_reads_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, home = _fixture(base)
            outside = base / "outside"
            outside.mkdir()
            agents_parent = home / ".agents"
            home.mkdir()
            _make_directory_redirect(agents_parent, outside)
            with self.assertRaisesRegex(install.InstallError, "link or junction"):
                install.health(root, home)

    def test_apply_creates_all_managed_links_and_health_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, home = _fixture(Path(temporary))
            plan = install.plan_install(root, home)
            try:
                receipt = install.apply_install(plan)
            except install.InstallError as exc:
                if "cannot create" in str(exc) or "junction" in str(exc):
                    self.skipTest(f"link creation unavailable: {exc}")
                raise
            self.assertEqual(receipt["status"], "applied")
            result = install.health(root, home)
            self.assertTrue(result["ok"])
            self.assertEqual(result["summary"], {"correct": 2, "missing": 0, "conflict": 0})

    def test_successful_link_receipt_can_be_compensated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, home = _fixture(Path(temporary))
            plan = install.plan_install(root, home)
            try:
                receipt = install.apply_install(plan)
            except install.InstallError as exc:
                if "cannot create" in str(exc) or "junction" in str(exc):
                    self.skipTest(f"link creation unavailable: {exc}")
                raise
            rollback = install.rollback_install(plan, receipt)
            self.assertEqual(rollback["status"], "rolled_back")
            self.assertFalse((home / ".agents" / "skills").exists())
            self.assertFalse((home / ".codex" / "AGENTS.md").exists())

    def test_file_hardlink_fallback_is_accepted_by_identity(self) -> None:
        if os.name != "nt":
            self.skipTest("the file-link privilege fallback is Windows-specific")
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, home = _fixture(base)
            source = root / "AGENTS.md"
            installed = home / ".codex" / "AGENTS.md"
            with mock.patch.object(install.os, "symlink", side_effect=_PrivilegeDenied("denied")):
                install._create_managed_link(source, installed, "file")
            self.assertTrue(os.path.samefile(source, installed))
            result = install.health(root, home)
            target = next(item for item in result["targets"] if item["name"] == "global_instructions")
            self.assertEqual(target["status"], "correct")
            self.assertEqual(target["observed_kind"], "managed_hardlink")
            installed.unlink()

    def test_owned_hardlink_refreshes_after_atomic_source_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, home = _fixture(base)
            installed = _seed_owned_hardlink(root, home)
            old_content = installed.read_text(encoding="utf-8")
            _replace_source_atomically(root / "AGENTS.md", "updated instructions")

            plan = install.plan_install(root, home)
            actions = {item["name"]: item["action"] for item in plan["operations"]}
            self.assertEqual(actions["global_instructions"], "refresh_owned")
            try:
                receipt = install.apply_install(plan)
            except install.InstallError as exc:
                if "cannot create" in str(exc) or "junction" in str(exc):
                    self.skipTest(f"link creation unavailable: {exc}")
                raise

            self.assertEqual(installed.read_text(encoding="utf-8"), "updated instructions")
            self.assertTrue(os.path.samefile(root / "AGENTS.md", installed))
            ownership = receipt["ownership"]
            self.assertIsInstance(ownership, dict)
            stale_backup = home / Path(ownership["stale_backup"])
            self.assertEqual(stale_backup.read_text(encoding="utf-8"), old_content)
            self.assertTrue(install.health(root, home)["ok"])

            rollback = install.rollback_install(plan, receipt)
            self.assertEqual(rollback["status"], "rolled_back")
            self.assertEqual(installed.read_text(encoding="utf-8"), old_content)
            self.assertFalse(os.path.samefile(root / "AGENTS.md", installed))
            restored = install._read_ownership(root, home)
            self.assertTrue(restored.target_matches)
            self.assertEqual(restored.record["target_sha256"], install.workspace.sha256(installed))

    def test_existing_hardlink_without_receipt_is_recorded_without_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, home = _fixture(base)
            installed = home / ".codex" / "AGENTS.md"
            installed.parent.mkdir(parents=True, exist_ok=True)
            os.link(root / "AGENTS.md", installed)

            plan = install.plan_install(root, home)
            operation = next(
                item for item in plan["operations"] if item["name"] == "global_instructions"
            )
            self.assertEqual(operation["action"], "keep")
            try:
                receipt = install.apply_install(plan)
            except install.InstallError as exc:
                if "cannot create" in str(exc) or "junction" in str(exc):
                    self.skipTest(f"link creation unavailable: {exc}")
                raise
            ownership = receipt["ownership"]
            self.assertIsInstance(ownership, dict)
            self.assertTrue(install._read_ownership(root, home).target_matches)
            self.assertTrue(os.path.samefile(root / "AGENTS.md", installed))

    def test_rollback_rejects_backup_path_traversal_before_mutation(self) -> None:
        for escape_home in (True, False):
            with self.subTest(escape_home=escape_home), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary).resolve()
                root, home = _fixture(base)
                installed = _seed_owned_hardlink(root, home)
                _replace_source_atomically(root / "AGENTS.md", "updated instructions")
                plan = install.plan_install(root, home)
                receipt = install.apply_install(plan)
                ownership = receipt["ownership"]
                before_target = installed.read_bytes()
                before_receipt = install._ownership_path(home).read_bytes()
                backup = home / ownership["stale_backup"]
                before_backup = backup.read_bytes()
                outside = base / "outside.txt"
                outside.write_bytes(b"owner file")
                ownership["stale_backup"] = (
                    "nested/../../outside.txt" if escape_home else
                    ownership["backup_dir"] + "/../other/AGENTS.md"
                )
                with self.assertRaisesRegex(
                    install.InstallError, "leaves the selected home|outside the operation directory"
                ):
                    install.rollback_install(plan, receipt)
                self.assertEqual(installed.read_bytes(), before_target)
                self.assertEqual(install._ownership_path(home).read_bytes(), before_receipt)
                self.assertEqual(backup.read_bytes(), before_backup)
                self.assertEqual(outside.read_bytes(), b"owner file")

    def test_modified_owned_hardlink_remains_a_custom_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, home = _fixture(base)
            installed = _seed_owned_hardlink(root, home)
            _replace_source_atomically(root / "AGENTS.md", "updated instructions")
            installed.unlink()
            installed.write_text("owner changed instructions", encoding="utf-8")

            plan = install.plan_install(root, home)
            operation = next(
                item for item in plan["operations"] if item["name"] == "global_instructions"
            )
            self.assertEqual(operation["action"], "conflict")
            with self.assertRaisesRegex(install.InstallError, "custom installation conflict"):
                install.apply_install(plan)
            self.assertEqual(installed.read_text(encoding="utf-8"), "owner changed instructions")

    def test_malformed_ownership_receipt_is_rejected_before_planning(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, home = _fixture(Path(temporary))
            receipt = install._ownership_path(home)
            receipt.parent.mkdir(parents=True, exist_ok=True)
            receipt.write_bytes(b"{")
            with self.assertRaisesRegex(install.InstallError, "ownership receipt is malformed"):
                install.plan_install(root, home)

    def test_redirected_ownership_receipt_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, home = _fixture(base)
            receipt = install._ownership_path(home)
            receipt.parent.mkdir(parents=True, exist_ok=True)
            outside = base / "outside-receipt.json"
            outside.write_text("{}", encoding="utf-8")
            try:
                receipt.symlink_to(outside)
            except (OSError, NotImplementedError) as exc:
                redirected_parent = receipt.parent
                try:
                    redirected_parent.rmdir()
                except OSError as cleanup_error:
                    self.skipTest(f"file redirect parent cleanup unavailable: {cleanup_error}")
                outside_parent = base / "outside-receipt-dir"
                outside_parent.mkdir()
                try:
                    _make_directory_redirect(redirected_parent, outside_parent)
                except (OSError, subprocess.CalledProcessError) as junction_error:
                    self.skipTest(f"file redirects unavailable: {exc}; junction: {junction_error}")
            with self.assertRaisesRegex(install.InstallError, "ownership receipt is a redirect|link or junction"):
                install.plan_install(root, home)

    def test_refresh_failure_restores_stale_target_and_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, home = _fixture(base)
            installed = _seed_owned_hardlink(root, home)
            original_receipt = install._ownership_path(home).read_bytes()
            _replace_source_atomically(root / "AGENTS.md", "updated instructions")

            skills_target = home / ".agents" / "skills"
            try:
                install._create_managed_link(root / "skills", skills_target, "directory")
            except install.InstallError as exc:
                if "cannot create" in str(exc) or "junction" in str(exc):
                    self.skipTest(f"directory link unavailable: {exc}")
                raise
            plan = install.plan_install(root, home)
            with mock.patch.object(
                install,
                "_write_ownership",
                side_effect=install.InstallError("forced ownership write failure"),
            ):
                with self.assertRaisesRegex(install.InstallError, "rolled back"):
                    install.apply_install(plan)
            self.assertEqual(installed.read_text(encoding="utf-8"), "English instructions")
            self.assertEqual(install._ownership_path(home).read_bytes(), original_receipt)
            self.assertFalse(os.path.samefile(root / "AGENTS.md", installed))
            self.assertTrue(os.path.samefile(root / "skills", skills_target))

    def test_post_move_backup_validation_failure_restores_stale_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, home = _fixture(base)
            installed = _seed_owned_hardlink(root, home)
            original_receipt = install._ownership_path(home).read_bytes()
            _replace_source_atomically(root / "AGENTS.md", "updated instructions")
            skills_target = home / ".agents" / "skills"
            try:
                install._create_managed_link(root / "skills", skills_target, "directory")
            except install.InstallError as exc:
                if "cannot create" in str(exc) or "junction" in str(exc):
                    self.skipTest(f"directory link unavailable: {exc}")
                raise
            plan = install.plan_install(root, home)
            with mock.patch.object(
                install,
                "_verify_stale_backup",
                side_effect=install.InstallError("forced backup verification failure"),
            ):
                with self.assertRaisesRegex(install.InstallError, "rolled back"):
                    install.apply_install(plan)
            self.assertEqual(installed.read_text(encoding="utf-8"), "English instructions")
            self.assertEqual(install._ownership_path(home).read_bytes(), original_receipt)
            self.assertTrue(os.path.samefile(root / "skills", skills_target))

    def test_refresh_rollback_rejects_backup_tampering_after_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            source = base / "source.txt"
            installed = base / "installed.txt"
            backup = base / "backup.txt"
            source.write_text("new source", encoding="utf-8")
            os.link(source, installed)
            backup.write_text("old source", encoding="utf-8")
            refreshed = install._RefreshedTarget(
                installed=installed,
                source=source,
                backup=backup,
                expected_sha256=install.workspace.sha256(backup),
                expected_identity=tuple(install._file_identity(backup)),
            )
            original_replace = install.os.replace

            def tamper_before_replace(source_path: Path, target_path: Path) -> None:
                if source_path == backup:
                    backup.write_text("tampered backup", encoding="utf-8")
                original_replace(source_path, target_path)

            with mock.patch.object(install.os, "replace", side_effect=tamper_before_replace):
                failures = install._restore_refreshed_targets([refreshed])
            self.assertTrue(failures)
            self.assertEqual(installed.read_text(encoding="utf-8"), "tampered backup")
            self.assertFalse(backup.exists())

    def test_rollback_restores_crlf_receipt_bytes_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, home = _fixture(base)
            installed = _seed_owned_hardlink(root, home)
            receipt_path = install._ownership_path(home)
            original_receipt = receipt_path.read_bytes().replace(b"\n", b"\r\n")
            receipt_path.write_bytes(original_receipt)
            _replace_source_atomically(root / "AGENTS.md", "updated instructions")
            skills_target = home / ".agents" / "skills"
            try:
                install._create_managed_link(root / "skills", skills_target, "directory")
            except install.InstallError as exc:
                if "cannot create" in str(exc) or "junction" in str(exc):
                    self.skipTest(f"directory link unavailable: {exc}")
                raise
            plan = install.plan_install(root, home)
            receipt = install.apply_install(plan)
            install.rollback_install(plan, receipt)
            self.assertEqual(receipt_path.read_bytes(), original_receipt)
            self.assertEqual(installed.read_text(encoding="utf-8"), "English instructions")

    def test_refresh_rollback_preserves_a_new_owner_redirect(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            source = base / "source.txt"
            wrong = base / "wrong-target"
            installed = base / "installed-target"
            backup = base / "backup.txt"
            source.write_text("new source", encoding="utf-8")
            wrong.mkdir()
            backup.write_text("old source", encoding="utf-8")
            try:
                _make_directory_redirect(installed, wrong)
            except (OSError, subprocess.CalledProcessError) as exc:
                self.skipTest(f"directory redirects unavailable: {exc}")
            refreshed = install._RefreshedTarget(
                installed=installed,
                source=source,
                backup=backup,
                expected_sha256=install.workspace.sha256(backup),
                expected_identity=tuple(install._file_identity(backup)),
            )
            failures = install._restore_refreshed_targets([refreshed])
            self.assertTrue(failures)
            self.assertEqual(installed.resolve(), wrong.resolve())
            self.assertTrue(backup.exists())

    def test_final_health_failure_restores_refreshed_target_and_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, home = _fixture(base)
            installed = _seed_owned_hardlink(root, home)
            original_receipt = install._ownership_path(home).read_bytes()
            _replace_source_atomically(root / "AGENTS.md", "updated instructions")
            skills_target = home / ".agents" / "skills"
            try:
                install._create_managed_link(root / "skills", skills_target, "directory")
            except install.InstallError as exc:
                if "cannot create" in str(exc) or "junction" in str(exc):
                    self.skipTest(f"directory link unavailable: {exc}")
                raise
            plan = install.plan_install(root, home)
            original_health = install.health
            calls = 0

            def fail_final(
                health_root: Path | str,
                health_home: Path | str,
                *,
                include_configuration: bool = False,
            ) -> dict[str, object]:
                nonlocal calls
                calls += 1
                result = original_health(
                    health_root,
                    health_home,
                    include_configuration=include_configuration,
                )
                if calls == 2:
                    result["ok"] = False
                return result

            with mock.patch.object(install, "health", side_effect=fail_final):
                with self.assertRaisesRegex(install.InstallError, "post-install health failed"):
                    install.apply_install(plan)
            self.assertEqual(installed.read_text(encoding="utf-8"), "English instructions")
            self.assertEqual(install._ownership_path(home).read_bytes(), original_receipt)
            self.assertFalse(os.path.samefile(root / "AGENTS.md", installed))
            self.assertTrue(os.path.samefile(root / "skills", skills_target))

    def test_foreign_copy_with_equal_content_remains_a_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, home = _fixture(Path(temporary))
            target = home / ".codex" / "AGENTS.md"
            target.parent.mkdir(parents=True)
            target.write_text((root / "AGENTS.md").read_text(encoding="utf-8"), encoding="utf-8")
            result = install.health(root, home)
            observed = next(item for item in result["targets"] if item["name"] == "global_instructions")
            self.assertEqual(observed["status"], "conflict")
            self.assertEqual(observed["observed_kind"], "file")

    def test_rollback_preserves_a_replaced_custom_copy_after_identity_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            source = base / "source.txt"
            target = base / "target.txt"
            source.write_text("source", encoding="utf-8")
            os.link(source, target)
            target.unlink()
            target.write_text("custom replacement", encoding="utf-8")
            failures = install._remove_created_links([(target, source)])
            self.assertEqual(len(failures), 1)
            self.assertEqual(target.read_text(encoding="utf-8"), "custom replacement")

    def test_arbitrary_redirect_is_custom_conflict_and_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, home = _fixture(base)
            wrong = base / "wrong-skills"
            wrong.mkdir()
            target = home / ".agents" / "skills"
            target.parent.mkdir(parents=True)
            try:
                target.symlink_to(wrong, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                if os.name != "nt":
                    self.skipTest(f"symbolic links unavailable: {exc}")
                with mock.patch.object(install.os, "symlink", side_effect=OSError("force junction")):
                    try:
                        install._create_managed_link(wrong, target, "directory")
                    except install.InstallError as fallback_error:
                        self.skipTest(f"native junction fallback unavailable: {fallback_error}")
            plan = install.plan_install(root, home)
            target_plan = next(item for item in plan["operations"] if item["name"] == "skills")
            self.assertEqual(target_plan["action"], "conflict")
            with self.assertRaises(install.InstallError):
                install.apply_install(plan)
            self.assertEqual(target.resolve(), wrong.resolve())

    def test_install_rolls_back_links_and_custom_paths_on_link_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, home = _fixture(base)
            custom = home / ".codex" / "AGENTS.md"
            custom.parent.mkdir(parents=True)
            custom.write_text("owner content", encoding="utf-8")
            snapshot = install.workspace.snapshot(home, base / "home-snapshot.json")
            plan = install.plan_install(
                root,
                home,
                replace=True,
                snapshot=snapshot,
                backup_root=base / "backup",
            )
            created: list[Path] = []

            def fake_create(source: Path, installed: Path, expected_kind: str) -> None:
                if len(created) == 1:
                    raise install.InstallError("forced link failure")
                installed.parent.mkdir(parents=True, exist_ok=True)
                installed.write_text("temporary marker", encoding="utf-8")
                created.append(installed)

            def fake_is_link(path: Path) -> bool:
                return path in created

            def fake_remove(path: Path) -> None:
                path.unlink()

            with mock.patch.object(install, "_create_managed_link", side_effect=fake_create), mock.patch.object(
                install, "_is_managed_link", side_effect=fake_is_link
            ), mock.patch.object(install, "_remove_managed_link", side_effect=fake_remove):
                with self.assertRaisesRegex(install.InstallError, "rolled back"):
                    install.apply_install(plan)
            self.assertEqual(custom.read_text(encoding="utf-8"), "owner content")
            self.assertFalse((home / ".agents" / "skills").exists())

    def test_fallback_command_rejects_expansion_characters(self) -> None:
        with self.assertRaises(install.InstallError):
            install._safe_command_path(Path("C:/unsafe/%PATH%!value"))

    def test_fallback_postcheck_rejects_a_missing_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            source = base / "source"
            installed = base / "home" / "link"
            source.mkdir()
            with mock.patch.object(install.os, "symlink", side_effect=OSError("force fallback")), mock.patch.object(
                install.subprocess, "run", return_value=None
            ):
                with self.assertRaises(install.InstallError):
                    install._create_managed_link(source, installed, "directory")

    def test_source_link_is_rejected_instead_of_followed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, home = _fixture(base)
            outside = base / "outside-skills"
            outside.mkdir()
            source = root / "skills"
            for child in source.iterdir():
                child.unlink()
            source.rmdir()
            try:
                source.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                if os.name != "nt":
                    self.skipTest(f"symbolic links unavailable: {exc}")
                with mock.patch.object(install.os, "symlink", side_effect=OSError("force junction")):
                    try:
                        install._create_managed_link(outside, source, "directory")
                    except install.InstallError as fallback_error:
                        self.skipTest(f"native junction fallback unavailable: {fallback_error}")
            with self.assertRaises(install.InstallError):
                install.health(root, home)

    def test_windows_directory_fallback_resolves_target(self) -> None:
        if os.name != "nt":
            self.skipTest("native junction fallback is Windows-specific")
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            source = base / "source"
            installed = base / "home" / "link"
            source.mkdir()
            with mock.patch.object(install.os, "symlink", side_effect=OSError("force junction")):
                try:
                    install._create_managed_link(source, installed, "directory")
                except install.InstallError as exc:
                    self.skipTest(f"native junction fallback unavailable: {exc}")
            self.assertEqual(installed.resolve(), source.resolve())
            install._remove_managed_link(installed)


if __name__ == "__main__":
    unittest.main()
