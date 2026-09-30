#!/usr/bin/env python3
"""installers/lib.sh arm_user_timer: link a user timer's units from ~/.dotfiles, start it, prove it.

Technique: `systemctl` and `systemd-detect-virt` are stubs first on PATH. The systemctl stub logs
each call and answers `list-timers` with $STUB_LISTED, so a test can make the timer register or
not; HOME is a scratch directory holding the ~/.dotfiles units.
"""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
LIB = DOTFILES / "installers" / "lib.sh"
IN_AGENTBOX = Path("/.dockerenv").exists()

SYSTEMCTL_STUB = r"""
echo "$*" >>"$STUB_LOG"
case "$*" in
    "--user enable --now "*) exit "${STUB_ENABLE_EXIT:-0}" ;;
    "--user list-timers --all "*) printf '%s' "$STUB_LISTED" ;;
    "--user show -p NextElapseUSecRealtime --value "*) echo "Thu 2026-10-01 00:00:00 UTC" ;;
esac
"""


def write_stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/bash\nset -euo pipefail\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class ArmUserTimer(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.home = root / "home"
        self.units = self.home / ".dotfiles" / "timers"
        self.units.mkdir(parents=True)
        stub_dir = root / "bin"
        stub_dir.mkdir()
        write_stub(stub_dir, "systemctl", SYSTEMCTL_STUB)
        write_stub(stub_dir, "systemd-detect-virt", 'echo "${STUB_VIRT:-none}"')
        self.log = root / "systemctl.log"
        self.user_units = self.home / ".config" / "systemd" / "user"
        self.env = {
            "PATH": f"{stub_dir}:/usr/bin:/bin",
            "HOME": str(self.home),
            # A workspace checkout: the links must still come from ~/.dotfiles.
            "DOTFILES_DIR": str(root / "workspace"),
            "STUB_LOG": str(self.log),
            "STUB_LISTED": "NEXT LEFT LAST PASSED UNIT ACTIVATES\n- - - - demo.timer demo.service\n",
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def with_units(self, *suffixes: str) -> None:
        for suffix in suffixes:
            (self.units / f"demo.{suffix}").write_text(f"[Unit]\nDescription=demo {suffix}\n")

    def arm(self, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "-c", 'set -euo pipefail; source "$1"; arm_user_timer timers demo', "arm", str(LIB)],
            capture_output=True,
            text=True,
            env={**self.env, **extra},
            check=False,
            timeout=30,
        )

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []

    @unittest.skipIf(IN_AGENTBOX, "inside an agentbox the helper refuses by design")
    def test_links_from_home_dotfiles_enables_and_proves_the_timer(self) -> None:
        self.with_units("service", "timer")
        result = self.arm()
        self.assertEqual(result.returncode, 0, result.stderr)
        for suffix in ("service", "timer"):
            link = self.user_units / f"demo.{suffix}"
            self.assertEqual(os.readlink(link), str(self.units / f"demo.{suffix}"))
        self.assertEqual(
            self.calls(),
            [
                "--user daemon-reload",
                "--user enable --now demo.timer",
                "--user list-timers --all demo.timer",
                "--user show -p NextElapseUSecRealtime --value demo.timer",
            ],
        )
        self.assertEqual(result.stdout, "demo: armed (Thu 2026-10-01 00:00:00 UTC)\n")

    def test_refuses_inside_an_agentbox(self) -> None:
        self.with_units("service", "timer")
        result = self.arm(STUB_VIRT="docker")
        self.assertEqual(result.returncode, 1)
        self.assertIn("agentbox", result.stderr)
        self.assertFalse(self.user_units.exists())
        self.assertEqual(self.calls(), [])

    @unittest.skipIf(IN_AGENTBOX, "inside an agentbox the helper refuses before it looks at the units")
    def test_refuses_before_linking_when_a_unit_is_missing(self) -> None:
        for present, missing in (("service", "timer"), ("timer", "service")):
            with self.subTest(missing=missing):
                for unit in self.units.iterdir():
                    unit.unlink()
                self.with_units(present)
                result = self.arm()
                self.assertEqual(result.returncode, 1)
                self.assertIn(str(self.units / f"demo.{missing}"), result.stderr)
                self.assertFalse(self.user_units.exists())
                self.assertEqual(self.calls(), [])

    @unittest.skipIf(IN_AGENTBOX, "inside an agentbox the helper refuses by design")
    def test_fails_when_the_timer_does_not_register(self) -> None:
        self.with_units("service", "timer")
        result = self.arm(STUB_LISTED="NEXT LEFT LAST PASSED UNIT ACTIVATES\n\n0 timers listed.\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("demo: timer did not register", result.stderr)
        self.assertNotIn("armed", result.stdout)

    @unittest.skipIf(IN_AGENTBOX, "inside an agentbox the helper refuses by design")
    def test_a_failed_step_fails_it_when_called_in_a_condition(self) -> None:
        # `|| exit 1` switches set -e off inside the function, so each step must return itself.
        self.with_units("service", "timer")
        result = subprocess.run(
            ["bash", "-c", 'set -euo pipefail; source "$1"; arm_user_timer timers demo || exit 1', "arm", str(LIB)],
            capture_output=True,
            text=True,
            env={**self.env, "STUB_ENABLE_EXIT": "4"},
            check=False,
            timeout=30,
        )
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("armed", result.stdout)
        self.assertNotIn("--user list-timers --all demo.timer", self.calls())

    @unittest.skipIf(IN_AGENTBOX, "inside an agentbox the helper refuses by design")
    def test_a_long_listing_after_the_row_still_proves_the_timer(self) -> None:
        # Piped into `grep -q`, the listing would be cut off at the row and systemctl, writing the
        # rest, would die of SIGPIPE; under pipefail a registered timer would then read as a failure.
        # 96 KB: past the 64 KB pipe buffer, under the 128 KB limit on one environment string.
        self.with_units("service", "timer")
        listed = self.env["STUB_LISTED"] + "footer line\n" * 8000
        result = self.arm(STUB_LISTED=listed)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("demo: armed", result.stdout)


if __name__ == "__main__":
    unittest.main()
