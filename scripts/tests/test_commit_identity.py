#!/usr/bin/env python3
"""The commit identity the dotfiles choose for a repository.

Every cybermonklabs repository but oh-my-pi (noreply, by its checkout's own config) is public and
commits as sami@thecybermonk.com; every theorem-labs repository commits as sami@theorem.dev. git
matches a clone by its remote URL (.gitconfig's includes), so a git clone with a remote in both
organizations commits as cybermonk. jj takes the identity git resolves for the backing git repository,
through shims/jj. Each case makes one commit in a scratch repository under a HOME of its own, whose
~/.dotfiles is this checkout, and reads the commit's author and committer.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from leak_terms_fixture import leak_check_environment  # scripts/tests/conftest.py puts shims/tests on sys.path
from scripts.tests.test_dotfiles_sync import real_binary

DOTFILES = Path(__file__).resolve().parents[2]
REAL_GIT = real_binary("git")
REAL_JJ = real_binary("jj")

DEFAULT = "Sami Jawhar <sami@trajectorylabs.net>"
CYBERMONK = "Sami Jawhar <sami@thecybermonk.com>"
THEOREM = "Sami Jawhar <sami@theorem.dev>"
NOREPLY = "Sami Jawhar <sjawhar@users.noreply.github.com>"


def wait_for_child(pid: int, comm: str, limit: float = 600) -> bool:
    """True once process PID has a child named COMM; False when PID is gone first or LIMIT seconds pass (as
    shims/tests/test_gcloud_shim_routing.py waits for a shim's flock(1))."""
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        try:
            if Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()[0] == "Z":
                return False
            kids = Path(f"/proc/{pid}/task/{pid}/children").read_text(encoding="utf-8").split()
        except OSError:
            return False
        for kid in kids:
            try:
                if Path(f"/proc/{kid}/comm").read_text(encoding="utf-8").strip() == comm:
                    return True
            except OSError:
                continue
        time.sleep(0.05)
    return False


def url_forms(repository: str) -> tuple[str, ...]:
    """Each form of a GitHub remote URL that a clone of REPOSITORY (owner/name) can have."""
    return tuple(f"{prefix}{repository}{suffix}"
                 for prefix in ("https://github.com/", "git@github.com:", "ssh://git@github.com/")
                 for suffix in ("", ".git"))


class Home(unittest.TestCase):
    """A HOME of its own, laid out as an installed machine has it: ~/.dotfiles is this checkout, ~/.gitconfig and
    ~/.jjconfig.toml links into it (installers/jj.sh makes the second), and jj reads the per-machine file, then the
    dotfiles' file (JJ_CONFIG as .bashrc sets it)."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.home = self.tmp / "home"
        (self.home / ".config" / "jj").mkdir(parents=True)
        (self.home / ".dotfiles").symlink_to(DOTFILES)
        (self.home / ".gitconfig").symlink_to(self.home / ".dotfiles" / ".gitconfig")
        (self.home / ".jjconfig.toml").symlink_to(self.home / ".dotfiles" / ".jjconfig.toml")
        machine = self.home / ".config" / "jj" / "config.toml"
        machine.write_text('[user]\nname = "Sami Jawhar"\nemail = "sami@trajectorylabs.net"\n', encoding="utf-8")
        # No identity, config location or scratch directory of whoever runs the suite reaches a case.
        kept = {k: v for k, v in os.environ.items()
                if not k.startswith(("GIT_", "JJ_", "XDG_")) and k not in ("EMAIL", "TMPDIR")}
        self.env = {**kept, "HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / ".config"),
                    "GIT_CONFIG_NOSYSTEM": "1", "TMPDIR": str(self.tmp),
                    "JJ_CONFIG": f"{machine}{os.pathsep}{self.home / '.dotfiles' / '.jjconfig.toml'}"}

    def run_ok(self, *argv: str, cwd: Path, env: dict[str, str | None] | None = None) -> str:
        r = self.run_command(*argv, cwd=cwd, env=env)
        self.assertEqual(r.returncode, 0, f"{argv}: {r.stdout}{r.stderr}")
        return r.stdout

    def run_command(self, *argv: str, cwd: Path, env: dict[str, str | None] | None = None) -> subprocess.CompletedProcess[str]:
        """ARGV in this HOME's environment, with ENV's variables set over it (None removes one)."""
        merged = {**self.env, **(env or {})}
        return subprocess.run(argv, cwd=cwd, env={k: v for k, v in merged.items() if v is not None},
                              capture_output=True, text=True, timeout=300, check=False)


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
    """jj through the checkout's shims/jj, as every shell and session runs it: the shim asks git which identity
    the backing git repository resolves, so .gitconfig's remote-URL rules and a checkout's own git config decide
    jj's identity wherever the checkout lives, and a repository's or workspace's own jj identity, and JJ_EMAIL,
    still win over git's."""

    def setUp(self) -> None:
        super().setUp()
        path = os.pathsep.join([str(DOTFILES / "shims"), str(Path(REAL_JJ).parent), str(Path(REAL_GIT).parent),
                                "/usr/bin", "/bin"])
        # The shim scans each message for leak terms, so it reads the test term list.
        self.env.update(leak_check_environment(path))

    def jj(self, *argv: str, cwd: Path, signed: bool = False, env: dict[str, str | None] | None = None) -> str:
        # The scratch HOME has no signing key and no watchman, and only the signing case is about signing.
        return self.run_ok("jj", *self.jj_options(signed), *argv, cwd=cwd, env=env)

    @staticmethod
    def jj_options(signed: bool = False) -> tuple[str, ...]:
        return ("--config=fsmonitor.backend=none",) if signed else ("--config=signing.behavior=drop", "--config=fsmonitor.backend=none")

    def git(self, *argv: str, cwd: Path) -> str:
        return self.run_ok(REAL_GIT, *argv, cwd=cwd)

    def init(self, path: str, *remotes: str, colocate: bool = True) -> Path:
        """A jj repository at PATH under HOME with REMOTES, colocated as `jj git init --colocate` makes one, else with
        its git repository inside .jj."""
        repo = self.home / path
        repo.mkdir(parents=True)
        self.jj("git", "init", "--colocate" if colocate else "--no-colocate", "--quiet", cwd=repo)
        for i, url in enumerate(remotes):
            self.jj("git", "remote", "add", f"r{i}", url, cwd=repo)
        return repo

    def identity(self, workspace: Path, revision: str = "@") -> tuple[str, str]:
        out = self.jj("log", "--no-graph", "-r", revision, "-T",
                      'author.name() ++ " <" ++ author.email() ++ ">\n" ++ committer.name() ++ " <" ++ committer.email() ++ ">\n"',
                      cwd=workspace)
        author, committer = out.splitlines()
        return author, committer

    def commit_identity(self, workspace: Path) -> tuple[str, str]:
        """The author and committer of one new commit in WORKSPACE."""
        self.jj("new", "--quiet", "-m", "x", cwd=workspace)
        return self.identity(workspace)

    def signing_key(self) -> None:
        """The machine's signing key, at the path .gitconfig and .jjconfig.toml name."""
        key = self.home / ".ssh" / "jj-signing"
        key.parent.mkdir(mode=0o700)
        self.run_ok("/usr/bin/ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key), cwd=self.tmp)

    def signed(self, repo: Path, revision: str) -> bool:
        commit = self.jj("log", "--no-graph", "-r", revision, "-T", "commit_id", cwd=repo)
        return "\ngpgsig " in self.git("cat-file", "commit", commit, cwd=repo)

    def lock_recorder(self, repo: Path, then: str) -> tuple[Path, Path, Path]:
        """A program that appends to SEEN, each time it runs, whether REPO's re-author lock is held and whether the
        program has the lock open itself, leaves a process running (its pid appended to LEFT), and then runs THEN."""
        lock = (repo / ".jj" / "repo" / "shims-jj-reauthor.lock").resolve()
        seen, left, program = self.tmp / "seen", self.tmp / "left-running", self.tmp / "recorder"
        program.write_text(
            "#!/bin/sh\n"
            f"held=no; flock -n {lock} true || held=yes\n"
            "inherited=no\n"
            f'for f in /proc/$$/fd/*; do [ "$(readlink "$f")" = "{lock}" ] && inherited=yes; done\n'
            f'echo "held=$held inherited=$inherited" >> {seen}\n'
            f"sleep 300 < /dev/null > /dev/null 2>&1 &\necho $! >> {left}\n"
            f"{then}\n", encoding="utf-8")
        program.chmod(0o755)
        return program, seen, left

    @staticmethod
    def lock_free(repo: Path) -> bool:
        """Whether REPO's re-author lock comes free within 5 s (the helper sees jj gone within 0.1 s)."""
        deadline = time.monotonic() + 5
        with open(repo / ".jj" / "repo" / "shims-jj-reauthor.lock", "a", encoding="utf-8") as probe:
            while True:
                try:
                    fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return True
                except BlockingIOError:
                    if time.monotonic() > deadline:
                        return False
                    time.sleep(0.05)

    @staticmethod
    def stop_left(left: Path) -> None:
        for pid in (left.read_text(encoding="utf-8").split() if left.exists() else ()):
            os.kill(int(pid), signal.SIGTERM)

    def stale_working_copy(self, path: str) -> Path:
        """A colocated repository whose undescribed, non-empty @ was made with the default address, before its
        cybermonklabs remote existed."""
        repo = self.home / path
        repo.mkdir(parents=True)
        self.jj("git", "init", "--colocate", "--quiet", cwd=repo)
        (repo / "f").write_text("work\n", encoding="utf-8")
        self.jj("git", "remote", "add", "origin", "https://github.com/cybermonklabs/legion.git", cwd=repo)
        self.assertEqual(self.identity(repo)[0], DEFAULT)
        return repo

    def cybermonk_working_copy(self, path: str) -> Path:
        """A colocated cybermonklabs repository whose undescribed, non-empty @ is cybermonk's."""
        repo = self.init(path, "https://github.com/cybermonklabs/legion.git")
        self.jj("describe", "--quiet", "-m", "base", cwd=repo)
        self.jj("new", "--quiet", cwd=repo)
        (repo / "f").write_text("a session's work\n", encoding="utf-8")
        self.assertEqual(self.identity(repo), (CYBERMONK, CYBERMONK))
        return repo

    def divergent(self, repo: Path) -> str:
        return self.jj("log", "--no-graph", "-r", "divergent()", "-T", 'change_id.short() ++ " " ++ author.email() ++ "\n"', cwd=repo)

    def last_operation(self, repo: Path) -> str:
        return self.jj("--ignore-working-copy", "op", "log", "--no-graph", "-n", "1", "-T", "id.short()", cwd=repo)

    def test_a_cybermonklabs_checkout_commits_as_cybermonk_wherever_it_lives(self) -> None:
        for path in ("src/legion", "Code/legion", "src/legion-INFRA-4980", "elsewhere/legion"):
            with self.subTest(path=path):
                self.assertEqual(self.commit_identity(self.init(path, "https://github.com/cybermonklabs/legion.git")),
                                 (CYBERMONK, CYBERMONK))

    def test_a_cybermonklabs_checkout_whose_git_repository_is_inside_jj_commits_as_cybermonk(self) -> None:
        repo = self.init("elsewhere/legion", "https://github.com/cybermonklabs/legion.git", colocate=False)
        self.assertFalse((repo / ".git").exists())
        self.assertEqual(self.commit_identity(repo), (CYBERMONK, CYBERMONK))

    def test_a_fresh_git_clone_made_colocated_commits_as_cybermonk(self) -> None:
        # cybermonklabs/legion#4775's checkout: `git clone`, then `jj git init --colocate`. jj keeps the author of the
        # working-copy commit that init makes, and that commit is the one a describe then turns into the change.
        source = self.tmp / "source"
        self.git("init", "-q", str(source), cwd=self.tmp)
        self.git("-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "base", cwd=source)
        clone = self.home / "src" / "legion-INFRA-4980"
        self.git("clone", "-q", str(source), str(clone), cwd=self.tmp)
        self.git("remote", "set-url", "origin", "https://github.com/cybermonklabs/legion.git", cwd=clone)
        self.jj("git", "init", "--colocate", "--quiet", cwd=clone)
        self.jj("describe", "--quiet", "-m", "the change", cwd=clone)
        self.assertEqual(self.identity(clone), (CYBERMONK, CYBERMONK))
        self.assertEqual(self.commit_identity(clone), (CYBERMONK, CYBERMONK))

    def test_a_jj_git_clone_of_a_cybermonklabs_url_commits_as_cybermonk(self) -> None:
        # The working-copy commit `jj git clone` makes, too: the shim asks git about the URL before the clone exists.
        # git fetches the GitHub URL from a local repository instead, so the case needs no network.
        source = self.tmp / "source.git"
        self.git("init", "-q", "--bare", str(source), cwd=self.tmp)
        work = self.tmp / "work"
        self.git("clone", "-q", str(source), str(work), cwd=self.tmp)
        self.git("-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "base", cwd=work)
        self.git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=work)
        url = "https://github.com/cybermonklabs/legion.git"
        self.env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0=f"url.{source}.insteadOf", GIT_CONFIG_VALUE_0=url)
        clone = self.home / "elsewhere" / "legion"
        clone.parent.mkdir(parents=True)
        self.jj("git", "clone", "--colocate", "--quiet", url, str(clone), cwd=self.home)
        self.assertEqual(self.git("config", "--get", "remote.origin.url", cwd=clone).strip(), url)
        self.jj("describe", "--quiet", "-m", "the change", cwd=clone)
        self.assertEqual(self.identity(clone), (CYBERMONK, CYBERMONK))

    def test_jj_git_init_on_a_git_repository_elsewhere_takes_its_identity(self) -> None:
        # `jj git init --git-repo`: the remote of the bare repository it names decides, by an absolute or a relative path.
        for name, spelling in (("absolute", lambda bare: [f"--git-repo={bare}"]),
                               ("relative", lambda bare: ["--git-repo", f"../{bare.name}"])):
            with self.subTest(path=name):
                bare = self.home / "elsewhere" / f"{name}.git"
                self.git("init", "-q", "--bare", str(bare), cwd=self.tmp)
                self.git("--git-dir", str(bare), "remote", "add", "origin", "https://github.com/cybermonklabs/legion.git",
                         cwd=self.tmp)
                workspace = self.home / "elsewhere" / name
                workspace.mkdir()
                self.jj("git", "init", "--quiet", *spelling(bare), cwd=workspace)
                self.assertEqual(self.identity(workspace), (CYBERMONK, CYBERMONK))

    def test_an_inherited_dotfiles_git_email_is_not_taken(self) -> None:
        # A repository whose git names no address of its own: only the default may decide.
        self.env["DOTFILES_GIT_EMAIL"] = "sami@theorem.dev"
        repo = self.home / "elsewhere" / "fresh"
        repo.mkdir(parents=True)
        self.jj("git", "init", "--colocate", "--quiet", cwd=repo)
        self.assertEqual(self.identity(repo), (DEFAULT, DEFAULT))

    def test_a_remote_added_after_init_gives_the_undescribed_working_copy_commit_its_identity(self) -> None:
        # `jj git init`, some work, then the remote: init made the working-copy commit with the default author, and
        # signing.behavior = "own" leaves a commit unsigned whose author is not jj's address.
        self.signing_key()
        for command, change in (("describe", "@"), ("commit", "@-"), ("new", "@-")):
            with self.subTest(command=command):
                repo = self.home / "elsewhere" / f"legion-{command}"
                repo.mkdir(parents=True)
                self.jj("git", "init", "--colocate", "--quiet", cwd=repo)
                (repo / "f").write_text("work\n", encoding="utf-8")
                self.jj("git", "remote", "add", "origin", "https://github.com/cybermonklabs/legion.git", cwd=repo)
                self.jj(command, "--quiet", "-m", "the change", cwd=repo, signed=True)
                self.assertEqual(self.identity(repo, change), (CYBERMONK, CYBERMONK))
                self.assertTrue(self.signed(repo, change), "the change is not signed")

    def test_a_remote_url_git_changed_gives_the_undescribed_working_copy_commit_its_identity(self) -> None:
        # A colocated repository whose remote git changed, which never passes the jj shim.
        self.signing_key()
        source = self.tmp / "source"
        self.git("init", "-q", str(source), cwd=self.tmp)
        self.git("-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "base", cwd=source)
        clone = self.home / "src" / "legion-x"
        self.git("clone", "-q", str(source), str(clone), cwd=self.tmp)
        self.jj("git", "init", "--colocate", "--quiet", cwd=clone)
        self.git("remote", "set-url", "origin", "https://github.com/cybermonklabs/legion.git", cwd=clone)
        (clone / "f").write_text("work\n", encoding="utf-8")
        self.jj("commit", "--quiet", "-m", "the change", cwd=clone, signed=True)
        self.assertEqual(self.identity(clone, "@-"), (CYBERMONK, CYBERMONK))
        self.assertTrue(self.signed(clone, "@-"), "the change is not signed")

    def test_a_described_working_copy_commit_keeps_its_author(self) -> None:
        # Only a commit nobody has described yet takes the new address; a described one is someone's finished work.
        repo = self.home / "elsewhere" / "legion"
        repo.mkdir(parents=True)
        self.jj("git", "init", "--colocate", "--quiet", cwd=repo)
        self.jj("describe", "--quiet", "-m", "work in progress", cwd=repo)
        self.jj("git", "remote", "add", "origin", "https://github.com/cybermonklabs/legion.git", cwd=repo)
        self.jj("describe", "--quiet", "-m", "work in progress, renamed", cwd=repo)
        self.assertEqual(self.identity(repo), (DEFAULT, CYBERMONK))

    def test_a_working_copy_commit_with_a_bookmark_keeps_its_author(self) -> None:
        repo = self.stale_working_copy("elsewhere/legion")
        self.jj("bookmark", "create", "b", "-r", "@", cwd=repo)
        self.jj("describe", "--quiet", "-m", "the change", cwd=repo)
        self.assertEqual(self.identity(repo), (DEFAULT, CYBERMONK))

    def test_an_author_someone_set_is_kept(self) -> None:
        # `jj metaedit --author` credits a co-author on purpose; only an address jj itself set is replaced.
        pat = "Pat Pair <pat@example.org>"
        for command, change in (("describe", "@"), ("commit", "@-"), ("new", "@-")):
            with self.subTest(command=command):
                repo = self.stale_working_copy(f"elsewhere/pair-{command}")
                self.jj("metaedit", "--quiet", "--author", pat, cwd=repo)
                self.jj(command, "--quiet", "-m", "Pat's fix", cwd=repo)
                self.assertEqual(self.identity(repo, change)[0], pat)

    def test_a_describe_the_leak_check_refuses_changes_nothing(self) -> None:
        # The shim says "nothing changed"; a re-author before the check would already have made an operation.
        repo = self.stale_working_copy("elsewhere/legion")
        before = self.last_operation(repo)
        refused = self.run_command("jj", *self.jj_options(), "describe", "-m", "mentions zorbleplex", cwd=repo)
        self.assertNotEqual(refused.returncode, 0, refused.stdout)
        self.assertIn("nothing changed", refused.stderr)
        self.assertEqual(self.last_operation(repo), before)
        self.assertEqual(self.identity(repo)[0], DEFAULT)

    def test_an_editor_a_late_config_file_names_still_runs_through_the_leak_term_check(self) -> None:
        # jj applies --config and --config-file in command-line order, so a --config-file after the subcommand must
        # not come after the wrapper's ui.editor and run its own editor unchecked. No --config on the command line.
        editor = self.tmp / "leaking-editor"
        editor.write_text("#!/bin/sh\nprintf 'mentions zorbleplex\\n' > \"$1\"\n", encoding="utf-8")
        editor.chmod(0o755)
        late = self.tmp / "late.toml"
        late.write_text(f'signing.behavior = "drop"\nfsmonitor.backend = "none"\nui.editor = "{editor}"\n', encoding="utf-8")
        for command in ("describe", "commit"):
            with self.subTest(command=command):
                repo = self.init(f"elsewhere/late-{command}")
                (repo / "f").write_text("work\n", encoding="utf-8")
                refused = self.run_command("jj", command, "--config-file", str(late), cwd=repo)
                self.assertNotEqual(refused.returncode, 0, refused.stdout + refused.stderr)
                self.assertIn("zorbleplex", refused.stderr)
                self.assertEqual(self.jj("log", "--no-graph", "-r", "::@ & description(glob:'*zorbleplex*')", "-T", "commit_id",
                                         cwd=repo), "")

    def early_config_file_editor_refused(self, command: str) -> None:
        """COMMAND, given a --config-file before the subcommand whose editor writes a leak term, and a --config after
        it, is refused and saves no description. jj counts the place of a --config after the subcommand from the
        subcommand, and of a --config-file before it from the start of the line, so that file could come after the
        wrapper's ui.editor and run its own editor unchecked."""
        editor = self.tmp / "leaking-editor"
        editor.write_text("#!/bin/sh\nprintf 'mentions zorbleplex\\n' > \"$1\"\n", encoding="utf-8")
        editor.chmod(0o755)
        early = self.tmp / "early.toml"
        early.write_text(f'signing.behavior = "drop"\nfsmonitor.backend = "none"\nui.editor = "{editor}"\n', encoding="utf-8")
        repo = self.init(f"elsewhere/early-{command}")
        (repo / "f").write_text("work\n", encoding="utf-8")
        refused = self.run_command("jj", "--config=ui.color=never", "--config-file", str(early), command,
                                   "--config", "user.name=Sami Jawhar", cwd=repo)
        self.assertNotEqual(refused.returncode, 0, refused.stdout + refused.stderr)
        self.assertIn("zorbleplex", refused.stderr)
        self.assertEqual(self.jj("log", "--no-graph", "-r", "::@ & description(glob:'*zorbleplex*')", "-T", "commit_id",
                                 cwd=repo), "")

    def test_a_describe_whose_editor_an_early_config_file_names_runs_it_through_the_leak_term_check(self) -> None:
        self.early_config_file_editor_refused("describe")

    def test_a_commit_whose_editor_an_early_config_file_names_runs_it_through_the_leak_term_check(self) -> None:
        self.early_config_file_editor_refused("commit")

    def test_a_describe_through_the_editor_gives_the_working_copy_commit_its_identity(self) -> None:
        # The shim runs the editor through the leak-term wrapper, after the re-author. The wrapper's ui.editor must
        # not drop the caller's own --config (here signing.behavior=drop, without which jj signs with no key and fails).
        editor = self.tmp / "editor"
        editor.write_text("#!/bin/sh\nprintf 'from the editor\\n' > \"$1\"\n", encoding="utf-8")
        editor.chmod(0o755)
        repo = self.stale_working_copy("elsewhere/legion")
        self.jj(f"--config=ui.editor={editor}", "describe", "--quiet", cwd=repo)
        self.assertEqual(self.jj("log", "--no-graph", "-r", "@", "-T", "description", cwd=repo), "from the editor\n")
        self.assertEqual(self.identity(repo), (CYBERMONK, CYBERMONK))

    def test_a_command_that_leaves_the_working_copy_commit_leaves_its_author(self) -> None:
        # Describing another commit, or a new one that does not move @ there (--no-edit), works on something else.
        for name, argv in (("describe -r @-", ["describe", "--quiet", "-r", "@-", "-m", "base, reworded"]),
                           ("new --no-edit root()", ["new", "--quiet", "--no-edit", "-m", "sibling", "root()"]),
                           ("new --no-edit", ["new", "--quiet", "--no-edit", "-m", "child"])):
            with self.subTest(command=name):
                repo = self.home / "elsewhere" / name.replace(" ", "_")
                repo.mkdir(parents=True)
                self.jj("git", "init", "--colocate", "--quiet", cwd=repo)
                self.jj("describe", "--quiet", "-m", "base", cwd=repo)
                self.jj("new", "--quiet", cwd=repo)
                (repo / "f").write_text("work\n", encoding="utf-8")
                self.jj("git", "remote", "add", "origin", "https://github.com/cybermonklabs/legion.git", cwd=repo)
                self.jj(*argv, cwd=repo)
                self.assertEqual(self.identity(repo)[0], DEFAULT)

    def test_naming_the_working_copy_commit_by_its_id_leaves_no_divergent_change(self) -> None:
        # `jj agent-log` prints commit ids; a re-author in an operation of its own would leave the named commit
        # beside its rewrite.
        for command in ("describe", "new"):
            with self.subTest(command=command):
                repo = self.stale_working_copy(f"elsewhere/by-id-{command}")
                commit = self.jj("log", "--no-graph", "-r", "@", "-T", "commit_id", cwd=repo)
                if command == "describe":
                    self.jj("describe", "--quiet", "-r", commit, "-m", "named by commit id", cwd=repo)
                else:
                    self.jj("new", "--quiet", commit, cwd=repo)
                self.assertEqual(self.divergent(repo), "")

    def test_the_lock_is_held_while_jj_runs_and_reaches_nothing_jj_starts(self) -> None:
        # Commands queued behind a re-author run one at a time: a helper holds the lock until jj exits. jj and what it
        # starts get no copy of it, so a process jj leaves running (a watchman server, say) cannot keep it. jj starts
        # the signing program for the re-author's metaedit and again for the describe; it records, each time, whether
        # the lock is held and whether it has the lock open itself, and leaves a process running past jj.
        self.signing_key()
        repo = self.stale_working_copy("elsewhere/legion")
        program, seen, left = self.lock_recorder(repo, 'exec /usr/bin/ssh-keygen "$@"')
        try:
            self.jj(f"--config=signing.backends.ssh.program={program}", "describe", "--quiet", "-m", "the change", cwd=repo,
                    signed=True)
            free = self.lock_free(repo)
        finally:
            self.stop_left(left)
        self.assertEqual(seen.read_text(encoding="utf-8").splitlines(), ["held=yes inherited=no"] * 2)
        self.assertTrue(free, "a process jj started holds the lock after jj exited")
        self.assertEqual(self.identity(repo), (CYBERMONK, CYBERMONK))
        self.assertTrue(self.signed(repo, "@"), "the change is not signed")

    def test_a_describe_from_stdin_runs_jj_with_the_lock_held_and_no_copy_of_it(self) -> None:
        # `describe --stdin` runs jj in a subshell the shim waits for, holding the lock itself until jj exits.
        self.signing_key()
        repo = self.stale_working_copy("elsewhere/legion")
        program, seen, left = self.lock_recorder(repo, 'exec /usr/bin/ssh-keygen "$@"')
        try:
            done = subprocess.run(["jj", *self.jj_options(signed=True), f"--config=signing.backends.ssh.program={program}",
                                   "describe", "--quiet", "--stdin"], cwd=repo, env=self.env, input="the change\n",
                                  capture_output=True, text=True, timeout=300, check=False)
            free = self.lock_free(repo)
        finally:
            self.stop_left(left)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(seen.read_text(encoding="utf-8").splitlines(), ["held=yes inherited=no"] * 2)
        self.assertTrue(free, "a process jj started holds the lock after jj exited")
        self.assertEqual(self.jj("log", "--no-graph", "-r", "@", "-T", "description", cwd=repo), "the change\n")
        self.assertEqual(self.identity(repo), (CYBERMONK, CYBERMONK))

    def test_a_commit_with_a_diff_tool_runs_it_with_the_lock_released(self) -> None:
        # A diff editor, like an editor, stays open as long as a person likes; jj runs the tool with $left $right, and
        # this one keeps every change.
        repo = self.stale_working_copy("elsewhere/legion")
        program, seen, left = self.lock_recorder(repo, "exit 0")
        try:
            self.jj("commit", "--quiet", "--tool", str(program), "-m", "picked", cwd=repo)
        finally:
            self.stop_left(left)
        self.assertEqual(seen.read_text(encoding="utf-8").splitlines(), ["held=no inherited=no"])
        self.assertEqual(self.identity(repo, "@-"), (CYBERMONK, CYBERMONK))

    def path_recorders(self, repo: Path, seen: Path) -> Path:
        """A directory with a jj and a git that append to SEEN, each time they run, their subcommand, whether REPO's
        re-author lock is held and whether they have it open themselves, and then run the real one."""
        lock = (repo / ".jj" / "repo" / "shims-jj-reauthor.lock").resolve()
        recorders = self.tmp / f"recorders-{seen.name}"
        recorders.mkdir()
        for name, real in (("jj", REAL_JJ), ("git", REAL_GIT)):
            program = recorders / name
            program.write_text(
                "#!/bin/sh\n"
                f"held=no; flock -n {lock} true 2> /dev/null || held=yes\n"
                "inherited=no\n"
                f'for f in /proc/$$/fd/*; do [ "$(readlink "$f")" = "{lock}" ] && inherited=yes; done\n'
                "sub=; skip=\n"
                'for a in "$@"; do\n'
                '    if [ -n "$skip" ]; then skip=; continue; fi\n'
                '    case "$a" in\n'
                "        --config | --config-file | -R | --repository | --at-op | --at-operation | --color | -C | -c) skip=1 ;;\n"
                "        -*) ;;\n"
                '        *) sub="$a"; break ;;\n'
                "    esac\n"
                "done\n"
                f'echo "{name} $sub held=$held inherited=$inherited" >> {seen}\n'
                f'exec {real} "$@"\n', encoding="utf-8")
            program.chmod(0o755)
        return recorders

    def test_nothing_the_shim_starts_while_it_holds_the_lock_gets_a_copy_of_it(self) -> None:
        # The lock is a bash file descriptor, and a child inherits each one its parent has open, so each process the
        # shim starts while it holds the lock must close it: the second look at @, the re-author's metaedit, the trailer
        # check (which runs only where JJ_CONFIG names an omp session's omp-attribution-*.toml overlay), and jj itself,
        # run directly or fed a description from stdin. A jj and a git ahead of the real ones on PATH record each run.
        session = "01a0fa64-b3a5-7415-b143-c2a338b5da8b"
        overlay = self.tmp / f"omp-attribution-{session}.toml"
        overlay.write_text(f"[templates]\ncommit_trailers = '\"Omp-Session: {session}\"'\n", encoding="utf-8")
        for name, argv, stdin in (("message", ("-m", "the change"), None), ("stdin", ("--stdin",), "the change\n")):
            with self.subTest(describe=name):
                repo = self.stale_working_copy(f"elsewhere/legion-{name}")
                seen = self.tmp / f"seen-{name}"
                path = os.pathsep.join([str(DOTFILES / "shims"), str(self.path_recorders(repo, seen)), self.env["PATH"]])
                done = subprocess.run(["jj", *self.jj_options(), "describe", "--quiet", *argv], cwd=repo, input=stdin,
                                      env={**self.env, "PATH": path, "JJ_CONFIG": f"{self.env['JJ_CONFIG']}{os.pathsep}{overlay}"},
                                      capture_output=True, text=True, timeout=300, check=False)
                self.assertEqual(done.returncode, 0, done.stderr)
                runs = seen.read_text(encoding="utf-8").splitlines()
                held = {" ".join(run.split()[:2]) for run in runs if " held=yes " in run}
                self.assertLessEqual({"jj log", "jj metaedit", "jj describe", "git config"}, held, runs)
                self.assertEqual([run for run in runs if run.endswith(" inherited=yes")], [], runs)
                self.assertEqual(self.identity(repo), (CYBERMONK, CYBERMONK))

    def test_a_re_author_in_a_pid_namespace_that_sees_the_hosts_proc_returns(self) -> None:
        # Under the host's /proc, /proc/$$ is another process, which never becomes a zombie: a lock helper watching it
        # would never stop, so the re-author lock would stay held after jj returned. util-linux unshare makes the pid
        # namespace and leaves /proc the host's; a shell is the namespace's init, so jj's shim runs as pid 2 and
        # /proc/$$ is a host kernel thread. The shell reads the lock while it is still alive: once the namespace's init
        # exits, the kernel ends every process left in it, which would hide a helper that never stops.
        unshare, flock = shutil.which("unshare"), shutil.which("flock")
        if unshare is None or flock is None:
            self.skipTest("no unshare or flock, so no pid namespace to run jj in")
        namespace = [unshare, "--user", "--map-current-user", "--pid", "--fork"]
        probe = subprocess.run([*namespace, "true"], capture_output=True, text=True, timeout=60, check=False)
        if probe.returncode:
            self.skipTest(f"unshare cannot make a pid namespace here (no user namespaces?): {probe.stderr.strip()}")
        repo = self.stale_working_copy("elsewhere/legion")
        init = ('jj "$@"; status=$?; "$FLOCK" -n "$LOCK" true || { echo "the re-author lock is still held" >&2; exit 97; }; '
                'exit "$status"')
        env = {**self.env, "FLOCK": flock, "LOCK": str(repo / ".jj" / "repo" / "shims-jj-reauthor.lock")}
        sandbox = subprocess.Popen([*namespace, "sh", "-c", init, "sh", *self.jj_options(), "describe", "--quiet", "-m",
                                    "the change"], cwd=repo, env=env, text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        try:
            _, stderr = sandbox.communicate(timeout=120)
        except subprocess.TimeoutExpired:
            os.killpg(sandbox.pid, signal.SIGKILL)
            sandbox.communicate()
            self.fail("jj in a pid namespace did not return within 120 s")
        self.assertEqual(sandbox.returncode, 0, stderr)
        self.assertEqual(self.identity(repo), (CYBERMONK, CYBERMONK))

    def test_an_editor_describe_keeps_a_config_given_before_a_late_config_file(self) -> None:
        # The wrapper's --config goes after the late --config-file, and the --config given before the subcommand moves
        # to just after it; there it (signing.behavior=drop, without which jj signs with no key and fails) must still apply.
        editor = self.tmp / "editor"
        editor.write_text("#!/bin/sh\nprintf 'from the editor\\n' > \"$1\"\n", encoding="utf-8")
        editor.chmod(0o755)
        late = self.tmp / "late.toml"
        late.write_text(f'ui.editor = "{editor}"\n', encoding="utf-8")
        repo = self.init("elsewhere/early")
        done = self.run_command("jj", *self.jj_options(), "describe", "--config-file", str(late), cwd=repo)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.jj("log", "--no-graph", "-r", "@", "-T", "description", cwd=repo), "from the editor\n")

    def test_the_shim_jj_config_and_git_identity_name_the_same_addresses(self) -> None:
        # The scopes turn git's address into jj's, git-identity sets one of them, and the shim re-authors only from
        # one of them: an address missing from any list would leave @ with an author nothing replaces.
        scopes = set(re.findall(r'^--when\.environments = \["DOTFILES_GIT_EMAIL=([^"]+)"\]$',
                                (DOTFILES / ".jjconfig.toml").read_text(encoding="utf-8"), re.M))
        known = set(re.findall(r'^[A-Z]+_EMAIL="([^"]+)"$', (DOTFILES / "scripts" / "git-identity").read_text(encoding="utf-8"),
                               re.M))
        cases = re.findall(r"^\s+((?:[\w.+-]+@[\w.-]+)(?: \| [\w.+-]+@[\w.-]+)*)\) return 0 ;;$",
                           (DOTFILES / "shims" / "jj").read_text(encoding="utf-8"), re.M)
        self.assertEqual(len(cases), 1, cases)
        self.assertEqual(len(scopes), 4, scopes)
        self.assertEqual(scopes, known)
        self.assertEqual(set(cases[0].split(" | ")), scopes)

    def test_a_working_copy_commit_made_with_another_known_address_takes_gits(self) -> None:
        # The noreply address, say, from a repository that commits as cybermonk now.
        noreply = {"JJ_USER": "Sami Jawhar", "JJ_EMAIL": "sjawhar@users.noreply.github.com"}
        repo = self.home / "elsewhere" / "legion"
        repo.mkdir(parents=True)
        self.jj("git", "init", "--colocate", "--quiet", cwd=repo, env=noreply)
        (repo / "f").write_text("work\n", encoding="utf-8")
        self.jj("git", "remote", "add", "origin", "https://github.com/cybermonklabs/legion.git", cwd=repo, env=noreply)
        self.assertEqual(self.identity(repo)[0], NOREPLY)
        self.jj("describe", "--quiet", "-m", "the change", cwd=repo)
        self.assertEqual(self.identity(repo), (CYBERMONK, CYBERMONK))

    def test_the_lock_is_free_once_jj_exits_though_no_one_has_reaped_it(self) -> None:
        # A caller that waits on several jj runs in turn reaps each one late; until then the exited jj is a zombie,
        # and the lock must not wait for the reaping.
        repo = self.stale_working_copy("elsewhere/legion")
        lock = repo / ".jj" / "repo" / "shims-jj-reauthor.lock"
        describe = subprocess.Popen(["jj", *self.jj_options(), "describe", "--quiet", "-m", "the change"], cwd=repo,
                                    env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            # Read from /proc, since Popen.poll() would reap it.
            deadline = time.monotonic() + 600
            while Path(f"/proc/{describe.pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()[0] != "Z":
                self.assertLess(time.monotonic(), deadline, "jj never exited")
                time.sleep(0.05)
            free, deadline = False, time.monotonic() + 5
            with open(lock, "a", encoding="utf-8") as probe:
                while not free and time.monotonic() < deadline:
                    try:
                        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        free = True
                    except BlockingIOError:
                        time.sleep(0.05)
        finally:
            describe.wait(timeout=300)
        self.assertEqual(describe.returncode, 0)
        self.assertTrue(free, "the lock waits for the caller to reap jj")
        self.assertEqual(self.identity(repo), (CYBERMONK, CYBERMONK))

    def test_an_editor_runs_with_the_lock_released(self) -> None:
        # An editor stays open as long as a person likes, and the lock serves the whole repository.
        repo = self.stale_working_copy("elsewhere/legion")
        lock = repo / ".jj" / "repo" / "shims-jj-reauthor.lock"
        started, go = self.tmp / "started", self.tmp / "go"
        editor = self.tmp / "waiting-editor"
        editor.write_text(f"#!/bin/sh\ntouch {started}\nwhile [ ! -e {go} ]; do sleep 0.1; done\n"
                          "printf 'from the editor\\n' > \"$1\"\n", encoding="utf-8")
        editor.chmod(0o755)
        describe = subprocess.Popen(["jj", *self.jj_options(), f"--config=ui.editor={editor}", "describe", "--quiet"],
                                    cwd=repo, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 600
            while not started.exists() and describe.poll() is None:
                self.assertLess(time.monotonic(), deadline, "the editor never started")
                time.sleep(0.05)
            self.assertTrue(started.exists(), "the describe ended before its editor started")
            with open(lock, "a", encoding="utf-8") as probe:
                try:
                    fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    free = True
                    fcntl.flock(probe, fcntl.LOCK_UN)
                except BlockingIOError:
                    free = False
        finally:
            go.touch()
            _, err = describe.communicate(timeout=300)
        self.assertEqual(describe.returncode, 0, err)
        self.assertTrue(free, "the lock is held while the editor is open")
        self.assertEqual(self.identity(repo), (CYBERMONK, CYBERMONK))

    def test_a_jj_run_inside_the_editor_while_the_lock_is_held_finishes(self) -> None:
        # Only a command on a stale @ waits for the lock, and the command holding it has just made @ current.
        repo = self.stale_working_copy("elsewhere/legion")
        nested = self.tmp / "nested.err"
        options = " ".join(self.jj_options())
        editor = self.tmp / "nesting-editor"
        editor.write_text(f"#!/bin/sh\njj {options} --ignore-working-copy log -r @ -T description > /dev/null 2>> {nested} "
                          f"&& jj {options} --ignore-working-copy new --quiet --no-edit -m nested 'root()' 2>> {nested} "
                          f"&& printf 'from the editor\\n' > \"$1\"\n", encoding="utf-8")
        editor.chmod(0o755)
        self.jj(f"--config=ui.editor={editor}", "describe", "--quiet", cwd=repo)
        self.assertNotIn("waited", nested.read_text(encoding="utf-8"))
        self.assertEqual(self.jj("log", "--no-graph", "-r", "@", "-T", "description", cwd=repo), "from the editor\n")
        self.assertEqual(self.identity(repo), (CYBERMONK, CYBERMONK))

    def test_a_rewrite_waits_for_the_repositorys_lock_and_looks_again(self) -> None:
        # Another session rewrites @ under the lock while this one waits for it; this one then finds @ done and
        # rewrites nothing, so the change has one rewrite and no divergent copy.
        repo = self.stale_working_copy("elsewhere/legion")
        with open(repo / ".jj" / "repo" / "shims-jj-reauthor.lock", "a", encoding="utf-8") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            new = subprocess.Popen(["jj", *self.jj_options(), "new", "--quiet"], cwd=repo, env=self.env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            waited = wait_for_child(new.pid, "flock")
            self.jj("metaedit", "--quiet", "--update-author", cwd=repo)
        _, err = new.communicate(timeout=300)
        self.assertTrue(waited, "the shim did not wait for the repository's lock")
        self.assertEqual(new.returncode, 0, err)
        rewrites = self.jj("op", "log", "--no-graph", "-T", 'description.first_line() ++ "\n"', cwd=repo)
        self.assertEqual(rewrites.count("edit commit metadata"), 1, rewrites)
        self.assertEqual(self.divergent(repo), "")
        self.assertEqual(self.identity(repo, "@-")[0], CYBERMONK)

    def test_parallel_news_on_a_stale_working_copy_commit_leave_no_divergent_change(self) -> None:
        # Six sessions at once: one re-authors @ under the repository's lock, and the rest find it done.
        for trial in range(5):
            with self.subTest(trial=trial):
                repo = self.stale_working_copy(f"elsewhere/parallel-{trial}")
                news = [subprocess.Popen(["jj", *self.jj_options(), "new", "--quiet"], cwd=repo, env=self.env,
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(6)]
                for new in news:
                    new.communicate(timeout=300)
                self.assertEqual(self.divergent(repo), "")
                self.assertEqual(self.identity(repo, "root()+")[0], CYBERMONK)

    def test_a_bots_working_copy_commit_is_kept_when_a_person_describes_its_parent(self) -> None:
        bot = {"JJ_USER": "sjawhar-agent[bot]", "JJ_EMAIL": "329211056+sjawhar-agent[bot]@users.noreply.github.com"}
        repo = self.home / "legion-ws" / "worker"
        repo.mkdir(parents=True)
        self.jj("git", "init", "--colocate", "--quiet", cwd=repo, env=bot)
        self.jj("git", "remote", "add", "origin", "https://github.com/cybermonklabs/legion.git", cwd=repo, env=bot)
        self.jj("describe", "--quiet", "-m", "worker base", cwd=repo, env=bot)
        self.jj("new", "--quiet", cwd=repo, env=bot)
        (repo / "w").write_text("the worker's unfinished work\n", encoding="utf-8")
        self.jj("describe", "--quiet", "-r", "@-", "-m", "worker base, reworded by a person", cwd=repo)
        self.assertEqual(self.identity(repo)[0], "sjawhar-agent[bot] <329211056+sjawhar-agent[bot]@users.noreply.github.com>")

    def test_a_run_whose_jj_address_is_not_gits_leaves_the_author(self) -> None:
        # With no JJ_CONFIG, jj reads the shared file first and the per-machine file after it, whose address wins
        # (.jjconfig.toml); with an empty one it has no address at all. Neither is git's, so @ keeps cybermonk.
        for name, value in (("unset", None), ("empty", "")):
            with self.subTest(jj_config=name):
                repo = self.cybermonk_working_copy(f"src/legion-{name}")
                self.jj("describe", "--quiet", "-m", "the change", cwd=repo, env={"JJ_CONFIG": value})
                self.assertEqual(self.identity(repo)[0], CYBERMONK)

    def test_a_cybermonklabs_checkout_commits_as_cybermonk_under_omps_jj_config(self) -> None:
        # An omp session that inherits no JJ_CONFIG chains the user config files jj names
        # (omp/extensions/session-env.ts). Its session start runs here, under this HOME, and the commit uses the
        # chain it exports.
        env = {k: v for k, v in self.env.items()
               if k not in ("JJ_CONFIG", "OMP_SESSION_ID", "PI_COMMIT_TRAILERS", "GH_CONFIG_DIR", "CLOUDSDK_CONFIG",
                            "GOOGLE_APPLICATION_CREDENTIALS", "AGENT_SECRETS_HELPER_SOCK", "AGENTBOX_BOX")}
        env["XDG_RUNTIME_DIR"] = str(self.tmp)
        driver = DOTFILES / "omp" / "extensions" / "__tests__" / "session-env-driver.ts"
        start = {"kind": "main", "file": str(self.home / "notes" / "handmade.jsonl")}
        r = subprocess.run([real_binary("bun"), str(driver), json.dumps(start)], cwd=self.tmp, env=env,
                           capture_output=True, text=True, timeout=120, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        chain = json.loads(r.stdout)["jjConfig"]
        # The chain jj named, not a fallback of omp's own.
        self.assertIn(str(self.home / ".jjconfig.toml"), chain.split(":"))
        self.env["JJ_CONFIG"] = chain
        repo = self.init("elsewhere/legion", "https://github.com/cybermonklabs/legion.git")
        self.assertEqual(self.commit_identity(repo), (CYBERMONK, CYBERMONK))

    def test_a_workspace_of_a_cybermonklabs_checkout_commits_as_cybermonk_wherever_it_lives(self) -> None:
        repo = self.init("elsewhere/legion", "https://github.com/cybermonklabs/legion")
        workspace = self.home / "src" / "legion-ws"
        workspace.parent.mkdir(parents=True)
        self.jj("workspace", "add", "--quiet", str(workspace), cwd=repo)
        self.assertEqual(self.identity(workspace), (CYBERMONK, CYBERMONK))
        self.assertEqual(self.commit_identity(workspace), (CYBERMONK, CYBERMONK))

    def test_a_theorem_labs_checkout_commits_as_theorem_wherever_it_lives(self) -> None:
        for path in ("Code/personal/theorem/agent-infra", "src/agent-infra"):
            with self.subTest(path=path):
                repo = self.init(path, "https://github.com/theorem-labs/agent-infra")
                self.assertEqual(self.commit_identity(repo), (THEOREM, THEOREM))

    def test_a_checkouts_own_git_identity_decides(self) -> None:
        # ~/src/oh-my-pi's local git config holds the noreply address, which wins over its cybermonklabs remote.
        for path in ("src/oh-my-pi", "elsewhere/oh-my-pi"):
            with self.subTest(path=path):
                repo = self.init(path, "https://github.com/can1357/oh-my-pi", "https://github.com/cybermonklabs/oh-my-pi")
                self.git("config", "--local", "user.email", "sjawhar@users.noreply.github.com", cwd=repo)
                self.assertEqual(self.commit_identity(repo), (NOREPLY, NOREPLY))

    def test_a_repositorys_or_workspaces_own_jj_identity_wins_over_gits(self) -> None:
        # `git-identity nr` in one knives workspace sets jj's identity for that workspace alone, and leaves git's.
        for scope in ("--workspace", "--repo"):
            with self.subTest(scope=scope):
                repo = self.init(f"elsewhere/legion{scope}", "https://github.com/cybermonklabs/legion")
                self.jj("config", "set", scope, "user.email", "sjawhar@users.noreply.github.com", cwd=repo)
                self.assertEqual(self.commit_identity(repo), (NOREPLY, NOREPLY))

    def test_jj_user_and_jj_email_win(self) -> None:
        # The Legion daemon commits as its role's GitHub App this way.
        repo = self.init("elsewhere/legion", "https://github.com/cybermonklabs/legion")
        bot = "sjawhar-agent[bot] <329211056+sjawhar-agent[bot]@users.noreply.github.com>"
        self.env.update(JJ_USER="sjawhar-agent[bot]", JJ_EMAIL="329211056+sjawhar-agent[bot]@users.noreply.github.com")
        self.assertEqual(self.commit_identity(repo), (bot, bot))

    def test_any_other_checkout_commits_with_the_default_identity(self) -> None:
        for path, remotes in (("src/other", ()), ("src/legion-other", ("https://github.com/sjawhar/pivot",)),
                              ("Code/legion-other", ("https://github.com/cybermonklabs-other/x",))):
            with self.subTest(path=path):
                self.assertEqual(self.commit_identity(self.init(path, *remotes)), (DEFAULT, DEFAULT))

    def test_a_jj_rewrite_of_a_cybermonk_git_commit_keeps_its_signature(self) -> None:
        # With signing.behavior = "own", jj signs a rewrite of a commit whose author email is its user.email and drops
        # the signature of any other; a git commit there is cybermonk's, so jj must be too.
        self.signing_key()
        repo = self.home / "elsewhere" / "legion"
        repo.mkdir(parents=True)
        self.git("init", "-q", cwd=repo)
        self.git("remote", "add", "origin", "https://github.com/cybermonklabs/legion.git", cwd=repo)
        self.git("commit", "-q", "--allow-empty", "-m", "base", cwd=repo)
        self.assertIn("\ngpgsig ", self.git("cat-file", "commit", "HEAD", cwd=repo), "git did not sign its commit")
        self.jj("git", "init", "--colocate", "--quiet", cwd=repo, signed=True)
        self.jj("describe", "--quiet", "-r", "@-", "-m", "rewritten", cwd=repo, signed=True)
        self.assertTrue(self.signed(repo, "@-"), "jj dropped the signature")
        self.assertEqual(self.identity(repo, "@-"), (CYBERMONK, CYBERMONK))


if __name__ == "__main__":
    unittest.main()
