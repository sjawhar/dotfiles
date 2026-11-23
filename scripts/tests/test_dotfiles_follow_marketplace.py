#!/usr/bin/env python3
"""Marketplace pins upgrade, report failures and recover a dirty follow-managed lock.

Run with: python3 -m unittest -v scripts.tests.test_dotfiles_follow_marketplace
The shared follower harness supplies throwaway git repositories and fake mise and bun;
only the fake omp of scripts/tests/fake_omp.py handles marketplace commands, with no real omp or
network access.
"""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace

from scripts.tests import fake_omp
from scripts.tests.test_dotfiles_follow import FOLLOW, SCRIPT, TOOLS_PASSED, Harness, tools_passed_in
from scripts.tests.marketplace_version_cases import CASES, EMPTY_CASES, MISSING, RAW_CASES


class MarketplaceFixture(Harness):
    """The fixture and helpers of the marketplace cases, with fake_omp's fake as its shims/omp. It
    holds no case itself, so a class with cases of its own on this fixture (TeamCatalogFollow) does
    not run every case again."""

    def setUp(self) -> None:
        super().setUp()
        self.omp_log = self.home.parent / "omp.log"
        os.environ["FAKE_OMP_LOG"] = str(self.omp_log)
        self.fake_state = self.home.parent / "omp-state.json"
        os.environ["FAKE_OMP_STATE"] = str(self.fake_state)
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
        fake_omp.write(self.upstream / "shims/omp")
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
        return fake_omp.read_args(self.omp_log)

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


class MarketplacePlugins(MarketplaceFixture):
    """The marketplace cases of follow's run. The fake registers the fixture's three catalogs, and
    the team catalog still has the name it is registered under, verification-skills, so no move is
    under way. Each run's step lists the registrations, fetches that catalog (`marketplace update
    verification-skills`) and upgrades dev-infra, one of its four plugins; follow's loop then
    upgrades verification-skills, a plugin of that catalog outside the four, with no second update,
    and updates and upgrades core-ops. Each plugin is upgraded only when it is below its lock
    entry."""

    def setUp(self) -> None:
        super().setUp()
        fake_omp.write_state(self.fake_state, {
            "registrations": [
                ["verification-skills", fake_omp.TEAM_SOURCE],
                ["core-ops", "theorem-labs/core-ops"],
                ["claude-plugins-official", "anthropics/claude-plugins-official"],
            ],
            "catalogs": {fake_omp.TEAM_SOURCE: {"name": "verification-skills"}},
        })
        # The fake rewrites the lock on every call, so a clean lock after a run shows that follow
        # put it back, whichever commands ran.
        os.environ["FAKE_OMP_REWRITE_LOCK"] = "1"

    def test_changed_pins_upgrade_unchanged_and_bun_pins_do_not(self) -> None:
        self.take_upstream()
        why = FOLLOW.install_tools(self.repo, self.options())
        self.assertEqual(why, "")
        self.assertEqual(self.calls(), [
            ["plugin", "marketplace", "list"],
            ["plugin", "marketplace", "update", "verification-skills"],
            ["plugin", "upgrade", "dev-infra@verification-skills"],
            ["plugin", "upgrade", "verification-skills@verification-skills"],
            ["plugin", "marketplace", "update", "core-ops"],
            ["plugin", "upgrade", "core-ops@core-ops"],
        ])
        for name in ("dev-infra", "verification-skills", "core-ops"):
            self.assertEqual(self.version(name), "2.0.0")
        self.assertEqual(self.version("slack"), "1.0.0")
        self.assertEqual(self.version("@sjawhar/pi-envoy"), "1.0.0")
        self.assert_clean_lock()
        # Retrying tools at this HEAD fetches the team catalog again, and must not re-upgrade
        # anything already at its pin.
        self.assertEqual(FOLLOW.install_tools(self.repo, self.options()), "")
        self.assertEqual(self.calls()[6:], [
            ["plugin", "marketplace", "list"],
            ["plugin", "marketplace", "update", "verification-skills"],
        ])
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
            ["plugin", "marketplace", "list"],
            ["plugin", "marketplace", "update", "verification-skills"],
            ["plugin", "upgrade", "dev-infra@verification-skills"],
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
            ["plugin", "marketplace", "list"],
            ["plugin", "marketplace", "update", "verification-skills"],
            ["plugin", "upgrade", "verification-skills@verification-skills"],
            ["plugin", "marketplace", "update", "core-ops"],
            ["plugin", "upgrade", "core-ops@core-ops"],
        ])
        self.assertNotIn("dev-infra@verification-skills", json.loads(self.installed_path.read_text())["plugins"])
        self.assertEqual(self.version("core-ops"), "2.0.0")
        self.assert_clean_lock()

    def test_ambiguous_marketplace_source_is_reported_and_other_plugins_continue(self) -> None:
        # A plugin outside the team catalog's four, installed under a second ID. One of the four
        # under a catalog that no registration holds is a plugin the team catalog step moves.
        installed = json.loads(self.installed_path.read_text())
        installed["plugins"]["verification-skills@another-source"] = installed["plugins"]["verification-skills@verification-skills"]
        self.installed_path.write_text(json.dumps(installed))
        messages = self.run_follow()
        self.assertEqual(len(messages), 1)
        self.assertIn("TOOLS NOT INSTALLED", messages[0])
        self.assertIn("omp marketplace plugin verification-skills has no unique installed marketplace", messages[0])
        self.assertIn("source", messages[0])
        self.assertEqual(self.version("core-ops"), "2.0.0")
        self.assertFalse(any(args[:2] == ["plugin", "upgrade"] and args[2].startswith("verification-skills@") for args in self.calls()))
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
            ["plugin", "marketplace", "list"],
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
        # The step's update fails, so it tries `marketplace add`, which Oh My Pi refuses while the
        # name is registered. With that catalog not fetched, no plugin of it is upgraded, and
        # follow's loop goes on to core-ops.
        self.assertEqual(self.calls(), [
            ["plugin", "marketplace", "list"],
            ["plugin", "marketplace", "update", "verification-skills"],
            ["plugin", "marketplace", "add", fake_omp.TEAM_SOURCE],
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
            ["plugin", "marketplace", "list"],
            ["plugin", "marketplace", "update", "verification-skills"],
            ["plugin", "upgrade", "dev-infra@verification-skills"],
        ])
        self.assertEqual(self.run_follow(now=1_001_200.0), [])
        self.assertEqual(len(self.calls()), 9)
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
            ["plugin", "marketplace", "list"],
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
            ["plugin", "marketplace", "list"],
            ["plugin", "marketplace", "update", "verification-skills"],
            ["plugin", "upgrade", "dev-infra@verification-skills"],
        ])
        self.assertEqual(self.state_data()["tools_ok_head"], self.target)
        self.assert_clean_lock()
        self.assertEqual(self.run_follow(now=1_001_200.0), [])
        self.assertEqual(len(self.calls()), 9)

    def test_same_head_recovery_with_matching_pins_runs_no_upgrade(self) -> None:
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
        self.assertEqual(len(self.calls()), 8)
        self.assertEqual(self.calls()[6:], [
            ["plugin", "marketplace", "list"],
            ["plugin", "marketplace", "update", "verification-skills"],
        ])
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
            ["plugin", "marketplace", "list"],
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
        # The 2 s budget gives a loaded machine time to start the fake omp and log its calls (the
        # team catalog step's list, then its update). The delay of the update is far past the
        # budget, so the step ends early only when the budget stops it.
        delay = 10
        os.environ["FAKE_OMP_DELAY"] = str(delay)
        timeout = self.options().mise_timeout
        with unittest.mock.patch.object(FOLLOW, "MARKETPLACE_TIMEOUT", 2.0):
            start = time.monotonic()
            why = FOLLOW.install_marketplace_plugins(self.repo, timeout)
            elapsed = time.monotonic() - start
        self.assertIn("budget", why)
        self.assertLess(elapsed, delay)
        self.assertEqual(self.calls(), [
            ["plugin", "marketplace", "list"],
            ["plugin", "marketplace", "update", "verification-skills"],
        ])
        self.assert_clean_lock()

    def sweeps(self) -> list[str]:
        path = Path(f"{self.fake_log}.sweep")
        return path.read_text().splitlines() if path.exists() else []


