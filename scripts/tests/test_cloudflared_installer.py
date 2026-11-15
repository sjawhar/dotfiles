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


def sha256_file(path: Path) -> str:
    """Hash a file, so a mismatch reports two digests instead of megabytes of bytes."""
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


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
elif [[ "${1:-}" == -i ]]; then
    for argument in "$@"; do
        if [[ "$argument" == *.deb ]]; then
            digest="$(sha256sum "$argument" | cut -d' ' -f1)"
            printf 'dpkg-saw %s %s\n' "$digest" "$argument" >> "$CALL_LOG"
        fi
    done
    if [[ "${DPKG_FAILS:-}" == 1 ]]; then
        exit 1
    fi
    printf '%s\n' "${DPKG_INSTALLS_VERSION:-2026.10.7}" > "$CF_VERSION_FILE"
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

update=0
install=0
pinned_version=0
for argument in "$@"; do
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
if (( install && pinned_version )); then
    printf "E: Version '2026.10.7' for 'cloudflared' was not found\n" >&2
    exit 100
fi
printf 'fake apt-get: unexpected call\n' >&2
exit 100'''

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

        install_prefix = "sudo dpkg -i "
        install_calls = [call for call in calls if call.startswith(install_prefix)]
        self.assertEqual(len(install_calls), 1, calls)
        release_path = Path(install_calls[0][len(install_prefix) :])
        self.assertTrue(str(release_path).startswith(f"{self.tmpdir}{os.sep}"), release_path)
        self.assertEqual(release_path.name, f"cloudflared-linux-{arch}.deb")
        self.assertTrue(str(release_path).endswith(".deb"), release_path)

        digest = hashlib.sha256(payload).hexdigest()
        self.assertEqual(calls.count(f"dpkg-saw {digest} {release_path}"), 1, calls)
        self.assertFalse(any(call.startswith("apt-get ") for call in calls), calls)

    def assert_no_legacy_repository_setup(self) -> None:
        self.assertFalse(
            (self.install_root / "usr/share/keyrings/cloudflare-main.gpg").exists()
        )
        self.assertFalse(
            (self.install_root / "etc/apt/sources.list.d/cloudflared.list").exists()
        )
        calls = self.calls()
        self.assertFalse(any(call.startswith("gpg ") for call in calls), calls)
        self.assertFalse(any(call.startswith("apt-get ") for call in calls), calls)

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
        for command in ("curl ", "sudo ", "dpkg -i ", "apt-get "):
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
        self.assertFalse(any(call.startswith("dpkg -i ") for call in calls), calls)
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
        self.assertFalse(any(call.startswith("dpkg -i ") for call in calls), calls)
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

        result = self.install(DPKG_INSTALLS_VERSION="2026.11.0")

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(f"cloudflared: installed version is not {VERSION}", result.stderr)
        self.assert_tmpdir_empty()

    def test_dpkg_failure_removes_the_temporary_release_file(self) -> None:
        payload = b"cloudflared release with a failing dpkg invocation\n"
        self.serve_payload(payload)
        self.use_digest("amd64", payload)

        result = self.install(DPKG_FAILS="1")

        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assert_tmpdir_empty()


@unittest.skipUnless(
    all(
        shutil.which(command)
        for command in ("apt-get", "apt-cache", "apt-config", "dpkg", "dpkg-deb")
    ),
    "requires apt-get, apt-cache, apt-config, dpkg, and dpkg-deb",
)
class RealAptKeepsThePin(InstallerHarness, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.real_apt_get = shutil.which("apt-get")
        self.real_apt_cache = shutil.which("apt-cache")
        self.real_apt_config = shutil.which("apt-config")
        self.real_dpkg = shutil.which("dpkg")
        self.real_dpkg_deb = shutil.which("dpkg-deb")
        self.assertTrue(
            all(
                (
                    self.real_apt_get,
                    self.real_apt_cache,
                    self.real_apt_config,
                    self.real_dpkg,
                    self.real_dpkg_deb,
                )
            )
        )
        # dpkg needs the sbin directories in PATH for ldconfig and start-stop-daemon.
        self.env["PATH"] = (
            f"{self.bin_dir}:/usr/local/sbin:/usr/local/bin:/usr/sbin:"
            "/usr/bin:/sbin:/bin"
        )

        self.apt_root = self.root / "apt"
        self.etc = self.apt_root / "etc/apt"
        self.source_parts = self.etc / "sources.list.d"
        self.state = self.apt_root / "var/lib/apt"
        self.cache = self.apt_root / "var/cache/apt"
        self.status = self.apt_root / "var/lib/dpkg/status"
        self.empty_status = self.apt_root / "var/lib/dpkg/empty-status"
        self.empty_preferences = self.root / "empty-preferences"
        self.empty_apt_parts = self.root / "empty-apt-conf.d"
        self.missing_source_list = self.root / "missing-sources.list"
        self.missing_apt_main = self.root / "missing-apt.conf"
        self.apt_config = self.root / "apt.conf"
        self.repo = self.apt_root / "repo"
        for directory in (
            self.source_parts,
            self.state / "lists/partial",
            self.cache / "archives/partial",
            self.status.parent,
            self.empty_preferences,
            self.empty_apt_parts,
            self.repo,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self.status.write_text("", encoding="utf-8")
        self.empty_status.write_text("", encoding="utf-8")
        self._write_apt_config()
        self.deb = self._release_deb()
        self.deb_bytes = self.deb.read_bytes()
        self.repository_deb = self._repository_deb()
        self._write_repository()

    def _write_apt_config(self) -> None:
        self.apt_config.write_text(
            "\n".join(
                (
                    f'Dir "{self.apt_root}";',
                    f'Dir::State "{self.state}";',
                    f'Dir::State::status "{self.status}";',
                    f'Dir::Cache "{self.cache}";',
                    f'Dir::Etc "{self.etc}";',
                    f'Dir::Etc::sourcelist "{self.missing_source_list}";',
                    f'Dir::Etc::sourceparts "{self.source_parts}";',
                    f'Dir::Etc::parts "{self.empty_apt_parts}";',
                    f'Dir::Etc::main "{self.missing_apt_main}";',
                    f'Dir::Etc::preferencesparts "{self.empty_preferences}";',
                    'Debug::NoLocking "true";',
                    'APT::Architecture "amd64";',
                )
            )
            + "\n",
            encoding="utf-8",
        )

    def _build_deb(
        self,
        package_root: Path,
        output: Path,
        version: str,
        cloudflared: bytes,
        control_text: str | None = None,
    ) -> Path:
        control = package_root / "DEBIAN/control"
        binary = package_root / "usr/bin/cloudflared"
        control.parent.mkdir(parents=True, exist_ok=True)
        binary.parent.mkdir(parents=True, exist_ok=True)
        if control_text is None:
            control_text = (
                "Package: cloudflared\n"
                f"Version: {version}\n"
                "Architecture: amd64\n"
                "Maintainer: Cloudflared installer test <test@example.invalid>\n"
                "Description: Minimal cloudflared package for apt pin testing\n"
            )
        control.write_text(control_text, encoding="utf-8")
        binary.write_bytes(cloudflared)
        binary.chmod(0o755)
        result = subprocess.run(
            [self.real_dpkg_deb, "--build", "-Zgzip", str(package_root), str(output)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        output_text = self.output(result)
        self.assertEqual(result.returncode, 0, output_text)
        return output

    def _release_deb(self) -> Path:
        configured = os.environ.get("CLOUDFLARED_DEB")
        if configured:
            release_deb = Path(configured)
            self.assertTrue(release_deb.is_absolute(), "CLOUDFLARED_DEB must be absolute")
            self.assertTrue(release_deb.is_file(), f"missing {release_deb}")
            return release_deb

        return self._build_deb(
            self.root / "checked-cloudflared",
            self.root / "cloudflared-linux-amd64.deb",
            VERSION,
            b"checked cloudflared release file\n",
        )

    def _deb_control(self, deb: Path) -> str:
        result = subprocess.run(
            [self.real_dpkg_deb, "-f", str(deb)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, self.output(result))
        return result.stdout

    def _repository_deb(self) -> Path:
        return self._build_deb(
            self.root / "repository-cloudflared",
            self.repo / f"cloudflared_{VERSION}_amd64.deb",
            VERSION,
            b"repository cloudflared release file\n",
            self._deb_control(self.deb),
        )

    def _write_repository(self) -> None:
        repository_digest = hashlib.sha256(self.repository_deb.read_bytes()).hexdigest()
        # The same-version entry lists the checked .deb's Size: apt takes an index entry with the
        # same control fields and Size as the command-line file's package and fetches it from the
        # repository. A hostile repository would pad its file to that size.
        (self.repo / "Packages").write_text(
            "Package: cloudflared\n"
            "Version: 2026.11.0\n"
            "Architecture: amd64\n"
            "Maintainer: Cloudflared installer test <test@example.invalid>\n"
            "Filename: cloudflared_2026.11.0_amd64.deb\n"
            "Size: 1\n"
            "Description: Newer package offered by the local repository\n"
            "\n"
            f"{self._deb_control(self.deb)}"
            f"Filename: {self.repository_deb.name}\n"
            f"Size: {self.deb.stat().st_size}\n"
            f"SHA256: {repository_digest}\n",
            encoding="utf-8",
        )
        (self.source_parts / "local.list").write_text(
            f"deb [trusted=yes] file:{self.repo} ./\n", encoding="utf-8"
        )

    @staticmethod
    def output(result: subprocess.CompletedProcess[str]) -> str:
        return f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    def apt_environment(self) -> dict[str, str]:
        return {**os.environ, "APT_CONFIG": str(self.apt_config)}

    @staticmethod
    def apt_options(status: Path, preferences: Path) -> list[str]:
        return [
            "-o",
            f"Dir::State::status={status}",
            "-o",
            f"Dir::Etc::preferencesparts={preferences}",
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
            env=self.apt_environment(),
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
            env=self.apt_environment(),
        )

    def run_apt_config(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [self.real_apt_config, "dump"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=self.apt_environment(),
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

    def _prepare_install_tree(self) -> None:
        dpkg_state = self.install_root / "var/lib/dpkg"
        for directory in (
            dpkg_state / "updates",
            dpkg_state / "info",
            dpkg_state / "triggers",
        ):
            directory.mkdir(parents=True, exist_ok=True)
        (dpkg_state / "status").write_text("", encoding="utf-8")

    def _extract_deb(self, deb: Path, destination: Path) -> None:
        result = subprocess.run(
            [self.real_dpkg_deb, "-x", str(deb), str(destination)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, self.output(result))

    def _regular_files_in_deb(self, deb: Path) -> list[Path]:
        result = subprocess.run(
            [self.real_dpkg_deb, "-c", str(deb)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
        self.assertEqual(result.returncode, 0, self.output(result))
        files = []
        for line in result.stdout.splitlines():
            if line.startswith("-"):
                fields = line.split(maxsplit=5)
                self.assertEqual(len(fields), 6, line)
                self.assertTrue(fields[5].startswith("./"), line)
                files.append(Path(fields[5][2:]))
        return files

    def assert_checked_deb_installed(self) -> None:
        checked_files = self._regular_files_in_deb(self.deb)
        checked_tree = self.root / "checked-deb"
        self._extract_deb(self.deb, checked_tree)
        for relative_path in checked_files:
            expected = checked_tree / relative_path
            installed = self.install_root / relative_path
            self.assertTrue(installed.is_file(), installed)
            self.assertEqual(sha256_file(installed), sha256_file(expected), installed)

    def write_real_dpkg_fake(self) -> None:
        write_command(
            self.bin_dir,
            "dpkg",
            r'''echo "dpkg $*" >> "$CALL_LOG"
