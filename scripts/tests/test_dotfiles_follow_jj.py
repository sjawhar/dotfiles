#!/usr/bin/env python3
"""Tests for dotfiles-follow on its colocated jj checkout path.

The fixture has a real jj/git colocated checkout at $HOME/.dotfiles. Its origin is local, and
mise, Bun, omp, Envoy, and the hook sweep are stand-ins. This keeps every case off the network
and checks that follow delegates the working-copy move to dotfiles-sync.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from scripts.tests.test_dotfiles_follow import FOLLOW, Harness
from scripts.tests.test_dotfiles_sync import real_binary


REAL_GIT = real_binary("git")
REAL_JJ = real_binary("jj")


FAKE_OMP = r'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
args = sys.argv[1:]
record = {
    "args": args,
    "has_omp_profile": "OMP_PROFILE" in os.environ,
    "has_pi_profile": "PI_PROFILE" in os.environ,
    "agent_dir": os.environ.get("PI_CODING_AGENT_DIR"),
}
with Path(os.environ["FAKE_OMP_LOG"]).open("a") as log:
    log.write(json.dumps(record) + "\n")
if os.environ.get("OMP_PROFILE") == "legion" or os.environ.get("PI_PROFILE") == "legion":
    sys.exit(1)
if args[:3] == ["plugin", "marketplace", "update"]:
    sys.exit(0)
if args[:2] == ["plugin", "upgrade"]:
    installed_path = repo / "omp/plugins/installed_plugins.json"
    installed = json.loads(installed_path.read_text())
    installed["plugins"][args[2]][0]["version"] = "2.0.0"
    installed_path.write_text(json.dumps(installed))
    sys.exit(0)
sys.exit(99)
'''


class JjHarness(Harness):
    def setUp(self) -> None:
        super().setUp()
        fakebin = Path(os.environ["PATH"].split(os.pathsep)[0])
        (fakebin / "git").symlink_to(REAL_GIT)
        (fakebin / "jj").symlink_to(REAL_JJ)
        jj_config = self.home.parent / "jj.toml"
        jj_config.write_text(
            '[user]\nname = "Test"\nemail = "test@example.com"\n[signing]\nbehavior = "drop"\n'
        )
        os.environ["JJ_CONFIG"] = str(jj_config)

        shutil.rmtree(self.repo)
        sync = self.upstream / "scripts/dotfiles-sync"
        shutil.copy2(Path(__file__).resolve().parents[1] / "dotfiles-sync", sync)
        linker = self.upstream / "scripts/omp-link-rules"
        linker.write_text("#!/bin/sh\nexit 0\n")
        linker.chmod(linker.stat().st_mode | stat.S_IEXEC)
        omp = self.upstream / "shims/omp"
        omp.parent.mkdir()
        omp.write_text(FAKE_OMP)
        omp.chmod(omp.stat().st_mode | stat.S_IEXEC)
        (self.upstream / "omp/plugins/omp-plugins.lock.json").write_text(
            json.dumps({"plugins": {"dev-infra": {"version": "2.0.0"}}}) + "\n"
        )
        (self.upstream / ".gitignore").write_text(
            (self.upstream / ".gitignore").read_text() + "omp/plugins/installed_plugins.json\n"
        )
        self.sh("git", "add", "-A", cwd=self.upstream)
        self.sh("git", "commit", "-q", "-m", "jj follow fixture", cwd=self.upstream)
        self.sh("git", "push", "-q", "origin", "HEAD:main", cwd=self.upstream)

        self.sh("jj", "git", "clone", "--quiet", "--colocate", str(self.origin), str(self.repo))
        installed = {
            "plugins": {
                "dev-infra@verification-skills": [{"version": "1.0.0"}],
            }
        }
        (self.repo / "omp/plugins/installed_plugins.json").write_text(json.dumps(installed))
        self.omp_log = self.home.parent / "omp.log"
        os.environ["FAKE_OMP_LOG"] = str(self.omp_log)
        self.local_a = self.repo / "secrets.human.d/LOCAL_A.env"
        self.local_b = self.repo / "LOCAL_B.env"
        self.local_a.parent.mkdir()
        self.local_a.write_bytes(b"LOCAL_A=keep these bytes\n")
        self.local_b.write_bytes(b"LOCAL_B=keep these bytes\x00\n")
        self.local_bytes = {self.local_a: self.local_a.read_bytes(), self.local_b: self.local_b.read_bytes()}
        self.origin_before = self.origin_main()
        self.target = self.commit_upstream("upstream change", scripts__tool="v2\n")

    def jj(self, *args: str) -> str:
        return self.sh("jj", *args, cwd=self.repo)

    def origin_main(self) -> str:
        return self.sh("git", "--git-dir", str(self.origin), "rev-parse", "main")

    def diff_paths(self) -> set[str]:
        return {line for line in self.jj("diff", "-r", "@", "--name-only").splitlines() if line}

    def omp_calls(self) -> list[dict]:
        if not self.omp_log.exists():
            return []
        return [json.loads(line) for line in self.omp_log.read_text().splitlines()]

    def watch_git_pushes(self) -> Path:
        push_log = self.home.parent / "git-push.log"
        fake_git = Path(os.environ["PATH"].split(os.pathsep)[0]) / "git"
        fake_git.unlink()
        fake_git.write_text(
            """#!/bin/sh
