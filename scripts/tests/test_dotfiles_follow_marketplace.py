#!/usr/bin/env python3
"""Marketplace pins upgrade, report failures and recover a dirty follow-managed lock.

Run with: python3 -m unittest -v scripts.tests.test_dotfiles_follow_marketplace
The shared follower harness supplies throwaway git repositories and fake mise and bun;
only the shim below handles marketplace commands, with no real omp or network access.
"""

import json
import os
import signal
import subprocess
import sys
import time
import unittest
import unittest.mock
from pathlib import Path

from scripts.tests.test_dotfiles_follow import FOLLOW, TOOLS_PASSED, Harness, tools_passed_in
from scripts.tests.marketplace_version_cases import CASES, EMPTY_CASES, MISSING, RAW_CASES


FAKE_OMP = r'''#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import time
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
plugins = repo / "omp/plugins"
args = sys.argv[1:]
with Path(os.environ["FAKE_OMP_LOG"]).open("a") as log:
    log.write(json.dumps(args) + "\n")
data_log = os.environ.get("FAKE_OMP_DATA_LOG")
if data_log:
    with Path(data_log).open("a") as log:
        log.write(os.environ.get("MISE_DATA_DIR", "unset") + "\n")
# Both successful commands and midway failures can rewrite the tracked lock.
(plugins / "omp-plugins.lock.json").write_text('{"plugins": {}, "rewritten": true}\n')
if args[:3] == ["plugin", "marketplace", "update"] and len(args) == 4:
    time.sleep(float(os.environ.get("FAKE_OMP_DELAY", "0")))
    if args[3] == os.environ.get("FAKE_OMP_UPDATE_FAIL"):
        print("catalog refresh failed", file=sys.stderr)
        sys.exit(8)
    sys.exit(0)
if args[:2] == ["plugin", "upgrade"] and len(args) == 3:
    name, _, source = args[2].rpartition("@")
    installed_path = plugins / "installed_plugins.json"
    installed = json.loads(installed_path.read_text())
    record = installed["plugins"][args[2]][0]
    if name in os.environ.get("FAKE_OMP_FAIL", "").split(","):
        record["version"] = "partial"
        installed_path.write_text(json.dumps(installed))
        print("upgrade failed midway: " + name, file=sys.stderr)
        sys.exit(7)
    if name == os.environ.get("FAKE_OMP_HANG"):
        record["version"] = "partial"
        installed_path.write_text(json.dumps(installed))
        marker = Path(os.environ["FAKE_OMP_MARKER"])
        late = marker.with_suffix(".late")
        child = subprocess.Popen([
            sys.executable, "-c",
            "import pathlib, time; time.sleep(2); "
            + "pathlib.Path(" + repr(str(plugins / "omp-plugins.lock.json")) + ").write_text('late child write'); "
            + "pathlib.Path(" + repr(str(late)) + ").write_text('child survived')",
        ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        marker.write_text(json.dumps({"pid": child.pid, "deadline": time.monotonic() + 2}))
        time.sleep(30)
    record["version"] = json.loads(os.environ["FAKE_OMP_VERSIONS"])[name]
    installed_path.write_text(json.dumps(installed))
    if name == os.environ.get("FAKE_OMP_INVALID_BYTES"):
        os.write(1, b"\xff\n")
        sys.exit(7)
    sys.exit(0)
print("unexpected omp arguments: " + repr(args), file=sys.stderr)
sys.exit(99)
'''


