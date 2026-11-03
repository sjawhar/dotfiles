#!/usr/bin/env python3
"""devbox/tmp-stale-reap ends each pass at its own deadline (dispatch://INFRA-672).

At load 500 on 2026-11-03 a pass ran from 08:16:48Z until systemd killed it at the unit's
30-minute TimeoutStartSec, 265 removals in: the listing and the /proc scan took 7 minutes and
each removal about 5 s. A pass now has a deadline (TMP_REAP_RUN_SECONDS). At it, the pass stops
on an entry boundary, says how far it got in one line, exits 0, and keeps a cursor; the next
pass starts at the cursor. Every disk step runs in a child under the time left, so one stalled
stat or unlink cannot carry the run past the deadline.

A stalled disk is played by shims first on PATH: a `find` or `rm` that sleeps on one path, and a
`timeout` that cannot end its child, as no signal ends a stat in D state. /proc is a scratch
directory too (TMP_REAP_PROC), so a test decides which process holds what.
"""

import os
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "devbox" / "tmp-stale-reap"
BUDGET = 15  # the deadline a test pass gets, in seconds
SLACK = 12  # what a loaded box may add: the cut step's 2 s, the cursor write, process starts
STALL = 60  # how long a stalled step takes when nothing stops it

STALLING = """#!/bin/sh
# A {tool} that stalls on the path in {var}, as one on a stalled disk does.
for arg in "$@"; do
    if [ -n "${var}" ] && [ "$arg" = "${var}" ]; then
        sleep {stall}
    fi
done
exec {real} "$@"
"""

UNKILLABLE_TIMEOUT = """#!/bin/sh
# A timeout whose child no signal ends, as none ends a stat in D state: it can only wait for it.
while [ $# -gt 0 ]; do
    case "$1" in
        -s | -k | --signal | --kill-after) shift 2 ;;
        -*) shift ;;
        *) break ;;
    esac
done
shift
exec "$@"
"""