# ------------------------------------------------------------------------------- the team catalog

# The names of INFRA-5366's plan: S the team catalog's source, A and B its name before and after the
# rename of dispatch://INFRA-4807, O a catalog of another repository, and the four plugins it moves.
S = fake_omp.TEAM_SOURCE
A = fake_omp.OLD_NAME
B = fake_omp.NEW_NAME
O = fake_omp.OTHER_NAME
FOUR = fake_omp.TEAM_PLUGINS
# Oh My Pi 18.18.6's stdout of `omp plugin marketplace list`: no registration, one, two, and two in
# colour. They were captured from the build mise.toml pins, v18.18.6-sami.20261119-162325, with
# these commands, run in scripts/tests/omp-marketplace-list/:
#
#   COPY=$(mktemp -d)/cat && git clone -q https://github.com/theorem-labs/verification-skills "$COPY"   # main of the catalog, named agent-skills
#   H=$(mktemp -d) && git config -f "$H/.gitconfig" url."file://$COPY".insteadOf https://github.com/theorem-labs/verification-skills.git
#   E="env -u OMP_PROFILE -u PI_PROFILE -u PI_CODING_AGENT_DIR -u XDG_DATA_HOME -u PI_CONFIG_DIR -u PI_CONFIG_FILES -u FORCE_COLOR HOME=$H XDG_CONFIG_HOME=$H/.config XDG_CACHE_HOME=$H/.cache XDG_STATE_HOME=$H/.local/state"
#   $E omp plugin marketplace list > none.txt                       # no registration
#   $E omp plugin marketplace add theorem-labs/verification-skills && $E omp plugin marketplace list > one.txt
#   # INFRA-4807's rename in the copy: the name, and each @agent-skills dependency, in .claude-plugin/marketplace.json,
#   # plugins/tasks/.claude-plugin/plugin.json and plugins/verification/.claude-plugin/plugin.json
#   sed -i 's/"agent-skills"/"theorem-skills"/; s/@agent-skills/@theorem-skills/g' "$COPY"/.claude-plugin/marketplace.json "$COPY"/plugins/tasks/.claude-plugin/plugin.json "$COPY"/plugins/verification/.claude-plugin/plugin.json
#   git -C "$COPY" -c user.name=t -c user.email=t@t commit -qam rename
#   $E omp plugin marketplace add theorem-labs/verification-skills && $E omp plugin marketplace list > two.txt
#   $E FORCE_COLOR=1 omp plugin marketplace list > two-color.txt
FIXTURES = Path(__file__).resolve().with_name("omp-marketplace-list")
# The mode prints each failure on stderr as one line that starts so, with its details indented
# under it.
MODE_FAILURE = "dotfiles-follow: team catalog: "
# The limit on one run of the mode or of a fake: it only stops a stuck run.
GUARD = 120

