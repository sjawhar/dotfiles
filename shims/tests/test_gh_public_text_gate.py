#!/usr/bin/env python3
"""scripts/public-text-gate gives a gh command line to the shared public text check, and shims/gh
refuses the call when the check refuses it. A stub check records the argv it was given and returns
the code the test asks for, so these tests need no network and no real check on the machine. The
last pair of tests covers a machine with no check at all: a read runs, a write is refused."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SHIMS = Path(__file__).parents[1]
DOTFILES = SHIMS.parent
GATE = DOTFILES / "scripts" / "public-text-gate"
GH = SHIMS / "gh"
SESSION = "01a0fa64-b3a5-7415-b143-c2a338b5da8b"
STUB = """#!/bin/bash
printf '%s\\n' "$*" >> "$STUB_LOG"
exit ${STUB_RC:-0}
"""
FAKE_GH = """#!/bin/bash
printf '%s\\n' "$*" >> "$GH_LOG"
exit 0
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.stub = self.bin / "stub-check"
        self.stub.write_text(STUB)
        self.stub.chmod(0o755)
        self.stub_log = self.tmp / "stub.log"
        self.stub_log.touch()
        fake_gh = self.bin / "gh"
        fake_gh.write_text(FAKE_GH)
        fake_gh.chmod(0o755)
        self.gh_log = self.tmp / "gh.log"
        self.gh_log.touch()
        self.env = {
            **{k: v for k, v in os.environ.items() if not k.startswith(("GIT_CONFIG_", "PUBLIC_LEAK_CHECK"))},
            "PATH": os.pathsep.join([str(self.bin), os.environ["PATH"]]),
            "HOME": str(self.tmp / "home"),
            "XDG_CONFIG_HOME": str(self.tmp / "config"),
            "XDG_CACHE_HOME": str(self.tmp / "cache"),
            "DOTFILES_DIR": str(DOTFILES),
            "STUB_LOG": str(self.stub_log),
            "GH_LOG": str(self.gh_log),
            "KNIVES_GH_BYPASS": "1",
            "OMP_SESSION_ID": SESSION,
        }
        (self.tmp / "home").mkdir()
        (self.tmp / "config").mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_gate(self, *argv, rc="0", check_path=None):
        env = {**self.env, "STUB_RC": rc}
        if check_path is not None:
            env["PUBLIC_LEAK_CHECK"] = check_path
        return subprocess.run([str(GATE), *argv], env=env, capture_output=True, text=True, check=False)

    def run_shim(self, *argv, rc="0", check_path=None):
        env = {**self.env, "STUB_RC": rc}
        if check_path is not None:
            env["PUBLIC_LEAK_CHECK"] = check_path
        return subprocess.run([str(GH), *argv], env=env, capture_output=True, text=True, check=False)


class Gate(Base):
    def test_gate_hands_the_whole_command_line_to_the_check(self):
        result = self.run_gate("pr", "comment", "9", "-R", "o/r", "--body", "hello", check_path=str(self.stub))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.stub_log.read_text().strip(), "gh-guard pr comment 9 -R o/r --body hello")

    def test_gate_refuses_when_the_check_refuses(self):
        result = self.run_gate("pr", "comment", "9", "-R", "o/r", "--body", "x", rc="3", check_path=str(self.stub))
        self.assertNotEqual(result.returncode, 0)

    def test_gate_finds_the_check_on_path(self):
        on_path = self.bin / "public-leak-check.sh"
        shutil.copy(self.stub, on_path)
        result = self.run_gate("pr", "comment", "9", "-R", "o/r", "--body", "x")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("gh-guard pr comment", self.stub_log.read_text())

    def test_gate_finds_the_check_through_the_machine_pointer(self):
        pointer = Path(self.env["XDG_CONFIG_HOME"]) / "public-leak-check" / "path"
        pointer.parent.mkdir(parents=True)
        pointer.write_text(str(self.stub))
        result = self.run_gate("issue", "comment", "1", "-R", "o/r", "--body", "x")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("gh-guard issue comment", self.stub_log.read_text())

    def test_without_a_check_a_read_runs_and_a_write_is_refused(self):
        read = self.run_gate("pr", "view", "9", "-R", "o/r")
        self.assertEqual(read.returncode, 0, read.stderr)
        write = self.run_gate("pr", "comment", "9", "-R", "o/r", "--body", "x")
        self.assertEqual(write.returncode, 1)
        self.assertIn("no public text check", write.stderr)
        api = self.run_gate("api", "-X", "POST", "repos/o/r/issues/1/comments", "-f", "body=x")
        self.assertEqual(api.returncode, 1)
        self.assertEqual(self.stub_log.read_text(), "")


class Shim(Base):
    def test_shim_stops_a_refused_write_before_gh_runs(self):
        result = self.run_shim("pr", "comment", "9", "-R", "o/r", "--body", "x", rc="3", check_path=str(self.stub))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.gh_log.read_text(), "", "gh ran although the check refused the text")

    def test_shim_runs_gh_when_the_check_passes(self):
        result = self.run_shim("pr", "comment", "9", "-R", "o/r", "--body", "x", check_path=str(self.stub))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("pr comment 9 -R o/r --body x", self.gh_log.read_text())

    def test_shim_asks_the_check_about_an_api_write(self):
        result = self.run_shim("api", "-X", "POST", "repos/o/r/issues/1/comments", "-f", "body=x",
                               rc="3", check_path=str(self.stub))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.gh_log.read_text(), "")

    def test_shim_lets_a_read_through(self):
        result = self.run_shim("pr", "view", "9", "-R", "o/r", check_path=str(self.stub))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("pr view 9 -R o/r", self.gh_log.read_text())


if __name__ == "__main__":
    unittest.main()
