#!/usr/bin/env python3
"""installers/lib.sh disarm_user_timer: take back a user timer that arm_user_timer armed.

devbox/install.sh armed the seven disk cleaners' own timers and never box-cleanup.timer, which runs
all seven every 15 minutes; a re-run would have run each cleaner twice. It now arms box-cleanup and
disarms the seven, so a box that still has one loses it.

Technique: the stubs of test_arm_user_timer (`systemctl` logs each call, `systemd-detect-virt`
answers $STUB_VIRT); HOME is a scratch directory with the ~/.dotfiles units and the links
arm_user_timer would have made.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.tests.test_arm_user_timer import IN_AGENTBOX, LIB, SYSTEMCTL_STUB, write_stub

DOTFILES = Path(__file__).resolve().parents[2]


class DisarmUserTimer(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.home = root / "home"
        self.units = self.home / ".dotfiles" / "timers"
        self.units.mkdir(parents=True)
        for suffix in ("service", "timer"):
            (self.units / f"demo.{suffix}").write_text(f"[Unit]\nDescription=demo {suffix}\n")
        stub_dir = root / "bin"
        stub_dir.mkdir()
        write_stub(stub_dir, "systemctl", SYSTEMCTL_STUB)
        write_stub(stub_dir, "systemd-detect-virt", 'echo "${STUB_VIRT:-none}"')
        self.log = root / "systemctl.log"
        self.user_units = self.home / ".config" / "systemd" / "user"
        self.user_units.mkdir(parents=True)
        self.env = {"PATH": f"{stub_dir}:/usr/bin:/bin", "HOME": str(self.home), "STUB_LOG": str(self.log),
                    "STUB_LISTED": ""}

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def link(self, suffix: str, target: Path | None = None) -> Path:
        path = self.user_units / f"demo.{suffix}"
        path.symlink_to(target or self.units / f"demo.{suffix}")
        return path

    def disarm(self, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "-c", 'set -euo pipefail; source "$1"; disarm_user_timer timers demo', "disarm", str(LIB)],
            capture_output=True, text=True, env={**self.env, **extra}, check=False, timeout=30)

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []

    @unittest.skipIf(IN_AGENTBOX, "inside an agentbox the helper refuses by design")
    def test_disables_the_timer_and_removes_both_links(self) -> None:
        timer, service = self.link("timer"), self.link("service")
        result = self.disarm()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--user disable --now demo.timer", self.calls())
        self.assertEqual(self.calls()[-1], "--user daemon-reload")
        self.assertFalse(timer.is_symlink())
        self.assertFalse(service.is_symlink())
        self.assertIn("disarming it", result.stdout)
        self.assertTrue((self.units / "demo.timer").exists(), "the unit in ~/.dotfiles stays")

    @unittest.skipIf(IN_AGENTBOX, "inside an agentbox the helper refuses by design")
    def test_a_box_that_never_armed_it_changes_nothing(self) -> None:
        result = self.disarm()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((result.stdout, self.calls()), ("", []))

    @unittest.skipIf(IN_AGENTBOX, "inside an agentbox the helper refuses by design")
    def test_a_unit_of_the_same_name_from_elsewhere_is_left_alone(self) -> None:
        other = self.home / "elsewhere"
        other.mkdir()
        (other / "demo.timer").write_text("[Unit]\n")
        timer = self.link("timer", other / "demo.timer")
        result = self.disarm()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [])
        self.assertTrue(timer.is_symlink())

    @unittest.skipIf(IN_AGENTBOX, "inside an agentbox the helper refuses by design")
    def test_a_failed_disable_fails_it_and_keeps_the_links(self) -> None:
        timer = self.link("timer")
        stub = Path(self.env["PATH"].split(":")[0]) / "systemctl"
        stub.write_text(stub.read_text().replace('"--user enable --now "*)', '"--user disable --now "*) exit 1 ;;\n'
                                                 '    "--user enable --now "*)'))
        result = self.disarm()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(timer.is_symlink())

    def test_refuses_inside_an_agentbox(self) -> None:
        self.link("timer")
        result = self.disarm(STUB_VIRT="docker")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls(), [])


class DevboxInstallCleaners(unittest.TestCase):
    """devbox/install.sh arms box-cleanup.timer, and disarms the timer of every cleaner it runs."""

    def test_arms_box_cleanup_and_disarms_each_cleaner_it_runs(self) -> None:
        install = (DOTFILES / "devbox" / "install.sh").read_text()
        steps = re.findall(r'^\s*"([a-z0-9-]+)\|\d+\|', (DOTFILES / "devbox" / "box-cleanup").read_text(), re.M)
        # The steps that once ran from a timer of their own (the hook sweep's two never had one).
        timed = [s for s in steps if (DOTFILES / "devbox" / f"{s}.timer").exists()]
        self.assertEqual(len(timed), 7, timed)
        armed = re.findall(r"^\s*arm_user_timer devbox ([a-z0-9-]+)\s*$", install, re.M)
        self.assertIn("box-cleanup", armed)
        self.assertEqual([s for s in steps if s in armed], [], "a step box-cleanup runs has its own timer armed")
        loop = re.search(r"^for cleaner in (.*?); do\n\s*disarm_user_timer devbox \"\$cleaner\"\ndone$",
                         install, re.M | re.S)
        self.assertIsNotNone(loop, "no disarm loop over the cleaners")
        assert loop is not None
        self.assertEqual(sorted(loop.group(1).replace("\\\n", " ").split()), sorted(timed))


if __name__ == "__main__":
    unittest.main()
