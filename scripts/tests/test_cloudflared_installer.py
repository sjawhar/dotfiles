#!/usr/bin/env python3
"""Tests for the pinned cloudflared release-file installer."""

from __future__ import annotations

import hashlib
import os
import re
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
PIN_TEXT = (
    "# Pinned by installers/cloudflared.sh; change VERSION there to upgrade.\n"
    "Package: cloudflared\n"
    f"Pin: version {VERSION}\n"
    "Pin-Priority: 1001\n"
)
RELEASE_URL = "https://github.com/cloudflare/cloudflared/releases/download"


def write_command(directory: Path, name: str, body: str) -> None:
    """Write an executable bash fake into *directory*."""
    path = directory / name
    path.write_text(f"#!/bin/bash\nset -euo pipefail\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class InstallerHarness:
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.dotfiles = self.root / "dotfiles"
        installers = self.dotfiles / "installers"
        installers.mkdir(parents=True)
        self.installer = installers / "cloudflared.sh"
        shutil.copy2(INSTALLER, self.installer)
        shutil.copy2(LIBRARY, installers / "lib.sh")

        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self.log = self.root / "calls.log"
        self.version_file = self.root / "cloudflared-version"
        self.payload = self.root / "curl-payload"
        self.payload.write_bytes(b"default cloudflared release payload\n")
        self.install_root = self.root / "install-root"
        self.tmpdir = self.root / "tmp"
        self.tmpdir.mkdir()

        self._write_fakes()
        self.env = {
            "HOME": str(self.root),
            "PATH": f"{self.bin_dir}:/usr/bin:/bin",
            "INSTALL_ROOT": str(self.install_root),
            "TMPDIR": str(self.tmpdir),
            "CALL_LOG": str(self.log),
            "CF_VERSION_FILE": str(self.version_file),
            "CURL_PAYLOAD": str(self.payload),
            "FAKE_ARCH": "amd64",
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _reset_harness(self) -> None:
        self.temp_dir.cleanup()
        self.setUp()

    def _write_fakes(self) -> None:
        write_command(
            self.bin_dir,
            "curl",
            r'''echo "curl $*" >> "$CALL_LOG"
output=''
url=''
for ((index = 1; index <= $#; index++)); do
    argument="${!index}"
    if [[ "$argument" == -o ]]; then
        next=$((index + 1))
        output="${!next}"
    elif [[ "$argument" == http://* || "$argument" == https://* ]]; then
        url="$argument"
    fi
done

if [[ "${CURL_FAILS:-}" == 1 || -z "$output" || -z "$url" ]]; then
    exit 22
fi

release_url="https://github.com/cloudflare/cloudflared/releases/download/2026.10.7/cloudflared-linux-${FAKE_ARCH}.deb"
if [[ "$url" == "$release_url" ]]; then
    cp "$CURL_PAYLOAD" "$output"
elif [[ "$url" == "https://pkg.cloudflare.com/cloudflare-main.gpg" ]]; then
    printf key > "$output"
else
    exit 22
fi''',
        )
        write_command(
            self.bin_dir,
            "dpkg",
            r'''echo "dpkg $*" >> "$CALL_LOG"
if [[ "${1:-}" == --print-architecture ]]; then
    printf '%s\n' "$FAKE_ARCH"
else
    exit 2
fi''',
        )
        write_command(
            self.bin_dir,
            "sudo",
            r'''echo "sudo $*" >> "$CALL_LOG"
exec "$@"''',
        )
        write_command(self.bin_dir, "apt-get", self._standard_apt_get_fake())
        write_command(
            self.bin_dir,
            "cloudflared",
            r'''echo "cloudflared $*" >> "$CALL_LOG"
if [[ ! -f "$CF_VERSION_FILE" ]]; then
    printf 'command not found\n' >&2
    exit 127
fi
printf 'cloudflared version %s\n' "$(<"$CF_VERSION_FILE")"''',
        )
        write_command(
            self.bin_dir,
            "gpg",
            rf'''echo "gpg $*" >> "$CALL_LOG"
printf 'fpr:::::::::{FINGERPRINT}:\n' ''',
        )

    @staticmethod
    def _standard_apt_get_fake() -> str:
        return r'''echo "apt-get $*" >> "$CALL_LOG"

install=0
pinned_version=0
update=0
for argument in "$@"; do
    if [[ "$argument" == *.deb ]]; then
        digest="$(sha256sum "$argument" | cut -d' ' -f1)"
        printf 'apt-get-saw %s %s\n' "$digest" "$argument" >> "$CALL_LOG"
    fi
    if [[ "$argument" == update ]]; then
        update=1
    elif [[ "$argument" == install ]]; then
        install=1
    elif [[ "$argument" == cloudflared=2026.10.7 ]]; then
        pinned_version=1
    fi
done

if (( update )); then
    exit 0
fi
if (( pinned_version )); then
    printf "E: Version '2026.10.7' for 'cloudflared' was not found\n" >&2
    exit 100
fi
if [[ "${APT_FAILS:-}" == 1 ]]; then
    exit 100
fi
if (( install )); then
    printf '%s\n' "${APT_INSTALLS_VERSION:-2026.10.7}" > "$CF_VERSION_FILE"
fi'''

    def serve_payload(self, payload: bytes) -> None:
        self.payload.write_bytes(payload)

    def use_digest(self, arch: str, payload: bytes) -> None:
        """Set the copied installer's expected digest when that line exists."""
        digest = hashlib.sha256(payload).hexdigest()
        name = f"SHA256_{arch.upper()}"
        contents = self.installer.read_text(encoding="utf-8")
        contents = re.sub(
            rf"^{re.escape(name)}=.*$",
            f"{name}={digest}",
            contents,
            flags=re.MULTILINE,
        )
        self.installer.write_text(contents, encoding="utf-8")

    def install(self, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(self.installer)],
            env={**self.env, **env},
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def calls(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []

    def assert_tmpdir_empty(self) -> None:
        self.assertEqual(list(self.tmpdir.iterdir()), [])

    def assert_pin_file(self) -> None:
        pin = self.install_root / "etc/apt/preferences.d/cloudflared.pref"
        self.assertEqual(pin.read_text(encoding="utf-8"), PIN_TEXT)
        self.assertEqual(stat.S_IMODE(pin.stat().st_mode), 0o644)

    def assert_release_file_install(self, arch: str, payload: bytes) -> None:
        calls = self.calls()
        release_url = f"{RELEASE_URL}/{VERSION}/cloudflared-linux-{arch}.deb"
        curl_calls = [call for call in calls if call.startswith("curl ")]
        self.assertEqual(len(curl_calls), 1, calls)
        self.assertIn(release_url, curl_calls[0])

        install_prefix = "sudo apt-get install -y --allow-downgrades "
        install_calls = [call for call in calls if call.startswith(install_prefix)]
        self.assertEqual(len(install_calls), 1, calls)
        release_path = Path(install_calls[0][len(install_prefix) :])
        self.assertTrue(str(release_path).startswith(f"{self.tmpdir}{os.sep}"), release_path)
        self.assertEqual(release_path.name, f"cloudflared-linux-{arch}.deb")
        self.assertTrue(
            str(release_path).endswith(".deb"),
            "apt-get rejects a local package path without a .deb suffix",
        )

        digest = hashlib.sha256(payload).hexdigest()
        self.assertEqual(calls.count(f"apt-get-saw {digest} {release_path}"), 1, calls)

    def assert_no_legacy_repository_setup(self) -> None:
        self.assertFalse(
            (self.install_root / "usr/share/keyrings/cloudflare-main.gpg").exists()
        )
        self.assertFalse(
            (self.install_root / "etc/apt/sources.list.d/cloudflared.list").exists()
        )
        calls = self.calls()
        self.assertFalse(any(call.startswith("gpg ") for call in calls), calls)
        self.assertFalse(any("apt-get update" in call for call in calls), calls)

    def assert_clean_release_install(self, arch: str, payload: bytes) -> None:
        self.assert_release_file_install(arch, payload)
        self.assert_pin_file()
        self.assert_no_legacy_repository_setup()
        self.assertEqual(self.version_file.read_text(encoding="utf-8").strip(), VERSION)
        self.assert_tmpdir_empty()

class CloudflaredInstaller(InstallerHarness, unittest.TestCase):

    def test_no_cloudflared_installs_checked_release_for_each_architecture(self) -> None:
        for index, arch in enumerate(("amd64", "arm64")):
            with self.subTest(arch=arch):
                if index:
                    self._reset_harness()
                payload = f"cloudflared release for {arch}\n".encode()
                self.serve_payload(payload)
                self.use_digest(arch, payload)

                result = self.install(FAKE_ARCH=arch)

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assert_clean_release_install(arch, payload)

    def test_older_cloudflared_installed_installs_the_release_file(self) -> None:
        payload = b"cloudflared release replacing an older version\n"
        self.serve_payload(payload)
        self.use_digest("amd64", payload)
        self.version_file.write_text("2025.1.1\n", encoding="utf-8")

        result = self.install()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_clean_release_install("amd64", payload)

    def test_unsupported_architecture_stops_before_downloading_or_writing(self) -> None:
        result = self.install(FAKE_ARCH="i386")

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("i386", result.stderr)
        calls = self.calls()
        for command in ("curl ", "sudo ", "apt-get "):
            self.assertFalse(any(call.startswith(command) for call in calls), calls)
        self.assertFalse(self.install_root.exists())
        self.assert_tmpdir_empty()

    def test_checksum_mismatch_installs_nothing(self) -> None:
        received = b"the downloaded release file\n"
        expected = b"a different release file\n"
        self.serve_payload(received)
        self.use_digest("amd64", expected)

        result = self.install()

        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn(hashlib.sha256(received).hexdigest(), result.stderr)
        self.assertIn(hashlib.sha256(expected).hexdigest(), result.stderr)
        self.assertIn("installed nothing", result.stderr)
        calls = self.calls()
        self.assertFalse(any(call.startswith("sudo ") for call in calls), calls)
        self.assertFalse(any(call.startswith("apt-get ") for call in calls), calls)
        self.assertFalse(self.install_root.exists())
        self.assert_tmpdir_empty()

    def test_curl_failure_stops_without_installing(self) -> None:
        payload = b"cloudflared release for curl failure\n"
        self.serve_payload(payload)
        self.use_digest("amd64", payload)

        result = self.install(CURL_FAILS="1")

        self.assertNotEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertFalse(any(call.startswith("sudo ") for call in calls), calls)
        self.assertFalse(any(call.startswith("apt-get ") for call in calls), calls)
        self.assert_tmpdir_empty()

    def test_current_pinned_version_makes_no_installation_call(self) -> None:
        self.version_file.write_text(VERSION + "\n", encoding="utf-8")

        result = self.install()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), ["cloudflared --version"])
        self.assertFalse(self.install_root.exists())

    def test_second_run_after_failed_run_installs_the_release_file(self) -> None:
        keyring = self.install_root / "usr/share/keyrings/cloudflare-main.gpg"
        source = self.install_root / "etc/apt/sources.list.d/cloudflared.list"
        pin = self.install_root / "etc/apt/preferences.d/cloudflared.pref"
        keyring.parent.mkdir(parents=True)
        source.parent.mkdir(parents=True)
        pin.parent.mkdir(parents=True)
        keyring.write_bytes(b"key")
        source.write_text(
            "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] "
            "https://pkg.cloudflare.com/cloudflared any main\n",
            encoding="utf-8",
        )
        pin.write_text(PIN_TEXT, encoding="utf-8")
        key_before = keyring.read_bytes()
        source_before = source.read_bytes()

        payload = b"cloudflared release after apt repository failure\n"
        self.serve_payload(payload)
        self.use_digest("amd64", payload)

        result = self.install()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_release_file_install("amd64", payload)
        self.assertEqual(keyring.read_bytes(), key_before)
        self.assertEqual(source.read_bytes(), source_before)
        self.assert_pin_file()
        calls = self.calls()
        self.assertFalse(any(call.startswith("gpg ") for call in calls), calls)
        self.assertFalse(any("apt-get update" in call for call in calls), calls)
        self.assert_tmpdir_empty()

    def test_installer_rejects_a_different_installed_version(self) -> None:
        payload = b"cloudflared release with a bad installation result\n"
        self.serve_payload(payload)
        self.use_digest("amd64", payload)

        result = self.install(APT_INSTALLS_VERSION="2026.11.0")

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(f"cloudflared: installed version is not {VERSION}", result.stderr)
        self.assert_tmpdir_empty()

    def test_apt_failure_removes_the_temporary_release_file(self) -> None:
        payload = b"cloudflared release with a failing apt invocation\n"
        self.serve_payload(payload)
        self.use_digest("amd64", payload)

        result = self.install(APT_FAILS="1")

        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assert_tmpdir_empty()


@unittest.skipUnless(
    all(shutil.which(command) for command in ("apt-get", "apt-cache", "dpkg-deb")),
    "requires apt-get, apt-cache, and dpkg-deb",
)
class RealAptKeepsThePin(InstallerHarness, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.real_apt_get = shutil.which("apt-get")
        self.real_apt_cache = shutil.which("apt-cache")
        self.real_dpkg_deb = shutil.which("dpkg-deb")
        self.assertTrue(
            all((self.real_apt_get, self.real_apt_cache, self.real_dpkg_deb))
        )

        self.apt_root = self.root / "apt"
        self.etc = self.apt_root / "etc/apt"
        self.source_parts = self.etc / "sources.list.d"
        self.state = self.apt_root / "var/lib/apt"
        self.cache = self.apt_root / "var/cache/apt"
        self.status = self.apt_root / "var/lib/dpkg/status"
        self.empty_status = self.apt_root / "var/lib/dpkg/empty-status"
        self.empty_preferences = self.root / "empty-preferences"
        self.repo = self.apt_root / "repo"
        for directory in (
            self.source_parts,
            self.state / "lists/partial",
            self.cache / "archives/partial",
            self.status.parent,
            self.empty_preferences,
            self.repo,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self.empty_status.write_text("", encoding="utf-8")
        self._write_repository()
        self.deb = self._release_deb()
        self.deb_bytes = self.deb.read_bytes()

    def _release_deb(self) -> Path:
        configured = os.environ.get("CLOUDFLARED_DEB")
        if configured:
            release_deb = Path(configured)
            self.assertTrue(release_deb.is_absolute(), "CLOUDFLARED_DEB must be absolute")
            self.assertTrue(release_deb.is_file(), f"missing {release_deb}")
            return release_deb

        package_root = self.root / "minimal-cloudflared"
        control = package_root / "DEBIAN/control"
        control.parent.mkdir(parents=True)
        control.write_text(
            "Package: cloudflared\n"
            f"Version: {VERSION}\n"
            "Architecture: amd64\n"
            "Maintainer: Cloudflared installer test <test@example.invalid>\n"
            "Description: Minimal cloudflared package for apt pin testing\n",
            encoding="utf-8",
        )
        release_deb = self.root / "cloudflared-linux-amd64.deb"
        result = subprocess.run(
            [self.real_dpkg_deb, "--build", str(package_root), str(release_deb)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        output = self.output(result)
        self.assertEqual(result.returncode, 0, output)
        return release_deb

    def _write_repository(self) -> None:
        (self.repo / "Packages").write_text(
            "Package: cloudflared\n"
            "Version: 2026.11.0\n"
            "Architecture: amd64\n"
            "Maintainer: Cloudflared installer test <test@example.invalid>\n"
            "Filename: cloudflared_2026.11.0_amd64.deb\n"
            "Size: 1\n"
            "Description: Newer package offered by the local repository\n",
            encoding="utf-8",
        )
        (self.source_parts / "local.list").write_text(
            f"deb [trusted=yes] file:{self.repo} ./\n", encoding="utf-8"
        )

    @staticmethod
    def output(result: subprocess.CompletedProcess[str]) -> str:
        return f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    def apt_options(self, status: Path, preferences: Path) -> list[str]:
        return [
            "-o",
            f"Dir={self.apt_root}",
            "-o",
            f"Dir::State::status={status}",
            "-o",
            f"Dir::Etc={self.etc}",
            "-o",
            "Dir::Etc::sourcelist=-",
            "-o",
            f"Dir::Etc::sourceparts={self.source_parts}",
            "-o",
            f"Dir::Etc::preferencesparts={preferences}",
            "-o",
            f"Dir::Cache={self.cache}",
            "-o",
            f"Dir::State={self.state}",
            "-o",
            "Debug::NoLocking=1",
            "-o",
            "APT::Architecture=amd64",
        ]

    def run_apt(
        self, arguments: list[str], status: Path, preferences: Path
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [self.real_apt_get, *self.apt_options(status, preferences), *arguments],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def run_apt_cache(
        self, arguments: list[str], status: Path, preferences: Path
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [self.real_apt_cache, *self.apt_options(status, preferences), *arguments],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def write_installed_status(self) -> None:
        self.status.write_text(
            "Package: cloudflared\n"
            "Status: install ok installed\n"
            "Priority: optional\n"
            "Section: utils\n"
            "Installed-Size: 1\n"
            "Maintainer: Cloudflared installer test <test@example.invalid>\n"
            "Architecture: amd64\n"
            f"Version: {VERSION}\n"
            "Description: Installed cloudflared version for apt pin testing\n",
            encoding="utf-8",
        )

    def write_real_apt_fake(self) -> None:
        write_command(
            self.bin_dir,
            "apt-get",
            r'''echo "apt-get $*" >> "$CALL_LOG"

install=0
for argument in "$@"; do
    if [[ "$argument" == *.deb ]]; then
        digest="$(sha256sum "$argument" | cut -d' ' -f1)"
        printf 'apt-get-saw %s %s\n' "$digest" "$argument" >> "$CALL_LOG"
    fi
    if [[ "$argument" == install ]]; then
        install=1
    fi
done

if (( install )); then
    mapfile -t options < "$REAL_APT_OPTIONS"
    if "$REAL_APT_GET" -s "${options[@]}" "$@" > "$APT_CAPTURE" 2>&1
    then
        printf '%s\n' "2026.10.7" > "$CF_VERSION_FILE"
    else
        result=$?
        cat "$APT_CAPTURE" >&2
        exit "$result"
    fi
fi''',
        )

    def test_upgrade_control_then_installer_pin_keeps_the_installed_release(self) -> None:
        update = self.run_apt(["update"], self.empty_status, self.empty_preferences)
        self.assertEqual(update.returncode, 0, self.output(update))

        unpinned_policy = self.run_apt_cache(
            ["policy", "cloudflared"], self.empty_status, self.empty_preferences
        )
        self.assertEqual(unpinned_policy.returncode, 0, self.output(unpinned_policy))
        self.assertIn("2026.11.0", self.output(unpinned_policy), self.output(unpinned_policy))

        self.write_installed_status()
        upgrade_without_pin = self.run_apt(
            ["-s", "upgrade"], self.status, self.empty_preferences
        )
        self.assertEqual(upgrade_without_pin.returncode, 0, self.output(upgrade_without_pin))
        self.assertIn(
            "Inst cloudflared [2026.10.7] (2026.11.0",
            self.output(upgrade_without_pin),
            self.output(upgrade_without_pin),
        )

        self.serve_payload(self.deb_bytes)
        self.use_digest("amd64", self.deb_bytes)
        apt_capture = self.root / "installer-apt-output.txt"
        self.write_real_apt_fake()
        pin_dir = self.install_root / "etc/apt/preferences.d"
        apt_options = self.root / "real-apt-options"
        apt_options.write_text(
            "\n".join(self.apt_options(self.empty_status, pin_dir)) + "\n",
            encoding="utf-8",
        )

        result = self.install(
            REAL_APT_GET=self.real_apt_get,
            REAL_APT_OPTIONS=str(apt_options),
            APT_CAPTURE=str(apt_capture),
        )
        apt_output = apt_capture.read_text(encoding="utf-8") if apt_capture.exists() else ""
        self.assertEqual(result.returncode, 0, f"{result.stderr}\n{apt_output}")
        self.assertIn(
            "Inst cloudflared (2026.10.7 local-deb [amd64])",
            apt_output,
            apt_output,
        )

        upgrade_with_pin = self.run_apt(["-s", "upgrade"], self.status, pin_dir)
        self.assertEqual(upgrade_with_pin.returncode, 0, self.output(upgrade_with_pin))
        self.assertFalse(
            any(
                line.startswith("Inst cloudflared")
                for line in self.output(upgrade_with_pin).splitlines()
            ),
            self.output(upgrade_with_pin),
        )

        pinned_policy = self.run_apt_cache(["policy", "cloudflared"], self.status, pin_dir)
        self.assertEqual(pinned_policy.returncode, 0, self.output(pinned_policy))
        self.assertIn(
            f"Candidate: {VERSION}", self.output(pinned_policy), self.output(pinned_policy)
        )


if __name__ == "__main__":
    unittest.main()
