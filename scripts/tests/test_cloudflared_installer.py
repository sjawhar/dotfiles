#!/usr/bin/env python3
"""installers/cloudflared.sh installs only Cloudflare's pinned Access client."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


DOTFILES = Path(__file__).resolve().parents[2]
INSTALLER = DOTFILES / "installers" / "cloudflared.sh"
LIBRARY = DOTFILES / "installers" / "lib.sh"
VERSION = "2026.10.7"
FINGERPRINT = "CC94B39C77AE7342A68B89628A682D308D4E5E73"


def write_command(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/bash\nset -euo pipefail\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class CloudflaredInstaller(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.dotfiles = self.root / "dotfiles"
        installers = self.dotfiles / "installers"
        installers.mkdir(parents=True)
        shutil.copy2(INSTALLER, installers / "cloudflared.sh")
        shutil.copy2(LIBRARY, installers / "lib.sh")
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self.log = self.root / "calls.log"
        self.version_file = self.root / "cloudflared-version"
        self.install_root = self.root / "install-root"
        self.key_file = self.root / "downloaded-key"

        write_command(self.bin_dir, "curl", 'echo "curl $*" >> "$CALL_LOG"; printf key > "${!#}"')
        write_command(
            self.bin_dir,
            "gpg",
            'echo "gpg $*" >> "$CALL_LOG"; printf "fpr:::::::::%s:\\n" "$GPG_FINGERPRINT"',
        )
        write_command(self.bin_dir, "sudo", 'echo "sudo $*" >> "$CALL_LOG"; exec "$@"')
        write_command(
            self.bin_dir,
            "apt-get",
            '''echo "apt-get $*" >> "$CALL_LOG"
case "$1" in
  install) printf '%s\\n' "$PINNED_VERSION" > "$CF_VERSION_FILE" ;;
esac''',
        )
        write_command(
            self.bin_dir,
            "cloudflared",
            '''echo "cloudflared $*" >> "$CALL_LOG"
if [[ -f "$CF_VERSION_FILE" ]]; then
  printf 'cloudflared version %s\\n' "$(<"$CF_VERSION_FILE")"
else
  printf 'cloudflared version 2025.1.1\\n'
fi''',
        )
        self.env = {
            "HOME": str(self.root / "home"),
            "PATH": f"{self.bin_dir}:/usr/bin:/bin",
            "CALL_LOG": str(self.log),
            "CF_VERSION_FILE": str(self.version_file),
            "PINNED_VERSION": VERSION,
            "GPG_FINGERPRINT": FINGERPRINT,
            "INSTALL_ROOT": str(self.install_root),
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def install(self, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(self.dotfiles / "installers/cloudflared.sh")],
            env={**self.env, **env},
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def calls(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []

    def test_writes_the_pinned_repository_files_and_installs_in_order(self) -> None:
        result = self.install()

        self.assertEqual(result.returncode, 0, result.stderr)
        keyring = self.install_root / "usr/share/keyrings/cloudflare-main.gpg"
        source = self.install_root / "etc/apt/sources.list.d/cloudflared.list"
        pin = self.install_root / "etc/apt/preferences.d/cloudflared.pref"
        self.assertEqual(keyring.read_text(encoding="utf-8"), "key")
        self.assertEqual(
            source.read_text(encoding="utf-8"),
            "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main\n",
        )
        self.assertEqual(
            pin.read_text(encoding="utf-8"),
            "# Pinned by installers/cloudflared.sh; change VERSION there to upgrade.\n"
            "Package: cloudflared\nPin: version 2026.10.7\nPin-Priority: 1001\n",
        )
        calls = self.calls()
        sudo_update = (
            "sudo apt-get update -o Dir::Etc::sourcelist=sources.list.d/cloudflared.list "
            "-o Dir::Etc::sourceparts=- -o APT::Get::List-Cleanup=0"
        )
        update = (
            "apt-get update -o Dir::Etc::sourcelist=sources.list.d/cloudflared.list "
            "-o Dir::Etc::sourceparts=- -o APT::Get::List-Cleanup=0"
        )
        sudo_install = "sudo apt-get install -y --allow-downgrades cloudflared=2026.10.7"
        install = "apt-get install -y --allow-downgrades cloudflared=2026.10.7"
        self.assertLess(calls.index(sudo_update), calls.index(update))
        self.assertLess(calls.index(update), calls.index(sudo_install))
        self.assertLess(calls.index(sudo_install), calls.index(install))
        self.assertEqual(self.version_file.read_text(encoding="utf-8").strip(), VERSION)

    def test_wrong_key_fingerprint_stops_before_any_write(self) -> None:
        result = self.install(GPG_FINGERPRINT="BADFINGERPRINT")

        self.assertEqual(result.returncode, 1)
        self.assertIn("fingerprint", result.stderr)
        self.assertFalse(self.install_root.exists())
        self.assertFalse(any(call.startswith("sudo ") or call.startswith("apt-get") for call in self.calls()))

    def test_current_pinned_version_makes_no_installation_call(self) -> None:
        self.version_file.write_text(VERSION + "\n", encoding="utf-8")

        result = self.install()

        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertEqual(calls, ["cloudflared --version"])
        self.assertFalse(self.install_root.exists())


if __name__ == "__main__":
    unittest.main()
