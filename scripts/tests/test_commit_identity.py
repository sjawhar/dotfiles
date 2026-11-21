#!/usr/bin/env python3
"""The commit identity the dotfiles choose for a repository.

Every cybermonklabs repository but oh-my-pi (noreply, by its checkout's own config) is public and
commits as sami@thecybermonk.com; every theorem-labs repository commits as sami@theorem.dev. git
matches a clone by its remote URL (.gitconfig's includes), so a git clone with a remote in both
organizations commits as cybermonk. jj matches a checkout by its repository path (.jjconfig.toml's
scopes) and reads no remote. Each case makes one commit in a scratch repository under a HOME of its
own, whose ~/.dotfiles is this checkout, and reads the commit's author and committer.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.tests.test_dotfiles_sync import real_binary

DOTFILES = Path(__file__).resolve().parents[2]
REAL_GIT = real_binary("git")
REAL_JJ = real_binary("jj")

DEFAULT = "Sami Jawhar <sami@trajectorylabs.net>"
CYBERMONK = "Sami Jawhar <sami@thecybermonk.com>"
THEOREM = "Sami Jawhar <sami@theorem.dev>"
NOREPLY = "Sami Jawhar <sjawhar@users.noreply.github.com>"


def url_forms(repository: str) -> tuple[str, ...]:
    """Each form of a GitHub remote URL that a clone of REPOSITORY (owner/name) can have."""
    return tuple(f"{prefix}{repository}{suffix}"
                 for prefix in ("https://github.com/", "git@github.com:", "ssh://git@github.com/")
                 for suffix in ("", ".git"))


class Home(unittest.TestCase):
    """A HOME of its own, laid out as an installed machine has it: ~/.dotfiles is this checkout,
    ~/.gitconfig a link into it, and jj reads the per-machine file, then the dotfiles' file
    (JJ_CONFIG as .bashrc sets it)."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.home = self.tmp / "home"
        (self.home / ".config" / "jj").mkdir(parents=True)
        (self.home / ".dotfiles").symlink_to(DOTFILES)
        (self.home / ".gitconfig").symlink_to(self.home / ".dotfiles" / ".gitconfig")
        machine = self.home / ".config" / "jj" / "config.toml"
        machine.write_text('[user]\nname = "Sami Jawhar"\nemail = "sami@trajectorylabs.net"\n', encoding="utf-8")
        # No identity, config location or scratch directory of whoever runs the suite reaches a case.
        kept = {k: v for k, v in os.environ.items()
                if not k.startswith(("GIT_", "JJ_", "XDG_")) and k not in ("EMAIL", "TMPDIR")}
        self.env = {**kept, "HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / ".config"),
                    "GIT_CONFIG_NOSYSTEM": "1", "TMPDIR": str(self.tmp),
                    "JJ_CONFIG": f"{machine}{os.pathsep}{self.home / '.dotfiles' / '.jjconfig.toml'}"}

    def run_ok(self, *argv: str, cwd: Path) -> str:
        r = subprocess.run(argv, cwd=cwd, env=self.env, capture_output=True, text=True, timeout=120, check=False)
        self.assertEqual(r.returncode, 0, f"{argv}: {r.stdout}{r.stderr}")
        return r.stdout


