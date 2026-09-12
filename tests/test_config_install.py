from __future__ import annotations

import io
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
import importlib.util
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from nexus import config_install, runtime


def _fixture(base: Path) -> tuple[Path, Path]:
    base = base.resolve()
    root = base / "project"
    home = base / "home"
    (root / ".codex").mkdir(parents=True)
    (root / "skills").mkdir()
    (root / "AGENTS.md").write_text("instructions", encoding="utf-8")
    source = Path(__file__).resolve().parents[1] / ".codex" / "config.toml"
    (root / ".codex" / "config.toml").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return root, home


def _offline_runtime() -> runtime.Runtime:
    """Represent a selected executable without requiring Codex on the host."""

    return runtime.Runtime(
        Path(sys.executable).resolve(),
        "explicit",
        "offline-fixture",
        False,
        False,
        False,
        "offline test runtime",
    )


def _unavailable_runtime() -> runtime.Runtime:
    """Represent the production no-Codex case for the availability guard."""

    return runtime.Runtime(
        None,
        "unavailable",
        None,
        False,
        False,
        False,
        "no usable Codex executable was found",
    )


class ConfigInstallTests(unittest.TestCase):
    def test_redirected_stage_is_rejected_before_read_or_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            outside = base / "outside"
            outside.mkdir()
            (outside / "config.toml").write_text('model = "gpt-6-astra"\n', encoding="utf-8")
            redirect = base / "stage"
            try:
                os.symlink(outside, redirect, target_is_directory=True)
            except OSError as exc:
                if os.name != "nt":
                    self.skipTest(f"directory redirects unavailable: {exc}")
                subprocess.run(
                    [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", "mklink", "/J", str(redirect), str(outside)],
                    check=True, capture_output=True,
                )
            stage = redirect / "config.toml"
            target = base / "owner.toml"
            original = b"# owner file\n"
            target.write_bytes(original)
            with self.assertRaisesRegex(config_install.ConfigInstallError, "redirect"):
                config_install._verify_stage(stage, None, {"model": "gpt-6-astra"}, [])
            with self.assertRaisesRegex(config_install.ConfigInstallError, "redirect"):
                config_install._publish_exclusive(target, stage, hashlib.sha256(original).hexdigest())
            self.assertEqual(target.read_bytes(), original)
            self.assertEqual(list(base.glob(".*.capture-*")), [])

    def test_plan_is_sanitized_and_reports_owned_keys_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, home = _fixture(Path(temporary))
            plan = config_install.plan_config(root, home, codex=_offline_runtime())
            self.assertEqual(plan["target"], "user-config")
            self.assertNotIn(str(home), repr(plan))
            self.assertIn("model", plan["owned"])
            self.assertNotIn("agents.max_depth", plan["owned"])

    def test_typed_feature_shorthand_migration_preserves_unowned_siblings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary).resolve() / "config.toml"
            stage.write_text(
                '[features.network_proxy]\n'
                'enabled = true\n'
                '[plugins.custom]\n'
                'enabled = true\n',
                encoding="utf-8",
            )
            with self.subTest("legacy boolean shorthand"):
                raw, after = config_install._verify_stage(
                    stage,
                    {
                        "features": {"network_proxy": True},
                        "plugins": {"custom": {"enabled": True}},
                    },
                    {"features.network_proxy.enabled": True},
                    [],
                )
                self.assertTrue(raw)
                self.assertEqual(after["features"]["network_proxy"]["enabled"], True)

            with self.subTest("existing typed optional sibling"):
                stage.write_text(
                    '[features.network_proxy]\n'
                    'enabled = true\n'
                    'proxy_url = "http://proxy.example"\n'
                    '[plugins.custom]\n'
                    'enabled = true\n',
                    encoding="utf-8",
                )
                raw, after = config_install._verify_stage(
                    stage,
                    {
                        "features": {
                            "network_proxy": {
                                "enabled": False,
                                "proxy_url": "http://proxy.example",
                            }
                        },
                        "plugins": {"custom": {"enabled": True}},
                    },
                    {"features.network_proxy.enabled": True},
                    [],
                )
                self.assertTrue(raw)
                self.assertEqual(after["features"]["network_proxy"]["proxy_url"], "http://proxy.example")

            with self.subTest("non-activation leaf cannot consume shorthand"):
                stage.write_text(
                    '[features.network_proxy]\n'
                    'proxy_url = "http://proxy.example"\n',
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(config_install.ConfigInstallError, "unrelated user setting"):
                    config_install._verify_stage(
                        stage,
                        {"features": {"network_proxy": True}},
                        {"features.network_proxy.proxy_url": "http://proxy.example"},
                        [],
                    )

    def test_malformed_user_toml_is_rejected_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, home = _fixture(Path(temporary))
            target = home / ".codex" / "config.toml"
            target.parent.mkdir(parents=True)
            target.write_text("model = [", encoding="utf-8")
            original = target.read_bytes()
            with self.assertRaisesRegex(config_install.ConfigInstallError, "malformed TOML"):
                config_install.plan_config(root, home, codex=_offline_runtime())
            self.assertEqual(target.read_bytes(), original)

    def test_stale_plan_fails_before_native_writer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, home = _fixture(Path(temporary))
            target = home / ".codex" / "config.toml"
            target.parent.mkdir(parents=True)
            target.write_text("# owner\nmodel = \"old\"\n[plugins.custom]\nenabled = true\n", encoding="utf-8")
            plan = config_install.plan_config(root, home, codex=_offline_runtime())
            target.write_text(target.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")
            with mock.patch.object(config_install, "_native_batch_write") as writer:
                with self.assertRaisesRegex(config_install.ConfigInstallError, "changed after planning"):
                    config_install.apply_config(plan, root, home, codex=_offline_runtime())
                writer.assert_not_called()

    def test_second_plan_is_zero_change_after_apply(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, home = _fixture(Path(temporary))

            def fake_native(_executable, _home, stage, _edits):
                stage.write_text((root / ".codex" / "config.toml").read_text(encoding="utf-8"), encoding="utf-8")

            first = config_install.plan_config(root, home, codex=_offline_runtime())
            with mock.patch.object(config_install, "_native_batch_write", side_effect=fake_native):
                config_install.apply_config(first, root, home, codex=_offline_runtime())
            second = config_install.plan_config(root, home, codex=_offline_runtime())
            self.assertFalse(second["needs_write"])
            with mock.patch.object(config_install, "_native_batch_write") as writer:
                receipt = config_install.apply_config(second, root, home, codex=_offline_runtime())
                writer.assert_not_called()
            self.assertEqual(receipt.receipt["status"], "unchanged")

    def test_boolean_integer_type_drift_is_not_treated_as_equal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, home = _fixture(Path(temporary))
            target = home / ".codex" / "config.toml"
            target.parent.mkdir(parents=True)
            source = (root / ".codex" / "config.toml").read_text(encoding="utf-8")
            target.write_text(source.replace("apps = true", "apps = 1", 1), encoding="utf-8")
            health = config_install.health(root, home)
            self.assertFalse(health["ok"])
            self.assertIn("features.apps", health["drift_keys"])
            plan = config_install.plan_config(root, home, codex=_offline_runtime())
            self.assertTrue(plan["needs_write"])

    def test_exclusive_publish_preserves_a_target_that_appears_late(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            target = base / "config.toml"
            stage = base / "stage.toml"
            target.write_text('model = "old"\n', encoding="utf-8")
            stage.write_text('model = "new"\n', encoding="utf-8")
            expected = config_install._sha_bytes(target.read_bytes())

            def race_link(_source, destination):
                Path(destination).write_text('model = "owner change"\n', encoding="utf-8")
                raise FileExistsError(destination)

            with mock.patch.object(config_install.os, "link", side_effect=race_link):
                with self.assertRaisesRegex(config_install.ConfigInstallError, "target appeared"):
                    config_install._publish_exclusive(target, stage, expected)
            self.assertEqual(target.read_text(encoding="utf-8"), 'model = "owner change"\n')

    def test_failed_native_merge_leaves_original_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, home = _fixture(Path(temporary))
            target = home / ".codex" / "config.toml"
            target.parent.mkdir(parents=True)
            target.write_text("# owner\nmodel = \"old\"\n[plugins.custom]\nenabled = true\n", encoding="utf-8")
            original = target.read_bytes()
            plan = config_install.plan_config(root, home, codex=_offline_runtime())
            with mock.patch.object(
                config_install,
                "_native_batch_write",
                side_effect=config_install.ConfigInstallError("forced merge failure"),
            ):
                with self.assertRaisesRegex(config_install.ConfigInstallError, "forced merge failure"):
                    config_install.apply_config(plan, root, home, codex=_offline_runtime())
            self.assertEqual(target.read_bytes(), original)

    def test_apply_rejects_unavailable_codex_before_native_writer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, home = _fixture(Path(temporary))
            plan = config_install.plan_config(root, home, codex=_unavailable_runtime())
            with mock.patch.object(config_install, "_native_batch_write") as writer:
                with self.assertRaisesRegex(
                    config_install.ConfigInstallError,
                    "compatible Codex executable is required",
                ):
                    config_install.apply_config(plan, root, home, codex=_unavailable_runtime())
                writer.assert_not_called()
            self.assertFalse((home / ".codex" / "config.toml").exists())

    def test_writer_dropping_unrelated_settings_is_rejected_before_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, home = _fixture(Path(temporary))
            target = home / ".codex" / "config.toml"
            target.parent.mkdir(parents=True)
            original = b'[plugins.custom]\nenabled = true\n'
            target.write_bytes(original)
            plan = config_install.plan_config(root, home, codex=_offline_runtime())

            def lossy_writer(_executable, _home, stage, _edits):
                stage.write_bytes((root / ".codex" / "config.toml").read_bytes())

            with mock.patch.object(config_install, "_native_batch_write", side_effect=lossy_writer):
                with self.assertRaisesRegex(config_install.ConfigInstallError, "unrelated user setting"):
                    config_install.apply_config(plan, root, home, codex=_offline_runtime())
            self.assertEqual(target.read_bytes(), original)

    def test_writer_changing_unrelated_value_types_is_rejected_before_publication(self) -> None:
        cases = (
            ("true to integer", "true", "1"),
            ("false to integer", "false", "0"),
            ("integer to boolean", "1", "true"),
            ("integer to float", "1", "1.0"),
            ("array item", "[true, 2]", "[1, 2]"),
            ("table inside array", "[{ enabled = true }]", "[{ enabled = 1 }]"),
        )
        for label, before_value, after_value in cases:
            with self.subTest(label), tempfile.TemporaryDirectory() as temporary:
                root, home = _fixture(Path(temporary))
                target = home / ".codex" / "config.toml"
                target.parent.mkdir(parents=True)
                original = f"[plugins.custom]\noptions = {before_value}\n".encode("utf-8")
                target.write_bytes(original)
                plan = config_install.plan_config(root, home, codex=_offline_runtime())

                def corrupting_writer(_executable, _home, stage, _edits):
                    source = (root / ".codex" / "config.toml").read_text(encoding="utf-8")
                    stage.write_text(
                        source + f"\n[plugins.custom]\noptions = {after_value}\n",
                        encoding="utf-8",
                    )

                with mock.patch.object(
                    config_install, "_native_batch_write", side_effect=corrupting_writer
                ), mock.patch.object(
                    config_install, "_publish_exclusive", wraps=config_install._publish_exclusive
                ) as publish:
                    with self.assertRaisesRegex(config_install.ConfigInstallError, "unrelated user setting"):
                        config_install.apply_config(plan, root, home, codex=_offline_runtime())
                    publish.assert_not_called()
                self.assertEqual(target.read_bytes(), original)

    def test_tampered_private_backup_is_refused_on_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, home = _fixture(base)
            target = home / ".codex" / "config.toml"
            target.parent.mkdir(parents=True)
            target.write_text("# owner\nmodel = \"old\"\n", encoding="utf-8")

            def fake_native(_executable, _home, stage, _edits):
                stage.write_text((root / ".codex" / "config.toml").read_text(encoding="utf-8"), encoding="utf-8")

            plan = config_install.plan_config(root, home, codex=_offline_runtime())
            with mock.patch.object(config_install, "_native_batch_write", side_effect=fake_native):
                applied = config_install.apply_config(plan, root, home, codex=_offline_runtime())
            assert applied.backup is not None
            applied.backup.write_text("model = \"tampered\"\n", encoding="utf-8")
            with self.assertRaisesRegex(config_install.ConfigInstallError, "integrity"):
                config_install.rollback_config(applied)
            self.assertIn("gpt-6-astra", target.read_text(encoding="utf-8"))

    def test_post_publish_rollback_failure_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, home = _fixture(base)
            target = home / ".codex" / "config.toml"
            target.parent.mkdir(parents=True)
            target.write_text("model = \"old\"\n", encoding="utf-8")

            def fake_native(_executable, _home, stage, _edits):
                stage.write_text((root / ".codex" / "config.toml").read_text(encoding="utf-8"), encoding="utf-8")

            plan = config_install.plan_config(root, home, codex=_offline_runtime())
            with mock.patch.object(config_install, "_native_batch_write", side_effect=fake_native), mock.patch.object(
                config_install,
                "_verify_stage",
                side_effect=[(b"", {}), config_install.ConfigInstallError("forced postcheck")],
            ), mock.patch.object(
                config_install,
                "_replace_from_backup",
                side_effect=config_install.ConfigInstallError("forced rollback failure"),
            ):
                with self.assertRaisesRegex(config_install.ConfigInstallError, "rollback was incomplete"):
                    config_install.apply_config(plan, root, home, codex=_offline_runtime())

    def test_post_publish_owner_edit_is_preserved_when_rollback_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, home = _fixture(base)
            target = home / ".codex" / "config.toml"
            target.parent.mkdir(parents=True)
            target.write_text("model = \"old\"\n", encoding="utf-8")

            def fake_native(_executable, _home, stage, _edits):
                stage.write_text((root / ".codex" / "config.toml").read_text(encoding="utf-8"), encoding="utf-8")

            original_verify = config_install._verify_stage
            verify_calls = 0

            def owner_edit_after_publish(stage, before, owned, remove):
                nonlocal verify_calls
                verify_calls += 1
                result = original_verify(stage, before, owned, remove)
                if verify_calls == 2:
                    target.write_text('model = "owner-change"\n', encoding="utf-8")
                    raise config_install.ConfigInstallError("forced postcheck")
                return result

            plan = config_install.plan_config(root, home, codex=_offline_runtime())
            with mock.patch.object(config_install, "_native_batch_write", side_effect=fake_native), mock.patch.object(
                config_install, "_verify_stage", side_effect=owner_edit_after_publish
            ):
                with self.assertRaisesRegex(config_install.ConfigInstallError, "rollback was incomplete"):
                    config_install.apply_config(plan, root, home, codex=_offline_runtime())
            self.assertEqual(target.read_text(encoding="utf-8"), 'model = "owner-change"\n')

    def test_staging_cleanup_warning_is_reported_on_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, home = _fixture(base)
            target = home / ".codex" / "config.toml"
            target.parent.mkdir(parents=True)
            target.write_text("model = \"old\"\n", encoding="utf-8")

            def fake_native(_executable, _home, stage, _edits):
                stage.write_text((root / ".codex" / "config.toml").read_text(encoding="utf-8"), encoding="utf-8")

            plan = config_install.plan_config(root, home, codex=_offline_runtime())
            with mock.patch.object(config_install, "_native_batch_write", side_effect=fake_native), mock.patch.object(
                config_install,
                "_cleanup_stage_home",
                return_value=config_install.ConfigInstallError("forced cleanup failure"),
            ):
                applied = config_install.apply_config(plan, root, home, codex=_offline_runtime())
            self.assertEqual(applied.receipt["warnings"], ["staging cleanup was incomplete"])

    def test_silent_native_server_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stage_home = Path(temporary).resolve()
            stage = stage_home / "config.toml"
            stage.write_text("", encoding="utf-8")
            real_popen = config_install.subprocess.Popen

            def silent_process(*_args, **kwargs):
                return real_popen(
                    [sys.executable, "-c", "import time; time.sleep(10)"],
                    **kwargs,
                )

            with mock.patch.object(config_install.subprocess, "Popen", side_effect=silent_process), mock.patch.object(
                config_install, "RPC_TIMEOUT_SECONDS", 0.05
            ):
                with self.assertRaisesRegex(config_install.ConfigInstallError, "timed out|did not return"):
                    config_install._native_batch_write(Path(sys.executable), stage_home, stage, [])

    def test_staged_merge_preserves_unrelated_settings_and_removes_legacy_keys(self) -> None:
        selected = _offline_runtime()
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root, home = _fixture(base)
            target = home / ".codex" / "config.toml"
            target.parent.mkdir(parents=True)
            target.write_text(
                "# preserve this comment\n"
                "model = \"old\"\n"
                "project_doc_fallback_filenames = [\"foreign.md\"]\n"
                "project_doc_max_bytes = 100\n"
                "project_root_markers = [\".git\"]\n"
                "[agents]\n"
                "max_threads = 6\n"
                "max_depth = 1\n"
                "job_max_runtime_seconds = 42\n"
                "[features]\n"
                "multi_agent = true\n"
                "enable_request_compression = true\n"
                "[plugins.custom]\n"
                "enabled = true\n",
                encoding="utf-8",
            )

            def fake_native(_executable, _home, stage, _edits):
                source = (root / ".codex" / "config.toml").read_text(encoding="utf-8")
                stage.write_text(
                    "# preserve this comment\n"
                    + source
                    + "\n[plugins.custom]\nenabled = true\n",
                    encoding="utf-8",
                )

            plan = config_install.plan_config(root, home, codex=selected)
            with mock.patch.object(config_install, "_native_batch_write", side_effect=fake_native):
                applied = config_install.apply_config(plan, root, home, codex=selected)
            self.assertEqual(applied.receipt["status"], "applied")
            text = target.read_text(encoding="utf-8")
            self.assertIn("# preserve this comment", text)
            self.assertIn("[plugins.custom]", text)
            self.assertNotIn("max_threads", text)
            self.assertNotIn("max_depth", text)
            self.assertNotIn("job_max_runtime_seconds", text)
            self.assertNotIn("project_doc_max_bytes", text)
            self.assertTrue(config_install.health(root, home)["ok"])
            config_install.rollback_config(applied)
            self.assertIn("max_threads", target.read_text(encoding="utf-8"))

    def test_setup_dry_run_does_not_create_home_or_start_writer(self) -> None:
        setup_path = Path(__file__).resolve().parents[1] / "setup.py"
        spec = importlib.util.spec_from_file_location("staged_setup_dry_run", setup_path)
        self.assertIsNotNone(spec)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            root, home = _fixture(Path(temporary))
            with mock.patch.object(config_install, "_native_batch_write") as writer:
                output = io.StringIO()
                with redirect_stdout(output):
                    result = module.main(["--dry-run", "--root", str(root), "--home", str(home)])
                self.assertEqual(result, 0)
                writer.assert_not_called()
            self.assertFalse(home.exists())
            self.assertIn('"schema":"codex-nexus/setup-plan/v1"', output.getvalue())


if __name__ == "__main__":
    unittest.main()
