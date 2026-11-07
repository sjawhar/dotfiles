#!/usr/bin/env python3
"""The jj shim checks the full outgoing stack, without depending on jj's git executable.

GitHub-looking URLs use only git-remote-sink, whose transport is a local bare path.
No network protocol is allowed, and every push asserts the local transport first.
"""
import os
import subprocess
import unittest
from pathlib import Path

import test_jj_push_leak_gate as gate
from test_jj_push_leak_gate import ROCQ, PRIVATE
from test_jj_lock_shim import real_jj, run_bounded

# The agent markers shims/jj reads, and its own agent_context function, cut from the shim, so the harness can prove a
# "person" environment is one the shim does not take for an agent session.
AGENT_MARKERS = ("OMP_SESSION_ID", "PI_SESSION_FILE", "PI_TOOL_BRIDGE_URL", "AGENTBOX_SESSION",
                 "CLAUDECODE", "ENVOY_SESSION_ID", "AGENT_SHIM_AGENT", "LEGION_ROLE", "LEGION_TREE",
                 "LEGION_CONTROLLER", "LEGION_DAEMON_URL")
_JJ_SHIM = (Path(__file__).parents[1] / "jj").read_text(encoding="utf-8")
AGENT_CONTEXT = _JJ_SHIM[_JJ_SHIM.index("agent_context() {"):_JJ_SHIM.index("\n}\n", _JJ_SHIM.index("agent_context() {")) + 3]


