#!/usr/bin/env python3
"""shims/git and shims/jj refuse an agent session's push to a public theorem-labs repository unless
the checkout sets omp.publicPush (scripts/omp-public-push), against the real git and jj, with GitHub
answered by the fake curl and gh of test_session_trailer_visibility. Nothing reaches GitHub: every
push that is allowed lands in a local bare repository through a pushurl or an insteadOf."""
import os
import subprocess
import unittest
from pathlib import Path

from test_session_trailer_visibility import PUBLIC, SESSION, Base, real_jj

INTERNAL = "https://github.com/theorem-labs/oss-verify-proofs-internal"


class PushBase(Base):
    def setUp(self):
        super().setUp()
        self.bare = self.tmp / "bare.git"
        self.run_ok("git", "init", "-q", "--bare", str(self.bare), cwd=self.tmp, env=self.plain)

    def push(self, cmd: list[str], repo: Path, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(cmd, cwd=repo, env={**self.agent, **extra}, capture_output=True, text=True, check=False)

    def assertRefused(self, result: subprocess.CompletedProcess[str], slug: str = "theorem-labs/oss-verify-proofs"):
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(f"push refused: github.com/{slug} is a public repository", result.stderr)
        self.assertIn("git config omp.publicPush true", result.stderr)
        self.assertIn("`gh api` writes (contents PUT, git/refs) are not covered", result.stderr)

    def assertPushed(self, result: subprocess.CompletedProcess[str]):
        self.assertEqual(result.returncode, 0, result.stderr)
        heads = self.run_ok("git", "--git-dir", str(self.bare), "for-each-ref", "--format=%(refname)", cwd=self.tmp, env=self.plain)
        self.assertIn("refs/heads/main", heads)


class GitPushTest(PushBase):
    def repo(self, *remotes: tuple[str, str]) -> Path:
        repo = self.tmp / "repo"
        repo.mkdir()
        self.run_ok("git", "init", "-q", cwd=repo, env=self.plain)
        self.run_ok("git", "commit", "-q", "--allow-empty", "-m", "work", cwd=repo, env=self.plain)
        for name, url in remotes:
            self.run_ok("git", "remote", "add", name, url, cwd=repo, env=self.plain)
            self.run_ok("git", "config", f"remote.{name}.pushurl", str(self.bare), cwd=repo, env=self.plain)
        return repo

    def git(self, repo: Path, *args: str):
        self.run_ok("git", *args, cwd=repo, env=self.plain)

    def test_a_public_theorem_labs_remote_is_refused(self):
        repo = self.repo(("origin", PUBLIC))
        self.assertRefused(self.push(["git", "push", "origin", "main"], repo, FAKE_DEFAULT="200"))
        self.assertEqual(self.calls(), ["theorem-labs/oss-verify-proofs"])

    def test_the_default_remote_is_judged_too(self):
        repo = self.repo(("origin", PUBLIC))
        self.assertRefused(self.push(["git", "push"], repo, FAKE_DEFAULT="200"))

    def test_the_opt_in_allows_it(self):
        repo = self.repo(("origin", PUBLIC))
        self.git(repo, "config", "omp.publicPush", "true")
        self.assertPushed(self.push(["git", "push", "origin", "main"], repo, FAKE_DEFAULT="200"))
        self.assertEqual(self.calls(), [])

    def test_private_internal_and_other_owners_are_allowed(self):
        repo = self.repo(("origin", "git@github.com:theorem-labs/grading-tools.git"), ("internal", INTERNAL), ("mine", "https://github.com/sjawhar/dotfiles"))
        codes = "theorem-labs/grading-tools=404 theorem-labs/oss-verify-proofs-internal=403"
        self.assertPushed(self.push(["git", "push", "origin", "main"], repo, FAKE_CODES=codes))
        self.assertPushed(self.push(["git", "push", "internal", "main"], repo, FAKE_CODES=codes, FAKE_GH="internal"))
        self.assertPushed(self.push(["git", "push", "mine", "main"], repo, FAKE_DEFAULT="200"))
        self.assertNotIn("sjawhar/dotfiles", self.calls())

    def test_unknown_visibility_is_refused(self):
        repo = self.repo(("origin", PUBLIC))
        result = self.push(["git", "push", "origin", "main"], repo, FAKE_DEFAULT="fail")
        self.assertRefused(result)
        self.assertIn("GitHub could not say whether it is public", result.stderr)

    def test_a_dry_run_passes_without_asking(self):
        repo = self.repo(("origin", PUBLIC))
        for flags in (["--dry-run"], ["-n"], ["-fn"]):
            result = self.push(["git", "push", *flags, "origin", "main"], repo, FAKE_DEFAULT="200")
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [])

    def test_a_named_non_default_remote_is_the_one_judged(self):
        repo = self.repo(("origin", "https://github.com/sjawhar/dotfiles"), ("public", PUBLIC))
        self.assertRefused(self.push(["git", "push", "public", "main"], repo, FAKE_DEFAULT="200"))
        self.assertPushed(self.push(["git", "push", "origin", "main"], repo, FAKE_DEFAULT="200"))
        self.git(repo, "config", "remote.pushDefault", "public")
        self.assertRefused(self.push(["git", "push"], repo, FAKE_DEFAULT="200"))
        self.assertRefused(self.push(["git", "push", "--repo", "public"], repo, FAKE_DEFAULT="200"))

    def test_a_url_argument_is_judged_by_the_url(self):
        repo = self.repo()
        # Both URLs land in the bare repository if git ever pushes to them.
        self.git(repo, "config", f"url.{self.bare}.insteadOf", "https://github.com/theorem-labs/")
        codes = "theorem-labs/oss-verify-proofs=200 theorem-labs/oss-verify-proofs-internal=404"
        self.assertRefused(self.push(["git", "push", "https://github.com/theorem-labs/oss-verify-proofs", "HEAD:refs/heads/main"], repo, FAKE_CODES=codes))
        self.assertRefused(self.push(["git", "-C", str(repo), "push", PUBLIC, "HEAD:refs/heads/main"], self.tmp, FAKE_CODES=codes))
        self.git(repo, "config", "--unset", f"url.{self.bare}.insteadOf")
        self.git(repo, "config", f"url.{self.bare}.insteadOf", INTERNAL)
        self.assertPushed(self.push(["git", "push", INTERNAL, "HEAD:refs/heads/main"], repo, FAKE_CODES=codes))

    def test_outside_an_agent_session_nothing_is_checked(self):
        repo = self.repo(("origin", PUBLIC))
        result = subprocess.run(["git", "push", "origin", "main"], cwd=repo, env={**self.plain, "FAKE_DEFAULT": "200"}, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [])


