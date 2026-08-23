#!/usr/bin/env python3
"""Regression coverage for forward roles with no ambient relay environment."""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
INSTALLER = DOTFILES / "installers" / "forward.sh"
BASHRC = DOTFILES / ".bashrc"


def write_executable(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/bash\nset -euo pipefail\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class ForwardServeEnvironment(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.dotfiles = self.root / "dotfiles"
        self.home = self.root / "home"
        self.config_home = self.root / "config"
        self.stub_dir = self.root / "bin"
        (self.dotfiles / "bin").mkdir(parents=True)
        (self.dotfiles / "forward").mkdir()
        self.home.mkdir()
        self.stub_dir.mkdir()

        write_executable(
            self.dotfiles / "bin" / "mise",
            'if [ "${1:-}" = "which" ] || [ "${1:-}" = "exec" ]; then exit 0; fi',
        )
        write_executable(self.stub_dir / "systemctl", "exit 0")
        for name in (
            "config.toml",
            "config-serve.toml",
            "forward-daemon.service",
            "forward-serve.service",
            "omp-browser-relay.service",
        ):
            (self.dotfiles / "forward" / name).write_text("test\n", encoding="utf-8")
        self.env = {
            **os.environ,
            "DOTFILES_DIR": str(self.dotfiles),
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.config_home),
            "PATH": f"{self.stub_dir}:{os.environ['PATH']}",
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def install(self, role: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(INSTALLER), role],
            capture_output=True,
            text=True,
            env=self.env,
            check=False,
        )

    def shell_relay_url(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "-c", 'source "$1"; printf "%s" "${BROWSER_RELAY_URL-}"', "--", str(BASHRC)],
            capture_output=True,
            text=True,
            env=self.env,
            check=False,
        )

    def test_serve_role_installs_no_relay_environment_or_overlay(self) -> None:
        """The ambient relay endpoint is gone; grants supply per-session endpoints."""
        install = self.install("serve")

        self.assertEqual(install.returncode, 0, install.stderr)
        self.assertFalse(
            (self.config_home / "environment.d" / "browser-relay.conf").exists()
        )
        self.assertFalse((self.config_home / "omp" / "browser-relay.yml").exists())
        shell = self.shell_relay_url()
        self.assertEqual(shell.returncode, 0, shell.stderr)
        self.assertEqual(shell.stdout, "")

    def test_serve_role_removes_a_stale_relay_environment_and_overlay(self) -> None:
        """Reinstalling cleans up what bypass-era installs wrote."""
        environment_dir = self.config_home / "environment.d"
        environment_dir.mkdir(parents=True)
        (environment_dir / "browser-relay.conf").write_text(
            "BROWSER_RELAY_URL=http://stale.test\n", encoding="utf-8"
        )
        omp_dir = self.config_home / "omp"
        omp_dir.mkdir(parents=True)
        (omp_dir / "browser-relay.yml").symlink_to(self.dotfiles / "gone.yml")

        install = self.install("serve")

        self.assertEqual(install.returncode, 0, install.stderr)
        self.assertFalse((environment_dir / "browser-relay.conf").exists())
        self.assertFalse((omp_dir / "browser-relay.yml").is_symlink())

    def test_daemon_role_installs_no_relay_environment(self) -> None:
        """The laptop role never had the ambient endpoint and must not gain one."""
        install = self.install("daemon")

        self.assertEqual(install.returncode, 0, install.stderr)
        self.assertFalse(
            (self.config_home / "environment.d" / "browser-relay.conf").exists()
        )
        self.assertFalse((self.config_home / "omp" / "browser-relay.yml").exists())


if __name__ == "__main__":
    unittest.main()
