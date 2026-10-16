#!/usr/bin/env python3
"""Regression tests for the gws package-wrapper shim.

Each test builds a miniature dotfiles layout so the shim resolves its package
relative to its own location. A stand-in real gws follows the shim on PATH:
it proves a missing package cannot fall through to Application Default
Credentials while the package wrapper still receives the original environment.
"""

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


SOURCE_SHIM = Path(__file__).resolve().parent.parent / "gws"


def write_executable(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class GwsShim(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dotfiles = Path(self.tmp.name) / "dotfiles"
        self.shims = self.dotfiles / "shims"
        self.shims.mkdir(parents=True)
        self.shim = self.shims / "gws"
        if SOURCE_SHIM.is_symlink():
            os.symlink(os.readlink(SOURCE_SHIM), self.shim)
        else:
            shutil.copy2(SOURCE_SHIM, self.shim)

        self.real_gws_dir = Path(self.tmp.name) / "real-gws"
        self.real_gws_dir.mkdir()
        write_executable(self.real_gws_dir / "gws", 'echo "REAL-GWS-STUB"')
        self.plugin_dir = self.dotfiles / "omp/plugins"
        self.wrapper = self.plugin_dir / "node_modules/gws-accounts/bin/gws"
        self.env = {"PATH": f"{self.shims}:{self.real_gws_dir}:/usr/bin:/bin"}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_gws(self, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["gws", "drive", "about", "get"],
            capture_output=True,
            text=True,
            env={**self.env, **(extra_env or {})},
        )

    def assert_missing_package_failure(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(result.stderr.splitlines()), 1)
        self.assertTrue(result.stderr.startswith("gws-accounts: wrapper missing at "))
        self.assertIn("omp/plugins/node_modules/gws-accounts/bin/gws", result.stderr)
        self.assertIn("bun install", result.stderr)
        self.assertNotIn("REAL-GWS-STUB", result.stdout)
        self.assertNotIn("REAL-GWS-STUB", result.stderr)


    def install_package_wrapper(self) -> None:
        self.wrapper.parent.mkdir(parents=True)
        write_executable(
            self.wrapper,
            '# gws-accounts-wrapper\n'
            'if [[ "${GWS_ACCOUNTS_HANDED:-}" != "1234 /tmp/real-gws" ]]; then\n'
            '  echo "loop guard changed" >&2\n'
            '  exit 2\n'
            'fi\n'
            'echo "gws-accounts: no account selected" >&2\n'
            'exit 1',
        )

    def test_present_package_executes_accounts_wrapper_without_changing_loop_guard(self):
        self.install_package_wrapper()

        result = self.run_gws({"GWS_ACCOUNTS_HANDED": "1234 /tmp/real-gws"})

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("gws-accounts: no account selected", result.stderr)
        self.assertNotIn("REAL-GWS-STUB", result.stdout)

    def test_missing_package_fails_loudly_without_running_real_gws(self):
        self.plugin_dir.mkdir(parents=True)

        self.assert_missing_package_failure(self.run_gws())

    def test_missing_plugin_directory_fails_loudly_without_running_real_gws(self):
        self.assert_missing_package_failure(self.run_gws())


if __name__ == "__main__":
    unittest.main()
