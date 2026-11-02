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
# The porcelain line of the lock shims/git puts on every `git worktree add` of an agent session.
ADD_LOCK = "locked agent session: locked on add (AGENTC-431)"


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

    def added(self, name: str) -> Path:
        """A tree an agent session added, so it carries the shim's add-lock."""
        path = self.tmp / name
        self.assertEqual(self.git("worktree", "add", "-q", "--detach", str(path)).returncode, 0)
        self.assertEqual(self.registrations()[str(path)], ADD_LOCK)
        return path

    def test_a_removal_git_refuses_keeps_the_add_lock(self):
        # Uncommitted work and no --force: git refuses, and the tree keeps the lock it was added with.
        path = self.added("dirty")
        (path / "work.txt").write_text("mine\n")
        result = self.git("worktree", "remove", str(path))
        self.assertEqual(result.returncode, 128)
        self.assertIn("contains modified or untracked files, use --force to delete it", result.stderr)
        self.assertTrue((path / "work.txt").is_file())
        self.assertEqual(self.registrations()[str(path)], ADD_LOCK, "a refused removal left the tree unlocked")

    def test_a_tree_holding_a_submodule_is_still_refused_without_force(self):
        # git refuses such a tree unless --force says otherwise; the double --force that takes the
        # add-lock along must not take that refusal away, or the submodule's work is deleted.
        sub = self.tmp / "sub"
        sub.mkdir()
        for cmd in (["git", "init", "-q"], ["git", "commit", "-q", "--allow-empty", "-m", "sub"]):
            subprocess.run(cmd, cwd=sub, env=self.plain, check=True, capture_output=True)
        path = self.added("withsub")
        for cmd in (
            ["git", "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub), "s"],
            ["git", "commit", "-q", "-m", "add the submodule"],
        ):
            subprocess.run(cmd, cwd=path, env=self.plain, check=True, capture_output=True)
        result = self.git("worktree", "remove", str(path))
        self.assertEqual(result.returncode, 128)
        self.assertIn("working trees containing submodules cannot be moved or removed", result.stderr)
        self.assertTrue((path / "s" / ".git").exists())
        self.assertEqual(self.registrations()[str(path)], ADD_LOCK)

    def test_a_forced_removal_takes_a_dirty_tree_and_its_add_lock(self):
        path = self.added("dirty")
        (path / "work.txt").write_text("mine\n")
        result = self.git("worktree", "remove", "--force", str(path))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(path.exists())
        self.assertNotIn(str(path), self.registrations())

    def test_only_the_shims_own_lock_reasons_are_let_past(self):
        # Both agent add-locks, this shim's and shims/jj's, are the shims' own; a reason that only
        # mentions the issue is not.
        for reason, removed in (
            ("agent session: locked on add (AGENTC-431)", True),
            ("jj shim: locked on add (AGENTC-431)", True),
            ("held for the AGENTC-431 review", False),
        ):
            with self.subTest(reason=reason):
                path = self.tmp / f"held-{len(reason)}"
                self.git("worktree", "add", "-q", "--detach", "--lock", "--reason", reason, str(path))
                result = self.git("worktree", "remove", str(path))
                self.assertEqual(result.returncode == 0, removed, result.stderr)
                self.assertEqual(str(path) in self.registrations(), not removed)

    def test_a_tree_named_from_where_every_dash_c_leaves_git_is_seen(self):
        # git applies each -C in order (an empty one changes nothing) and reads a relative worktree
        # path from where they leave it, not from the caller's directory.
        deeper = self.tmp / "elsewhere" / "deeper"
        deeper.mkdir(parents=True)
        for name, dash_c in (("one", ["-C", "../../repo"]), ("several", ["-C", "", "-C", "../..", "-C", "repo"])):
            with self.subTest(dash_c=dash_c):
                path = self.added(name)
                result = self.git(*dash_c, "worktree", "remove", f"../{name}", cwd=deeper)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(path.exists())
                self.assertNotIn(str(path), self.registrations())

    def test_a_tree_named_by_the_end_of_its_path_is_seen(self):
        # git looks a worktree up first by a trailing part of its path that names one tree only.
        for name in ("named", "nest/inner"):
            with self.subTest(name=name):
                path = self.added(name)
                result = self.git("worktree", "remove", name)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(path.exists())
                self.assertNotIn(str(path), self.registrations())

    def test_a_name_two_trees_end_in_removes_neither(self):
        first, second = self.added("a/same"), self.added("b/same")
        result = self.git("worktree", "remove", "same")
        self.assertEqual(result.returncode, 128)
        self.assertIn("is not a working tree", result.stderr)
        for path in (first, second):
            self.assertTrue(path.is_dir())
            self.assertEqual(self.registrations()[str(path)], ADD_LOCK)

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
