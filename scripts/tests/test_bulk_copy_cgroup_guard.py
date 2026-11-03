#!/usr/bin/env python3
"""devbox/bulk-copy/cgroup-guard.sh keeps a copy's cgroup from dying with millions of inodes.

On sami-devbox (7.0.0-1014-gcp) on 2026-11-02 and 2026-11-03, a copy unit ended holding millions of
charged inodes. The kernel then has to move every inode of the dead cgroup's writeback to the parent,
and that move, before torvalds f6988c90671e, re-walks every queued inode on each pass. Every umount
hung behind it until a reset (dispatch://INFRA-993). The 11-03 unit had MemoryHigh=16G and ended by
itself, so the reclaim-before-stop in pull-unit.sh never ran.

The guard reclaims the cgroup while it is alive: as it goes when the slab passes a bound, and to
under a floor whenever its command ends, by itself or on a signal. The cgroup here is a scratch
directory (GUARD_CG). A `tee` shim first on PATH plays the kernel: each write to memory.reclaim is
logged, and the shim cuts memory.current and slab_reclaimable to a quarter.
"""

import os
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "devbox" / "bulk-copy" / "cgroup-guard.sh"
GIB = 1024**3
MIB = 1024**2

TEE_SHIM = r"""#!/usr/bin/env python3
# Plays memory.reclaim: log the request, then reclaim three quarters of the cgroup.
import os, sys
target = sys.argv[-1]
data = sys.stdin.read().strip()
if not target.endswith("memory.reclaim"):
    os.execvp("/usr/bin/tee", ["tee"] + sys.argv[1:])
d = os.path.dirname(target)
cur = int(open(os.path.join(d, "memory.current")).read())
with open(os.path.join(d, "reclaim.log"), "a") as f:
    f.write(f"{data} {cur}\n")
open(os.path.join(d, "memory.current"), "w").write(str(cur // 4))
lines = []
for line in open(os.path.join(d, "memory.stat")):
    k, v = line.split()
    if k in ("slab_reclaimable", "file"):
        v = str(int(v) // 4)
    lines.append(f"{k} {v}\n")
open(os.path.join(d, "memory.stat"), "w").writelines(lines)
"""


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cgroup-guard-"))
        self.cg = self.tmp / "cg"
        self.cg.mkdir()
        bin_ = self.tmp / "bin"
        bin_.mkdir()
        (bin_ / "tee").write_text(TEE_SHIM)
        (bin_ / "tee").chmod(0o755)
        (self.cg / "memory.reclaim").write_text("")
        self.env = dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}", GUARD_CG=str(self.cg),
                        GUARD_INTERVAL="1")
        self.set_memory(5 * GIB, slab=2 * GIB)

    def set_memory(self, current, slab):
        (self.cg / "memory.current").write_text(str(current))
        (self.cg / "memory.stat").write_text(f"file {current - slab}\nslab_reclaimable {slab}\n")

    def requests(self):
        log = self.cg / "reclaim.log"
        if not log.exists():
            return []
        return [tuple(map(int, l.split())) for l in log.read_text().splitlines()]

    def guard(self, *args, **kw):
        return subprocess.run([str(SCRIPT), *args], env=self.env, capture_output=True, text=True,
                              timeout=60, **kw)

    def test_drains_when_the_command_ends_by_itself(self):
        # The 11-03 case: the copy ended on its own, and nothing reclaimed before the cgroup died.
        self.set_memory(5 * GIB, slab=100 * MIB)  # under the slab bound: no reclaim while it runs
        r = self.guard("run", "--", "true")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLess(int((self.cg / "memory.current").read_text()), 256 * MIB)
        self.assertTrue(self.requests())
        self.assertIn("drained", r.stderr)

    def test_asks_for_what_the_cgroup_holds_never_more(self):
        # A request larger than what is left made the write retry for over 25 minutes on 11-03.
        self.guard("drain")
        reqs = self.requests()
        self.assertGreaterEqual(len(reqs), 2)
        for asked, held in reqs:
            self.assertEqual(asked, held)

    def test_keeps_the_command_exit_code(self):
        r = self.guard("run", "--", "sh", "-c", "exit 3")
        self.assertEqual(r.returncode, 3)
        self.assertLess(int((self.cg / "memory.current").read_text()), 256 * MIB)

    def test_the_command_reads_the_guard_stdin(self):
        # rsync --server under --rsync-path speaks its protocol on stdin; on 2026-11-03 a guard that
        # left the command /dev/null failed it with "error in rsync protocol data stream (code 12)".
        r = self.guard("run", "--", "cat", input="protocol bytes\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "protocol bytes\n")

    def test_exits_when_the_command_does_not_at_the_end_of_its_nap(self):
        # One rsync per small tree under a 30 s nap cost ~30 s per tree (910 trees = 7.5 h, 11-03).
        self.env["GUARD_INTERVAL"] = "120"
        t0 = time.monotonic()
        r = self.guard("run", "--", "true")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLess(time.monotonic() - t0, 60)

    def test_reclaims_while_running_when_the_slab_passes_the_bound(self):
        # 2 GiB of slab is over the 1 GiB bound (~800k inodes): reclaimed before the command ends.
        # The command waits up to 30 s for a reclaim, so a loaded box cannot fail it on timing.
        log = self.cg / "reclaim.log"
        r = self.guard("run", "--", "sh", "-c",
                       f"for i in $(seq 60); do [ -s {log} ] && exit 0; sleep 0.5; done; exit 1")
        self.assertEqual(r.returncode, 0, "no reclaim happened while the command ran: " + r.stderr)
        self.assertIn("slab_reclaimable", r.stderr)

    def test_no_write_when_already_small(self):
        self.set_memory(100 * MIB, slab=50 * MIB)
        r = self.guard("run", "--", "true")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.requests(), [])

    def test_term_passes_to_the_command_and_still_drains(self):
        # systemctl stop and a person's Ctrl-C both end the copy early; the cgroup must drain anyway.
        p = subprocess.Popen([str(SCRIPT), "run", "--", "sleep", "600"], env=self.env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(1.5)
        t0 = time.monotonic()
        p.send_signal(signal.SIGTERM)
        _, err = p.communicate(timeout=60)
        # Far under the 600 s the command would take; the drain's shim runs take ~3 s each at load 360.
        self.assertLess(time.monotonic() - t0, 45)
        self.assertEqual(p.returncode, 128 + signal.SIGTERM)
        self.assertIn("drained", err)
        self.assertLess(int((self.cg / "memory.current").read_text()), 256 * MIB)


if __name__ == "__main__":
    unittest.main()
