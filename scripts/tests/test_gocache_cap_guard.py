#!/usr/bin/env python3
"""devbox/gocache-cap never removes a Go build cache entry a running build may still read.

From 2026-11-15 08:17Z to 2026-11-16 04:18Z gocache-cap ran `go clean -cache` four times on
sami-devbox's shared cache while builds ran, and each run broke them with `link: cannot open file
…/gocache/…-d` (dispatch://INFRA-3905). Go sets an entry's mtime to now when a build uses it and
the mtime is over an hour old, so a `go` command that started at S has looked up nothing whose
mtime is older than S - 1 h. Every deletion must stay behind that line for the oldest running `go`
command (less a 10-minute margin), whatever the ages and sizes say; `go clean -cache` must never
run; and when the processes cannot be listed, nothing is deleted.

The `go` commands of every user count: an agent box's builds run on the host as uid 101000. A
zombie `go` does not: from 01:26Z on 2026-11-21 one in a container whose pid 1 reaps nothing held
the line at 22:06:36Z the day before, and every pass deleted nothing while the cache grew to 573 GB.

Technique: a `ps` stub first on PATH prints the columns asked for of a process table the test gives
it, all of them for `-e` and one user's for `-u`, so a test chooses which builds run and as whom; a
`go` stub records any call, so a test sees whether the cleaner ran Go. Entries are real files in a
scratch cache, aged with utime.
"""

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

DEVBOX = Path(__file__).resolve().parents[2] / "devbox"

# procps's ps, for the columns gocache-cap asks for: each `-o name=` column of the rows of
# STUB_PS_TABLE ("etimes pid uid stat comm" per line), every row for -e or -A, one uid's for -u UID.
# STUB_PS_RAW, when set, is printed as it is instead, for output ps should never give.
PS_STUB = r"""#!/bin/bash
rc="${STUB_PS_RC:-0}"
if [[ -n "${STUB_PS_RAW+set}" ]]; then
    printf '%s' "$STUB_PS_RAW"
    exit "$rc"
fi
uid="" fields=""
while (($#)); do
    case "$1" in
        -e | -A) uid="" ;;
        -u) uid="$2"; shift ;;
        -o) fields="$2"; shift ;;
    esac
    shift
done
IFS=, read -ra cols <<<"$fields"
while read -r etimes pid puid stat comm; do
    [[ -n "$etimes" ]] || continue
    [[ -z "$uid" || "$uid" == "$puid" ]] || continue
    out=()
    for col in "${cols[@]}"; do
        case "${col%=}" in
            etimes) out+=("$etimes") ;;
            pid) out+=("$pid") ;;
            uid) out+=("$puid") ;;
            stat) out+=("$stat") ;;
            comm) out+=("$comm") ;;
        esac
    done
    echo "${out[*]}"
done <<<"${STUB_PS_TABLE:-}"
exit "$rc"
"""

GO_STUB = r"""#!/bin/bash
echo "$*" >>"$STUB_GO_CALLS"
exit 1
"""

MIN = 60
HOUR = 3600
UID = os.getuid()
# An agent box's uid 1000 on the host, as Sysbox maps it on sami-devbox.
BOX_UID = 101000
# This script's own shell is always among the processes ps lists.
SELF = f"5 100 {UID} S bash\n"
# The stubbed host's uptime: above every age a test gives a running go command (15000 s at most)
# and below the wrapped age procps prints (about 4.1e9 s). gocache-cap reads an age above the
# uptime as wrapped, so the host's own uptime, copied, failed these tests on a machine up for less
# than about 4 hours, a fresh CI runner among them.
UPTIME = 30 * 24 * HOUR


def go(age: int, pid: int = 4242, uid: int = UID, stat: str = "Sl") -> str:
    return f"{age} {pid} {uid} {stat} go\n"


