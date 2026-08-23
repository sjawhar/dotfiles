#!/usr/bin/env python3
"""OMP relay overlay routing tests."""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
SHIM = DOTFILES / "shims" / "omp"


def write_stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/bash\nset -euo pipefail\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class OmpRelayConfig(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.stub_dir = self.root / "bin"
        self.stub_dir.mkdir()
        self.config_home = self.root / "config"
        (self.config_home / "omp").mkdir(parents=True)
        write_stub(
            self.stub_dir,
            "secrets",
            'while [[ "${1:-}" != "--" ]]; do shift; done\nshift\nexec "$@"',
        )
        write_stub(self.stub_dir, "mise", 'printf "%s\\n" "${PI_CONFIG_FILES-<unset>}"')
        self.env = {
            **os.environ,
            "DOTFILES_DIR": str(DOTFILES),
            "HOME": str(self.root),
            "PATH": f"{self.stub_dir}:{os.environ['PATH']}",
            "XDG_CONFIG_HOME": str(self.config_home),
        }
        self.env.pop("PI_CONFIG_FILES", None)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def run_shim(self, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(SHIM), "--version"],
            capture_output=True,
            text=True,
            env={**self.env, **(extra_env or {})},
            check=False,
        )

    def test_omits_relay_url_without_the_devbox_overlay(self) -> None:
        """Machines without the role-installed overlay do not receive a relay endpoint."""
        result = self.run_shim()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "<unset>\n")

    def test_ignores_a_stray_relay_overlay(self) -> None:
        """The overlay contract is retired; a leftover file must not re-enter config."""
        relay_config = self.config_home / "omp" / "browser-relay.yml"
        relay_config.write_text(
            "browser:\n  relayUrl: http://100.100.92.97:12803\n", encoding="utf-8"
        )

        result = self.run_shim({"PI_CONFIG_FILES": "/tmp/caller.yml"})

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "/tmp/caller.yml\n")


if __name__ == "__main__":
    unittest.main()
