#!/usr/bin/env python3
"""Regression tests for agent-workspace: a second tree of one repository shares its objects.

The case these were written for: on 2026-10-30, eight children of
~/.worktrees/<task repo> each held an object store of its own, 19.0 GB for one
repository whose history is 4.8 GB, and the root filesystem of sami-rocqperf reached 14 MiB
free. The tests build the same shape in miniature and check the six things the provisioner
must get right: the default shares, the flag that does not is the flag, removal clears the
registration the source keeps, removal takes off the lock shims/git puts on an agent session's
tree and no other lock, a tree whose source is gone is named rather than handed over broken,
and a colocated jj workspace is removed as a jj workspace and not as a git worktree.
"""

import importlib.machinery
import importlib.util
import io
import os
import pwd
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "agent-workspace"
LOADER = importlib.machinery.SourceFileLoader("agent_workspace", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
AW = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(AW)
SHIMS = Path(__file__).resolve().parents[2] / "shims"
# shims/git tells an agent session by these, and by a gh-app-routes include in GIT_CONFIG_*.
AGENT_MARKERS = ("OMP_SESSION_ID", "PI_SESSION_FILE", "PI_TOOL_BRIDGE_URL", "AGENTBOX_SESSION")
# What shims/git writes on every `git worktree add` an agent session runs.
ADD_LOCK = "agent session: locked on add (AGENTC-431)"
# What shims/jj writes on the git worktree of a `jj workspace add` shims/git did not lock.
JJ_LOCK = "jj shim: locked on add (AGENTC-431)"

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
USER = pwd.getpwuid(os.getuid()).pw_name


def git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, env=GIT_ENV, check=True, capture_output=True, text=True
    )


class WorkspaceCase(unittest.TestCase):
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
        # The suite itself often runs INSIDE an agent box, whose AGENTBOX_* variables would
        # otherwise decide where these trees go. Each case says for itself whether it is in a box.
        box_env = {k: os.environ.get(k) for k in ("AGENTBOX_BOX", "AGENTBOX_DIRS", "AGENTBOX_IDENTITY_FILE")}
        os.environ.pop("AGENTBOX_BOX", None)
        os.environ.pop("AGENTBOX_DIRS", None)
        os.environ["AGENTBOX_IDENTITY_FILE"] = str(self.base / "no-identity-file")

        def restore_box() -> None:
            for key, value in box_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.addCleanup(restore_box)

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


class AgentWorkspaceTest(WorkspaceCase):
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