class BoundedPassTest(unittest.TestCase):
    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix="tmp-stale-reap-test."))
        self.addCleanup(self.cleanup)
        self.root = self.work / "root"
        self.proc = self.work / "proc"
        self.state = self.work / "state"
        self.shims = self.work / "shims"
        for path in (self.root, self.proc, self.shims):
            path.mkdir()
        for tool, var in (("find", "REAP_TEST_STALL_FIND"), ("rm", "REAP_TEST_STALL_RM")):
            self.shim(self.shims, tool, STALLING.format(tool=tool, var=var, stall=STALL, real=shutil.which(tool)))
        self.groups = []
        self.runs = 0

    def cleanup(self):
        for group in self.groups:
            try:
                os.killpg(group, signal.SIGKILL)
            except ProcessLookupError:
                pass
        for here, dirs, _ in os.walk(self.work):
            for name in dirs:
                os.chmod(os.path.join(here, name), 0o700)
        shutil.rmtree(self.work, ignore_errors=True)

    def shim(self, where, tool, text):
        path = where / tool
        path.write_text(text)
        path.chmod(0o755)

    def entries(self, *names, settle=3):
        """Stale top-level directories, each with one file, older than the age window once
        SETTLE seconds have passed (the window reap() gives is 1.8 s)."""
        for name in names:
            (self.root / name).mkdir()
            (self.root / name / "data").write_text(name)
        time.sleep(settle)

    def hold(self, name, pid=4242):
        """A process holding NAME/data open, as /proc/<pid>/fd/3 shows it."""
        fd = self.proc / str(pid) / "fd"
        fd.mkdir(parents=True)
        (fd / "3").symlink_to(self.root / name / "data")
        return fd / "3"

    def reap(self, *args, path=None, **env):
        """One pass with stdout in a file, so a child left behind cannot hold the test."""
        self.runs += 1
        log_path = self.work / f"run-{self.runs}.log"
        environment = {
            **os.environ,
            "PATH": f"{path or self.shims}:{os.environ['PATH']}",
            "TMP_REAP_ROOT": str(self.root),
            "TMP_REAP_PROC": str(self.proc),
            "TMP_REAP_STATE": str(self.state),
            "TMP_REAP_AGE_HOURS": "0.0005",
            "TMP_REAP_PROBE_SECONDS": "120",
            "TMP_REAP_IO_PRESSURE_MAX": "1000",
            "TMP_REAP_RUN_SECONDS": str(BUDGET),
            **env,
        }
        with open(log_path, "w") as log:
            began = time.monotonic()
            child = subprocess.Popen(
                [str(SCRIPT), *(args or ("--apply",))],
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=environment,
                start_new_session=True,
            )
            self.groups.append(child.pid)
            try:
                status = child.wait(timeout=STALL + 45)
            except subprocess.TimeoutExpired:
                child.kill()
                status = child.wait()
            elapsed = time.monotonic() - began
        return status, log_path.read_text(), elapsed

    def stop_line(self, removals, checked, resumes, during=""):
        return (
            f"tmp-stale-reap: {self.root}: stopped at its deadline after {removals} removals,"
            f" {checked} entries checked; next pass resumes at {resumes}{during}"
        )

    def removed(self, out):
        return [line.split(" ")[2] for line in out.splitlines() if line.startswith("tmp-stale-reap: removed ")]

    def cursor(self):
        return (self.state / "cursor").read_text()

    def test_a_stalled_walk_stops_the_pass_at_its_deadline_and_the_next_pass_resumes_there(self):
        self.entries("a-held", "b-stale", "m-slow", "z-stale")
        holder = self.hold("a-held")

        status, out, elapsed = self.reap(REAP_TEST_STALL_FIND=str(self.root / "m-slow"))
        self.assertIn(self.stop_line(1, 2, self.root / "m-slow"), out)
        self.assertEqual(status, 0, out)
        self.assertLess(elapsed, BUDGET + SLACK, out)
        self.assertEqual(self.removed(out), [str(self.root / "b-stale")])
        self.assertTrue((self.root / "m-slow").exists())
        self.assertTrue((self.root / "z-stale").exists())
        self.assertEqual(self.cursor(), str(self.root / "m-slow"))

        # The holder has gone and the disk answers again: the next pass starts at the cursor,
        # goes on to the end, and wraps round to the entry before it.
        holder.unlink()
        status, out, _ = self.reap()
        self.assertEqual(status, 0, out)
        self.assertEqual(
            self.removed(out), [str(self.root / name) for name in ("m-slow", "z-stale", "a-held")]
        )
        self.assertIn(f"tmp-stale-reap: {self.root}: 3 removed", out)
        self.assertEqual(sorted(os.listdir(self.root)), [])
        self.assertEqual(self.cursor(), "")

    def test_a_stalled_removal_leaves_its_rest_for_the_next_pass_and_resumes_after_it(self):
        self.entries("a-held", "b-stale", "m-slow", "z-stale")
        holder = self.hold("a-held")

        status, out, elapsed = self.reap(REAP_TEST_STALL_RM=str(self.root / "m-slow"))
        self.assertIn(self.stop_line(1, 3, self.root / "z-stale"), out)
        self.assertEqual(status, 0, out)
        self.assertLess(elapsed, BUDGET + SLACK, out)
        remains = [name for name in os.listdir(self.root) if name.startswith(".tmp-stale-reap-removing.")]
        self.assertEqual(len(remains), 1, os.listdir(self.root))
        self.assertIn(
            f"the deadline cut the removal of {self.root / 'm-slow'}; what is left of it is now"
            f" {self.root / remains[0]}, which the next pass removes first",
            out,
        )
        self.assertFalse((self.root / "m-slow").exists())
        self.assertEqual(self.cursor(), str(self.root / "z-stale"))

        holder.unlink()
        status, out, _ = self.reap()
        self.assertEqual(status, 0, out)
        self.assertEqual(
            self.removed(out), [str(self.root / name) for name in (remains[0], "z-stale", "a-held")]
        )
        self.assertEqual(sorted(os.listdir(self.root)), [])

    def test_a_child_no_signal_ends_does_not_hold_the_pass_past_its_deadline(self):
        self.entries("a-held", "b-stale", "m-slow", "z-stale")
        self.hold("a-held")
        unkillable = self.work / "unkillable"
        unkillable.mkdir()
        self.shim(unkillable, "timeout", UNKILLABLE_TIMEOUT)

        status, out, elapsed = self.reap(
            path=f"{unkillable}:{self.shims}", REAP_TEST_STALL_FIND=str(self.root / "m-slow")
        )
        self.assertIn(self.stop_line(1, 2, self.root / "m-slow"), out)
        self.assertEqual(status, 0, out)
        self.assertLess(elapsed, BUDGET + SLACK, out)
        self.assertEqual(self.cursor(), str(self.root / "m-slow"))

    def test_a_stalled_listing_stops_before_any_entry_and_keeps_the_cursor(self):
        self.entries("a-stale", "m-stale", "z-stale")
        self.state.mkdir()
        (self.state / "cursor").write_text(str(self.root / "m-stale"))

        status, out, elapsed = self.reap(REAP_TEST_STALL_FIND=str(self.root))
        self.assertIn(
            self.stop_line(0, 0, self.root / "m-stale", f" (the deadline came during the listing of {self.root})"),
            out,
        )
        self.assertEqual(status, 0, out)
        self.assertLess(elapsed, BUDGET + SLACK, out)
        self.assertEqual(sorted(os.listdir(self.root)), ["a-stale", "m-stale", "z-stale"])
        self.assertEqual(self.cursor(), str(self.root / "m-stale"))

    def test_every_gate_still_holds_under_the_deadline(self):
        """The keeps of devbox/tmp-stale-reap.md's proof, with /proc a scratch directory. The
        window is 10.8 s here, so that the entry made just before each pass stays young."""
        window = {"TMP_REAP_AGE_HOURS": "0.003"}
        self.entries("held", "mapped", "envdir", "sockdir", "stale", "readonly", settle=0)
        (self.root / "readonly" / "sub").mkdir()
        (self.root / "readonly" / "sub" / "file").write_text("x")
        (self.root / "readonly" / "sub").chmod(0o555)
        os.mknod(self.root / "sockdir" / "s.sock", stat.S_IFSOCK | 0o600)
        (self.root / ".X11-unix").mkdir()
        (self.root / "alink").symlink_to("/nonexistent")
        self.hold("held")
        (self.proc / "77").mkdir()
        (self.proc / "77" / "maps").write_text(f"7f0000-7f1000 r-xp 00000000 00:00 0 {self.root}/mapped/data\n")
        (self.proc / "88").mkdir()
        (self.proc / "88" / "environ").write_bytes(f"PATH=/usr/bin\0TMPDIR={self.root}/envdir\0".encode())
        time.sleep(12)
        (self.root / "young").mkdir()

        status, out, _ = self.reap("--dry-run", **window)
        self.assertEqual(status, 0, out)
        for line in (
            f"keep {self.root}/.X11-unix (name matches the keep pattern '.X11-unix')",
            f"keep {self.root}/alink (a 'l', not a plain file or directory)",
            f"keep {self.root}/young (changed 0.0h ago, inside the 0.003h window)",
            f"keep {self.root}/held (live: pid 4242 via an open file)",
            f"keep {self.root}/mapped (live: pid 77 via a mapped file)",
            f"keep {self.root}/envdir (live: pid 88 via TMPDIR)",
            f"keep {self.root}/sockdir (it holds a unix socket: {self.root}/sockdir/s.sock)",
            f"would remove {self.root}/readonly",
            f"would remove {self.root}/stale",
        ):
            self.assertIn(f"tmp-stale-reap: {line}", out)
        self.assertFalse(self.state.exists(), "a hand run moved the cursor")

        (self.root / "young" / "file").write_text("x")
        status, out, _ = self.reap(**window)
        self.assertEqual(status, 0, out)
        self.assertEqual(self.removed(out), [f"{self.root}/readonly", f"{self.root}/stale"])
        self.assertEqual(
            sorted(os.listdir(self.root)),
            [".X11-unix", "alink", "envdir", "held", "mapped", "sockdir", "young"],
        )


if __name__ == "__main__":
    unittest.main()
