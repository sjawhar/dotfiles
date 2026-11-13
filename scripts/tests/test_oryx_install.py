#!/usr/bin/env python3
"""oryx/install.sh arms dotfiles-follow without changing forward or using Tailscale."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


DOTFILES = Path(__file__).resolve().parents[2]


SYSTEMCTL = r'''echo "$*" >>"$STUB_LOG"
case "$*" in
    "--user list-timers --all dotfiles-follow.timer")
        printf 'NEXT LEFT LAST PASSED UNIT ACTIVATES\nThu 1min - dotfiles-follow.timer dotfiles-follow.service\n'
        ;;
    "--user show -p NextElapseUSecRealtime --value dotfiles-follow.timer")
        echo 'Thu 2026-11-13 00:00:00 UTC'
        ;;
esac
'''


def write_command(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/bash\nset -euo pipefail\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class OryxInstall(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.home = root / "home"
        self.dotfiles = self.home / ".dotfiles"
        self.home.mkdir()
        shutil.copytree(
            DOTFILES,
            self.dotfiles,
            symlinks=True,
            ignore=shutil.ignore_patterns(".git", "__pycache__"),
        )
        self.installer = self.dotfiles / "oryx/install.sh"
        self.log = root / "systemctl.log"
        self.forward_log = root / "forward.log"
        fakebin = root / "bin"
        fakebin.mkdir()
        write_command(fakebin, "hostname", 'printf "%s\\n" "$STUB_HOSTNAME"')
        write_command(fakebin, "systemd-detect-virt", "exit 1")
        write_command(fakebin, "systemctl", SYSTEMCTL)
        write_command(fakebin, "forward", 'echo "$*" >>"$FORWARD_LOG"')
        for tool in ("dirname", "mkdir", "ln", "grep"):
            tool_path = shutil.which(tool)
            if tool_path is None:
                raise RuntimeError(f"test requires {tool}")
            (fakebin / tool).symlink_to(tool_path)
        self.env = {
            "HOME": str(self.home),
            "PATH": str(fakebin),
            "STUB_HOSTNAME": "oryx",
            "STUB_LOG": str(self.log),
            "FORWARD_LOG": str(self.forward_log),
        }
        self.forward_config = self.home / ".config/forward/config.toml"
        self.forward_config.parent.mkdir(parents=True)
        self.forward_bytes = b'listen = "127.0.0.1"\npeer = "127.0.0.1"\n'
        self.forward_config.write_bytes(self.forward_bytes)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def install(self, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/bash", str(self.installer)],
            env={**self.env, **extra},
            capture_output=True,
            text=True,
        )

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_arms_follow_without_tailscale_or_forward(self) -> None:
        self.assertIsNone(shutil.which("tailscale", path=self.env["PATH"]))

        result = self.install()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("dotfiles-follow: armed", result.stdout)
        dropin = self.home / ".config/systemd/user/dotfiles-follow.service.d/oryx.conf"
        self.assertTrue(dropin.is_symlink())
        self.assertEqual(dropin.resolve(), self.dotfiles / "oryx/dotfiles-follow.service.d/oryx.conf")
        for suffix in ("service", "timer"):
            unit = self.home / f".config/systemd/user/dotfiles-follow.{suffix}"
            self.assertTrue(unit.is_symlink())
            self.assertEqual(unit.resolve(), self.dotfiles / f"devbox/dotfiles-follow.{suffix}")
        self.assertEqual(self.calls(), [
            "--user daemon-reload",
            "--user enable --now dotfiles-follow.timer",
            "--user list-timers --all dotfiles-follow.timer",
            "--user show -p NextElapseUSecRealtime --value dotfiles-follow.timer",
        ])
        self.assertEqual(self.forward_config.read_bytes(), self.forward_bytes)
        self.assertFalse(self.forward_log.exists())

    def test_refuses_on_another_host_before_linking_or_arming(self) -> None:
        result = self.install(STUB_HOSTNAME="sami-devbox")

        self.assertEqual(result.returncode, 1)
        self.assertIn("runs only on oryx", result.stderr)
        self.assertFalse((self.home / ".config/systemd/user/dotfiles-follow.service.d/oryx.conf").exists())
        self.assertFalse((self.home / ".config/systemd/user/dotfiles-follow.service").exists())
        self.assertFalse((self.home / ".config/systemd/user/dotfiles-follow.timer").exists())
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.forward_config.read_bytes(), self.forward_bytes)


if __name__ == "__main__":
    unittest.main()
