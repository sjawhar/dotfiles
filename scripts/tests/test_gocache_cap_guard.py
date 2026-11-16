#!/usr/bin/env python3
"""devbox/gocache-cap never removes a Go build cache entry a running build may still read.

From 2026-11-15 08:17Z to 2026-11-16 04:18Z gocache-cap ran `go clean -cache` four times on
sami-devbox's shared cache while builds ran, and each run broke them with `link: cannot open file
…/gocache/…-d` (dispatch://INFRA-3905). Go sets an entry's mtime to now when a build uses it and
the mtime is over an hour old, so a `go` command that started at S has looked up nothing whose
mtime is older than S - 1 h. Every deletion must stay behind that line for the oldest running `go`
command (less a 10-minute margin), whatever the ages and sizes say; `go clean -cache` must never
run; and when the processes cannot be listed, nothing is deleted.

Technique: a `ps` stub first on PATH prints the lines a test gives it, and a `go` stub records any
call, so a test chooses which builds run and sees whether the cleaner ran Go. Entries are real files
in a scratch cache, aged with utime.
"""

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

DEVBOX = Path(__file__).resolve().parents[2] / "devbox"

PS_STUB = r"""#!/bin/bash
printf '%s' "${STUB_PS_OUT:-}"
exit "${STUB_PS_RC:-0}"
"""

GO_STUB = r"""#!/bin/bash
echo "$*" >>"$STUB_GO_CALLS"
exit 1
"""

MIN = 60
HOUR = 3600
# This script's own shell is always among the processes ps lists.
SELF = "5 100 bash\n"


