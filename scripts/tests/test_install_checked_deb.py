#!/usr/bin/env python3
"""Tests for install_checked_deb in installers/lib.sh."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
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
            f'''if [[ "${{1:-}}" == --compare-versions ]]; then
    exec /usr/bin/dpkg "$@"
fi
FAKE_COMMAND=dpkg {logger}
exit "${{DPKG_STATUS:-0}}"''',
        )
        write_command(
            self.bin_dir,
            "dpkg-query",
            '''if [[ -v DPKG_QUERY_OUTPUT ]]; then
    printf '%s' "$DPKG_QUERY_OUTPUT"
    exit 0
fi
echo "dpkg-query: no packages found matching ${!#}" >&2
exit 1''',
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
        deb = self.build_deb(depends="debconf (>= 0.5),\n rsync")
        control = subprocess.run(
            ["dpkg-deb", "--info", str(deb), "control"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        self.assertIn("Depends: debconf (>= 0.5),\n rsync\n", control)

        result = self.run_helper(deb, sha256_file(deb))
        self.assertEqual(result.returncode, 0, result.stderr)

        calls = self.read_calls()
        self.assertEqual(
            calls[0]["argv"],
            ["satisfy", "-y", "--no-install-recommends", "debconf (>= 0.5), rsync"],
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

    def test_newer_installed_version_is_refused(self) -> None:
        deb = self.build_deb(depends="rsync")
        result = self.run_handled_failure(
            deb,
            sha256_file(deb),
            {"DPKG_QUERY_OUTPUT": "install installed 2.0"},
        )
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("checked-deb-control 2.0 is installed, newer than 1.0", result.stderr)
        self.assertIn("installed nothing", result.stderr)
        self.assertEqual(self.read_calls(), [])

    def test_package_held_at_another_version_is_refused(self) -> None:
        deb = self.build_deb(depends="rsync")
        result = self.run_handled_failure(
            deb,
            sha256_file(deb),
            {"DPKG_QUERY_OUTPUT": "hold installed 0.5"},
        )
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("checked-deb-control is on hold at 0.5", result.stderr)
        self.assertIn("installed nothing", result.stderr)
        self.assertEqual(self.read_calls(), [])

    def test_older_installed_version_is_upgraded(self) -> None:
        deb = self.build_deb(depends="rsync")
        result = self.run_helper(
            deb,
            sha256_file(deb),
            {"DPKG_QUERY_OUTPUT": "install installed 0.5"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        calls = self.read_calls()
        self.assertEqual([call["cmd"] for call in calls], ["apt-get", "dpkg"])
        self.assertEqual(calls[1]["argv"], ["-i", str(deb)])

    def test_package_held_at_the_same_version_is_installed(self) -> None:
        deb = self.build_deb(depends="rsync")
        result = self.run_helper(
            deb,
            sha256_file(deb),
            {"DPKG_QUERY_OUTPUT": "hold half-configured 1.0"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        calls = self.read_calls()
        self.assertEqual([call["cmd"] for call in calls], ["apt-get", "dpkg"])
        self.assertEqual(calls[1]["argv"], ["-i", str(deb)])

    def write_selection_state_fakes(self, state: Path) -> None:
        """Replace the fakes with ones that keep dpkg's selection and status in *state*.

        *state* holds "<want> <status> <version>". Like the real dpkg 1.22, `dpkg -i`
        sets the want back to install; `dpkg --set-selections` and `apt-mark` set it.
        """
        write_command(
            self.bin_dir,
            "dpkg-query",
            '''python3 - "$@" <<'PY'
import os
import sys

want, status, version = open(os.environ["SELECTION_STATE"]).read().split()
fmt = next(arg.removeprefix("-f=") for arg in sys.argv[1:] if arg.startswith("-f="))
for key, value in {
    "${db:Status-Want}": want,
    "${db:Status-Status}": status,
    "${db:Status-Abbrev}": want[0] + status[0] + " ",
    "${Status}": f"{want} ok {status}",
    "${Version}": version,
    "${Package}": "checked-deb-control",
}.items():
    fmt = fmt.replace(key, value)
sys.stdout.write(fmt.replace("\\\\n", "\\n"))
PY''',
        )
        write_command(
            self.bin_dir,
            "dpkg",
            r'''if [[ "${1:-}" == --compare-versions ]]; then
    exec /usr/bin/dpkg "$@"
fi
read -r want status version < "$SELECTION_STATE"
case "${1:-}" in
    -i) version="$(dpkg-deb --field "$2" Version)"; echo "install installed $version" > "$SELECTION_STATE" ;;
    --set-selections)
        while read -r package selection; do
            if [[ "$package" == checked-deb-control ]]; then echo "$selection $status $version" > "$SELECTION_STATE"; fi
        done ;;
esac
printf 'dpkg %s\n' "$*" >> "$CALL_LOG.selection"''',
        )
        write_command(
            self.bin_dir,
            "apt-mark",
            r'''read -r want status version < "$SELECTION_STATE"
case "${1:-}" in
    hold) echo "hold $status $version" > "$SELECTION_STATE" ;;
    unhold) echo "install $status $version" > "$SELECTION_STATE" ;;
    showhold) if [[ "$want" == hold ]]; then echo checked-deb-control; fi ;;
esac
printf 'apt-mark %s\n' "$*" >> "$CALL_LOG.selection"''',
        )

    def test_package_held_at_the_same_version_stays_held(self) -> None:
        # A package held at the pinned version but not fully installed (sysbox-ce after its
        # debconf script refused) is installed again from the checked file. apt-get install of
        # the same file keeps the hold (measured: `hold ok unpacked 1.104.4` became
        # `hold ok installed`); `dpkg -i` alone sets the want to install and drops it.
        deb = self.build_deb(depends="rsync")
        state = self.root / "selection-state"
        state.write_text("hold half-configured 1.0\n", encoding="utf-8")
        self.write_selection_state_fakes(state)

        result = self.run_helper(deb, sha256_file(deb), {"SELECTION_STATE": str(state)})
        self.assertEqual(result.returncode, 0, result.stderr)

        selection_log = Path(f"{self.log}.selection").read_text(encoding="utf-8")
        self.assertIn(f"dpkg -i {deb}", selection_log)
        self.assertEqual(state.read_text(encoding="utf-8").split(), ["hold", "installed", "1.0"])

    def test_newer_configuration_files_only_are_not_installed(self) -> None:
        deb = self.build_deb(depends="rsync")
        result = self.run_helper(
            deb,
            sha256_file(deb),
            {"DPKG_QUERY_OUTPUT": "deinstall config-files 2.0"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        calls = self.read_calls()
        self.assertEqual([call["cmd"] for call in calls], ["apt-get", "dpkg"])
        self.assertEqual(calls[1]["argv"], ["-i", str(deb)])

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
package=package-sentinel
version=version-sentinel
installed=installed-sentinel
want=want-sentinel
state=state-sentinel
installed_version=installed-version-sentinel
pre_depends=pre-depends-sentinel
depends=depends-sentinel
deps=(deps-zero deps-one)
trap 'echo bye' EXIT
cd "$CALLER_DIR"
state() {{
    declare -p deb expected actual package version installed want state installed_version pre_depends depends deps
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
