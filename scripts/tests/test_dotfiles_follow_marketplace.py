#!/usr/bin/env python3
"""Marketplace pins upgrade, report failures and recover a dirty follow-managed lock.

Run with: python3 -m unittest -v scripts.tests.test_dotfiles_follow_marketplace
The shared follower harness supplies throwaway git repositories and fake mise/bun/Envoy;
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

from scripts.tests.test_dotfiles_follow import FOLLOW, Harness


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

    def version(self, name: str) -> str:
        return json.loads(self.installed_path.read_text())["plugins"][f"{name}@{self.sources[name]}"][0]["version"]

    def assert_clean_lock(self) -> None:
        self.assertEqual((self.repo / "omp/plugins/omp-plugins.lock.json").read_bytes(), self.want_lock.encode())
        self.assertEqual(self.sh("git", "status", "--porcelain", "--", "omp/plugins/omp-plugins.lock.json", cwd=self.repo), "")

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

    def test_catalog_latest_different_from_pin_is_a_tool_failure(self) -> None:
        self.take_upstream()
        os.environ["FAKE_OMP_VERSIONS"] = json.dumps({
            "dev-infra": "3.0.0", "verification-skills": "2.0.0", "core-ops": "2.0.0",
        })
        why = FOLLOW.install_tools(self.repo, self.options())
        self.assertIn("dev-infra", why)
        self.assertIn("3.0.0", why)
        self.assertIn("2.0.0", why)
        self.assertEqual(self.version("core-ops"), "2.0.0")
        self.assert_clean_lock()

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

    def test_post_update_alerts_escalate_once_with_on_main_headline(self) -> None:
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
        escalated = self.run_follow(now=1_007_200.0)
        self.assertEqual(len(escalated), 1)
        self.assertIn("is on main, but a post-update step is still failing", escalated[0].splitlines()[0])
        self.assertNotIn("NOT FOLLOWING MAIN", escalated[0].splitlines()[0])
        self.assertIn("2.0h after the first alert", escalated[0])
        self.assertEqual(self.run_follow(now=1_007_800.0), [])
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
        mise = self.home.parent / "bin/mise"
        original_mise = mise.read_text()
        marker = self.home.parent / "mise-recovery.pid"
        mise.write_text(
            "#!/usr/bin/env python3\nimport os, sys, time\nfrom pathlib import Path\n"
            + "if sys.argv[1:] == ['install']:\n"
            + f"    Path({str(marker)!r}).write_text(str(os.getpid()))\n"
            + "    time.sleep(30)\n"
        )
        driver = (
            "from scripts.tests.test_dotfiles_follow import FOLLOW, FakeEnvoy; "
            "FakeEnvoy.instances = []; FOLLOW.Envoy = FakeEnvoy; "
            "raise SystemExit(FOLLOW.main())"
        )
        proc = subprocess.Popen([
            sys.executable, "-c", driver,
            "--repo", str(self.repo), "--state", str(self.state), "--hooks-dir", str(self.hooks_dir),
        ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
        try:
            deadline = time.monotonic() + 10
            while not marker.exists() and proc.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(marker.exists(), "the recovery run never reached mise install")
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
        os.environ["FAKE_OMP_DELAY"] = "0.5"
        start = time.monotonic()
        with unittest.mock.patch.object(FOLLOW, "MARKETPLACE_TIMEOUT", 0.2, create=True):
            why = FOLLOW.install_tools(self.repo, self.options())
        self.assertIn("budget", why)
        self.assertLess(time.monotonic() - start, 2)
        self.assertEqual(self.calls(), [["plugin", "marketplace", "update", "verification-skills"]])
        self.assert_clean_lock()

    def sweeps(self) -> list[str]:
        path = Path(f"{self.fake_log}.sweep")
        return path.read_text().splitlines() if path.exists() else []


if __name__ == "__main__":
    unittest.main()
