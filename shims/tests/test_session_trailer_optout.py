#!/usr/bin/env python3
"""shims/git and shims/jj give an omp session's commits the Omp-Session trailer in every repository,
public ones included, and leave it off only where the checkout sets `omp.sessionTrailer false`
(scripts/omp-session-trailer), against the real git and jj. A fake curl and gh on PATH record any
call, so the tests also show that no commit asks GitHub anything."""
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

SHIMS = Path(__file__).parents[1]
SCRIPTS = SHIMS.parent / "scripts"
AGENT_MARKERS = ("OMP_SESSION_ID", "PI_SESSION_FILE", "PI_TOOL_BRIDGE_URL", "AGENTBOX_SESSION")
SESSION = "01a0fa64-b3a5-7415-b143-c2a338b5da8b"
TRAILER = f"Omp-Session: {SESSION}"
PUBLIC = "https://github.com/theorem-labs/oss-verify-proofs.git"
FAKE = '#!/bin/bash\necho "$0 $*" >> "$FAKE_LOG"\nexit 1\n'


def real_jj() -> str | None:
    for d in os.environ["PATH"].split(os.pathsep):
        c = Path(d) / "jj"
        if c.is_file() and os.access(c, os.X_OK) and b"jj-lock-shim" not in c.read_bytes()[:512] and c.resolve().name != "mise":
            return str(c)
    found = subprocess.run(["mise", "which", "jj"], capture_output=True, text=True, check=False).stdout.strip()
    return found or None


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        fake = self.tmp / "fake-bin"
        fake.mkdir()
        for name in ("curl", "gh"):
            (fake / name).write_text(FAKE)
            (fake / name).chmod(0o755)
        self.log = self.tmp / "network-calls"
        self.log.touch()
        config = self.tmp / "gitconfig"
        config.write_text("[user]\n\tname = Agent\n\temail = agent@example.com\n[init]\n\tdefaultBranch = main\n")
        base = {k: v for k, v in os.environ.items() if k not in AGENT_MARKERS and not k.startswith("GIT_CONFIG_") and k != "JJ_CONFIG"}
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

    def run_ok(self, *argv: str, cwd: Path, env: dict[str, str]) -> str:
        result = subprocess.run(list(argv), cwd=cwd, env=env, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout


class GitTest(Base):
    def repo(self, *remotes: tuple[str, str]) -> Path:
        repo = self.tmp / "repo"
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

    def test_no_remote_keeps_it(self):
        self.assertIn(TRAILER, self.commit(self.repo()))

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
        repo = self.repo(("origin", PUBLIC))
        script = str(SCRIPTS / "omp-session-trailer")
        result = subprocess.run([script, "git"], cwd=repo, env=self.plain, capture_output=True, text=True, check=False)
        self.assertEqual((result.returncode, result.stdout), (0, "on: omp.sessionTrailer is unset\n"))
        self.run_ok("git", "config", "omp.sessionTrailer", "false", cwd=repo, env=self.plain)
        result = subprocess.run([script, "git"], cwd=repo, env=self.plain, capture_output=True, text=True, check=False)
        self.assertEqual((result.returncode, result.stdout), (1, "off: omp.sessionTrailer is false\n"))


class JjTest(Base):
    def setUp(self):
        super().setUp()
        jj = real_jj()
        if jj is None:
            raise unittest.SkipTest("no real jj")
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

    def colocated(self, name: str, url: str | None, opt_out: bool = False) -> Path:
        repo = self.tmp / name
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
        self.assertIn(TRAILER, self.commit(self.colocated("none", None)))

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
        self.assertIn(TRAILER, self.commit(repo))
        store = repo / ".jj" / "repo" / "store"
        backing = (store / (store / "git_target").read_text().strip()).resolve()
        self.run_ok("git", "--git-dir", str(backing), "config", "omp.sessionTrailer", "false", cwd=repo, env=self.plain)
        self.assertEqual(self.commit(repo), "work\n")


if __name__ == "__main__":
    unittest.main()
