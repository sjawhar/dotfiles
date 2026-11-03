#!/usr/bin/env python3
"""A box can `ssh sami-rocqperf` (agentbox/box-ssh-config).

A box holds the key and the pinned known_hosts for sami-rocqperf (agentbox/mounts) but had no ssh
entry naming the user, the port and those files, so `ssh sami-rocqperf` from a box connected as
the box user on port 22 and was refused (the cost tracking lead's box on sami-devbox,
2026-11-03 15:04Z). The entrypoint runs this at box start; every path it writes is the box's own
$HOME, never `~`, since the box's home is the host user's path.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "agentbox" / "box-ssh-config"


class BoxSshConfig(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="box-ssh-"))
        self.addCleanup(shutil.rmtree, self.home)
        (self.home / ".ssh").mkdir()

    def run_it(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(SCRIPT), str(self.home)], capture_output=True, text=True, timeout=60, check=False)

    def key(self, host: str = "sami-rocqperf", known_hosts: bool = True) -> None:
        (self.home / ".ssh" / host).write_text("key\n", encoding="utf-8")
        if known_hosts:
            (self.home / ".ssh" / f"known_hosts.{host}").write_text("host key\n", encoding="utf-8")

    def test_the_entry_names_the_user_the_port_and_the_box_s_own_paths(self) -> None:
        self.key()
        self.assertEqual(self.run_it().returncode, 0)
        conf = (self.home / ".ssh/config.d/sami-rocqperf.conf").read_text(encoding="utf-8")
        self.assertEqual(conf.splitlines(), [
            "Host sami-rocqperf",
            "  User sami",
            "  Port 443",
            f"  IdentityFile {self.home}/.ssh/sami-rocqperf",
            "  IdentitiesOnly yes",
            f"  UserKnownHostsFile {self.home}/.ssh/known_hosts.sami-rocqperf",
        ])
        self.assertNotIn("~", conf)
        self.assertEqual((self.home / ".ssh/config").read_text(encoding="utf-8").splitlines()[0],
                         f"Include {self.home}/.ssh/config.d/*.conf")
        self.assertEqual(oct((self.home / ".ssh/config.d/sami-rocqperf.conf").stat().st_mode)[-3:], "600")
        # `ssh -G` reads it the way ssh itself does.
        shown = subprocess.run(["ssh", "-F", str(self.home / ".ssh/config"), "-G", "sami-rocqperf"],
                               capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(shown.returncode, 0, shown.stderr)
        settings = dict(line.split(" ", 1) for line in shown.stdout.splitlines() if " " in line)
        self.assertEqual(settings.get("user"), "sami")
        self.assertEqual(settings.get("port"), "443")
        self.assertIn(f"{self.home}/.ssh/sami-rocqperf", shown.stdout)

    def test_a_machine_with_no_key_gets_no_entry_and_running_again_changes_nothing(self) -> None:
        self.key()
        self.assertEqual(self.run_it().returncode, 0)
        self.assertFalse((self.home / ".ssh/config.d/sami-leanperf.conf").exists())
        before = (self.home / ".ssh/config").read_text(encoding="utf-8")
        mine = (self.home / ".ssh/config.d/sami-rocqperf.conf")
        mine.write_text("Host sami-rocqperf\n  Port 2222\n", encoding="utf-8")
        self.assertEqual(self.run_it().returncode, 0)
        self.assertEqual(mine.read_text(encoding="utf-8"), "Host sami-rocqperf\n  Port 2222\n",
                         "an entry that is there already is never written over")
        self.assertEqual((self.home / ".ssh/config").read_text(encoding="utf-8"), before,
                         "the Include line is added once")

    def test_a_config_the_box_already_has_keeps_its_lines_under_the_include(self) -> None:
        (self.home / ".ssh/config").write_text("Host github.com\n  User git\n", encoding="utf-8")
        self.key("sami-leanperf", known_hosts=False)
        self.assertEqual(self.run_it().returncode, 0)
        lines = (self.home / ".ssh/config").read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], f"Include {self.home}/.ssh/config.d/*.conf")
        self.assertIn("Host github.com", lines)
        conf = (self.home / ".ssh/config.d/sami-leanperf.conf").read_text(encoding="utf-8")
        self.assertNotIn("UserKnownHostsFile", conf, "no known_hosts file, no line for it")


if __name__ == "__main__":
    unittest.main()
