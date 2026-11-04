#!/usr/bin/env python3
"""The jj shim checks the full outgoing stack, without depending on jj's git executable.

GitHub-looking URLs use only git-remote-sink, whose transport is a local bare path.
No network protocol is allowed, and every push asserts the local transport first.
"""
import os
import subprocess
import unittest
from pathlib import Path

from test_jj_push_leak_gate import JjPushLeakGateTest, ROCQ, PRIVATE
from test_jj_lock_shim import real_jj, run_bounded


class DirectPushGateTest(JjPushLeakGateTest):
    # Do not inherit the git shim's separate regression cases.
    def setUp(self):
        self.jj_binary = "jj"
        super().setUp()
        # The old gate does not see this executable: it must be protected at jj entry.
        self.git("config", "core.hooksPath", "/dev/null")
        self.push_config = ("--config", 'git.executable-path="/usr/bin/git"')
        config = self.home / ".config" / "gh-agent"
        config.mkdir(parents=True)
        (config / "hosts.yml").touch()
        (config / "config.yml").touch()
        self.env.update(DOTFILES_DIR=str(Path(__file__).parents[2]), GH_READ_CACHE="0", KNIVES_GH_BYPASS="1", GH_TOKEN="fixture-not-a-secret")

    def push(self, *args, cwd=None):
        self.keep_cache_fresh()
        # The helper derives this exact path from this exact URL, not pushurl.
        for remote in self.git("remote").splitlines():
            url = self.git("remote", "get-url", "--push", remote)
            self.assertIn(url, (ROCQ, PRIVATE))
            self.assertEqual(self.git("config", f"remote.{remote}.vcs"), "sink")
            sink = self.sink_of(url)
            self.assertTrue(sink.is_relative_to(self.tmp))
            if not sink.exists():
                subprocess.run(["/usr/bin/git", "init", "-q", "--bare", str(sink)], check=True, env=self.env)
            self.assertEqual(subprocess.run(["/usr/bin/git", "--git-dir", str(sink), "rev-parse", "--is-bare-repository"], capture_output=True, text=True).stdout.strip(), "true")
        self.assertEqual(self.env["GIT_ALLOW_PROTOCOL"], "sink")
        return self.jj(*self.push_config, *getattr(self, "push_command", ("git", "push")), *args, cwd=cwd)

    def test_middle_commit_blocks_entire_stack(self):
        first = self.commit("a", "one", "first")
        middle = self.commit("b", "two", f"Port the guard check graded on {self.customer}")
        last = self.commit("c", "three", "last", "stack")
        result = self.push("-b", "stack")
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("LEAK CHECK: refusing to push", result.stderr)
        self.assertIn(middle[:7], result.stderr)
        for commit in (first, middle, last):
            self.assertEqual(self.received(commit), [])
        self.assertEqual(subprocess.run(["/usr/bin/git", "--git-dir", str(self.sink_of(ROCQ)), "for-each-ref"], capture_output=True, text=True).stdout, "")

    def test_clean_push_reaches_local_bare(self):
        commit = self.commit("a", "clean", "clean", "clean")
        self.assert_pushed(self.push("-b", "clean"), commit)

    def test_no_check_public_refuses(self):
        self.env["PUBLIC_LEAK_CHECK"] = str(self.tmp / "missing")
        # No cached answer: exercise the visibility API through fake gh.
        (self.cache / "theorem-labs_rocq").unlink()
        (self.ghdir / "gh").write_text('#!/bin/bash\nprintf "public\\n"\n')
        commit = self.commit("a", "clean", "clean", "clean")
        result = self.push("-b", "clean")
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("no public leak check", result.stderr)
        self.assertEqual(self.received(commit), [])

    def test_private_push_passes_sensitive_message(self):
        self.git("remote", "set-url", "origin", PRIVATE)
        commit = self.commit("a", "one", f"Port the guard check graded on {self.customer}", "private")
        self.assert_pushed(self.push("-b", "private"), commit, PRIVATE)

    def test_private_no_check_passes(self):
        self.env["PUBLIC_LEAK_CHECK"] = str(self.tmp / "missing")
        self.test_private_push_passes_sensitive_message()

    def test_nonpush_unchanged(self):
        argv = ["log", "-r", "@", "--no-graph", "-T", "commit_id"]
        direct = run_bounded([str(real_jj()), *argv], self.repo, self.env)
        through = self.jj(*argv)
        self.assertEqual((through.returncode, through.stdout, through.stderr), (direct.returncode, direct.stdout, direct.stderr))

    def test_quiet_clean_push_passes(self):
        commit = self.commit("a", "clean", "clean", "clean")
        self.assert_pushed(self.push("--quiet", "-b", "clean"), commit)

    def test_all_remote_plans_pass_before_any_remote_receives(self):
        self.git("remote", "set-url", "origin", PRIVATE)
        self.remote("public", ROCQ)
        commit = self.commit("a", "one", f"Port the guard check graded on {self.customer}", "stack")
        result = self.push("--remote", "origin", "--remote", "public", "-b", "stack")
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("LEAK CHECK: refusing to push", result.stderr)
        self.assertEqual(self.received(commit), [])

    def test_missing_check_unknown_refuses(self):
        self.env["PUBLIC_LEAK_CHECK"] = str(self.tmp / "missing")
        (self.cache / "theorem-labs_rocq").unlink()
        commit = self.commit("a", "clean", "clean", "clean")
        self.assertNotEqual(self.push("-b", "clean").returncode, 0)
        self.assertEqual(self.received(commit), [])

    def test_api_confirmed_private_without_check_passes(self):
        self.env["PUBLIC_LEAK_CHECK"] = str(self.tmp / "missing")
        (self.cache / "theorem-labs_private-fixture").unlink()
        (self.ghdir / "gh").write_text('#!/bin/bash\nprintf "private\\n"\n')
        self.test_private_push_passes_sensitive_message()

    def test_checker_error_refuses_without_publishing(self):
        check = self.tmp / "broken-check"
        check.write_text('#!/bin/bash\necho "LEAK CHECK ERROR: injected" >&2\nexit 2\n')
        check.chmod(0o755)
        self.env["PUBLIC_LEAK_CHECK"] = str(check)
        commit = self.commit("a", "clean", "clean", "clean")
        result = self.push("-b", "clean")
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("exit 2", result.stderr)
        self.assertEqual(self.received(commit), [])

    def test_move_and_delete_use_pre_push_old_and_new_ids(self):
        first = self.commit("a", "first", "first", "topic")
        self.assert_pushed(self.push("-b", "topic"), first)
        second = self.commit("b", "second", "second", "topic")
        recorder = self.tmp / "check-recorder"
        lines = self.tmp / "updates"
        recorder.write_text(f'#!/bin/bash\ncat > "{lines}"\nexec bash "{self.check}" "$@" < "{lines}"\n')
        recorder.chmod(0o755)
        self.env["PUBLIC_LEAK_CHECK"] = str(recorder)
        self.assert_pushed(self.push("-b", "topic"), second)
        self.assertEqual(lines.read_text().strip(), f"refs/heads/topic {second} refs/heads/topic {first}")
        self.ok("bookmark", "delete", "topic")
        self.assertEqual(self.push("-b", "topic").returncode, 0)
        self.assertEqual(lines.read_text().strip(), f"refs/heads/topic {'0' * 40} refs/heads/topic {second}")

    def test_repository_equals_option_preserves_clean_push(self):
        commit = self.commit("a", "clean", "clean", "clean")
        self.assert_pushed(self.push(f"-R={self.repo}", "-b", "clean", cwd=self.tmp), commit)

    def test_push_help_is_unchanged_outside_repository(self):
        direct = run_bounded([str(real_jj()), "git", "push", "--help"], self.tmp, self.env)
        through = self.jj("git", "push", "--help", cwd=self.tmp)
        self.assertEqual((through.returncode, through.stdout, through.stderr), (direct.returncode, direct.stdout, direct.stderr))

    def test_unresolved_ssh_target_is_not_treated_as_non_github(self):
        ssh = self.fake / "ssh"
        ssh.write_text('#!/bin/bash\nexit 1\n')
        ssh.chmod(0o755)
        helper = Path(__file__).parents[2] / "scripts" / "github-repo-visibility"
        result = subprocess.run([str(helper), "--slug", "alias:theorem-labs/rocq.git"], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), "unknown")

    def test_nested_push_alias_blocks_middle_commit_from_repository_context(self):
        first = self.commit("a", "one", "first")
        middle = self.commit("b", "two", f"Port the guard check graded on {self.customer}")
        last = self.commit("c", "three", "last", "stack")
        self.ok("config", "set", "--repo", "aliases.p", '["push-stack", "--bookmark", "stack"]')
        self.ok("config", "set", "--repo", "aliases.push-stack", '["git", "push"]')
        self.push_command = ("p",)
        result = self.push("--quiet")
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("LEAK CHECK: refusing to push", result.stderr)
        self.assertIn(middle[:7], result.stderr)
        for commit in (first, middle, last):
            self.assertEqual(self.received(commit), [])

    def test_clean_push_alias_preserves_own_and_caller_arguments(self):
        commit = self.commit("a", "clean", "clean", "clean")
        self.ok("config", "set", "--repo", "aliases.p", '["git", "push", "--bookmark", "clean"]')
        self.push_command = ("p",)
        self.assert_pushed(self.push("--remote", "origin"), commit)

    def test_nonpush_alias_is_unchanged(self):
        self.ok("config", "set", "--repo", "aliases.l", '["log", "-r", "@", "--no-graph", "-T", "commit_id"]')
        direct = run_bounded([str(real_jj()), "l"], self.repo, self.env)
        through = self.jj("l")
        self.assertEqual((through.returncode, through.stdout, through.stderr), (direct.returncode, direct.stdout, direct.stderr))

    def test_up_to_date_push_passes_without_a_check(self):
        commit = self.commit("a", "clean", "clean", "clean")
        self.assert_pushed(self.push("-b", "clean"), commit)
        before = self.received(commit)
        self.env["PUBLIC_LEAK_CHECK"] = str(self.tmp / "missing")
        result = self.push("-b", "clean")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Nothing changed.", result.stderr)
        self.assertEqual(self.received(commit), before)

    def test_alias_config_failure_refuses_without_publishing(self):
        commit = self.commit("a", "clean", "clean", "clean")
        self.ok("config", "set", "--repo", "aliases.p", '["git", "push", "-b", "clean"]')
        wrapper = self.fake / "jj"
        wrapper.write_text('#!/bin/bash\nprevious=""\nfor arg in "$@"; do\n if [[ "$previous" == config && "$arg" == list ]]; then exit 2; fi\n previous="$arg"\ndone\nexec "' + str(real_jj()) + '" "$@"\n')
        wrapper.chmod(0o755)
        self.push_command = ("p",)
        result = self.push()
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("alias", result.stderr)
        self.assertEqual(self.received(commit), [])

    def test_alias_depth_limit_refuses_without_publishing(self):
        commit = self.commit("a", "clean", "clean", "clean")
        for index in range(9):
            self.ok("config", "set", "--repo", f"aliases.p{index}", f'["p{index + 1}"]')
        self.ok("config", "set", "--repo", "aliases.p9", '["git", "push", "-b", "clean"]')
        self.push_command = ("p0",)
        result = self.push()
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("alias", result.stderr)
        self.assertEqual(self.received(commit), [])

    def test_alias_cannot_bypass_deleted_refusal(self):
        commit = self.commit("a", "clean", "clean", "clean")
        self.ok("config", "set", "--repo", "aliases.p", '["git", "push", "--deleted"]')
        self.push_command = ("p",)
        result = self.push()
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("is refused in an agent session", result.stderr)
        self.assertEqual(self.received(commit), [])

    def test_alias_lookup_matches_invocation_context_before_repository_option(self):
        invocation = self.repo
        self.ok("config", "set", "--repo", "aliases.p", '["git", "push", "-b", "stack"]')
        other = self.tmp / "other"
        self.ok("git", "init", "--colocate", str(other))
        self.repo = other
        self.remote("origin", ROCQ)
        self.ok("config", "set", "--repo", "aliases.p", '["log"]')
        first = self.commit("a", "one", "first")
        middle = self.commit("b", "two", f"Port the guard check graded on {self.customer}")
        last = self.commit("c", "three", "last", "stack")
        self.push_config += ("--repository", str(other))
        self.push_command = ("p",)
        result = self.push(cwd=invocation)
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("LEAK CHECK: refusing to push", result.stderr)
        for commit in (first, middle, last):
            self.assertEqual(self.received(commit), [])


# Keep these cases separate from the git shim's regression suite.
def load_tests(loader, tests, pattern):
    return unittest.TestSuite(DirectPushGateTest(name) for name in DirectPushGateTest.__dict__ if name.startswith("test_"))


if __name__ == "__main__":
    unittest.main()
