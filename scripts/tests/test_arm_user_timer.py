#!/usr/bin/env python3
"""installers/lib.sh arm_user_timer: link a user timer's units from ~/.dotfiles, start it, prove it,
and leave alone a timer someone stopped on purpose.

Technique: `systemctl` and `systemd-detect-virt` are stubs first on PATH. The systemctl stub logs
each call, answers `list-timers` with $STUB_LISTED, so a test can make the timer register or not,
and answers the unit's state from $STUB_ENABLED (an is-enabled word) and $STUB_ACTIVE (an is-active
word) until an `enable --now` succeeds, then enabled and active. HOME is a scratch directory
holding the ~/.dotfiles units.
"""

from __future__ import annotations

import os
import shutil
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
enabled="${STUB_ENABLED:-not-found}" active="${STUB_ACTIVE:-inactive}"
if [[ -e "$STUB_LOG.armed" ]]; then enabled=enabled active=active; fi
case "$*" in
    "--user enable --now "*)
        [[ "${STUB_ENABLE_EXIT:-0}" != 0 ]] || : >"$STUB_LOG.armed"
        exit "${STUB_ENABLE_EXIT:-0}" ;;
    "--user is-enabled "*) [[ "$*" == *--quiet* ]] || echo "$enabled"; [[ "$enabled" == enabled ]] ;;
    "--user is-active "*) [[ "$*" == *--quiet* ]] || echo "$active"; [[ "$active" == active ]] ;;
    "--user list-timers --all "*) printf '%s' "$STUB_LISTED" ;;
    "--user show -p NextElapseUSecRealtime --value "*) echo "Thu 2026-10-01 00:00:00 UTC" ;;
    "--user show -p MainPID --value "*) echo 4242 ;;
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

    def link(self, suffix: str) -> None:
        self.user_units.mkdir(parents=True, exist_ok=True)
        (self.user_units / f"demo.{suffix}").symlink_to(self.units / f"demo.{suffix}")

    def reset(self) -> None:
        """Back to a machine with no links and no calls, for the next case."""
        shutil.rmtree(self.user_units, ignore_errors=True)
        for path in (self.log, Path(f"{self.log}.armed")):
            path.unlink(missing_ok=True)

    def links(self) -> list[str]:
        return sorted(path.name for path in self.user_units.iterdir()) if self.user_units.exists() else []

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
                "--user is-active --quiet demo.timer",
                "--user is-enabled demo.timer",
                "--user daemon-reload",
                "--user enable --now demo.timer",
                "--user list-timers --all demo.timer",
                "--user show -p NextElapseUSecRealtime --value demo.timer",
            ],
        )
        self.assertEqual(result.stdout, "demo: armed (Thu 2026-10-01 00:00:00 UTC)\n")

    @unittest.skipIf(IN_AGENTBOX, "inside an agentbox the helper refuses by design")
    def test_a_timer_someone_stopped_is_left_alone(self) -> None:
        # Measured on systemd 255: `systemctl --user disable --now demo.timer` removes the timer's
        # own link, since it points outside the unit search path, and keeps the service's link; the
        # timer then reads not-found, and failed right after the stop. A timer linked but wanted by
        # nothing reads linked; one whose unit file stands in the search path reads disabled.
        self.with_units("service", "timer")
        cases = (
            ("not-found", "inactive", ("service",)),
            ("not-found", "failed", ("service",)),
            ("linked", "inactive", ("service", "timer")),
            ("disabled", "inactive", ()),
        )
        for enabled, active, links in cases:
            with self.subTest(enabled=enabled, active=active, links=links):
                self.reset()
                for suffix in links:
                    self.link(suffix)
                result = self.arm(STUB_ENABLED=enabled, STUB_ACTIVE=active)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("demo.timer is", result.stdout)
                self.assertIn("someone stopped it on purpose", result.stdout)
                self.assertIn(f"systemctl --user enable --now {self.units}/demo.timer", result.stdout)
                self.assertNotIn("demo: armed", result.stdout)
                self.assertEqual(self.calls(), ["--user is-active --quiet demo.timer", "--user is-enabled demo.timer"])
                self.assertEqual(self.links(), sorted(f"demo.{suffix}" for suffix in links))

    @unittest.skipIf(IN_AGENTBOX, "inside an agentbox the helper refuses by design")
    def test_an_enabled_or_running_timer_is_armed_again(self) -> None:
        self.with_units("service", "timer")
        for enabled, active in (("enabled", "inactive"), ("enabled", "active"), ("linked", "active")):
            with self.subTest(enabled=enabled, active=active):
                self.reset()
                self.link("service")
                self.link("timer")
                result = self.arm(STUB_ENABLED=enabled, STUB_ACTIVE=active)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("demo: armed", result.stdout)
                calls = self.calls()
                self.assertEqual(calls[0], "--user is-active --quiet demo.timer", "the state is read before acting")
                self.assertIn("--user enable --now demo.timer", calls)

    @unittest.skipIf(IN_AGENTBOX, "inside an agentbox the helper refuses by design")
    def test_a_new_timer_beside_a_service_link_of_another_unit_is_armed(self) -> None:
        # Only a service link that is this repository's unit says the timer was armed here before.
        self.with_units("service", "timer")
        other = self.home / "elsewhere"
        other.mkdir()
        (other / "demo.service").touch()
        self.user_units.mkdir(parents=True)
        (self.user_units / "demo.service").symlink_to(other / "demo.service")
        result = self.arm()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("someone stopped it on purpose", result.stdout)
        self.assertIn("demo: armed", result.stdout)
        self.assertIn("--user is-enabled demo.timer", self.calls())

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
