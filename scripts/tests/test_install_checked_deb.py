#!/usr/bin/env python3
"""Tests for install_checked_deb in installers/lib.sh."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path


DOTFILES = Path(__file__).resolve().parents[2]
DEFAULT_LIBRARY = DOTFILES / "installers" / "lib.sh"
LIBRARY = Path(os.environ.get("INSTALL_CHECKED_DEB_LIB", DEFAULT_LIBRARY))


def write_command(directory: Path, name: str, body: str) -> None:
    """Write an executable Bash fake into *directory*."""
    path = directory / name
    path.write_text(f"#!/bin/bash\nset -euo pipefail\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest of *path*."""
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


@unittest.skipUnless(shutil.which("dpkg-deb"), "dpkg-deb is required")
class InstallCheckedDeb(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self.log = self.root / "calls.jsonl"
        self.build_number = 0
        self._write_fakes()
        self.env = os.environ.copy()
        self.env.update(
            {
                "HOME": str(self.root),
                "PATH": f"{self.bin_dir}:{self.env['PATH']}",
                "CALL_LOG": str(self.log),
                "INSTALL_CHECKED_DEB_LIB": str(LIBRARY),
            }
        )
        self.env.pop("DEBIAN_FRONTEND", None)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_fakes(self) -> None:
        logger = r'''python3 - "$@" <<'PY'
import json
import os
import sys

with open(os.environ["CALL_LOG"], "a", encoding="utf-8") as log:
    json.dump(
        {
            "cmd": os.environ["FAKE_COMMAND"],
            "argv": sys.argv[1:],
            "DEBIAN_FRONTEND": os.environ.get("DEBIAN_FRONTEND"),
        },
        log,
        separators=(",", ":"),
    )
    log.write("\n")
PY'''
        write_command(
            self.bin_dir,
            "apt-get",
            f'''FAKE_COMMAND=apt-get {logger}
exit "${{APT_STATUS:-0}}"''',
        )
        write_command(
            self.bin_dir,
            "dpkg",
            f'''FAKE_COMMAND=dpkg {logger}
exit "${{DPKG_STATUS:-0}}"''',
        )

    def build_deb(
        self,
        *,
        pre_depends: str | None = None,
        depends: str | None = None,
        recommends: str | None = None,
    ) -> Path:
        """Build a small package with the requested dependency fields."""
        self.build_number += 1
        package_root = self.root / f"package-{self.build_number}"
        control_dir = package_root / "DEBIAN"
        control_dir.mkdir(parents=True)
        fields = [
            "Package: checked-deb-control",
            "Version: 1.0",
            "Architecture: all",
            "Maintainer: Test <test@example.invalid>",
        ]
        if pre_depends is not None:
            fields.append(f"Pre-Depends: {pre_depends}")
        if depends is not None:
            fields.append(f"Depends: {depends}")
        if recommends is not None:
            fields.append(f"Recommends: {recommends}")
        fields.append("Description: package used by install_checked_deb tests")
        (control_dir / "control").write_text("\n".join(fields) + "\n", encoding="utf-8")

        deb = self.root / f"package-{self.build_number}.deb"
        subprocess.run(
            ["dpkg-deb", "--root-owner-group", "--build", str(package_root), str(deb)],
            check=True,
            capture_output=True,
            text=True,
        )
        return deb

    @staticmethod
    def _tar_gzip(files: dict[str, bytes]) -> bytes:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w", format=tarfile.GNU_FORMAT) as archive:
            for name, contents in files.items():
                entry = tarfile.TarInfo(name)
                entry.mode = 0o644
                entry.size = len(contents)
                entry.mtime = 0
                archive.addfile(entry, io.BytesIO(contents))
        return gzip.compress(buffer.getvalue(), mtime=0)

    @staticmethod
    def _ar_member(name: str, contents: bytes) -> bytes:
        header = (
            f"{name + '/':<16}{0:<12}{0:<6}{0:<6}{0o100644:<8o}{len(contents):<10}`\n".encode(
                "ascii"
            )
        )
        assert len(header) == 60
        return header + contents + (b"\n" if len(contents) % 2 else b"")

    def build_folded_deb(self) -> Path:
        """Build an ar package whose Depends field retains a folded line."""
        control = (
            "Package: checked-deb-folded\n"
            "Version: 1.0\n"
            "Architecture: all\n"
            "Maintainer: Test <test@example.invalid>\n"
            "Depends: debconf (>= 0.5),\n"
            " rsync\n"
            "Description: package with a folded dependency\n"
        ).encode("utf-8")
        control_tar = self._tar_gzip({"control": control})
        data_tar = self._tar_gzip({})
        deb = self.root / "folded.deb"
        deb.write_bytes(
            b"!<arch>\n"
            + self._ar_member("debian-binary", b"2.0\n")
            + self._ar_member("control.tar.gz", control_tar)
            + self._ar_member("data.tar.gz", data_tar)
        )
        return deb

    def raw_field(self, deb: Path, name: str) -> str:
        """Read a field as written in the package control file."""
        control_tar = subprocess.run(
            ["dpkg-deb", "--ctrl-tarfile", str(deb)],
            check=True,
            capture_output=True,
        ).stdout
        with tarfile.open(fileobj=io.BytesIO(control_tar), mode="r:*") as archive:
            control_member = next(
                (
                    member
                    for member in archive
                    if member.name.removeprefix("./") == "control"
                ),
                None,
            )
            if control_member is None:
                raise AssertionError("package control file is missing")
            control_file = archive.extractfile(control_member)
            if control_file is None:
                raise AssertionError("package control file is missing")
            control = control_file.read().decode("utf-8")

        prefix = f"{name}: "
        lines = control.splitlines(keepends=True)
        for index, line in enumerate(lines):
            if not line.startswith(prefix):
                continue
            value = line.removeprefix(prefix)
            for continuation in lines[index + 1 :]:
                if not continuation.startswith((" ", "\t")):
                    break
                value += continuation[1:]
            return value
        raise AssertionError(f"package control file has no {name} field")

    def field(self, deb: Path, name: str) -> str:
        """Read a package control field with the real dpkg-deb command."""
        return subprocess.run(
            ["dpkg-deb", "--field", str(deb), name],
            check=True,
            capture_output=True,
            text=True,
        ).stdout

    def read_calls(self) -> list[dict[str, object]]:
        """Read the fake command records in call order."""
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def run_shell(
        self, body: str, extra_env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run the helper through Bash with the test library and environment."""
        env = self.env.copy()
        if extra_env:
            env.update(extra_env)
        env.pop("DEBIAN_FRONTEND", None)
        return subprocess.run(
            [
                "bash",
                "-c",
                "set -euo pipefail\nsource \"$INSTALL_CHECKED_DEB_LIB\"\n" + body,
            ],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
        )

    def run_helper(
        self, deb: Path, expected: str, extra_env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run the helper normally for a package and expected digest."""
        env = {"DEB": str(deb), "EXPECTED": expected}
        if extra_env:
            env.update(extra_env)
        return self.run_shell('install_checked_deb "$DEB" "$EXPECTED"', env)

    def run_handled_failure(
        self, deb: Path, expected: str, extra_env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run the helper through || while preserving its failure status."""
        env = {"DEB": str(deb), "EXPECTED": expected}
        if extra_env:
            env.update(extra_env)
        return self.run_shell('install_checked_deb "$DEB" "$EXPECTED" || exit "$?"', env)

    def test_pre_depends_and_depends_are_satisfied_before_dpkg(self) -> None:
        deb = self.build_deb(
            pre_depends="jq",
            depends="debconf (>= 0.5) | debconf-2.0, rsync",
            recommends="unwanted-recommendation",
        )
        result = self.run_helper(deb, sha256_file(deb))
        self.assertEqual(result.returncode, 0, result.stderr)

        calls = self.read_calls()
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["cmd"], "apt-get")
        self.assertEqual(
            calls[0]["argv"],
            [
                "satisfy",
                "-y",
                "--no-install-recommends",
                "jq",
                "debconf (>= 0.5) | debconf-2.0, rsync",
            ],
        )
        self.assertEqual(calls[1]["cmd"], "dpkg")
        self.assertEqual(calls[1]["argv"], ["-i", str(deb)])
        self.assertEqual(calls[0]["DEBIAN_FRONTEND"], "noninteractive")
        self.assertEqual(calls[1]["DEBIAN_FRONTEND"], "noninteractive")
        for unwanted in ("unwanted-recommendation", str(deb), "checked-deb-control"):
            self.assertFalse(
                any(unwanted in argument for argument in calls[0]["argv"]),
                f"apt-get argv {calls[0]['argv']} holds {unwanted}",
            )

    def test_depends_only_omits_empty_argument(self) -> None:
        deb = self.build_deb(depends="rsync")
        result = self.run_helper(deb, sha256_file(deb))
        self.assertEqual(result.returncode, 0, result.stderr)

        calls = self.read_calls()
        self.assertEqual(calls[0]["argv"], ["satisfy", "-y", "--no-install-recommends", "rsync"])
        self.assertNotIn("", calls[0]["argv"])

    def test_pre_depends_only_omits_empty_argument(self) -> None:
        deb = self.build_deb(pre_depends="jq")
        result = self.run_helper(deb, sha256_file(deb))
        self.assertEqual(result.returncode, 0, result.stderr)

        calls = self.read_calls()
        self.assertEqual(calls[0]["argv"], ["satisfy", "-y", "--no-install-recommends", "jq"])
        self.assertNotIn("", calls[0]["argv"])

    def test_no_dependency_fields_skips_apt_get(self) -> None:
        deb = self.build_deb()
        result = self.run_helper(deb, sha256_file(deb))
        self.assertEqual(result.returncode, 0, result.stderr)

        self.assertEqual(
            self.read_calls(),
            [{"cmd": "dpkg", "argv": ["-i", str(deb)], "DEBIAN_FRONTEND": "noninteractive"}],
        )

    def test_folded_depends_is_passed_unchanged(self) -> None:
        deb = self.build_folded_deb()
        depends_output = self.raw_field(deb, "Depends")
        depends = depends_output.rstrip("\n")
        self.assertIn("\n", depends)
        expected_depends = self.field(deb, "Depends").rstrip("\n")

        result = self.run_helper(deb, sha256_file(deb))
        self.assertEqual(result.returncode, 0, result.stderr)

        calls = self.read_calls()
        self.assertEqual(
            calls[0]["argv"],
            ["satisfy", "-y", "--no-install-recommends", expected_depends],
        )

    def test_wrong_sha256_returns_one_without_install(self) -> None:
        deb = self.build_deb()
        actual = sha256_file(deb)
        expected = "0" * 64 if actual != "0" * 64 else "1" * 64
        result = self.run_shell(
            'install_checked_deb "$DEB" "$EXPECTED" || echo "rc=$?"',
            {"DEB": str(deb), "EXPECTED": expected},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("rc=1", result.stdout)
        self.assertIn("SHA-256", result.stderr)
        self.assertIn(actual, result.stderr)
        self.assertIn(expected, result.stderr)
        self.assertEqual(self.read_calls(), [])

    def test_invalid_deb_field_read_returns_failure_without_install(self) -> None:
        deb = self.root / "not-a-deb"
        deb.write_bytes(b"not a Debian package\n")
        result = self.run_handled_failure(deb, sha256_file(deb))
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read_calls(), [])

    def test_apt_failure_stops_before_dpkg(self) -> None:
        deb = self.build_deb(depends="rsync")
        result = self.run_handled_failure(
            deb,
            sha256_file(deb),
            {"APT_STATUS": "100"},
        )
        self.assertNotEqual(result.returncode, 0, result.stderr)

        calls = self.read_calls()
        self.assertEqual([call["cmd"] for call in calls], ["apt-get"])

    def test_dpkg_failure_is_returned(self) -> None:
        deb = self.build_deb()
        result = self.run_handled_failure(
            deb,
            sha256_file(deb),
            {"DPKG_STATUS": "2"},
        )
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual([call["cmd"] for call in self.read_calls()], ["dpkg"])

    def assert_caller_state(self, deb: Path, expected: str, handled_failure: bool) -> None:
        """Compare caller state before and after a successful or handled call."""
        caller_dir = self.root / ("caller-failure" if handled_failure else "caller-success")
        caller_dir.mkdir()
        before = self.root / ("before-failure" if handled_failure else "before-success")
        after = self.root / ("after-failure" if handled_failure else "after-success")
        call = (
            'failure_status=0\ninstall_checked_deb "$DEB" "$EXPECTED" || failure_status=$?\n'
            '[[ "$failure_status" -eq 1 ]]'
            if handled_failure
            else 'install_checked_deb "$DEB" "$EXPECTED"'
        )
        result = self.run_shell(
            f'''deb=deb-sentinel
expected=expected-sentinel
actual=actual-sentinel
pre_depends=pre-depends-sentinel
depends=depends-sentinel
deps=(deps-zero deps-one)
trap 'echo bye' EXIT
cd "$CALLER_DIR"
state() {{
    declare -p deb expected actual pre_depends depends deps
    trap -p EXIT
    printf 'shell-options=%s\\n' "$-"
    shopt -o pipefail
    pwd
}}
state > "$STATE_BEFORE"
{call}
state > "$STATE_AFTER"
[[ ! -v DEBIAN_FRONTEND ]]''',
            {
                "CALLER_DIR": str(caller_dir),
                "DEB": str(deb),
                "EXPECTED": expected,
                "STATE_BEFORE": str(before),
                "STATE_AFTER": str(after),
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before.read_text(encoding="utf-8"), after.read_text(encoding="utf-8"), result.stderr)

    def test_caller_state_stays_unchanged(self) -> None:
        deb = self.build_deb(
            pre_depends="jq",
            depends="debconf (>= 0.5) | debconf-2.0, rsync",
            recommends="unwanted-recommendation",
        )
        actual = sha256_file(deb)
        self.assert_caller_state(deb, actual, handled_failure=False)
        wrong = "0" * 64 if actual != "0" * 64 else "1" * 64
        self.assert_caller_state(deb, wrong, handled_failure=True)


if __name__ == "__main__":
    unittest.main()
