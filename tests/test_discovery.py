from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from nexus import discovery, runtime


class NativeDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.instructions = "# Project rules\n\nPreserve the requested work.\n"
        (self.root / "AGENTS.md").write_text(self.instructions, encoding="utf-8")
        self.skill = self.root / "skills" / "example" / "SKILL.md"
        self.skill.parent.mkdir(parents=True)
        self.skill.write_text('---\nname: "example"\ndescription: "Check current source with native tools."\n---\n\nPrivate source body.\n', encoding="utf-8")
        self.selected = runtime.Runtime(self.root / "native-client", "explicit", "native-client", False, False, False)
        self.enterContext(mock.patch.object(runtime, "_home", return_value=self.root / "global-config"))

    def prompt(self, *, path: str = "r0/example/SKILL.md", name: str = "example", description: str = "Check current source with native tools.") -> list:
        return [
            {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": (
                "<skills_instructions>\nPRIVATE-PROMPT-DATA\n### Skill roots\n"
                f"- `r0` = `{(self.root / 'skills').as_posix()}`\n"
                f"### Available skills\n- {name}: {description} (file: {path})\n"
                "</skills_instructions>"
            )}]},
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": (
                f"# AGENTS.md instructions for {self.root}\n\n<INSTRUCTIONS>\n"
                f"{self.instructions}\n</INSTRUCTIONS>"
            )}]},
        ]

    def inspect(self, payload: object) -> dict:
        with mock.patch.object(runtime, "_run", return_value=runtime.CommandResult(0, json.dumps(payload), "")) as run:
            result = discovery.inspect_discovery(self.root, self.selected)
        run.assert_called_once_with(self.selected.command, "debug", "prompt-input", cwd=self.root)
        return result

    def test_native_catalog_matches_source_paths_without_retaining_prompt(self) -> None:
        result = self.inspect(self.prompt())
        self.assertTrue(result["ok"], result["errors"])
        self.assertTrue(result["observed"]["instructions"]["source_text_visible"])
        self.assertEqual(result["observed"]["skills"][0]["source_sha256"], hashlib.sha256(self.skill.read_bytes()).hexdigest())
        self.assertTrue(result["observed"]["skills"][0]["cataloged_at_source"])
        self.assertFalse(result["prompt_retained"])
        self.assertFalse(result["skill_bodies_loaded"])
        self.assertFalse(result["model_call"])
        serialized = json.dumps(result)
        for private_value in ("PRIVATE-PROMPT-DATA", "Private source body.", str(self.root), self.root.as_posix(), self.instructions):
            self.assertNotIn(private_value, serialized)

    def test_absolute_catalog_path_is_supported(self) -> None:
        self.assertTrue(self.inspect(self.prompt(path=self.skill.as_posix()))["ok"])

    def test_shortened_description_is_an_observation_not_failure(self) -> None:
        result = self.inspect(self.prompt(description="Check current source"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["observed"]["skills"][0]["description_status"], "shortened")

    def test_name_substrings_and_wrong_source_paths_do_not_prove_discovery(self) -> None:
        for payload in (
            self.prompt(name="other-example"),
            self.prompt(path=(self.root / "other" / "example" / "SKILL.md").as_posix()),
            self.prompt(path="r99/example/SKILL.md"),
        ):
            with self.subTest(payload=payload[0]["content"][0]["text"]):
                result = self.inspect(payload)
                self.assertFalse(result["ok"])
                self.assertIn("source-skill", {item["check"] for item in result["errors"]})

    def test_duplicate_catalog_identity_is_ambiguous(self) -> None:
        payload = self.prompt()
        payload[0]["content"].append(copy.deepcopy(payload[0]["content"][0]))
        self.assertFalse(self.inspect(payload)["ok"])

    def test_conflicting_root_aliases_fail_with_sanitized_error(self) -> None:
        payload = self.prompt()
        payload[0]["content"][0]["text"] = payload[0]["content"][0]["text"].replace(
            "### Available skills", f"- `r0` = `{self.root.as_posix()}/PRIVATE-ALIAS`\n### Available skills"
        )
        result = self.inspect(payload)
        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0]["check"], "prompt-schema")
        self.assertNotIn("PRIVATE-ALIAS", json.dumps(result))

    def test_skill_bullets_outside_native_catalog_do_not_prove_discovery(self) -> None:
        payload = self.prompt()
        payload[0]["content"][0]["text"] = payload[0]["content"][0]["text"].replace("<skills_instructions>", "<quoted_example>").replace("</skills_instructions>", "</quoted_example>")
        self.assertFalse(self.inspect(payload)["ok"])

    def test_skill_bullets_outside_available_skills_section_do_not_prove_discovery(self) -> None:
        payload = self.prompt()
        payload[0]["content"][0]["text"] = payload[0]["content"][0]["text"].replace("### Available skills", "### Example catalog")
        self.assertFalse(self.inspect(payload)["ok"])

    def test_fenced_example_inside_catalog_is_not_a_discovered_skill(self) -> None:
        payload = self.prompt()
        payload[0]["content"][0]["text"] = payload[0]["content"][0]["text"].replace(
            "### Available skills\n", "### Available skills\n```text\n"
        ).replace("</skills_instructions>", "```\n</skills_instructions>")
        self.assertFalse(self.inspect(payload)["ok"])

    def test_fenced_complete_catalog_does_not_prove_native_discovery(self) -> None:
        payload = self.prompt()
        payload[0]["content"][0]["text"] = "```text\n" + payload[0]["content"][0]["text"] + "\n```"
        self.assertFalse(self.inspect(payload)["ok"])

    def test_instruction_quote_outside_workspace_block_is_insufficient(self) -> None:
        payload = self.prompt()
        payload[1]["content"][0]["text"] = self.instructions
        result = self.inspect(payload)
        self.assertFalse(result["ok"])
        self.assertIn("source-instructions", {item["check"] for item in result["errors"]})

    def test_wrong_workspace_and_partial_instructions_fail(self) -> None:
        original = self.prompt()
        for old, new in ((str(self.root), str(self.root / "other")), (self.instructions, "# Project rules\n")):
            payload = copy.deepcopy(original)
            payload[1]["content"][0]["text"] = payload[1]["content"][0]["text"].replace(old, new)
            with self.subTest(replacement=new):
                self.assertFalse(self.inspect(payload)["ok"])

    def test_non_native_prompt_structures_fail_closed(self) -> None:
        malformed = [None, {}, [], ["PRIVATE-MESSAGE"], [{"type": "message", "role": [], "content": []}]]
        missing_text = self.prompt()
        missing_text[0]["content"][0]["text"] = {"PRIVATE-FIELD": True}
        malformed.append(missing_text)
        for payload in malformed:
            with self.subTest(payload=payload):
                result = self.inspect(payload)
                self.assertFalse(result["ok"])
                self.assertEqual(result["errors"][0]["check"], "prompt-schema")
                self.assertNotIn("PRIVATE-", json.dumps(result))

    def test_failed_truncated_timed_out_or_malformed_output_is_not_parsed(self) -> None:
        cases = [
            (runtime.CommandResult(1, "PRIVATE-STDOUT", "PRIVATE-STDERR"), "discovery-command"),
            (runtime.CommandResult(124, "PRIVATE-STDOUT", "PRIVATE-STDERR", timed_out=True), "discovery-timeout"),
            (runtime.CommandResult(0, json.dumps(self.prompt()), "PRIVATE-STDERR", output_truncated=True), "discovery-output-limit"),
            (runtime.CommandResult(0, "PRIVATE-NON-JSON", ""), "prompt-json"),
        ]
        for command, expected in cases:
            with self.subTest(check=expected), mock.patch.object(runtime, "_run", return_value=command):
                result = discovery.inspect_discovery(self.root, self.selected)
                self.assertFalse(result["ok"])
                self.assertEqual(result["errors"][0]["check"], expected)
                self.assertNotIn("PRIVATE-", json.dumps(result))

    def test_source_change_during_native_probe_invalidates_discovery(self) -> None:
        payload = self.prompt()
        def run(*_args: object, **_kwargs: object) -> runtime.CommandResult:
            self.skill.write_text(self.skill.read_text() + "\nChanged source.\n")
            return runtime.CommandResult(0, json.dumps(payload), "")
        with mock.patch.object(runtime, "_run", side_effect=run):
            result = discovery.inspect_discovery(self.root, self.selected)
        self.assertFalse(result["ok"])
        self.assertIn("source-freshness", {item["check"] for item in result["errors"]})

    def test_new_skill_during_probe_invalidates_complete_catalog_observation(self) -> None:
        payload = self.prompt()
        def run(*_args: object, **_kwargs: object) -> runtime.CommandResult:
            new_skill = self.root / "skills" / "new-skill" / "SKILL.md"
            new_skill.parent.mkdir()
            new_skill.write_text('---\nname: "new-skill"\ndescription: "New source skill."\n---\n')
            return runtime.CommandResult(0, json.dumps(payload), "")
        with mock.patch.object(runtime, "_run", side_effect=run):
            result = discovery.inspect_discovery(self.root, self.selected)
        self.assertFalse(result["ok"])
        self.assertIn("source-freshness", {item["check"] for item in result["errors"]})

    def test_matching_global_copy_is_separate_from_managed_link_proof(self) -> None:
        installed = self.root / "global-config" / "AGENTS.md"
        installed.parent.mkdir()
        installed.write_bytes((self.root / "AGENTS.md").read_bytes())
        result = self.inspect(self.prompt())
        self.assertTrue(result["ok"])
        self.assertEqual(result["observed"]["global_instruction_file"]["status"], "matches_source")
        self.assertFalse(result["observed"]["global_instruction_file"]["same_file_as_source"])
        self.assertFalse(result["observed"]["instructions"]["global_origin_verified"])
        self.assertTrue(result["warnings"])

    def test_different_global_file_does_not_hide_valid_workspace_visibility(self) -> None:
        installed = self.root / "global-config" / "AGENTS.md"
        installed.parent.mkdir()
        installed.write_text("PRIVATE-OWNER-GLOBAL-INSTRUCTIONS")
        result = self.inspect(self.prompt())
        self.assertTrue(result["ok"])
        self.assertEqual(result["observed"]["global_instruction_file"]["status"], "differs_from_source")
        self.assertNotIn("PRIVATE-OWNER", json.dumps(result))

    def test_missing_source_and_capability_fixture_do_not_start_native_client(self) -> None:
        with mock.patch.object(runtime, "_run", side_effect=AssertionError("client must not run")):
            self.assertFalse(discovery.inspect_discovery(self.root, {})["ok"])
            (self.root / "AGENTS.md").unlink()
            result = discovery.inspect_discovery(self.root, self.selected)
        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0]["check"], "discovery-source")


if __name__ == "__main__":
    unittest.main()