class WorkspaceRootTest(WorkspaceCase):
    """Which root a new tree lands in, and that a tree in the old one keeps working.

    The root filesystem of sami-rocqperf, 495 GiB shared by about a hundred sessions, reached
    about 1 GiB free at 05:19Z on 2026-11-01 from new checkouts alone, so the default root is
    the machine's big disk. Nothing already under ~/.worktrees is moved: `git worktree move`
    across devices fails with `Invalid cross-device link`.
    """

    def setUp(self) -> None:
        super().setUp()
        # The default rule is what is under test, so the override the other tests use is gone.
        os.environ.pop("AGENT_WORKSPACE_ROOT", None)
        self.home = self.base / "home"
        self.legacy = self.home / ".worktrees"
        self.legacy.mkdir(parents=True)
        self.nvme = self.base / "mnt-nvme"
        self.big_disk = self.nvme / USER
        self.big = self.big_disk / "worktrees"
        old_home, old_nvme = os.environ.get("HOME"), AW.NVME
        os.environ["HOME"] = str(self.home)
        AW.NVME = self.nvme

        def restore() -> None:
            AW.NVME = old_nvme
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home

        self.addCleanup(restore)

    def test_new_lands_on_the_big_disk_when_the_machine_has_one(self) -> None:
        self.big_disk.mkdir(parents=True)
        code, out = self.run_aw("new", "proj", "alpha")
        self.assertEqual(code, 0)
        tree = self.big / "proj" / "alpha"
        self.assertTrue((tree / "file.txt").is_file(), "the tree was not made on the big disk")
        self.assertEqual(out.strip().splitlines()[-1], str(tree), "new must print the path it made")

    def test_new_falls_back_to_the_home_root_without_that_disk(self) -> None:
        self.assertFalse(self.big_disk.exists())
        code, out = self.run_aw("new", "proj", "alpha")
        self.assertEqual(code, 0)
        tree = self.legacy / "proj" / "alpha"
        self.assertTrue((tree / "file.txt").is_file())
        self.assertEqual(out.strip().splitlines()[-1], str(tree))

    @unittest.skipIf(os.getuid() == 0, "root writes through a mode that denies everyone else")
    def test_a_big_disk_this_user_cannot_write_is_not_used(self) -> None:
        self.big_disk.mkdir(parents=True)
        self.big_disk.chmod(0o500)
        self.addCleanup(self.big_disk.chmod, 0o700)
        code, _ = self.run_aw("new", "proj", "alpha")
        self.assertEqual(code, 0)
        self.assertTrue((self.legacy / "proj" / "alpha" / "file.txt").is_file())

    def test_the_override_still_wins_over_both(self) -> None:
        self.big_disk.mkdir(parents=True)
        os.environ["AGENT_WORKSPACE_ROOT"] = str(self.base / "elsewhere")
        self.assertEqual(AW.ws_roots(), [self.base / "elsewhere"])
        code, _ = self.run_aw("new", "proj", "alpha")
        self.assertEqual(code, 0)
        self.assertTrue((self.base / "elsewhere" / "proj" / "alpha" / "file.txt").is_file())

    def test_list_and_rm_find_a_tree_under_the_legacy_root(self) -> None:
        self.run_aw("new", "proj", "alpha")  # made before the big disk existed
        tree = self.legacy / "proj" / "alpha"
        self.assertTrue(tree.is_dir())
        self.big_disk.mkdir(parents=True)  # from here the big disk is the default
        self.assertEqual(AW.ws_root(), self.big)

        code, out = self.run_aw("list")
        self.assertEqual(code, 0)
        self.assertIn(str(self.legacy / "proj"), out)
        self.assertIn("legacy root", out)

        code, out = self.run_aw("rm", "proj", "alpha")
        self.assertEqual(code, 0, out)
        self.assertFalse(tree.exists(), "rm did not find the tree under the legacy root")
        listing = git("worktree", "list", "--porcelain", cwd=self.source).stdout
        self.assertNotIn(str(tree), listing)

    def test_rm_clears_a_legacy_registration_a_plain_deletion_left_behind(self) -> None:
        self.run_aw("new", "proj", "alpha")
        tree = self.legacy / "proj" / "alpha"
        shutil.rmtree(tree)
        self.big_disk.mkdir(parents=True)
        code, out = self.run_aw("rm", "proj", "alpha")
        self.assertEqual(code, 0, out)
        self.assertIn("still registered it", out)
        listing = git("worktree", "list", "--porcelain", cwd=self.source).stdout
        self.assertNotIn(str(tree), listing)


