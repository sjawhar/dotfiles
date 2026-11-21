#!/usr/bin/env python3
"""The session-tmp root of devbox/session-scratch-reap: each agent session's own TMPDIR.

On sami-devbox every box-cleanup pass from 2026-11-21T02:00Z failed this step on one directory,
/mnt/scratch/sami/session-tmp/943835-wuvEGAU1. Its session was gone, but a container that ran as
root had left tmp.X7DxElRLHB in it (root:root, mode 700). rm -rf took everything the user owned,
then stopped at `Permission denied`, and the step exited 1 on every 15-minute pass over a
directory this user can never remove. The two passes before that (00:06Z and 01:08Z) ran into
box-cleanup's 30-minute step limit while one rm -rf ran on.

The rules tested here:

  another user's entry at any depth keeps the     test_another_users_entry_keeps_the_whole_directory
  whole directory, named, and is not a failure
  the run says what it removed, and only that     test_reclaimed_bytes_is_what_this_run_removed
  a run stops at its own deadline, and the next   test_a_run_stops_at_its_deadline_and_the_next_finishes
  run finishes the job
  a filesystem mounted inside a directory keeps   test_a_mount_inside_keeps_the_directory_and_its_files
  it, and its files survive; a bind mount of the  test_a_bind_mount_of_the_same_filesystem_keeps_it
  same filesystem too, which rm's
  --one-file-system alone does not stop

The mount tests run the reaper in a user and mount namespace of its own (`unshare -rm`), where an
unprivileged user may mount a tmpfs or bind a directory, and read the mounted files from inside it.
"""

import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "devbox" / "session-scratch-reap"
# Above any pid_max (at most 2**22), so no process can have it: the directory's session is gone.
DEAD_PID = 999999990
HOUR = 3600
BUDGET = 15  # the deadline a test run gets, in seconds
SLACK = 12  # what a loaded box may add: the census, the /proc walk, process starts
STALL = 60  # how long a stalled rm takes when nothing stops it

STALLING_RM = """#!/bin/sh
# An rm that stalls on the path in REAP_TEST_STALL_RM, as one deep in a huge tree does.
for arg in "$@"; do
    if [ -n "$REAP_TEST_STALL_RM" ] && [ "$arg" = "$REAP_TEST_STALL_RM" ]; then
        sleep {stall}
    fi
done
exec {real} "$@"
"""


def sudo_works():
    return shutil.which("sudo") is not None and subprocess.run(
        ["sudo", "-n", "true"], capture_output=True, check=False
    ).returncode == 0


def unshare_works():
    """Can this user mount a tmpfs in a user and mount namespace of its own?"""
    if shutil.which("unshare") is None:
        return False
    return subprocess.run(
        ["unshare", "-rm", "sh", "-c", 'd=$(mktemp -d) && mount -t tmpfs none "$d" && umount "$d" && rmdir "$d"'],
        capture_output=True, check=False,
    ).returncode == 0


# Run inside `unshare -rm`: make the mount ("$MOUNT_SETUP"), run the reaper, then say whether the
# mounted file is still there, as the namespace and its mounts end with this shell.
IN_NAMESPACE = """
set -e
eval "$MOUNT_SETUP"
set +e
"$REAPER"
rc=$?
if [ -f "$MOUNTED_FILE" ]; then echo "MOUNTED FILE SURVIVED"; else echo "MOUNTED FILE GONE"; fi
exit $rc
"""


def tree_bytes(path):
    """What `find PATH -printf %s` adds up to: the directory itself and every entry below it."""
    total = os.lstat(path).st_size
    for here, dirs, files in os.walk(path):
        for name in dirs + files:
            total += os.lstat(os.path.join(here, name)).st_size
    return total


