#!/usr/bin/env python3
"""phone/termux-setup creates an idempotent Cloudflare Access SSH client."""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


DOTFILES = Path(__file__).resolve().parents[2]
README = DOTFILES / "README.md"
SETUP = DOTFILES / "phone" / "termux-setup"
ORYX_ED25519_KEY = "AAAAC3NzaC1lZDI1NTE5AAAAIN16uW+GsQu5KiAmxnFHlSdoz7jDM3N938v6hAZnrF53"
ORYX_ED25519_FINGERPRINT = "SHA256:vBGetDgyAzjQXb4KcsNiUHv4Hxi+fyDHUH+pD+t0ukc"
ORYX_ECDSA_KEY = "AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBEqq9YBGsfmknirbH3aZEOhdkZrD9CpRljjMsFh+zZU+Hc4az4BEJWVcf3t4umhvg40zFa+qaTiU6lLp9t3lfcA="
ORYX_ECDSA_FINGERPRINT = "SHA256:gdwcOzRloi72e7Mhl3gRUYKQ9ogFLRj5cpBjBLN964c"
KNOWN_HOSTS_LINE = f"oryx.thecybermonk.com ssh-ed25519 {ORYX_ED25519_KEY}"
README_URL = "https://raw.githubusercontent.com/sjawhar/dotfiles/main/phone/termux-setup"
README_COMMAND = f'f=$(mktemp) && curl -fsSL {README_URL} -o "$f" && bash "$f"; rm -f "$f"'


