#!/usr/bin/env python3
"""A box's page cache is reclaimed just before agentbox stops the box (scripts/agentbox box_reclaim).

sami-devbox's 7.0 kernel lacks upstream f6988c90671e: a cgroup that dies holding many page-cache
inodes has each of them switched to another writeback context, one at a time, and every umount
and `agentbox new` waits behind that (02:27-02:40Z 2026-11-04, after two box closes at
02:18:48Z). So the stop paths write the box cgroup's memory.current into its memory.reclaim first.

Technique: source scripts/agentbox with a stub `docker` that answers the box's pid, a fixture
/proc/<pid>/cgroup naming a docker scope, and a fixture cgroup tree whose memory.reclaim is a
plain file the test reads back.
"""

from __future__ import annotations

import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
AGENTBOX = DOTFILES / "scripts" / "agentbox"


def write_stub(directory: Path, name: str, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class BoxReclaim(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        (self.home / ".dotfiles").symlink_to(DOTFILES)
        self.stubs = self.root / "bin"
        write_stub(self.stubs, "docker", 'case "$*" in *State.Pid*) echo 4242 ;; esac; exit 0')
        self.proc = self.root / "proc"
        (self.proc / "4242").mkdir(parents=True)
        self.cgroups = self.root / "cgroup"
        self.scope = self.cgroups / "user.slice" / "user-1000-agentbox.slice" / "docker-0123456789ab.scope"
        (self.scope / "init.scope").mkdir(parents=True)
        (self.proc / "4242" / "cgroup").write_text(
            "0::/user.slice/user-1000-agentbox.slice/docker-0123456789ab.scope/init.scope\n", encoding="utf-8")
        self.env = {
            "HOME": str(self.home),
            "PATH": f"{self.stubs}:/usr/bin:/bin",
            "AGENTBOX_PROC": str(self.proc),
            "AGENTBOX_CGROUP_ROOT": str(self.cgroups),
        }

    def reclaim(self, current: int) -> subprocess.CompletedProcess[str]:
        (self.scope / "memory.current").write_text(f"{current}\n", encoding="utf-8")
        (self.scope / "memory.reclaim").write_text("", encoding="utf-8")
        return subprocess.run(["bash", "-c", f'source "{AGENTBOX}"\nbox_reclaim agentbox-0123456789ab'],
                              capture_output=True, text=True, env=self.env, check=False, timeout=60)

    def test_a_box_holding_page_cache_is_reclaimed_at_its_own_scope(self) -> None:
        result = self.reclaim(6 * 1024 ** 3)
        self.assertEqual(result.returncode, 0, result.stderr)
        written = (self.scope / "memory.reclaim").read_text(encoding="utf-8")
        self.assertIn(str(6 * 1024 ** 3), written, "the box's whole current memory is asked back")
        self.assertFalse((self.scope / "init.scope" / "memory.reclaim").exists(),
                         "the container's own scope, never a cgroup nested inside it")

    def test_a_box_holding_little_is_left_alone_and_a_missing_box_is_no_error(self) -> None:
        result = self.reclaim(100 * 1024 ** 2)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.scope / "memory.reclaim").read_text(encoding="utf-8"), "")
        write_stub(self.stubs, "docker", "exit 1")
        gone = subprocess.run(["bash", "-c", f'source "{AGENTBOX}"\nbox_reclaim agentbox-gone'],
                              capture_output=True, text=True, env=self.env, check=False, timeout=60)
        self.assertEqual(gone.returncode, 0, gone.stderr)

    def test_every_stop_agentbox_drives_reclaims_first(self) -> None:
        """The launcher's hang-up and TERM traps, close_box's forced removal and a move's hang-up of
        a boxed session each call box_reclaim right before they stop the container."""
        text = AGENTBOX.read_text(encoding="utf-8")
        for stop in ('box_reclaim "$box"; docker kill -s SIGHUP "$box"',
                     'box_reclaim "$box"\n        docker rm -f "$box"',
                     'box_reclaim "${boxes[i]}"\n            docker kill -s SIGHUP "${boxes[i]}"'):
            self.assertIn(stop, text)


if __name__ == "__main__":
    unittest.main()
