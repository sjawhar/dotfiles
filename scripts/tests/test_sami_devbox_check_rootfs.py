#!/usr/bin/env python3
"""Tests for devbox/sami-devbox-check's host_rootfs: A2's root-disk line.

The boot disk holds the OS only (dispatch://INFRA-993, "Shape and disks"). The line FAILs when /
uses over 40 GiB, when a directory on the root filesystem outside the OS's own (/usr, /var/lib/apt,
/var/cache/apt, /boot, /snap, /var/lib/snapd, /opt, /var/log) holds over 1 GiB of its own, naming
the deepest such directory, and when /var/log holds over 2 GiB. The function is run from the real
script, with `sudo`, `df` and `du` on PATH that print what each case gives.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
SCRIPT = DOTFILES / "devbox" / "sami-devbox-check"
GIB = 1024**3
FAKE_SUDO = """#!/bin/sh
[ "$1" = -n ] && shift
exec "$@"
"""
FAKE_DF = """#!/bin/sh
echo Used
cat "$FAKE_DIR/df"
"""
# du -x -B1 -0 /: the records of the case, its stderr, its exit code.
FAKE_DU = """#!/bin/sh
cat "$FAKE_DIR/du.out"
cat "$FAKE_DIR/du.err" >&2
exit "$(cat "$FAKE_DIR/du.rc")"
"""


class HostRootfsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        bin_dir = self.dir / "bin"
        bin_dir.mkdir()
        for name, text in (("sudo", FAKE_SUDO), ("df", FAKE_DF), ("du", FAKE_DU)):
            (bin_dir / name).write_text(text)
            (bin_dir / name).chmod(0o755)
        self.env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", FAKE_DIR=str(self.dir))

    def run_case(self, used: int, dirs: dict[str, int], err: str = "", rc: int = 0) -> tuple[int, str]:
        (self.dir / "df").write_text(f"{used}\n")
        (self.dir / "du.out").write_bytes(b"".join(f"{size}\t{path}".encode() + b"\0" for path, size in dirs.items()))
        (self.dir / "du.err").write_text(err)
        (self.dir / "du.rc").write_text(str(rc))
        body = f"source <(sed '/^case \"${{1:-}}\" in/,$d' {SCRIPT}); host_rootfs; (( fails == 0 ))"
        out = subprocess.run(["bash", "-c", body], env=self.env, capture_output=True, text=True, timeout=60)
        return out.returncode, out.stdout

    # A root like sami-devbox's on 2026-11-03 after the lane trees moved: 6.8G, most of it /usr.
    CLEAN = {"/usr": 6 * GIB, "/var/log": GIB // 2, "/var": GIB // 2 + GIB // 4, "/root": GIB // 10, "/": 7 * GIB}

    def test_os_only_root_passes(self) -> None:
        rc, out = self.run_case(7 * GIB, self.CLEAN)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.count("ok   root disk:"), 3, out)

    def test_a_big_directory_outside_the_os_fails_and_is_named(self) -> None:
        dirs = {"/theorem/build-logs/r17pool": 6 * GIB, "/theorem/build-logs": 8 * GIB, "/theorem": 9 * GIB, "/usr": 6 * GIB, "/": 16 * GIB}
        rc, out = self.run_case(16 * GIB, dirs)
        self.assertNotEqual(rc, 0, out)
        fail = [line for line in out.splitlines() if line.startswith("FAIL")]
        self.assertEqual(len(fail), 1, out)
        self.assertIn("/theorem/build-logs/r17pool 6.0G", fail[0])
        self.assertNotIn("/theorem ", fail[0])

    def test_bytes_only_in_os_directories_pass_however_large(self) -> None:
        rc, out = self.run_case(30 * GIB, {"/usr": 20 * GIB, "/opt": 5 * GIB, "/var/lib/snapd": 3 * GIB, "/var/lib": 3 * GIB, "/var": 3 * GIB, "/": 28 * GIB})
        self.assertEqual(rc, 0, out)

    def test_root_over_40_gib_fails(self) -> None:
        rc, out = self.run_case(41 * GIB, self.CLEAN)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("FAIL root disk: / uses", out)

    def test_var_log_over_2_gib_fails(self) -> None:
        rc, out = self.run_case(9 * GIB, dict(self.CLEAN, **{"/var/log": 3 * GIB, "/var": 3 * GIB, "/": 9 * GIB}))
        self.assertNotEqual(rc, 0, out)
        self.assertIn("FAIL root disk: /var/log holds 3.0G", out)

    def test_a_du_past_its_bound_fails(self) -> None:
        rc, out = self.run_case(7 * GIB, {}, rc=124)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("did not finish in 120 s", out)

    def test_paths_gone_during_the_walk_are_a_note(self) -> None:
        err = "du: cannot access '/var/tmp/x/y': No such file or directory\n"
        rc, out = self.run_case(7 * GIB, self.CLEAN, err=err, rc=1)
        self.assertEqual(rc, 0, out)
        self.assertIn("note root disk: du -x /: 1 path(s) went away", out)

    def test_any_other_du_error_fails(self) -> None:
        rc, out = self.run_case(7 * GIB, self.CLEAN, err="du: fts_read failed: Input/output error\n", rc=1)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("FAIL root disk: sudo du -x / | python3 exited 1 0", out)


if __name__ == "__main__":
    unittest.main()
