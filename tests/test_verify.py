"""Regression checks for integrity and packaging failure boundaries."""

import contextlib
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from nexus.verify import inventory, package_source, skill_metadata, static_checks, update_inventory, verify
from nexus.workspace import WorkspaceError, source_files


class VerifyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "repo"
        self.root.mkdir()
        (self.root / "README.md").write_text("English source\n", encoding="utf-8")

    def test_inventory_binds_content(self):
        before = inventory(self.root)
        (self.root / "README.md").write_text("Changed source\n", encoding="utf-8")
        self.assertNotEqual(before, inventory(self.root))

    def test_personal_config_copy_cannot_enter_distributable_codex_source(self):
        (self.root / ".codex").mkdir()
        (self.root / ".codex/config.txt").write_text("personal setting copy", encoding="utf-8")
        report = static_checks(self.root)
        self.assertIn("unowned Codex source surface: .codex/config.txt", report["errors"])

    def test_relative_roots_match_absolute_public_source_operations(self):
        with contextlib.chdir(self.base):
            relative = Path("repo")
            self.assertEqual(inventory(relative), inventory(self.root))
            self.assertEqual(static_checks(relative), static_checks(self.root))
            self.assertEqual(update_inventory(relative), inventory(self.root))
        self.assertTrue((self.root / "SOURCE_MANIFEST.json").is_file())

    def test_public_verification_rejects_redirect_before_executing_tests(self):
        redirect = self.base / "redirect"
        try:
            os.symlink(self.root, redirect, target_is_directory=True)
        except OSError as exc:
            if os.name != "nt":
                self.skipTest(f"directory redirects unavailable: {exc}")
            subprocess.run(
                [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", "mklink", "/J", str(redirect), str(self.root)],
                check=True, capture_output=True,
            )
        with patch("nexus.verify.subprocess.run") as command:
            with self.assertRaises(WorkspaceError):
                verify(redirect)
            command.assert_not_called()

    def test_package_roundtrip_and_reproducibility(self):
        update_inventory(self.root)
        a = package_source(self.root, self.base / "a.zip")
        b = package_source(self.root, self.base / "b.zip")
        self.assertEqual(a["sha256"], b["sha256"])
        with zipfile.ZipFile(self.base / "a.zip") as archive:
            self.assertEqual(archive.read("README.md"), (self.root / "README.md").read_bytes())
            self.assertEqual(set(archive.namelist()), {"README.md", "SOURCE_MANIFEST.json"})

    def test_stale_manifest_rejected_before_package_creation(self):
        update_inventory(self.root)
        (self.root / "README.md").write_text("Changed", encoding="utf-8")
        target = self.base / "a.zip"
        with self.assertRaisesRegex(ValueError, "stale"):
            package_source(self.root, target)
        self.assertFalse(target.exists())

    def test_post_write_failure_never_publishes_archive(self):
        update_inventory(self.root)
        before = inventory(self.root)
        target = self.base / "invalid.zip"
        with patch("nexus.verify.inventory", side_effect=[before, {}]):
            with self.assertRaisesRegex(ValueError, "source changed"):
                package_source(self.root, target)
        self.assertFalse(target.exists())
        self.assertEqual(list(self.base.glob(".nexus-package-*")), [])

    def test_concurrent_destination_is_preserved(self):
        update_inventory(self.root)
        target = self.base / "raced.zip"
        def concurrent_file(source, destination):
            Path(destination).write_bytes(b"owner archive")
            raise FileExistsError(destination)
        with patch("nexus.verify.os.link", side_effect=concurrent_file):
            with self.assertRaises(FileExistsError):
                package_source(self.root, target)
        self.assertEqual(target.read_bytes(), b"owner archive")
        self.assertEqual(list(self.base.glob(".nexus-package-*")), [])

    def test_package_inside_source_and_existing_target_rejected(self):
        update_inventory(self.root)
        with self.assertRaises(ValueError):
            package_source(self.root, self.root / "archive.zip")
        target = self.base / "existing.zip"
        target.write_bytes(b"owner content")
        with self.assertRaises(ValueError):
            package_source(self.root, target)
        self.assertEqual(target.read_bytes(), b"owner content")

    def test_generated_manifest_excluded_from_source_identity(self):
        before = inventory(self.root)
        (self.root / "SOURCE_MANIFEST.json").write_text("{}", encoding="utf-8")
        self.assertEqual(before, inventory(self.root))

    def test_metadata_duplicate_or_block_value_rejected(self):
        for header in ("name: one\nname: two\ndescription: valid description",
                       "name: one\ndescription: |\n  nested", "name: one"):
            with self.assertRaises(ValueError):
                skill_metadata("---\n" + header + "\n---\nBody")

    def test_metadata_plain_and_quoted(self):
        result = skill_metadata('---\nname: evidence-research\ndescription: "Research with source records."\n---\nBody')
        self.assertEqual(result["name"], "evidence-research")

    def test_model_comparison_prose_does_not_change_runtime_selection(self):
        source_root = Path(__file__).resolve().parents[1]
        for source in source_files(source_root):
            target = self.root / source.relative_to(source_root)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        readme = self.root / "README.md"
        readme.write_text(
            readme.read_text(encoding="utf-8") + "\nComparison baseline: GPT-" + str(4) + ".\n",
            encoding="utf-8",
        )
        comparison = static_checks(self.root)
        self.assertTrue(comparison["ok"], comparison["errors"])

        configuration = self.root / ".codex" / "config.toml"
        configuration.write_text(
            configuration.read_text(encoding="utf-8").replace(
                'model = "gpt-6-astra"', 'model = "unsupported-baseline"', 1
            ),
            encoding="utf-8",
        )
        changed_selection = static_checks(self.root)
        self.assertFalse(changed_selection["ok"])
        self.assertTrue(any(error.get("check") == "config-model" for error in changed_selection["errors"] if isinstance(error, dict)))

    def test_scan_rejects_non_english_and_secret_markers(self):
        examples = (("\u0111\u1ed5i", "non-English alphabet"),
                    ("Source " + chr(0x2014) + " style", "em dash violates"),
                    ("sk-" + "A" * 26, "possible credential"))
        for text, marker in examples:
            with self.subTest(marker=marker):
                (self.root / "README.md").write_text(text, encoding="utf-8")
                result = static_checks(self.root)
                self.assertTrue(any(marker in str(error) for error in result["errors"]))

    def test_empty_unowned_tree_is_not_hidden_by_inventory(self):
        (self.root / "unused-runner").mkdir()
        result = static_checks(self.root)
        self.assertIn("unowned root entry: unused-runner", result["errors"])

    def test_private_cache_never_silently_passes_source_scan(self):
        (self.root / "__pycache__").mkdir()
        result = static_checks(self.root)
        self.assertTrue(any("cache in source tree" in str(error) for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()
