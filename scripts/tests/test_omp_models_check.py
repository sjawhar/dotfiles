#!/usr/bin/env python3
"""Credential command paths and both models builders, in a private home only."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
CHECK = DOTFILES / "scripts" / "omp-models-check"


def write(path: Path, text: str, executable: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".new")
    tmp.write_text(text)
    tmp.chmod(0o700 if executable else 0o600)
    tmp.replace(path)
    return path


class ModelsCheck(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.dot = self.home / ".dotfiles"
        self.agent = self.home / ".omp" / "agent"
        self.profile = self.home / ".omp" / "profiles" / "guard-test" / "agent"
        for directory in (self.dot / "scripts", self.dot / "shims", self.agent, self.profile):
            directory.mkdir(parents=True, exist_ok=True)
        for name in ("omp-models-build", "omp-models-check"):
            source = DOTFILES / "scripts" / name
            if source.exists():
                shutil.copy2(source, self.dot / "scripts" / name)
        shutil.copy2(DOTFILES / "shims" / "omp", self.dot / "shims" / "omp")
        write(self.home / "bin" / "mise", '#!/bin/sh\n[ "$*" = "env -s bash" ]\n', True)
        # Stop at the exec boundary: no model calls, registration or shared-state writes.
        write(self.dot / "scripts" / "agent-session", '#!/bin/sh\nprintf "launched\\n"\n', True)
        # Resolve tools before changing HOME, so mise shims do not read an empty config.
        tools = [str(Path(sys.executable).parent), str(Path(shutil.which("yq")).parent)]
        self.env = dict(os.environ, HOME=str(self.home), DOTFILES_DIR=str(self.dot),
                        PATH=":".join([str(self.home / "bin"), *tools, os.environ["PATH"]]),
                        OMP_MEMORY_SCOPE="off", OMP_PROFILE="", PI_PROFILE="", PI_CONFIG_DIR=".omp",
                        XDG_CONFIG_HOME=str(self.home / ".config"))

    def run_check(self, text: str) -> subprocess.CompletedProcess:
        self.input = write(self.home / "models.yml", text)
        return subprocess.run(["python3", str(CHECK), str(self.input)], env=self.env,
                              capture_output=True, text=True)

    def build(self, text: str) -> subprocess.CompletedProcess:
        write(self.dot / "omp" / "models.yml", text)
        return subprocess.run(["bash", str(self.dot / "scripts" / "omp-models-build"),
                               str(self.agent / "models.yml")], env=self.env,
                              capture_output=True, text=True)

    def launch(self, overlay: str) -> subprocess.CompletedProcess:
        write(self.agent / "models.yml", "providers: {}\n")
        write(self.profile / "models.overlay.yml", overlay)
        return subprocess.run(["bash", str(self.dot / "shims" / "omp"), "--profile", "guard-test"],
                              env=self.env, capture_output=True, text=True)

    def test_rejects_path_dependent_or_empty_command_without_executing_it(self) -> None:
        for command in ("!gateway-token private-argument", "!./gateway-token", "!../token", "!   ",
                        f"!$(touch {self.home}/should-not-exist)/token", '!"gateway-token"'):
            with self.subTest(command=command):
                result = self.run_check('providers:\n  gateway:\n    apiKey: ' + repr(command) + '\n')
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(str(self.input) + ":3:", result.stderr)
                self.assertIn("apiKey", result.stderr)
                self.assertIn("absolute", result.stderr)
                self.assertIn("!/", result.stderr)
                self.assertNotIn("private-argument", result.stderr)
                self.assertFalse((self.home / "should-not-exist").exists())

    def test_absolute_and_quoted_absolute_paths_pass_without_execution(self) -> None:
        for command in ('!/bin/false', '! /not-installed/token arg', '!"/a path/token" --scope all',
                        f'!/bin/touch {self.home}/should-not-exist'):
            with self.subTest(command=command):
                result = self.run_check('providers: {gateway: {apiKey: ' + repr(command) + '}}\n')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout + result.stderr, "")
                self.assertFalse((self.home / "should-not-exist").exists())

    def test_no_command_is_unchanged_and_unrelated_bangs_are_not_commands(self) -> None:
        text = 'providers:\n  example:\n    apiKey: API_TOKEN\n    name: "!not-a-command"\n    headers: {X-Plain: literal}\n'
        result = self.run_check(text)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.input.read_text(), text)
        self.assertEqual(result.stdout + result.stderr, "")

    def test_headers_at_provider_model_and_override_levels_are_checked(self) -> None:
        text = '''providers:
  example:
    apiKey: "!/bin/true not-a-secret"
    headers:
      X-Provider: "!provider-token private-value"
    models:
      - id: example
        headers:
          X-Model: "!model-token"
    modelOverrides:
      example:
        headers:
          X-Override: "!override-token"
'''
        result = self.run_check(text)
        self.assertEqual(result.returncode, 1, result.stderr)
        for line, name in ((5, "X-Provider"), (9, "X-Model"), (13, "X-Override")):
            self.assertIn(str(self.input) + f":{line}:", result.stderr)
            self.assertIn(name, result.stderr)
        self.assertNotIn("private-value", result.stderr)

    def test_alias_command_is_checked(self) -> None:
        result = self.run_check('token: &token "!gateway-token"\nproviders:\n  example:\n    apiKey: *token\n')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("apiKey", result.stderr)

    def test_invalid_yaml_fails_without_disclosing_source_contents(self) -> None:
        result = self.run_check('providers: ["private-key"\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(str(self.input), result.stderr)
        self.assertNotIn("private-key", result.stderr)

    def test_default_builder_rejects_source_and_keeps_previous_output(self) -> None:
        out = write(self.agent / "models.yml", "previous output\n")
        result = self.build('providers:\n  example:\n    apiKey: "!not-a-secret-command"\n')
        self.assertNotEqual(result.returncode, 0, "builder accepted a bare-name command")
        self.assertIn(str(self.dot / "omp" / "models.yml") + ":3:", result.stderr)
        self.assertEqual(out.read_text(), "previous output\n")
        self.assertEqual(list(self.agent.glob("models.yml.new.*")), [])

    def test_default_builder_checks_machine_local_overlay(self) -> None:
        local = write(self.dot / "omp" / "models.local.yml", 'providers: {example: {apiKey: "!not-a-secret-command"}}\n')
        result = self.build("providers: {}\n")
        self.assertNotEqual(result.returncode, 0, "builder ignored models.local.yml")
        self.assertIn(str(local), result.stderr)
        self.assertFalse((self.agent / "models.yml").exists())

    def test_profile_merge_rejects_overlay_without_launch_or_replacement(self) -> None:
        out = write(self.profile / "models.yml", "previous output\n")
        result = self.launch('providers:\n  example:\n    apiKey: "!not-a-secret-command"\n')
        self.assertNotEqual(result.returncode, 0, "profile merge accepted a bare-name command")
        self.assertNotIn("launched", result.stdout)
        self.assertIn(str(self.profile / "models.overlay.yml") + ":3:", result.stderr)
        self.assertEqual(out.read_text(), "previous output\n")
        self.assertEqual(list(self.profile.glob("models.yml.new.*")), [])

    def test_profile_merge_checks_default_input_too(self) -> None:
        write(self.agent / "models.yml", 'providers: {example: {apiKey: "!not-a-secret-command"}}\n')
        write(self.profile / "models.overlay.yml", "providers: {}\n")
        result = subprocess.run(["bash", str(self.dot / "shims" / "omp"), "--profile", "guard-test"],
                                env=self.env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(str(self.agent / "models.yml"), result.stderr)
        self.assertNotIn("launched", result.stdout)

    def test_profile_rechecks_merged_bytes_if_overlay_changes_after_source_check(self) -> None:
        out = write(self.profile / "models.yml", "previous output\n")
        replacement = write(self.home / "replacement.yml", 'providers: {example: {apiKey: "!not-a-secret-command"}}\n')
        self.env.update(REAL_YQ=shutil.which("yq"), REPLACEMENT=str(replacement),
                        OVERLAY=str(self.profile / "models.overlay.yml"))
        write(self.home / "bin" / "yq", '''#!/bin/sh
if [ "$1" = eval-all ]; then
    cp "$REPLACEMENT" "$OVERLAY.new" && mv "$OVERLAY.new" "$OVERLAY" || exit 1
fi
exec "$REAL_YQ" "$@"
''', True)
        result = self.launch("providers: {}\n")
        self.assertNotEqual(result.returncode, 0, "unchecked merge bytes reached the profile")
        self.assertNotIn("launched", result.stdout)
        self.assertIn("absolute", result.stderr)
        self.assertEqual(out.read_text(), "previous output\n")
        self.assertEqual(list(self.profile.glob("models.yml.new.*")), [])

    def test_valid_profile_launch_has_identical_real_merge_bytes(self) -> None:
        overlay = 'providers: {example: {apiKey: "!/bin/false not-a-secret", headers: {X-Token: "!/bin/true"}}}\n'
        result = self.launch(overlay)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "launched\n")
        expected = subprocess.check_output(["yq", "eval-all", ". as $item ireduce ({}; . * $item)",
                                            str(self.agent / "models.yml"), str(self.profile / "models.overlay.yml")])
        self.assertEqual((self.profile / "models.yml").read_bytes(), expected)
        self.assertEqual((self.profile / "models.yml").stat().st_mode & 0o777, 0o600)

    def test_missing_key_file_fallback_has_absolute_command_and_reads_later_key(self) -> None:
        key = self.home / "key with ' quote"
        result = self.build('providers:\n  example:\n    apiKeyFile: "' + str(key) + '"\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        out = self.agent / "models.yml"
        checked = subprocess.run(["python3", str(CHECK), str(out)], capture_output=True, text=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        command = subprocess.check_output(["yq", "-r", ".providers.example.apiKey", str(out)], text=True).strip()
        write(key, "later-key\n")
        resolved = subprocess.run(["/bin/sh", "-c", command[1:]], capture_output=True, text=True,
                                  env=dict(self.env, PATH="/usr/bin:/bin"))
        self.assertEqual(resolved.returncode, 0, resolved.stderr)
        self.assertEqual(resolved.stdout, "later-key")


if __name__ == "__main__":
    unittest.main()