class DirectPushGateTest(gate.JjPushLeakGateTest):
    # Only the cases below run here; the git shim's own cases, which this class inherits with its fixture, are blanked
    # after the class, so no test loader runs them through shims/jj (whose refusal is not the git shim's).
    def setUp(self):
        self.jj_binary = "jj"
        super().setUp()
        # The old gate does not see this executable: it must be protected at jj entry.
        self.git("config", "core.hooksPath", "/dev/null")
        self.push_config = ("--config", 'git.executable-path="/usr/bin/git"')

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
        ssh = self.ghdir / "ssh"  # the ssh first on PATH, in place of the base class's SSH_CONFIG one
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

    # Round 8 (dispatch://BET-706, Sami: dispatch://BET-64/ask/0b96691d-1104-4a08-bda7-e544a32215b5): a person's own
    # `jj git push` outside an agent session runs the same gate where this machine has the check installed.
    def as_person(self):
        """No agent marker shims/jj reads (agent_context): none of the four session variables, no GIT_CONFIG_*
        entries (the gh-app routing include is one), no SSH_CONNECTION, which in a session with no terminal is
        an agent's command too, and no SSH_TTY or TMUX, which would make a case that means an agent a person
        (INFRA-1538), whatever the environment running this test holds."""
        dropped = (*AGENT_MARKERS, "SSH_CONNECTION", "SSH_TTY", "TMUX", "AGENT_SHIM_PERSON")
        self.env = {k: v for k, v in self.env.items() if k not in dropped and not k.startswith("GIT_CONFIG_")}
        probe = subprocess.run(["bash", "-c", "set -u; " + AGENT_CONTEXT + "\nagent_context"], env=self.env,
                               stdin=subprocess.DEVNULL, capture_output=True, text=True)
        self.assertEqual(probe.returncode, 1, "an agent marker is still set: " + probe.stderr)

    def without_check(self):
        """No check on this machine, by any of the three ways the shim looks: PUBLIC_LEAK_CHECK, PATH, the pointer."""
        self.env.pop("PUBLIC_LEAK_CHECK", None)
        self.env["XDG_CONFIG_HOME"] = str(self.tmp / "no-check-config")
        self.env["PATH"] = os.pathsep.join(p for p in self.env["PATH"].split(os.pathsep) if not (Path(p) / "public-leak-check.sh").exists())

    def test_a_persons_leaking_push_is_refused_where_the_check_is_installed(self):
        # (a)
        self.as_person()
        commit = self.leak()
        result = self.push("-b", "b1")
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("LEAK CHECK: refusing to push", result.stderr)
        self.assertEqual(result.stderr.rstrip().splitlines()[-1],
                         "error: jj shim: to push this one command without the public leak check, run it with DOTFILES_JJ_NO_LEAK_CHECK=1.")
        self.assertEqual(self.received(commit), [])
        self.assertEqual(subprocess.run(["/usr/bin/git", "--git-dir", str(self.sink_of(ROCQ)), "for-each-ref"], capture_output=True, text=True).stdout, "")

    def test_a_persons_clean_push_lands_after_one_check(self):
        # (b)
        self.as_person()
        checks = self.count_checks()
        commit = self.commit("a", "clean", "clean", "clean")
        self.assert_pushed(self.push("-b", "clean"), commit)
        self.assertEqual(self.lines(checks), [f"origin {ROCQ}"])

    def test_a_persons_push_without_a_check_is_unchanged(self):
        # (c) A machine without the check (a laptop): the push goes through as jj runs it, the leak included.
        self.as_person()
        self.without_check()
        commit = self.leak()
        result = self.push("-b", "b1")
        self.assert_pushed(result, commit)
        self.assertNotIn("jj shim", result.stderr)

    def test_a_person_may_skip_the_check_for_one_command(self):
        # (d) DOTFILES_JJ_NO_LEAK_CHECK=1 outside an agent session: no check runs, the push goes through.
        self.as_person()
        checks = self.count_checks()
        self.env["DOTFILES_JJ_NO_LEAK_CHECK"] = "1"
        commit = self.leak()
        self.assert_pushed(self.push("-b", "b1"), commit)
        self.assertEqual(self.lines(checks), [])

    def test_an_agent_cannot_skip_the_check(self):
        # (e) In an agent session the variable is ignored, and the refusal names no way past it.
        self.env["DOTFILES_JJ_NO_LEAK_CHECK"] = "1"
        commit = self.leak()
        result = self.push("-b", "b1")
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("LEAK CHECK: refusing to push", result.stderr)
        self.assertNotIn("DOTFILES_JJ_NO_LEAK_CHECK", result.stderr)
        self.assertEqual(self.received(commit), [])

    def test_a_push_over_ssh_with_no_terminal_is_an_agents(self):
        # INFRA-1538: `ssh sami-devbox jj git push` cannot skip the check, as no agent can.
        self.as_person()
        self.env["SSH_CONNECTION"] = "10.0.0.1 54321 10.0.0.2 22"
        self.env["DOTFILES_JJ_NO_LEAK_CHECK"] = "1"
        commit = self.leak()
        result = self.push("-b", "b1")
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("LEAK CHECK: refusing to push", result.stderr)
        self.assertNotIn("DOTFILES_JJ_NO_LEAK_CHECK", result.stderr)
        self.assertEqual(self.received(commit), [])

    def test_agent_shim_person_keeps_the_persons_skip_over_ssh(self):
        self.as_person()
        self.env["SSH_CONNECTION"] = "10.0.0.1 54321 10.0.0.2 22"
        self.env["AGENT_SHIM_PERSON"] = "1"
        self.env["DOTFILES_JJ_NO_LEAK_CHECK"] = "1"
        checks = self.count_checks()
        commit = self.leak()
        self.assert_pushed(self.push("-b", "b1"), commit)
        self.assertEqual(self.lines(checks), [])

    def test_a_persons_ssh_session_keeps_the_persons_skip(self):
        # jj's own stdin here is no terminal, as it is for any command in a pipeline; the
        # person's session is what decides (review of INFRA-1538).
        self.as_person()
        self.env["SSH_CONNECTION"] = "10.0.0.1 54321 10.0.0.2 22"
        self.env["SSH_TTY"] = "/dev/pts/7"
        self.env["DOTFILES_JJ_NO_LEAK_CHECK"] = "1"
        checks = self.count_checks()
        commit = self.leak()
        self.assert_pushed(self.push("-b", "b1"), commit)
        self.assertEqual(self.lines(checks), [])

    def test_an_agent_without_a_check_is_unchanged(self):
        # (f) Agent session, no check: a public destination is refused, a confirmed-private one passes.
        self.without_check()
        commit = self.commit("a", "clean", "clean", "clean")
        result = self.push("-b", "clean")
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("no public leak check", result.stderr)
        self.assertNotIn("DOTFILES_JJ_NO_LEAK_CHECK", result.stderr)
        self.assertEqual(self.received(commit), [])
        self.test_private_push_passes_sensitive_message()

    def test_a_person_is_not_refused_deleted(self):
        # The --deleted refusal stays agent-only: a person's `jj git push --deleted` with nothing to delete runs.
        self.as_person()
        result = self.push("--deleted")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("is refused in an agent session", result.stderr)

    # Round 8b (review slice3-xf-dotfiles.md "## Round 8", P2): a person's command that is not a push runs as jj runs
    # it, however deep its aliases; a deep push alias is still resolved and checked.
    def alias_chain(self, prefix: str, levels: int, target: list[str]) -> str:
        for index in range(levels):
            self.ok("config", "set", "--repo", f"aliases.{prefix}{index}", f'["{prefix}{index + 1}"]')
        self.ok("config", "set", "--repo", f"aliases.{prefix}{levels}", "[" + ", ".join(f'"{t}"' for t in target) + "]")
        return f"{prefix}0"

    def test_a_persons_deep_non_push_alias_runs_unchanged(self):
        # The reviewer's l0 -> ... -> l9 -> log chain, with the check installed.
        self.as_person()
        first = self.alias_chain("l", 9, ["log", "-r", "@", "--no-graph", "-T", "commit_id"])
        direct = run_bounded([str(real_jj()), first], self.repo, self.env)
        self.assertEqual(direct.returncode, 0, direct.stderr)
        through = self.jj(first)
        self.assertEqual((through.returncode, through.stdout, through.stderr), (direct.returncode, direct.stdout, direct.stderr))

    def test_a_persons_deep_push_alias_is_checked(self):
        self.as_person()
        commit = self.leak()
        self.push_command = (self.alias_chain("p", 9, ["git", "push", "-b", "b1"]),)
        result = self.push()
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("LEAK CHECK: refusing to push", result.stderr)
        self.assertEqual(self.received(commit), [])

    def test_an_agents_deep_push_alias_is_still_refused_at_the_cap(self):
        commit = self.leak()
        self.push_command = (self.alias_chain("p", 9, ["git", "push", "-b", "b1"]),)
        result = self.push()
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("alias expansion exceeds 8 levels", result.stderr)
        self.assertEqual(self.received(commit), [])

    # Round 8c P1 (review of 8b): for a person the shim cannot prove an unresolved alias is not a push, so it fails
    # closed, except for a cycle, which jj refuses itself.
    BYPASS_LINE = "error: jj shim: to push this one command without the public leak check, run it with DOTFILES_JJ_NO_LEAK_CHECK=1."

    def assert_person_refused_unpushed(self, result, commit, why: str):
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn(why, result.stderr)
        self.assertEqual(result.stderr.rstrip().splitlines()[-1], self.BYPASS_LINE)
        self.assertEqual(self.received(commit), [])

    def test_a_persons_push_alias_past_64_levels_is_refused(self):
        self.as_person()
        commit = self.leak()
        self.push_command = (self.alias_chain("q", 66, ["git", "push", "-b", "b1"]),)
        self.assert_person_refused_unpushed(self.push(), commit, "alias expansion exceeds 64 levels")

    def test_a_persons_short_push_alias_is_refused_when_the_alias_probe_fails(self):
        self.as_person()
        commit = self.leak()
        self.ok("config", "set", "--repo", "aliases.p", '["git", "push", "-b", "b1"]')
        wrapper = self.fake / "jj"
        wrapper.write_text('#!/bin/bash\nprevious=""\nfor arg in "$@"; do\n if [[ "$previous" == config && "$arg" == list ]]; then exit 2; fi\n previous="$arg"\ndone\nexec "' + str(real_jj()) + '" "$@"\n')
        wrapper.chmod(0o755)
        self.push_command = ("p",)
        self.assert_person_refused_unpushed(self.push(), commit, "cannot read alias configuration for p")

    def test_a_persons_builtin_runs_without_any_alias_work(self):
        # A builtin other than `git` is no push and needs no alias probe: it runs even where the probe fails.
        self.as_person()
        wrapper = self.fake / "jj"
        wrapper.write_text('#!/bin/bash\nprevious=""\nfor arg in "$@"; do\n if [[ "$previous" == config && "$arg" == list ]]; then exit 2; fi\n previous="$arg"\ndone\nexec "' + str(real_jj()) + '" "$@"\n')
        wrapper.chmod(0o755)
        argv = ["log", "-r", "@", "--no-graph", "-T", "commit_id"]
        direct = run_bounded([str(real_jj()), *argv], self.repo, self.env)
        through = self.jj(*argv)
        self.assertEqual((through.returncode, through.stdout, through.stderr), (direct.returncode, direct.stdout, direct.stderr))

    def test_a_persons_cyclic_alias_gets_jjs_own_error_and_pushes_nothing(self):
        self.as_person()
        commit = self.leak()
        self.ok("config", "set", "--repo", "aliases.c1", '["c2", "git", "push", "-b", "b1"]')
        self.ok("config", "set", "--repo", "aliases.c2", '["c1"]')
        direct = run_bounded([str(real_jj()), "c1"], self.repo, self.env)
        self.assertNotEqual(direct.returncode, 0, direct.stderr)
        through = self.jj("c1")
        self.assertEqual((through.returncode, through.stdout, through.stderr), (direct.returncode, direct.stdout, direct.stderr))
        self.assertNotIn("jj shim", through.stderr)
        self.assertEqual(self.received(commit), [])


# The git shim's cases belong to test_jj_push_leak_gate alone. Run through this class, every jj push goes through
# shims/jj, which refuses at jj's entry, so their assertions on the git shim's own lines fail. A test loader that
# ignores load_tests (pytest), or a test named on the command line, used to run them here; a name that is not callable
# is no test to unittest's loader or pytest's. The base class is imported as a module attribute, so it is collected
# only from its own module.
for _name in dir(gate.JjPushLeakGateTest):
    if _name.startswith("test_") and _name not in DirectPushGateTest.__dict__:
        setattr(DirectPushGateTest, _name, None)
del _name


if __name__ == "__main__":
    unittest.main()
