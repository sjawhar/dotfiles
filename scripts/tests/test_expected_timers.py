#!/usr/bin/env python3
"""installers/expected-timers.sh: arm every timer devbox/expected-timers.txt names, except one someone
stopped on purpose, which it names, leaves alone and counts.

Technique: the stubs of test_arm_user_timer (`systemctl` logs each call and answers the unit's state
from $STUB_ENABLED and $STUB_ACTIVE until an `enable --now` succeeds); HOME is a scratch directory
whose ~/.dotfiles holds the list and the timer's units in omp/.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.tests.test_arm_user_timer import IN_AGENTBOX, SYSTEMCTL_STUB, write_stub

INSTALLER = Path(__file__).resolve().parents[2] / "installers" / "expected-timers.sh"


@unittest.skipIf(IN_AGENTBOX, "inside an agentbox the installer refuses by design")
class ExpectedTimers(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.home = root / "home"
        dotfiles = self.home / ".dotfiles"
        (dotfiles / "devbox").mkdir(parents=True)
        (dotfiles / "devbox" / "expected-timers.txt").write_text("demo.timer  # owner: the test\n")
        self.units = dotfiles / "omp"
        self.units.mkdir()
        for suffix in ("service", "timer"):
            (self.units / f"demo.{suffix}").write_text(f"[Unit]\nDescription=demo {suffix}\n")
        stub_dir = root / "bin"
        stub_dir.mkdir()
        write_stub(stub_dir, "systemctl", SYSTEMCTL_STUB)
        write_stub(stub_dir, "systemd-detect-virt", "echo none")
        self.log = root / "systemctl.log"
        self.user_units = self.home / ".config" / "systemd" / "user"
        self.env = {"PATH": f"{stub_dir}:/usr/bin:/bin", "HOME": str(self.home), "STUB_LOG": str(self.log),
                    "STUB_LISTED": "NEXT LEFT LAST PASSED UNIT ACTIVATES\n- - - - demo.timer demo.service\n"}

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def install(self, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", str(INSTALLER)], capture_output=True, text=True,
                              env={**self.env, **extra}, check=False, timeout=30)

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_a_timer_someone_stopped_is_left_alone_and_counted(self) -> None:
        # `systemctl --user disable --now demo.timer` took the timer's link and kept the service's.
        self.user_units.mkdir(parents=True)
        (self.user_units / "demo.service").symlink_to(self.units / "demo.service")
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("someone stopped it on purpose", result.stdout)
        self.assertIn("0 already armed, 0 armed now, 1 left alone, 0 could not be armed", result.stdout)
        self.assertNotIn("--user enable --now demo.timer", self.calls())
        self.assertFalse((self.user_units / "demo.timer").exists())

    def test_a_timer_of_this_user_manager_someone_stopped_is_left_alone(self) -> None:
        # A lane's timer whose unit files stand in ~/.config/systemd/user, not in ~/.dotfiles.
        for path in self.units.iterdir():
            path.unlink()
        self.user_units.mkdir(parents=True)
        (self.user_units / "demo.timer").write_text("[Timer]\nOnCalendar=hourly\n")
        result = self.install(STUB_ENABLED="disabled")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("someone stopped it on purpose", result.stdout)
        self.assertIn("1 left alone", result.stdout)
        self.assertNotIn("--user enable --now demo.timer", self.calls())

    def test_a_new_timer_is_armed(self) -> None:
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--user enable --now demo.timer", self.calls())
        self.assertIn("0 already armed, 1 armed now, 0 left alone, 0 could not be armed", result.stdout)


if __name__ == "__main__":
    unittest.main()