def write_command(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/bash\nset -euo pipefail\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class TermuxSetup(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.command = self.root / "termux-setup"
        shutil.copy2(SETUP, self.command)
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self.pkg_log = self.root / "pkg.log"
        write_command(
            self.bin_dir / "pkg",
            '''printf '%s\\n' "$*" >> "$PKG_LOG"
printf 'pkg standard output\\n'
printf 'pkg error output\\n' >&2
exit "${PKG_EXIT:-0}"''',
        )
        self.env = {
            "HOME": str(self.home),
            "PATH": f"/usr/bin:/bin:{self.bin_dir}",
            "PKG_LOG": str(self.pkg_log),
        }
        self.assertEqual(shutil.which("ssh-keygen", path=self.env["PATH"]), "/usr/bin/ssh-keygen")
        self.assertEqual(shutil.which("ssh", path=self.env["PATH"]), "/usr/bin/ssh")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @property
    def ssh_dir(self) -> Path:
        return self.home / ".ssh"

    @property
    def key(self) -> Path:
        return self.ssh_dir / "id_ed25519"

    @property
    def config(self) -> Path:
        return self.ssh_dir / "config"

    @property
    def known_hosts(self) -> Path:
        return self.ssh_dir / "known_hosts"

    def invoke(self, script: Path | None = None, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(script or self.command)],
            cwd=self.home,
            env={**self.env, **extra},
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

    def pkg_calls(self) -> list[str]:
        if not self.pkg_log.exists():
            return []
        return self.pkg_log.read_text(encoding="utf-8").splitlines()

    def ssh_values(self) -> dict[str, list[str]]:
        result = subprocess.run(
            ["/usr/bin/ssh", "-F", str(self.config), "-G", "oryx"],
            cwd=self.home,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        values: dict[str, list[str]] = {}
        for line in result.stdout.splitlines():
            name, value = line.split(maxsplit=1)
            values.setdefault(name, []).append(value)
        return values

    def fingerprint(self) -> str:
        result = subprocess.run(
            ["/usr/bin/ssh-keygen", "-lf", str(self.known_hosts)],
            cwd=self.home,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def readme_command(self, url: str) -> str:
        text = README.read_text(encoding="utf-8")
        match = re.search(rf"^{re.escape(README_COMMAND)}$", text, re.MULTILINE)
        self.assertIsNotNone(match, "README must contain the phone bootstrap command verbatim")
        return match.group(0).replace(README_URL, url)

    def run_readme_command(self, url: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "-c", self.readme_command(url)],
            cwd=self.home,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

    def test_writes_the_access_ssh_client_and_prints_only_its_public_key(self) -> None:
        result = self.invoke()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, self.key.with_suffix(".pub").read_text(encoding="utf-8"))
        self.assertEqual(self.pkg_calls(), ["install -y openssh cloudflared"])
        self.assertEqual(stat.S_IMODE(self.ssh_dir.stat().st_mode), 0o700)

        values = self.ssh_values()
        self.assertEqual(values["hostname"], ["oryx.thecybermonk.com"])
        self.assertEqual(values["user"], ["agent"])
        self.assertIn("~/.ssh/id_ed25519", values["identityfile"])
        self.assertEqual(values["identitiesonly"], ["yes"])
        self.assertEqual(values["proxycommand"], ["cloudflared access ssh --hostname %h"])
        fingerprint = self.fingerprint()
        self.assertIn(ORYX_ED25519_FINGERPRINT, fingerprint)
        self.assertIn("(ED25519)", fingerprint)

    def test_second_run_keeps_the_key_and_unrelated_ssh_entries(self) -> None:
        first = self.invoke()
        self.assertEqual(first.returncode, 0, first.stderr)
        key_before = self.key.read_bytes()
        public_key = self.key.with_suffix(".pub").read_text(encoding="utf-8")
        unrelated_config = "Host other\n  HostName other.example\n"
        unrelated_known_host = f"other.example ssh-ed25519 {ORYX_ED25519_KEY}\n"
        with self.config.open("a", encoding="utf-8") as file:
            file.write("\n" + unrelated_config)
        with self.known_hosts.open("a", encoding="utf-8") as file:
            file.write(unrelated_known_host)

        second = self.invoke()

        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(second.stderr, "")
        self.assertEqual(second.stdout, public_key)
        self.assertEqual(self.key.read_bytes(), key_before)
        config = self.config.read_text(encoding="utf-8")
        self.assertIn(unrelated_config, config)
        self.assertEqual(config.splitlines().count("# BEGIN phone/termux-setup oryx"), 1)
        self.assertEqual(config.splitlines().count("Host oryx"), 1)
        known_hosts = self.known_hosts.read_text(encoding="utf-8").splitlines()
        self.assertIn(unrelated_known_host.rstrip(), known_hosts)
        self.assertEqual(known_hosts.count(KNOWN_HOSTS_LINE), 1)
        self.assertEqual(self.pkg_calls(), ["install -y openssh cloudflared", "install -y openssh cloudflared"])

    def test_pkg_output_reaches_standard_error_only_on_failure(self) -> None:
        result = self.invoke(PKG_EXIT="1")

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "pkg standard output\npkg error output\n")
        self.assertFalse(self.key.exists())

    def test_readme_command_runs_the_downloaded_script_and_skips_a_missing_file(self) -> None:
        success = self.run_readme_command(self.command.as_uri())

        self.assertEqual(success.returncode, 0, success.stderr)
        self.assertEqual(success.stdout, self.key.with_suffix(".pub").read_text(encoding="utf-8"))
        self.assertTrue(self.key.exists())

        missing_root = self.root / "missing-home"
        missing_root.mkdir()
        missing_env = {**self.env, "HOME": str(missing_root), "PKG_LOG": str(self.root / "missing-pkg.log")}
        missing = subprocess.run(
            ["bash", "-c", self.readme_command((self.root / "missing-termux-setup").as_uri())],
            cwd=missing_root,
            env=missing_env,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

        self.assertEqual(missing.returncode, 0, missing.stderr)
        self.assertEqual(missing.stdout, "")
        self.assertFalse((missing_root / ".ssh/id_ed25519").exists())
        self.assertFalse((self.root / "missing-pkg.log").exists())

    def test_ecdsa_key_fails_the_ed25519_fingerprint_assertion(self) -> None:
        negative_command = self.root / "termux-setup-ecdsa"
        command_text = SETUP.read_text(encoding="utf-8")
        ecdsa_line = f"oryx.thecybermonk.com ecdsa-sha2-nistp256 {ORYX_ECDSA_KEY}"
        self.assertEqual(command_text.count(KNOWN_HOSTS_LINE), 1)
        negative_command.write_text(command_text.replace(KNOWN_HOSTS_LINE, ecdsa_line), encoding="utf-8")
        negative_command.chmod(negative_command.stat().st_mode | stat.S_IEXEC)

        result = self.invoke(negative_command)

        self.assertEqual(result.returncode, 0, result.stderr)
        fingerprint = self.fingerprint()
        self.assertIn(ORYX_ECDSA_FINGERPRINT, fingerprint)
        self.assertIn("(ECDSA)", fingerprint)
        with self.assertRaises(AssertionError):
            self.assertIn(ORYX_ED25519_FINGERPRINT, fingerprint)


if __name__ == "__main__":
    unittest.main()