class JjPushTest(PushBase):
    def setUp(self):
        super().setUp()
        jj = real_jj()
        if jj is None:
            raise unittest.SkipTest("no real jj")
        self.agent["PATH"] = os.pathsep.join([str(self.tmp / "fake-bin"), str(Path(__file__).parents[1]), str(Path(jj).parent), self.agent["PATH"]])
        user = self.tmp / "jj-user.toml"
        user.write_text('[user]\nname = "Agent"\nemail = "agent@example.com"\n[signing]\nbehavior = "drop"\n')
        self.agent["JJ_CONFIG"] = str(user)
        self.repo = self.tmp / "jjrepo"
        self.repo.mkdir()
        self.run_ok("git", "init", "-q", cwd=self.repo, env=self.plain)
        self.run_ok("git", "remote", "add", "origin", PUBLIC, cwd=self.repo, env=self.plain)
        self.run_ok("git", "config", "remote.origin.pushurl", str(self.bare), cwd=self.repo, env=self.plain)
        self.jj("git", "init", "--colocate")
        (self.repo / "file").write_text("x")
        self.jj("commit", "-m", "work")
        self.jj("bookmark", "create", "main", "-r", "@-")

    def jj(self, *args: str, **extra: str) -> str:
        return self.run_ok("jj", *args, cwd=self.repo, env={**self.agent, **extra})

    def test_a_public_theorem_labs_remote_is_refused(self):
        self.assertRefused(self.push(["jj", "git", "push", "-b", "main"], self.repo, FAKE_DEFAULT="200"))

    def test_the_opt_in_allows_it(self):
        self.run_ok("git", "config", "omp.publicPush", "true", cwd=self.repo, env=self.plain)
        self.assertPushed(self.push(["jj", "git", "push", "-b", "main"], self.repo, FAKE_DEFAULT="200"))

    def test_a_dry_run_passes(self):
        result = self.push(["jj", "git", "push", "-b", "main", "--dry-run"], self.repo, FAKE_DEFAULT="200")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [])

    def test_the_remote_option_and_git_push_setting_pick_the_remote(self):
        self.run_ok("git", "remote", "add", "mine", "https://github.com/sjawhar/dotfiles", cwd=self.repo, env=self.plain)
        self.run_ok("git", "config", "remote.mine.pushurl", str(self.bare), cwd=self.repo, env=self.plain)
        self.assertPushed(self.push(["jj", "git", "push", "-b", "main", "--remote", "mine"], self.repo, FAKE_DEFAULT="200"))
        self.assertRefused(self.push(["jj", "git", "push", "-b", "main", "--remote=origin"], self.repo, FAKE_DEFAULT="200"))
        self.assertPushed(self.push(["jj", "--config", "git.push=mine", "git", "push", "-b", "main"], self.repo, FAKE_DEFAULT="200"))


if __name__ == "__main__":
    unittest.main()
