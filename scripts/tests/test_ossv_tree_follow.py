#!/usr/bin/env python3
"""Tests for devbox/ossv-tree-follow: it fast-forwards a clone of ops, clears a path in the way
only when its file already holds ops' bytes, and refuses, keeping the file, when the bytes differ.

Each case makes a throwaway origin with branch ops, lets the script clone it, moves the origin on,
puts files in the clone's way, and runs the script again (dispatch://INFRA-993: `agentbox move --to`
copied stale and current files into sami-devbox's clone, and every run refused for an hour).
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
SCRIPT = DOTFILES / "devbox" / "ossv-tree-follow"


def git(cwd: Path, *args: str) -> str:
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    return subprocess.run(["git", "-c", "commit.gpgsign=false", "-C", str(cwd), *args], env=env, check=True,
                          capture_output=True, text=True).stdout.strip()


class OssvTreeFollowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.work = root / "work"
        self.work.mkdir()
        git(self.work, "init", "-q", "-b", "ops")
        (self.work / "bin").mkdir()
        (self.work / "bin" / "tool").write_text("v1\n")
        (self.work / "notes.tsv").write_text("a\n")
        (self.work / "peaks.tsv").write_text("p\n")
        git(self.work, "add", "-A")
        git(self.work, "commit", "-q", "-m", "one")
        self.tree = root / "tree"
        run = root / "run"
        run.mkdir()
        self.env = dict(os.environ, OSSV_TREE_FOLLOW_TREE=str(self.tree), OSSV_TREE_FOLLOW_REMOTE=str(self.work),
                        OSSV_TREE_FOLLOW_REFERENCE=str(root / "none"), XDG_RUNTIME_DIR=str(run))
        first = self.follow()
        self.assertEqual(first.returncode, 0, first.stderr)

    def follow(self) -> subprocess.CompletedProcess:
        return subprocess.run([str(SCRIPT)], env=self.env, capture_output=True, text=True, timeout=60)

    def advance(self) -> str:
        """ops adds briefs/new.md and changes notes.tsv and bin/tool."""
        (self.work / "briefs").mkdir()
        (self.work / "briefs" / "new.md").write_text("brief\n")
        (self.work / "notes.tsv").write_text("a\nb\n")
        (self.work / "bin" / "tool").write_text("v2\n")
        git(self.work, "add", "-A")
        git(self.work, "commit", "-q", "-m", "two")
        return git(self.work, "rev-parse", "HEAD")

    def head(self) -> str:
        return git(self.tree, "rev-parse", "HEAD")

    def test_files_holding_ops_bytes_are_cleared_with_a_note(self) -> None:
        tip = self.advance()
        (self.tree / "briefs").mkdir()
        (self.tree / "briefs" / "new.md").write_text("brief\n")  # untracked, ops' bytes
        (self.tree / "notes.tsv").write_text("a\nb\n")  # tracked, changed to ops' bytes
        (self.tree / "peaks.tsv").write_text("p\nlocal\n")  # a local change no new commit touches
        run = self.follow()
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(self.head(), tip)
        self.assertIn("note: untracked briefs/new.md held origin/ops's bytes; removed", run.stdout)
        self.assertIn("note: notes.tsv held a local change equal to origin/ops's bytes; undone", run.stdout)
        self.assertEqual((self.tree / "briefs" / "new.md").read_text(), "brief\n")
        self.assertEqual((self.tree / "notes.tsv").read_text(), "a\nb\n")
        self.assertEqual((self.tree / "peaks.tsv").read_text(), "p\nlocal\n")
        self.assertEqual((self.tree / "bin" / "tool").read_text(), "v2\n")

    def test_an_untracked_file_with_other_bytes_refuses_and_is_kept(self) -> None:
        self.advance()
        before = self.head()
        (self.tree / "briefs").mkdir()
        (self.tree / "briefs" / "new.md").write_text("an older brief\n")
        run = self.follow()
        self.assertEqual(run.returncode, 1, run.stdout)
        self.assertIn("stays at", run.stderr)
        self.assertEqual(self.head(), before)
        self.assertEqual((self.tree / "briefs" / "new.md").read_text(), "an older brief\n")
        self.assertEqual((self.tree / "bin" / "tool").read_text(), "v1\n")

    def test_a_tracked_change_with_other_bytes_refuses_and_is_kept(self) -> None:
        self.advance()
        before = self.head()
        (self.tree / "notes.tsv").write_text("a\nmine\n")
        run = self.follow()
        self.assertEqual(run.returncode, 1, run.stdout)
        self.assertEqual(self.head(), before)
        self.assertEqual((self.tree / "notes.tsv").read_text(), "a\nmine\n")

    def test_nothing_is_cleared_when_the_clone_cannot_fast_forward(self) -> None:
        self.advance()
        (self.tree / "local.md").write_text("x\n")
        git(self.tree, "add", "local.md")
        git(self.tree, "commit", "-q", "-m", "a commit ops lacks")
        before = self.head()
        (self.tree / "briefs").mkdir()
        (self.tree / "briefs" / "new.md").write_text("brief\n")
        run = self.follow()
        self.assertEqual(run.returncode, 1, run.stdout)
        self.assertEqual(self.head(), before)
        self.assertTrue((self.tree / "briefs" / "new.md").exists())
        self.assertNotIn("note:", run.stdout)

    def test_a_path_ops_deletes_does_not_stop_the_run(self) -> None:
        # 2026-11-03 12:00Z: clear_same's `read` met a deleted path and the run exited 1, silently.
        git(self.work, "rm", "-q", "peaks.tsv")
        (self.work / "notes.tsv").write_text("a\nb\n")
        git(self.work, "commit", "-q", "-am", "ops deletes peaks.tsv")
        tip = git(self.work, "rev-parse", "HEAD")
        (self.tree / "notes.tsv").write_text("a\nb\n")
        run = self.follow()
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(self.head(), tip)
        self.assertFalse((self.tree / "peaks.tsv").exists())

    def test_an_unexpected_failure_says_why(self) -> None:
        self.advance()
        # The fetch fails: the remote is gone. set -e stops the run; the journal must say where.
        git(self.tree, "remote", "set-url", "origin", str(self.work.parent / "gone"))
        run = self.follow()
        self.assertNotEqual(run.returncode, 0)
        self.assertRegex(run.stderr, r"ossv-tree-follow: stopped at line \d+: `.*git -C .* fetch .*` exited \d+")


if __name__ == "__main__":
    unittest.main()