if [[ "${1:-}" == --print-architecture ]]; then
    printf 'amd64\n'
elif [[ "${1:-}" == -i ]]; then
    for argument in "$@"; do
        if [[ "$argument" == *.deb ]]; then
            digest="$(sha256sum "$argument" | cut -d' ' -f1)"
            printf 'dpkg-saw %s %s\n' "$digest" "$argument" >> "$CALL_LOG"
            "$REAL_DPKG" --force-not-root --root="$REAL_INSTALL_TREE" --log=/dev/null --unpack "$argument"
        fi
    done
    printf '%s\n' "2026.10.7" > "$CF_VERSION_FILE"
else
    exit 2
fi''',
        )

    def write_real_apt_fake(self) -> None:
        write_command(
            self.bin_dir,
            "apt-get",
            r'''echo "apt-get $*" >> "$CALL_LOG"

install=0
for argument in "$@"; do
    if [[ "$argument" == install ]]; then
        install=1
    fi
done

if (( ! install )); then
    printf 'fake apt-get: unexpected call\n' >&2
    exit 100
fi

if ! APT_CONFIG="$REAL_APT_CONFIG" "$REAL_APT_GET" \
    -o "Dir::State::status=$REAL_EMPTY_STATUS" \
    -o "Dir::Etc::preferencesparts=$REAL_PIN_DIR" \
    -d --print-uris -y "$@" > "$REAL_APT_CAPTURE" 2>&1