class BoxWorkspaceRootTest(WorkspaceCase):
    """In an agent box a new workspace must land on a HOST path.

    A box's `$HOME` and `/tmp` are its own disk image and its `/mnt/nvme/<user>` may be its own
    container layer; all of it dies with the container, and a box is given only the host
    directories it was launched with (`AGENTBOX_DIRS`). On 2026-11-04 a session in a box had 7
    workspaces under `~/.worktrees`, every one of them on the box's private disk.
    """

    def setUp(self) -> None:
        super().setUp()
        os.environ.pop("AGENT_WORKSPACE_ROOT", None)
        self.home = self.base / "home"
        self.legacy = self.home / ".worktrees"
        self.legacy.mkdir(parents=True)
        self.nvme = self.base / "mnt-nvme"  # no big disk in this box
        self.bind = self.base / "bound-lane"
        self.bind.mkdir()
        old = {k: os.environ.get(k) for k in ("HOME", "AGENTBOX_BOX", "AGENTBOX_DIRS")}
        old_nvme = AW.NVME
        os.environ["HOME"] = str(self.home)
        os.environ["AGENTBOX_BOX"] = "agentbox-0123456789ab"
        os.environ["AGENTBOX_DIRS"] = str(self.bind)
        AW.NVME = self.nvme

        def restore() -> None:
            AW.NVME = old_nvme
            for key, value in old.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.addCleanup(restore)

    def test_a_new_workspace_lands_under_a_bound_dir_not_the_box_s_own_home(self) -> None:
        self.assertEqual(AW.ws_root(), self.bind / "worktrees")
        code, out = self.run_aw("new", "proj", "alpha")
        self.assertEqual(code, 0, out)
        tree = self.bind / "worktrees" / "proj" / "alpha"
        self.assertTrue((tree / "file.txt").is_file(), out)
        self.assertFalse((self.legacy / "proj").exists(), "a box wrote to its own private home")

    def test_a_bound_dir_that_is_not_a_checkout_is_preferred(self) -> None:
        checkout = self.base / "bound-checkout"
        (checkout / ".git").mkdir(parents=True)
        os.environ["AGENTBOX_DIRS"] = f"{checkout}:{self.bind}"
        self.assertEqual(AW.ws_root(), self.bind / "worktrees")

    def test_a_box_with_no_host_directory_refuses_and_says_what_to_set(self) -> None:
        os.environ["AGENTBOX_DIRS"] = ""
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit) as caught:
            AW.ws_root()
        self.assertEqual(int(caught.exception.code or 0), 1)
        self.assertIn("AGENT_WORKSPACE_ROOT", err.getvalue())
        self.assertIn("agent box", err.getvalue())

    def test_the_override_is_still_obeyed_in_a_box(self) -> None:
        os.environ["AGENT_WORKSPACE_ROOT"] = str(self.base / "elsewhere")
        self.assertEqual(AW.ws_root(), self.base / "elsewhere")

    def test_a_tree_already_under_the_box_home_is_still_found(self) -> None:
        os.environ["AGENT_WORKSPACE_ROOT"] = str(self.legacy)
        self.run_aw("new", "proj", "alpha")
        os.environ.pop("AGENT_WORKSPACE_ROOT")
        self.assertIn(self.legacy, AW.ws_roots())
        code, listing = self.run_aw("list")
        self.assertEqual(code, 0, listing)
        self.assertIn(str(self.legacy / "proj"), listing)
        self.assertIn("alpha", listing)


