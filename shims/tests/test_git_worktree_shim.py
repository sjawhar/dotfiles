#!/usr/bin/env python3
"""shims/git against the real git, in scratch repositories."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SHIMS = Path(__file__).parents[1]
AGENT_MARKERS = ("OMP_SESSION_ID", "PI_SESSION_FILE", "PI_TOOL_BRIDGE_URL", "AGENTBOX_SESSION")


def plain_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in AGENT_MARKERS and not k.startswith("GIT_CONFIG_")}
    env["PATH"] = f"{SHIMS}{os.pathsep}{os.environ['PATH']}"
    return env


class GitWorktreeShimTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.plain = plain_env()
        self.agent = {**self.plain, "OMP_SESSION_ID": "test-session"}
        for cmd in (["git", "init", "-q"], ["git", "commit", "-q", "--allow-empty", "-m", "init"]):
            subprocess.run(cmd, cwd=self.repo, env=self.plain, check=True, capture_output=True)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def git(self, *args: str, env: dict[str, str] | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", *args], cwd=cwd or self.repo, env=env or self.agent, capture_output=True, text=True, check=False)

    def registrations(self) -> dict[str, str]:
        out = self.git("worktree", "list", "--porcelain", env=self.plain).stdout
        regs: dict[str, str] = {}
        for block in out.strip().split("\n\n"):
            lines = block.splitlines()
            regs[lines[0].removeprefix("worktree ")] = next((line for line in lines if line.startswith("locked")), "")
        return regs

    def missing_worktree(self, name: str) -> Path:
        path = self.tmp / name
        self.git("worktree", "add", "-q", "--detach", str(path), env=self.plain)
        shutil.rmtree(path)
        return path

    def test_a_bare_prune_is_refused_in_an_agent_session_and_deletes_nothing(self):
        path = self.missing_worktree("gone")
        result = self.git("worktree", "prune")
        self.assertEqual(result.returncode, 1)
        self.assertIn("refused", result.stderr)
        self.assertIn(str(path), self.registrations())

    def test_a_prune_behind_global_options_is_still_seen(self):
        self.missing_worktree("gone")
        result = self.git("-C", str(self.repo), "worktree", "prune", cwd=self.tmp)
        self.assertEqual(result.returncode, 1)

    def test_a_dry_run_prune_is_allowed(self):
        self.missing_worktree("gone")
        self.assertEqual(self.git("worktree", "prune", "-n").returncode, 0)

    def test_outside_an_agent_session_prune_behaves_as_git_does(self):
        path = self.missing_worktree("gone")
        self.assertEqual(self.git("worktree", "prune", env=self.plain).returncode, 0)
        self.assertNotIn(str(path), self.registrations())

    def test_an_added_worktree_is_locked_and_its_owner_can_still_remove_it(self):
        path = self.tmp / "wt"
        self.assertEqual(self.git("worktree", "add", "-q", "--detach", str(path)).returncode, 0)
        self.assertIn("AGENTC-431", self.registrations()[str(path)])
        result = self.git("worktree", "remove", str(path))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(str(path), self.registrations())

    def test_a_lock_someone_else_set_still_blocks_remove(self):
        path = self.tmp / "held"
        self.git("worktree", "add", "-q", "--detach", "--lock", "--reason", "mine", str(path))
        self.assertEqual(self.registrations()[str(path)], "locked mine")
        self.assertNotEqual(self.git("worktree", "remove", str(path)).returncode, 0)
        self.assertIn(str(path), self.registrations())

    def test_a_caller_path_without_the_system_dirs_still_reaches_real_git(self):
        env = {**self.agent, "PATH": str(SHIMS)}
        self.assertEqual(self.git("rev-parse", "HEAD", env=env).returncode, 0)
        self.missing_worktree("gone")
        refused = self.git("worktree", "prune", env=env)
        self.assertEqual(refused.returncode, 1)
        self.assertIn("refused", refused.stderr)

    def test_other_commands_pass_through_unchanged(self):
        direct = subprocess.run(["/usr/bin/git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True, check=True).stdout
        self.assertEqual(self.git("rev-parse", "HEAD").stdout, direct)

    def test_a_wrapper_above_the_shim_that_hands_off_positionally_is_not_chosen_again(self):
        # knives gh puts such a wrapper first on PATH inside a jj checkout; a shim that
        # scans PATH from the top picks it as "the real git" and the two exec each other
        # forever (four hung `gh pr create`s at ~70% CPU each, 2026-09-27).
        wrapper = self.tmp / "wrapper"
        wrapper.mkdir()
        (wrapper / "git").write_text(
            "#!/bin/bash\n"
            '_self="$(cd "$(dirname "$0")" && pwd)"\n'
            "_after=false\n"
            "IFS=':' read -ra _dirs <<< \"$PATH\"\n"
            'for _d in "${_dirs[@]}"; do\n'
            '    if [[ "$_d" == "$_self" ]]; then _after=true; continue; fi\n'
            '    [[ "$_after" == true && -x "$_d/git" ]] && exec "$_d/git" "$@"\n'
            "done\n"
            "exit 127\n"
        )
        (wrapper / "git").chmod(0o755)
        env = {**self.agent, "PATH": f"{wrapper}{os.pathsep}{self.agent['PATH']}"}
        direct = subprocess.run(["/usr/bin/git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True, check=True).stdout
        try:
            result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, env=env, capture_output=True, text=True, check=False, timeout=10)
        except subprocess.TimeoutExpired:
            self.fail("the shim chose the wrapper above it and the two exec each other forever")
        self.assertEqual(result.stdout, direct)


if __name__ == "__main__":
    unittest.main()
