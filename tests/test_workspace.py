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
            self.assertEqual((root / "remove.txt").read_text(encoding="utf-8"), "remove")

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
