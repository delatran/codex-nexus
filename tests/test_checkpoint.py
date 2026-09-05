from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

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
        report = validate_checkpoint(packet, self.root)
        self.assertFalse(report["ok"])
        self.assertEqual(report["stale_files"], ["changed.txt"])
        self.assertIn("stale-source-file", {item["code"] for item in report["errors"]})

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
        with self.assertRaises(CheckpointError):
            create_checkpoint(
                self.root,
                ["../outside.txt"],
                "goal",
                "next",
                done_condition="done",
            )
        packet = self._packet()
        packet["source"]["files"][0]["path"] = "../outside.txt"
        report = validate_checkpoint(packet, self.root)
        self.assertFalse(report["ok"])
        self.assertIn("path-escape", {item["code"] for item in report["errors"]})

    def test_symlink_or_junction_root_is_rejected(self) -> None:
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret\n", encoding="utf-8")
        packet = self._packet()
        redirect = self.root / "redirect"
        if os.name == "nt":
            command = f"mklink /J {redirect} {outside}"
            completed = subprocess.run(
                ["cmd.exe", "/d", "/c", command],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode != 0:
                self.skipTest(f"junction creation unavailable: {completed.stderr.strip()}")
        else:
            try:
                redirect.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"link creation unavailable: {exc}")
        with self.assertRaises(CheckpointError):
            create_checkpoint(
                self.root,
                ["changed.txt"],
                "goal",
                "next",
                done_condition="done",
            )
        report = validate_checkpoint(packet, self.root)
        self.assertFalse(report["ok"])
        self.assertTrue(any(item["code"] == "unsafe-root" for item in report["errors"]))

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
