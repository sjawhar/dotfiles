#!/usr/bin/env python3
"""shims/git and shims/jj give an omp session's commits the Omp-Session trailer in every repository,
public ones included, and leave it off only where the checkout sets `omp.sessionTrailer false` or
the repository is a throwaway one: no remote, and its common directory under a temporary root
(scripts/omp-session-trailer), against the real git and jj. A fake curl and gh on PATH record any
call, so the tests also show that no commit asks GitHub anything. self.tmp (mkdtemp) is under a
temporary root; self.kept, under ~/.cache, is not."""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_jj_lock_shim import real_jj  # noqa: E402

SHIMS = Path(__file__).parents[1]
SCRIPTS = SHIMS.parent / "scripts"
AGENT_MARKERS = ("OMP_SESSION_ID", "PI_SESSION_FILE", "PI_TOOL_BRIDGE_URL", "AGENTBOX_SESSION",
                 "CLAUDECODE", "ENVOY_SESSION_ID", "AGENT_SHIM_AGENT", "LEGION_ROLE", "LEGION_TREE",
                 "LEGION_CONTROLLER", "LEGION_DAEMON_URL")
SESSION = "01a0fa64-b3a5-7415-b143-c2a338b5da8b"
TRAILER = f"Omp-Session: {SESSION}"
PUBLIC = "https://github.com/theorem-labs/oss-verify-proofs.git"
FAKE = '#!/bin/bash\necho "$0 $*" >> "$FAKE_LOG"\nexit 1\n'


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        cache = Path.home() / ".cache"
        cache.mkdir(exist_ok=True)
        self.kept = Path(tempfile.mkdtemp(prefix="omp-trailer-test-", dir=cache))
        fake = self.tmp / "fake-bin"
        fake.mkdir()
        for name in ("curl", "gh"):
            (fake / name).write_text(FAKE)
            (fake / name).chmod(0o755)
        self.log = self.tmp / "network-calls"
        self.log.touch()
        config = self.tmp / "gitconfig"
        config.write_text("[user]\n\tname = Agent\n\temail = agent@example.com\n[init]\n\tdefaultBranch = main\n")
        # SSH_CONNECTION in a session with no terminal is an agent's command too (INFRA-1538), so a
        # person's environment here drops it, and SSH_TTY and TMUX with it, wherever this suite is run from.
        dropped = (*AGENT_MARKERS, "SSH_CONNECTION", "SSH_TTY", "TMUX", "AGENT_SHIM_PERSON", "JJ_CONFIG")
        base = {k: v for k, v in os.environ.items() if k not in dropped and not k.startswith("GIT_CONFIG_")}
        self.plain = {
            **base,
            "PATH": os.pathsep.join([str(fake), str(SHIMS), os.environ["PATH"]]),
            "GIT_CONFIG_GLOBAL": str(config),
            "GIT_CONFIG_NOSYSTEM": "1",
            "FAKE_LOG": str(self.log),
        }
        self.agent = {**self.plain, "OMP_SESSION_ID": SESSION}

    def tearDown(self):
        self.assertEqual(self.log.read_text(), "", "a commit asked the network")
        shutil.rmtree(self.tmp)
        shutil.rmtree(self.kept)

    def run_ok(self, *argv: str, cwd: Path, env: dict[str, str]) -> str:
        result = subprocess.run(list(argv), cwd=cwd, env=env, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout


class GitTest(Base):
    def repo(self, *remotes: tuple[str, str], under: Path | None = None) -> Path:
        repo = (under or self.tmp) / "repo"
        repo.mkdir()
        self.run_ok("git", "init", "-q", cwd=repo, env=self.plain)
        for name, url in remotes:
            self.run_ok("git", "remote", "add", name, url, cwd=repo, env=self.plain)
        return repo

    def commit(self, repo: Path) -> str:
        self.run_ok("git", "commit", "-q", "--allow-empty", "-m", "work", cwd=repo, env=self.agent)
        return self.run_ok("git", "log", "-1", "--format=%B", cwd=repo, env=self.plain)

    def test_a_public_theorem_labs_repository_keeps_it(self):
        self.assertIn(TRAILER, self.commit(self.repo(("origin", PUBLIC), ("upstream", "https://github.com/rocq-prover/rocq.git"))))

    def test_no_remote_under_a_temporary_root_leaves_it_off_until_a_remote_is_added(self):
        repo = self.repo()
        self.assertEqual(self.commit(repo).strip(), "work")
        self.run_ok("git", "remote", "add", "origin", PUBLIC, cwd=repo, env=self.plain)
        self.assertIn(TRAILER, self.commit(repo))

    def test_no_remote_outside_a_temporary_root_keeps_it(self):
        self.assertIn(TRAILER, self.commit(self.repo(under=self.kept)))

    def test_a_temporary_worktree_of_a_repository_with_a_remote_keeps_it(self):
        repo = self.repo(("origin", PUBLIC))
        self.run_ok("git", "commit", "-q", "--allow-empty", "-m", "base", cwd=repo, env=self.plain)
        self.run_ok("git", "worktree", "add", "-q", "-b", "side", str(self.tmp / "wt"), cwd=repo, env=self.plain)
        self.assertIn(TRAILER, self.commit(self.tmp / "wt"))

    def test_a_temporary_worktree_of_a_kept_repository_keeps_it(self):
        repo = self.repo(under=self.kept)
        self.run_ok("git", "commit", "-q", "--allow-empty", "-m", "base", cwd=repo, env=self.plain)
        self.run_ok("git", "worktree", "add", "-q", "-b", "side", str(self.tmp / "wt"), cwd=repo, env=self.plain)
        self.assertIn(TRAILER, self.commit(self.tmp / "wt"))

    def test_a_symlinked_tmpdir_still_counts(self):
        # The root is under self.kept, so only $TMPDIR, reached through a symlink, can match it.
        root = self.kept / "session-tmp"
        root.mkdir()
        link = self.kept / "tmp-link"
        link.symlink_to(root)
        repo = root / "repo"
        repo.mkdir()
        self.run_ok("git", "init", "-q", cwd=repo, env=self.plain)
        self.assertIn(TRAILER, self.commit(repo))
        env = {**self.agent, "TMPDIR": str(link)}
        self.run_ok("git", "commit", "-q", "--allow-empty", "-m", "work", cwd=link / "repo", env=env)
        self.assertEqual(self.run_ok("git", "log", "-1", "--format=%B", cwd=repo, env=self.plain).strip(), "work")

    def test_true_keeps_it_in_a_throwaway_repository(self):
        repo = self.repo()
        self.run_ok("git", "config", "omp.sessionTrailer", "true", cwd=repo, env=self.plain)
        self.assertIn(TRAILER, self.commit(repo))

    def test_the_opt_out_leaves_it_off_outside_a_temporary_root(self):
        repo = self.repo(under=self.kept)
        self.run_ok("git", "config", "omp.sessionTrailer", "false", cwd=repo, env=self.plain)
        self.assertEqual(self.commit(repo).strip(), "work")

    def test_a_merge_in_a_throwaway_repository_carries_none(self):
        # A merge takes no --trailer: shims/git points core.hooksPath at git-hooks/ only where the script says yes.
        repo = self.repo()
        self.run_ok("git", "commit", "-q", "--allow-empty", "-m", "base", cwd=repo, env=self.plain)
        self.run_ok("git", "checkout", "-q", "-b", "side", cwd=repo, env=self.plain)
        self.run_ok("git", "commit", "-q", "--allow-empty", "-m", "side", cwd=repo, env=self.plain)
        self.run_ok("git", "checkout", "-q", "main", cwd=repo, env=self.plain)
        self.run_ok("git", "commit", "-q", "--allow-empty", "-m", "main", cwd=repo, env=self.plain)
        self.run_ok("git", "merge", "-q", "--no-edit", "side", cwd=repo, env=self.agent)
        self.assertNotIn(TRAILER, self.run_ok("git", "log", "-1", "--format=%B", cwd=repo, env=self.plain))
        self.run_ok("git", "remote", "add", "origin", PUBLIC, cwd=repo, env=self.plain)
        self.run_ok("git", "reset", "-q", "--hard", "HEAD~1", cwd=repo, env=self.plain)
        self.run_ok("git", "merge", "-q", "--no-edit", "side", cwd=repo, env=self.agent)
        self.assertIn(TRAILER, self.run_ok("git", "log", "-1", "--format=%B", cwd=repo, env=self.plain))

    def test_the_opt_out_leaves_it_off_and_true_changes_nothing(self):
        repo = self.repo(("origin", PUBLIC))
        self.run_ok("git", "config", "omp.sessionTrailer", "false", cwd=repo, env=self.plain)
        self.assertEqual(self.commit(repo).strip(), "work")
        self.run_ok("git", "config", "omp.sessionTrailer", "true", cwd=repo, env=self.plain)
        self.assertIn(TRAILER, self.commit(repo))

    def test_a_command_line_opt_out_counts(self):
        repo = self.repo(("origin", PUBLIC))
        self.run_ok("git", "-c", "omp.sessionTrailer=false", "commit", "-q", "--allow-empty", "-m", "work", cwd=repo, env=self.agent)
        self.assertEqual(self.run_ok("git", "log", "-1", "--format=%B", cwd=repo, env=self.plain).strip(), "work")

    def test_the_script_says_why(self):
        script = str(SCRIPTS / "omp-session-trailer")

        def ask(repo: Path, **extra: str) -> tuple[int, str]:
            result = subprocess.run([script, "git"], cwd=repo, env={**self.plain, **extra}, capture_output=True, text=True, check=False)
            return result.returncode, result.stdout

        repo = self.repo()
        common = os.path.realpath(repo / ".git")
        self.assertEqual(ask(repo)[0], 1)
        self.assertRegex(ask(repo)[1], rf"^off: no remote, and the git common directory {common} is under the temporary root .+\n$")
        self.run_ok("git", "remote", "add", "origin", PUBLIC, cwd=repo, env=self.plain)
        self.assertEqual(ask(repo), (0, "on: omp.sessionTrailer is unset and the repository has a remote\n"))
        self.run_ok("git", "config", "omp.sessionTrailer", "false", cwd=repo, env=self.plain)
        self.assertEqual(ask(repo), (1, "off: omp.sessionTrailer is false\n"))
        self.run_ok("git", "config", "omp.sessionTrailer", "true", cwd=repo, env=self.plain)
        self.assertEqual(ask(repo), (0, "on: omp.sessionTrailer is true\n"))
        kept = self.repo(under=self.kept)
        self.assertEqual(
            ask(kept),
            (0, f"on: omp.sessionTrailer is unset; no remote, but the git common directory {os.path.realpath(kept / '.git')} is under no temporary root\n"),
        )
        # Not a repository (the ceiling keeps git from finding one above it, such as a home directory under git).
        self.assertEqual(ask(self.kept, GIT_CEILING_DIRECTORIES=str(self.kept.parent)), (0, "on: omp.sessionTrailer is unset and git remote failed\n"))


class JjTest(Base):
    def setUp(self):
        super().setUp()
        jj = real_jj()
        self.agent["PATH"] = os.pathsep.join([str(self.tmp / "fake-bin"), str(SHIMS), str(Path(jj).parent), self.agent["PATH"]])
        user = self.tmp / "jj-user.toml"
        user.write_text('[user]\nname = "Agent"\nemail = "agent@example.com"\n[signing]\nbehavior = "drop"\n')
        # What omp/extensions/session-env.ts writes.
        overlay = self.tmp / f"omp-attribution-{SESSION}.toml"
        overlay.write_text(
            f"[templates]\ncommit_trailers = '\"{TRAILER}\"'\n\n[revset-aliases]\n"
            f"\"immutable_heads()\" = 'builtin_immutable_heads() | (ancestors(visible_heads(), 4) ~ ::builtin_immutable_heads() ~ description(glob:\"*{TRAILER}*\") ~ empty() ~ present(@) ~ (description(exact:\"\") ~ working_copies()))'\n"
        )
        self.agent["JJ_CONFIG"] = f"{user}:{overlay}"

    def jj(self, *args: str, cwd: Path) -> str:
        return self.run_ok("jj", *args, cwd=cwd, env=self.agent)

    def colocated(self, name: str, url: str | None, opt_out: bool = False, under: Path | None = None) -> Path:
        repo = (under or self.tmp) / name
        repo.mkdir()
        self.run_ok("git", "init", "-q", cwd=repo, env=self.plain)
        if url:
            self.run_ok("git", "remote", "add", "origin", url, cwd=repo, env=self.plain)
        if opt_out:
            self.run_ok("git", "config", "omp.sessionTrailer", "false", cwd=repo, env=self.plain)
        self.jj("git", "init", "--colocate", cwd=repo)
        return repo

    def commit(self, repo: Path) -> str:
        (repo / "file").write_text(str(time.time_ns()))
        self.jj("commit", "-m", "work", cwd=repo)
        return self.jj("log", "-r", "@-", "--no-graph", "-T", "description", cwd=repo)

    def test_a_public_theorem_labs_repository_keeps_it(self):
        self.assertIn(TRAILER, self.commit(self.colocated("pub", PUBLIC)))
        self.assertIn(TRAILER, self.commit(self.colocated("none", None, under=self.kept)))

    def test_a_throwaway_repository_leaves_it_off(self):
        self.assertEqual(self.commit(self.colocated("none", None)), "work\n")

    def test_the_opt_out_leaves_it_off_and_its_commit_stays_rewritable(self):
        repo = self.colocated("out", PUBLIC, opt_out=True)
        self.assertEqual(self.commit(repo), "work\n")
        self.jj("describe", "-r", "@-", "-m", "reworded", cwd=repo)
        self.assertEqual(self.jj("log", "-r", "@-", "--no-graph", "-T", "description", cwd=repo), "reworded\n")

    def test_a_secondary_workspace_and_a_repository_named_by_r_are_judged_by_their_repository(self):
        repo = self.colocated("out", PUBLIC, opt_out=True)
        self.jj("workspace", "add", "../ws", cwd=repo)
        self.assertEqual(self.commit(self.tmp / "ws"), "work\n")
        (repo / "file").write_text("r")
        self.jj("-R", str(repo), "commit", "-m", "by -R", cwd=self.tmp)
        self.assertEqual(self.jj("log", "-r", "@-", "--no-graph", "-T", "description", cwd=repo), "by -R\n")

    def test_a_non_colocated_repository_reads_its_backing_git_repository(self):
        repo = self.tmp / "nc"
        repo.mkdir()
        self.jj("git", "init", "--no-colocate", cwd=repo)
        self.jj("git", "remote", "add", "origin", PUBLIC, cwd=repo)
        self.assertIn(TRAILER, self.commit(repo))
        store = repo / ".jj" / "repo" / "store"
        backing = (store / (store / "git_target").read_text().strip()).resolve()
        self.run_ok("git", "--git-dir", str(backing), "config", "omp.sessionTrailer", "false", cwd=repo, env=self.plain)
        self.assertEqual(self.commit(repo), "work\n")


if __name__ == "__main__":
    unittest.main()
