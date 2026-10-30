#!/usr/bin/env python3
"""Regression tests for worktree-clone-check: it flags the clone and leaves the worktree alone.

The case it was written for is in scripts/worktree-clone-check.md: five children of
~/.worktrees/binary-equivalence-taiga held their own object stores on 2026-10-30 and root hit
zero bytes free. The tests below build the same shape in miniature — one real worktree, one
clone — and check the three things the gate must get right: which child it names, that an
identical store reached by a hardlink is not counted as duplicated bytes, and that a copy with
its own inode is.
"""

import importlib.machinery
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "worktree-clone-check"
LOADER = importlib.machinery.SourceFileLoader("worktree_clone_check", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
CHECK = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(CHECK)

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}
SESSION = "01a13d00-0000-7000-8000-000000000001"


def git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, env=GIT_ENV, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class WorktreeCloneCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "worktrees"
        (self.root / "proj").mkdir(parents=True)

        # The upstream repository lives OUTSIDE the root, as ~/src/<project> does.
        self.origin = self.base / "origin"
        self.origin.mkdir()
        git("init", "-q", "-b", "main", ".", cwd=self.origin)
        (self.origin / "file").write_text("one\n")
        git("add", "file", cwd=self.origin)
        git("commit", "-qm", f"first\n\nOmp-Session: {SESSION}", cwd=self.origin)
        git("repack", "-adq", cwd=self.origin)  # a pack, so the stores have an object set

    def run_check(self, *argv: str) -> tuple[int, str]:
        out = io.StringIO()
        saved = sys.argv
        sys.argv = ["worktree-clone-check", str(self.root), *argv]
        try:
            with redirect_stdout(out):
                code = CHECK.main()
        finally:
            sys.argv = saved
        return code, out.getvalue()

    def test_flags_the_clone_and_not_the_worktree(self) -> None:
        git("worktree", "add", "-q", "-b", "real", str(self.root / "proj" / "real"), cwd=self.origin)
        git("clone", "-q", str(self.origin), str(self.root / "proj" / "copy"), cwd=self.base)
        self.assertTrue((self.root / "proj" / "real" / ".git").is_file())
        self.assertTrue((self.root / "proj" / "copy" / ".git").is_dir())

        code, out = self.run_check()

        self.assertEqual(code, 1, out)
        self.assertIn("1 clone(s), 1 real worktree(s)", out)
        self.assertIn("clone  proj/copy", out)
        self.assertNotIn("proj/real", out)
        self.assertIn(f"session Omp-Session: {SESSION}", out)
        self.assertIn("head    branch main", out)
        self.assertIn("reflog", out)
        self.assertIn("clone: from", out)
        # It changed nothing: both children are still what they were.
        self.assertTrue((self.root / "proj" / "real" / ".git").is_file())
        self.assertTrue((self.root / "proj" / "copy" / ".git").is_dir())

    def test_clean_root_exits_zero(self) -> None:
        git("worktree", "add", "-q", "-b", "real", str(self.root / "proj" / "real"), cwd=self.origin)

        code, out = self.run_check()

        self.assertEqual(code, 0, out)
        self.assertIn("0 clone(s), 1 real worktree(s)", out)
        self.assertIn("duplication: 0 bytes", out)

    def test_hardlinked_store_is_not_duplicated_bytes_but_a_second_inode_is(self) -> None:
        # `git clone <path>` hardlinks the packs: two directories, one copy on disk.
        git("clone", "-q", str(self.origin), str(self.root / "proj" / "hardlinked"), cwd=self.base)
        git("clone", "-q", str(self.root / "proj" / "hardlinked"),
            str(self.root / "proj" / "hardlinked-too"), cwd=self.base)
        code, out = self.run_check()
        self.assertEqual(code, 1, out)
        self.assertIn("hardlinked to another store", out)
        self.assertIn("duplication: 0 bytes", out)

        # --no-hardlinks copies the same objects to their own inode: now bytes are duplicated.
        git("clone", "-q", "--no-hardlinks", str(self.origin),
            str(self.root / "proj" / "own-inode"), cwd=self.base)
        code, out = self.run_check()
        self.assertEqual(code, 1, out)
        self.assertIn("3 clone(s)", out)
        self.assertIn("objects an earlier store already holds", out)
        duplicated = [line for line in out.splitlines() if line.startswith("duplication: ")]
        self.assertEqual(len(duplicated), 1, out)
        self.assertNotIn("duplication: 0 bytes", duplicated[0])

    def test_quick_applies_the_rule_only(self) -> None:
        git("worktree", "add", "-q", "-b", "real", str(self.root / "proj" / "real"), cwd=self.origin)
        git("clone", "-q", str(self.origin), str(self.root / "proj" / "copy"), cwd=self.base)

        code, out = self.run_check("--quick")

        self.assertEqual(code, 1, out)
        self.assertIn("clone  proj/copy", out)
        self.assertNotIn("duplication", out)

    def test_symlinked_child_is_not_followed_by_default(self) -> None:
        elsewhere = self.base / "elsewhere"
        git("clone", "-q", str(self.origin), str(elsewhere), cwd=self.base)
        (self.root / "proj" / "linked").symlink_to(elsewhere)

        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        self.assertIn("1 symlinked child(ren) not followed", out)

        code, out = self.run_check("--follow-symlinks")
        self.assertEqual(code, 1, out)
        self.assertIn("clone  proj/linked", out)


if __name__ == "__main__":
    unittest.main()
