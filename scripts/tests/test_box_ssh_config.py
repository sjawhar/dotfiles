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

    def test_the_entry_is_written_system_wide_because_ssh_reads_the_passwd_home(self) -> None:
        """ssh reads the user config at the home the password database gives (getpwuid), which in
        a box is /home/ubuntu for the box user while $HOME is the host user's path. So the user
        file alone is never read: `ssh sami-rocqperf` from VERIFY's box went to port 22 and timed
        out (19:26Z, 2026-11-03). The same entry goes to /etc/ssh/ssh_config.d, which every user
        reads. Here the system directory is a temporary one, since a test writes nothing in /etc."""
        self.key()
        etc = self.home / "etc-ssh"
        (etc / "ssh_config.d").mkdir(parents=True)
        result = subprocess.run([str(SCRIPT), str(self.home)], capture_output=True, text=True, timeout=60,
                                check=False, env={**os.environ, "AGENTBOX_SSH_SYSTEM_DIR": str(etc / "ssh_config.d")})
        self.assertEqual(result.returncode, 0, result.stderr)
        system = etc / "ssh_config.d" / "50-agentbox-sami-rocqperf.conf"
        self.assertTrue(system.is_file(), list((etc / "ssh_config.d").iterdir()))
        self.assertEqual(system.read_text(encoding="utf-8"),
                         (self.home / ".ssh/config.d/sami-rocqperf.conf").read_text(encoding="utf-8"))
        shown = subprocess.run(["ssh", "-F", str(system), "-G", "sami-rocqperf"],
                               capture_output=True, text=True, timeout=60, check=False)
        self.assertIn("port 443", shown.stdout)

    def test_a_machine_with_no_key_is_reached_as_this_node_and_running_again_changes_nothing(self) -> None:
        """Tailscale SSH authenticates the node, so a machine this box holds no key for still
        gets an entry; its host key is taken on first contact and kept in the box's own file
        (`ssh sami-devbox` from VERIFY's box was refused on the host key, 19:26Z 2026-11-03)."""
        self.key()
        self.assertEqual(self.run_it().returncode, 0)
        devbox = (self.home / ".ssh/config.d/sami-devbox.conf").read_text(encoding="utf-8")
        self.assertEqual(devbox.splitlines(), [
            "Host sami-devbox",
            "  User sami",
            "  Port 22",
            f"  UserKnownHostsFile {self.home}/.ssh/known_hosts.agentbox",
            "  StrictHostKeyChecking accept-new",
        ])
        self.assertTrue((self.home / ".ssh/known_hosts.agentbox").exists())
        self.assertNotIn("IdentityFile", devbox)
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
        self.assertIn(f"UserKnownHostsFile {self.home}/.ssh/known_hosts.agentbox", conf)
        self.assertIn("IdentityFile", conf, "its key is mounted, so it is the only identity")


if __name__ == "__main__":
    unittest.main()
