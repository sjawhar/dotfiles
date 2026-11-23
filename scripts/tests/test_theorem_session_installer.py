#!/usr/bin/env python3
"""installers/theorem-session.sh installs the pinned theorem-session release, checked by SHA-256.

The installer and lib.sh are copied into a throwaway dotfiles tree whose pins name the digests of
an archive the test builds; `uname`, `gh-app-token` and `gh` are stubs on PATH that record their
calls, and `gh` serves that archive. HOME and TMPDIR are throwaway directories.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
INSTALLER = DOTFILES / "installers" / "theorem-session.sh"
LIBRARY = DOTFILES / "installers" / "lib.sh"
TAG = "theorem-session-v0.1.1"
TOKEN = "ghs_STUB_READ_TOKEN"


def write_command(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/bash\nset -euo pipefail\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def archive_of(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class TheoremSessionInstaller(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        installers = self.root / "dotfiles" / "installers"
        installers.mkdir(parents=True)
        self.installer = installers / "theorem-session.sh"
        shutil.copy2(INSTALLER, self.installer)
        shutil.copy2(LIBRARY, installers / "lib.sh")
        self.home = self.root / "home"
        self.home.mkdir()
        self.bin_dir = self.home / ".local" / "bin"
        self.installed = self.bin_dir / "theorem-session"
        self.tmpdir = self.root / "tmp"
        self.tmpdir.mkdir()
        self.stubs = self.root / "stubs"
        self.stubs.mkdir()
        self.log = self.root / "calls.log"
        self.served = self.root / "served.tar.gz"
        write_command(self.stubs, "uname", 'case "$1" in -s) echo "$FAKE_OS" ;; -m) echo "$FAKE_MACHINE" ;; *) exit 2 ;; esac')
        write_command(self.stubs, "gh-app-token", r'''echo "gh-app-token $*" >> "$CALL_LOG"
[[ -z "${TOKEN_FAILS:-}" ]] || { echo "gh-app-token: stub refusal" >&2; exit 1; }
echo "$STUB_TOKEN"''')
        # Serves the archive under the name its one --pattern asks for, into --dir.
        write_command(self.stubs, "gh", r'''echo "gh $* token=${GH_TOKEN:-none}" >> "$CALL_LOG"
[[ -z "${GH_FAILS:-}" ]] || { echo "gh: stub failure" >&2; exit 1; }
dir="" pattern=""
while (($#)); do
    case "$1" in --dir) dir="$2"; shift ;; --pattern) pattern="$2"; shift ;; esac
    shift
done
cp "$SERVED" "$dir/$pattern"''')
        self.binary = b"#!/bin/sh\necho theorem-session from the release\n"
        self.env = {
            "HOME": str(self.home),
            "PATH": f"{self.stubs}:/usr/bin:/bin",
            "TMPDIR": str(self.tmpdir),
            "CALL_LOG": str(self.log),
            "SERVED": str(self.served),
            "STUB_TOKEN": TOKEN,
            "FAKE_OS": "Linux",
            "FAKE_MACHINE": "x86_64",
        }

    def serve(self, arch: str, members: dict[str, bytes] | None = None, pin_archive: bytes | None = None,
              pin_binary: bytes | None = None) -> bytes:
        """Serve an archive holding *members* (the release binary by default) and pin its digests in the copy."""
        archive = archive_of(members if members is not None else {"theorem-session": self.binary})
        self.served.write_bytes(archive)
        self.pin(f"SHA256_{arch.upper()}", sha256(pin_archive if pin_archive is not None else archive))
        self.pin(f"BINARY_SHA256_{arch.upper()}", sha256(pin_binary if pin_binary is not None else self.binary))
        return archive

    def pin(self, name: str, digest: str) -> None:
        text, count = re.subn(rf"^{name}=[0-9a-f]{{64}}$", f"{name}={digest}", self.installer.read_text(encoding="utf-8"),
                              flags=re.MULTILINE)
        self.assertEqual(count, 1, name)
        self.installer.write_text(text, encoding="utf-8")

    def install(self, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", str(self.installer)], env={**self.env, **env}, capture_output=True, text=True,
                              timeout=60, check=False)

    def calls(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []

    def place(self, data: bytes, mode: int = 0o755) -> os.stat_result:
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        self.installed.write_bytes(data)
        self.installed.chmod(mode)
        return self.installed.stat()

    def assert_nothing_left_behind(self) -> None:
        self.assertEqual(list(self.tmpdir.iterdir()), [])
        if self.bin_dir.exists():
            self.assertEqual([p.name for p in self.bin_dir.iterdir() if p.name != "theorem-session"], [])

    def assert_installed(self) -> None:
        self.assertEqual(self.installed.read_bytes(), self.binary)
        self.assertFalse(self.installed.is_symlink())
        self.assertEqual(stat.S_IMODE(self.installed.stat().st_mode), 0o755)

    def test_installs_the_pinned_archive_for_each_architecture(self) -> None:
        for arch, machine in (("amd64", "x86_64"), ("arm64", "aarch64"), ("arm64", "arm64")):
            with self.subTest(machine=machine):
                self.installed.unlink(missing_ok=True)
                self.log.unlink(missing_ok=True)
                self.serve(arch)
                result = self.install(FAKE_MACHINE=machine)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assert_installed()
                calls = self.calls()
                self.assertEqual(len(calls), 2, calls)
                self.assertEqual(calls[0], "gh-app-token agent --owner theorem-labs --repo agent-infra --read-only")
                self.assertRegex(calls[1], rf"^gh release download {TAG} --repo theorem-labs/agent-infra "
                                 rf"--pattern theorem-session-linux-{arch}\.tar\.gz --dir {re.escape(str(self.tmpdir))}/\S+ "
                                 rf"token={TOKEN}$")
                self.assertIn(f"installed {TAG} (theorem-session-linux-{arch}.tar.gz)", result.stdout)
                self.assert_nothing_left_behind()

    def test_a_matching_installed_binary_is_a_no_op(self) -> None:
        self.serve("amd64")
        before = self.place(self.binary)
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [], "a matching binary mints no token and downloads nothing")
        after = self.installed.stat()
        self.assertEqual((after.st_ino, after.st_mtime_ns), (before.st_ino, before.st_mtime_ns))
        self.assertIn("already", result.stdout)
        self.assert_nothing_left_behind()

    def test_a_second_run_after_an_install_is_a_no_op(self) -> None:
        self.serve("amd64")
        self.assertEqual(self.install().returncode, 0)
        self.log.unlink()
        before = self.installed.stat()
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.installed.stat().st_ino, before.st_ino)

    def test_an_archive_digest_mismatch_installs_nothing(self) -> None:
        archive = self.serve("amd64", pin_archive=b"another archive")
        old = b"#!/bin/sh\necho an older theorem-session\n"
        before = self.place(old)
        result = self.install()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(sha256(archive), result.stderr)
        self.assertIn(sha256(b"another archive"), result.stderr)
        self.assertIn("installed nothing", result.stderr)
        self.assertEqual(self.installed.read_bytes(), old)
        self.assertEqual(self.installed.stat().st_ino, before.st_ino)
        self.assert_nothing_left_behind()

    def test_an_archive_digest_mismatch_with_nothing_installed_creates_nothing(self) -> None:
        self.serve("arm64", pin_archive=b"another archive")
        result = self.install(FAKE_MACHINE="aarch64")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(self.installed.exists())
        self.assert_nothing_left_behind()

    def test_a_binary_digest_mismatch_installs_nothing(self) -> None:
        self.serve("amd64", pin_binary=b"another binary")
        result = self.install()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(sha256(self.binary), result.stderr)
        self.assertIn(sha256(b"another binary"), result.stderr)
        self.assertFalse(self.installed.exists())
        self.assert_nothing_left_behind()

    def test_an_archive_without_the_binary_installs_nothing(self) -> None:
        self.serve("amd64", members={"bin/theorem-session": self.binary})
        result = self.install()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("holds no theorem-session", result.stderr)
        self.assertFalse(self.installed.exists())
        self.assert_nothing_left_behind()

    def test_a_different_installed_binary_is_replaced(self) -> None:
        self.serve("amd64")
        old = b"#!/bin/sh\necho a hand-built theorem-session\n"
        self.place(old)
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_installed()
        self.assertIn(f"replacing a binary with SHA-256 {sha256(old)}", result.stdout)
        self.assert_nothing_left_behind()

    def test_a_matching_binary_that_is_not_executable_or_is_a_link_is_installed(self) -> None:
        self.serve("amd64")
        self.place(self.binary, mode=0o644)
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_installed()
        self.log.unlink()
        target = self.root / "elsewhere"
        target.write_bytes(self.binary)
        target.chmod(0o755)
        self.installed.unlink()
        self.installed.symlink_to(target)
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.calls()), 2, self.calls())
        self.assert_installed()
        self.assertEqual(target.read_bytes(), self.binary)

    def test_a_directory_at_the_path_is_left_alone(self) -> None:
        self.serve("amd64")
        self.installed.mkdir(parents=True)
        result = self.install()
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(self.installed.iterdir()), [])
        self.assert_nothing_left_behind()

    def test_an_unpinned_platform_downloads_nothing(self) -> None:
        for system, machine in (("Darwin", "arm64"), ("Linux", "i686")):
            with self.subTest(system=system, machine=machine):
                result = self.install(FAKE_OS=system, FAKE_MACHINE=machine)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(f"{system} {machine}", result.stderr)
                self.assertEqual(self.calls(), [])
                self.assertFalse(self.installed.exists())

    def test_no_token_or_a_failed_download_installs_nothing(self) -> None:
        self.serve("amd64")
        result = self.install(TOKEN_FAILS="1")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("minted no token", result.stderr)
        self.assertFalse(any(call.startswith("gh ") for call in self.calls()), self.calls())
        result = self.install(STUB_TOKEN="")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(any(call.startswith("gh ") for call in self.calls()), self.calls())
        result = self.install(GH_FAILS="1")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("gh release download", result.stderr)
        self.assertFalse(self.installed.exists())
        self.assert_nothing_left_behind()


if __name__ == "__main__":
    unittest.main()