class SessionRootTest(unittest.TestCase):
    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix="session-scratch-reap-test."))
        self.addCleanup(shutil.rmtree, self.work, ignore_errors=True)
        self.root = self.work / "session-tmp"
        self.root.mkdir()

    def session(self, name, files):
        """A session's scratch directory holding FILES ({relative path: bytes}), an hour old."""
        path = self.root / name
        path.mkdir()
        for rel, data in files.items():
            (path / rel).parent.mkdir(parents=True, exist_ok=True)
            (path / rel).write_bytes(data)
        return path

    def age(self, path):
        when = time.time() - HOUR
        os.utime(path, (when, when))

    def run_reaper(self, *args, **env):
        return subprocess.run(
            [str(SCRIPT), *args],
            capture_output=True,
            text=True,
            timeout=300,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            env={
                **os.environ,
                "SESSION_SCRATCH_ROOT": str(self.root),
                "SESSION_SCRATCH_GRACE_SECONDS": "600",
                # This box's own IO pressure must not decide a test.
                "SESSION_SCRATCH_IO_PRESSURE_MAX": "100",
                **{key: str(value) for key, value in env.items()},
            },
        )

    def summary(self, out):
        for line in out.splitlines():
            if line.startswith(f"session-scratch-reap: {self.root}: ") and " made by hand, " in line:
                return line
        self.fail(f"no summary line in:\n{out}")

    def reclaimed(self, out):
        lines = [line for line in out.splitlines() if "reclaimed-bytes:" in line]
        self.assertEqual(len(lines), 1, out)
        return int(lines[0].rsplit(":", 1)[1])

    @unittest.skipUnless(sudo_works(), "needs `sudo -n` to make an entry another user owns")
    def test_another_users_entry_keeps_the_whole_directory(self):
        """The incident's tree: the user's own files and a root-owned, mode-700 directory."""
        dead = self.session(f"{DEAD_PID}-wuvEGAU1", {"work/notes.txt": b"x" * 100})
        foreign = dead / "tmp.X7DxElRLHB"
        sudo = ["sudo", "-n"]
        subprocess.run([*sudo, "install", "-d", "-m", "700", "-o", "root", "-g", "root", str(foreign)], check=True)
        self.addCleanup(subprocess.run, [*sudo, "rm", "-rf", "--", str(foreign)], check=False)
        subprocess.run([*sudo, "install", "-m", "755", "-o", "root", "-g", "root", "/dev/null",
                        str(foreign / "first-boot.sh")], check=True)
        self.age(dead)
        clean = self.session(f"{DEAD_PID + 1}-cleanAAA", {"data": b"y" * 10})
        self.age(clean)

        done = self.run_reaper()

        out = done.stdout + done.stderr
        self.assertEqual(done.returncode, 0, out)
        self.assertIn(f"keep {dead} (it holds another user's file: {foreign}, uid 0", done.stdout)
        self.assertTrue((dead / "work" / "notes.txt").exists(), "nothing of a kept directory is removed")
        self.assertNotIn("FAILED", out)
        self.assertFalse(clean.exists(), "the run still removes the dead directories it can")
        summary = self.summary(done.stdout)
        self.assertIn("1 holding another user's files", summary)
        self.assertIn("0 failed", summary)

    def test_reclaimed_bytes_is_what_this_run_removed(self):
        dead = self.session(f"{DEAD_PID}-dead0001", {"a/b/data": b"x" * 5000, "c": b"y" * 123})
        self.age(dead)
        held = self.session(f"{DEAD_PID + 1}-held0001", {"data": b"z" * 7000})
        self.age(held)
        holder = subprocess.Popen(["sleep", "300"], cwd=held)
        self.addCleanup(holder.wait)
        self.addCleanup(holder.kill)
        expected = tree_bytes(dead)

        dry = self.run_reaper("--dry-run")
        self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)
        self.assertEqual(self.reclaimed(dry.stdout), 0, "a dry run removes nothing, so it reclaims nothing")
        self.assertTrue(dead.exists())

        done = self.run_reaper()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn(f"removed {dead} (", done.stdout)
        self.assertFalse(dead.exists())
        self.assertIn(f"keep {held} (live: pid {holder.pid} via cwd)", done.stdout)
        self.assertEqual(self.reclaimed(done.stdout), expected, "only the removed directory counts")

    def test_a_run_stops_at_its_deadline_and_the_next_finishes(self):
        shims = self.work / "shims"
        shims.mkdir()
        rm = shims / "rm"
        rm.write_text(STALLING_RM.format(stall=STALL, real=shutil.which("rm")))
        rm.chmod(0o755)
        dead = self.session(f"{DEAD_PID}-huge0001", {"data": b"x" * 100})
        self.age(dead)

        start = time.monotonic()
        try:
            done = self.run_reaper(
                PATH=f"{shims}:{os.environ['PATH']}",
                REAP_TEST_STALL_RM=dead,
                SESSION_SCRATCH_RUN_SECONDS=BUDGET,
            )
        finally:
            elapsed = time.monotonic() - start
        out = done.stdout + done.stderr
        self.assertLess(elapsed, BUDGET + SLACK, f"the run went {elapsed:.0f}s past a {BUDGET}s deadline:\n{out}")
        self.assertEqual(done.returncode, 0, out)
        self.assertIn(f"stopped at its deadline during the removal of {dead}", done.stdout)
        self.assertNotIn(f"removed {dead}", done.stdout)
        self.assertEqual(self.reclaimed(done.stdout), 0)
        self.assertIn("0 failed", self.summary(done.stdout))

        again = self.run_reaper(SESSION_SCRATCH_RUN_SECONDS=BUDGET)
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn(f"removed {dead} (", again.stdout)
        self.assertFalse(dead.exists())

    def run_in_namespace(self, setup, mounted_file):
        return subprocess.run(
            ["unshare", "-rm", "sh", "-c", IN_NAMESPACE],
            capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL, start_new_session=True,
            env={**os.environ, "SESSION_SCRATCH_ROOT": str(self.root), "SESSION_SCRATCH_GRACE_SECONDS": "600",
                 "SESSION_SCRATCH_IO_PRESSURE_MAX": "100", "REAPER": str(SCRIPT), "MOUNT_SETUP": setup,
                 "MOUNTED_FILE": str(mounted_file)},
        )

    @unittest.skipUnless(unshare_works(), "needs `unshare -rm` to mount a tmpfs without root")
    def test_a_mount_inside_keeps_the_directory_and_its_files(self):
        dead = self.session(f"{DEAD_PID}-mount001", {"work/notes.txt": b"x" * 100, "mnt/.keep": b""})
        self.age(dead)
        clean = self.session(f"{DEAD_PID + 1}-cleanAAA", {"data": b"y" * 10})
        self.age(clean)
        mounted = dead / "mnt" / "data"

        done = self.run_in_namespace(f'mount -t tmpfs none "{dead}/mnt"; echo precious > "{mounted}"', mounted)

        out = done.stdout + done.stderr
        self.assertEqual(done.returncode, 0, out)
        self.assertIn("MOUNTED FILE SURVIVED", done.stdout)
        self.assertIn(f"keep {dead} (a filesystem is mounted inside it, at {dead}/mnt;", done.stdout)
        self.assertTrue((dead / "work" / "notes.txt").exists(), "nothing of a kept directory is removed")
        self.assertFalse(clean.exists(), "the run still removes the dead directories it can")
        self.assertNotIn("FAILED", out)
        summary = self.summary(done.stdout)
        self.assertIn("1 holding a mount", summary)
        self.assertIn("0 failed", summary)

    @unittest.skipUnless(unshare_works(), "needs `unshare -rm` to bind a directory without root")
    def test_a_bind_mount_of_the_same_filesystem_keeps_it(self):
        # A bind mount of a directory on the same disk has the same device, so rm's
        # --one-file-system goes through it; only the mount table shows it.
        precious = self.work / "precious"
        precious.mkdir()
        (precious / "data").write_text("precious\n")
        dead = self.session(f"{DEAD_PID}-bind0001", {"bound/.keep": b""})
        self.age(dead)

        done = self.run_in_namespace(f'mount --bind "{precious}" "{dead}/bound"', dead / "bound" / "data")

        out = done.stdout + done.stderr
        self.assertEqual(done.returncode, 0, out)
        self.assertIn("MOUNTED FILE SURVIVED", done.stdout)
        self.assertIn(f"keep {dead} (a filesystem is mounted inside it, at {dead}/bound;", done.stdout)
        self.assertTrue((precious / "data").exists(), "the bound directory's own file survives outside too")
        self.assertIn("1 holding a mount", self.summary(done.stdout))


if __name__ == "__main__":
    unittest.main()
