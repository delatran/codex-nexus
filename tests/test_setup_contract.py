from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import tomllib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from nexus import config_install, install


def _load_setup():
    setup_path = Path(__file__).resolve().parents[1] / "setup.py"
    spec = importlib.util.spec_from_file_location("codex_nexus_setup_contract", setup_path)
    if spec is None or spec.loader is None:
        raise AssertionError("setup.py could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fixture(base: Path) -> tuple[Path, Path, Path, Path]:
    root = base / "project"
    home = base / "home"
    (root / ".codex").mkdir(parents=True)
    (root / "skills").mkdir()
    (root / "skills" / "example.md").write_text("example", encoding="utf-8")
    (root / "AGENTS.md").write_text("English instructions", encoding="utf-8")

    source = Path(__file__).resolve().parents[1] / ".codex" / "config.toml"
    source_text = source.read_text(encoding="utf-8")
    (root / ".codex" / "config.toml").write_text(source_text, encoding="utf-8")

    target = home / ".codex" / "config.toml"
    target.parent.mkdir(parents=True)
    target.write_text(
        source_text.replace('model = "gpt-6-astra"', 'model = "old-model"', 1)
        + '\n[mcp_servers.custom]\ncommand = "keep-me"\n'
        + "[plugins.custom]\nenabled = true\n"
        + '[preferences]\ntheme = "dark"\n',
        encoding="utf-8",
    )

    codex = base / "codex.exe"
    codex.write_text("test executable placeholder", encoding="utf-8")
    return root, home, target, codex


def _run_setup(module, arguments: list[str]) -> tuple[int, dict[str, object]]:
    output = io.StringIO()
    with redirect_stdout(output):
        result = module.main(arguments)
    return result, json.loads(output.getvalue())


def _fake_native_merge(_executable: Path, _stage_home: Path, stage: Path, _edits: list[dict[str, object]]) -> None:
    content = stage.read_text(encoding="utf-8")
    if 'model = "old-model"' not in content:
        raise AssertionError("fixture did not require the native writer to change model")
    stage.write_text(content.replace('model = "old-model"', 'model = "gpt-6-astra"', 1), encoding="utf-8")


class SetupContractTests(unittest.TestCase):
    def test_unused_option_combinations_fail_before_planning(self) -> None:
        module = _load_setup()
        combinations = (["--health", "--codex", "unused"], ["--health", "--replace"],
                        ["--snapshot", "unused"], ["--backup-root", "unused"])
        for arguments in combinations:
            with self.subTest(arguments=arguments), redirect_stderr(io.StringIO()):
                with mock.patch.object(module, "_plan") as planner:
                    with self.assertRaises(SystemExit) as raised:
                        module.main(arguments)
                    self.assertEqual(raised.exception.code, 2)
                    planner.assert_not_called()

    def test_default_apply_preserves_unrelated_config_and_is_idempotent(self) -> None:
        module = _load_setup()
        with tempfile.TemporaryDirectory() as temporary:
            root, home, target, codex = _fixture(Path(temporary).resolve())
            arguments = ["--root", str(root), "--home", str(home), "--codex", str(codex)]
            before_unrelated = {
                "mcp_servers": {"custom": {"command": "keep-me"}},
                "plugins": {"custom": {"enabled": True}},
                "preferences": {"theme": "dark"},
            }

            with mock.patch.object(
                config_install, "_native_batch_write", side_effect=_fake_native_merge
            ) as writer:
                first_code, first = _run_setup(module, arguments)
                first_bytes = target.read_bytes()
                second_code, second = _run_setup(module, arguments)

            self.assertEqual(first_code, 0)
            self.assertEqual(second_code, 0)
            self.assertEqual(first["schema"], "codex-nexus/setup-receipt/v1")
            self.assertEqual(first["status"], "applied")
            self.assertEqual(second["status"], "applied")
            self.assertEqual(first["configuration"]["status"], "applied")
            self.assertEqual(second["configuration"]["status"], "unchanged")
            self.assertEqual(writer.call_count, 1)
            self.assertEqual(target.read_bytes(), first_bytes)

            observed = tomllib.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(observed["model"], "gpt-6-astra")
            for section, expected in before_unrelated.items():
                self.assertEqual(observed[section], expected)

            health = install.health(root, home, include_configuration=True)
            self.assertTrue(health["ok"])
            self.assertEqual(second["links"]["created"], [])

    def test_explicit_dry_run_is_read_only_for_combined_plan(self) -> None:
        module = _load_setup()
        with tempfile.TemporaryDirectory() as temporary:
            root, home, target, codex = _fixture(Path(temporary).resolve())
            original_target = target.read_bytes()
            arguments = [
                "--dry-run",
                "--root",
                str(root),
                "--home",
                str(home),
                "--codex",
                str(codex),
            ]

            with mock.patch.object(config_install, "_native_batch_write") as writer:
                result, plan = _run_setup(module, arguments)

            self.assertEqual(result, 0)
            self.assertEqual(plan["schema"], "codex-nexus/setup-plan/v1")
            self.assertEqual(plan["links"]["schema"], "codex-nexus/install-plan/v1")
            self.assertEqual(plan["configuration"]["schema"], "codex-nexus/config-plan/v1")
            self.assertTrue(plan["configuration"]["needs_write"])
            writer.assert_not_called()
            self.assertEqual(target.read_bytes(), original_target)
            self.assertFalse((home / ".agents" / "skills").exists())
            self.assertFalse((home / ".codex" / "AGENTS.md").exists())

    def test_final_health_failure_restores_links_and_configuration(self) -> None:
        module = _load_setup()
        with tempfile.TemporaryDirectory() as temporary:
            root, home, target, codex = _fixture(Path(temporary).resolve())
            original_config = target.read_bytes()
            original_health = install.health

            def fail_combined_health(
                health_root: Path | str,
                health_home: Path | str,
                *,
                include_configuration: bool = False,
            ) -> dict[str, object]:
                if include_configuration:
                    return {
                        "schema": install.SCHEMA_HEALTH,
                        "ok": False,
                        "targets": [],
                        "summary": {"correct": 0, "missing": 2, "conflict": 0},
                        "configuration": {"ok": True},
                    }
                return original_health(
                    health_root,
                    health_home,
                    include_configuration=include_configuration,
                )

            with mock.patch.object(
                config_install, "_native_batch_write", side_effect=_fake_native_merge
            ), mock.patch.object(module.install, "health", side_effect=fail_combined_health):
                result, receipt = _run_setup(
                    module,
                    ["--root", str(root), "--home", str(home), "--codex", str(codex)],
                )

            self.assertEqual(result, 2)
            self.assertEqual(receipt["status"], "error")
            self.assertIn("healthy installation", receipt["error"])
            self.assertEqual(target.read_bytes(), original_config)
            self.assertFalse((home / ".agents" / "skills").exists())
            self.assertFalse((home / ".codex" / "AGENTS.md").exists())
            self.assertFalse(install._ownership_path(home).exists())
            self.assertEqual(install.health(root, home)["summary"]["missing"], 2)


if __name__ == "__main__":
    unittest.main()
