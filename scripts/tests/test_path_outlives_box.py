#!/usr/bin/env python3
"""path-outlives-box: a write's survival is read from the mount, never from the path.

The bug this guards against is not a crash: it is a confident wrong answer. On 2026-11-08
two lanes lost work kept at /mnt/nvme/... because that path is a host bind in a box that was
given a --dir for it and the box's own overlay in every other box, at the same moment, on the
same machine. A checker that decides from the path string is wrong for half the fleet, so each
case below drives the tool through a stub findmnt and asserts the verdict AND the exit status.
"""
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "path-outlives-box"


def run(findmnt_output, args, extra_env=None):
    """Run the tool with a stub findmnt that prints findmnt_output for any path."""
    with tempfile.TemporaryDirectory() as d:
        stub = Path(d) / "findmnt"
        stub.write_text("#!/usr/bin/env bash\nprintf '%s\\n' \"$FINDMNT_OUT\"\n")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        env = dict(os.environ)
        env["PATH"] = f"{d}:{env['PATH']}"
        env["FINDMNT_OUT"] = findmnt_output
        env.pop("AGENTBOX_BOX", None)
        env.update(extra_env or {})
        p = subprocess.run([str(TOOL)] + args, capture_output=True, text=True, env=env)
        return p


class PathOutlivesBox(unittest.TestCase):
    def test_host_disk_passes(self):
        """A real block device outlives the box: HOST, exit 0."""
        p = run("/dev/nvme47n1[/sami] xfs /mnt/durable/sami", ["/mnt/durable/sami/keep"])
        self.assertIn("HOST", p.stdout)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_overlay_is_box_private(self):
        """The case that cost two lanes their work: overlay means the write dies."""
        p = run("overlay overlay /", ["/mnt/nvme/sami/holdout"])
        self.assertIn("BOX", p.stdout)
        self.assertIn("DIES", p.stdout)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)

    def test_same_path_both_ways(self):
        """THE discriminating test: one path, two boxes, two answers.

        /mnt/nvme/sami/tmp was /dev/nvme46n1[/sami/tmp] xfs in agentbox-57869c9c891d and
        overlay in agentbox-ecdd9de2d619 at 15:49Z on 2026-11-08. A path-based checker
        cannot produce both, so this is what fails without reading the mount.
        """
        path = "/mnt/nvme/sami/tmp"
        bound = run("/dev/nvme46n1[/sami/tmp] xfs /mnt/nvme/sami/tmp", [path])
        private = run("overlay overlay /", [path])
        self.assertIn("HOST", bound.stdout)
        self.assertEqual(bound.returncode, 0)
        self.assertIn("BOX", private.stdout)
        self.assertEqual(private.returncode, 1)

    def test_local_ssd_is_not_safe(self):
        """A real device that is nonetheless empty after a machine stop must not read as safe."""
        p = run("/dev/md0 xfs /mnt/scratch", ["/mnt/scratch/sami/build"])
        self.assertIn("EPHEMERAL", p.stdout)
        self.assertIn("machine stop", p.stdout)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)

    def test_tmpfs_is_memory(self):
        p = run("tmpfs tmpfs /dev/shm", ["/dev/shm/x"])
        self.assertIn("MEMORY", p.stdout)
        self.assertEqual(p.returncode, 1)

    def test_names_the_box_it_dies_with(self):
        """'Dies with the box' is useless without saying which box."""
        p = run("overlay overlay /", ["/mnt/nvme/sami/x"],
                extra_env={"AGENTBOX_BOX": "agentbox-ecdd9de2d619"})
        self.assertIn("agentbox-ecdd9de2d619", p.stdout)

    def test_unresolvable_path_is_not_silently_safe(self):
        """findmnt printing nothing must never read as HOST."""
        p = run("", ["/no/such/path"])
        self.assertIn("UNKNOWN", p.stdout)
        self.assertNotIn("HOST", p.stdout)
        self.assertEqual(p.returncode, 1)

    def test_exit_is_worst_case_over_several_paths(self):
        """A handoff writer gates on the exit status, so one bad path must fail the run."""
        p = run("overlay overlay /", ["/a", "/b"])
        self.assertEqual(p.returncode, 1)

    def test_no_paths_is_usage_error(self):
        p = run("overlay overlay /", [])
        self.assertEqual(p.returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2) if "-v" in sys.argv else unittest.main()