for argument in "$@"; do
    if [ "$argument" = push ]; then
        printf '%s\n' "$*" >> "$FAKE_GIT_PUSH_LOG"
    fi
done
exec "$FAKE_REAL_GIT" "$@"
"""
        )
        fake_git.chmod(fake_git.stat().st_mode | stat.S_IEXEC)
        os.environ["FAKE_GIT_PUSH_LOG"] = str(push_log)
        os.environ["FAKE_REAL_GIT"] = REAL_GIT
        return push_log


    def no_tool_run(self) -> None:
        self.assertFalse(self.omp_log.exists())
        log = self.fake_log.read_text() if self.fake_log.exists() else ""
        self.assertNotIn("install", log)


class JjFollow(JjHarness):
    def test_moves_a_colocated_checkout_runs_tools_keeps_local_files_and_does_not_push(self) -> None:
        (self.repo / "omp/plugins/omp-plugins.lock.json").write_text("interrupted marketplace lock\n")
        push_log = self.watch_git_pushes()
        git = FOLLOW.git
        status_calls: list[tuple[str, ...]] = []

        def record_status(repo: Path, *args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
            if args[:1] == ("status",):
                status_calls.append(args)
            return git(repo, *args, **kwargs)

        with unittest.mock.patch.object(FOLLOW, "git", side_effect=record_status):
            messages = self.run_follow()

        self.assertEqual(messages, [])
        self.assertEqual(self.jj("log", "-r", "@-", "--no-graph", "-T", "commit_id"), self.jj("log", "-r", "main@origin", "--no-graph", "-T", "commit_id"))
        self.assertEqual(self.jj("log", "-r", "@", "--no-graph", "-T", "description"), "")
        self.assertEqual(self.origin_main(), self.target)
        self.assertFalse(push_log.exists())
        for path, want in self.local_bytes.items():
            self.assertEqual(path.read_bytes(), want)
        self.assertEqual(self.diff_paths(), {"LOCAL_B.env", "secrets.human.d/LOCAL_A.env"})
        self.assertIn("install", self.fake_log.read_text())
        self.assertIn("bun install --frozen-lockfile", self.fake_log.read_text())
        self.assertEqual([call["args"] for call in self.omp_calls()], [
            ["plugin", "marketplace", "update", "verification-skills"],
            ["plugin", "upgrade", "dev-infra@verification-skills"],
        ])
        self.assertEqual(
            status_calls,
            [
                ("status", "--porcelain=v1", "-z", "--untracked-files=all", "--", FOLLOW.MARKETPLACE_LOCK),
                ("status", "--porcelain=v1", "-z", "--untracked-files=all", "--", FOLLOW.MARKETPLACE_LOCK),
            ],
        )
        for call in self.omp_calls():
            self.assertFalse(call["has_omp_profile"])
            self.assertFalse(call["has_pi_profile"])

    def test_conflicting_working_copy_is_reported_without_tools_or_file_changes(self) -> None:
        local = self.repo / "shared.txt"
        local.write_text("local edit\n")
        (self.upstream / "shared.txt").write_text("upstream edit\n")
        self.commit_upstream("conflict")
        before = local.read_bytes()

        messages = self.run_follow()

        self.assertEqual(len(messages), 1)
        self.assertIn("DOTFILES NOT FOLLOWING MAIN", messages[0])
        self.assertIn("DOTFILES SYNC REFUSED", messages[0])
        self.assertIn("CONFLICT", messages[0])
        self.assertEqual(local.read_bytes(), before)
        self.no_tool_run()

    def test_a_failed_fetch_leaves_an_open_sync_refusal_open(self) -> None:
        (self.repo / "shared.txt").write_text("local edit\n")
        (self.upstream / "shared.txt").write_text("upstream edit\n")
        self.commit_upstream("conflict")
        first = self.run_follow()
        self.assertEqual(len(first), 1)
        self.assertIn("DOTFILES SYNC REFUSED", first[0])
        self.origin.rename(self.origin.with_name("unreachable.git"))

        # The sync stops at its fetch and judges nothing, so no message says the refusal cleared.
        self.assertEqual(self.run_follow(now=1_000_600.0), [])

    def test_unpublished_commit_is_reported_without_tools_or_file_changes(self) -> None:
        local = self.repo / "other.txt"
        local.write_text("local commit\n")
        self.jj("commit", "--quiet", "-m", "local change", "other.txt")
        (self.upstream / "shared.txt").write_text("upstream edit\n")
        self.commit_upstream("new upstream")
        before = local.read_bytes()

        messages = self.run_follow()

        self.assertEqual(len(messages), 1)
        self.assertIn("DOTFILES NOT FOLLOWING MAIN", messages[0])
        self.assertIn("UNPUBLISHED", messages[0])
        self.assertEqual(local.read_bytes(), before)
        self.no_tool_run()

    def test_tools_retry_after_mise_fails_then_succeeds_at_the_same_revision(self) -> None:
        os.environ["FAKE_MISE_RC"] = "1"
        self.run_follow()
        self.assertNotEqual(self.state_data().get("tools_ok_head"), self.target)
        os.environ["FAKE_MISE_RC"] = "0"

        self.run_follow(now=1_000_600.0)

        self.assertEqual(self.state_data()["tools_ok_head"], self.target)
        self.assertEqual(self.fake_log.read_text().splitlines().count("install"), 2)

    def test_legion_profile_skips_omp_and_reports_the_plugin_step_failure(self) -> None:
        profile = Path(os.environ["XDG_CONFIG_HOME"]) / "omp/profile"
        profile.parent.mkdir(parents=True)
        profile.write_text("legion\n")

        messages = self.run_follow()

        self.assertEqual(len(messages), 1)
        self.assertIn("TOOLS NOT INSTALLED", messages[0])
        self.assertIn("machine profile is legion", messages[0])
        self.assertEqual(self.omp_calls(), [])

    def test_no_hook_sweep_skips_a_missing_sweep_without_a_condition(self) -> None:
        (self.hooks_dir / "sweep-hooks.sh").unlink()

        messages = self.run_follow(no_hook_sweep=True)

        self.assertEqual(messages, [])
        self.assertNotIn("hooks", self.state_data().get("incidents", {}))
        self.assertFalse(Path(f"{self.fake_log}.sweep").exists())

    def test_without_no_hook_sweep_a_missing_sweep_stays_a_condition(self) -> None:
        (self.hooks_dir / "sweep-hooks.sh").unlink()

        messages = self.run_follow()

        self.assertEqual(len(messages), 1)
        self.assertIn("PRE-PUSH HOOK NOT INSTALLED", messages[0])

    def test_a_jj_checkout_other_than_home_dotfiles_is_refused(self) -> None:
        other = self.home / "other-dotfiles"
        self.sh("jj", "git", "clone", "--quiet", "--colocate", str(self.origin), str(other))
        state = self.home.parent / "other-state.json"

        messages = self.run_follow(repo=other, state=state)

        self.assertEqual(len(messages), 1)
        self.assertIn("only ~/.dotfiles may be moved", messages[0])
        self.assertEqual(self.origin_main(), self.target)


class RoutingEnvoy:
    instances: list["RoutingEnvoy"] = []
    publish_result = False

    def __init__(self, timeout: float = 8.0) -> None:
        self.published: list[tuple[str, str]] = []
        self.sent: list[tuple[str, str]] = []
        type(self).instances.append(self)

    def publish(self, topic: str, message: str) -> bool:
        self.published.append((topic, message))
        return type(self).publish_result

    def send(self, session_id: str, message: str) -> bool:
        self.sent.append((session_id, message))
        return True


class JjAlertRouting(JjHarness):
    def follow_failure(self, **options: object) -> RoutingEnvoy:
        RoutingEnvoy.instances = []
        os.environ["FAKE_MISE_RC"] = "1"
        with unittest.mock.patch.object(FOLLOW, "Envoy", RoutingEnvoy):
            with unittest.mock.patch.object(FOLLOW.time, "time", lambda: 1_000_000.0):
                self.assertEqual(FOLLOW.run_once(self.options(**options)), 0)
        return RoutingEnvoy.instances[-1]

    def test_box_health_role_has_no_sre_fallback_when_publish_fails(self) -> None:
        envoy = self.follow_failure(box_health_role="legion-home-controller")

        self.assertEqual([topic for topic, _ in envoy.published], ["notifications.role.legion-home-controller"])
        self.assertEqual(envoy.sent, [])

    def test_box_health_owner_wins_over_the_configured_role(self) -> None:
        owner = "01abcdef-1234-5678-9abc-def012345678"
        os.environ["BOX_HEALTH_OWNER"] = owner
        envoy = self.follow_failure(box_health_role="legion-home-controller")

        self.assertEqual(envoy.published, [])
        self.assertEqual([session for session, _ in envoy.sent], [owner])

    def test_default_routing_still_uses_the_lead_role_then_sre(self) -> None:
        envoy = self.follow_failure()

        self.assertEqual([topic for topic, _ in envoy.published], ["notifications.role.lead-box-health"])
        self.assertEqual([session for session, _ in envoy.sent], [FOLLOW.SRE])

    def test_box_health_role_validation(self) -> None:
        self.assertEqual(FOLLOW.health_role("legion-home-controller"), "legion-home-controller")
        with self.assertRaises(argparse.ArgumentTypeError):
            FOLLOW.health_role("Legion_home_controller")


class JjSyncFailures(JjHarness):
    def test_a_failed_fetch_waits_fetch_grace_runs_as_in_git_mode(self) -> None:
        self.assertEqual(self.run_follow(), [])
        self.origin.rename(self.origin.with_name("unreachable.git"))

        # --fetch-grace is 3 (Harness.options): two failed runs are quiet, the third says it.
        self.assertEqual(self.run_follow(now=1_000_600.0), [])
        self.assertEqual(self.run_follow(now=1_001_200.0), [])
        third = self.run_follow(now=1_001_800.0)

        self.assertEqual(len(third), 1)
        self.assertIn("DOTFILES NOT FOLLOWING MAIN", third[0])
        for path, want in self.local_bytes.items():
            self.assertEqual(path.read_bytes(), want)

    def test_omp_steps_do_not_inherit_a_legion_sessions_agent_dir(self) -> None:
        # A run by hand from a Legion session carries all three variables. omp uses
        # PI_CODING_AGENT_DIR when no profile is named; scripts/omp-teleport unsets all three.
        legion_agent_dir = str(self.home / ".omp/profiles/legion/agent")
        os.environ.update(OMP_PROFILE="legion", PI_PROFILE="legion", PI_CODING_AGENT_DIR=legion_agent_dir)

        self.assertEqual(self.run_follow(), [])

        calls = self.omp_calls()
        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertFalse(call["has_omp_profile"])
            self.assertFalse(call["has_pi_profile"])
            self.assertIsNone(call["agent_dir"])

    def test_conflict_refusal_deletes_the_anchor_bookmark(self) -> None:
        (self.repo / "shared.txt").write_text("local edit\n")
        (self.upstream / "shared.txt").write_text("upstream edit\n")
        self.commit_upstream("conflict")

        messages = self.run_follow()

        self.assertIn("CONFLICT", messages[0])
        self.assertNotIn("dotfiles-sync-anchor", self.jj("bookmark", "list", "-T", 'name ++ "\\n"'))

    def test_a_held_sync_lock_is_named_not_reported_as_a_timeout(self) -> None:
        # Follow kills dotfiles-sync after 2 x --fetch-timeout, and the sync waits
        # DOTFILES_SYNC_LOCK_WAIT (300 s by default) for its lock, so the sync's own refusal
        # must arrive before the kill. Scaled down: --fetch-timeout 1, a 2 s kill.
        handle = (self.repo / ".git/dotfiles-sync.lock").open("w")
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            messages = self.run_follow(fetch_timeout=1.0)
        finally:
            handle.close()

        self.assertEqual(len(messages), 1)
        self.assertIn("another dotfiles-sync holds", messages[0])


if __name__ == "__main__":
    unittest.main()
