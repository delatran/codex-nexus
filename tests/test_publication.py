"""New receipts must preserve a destination created during staging."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from nexus import checkpoint, workspace
from nexus.__main__ import main


class PublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "source"
        self.root.mkdir()
        (self.root / "owner.txt").write_bytes(b"source input\n")

    def _concurrent_destination(self, output: Path):
        original = workspace.tempfile.mkstemp

        def interleave(*args, **kwargs):
            result = original(*args, **kwargs)
            output.write_bytes(b"concurrent owner output\n")
            return result

        return mock.patch.object(workspace.tempfile, "mkstemp", side_effect=interleave)

    def test_receipt_collision_preserves_owner_and_reports_failure(self) -> None:
        packet = self.base / "input.json"
        packet.write_text("{}", encoding="utf-8")
        output = self.base / "receipt.json"
        stdout = io.StringIO()
        with self._concurrent_destination(output), contextlib.redirect_stdout(stdout):
            status = main(["evidence", str(packet), "--root", str(self.root), "--output", str(output)])
        self.assertEqual(status, 2)
        self.assertFalse(json.loads(stdout.getvalue())["ok"])
        self.assertEqual(output.read_bytes(), b"concurrent owner output\n")
        self.assertEqual(packet.read_bytes(), b"{}")
        self.assertEqual(list(self.base.glob(".*.tmp")), [])

    def test_snapshot_collision_preserves_owner(self) -> None:
        output = self.base / "snapshot.json"
        with self._concurrent_destination(output), self.assertRaises(FileExistsError):
            workspace.snapshot(self.root, output)
        self.assertEqual(output.read_bytes(), b"concurrent owner output\n")
        self.assertEqual((self.root / "owner.txt").read_bytes(), b"source input\n")
        self.assertEqual(list(self.base.glob(".*.tmp")), [])

    def test_checkpoint_collision_preserves_owner(self) -> None:
        output = self.base / "checkpoint.json"
        with self._concurrent_destination(output), self.assertRaises(checkpoint.CheckpointError):
            checkpoint.create_checkpoint(
                self.root, ["owner.txt"], "repair", "verify", done_condition="done",
                destination=output,
            )
        self.assertEqual(output.read_bytes(), b"concurrent owner output\n")
        self.assertEqual(list(self.base.glob(".*.tmp")), [])

    def test_new_json_is_complete_and_does_not_replace_existing_output(self) -> None:
        output = self.base / "receipt.json"
        value = {"result": "\u00e9", "count": 1}
        workspace.write_json(output, value, overwrite=False)
        before = output.read_bytes()
        self.assertEqual(json.loads(before), value)
        self.assertTrue(before.endswith(b"\n"))
        with self.assertRaises(FileExistsError):
            workspace.write_json(output, {"changed": True}, overwrite=False)
        self.assertEqual(output.read_bytes(), before)
        self.assertEqual(list(self.base.glob(".*.tmp")), [])

    def test_intentional_json_replacement_still_updates_generated_output(self) -> None:
        output = self.base / "manifest.json"
        workspace.write_json(output, {"revision": 1})
        workspace.write_json(output, {"revision": 2})
        self.assertEqual(json.loads(output.read_bytes()), {"revision": 2})

    def test_unavailable_exclusive_publication_leaves_no_partial_receipt(self) -> None:
        output = self.base / "receipt.json"
        with mock.patch.object(workspace.os, "link", side_effect=OSError("link unavailable")):
            with self.assertRaises(OSError):
                workspace.write_json(output, {"ok": True}, overwrite=False)
        self.assertFalse(output.exists())
        self.assertEqual(list(self.base.glob(".*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
