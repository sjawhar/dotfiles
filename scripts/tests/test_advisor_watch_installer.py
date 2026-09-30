#!/usr/bin/env python3
"""installers/advisor-watch.sh: arming the daily AskGate readout.

The unit runs the shared checkout (`%h/.dotfiles`), so the installer checks that checkout and links
the unit files from it too, whichever checkout the installer itself runs from: a link into a
workspace dies when the workspace is forgotten, and the timer, the readout and every kill it would
apply die with it. It refuses to arm when that checkout's envoy predates `send --source`, which
would publish the readout to the topic "--source".

Technique: a temporary HOME whose .dotfiles holds the unit files, a stub advisor-report and an
envoy stub; stubs first on PATH for systemctl (records its argv, answers show-environment,
list-timers and show) and for the tools the unit needs. The installer runs from this checkout,
standing in for a scratch workspace.
"""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
INSTALLER = DOTFILES / "installers" / "advisor-watch.sh"
UNITS = ("advisor-watch.service", "advisor-watch.timer")

SYSTEMCTL_STUB = r"""
printf '%s\n' "$*" >> "$SYSTEMCTL_LOG"
case "$*" in
    "--user show-environment") echo "PATH=$STUB_BIN:/usr/bin:/bin" ;;
    "--user list-timers --all advisor-watch.timer") echo "Thu 2026-10-01 06:00:00 UTC 20h - - advisor-watch.timer advisor-watch.service" ;;
    "--user show -p NextElapseUSecRealtime --value advisor-watch.timer") echo "Thu 2026-10-01 06:00:00 UTC" ;;
esac
"""
# scripts/envoy as it is: `send --source` alone is refused naming the flag.
ENVOY_STUB = r"""
if [ "$*" = "send --source" ]; then echo "--source accepts only envoy" >&2; exit 2; fi
"""
# scripts/envoy before `--source` existed: `send --source` alone is a usage error.
ENVOY_WITHOUT_SOURCE = r"""
echo "Usage: envoy send <target> <message>" >&2
exit 1
"""


def write_stub(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/bash\nset -euo pipefail\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@unittest.skipIf(Path("/.dockerenv").exists(), "the installer refuses to run inside a container by design")
class AdvisorWatchInstaller(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.home = Path(self.temp_dir.name)
        self.shared = self.home / ".dotfiles"
        (self.shared / "omp").mkdir(parents=True)
        (self.shared / "scripts").mkdir()
        for unit in UNITS:
            (self.shared / "omp" / unit).write_text((DOTFILES / "omp" / unit).read_text(encoding="utf-8"), encoding="utf-8")
        write_stub(self.shared / "scripts" / "advisor-report", "exit 0")
        write_stub(self.shared / "scripts" / "envoy", ENVOY_STUB)
        stub_bin = self.home / "bin"
        stub_bin.mkdir()
        write_stub(stub_bin / "systemctl", SYSTEMCTL_STUB)
        for tool in ("python3", "curl", "jq"):
            write_stub(stub_bin / tool, "exit 0")
        self.systemctl_log = self.home / "systemctl.log"
        # Built from scratch: no DOTFILES_DIR, so lib.sh names this checkout, not the shared one.
        self.env = {
            "PATH": f"{stub_bin}:/usr/bin:/bin",
            "HOME": str(self.home),
            "STUB_BIN": str(stub_bin),
            "SYSTEMCTL_LOG": str(self.systemctl_log),
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def install(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", str(INSTALLER)], env=self.env, capture_output=True, text=True, check=False)

    def links(self) -> dict[str, str]:
        user_units = self.home / ".config" / "systemd" / "user"
        return {unit: os.readlink(user_units / unit) for unit in UNITS if (user_units / unit).is_symlink()}

    def systemctl_calls(self) -> str:
        return self.systemctl_log.read_text(encoding="utf-8") if self.systemctl_log.exists() else ""

    def test_run_from_another_checkout_links_the_shared_one(self) -> None:
        proc = self.install()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("advisor-watch: armed", proc.stdout)
        self.assertEqual(self.links(), {unit: str(self.shared / "omp" / unit) for unit in UNITS})
        self.assertIn("--user enable --now advisor-watch.timer", self.systemctl_calls())

    def test_an_envoy_without_source_arms_nothing(self) -> None:
        write_stub(self.shared / "scripts" / "envoy", ENVOY_WITHOUT_SOURCE)
        proc = self.install()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("has no 'send --source envoy'", proc.stderr)
        self.assertEqual(self.links(), {})
        self.assertNotIn("enable", self.systemctl_calls())

    def test_a_shared_checkout_without_the_units_arms_nothing(self) -> None:
        # advisor-report and envoy already there, the unit files not yet: linking would leave dangling links.
        (self.shared / "omp" / "advisor-watch.timer").unlink()
        proc = self.install()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("advisor-watch.timer is missing; advance ~/.dotfiles", proc.stderr)
        self.assertEqual(self.links(), {})
        self.assertNotIn("daemon-reload", self.systemctl_calls())
        self.assertNotIn("enable", self.systemctl_calls())


if __name__ == "__main__":
    unittest.main()
