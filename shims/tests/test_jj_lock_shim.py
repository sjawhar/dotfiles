#!/usr/bin/env python3
"""shims/jj against the real jj, in scratch colocated repositories."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SHIMS = Path(__file__).parents[1]


def real_jj_dir():
    for d in os.environ["PATH"].split(os.pathsep):
        c = Path(d) / "jj"
        if c.is_file() and os.access(c, os.X_OK) and b"jj-lock-shim" not in c.read_bytes()[:512]:
            return str(c.parent)
    raise unittest.SkipTest("no real jj on PATH")


class JjLockShimTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.env = {**os.environ, "PATH": f"{SHIMS}{os.pathsep}{real_jj_dir()}{os.pathsep}{os.environ['PATH']}"}
        self.repo = self.tmp / "main"
        self.repo.mkdir()
        for cmd in (["git", "init", "-q"], ["git", "commit", "-q", "--allow-empty", "-m", "init"], ["jj", "git", "init", "--colocate"]):
            subprocess.run(cmd, cwd=self.repo, env=self.env, check=True, capture_output=True)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def jj(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["jj", *args], cwd=cwd or self.repo, env=self.env, capture_output=True, text=True, check=False)

    def worktrees(self):
        out = subprocess.run(["git", "worktree", "list", "--porcelain"], cwd=self.repo, env=self.env, capture_output=True, text=True, check=True).stdout
        blocks = [b.splitlines() for b in out.strip().split("\n\n")]
        return {b[0].removeprefix("worktree "): any(line.startswith("locked") for line in b) for b in blocks}

    def test_an_added_workspace_is_locked_and_forget_still_removes_it(self):
        dest = self.tmp / "ws-a"
        self.assertEqual(self.jj("workspace", "add", "../ws-a", "--name", "a").returncode, 0)
        self.assertTrue(self.worktrees()[str(dest)], "the new workspace's git worktree is not locked")
        self.assertEqual(self.jj("workspace", "forget", "a").returncode, 0)
        self.assertNotIn(str(dest), self.worktrees())

    def test_add_through_repository_flag_from_another_directory_is_locked(self):
        result = self.jj("-R", str(self.repo), "workspace", "add", "ws-b", "--name", "b", cwd=self.tmp)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.worktrees()[str(self.tmp / "ws-b")])

    def test_in_an_agent_session_the_git_shims_lock_is_not_reported_as_a_failure(self):
        self.env["OMP_SESSION_ID"] = "test-session"
        result = self.jj("workspace", "add", "../ws-agent", "--name", "agent")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("could NOT be locked", result.stderr)
        self.assertTrue(self.worktrees()[str(self.tmp / "ws-agent")])

    def test_a_failed_add_keeps_jjs_exit_code_and_message(self):
        (self.tmp / "occupied").mkdir()
        (self.tmp / "occupied" / "file").write_text("x", encoding="utf-8")
        result = self.jj("workspace", "add", "../occupied", "--name", "c")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Destination path exists", result.stderr)
        self.assertNotIn(str(self.tmp / "occupied"), self.worktrees())

    def test_other_commands_pass_through_unchanged(self):
        direct = subprocess.run([str(Path(real_jj_dir()) / "jj"), "log", "-r", "@", "--no-graph", "-T", "change_id"], cwd=self.repo, capture_output=True, text=True, check=True).stdout
        self.assertEqual(self.jj("log", "-r", "@", "--no-graph", "-T", "change_id").stdout, direct)


if __name__ == "__main__":
    unittest.main()
