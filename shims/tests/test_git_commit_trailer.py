#!/usr/bin/env python3
"""shims/git gives an omp session's `git commit` its Omp-Session trailer, against the real git."""
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


if __name__ == "__main__":
    unittest.main()
