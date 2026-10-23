#!/usr/bin/env python3
"""shims/git and shims/jj leave the Omp-Session trailer off in a public theorem-labs repository
(scripts/omp-session-trailer), against the real git and jj, with GitHub answered by a fake curl
and a fake gh."""
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

# curl as the script calls it: the HTTP code GitHub answers for each repository on stdout, from
# FAKE_CODES ("owner/repo=code …", FAKE_DEFAULT otherwise); "fail" is a timeout. Every call is logged.
FAKE_CURL = """#!/bin/bash
url="${@: -1}"
slug="${url#https://api.github.com/repos/}"
echo "$slug" >> "$FAKE_LOG"
code="${FAKE_DEFAULT:-404}"
for pair in ${FAKE_CODES:-}; do [[ "${pair%%=*}" == "$slug" ]] && code="${pair#*=}"; done
if [[ "$code" == fail ]]; then printf 000; exit 28; fi
printf '%s' "$code"
"""
FAKE_GH = """#!/bin/bash
echo "gh $*" >> "$FAKE_LOG"
[[ -n "${FAKE_GH:-}" ]] || exit 1
echo "$FAKE_GH"
"""


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
        for name, body in (("curl", FAKE_CURL), ("gh", FAKE_GH)):
            (fake / name).write_text(body)
            (fake / name).chmod(0o755)
        self.log = self.tmp / "github-calls"
        self.log.touch()
        config = self.tmp / "gitconfig"
        config.write_text("[user]\n\tname = Agent\n\temail = agent@example.com\n[init]\n\tdefaultBranch = main\n")
        base = {k: v for k, v in os.environ.items() if k not in AGENT_MARKERS and not k.startswith("GIT_CONFIG_") and k != "JJ_CONFIG"}
        self.plain = {
            **base,
            # The fakes come first: shims/gh would otherwise answer the script's `gh`.
            "PATH": os.pathsep.join([str(fake), str(SHIMS), os.environ["PATH"]]),
            "GIT_CONFIG_GLOBAL": str(config),
            "GIT_CONFIG_NOSYSTEM": "1",
            "XDG_CACHE_HOME": str(self.tmp / "cache"),
            "FAKE_LOG": str(self.log),
        }
        self.agent = {**self.plain, "OMP_SESSION_ID": SESSION}

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_ok(self, *argv: str, cwd: Path, env: dict[str, str]) -> str:
        result = subprocess.run(list(argv), cwd=cwd, env=env, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines()


class GitTest(Base):
    def repo(self, *remotes: tuple[str, str]) -> Path:
        repo = self.tmp / "repo"
        repo.mkdir()
        self.run_ok("git", "init", "-q", cwd=repo, env=self.plain)
        for name, url in remotes:
            self.run_ok("git", "remote", "add", name, url, cwd=repo, env=self.plain)
        return repo

    def commit(self, repo: Path, **extra: str) -> str:
        env = {**self.agent, **extra}
        self.run_ok("git", "commit", "-q", "--allow-empty", "-m", "work", cwd=repo, env=env)
        return self.run_ok("git", "log", "-1", "--format=%B", cwd=repo, env=self.plain)

    def test_a_public_theorem_labs_repository_gets_no_trailer(self):
        repo = self.repo(("origin", PUBLIC))
        self.assertEqual(self.commit(repo, FAKE_DEFAULT="200").strip(), "work")
        self.assertEqual(self.calls(), ["theorem-labs/oss-verify-proofs"])

    def test_a_private_theorem_labs_repository_keeps_it(self):
        repo = self.repo(("origin", "git@github.com:theorem-labs/grading-tools.git"))
        self.assertIn(TRAILER, self.commit(repo, FAKE_DEFAULT="404"))

    def test_another_owner_keeps_it_without_asking_github(self):
        repo = self.repo(("origin", "https://github.com/sjawhar/dotfiles"))
        self.assertIn(TRAILER, self.commit(repo, FAKE_DEFAULT="200"))
        self.assertEqual(self.calls(), [])

    def test_no_remote_keeps_it(self):
        self.assertIn(TRAILER, self.commit(self.repo(), FAKE_DEFAULT="200"))
        self.assertEqual(self.calls(), [])

    def test_a_public_theorem_labs_remote_beside_a_private_origin_turns_it_off(self):
        repo = self.repo(("origin", "https://github.com/theorem-labs/oss-verify.git"), ("public", "ssh://git@github.com/Theorem-Labs/OSS-Verify-Proofs"))
        out = self.commit(repo, FAKE_CODES="theorem-labs/oss-verify=404 theorem-labs/oss-verify-proofs=200")
        self.assertEqual(out.strip(), "work")

    def test_github_not_answering_turns_it_off(self):
        repo = self.repo(("origin", PUBLIC))
        self.assertEqual(self.commit(repo, FAKE_DEFAULT="fail").strip(), "work")

    def test_a_rate_limited_anonymous_request_asks_gh(self):
        repo = self.repo(("origin", PUBLIC))
        self.assertIn(TRAILER, self.commit(repo, FAKE_DEFAULT="403", FAKE_GH="private"))
        self.assertIn("gh api repos/theorem-labs/oss-verify-proofs --jq .visibility", self.calls())
        (self.tmp / "cache").joinpath("omp/github-visibility/theorem-labs/oss-verify-proofs").unlink()
        self.assertEqual(self.commit(repo, FAKE_DEFAULT="429", FAKE_GH="").strip(), "work")

    def test_the_answer_is_cached_until_it_expires(self):
        repo = self.repo(("origin", PUBLIC))
        self.commit(repo, FAKE_DEFAULT="404")
        self.assertIn(TRAILER, self.commit(repo, FAKE_DEFAULT="200"))
        self.assertEqual(len(self.calls()), 1)
        cached = self.tmp / "cache/omp/github-visibility/theorem-labs/oss-verify-proofs"
        cached.write_text(f"{int(time.time()) - 301} private\n")
        self.assertEqual(self.commit(repo, FAKE_DEFAULT="200").strip(), "work")
        self.assertEqual(len(self.calls()), 2)

    def test_the_explicit_setting_wins_both_ways(self):
        repo = self.repo(("origin", PUBLIC))
        self.run_ok("git", "config", "omp.sessionTrailer", "true", cwd=repo, env=self.plain)
        self.assertIn(TRAILER, self.commit(repo, FAKE_DEFAULT="200"))
        self.run_ok("git", "config", "omp.sessionTrailer", "false", cwd=repo, env=self.plain)
        self.run_ok("git", "remote", "remove", "origin", cwd=repo, env=self.plain)
        self.assertEqual(self.commit(repo).strip(), "work")
        self.assertEqual(self.calls(), [])

    def test_the_script_says_why(self):
        repo = self.repo(("origin", PUBLIC))
        result = subprocess.run([str(SCRIPTS / "omp-session-trailer"), "git"], cwd=repo, env={**self.plain, "FAKE_DEFAULT": "200"}, capture_output=True, text=True, check=False)
        self.assertEqual((result.returncode, result.stdout), (1, "off: github.com/theorem-labs/oss-verify-proofs is public\n"))


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

    def jj(self, *args: str, cwd: Path, **extra: str) -> str:
        return self.run_ok("jj", *args, cwd=cwd, env={**self.agent, **extra})

    def colocated(self, name: str, url: str | None) -> Path:
        repo = self.tmp / name
        repo.mkdir()
        self.run_ok("git", "init", "-q", cwd=repo, env=self.plain)
        if url:
            self.run_ok("git", "remote", "add", "origin", url, cwd=repo, env=self.plain)
        self.jj("git", "init", "--colocate", cwd=repo)
        return repo

    def commit(self, repo: Path, **extra: str) -> str:
        (repo / "file").write_text(str(time.time_ns()))
        self.jj("commit", "-m", "work", cwd=repo, **extra)
        return self.jj("log", "-r", "@-", "--no-graph", "-T", "description", cwd=repo)

    def test_a_public_theorem_labs_repository_gets_no_trailer_and_its_commit_stays_rewritable(self):
        repo = self.colocated("pub", PUBLIC)
        self.assertEqual(self.commit(repo, FAKE_DEFAULT="200"), "work\n")
        self.jj("describe", "-r", "@-", "-m", "reworded", cwd=repo, FAKE_DEFAULT="200")
        self.assertEqual(self.jj("log", "-r", "@-", "--no-graph", "-T", "description", cwd=repo), "reworded\n")

    def test_a_private_repository_or_no_remote_keeps_it(self):
        self.assertIn(TRAILER, self.commit(self.colocated("priv", "git@github.com:theorem-labs/grading-tools.git"), FAKE_DEFAULT="404"))
        self.assertIn(TRAILER, self.commit(self.colocated("none", None), FAKE_DEFAULT="200"))

    def test_a_secondary_workspace_and_a_repository_named_by_r_are_judged_by_their_repository(self):
        repo = self.colocated("pub", PUBLIC)
        self.jj("workspace", "add", "../ws", cwd=repo)
        self.assertEqual(self.commit(self.tmp / "ws", FAKE_DEFAULT="200"), "work\n")
        (repo / "file").write_text("r")
        self.jj("-R", str(repo), "commit", "-m", "by -R", cwd=self.tmp, FAKE_DEFAULT="200")
        self.assertEqual(self.jj("log", "-r", "@-", "--no-graph", "-T", "description", cwd=repo), "by -R\n")

    def test_a_non_colocated_repository_reads_its_backing_git_repository(self):
        repo = self.tmp / "nc"
        repo.mkdir()
        self.jj("git", "init", cwd=repo)
        self.jj("git", "remote", "add", "origin", PUBLIC, cwd=repo)
        self.assertEqual(self.commit(repo, FAKE_DEFAULT="200"), "work\n")

    def test_the_explicit_setting_forces_it_on(self):
        repo = self.colocated("pub", PUBLIC)
        self.run_ok("git", "config", "omp.sessionTrailer", "true", cwd=repo, env=self.plain)
        self.assertIn(TRAILER, self.commit(repo, FAKE_DEFAULT="200"))

    def test_a_read_only_command_does_not_ask(self):
        repo = self.colocated("pub", PUBLIC)
        self.jj("log", cwd=repo, FAKE_DEFAULT="200")
        self.jj("st", cwd=repo, FAKE_DEFAULT="200")
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
