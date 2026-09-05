"""Protect source and evidence inputs from report-output mistakes."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from nexus.__main__ import main


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.packet = self.root / "packet.json"
        self.packet.write_text("{}", encoding="utf-8")

    def run_command(self, *args):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            status = main(list(args))
        return status, json.loads(buffer.getvalue())

    def test_receipt_cannot_replace_source(self):
        target = self.root / "README.md"
        target.write_text("Owner source", encoding="utf-8")
        status, result = self.run_command("evidence", str(self.packet), "--root", str(self.root), "--output", str(target))
        self.assertEqual(status, 2)
        self.assertFalse(result["ok"])
        self.assertEqual(target.read_text(), "Owner source")

    def test_receipt_cannot_replace_external_input(self):
        source_root = self.root / "source"
        source_root.mkdir()
        before = self.packet.read_bytes()
        status, _ = self.run_command("evidence", str(self.packet), "--root", str(source_root), "--output", str(self.packet))
        self.assertEqual(status, 2)
        self.assertEqual(self.packet.read_bytes(), before)

    def test_failed_packet_stays_failed_in_saved_receipt(self):
        target = self.root / "artifacts" / "failed.json"
        status, result = self.run_command("evidence", str(self.packet), "--root", str(self.root), "--output", str(target))
        self.assertEqual(status, 1)
        self.assertFalse(result["ok"])
        self.assertEqual(json.loads(target.read_text()), result)

    def test_direct_entrypoint_runs_from_unrelated_project_without_pythonpath(self):
        entry = Path(__file__).resolve().parents[1] / "nexus" / "__main__.py"
        caller = self.root / "owner project"
        caller.mkdir()
        (caller / "packet.json").write_text("{}", encoding="utf-8")
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        environment.pop("PYTHONPATH", None)
        result = subprocess.run(
            [sys.executable, "-B", str(entry), "evidence", "packet.json",
             "--root", str(caller), "--output", "artifacts/result.json"],
            cwd=caller, env=environment, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(result.stdout)
        self.assertFalse(report["ok"])
        self.assertEqual(json.loads((caller / "artifacts/result.json").read_text()), report)


if __name__ == "__main__":
    unittest.main()
