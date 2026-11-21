#!/usr/bin/env python3
"""Tests for devbox/hook-sweep, the step of devbox/box-cleanup that runs one pass of the leak check's hook sweep.

The script loads the installed sweep-hooks.sh up to its `i=0` line and runs `sweep` once over its fast roots, or
its fast and slow roots, with the hooks directory as `here` and a private log. A stand-in sweep-hooks.sh in a
temporary HOOK_SWEEP_DIR records the roots it was given and writes the log lines FAKE_SWEEP names, then the summary.
The step must fail (so box-cleanup counts it) when the pass failed a checkout or wrote no summary, and must pass on
an opted-out or deferred checkout, which need no action from it.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
SCRIPT = DOTFILES / "devbox" / "hook-sweep"

FAKE_SWEEP = r"""#!/usr/bin/env bash
fast_roots=(/fast/a /fast/b)
slow_roots=(/slow/c)
sweep() {
  printf '%s\n' "$here" > "$here/here-was"
  printf '%s\n' "$@" > "$here/roots-were"
  [ "${FAKE_NO_SUMMARY:-}" = 1 ] && return 0
  local line
  while IFS= read -r line; do [ -z "$line" ] || echo "2026-11-21T00:00:00Z $line" >> "$log"; done <<<"${FAKE_SWEEP:-}"
  echo "2026-11-21T00:00:00Z sweep [$*]: 3 checkouts hooked or public or unreadable${FAKE_OPTED:-}, ${FAKE_DEFERRED:-0} deferred, 1 installed, ${FAKE_FAILED:-0} failed" >> "$log"
}
i=0
while :; do sleep 300; done
"""


class HookSweep(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="hook-sweep-test-"))
        self.hooks = self.tmp / "hooks"
        self.hooks.mkdir()
        (self.hooks / "sweep-hooks.sh").write_text(FAKE_SWEEP, encoding="utf-8")

    def tearDown(self) -> None:
        subprocess.run(["rm", "-rf", str(self.tmp)], check=False)

    def run_step(self, kind: str, **env: str) -> subprocess.CompletedProcess[str]:
        e = {"PATH": os.environ["PATH"], "HOME": str(self.tmp), "TMPDIR": str(self.tmp), "HOOK_SWEEP_DIR": str(self.hooks)}
        e.update(env)
        return subprocess.run(["bash", str(SCRIPT), kind], env=e, capture_output=True, text=True, timeout=60, check=False)

    def roots(self) -> list[str]:
        return (self.hooks / "roots-were").read_text(encoding="utf-8").split()

    def test_a_fast_pass_sweeps_the_fast_roots_with_the_hooks_directory_as_here(self) -> None:
        r = self.run_step("fast", FAKE_SWEEP="INSTALLED /fast/a/new")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.roots(), ["/fast/a", "/fast/b"])
        self.assertEqual((self.hooks / "here-was").read_text(encoding="utf-8").strip(), str(self.hooks))
        self.assertIn(" INSTALLED /fast/a/new", r.stdout)
        self.assertIn("reclaimed-bytes: 0", r.stdout)
        self.assertRegex(r.stdout, r"(?m) sweep \[/fast/a /fast/b\]: 3 checkouts.*, 0 failed$")

    def test_a_slow_pass_sweeps_the_fast_and_slow_roots(self) -> None:
        r = self.run_step("slow")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.roots(), ["/fast/a", "/fast/b", "/slow/c"])

    def test_a_failed_checkout_fails_the_step(self) -> None:
        r = self.run_step("fast", FAKE_SWEEP="FAILED /fast/b/x", FAKE_FAILED="1")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(" FAILED /fast/b/x", r.stdout)

    def test_a_pass_without_its_summary_fails_the_step(self) -> None:
        r = self.run_step("fast", FAKE_NO_SUMMARY="1")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("without its summary line", r.stderr)

    def test_opted_out_and_deferred_checkouts_pass_the_step(self) -> None:
        r = self.run_step("fast", FAKE_SWEEP="OPTED-OUT /fast/a/o (its core.hooksPath is /dev/null)\nDEFERRED /fast/b/d",
                          FAKE_OPTED=", 1 opted out (/fast/a/o)", FAKE_DEFERRED="1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(" OPTED-OUT /fast/a/o", r.stdout)
        self.assertIn(" DEFERRED /fast/b/d", r.stdout)

    def test_a_missing_sweep_and_a_bad_argument_are_usage_errors(self) -> None:
        (self.hooks / "sweep-hooks.sh").unlink()
        r = self.run_step("fast")
        self.assertEqual(r.returncode, 2)
        self.assertIn("sweep-hooks.sh is missing", r.stderr)
        self.assertEqual(self.run_step("medium").returncode, 2)

    def test_the_private_log_is_removed(self) -> None:
        self.run_step("fast")
        self.assertEqual(list(self.tmp.glob("hook-sweep-*")), [])


if __name__ == "__main__":
    unittest.main()
