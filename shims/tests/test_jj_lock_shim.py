#!/usr/bin/env python3
"""shims/jj against the real jj, in scratch colocated repositories."""
import os
import signal
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SHIMS = Path(__file__).parents[1]


def real_jj() -> Path:
    """The jj binary itself: the first jj on PATH that is neither shims/jj nor a mise shim (a link
    to mise, which reaches the binary only where mise has a jj version for the directory), else
    the one `mise which jj` names. A test that moves HOME needs the binary, not mise's shim."""
    mise_shim = False
    for d in os.environ["PATH"].split(os.pathsep):
        c = Path(d) / "jj"
        if not (c.is_file() and os.access(c, os.X_OK)) or b"jj-lock-shim" in c.read_bytes()[:512]:
            continue
        if c.resolve().name == "mise":
            mise_shim = True
            continue
        return c
    if mise_shim:
        found = subprocess.run(["mise", "which", "jj"], capture_output=True, text=True, check=False).stdout.strip()
        if found:
            return Path(found)
    raise unittest.SkipTest("no real jj on PATH")


def real_jj_dir() -> str:
    return str(real_jj().parent)


# The shim appends /usr/bin and /bin to PATH, so a jj there answers every call the tests below
# expect no jj to answer.
SYSTEM_JJ = [p for p in ("/usr/bin/jj", "/bin/jj") if os.path.exists(p)]


def run_bounded(argv: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """argv in a session of its own, so a shim that loops fails the test at the timeout and
    leaves no process behind: the workspace-add path forks a new subshell on every pass."""
    proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        out, err = proc.communicate(timeout=60)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        raise
    return subprocess.CompletedProcess(argv, proc.returncode, out, err)


# What mise's shim does when mise has no jj version for the directory: exec the first jj on PATH
# outside its own directory, which is shims/jj when the shims come first.
HAND_BACK = """#!/bin/bash
own="${BASH_SOURCE[0]%/*}"
IFS=':'
for d in $PATH; do
    [[ "$d" -ef "$own" ]] && continue
    [[ -x "$d/jj" ]] && exec "$d/jj" "$@"
done
echo "hand-back: no jj on PATH" >&2
exit 127
"""


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

    def hand_back_dir(self) -> Path:
        d = self.tmp / "hand-back"
        d.mkdir(exist_ok=True)
        (d / "jj").write_text(HAND_BACK, encoding="utf-8")
        (d / "jj").chmod(0o755)
        return d

    def jj(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return run_bounded(["jj", *args], cwd or self.repo, self.env)

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

    def test_a_jj_that_hands_the_call_back_is_skipped_for_the_jj_past_it(self):
        direct = subprocess.run([str(Path(real_jj_dir()) / "jj"), "log", "-r", "@", "--no-graph", "-T", "change_id"], cwd=self.repo, capture_output=True, text=True, check=True).stdout
        self.env["PATH"] = os.pathsep.join([str(SHIMS), str(self.hand_back_dir()), real_jj_dir(), os.environ["PATH"]])
        result = self.jj("log", "-r", "@", "--no-graph", "-T", "change_id")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, direct)

    def test_workspace_add_through_a_jj_that_hands_the_call_back_is_locked(self):
        self.env["PATH"] = os.pathsep.join([str(SHIMS), str(self.hand_back_dir()), real_jj_dir(), os.environ["PATH"]])
        result = self.jj("workspace", "add", "../ws-back", "--name", "back")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.worktrees()[str(self.tmp / "ws-back")])

    @unittest.skipIf(SYSTEM_JJ, f"{SYSTEM_JJ} would answer where this test needs no jj")
    def test_when_every_jj_hands_the_call_back_the_shim_fails_instead_of_looping(self):
        hand_back = self.hand_back_dir()
        home = self.tmp / "home"
        home.mkdir()
        # No real jj on PATH or in the shim's fallbacks under this HOME.
        env = {**self.env, "PATH": os.pathsep.join([str(SHIMS), str(hand_back)]), "HOME": str(home), "DOTFILES_DIR": str(home / ".dotfiles")}
        result = run_bounded(["jj", "--version"], self.repo, env)
        self.assertEqual(result.returncode, 127, result.stderr)
        self.assertIn(f"{hand_back / 'jj'} handed the call back", result.stderr)

    @unittest.skipIf(SYSTEM_JJ, f"{SYSTEM_JJ} would answer where this test needs no jj")
    def test_a_marker_from_another_pid_skips_nothing(self):
        # A nested jj (a git hook, `jj util exec -- jj …`) inherits the outer shim's marker under
        # another pid; pid 1 is never the shim. The real jj the marker names is the only jj on
        # PATH or in the shim's fallbacks, so skipping it would leave the call nothing to run.
        real = real_jj()
        direct = subprocess.run([str(real), "log", "-r", "@", "--no-graph", "-T", "change_id"], cwd=self.repo, capture_output=True, text=True, check=True).stdout
        home = self.tmp / "home"
        home.mkdir()
        env = {
            **self.env,
            "PATH": os.pathsep.join([str(SHIMS), str(real.parent)]),
            "HOME": str(home),
            "DOTFILES_DIR": str(home / ".dotfiles"),
            "DOTFILES_JJ_SHIM": f"1 {real}",
        }
        result = run_bounded(["jj", "log", "-r", "@", "--no-graph", "-T", "change_id"], self.repo, env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, direct)


if __name__ == "__main__":
    unittest.main()