class GocacheCapGuard(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        stubs = self.root / "bin"
        stubs.mkdir()
        for name, text in (("ps", PS_STUB), ("go", GO_STUB)):
            stub = stubs / name
            stub.write_text(text, encoding="utf-8")
            stub.chmod(0o755)
        self.go_calls = self.root / "go-calls"
        self.cache = self.root / "gocache"
        (self.cache / "ab").mkdir(parents=True)
        (self.cache / "trim.txt").write_text("1794800777", encoding="utf-8")
        (self.cache / "README").write_text("This directory holds cached build artifacts from the Go build system.\n",
                                           encoding="utf-8")
        self.env = {"HOME": str(self.root), "PATH": f"{stubs}:{os.environ['PATH']}", "TMPDIR": str(self.root),
                    "STUB_GO_CALLS": str(self.go_calls), "GOCACHE_CAP_DIR": str(self.cache)}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def entry(self, name: str, age: int, size: int = 100, cache: Path | None = None) -> Path:
        path = (cache or self.cache) / "ab" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)
        then = time.time() - age
        os.utime(path, (then, then))
        return path

    def run_cap(self, ps_out: str, ps_rc: int = 0, **settings: str) -> subprocess.CompletedProcess:
        env = dict(self.env, STUB_PS_OUT=ps_out, STUB_PS_RC=str(ps_rc), **settings)
        return subprocess.run([str(DEVBOX / "gocache-cap")], capture_output=True, text=True, env=env, check=False,
                              timeout=60)

    def assert_go_never_ran(self) -> None:
        self.assertFalse(self.go_calls.exists(), "gocache-cap ran go: " +
                         (self.go_calls.read_text(encoding="utf-8") if self.go_calls.exists() else ""))

    def test_keeps_every_entry_the_oldest_running_go_command_may_hold(self) -> None:
        # A go command running for 2 h may hold an entry it looked up at its start, whose mtime is
        # then up to 3 h old; the line is 3 h 10 min back. Ages of 0 leave only that line.
        held = self.entry("held-d", 3 * HOUR)
        free = self.entry("free-d", 4 * HOUR, size=123)
        r = self.run_cap(SELF + "7200 4242 go\n", GOCACHE_TRIM_BYTES="0", GOCACHE_CAP_BYTES="0",
                         GOCACHE_TRIM_AGE_MIN="0", GOCACHE_CAP_AGE_MIN="0")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(held.exists(), r.stdout)
        self.assertFalse(free.exists(), r.stdout)
        self.assertIn("pid 4242", r.stdout)
        self.assertIn("gocache-cap: reclaimed-bytes: 123\n", r.stdout)
        self.assert_go_never_ran()

    def test_with_no_go_command_running_keeps_the_last_hour_and_the_margin(self) -> None:
        kept = self.entry("recent-d", 69 * MIN)
        gone = self.entry("older-d", 71 * MIN)
        r = self.run_cap(SELF, GOCACHE_TRIM_BYTES="0", GOCACHE_CAP_BYTES="0",
                         GOCACHE_TRIM_AGE_MIN="0", GOCACHE_CAP_AGE_MIN="0")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(kept.exists(), r.stdout)
        self.assertFalse(gone.exists(), r.stdout)
        self.assertIn("no go command was running", r.stdout)
        self.assert_go_never_ran()

    def test_under_the_cap_only_the_trim_age_is_deleted(self) -> None:
        warm = self.entry("warm-d", 170 * MIN)
        cold = self.entry("cold-d", 190 * MIN, size=7)
        r = self.run_cap(SELF, GOCACHE_TRIM_BYTES="0", GOCACHE_CAP_BYTES="1000000000")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(warm.exists(), r.stdout)
        self.assertFalse(cold.exists(), r.stdout)
        self.assertIn("deleted 7 bytes of entries unused for 180 min", r.stdout)
        self.assertIn("gocache-cap: reclaimed-bytes: 7\n", r.stdout)

    def test_over_the_cap_deletes_down_to_the_cap_age_and_no_further(self) -> None:
        warm = self.entry("warm-d", 110 * MIN)
        cooler = self.entry("cooler-d", 130 * MIN, size=11)
        cold = self.entry("cold-d", 190 * MIN, size=13)
        r = self.run_cap(SELF, GOCACHE_TRIM_BYTES="0", GOCACHE_CAP_BYTES="0")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(warm.exists(), r.stdout)
        self.assertFalse(cooler.exists(), r.stdout)
        self.assertFalse(cold.exists(), r.stdout)
        self.assertIn("still over the cap", r.stdout)
        self.assertIn("gocache-cap: reclaimed-bytes: 24\n", r.stdout)
        self.assertTrue((self.cache / "trim.txt").exists())
        self.assertTrue((self.cache / "README").exists())
        self.assert_go_never_ran()

    def test_a_lane_cache_inside_it_keeps_the_same_line(self) -> None:
        lane = self.cache / "INFRA"
        lane.mkdir()
        (lane / "trim.txt").write_text("1794800777", encoding="utf-8")
        held = self.entry("held-d", 3 * HOUR, cache=lane)
        free = self.entry("free-d", 4 * HOUR, cache=lane)
        r = self.run_cap(SELF + "7200 4242 go\n", GOCACHE_TRIM_BYTES="0", GOCACHE_CAP_BYTES="0",
                         GOCACHE_TRIM_AGE_MIN="0", GOCACHE_CAP_AGE_MIN="0")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(held.exists(), r.stdout)
        self.assertFalse(free.exists(), r.stdout)

    def test_deletes_nothing_when_ps_fails(self) -> None:
        old = self.entry("old-d", 10 * HOUR)
        r = self.run_cap(SELF, ps_rc=1, GOCACHE_TRIM_BYTES="0", GOCACHE_CAP_BYTES="0")
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(old.exists(), r.stdout)

    def test_deletes_nothing_when_ps_lists_no_process(self) -> None:
        old = self.entry("old-d", 10 * HOUR)
        r = self.run_cap("", GOCACHE_TRIM_BYTES="0", GOCACHE_CAP_BYTES="0")
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ps listed no process", r.stderr)
        self.assertTrue(old.exists(), r.stdout)

    def test_deletes_nothing_when_a_ps_line_cannot_be_read(self) -> None:
        old = self.entry("old-d", 10 * HOUR)
        r = self.run_cap(SELF + "1-02:03:04 4242 go\n", GOCACHE_TRIM_BYTES="0", GOCACHE_CAP_BYTES="0")
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("cannot read the age", r.stderr)
        self.assertTrue(old.exists(), r.stdout)

    def test_a_go_command_that_started_after_ps_read_the_clock_counts_as_new(self) -> None:
        # procps prints such a process's age wrapped to about 4.1e9 s. Taken as an age, it would
        # put the line before the epoch and the cleaner would delete nothing for as long as it ran.
        gone = self.entry("older-d", 2 * HOUR)
        r = self.run_cap(SELF + "4123168608 4242 go\n", GOCACHE_TRIM_BYTES="0", GOCACHE_CAP_BYTES="0",
                         GOCACHE_TRIM_AGE_MIN="0", GOCACHE_CAP_AGE_MIN="0")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(gone.exists(), r.stdout)


if __name__ == "__main__":
    unittest.main()