class MarketplacePlugins(Harness):
    def setUp(self) -> None:
        super().setUp()
        self.omp_log = self.home.parent / "omp.log"
        os.environ["FAKE_OMP_LOG"] = str(self.omp_log)
        os.environ["FAKE_OMP_FAIL"] = ""
        os.environ["FAKE_OMP_UPDATE_FAIL"] = ""
        for key in ("FAKE_OMP_INVALID_BYTES", "FAKE_OMP_HANG", "FAKE_OMP_MARKER", "FAKE_OMP_DELAY"):
            os.environ.pop(key, None)
        os.environ["FAKE_OMP_VERSIONS"] = json.dumps({
            "dev-infra": "2.0.0", "verification-skills": "2.0.0", "core-ops": "2.0.0",
        })
        self.sources = {
            "dev-infra": "verification-skills",
            "verification-skills": "verification-skills",
            "core-ops": "core-ops",
            "slack": "claude-plugins-official",
            "@sjawhar/pi-envoy": "npm",
        }
        self.lock_v1 = json.dumps({
            "plugins": {
                name: {"version": "1.0.0", "enabledFeatures": None, "enabled": True}
                for name in self.sources
            },
            "settings": {},
        }, indent=2) + "\n"
        shim = self.upstream / "shims/omp"
        shim.parent.mkdir()
        shim.write_text(FAKE_OMP)
        shim.chmod(0o755)
        ignored = (self.upstream / ".gitignore").read_text()
        self.commit_upstream(
            "marketplace fixtures",
            **{
                "omp__plugins__omp-plugins.lock.json": self.lock_v1,
                ".gitignore": ignored + "omp/plugins/installed_plugins.json\n",
            },
        )
        self.take_upstream()
        installed = {
            "version": 2,
            "plugins": {
                f"{name}@{source}": [{
                    "scope": "user",
                    "installPath": str(self.repo / "omp/plugins/cache/plugins" / name),
                    "version": "1.0.0",
                    "installedAt": "2026-11-01T00:00:00.000Z",
                    "lastUpdated": "2026-11-01T00:00:00.000Z",
                }]
                for name, source in self.sources.items()
            },
        }
        self.installed_path = self.repo / "omp/plugins/installed_plugins.json"
        self.installed_path.write_text(json.dumps(installed))
        wanted = json.loads(self.lock_v1)
        for name in ("dev-infra", "verification-skills", "core-ops"):
            wanted["plugins"][name]["version"] = "2.0.0"
        # A lock entry for a Bun dependency must not be treated as a marketplace pin.
        wanted["plugins"]["@sjawhar/pi-envoy"]["version"] = "99.0.0"
        wanted["plugins"]["compound-engineering"] = {
            "version": "3.32.0", "enabledFeatures": None, "enabled": True,
        }
        self.want_lock = json.dumps(wanted, indent=2) + "\n"
        self.target = self.commit_upstream(
            "bump marketplace pins",
            **{"omp__plugins__omp-plugins.lock.json": self.want_lock},
        )

    def take_upstream(self) -> None:
        self.sh("git", "fetch", "-q", "origin", "main", cwd=self.repo)
        self.sh("git", "merge", "--ff-only", "--quiet", "origin/main", cwd=self.repo)

    def calls(self) -> list[list[str]]:
        return [json.loads(line) for line in self.omp_log.read_text().splitlines()] if self.omp_log.exists() else []

    def data_calls(self) -> list[str]:
        path = Path(os.environ["FAKE_OMP_DATA_LOG"])
        return path.read_text().splitlines() if path.exists() else []

    def version(self, name: str) -> str:
        return json.loads(self.installed_path.read_text())["plugins"][f"{name}@{self.sources[name]}"][0]["version"]

    def assert_clean_lock(self) -> None:
        self.assertEqual((self.repo / "omp/plugins/omp-plugins.lock.json").read_bytes(), self.want_lock.encode())
        self.assertEqual(self.sh("git", "status", "--porcelain", "--", "omp/plugins/omp-plugins.lock.json", cwd=self.repo), "")

    def commit_wanted_lock(self, wanted: dict) -> None:
        self.want_lock = json.dumps(wanted, indent=2) + "\n"
        self.target = self.commit_upstream(
            "change marketplace pin",
            **{"omp__plugins__omp-plugins.lock.json": self.want_lock},
        )
        self.take_upstream()

    def test_changed_pins_upgrade_unchanged_and_bun_pins_do_not(self) -> None:
        self.take_upstream()
        why = FOLLOW.install_tools(self.repo, self.options())
        self.assertEqual(why, "")
        self.assertEqual(self.calls(), [
            ["plugin", "marketplace", "update", "verification-skills"],
            ["plugin", "upgrade", "dev-infra@verification-skills"],
            ["plugin", "marketplace", "update", "verification-skills"],
            ["plugin", "upgrade", "verification-skills@verification-skills"],
            ["plugin", "marketplace", "update", "core-ops"],
            ["plugin", "upgrade", "core-ops@core-ops"],
        ])
        for name in ("dev-infra", "verification-skills", "core-ops"):
            self.assertEqual(self.version(name), "2.0.0")
        self.assertEqual(self.version("slack"), "1.0.0")
        self.assertEqual(self.version("@sjawhar/pi-envoy"), "1.0.0")
        self.assert_clean_lock()
        # Retrying tools at this HEAD must not re-upgrade anything already at its pin.
        self.assertEqual(FOLLOW.install_tools(self.repo, self.options()), "")
        self.assertEqual(len(self.calls()), 6)
        self.assert_clean_lock()

    def test_only_home_dotfiles_upgrades_marketplace_plugins(self) -> None:
        # Main's code ran shims/omp plugin marketplace update and plugin upgrade for any --repo, and
        # ~/.omp/plugins links to ~/.dotfiles/omp/plugins (installers/omp.sh), so an upgrade from
        # another checkout could write this machine's plugins. Both rows hold the lock entries of
        # the target commit (2.0.0) above the same installed records (1.0.0). A run of another clone
        # makes no omp call and logs why; its control, a run of ~/.dotfiles, upgrades as before.
        other = self.other_checkout()
        other_installed = other / "omp/plugins/installed_plugins.json"
        other_installed.write_text(self.installed_path.read_text())
        upgrades = [
            ["plugin", "marketplace", "update", "verification-skills"],
            ["plugin", "upgrade", "dev-infra@verification-skills"],
            ["plugin", "marketplace", "update", "verification-skills"],
            ["plugin", "upgrade", "verification-skills@verification-skills"],
            ["plugin", "marketplace", "update", "core-ops"],
            ["plugin", "upgrade", "core-ops@core-ops"],
        ]
        with self.subTest(checkout="another clone"):
            self.assertEqual((other / FOLLOW.MARKETPLACE_LOCK).read_text(), self.want_lock)
            before = other_installed.read_text()
            lines = self.logged_run(1_000_000.0, repo=other, provision=other / "devbox/sami-devbox-provision")
            self.assertEqual(self.calls(), [])
            self.assertIn(f"plugins: {other} is not ~/.dotfiles; marketplace plugins not upgraded", lines)
            self.assertIn(tools_passed_in(other), lines)
            self.assertEqual(other_installed.read_text(), before)
            self.assertEqual(self.sh("git", "status", "--porcelain", cwd=other), "")
        with self.subTest(checkout="~/.dotfiles"):
            before = len(self.calls())
            lines = self.logged_run(1_000_600.0)
            self.assertEqual(self.head(), self.target)
            self.assertEqual(self.calls()[before:], upgrades)
            self.assertFalse([line for line in lines if "marketplace plugins not upgraded" in line], lines)
            self.assertIn(TOOLS_PASSED, lines)
            for name in ("dev-infra", "verification-skills", "core-ops"):
                self.assertEqual(self.version(name), "2.0.0")
            self.assert_clean_lock()

    def test_marketplace_commands_get_the_mise_data_directory(self) -> None:
        self.assertNotIn("MISE_DATA_DIR", os.environ)
        os.environ["FAKE_OMP_DATA_LOG"] = str(self.home.parent / "omp-data.log")
        self.take_upstream()

        self.assertEqual(FOLLOW.install_tools(self.repo, self.options()), "")

        records = self.fake_log.read_text().splitlines()
        mise = next(record for record in records if record.startswith("install held "))
        self.assertEqual(mise.rsplit(" ", 1)[1], str(self.home / ".mise"))
        self.assertGreater(len(self.calls()), 0)
        self.assertEqual(self.data_calls(), [mise.rsplit(" ", 1)[1]] * len(self.calls()))

    def test_midway_failures_aggregate_and_do_not_stop_other_plugins(self) -> None:
        self.take_upstream()
        os.environ["FAKE_OMP_FAIL"] = "dev-infra,verification-skills"
        why = FOLLOW.install_tools(self.repo, self.options())
        self.assertIn("dev-infra@verification-skills", why)
        self.assertIn("verification-skills@verification-skills", why)
        self.assertIn("exited 7", why)
        self.assertIn("upgrade failed midway", why)
        self.assertEqual(self.version("dev-infra"), "partial")
        self.assertEqual(self.version("verification-skills"), "partial")
        self.assertEqual(self.version("core-ops"), "2.0.0")
        self.assertEqual(self.calls()[-1], ["plugin", "upgrade", "core-ops@core-ops"])
        self.assert_clean_lock()

    def test_unregistered_plugin_is_skipped_silently_and_follow_stays_clean(self) -> None:
        installed = json.loads(self.installed_path.read_text())
        del installed["plugins"]["dev-infra@verification-skills"]
        self.installed_path.write_text(json.dumps(installed))
        self.assertEqual(self.run_follow(), [])
        self.assertEqual(self.head(), self.target)
        self.assertEqual(self.state_data()["last_outcome"], "ok")
        self.assertEqual(self.state_data()["tools_ok_head"], self.target)
        self.assertEqual(self.calls(), [
            ["plugin", "marketplace", "update", "verification-skills"],
            ["plugin", "upgrade", "verification-skills@verification-skills"],
            ["plugin", "marketplace", "update", "core-ops"],
            ["plugin", "upgrade", "core-ops@core-ops"],
        ])
        self.assertNotIn("dev-infra@verification-skills", json.loads(self.installed_path.read_text())["plugins"])
        self.assertEqual(self.version("core-ops"), "2.0.0")
        self.assert_clean_lock()

    def test_ambiguous_marketplace_source_is_reported_and_other_plugins_continue(self) -> None:
        installed = json.loads(self.installed_path.read_text())
        installed["plugins"]["dev-infra@another-source"] = installed["plugins"]["dev-infra@verification-skills"]
        self.installed_path.write_text(json.dumps(installed))
        messages = self.run_follow()
        self.assertEqual(len(messages), 1)
        self.assertIn("TOOLS NOT INSTALLED", messages[0])
        self.assertIn("dev-infra", messages[0])
        self.assertIn("source", messages[0])
        self.assertEqual(self.version("core-ops"), "2.0.0")
        self.assertFalse(any(args[:2] == ["plugin", "upgrade"] and args[2].startswith("dev-infra@") for args in self.calls()))
        self.assertEqual(self.state_data()["last_outcome"], "tools")
        self.assert_clean_lock()

    def test_catalog_below_the_entry_is_a_tool_failure(self) -> None:
        self.take_upstream()
        os.environ["FAKE_OMP_VERSIONS"] = json.dumps({
            "dev-infra": "1.5.0", "verification-skills": "2.0.0", "core-ops": "2.0.0",
        })
        why = FOLLOW.install_tools(self.repo, self.options())
        self.assertIn("dev-infra", why)
        self.assertIn("1.5.0", why)
        self.assertIn("2.0.0", why)
        self.assertEqual(self.version("core-ops"), "2.0.0")
        self.assert_clean_lock()

    def test_installed_above_the_entry_runs_no_omp_command(self) -> None:
        self.take_upstream()
        installed = json.loads(self.installed_path.read_text())
        installed["plugins"]["dev-infra@verification-skills"][0]["version"] = "2.1.0"
        self.installed_path.write_text(json.dumps(installed))

        self.assertEqual(FOLLOW.install_tools(self.repo, self.options()), "")
        self.assertFalse(
            any(
                "dev-infra" in argument
                for args in self.calls()
                for argument in args
            )
        )
        self.assert_clean_lock()

    def test_upgrade_to_a_version_above_the_entry_passes(self) -> None:
        wanted = json.loads(self.want_lock)
        wanted["plugins"]["dev-infra"]["version"] = "0.3.1"
        self.commit_wanted_lock(wanted)
        installed = json.loads(self.installed_path.read_text())
        installed["plugins"]["dev-infra@verification-skills"][0]["version"] = "0.3.0"
        self.installed_path.write_text(json.dumps(installed))
        os.environ["FAKE_OMP_VERSIONS"] = json.dumps({
            "dev-infra": "0.3.2", "verification-skills": "2.0.0", "core-ops": "2.0.0",
        })

        self.assertEqual(FOLLOW.install_tools(self.repo, self.options()), "")
        self.assertIn(["plugin", "marketplace", "update", "verification-skills"], self.calls())
        self.assertIn(["plugin", "upgrade", "dev-infra@verification-skills"], self.calls())
        self.assertEqual(self.version("dev-infra"), "0.3.2")
        self.assert_clean_lock()

    def test_numeric_components_compare_as_numbers(self) -> None:
        wanted = json.loads(self.want_lock)
        wanted["plugins"]["dev-infra"]["version"] = "0.3.9"
        self.commit_wanted_lock(wanted)
        installed = json.loads(self.installed_path.read_text())
        installed["plugins"]["dev-infra@verification-skills"][0]["version"] = "0.3.10"
        self.installed_path.write_text(json.dumps(installed))

        self.assertEqual(FOLLOW.install_tools(self.repo, self.options()), "")
        self.assertFalse(
            any(
                "dev-infra" in argument
                for args in self.calls()
                for argument in args
            )
        )
        self.assert_clean_lock()

    def test_a_version_that_is_not_dotted_numbers_needs_equality(self) -> None:
        wanted = json.loads(self.want_lock)
        wanted["plugins"]["dev-infra"]["version"] = "0.3.1"
        self.commit_wanted_lock(wanted)
        installed = json.loads(self.installed_path.read_text())
        installed["plugins"]["dev-infra@verification-skills"][0]["version"] = "0.3.1-beta"
        self.installed_path.write_text(json.dumps(installed))
        os.environ["FAKE_OMP_VERSIONS"] = json.dumps({
            "dev-infra": "0.3.1", "verification-skills": "2.0.0", "core-ops": "2.0.0",
        })

        self.assertEqual(FOLLOW.install_tools(self.repo, self.options()), "")
        self.assertIn(["plugin", "upgrade", "dev-infra@verification-skills"], self.calls())
        self.assertEqual(self.version("dev-infra"), "0.3.1")
        self.assert_clean_lock()

        wanted["plugins"]["dev-infra"]["version"] = "2.0.0"
        self.commit_wanted_lock(wanted)
        installed = json.loads(self.installed_path.read_text())
        installed["plugins"]["dev-infra@verification-skills"][0]["version"] = "partial"
        self.installed_path.write_text(json.dumps(installed))
        os.environ["FAKE_OMP_VERSIONS"] = json.dumps({"dev-infra": "partial"})
        calls_before = len(self.calls())

        why = FOLLOW.install_tools(self.repo, self.options())
        self.assertIn("dev-infra", why)
        self.assertIn("partial", why)
        self.assertEqual(self.calls()[calls_before:], [
            ["plugin", "marketplace", "update", "verification-skills"],
            ["plugin", "upgrade", "dev-infra@verification-skills"],
        ])
        self.assert_clean_lock()

    def test_a_version_that_is_not_a_string_is_upgraded(self) -> None:
        self.take_upstream()
        installed = json.loads(self.installed_path.read_text())
        installed["plugins"]["dev-infra@verification-skills"][0]["version"] = None
        self.installed_path.write_text(json.dumps(installed))

        self.assertEqual(FOLLOW.install_tools(self.repo, self.options()), "")
        self.assertIn(["plugin", "marketplace", "update", "verification-skills"], self.calls())
        self.assertIn(["plugin", "upgrade", "dev-infra@verification-skills"], self.calls())
        self.assertEqual(self.version("dev-infra"), "2.0.0")
        self.assert_clean_lock()

    def test_reordered_non_string_value_needs_no_omp_command(self) -> None:
        wanted = json.loads(self.want_lock)
        wanted["plugins"]["dev-infra"]["version"] = {"a": 1, "b": 2}
        self.commit_wanted_lock(wanted)
        installed = json.loads(self.installed_path.read_text())
        installed["plugins"]["dev-infra@verification-skills"][0]["version"] = {"b": 2, "a": 1}
        self.installed_path.write_text(json.dumps(installed))

        self.assertEqual(FOLLOW.install_tools(self.repo, self.options()), "")

        self.assertFalse(
            any(
                "dev-infra" in argument
                for args in self.calls()
                for argument in args
            )
        )
        self.assert_clean_lock()

    def test_unequal_non_string_value_is_reported_after_upgrade(self) -> None:
        wanted = json.loads(self.want_lock)
        wanted["plugins"]["dev-infra"]["version"] = {"a": 2}
        self.commit_wanted_lock(wanted)
        installed = json.loads(self.installed_path.read_text())
        installed["plugins"]["dev-infra@verification-skills"][0]["version"] = {"a": 1}
        self.installed_path.write_text(json.dumps(installed))
        os.environ["FAKE_OMP_VERSIONS"] = json.dumps({
            "dev-infra": {"a": 1},
            "verification-skills": "2.0.0",
            "core-ops": "2.0.0",
        })

        why = FOLLOW.install_tools(self.repo, self.options())

        self.assertIn(["plugin", "marketplace", "update", "verification-skills"], self.calls())
        self.assertIn(["plugin", "upgrade", "dev-infra@verification-skills"], self.calls())
        self.assertIn(
            'dev-infra@verification-skills\'s installed record holds {"a": 1}, '
            "which is not a version string.",
            why,
        )
        self.assertNotIn("below", why)
        self.assert_clean_lock()

    def test_version_at_least_table(self) -> None:
        cases = CASES + tuple(
            (have, want, follow_expected)
            for have, want, follow_expected, _installer_expected in EMPTY_CASES
        )
        for have, want, expected in cases:
            with self.subTest(have=have, want=want):
                have = None if have is MISSING else have
                want = None if want is MISSING else want
                self.assertEqual(FOLLOW.version_at_least(have, want), expected)

    def test_version_at_least_raw_json_table(self) -> None:
        for have_json_text, want_json_text, expected in RAW_CASES:
            with self.subTest(have=have_json_text, want=want_json_text):
                self.assertEqual(
                    FOLLOW.version_at_least(json.loads(have_json_text), json.loads(want_json_text)),
                    expected,
                )

    def test_failed_catalog_update_does_not_upgrade_but_keeps_going(self) -> None:
        self.take_upstream()
        os.environ["FAKE_OMP_UPDATE_FAIL"] = "verification-skills"
        why = FOLLOW.install_tools(self.repo, self.options())
        self.assertIn("catalog refresh failed", why)
        self.assertIn("exited 8", why)
        self.assertEqual(self.calls(), [
            ["plugin", "marketplace", "update", "verification-skills"],
            ["plugin", "marketplace", "update", "verification-skills"],
            ["plugin", "marketplace", "update", "core-ops"],
            ["plugin", "upgrade", "core-ops@core-ops"],
        ])
        self.assertEqual(self.version("dev-infra"), "1.0.0")
        self.assertEqual(self.version("core-ops"), "2.0.0")
        self.assert_clean_lock()

    def test_checkout_failure_restores_saved_bytes(self) -> None:
        self.take_upstream()
        failed = subprocess.CompletedProcess([], 1, "", "checkout unavailable")
        with unittest.mock.patch.object(FOLLOW, "git", return_value=failed) as git:
            why = FOLLOW.install_tools(self.repo, self.options())
        self.assertEqual(why, "")
        self.assertTrue(any(call.args[1:] == ("checkout", "--", "omp/plugins/omp-plugins.lock.json") for call in git.call_args_list))
        self.assert_clean_lock()

    def test_follower_reports_failure_retries_at_same_head_and_clears(self) -> None:
        os.environ["FAKE_OMP_FAIL"] = "dev-infra"
        messages = self.run_follow()
        self.assertEqual(self.head(), self.target)
        self.assertEqual(len(messages), 1)
        self.assertIn("is on main, but a post-update step failed", messages[0].splitlines()[0])
        self.assertNotIn("NOT FOLLOWING MAIN", messages[0].splitlines()[0])
        self.assertIn("TOOLS NOT INSTALLED", messages[0])
        self.assertIn("dev-infra@verification-skills", messages[0])
        self.assertIn("next run tries again", messages[0])
        self.assertNotEqual(self.state_data().get("tools_ok_head"), self.target)
        self.assertEqual(self.state_data()["last_outcome"], "tools")
        self.assertEqual(len(self.sweeps()), 1)
        self.assert_clean_lock()
        os.environ["FAKE_OMP_FAIL"] = ""
        cleared = self.run_follow(now=1_000_600.0)
        self.assertEqual(len(cleared), 1)
        self.assertIn("TOOLS NOT INSTALLED cleared", cleared[0])
        self.assertEqual(self.state_data()["tools_ok_head"], self.target)
        self.assertEqual(self.state_data()["last_outcome"], "ok")
        self.assertEqual(self.calls()[6:], [
            ["plugin", "marketplace", "update", "verification-skills"],
            ["plugin", "upgrade", "dev-infra@verification-skills"],
        ])
        self.assertEqual(self.run_follow(now=1_001_200.0), [])
        self.assertEqual(len(self.calls()), 8)
        self.assert_clean_lock()

    def test_post_update_report_opens_once_with_on_main_headline(self) -> None:
        os.environ["FAKE_OMP_FAIL"] = "dev-infra"
        os.environ["FAKE_SWEEP"] = "failed"
        opened = self.run_follow()
        self.assertEqual(len(opened), 1)
        self.assertIn("is on main, but a post-update step failed", opened[0].splitlines()[0])
        self.assertNotIn("NOT FOLLOWING MAIN", opened[0].splitlines()[0])
        self.assertIn("TOOLS NOT INSTALLED", opened[0])
        self.assertIn("PRE-PUSH HOOK NOT INSTALLED", opened[0])
        self.assertEqual(self.head(), self.target)
        self.assertEqual(self.run_follow(now=1_000_600.0), [])
        # Not reported again while it holds, even two hours later.
        self.assertEqual(self.run_follow(now=1_007_200.0), [])
        os.environ["FAKE_OMP_FAIL"] = ""
        os.environ["FAKE_SWEEP"] = "ok"
        cleared = self.run_follow(now=1_008_400.0)
        self.assertEqual(len(cleared), 1)
        self.assertIn("TOOLS NOT INSTALLED cleared", cleared[0])
        self.assertIn("PRE-PUSH HOOK NOT INSTALLED cleared", cleared[0])
        self.assertEqual(self.state_data()["last_outcome"], "ok")

    def test_follow_failure_takes_headline_priority_over_hook_failure_at_main(self) -> None:
        self.assertEqual(self.run_follow(), [])
        (self.repo / "scripts/tool").write_text("another person's edit\n")
        os.environ["FAKE_SWEEP"] = "failed"
        opened = self.run_follow(now=1_000_600.0)
        self.assertEqual(len(opened), 1)
        self.assertIn("DOTFILES NOT FOLLOWING MAIN", opened[0].splitlines()[0])
        self.assertIn("UNCOMMITTED CHANGES", opened[0])
        self.assertIn("PRE-PUSH HOOK NOT INSTALLED", opened[0])
        self.assertEqual(self.head(), self.target)
        self.assertEqual((self.repo / "scripts/tool").read_text(), "another person's edit\n")

    def test_existing_follow_failure_keeps_priority_when_only_new_hook_failure_opens(self) -> None:
        self.assertEqual(self.run_follow(), [])
        data = self.state_data()
        data["tools_ok_head"] = None
        self.state.write_text(json.dumps(data))
        self.sh("git", "remote", "set-url", "origin", str(self.home / "missing.git"), cwd=self.repo)
        os.environ["FAKE_MISE_RC"] = "1"
        opened = self.run_follow(now=1_000_600.0, fetch_grace=1)
        self.assertEqual(len(opened), 1)
        self.assertIn("DOTFILES NOT FOLLOWING MAIN", opened[0].splitlines()[0])
        self.assertIn("CANNOT FETCH origin/main", opened[0])
        self.assertIn("TOOLS NOT INSTALLED", opened[0])
        self.assertEqual(self.head(), self.target)
        os.environ["FAKE_SWEEP"] = "failed"
        newer = self.run_follow(now=1_001_200.0, fetch_grace=1)
        self.assertEqual(len(newer), 1)
        self.assertIn("DOTFILES NOT FOLLOWING MAIN", newer[0].splitlines()[0])
        self.assertIn("PRE-PUSH HOOK NOT INSTALLED", newer[0])
        self.assertEqual(set(self.state_data()["incidents"]), {"fetch", "tools", "hooks"})

    def wait_for_child_write_window(self, marker: Path) -> None:
        child = json.loads(marker.read_text())
        try:
            time.sleep(max(0, child["deadline"] - time.monotonic()) + 0.2)
            self.assert_clean_lock()
            self.assertFalse(marker.with_suffix(".late").exists(), "omp's child survived cleanup")
        finally:
            try:
                os.kill(child["pid"], signal.SIGKILL)
            except ProcessLookupError:
                pass

    def test_timeout_kills_delayed_child_before_restoring_lock(self) -> None:
        self.take_upstream()
        marker = self.home.parent / "timeout-child.json"
        os.environ["FAKE_OMP_HANG"] = "dev-infra"
        os.environ["FAKE_OMP_MARKER"] = str(marker)
        why = FOLLOW.install_tools(self.repo, self.options(mise_timeout=0.5))
        self.assertIn("timed out", why)
        self.assertEqual(self.version("core-ops"), "2.0.0")
        self.wait_for_child_write_window(marker)

    def assert_dirty_lock_recovers(self, content: bytes | None, staged: bool = False) -> None:
        lock = self.repo / "omp/plugins/omp-plugins.lock.json"
        if content is None:
            lock.unlink()
        else:
            lock.write_bytes(content)
        if staged:
            self.sh("git", "add", "omp/plugins/omp-plugins.lock.json", cwd=self.repo)
        git = FOLLOW.git
        saw_merge = []

        def observe_merge(repo, *args, **kwargs):
            if args[:2] == ("merge", "--ff-only"):
                # Recovery is to HEAD, before the fast-forward installs the new pin bytes.
                self.assertEqual(lock.read_bytes(), self.lock_v1.encode())
                self.assertEqual(self.sh("git", "status", "--porcelain", "--", "omp/plugins/omp-plugins.lock.json", cwd=self.repo), "")
                saw_merge.append(True)
            return git(repo, *args, **kwargs)

        with unittest.mock.patch.object(FOLLOW, "git", side_effect=observe_merge):
            messages = self.run_follow()
        self.assertEqual(messages, [])
        self.assertTrue(saw_merge, "the follower never attempted the fast-forward")
        self.assertEqual(self.head(), self.target)
        self.assertEqual(self.state_data()["tools_ok_head"], self.target)
        for name in ("dev-infra", "verification-skills", "core-ops"):
            self.assertEqual(self.version(name), "2.0.0")
        self.assertEqual(len(self.calls()), 6)
        self.assert_clean_lock()

    def test_partial_lock_is_restored_before_fast_forward_and_plugins_install(self) -> None:
        self.assert_dirty_lock_recovers(b'{"plugins":')

    def test_wrong_version_lock_is_restored_before_fast_forward_and_plugins_install(self) -> None:
        self.assert_dirty_lock_recovers(self.want_lock.replace("2.0.0", "999.0.0").encode())

    def test_garbage_lock_is_restored_before_fast_forward_and_plugins_install(self) -> None:
        self.assert_dirty_lock_recovers(b"\xff\x00not JSON")

    def test_target_equal_lock_is_restored_before_fast_forward_and_plugins_install(self) -> None:
        self.assert_dirty_lock_recovers(self.want_lock.encode())

    def test_staged_lock_is_restored_before_fast_forward_and_plugins_install(self) -> None:
        self.assert_dirty_lock_recovers(b"staged anomaly", staged=True)

    def test_deleted_lock_is_restored_before_fast_forward_and_plugins_install(self) -> None:
        self.assert_dirty_lock_recovers(None)

    def test_current_head_recovers_dirty_lock_and_rechecks_installed_pins(self) -> None:
        self.assertEqual(self.run_follow(), [])
        installed = json.loads(self.installed_path.read_text())
        installed["plugins"]["dev-infra@verification-skills"][0]["version"] = "1.0.0"
        self.installed_path.write_text(json.dumps(installed))
        (self.repo / "omp/plugins/omp-plugins.lock.json").write_text("interrupted upgrade")
        self.assertEqual(self.run_follow(now=1_000_600.0), [])
        self.assertEqual(self.head(), self.target)
        self.assertEqual(self.version("dev-infra"), "2.0.0")
        self.assertEqual(self.calls()[6:], [
            ["plugin", "marketplace", "update", "verification-skills"],
            ["plugin", "upgrade", "dev-infra@verification-skills"],
        ])
        self.assertEqual(self.state_data()["tools_ok_head"], self.target)
        self.assert_clean_lock()

    def test_sigterm_during_same_head_recovery_does_not_lose_tools_retry(self) -> None:
        self.assertEqual(self.run_follow(), [])
        installed = json.loads(self.installed_path.read_text())
        installed["plugins"]["dev-infra@verification-skills"][0]["version"] = "1.0.0"
        self.installed_path.write_text(json.dumps(installed))
        (self.repo / "omp/plugins/omp-plugins.lock.json").write_text(self.lock_v1)
        # This run is FOLLOW.main() in a child, on the real clock and the real --slow-sweep-interval:
        # a slow-root pass ended just now, so it is not overdue and the run does not leave its tools
        # step to the next run (dotfiles-follow.md step 8).
        data = self.state_data()
        data["slow_sweep_at"] = time.time()
        self.state.write_text(json.dumps(data))
        mise = self.home.parent / "bin/mise"
        original_mise = mise.read_text()
        marker = self.home.parent / "mise-recovery.pid"
        mise.write_text(
            "#!/usr/bin/env python3\nimport os, sys, time\nfrom pathlib import Path\n"
            + "if sys.argv[1:] == ['install']:\n"
            + f"    Path({str(marker)!r}).write_text(str(os.getpid()))\n"
            + "    time.sleep(30)\n"
        )
        driver = "from scripts.tests.test_dotfiles_follow import FOLLOW; raise SystemExit(FOLLOW.main())"
        proc = subprocess.Popen([
            sys.executable, "-c", driver,
            "--repo", str(self.repo), "--state", str(self.state), "--hooks-dir", str(self.hooks_dir),
        ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
        try:
            deadline = time.monotonic() + 10
            while not marker.exists() and proc.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            if not marker.exists():
                proc.terminate()
                output, _ = proc.communicate(timeout=10)
                self.fail(f"the recovery run never reached mise install:\n{output}")
            self.assert_clean_lock()
            self.assertEqual(self.version("dev-infra"), "1.0.0")
            proc.send_signal(signal.SIGTERM)
            output, _ = proc.communicate(timeout=10)
            self.assertNotEqual(proc.returncode, 0, output)
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            if marker.exists():
                try:
                    os.killpg(int(marker.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass
            mise.write_text(original_mise)

        # Back on the harness's clock (now = 1_000_600), where that real time is in the future.
        data = self.state_data()
        data["slow_sweep_at"] = 999_000.0
        self.state.write_text(json.dumps(data))
        checkpoint = self.state_data().get("tools_ok_head")
        self.assertEqual(self.run_follow(now=1_000_600.0), [])
        self.assertEqual(self.version("dev-infra"), "2.0.0")
        self.assertIsNone(checkpoint)
        self.assertEqual(self.calls()[6:], [
            ["plugin", "marketplace", "update", "verification-skills"],
            ["plugin", "upgrade", "dev-infra@verification-skills"],
        ])
        self.assertEqual(self.state_data()["tools_ok_head"], self.target)
        self.assert_clean_lock()
        self.assertEqual(self.run_follow(now=1_001_200.0), [])
        self.assertEqual(len(self.calls()), 8)

    def test_same_head_recovery_with_matching_pins_does_not_call_omp(self) -> None:
        self.assertEqual(self.run_follow(), [])
        (self.repo / "omp/plugins/omp-plugins.lock.json").write_text("local anomaly")
        git = FOLLOW.git

        def check_checkpoint(repo, *args, **kwargs):
            if args == ("checkout", "HEAD", "--", "omp/plugins/omp-plugins.lock.json"):
                self.assertIsNone(self.state_data()["tools_ok_head"])
                other = FOLLOW.State(self.state)
                try:
                    self.assertFalse(other.locked, "recovery released the state lock")
                finally:
                    other.close()
            return git(repo, *args, **kwargs)

        with unittest.mock.patch.object(FOLLOW, "git", side_effect=check_checkpoint):
            self.assertEqual(self.run_follow(now=1_000_600.0), [])
        self.assertEqual(len(self.calls()), 6)
        self.assertEqual(self.state_data()["tools_ok_head"], self.target)
        self.assert_clean_lock()

    def test_lock_recovery_does_not_discard_other_dirty_files(self) -> None:
        (self.repo / "omp/plugins/omp-plugins.lock.json").write_text("interrupted upgrade")
        (self.repo / "scripts/tool").write_text("somebody else's edit\n")
        messages = self.run_follow()
        self.assertEqual(len(messages), 1)
        self.assertIn("UNCOMMITTED CHANGES", messages[0])
        self.assertIn(" M scripts/tool", messages[0])
        self.assertNotIn(" M omp/plugins/omp-plugins.lock.json", messages[0])
        self.assertEqual((self.repo / "scripts/tool").read_text(), "somebody else's edit\n")
        self.assertEqual((self.repo / "omp/plugins/omp-plugins.lock.json").read_text(), self.lock_v1)
        self.assertEqual(self.calls(), [])

    def test_timed_out_upgrade_with_failed_restore_recovers_on_next_run(self) -> None:
        marker = self.home.parent / "recovery-child.json"
        os.environ["FAKE_OMP_HANG"] = "dev-infra"
        os.environ["FAKE_OMP_MARKER"] = str(marker)
        git = FOLLOW.git

        def fail_cleanup(repo, *args, **kwargs):
            if args == ("checkout", "--", "omp/plugins/omp-plugins.lock.json"):
                return subprocess.CompletedProcess(args, 1, "", "cleanup interrupted")
            return git(repo, *args, **kwargs)

        with unittest.mock.patch.object(FOLLOW, "git", side_effect=fail_cleanup):
            with unittest.mock.patch.object(Path, "write_bytes", side_effect=OSError("cleanup interrupted")):
                messages = self.run_follow(mise_timeout=0.5)
        self.assertEqual(len(messages), 1)
        self.assertIn("timed out", messages[0])
        self.assertIn("Cannot restore", messages[0])
        self.assertNotEqual((self.repo / "omp/plugins/omp-plugins.lock.json").read_text(), self.want_lock)
        self.assertEqual(self.state_data()["last_outcome"], "tools")
        self.assertNotEqual(self.state_data().get("tools_ok_head"), self.target)
        os.environ["FAKE_OMP_HANG"] = ""
        cleared = self.run_follow(now=1_000_600.0)
        self.assertEqual(len(cleared), 1)
        self.assertIn("TOOLS NOT INSTALLED cleared", cleared[0])
        self.assertEqual(self.state_data()["last_outcome"], "ok")
        self.assertEqual(self.state_data()["tools_ok_head"], self.target)
        self.assertEqual(self.version("dev-infra"), "2.0.0")
        self.assertEqual(self.calls()[6:], [
            ["plugin", "marketplace", "update", "verification-skills"],
            ["plugin", "upgrade", "dev-infra@verification-skills"],
        ])
        self.wait_for_child_write_window(marker)

    def test_invalid_output_bytes_report_failure_and_follow_keeps_going(self) -> None:
        os.environ["FAKE_OMP_INVALID_BYTES"] = "dev-infra"
        messages = self.run_follow()
        self.assertEqual(len(messages), 1)
        self.assertIn("TOOLS NOT INSTALLED", messages[0])
        self.assertIn("dev-infra@verification-skills", messages[0])
        self.assertIn("exited 7", messages[0])
        self.assertIn("\ufffd", messages[0])
        self.assertEqual(self.version("dev-infra"), "2.0.0")
        self.assertEqual(self.version("core-ops"), "2.0.0")
        self.assertEqual(len(self.sweeps()), 1)
        self.assertEqual(self.state_data()["last_outcome"], "tools")
        self.assertNotEqual(self.state_data().get("tools_ok_head"), self.target)
        self.assert_clean_lock()

    def test_malformed_plugin_record_does_not_stop_later_plugins(self) -> None:
        installed = json.loads(self.installed_path.read_text())
        installed["plugins"]["dev-infra@verification-skills"] = [42]
        self.installed_path.write_text(json.dumps(installed))
        messages = self.run_follow()
        self.assertEqual(len(messages), 1)
        self.assertIn("TOOLS NOT INSTALLED", messages[0])
        self.assertIn("dev-infra", messages[0])
        self.assertEqual(self.version("core-ops"), "2.0.0")
        self.assertEqual(len(self.sweeps()), 1)
        self.assertEqual(self.state_data()["last_outcome"], "tools")
        self.assert_clean_lock()

    def test_marketplace_budget_stops_launching_commands_and_restores_lock(self) -> None:
        self.take_upstream()
        # The 2 s budget gives a loaded machine time to start the fake omp and log its call.
        # The delay is far past the budget, so the step ends early only when the budget stops it.
        delay = 10
        os.environ["FAKE_OMP_DELAY"] = str(delay)
        timeout = self.options().mise_timeout
        with unittest.mock.patch.object(FOLLOW, "MARKETPLACE_TIMEOUT", 2.0):
            start = time.monotonic()
            why = FOLLOW.install_marketplace_plugins(self.repo, timeout)
            elapsed = time.monotonic() - start
        self.assertIn("budget", why)
        self.assertLess(elapsed, delay)
        self.assertEqual(self.calls(), [["plugin", "marketplace", "update", "verification-skills"]])
        self.assert_clean_lock()

    def sweeps(self) -> list[str]:
        path = Path(f"{self.fake_log}.sweep")
        return path.read_text().splitlines() if path.exists() else []


if __name__ == "__main__":
    unittest.main()
