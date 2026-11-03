#!/usr/bin/env python3
"""scripts/public-text-gate gives a gh command line to the shared public text check, and shims/gh
refuses the call when the check refuses it. A stub check records the argv it was given and returns
the code the test asks for, so these tests need no network and no real check on the machine. The
last pair of Gate tests covers a machine with no check at all: a read runs, a write is refused. The
Stdin tests cover the check's exit 4 (the call reads text from stdin): the shim reads stdin into an
unlinked private file, hands it to the check again as PUBLIC_LEAK_CHECK_STDIN=/dev/fd/N, and gives gh
the same bytes only when that second check passes."""

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
# A check that asks for stdin (exit 4) when no buffer is handed over, as the shared check does for `--input -`; with
# a buffer it records how the buffer was handed over and the bytes it read, then exits STUB_RC2.
STDIN_STUB = """#!/bin/bash
printf 'run pointer=%s\\n' "${PUBLIC_LEAK_CHECK_STDIN:-none}" >> "$STUB_LOG"
[ -n "${PUBLIC_LEAK_CHECK_STDIN:-}" ] || exit 4
[[ $PUBLIC_LEAK_CHECK_STDIN =~ ^/dev/fd/[0-9]+$ ]] && [ -f "$PUBLIC_LEAK_CHECK_STDIN" ] || exit 2
cat "$PUBLIC_LEAK_CHECK_STDIN" > "$STUB_READ"
exit ${STUB_RC2:-0}
"""
FAKE_GH = """#!/bin/bash
printf '%s\\n' "$*" >> "$GH_LOG"
[ -z "${GH_STDIN:-}" ] || cat > "$GH_STDIN"
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
            **{k: v for k, v in os.environ.items()
               if not k.startswith(("GIT_CONFIG_", "PUBLIC_LEAK_CHECK", "GH_READ_CACHE", "GH_SHIM_"))},
            "PATH": os.pathsep.join([str(self.bin), os.environ["PATH"]]),
            "HOME": str(self.tmp / "home"),
            "XDG_CONFIG_HOME": str(self.tmp / "config"),
            "XDG_CACHE_HOME": str(self.tmp / "cache"),
            "DOTFILES_DIR": str(DOTFILES),
            "STUB_LOG": str(self.stub_log),
            "GH_LOG": str(self.gh_log),
            "KNIVES_GH_BYPASS": "1",
            "OMP_SESSION_ID": SESSION,
            # The shim hands every call to scripts/gh-read-cache; without this its
            # entries and call log go to the machine's real cache.
            "GH_READ_CACHE_DIR": str(self.tmp / "gh-read-cache"),
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


class Once(Base):
    """The check runs once for each call that reaches gh, on the arguments gh runs with, and not at all for a read
    the host read cache answers, which sends nothing: the shim hands the call to the cache before the gates, and
    every call the cache makes re-enters the shim, which runs them. Until 2026-11-03 the gates also ran ahead of the
    cache, so every call ran the check twice (4.6 s p50 per run over 20 calls at load ~290)."""

    def checks(self):
        return self.stub_log.read_text().splitlines()

    def gh_calls(self):
        return self.gh_log.read_text().splitlines()

    def test_a_read_that_reaches_gh_is_checked_once(self):
        result = self.run_shim("pr", "view", "9", "-R", "o/r", check_path=str(self.stub))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.checks(), ["gh-guard pr view 9 -R o/r"])
        self.assertEqual(self.gh_calls(), ["pr view 9 -R o/r"])

    def test_a_write_is_checked_once(self):
        result = self.run_shim("pr", "comment", "9", "-R", "o/r", "--body", "x", check_path=str(self.stub))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.checks(), ["gh-guard pr comment 9 -R o/r --body x"])
        self.assertEqual(self.gh_calls(), ["pr comment 9 -R o/r --body x"])

    def test_the_check_reads_the_arguments_gh_runs_with(self):
        # A plain `gh api` read goes out with the cache's --include added; the check reads that call, not the one typed.
        result = self.run_shim("api", "repos/o/r", check_path=str(self.stub))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.gh_calls(), ["api repos/o/r --include"])
        self.assertEqual(self.checks(), ["gh-guard api repos/o/r --include"])

    def test_a_cache_hit_runs_no_check_and_sends_nothing(self):
        for _ in range(2):
            result = self.run_shim("pr", "view", "9", "-R", "o/r", "--json", "state", check_path=str(self.stub))
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.checks()), 1, self.checks())
        self.assertEqual(len(self.gh_calls()), 1, self.gh_calls())

    def test_a_refused_read_is_never_answered_from_the_cache(self):
        for _ in range(2):
            result = self.run_shim("pr", "view", "9", "-R", "o/r", "--json", "state", rc="3",
                                   check_path=str(self.stub))
            self.assertEqual(result.returncode, 1, "a read the check refused was answered")
        self.assertEqual(len(self.checks()), 2, self.checks())
        self.assertEqual(self.gh_calls(), [])

    def test_with_the_cache_off_the_check_still_runs_once_before_gh(self):
        env = {**self.env, "STUB_RC": "3", "PUBLIC_LEAK_CHECK": str(self.stub), "GH_READ_CACHE": "0"}
        argv = [str(GH), "pr", "comment", "9", "-R", "o/r", "--body", "x"]
        refused = subprocess.run(argv, env=env, capture_output=True, text=True, check=False)
        self.assertEqual(refused.returncode, 1)
        self.assertEqual(self.gh_calls(), [])
        env["STUB_RC"] = "0"
        passed = subprocess.run(argv, env=env, capture_output=True, text=True, check=False)
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertEqual(len(self.checks()), 2, self.checks())
        self.assertEqual(self.gh_calls(), ["pr comment 9 -R o/r --body x"])


class Stdin(Base):
    BODY = b'{"event": "COMMENT", "body": "Reviewed at abc123."}\n\x00binary\xff\n'

    def run_stdin(self, extra=None, data=BODY, rc2="0"):
        stub = self.bin / "stdin-check"
        stub.write_text(STDIN_STUB)
        stub.chmod(0o755)
        (self.tmp / "t").mkdir(exist_ok=True)
        env = {**self.env, "PUBLIC_LEAK_CHECK": str(stub), "STUB_RC2": rc2, "STUB_READ": str(self.tmp / "read"),
               "GH_STDIN": str(self.tmp / "gh-stdin"), "TMPDIR": str(self.tmp / "t"), **(extra or {})}
        result = subprocess.run([str(GH), "api", "--method", "POST", "repos/o/r/pulls/1/reviews", "--input", "-"],
                                env=env, input=data, capture_output=True, check=False)
        return result, self.stub_log.read_text().splitlines(), sorted((self.tmp / "t").iterdir())

    def test_shim_buffers_stdin_and_gh_gets_the_bytes_the_check_read(self):
        result, runs, left = self.run_stdin()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(runs[0], "run pointer=none")
        self.assertRegex(runs[1], r"^run pointer=/dev/fd/[0-9]+$")
        self.assertEqual((self.tmp / "read").read_bytes(), self.BODY)
        self.assertEqual((self.tmp / "gh-stdin").read_bytes(), self.BODY)
        self.assertEqual(left, [], "the shim left its stdin buffer behind")

    def test_shim_refuses_when_the_check_refuses_the_buffered_stdin(self):
        result, runs, left = self.run_stdin(rc2="3")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.gh_log.read_text(), "", "gh ran although the check refused the stdin")
        self.assertEqual(left, [])

    def test_shim_clears_a_stdin_pointer_its_caller_set(self):
        # A caller-set pointer to a file of clean text made the check read that file while gh sent the real stdin.
        decoy = self.tmp / "decoy"
        decoy.write_text("clean")
        result, runs, _ = self.run_stdin(extra={"PUBLIC_LEAK_CHECK_STDIN": str(decoy)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(runs[0], "run pointer=none")
        self.assertNotIn(str(decoy), "\n".join(runs))
        self.assertEqual((self.tmp / "read").read_bytes(), self.BODY)

    def test_shim_refuses_stdin_over_64_mib(self):
        result, runs, left = self.run_stdin(data=b"a" * (64 * 1024 * 1024 + 1))
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"over 64 MiB", result.stderr)
        self.assertEqual(self.gh_log.read_text(), "")
        self.assertEqual(len(runs), 1, "the check was run on stdin it cannot read whole")
        self.assertEqual(left, [])


if __name__ == "__main__":
    unittest.main()