LIST = ["plugin", "marketplace", "list"]
ADD = ["plugin", "marketplace", "add", S]


def update(name: str) -> list[str]:
    return ["plugin", "marketplace", "update", name]


def remove(name: str) -> list[str]:
    return ["plugin", "marketplace", "remove", name]


def install(ident: str) -> list[str]:
    return ["plugin", "install", ident]


def uninstall(ident: str) -> list[str]:
    return ["plugin", "uninstall", ident]


def upgrade(ident: str) -> list[str]:
    return ["plugin", "upgrade", ident]


def at(name: str, version: str = "0.2.0", plugins: tuple[str, ...] = FOUR) -> dict[str, str]:
    """Installed records: each of PLUGINS as <plugin>@NAME at VERSION."""
    return {ident: version for ident in fake_omp.ids(name, plugins)}


def failure_blocks(text: str, starts) -> list[str]:
    """Each failure in TEXT: a line for which STARTS is true, and the indented lines after it."""
    blocks: list[str] = []
    for line in text.splitlines():
        if starts(line):
            blocks.append(line)
        elif blocks and line[:1].isspace():
            blocks[-1] += "\n" + line
    return blocks


class Run:
    """One run of the step: its exit status, its failures (failure_blocks) and what it printed."""

    def __init__(self, code: int, failures: list[str], out: str, err: str) -> None:
        self.code = code
        self.failures = failures
        self.out = out
        self.err = err

    def context(self) -> str:
        return f"exit {self.code}\nstdout:\n{self.out}\nstderr:\n{self.err}"


