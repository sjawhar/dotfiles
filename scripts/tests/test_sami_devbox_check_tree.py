#!/usr/bin/env python3
"""Tests for devbox/sami-devbox-check's live-tree lines (dispatch://INFRA-993): data_copy_check judges
the stamp TOOLS's loop 6 writes (at most 2 h old; a note, neither ok nor FAIL, before
OSSV_DATA_COPY_FROM), and ossv_tree_host_check refuses an active ossv-tree-follow timer. The functions
are run from the real script, with the time to judge at passed in and a stand-in systemctl on PATH.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
SCRIPT = DOTFILES / "devbox" / "sami-devbox-check"
FROM = 1793752200  # 2026-11-04T00:30:00Z, the script's OSSV_DATA_COPY_FROM
HOUR = 3600


def utc(t: int) -> str:
    return subprocess.run(["date", "-u", "-d", f"@{t}", "+%Y-%m-%dT%H:%M:%SZ"], capture_output=True, text=True, check=True).stdout.strip()


class LiveTreeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.stamp = self.root / "ossv-data-copy.ok"
        self.bin = self.root / "bin"
        self.bin.mkdir()

    def run_fn(self, call: str, *args: str) -> subprocess.CompletedProcess:
        body = (f'source <(sed \'/^case "${{1:-}}" in/,$d\' {SCRIPT}); '
                f'OSSV_DATA_COPY_OK="$1"; shift; {call} "$@"; echo "fails=$fails"')
        env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}")
        return subprocess.run(["bash", "-c", body, "test", str(self.stamp), *args],
                              capture_output=True, text=True, env=env, timeout=60)

    def judge(self, now: int, stamp: str | None) -> str:
        if stamp is not None:
            self.stamp.write_text(stamp)
        out = self.run_fn("data_copy_check", str(now))
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout

    def test_fresh_stamp_is_ok_on_both_sides_of_the_date(self) -> None:
        for now in (FROM - HOUR, FROM + HOUR):
            out = self.judge(now, utc(now - 90 * 60) + "\n")
            self.assertRegex(out, r"^ok   data copy: .* 90 min ago", now)
            self.assertIn("fails=0", out)

    def test_before_the_date_a_missing_or_old_stamp_is_a_note(self) -> None:
        out = self.judge(FROM - 60, None)
        self.assertRegex(out, r"^note data copy: .*reads 'nothing'.*not expected yet")
        self.assertIn("fails=0", out)
        out = self.judge(FROM - 60, utc(FROM - 60 - 3 * HOUR) + "\n")
        self.assertRegex(out, r"^note data copy: .*not expected yet")
        self.assertIn("fails=0", out)

    def test_after_the_date_a_missing_old_or_garbled_stamp_fails(self) -> None:
        out = self.judge(FROM, None)
        self.assertRegex(out, r"^FAIL data copy: .*reads 'nothing'")
        self.assertIn("fails=1", out)
        out = self.judge(FROM + 60, utc(FROM + 60 - 2 * HOUR - 1) + "\n")
        self.assertRegex(out, r"^FAIL data copy: ")
        self.assertIn("fails=1", out)
        out = self.judge(FROM + 60, "not a time\n")
        self.assertRegex(out, r"^FAIL data copy: .*reads 'not a time'")
        self.stamp.write_text("")
        self.assertRegex(self.run_fn("data_copy_check", str(FROM + 60)).stdout, r"^FAIL data copy: .*reads 'nothing'")

    def test_an_active_follow_timer_fails(self) -> None:
        for state, line in (("active", r"(?m)^FAIL N6 tools: ossv-tree-follow.timer is active"),
                            ("inactive", r"(?m)^ok   N6 tools: no ossv-tree-follow timer runs \(is-active: inactive\)")):
            systemctl = self.bin / "systemctl"
            systemctl.write_text(f"#!/bin/sh\necho {state}\n[ {state} = active ]\n")
            systemctl.chmod(0o755)
            self.stamp.write_text(utc(FROM) + "\n")
            out = self.run_fn("ossv_tree_host_check")
            self.assertRegex(out.stdout, line)


if __name__ == "__main__":
    unittest.main()
