#!/usr/bin/env python3
"""Test marketplace version handling in installers/omp.sh.

Run with: python3 -m unittest -v scripts.tests.test_omp_marketplace_installer
The test extracts the installer functions and uses an omp stub, so it makes no
network requests and does not run the installer's setup steps.
"""

from __future__ import annotations

import base64
import json
import os
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


INSTALLER = Path(__file__).resolve().parents[2] / "installers/omp.sh"
PLUGIN = "engineering@agent-skills"
MARKETPLACE = "theorem-labs/verification-skills"
CATALOG = "agent-skills"
FUNCTION_NAMES = (
    "marketplace_plugin_version",
    "marketplace_version_at_least",
    "install_marketplace_plugin",
)

FAKE_OMP = r'''#!/usr/bin/env bash
set -euo pipefail

printf 'CALL %s\n' "$*" >> "$FAKE_OMP_LOG"

rewrite_lock() {
    local encoded lock="${DOTFILES_DIR}/omp/plugins/omp-plugins.lock.json"
    encoded="$(base64 < "$lock" | tr -d '\n')"
    printf 'LOCK %s\n' "$encoded" >> "$FAKE_OMP_LOG"
    printf '%s\n' 'other lock bytes' > "$lock"
}

if [ "$1" = plugin ] && [ "$2" = marketplace ] && [ "$3" = list ]; then
    printf '%s\n' "$FAKE_OMP_CATALOGS"
    exit 0
fi
if [ "$1" = plugin ] && [ "$2" = marketplace ] && [ "$3" = add ]; then
    exit 0
fi
if [ "$1" = plugin ] && [ "$2" = marketplace ] && [ "$3" = update ]; then
    rewrite_lock
    exit "$FAKE_OMP_UPDATE_RC"
fi
if [ "$1" = plugin ] && [ "$2" = upgrade ]; then
    rewrite_lock
    installed="${DOTFILES_DIR}/omp/plugins/installed_plugins.json"
    jq --arg plugin "$3" --arg version "$FAKE_OMP_UPGRADE_TO" \
        '.plugins[$plugin][0].version = $version' "$installed" > "$installed.tmp"
    mv "$installed.tmp" "$installed"
    exit "$FAKE_OMP_UPGRADE_RC"
fi
if [ "$1" = plugin ] && [ "$2" = install ]; then
    rewrite_lock
    if [ "$FAKE_OMP_INSTALL_RC" -ne 0 ]; then
        exit "$FAKE_OMP_INSTALL_RC"
    fi
    installed="${DOTFILES_DIR}/omp/plugins/installed_plugins.json"
    jq --arg plugin "$3" --arg version "$FAKE_OMP_INSTALL_TO" \
        '.plugins[$plugin] = [{"version": $version}]' "$installed" > "$installed.tmp"
    mv "$installed.tmp" "$installed"
    exit 0
fi

echo "unexpected omp arguments: $*" >&2
exit 99
'''


def write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def extract_functions(directory: Path) -> Path:
    source = INSTALLER.read_text(encoding="utf-8")
    definitions: list[str] = []
    for name in FUNCTION_NAMES:
        matches = re.findall(
            rf"^{re.escape(name)}\(\) \{{\n.*?^\}}\n", source, re.MULTILINE | re.DOTALL
        )
        if len(matches) != 1:
            raise AssertionError(f"{name}: found {len(matches)} definitions")
        definitions.append(matches[0])

    functions = directory / "omp-marketplace-functions.sh"
    functions.write_text("\n".join(definitions), encoding="utf-8")
    return functions


