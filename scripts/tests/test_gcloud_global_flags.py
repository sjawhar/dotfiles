#!/usr/bin/env python3
"""scripts/gcloud-global-flags against a stub Cloud SDK.

The real SDK is never run: --sdk-root names a stub whose bundled "python3" prints
a flag table from a JSON file beside it, and PATH holds only a recording
directory, so no real gcloud is reachable from any invocation here.
"""
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GENERATOR = REPO / "scripts" / "gcloud-global-flags"
SHIM = REPO / "shims" / "gcloud"
FIXTURE = REPO / "shims" / "tests" / "gcloud_global_flags_589.json"

# Logs its own name and argv, so the test can see every command a run started.
RECORDER = """#!/bin/sh
printf '%s\\n' "${0##*/} $*" >> "$RECORD_LOG"
"""
# python3 on PATH: logged, then the real interpreter, which the preamble needs.
PYTHON_RECORDER = """#!/bin/sh
printf '%s\\n' "python3 $*" >> "$RECORD_LOG"
exec /usr/bin/python3 "$@"
"""
# The stub SDK's bundled interpreter: it ignores the extraction script it is
# handed and prints the parser table the test chose.
STUB_SDK_PYTHON = """#!/bin/sh
exec /bin/cat "${0%/*}/flags.json"
"""


def write_executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def fixture_nargs() -> dict[str, object]:
    """The SDK 589 table in the shape the SDK's parser reports: nargs per flag."""
    counts = json.loads(FIXTURE.read_text(encoding="utf-8"))["flags"]
    return {flag: (None if count == 1 else count) for flag, count in counts.items()}


class GcloudGlobalFlags(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.workdir = root / "work"
        self.workdir.mkdir()
        self.bin = root / "bin"
        self.log = root / "record.log"
        self.log.write_text("", encoding="utf-8")
        for name in ("gcloud", "gsutil", "bq"):
            write_executable(self.bin / name, RECORDER)
        write_executable(self.bin / "python3", PYTHON_RECORDER)
        self.sdk = root / "stub-sdk"
        (self.sdk / "VERSION").parent.mkdir(parents=True)
        (self.sdk / "VERSION").write_text("589.0.0\n", encoding="utf-8")
        write_executable(self.sdk / "platform" / "bundledpythonunix" / "bin" / "python3", STUB_SDK_PYTHON)
        self.set_parser(fixture_nargs())
        self.env = {"PATH": str(self.bin), "HOME": str(root), "RECORD_LOG": str(self.log)}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def set_parser(self, flags: dict[str, object]) -> None:
        (self.sdk / "platform" / "bundledpythonunix" / "bin" / "flags.json").write_text(
            json.dumps(flags), encoding="utf-8"
        )

    def run_generator(self, launcher: list[str], *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*launcher, str(GENERATOR), "--sdk-root", str(self.sdk), *args],
            capture_output=True,
            text=True,
            env=self.env,
            cwd=self.workdir,
        )

    def recorded(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").splitlines()

    def test_every_launcher_runs_python_and_nothing_else(self):
        """A shell handed the file runs only the preamble: one exec of python3."""
        for launcher in ([], ["/usr/bin/python3"], ["/bin/bash"], ["/bin/sh"], ["/usr/bin/dash"]):
            with self.subTest(launcher=launcher):
                self.log.write_text("", encoding="utf-8")
                result = self.run_generator(launcher)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("GCLOUD_GLOBAL_VALUE_FLAGS=(", result.stdout)
                self.assertNotIn("not found", result.stderr)
                commands = [line.split()[0] for line in self.recorded()]
                self.assertNotIn("gcloud", commands)
                self.assertEqual(set(commands) - {"python3"}, set())
                if launcher in (["/bin/bash"], ["/bin/sh"], ["/usr/bin/dash"]):
                    # The preamble's exec, with the file and every argument intact.
                    self.assertEqual(
                        self.recorded(),
                        [f"python3 {GENERATOR} --sdk-root {self.sdk}"],
                    )
                self.assertEqual(sorted(p.name for p in self.workdir.iterdir()), [])

    def test_file_holds_no_shell_command_substitution(self):
        text = GENERATOR.read_text(encoding="utf-8")
        self.assertNotIn("`", text)
        self.assertNotIn("$(", text)

    def test_json_mode_reports_each_flags_value_count(self):
        result = self.run_generator([], "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), json.loads(FIXTURE.read_text(encoding="utf-8")))

    def test_check_passes_when_the_stub_parser_matches_the_shim(self):
        result = self.run_generator([], "--check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("matches Cloud SDK 589.0.0", result.stdout)

    def test_check_names_each_drift_in_the_stub_parser(self):
        flags = fixture_nargs()
        flags["--authority-selector"] = 0  # a value flag turned boolean
        flags["--new-global"] = None  # a flag a later SDK added
        del flags["--trace-token"]  # a flag a later SDK dropped
        self.set_parser(flags)
        result = self.run_generator([], "--check")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("GCLOUD_GLOBAL_VALUE_FLAGS: missing --new-global", result.stderr)
        self.assertIn("GCLOUD_GLOBAL_BOOLEAN_FLAGS: missing --authority-selector", result.stderr)
        self.assertIn("--trace-token", result.stderr)

    def test_check_names_a_version_the_shim_does_not_cite(self):
        (self.sdk / "VERSION").write_text("590.0.0\n", encoding="utf-8")
        result = self.run_generator([], "--check")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("comment cites SDK 589.0.0, this SDK is 590.0.0", result.stderr)

    def test_a_flag_taking_two_values_is_refused(self):
        flags = fixture_nargs()
        flags["--pair"] = 2
        self.set_parser(flags)
        result = self.run_generator([])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--pair: takes 2 values", result.stderr)


if __name__ == "__main__":
    unittest.main()
