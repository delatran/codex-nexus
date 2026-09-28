from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from nexus import workspace
from nexus.checkpoint import CheckpointError, create_checkpoint, validate_checkpoint


class CheckpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "source"
        self.root.mkdir()
        (self.root / "changed.txt").write_text("before\n", encoding="utf-8")
        (self.root / "stable.txt").write_text("stable\n", encoding="utf-8")

    def _packet(self, **kwargs):
        options = {
            "done_condition": "all selected hashes and observed verifiers agree",
            "verifier_receipts": [{"name": "unit", "state": "observed", "receipt": "PASS"}],
        }
        options.update(kwargs)
        return create_checkpoint(
            self.root,
            ["changed.txt", "stable.txt"],
            "verify the source state",
            "inspect the current receipt",
            **options,
        )

    def _directory_link(self, link: Path, target: Path) -> None:
        if os.name == "nt":
            completed = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode != 0:
                self.skipTest(f"junction creation unavailable: {completed.stderr.strip()}")
        else:
            try:
                link.symlink_to(target, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"link creation unavailable: {exc}")

    def test_explicit_creation_and_validation_do_not_enumerate_workspace(self) -> None:
        with patch.object(workspace, "source_files", side_effect=AssertionError("unexpected tree scan")) as inventory:
            packet = self._packet()
            report = validate_checkpoint(packet, self.root)
        inventory.assert_not_called()
        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(report["checked_files"], 2)

    def test_complete_selection_uses_source_inventory(self) -> None:
        excluded = self.root / "artifacts"
        excluded.mkdir()
        (excluded / "receipt.txt").write_text("not source", encoding="utf-8")
        with patch.object(workspace, "source_files", wraps=workspace.source_files) as inventory:
            packet = create_checkpoint(self.root, None, "goal", "next", done_condition="done")
        inventory.assert_called_once_with(self.root)
        self.assertEqual([entry["path"] for entry in packet["source"]["files"]], ["changed.txt", "stable.txt"])

    def test_complete_inventory_must_also_use_safe_packet_paths(self) -> None:
        for relative in ("source.txt.", "source.txt ", "file:stream"):
            with self.subTest(path=relative):
                with patch.object(workspace, "source_files", return_value=[self.root / relative]):
                    with self.assertRaises(CheckpointError):
                        create_checkpoint(self.root, None, "goal", "next", done_condition="done")

    def test_exclusions_match_full_inventory_canonical_directory_names(self) -> None:
        directory = self.root / "Artifacts"
        directory.mkdir()
        (directory / "data.txt").write_text("source under differently cased name", encoding="utf-8")
        for selected in (None, ["Artifacts/data.txt"]):
            with self.subTest(selection=selected):
                packet = create_checkpoint(self.root, selected, "goal", "next", done_condition="done")
                self.assertIn("Artifacts/data.txt", [entry["path"] for entry in packet["source"]["files"]])
                report = validate_checkpoint(packet, self.root)
                self.assertTrue(report["ok"], report["errors"])

    def test_absolute_selected_paths_remain_relative_in_packet(self) -> None:
        packet = create_checkpoint(
            self.root, [self.root / "stable.txt"], "goal", "next", done_condition="done"
        )
        self.assertEqual([entry["path"] for entry in packet["source"]["files"]], ["stable.txt"])

    def test_unselected_changes_do_not_invalidate_declared_sources(self) -> None:
        packet = create_checkpoint(
            self.root, ["stable.txt"], "goal", "next", done_condition="done"
        )
        (self.root / "changed.txt").write_text("changed outside selection", encoding="utf-8")
        report = validate_checkpoint(packet, self.root)
        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(report["checked_files"], 1)
        self.assertFalse(report["completion_proven"])

    def test_recorded_verifier_does_not_prove_completion_or_resume(self) -> None:
        packet = self._packet()
        report = validate_checkpoint(packet, self.root)
        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(report["checked_files"], 2)
        self.assertEqual(report["declared_receipts"], 1)
        self.assertEqual(report["observed_receipts"], 1)
        self.assertTrue(report["recorded_checks_complete"])
        self.assertFalse(report["completion_proven"])
        self.assertTrue(report["requires_current_verifier_recheck"])
        self.assertTrue(report["requires_current_authority_recheck"])
        self.assertTrue(report["continuation_ready"])
        self.assertFalse(report["authority_granted"])
        self.assertFalse(report["resumed"])

    def test_changed_file_is_stale(self) -> None:
        packet = self._packet()
        (self.root / "changed.txt").write_text("after\n", encoding="utf-8")
        with patch.object(workspace, "source_files", side_effect=AssertionError("unexpected tree scan")):
            report = validate_checkpoint(packet, self.root)
        self.assertFalse(report["ok"])
        self.assertEqual(report["stale_files"], ["changed.txt"])
        self.assertIn("stale-source-file", {item["code"] for item in report["errors"]})

    def test_missing_selected_file_is_rejected_on_create_and_validate(self) -> None:
        packet = self._packet()
        (self.root / "changed.txt").unlink()
        with self.assertRaises(CheckpointError):
            self._packet()
        report = validate_checkpoint(packet, self.root)
        self.assertFalse(report["ok"])
        self.assertEqual(report["checked_files"], 1)
        self.assertIn("source-path-not-current", {item["code"] for item in report["errors"]})

    def test_excluded_directory_is_not_an_explicit_source(self) -> None:
        for relative in (".git/data.txt", "nested/artifacts/data.txt", "__pycache__/data.txt", ".pytest_cache/data.txt"):
            with self.subTest(path=relative):
                excluded = self.root / relative
                excluded.parent.mkdir(parents=True)
                excluded.write_text("excluded", encoding="utf-8")
                with self.assertRaisesRegex(CheckpointError, "excluded source directories"):
                    create_checkpoint(self.root, [relative], "goal", "next", done_condition="done")
                packet = self._packet()
                packet["source"]["files"][0]["path"] = relative
                report = validate_checkpoint(packet, self.root)
                self.assertFalse(report["ok"])
                self.assertIn("source-path-not-current", {item["code"] for item in report["errors"]})

    def test_selected_directory_is_not_a_regular_source_file(self) -> None:
        directory = self.root / "directory"
        directory.mkdir()
        with self.assertRaisesRegex(CheckpointError, "regular source file"):
            create_checkpoint(self.root, ["directory"], "goal", "next", done_condition="done")
        packet = self._packet()
        packet["source"]["files"][0]["path"] = "directory"
        report = validate_checkpoint(packet, self.root)
        self.assertFalse(report["ok"])
        self.assertIn("source-path-not-current", {item["code"] for item in report["errors"]})

    def test_scalar_placeholders_are_not_observed_receipts(self) -> None:
        for placeholder in (0, 1, False, True, None, 1.5):
            with self.subTest(placeholder=placeholder):
                packet = self._packet()
                packet["verifier_receipts"][0]["receipt"] = placeholder
                report = validate_checkpoint(packet, self.root)
                self.assertFalse(report["ok"])
                self.assertEqual(report["observed_receipts"], 0)
                self.assertFalse(report["recorded_checks_complete"])

    def test_structured_exit_status_is_a_recorded_receipt(self) -> None:
        packet = self._packet()
        packet["verifier_receipts"][0]["receipt"] = {"exit_code": 0, "command": "unit tests"}
        report = validate_checkpoint(packet, self.root)
        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(report["observed_receipts"], 1)
        self.assertFalse(report["completion_proven"])

    def test_path_escape_is_rejected_on_create_and_validate(self) -> None:
        outside = self.base / "outside.txt"
        outside.write_text("outside\n", encoding="utf-8")
        for relative in (
            "../outside.txt", "../source/stable.txt", "nested/../stable.txt",
            "/outside.txt", "C:/outside.txt", "C:stable.txt", "stable.txt:stream",
            "C:\\outside.txt", "\\\\server\\share\\file.txt", "stable\x00.txt",
        ):
            with self.subTest(path=relative):
                with self.assertRaises(CheckpointError):
                    create_checkpoint(self.root, [relative], "goal", "next", done_condition="done")
                packet = self._packet()
                packet["source"]["files"][0]["path"] = relative
                report = validate_checkpoint(packet, self.root)
                self.assertFalse(report["ok"])
                self.assertIn("path-escape", {item["code"] for item in report["errors"]})

    def test_trailing_dot_or_space_aliases_are_rejected(self) -> None:
        excluded = self.root / ".git"
        excluded.mkdir()
        (excluded / "config").write_text("excluded", encoding="utf-8")
        for relative in ("stable.txt.", "stable.txt ", ".git./config", ".git /config"):
            with self.subTest(path=relative):
                with self.assertRaises(CheckpointError):
                    create_checkpoint(self.root, [relative], "goal", "next", done_condition="done")
                packet = self._packet()
                packet["source"]["files"][0]["path"] = relative
                report = validate_checkpoint(packet, self.root)
                self.assertFalse(report["ok"])
                self.assertIn("path-escape", {item["code"] for item in report["errors"]})

    @unittest.skipUnless(os.name == "nt", "Windows case-insensitive filename aliases")
    def test_case_aliases_use_canonical_names_and_cannot_duplicate_sources(self) -> None:
        packet = create_checkpoint(
            self.root, ["STABLE.TXT"], "goal", "next", done_condition="done"
        )
        self.assertEqual(packet["source"]["files"][0]["path"], "stable.txt")
        with self.assertRaisesRegex(CheckpointError, "duplicate paths"):
            create_checkpoint(
                self.root, ["STABLE.TXT", "stable.txt"], "goal", "next", done_condition="done"
            )
        packet["source"]["files"][0]["path"] = "STABLE.TXT"
        report = validate_checkpoint(packet, self.root)
        self.assertFalse(report["ok"])
        self.assertIn("source-path-not-current", {item["code"] for item in report["errors"]})

    @unittest.skipUnless(os.name == "nt", "Windows case-insensitive directory aliases")
    def test_case_alias_cannot_select_excluded_source_directory(self) -> None:
        excluded = self.root / ".git"
        excluded.mkdir()
        (excluded / "config").write_text("excluded", encoding="utf-8")
        with self.assertRaisesRegex(CheckpointError, "excluded source directories"):
            create_checkpoint(self.root, [".GIT/config"], "goal", "next", done_condition="done")
        packet = self._packet()
        packet["source"]["files"][0]["path"] = ".GIT/config"
        report = validate_checkpoint(packet, self.root)
        self.assertFalse(report["ok"])
        self.assertIn("source-path-not-current", {item["code"] for item in report["errors"]})

    def test_unrelated_redirect_does_not_block_explicit_source_selection(self) -> None:
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret\n", encoding="utf-8")
        redirect = self.root / "redirect"
        self._directory_link(redirect, outside)
        packet = self._packet()
        report = validate_checkpoint(packet, self.root)
        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(report["checked_files"], 2)
        with self.assertRaises(CheckpointError):
            create_checkpoint(self.root, None, "goal", "next", done_condition="done")

    def test_selected_redirect_is_rejected_even_when_target_is_inside_root(self) -> None:
        for target in (self.base / "outside", self.root / "inside"):
            with self.subTest(target=target):
                target.mkdir()
                (target / "data.txt").write_text("data\n", encoding="utf-8")
                redirect = self.root / f"redirect-{target.name}"
                self._directory_link(redirect, target)
                relative = f"{redirect.name}/data.txt"
                with self.assertRaises(CheckpointError):
                    create_checkpoint(self.root, [relative], "goal", "next", done_condition="done")
                packet = self._packet()
                packet["source"]["files"][0]["path"] = relative
                report = validate_checkpoint(packet, self.root)
                self.assertFalse(report["ok"])
                self.assertIn("source-path-not-current", {item["code"] for item in report["errors"]})

    def test_redirecting_root_is_rejected(self) -> None:
        redirect = self.base / "redirect"
        self._directory_link(redirect, self.root)
        with self.assertRaises(CheckpointError):
            create_checkpoint(redirect, ["changed.txt"], "goal", "next", done_condition="done")
        report = validate_checkpoint(self._packet(), redirect)
        self.assertFalse(report["ok"])
        self.assertIn("unsafe-root", {item["code"] for item in report["errors"]})

    def test_checkpoint_rejects_root_below_redirecting_parent(self) -> None:
        physical = self.base / "physical"
        nested = physical / "nested"
        nested.mkdir(parents=True)
        (nested / "data.txt").write_text("fixture", encoding="utf-8")
        alias = self.base / "alias"
        if os.name == "nt":
            subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(alias), str(physical)],
                capture_output=True, check=True,
            )
        else:
            alias.symlink_to(physical, target_is_directory=True)
        with self.assertRaises(CheckpointError):
            create_checkpoint(alias / "nested", ["data.txt"], "goal", "next", done_condition="done")
        report = validate_checkpoint(self._packet(), alias / "nested")
        self.assertFalse(report["ok"])
        self.assertIn("unsafe-root", {item["code"] for item in report["errors"]})

    def test_duplicate_pending_id_across_tool_and_delegation_is_blocked(self) -> None:
        with self.assertRaisesRegex(CheckpointError, "duplicate pending ID"):
            self._packet(
                pending_tools=["work-1"],
                pending_delegations=["work-1"],
            )
        packet = self._packet()
        packet["pending_tools"] = [{"id": "same", "state": "completed", "generation": 0}]
        packet["pending_delegations"] = [{"id": "same", "state": "failed", "generation": 0}]
        report = validate_checkpoint(packet, self.root)
        self.assertFalse(report["ok"])
        self.assertIn("duplicate-pending-id", {item["code"] for item in report["errors"]})

    def test_late_result_generation_is_not_accepted(self) -> None:
        packet = self._packet(generation=4)
        packet["pending_tools"] = [
            {"id": "tool-1", "state": "completed", "generation": 3, "result_generation": 3}
        ]
        report = validate_checkpoint(packet, self.root)
        self.assertFalse(report["ok"])
        codes = {item["code"] for item in report["errors"]}
        self.assertIn("late-result-generation", codes)

    def test_skipped_verifier_is_not_a_pass(self) -> None:
        packet = self._packet(
            verifier_receipts=[{"name": "integration", "state": "skipped", "reason": "not run"}]
        )
        report = validate_checkpoint(packet, self.root)
        self.assertFalse(report["ok"])
        self.assertIn("verifier-skipped", {item["code"] for item in report["errors"]})

    def test_pending_work_and_open_question_are_blockers(self) -> None:
        packet = self._packet(
            pending_tools=[{"id": "tool-1", "state": "pending"}],
            unresolved_questions=["which target is authorized?"],
        )
        report = validate_checkpoint(packet, self.root)
        self.assertFalse(report["ok"])
        self.assertEqual(report["pending_blockers"], ["tool-1"])
        codes = {item["code"] for item in report["errors"]}
        self.assertTrue({"pending-work", "unresolved-question"} <= codes)

    def test_only_resolved_question_status_clears_its_blocker(self) -> None:
        for status in ("open", "unresolved", "pending", "resolved"):
            with self.subTest(status=status):
                packet = self._packet(unresolved_questions=[{"question": "which target?", "status": status}])
                report = validate_checkpoint(packet, self.root)
                self.assertEqual(report["ok"], status == "resolved")
                self.assertEqual(report["continuation_ready"], status == "resolved")
                self.assertFalse(report["completion_proven"])
                if status != "resolved":
                    self.assertIn("unresolved-question", {item["code"] for item in report["errors"]})

    def test_unknown_question_status_cannot_clear_a_blocker(self) -> None:
        for status in ("opne", "", "done", "RESOLVED", [], None):
            with self.subTest(status=status):
                question = {"question": "which target?", "status": status}
                with self.assertRaisesRegex(CheckpointError, "status is unsupported"):
                    self._packet(unresolved_questions=[question])
                packet = self._packet()
                packet["unresolved_questions"] = [question]
                report = validate_checkpoint(packet, self.root)
                self.assertFalse(report["ok"])
                self.assertFalse(report["continuation_ready"])
                self.assertIn("question-status", {item["code"] for item in report["errors"]})

    def test_stale_timestamp_is_reported(self) -> None:
        old = datetime.now(timezone.utc) - timedelta(days=2)
        packet = self._packet(created_at=old)
        report = validate_checkpoint(
            packet,
            self.root,
            now=datetime.now(timezone.utc),
            max_age_seconds=60,
        )
        self.assertFalse(report["ok"])
        self.assertIn("stale-checkpoint", {item["code"] for item in report["errors"]})

    def test_destination_uses_workspace_json_writer(self) -> None:
        destination = self.base / "receipts" / "checkpoint.json"
        packet = self._packet(destination=destination)
        self.assertEqual(packet, json.loads(destination.read_text(encoding="utf-8")))
        with self.assertRaises(CheckpointError):
            self._packet(destination=self.root / "checkpoint.json")
        original = destination.read_text(encoding="utf-8")
        with self.assertRaises(CheckpointError):
            self._packet(destination=destination)
        self.assertEqual(destination.read_text(encoding="utf-8"), original)

    def test_destination_rejects_redirecting_parent_before_write(self) -> None:
        target = self.base / "destination-target"
        target.mkdir()
        redirect = self.base / "destination-link"
        if os.name == "nt":
            completed = subprocess.run(
                ["cmd.exe", "/d", "/c", f"mklink /J {redirect} {target}"],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode != 0:
                self.skipTest(f"junction creation unavailable: {completed.stderr.strip()}")
        else:
            try:
                redirect.symlink_to(target, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"link creation unavailable: {exc}")
        with self.assertRaises(CheckpointError):
            self._packet(destination=redirect / "checkpoint.json")
        self.assertFalse((target / "checkpoint.json").exists())

    def test_empty_source_requires_explicit_reason_but_can_save_unfinished_state(self) -> None:
        with self.assertRaisesRegex(CheckpointError, "empty source"):
            create_checkpoint(
                self.root,
                [],
                "record an external state",
                "ask the owner for the source",
                done_condition="owner confirms the target",
            )
        packet = create_checkpoint(
            self.root,
            [],
            "record an external state",
            "ask the owner for the source",
            done_condition="owner confirms the target",
            empty_source_reason="The source is held by an external system.",
        )
        report = validate_checkpoint(packet, self.root)
        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(report["observed_receipts"], 0)
        self.assertFalse(report["recorded_checks_complete"])
        self.assertFalse(report["completion_proven"])
        self.assertFalse(report["continuation_ready"])
        self.assertIn("verifier-not-run", {item["code"] for item in report["warnings"]})

    def test_no_verifier_is_explicit_noncompletion(self) -> None:
        packet = self._packet(verifier_receipts=[])
        report = validate_checkpoint(packet, self.root)
        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(report["declared_receipts"], 0)
        self.assertEqual(report["observed_receipts"], 0)
        self.assertFalse(report["recorded_checks_complete"])
        self.assertFalse(report["continuation_ready"])
        self.assertFalse(report["completion_proven"])
        self.assertIn("verifier-not-run", {item["code"] for item in report["warnings"]})
        packet.pop("verifier_receipts")
        report = validate_checkpoint(packet, self.root)
        self.assertTrue(report["ok"], report["errors"])
        self.assertFalse(report["recorded_checks_complete"])
        self.assertIn("verifier-not-run", {item["code"] for item in report["warnings"]})

    def test_untrusted_checkpoint_cannot_claim_authority(self) -> None:
        packet = self._packet()
        packet["authority_granted"] = True
        report = validate_checkpoint(packet, self.root)
        self.assertFalse(report["ok"])
        self.assertFalse(report["authority_granted"])
        self.assertIn("authority-claim", {item["code"] for item in report["errors"]})

    def test_malformed_json_values_return_structured_errors(self) -> None:
        packet = self._packet()
        packet["pending_tools"] = [{"id": "tool-1", "state": [], "generation": 0}]
        packet["unresolved_questions"] = [{"question": "q", "status": []}]
        packet["verifier_receipts"] = [{"name": "check", "state": {}}]
        report = validate_checkpoint(packet, self.root)
        self.assertFalse(report["ok"])
        self.assertGreaterEqual(len(report["errors"]), 3)


if __name__ == "__main__":
    unittest.main()