class MarketplaceList(unittest.TestCase):
    """marketplace_registrations reads the pin's own output of `omp plugin marketplace list` (P1 to
    P5 of INFRA-5366's plan), and the fake omp prints those bytes for the same registrations."""

    @staticmethod
    def fixture(name: str) -> str:
        return (FIXTURES / name).read_text()

    def test_p1_no_registration(self) -> None:
        self.assertEqual(FOLLOW.marketplace_registrations(self.fixture("none.txt")), [])

    def test_p2_one_registration(self) -> None:
        self.assertEqual(FOLLOW.marketplace_registrations(self.fixture("one.txt")), [(A, S)])

    def test_p3_two_registrations_in_list_order(self) -> None:
        self.assertEqual(FOLLOW.marketplace_registrations(self.fixture("two.txt")), [(A, S), (B, S)])

    def test_p4_two_registrations_in_colour(self) -> None:
        self.assertEqual(FOLLOW.marketplace_registrations(self.fixture("two-color.txt")), [(A, S), (B, S)])

    def test_p5_text_of_no_known_shape_raises(self) -> None:
        # An output the step cannot read is never read as "no registration".
        for text in (
            "",
            "\n",
            "omp: unknown command\n",
            "No marketplaces configured\n",
            "Configured Marketplaces:\n\n",
            f"Configured Marketplaces:\n\n  {A}\n",
            f"Configured Marketplaces:\n\n  {A}  {S}",
            f"Configured Marketplaces:\n  {A}  {S}\n",
        ):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    FOLLOW.marketplace_registrations(text)

    def test_the_fake_lists_the_pins_bytes(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        omp = fake_omp.write(root / "omp")
        for name, registrations in (("none.txt", []), ("one.txt", [[A, S]]), ("two.txt", [[A, S], [B, S]])):
            with self.subTest(fixture=name):
                state = root / f"{name}.json"
                fake_omp.write_state(state, {"registrations": registrations})
                done = subprocess.run(
                    [str(omp), *LIST],
                    env={"PATH": os.environ["PATH"], "FAKE_OMP_STATE": str(state), "FAKE_OMP_LOG": str(root / "calls")},
                    capture_output=True, check=True, timeout=GUARD, stdin=subprocess.DEVNULL,
                )
                self.assertEqual(done.stdout, (FIXTURES / name).read_bytes())


class TeamCatalogStep(unittest.TestCase):
    """The team catalog step of dotfiles-follow: the rows of the state table of INFRA-5366's plan.

    Each run gets a machine of its own: a git repository holding the lock (fake_omp.TEAM_LOCK), with
    the fake omp of fake_omp.py as its shims/omp and as `omp` on PATH, the row's installed records,
    and the fake's state (its registrations and the catalog's current name). A run is the mode as a
    subprocess, `dotfiles-follow --team-catalog --repo <checkout>` (F, the sync's call) or with
    --installer (I, installers/omp.sh's call), or follow's own run, install_marketplace_plugins, in
    this process. A row asserts the exact Oh My Pi calls, the installed records and registrations
    after the run, its failures and exit status, and that the lock holds HEAD's bytes after it."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.fakebin = fake_omp.write(self.root / "bin" / "omp").parent
        self.config = self.root / "config"
        home = self.root / "home"
        home.mkdir()
        self.machines = 0
        # Nothing of this process's environment but what the runs need: no OMP_PROFILE, PI_PROFILE
        # or PI_CODING_AGENT_DIR of the session that runs the tests.
        self.env = {
            "PATH": os.pathsep.join([str(self.fakebin), str(Path(sys.executable).parent), "/usr/local/bin", "/usr/bin", "/bin"]),
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(self.config),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        }

    def git(self, repo: Path, *args: str) -> bytes:
        return subprocess.run(["git", "-C", str(repo), *args], env=self.env, check=True, capture_output=True).stdout

    def machine(
        self,
        installed: dict[str, str],
        registrations: list[tuple[str, str]],
        catalog: str,
        versions: dict[str, str] | None = None,
        **state: object,
    ) -> SimpleNamespace:
        """A machine whose Oh My Pi holds INSTALLED ({ID: version}) and REGISTRATIONS, in order,
        while the team catalog is now named CATALOG and gives VERSIONS (0.2.0 for a plugin it does
        not name); STATE is more of the fake's state."""
        self.machines += 1
        root = self.root / f"machine-{self.machines}"
        repo = root / "dotfiles"
        fake_omp.write(repo / "shims" / "omp")
        lock = repo / FOLLOW.MARKETPLACE_LOCK
        lock.parent.mkdir(parents=True)
        lock.write_text(fake_omp.TEAM_LOCK)
        (repo / ".gitignore").write_text("omp/plugins/installed_plugins.json\n")
        self.git(repo, "init", "-q")
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-q", "-m", "a machine")
        fake_omp.write_installed(lock.parent / "installed_plugins.json", installed)
        state_path = root / "omp-state.json"
        fake_omp.write_state(state_path, {
            "registrations": [list(entry) for entry in registrations],
            "catalogs": {S: {"name": catalog, "versions": versions or {}}},
            **state,
        })
        log = root / "omp-calls.jsonl"
        env = {**self.env, "FAKE_OMP_STATE": str(state_path), "FAKE_OMP_LOG": str(log)}
        return SimpleNamespace(repo=repo, lock=lock, state=state_path, log=log, env=env)

    @staticmethod
    def calls(machine: SimpleNamespace) -> list[list[str]]:
        return fake_omp.read_args(machine.log)

    @staticmethod
    def installed(machine: SimpleNamespace) -> dict[str, object]:
        return fake_omp.read_installed(machine.lock.parent / "installed_plugins.json")

    @staticmethod
    def registrations(machine: SimpleNamespace) -> list[list[str]]:
        return fake_omp.read_registrations(machine.state)

    def assert_lock_at_head(self, machine: SimpleNamespace) -> None:
        self.assertEqual(machine.lock.read_bytes(), self.git(machine.repo, "show", f"HEAD:{FOLLOW.MARKETPLACE_LOCK}"))

    def mode(self, machine: SimpleNamespace, *flags: str, env: dict[str, str] | None = None) -> Run:
        done = subprocess.run(
            [sys.executable, str(SCRIPT), "--team-catalog", *flags, "--repo", str(machine.repo)],
            env=env or machine.env, capture_output=True, text=True, timeout=GUARD, stdin=subprocess.DEVNULL,
        )
        return Run(done.returncode, failure_blocks(done.stderr, lambda line: line.startswith(MODE_FAILURE)), done.stdout, done.stderr)

    def follow(self, machine: SimpleNamespace) -> Run:
        with unittest.mock.patch.dict(os.environ, machine.env, clear=True):
            why = FOLLOW.install_marketplace_plugins(machine.repo, 60.0)
        return Run(1 if why else 0, failure_blocks(why, lambda line: not line[:1].isspace()), "", why)

    def run_as(self, kind: str, machine: SimpleNamespace) -> Run:
        if kind == "F":
            return self.mode(machine)
        if kind == "I":
            return self.mode(machine, "--installer")
        return self.follow(machine)

    def assert_run(
        self,
        machine: SimpleNamespace,
        run: Run,
        calls: list[list[str]],
        installed: Mapping[str, object],
        registrations: list[tuple[str, str]],
        failures: tuple[tuple[str, ...], ...] = (),
    ) -> None:
        """RUN made exactly CALLS and left INSTALLED and REGISTRATIONS; it reported one failure for
        each entry of FAILURES, holding each of the entry's words, and exited 1 when there was one,
        else 0; and the lock holds HEAD's bytes."""
        self.assertEqual(self.calls(machine), calls, run.context())
        self.assertEqual(self.installed(machine), installed, run.context())
        self.assertEqual(self.registrations(machine), [list(entry) for entry in registrations], run.context())
        self.assertEqual(len(run.failures), len(failures), run.context())
        for block, words in zip(run.failures, failures):
            for word in words:
                self.assertIn(word, block, run.context())
        self.assertEqual(run.code, 1 if failures else 0, run.context())
        self.assert_lock_at_head(machine)

    def test_st01_a_change_of_name_moves_the_four_to_the_new_name(self) -> None:
        for kind in ("F", "I", "follow"):
            with self.subTest(run=kind):
                machine = self.machine(at(A), [(A, S)], B)
                self.assert_run(machine, self.run_as(kind, machine), fake_omp.move_calls(A, B), at(B), [(B, S)])

    def test_st01b_the_pins_exit_1_for_an_install_the_lock_refuses_moves_the_same(self) -> None:
        machine = self.machine(at(A), [(A, S)], B, lock_refusal_exit=1)
        self.assert_run(machine, self.mode(machine), fake_omp.move_calls(A, B), at(B), [(B, S)])

    def test_st02_no_change_of_name_fetches_once_and_changes_nothing(self) -> None:
        for kind in ("F", "I", "follow"):
            with self.subTest(run=kind):
                machine = self.machine(at(A), [(A, S)], A)
                self.assert_run(machine, self.run_as(kind, machine), [LIST, update(A)], at(A), [(A, S)])

    def test_st03_a_failed_add_uninstalls_nothing_and_removes_no_registration(self) -> None:
        machine = self.machine(at(A), [(A, S)], B, fail={"add": True})
        self.assert_run(machine, self.mode(machine), [LIST, update(A), ADD], at(A), [(A, S)], ((
            f"plugin marketplace update {A}",
            f'Marketplace catalog name changed from "{A}" to "{B}"',
            f"plugin marketplace add {S}",
            f"git clone of {S} failed",
        ),))

    def test_st03b_a_failed_update_and_a_refused_add_uninstall_nothing(self) -> None:
        machine = self.machine(at(A), [(A, S)], A, fail={"update": [A]})
        self.assert_run(machine, self.mode(machine), [LIST, update(A), ADD], at(A), [(A, S)], ((
            f"plugin marketplace update {A}",
            f"git fetch of {S} failed",
            f"plugin marketplace add {S}",
            f'Marketplace "{A}" already exists',
        ),))

    def test_st04_a_failed_install_keeps_the_old_registration_and_the_other_installs_run(self) -> None:
        machine = self.machine(at(A), [(A, S)], B, fail={"install": [f"tasks@{B}"]})
        self.assert_run(
            machine, self.mode(machine), fake_omp.move_calls(A, B)[:-1], at(B, plugins=("verification", "engineering", "dev-infra")),
            [(A, S), (B, S)], ((f"tasks@{B}", f"plugin install tasks@{B}"),),
        )

    def test_st05_the_next_run_ends_the_move_through_the_new_registration(self) -> None:
        machine = self.machine(at(B, plugins=("verification", "engineering", "dev-infra")), [(A, S), (B, S)], B)
        self.assert_run(
            machine, self.mode(machine), [LIST, update(A), update(B), install(f"tasks@{B}"), remove(A)], at(B), [(B, S)],
        )

    def test_st06_no_registration_and_none_of_the_four_fetches_nothing(self) -> None:
        machine = self.machine({}, [], B)
        run = self.mode(machine)
        self.assert_run(machine, run, [LIST], {}, [])
        self.assertIn(S, run.out)
        self.assertIn("nothing fetched", run.out)

    def test_st06i_the_installer_installs_the_four_on_a_machine_with_none(self) -> None:
        machine = self.machine({}, [], B)
        self.assert_run(
            machine, self.mode(machine, "--installer"), [LIST, ADD, LIST, *map(install, fake_omp.ids(B))], at(B), [(B, S)],
        )

    def test_st07_a_plugin_under_an_unregistered_name_is_moved(self) -> None:
        machine = self.machine({f"engineering@{A}": "0.2.0"}, [], B)
        self.assert_run(
            machine, self.mode(machine), [LIST, ADD, LIST, uninstall(f"engineering@{A}"), *map(install, fake_omp.ids(B))],
            at(B), [(B, S)],
        )

    def other_repository_machine(self, catalog: str) -> SimpleNamespace:
        """ST8's start: v, t and e of the team catalog A, and dev-infra of O, a catalog of another
        repository; both registered, A first."""
        installed = {**at(A, plugins=("verification", "tasks", "engineering")), f"dev-infra@{O}": "0.2.0"}
        return self.machine(installed, [(A, S), (O, fake_omp.OTHER_SOURCE)], catalog)

    def test_st08_a_plugin_of_another_repository_is_not_moved(self) -> None:
        machine = self.other_repository_machine(A)
        before = self.installed(machine)
        self.assert_run(machine, self.mode(machine), [LIST, update(A)], before, [(A, S), (O, fake_omp.OTHER_SOURCE)])

    def test_st08i_the_installer_reports_the_install_a_plugin_of_another_repository_blocks(self) -> None:
        machine = self.other_repository_machine(A)
        before = self.installed(machine)
        self.assert_run(
            machine, self.mode(machine, "--installer"), [LIST, update(A), install(f"dev-infra@{A}")], before,
            [(A, S), (O, fake_omp.OTHER_SOURCE)], ((f"dev-infra@{A}", f"plugin install dev-infra@{A}"),),
        )

    def test_st08b_a_move_keeps_a_plugin_of_another_repository_and_the_old_registration(self) -> None:
        machine = self.other_repository_machine(B)
        three = ("verification", "tasks", "engineering")
        self.assert_run(
            machine, self.mode(machine),
            [LIST, update(A), ADD, LIST, *map(uninstall, fake_omp.ids(A, three)), *map(install, fake_omp.ids(B))],
            {**at(B, plugins=three), f"dev-infra@{O}": "0.2.0"}, [(A, S), (O, fake_omp.OTHER_SOURCE), (B, S)],
            ((f"dev-infra@{B}", f"plugin install dev-infra@{B}"),),
        )

    def test_st09_updates_go_in_list_order_until_one_succeeds(self) -> None:
        machine = self.machine({f"verification@{A}": "0.2.0", **at(B, plugins=FOUR[1:])}, [(A, S), (B, S)], B)
        self.assert_run(
            machine, self.mode(machine),
            [LIST, update(A), update(B), uninstall(f"verification@{A}"), install(f"verification@{B}"), remove(A)],
            at(B), [(B, S)],
        )

    def test_st10_a_plugin_whose_old_registration_is_gone_is_moved(self) -> None:
        installed = {f"engineering@{A}": "0.2.0", **at(B, plugins=("verification", "tasks", "dev-infra"))}
        machine = self.machine(installed, [(B, S)], B)
        self.assert_run(
            machine, self.mode(machine), [LIST, update(B), uninstall(f"engineering@{A}"), install(f"engineering@{B}")],
            at(B), [(B, S)],
        )

    def test_st11_a_failed_uninstall_installs_that_plugin_nowhere_and_the_others_go_on(self) -> None:
        machine = self.machine(at(A), [(A, S)], B, fail={"uninstall": [f"tasks@{A}"]})
        self.assert_run(
            machine, self.mode(machine),
            [
                LIST, update(A), ADD, LIST, *map(uninstall, fake_omp.ids(A)),
                *map(install, fake_omp.ids(B, ("verification", "engineering", "dev-infra"))),
            ],
            {f"tasks@{A}": "0.2.0", **at(B, plugins=("verification", "engineering", "dev-infra"))},
            [(A, S), (B, S)], ((f"tasks@{A}", f"plugin uninstall tasks@{A}"),),
        )

    def test_st12_an_install_that_exits_0_and_installs_nothing_is_a_failure(self) -> None:
        machine = self.machine(at(A), [(A, S)], B, fail={"install_noop": [f"tasks@{B}"]})
        self.assert_run(
            machine, self.mode(machine), fake_omp.move_calls(A, B)[:-1], at(B, plugins=("verification", "engineering", "dev-infra")),
            [(A, S), (B, S)], ((f"tasks@{B}", f"plugin install tasks@{B}", "exited 0"),),
        )

    def test_st13_a_moved_plugin_below_the_lock_is_a_failure(self) -> None:
        below = (f"engineering@{B} is 0.0.9, below 0.1.0",)
        move = fake_omp.move_calls(A, B)
        for kind, calls in (("F", move), ("I", [*move, upgrade(f"engineering@{B}")])):
            with self.subTest(run=kind):
                machine = self.machine(at(A), [(A, S)], B, versions={"engineering": "0.0.9"})
                self.assert_run(
                    machine, self.run_as(kind, machine), calls, {**at(B), f"engineering@{B}": "0.0.9"}, [(B, S)], (below,),
                )

    def test_st14_a_plugin_below_the_lock_is_upgraded_by_the_installer_and_follow_only(self) -> None:
        for kind in ("F", "I", "follow"):
            with self.subTest(run=kind):
                machine = self.machine({**at(A), f"engineering@{A}": "0.0.5"}, [(A, S)], A)
                run = self.run_as(kind, machine)
                if kind == "F":
                    self.assert_run(machine, run, [LIST, update(A)], {**at(A), f"engineering@{A}": "0.0.5"}, [(A, S)])
                else:
                    self.assert_run(machine, run, [LIST, update(A), upgrade(f"engineering@{A}")], at(A), [(A, S)])

    def test_st15_a_second_run_after_the_move_changes_nothing(self) -> None:
        for kind in ("F", "I"):
            with self.subTest(run=kind):
                machine = self.machine(at(B), [(B, S)], B)
                self.assert_run(machine, self.run_as(kind, machine), [LIST, update(B)], at(B), [(B, S)])

    def test_st16_follows_run_puts_back_a_lock_a_killed_run_left(self) -> None:
        machine = self.machine(at(A), [(A, S)], A)
        killed = json.loads(fake_omp.TEAM_LOCK)
        del killed["plugins"]["tasks"]
        machine.lock.write_text(json.dumps(killed, indent=2) + "\n")
        self.assert_run(machine, self.follow(machine), [LIST, update(A)], at(A), [(A, S)])

    def test_st16b_the_syncs_call_keeps_an_uncommitted_edit_of_the_lock_when_nothing_moves(self) -> None:
        machine = self.machine(at(A), [(A, S)], A)
        edited = fake_omp.TEAM_LOCK.replace('"settings": {}', '"settings": {"edited": "by a session"}').encode()
        self.assertNotEqual(edited, fake_omp.TEAM_LOCK.encode())
        machine.lock.write_bytes(edited)
        run = self.mode(machine)
        self.assertEqual(self.calls(machine), [LIST, update(A)], run.context())
        self.assertEqual((run.code, run.failures), (0, []), run.context())
        self.assertEqual(self.installed(machine), at(A))
        self.assertEqual(machine.lock.read_bytes(), edited)

    def test_st17_sigterm_kills_the_running_command_and_puts_the_lock_back(self) -> None:
        marker = self.root / "install.pid"
        machine = self.machine(
            at(A), [(A, S)], B, sleep={"args": install(f"tasks@{B}"), "seconds": 3 * GUARD, "marker": str(marker)},
        )
        proc = subprocess.Popen(
            [sys.executable, str(SCRIPT), "--team-catalog", "--repo", str(machine.repo)],
            env=machine.env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=True,
        )
        fake = 0
        try:
            deadline = time.monotonic() + GUARD
            while not marker.exists() and proc.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            if not marker.exists():
                proc.kill()
                out, err = proc.communicate()
                self.fail(f"the mode never reached the second install (exit {proc.returncode}):\n{out}{err}")
            fake = json.loads(marker.read_text())["pid"]
            proc.send_signal(signal.SIGTERM)
            out, err = proc.communicate(timeout=GUARD)
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.communicate()
        try:
            self.assertEqual(proc.returncode, 143, out + err)
            with self.assertRaises(ProcessLookupError, msg="the fake's process group outlived the mode"):
                os.killpg(fake, 0)
        finally:
            try:
                os.killpg(fake, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.assertEqual(self.calls(machine), fake_omp.move_calls(A, B)[:10])
        self.assert_lock_at_head(machine)

    def test_st18_a_registration_spelled_as_a_url_is_the_team_catalog(self) -> None:
        machine = self.machine(at(A), [(A, "https://github.com/theorem-labs/verification-skills.git")], B)
        self.assert_run(machine, self.mode(machine), fake_omp.move_calls(A, B), at(B), [(B, S)])

    def test_st19_a_legion_machine_profile_stops_the_syncs_call_only(self) -> None:
        profile = self.config / "omp" / "profile"
        profile.parent.mkdir(parents=True)
        profile.write_text("legion\n")
        with self.subTest(run="F"):
            machine = self.machine(at(A), [(A, S)], A)
            self.assert_run(machine, self.mode(machine), [], at(A), [(A, S)], (("legion",),))
        with self.subTest(run="I"):
            machine = self.machine(at(A), [(A, S)], A)
            self.assert_run(machine, self.mode(machine, "--installer"), [LIST, update(A)], at(A), [(A, S)])

    def test_st20_the_syncs_call_clears_the_profile_and_the_installers_keeps_it(self) -> None:
        agent = str(self.root / "pbc-agent")
        profile = {"OMP_PROFILE": "pbc", "PI_PROFILE": "pbc", "PI_CODING_AGENT_DIR": agent}
        for kind, flags in (("F", ()), ("I", ("--installer",))):
            with self.subTest(run=kind):
                machine = self.machine(at(A), [(A, S)], A)
                self.assert_run(machine, self.mode(machine, *flags, env={**machine.env, **profile}), [LIST, update(A)], at(A), [(A, S)])
                omp = machine.repo / "shims" / "omp" if kind == "F" else self.fakebin / "omp"
                for record in fake_omp.read_calls(machine.log):
                    self.assertEqual(record["path"], str(omp))
                    self.assertEqual(record["has_omp_profile"], kind == "I")
                    self.assertEqual(record["has_pi_profile"], kind == "I")
                    self.assertEqual(record["agent_dir"], agent if kind == "I" else None)

    def test_st21_a_list_output_of_no_known_shape_is_a_failure(self) -> None:
        for text in ("omp: the registry is busy\n", ""):
            with self.subTest(text=text):
                machine = self.machine(at(A), [(A, S)], A, list_output=text)
                self.assert_run(machine, self.mode(machine), [LIST], at(A), [(A, S)], (("plugin marketplace list",),))

    def test_st22_without_a_lock_the_mode_says_so_and_runs_nothing(self) -> None:
        machine = self.machine(at(A), [(A, S)], B)
        machine.lock.unlink()
        run = self.mode(machine)
        self.assertEqual((run.code, run.failures), (0, []), run.context())
        self.assertEqual(self.calls(machine), [])
        self.assertIn("team catalog:", run.out)
        self.assertFalse(machine.lock.exists())

    def test_st23_a_spent_budget_starts_no_more_commands_and_the_lock_is_put_back(self) -> None:
        marker = self.root / "install.pid"
        machine = self.machine(at(A), [(A, S)], B, sleep={"args": install(f"tasks@{B}"), "seconds": 3 * GUARD, "marker": str(marker)})
        started = time.monotonic()
        with unittest.mock.patch.object(FOLLOW, "MARKETPLACE_TIMEOUT", 10.0):
            run = self.follow(machine)
        # Without the budget, the install that sleeps would run to its own limit.
        self.assertLess(time.monotonic() - started, FOLLOW.MARKETPLACE_COMMAND_TIMEOUT)
        self.assertEqual(self.calls(machine), fake_omp.move_calls(A, B)[:10], run.context())
        self.assertTrue([block for block in run.failures if "budget" in block], run.context())
        self.assertEqual(run.code, 1)
        self.assertEqual(self.installed(machine), at(B, plugins=("verification",)))
        self.assert_lock_at_head(machine)


class TeamCatalogFollow(MarketplaceFixture):
    """The team catalog step through follow's run (FI1 to FI5 of INFRA-5366's plan), on the fixture
    of MarketplaceFixture."""

    def installed(self) -> dict[str, object]:
        return fake_omp.read_installed(self.installed_path)

    def team_machine(self, installed: dict[str, str], registrations: list[tuple[str, str]], catalog: str, **state: object) -> None:
        """~/.dotfiles at a new main whose lock holds the four at 0.1.0 and core-ops at 2.0.0; Oh My
        Pi holding INSTALLED and core-ops@core-ops at 2.0.0, with REGISTRATIONS and core-ops's; the
        team catalog now named CATALOG; STATE more of the fake's state."""
        wanted = json.loads(fake_omp.TEAM_LOCK)
        wanted["plugins"]["core-ops"] = {"version": "2.0.0", "enabledFeatures": None, "enabled": True}
        self.commit_wanted_lock(wanted)
        records = {**installed, "core-ops@core-ops": "2.0.0"}
        fake_omp.write_installed(self.installed_path, records)
        fake_omp.write_state(self.fake_state, {
            "registrations": [*map(list, registrations), ["core-ops", "theorem-labs/core-ops"]],
            "catalogs": {S: {"name": catalog}},
            **state,
        })

    def test_fi1_follow_moves_the_four_and_its_tools_condition_clears(self) -> None:
        self.team_machine(at(A), [(A, S)], B, fail={"add": True})
        opened = self.run_follow()
        self.assertEqual(len(opened), 1, opened)
        self.assertIn("TOOLS NOT INSTALLED", opened[0])
        self.assertIn(f"plugin marketplace add {S}", opened[0])
        self.assertEqual(self.calls(), [LIST, update(A), ADD])
        self.assertEqual(self.installed(), {**at(A), "core-ops@core-ops": "2.0.0"})
        state = fake_omp.read_state(self.fake_state)
        del state["fail"]
        fake_omp.write_state(self.fake_state, state)

        cleared = self.run_follow(now=1_000_600.0)
        self.assertEqual(len(cleared), 1, cleared)
        self.assertIn("TOOLS NOT INSTALLED cleared", cleared[0])
        self.assertEqual(self.calls()[3:], fake_omp.move_calls(A, B))
        self.assertEqual(self.installed(), {**at(B), "core-ops@core-ops": "2.0.0"})
        self.assertEqual(self.state_data()["tools_ok_head"], self.target)
        self.assert_clean_lock()

    def test_fi2_follow_fetches_the_catalog_once_with_no_change_of_name(self) -> None:
        self.team_machine(at(A), [(A, S)], A)
        self.assertEqual(self.run_follow(), [])
        self.assertEqual(self.calls(), [LIST, update(A)])
        self.assertEqual(self.state_data()["tools_ok_head"], self.target)
        self.assert_clean_lock()

    def test_fi3_a_plugin_of_the_team_catalog_outside_the_four_gets_no_second_update(self) -> None:
        # The fixture's catalog of the team source is still named verification-skills, so nothing
        # moves, and dev-infra (one of the four) is upgraded by the step, verification-skills (not
        # one of the four) by the loop, with no second update of the catalog the step fetched.
        self.take_upstream()
        fake_omp.write_state(self.fake_state, {"registrations": [
            ["verification-skills", S],
            ["core-ops", "theorem-labs/core-ops"],
            ["claude-plugins-official", "anthropics/claude-plugins-official"],
        ]})
        self.assertEqual(FOLLOW.install_tools(self.repo, self.options()), "")
        self.assertEqual(self.calls(), [
            LIST,
            update("verification-skills"),
            upgrade("dev-infra@verification-skills"),
            upgrade("verification-skills@verification-skills"),
            update("core-ops"),
            upgrade("core-ops@core-ops"),
        ])
        for name in ("dev-infra", "verification-skills", "core-ops"):
            self.assertEqual(self.version(name), "2.0.0")
        self.assert_clean_lock()

    def test_fi4_a_failed_install_is_follows_tools_condition(self) -> None:
        self.team_machine(at(A), [(A, S)], B, fail={"install": [f"tasks@{B}"]})
        messages = self.run_follow()
        self.assertEqual(len(messages), 1, messages)
        self.assertIn("TOOLS NOT INSTALLED", messages[0])
        self.assertIn(f"`omp plugin install tasks@{B}`", messages[0])
        self.assertEqual(self.calls(), fake_omp.move_calls(A, B)[:-1])
        self.assertNotEqual(self.state_data().get("tools_ok_head"), self.target)
        self.assert_clean_lock()

    def test_fi5_a_plugin_of_another_repository_gets_its_catalogs_update_and_upgrade(self) -> None:
        installed = {**at(A, plugins=("verification", "tasks", "engineering")), f"dev-infra@{O}": "0.0.5"}
        self.team_machine(installed, [(A, S), (O, fake_omp.OTHER_SOURCE)], A)
        self.assertEqual(FOLLOW.install_tools(self.repo, self.options()), "")
        self.assertEqual(self.calls(), [LIST, update(A), update(O), upgrade(f"dev-infra@{O}")])
        self.assertEqual(self.installed()[f"dev-infra@{O}"], "2.0.0")
        self.assert_clean_lock()


if __name__ == "__main__":
    unittest.main()