class Harness(unittest.TestCase):
    """A scratch tree with the ps and go stubs, and one Go build cache in it; runs gocache-cap there."""

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
        self.cache = self.make_cache(self.root / "gocache")
        # The stubbed processes' /proc, so a pid the stub lists never reads a real process's GOCACHE.
        self.proc = self.root / "proc"
        self.proc.mkdir()
        (self.proc / "uptime").write_text(f"{UPTIME}.00 {UPTIME}.00\n", encoding="utf-8")
        self.env = {"HOME": str(self.root), "PATH": f"{stubs}:{os.environ['PATH']}", "TMPDIR": str(self.root),
                    "STUB_GO_CALLS": str(self.go_calls), "GOCACHE_CAP_ROOTS": str(self.cache),
                    "PROC_ROOT": str(self.proc)}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def environ(self, pid: int, **variables: str) -> None:
        """Give the stubbed process PID these environment variables in the harness's /proc."""
        (self.proc / str(pid)).mkdir()
        (self.proc / str(pid) / "environ").write_bytes(
            b"".join(f"{k}={v}\0".encode() for k, v in variables.items()))

    @staticmethod
    def make_cache(path: Path) -> Path:
        """A directory as Go leaves a build cache: trim.txt, README and the 00-ff entry directories."""
        (path / "ab").mkdir(parents=True)
        (path / "trim.txt").write_text("1794800777", encoding="utf-8")
        (path / "README").write_text("This directory holds cached build artifacts from the Go build system.\n",
                                     encoding="utf-8")
        return path

    def entry(self, name: str, age: int, size: int = 100, cache: Path | None = None) -> Path:
        path = (cache or self.cache) / "ab" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)
        then = time.time() - age
        os.utime(path, (then, then))
        return path

    def run_cap(self, table: str = SELF, raw: str | None = None, ps_rc: str = "0",
                **settings: str) -> subprocess.CompletedProcess[str]:
        env = dict(self.env, STUB_PS_TABLE=table, STUB_PS_RC=ps_rc, **settings)
        if raw is not None:
            env["STUB_PS_RAW"] = raw
        return subprocess.run([str(DEVBOX / "gocache-cap")], capture_output=True, text=True, env=env, check=False,
                              timeout=60)

    def assert_go_never_ran(self) -> None:
        self.assertFalse(self.go_calls.exists(), "gocache-cap ran go: " +
                         (self.go_calls.read_text(encoding="utf-8") if self.go_calls.exists() else ""))


# Ages of 0 leave only the line the running builds draw.
NO_AGES = {"GOCACHE_TRIM_BYTES": "0", "GOCACHE_CAP_BYTES": "0", "GOCACHE_TRIM_AGE_MIN": "0",
           "GOCACHE_CAP_AGE_MIN": "0"}