then
    result=$?
    cat "$REAL_APT_CAPTURE" >&2
    exit "$result"
fi

uri=''
while IFS= read -r line; do
    case "$line" in
        "'file:"*)
            uri="${line:1}"
            uri="${uri%%\'*}"
            break
            ;;
    esac
done < "$REAL_APT_CAPTURE"
if [[ -z "$uri" ]]; then
    cat "$REAL_APT_CAPTURE" >&2
    exit 1
fi

"$REAL_DPKG" --force-not-root --root="$REAL_INSTALL_TREE" --log=/dev/null --unpack "${uri#file:}"
printf '%s\n' "2026.10.7" > "$CF_VERSION_FILE"''',
        )

    def test_upgrade_control_then_installer_pin_keeps_the_installed_release(self) -> None:
        isolation = self.run_apt_config()
        self.assertEqual(isolation.returncode, 0, self.output(isolation))
        self.assertNotIn("Post-Invoke", isolation.stdout, self.output(isolation))
        self.assertNotIn("Pre-Invoke", isolation.stdout, self.output(isolation))
        self.assertNotRegex(
            isolation.stdout,
            r"(?m)^DPkg::.*(?:Invoke|Install-Pkgs)",
            self.output(isolation),
        )

        update = self.run_apt(["update"], self.empty_status, self.empty_preferences)
        self.assertEqual(update.returncode, 0, self.output(update))

        unpinned_policy = self.run_apt_cache(
            ["policy", "cloudflared"], self.empty_status, self.empty_preferences
        )
        self.assertEqual(unpinned_policy.returncode, 0, self.output(unpinned_policy))
        self.assertIn("2026.11.0", unpinned_policy.stdout, self.output(unpinned_policy))

        self.write_installed_status()
        upgrade_without_pin = self.run_apt(
            ["-s", "upgrade"], self.status, self.empty_preferences
        )
        self.assertEqual(upgrade_without_pin.returncode, 0, self.output(upgrade_without_pin))
        self.assertIn(
            "Inst cloudflared [2026.10.7] (2026.11.0",
            upgrade_without_pin.stdout,
            self.output(upgrade_without_pin),
        )

        hazard_preferences = self.root / "hazard-preferences"
        hazard_preferences.mkdir()
        (hazard_preferences / "cloudflared.pref").write_text(
            PIN_TEXT, encoding="utf-8"
        )
        hazard = self.run_apt(
            [
                "-d",
                "--print-uris",
                "-y",
                "install",
                "--allow-downgrades",
                str(self.deb),
            ],
            self.empty_status,
            hazard_preferences,
        )
        self.assertEqual(hazard.returncode, 0, self.output(hazard))
        self.assertIn(str(self.repository_deb), hazard.stdout, self.output(hazard))
        self.assertNotIn(str(self.deb), hazard.stdout, self.output(hazard))

        self._prepare_install_tree()
        self.serve_payload(self.deb_bytes)
        self.use_digest("amd64", self.deb_bytes)
        self.write_real_dpkg_fake()
        self.write_real_apt_fake()
        pin_dir = self.install_root / "etc/apt/preferences.d"
        apt_capture = self.root / "installer-apt-output.txt"
        result = self.install(
            REAL_APT_CONFIG=str(self.apt_config),
            REAL_APT_GET=self.real_apt_get,
            REAL_APT_CAPTURE=str(apt_capture),
            REAL_DPKG=self.real_dpkg,
            REAL_EMPTY_STATUS=str(self.empty_status),
            REAL_INSTALL_TREE=str(self.install_root),
            REAL_PIN_DIR=str(pin_dir),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_checked_deb_installed()
        repository_tree = self.root / "repository-deb"
        self._extract_deb(self.repository_deb, repository_tree)
        self.assertNotEqual(
            sha256_file(self.install_root / "usr/bin/cloudflared"),
            sha256_file(repository_tree / "usr/bin/cloudflared"),
        )
        checked_digest = hashlib.sha256(self.deb_bytes).hexdigest()
        calls = self.calls()
        install_prefix = "sudo dpkg -i "
        install_calls = [call for call in calls if call.startswith(install_prefix)]
        self.assertEqual(len(install_calls), 1, calls)
        release_path = install_calls[0][len(install_prefix) :]
        self.assertEqual(
            calls.count(f"dpkg-saw {checked_digest} {release_path}"), 1, calls
        )

        installed_status = self.install_root / "var/lib/dpkg/status"
        installed_status.write_text(
            installed_status.read_text(encoding="utf-8").replace(
                "install ok unpacked", "install ok installed"
            ),
            encoding="utf-8",
        )
        upgrade_with_pin = self.run_apt(
            ["-s", "upgrade"], installed_status, pin_dir
        )
        self.assertEqual(upgrade_with_pin.returncode, 0, self.output(upgrade_with_pin))
        self.assertFalse(
            any(
                line.startswith("Inst cloudflared")
                for line in upgrade_with_pin.stdout.splitlines()
            ),
            self.output(upgrade_with_pin),
        )

        pinned_policy = self.run_apt_cache(
            ["policy", "cloudflared"], installed_status, pin_dir
        )
        self.assertEqual(pinned_policy.returncode, 0, self.output(pinned_policy))
        self.assertIn(
            f"Candidate: {VERSION}", pinned_policy.stdout, self.output(pinned_policy)
        )


if __name__ == "__main__":
    unittest.main()