class GitIdentityTest(Home):
    def git(self, *argv: str, cwd: Path) -> str:
        return self.run_ok(REAL_GIT, *argv, cwd=cwd)

    def commit_identity(self, *remotes: str, local_email: str | None = None) -> tuple[str, str]:
        """The author and committer of one commit in a new repository with REMOTES."""
        repo = Path(tempfile.mkdtemp(dir=self.tmp, prefix="repo-"))
        self.git("init", "-q", str(repo), cwd=self.tmp)
        for i, url in enumerate(remotes):
            self.git("remote", "add", f"r{i}", url, cwd=repo)
        if local_email:
            self.git("config", "--local", "user.email", local_email, cwd=repo)
        # The scratch HOME has no signing key, and signing is not what is under test.
        self.git("-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "x", cwd=repo)
        author, committer = self.git("log", "-1", "--format=%an <%ae>%n%cn <%ce>", cwd=repo).splitlines()
        return author, committer

    def test_a_cybermonklabs_remote_in_each_url_form_commits_as_cybermonk(self) -> None:
        for repository in ("cybermonklabs/legion", "cybermonklabs/oh-my-pi"):
            for url in url_forms(repository):
                with self.subTest(url=url):
                    self.assertEqual(self.commit_identity(url), (CYBERMONK, CYBERMONK))

    def test_legions_old_name_still_commits_as_cybermonk(self) -> None:
        for url in url_forms("sjawhar/legion"):
            with self.subTest(url=url):
                self.assertEqual(self.commit_identity(url), (CYBERMONK, CYBERMONK))

    def test_a_remote_in_both_organizations_commits_as_cybermonk(self) -> None:
        # Both orders of the remotes: the include that comes later in .gitconfig decides, not the remote.
        for remotes in (("https://github.com/theorem-labs/agent-infra", "https://github.com/cybermonklabs/legion.git"),
                        ("git@github.com:cybermonklabs/legion.git", "git@github.com:theorem-labs/agent-infra.git")):
            with self.subTest(remotes=remotes):
                self.assertEqual(self.commit_identity(*remotes), (CYBERMONK, CYBERMONK))

    def test_a_cybermonklabs_remote_in_another_case_or_with_a_user_part_commits_as_cybermonk(self) -> None:
        # Alone and beside a theorem-labs remote, where a missed shape would commit as sami@theorem.dev.
        for url in ("https://github.com/CybermonkLabs/legion", "https://github.com/CYBERMONKLABS/legion.git",
                    "git@github.com:Cybermonklabs/legion.git", "ssh://git@github.com/CyberMonkLabs/oh-my-pi",
                    "https://x-access-token@github.com/cybermonklabs/legion.git",
                    "https://user@github.com/cybermonklabs/legion", "https://user@github.com/CybermonkLabs/legion"):
            for remotes in ((url,), ("https://github.com/theorem-labs/agent-infra", url)):
                with self.subTest(remotes=remotes):
                    self.assertEqual(self.commit_identity(*remotes), (CYBERMONK, CYBERMONK))

    def test_a_lookalike_organization_beside_a_theorem_labs_remote_commits_as_theorem(self) -> None:
        for url in ("https://github.com/cybermonklabs-other/x", "https://github.com/CybermonkLabs-other/x",
                    "https://user@github.com/cybermonklabs-other/x", "git@github.com:cybermonklabs-other/x.git"):
            with self.subTest(url=url):
                self.assertEqual(self.commit_identity("https://github.com/theorem-labs/agent-infra", url), (THEOREM, THEOREM))

    def test_a_theorem_labs_remote_alone_commits_as_theorem(self) -> None:
        for url in url_forms("theorem-labs/agent-infra"):
            with self.subTest(url=url):
                self.assertEqual(self.commit_identity(url), (THEOREM, THEOREM))

    def test_any_other_remote_commits_with_the_default_identity(self) -> None:
        for remotes in ((), ("https://github.com/sjawhar/dotfiles",), ("https://github.com/cybermonklabs-other/x",),
                        ("https://github.com/can1357/oh-my-pi",)):
            with self.subTest(remotes=remotes):
                self.assertEqual(self.commit_identity(*remotes), (DEFAULT, DEFAULT))

    def test_a_repositorys_own_identity_wins_over_the_rule(self) -> None:
        # ~/src/oh-my-pi's local config sets the noreply address (`git-identity nr`).
        self.assertEqual(self.commit_identity("https://github.com/cybermonklabs/oh-my-pi",
                                              local_email="sjawhar@users.noreply.github.com"), (NOREPLY, NOREPLY))

    def test_without_the_cybermonklabs_rules_a_remote_in_both_organizations_commits_as_theorem(self) -> None:
        # The control of the case above: the theorem-labs rule matches the same repository, so the
        # cybermonklabs rules, and their place after it, are what make it commit as cybermonk.
        text = (DOTFILES / ".gitconfig").read_text(encoding="utf-8")
        stanza = re.compile(r'^\[includeIf "hasconfig:remote\.\*\.url:(?![^"]*sjawhar/legion)[^"]*"\]\n'
                            r'\tpath = ~/\.dotfiles/cybermonk-identity\.gitconfig\n', re.M)
        without, removed = stanza.subn("", text)
        self.assertEqual(removed, 4, "the four cybermonklabs rules were not found")
        (self.home / ".gitconfig").unlink()
        (self.home / ".gitconfig").write_text(without, encoding="utf-8")
        self.assertEqual(self.commit_identity("https://github.com/theorem-labs/agent-infra",
                                              "https://github.com/cybermonklabs/legion.git"), (THEOREM, THEOREM))


class JjIdentityTest(Home):
    """jj has no remote condition: .jjconfig.toml matches a checkout by its repository path, and a
    workspace has its main checkout's repository path."""

    def jj(self, *argv: str, cwd: Path) -> str:
        # The scratch HOME has no signing key and no watchman, and neither is under test.
        return self.run_ok(REAL_JJ, "--config=signing.behavior=drop", "--config=fsmonitor.backend=none", *argv, cwd=cwd)

    def init(self, path: str, *remotes: str) -> Path:
        repo = self.home / path
        repo.mkdir(parents=True)
        self.jj("git", "init", "--quiet", cwd=repo)
        for i, url in enumerate(remotes):
            self.jj("git", "remote", "add", f"r{i}", url, cwd=repo)
        return repo

    def commit_identity(self, workspace: Path) -> tuple[str, str]:
        """The author and committer of one new commit in WORKSPACE."""
        self.jj("new", "--quiet", "-m", "x", cwd=workspace)
        out = self.jj("log", "--no-graph", "-r", "@", "-T",
                      'author.name() ++ " <" ++ author.email() ++ ">\n" ++ committer.name() ++ " <" ++ committer.email() ++ ">\n"',
                      cwd=workspace)
        author, committer = out.splitlines()
        return author, committer

    def test_a_cybermonklabs_checkout_commits_as_cybermonk(self) -> None:
        for path in ("src/legion", "Code/legion"):
            with self.subTest(path=path):
                self.assertEqual(self.commit_identity(self.init(path, "https://github.com/cybermonklabs/legion.git")),
                                 (CYBERMONK, CYBERMONK))

    def test_a_workspace_of_the_checkout_commits_as_cybermonk_wherever_it_lives(self) -> None:
        repo = self.init("Code/legion", "https://github.com/cybermonklabs/legion")
        workspace = self.home / "src" / "legion-ws"
        workspace.parent.mkdir(parents=True)
        self.jj("workspace", "add", "--quiet", str(workspace), cwd=repo)
        self.assertEqual(self.commit_identity(workspace), (CYBERMONK, CYBERMONK))

    def test_a_theorem_labs_checkout_commits_as_theorem(self) -> None:
        repo = self.init("Code/personal/theorem/agent-infra", "https://github.com/theorem-labs/agent-infra")
        self.assertEqual(self.commit_identity(repo), (THEOREM, THEOREM))

    def test_oh_my_pi_commits_as_noreply(self) -> None:
        repo = self.init("src/oh-my-pi", "https://github.com/can1357/oh-my-pi", "https://github.com/cybermonklabs/oh-my-pi")
        self.assertEqual(self.commit_identity(repo), (NOREPLY, NOREPLY))

    def test_any_other_checkout_commits_with_the_default_identity(self) -> None:
        for path in ("src/other", "src/legion-other", "Code/legion-other"):
            with self.subTest(path=path):
                self.assertEqual(self.commit_identity(self.init(path, "https://github.com/sjawhar/pivot")), (DEFAULT, DEFAULT))


if __name__ == "__main__":
    unittest.main()
