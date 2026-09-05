from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from unittest import mock
from pathlib import Path

from nexus import verify, workspace


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


class WorkspaceSnapshotTests(unittest.TestCase):
    def test_snapshot_is_deterministic_and_excludes_generated_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root = base / "source"
            root.mkdir()
            (root / "z.txt").write_text("z", encoding="utf-8")
            (root / "a.txt").write_text("a", encoding="utf-8")
            for name in (".git", "artifacts", "__pycache__", ".pytest_cache"):
                ignored = root / name
                ignored.mkdir()
                (ignored / "generated.txt").write_text("ignore", encoding="utf-8")
            manifest = workspace.snapshot(root, base / "snapshot.json")
            self.assertEqual(
                [item["path"] for item in manifest["source_files"]], ["a.txt", "z.txt"]
            )
            stored = json.loads((base / "snapshot.json").read_text(encoding="utf-8"))
            self.assertEqual(stored, manifest)
            self.assertEqual(workspace.source_files(root), [root / "a.txt", root / "z.txt"])

    def test_pytest_cache_is_excluded_from_inventory_and_package(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root = base / "source"
            root.mkdir()
            source = root / "actual.py"
            source.write_text("print('source')\n", encoding="utf-8")
            cache = root / ".pytest_cache" / "v" / "cache"
            cache.mkdir(parents=True)
            (cache / "lastfailed").write_text("generated", encoding="utf-8")

            manifest = verify.update_inventory(root)
            self.assertEqual(manifest["file_count"], 1)
            self.assertEqual(manifest["files"][0]["path"], "actual.py")

            package = base / "source.zip"
            result = verify.package_source(root, package)
            self.assertTrue(result["ok"])
            with zipfile.ZipFile(package) as archive:
                names = set(archive.namelist())
            self.assertIn("actual.py", names)
            self.assertNotIn(".pytest_cache/v/cache/lastfailed", names)

    def test_snapshot_rejects_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root = base / "source"
            outside = base / "outside"
            root.mkdir()
            outside.mkdir()
            (outside / "secret.txt").write_text("secret", encoding="utf-8")
            _make_directory_redirect(root / "escape", outside)
            with self.assertRaises(workspace.PathSafetyError):
                workspace.source_files(root)

    def test_snapshot_destination_must_be_outside_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "source"
            root.mkdir()
            with self.assertRaises(workspace.PathSafetyError):
                workspace.snapshot(root, root / "snapshot.json")

    def test_snapshot_does_not_replace_an_existing_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root = base / "source"
            root.mkdir()
            destination = base / "snapshot.json"
            destination.write_text("owner record", encoding="utf-8")
            with self.assertRaises(workspace.WorkspaceError):
                workspace.snapshot(root, destination)
            self.assertEqual(destination.read_text(encoding="utf-8"), "owner record")


class WorkspaceQuarantineTests(unittest.TestCase):
    def _fixture(self, base: Path, files: dict[str, bytes], paths: list[str] | None = None):
        root, backup = base / "source", base / "backup"
        root.mkdir()
        for relative, content in files.items():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        plan = workspace.plan_quarantine(root, list(files) if paths is None else paths, backup)
        return root, backup, plan

    def _with_backup_root(self, plan, backup: Path):
        from hashlib import sha256

        changed = dict(plan, backup_root=str(backup))
        unsigned = {key: changed[key] for key in ("schema", "root", "backup_root", "targets", "files")}
        changed["plan_sha256"] = sha256(
            json.dumps(unsigned, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return changed

    def test_stale_hash_fails_before_any_move(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root = base / "source"
            root.mkdir()
            (root / "first.txt").write_text("first", encoding="utf-8")
            (root / "second.txt").write_text("second", encoding="utf-8")
            plan = workspace.plan_quarantine(
                root, ["first.txt", "second.txt"], base / "backup"
            )
            (root / "second.txt").write_text("changed", encoding="utf-8")
            with self.assertRaises(workspace.StalePlanError):
                workspace.apply_quarantine(plan)
            self.assertTrue((root / "first.txt").exists())
            self.assertTrue((root / "second.txt").exists())
            self.assertFalse((base / "backup").exists())

    def test_successful_quarantine_is_reversible_by_receipt_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root = base / "source"
            root.mkdir()
            (root / "remove.txt").write_text("remove", encoding="utf-8")
            plan = workspace.plan_quarantine(root, ["remove.txt"], base / "backup")
            receipt = workspace.apply_quarantine(plan)
            self.assertEqual(receipt["status"], "applied")
            self.assertEqual(receipt["moved"], [{"source": "remove.txt", "backup": "remove.txt"}])
            self.assertFalse((root / "remove.txt").exists())
            self.assertEqual((base / "backup" / "remove.txt").read_text(encoding="utf-8"), "remove")
            self.assertNotIn(str(root), json.dumps(receipt))
            restored = workspace.restore_quarantine(plan, receipt)
            self.assertEqual(restored["status"], "restored")
            self.assertTrue(restored["backup_retained"])
            self.assertEqual((root / "remove.txt").read_text(encoding="utf-8"), "remove")
            self.assertEqual((base / "backup" / "remove.txt").read_text(encoding="utf-8"), "remove")
            self.assertEqual(list((base / "backup").glob(".capture-*")), [])

    def test_directory_roundtrip_preserves_binary_files_and_empty_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, backup, _plan = self._fixture(base, {"custom/data.bin": b"\x00\xff\r\n"})
            (root / "custom" / "empty").mkdir()
            plan = workspace.plan_quarantine(root, ["custom"], backup)
            receipt = workspace.apply_quarantine(plan)
            self.assertFalse((root / "custom").exists())
            self.assertEqual(list(backup.glob(".capture-*")), [])
            restored = workspace.restore_quarantine(plan, receipt)
            self.assertTrue(restored["backup_retained"])
            for boundary in (root, backup):
                self.assertEqual((boundary / "custom" / "data.bin").read_bytes(), b"\x00\xff\r\n")
                self.assertTrue((boundary / "custom" / "empty").is_dir())

    def test_backup_destination_must_stay_outside_source_even_with_ancestor_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, _backup, _plan = self._fixture(base, {"source/owner.txt": b"owner"})
            with self.assertRaises(workspace.PathSafetyError):
                workspace.plan_quarantine(root, ["source/owner.txt"], base)
            self.assertEqual((root / "source" / "owner.txt").read_bytes(), b"owner")

    def test_loaded_apply_and_restore_recheck_actual_backup_destinations(self) -> None:
        for operation in ("apply", "restore"):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary).resolve()
                root, backup, plan = self._fixture(base, {"source/owner.txt": b"owner"})
                if operation == "restore":
                    receipt = workspace.apply_quarantine(plan)
                    (backup / "source" / "owner.txt").replace(root / "owner.txt")
                changed = self._with_backup_root(plan, base)
                with self.assertRaises(workspace.PathSafetyError):
                    if operation == "apply":
                        workspace.apply_quarantine(changed)
                    else:
                        workspace.restore_quarantine(
                            changed, dict(receipt, plan_sha256=changed["plan_sha256"])
                        )
                kept = root / ("source/owner.txt" if operation == "apply" else "owner.txt")
                self.assertEqual(kept.read_bytes(), b"owner")

    def test_ancestor_backup_root_is_valid_for_an_external_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, _backup, _plan = self._fixture(base, {"owner.txt": b"owner"})
            plan = workspace.plan_quarantine(root, ["owner.txt"], base)
            receipt = workspace.apply_quarantine(plan)
            self.assertEqual((base / "owner.txt").read_bytes(), b"owner")
            self.assertFalse((root / "owner.txt").exists())
            workspace.restore_quarantine(plan, receipt)
            self.assertEqual((root / "owner.txt").read_bytes(), b"owner")

    def test_file_added_during_capture_is_rejected_and_all_owner_bytes_return(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, backup, plan = self._fixture(base, {"custom/planned.txt": b"planned"}, ["custom"])
            original_move = workspace.shutil.move

            def add_then_move(source, destination):
                (Path(source) / "new-owner.txt").write_bytes(b"new owner bytes")
                return original_move(source, destination)

            with mock.patch.object(workspace.shutil, "move", side_effect=add_then_move):
                with self.assertRaisesRegex(workspace.QuarantineError, "all moved paths were restored"):
                    workspace.apply_quarantine(plan)
            self.assertEqual((root / "custom" / "planned.txt").read_bytes(), b"planned")
            self.assertEqual((root / "custom" / "new-owner.txt").read_bytes(), b"new owner bytes")
            self.assertFalse((backup / "custom").exists())

    def test_partial_capture_failure_is_reported_without_removing_either_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, backup, plan = self._fixture(base, {"owner.txt": b"original"})

            def partial_move(_source, destination):
                Path(destination).write_bytes(b"partial")
                raise OSError("injected cross-volume copy failure")

            with mock.patch.object(workspace.shutil, "move", side_effect=partial_move):
                with self.assertRaisesRegex(workspace.QuarantineError, "rollback was incomplete"):
                    workspace.apply_quarantine(plan)
            self.assertEqual((root / "owner.txt").read_bytes(), b"original")
            captures = list(backup.glob(".capture-*/owner.txt"))
            self.assertEqual(len(captures), 1)
            self.assertEqual(captures[0].read_bytes(), b"partial")

    def test_rollback_conflict_preserves_owner_edit_and_reports_retained_original(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, backup, plan = self._fixture(base, {"a.txt": b"original a", "b.txt": b"original b"})
            original_move = workspace.shutil.move
            calls = 0

            def move_then_fail(source, destination):
                nonlocal calls
                calls += 1
                if calls == 1:
                    result = original_move(source, destination)
                    Path(source).write_bytes(b"new owner save")
                    return result
                raise OSError("injected second capture failure")

            with mock.patch.object(workspace.shutil, "move", side_effect=move_then_fail):
                with self.assertRaisesRegex(workspace.QuarantineError, "rollback was incomplete") as caught:
                    workspace.apply_quarantine(plan)
            self.assertEqual(calls, 2)
            self.assertEqual((root / "a.txt").read_bytes(), b"new owner save")
            self.assertEqual((root / "b.txt").read_bytes(), b"original b")
            self.assertEqual((backup / "a.txt").read_bytes(), b"original a")
            capture = next(backup.glob(".capture-*/a.txt"))
            self.assertEqual(capture.read_bytes(), b"original a")
            self.assertIn(str(capture.parent), str(caught.exception))

    def test_concurrent_final_backup_file_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, backup, plan = self._fixture(base, {"owner.txt": b"original"})
            original_open = workspace.os.open
            collisions = 0

            def create_owner_first(path, flags, *args, **kwargs):
                nonlocal collisions
                if Path(path) == backup / "owner.txt":
                    collisions += 1
                    Path(path).write_bytes(b"concurrent backup owner")
                return original_open(path, flags, *args, **kwargs)

            with mock.patch.object(workspace.os, "open", side_effect=create_owner_first):
                with self.assertRaises(workspace.QuarantineError):
                    workspace.apply_quarantine(plan)
            self.assertEqual(collisions, 1)
            self.assertEqual((backup / "owner.txt").read_bytes(), b"concurrent backup owner")
            self.assertEqual((root / "owner.txt").read_bytes(), b"original")

    def test_concurrent_final_backup_directory_is_never_used_as_a_container(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, backup, plan = self._fixture(base, {"custom/planned.txt": b"original"}, ["custom"])
            original_mkdir = workspace.os.mkdir
            collisions = 0

            def create_owner_first(path, *args, **kwargs):
                nonlocal collisions
                if Path(path) == backup / "custom":
                    collisions += 1
                    original_mkdir(path, *args, **kwargs)
                    (Path(path) / "owner.txt").write_bytes(b"concurrent owner")
                return original_mkdir(path, *args, **kwargs)

            with mock.patch.object(workspace.os, "mkdir", side_effect=create_owner_first):
                with self.assertRaises(workspace.QuarantineError):
                    workspace.apply_quarantine(plan)
            self.assertEqual(collisions, 1)
            self.assertEqual(list((backup / "custom").iterdir()), [backup / "custom" / "owner.txt"])
            self.assertEqual((backup / "custom" / "owner.txt").read_bytes(), b"concurrent owner")
            self.assertEqual((root / "custom" / "planned.txt").read_bytes(), b"original")

    def test_concurrent_restore_file_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, backup, plan = self._fixture(base, {"owner.txt": b"original"})
            receipt = workspace.apply_quarantine(plan)
            original_open = workspace.os.open
            collisions = 0

            def create_owner_first(path, flags, *args, **kwargs):
                nonlocal collisions
                if Path(path) == root / "owner.txt":
                    collisions += 1
                    Path(path).write_bytes(b"concurrent source owner")
                return original_open(path, flags, *args, **kwargs)

            with mock.patch.object(workspace.os, "open", side_effect=create_owner_first):
                with self.assertRaisesRegex(workspace.QuarantineError, "incomplete"):
                    workspace.restore_quarantine(plan, receipt)
            self.assertEqual(collisions, 1)
            self.assertEqual((root / "owner.txt").read_bytes(), b"concurrent source owner")
            self.assertEqual((backup / "owner.txt").read_bytes(), b"original")

    def test_concurrent_restore_directory_is_never_used_as_a_container(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, backup, plan = self._fixture(base, {"custom/planned.txt": b"original"}, ["custom"])
            receipt = workspace.apply_quarantine(plan)
            original_mkdir = workspace.os.mkdir
            collisions = 0

            def create_owner_first(path, *args, **kwargs):
                nonlocal collisions
                if Path(path) == root / "custom":
                    collisions += 1
                    original_mkdir(path, *args, **kwargs)
                    (Path(path) / "owner.txt").write_bytes(b"concurrent owner")
                return original_mkdir(path, *args, **kwargs)

            with mock.patch.object(workspace.os, "mkdir", side_effect=create_owner_first):
                with self.assertRaisesRegex(workspace.QuarantineError, "incomplete"):
                    workspace.restore_quarantine(plan, receipt)
            self.assertEqual(collisions, 1)
            self.assertEqual(list((root / "custom").iterdir()), [root / "custom" / "owner.txt"])
            self.assertEqual((root / "custom" / "owner.txt").read_bytes(), b"concurrent owner")
            self.assertEqual((backup / "custom" / "planned.txt").read_bytes(), b"original")

    def test_concurrent_rollback_file_preserves_both_owner_and_original(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, backup, plan = self._fixture(base, {"a.txt": b"original a", "b.txt": b"original b"})
            original_move, original_open = workspace.shutil.move, workspace.os.open
            moves = 0
            collisions = 0

            def fail_second_move(source, destination):
                nonlocal moves
                moves += 1
                if moves == 2:
                    raise OSError("injected second capture failure")
                return original_move(source, destination)

            def create_owner_first(path, flags, *args, **kwargs):
                nonlocal collisions
                if Path(path) == root / "a.txt":
                    collisions += 1
                    Path(path).write_bytes(b"concurrent source owner")
                return original_open(path, flags, *args, **kwargs)

            with mock.patch.object(workspace.shutil, "move", side_effect=fail_second_move), mock.patch.object(
                workspace.os, "open", side_effect=create_owner_first
            ):
                with self.assertRaisesRegex(workspace.QuarantineError, "rollback was incomplete"):
                    workspace.apply_quarantine(plan)
            self.assertEqual((moves, collisions), (2, 1))
            self.assertEqual((root / "a.txt").read_bytes(), b"concurrent source owner")
            self.assertEqual((root / "b.txt").read_bytes(), b"original b")
            self.assertEqual((backup / "a.txt").read_bytes(), b"original a")

    def test_restore_detects_changed_output_and_retains_original_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, backup, plan = self._fixture(base, {"owner.txt": b"original"})
            receipt = workspace.apply_quarantine(plan)
            original_copystat = workspace.shutil.copystat

            def change_after_copy(source, destination, **kwargs):
                original_copystat(source, destination, **kwargs)
                if Path(destination) == root / "owner.txt":
                    Path(destination).write_bytes(b"owner edit during restore")

            with mock.patch.object(workspace.shutil, "copystat", side_effect=change_after_copy):
                with self.assertRaisesRegex(workspace.QuarantineError, "incomplete"):
                    workspace.restore_quarantine(plan, receipt)
            self.assertEqual((root / "owner.txt").read_bytes(), b"owner edit during restore")
            self.assertEqual((backup / "owner.txt").read_bytes(), b"original")

    def test_partial_restore_copy_is_identified_and_backup_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, backup, plan = self._fixture(base, {"owner.txt": b"original"})
            receipt = workspace.apply_quarantine(plan)

            def partial_copy(_reader, writer, *args, **kwargs):
                writer.write(b"partial")
                raise OSError("injected copy failure")

            with mock.patch.object(workspace.shutil, "copyfileobj", side_effect=partial_copy):
                with self.assertRaisesRegex(workspace.QuarantineError, "source paths retained.*owner.txt"):
                    workspace.restore_quarantine(plan, receipt)
            self.assertEqual((root / "owner.txt").read_bytes(), b"partial")
            self.assertEqual((backup / "owner.txt").read_bytes(), b"original")

    def test_restore_copy_rejects_a_redirect_added_after_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, backup, plan = self._fixture(base, {"custom/planned.txt": b"original"}, ["custom"])
            receipt = workspace.apply_quarantine(plan)
            outside = base / "outside"
            outside.mkdir()
            (outside / "private.txt").write_bytes(b"outside bytes")
            original_copytree = workspace.shutil.copytree

            def inject_redirect(source, destination, *args, **kwargs):
                _make_directory_redirect(Path(source) / "redirect", outside)
                return original_copytree(source, destination, *args, **kwargs)

            with mock.patch.object(workspace.shutil, "copytree", side_effect=inject_redirect):
                with self.assertRaisesRegex(workspace.QuarantineError, "redirect"):
                    workspace.restore_quarantine(plan, receipt)
            self.assertFalse((root / "custom").exists())
            self.assertEqual((outside / "private.txt").read_bytes(), b"outside bytes")
            self.assertEqual((backup / "custom" / "planned.txt").read_bytes(), b"original")

    def test_capture_cleanup_preserves_changed_files_and_unexpected_empty_directories(self) -> None:
        for addition in ("changed-file", "empty-directory"):
            with self.subTest(addition=addition), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary).resolve()
                root, backup, plan = self._fixture(base, {"owner.txt": b"original"})
                original_cleanup = workspace._cleanup_quarantine_capture

                def change_capture(source_root, capture_root, expected, directories, **kwargs):
                    if addition == "changed-file":
                        (capture_root / "owner.txt").write_bytes(b"new capture owner bytes")
                    else:
                        (capture_root / "owner-empty-directory").mkdir()
                    return original_cleanup(source_root, capture_root, expected, directories, **kwargs)

                with mock.patch.object(workspace, "_cleanup_quarantine_capture", side_effect=change_capture):
                    receipt = workspace.apply_quarantine(plan)
                self.assertEqual(receipt["status"], "applied")
                self.assertTrue(receipt["warnings"])
                capture = backup / receipt["capture_retained"]
                if addition == "changed-file":
                    self.assertEqual((capture / "owner.txt").read_bytes(), b"new capture owner bytes")
                else:
                    self.assertTrue((capture / "owner-empty-directory").is_dir())
                self.assertFalse((root / "owner.txt").exists())
                self.assertEqual((backup / "owner.txt").read_bytes(), b"original")

    def test_backup_edit_at_capture_cleanup_cannot_discard_the_original_or_report_applied(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, backup, plan = self._fixture(base, {"owner.txt": b"original"})
            original_cleanup = workspace._cleanup_quarantine_capture

            def edit_backup_before_cleanup(*args, **kwargs):
                (backup / "owner.txt").write_bytes(b"new backup owner bytes")
                return original_cleanup(*args, **kwargs)

            with mock.patch.object(workspace, "_cleanup_quarantine_capture", side_effect=edit_backup_before_cleanup):
                with self.assertRaises(workspace.QuarantineError) as caught:
                    workspace.apply_quarantine(plan)
            self.assertEqual((backup / "owner.txt").read_bytes(), b"new backup owner bytes")
            self.assertEqual((root / "owner.txt").read_bytes(), b"original")
            capture = next(backup.glob(".capture-*/owner.txt"))
            self.assertEqual(capture.read_bytes(), b"original")
            self.assertIn(str(capture.parent), str(caught.exception))

    def test_later_backup_edit_reports_an_absent_capture_honestly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, backup, plan = self._fixture(base, {"owner.txt": b"original"})
            original_cleanup = workspace._cleanup_quarantine_capture

            def edit_backup_after_cleanup(*args, **kwargs):
                result = original_cleanup(*args, **kwargs)
                self.assertIsNone(result)
                (backup / "owner.txt").write_bytes(b"later backup owner bytes")
                return result

            with mock.patch.object(workspace, "_cleanup_quarantine_capture", side_effect=edit_backup_after_cleanup):
                with self.assertRaisesRegex(workspace.QuarantineError, "capture directory is absent"):
                    workspace.apply_quarantine(plan)
            self.assertEqual((backup / "owner.txt").read_bytes(), b"later backup owner bytes")
            self.assertFalse((root / "owner.txt").exists())
            self.assertEqual(list(backup.glob(".capture-*")), [])

    def test_quarantine_rejects_symlink_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root = base / "source"
            outside = base / "outside"
            root.mkdir()
            outside.mkdir()
            (outside / "secret.txt").write_text("secret", encoding="utf-8")
            _make_directory_redirect(root / "alias", outside)
            with self.assertRaises(workspace.PathSafetyError):
                workspace.plan_quarantine(root, ["alias"], base / "backup")

    def test_restore_rejects_a_redirecting_parent_before_moving(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root = base / "source"
            outside = base / "outside"
            root.mkdir()
            outside.mkdir()
            parent = root / "foo"
            parent.mkdir()
            (parent / "bar.txt").write_text("bar", encoding="utf-8")
            plan = workspace.plan_quarantine(root, ["foo/bar.txt"], base / "backup")
            receipt = workspace.apply_quarantine(plan)
            parent.rmdir()
            _make_directory_redirect(parent, outside)
            with self.assertRaises(workspace.PathSafetyError):
                workspace.restore_quarantine(plan, receipt)
            self.assertFalse((outside / "bar.txt").exists())
            self.assertEqual((base / "backup" / "foo" / "bar.txt").read_text(encoding="utf-8"), "bar")

    def test_quarantine_rollback_rejects_a_redirecting_parent_before_moving(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root = base / "source"
            outside = base / "outside"
            root.mkdir()
            outside.mkdir()
            first_parent = root / "first"
            second_parent = root / "second"
            first_parent.mkdir()
            second_parent.mkdir()
            (first_parent / "one.txt").write_text("one", encoding="utf-8")
            (second_parent / "two.txt").write_text("two", encoding="utf-8")
            plan = workspace.plan_quarantine(
                root,
                ["first/one.txt", "second/two.txt"],
                base / "backup",
            )
            original_move = workspace.shutil.move
            calls = 0

            def move_with_redirect(source: str, destination: str) -> str:
                nonlocal calls
                calls += 1
                if calls == 1:
                    result = original_move(source, destination)
                    first_parent.rmdir()
                    _make_directory_redirect(first_parent, outside)
                    return result
                raise OSError("forced quarantine failure")

            with mock.patch.object(workspace.shutil, "move", side_effect=move_with_redirect):
                with self.assertRaises(workspace.QuarantineError):
                    workspace.apply_quarantine(plan)
            self.assertFalse((outside / "one.txt").exists())
            self.assertEqual((base / "backup" / "first" / "one.txt").read_text(encoding="utf-8"), "one")

    def test_atomic_json_writer_is_utf8_and_stable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "nested" / "record.json"
            unicode_value = "nh\u00e2n"
            workspace.write_json(path, {"b": 2, "a": unicode_value})
            expected = '{\n  "a": "nh' + chr(0x00E2) + 'n",\n  "b": 2\n}\n'
            self.assertEqual(
                path.read_text(encoding="utf-8"),
                expected,
            )


if __name__ == "__main__":
    unittest.main()