class AddLockTest(WorkspaceCase):
    """`rm` takes off the two agent add-locks, wherever it runs, and no other lock.

    shims/git locks every `git worktree add` of an agent session, `agent-workspace new` included,
    and shims/jj locks the git worktree of a `jj workspace add` the git shim did not lock; each
    shim lets a `git worktree remove` past either lock only inside an agent session.
    On 2026-11-01 the root-disk sweep ran `agent-workspace rm` from a systemd unit, where the shim
    sees no session, and git refused nearly every tree an agent had made with `cannot remove a
    locked working tree` (728 of the 871 registrations of one canonical checkout carried it).
    """

    def setUp(self) -> None:
        super().setUp()
        config_env = re.compile(r"GIT_CONFIG_(COUNT|KEY_\d+|VALUE_\d+)")
        outside = {
            k: v for k, v in GIT_ENV.items() if k not in AGENT_MARKERS and not config_env.fullmatch(k)
        }
        # This checkout's shims first, as a session's PATH and the sweep's unit both have them.
        outside["PATH"] = f"{SHIMS}{os.pathsep}{outside['PATH']}"
        outside["GIT_OPTIONAL_LOCKS"] = "0"
        self.outside = outside
        self.inside = {**outside, "OMP_SESSION_ID": "01a00000-0000-7000-8000-000000000000"}

    def aw(self, env: dict[str, str], *argv: str) -> tuple[int, str]:
        """Run agent-workspace with every git it starts seeing `env`; stderr joins the output."""
        err = io.StringIO()
        with mock.patch.object(AW, "GIT_ENV", env), redirect_stderr(err):
            code, out = self.run_aw(*argv)
        return code, out + err.getvalue()

    def locks(self) -> dict[str, str | None]:
        """git's view: each registered tree, and its lock reason (None when it is not locked)."""
        listing = git("worktree", "list", "--porcelain", cwd=self.source).stdout
        regs: dict[str, str | None] = {}
        for block in listing.strip().split("\n\n"):
            lines = block.splitlines()
            path = lines[0].removeprefix("worktree ")
            regs[path] = next((line.removeprefix("locked").strip() for line in lines if line.startswith("locked")), None)
        return regs

    def test_rm_removes_a_tree_an_agent_session_made_from_inside_a_session_or_outside_one(self) -> None:
        for where, env in (("inside", self.inside), ("outside", self.outside)):
            with self.subTest(rm_runs=where):
                tree = self.wt / "proj" / f"made-in-session-rm-{where}"
                code, out = self.aw(self.inside, "new", "proj", tree.name)
                self.assertEqual(code, 0, out)
                self.assertEqual(self.locks()[str(tree)], ADD_LOCK, "shims/git did not lock the add")
                code, out = self.aw(env, "rm", "proj", tree.name)
                self.assertEqual(code, 0, out)
                self.assertFalse(tree.exists())
                self.assertNotIn(str(tree), self.locks(), "the registration and its lock must go with the tree")

    def test_rm_releases_both_agent_add_locks_inside_a_session_and_outside_one(self) -> None:
        for n, (reason, (where, env)) in enumerate(
            (r, e) for r in (ADD_LOCK, JJ_LOCK) for e in (("inside", self.inside), ("outside", self.outside))
        ):
            with self.subTest(reason=reason, rm_runs=where):
                tree = self.wt / "proj" / f"agent-lock-{n}"
                code, out = self.aw(self.outside, "new", "proj", tree.name)
                self.assertEqual(code, 0, out)
                git("worktree", "lock", "--reason", reason, str(tree), cwd=self.source)
                code, out = self.aw(env, "rm", "proj", tree.name)
                self.assertEqual(code, 0, out)
                self.assertFalse(tree.exists())
                self.assertNotIn(str(tree), self.locks())

    def test_rm_leaves_every_other_lock_and_its_tree_inside_a_session_and_outside_one(self) -> None:
        # The second reason names the same issue and is still neither agent add-lock.
        for n, (reason, (where, env)) in enumerate(
            (r, e)
            for r in ("a person is using this tree", "held for the AGENTC-431 review")
            for e in (("inside", self.inside), ("outside", self.outside))
        ):
            with self.subTest(reason=reason, rm_runs=where):
                tree = self.wt / "proj" / f"held-{n}"
                code, out = self.aw(self.outside, "new", "proj", tree.name)
                self.assertEqual(code, 0, out)
                git("worktree", "lock", "--reason", reason, str(tree), cwd=self.source)
                code, out = self.aw(env, "rm", "--force", "proj", tree.name)
                self.assertEqual(code, 1, out)
                self.assertIn("locked", out)
                self.assertTrue((tree / "file.txt").is_file(), "a tree someone else locked was removed")
                self.assertEqual(self.locks()[str(tree)], reason)

    def test_a_removal_git_refuses_leaves_the_add_lock_on(self) -> None:
        # Moved by hand without `git worktree repair`: the registration still names the old path,
        # so git finds no worktree at the new one and removes nothing.
        tree = self.wt / "proj" / "alpha"
        code, out = self.aw(self.inside, "new", "proj", "alpha")
        self.assertEqual(code, 0, out)
        moved = tree.with_name("alpha-moved")
        tree.rename(moved)
        code, out = self.aw(self.outside, "rm", str(moved))
        self.assertEqual(code, 1, out)
        self.assertEqual(self.locks()[str(tree)], ADD_LOCK, "a tree still registered was left unlocked")


if __name__ == "__main__":
    unittest.main()
