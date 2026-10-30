#!/usr/bin/env python3
"""Regression tests for agent-workspace: a second tree of one repository shares its objects.

The case these were written for: on 2026-10-30, eight children of
~/.worktrees/binary-equivalence-taiga each held an object store of its own, 19.0 GB for one
repository whose history is 4.8 GB, and the root filesystem of sami-rocqperf reached 14 MiB
free. The tests build the same shape in miniature and check the five things the provisioner
must get right: the default shares, the flag that does not is the flag, removal clears the
registration the source keeps, a tree whose source is gone is named rather than handed over
broken, and a colocated jj workspace is removed as a jj workspace and not as a git worktree.
"""

import importlib.machinery
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "agent-workspace"
LOADER = importlib.machinery.SourceFileLoader("agent_workspace", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
AW = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(AW)

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}
HAVE_JJ = shutil.which("jj") is not None


def git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, env=GIT_ENV, check=True, capture_output=True, text=True
    )


class AgentWorkspaceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.src = self.base / "src"
        self.wt = self.base / "worktrees"
        self.src.mkdir()
        self.wt.mkdir()
        self.origin = self.base / "origin.git"
        git("init", "-q", "--bare", str(self.origin), cwd=self.base)
        self.source = self.src / "proj"
        git("clone", "-q", str(self.origin), str(self.source), cwd=self.base)
        (self.source / "file.txt").write_text("one\n")
        git("add", "-A", cwd=self.source)
        git("commit", "-qm", "init", cwd=self.source)
        git("branch", "-M", "main", cwd=self.source)
        git("push", "-q", "-u", "origin", "main", cwd=self.source)
        os.environ["AGENT_WORKSPACE_SRC"] = str(self.src)
        os.environ["AGENT_WORKSPACE_ROOT"] = str(self.wt)
        self.addCleanup(os.environ.pop, "AGENT_WORKSPACE_SRC", None)
        self.addCleanup(os.environ.pop, "AGENT_WORKSPACE_ROOT", None)

    def run_aw(self, *argv: str) -> tuple[int, str]:
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                code = AW.main(list(argv))
        except SystemExit as exc:
            code = int(exc.code or 0)
        return code, buf.getvalue()

    def object_dirs(self, tree: Path) -> bool:
        """Does this tree carry an object store of its own?"""
        return (tree / ".git").is_dir() and not (tree / ".git" / "objects" / "info" / "alternates").is_file()

    # ------------------------------------------------------------------ the default

    def test_new_without_a_flag_makes_a_worktree_that_carries_no_objects(self) -> None:
        code, _ = self.run_aw("new", "proj", "alpha")
        self.assertEqual(code, 0)
        tree = self.wt / "proj" / "alpha"
        self.assertTrue((tree / "file.txt").is_file(), "the working tree was not written")
        self.assertTrue((tree / ".git").is_file(), "a worktree's .git must be a pointer file")
        self.assertFalse(self.object_dirs(tree))

    def test_a_second_workspace_adds_no_second_object_store(self) -> None:
        self.run_aw("new", "proj", "alpha")
        before = AW.tree_size(self.source / ".git")
        self.run_aw("new", "proj", "beta")
        after = AW.tree_size(self.source / ".git")
        # The source grows by the new branch's refs and the worktree's own small gitdir, never
        # by a copy of the history.
        self.assertLess(after - before, 256 * 1024)
        self.assertFalse(self.object_dirs(self.wt / "proj" / "beta"))

    def test_the_independent_flag_is_what_costs_a_second_object_store(self) -> None:
        code, _ = self.run_aw("new", "proj", "solo", "--independent")
        self.assertEqual(code, 0)
        self.assertTrue(self.object_dirs(self.wt / "proj" / "solo"))

    def test_separate_repo_borrows_objects_and_records_the_borrower(self) -> None:
        code, _ = self.run_aw("new", "proj", "sep", "--separate-repo")
        self.assertEqual(code, 0)
        tree = self.wt / "proj" / "sep"
        alt = tree / ".git" / "objects" / "info" / "alternates"
        self.assertTrue(alt.is_file(), "a --shared clone must borrow through alternates")
        self.assertIn(str(self.source / ".git" / "objects"), alt.read_text())
        self.assertIn(str(tree), AW.read_borrowers(self.source))
        # Automatic maintenance in the source must be off, or its gc can prune what the
        # borrower alone needs.
        self.assertEqual(
            git("config", "--get", "gc.auto", cwd=self.source).stdout.strip(), "0"
        )

    # ------------------------------------------------------------------ removal

    def test_rm_takes_away_the_registration_the_source_keeps(self) -> None:
        self.run_aw("new", "proj", "alpha")
        code, _ = self.run_aw("rm", "proj", "alpha")
        self.assertEqual(code, 0)
        self.assertFalse((self.wt / "proj" / "alpha").exists())
        listing = git("worktree", "list", "--porcelain", cwd=self.source).stdout
        self.assertNotIn(str(self.wt / "proj" / "alpha"), listing)

    def test_rm_refuses_a_tree_holding_work_no_remote_has(self) -> None:
        self.run_aw("new", "proj", "alpha")
        (self.wt / "proj" / "alpha" / "scratch.txt").write_text("mine\n")
        code, _ = self.run_aw("rm", "proj", "alpha")
        self.assertEqual(code, 1)
        self.assertTrue((self.wt / "proj" / "alpha").exists())
        code, _ = self.run_aw("rm", "proj", "alpha", "--force")
        self.assertEqual(code, 0)

    def test_rm_deletes_the_branch_only_when_a_remote_holds_all_of_it(self) -> None:
        self.run_aw("new", "proj", "alpha")
        self.run_aw("rm", "proj", "alpha")
        self.assertEqual(
            git("branch", "--list", "alpha", cwd=self.source).stdout.strip(),
            "",
            "a branch a remote already holds must not be left in the shared checkout",
        )
        self.run_aw("new", "proj", "beta")
        (self.wt / "proj" / "beta" / "file.txt").write_text("two\n")
        git("commit", "-qam", "work", cwd=self.wt / "proj" / "beta")
        self.run_aw("rm", "proj", "beta", "--force")
        self.assertIn(
            "beta",
            git("branch", "--list", "beta", cwd=self.source).stdout,
            "a branch holding work no remote has must be kept",
        )

    def test_rm_clears_a_registration_a_plain_deletion_left_behind(self) -> None:
        self.run_aw("new", "proj", "alpha")
        shutil.rmtree(self.wt / "proj" / "alpha")  # what an agent does today
        code, out = self.run_aw("rm", "proj", "alpha")
        self.assertEqual(code, 0)
        self.assertIn("still registered it", out)
        listing = git("worktree", "list", "--porcelain", cwd=self.source).stdout
        self.assertNotIn(str(self.wt / "proj" / "alpha"), listing)

    def test_clearing_one_registration_leaves_another_live_one_alone(self) -> None:
        self.run_aw("new", "proj", "alpha")
        self.run_aw("new", "proj", "beta")
        shutil.rmtree(self.wt / "proj" / "alpha")
        self.run_aw("rm", "proj", "alpha")
        listing = git("worktree", "list", "--porcelain", cwd=self.source).stdout
        self.assertIn(str(self.wt / "proj" / "beta"), listing)

    # ------------------------------------------------------------------ the hazard

    def test_a_tree_whose_source_is_gone_is_named_with_what_to_do(self) -> None:
        self.run_aw("new", "proj", "alpha")
        (self.src / "proj").rename(self.src / "proj-moved")
        code, out = self.run_aw("list")
        self.assertEqual(code, 1)
        self.assertIn("cannot be used", out)
        self.assertIn(str(self.src / "proj"), out)

    def test_new_refuses_when_there_is_no_canonical_checkout(self) -> None:
        code, _ = self.run_aw("new", "absent", "alpha")
        self.assertEqual(code, 1)

    def test_new_refuses_to_cut_a_workspace_from_a_workspace(self) -> None:
        self.run_aw("new", "proj", "alpha")
        code, _ = self.run_aw("new", str(self.wt / "proj" / "alpha"), "beta")
        self.assertEqual(code, 1)

    # ------------------------------------------------------------------ jj

    @unittest.skipUnless(HAVE_JJ, "jj is not installed")
    def test_a_colocated_jj_workspace_is_removed_as_a_jj_workspace(self) -> None:
        subprocess.run(
            ["jj", "git", "init", "--colocate"], cwd=self.source, check=True, capture_output=True
        )
        code, _ = self.run_aw("new", "proj", "wsa")
        self.assertEqual(code, 0)
        self.assertEqual(AW.classify(self.wt / "proj" / "wsa")["kind"], "jj-workspace")
        code, _ = self.run_aw("rm", "proj", "wsa")
        self.assertEqual(code, 0)
        workspaces = subprocess.run(
            ["jj", "-R", str(self.source), "workspace", "list"],
            check=True, capture_output=True, text=True,
        ).stdout
        self.assertNotIn("wsa:", workspaces)
        listing = git("worktree", "list", "--porcelain", cwd=self.source).stdout
        self.assertNotIn(str(self.wt / "proj" / "wsa"), listing)


if __name__ == "__main__":
    unittest.main()
