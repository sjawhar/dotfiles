#!/usr/bin/env python3
"""installers/lib.sh arm_user_service: link a user service from ~/.dotfiles, start it, prove it runs,
and leave alone a service someone stopped on purpose.

Technique: the stubs of test_arm_user_timer (`systemctl` logs each call and answers the unit's state
from $STUB_ENABLED and $STUB_ACTIVE until an `enable --now` succeeds; `systemd-detect-virt` answers
$STUB_VIRT), and a `sleep` that returns at once; HOME is a scratch directory with the ~/.dotfiles unit.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.tests.test_arm_user_timer import IN_AGENTBOX, LIB, SYSTEMCTL_STUB, write_stub


class ArmUserService(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.home = root / "home"
        self.units = self.home / ".dotfiles" / "services"
        self.units.mkdir(parents=True)
        (self.units / "demo.service").write_text("[Unit]\nDescription=demo\n")
        stub_dir = root / "bin"
        stub_dir.mkdir()
        write_stub(stub_dir, "systemctl", SYSTEMCTL_STUB)
        write_stub(stub_dir, "systemd-detect-virt", 'echo "${STUB_VIRT:-none}"')
        write_stub(stub_dir, "sleep", "exit 0")
        self.log = root / "systemctl.log"
        self.user_units = self.home / ".config" / "systemd" / "user"
        self.env = {"PATH": f"{stub_dir}:/usr/bin:/bin", "HOME": str(self.home), "STUB_LOG": str(self.log)}

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def arm(self, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "-c", 'set -euo pipefail; source "$1"; arm_user_service services demo', "arm", str(LIB)],
            capture_output=True, text=True, env={**self.env, **extra}, check=False, timeout=30)

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []

    def link(self) -> Path:
        self.user_units.mkdir(parents=True, exist_ok=True)
        path = self.user_units / "demo.service"
        path.symlink_to(self.units / "demo.service")
        return path

    def reset(self) -> None:
        """Back to a machine with no link and no calls, for the next case."""
        shutil.rmtree(self.user_units, ignore_errors=True)
        for path in (self.log, Path(f"{self.log}.armed")):
            path.unlink(missing_ok=True)

    @unittest.skipIf(IN_AGENTBOX, "inside an agentbox the helper refuses by design")
    def test_a_service_someone_stopped_is_left_alone(self) -> None:
        # Linked and wanted by nothing, or a unit file in the search path that reads disabled; not
        # running, whether it stopped cleanly or failed.
        for enabled, active, linked in (("linked", "inactive", True), ("linked", "failed", True),
                                        ("disabled", "inactive", False)):
            with self.subTest(enabled=enabled, active=active):
                self.reset()
                if linked:
                    self.link()
                result = self.arm(STUB_ENABLED=enabled, STUB_ACTIVE=active)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("demo.service is", result.stdout)
                self.assertIn("someone stopped it on purpose", result.stdout)
                self.assertIn(f"systemctl --user enable --now {self.units}/demo.service", result.stdout)
                self.assertNotIn("demo: armed", result.stdout)
                self.assertEqual(self.calls(), ["--user is-active --quiet demo.service", "--user is-enabled demo.service"])
                self.assertEqual((self.user_units / "demo.service").is_symlink(), linked)

    @unittest.skipIf(IN_AGENTBOX, "inside an agentbox the helper refuses by design")
    def test_a_new_service_is_linked_started_and_proved(self) -> None:
        result = self.arm()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(os.readlink(self.user_units / "demo.service"), str(self.units / "demo.service"))
        self.assertEqual(self.calls(), [
            "--user is-active --quiet demo.service",
            "--user is-enabled demo.service",
            "--user daemon-reload",
            "--user enable --now demo.service",
            "--user is-active demo.service",
            "--user show -p MainPID --value demo.service",
        ])
        self.assertEqual(result.stdout, "demo: armed (running, 4242)\n")

    @unittest.skipIf(IN_AGENTBOX, "inside an agentbox the helper refuses by design")
    def test_an_enabled_or_running_service_is_armed_again(self) -> None:
        for enabled, active in (("enabled", "inactive"), ("enabled", "active"), ("linked", "active")):
            with self.subTest(enabled=enabled, active=active):
                self.reset()
                self.link()
                result = self.arm(STUB_ENABLED=enabled, STUB_ACTIVE=active)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("demo: armed", result.stdout)
                calls = self.calls()
                self.assertEqual(calls[0], "--user is-active --quiet demo.service", "the state is read before acting")
                self.assertIn("--user enable --now demo.service", calls)

    def test_refuses_inside_an_agentbox(self) -> None:
        result = self.arm(STUB_VIRT="docker")
        self.assertEqual(result.returncode, 1)
        self.assertIn("agentbox", result.stderr)
        self.assertFalse(self.user_units.exists())
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