class GocacheCapGuard(Harness):
    def test_keeps_every_entry_the_oldest_running_go_command_may_hold(self) -> None:
        # A go command running for 2 h may hold an entry it looked up at its start, whose mtime is
        # then up to 3 h old; the line is 3 h 10 min back.
        held = self.entry("held-d", 3 * HOUR)
        free = self.entry("free-d", 4 * HOUR, size=123)
        r = self.run_cap(SELF + go(7200), **NO_AGES)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(held.exists(), r.stdout)
        self.assertFalse(free.exists(), r.stdout)
        self.assertIn("pid 4242", r.stdout)
        self.assertIn("gocache-cap: reclaimed-bytes: 123\n", r.stdout)
        self.assert_go_never_ran()

    def test_a_go_command_of_another_user_holds_the_line_too(self) -> None:
        # An agent box's build runs as uid 101000 on the host and may read a cache this user caps.
        held = self.entry("held-d", 3 * HOUR)
        free = self.entry("free-d", 4 * HOUR)
        r = self.run_cap(SELF + go(7200, pid=777, uid=BOX_UID), **NO_AGES)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(held.exists(), r.stdout)
        self.assertFalse(free.exists(), r.stdout)
        self.assertIn("pid 777", r.stdout)

    def test_a_zombie_go_command_holds_nothing(self) -> None:
        # It has exited; only the running one, 30 min old, draws the line (1 h 40 min back).
        kept = self.entry("recent-d", 90 * MIN)
        gone = self.entry("older-d", 3 * HOUR)
        r = self.run_cap(SELF + go(15000, pid=1104810, stat="Z") + go(1800, pid=555), **NO_AGES)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(kept.exists(), r.stdout)
        self.assertFalse(gone.exists(), r.stdout)
        self.assertIn("pid 555", r.stdout)
        self.assertNotIn("1104810", r.stdout)

    def test_a_zombie_main_thread_with_live_threads_still_holds_the_line(self) -> None:
        held = self.entry("held-d", 3 * HOUR)
        r = self.run_cap(SELF + go(7200, pid=888, stat="Zl"), **NO_AGES)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(held.exists(), r.stdout)
        self.assertIn("pid 888", r.stdout)

    def test_with_no_go_command_running_keeps_the_last_hour_and_the_margin(self) -> None:
        kept = self.entry("recent-d", 69 * MIN)
        gone = self.entry("older-d", 71 * MIN)
        r = self.run_cap(**NO_AGES)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(kept.exists(), r.stdout)
        self.assertFalse(gone.exists(), r.stdout)
        self.assertIn("no go command was running", r.stdout)
        self.assert_go_never_ran()

    def test_under_the_cap_only_the_trim_age_is_deleted(self) -> None:
        warm = self.entry("warm-d", 170 * MIN)
        cold = self.entry("cold-d", 190 * MIN, size=7)
        r = self.run_cap(GOCACHE_TRIM_BYTES="0", GOCACHE_CAP_BYTES="1000000000")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(warm.exists(), r.stdout)
        self.assertFalse(cold.exists(), r.stdout)
        self.assertIn("deleted 7 bytes of entries unused for 180 min", r.stdout)
        self.assertIn("gocache-cap: reclaimed-bytes: 7\n", r.stdout)

    def test_over_the_cap_deletes_down_to_the_cap_age_and_no_further(self) -> None:
        warm = self.entry("warm-d", 110 * MIN)
        cooler = self.entry("cooler-d", 130 * MIN, size=11)
        cold = self.entry("cold-d", 190 * MIN, size=13)
        r = self.run_cap(GOCACHE_TRIM_BYTES="0", GOCACHE_CAP_BYTES="0")
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
        lane = self.make_cache(self.cache / "INFRA")
        held = self.entry("held-d", 3 * HOUR, cache=lane)
        free = self.entry("free-d", 4 * HOUR, cache=lane)
        r = self.run_cap(SELF + go(7200), **NO_AGES)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(held.exists(), r.stdout)
        self.assertFalse(free.exists(), r.stdout)

    def test_deletes_nothing_when_ps_fails(self) -> None:
        old = self.entry("old-d", 10 * HOUR)
        r = self.run_cap(ps_rc="1", GOCACHE_TRIM_BYTES="0", GOCACHE_CAP_BYTES="0")
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(old.exists(), r.stdout)

    def test_deletes_nothing_when_ps_lists_no_process(self) -> None:
        old = self.entry("old-d", 10 * HOUR)
        r = self.run_cap(raw="", GOCACHE_TRIM_BYTES="0", GOCACHE_CAP_BYTES="0")
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ps listed no process", r.stderr)
        self.assertTrue(old.exists(), r.stdout)

    def test_deletes_nothing_when_a_ps_line_cannot_be_read(self) -> None:
        old = self.entry("old-d", 10 * HOUR)
        r = self.run_cap(raw="5 100 S bash\n1-02:03:04 4242 S go\n", GOCACHE_TRIM_BYTES="0", GOCACHE_CAP_BYTES="0")
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("cannot read the age", r.stderr)
        self.assertTrue(old.exists(), r.stdout)

    def test_a_go_command_that_started_after_ps_read_the_clock_counts_as_new(self) -> None:
        # procps prints such a process's age wrapped to about 4.1e9 s. Taken as an age, it would
        # put the line before the epoch and the cleaner would delete nothing for as long as it ran.
        gone = self.entry("older-d", 2 * HOUR)
        r = self.run_cap(SELF + go(4123168608), **NO_AGES)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(gone.exists(), r.stdout)


if __name__ == "__main__":
    unittest.main()
