#!/usr/bin/env python3
"""Regression coverage for the forward serve-role shell environment."""

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
RELAY_URL = "http://relay.test:12803"


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
        (self.dotfiles / "omp").mkdir()
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
        (self.dotfiles / "omp" / "config-serve.yml").write_text(
            f"browser:\n  relayUrl: {RELAY_URL}\n", encoding="utf-8"
        )
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

    def test_serve_role_provides_the_relay_url_to_interactive_shells(self) -> None:
        """Removing the serve-only environment file makes capture unconfigured."""
        install = self.install("serve")

        self.assertEqual(install.returncode, 0, install.stderr)
        environment_file = self.config_home / "environment.d" / "browser-relay.conf"
        self.assertEqual(
            environment_file.read_text(encoding="utf-8"),
            f"BROWSER_RELAY_URL={RELAY_URL}\n",
        )
        shell = self.shell_relay_url()
        self.assertEqual(shell.returncode, 0, shell.stderr)
        self.assertEqual(shell.stdout, RELAY_URL)

    def test_daemon_role_does_not_install_the_relay_environment(self) -> None:
        """Adding the environment file outside serve would leak devbox routing."""
        install = self.install("daemon")

        self.assertEqual(install.returncode, 0, install.stderr)
        self.assertFalse(
            (self.config_home / "environment.d" / "browser-relay.conf").exists()
        )
        shell = self.shell_relay_url()
        self.assertEqual(shell.returncode, 0, shell.stderr)
        self.assertEqual(shell.stdout, "")


if __name__ == "__main__":
    unittest.main()
