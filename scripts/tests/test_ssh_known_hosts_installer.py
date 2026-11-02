#!/usr/bin/env python3
"""installers/ssh-known-hosts.sh: ~/.ssh/known_hosts append-only, so no session's program can
write over the file every session shares.

On sami-rocqperf at 16:46Z on 2026-11-02 a test run with the real HOME wrote one github.com line
over the box's 73-line known_hosts (`printf ... > "$HOME/.ssh/known_hosts"`), and ssh failed in
every session until the file was restored. These tests run that same write against a file the
installer protected, in a HOME of their own. They need passwordless sudo and a file system with
the append-only attribute, so they skip without either.
"""

from __future__ import annotations

import getpass
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

INSTALLER = Path(__file__).resolve().parents[2] / "installers" / "ssh-known-hosts.sh"
GITHUB = "github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"
HOSTS = [f"host{i}.example ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBSMTE8mzzNzH5YkKIz4W0c{i}" for i in range(3)]
# What tests/py/test_sched_service.py ran through pool/job-run.sh's service_known_hosts.
WIPE = f"printf '%s\\n' '{GITHUB}' > \"$HOME/.ssh/known_hosts\""


def sudo_ok() -> bool:
    return subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0


def bash(script: str, home: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                          env=dict(os.environ, HOME=str(home)), check=False)


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ssh-known-hosts-"))
        self.home = self.tmp / "home"
        (self.home / ".ssh").mkdir(parents=True, mode=0o700)
        self.kh = self.home / ".ssh" / "known_hosts"
        (self.tmp / "etc").mkdir()  # the user's, so the root-owned drop-in in it can be removed
        self.dropin = self.tmp / "etc" / "50-known-hosts-append-only.conf"
        self.addCleanup(self.cleanup)

    def cleanup(self) -> None:
        if self.kh.exists() and sudo_ok():
            subprocess.run(["sudo", "chattr", "-a", str(self.kh)], check=False)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def install(self, path: str | None = None) -> subprocess.CompletedProcess:
        env = dict(os.environ, HOME=str(self.home), SSH_KNOWN_HOSTS_DROPIN=str(self.dropin))
        if path is not None:
            env["PATH"] = path
        return subprocess.run(["bash", str(INSTALLER)], capture_output=True, text=True, env=env, check=False)

    def append_only(self) -> bool:
        out = subprocess.run(["lsattr", "-d", str(self.kh)], capture_output=True, text=True).stdout
        return "a" in out.split(" ")[0]

    def lines(self) -> list[str]:
        return self.kh.read_text().splitlines()


@unittest.skipUnless(os.uname().sysname == "Linux" and sudo_ok(), "needs Linux and passwordless sudo")
class AppendOnly(Base):
    def setUp(self) -> None:
        super().setUp()
        probe = self.tmp / "probe"
        probe.touch()
        if subprocess.run(["sudo", "chattr", "+a", str(probe)], capture_output=True).returncode != 0:
            self.skipTest(f"{self.tmp} has no append-only attribute")
        subprocess.run(["sudo", "chattr", "-a", str(probe)], check=True)
        self.kh.write_text("".join(h + "\n" for h in HOSTS))
        self.kh.chmod(0o600)

    def test_the_wipe_is_refused_and_ssh_can_still_add_a_key(self) -> None:
        self.assertEqual(bash(WIPE, self.home).returncode, 0, "the reproduction does not wipe an open file")
        self.assertEqual(self.lines(), [GITHUB])
        self.kh.write_text("".join(h + "\n" for h in HOSTS))

        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.append_only())

        wipe = bash(WIPE, self.home)
        self.assertNotEqual(wipe.returncode, 0)
        self.assertIn("Operation not permitted", wipe.stderr)
        other = self.tmp / "other"
        other.write_text(GITHUB + "\n")
        for script in (f'mv "{other}" "$HOME/.ssh/known_hosts"', 'rm -f "$HOME/.ssh/known_hosts"',
                       ': > "$HOME/.ssh/known_hosts"', 'sed -i 1d "$HOME/.ssh/known_hosts"'):
            self.assertNotEqual(bash(script, self.home).returncode, 0, script)
        with self.assertRaises(PermissionError):
            open(self.kh, "w").close()
        self.assertEqual(self.lines(), HOSTS, "a line was lost")

        self.assertEqual(bash(f"printf '%s\\n' '{GITHUB}' >> \"$HOME/.ssh/known_hosts\"", self.home).returncode, 0)
        self.assertEqual(self.lines(), HOSTS + [GITHUB])

    def test_the_drop_in_turns_update_host_keys_off_for_this_user(self) -> None:
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        text = self.dropin.read_text()
        self.assertIn(f"Match localuser {getpass.getuser()}\n    UpdateHostKeys no\n", text)
        cfg = self.tmp / "ssh_config"
        for include, want in (("", "updatehostkeys true"), (f"Include {self.dropin}\n", "updatehostkeys false")):
            cfg.write_text(include)
            resolved = subprocess.run(["ssh", "-G", "-F", str(cfg), "host0.example"], capture_output=True, text=True)
            self.assertEqual(resolved.returncode, 0, resolved.stderr)
            self.assertIn(want, resolved.stdout.splitlines(), include or "no drop-in")

    def test_a_second_run_changes_nothing(self) -> None:
        self.assertEqual(self.install().returncode, 0)
        before = self.dropin.stat().st_mtime_ns
        again = self.install()
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(again.stdout, "")
        self.assertEqual(self.dropin.stat().st_mtime_ns, before)
        self.assertEqual(self.lines(), HOSTS)

    def test_a_missing_file_is_made_append_only(self) -> None:
        self.kh.unlink()
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.kh.read_text(), "")
        self.assertTrue(self.append_only())


class NoSudo(Base):
    def test_without_passwordless_sudo_nothing_changes(self) -> None:
        self.kh.write_text("".join(h + "\n" for h in HOSTS))
        fake = self.tmp / "bin"
        fake.mkdir()
        (fake / "sudo").write_text("#!/bin/sh\nexit 1\n")
        (fake / "sudo").chmod(0o755)
        result = self.install(path=f"{fake}:/usr/bin:/bin")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no passwordless sudo", result.stderr)
        self.assertFalse(self.dropin.exists())
        self.assertFalse(self.append_only())
        self.assertEqual(self.lines(), HOSTS)


if __name__ == "__main__":
    unittest.main()
