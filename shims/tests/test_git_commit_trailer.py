#!/usr/bin/env python3
"""shims/git gives every commit an omp session makes with `git commit`, `merge`, `pull`,
`cherry-pick` or `revert` its Omp-Session trailer, against the real git."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SHIMS = Path(__file__).parents[1]
AGENT_MARKERS = ("OMP_SESSION_ID", "PI_SESSION_FILE", "PI_TOOL_BRIDGE_URL", "AGENTBOX_SESSION")
SESSION = "01a0fa64-b3a5-7415-b143-c2a338b5da8b"


class GitCommitTrailerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        key = self.tmp / "signing"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
        signers = self.tmp / "allowed-signers"
        signers.write_text(f'agent@example.com namespaces="git" {(self.tmp / "signing.pub").read_text()}')
        config = self.tmp / "gitconfig"
        config.write_text(
            "[user]\n\tname = Agent\n\temail = agent@example.com\n\tsigningkey = " + str(key) + "\n"
            "[gpg]\n\tformat = ssh\n[gpg \"ssh\"]\n\tallowedSignersFile = " + str(signers) + "\n"
            "[commit]\n\tgpgsign = true\n[init]\n\tdefaultBranch = main\n"
        )
        base = {k: v for k, v in os.environ.items() if k not in AGENT_MARKERS and not k.startswith("GIT_CONFIG_")}
        self.plain = {**base, "PATH": f"{SHIMS}{os.pathsep}{os.environ['PATH']}", "GIT_CONFIG_GLOBAL": str(config), "GIT_CONFIG_NOSYSTEM": "1"}
        self.agent = {**self.plain, "OMP_SESSION_ID": SESSION}
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.git("init", "-q", env=self.plain)
        self.git("commit", "-q", "--allow-empty", "-m", "init", env=self.plain)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def git(self, *args: str, env: dict[str, str], cwd: Path | None = None) -> str:
        result = subprocess.run(["git", *args], cwd=cwd or self.repo, env=env, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def trailers(self) -> list[str]:
        out = self.git("log", "-1", "--format=%(trailers:key=Omp-Session,valueonly)", env=self.plain)
        return [line for line in out.splitlines() if line]

    def test_an_agent_commit_carries_one_trailer_naming_its_session_and_stays_signed(self):
        self.git("commit", "-q", "--allow-empty", "-m", "work", env=self.agent)
        self.assertEqual(self.trailers(), [SESSION])
        self.assertEqual(self.git("log", "-1", "--format=%G?", env=self.plain).strip(), "G")

    def test_an_amend_in_the_same_session_keeps_one_trailer(self):
        self.git("commit", "-q", "--allow-empty", "-m", "work", env=self.agent)
        self.git("commit", "-q", "--amend", "--allow-empty", "--no-edit", env=self.agent)
        self.assertEqual(self.trailers(), [SESSION])

    def test_a_commit_outside_an_agent_session_is_unchanged(self):
        self.git("commit", "-q", "--allow-empty", "-m", "work", env=self.plain)
        self.assertEqual(self.git("log", "-1", "--format=%B", env=self.plain).strip(), "work")

    def test_a_malformed_session_id_adds_no_trailer(self):
        self.git("commit", "-q", "--allow-empty", "-m", "work", env={**self.plain, "OMP_SESSION_ID": "test-session", "AGENTBOX_SESSION": "1"})
        self.assertEqual(self.trailers(), [])

    def test_the_repository_commit_msg_hook_still_runs(self):
        hook = self.repo / ".git" / "hooks" / "commit-msg"
        hook.write_text('#!/bin/sh\ntouch "$(git rev-parse --git-dir)/hook-ran"\ngit interpret-trailers --in-place --trailer "Hook: yes" "$1"\n')
        hook.chmod(0o755)
        self.git("commit", "-q", "--allow-empty", "-m", "work", env=self.agent)
        self.assertTrue((self.repo / ".git" / "hook-ran").exists())
        self.assertEqual(self.trailers(), [SESSION])
        self.assertIn("Hook: yes", self.git("log", "-1", "--format=%B", env=self.plain))

    def test_a_commit_behind_global_options_still_gets_the_trailer(self):
        self.git("-C", str(self.repo), "commit", "-q", "--allow-empty", "-m", "work", env=self.agent, cwd=self.tmp)
        self.assertEqual(self.trailers(), [SESSION])

    def branch_with_commit(self, name: str, message: str, env: dict[str, str]) -> None:
        self.git("checkout", "-q", "-b", name, env=self.plain)
        (self.repo / name).write_text(name)
        self.git("add", name, env=self.plain)
        self.git("commit", "-q", "-m", message, env=env)
        self.git("checkout", "-q", "main", env=self.plain)

    def test_a_merge_commit_carries_it_and_stays_signed(self):
        self.branch_with_commit("side", "side work", self.plain)
        (self.repo / "main-file").write_text("m")
        self.git("add", "main-file", env=self.plain)
        self.git("commit", "-q", "-m", "main work", env=self.plain)
        self.git("merge", "-q", "--no-edit", "side", env=self.agent)
        self.assertEqual(self.git("log", "-1", "--format=%P", env=self.plain).count(" "), 1)
        self.assertEqual(self.trailers(), [SESSION])
        self.assertEqual(self.git("log", "-1", "--format=%G?", env=self.plain).strip(), "G")

    def test_a_fast_forward_merge_and_a_plain_merge_are_otherwise_unchanged(self):
        self.branch_with_commit("side", "side work", self.plain)
        self.git("merge", "-q", "side", env=self.agent)
        self.assertEqual(self.git("log", "-1", "--format=%B", env=self.plain).strip(), "side work")
        self.branch_with_commit("other", "other work", self.plain)
        (self.repo / "x").write_text("x")
        self.git("add", "x", env=self.plain)
        self.git("commit", "-q", "-m", "x", env=self.plain)
        self.git("merge", "-q", "--no-edit", "other", env=self.plain)
        self.assertEqual(self.trailers(), [])

    def test_a_cherry_pick_and_a_revert_carry_it_once(self):
        other = "01a0fa64-0000-7415-b143-c2a338b5da8b"
        self.branch_with_commit("side", "side work", {**self.agent, "OMP_SESSION_ID": other})
        self.git("cherry-pick", "side", env=self.agent)
        self.assertEqual(self.trailers(), [other, SESSION])
        self.git("revert", "--no-edit", "HEAD", env=self.agent)
        self.assertEqual(self.trailers(), [SESSION])
        self.git("commit", "-q", "--amend", "--no-edit", env=self.agent)
        self.assertEqual(self.trailers(), [SESSION])

    def test_a_pull_merge_carries_it_and_a_pull_rebase_leaves_replayed_commits_alone(self):
        upstream = self.tmp / "upstream"
        self.git("clone", "-q", str(self.repo), str(upstream), env=self.plain)
        (upstream / "u").write_text("u")
        self.git("add", "u", env=self.plain, cwd=upstream)
        self.git("commit", "-q", "-m", "upstream work", env=self.plain, cwd=upstream)
        self.git("remote", "add", "up", str(upstream), env=self.plain)
        (self.repo / "l").write_text("l")
        self.git("add", "l", env=self.plain)
        self.git("commit", "-q", "-m", "local work", env=self.plain)
        self.git("pull", "-q", "--no-rebase", "--no-edit", "up", "main", env=self.agent)
        self.assertEqual(self.trailers(), [SESSION])
        self.git("reset", "-q", "--hard", "HEAD^1", env=self.plain)
        self.git("pull", "-q", "--rebase", "up", "main", env=self.agent)
        self.assertEqual(self.git("log", "-1", "--format=%B", env=self.plain).strip(), "local work")

    def test_the_repository_hooks_still_run_for_a_merge(self):
        hooks = self.repo / ".git" / "hooks"
        for name in ("prepare-commit-msg", "commit-msg", "post-merge"):
            (hooks / name).write_text(f'#!/bin/sh\necho "$*" > "$(git rev-parse --git-dir)/{name}-ran"\n')
            (hooks / name).chmod(0o755)
        self.branch_with_commit("side", "side work", self.plain)
        (self.repo / "m").write_text("m")
        self.git("add", "m", env=self.plain)
        self.git("commit", "-q", "-m", "m", env=self.plain)
        for name in ("prepare-commit-msg", "commit-msg"):
            (self.repo / ".git" / f"{name}-ran").unlink()
        self.git("merge", "-q", "--no-edit", "side", env=self.agent)
        self.assertEqual(self.trailers(), [SESSION])
        self.assertEqual((self.repo / ".git" / "prepare-commit-msg-ran").read_text().split()[1], "merge")
        self.assertTrue((self.repo / ".git" / "commit-msg-ran").exists())
        self.assertTrue((self.repo / ".git" / "post-merge-ran").exists())

    def test_a_configured_hooks_path_is_still_the_one_that_runs(self):
        own = self.tmp / "own-hooks"
        own.mkdir()
        (own / "commit-msg").write_text('#!/bin/sh\ngit interpret-trailers --in-place --trailer "Hook: yes" "$1"\n')
        (own / "commit-msg").chmod(0o755)
        self.git("config", "core.hooksPath", str(own), env=self.plain)
        self.git("commit", "-q", "--allow-empty", "-m", "base", env=self.plain)
        self.branch_with_commit("side", "side work", self.plain)
        self.git("commit", "-q", "--allow-empty", "-m", "m", env=self.plain)
        self.git("merge", "-q", "--no-edit", "side", env=self.agent)
        body = self.git("log", "-1", "--format=%B", env=self.plain)
        self.assertIn("Hook: yes", body)
        self.assertEqual(self.trailers(), [SESSION])

    def test_a_merge_in_an_opted_out_checkout_carries_none(self):
        self.git("config", "omp.sessionTrailer", "false", env=self.plain)
        self.branch_with_commit("side", "side work", self.plain)
        self.git("commit", "-q", "--allow-empty", "-m", "m", env=self.plain)
        self.git("merge", "-q", "--no-edit", "side", env=self.agent)
        self.assertEqual(self.trailers(), [])


if __name__ == "__main__":
    unittest.main()
