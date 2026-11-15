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

from scripts.tests.marketplace_version_cases import CASES, MISSING, RAW_CASES


INSTALLER = Path(__file__).resolve().parents[2] / "installers/omp.sh"
PLUGIN = "engineering@agent-skills"
MARKETPLACE = "theorem-labs/verification-skills"
CATALOG = "agent-skills"
FUNCTION_NAMES = (
    "marketplace_version_text",
    "marketplace_plugin_version",
    "marketplace_lock_version",
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
    if [ -n "$FAKE_OMP_UPGRADE_TO_JSON" ]; then
        jq --arg plugin "$3" --argjson version "$FAKE_OMP_UPGRADE_TO_JSON" \
            '.plugins[$plugin][0].version = $version' "$installed" > "$installed.tmp"
    else
        jq --arg plugin "$3" --arg version "$FAKE_OMP_UPGRADE_TO" \
            '.plugins[$plugin][0].version = $version' "$installed" > "$installed.tmp"
    fi
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
            "FAKE_OMP_UPGRADE_TO_JSON": "",
            "FAKE_OMP_UPGRADE_RC": "0",
            "FAKE_OMP_INSTALL_TO": "0.3.1",
            "FAKE_OMP_INSTALL_RC": "0",
        }
        self.lock_bytes = b""

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def prepare(
        self,
        have: object | None,
        want: object,
        *,
        update_rc: int = 0,
        upgrade_to: str = "0.3.1",
        upgrade_to_json: object | None = None,
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
        plugins: dict[str, list[dict[str, object]]] = {}
        if have is not None:
            plugins[PLUGIN] = [{"version": have}]
        self.installed_path.write_text(json.dumps({"version": 2, "plugins": plugins}), encoding="utf-8")
        self.env.update({
            "FAKE_OMP_UPDATE_RC": str(update_rc),
            "FAKE_OMP_UPGRADE_TO": upgrade_to,
            "FAKE_OMP_UPGRADE_TO_JSON": (
                "" if upgrade_to_json is None else json.dumps(upgrade_to_json, separators=(",", ":"))
            ),
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
                'omp: engineering@agent-skills is "partial", below 0.3.1, '
                "the lowest version omp-plugins.lock.json records"
            ],
        )

    def test_json_number_and_json_looking_string_are_not_equal(self) -> None:
        self.prepare(1, "json:1", upgrade_to="json:1")

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
        installed = json.loads(self.installed_path.read_text(encoding="utf-8"))
        self.assertEqual(installed["plugins"][PLUGIN][0]["version"], "json:1")

    def test_string_with_trailing_newline_is_not_a_dotted_number(self) -> None:
        self.prepare("2\n", "1", upgrade_to="2\n")

        result = self.install()

        self.assert_step(
            result,
            [
                "plugin marketplace list",
                "plugin marketplace update agent-skills",
                "plugin upgrade engineering@agent-skills",
            ],
            [
                'omp: engineering@agent-skills is "2\\n", below 1, '
                "the lowest version omp-plugins.lock.json records"
            ],
        )

    def test_missing_installed_record_reads_as_empty(self) -> None:
        self.prepare(None, "1")
        self.installed_path.unlink()

        result = subprocess.run(
            [
                "bash",
                "-c",
                'set -euo pipefail; source "$1"; value="$(marketplace_plugin_version "$2")"; '
                'printf "<%s>\\n" "$value"',
                "_",
                str(self.functions),
                PLUGIN,
            ],
            capture_output=True,
            text=True,
            env=self.env,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "<>\n")

    def test_unreadable_lock_stops_the_installer(self) -> None:
        self.prepare("1", "1")
        self.lock_path.unlink()
        self.lock_path.mkdir()

        result = subprocess.run(
            [
                "bash",
                "-c",
                'set -euo pipefail; source "$1"; install_marketplace_plugin "$2" "$3"; '
                "echo UNREACHABLE",
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

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.calls(), ["plugin marketplace list"])

    def test_non_string_installed_version_is_upgraded(self) -> None:
        self.prepare(2, "1", upgrade_to="2")

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
        installed = json.loads(self.installed_path.read_text(encoding="utf-8"))
        self.assertEqual(installed["plugins"][PLUGIN][0]["version"], "2")

    def test_non_string_installed_objects_are_reported_after_upgrade(self) -> None:
        cases = (
            ({"b": 2, "a": 1}, {"a": 1, "b": 2}),
            ({"a": 1, "b": 2}, {"b": 2, "a": 1}),
        )
        for have, want in cases:
            with self.subTest(have=have, want=want):
                self.prepare(have, want, upgrade_to_json=have)

                result = self.install()

                self.assert_step(
                    result,
                    [
                        "plugin marketplace list",
                        "plugin marketplace update agent-skills",
                        "plugin upgrade engineering@agent-skills",
                    ],
                    [
                        "omp: engineering@agent-skills is recorded as "
                        f"{json.dumps(have, separators=(',', ':'))}, which is not a version string"
                    ],
                )

    def test_non_string_lock_version_is_reported(self) -> None:
        self.prepare("1", 1, upgrade_to="1")

        result = self.install()

        self.assert_step(
            result,
            [
                "plugin marketplace list",
                "plugin marketplace update agent-skills",
                "plugin upgrade engineering@agent-skills",
            ],
            [
                "omp: omp-plugins.lock.json records 1 for engineering@agent-skills, "
                "which is not a version string"
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
        environment = self.env
        if "en_us.utf8" in subprocess.run(
            ["locale", "-a"], capture_output=True, text=True, check=False
        ).stdout.lower():
            environment = {**self.env, "LC_ALL": "en_US.UTF-8"}
        for have, want, expected in CASES:
            with self.subTest(have=have, want=want):
                installed = {
                    "version": 2,
                    "plugins": {
                        PLUGIN: [{}] if have is MISSING else [{"version": have}],
                    },
                }
                self.installed_path.write_text(json.dumps(installed), encoding="utf-8")
                lock = {
                    "plugins": {
                        "engineering": {} if want is MISSING else {"version": want},
                    },
                }
                self.lock_path.write_text(json.dumps(lock), encoding="utf-8")
                result = subprocess.run(
                    [
                        "bash",
                        "-c",
                        'set -euo pipefail; source "$1"; '
                        'if marketplace_version_at_least "$(marketplace_plugin_version "$2")" '
                        '"$(marketplace_lock_version engineering)"; then echo yes; else echo no; fi',
                        "_",
                        str(self.functions),
                        PLUGIN,
                    ],
                    capture_output=True,
                    text=True,
                    env=environment,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "yes\n" if expected else "no\n", result.stderr)

    def test_version_at_least_raw_json_table(self) -> None:
        environment = self.env
        if "en_us.utf8" in subprocess.run(
            ["locale", "-a"], capture_output=True, text=True, check=False
        ).stdout.lower():
            environment = {**self.env, "LC_ALL": "en_US.UTF-8"}
        for have_json_text, want_json_text, expected in RAW_CASES:
            with self.subTest(have=have_json_text, want=want_json_text):
                self.installed_path.write_text(
                    f'{{"version":2,"plugins":{{"{PLUGIN}":[{{"version":{have_json_text}}}]}}}}',
                    encoding="utf-8",
                )
                self.lock_path.write_text(
                    f'{{"plugins":{{"engineering":{{"version":{want_json_text}}}}}}}',
                    encoding="utf-8",
                )
                result = subprocess.run(
                    [
                        "bash",
                        "-c",
                        'set -euo pipefail; source "$1"; '
                        'if marketplace_version_at_least "$(marketplace_plugin_version "$2")" '
                        '"$(marketplace_lock_version engineering)"; then echo yes; else echo no; fi',
                        "_",
                        str(self.functions),
                        PLUGIN,
                    ],
                    capture_output=True,
                    text=True,
                    env=environment,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "yes\n" if expected else "no\n", result.stderr)

    @unittest.skipUnless(
        "en_us.utf8" in subprocess.run(["locale", "-a"], capture_output=True, text=True).stdout.lower(),
        "needs the en_US.UTF-8 locale, where bash's [0-9] also matches digits that are not ASCII",
    )
    def test_digits_that_are_not_ascii_need_equality_in_a_utf8_locale(self) -> None:
        # A person runs the installer in a UTF-8 locale. There bash's regex [0-9] also matches
        # digits such as U+0661 and U+FF11, while dotfiles-follow's Python [0-9] matches ASCII
        # alone, so the two read the same lock differently. Such a version is not dotted numbers,
        # and must equal the entry.
        cases = (
            ("\u0663.0", "2.0"),
            ("2", "\u0661"),
            ("1.\u0661", "1.0"),
            ("0.3.\uff11", "0.3.0"),
        )
        for have, want in cases:
            with self.subTest(have=have, want=want):
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
                    env={**self.env, "LC_ALL": "en_US.UTF-8"},
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "no\n", result.stderr)


if __name__ == "__main__":
    unittest.main()