class OmpMarketplaceInstaller(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.home = self.root / "home"
        self.dotfiles = self.root / "dotfiles"
        self.plugins = self.dotfiles / "omp/plugins"
        self.bin = self.root / "bin"
        self.tmp = self.root / "tmp"
        for directory in (self.home, self.plugins, self.bin, self.tmp):
            directory.mkdir(parents=True)

        self.functions = extract_functions(self.root)
        write_executable(self.bin / "omp", FAKE_OMP)
        self.lock_path = self.plugins / "omp-plugins.lock.json"
        self.installed_path = self.plugins / "installed_plugins.json"
        self.log_path = self.root / "omp.log"
        self.env = {
            "HOME": str(self.home),
            "DOTFILES_DIR": str(self.dotfiles),
            "PATH": f"{self.bin}:/usr/local/bin:/usr/bin:/bin",
            "TMPDIR": str(self.tmp),
            "FAKE_OMP_CATALOGS": MARKETPLACE,
            "FAKE_OMP_LOG": str(self.log_path),
            "FAKE_OMP_UPDATE_RC": "0",
            "FAKE_OMP_UPGRADE_TO": "0.3.1",
            "FAKE_OMP_UPGRADE_RC": "0",
            "FAKE_OMP_INSTALL_TO": "0.3.1",
            "FAKE_OMP_INSTALL_RC": "0",
        }
        self.lock_bytes = b""

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def prepare(
        self,
        have: str | None,
        want: str,
        *,
        update_rc: int = 0,
        upgrade_to: str = "0.3.1",
        upgrade_rc: int = 0,
        install_to: str = "0.3.1",
        install_rc: int = 0,
    ) -> None:
        self.log_path.unlink(missing_ok=True)
        self.lock_bytes = (
            json.dumps({"plugins": {"engineering": {"version": want}}, "settings": {}}, separators=(",", ":"))
            + "\n"
        ).encode()
        self.lock_path.write_bytes(self.lock_bytes)
        plugins: dict[str, list[dict[str, str]]] = {}
        if have is not None:
            plugins[PLUGIN] = [{"version": have}]
        self.installed_path.write_text(json.dumps({"version": 2, "plugins": plugins}), encoding="utf-8")
        self.env.update({
            "FAKE_OMP_UPDATE_RC": str(update_rc),
            "FAKE_OMP_UPGRADE_TO": upgrade_to,
            "FAKE_OMP_UPGRADE_RC": str(upgrade_rc),
            "FAKE_OMP_INSTALL_TO": install_to,
            "FAKE_OMP_INSTALL_RC": str(install_rc),
        })

    def install(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "bash",
                "-c",
                'set -euo pipefail; source "$1"; install_marketplace_plugin "$2" "$3"; echo INSTALLER-STEP-RETURNED',
                "_",
                str(self.functions),
                MARKETPLACE,
                PLUGIN,
            ],
            capture_output=True,
            text=True,
            env=self.env,
            check=False,
        )

    def calls(self) -> list[str]:
        if not self.log_path.exists():
            return []
        return [line[5:] for line in self.log_path.read_text(encoding="utf-8").splitlines() if line.startswith("CALL ")]

    def lock_snapshots(self) -> list[bytes]:
        if not self.log_path.exists():
            return []
        return [
            base64.b64decode(line[5:])
            for line in self.log_path.read_text(encoding="utf-8").splitlines()
            if line.startswith("LOCK ")
        ]

    def assert_step(
        self,
        result: subprocess.CompletedProcess[str],
        calls: list[str],
        stderr_lines: list[str],
    ) -> None:
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.splitlines()[-1:], ["INSTALLER-STEP-RETURNED"], result.stdout)
        self.assertEqual(self.lock_path.read_bytes(), self.lock_bytes)
        self.assertEqual(self.calls(), calls)
        self.assertEqual(result.stderr.splitlines(), stderr_lines)

    def test_above_the_entry_checks_the_catalog_without_upgrading(self) -> None:
        self.prepare("0.3.2", "0.3.1")

        result = self.install()

        self.assert_step(
            result,
            ["plugin marketplace list", "plugin marketplace update agent-skills"],
            [],
        )

    def test_above_the_entry_reports_an_unavailable_catalog_without_upgrading(self) -> None:
        self.prepare("0.3.2", "0.3.1", update_rc=1)

        result = self.install()

        self.assert_step(
            result,
            ["plugin marketplace list", "plugin marketplace update agent-skills"],
            [
                "omp: marketplace agent-skills no longer resolves; "
                "engineering@agent-skills is pinned at 0.3.2 and cannot be upgraded"
            ],
        )

    def test_below_the_entry_still_warns_after_an_upgrade_below_it(self) -> None:
        self.prepare("0.3.0", "0.3.1", upgrade_to="0.3.0")

        result = self.install()

        self.assert_step(
            result,
            [
                "plugin marketplace list",
                "plugin marketplace update agent-skills",
                "plugin upgrade engineering@agent-skills",
            ],
            [
                "omp: engineering@agent-skills is 0.3.0, below 0.3.1, "
                "the lowest version omp-plugins.lock.json records"
            ],
        )

    def test_upgrade_to_above_the_entry_needs_no_warning(self) -> None:
        self.prepare("0.3.0", "0.3.1", upgrade_to="0.3.2")

        result = self.install()

        self.assert_step(
            result,
            [
                "plugin marketplace list",
                "plugin marketplace update agent-skills",
                "plugin upgrade engineering@agent-skills",
            ],
            [],
        )

    def test_numeric_components_compare_as_numbers(self) -> None:
        self.prepare("0.3.10", "0.3.9")

        result = self.install()

        self.assert_step(
            result,
            ["plugin marketplace list", "plugin marketplace update agent-skills"],
            [],
        )

    def test_non_numeric_versions_need_exact_equality(self) -> None:
        self.prepare("0.3.1-beta", "0.3.1", upgrade_to="0.3.1")

        upgraded = self.install()

        self.assert_step(
            upgraded,
            [
                "plugin marketplace list",
                "plugin marketplace update agent-skills",
                "plugin upgrade engineering@agent-skills",
            ],
            [],
        )

        self.prepare("partial", "0.3.1", upgrade_to="partial")

        unchanged = self.install()

        self.assert_step(
            unchanged,
            [
                "plugin marketplace list",
                "plugin marketplace update agent-skills",
                "plugin upgrade engineering@agent-skills",
            ],
            [
                "omp: engineering@agent-skills is partial, below 0.3.1, "
                "the lowest version omp-plugins.lock.json records"
            ],
        )

    def test_failed_catalog_refresh_reports_its_exit_code_and_restores_the_lock(self) -> None:
        self.prepare("0.3.0", "0.3.1", update_rc=8)

        result = self.install()

        self.assert_step(
            result,
            ["plugin marketplace list", "plugin marketplace update agent-skills"],
            [
                "omp: engineering@agent-skills: omp plugin exited 8",
                "omp: engineering@agent-skills is 0.3.0, below 0.3.1, "
                "the lowest version omp-plugins.lock.json records",
            ],
        )

    def test_failed_install_reports_its_exit_code_and_restores_the_lock(self) -> None:
        self.prepare(None, "0.3.1", install_rc=9)

        result = self.install()

        self.assert_step(
            result,
            ["plugin marketplace list", "plugin install engineering@agent-skills"],
            [
                "omp: engineering@agent-skills: omp plugin exited 9",
                "omp: installing engineering@agent-skills failed",
            ],
        )
        snapshots = self.lock_snapshots()
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(json.loads(snapshots[0]), {"plugins": {}, "settings": {}})

    def test_failed_upgrade_still_warns_when_the_version_stays_below_the_entry(self) -> None:
        self.prepare("0.3.0", "0.3.1", upgrade_to="0.3.0", upgrade_rc=7)

        result = self.install()

        self.assert_step(
            result,
            [
                "plugin marketplace list",
                "plugin marketplace update agent-skills",
                "plugin upgrade engineering@agent-skills",
            ],
            [
                "omp: engineering@agent-skills: omp plugin exited 7",
                "omp: engineering@agent-skills is 0.3.0, below 0.3.1, "
                "the lowest version omp-plugins.lock.json records",
            ],
        )

    def test_failed_upgrade_to_above_the_entry_has_no_below_warning(self) -> None:
        self.prepare("0.3.0", "0.3.1", upgrade_to="0.3.2", upgrade_rc=7)

        result = self.install()

        self.assert_step(
            result,
            [
                "plugin marketplace list",
                "plugin marketplace update agent-skills",
                "plugin upgrade engineering@agent-skills",
            ],
            ["omp: engineering@agent-skills: omp plugin exited 7"],
        )

    def test_version_at_least_table(self) -> None:
        cases = (
            ("0.3.1", "0.3.1", True),
            ("0.3.2", "0.3.1", True),
            ("0.3.0", "0.3.1", False),
            ("0.3.10", "0.3.9", True),
            ("0.3.9", "0.3.10", False),
            ("1.2.0", "1.2", True),
            ("1.2", "1.2.0", True),
            ("1.2", "1.2.1", False),
            ("0.3.09", "0.3.9", True),
            ("0.3.08", "0.3.9", False),
            ("10", "9", True),
            ("99999999999999999999.0", "9.0", True),
            ("partial", "2.0.0", False),
            ("2.0.0", "partial", False),
            ("partial", "partial", True),
            ("0.3.1-beta", "0.3.1", False),
            ("0.3.1", "0.3.1-beta", False),
            ("v1.0.0", "1.0.0", False),
            ("", "1.0.0", False),
        )
        for have, want, expected in cases:
            with self.subTest(have=have, want=want):
                # In a condition, as the installer calls it; a crash under set -u is exit 1 with
                # no answer line, never read as "no".
                result = subprocess.run(
                    [
                        "bash",
                        "-c",
                        'set -euo pipefail; source "$1"; '
                        'if marketplace_version_at_least "$2" "$3"; then echo yes; else echo no; fi',
                        "_",
                        str(self.functions),
                        have,
                        want,
                    ],
                    capture_output=True,
                    text=True,
                    env=self.env,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "yes\n" if expected else "no\n", result.stderr)


if __name__ == "__main__":
    unittest.main()
