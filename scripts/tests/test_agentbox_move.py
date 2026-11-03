#!/usr/bin/env python3
"""`agentbox move` takes the sessions already running on this host into boxes (dispatch://INFRA-702).

`agentbox new` starts a box for a NEW session. `move` reads every live top-level omp outside a
box, works out the box each one would get, and relaunches its pane into `agentbox new <spec> --
omp --resume <id>`, waking it over Envoy. The three things that decide whether a window of about a hundred sessions
is safe are tested here:

* the SPEC a working directory gets. A move never names a repo: a clone would give the session a
  tree that is not the one it has (no uncommitted changes, no index, and none of the other 17
  sessions' work in the same checkout). `$SRC` is already bind-mounted into every box, so a
  checkout or a worktree under it needs no `--dir` beyond the working directory itself, while a
  tree outside `$SRC` needs its root and its git common dir named.
* the CLASS of each omp process: a session a move takes, a top-level omp with no tmux pane that
  only a person can decide, one already in a box, one inside a grading sandbox, and a subagent of
  another omp, which is not a session at all.
* the two refusals that stop a window going wrong: a session whose full id cannot be read (a
  prefix would resume another session) and a disk that the new box would take under its floor.

Technique, as test_agentbox_dirs.py: source `scripts/agentbox` (its source guard runs nothing)
with the host tools stubbed first on PATH, and point `AGENTBOX_PROC` at a fixture /proc tree.
"""

from __future__ import annotations

import fcntl
import json
import os
import stat
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
AGENTBOX = DOTFILES / "scripts" / "agentbox"


def write_stub(directory: Path, name: str, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class AgentboxMove(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.src = self.root / "src"
        self.src.mkdir()
        self.boxes = self.root / "boxes"
        self.boxes.mkdir(parents=True)
        self.proc = self.root / "proc"
        self.proc.mkdir()
        self.sessions = self.root / "sessions"
        self.sessions.mkdir()
        self.plan_dir = self.root / "plan"
        self.stubs = self.root / "bin"
        self.stubs.mkdir()
        # Nothing in these tests may reach a real host: every census input is a stub.
        write_stub(self.stubs, "pgrep", 'cat "$STUB_PGREP" 2>/dev/null; exit 0')
        write_stub(self.stubs, "tmux", 'cat "$STUB_TMUX" 2>/dev/null; exit 0')
        write_stub(self.stubs, "docker", 'cat "$STUB_DOCKER" 2>/dev/null; exit 0')
        write_stub(self.stubs, "envoy", 'printf "%s\\n" "$@" >>"$STUB_ENVOY"; exit 0')
        for name in ("STUB_PGREP", "STUB_TMUX", "STUB_DOCKER", "STUB_ENVOY"):
            (self.root / name).write_text("", encoding="utf-8")
        self.env = {
            "HOME": str(self.home),
            "PATH": f"{self.stubs}:/usr/bin:/bin",
            "DOTFILES_DIR": str(DOTFILES),
            "AGENTBOX_SRC": str(self.src),
            "AGENTBOX_BOXES": str(self.boxes),
            "AGENTBOX_PROC": str(self.proc),
            "AGENTBOX_DISK": "0",
            # The session transcripts the record reader walks, the window it reads, and where
            # `move --plan` leaves what it inferred for `move --self` to union with.
            "AGENTBOX_SESSION_ROOTS": str(self.sessions),
            "AGENTBOX_MOVE_SINCE": "2026-01-01T00:00:00Z",
            "AGENTBOX_PLAN_DIR": str(self.plan_dir),
            # The roots that hold many sessions' trees, pinned to the fixture's own home: this
            # host's TMPDIR is itself under /mnt/nvme/<user>, one of the real ones, so without
            # this the suite's answers would depend on where the temporary tree happens to be.
            "AGENTBOX_MOVE_ROOTS": ":".join(
                str(self.home / p) for p in ("Code", "Code/personal", ".worktrees", "tmp")
            ),
            # The per-session TMPDIR root (scripts/agent-session), pinned to the fixture: this
            # host's real root holds the suite's own temporary tree.
            "AGENTBOX_SESSION_TMP": str(self.root / "session-tmp"),
            # The durable disk every box binds whole, pinned to the fixture for the same reason.
            "AGENTBOX_DURABLE": str(self.root / "durable"),
            "STUB_PGREP": str(self.root / "STUB_PGREP"),
            "STUB_TMUX": str(self.root / "STUB_TMUX"),
            "STUB_DOCKER": str(self.root / "STUB_DOCKER"),
            "STUB_ENVOY": str(self.root / "STUB_ENVOY"),
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # ── fixtures ────────────────────────────────────────────────────────────────────────────
    @staticmethod
    def end_relauncher(proc: subprocess.Popen[bytes]) -> None:
        """A move_to_run a test started, and everything it started, ended by its own process group:
        a test that fails or times out must leave nothing running on this host."""
        if proc.poll() is not None:
            return
        for signal_number in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(proc.pid, signal_number)
            except ProcessLookupError:
                break
            try:
                proc.wait(timeout=20)
                break
            except subprocess.TimeoutExpired:
                continue

    def make_proc(
        self,
        pid: int,
        *,
        comm: str = "omp",
        ppid: int = 1,
        cwd: Path | str = "/",
        session: str | None = None,
        cgroup: str = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/omp.scope",
        cmdline: str = "omp",
        writes: tuple[Path | str, ...] = (),
        reads: tuple[Path | str, ...] = (),
        environ: dict[str, str] | None = None,
        session_leader: bool = False,
    ) -> None:
        """One process in the fixture /proc: stat, comm, cmdline, cgroup, cwd and its open files.

        `writes` and `reads` are files the process holds open; each gets an fd symlink and the
        `flags:` line of /proc/<pid>/fdinfo/<fd> that says which, since only a file open for
        WRITING names a directory the session's box must be given."""
        directory = self.proc / str(pid)
        (directory / "fd").mkdir(parents=True)
        (directory / "fdinfo").mkdir(parents=True)
        # Field 4 after the state is the session id; its own pid means `setsid`-detached.
        (directory / "stat").write_text(
            f"{pid} ({comm}) S {ppid} {ppid} {pid if session_leader else 0} 0 -1 0\n", encoding="utf-8")
        (directory / "comm").write_text(comm + "\n", encoding="utf-8")
        (directory / "cmdline").write_text(cmdline.replace(" ", "\0") + "\0", encoding="utf-8")
        (directory / "cgroup").write_text(cgroup + "\n", encoding="utf-8")
        (directory / "environ").write_text("".join(f"{k}={v}\0" for k, v in (environ or {}).items()), encoding="utf-8")
        os.symlink(str(cwd), directory / "cwd")
        fd = 30
        for path, flags in [(p, "0100002") for p in writes] + [(p, "0100000") for p in reads]:
            os.symlink(str(path), directory / "fd" / str(fd))
            (directory / "fdinfo" / str(fd)).write_text(f"pos:\t0\nflags:\t{flags}\n", encoding="utf-8")
            fd += 1
        if session:
            transcript = self.home / ".omp/agent/sessions/-work" / f"2026-11-01T09:00:00Z_{session}.jsonl"
            transcript.parent.mkdir(parents=True, exist_ok=True)
            transcript.write_text("", encoding="utf-8")
            os.symlink(str(transcript), directory / "fd" / "21")
            # omp holds its own transcript open for writing; ~/.omp is in the home allow-list,
            # so the inference must leave it out of every spec.
            (directory / "fdinfo" / "21").write_text("pos:\t0\nflags:\t0100002\n", encoding="utf-8")

    def set_pids(self, *pids: int) -> None:
        (self.root / "STUB_PGREP").write_text("".join(f"{pid}\n" for pid in pids), encoding="utf-8")

    def set_panes(self, panes: dict[int, tuple[str, str]]) -> None:
        (self.root / "STUB_TMUX").write_text(
            "".join(f"{pid}\t{pane}\t{at}\n" for pid, (pane, at) in panes.items()), encoding="utf-8"
        )

    def session_file(self, session: str) -> Path:
        """Where make_proc(session=...) puts the session's transcript, the path a move resumes by."""
        return self.home / ".omp/agent/sessions/-work" / f"2026-11-01T09:00:00Z_{session}.jsonl"

    def set_containers(self, containers: dict[str, str]) -> None:
        (self.root / "STUB_DOCKER").write_text(
            "".join(f"{cid} {name}\n" for cid, name in containers.items()), encoding="utf-8"
        )

    def git(self, *args: str, cwd: Path) -> None:
        subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"},
        )

    def checkout(self, name: str, parent: Path | None = None) -> Path:
        """A git checkout with one commit, under $SRC unless another parent is given."""
        path = (parent or self.src) / name
        path.mkdir(parents=True)
        self.git("init", "-q", "-b", "main", cwd=path)
        self.git("config", "user.email", "t@example.com", cwd=path)
        self.git("config", "user.name", "T", cwd=path)
        (path / "file").write_text("x\n", encoding="utf-8")
        self.git("add", "file", cwd=path)
        self.git("commit", "-qm", "one", cwd=path)
        return path

    # A run of the script under test. The cap is generous because this host carries ~100 agent
    # sessions: at load 300 a move that takes 20 s idle takes minutes, and a cap that fits an
    # idle host fails tests for the load alone (AGENTBOX_TEST_TIMEOUT to change it).
    TIMEOUT = float(os.environ.get("AGENTBOX_TEST_TIMEOUT", "900"))

    def run_bash(self, script: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        with subprocess.Popen(
            ["bash", "-c", f'source "{AGENTBOX}"\n{script}'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={**self.env, **(extra_env or {})}, start_new_session=True,
        ) as proc:
            try:
                out, err = proc.communicate(timeout=self.TIMEOUT)
            except subprocess.TimeoutExpired:
                # Nothing of a test outlives it: a move_to_run and its children die with it.
                self.end_relauncher(proc)
                raise
        return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)

    def census(self) -> list[list[str]]:
        result = self.run_bash("move_census")
        self.assertEqual(result.returncode, 0, result.stderr)
        # A census row's fields are unit-separated: a tab is IFS whitespace, which folds two
        # separators into one and shifts every field after an empty one.
        return [line.split("\x1f") for line in result.stdout.splitlines()]

    # ── the spec a working directory gets ───────────────────────────────────────────────────
    def test_a_canonical_checkout_is_named_as_its_own_directory_and_nothing_else(self) -> None:
        """$SRC is in every box already, so a checkout under it needs only its own path."""
        path = self.checkout("repo1")
        result = self.run_bash(f'move_dirs "{path}"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), [str(path)])

    def test_a_subdirectory_of_a_checkout_is_named_as_itself(self) -> None:
        path = self.checkout("repo1")
        (path / "sub").mkdir()
        result = self.run_bash(f'move_dirs "{path}/sub"')
        self.assertEqual(result.stdout.split(), [f"{path}/sub"])

    def test_a_worktree_outside_src_also_names_the_checkout_that_owns_it(self) -> None:
        """A worktree's .git is a file naming a common dir in another tree; without that tree
        the box has the working files and no repository."""
        checkout = self.checkout("repo1")
        tree = self.root / "worktrees" / "feature"
        self.git("worktree", "add", "-q", "-b", "feature", str(tree), cwd=checkout)
        owner = self.checkout("repo2", parent=self.root / "elsewhere")
        outside = self.root / "elsewhere" / "wt"
        self.git("worktree", "add", "-q", "-b", "feature", str(outside), cwd=owner)
        # A worktree of a checkout under $SRC: only its own path, since $SRC carries the owner.
        result = self.run_bash(f'move_dirs "{tree}"')
        self.assertEqual(result.stdout.split(), [str(tree)])
        # A worktree of a checkout outside $SRC: its own path first, then the owning checkout.
        result = self.run_bash(f'move_dirs "{outside}"')
        self.assertEqual(result.stdout.split(), [str(outside), str(owner)])

    def test_a_directory_that_is_not_a_repository_is_named_as_itself(self) -> None:
        lane = self.root / "lane"
        (lane / "agents").mkdir(parents=True)
        result = self.run_bash(f'move_dirs "{lane}"')
        self.assertEqual(result.stdout.split(), [str(lane)])

    def test_the_working_directories_a_box_cannot_be_given_are_refused_with_the_reason(self) -> None:
        gone = self.root / "gone"
        for path, reason in (
            ("/", "whole host filesystem"),
            (str(self.home), "shadow the box's own home"),
            (f"{self.boxes}/agentbox-aaaaaaaaaaaa", "already works inside the box tree"),
            (str(gone), "is gone"),
            ("", "cannot be read"),
        ):
            with self.subTest(path=path):
                if path.startswith(str(self.boxes)):
                    Path(path).mkdir(parents=True, exist_ok=True)
                result = self.run_bash(f'move_dirs "{path}"')
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertIn(reason, result.stderr)

    # ── the paths inferred from the session's own processes ─────────────────────────────────
    def test_a_live_child_s_working_directory_is_in_the_spec_with_its_reason(self) -> None:
        """The cwd of the omp is not the tree a lane works in: 20 sessions whose cwd was
        ~/src/oss-verifier worked in /mnt/nvme/sami/oss-verifier/... A path a box is not given is
        silently writable inside it and lost with it, so every directory the session's own
        processes use is inferred, each with why it was chosen."""
        lane = self.home / "lane"
        lane.mkdir()
        work = self.home / "oss-verifier" / "run-14"
        work.mkdir(parents=True)
        self.make_proc(400, comm="omp", ppid=1, cwd=lane)
        self.make_proc(401, comm="bash", ppid=400, cwd=work)
        result = self.run_bash(f'move_dirs_why "{lane}" 400')
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = [line.split("\t") for line in result.stdout.splitlines()]
        self.assertEqual([row[0] for row in rows], [str(lane), str(work)])
        self.assertEqual(rows[0][1], "its own working directory")
        self.assertIn("working directory of bash (pid 401)", rows[1][1])

    def test_a_file_a_session_holds_open_for_writing_names_its_directory(self) -> None:
        lane = self.home / "lane"
        lane.mkdir()
        written = self.home / "evidence" / "lane-7"
        written.mkdir(parents=True)
        (written / "out.log").write_text("", encoding="utf-8")
        only_read = self.home / "reference" / "notes"
        only_read.mkdir(parents=True)
        (only_read / "spec.md").write_text("", encoding="utf-8")
        self.make_proc(410, comm="omp", ppid=1, cwd=lane)
        self.make_proc(
            411,
            comm="cargo",
            ppid=410,
            cwd=lane,
            writes=(written / "out.log",),
            reads=(only_read / "spec.md",),
        )
        result = self.run_bash(f'move_dirs_why "{lane}" 410')
        rows = [line.split("\t") for line in result.stdout.splitlines()]
        self.assertEqual([row[0] for row in rows], [str(lane), str(written)])
        self.assertIn("cargo (pid 411) holds out.log open for writing", rows[1][1])
        # A file only READ is in no spec: reading it inside the box is the box's problem, and
        # mounting every library and config a tool opens would mount the host.
        self.assertNotIn(str(only_read), result.stdout)

    def test_a_path_every_box_has_already_is_never_named(self) -> None:
        """$SRC and the home allow-list (agentbox/mounts) are bind-mounted into every box at
        their own paths, so naming one would change nothing. A worktree under
        ~/src/<repo>/.worktrees/... is in that class: it IS visible in a box with no --dir."""
        lane = self.home / "lane"
        lane.mkdir()
        checkout = self.checkout("repo1")
        inner = checkout / ".worktrees" / "feature"
        inner.mkdir(parents=True)
        (inner / "f.txt").write_text("", encoding="utf-8")
        dotfiles = self.home / ".dotfiles"
        dotfiles.mkdir()
        (dotfiles / "notes").write_text("", encoding="utf-8")
        self.make_proc(420, comm="omp", ppid=1, cwd=lane, session="01a19700-0000-7000-8000-0000000000aa")
        self.make_proc(421, comm="git", ppid=420, cwd=inner, writes=(dotfiles / "notes",))
        result = self.run_bash(f'move_dirs_why "{lane}" 420')
        self.assertEqual(result.stdout.splitlines(), [f"{lane}\tits own working directory"])

    def test_a_path_on_the_durable_disk_is_never_named(self) -> None:
        """Every box binds /mnt/durable/<user> whole, so a cache or a cold tree under it is in
        the box already, and naming it would bind it a second time."""
        lane = self.home / "lane"
        lane.mkdir()
        cache = self.root / "durable" / "knotcurve-pivot-cache"
        cache.mkdir(parents=True)
        (cache / "pivot.bin").write_text("", encoding="utf-8")
        cold = self.root / "durable" / "nvme-cold" / "old-lane"
        cold.mkdir(parents=True)
        self.make_proc(425, comm="omp", ppid=1, cwd=lane)
        self.make_proc(426, comm="python3", ppid=425, cwd=cold, writes=(cache / "pivot.bin",))
        result = self.run_bash(f'move_dirs_why "{lane}" 425')
        self.assertEqual(result.stdout.splitlines(), [f"{lane}\tits own working directory"], result.stderr)

    def test_a_root_many_sessions_share_is_never_mounted_whole(self) -> None:
        """~/Code, ~/.worktrees and /mnt/<user>{,/tmp,/worktrees,/scratch} hold many sessions'
        trees. A box gets the first directory below the deepest such root, never the root."""
        lane = self.home / "lane"
        lane.mkdir()
        deep = self.home / "Code" / "personal" / "theorem" / "gates" / "g1"
        deep.mkdir(parents=True)
        tree = self.home / ".worktrees" / "legion" / "infra-140"
        tree.mkdir(parents=True)
        self.make_proc(430, comm="omp", ppid=1, cwd=lane)
        self.make_proc(431, comm="bash", ppid=430, cwd=deep)
        self.make_proc(432, comm="rg", ppid=431, cwd=tree)
        result = self.run_bash(f'move_dirs "{lane}" 430')
        self.assertEqual(
            result.stdout.split(),
            [str(lane), f"{self.home}/Code/personal/theorem", f"{self.home}/.worktrees/legion"],
        )

    def test_a_worktree_of_an_open_file_is_named_even_when_a_sibling_checkout_is_the_cwd(self) -> None:
        """Session 01a16640 on 2026-11-01: its cwd was ~/Code/legion, a real checkout of the same
        repository, while the trees it had open were elsewhere. The cwd alone would have given it
        a box whose `git status` answers about ANOTHER checkout."""
        cwd = self.checkout("legion", parent=self.home / "Code")
        owner = self.checkout("legion")
        inside_src = owner / ".worktrees" / "infra-140-s5"
        self.git("worktree", "add", "-q", "-b", "infra-140-s5", str(inside_src), cwd=owner)
        outside = self.home / "worktrees" / "legion" / "stopped-tree"
        self.git("worktree", "add", "-q", "-b", "stopped", str(outside), cwd=owner)
        (outside / "wip.rs").write_text("", encoding="utf-8")
        self.make_proc(440, comm="omp", ppid=1, cwd=cwd)
        self.make_proc(441, comm="bash", ppid=440, cwd=inside_src)
        self.make_proc(442, comm="rustc", ppid=440, cwd=self.home, writes=(outside / "wip.rs",))
        result = self.run_bash(f'move_dirs_why "{cwd}" 440')
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = [line.split("\t") for line in result.stdout.splitlines()]
        # The cwd stays first (a box works in its first --dir); the worktree under $SRC is in the
        # box already; the one outside arrives as its own tree root, not as the file's directory.
        self.assertEqual([row[0] for row in rows], [str(cwd), str(outside)])
        self.assertIn("rustc (pid 442) holds wip.rs open for writing", rows[1][1])
        # $HOME itself is the writer's cwd and is never a path a box is given.
        self.assertNotIn(f"{self.home}\t", result.stdout)

    def test_a_path_the_caller_names_is_added_after_the_inferred_ones(self) -> None:
        lane = self.home / "lane"
        lane.mkdir()
        child = self.home / "inferred"
        child.mkdir()
        named = self.home / "named-by-hand"
        named.mkdir()
        self.make_proc(450, comm="omp", ppid=1, cwd=lane)
        self.make_proc(451, comm="bash", ppid=450, cwd=child)
        result = self.run_bash(f'move_dirs_why "{lane}" 450 "" "{named}"')
        rows = [line.split("\t") for line in result.stdout.splitlines()]
        self.assertEqual([row[0] for row in rows], [str(lane), str(child), str(named)])
        self.assertEqual(rows[2][1], "you named it with --dir")

    def incident_session(self) -> tuple[Path, Path, Path]:
        """01a1798a at its 2026-11-02T10:42Z move, in the fixture: omp pid 53458 in its tree,
        its own TMPDIR session-tmp/53458-LivaHYZv (agent-session's `$$-XXXXXXXX`), where omp's
        python runner writes, and a lane tree it writes in too."""
        lane = self.home / "machcsl-riscv"
        lane.mkdir()
        scratch = self.root / "session-tmp" / "53458-LivaHYZv"
        runner = scratch / "omp-python-runner-1000"
        runner.mkdir(parents=True)
        (runner / "runner-2mgc0ebjdknhi.py").write_text("", encoding="utf-8")
        verified = self.home / "tmp" / "verified-linux" / "reviews"
        verified.mkdir(parents=True)
        (verified / "r.md").write_text("", encoding="utf-8")
        environ = {"AGENT_SESSION_SCRATCH": str(scratch), "TMPDIR": str(scratch)}
        self.make_proc(53458, comm="omp", ppid=1, cwd=lane, environ=environ)
        self.make_proc(53470, comm="python3", ppid=53458, cwd=runner, environ=environ,
                       writes=(runner / "runner-2mgc0ebjdknhi.py",))
        self.make_proc(53471, comm="bash", ppid=53458, cwd=lane, environ=environ, writes=(verified / "r.md",))
        return lane, scratch, verified

    def test_a_per_session_tmpdir_is_never_in_the_spec(self) -> None:
        """The 10:42Z spec named /mnt/nvme/sami/session-tmp/53458-LivaHYZv, which
        session-scratch-reap removes once the hung-up omp is gone, so the restarter's resume of
        that spec at 16:23Z was refused ("is not an existing directory")."""
        lane, scratch, verified = self.incident_session()
        result = self.run_bash(f'move_dirs "{lane}" 53458')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), [str(lane), str(self.home / "tmp" / "verified-linux")])
        self.assertNotIn(str(scratch), result.stdout)

    def test_the_omps_own_scratch_is_never_in_the_spec_wherever_it_is(self) -> None:
        """AGENT_SESSION_SCRATCH is agent-session's per-session directory, under the root or not
        (a nested launch inherits its parent session's): it dies with the session either way."""
        lane = self.home / "lane"
        lane.mkdir()
        scratch = self.home / "elsewhere" / "4242-AbCdEfGh"
        scratch.mkdir(parents=True)
        self.make_proc(4242, comm="omp", ppid=1, cwd=lane, environ={"AGENT_SESSION_SCRATCH": str(scratch)})
        self.make_proc(4243, comm="bash", ppid=4242, cwd=scratch)
        result = self.run_bash(f'move_dirs "{lane}" 4242')
        self.assertEqual(result.stdout.split(), [str(lane)], result.stderr)

    def test_a_session_working_in_or_naming_a_per_session_tmpdir_is_refused(self) -> None:
        """The working directory and a hand-named --dir are kept whatever the inference says, so
        either one in a TMPDIR that dies with the omp is a box no resume could start: refused."""
        lane, scratch, _verified = self.incident_session()
        in_it = self.run_bash(f'move_dirs "{scratch}" 53458')
        self.assertNotEqual(in_it.returncode, 0)
        self.assertIn("a per-session TMPDIR that is removed when this omp exits", in_it.stderr)
        named = self.run_bash(f'move_dirs "{lane}" 53458 "" "{scratch}"')
        self.assertNotEqual(named.returncode, 0)
        self.assertIn(f"--dir {scratch} is a per-session TMPDIR", named.stderr)

    def test_move_self_dir_is_refused_by_the_same_checks_agentbox_new_applies(self) -> None:
        lane = self.home / "lane"
        lane.mkdir()
        self.make_proc(460, comm="bash", ppid=1, cwd=lane)
        self.make_proc(461, comm="omp", ppid=460, cwd=lane, session="01a19700-0000-7000-8000-0000000000bb")
        self.set_pids(461)
        self.set_panes({460: ("%4", "agents:9.1")})
        write_stub(self.stubs, "df", 'if [[ "$*" == *target* ]]; then echo target; echo /; else echo avail; echo 214748364800; fi')
        for arg, reason in (
            (str(self.root / "absent"), "is not an existing directory"),
            ("/", "whole host filesystem"),
            (str(self.home), "shadow the box's own home"),
            (str(self.boxes), "inside the box tree"),
        ):
            with self.subTest(arg=arg):
                refused = self.run_bash(
                    f'move_own_omp() {{ echo 461; }}\ncmd_move_self --dry-run --dir "{arg}"'
                )
                self.assertEqual(refused.returncode, 1, refused.stdout)
                self.assertIn(reason, refused.stderr)
        named = self.home / "lane-evidence"
        named.mkdir()
        allowed = self.run_bash(
            'move_own_omp() { echo 461; }\nmove_image_bytes() { echo 280000000; }\n'
            f'cmd_move_self --dry-run --dir "{named}"'
        )
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertIn(f"--dir {lane} --dir {named} -- omp --resume", allowed.stdout)
        self.assertIn("you named it with --dir", allowed.stdout)

    def test_a_checkout_whose_objects_are_a_symlink_elsewhere_keeps_them(self) -> None:
        """`~/src/machcsl-riscv/.git/objects` on this host is a SYMLINK to
        /mnt/nvme/sami/git-objects/machcsl-riscv. A box given the tree and not the store answers
        `not a git repository` for every command in it, and `knives gh` fails with it (session
        01a1798a-995f, box agentbox-676432b23e44, 2026-11-01). $SRC is in every box, so the TREE
        is never in the spec and the store must be."""
        checkout = self.checkout("machcsl-riscv")
        store = self.home / "git-objects" / "machcsl-riscv"
        store.mkdir(parents=True)
        objects = checkout / ".git" / "objects"
        for child in list(objects.iterdir()):
            child.rename(store / child.name)
        objects.rmdir()
        objects.symlink_to(store)
        borrowed = self.home / "git-objects" / "shared-pool"
        borrowed.mkdir(parents=True)
        (store / "info").mkdir(parents=True, exist_ok=True)
        (store / "info" / "alternates").write_text(f"{borrowed}\n", encoding="utf-8")
        self.make_proc(495, comm="omp", ppid=1, cwd=checkout)
        result = self.run_bash(f'move_dirs_why "{checkout}" 495')
        self.assertEqual(result.returncode, 0, result.stderr)
        paths = [line.split("\t")[0] for line in result.stdout.splitlines()]
        # The store itself and the pool it borrows from, each at its own path.
        self.assertIn(str(store), paths)
        self.assertIn(str(borrowed), paths)
        self.assertIn("the git object store of", result.stdout)
        # git agrees the tree depends on that store: it is where its objects are.
        proof = subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", "--git-path", "objects"],
            capture_output=True,
            text=True,
            env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"},
        )
        self.assertEqual(proof.returncode, 0, proof.stderr)

    # ── the paths the session's own record names ────────────────────────────────────────────
    def transcript(self, session: str, calls: list[tuple[str, dict]], slug: str = "-lane") -> Path:
        """A session transcript holding those tool calls, where the record reader looks."""
        path = self.sessions / slug / f"2026-11-01T09:00:00Z_{session}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        for name, args in calls:
            lines.append(
                json.dumps(
                    {
                        "type": "message",
                        "timestamp": "2026-11-01T10:00:00.000Z",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "toolCall", "name": name, "arguments": args}],
                        },
                    }
                )
            )
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def test_a_dead_subagent_s_tree_is_found_in_the_session_s_own_record(self) -> None:
        """A supervisor writes almost nothing itself: its subagents do, and they have exited. A
        dead worker leaves no cwd and holds no open file, so a live scan sees none of its trees.
        Session 01a16220 on 2026-11-01 got two directories from a live scan and missed four.
        The record is the only source that sees them."""
        lane = self.home / "lane"
        lane.mkdir()
        pool = self.home / ".worktrees" / "agent-orchestration-owner" / "s1-pool"
        pool.mkdir(parents=True)
        # A real tree, so its own root is the boundary and not the shared root above it.
        (pool / ".git").mkdir()
        verified = self.home / "tmp" / "verified-linux"
        verified.mkdir(parents=True)
        (verified / "state.json").write_text("{}", encoding="utf-8")
        infra = self.home / "infra132"
        infra.mkdir()
        session = "01a19700-0000-7000-8000-000000000c01"
        self.transcript(
            session,
            [
                # a one-shot `cd <path> && …`: at no instant is a process there (01a16640)
                ("bash", {"command": f"cd {pool} && git status --short"}),
                # a file its own call named
                ("read", {"path": f"{verified}/state.json:1-20"}),
                # `git -C <path>`, inside a command
                ("bash", {"command": f"git -C {infra} log --oneline -1"}),
                # a file only mentioned in prose is not a tool call and names nothing
            ],
        )
        self.make_proc(470, comm="omp", ppid=1, cwd=lane, session=session)
        result = self.run_bash(f'move_dirs_why "{lane}" 470 "{session}"')
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = [line.split("\t") for line in result.stdout.splitlines()]
        self.assertEqual(
            sorted(row[0] for row in rows), sorted([str(lane), str(pool), str(verified), str(infra)])
        )
        # Each came from the record: directly, or through the git tree a recorded path is in.
        whys = {row[0]: row[1] for row in rows}
        self.assertIn("its own record today", whys[str(verified)])
        self.assertIn("its own record today", whys[str(infra)])
        self.assertTrue(
            "its own record today" in whys[str(pool)] or "git object store" in whys[str(pool)],
            whys[str(pool)],
        )
        # The session's own state directory is one of them, so a move keeps it.
        self.assertIn(str(verified), result.stdout)

    def test_a_session_with_no_live_child_process_is_told_so(self) -> None:
        lane = self.home / "lane"
        lane.mkdir()
        session = "01a19700-0000-7000-8000-000000000c02"
        self.make_proc(480, comm="bash", ppid=1, cwd=lane)
        self.make_proc(481, comm="omp", ppid=480, cwd=lane, session=session)
        self.set_pids(481)
        self.set_panes({480: ("%2", "agents:10.1")})
        write_stub(self.stubs, "df", 'if [[ "$*" == *target* ]]; then echo target; echo /; else echo avail; echo 214748364800; fi')
        plan = self.run_bash("move_image_bytes() { echo 280000000; }\ncmd_move_plan")
        self.assertEqual(plan.returncode, 0, plan.stderr)
        self.assertIn("no live child process", plan.stdout)
        sent = self.run_bash("move_image_bytes() { echo 280000000; }\ncmd_move --all")
        self.assertEqual(sent.returncode, 0, sent.stderr)
        self.assertIn("no live child process", (self.root / "STUB_ENVOY").read_text(encoding="utf-8"))

    def test_move_self_keeps_every_path_the_last_plan_gave_the_session(self) -> None:
        """`--plan` and `--self` run minutes apart and a live scan is a snapshot: at 18:48:20Z on
        2026-11-01 the plan gave 01a1798a /mnt/nvme/sami/tmp/verified-linux because one bash held
        a file open there, and the move minutes later gave the cwd alone — losing the directory
        that holds that session's own state. --self takes the union."""
        lane = self.home / "lane"
        lane.mkdir()
        state = self.home / "tmp" / "verified-linux"
        state.mkdir(parents=True)
        session = "01a19700-0000-7000-8000-000000000c03"
        self.make_proc(490, comm="bash", ppid=1, cwd=lane)
        self.make_proc(491, comm="omp", ppid=490, cwd=lane, session=session)
        self.set_pids(491)
        self.set_panes({490: ("%2", "agents:11.1")})
        write_stub(self.stubs, "df", 'if [[ "$*" == *target* ]]; then echo target; echo /; else echo avail; echo 214748364800; fi')
        # The plan ran while a process of the session held a file open in the state directory.
        self.make_proc(492, comm="cargo", ppid=491, cwd=state)
        plan = self.run_bash("move_image_bytes() { echo 280000000; }\ncmd_move_plan")
        self.assertEqual(plan.returncode, 0, plan.stderr)
        self.assertIn(str(state), plan.stdout)
        self.assertTrue((self.plan_dir / session).is_file())
        # That process has since exited, so a live scan now sees nothing there.
        for name in ("cwd",):
            (self.proc / "492" / name).unlink()
        (self.proc / "492" / "stat").unlink()
        moved = self.run_bash(
            'move_own_omp() { echo 491; }\nmove_image_bytes() { echo 280000000; }\ncmd_move_self --dry-run'
        )
        self.assertEqual(moved.returncode, 0, moved.stderr)
        self.assertIn(f"--dir {state}", moved.stdout)
        self.assertIn("the last `agentbox move --plan` gave it", moved.stdout)

    # ── the usage text ──────────────────────────────────────────────────────────────────────
    def test_move_help_prints_a_usage_that_names_every_form(self) -> None:
        result = self.run_bash("cmd_move --help")
        self.assertEqual(result.returncode, 0, result.stderr)
        for part in ("move --plan", "move --self", "--dir PATH", "--all"):
            self.assertIn(part, result.stdout)
        # A flag of another form is refused by name, not with a stale usage line.
        wrong = self.run_bash("cmd_move --plan --self")
        self.assertEqual(wrong.returncode, 1)
        self.assertIn("belongs to another form", wrong.stderr)
        self.assertIn("move --self", wrong.stderr)

    # ── what each omp process is ────────────────────────────────────────────────────────────
    def test_every_class_of_omp_process_is_told_apart(self) -> None:
        lane = self.root / "lane"
        lane.mkdir()
        self.make_proc(500, comm="bash", ppid=1, cwd=lane)
        self.make_proc(501, cwd=lane, ppid=500, session="01a19700-0000-7000-8000-00000000a001")
        self.make_proc(502, cwd=lane, ppid=501, session="01a19700-0000-7000-8000-00000000a002")
        self.make_proc(
            503,
            cwd=lane,
            ppid=1,
            session="01a19700-0000-7000-8000-00000000a003",
            cgroup="0::/system.slice/docker-aaaaaaaaaaaabbbbbbbbbbbb.scope",
        )
        self.make_proc(
            504,
            cwd="/work",
            ppid=1,
            session="01a19700-0000-7000-8000-00000000a004",
            cgroup="0::/system.slice/docker-ccccccccccccdddddddddddd.scope",
        )
        self.make_proc(505, comm="bwrap", ppid=1, cwd=lane)
        self.make_proc(506, cwd=lane, ppid=505, session="01a19700-0000-7000-8000-00000000a005")
        self.make_proc(
            507,
            cwd=lane,
            ppid=1,
            session="01a19700-0000-7000-8000-00000000a006",
            cgroup="0::/user.slice/user-1000.slice/user@1000.service/ossv-step.service",
        )
        self.make_proc(508, cwd=lane, ppid=500, cmdline="omp __omp_worker_daemon_broker")
        self.set_pids(501, 502, 503, 504, 506, 507, 508)
        self.set_panes({500: ("%7", "agents:1.2")})
        self.set_containers(
            {"aaaaaaaaaaaabbbbbbbbbbbb": "agentbox-aaaaaaaaaaaa", "ccccccccccccdddddddddddd": "grader-77"}
        )
        rows = {row[1]: row for row in self.census()}
        self.assertEqual(sorted(rows), ["501", "502", "503", "504", "506", "507"])
        self.assertEqual(rows["501"][0], "host")
        self.assertEqual(rows["501"][3:6], ["%7", "agents:1.2", str(lane)])
        self.assertEqual(rows["502"][0], "subagent")
        self.assertEqual(rows["503"][0], "boxed")
        self.assertEqual(rows["504"][0], "sandbox")
        self.assertEqual(rows["506"][0], "sandbox")
        self.assertEqual(rows["507"][0], "no-pane")
        self.assertIn("ossv-step.service", rows["507"][6])

    def pane_behind_another_socket(self, pane: str, tmux: str = "/sock/tmux-1000/default,28813,1") -> None:
        """LANE-E's shape (2026-11-02): omp -> bash shims/omp -> the pane's login shell -> the tmux
        server, on a server whose socket is not tmux's default, so the census's list-panes, from a
        tool shell with no TMUX, reached nothing. Only the server named by the omp's own TMUX knows
        pane %70, and says its process is 42507."""
        lane = self.root / "lane"
        lane.mkdir()
        self.make_proc(28813, comm="tmux: server", ppid=1, cwd=lane)
        self.make_proc(42507, comm="bash", ppid=28813, cwd=lane)
        self.make_proc(925768, comm="bash", ppid=42507, cwd=lane)
        self.make_proc(925946, ppid=925768, cwd=lane, session="01a170c2-69f0-7542-a611-195ce0c4a111",
                       environ={"TMUX": tmux, "TMUX_PANE": pane})
        self.set_pids(925946)
        (self.root / "TMUX_ARGS").write_text("", encoding="utf-8")
        write_stub(self.stubs, "tmux", "\n".join([
            f'printf "%s\\n" "$*" >>"{self.root / "TMUX_ARGS"}"',
            'case " $* " in',
            '  *" list-panes "*) echo "error connecting to /tmp/tmux-1000/default (No such file or directory)" >&2; exit 1 ;;',
            '  *" display-message "*)',
            '    [ "$1 $2" = "-S /sock/tmux-1000/default" ] || exit 1',
            '    case " $* " in *" -t %70 "*) printf "42507\\tlane:1.0\\n" ;; *" -t %71 "*) printf "999\\tother:2.0\\n" ;; *) exit 1 ;; esac ;;',
            "esac",
        ]))

    def test_a_pane_the_pane_list_misses_is_read_from_the_omps_own_tmux(self) -> None:
        self.pane_behind_another_socket("%70")
        result = self.run_bash("move_census")
        self.assertEqual(result.returncode, 0, result.stderr)
        # The failed list-panes is said, not hidden.
        self.assertIn("list-panes -a` failed (exit 1): error connecting to /tmp/tmux-1000/default", result.stderr)
        rows = [line.split("\x1f") for line in result.stdout.splitlines()]
        self.assertEqual([r[:5] for r in rows],
                         [["host", "925946", "01a170c2-69f0-7542-a611-195ce0c4a111", "%70", "lane:1.0"]])

    def test_a_pane_whose_process_is_not_the_walks_is_not_taken(self) -> None:
        # The environment can be inherited from elsewhere; the server must confirm the pane.
        self.pane_behind_another_socket("%71")
        rows = {row[1]: row for row in self.census()}
        self.assertEqual(rows["925946"][0], "no-pane")
        self.assertIn("28813:tmux: server", rows["925946"][6])

    def test_the_pane_list_asks_the_server_of_the_callers_own_tmux(self) -> None:
        self.pane_behind_another_socket("%70")
        result = self.run_bash("move_census", {"TMUX": "/sock/tmux-1000/default,28813,1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        args = (self.root / "TMUX_ARGS").read_text(encoding="utf-8").splitlines()
        self.assertTrue(args[0].startswith("-S /sock/tmux-1000/default list-panes -a"), args)

    def unreachable_tmux(self) -> None:
        # What LANE-E's tool shell got on the right socket: its tmux 3.4 against the 3.8 server.
        write_stub(self.stubs, "tmux", 'echo "server exited unexpectedly" >&2; exit 1')

    def test_a_server_no_tmux_can_reach_is_confirmed_from_proc_alone(self) -> None:
        self.pane_behind_another_socket("%70")
        self.unreachable_tmux()
        rows = [r[:5] for r in self.census()]
        self.assertEqual(rows, [["host", "925946", "01a170c2-69f0-7542-a611-195ce0c4a111", "%70", ""]])

    def test_a_tmux_that_names_another_server_is_not_trusted_from_proc(self) -> None:
        self.pane_behind_another_socket("%70", tmux="/sock/tmux-1000/default,4242,1")
        self.unreachable_tmux()
        self.assertEqual(self.census()[0][0], "no-pane")

    def test_the_server_is_asked_with_its_own_binary(self) -> None:
        self.pane_behind_another_socket("%70")
        own = self.root / "server-bin"
        own.mkdir()
        write_stub(own, "tmux", '[ "$1 $2 $3 $6" = "-S /sock/tmux-1000/default display-message %70" ] && printf "42507\\tlane:1.0\\n"')
        os.symlink(own / "tmux", self.proc / "28813" / "exe")
        self.unreachable_tmux()  # the tmux on PATH is another version
        rows = [r[:5] for r in self.census()]
        self.assertEqual(rows, [["host", "925946", "01a170c2-69f0-7542-a611-195ce0c4a111", "%70", "lane:1.0"]])

    def test_move_self_refuses_before_the_hang_up_when_tmux_cannot_reach_its_pane(self) -> None:
        self.pane_behind_another_socket("%70")
        self.unreachable_tmux()
        extra = self.root / "extra"
        extra.mkdir()
        result = self.run_bash(
            'move_own_omp() { echo 925946; }\nmove_image_bytes() { echo 280000000; }\n'
            'move_hand_off() { echo HANDED-OFF; }\n'
            f'cmd_move_self --dir "{extra}"'
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertNotIn("HANDED-OFF", result.stdout)
        self.assertIn("nothing was hung up", result.stderr)
        self.assertIn(f"agentbox move 01a170c2-69f0-7542-a611-195ce0c4a111 --now --dir {extra}", result.stderr)

    def test_a_lead_moves_one_session_from_outside_with_its_extra_paths(self) -> None:
        self.pane_behind_another_socket("%70")
        extra = self.root / "extra"
        extra.mkdir()
        env = 'move_image_bytes() { echo 280000000; }\n'
        moved = self.run_bash(env + f'cmd_move_notify 01a170c2 --now --dry-run --dir "{extra}"')
        self.assertEqual(moved.returncode, 0, moved.stderr)
        self.assertIn("would relaunch pane %70", moved.stdout)
        self.assertIn(f"--dir {extra} -- omp --resume {self.session_file('01a170c2-69f0-7542-a611-195ce0c4a111')}", moved.stdout)
        refused = self.run_bash(env + f'cmd_move_notify --all --now --dry-run --dir "{extra}"')
        self.assertEqual(refused.returncode, 1)
        self.assertIn("--dir names paths of ONE session", refused.stderr)

    # ── where the session lives ─────────────────────────────────────────────────────────────
    def nested_session(self, holds: Path | None, environ: dict[str, str] | None = None,
                       cmdline: str = "omp") -> None:
        """METARQ's shape (2026-11-02): a session started from another session's turn, whose
        transcript is inside THAT session's directory, so `omp --resume <id>` cannot find it.
        `holds` is the transcript the omp holds open, or None for an omp idle after a resume."""
        lane = self.root / "lane"
        lane.mkdir(exist_ok=True)
        self.make_proc(1100, comm="bash", ppid=1, cwd=lane)
        self.make_proc(1101, ppid=1100, cwd=lane, environ=environ, cmdline=cmdline,
                       writes=(holds,) if holds else ())
        self.set_pids(1101)
        self.set_panes({1100: ("%47", "agents:3.1")})

    METARQ = "01a170d9-5e66-7491-b0d2-fa6a2e55c14e"

    def nested_transcript(self, root: Path | None = None) -> Path:
        path = (root or self.home / ".omp/agent/sessions") / "-lane" \
            / "2026-10-14T17-14-49-559Z_01a13b68-d957-74b8-ad2b-b642fd17bfd9" \
            / f"2026-10-25T02-17-36-102Z_{self.METARQ}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        return path

    def test_a_nested_transcript_is_resumed_by_its_path_with_the_session_environment(self) -> None:
        nested = self.nested_transcript()
        self.nested_session(nested, environ={"OMP_PROFILE": "pbc", "TMUX_PANE": "%47", "HOME": "/x"})
        rows = {row[1]: row for row in self.census()}
        self.assertEqual(rows["1101"][:4], ["host", "1101", self.METARQ, "%47"])
        moved = self.run_bash('move_own_omp() { echo 1101; }\nmove_image_bytes() { echo 280000000; }\n'
                              'cmd_move_self --dry-run')
        self.assertEqual(moved.returncode, 0, moved.stderr)
        self.assertIn(f"agentbox new -e OMP_PROFILE=pbc --dir {self.root / 'lane'} -- omp --resume {nested}\n",
                      moved.stdout)
        # Only the variables that decide where the session lives go into the box.
        self.assertNotIn("TMUX_PANE=", moved.stdout)
        self.assertNotIn("HOME=/x", moved.stdout)

    def test_an_idle_omp_s_nested_transcript_is_found_under_its_profile(self) -> None:
        # Idle after a resume: no open transcript, its --resume names only the id. The profile's
        # agent directory is searched; omp's profile dirs link sessions to the default's.
        nested = self.nested_transcript(self.home / ".omp/profiles/pbc/agent/sessions")
        self.nested_session(None, environ={"OMP_PROFILE": "pbc"}, cmdline=f"omp --resume {self.METARQ}")
        moved = self.run_bash('move_image_bytes() { echo 280000000; }\n'
                              f'cmd_move_notify {self.METARQ} --now --dry-run')
        self.assertEqual(moved.returncode, 0, moved.stderr)
        self.assertIn(f"-e OMP_PROFILE=pbc --dir {self.root / 'lane'} -- omp --resume {nested}\n", moved.stdout)

    def test_a_session_whose_transcript_cannot_be_found_is_refused_before_the_hang_up(self) -> None:
        self.nested_session(None, cmdline=f"omp --resume {self.METARQ}")
        refused = self.run_bash('move_own_omp() { echo 1101; }\nmove_image_bytes() { echo 280000000; }\n'
                                'move_hand_off() { echo HANDED-OFF; }\ncmd_move_self')
        self.assertEqual(refused.returncode, 1, refused.stdout)
        self.assertNotIn("HANDED-OFF", refused.stdout)
        self.assertIn(f"no transcript of session {self.METARQ} can be read", refused.stderr)
        self.assertIn("nothing was hung up", refused.stderr)
        # From outside too: no notice and no hand-off.
        outside = self.run_bash(f'move_hand_off() {{ echo HANDED-OFF; }}\ncmd_move_notify {self.METARQ} --now')
        self.assertNotIn("HANDED-OFF", outside.stdout)
        self.assertIn("cannot be moved", outside.stderr)

    def test_a_transcript_the_box_would_not_see_is_refused_before_the_hang_up(self) -> None:
        # Outside every path the box gets: not under the home allow-list, not a --dir. The omp is
        # idle after a resume by that path, so no open file makes the path an inferred --dir.
        nested = self.nested_transcript(self.root / "elsewhere/sessions")
        self.nested_session(None, cmdline=f"omp --resume {nested}")
        refused = self.run_bash('move_own_omp() { echo 1101; }\nmove_image_bytes() { echo 280000000; }\n'
                                'move_hand_off() { echo HANDED-OFF; }\ncmd_move_self')
        self.assertEqual(refused.returncode, 1, refused.stdout)
        self.assertNotIn("HANDED-OFF", refused.stdout)
        self.assertIn("is not in any path the box gets", refused.stderr)
        # Naming its directory with --dir makes it reachable.
        allowed = self.run_bash('move_own_omp() { echo 1101; }\nmove_image_bytes() { echo 280000000; }\n'
                                f'cmd_move_self --dry-run --dir "{self.root / "elsewhere"}"')
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertIn(f"-- omp --resume {nested}\n", allowed.stdout)

    def test_the_plan_shows_each_session_s_resume_check(self) -> None:
        """`--plan` runs the move's own resume check: a nested transcript shows the path the box
        will resume, an unreachable one shows REFUSED with the reason, and the summary counts it."""
        nested = self.nested_transcript()
        self.nested_session(nested, environ={"OMP_PROFILE": "pbc"})
        far = self.root / "elsewhere/sessions/-lane" / "2026-10-25T02-17-36-102Z_01a19700-0000-7000-8000-00000000f001.jsonl"
        far.parent.mkdir(parents=True)
        far.write_text("", encoding="utf-8")
        self.make_proc(1102, ppid=1100, cwd=self.root / "lane", cmdline=f"omp --resume {far}")
        self.set_pids(1101, 1102)
        self.set_panes({1100: ("%47", "agents:3.1")})
        env = 'move_image_bytes() { echo 280000000; }\nmove_free_bytes() { echo 900000000000; }\n'
        plan = self.run_bash(env + "cmd_move_plan")
        self.assertEqual(plan.returncode, 0, plan.stderr)
        self.assertIn(f"resume: {nested}", plan.stdout)
        self.assertIn("-e OMP_PROFILE=pbc --dir", plan.stdout)
        self.assertIn(f"REFUSED: its transcript {far} is not in any path the box gets", plan.stdout)
        self.assertIn("resume check: 1 REFUSED, would not come back in a box and are not moved: "
                      "01a19700-0000-7000-8000-00000000f001", plan.stdout)
        doc = json.loads(self.run_bash(env + "cmd_move_plan --json").stdout)
        by = {s["session"]: s for s in doc["sessions"]}
        self.assertEqual(by[self.METARQ]["resume"], str(nested))
        self.assertIsNone(by[self.METARQ]["resume_refused"])
        self.assertIn("is not in any path the box gets", by["01a19700-0000-7000-8000-00000000f001"]["resume_refused"])
        self.assertEqual(doc["summary"]["resume_refused"], 1)

    def relaunch(self, environ: dict[str, str], respawn_fails: bool = False,
                 caller: dict[str, str] | None = None) -> list[str]:
        """Run the detached relauncher of `--all`/`move <id>`/`--now` for one session and return the
        tmux calls it made. The omp's pid is above any pid_max, so its hang-up and liveness checks
        reach no real process."""
        lane = self.root / "lane"
        lane.mkdir(exist_ok=True)
        self.make_proc(999999999, cwd=lane, session="01a170c2-69f0-7542-a611-195ce0c4a111", environ=environ)
        args = self.root / "TMUX_ARGS"
        args.write_text("", encoding="utf-8")
        write_stub(self.stubs, "tmux", "\n".join([
            f'printf "%s\\n" "$*" >>"{args}"',
            'case " $* " in',
            '  *" respawn-pane "*) exit ' + ("1" if respawn_fails else "0") + " ;;",
            '  *" display-message "*) printf "42507\\n" ;;',
            "esac",
            "exit 0",
        ]))
        result = self.run_bash(
            f'move_relaunch 999999999 01a170c2-69f0-7542-a611-195ce0c4a111 %70 "{lane}" '
            f'"{self.session_file("01a170c2-69f0-7542-a611-195ce0c4a111")}" --dir "{lane}"',
            {"AGENTBOX_RESPAWN_TRIES": "1", "AGENTBOX_RESPAWN_WAIT": "0", **(caller or {})},
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return args.read_text(encoding="utf-8").splitlines()

    def test_the_relauncher_drives_the_pane_on_the_target_omps_own_server(self) -> None:
        # The caller's TMUX names another server; the target omp's own wins.
        calls = self.relaunch({"TMUX": "/sock/tmux-1000/default,28813,1", "TMUX_PANE": "%70"},
                              caller={"TMUX": "/other/server,1,1"})
        self.assertTrue(calls)
        self.assertTrue(all(c.startswith("-S /sock/tmux-1000/default ") for c in calls), calls)
        self.assertTrue(any(c.startswith("-S /sock/tmux-1000/default respawn-pane -k -t %70 ") for c in calls), calls)

    def test_a_respawn_that_fails_types_the_launcher_on_the_same_server(self) -> None:
        calls = self.relaunch({"TMUX": "/sock/tmux-1000/default,28813,1"}, respawn_fails=True)
        self.assertTrue(any(c.startswith("-S /sock/tmux-1000/default send-keys -t %70 ") for c in calls), calls)
        self.assertTrue(all(c.startswith("-S /sock/tmux-1000/default ") for c in calls), calls)

    def test_a_target_without_tmux_in_its_environment_uses_the_callers_server(self) -> None:
        calls = self.relaunch({})
        self.assertTrue(calls)
        self.assertFalse(any(c.startswith("-S ") for c in calls), calls)

    def test_a_pane_tmux_answers_empty_for_is_gone_and_not_respawned(self) -> None:
        """tmux 3.8 answers `display-message -t <gone pane>` with exit 0 and an empty line, so the
        exit code said a closed pane was there and the relauncher tried `respawn-pane` on it every
        15 s for 10 minutes (pane %1665 of a drill session, 2026-11-02T15:19:08Z)."""
        lane = self.root / "lane"
        lane.mkdir()
        sid = "01a170c2-69f0-7542-a611-195ce0c4a111"
        self.make_proc(999999999, cwd=lane, session=sid, environ={"TMUX": "/sock/tmux-1000/default,28813,1"})
        args = self.root / "TMUX_ARGS"
        args.write_text("", encoding="utf-8")
        write_stub(self.stubs, "tmux", f'printf "%s\\n" "$*" >>"{args}"\nexit 0')
        result = self.run_bash(
            f'move_relaunch 999999999 {sid} %70 "{lane}" "{self.session_file(sid)}" --dir "{lane}"',
            {"AGENTBOX_RESPAWN_TRIES": "3", "AGENTBOX_RESPAWN_WAIT": "0"},
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(f"move {sid}: pane %70 is gone; nothing respawned.", result.stdout)
        calls = args.read_text(encoding="utf-8").splitlines()
        self.assertFalse(any(" respawn-pane " in f" {c} " for c in calls), calls)

    def test_the_relauncher_logs_a_dir_gone_since_the_spec_and_the_launcher_drops_it(self) -> None:
        """A respawn can run up to ten minutes after the spec was read, and the old omp's TMPDIR
        is reaped minutes after it exits: the pane's launcher leaves such a --dir out
        (--drop-gone-dirs), and the move's log, the one record anyone reads, names it."""
        lane = self.root / "lane"
        lane.mkdir()
        gone = self.root / "session-tmp" / "53458-LivaHYZv"
        sid = "01a1798a-99af-7266-95dd-fa4be35f9109"
        self.make_proc(999999999, cwd=lane, session=sid, environ={"TMUX": "/sock/tmux-1000/default,28813,1"})
        args = self.root / "TMUX_ARGS"
        args.write_text("", encoding="utf-8")
        write_stub(self.stubs, "tmux", "\n".join([
            f'printf "%s\\n" "$*" >>"{args}"',
            'case " $* " in *" display-message "*) printf "%%23\\n" ;; esac',
            "exit 0",
        ]))
        result = self.run_bash(
            f'move_relaunch 999999999 {sid} %23 "{lane}" "{self.session_file(sid)}" --dir "{lane}" --dir "{gone}"',
            {"AGENTBOX_RESPAWN_TRIES": "1", "AGENTBOX_RESPAWN_WAIT": "0"},
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"move {sid}: --dir {gone} no longer exists; the box starts without it", result.stdout)
        respawn = [c for c in args.read_text(encoding="utf-8").splitlines() if " respawn-pane " in f" {c} "]
        self.assertEqual(len(respawn), 1, respawn)
        self.assertIn("agentbox new --drop-gone-dirs --dir", respawn[0])

    def test_a_session_file_names_the_session_in_full_and_a_truncated_name_is_no_id(self) -> None:
        """A prefix would resume another session, so only the 36-character id in the transcript's
        name counts; everything else reads as "no id" and is refused rather than guessed."""
        lane = self.root / "lane"
        lane.mkdir()
        self.make_proc(600, comm="bash", ppid=1, cwd=lane)
        self.make_proc(601, cwd=lane, ppid=600, session="01a19700-0000-7000-8000-00000000b001")
        self.make_proc(602, cwd=lane, ppid=600)
        truncated = self.home / ".omp/agent/sessions/-work/2026-11-01T09:00:00Z_01a19700.jsonl"
        truncated.parent.mkdir(parents=True, exist_ok=True)
        truncated.write_text("", encoding="utf-8")
        os.symlink(str(truncated), self.proc / "602" / "fd" / "21")
        self.set_pids(601, 602)
        self.set_panes({600: ("%9", "agents:2.1")})
        rows = {row[1]: row for row in self.census()}
        self.assertEqual(rows["601"][2], "01a19700-0000-7000-8000-00000000b001")
        self.assertEqual(rows["602"][2], "")

    def test_an_omp_resumed_from_a_subagent_transcript_is_given_no_session_id(self) -> None:
        """Two live omp processes on 2026-11-01 were started as
        `omp --resume …/<parent id>/<session id>/ArityLane.jsonl`. A pattern that took the last
        uuid anywhere in that path would have handed both the id of a third, live session, and a
        move would then have resumed one session in three boxes. The id comes from the file
        name, `_<id>.jsonl`, and from nowhere else."""
        lane = self.root / "lane"
        lane.mkdir()
        victim = "01a19700-0000-7000-8000-00000000b101"
        nested = (
            f"{self.home}/.omp/agent/sessions/-lane/2026-10-14T17-14-49-559Z_{victim}"
            f"/2026-10-25T02-17-36-102Z_01a19700-0000-7000-8000-00000000b102/ArityLane.jsonl"
        )
        self.make_proc(610, comm="bash", ppid=1, cwd=lane)
        self.make_proc(611, cwd=lane, ppid=610, session=victim)
        self.make_proc(612, cwd=lane, ppid=610, cmdline=f"omp --resume {nested}")
        self.make_proc(613, cwd=lane, ppid=610, cmdline=f"omp --resume {nested}")
        self.set_pids(611, 612, 613)
        self.set_panes({610: ("%9", "agents:2.2")})
        rows = {row[1]: row for row in self.census()}
        self.assertEqual(rows["611"][2], victim)
        self.assertEqual(rows["612"][2], "")
        self.assertEqual(rows["613"][2], "")

    def test_a_session_id_two_processes_claim_goes_to_the_one_holding_it(self) -> None:
        """A `--resume <id>` argument is the second source, for a session idle after a resume
        that holds no writer. Where two processes name one id, only the holder keeps it."""
        lane = self.root / "lane"
        lane.mkdir()
        shared = "01a19700-0000-7000-8000-00000000b201"
        lone = "01a19700-0000-7000-8000-00000000b202"
        self.make_proc(620, comm="bash", ppid=1, cwd=lane)
        self.make_proc(621, cwd=lane, ppid=620, session=shared, cmdline=f"omp --resume {shared}")
        self.make_proc(622, cwd=lane, ppid=620, cmdline=f"omp --resume {shared}")
        self.make_proc(623, cwd=lane, ppid=620, cmdline=f"omp --resume {lone}")
        self.set_pids(621, 622, 623)
        self.set_panes({620: ("%9", "agents:2.3")})
        rows = {row[1]: row for row in self.census()}
        self.assertEqual(rows["621"][2], shared)
        self.assertEqual(rows["622"][2], "")
        self.assertIn("must never resume one session twice", rows["622"][6])
        # An id only one process claims is its own, and a move may use it.
        self.assertEqual(rows["623"][2], lone)

    def test_a_session_whose_id_cannot_be_read_is_never_moved(self) -> None:
        lane = self.root / "lane"
        lane.mkdir()
        self.make_proc(700, comm="bash", ppid=1, cwd=lane)
        self.make_proc(701, cwd=lane, ppid=700)
        self.set_pids(701)
        self.set_panes({700: ("%3", "agents:3.1")})
        plan = self.run_bash("cmd_move_plan")
        self.assertEqual(plan.returncode, 0, plan.stderr)
        self.assertIn("holds no session file open", plan.stdout)
        self.assertIn("0 with a spec, 1 that need a decision", plan.stdout)
        # --self refuses for the same reason rather than resuming a session it has guessed.
        selfrun = self.run_bash('move_own_omp() { echo 701; }\ncmd_move_self --dry-run')
        self.assertEqual(selfrun.returncode, 1)
        self.assertIn("cannot name its session in full", selfrun.stderr)

    # ── the two refusals ────────────────────────────────────────────────────────────────────
    def test_a_box_that_would_take_the_disk_under_its_reserve_is_refused(self) -> None:
        lane = self.root / "lane"
        lane.mkdir()
        self.make_proc(800, comm="bash", ppid=1, cwd=lane)
        self.make_proc(801, cwd=lane, ppid=800, session="01a19700-0000-7000-8000-00000000c001")
        self.set_pids(801)
        self.set_panes({800: ("%5", "agents:4.1")})
        # 18.6 GiB free on /mnt/nvme, whose reserve is 20 GiB: the box is refused, and the refusal
        # comes before anything is touched, so the dry run never prints its launch.
        write_stub(self.stubs, "df", 'if [[ "$*" == *target* ]]; then echo target; echo /mnt/nvme; else echo avail; echo 20000000000; fi')
        refused = self.run_bash(
            'move_own_omp() { echo 801; }\nmove_image_bytes() { echo 280000000; }\ncmd_move_self --dry-run'
        )
        self.assertEqual(refused.returncode, 2)
        self.assertIn("under its 20.0 GiB reserve", refused.stderr)
        self.assertEqual(refused.stdout, "")
        # 74.5 GiB free is under disk-fill-watch's 84 GB alert floor, but an alert floor is not a
        # reason to refuse: the same box is allowed through to its dry run.
        write_stub(self.stubs, "df", 'if [[ "$*" == *target* ]]; then echo target; echo /mnt/nvme; else echo avail; echo 80000000000; fi')
        allowed = self.run_bash(
            'move_own_omp() { echo 801; }\nmove_image_bytes() { echo 280000000; }\ncmd_move_self --dry-run'
        )
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertIn(
            f"agentbox new --dir {lane} -- omp --resume {self.session_file('01a19700-0000-7000-8000-00000000c001')}\n",
            allowed.stdout,
        )

    def test_a_session_inside_a_box_can_move_again_with_one_more_dir(self) -> None:
        """A box must not be a one-way door. Every box defect found so far is fixed by moving
        again with one more --dir, and a boxed session could do neither: the path is the HOST's,
        so checking it in the box's own view answered `--dir '<path>' is not an existing
        directory`, and --self then refused outright."""
        host_only = "/mnt/nvme/sami/tmp/a-path-this-box-does-not-have"
        self.assertFalse(Path(host_only).exists(), "the fixture needs a path this host lacks")
        nested = self.home / ".omp/agent/sessions/-lane/2026-10-14T17-14-49-559Z_01a13b68-d957-74b8-ad2b-b642fd17bfd9" \
            / "2026-10-25T02-17-36-102Z_01a19700-0000-7000-8000-000000000d09.jsonl"
        env = {
            "AGENTBOX_BOX": "agentbox-aaaaaaaaaaaa",
            "AGENTBOX_DIRS": f"{self.home}/lane:{self.home}/evidence",
            "OMP_SESSION_ID": "01a19700-0000-7000-8000-000000000d09",
            "PI_SESSION_FILE": str(nested),
            "TMUX_PANE": "%7",
        }
        result = self.run_bash(f'cmd_move_self --dry-run --dir "{host_only}"', env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already in box agentbox-aaaaaaaaaaaa", result.stdout)
        # This box's own paths, then the new one, and the session resumed by its transcript.
        self.assertIn(
            f"--dir {self.home}/lane --dir {self.home}/evidence --dir {host_only} -- omp --resume {nested}\n",
            result.stdout,
        )
        # A box started with no --dir has nothing to carry, and says so instead of guessing.
        bare = self.run_bash("cmd_move_self --dry-run", {"AGENTBOX_BOX": "agentbox-aaaaaaaaaaaa"})
        self.assertEqual(bare.returncode, 1)
        self.assertIn("no --dir", bare.stderr)

    # ── notifying other sessions ────────────────────────────────────────────────────────────
    def test_each_named_session_gets_the_notice_with_its_own_launch_line(self) -> None:
        lane = self.root / "lane"
        lane.mkdir()
        self.make_proc(900, comm="bash", ppid=1, cwd=lane)
        self.make_proc(901, cwd=lane, ppid=900, session="01a19700-0000-7000-8000-00000000d001")
        self.make_proc(902, comm="bash", ppid=1, cwd="/work")
        self.make_proc(
            903,
            cwd="/work",
            ppid=902,
            session="01a19700-0000-7000-8000-00000000d002",
            cgroup="0::/system.slice/docker-ccccccccccccdddddddddddd.scope",
        )
        self.set_pids(901, 903)
        self.set_panes({900: ("%5", "agents:5.1"), 902: ("%6", "agents:5.2")})
        self.set_containers({"ccccccccccccdddddddddddd": "grader-77"})
        write_stub(self.stubs, "df", 'if [[ "$*" == *target* ]]; then echo target; echo /mnt/nvme; else echo avail; echo 214748364800; fi')
        sent = self.run_bash("move_image_bytes() { echo 280000000; }\ncmd_move --all")
        self.assertEqual(sent.returncode, 0, sent.stderr)
        notice = (self.root / "STUB_ENVOY").read_text(encoding="utf-8")
        self.assertIn("01a19700-0000-7000-8000-00000000d001", notice)
        self.assertIn("agentbox move --self", notice)
        self.assertIn("Do not stop, delay or shorten any work", notice)
        self.assertIn(
            f"agentbox new --dir {lane} -- omp --resume {self.session_file('01a19700-0000-7000-8000-00000000d001')}`", notice
        )
        # The sandboxed omp is not a session on this host and is never notified.
        self.assertNotIn("01a19700-0000-7000-8000-00000000d002", notice)

    def test_a_dry_run_notifies_nobody(self) -> None:
        lane = self.root / "lane"
        lane.mkdir()
        self.make_proc(910, comm="bash", ppid=1, cwd=lane)
        self.make_proc(911, cwd=lane, ppid=910, session="01a19700-0000-7000-8000-00000000e001")
        self.set_pids(911)
        self.set_panes({910: ("%5", "agents:6.1")})
        write_stub(self.stubs, "df", 'if [[ "$*" == *target* ]]; then echo target; echo /mnt/nvme; else echo avail; echo 214748364800; fi')
        result = self.run_bash("move_image_bytes() { echo 280000000; }\ncmd_move --all --dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("would notify 01a19700-0000-7000-8000-00000000e001", result.stdout)
        self.assertEqual((self.root / "STUB_ENVOY").read_text(encoding="utf-8"), "")

    def test_the_plan_counts_every_class_and_the_disk_the_boxes_would_take(self) -> None:
        lane = self.root / "lane"
        lane.mkdir()
        self.make_proc(920, comm="bash", ppid=1, cwd=lane)
        self.make_proc(921, cwd=lane, ppid=920, session="01a19700-0000-7000-8000-00000000f001")
        self.make_proc(922, cwd=lane, ppid=921, session="01a19700-0000-7000-8000-00000000f002")
        self.make_proc(
            923,
            cwd="/work",
            ppid=1,
            session="01a19700-0000-7000-8000-00000000f003",
            cgroup="0::/system.slice/docker-ccccccccccccdddddddddddd.scope",
        )
        self.make_proc(924, cwd=lane, ppid=1, session="01a19700-0000-7000-8000-00000000f004")
        self.set_pids(921, 922, 923, 924)
        self.set_panes({920: ("%5", "agents:7.1")})
        self.set_containers({"ccccccccccccdddddddddddd": "grader-77"})
        write_stub(self.stubs, "df", 'if [[ "$*" == *target* ]]; then echo target; echo /mnt/nvme; else echo avail; echo 214748364800; fi')
        plan = self.run_bash("move_image_bytes() { echo 1073741824; }\ncmd_move_plan")
        self.assertEqual(plan.returncode, 0, plan.stderr)
        self.assertIn("host sessions outside a box: 1 (1 with a spec, 0 that need a decision)", plan.stdout)
        self.assertIn("not a pane (headless or unit-run, yours to decide): 1", plan.stdout)
        self.assertIn("1 in a grading sandbox, 1 subagents of another omp", plan.stdout)
        self.assertIn("1.0 GiB per box", plan.stdout)

    def test_the_plan_as_json_carries_every_session_and_the_headroom(self) -> None:
        lane = self.root / "lane"
        lane.mkdir()
        self.make_proc(930, comm="bash", ppid=1, cwd=lane)
        self.make_proc(931, cwd=lane, ppid=930, session="01a19700-0000-7000-8000-000000010001")
        self.set_pids(931)
        self.set_panes({930: ("%5", "agents:8.1")})
        write_stub(self.stubs, "df", 'if [[ "$*" == *target* ]]; then echo target; echo /mnt/nvme; else echo avail; echo 214748364800; fi')
        result = self.run_bash("move_image_bytes() { echo 1073741824; }\ncmd_move_plan --json")
        self.assertEqual(result.returncode, 0, result.stderr)
        import json

        plan = json.loads(result.stdout)
        self.assertEqual(plan["summary"]["movable"], 1)
        self.assertEqual(plan["summary"]["new_disk_total_bytes"], 1073741824)
        self.assertEqual(
            plan["summary"]["headroom_bytes"], 214748364800 - 21474836480 - 1073741824
        )
        self.assertEqual(plan["sessions"][0]["session"], "01a19700-0000-7000-8000-000000010001")
        self.assertEqual(plan["sessions"][0]["spec"], f"--dir {lane}")

    def test_a_moved_pane_resumes_with_no_prompt_and_starts_the_waker(self) -> None:
        # A positional after --resume is a user message to omp, which the transcript store counts
        # as text the person typed; the pane resumes bare and the waker wakes it over Envoy.
        sid = "01a19700-0000-7000-8000-000000020001"
        result = self.run_bash(f"move_pane_command {sid} /t/{sid}.jsonl --dir /lane")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"move --wake {sid}", result.stdout)
        self.assertTrue(result.stdout.rstrip().endswith(
            f"agentbox new --drop-gone-dirs --dir /lane -- omp --resume /t/{sid}.jsonl"), result.stdout)

    def test_the_waker_notifies_once_the_resumed_process_registers(self) -> None:
        sid = "01a19700-0000-7000-8000-000000020002"
        write_stub(
            self.stubs,
            "envoy",
            f'if [[ "$1" == ps ]]; then echo \'[{{"session_id":"{sid}","updated_at":99999999999999}}]\'; '
            'else printf "%s\\n" "$@" >>"$STUB_ENVOY"; fi; exit 0',
        )
        write_stub(self.stubs, "sleep", "exit 0")
        result = self.run_bash(f"MOVE_WAKE_WAIT=10\nmove_wake {sid}")
        self.assertEqual(result.returncode, 0, result.stderr)
        sent = (self.root / "STUB_ENVOY").read_text(encoding="utf-8").splitlines()
        self.assertEqual(sent[:4], ["send", "--source", "envoy", sid])
        self.assertIn("not a person", sent[4])

    def test_the_waker_never_notifies_the_old_registration(self) -> None:
        # An Envoy row last updated before the waker started is the old process's; a notice to it
        # would be lost, so the waker waits for a newer one and, without it, sends nothing.
        sid = "01a19700-0000-7000-8000-000000020003"
        write_stub(
            self.stubs,
            "envoy",
            f'if [[ "$1" == ps ]]; then echo \'[{{"session_id":"{sid}","updated_at":1}}]\'; '
            'else printf "%s\\n" "$@" >>"$STUB_ENVOY"; fi; exit 0',
        )
        write_stub(self.stubs, "sleep", "exit 0")
        result = self.run_bash(f"MOVE_WAKE_WAIT=10\nmove_wake {sid}")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("did not register on Envoy", result.stdout)
        self.assertEqual((self.root / "STUB_ENVOY").read_text(encoding="utf-8"), "")


    # ── move --to another machine (dispatch://INFRA-993) ────────────────────────────────────
    TO = "01a19700-0000-7000-8000-0000000e0001"
    TO2 = "01a19700-0000-7000-8000-0000000e0002"

    def target(self, resume: bool = True) -> None:
        """The other machine of `move --to`, reached through `ssh` as every move reaches one. The
        stub runs the remote command HERE, with the target's own tools first on PATH and the
        target's own /proc: a tmux that records each call and, on new-window/new-session, starts a
        fixture omp whose command line is that window's `--resume <path>` (unless `resume` is off:
        the omp that cannot load the session, which exits at once), and ends it on kill-window.
        rsync, setsid and this machine's tmux record their arguments; nothing reaches a real host."""
        self.remote = self.root / "remote"
        (self.remote / "proc").mkdir(parents=True)
        (self.remote / "PGREP").write_text("", encoding="utf-8")
        (self.remote / "TMUX").write_text("", encoding="utf-8")
        if not resume:
            (self.remote / "FAIL").write_text("", encoding="utf-8")
        write_stub(self.remote / "bin", "tmux", "\n".join([
            'printf "%s\\n" "$*" >>"$STUB_REMOTE/TMUX"',
            'case "$1" in',
            "  has-session) exit 1 ;;",
            "  new-window|new-session)",
            '    if [[ ! -e "$STUB_REMOTE/FAIL" ]]; then',
            '      r="$(printf "%s" "$*" | grep -o -- "--resume [^ ;]*" | tail -1)"',
            '      pid=$((4000000001 + $(wc -l <"$STUB_REMOTE/PGREP")))',
            '      mkdir -p "$AGENTBOX_PROC/$pid/fd"',
            '      printf "omp\\0--resume\\0%s\\0" "${r#--resume }" >"$AGENTBOX_PROC/$pid/cmdline"',
            '      echo "$pid (omp) S 1 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 4242" >"$AGENTBOX_PROC/$pid/stat"',
            '      echo "$pid" >>"$STUB_REMOTE/PGREP"',
            "    fi",
            '    echo "@7" ;;',
            '  kill-window) rm -rf "$AGENTBOX_PROC"/4*; : >"$STUB_REMOTE/PGREP" ;;',
            "esac",
            "exit 0",
        ]))
        write_stub(self.stubs, "ssh", "\n".join([
            'while [[ "$1" == -* ]]; do case "$1" in -o|-p|-F|-i|-l) shift 2 ;; *) shift ;; esac; done',
            'host="$1"; shift',
            'printf "%s\\t%s\\n" "$host" "$*" >>"$STUB_REMOTE/SSH"',
            '[[ -e "$STUB_REMOTE/DOWN" ]] && exit 255',
            # A rollback's cancel of the pending box start, refused on its own.
            '[[ -e "$STUB_REMOTE/CANCEL_FAIL" && "$*" == *"/starting/"* ]] && exit 255',
            # git push runs `git-receive-pack '<dir>'` over ssh: run it as it is.
            '[[ "$*" == "bash -lc "* ]] || { export PATH="$STUB_REMOTE/bin:$PATH"; exec bash -c "$*"; }',
            'cmd="$*"; cmd="${cmd#bash -lc }"',
            # A path listed in MISSING is not on the target (the check of the working directory,
            # and of the target's own clone: <common dir>/objects).
            'eval "script=$cmd"',
            'if [[ "$script" == "test -d "* ]] && grep -qxF -- "${script#test -d }" "$STUB_REMOTE/MISSING" 2>/dev/null; then exit 1; fi',
            'export PATH="$STUB_REMOTE/bin:$PATH" STUB_PGREP="$STUB_REMOTE/PGREP" AGENTBOX_PROC="$STUB_REMOTE/proc"',
            'eval "exec bash -c $cmd"',
        ]))
        write_stub(self.stubs, "rsync", 'printf "%s\\n" "$*" >>"$STUB_RSYNC"; exit "${STUB_RSYNC_RC:-0}"')
        write_stub(self.stubs, "setsid", 'printf "%s\\n" "$*" >>"$STUB_SETSID"; exit 0')
        self.local_tmux = self.root / "TMUX_ARGS"
        write_stub(self.stubs, "tmux", "\n".join([
            'case " $* " in',
            '  *" list-panes "*) cat "$STUB_TMUX" 2>/dev/null; exit 0 ;;',
            "esac",
            f'printf "%s\\n" "$*" >>"{self.local_tmux}"',
            'case " $* " in *" display-message "*) printf "42507\\n" ;; esac',
            "exit 0",
        ]))
        self.cutover = self.root / "cutover"
        self.cutover.mkdir()
        (self.cutover / "target-ready").write_text("2026-11-02T12:00:00Z\n", encoding="utf-8")
        self.env.update({
            "STUB_REMOTE": str(self.remote),
            "STUB_RSYNC": str(self.root / "RSYNC"),
            "STUB_SETSID": str(self.root / "SETSID"),
            "AGENTBOX_HOST": "oldbox",
            "AGENTBOX_MOVE_CUTOVER": str(self.cutover),
            "AGENTBOX_MOVE_TARGET_AGENTBOX": "/target/agentbox",
            # No test waits for the target's health check unless it asks to.
            "AGENTBOX_MOVE_READY_WAIT": "0",
            # Nor holds before the hang-up for `agentbox move --go`, unless it asks to.
            "AGENTBOX_MOVE_HOLD_WAIT": "0",
            "AGENTBOX_MOVE_SETTLE": "0",
            "AGENTBOX_MOVE_POLL": "0",
            "AGENTBOX_MOVE_START_WAIT": "0",
            "AGENTBOX_MOVE_HANGUP_WAIT": "0",
            "AGENTBOX_RESPAWN_TRIES": "1",
            "AGENTBOX_RESPAWN_WAIT": "0",
            "XDG_CACHE_HOME": str(self.root / "cache"),
        })

    def moving_session(self, sid: str = TO, pid: int = 999999901, pane: str = "%81") -> Path:
        """A host session ready to move, as the lanes have them: its omp (a pid above any pid_max, so
        the relauncher's hang-up reaches no real process) under a pane shell, working in a worktree
        of a checkout under $SRC with an uncommitted change, a child writing in a lane directory
        outside $SRC, OMP_PROFILE set, and METARQ's nested transcript, whose own directory holds a
        subagent's transcript and one archived behind a symlink to another disk. Returns the path."""
        repo = self.src / "repo" if (self.src / "repo").exists() else self.checkout("repo")
        self.worktree = repo / ".worktrees" / "wt"
        if not self.worktree.exists():
            self.git("worktree", "add", "-q", "-b", "wt", str(self.worktree), cwd=repo)
            (self.worktree / "file").write_text("uncommitted\n", encoding="utf-8")
        self.lane = self.root / "lane"
        self.lane.mkdir(exist_ok=True)
        transcript = self.home / ".omp/agent/sessions/-lane" \
            / "2026-10-14T17-14-49-559Z_01a13b68-d957-74b8-ad2b-b642fd17bfd9" \
            / f"2026-11-02T10-00-00-000Z_{sid}.jsonl"
        own = transcript.with_suffix("")
        own.mkdir(parents=True)
        transcript.write_text("{}\n", encoding="utf-8")
        (own / "Sub.jsonl").write_text("{}\n", encoding="utf-8")
        self.durable = self.root / "durable" / f"old-{sid}.jsonl"
        self.durable.parent.mkdir(exist_ok=True)
        self.durable.write_text("{}\n", encoding="utf-8")
        os.symlink(self.durable, own / "Old.jsonl")
        self.make_proc(pid - 1, comm="bash", ppid=1, cwd=self.worktree)
        self.make_proc(pid, ppid=pid - 1, cwd=self.worktree, writes=(transcript,), environ={"OMP_PROFILE": "pbc"})
        self.make_proc(pid + 5, comm="bash", ppid=pid, cwd=self.lane, writes=(self.lane / "out.log",))
        # A child working in a second checkout under $SRC: a tree every box here has, and the
        # other machine has not.
        self.other = self.src / "other" if (self.src / "other").exists() else self.checkout("other")
        self.make_proc(pid + 6, comm="bash", ppid=pid, cwd=self.other)
        return transcript

    def move_to(self, args: str = "--to devbox", own: int = 999999901, accept: bool = True) -> subprocess.CompletedProcess[str]:
        """`move --to` as the session runs it. The fixture's sessions have live children (the
        tree they work in comes from them), which a move ends: these tests accept that, and
        test_a_move_lists_and_refuses_what_dies_with_the_hang_up tests the refusal."""
        if accept:
            args += " --accept-orphans"
        result = self.run_bash(f"move_own_omp() {{ echo {own}; }}\ncmd_move_to {args}")
        # The hand-off is a background `setsid` (a stub here): wait for it, so it writes nothing
        # into the fixture after the test.
        if "handed to a detached relauncher" in result.stdout:
            for _ in range(100):
                if (self.root / "SETSID").exists():
                    break
                time.sleep(0.05)
        return result

    def jobs(self) -> list[Path]:
        return sorted((self.root / "cache/omp/agentbox/move-to").glob("*.json"))

    def move_log(self) -> list[list[str]]:
        log = self.cutover / "move-log.tsv"
        return [line.split("\t") for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []

    def test_a_session_moves_to_another_machine_and_resumes_there_by_its_transcript_path(self) -> None:
        transcript = self.moving_session()
        self.set_pids(999999901)
        self.set_panes({999999900: ("%81", "agents:4.1")})
        self.target()
        moved = self.move_to()
        self.assertEqual(moved.returncode, 0, moved.stdout + moved.stderr)
        self.assertIn("handed to a detached relauncher", moved.stdout)
        [job] = self.jobs()
        doc = json.loads(job.read_text(encoding="utf-8"))
        [member] = doc["members"]
        self.assertEqual((doc["to"], doc["from"], member["sid"], member["resume"]), ("devbox", "oldbox", self.TO, str(transcript)))
        self.assertEqual(member["spec"], ["-e", "OMP_PROFILE=pbc", "--dir", str(self.worktree), "--dir", str(self.lane)])
        # The transcript tree whole - the file, its own directory with the subagent, the archived
        # file behind the symlink - the worktree under $SRC with its uncommitted change, and the lane.
        for path in (transcript, transcript.with_suffix(""), self.durable, self.worktree, self.other):
            self.assertIn(str(path), doc["mirror"])
        # Git storage merges, and so does the lane, a directory in no git tree; the mirror of a
        # checkout leaves its own .git to the merge.
        self.assertEqual(doc["merge"], [str(self.lane), str(self.src / "other/.git"), str(self.src / "repo/.git")])
        self.assertEqual(doc["exclude"], [str(self.src / "other/.git")])
        # The relauncher, once the old omp has left this machine's process list.
        self.set_pids()
        run = self.run_bash(f'move_to_run "{job}"')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        # Two passes of the same two rsyncs: one before the hang-up, one after (only what changed).
        # Each pass is a copy and a modes-only run after it (read-only source trees), twice: once
        # before the hang-up and once after.
        lines = (self.root / "RSYNC").read_text(encoding="utf-8").splitlines()
        # The first pass walks everything: a mirror, its modes-only run, a merge, its modes run.
        # Every pass after it is a delta of the one before (--files-from), never a fresh walk.
        self.assertGreaterEqual(len(lines), 4, lines)
        for later in lines[4:]:
            self.assertIn("--files-from=", later, lines)
        mirror, merge = lines[0], lines[2]
        for modes in (lines[1], lines[3]):
            self.assertIn("--existing", modes)
            self.assertNotIn("--chmod", modes)
        self.assertIn(f"--delete --backup --backup-dir={self.cutover}/replaced/", mirror)
        self.assertIn("--exclude=agentbox-moved", mirror)
        self.assertTrue(mirror.endswith(" sami@devbox:/"), mirror)
        self.assertIn(f" {transcript} ", mirror)
        # The target has a clone of both repositories (the stub's target is this machine), so
        # their storage is not copied: the trees go through git and their .git stays out of the
        # mirror.
        self.assertIn(f"--exclude={self.src / 'other/.git'}/ ", mirror)
        self.assertIn(f"--exclude={self.worktree}/.git ", mirror)
        self.assertIn("--update --stats --chmod=Du+w --exclude=agentbox-moved", merge)
        self.assertTrue(merge.endswith(f"{self.lane} sami@devbox:/"), merge)
        self.assertIn("through git:", run.stdout)
        for marker in (transcript.with_suffix("") / "agentbox-moved", self.home / ".omp/agentbox-moved" / self.TO):
            body = marker.read_text(encoding="utf-8")
            self.assertIn("from=oldbox\nto=devbox\n", body)
            self.assertIn(f"transcript={transcript}\n", body)
        [started] = [c for c in (self.remote / "TMUX").read_text(encoding="utf-8").splitlines() if c.startswith("new-")]
        self.assertIn("new-session -d -P -F #{window_id} -s moved -n 01a19700", started)
        # A --dir the target lacks is left out at launch there, as on a resume here.
        self.assertIn(f"/target/agentbox new --drop-gone-dirs -e AGENTBOX_MOVED_FROM_WINDOW=", started)
        self.assertIn(f"-e OMP_PROFILE=pbc --dir {self.worktree} --dir {self.lane} -- omp --resume {transcript}", started)
        [line] = self.move_log()
        # The last column is the window the session came from on this machine (<tmux session>:<name>),
        # which the target keeps as the window's @stream option.
        self.assertEqual(line[1:], [self.TO, "g-01a19700", "oldbox", "devbox", "moved", str(transcript), "42507"])
        respawn = [c for c in self.local_tmux.read_text(encoding="utf-8").splitlines() if "respawn-pane" in c]
        self.assertEqual(len(respawn), 1, respawn)
        self.assertIn("-t %81", respawn[0])
        self.assertIn(f"session {self.TO} moved to devbox", respawn[0])

    def clone_fixture(self) -> tuple[Path, Path, Path]:
        """A checkout with an origin, and a `target` whose paths are the same ones under another
        root: move_remote is replaced in the test shell by one that maps the first root onto the
        second and runs the script here, since the stub's target machine is this one."""
        origin = self.root / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
        repo = self.checkout("cloneme")
        self.git("remote", "add", "origin", "https://github.com/x/cloneme", cwd=repo)
        self.git("push", "-q", str(origin), "HEAD:refs/heads/main", cwd=repo)
        far = self.root / "target-root"
        far.mkdir()
        write_stub(self.stubs, "git", "\n".join([
            f'if [ "$1 $2" = "clone --quiet" ]; then exec /usr/bin/git clone --quiet "{origin}" "$4"; fi',
            'exec /usr/bin/git "$@"',
        ]))
        return origin, repo, far

    def clone_run(self, repo: Path, far: Path) -> subprocess.CompletedProcess[str]:
        """move_to_clone_missing for one mirrored tree, against the mapped target."""
        return self.run_bash(
            'move_remote() { local s="${2//$STUB_SRC/$STUB_FAR}"; bash -c "$s"; }\n'
            f'MOVE_TO_MIRROR=("{repo}")\nmove_to_clone_missing devbox',
            {"STUB_SRC": str(self.src), "STUB_FAR": str(far)})

    def test_a_repository_the_target_lacks_is_cloned_there_before_the_hang_up(self) -> None:
        """The object store is the big part of a tree, so the clone is made before the hang-up and
        the tree then goes through git."""
        _origin, repo, far = self.clone_fixture()
        cloned = self.clone_run(repo, far)
        self.assertEqual(cloned.returncode, 0, cloned.stderr)
        # The message names the path as the TARGET has it; the fixture maps that under far.
        self.assertRegex(cloned.stdout, rf"cloned https://github.com/x/cloneme into {repo} on devbox \(\d+s")
        self.assertTrue((far / "cloneme" / ".git" / "objects").is_dir())
        # Run again: the path is there now, so nothing is cloned over it.
        again = self.clone_run(repo, far)
        self.assertEqual(again.stdout, "", again.stdout)

    def test_a_path_that_exists_on_the_target_is_never_cloned_over(self) -> None:
        _origin, repo, far = self.clone_fixture()
        (far / "cloneme").mkdir()
        (far / "cloneme" / "someone-elses-file").write_text("keep\n", encoding="utf-8")
        result = self.clone_run(repo, far)
        self.assertIn("the path exists on the target already", result.stdout)
        self.assertIn(f"{repo} is copied with rsync", result.stdout)
        self.assertEqual((far / "cloneme" / "someone-elses-file").read_text(encoding="utf-8"), "keep\n")
        self.assertFalse((far / "cloneme" / ".git").exists())

    def test_a_clone_that_fails_leaves_the_tree_to_rsync(self) -> None:
        _origin, repo, far = self.clone_fixture()
        write_stub(self.stubs, "git", 'if [ "$1 $2" = "clone --quiet" ]; then echo "fatal: auth" >&2; exit 128; fi\nexec /usr/bin/git "$@"')
        failed = self.clone_run(repo, far)
        self.assertEqual(failed.returncode, 0, failed.stderr)
        self.assertIn("git clone failed", failed.stdout)
        self.assertIn(f"{repo} is copied with rsync", failed.stdout)
        # And one with no usable origin.
        self.git("remote", "set-url", "origin", "/some/local/path", cwd=repo)
        local = self.clone_run(repo, far)
        self.assertIn("which the target may not be able to clone", local.stdout)

    def test_a_mirrored_tree_with_no_commit_is_copied_not_pushed(self) -> None:
        """LINUX-1248, 2026-11-03: its omp's working directory ~/src/machcsl-riscv was on an UNBORN
        branch (everything staged), and once the target had a clone of that repository the push
        failed AFTER the hang-up and the move rolled back. Such a tree is mirrored instead; its
        .git is neither pushed nor merged over the target's clone, and a worktree of the same
        repository still goes through git."""
        self.target()  # the stub target has the repository (it is this machine)
        repo = self.checkout("unborn")
        self.git("checkout", "-q", "--orphan", "fresh", cwd=repo)
        (repo / "staged.txt").write_text("work\n", encoding="utf-8")
        self.git("add", "staged.txt", cwd=repo)
        self.assertNotEqual(subprocess.run(["git", "-C", str(repo), "rev-parse", "-q", "--verify", "HEAD"],
                                           capture_output=True).returncode, 0)
        worktree = repo / ".worktrees" / "wt"
        self.git("worktree", "add", "-q", "-b", "wtb", str(worktree), "main", cwd=repo)
        result = self.run_bash(
            f'MOVE_TO_MIRROR=("{repo}" "{worktree}")\nMOVE_TO_MERGE=("{repo}/.git")\n'
            'move_to_git_native_plan devbox\n'
            'printf "native %s\\n" "${MOVE_TO_NATIVE[@]}"\nprintf "unborn %s\\n" "${MOVE_TO_UNBORN[@]}"\n'
            'printf "merge %s\\n" ${MOVE_TO_MERGE[@]+"${MOVE_TO_MERGE[@]}"}\n'
            'printf "exclude %s\\n" "${MOVE_TO_EXCLUDE[@]}"\n'
            'move_to_git_native_push devbox || echo "PUSH FAILED: $MOVE_TO_WHY"')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("PUSH FAILED", result.stdout)
        self.assertIn(f"native {worktree}\t{repo}/.git\n", result.stdout)
        self.assertIn(f"unborn {repo}/.git\n", result.stdout)
        self.assertNotIn(str(repo), result.stdout.split("merge ")[1].split("\n")[0] + "x")
        self.assertIn(f"exclude {worktree}/.git\n", result.stdout)
        self.assertIn(f"exclude {repo}/.git\n", result.stdout)

    def test_a_tree_whose_repository_the_target_lacks_copies_its_storage(self) -> None:
        """Without a clone of the repository on the target there is nothing to push into, so the
        tree's git storage is merged by rsync as before."""
        self.moving_session()
        self.set_pids(999999901)
        self.set_panes({999999900: ("%81", "agents:4.1")})
        self.target()
        self.assertEqual(self.move_to().returncode, 0)
        [job] = self.jobs()
        (self.remote / "MISSING").write_text(f"{self.src / 'repo/.git'}/objects\n{self.src / 'other/.git'}/objects\n", encoding="utf-8")
        self.set_pids()
        run = self.run_bash(f'move_to_run "{job}"')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertNotIn("through git:", run.stdout)
        _m1, _g1, _mirror, merge = (self.root / "RSYNC").read_text(encoding="utf-8").splitlines()
        self.assertIn(f"{self.lane} {self.src / 'other/.git'} {self.src / 'repo/.git'} sami@devbox:/", merge)

    def test_a_tree_moved_through_git_keeps_its_branch_head_and_uncommitted_change(self) -> None:
        """On the target the tree is a worktree of the target's own clone, on the same branch at
        the same commit, with the change it had uncommitted. The stub's target is this machine,
        so the tree is checked where it is, after the move rebuilt its index from HEAD."""
        self.moving_session()
        head = subprocess.run(["git", "-C", str(self.worktree), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        self.set_pids(999999901)
        self.set_panes({999999900: ("%81", "agents:4.1")})
        self.target()
        self.assertEqual(self.move_to().returncode, 0)
        [job] = self.jobs()
        self.set_pids()
        run = self.run_bash(f'move_to_run "{job}"')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        git = lambda *a: subprocess.run(["git", "-C", str(self.worktree), *a], capture_output=True, text=True).stdout.strip()
        self.assertEqual(git("rev-parse", "HEAD"), head)
        self.assertEqual(git("symbolic-ref", "--short", "HEAD"), "wt")
        self.assertEqual(git("status", "--short"), "M file")
        refs = subprocess.run(["git", "-C", str(self.src / "repo"), "for-each-ref", "refs/agentbox-move"], capture_output=True, text=True).stdout
        self.assertEqual(refs, "")

    def test_a_move_in_flight_is_marked_active_and_unmarked_on_every_outcome(self) -> None:
        """omp-memory-watch resumed LINUX-1248 on this host 6 s after the 04:10Z hang-up
        (2026-11-03), since nothing told it a `move --to` was in flight. The relauncher writes
        $MOVE_TO_JOBS/active/<sid> before the hang-up and removes it on every exit."""
        self.moving_session()
        self.set_pids(999999901)
        self.set_panes({999999900: ("%81", "agents:4.1")})
        self.target()
        active = self.root / "cache/omp/agentbox/move-to/active" / self.TO
        seen = self.root / "ACTIVE_DURING_COPY"
        # rsync, which runs after the hang-up, records the active file as it finds it.
        write_stub(self.stubs, "rsync", f'cat "{active}" >>"{seen}" 2>/dev/null || echo MISSING >>"{seen}"; printf "%s\\n" "$*" >>"$STUB_RSYNC"')
        self.assertEqual(self.move_to().returncode, 0)
        [job] = self.jobs()
        self.set_pids()
        moved = self.run_bash(f'move_to_run "{job}"')
        self.assertEqual(moved.returncode, 0, moved.stdout + moved.stderr)
        during = seen.read_text(encoding="utf-8")
        self.assertRegex(during, r"^pid=\d+\ntarget=devbox\nstart=\d+\n")
        self.assertFalse(active.exists())
        # Refused, before any hang-up: nothing left behind either.
        (self.cutover / "target-ready").unlink()
        refused = self.run_bash(f'move_to_run "{job}"')
        self.assertEqual(refused.returncode, 2)
        self.assertFalse(active.exists())

    def test_a_relauncher_stopped_by_a_signal_says_where_and_leaves_no_active_file(self) -> None:
        self.moving_session()
        self.set_pids(999999901)
        self.set_panes({999999900: ("%81", "agents:4.1")})
        self.target()
        active = self.root / "cache/omp/agentbox/move-to/active" / self.TO
        # The pass AFTER the hang-up is the one whose backup directory carries the bare stamp;
        # every pass before it has a suffix (-before, -hold, -go). Only that one waits here.
        write_stub(self.stubs, "rsync",
                   'case "$*" in *-before\\ *|*-hold\\ *|*-go\\ *) exit 0 ;; esac\n'
                   'case "$*" in *--backup-dir=*) ;; *) exit 0 ;; esac\n'
                   f'touch "{self.root}/IN_RSYNC"; sleep 60')
        # The session writes while the move holds, so the pass after the hang-up has work: a
        # pass with nothing to send runs no rsync at all.
        changing = self.worktree / "written-during-the-hold.txt"
        self.assertEqual(self.move_to().returncode, 0)
        [job] = self.jobs()
        self.set_pids()
        log = self.root / "relauncher.log"
        proc = subprocess.Popen(["bash", "-c", f'source "{AGENTBOX}"\n'
                                 f'move_to_hold() {{ printf x >"{changing}"; }}\n'
                                 f'move_to_run "{job}"'],
                                env=self.env, stdout=log.open("w"), stderr=subprocess.STDOUT, start_new_session=True)
        # A relauncher this test leaves behind runs on the real host (two of them waited in their
        # hold for an hour on 2026-11-03), so it dies with the test whatever happens here.
        self.addCleanup(self.end_relauncher, proc)
        # The pass after the hang-up is the fifth rsync of the move, and every pass before it
        # walks its paths, so on a loaded host this wait is minutes, not seconds.
        for _ in range(int(self.TIMEOUT * 10)):
            if (self.root / "IN_RSYNC").exists():
                break
            time.sleep(0.1)
        self.assertTrue((self.root / "IN_RSYNC").exists(), log.read_text(encoding="utf-8"))
        self.assertTrue(active.exists())
        proc.send_signal(15)
        proc.wait(timeout=30)
        text = log.read_text(encoding="utf-8")
        self.assertIn("stopped by signal TERM; state: copying to devbox (the sessions are hung up)", text)
        self.assertFalse(active.exists())

    def test_the_other_branches_are_pushed_before_the_hang_up(self) -> None:
        """LINUX-1248, 04:10Z: the push of every branch from a blob:none clone ran 3+ min after
        the hang-up. It now runs before; after the hang-up only HEAD goes."""
        self.moving_session()
        self.set_pids(999999901)
        self.set_panes({999999900: ("%81", "agents:4.1")})
        self.target()
        self.assertEqual(self.move_to().returncode, 0)
        [job] = self.jobs()
        self.set_pids()
        run = self.run_bash(f'move_to_run "{job}"')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        # The stub's target is this machine, so its branches are there already: once per
        # repository, before the hang-up. Two worktrees of repo/.git (wt) and other/.git: two lines.
        lines = [l for l in run.stdout.splitlines() if "branch" in l and ".git" in l]
        self.assertEqual(len(lines), 2, run.stdout)
        self.assertTrue(all("are on devbox already" in l or "before the hang-up" in l for l in lines), lines)
        hangup = run.stdout.index("hung up after")
        self.assertLess(run.stdout.index(lines[-1]), hangup, run.stdout)

    def test_a_first_copy_runs_before_the_hang_up_and_each_pass_logs_its_cost(self) -> None:
        """LINUX-1248's copy was 3.69 GB in 178,633 files, 760 s of its downtime (2026-11-03). The
        same two rsyncs now run once before the hang-up and again after it."""
        self.moving_session()
        self.set_pids(999999901)
        self.set_panes({999999900: ("%81", "agents:4.1")})
        self.target()
        write_stub(self.stubs, "rsync", 'printf "%s\\n" "$*" >>"$STUB_RSYNC"; '
                   'printf "Number of regular files transferred: 7\\nTotal transferred file size: 1,234 bytes\\nLiteral data: 1,000 bytes\\n"')
        self.assertEqual(self.move_to().returncode, 0)
        [job] = self.jobs()
        self.set_pids()
        run = self.run_bash(f'move_to_run "{job}"')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        before = run.stdout.index("rsync mirror before the hang-up: ")
        hangup = run.stdout.index("hung up after")
        after = run.stdout.index("rsync mirror after the hang-up: ")
        self.assertLess(before, hangup)
        self.assertLess(hangup, after)
        self.assertIn("rsync mirror before the hold", run.stdout)
        self.assertIn("rsync mirror at the go", run.stdout)
        self.assertIn("7 files transferred, 1,234 bytes transferred file size, 1,000 bytes literal data", run.stdout)

    def test_a_file_that_vanishes_while_it_is_copied_does_not_fail_the_move(self) -> None:
        """A session at work writes and removes temporary files, so a pass of the copy ends with
        rsync 24 ("some files vanished"). LANE-C's merge pass warned on
        `...packet.json_.gstmp` (2026-11-03 14:46Z). 24 is a warning: what the first pass missed
        the pass after the hang-up copies, and there a file the session no longer has is gone."""
        self.moving_session()
        self.set_pids(999999901)
        self.set_panes({999999900: ("%81", "agents:4.1")})
        self.target()
        write_stub(self.stubs, "rsync", 'printf "%s\\n" "$*" >>"$STUB_RSYNC"; '
                   'case "$*" in *--update*) echo \'file has vanished: "/w/x.json_.gstmp"\' >&2; exit 24;; esac; exit 0')
        self.assertEqual(self.move_to().returncode, 0)
        [job] = self.jobs()
        self.set_pids()
        run = self.run_bash(f'move_to_run "{job}"', {"AGENTBOX_MOVE_RSYNC_TICK": "0"})
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("1 file(s) vanished while they were copied; the move goes on (rsync exit 24)", run.stdout)
        self.assertNotIn("refused, nothing was hung up", run.stdout)
        self.assertEqual(self.move_log()[0][5], "moved")
        # Transient files are left out of both passes, so most of them never raise the warning.
        sent = (self.root / "RSYNC").read_text(encoding="utf-8")
        for pattern in ("--exclude=*.gstmp", "--exclude=.#*", "--exclude=*.tmp.*"):
            self.assertEqual(sent.count(pattern), len(sent.splitlines()),
                             f"{pattern} in every rsync of the move: {sent}")

    def test_a_long_copy_says_where_it_is_and_an_rsync_that_fails_refuses_with_a_notice(self) -> None:
        """LANE-C's pre-hang-up merge ran 76 minutes at 4 MB/s and logged nothing until it ended,
        so its owners read it as a dead move (2026-11-03). Each pass now says where it is every
        AGENTBOX_MOVE_RSYNC_TICK seconds, in the log and to each session that waits for it."""
        self.moving_session()
        self.set_pids(999999901)
        self.set_panes({999999900: ("%81", "agents:4.1")})
        self.target()
        write_stub(self.stubs, "rsync", 'printf "%s\\n" "$*" >>"$STUB_RSYNC"; '
                   'printf "  1,048,576  37%%    4.50MB/s    0:01:23 (xfr#9, to-chk=11/20)\\r"; sleep 3')
        self.assertEqual(self.move_to().returncode, 0)
        [job] = self.jobs()
        self.set_pids()
        run = self.run_bash(f'move_to_run "{job}"', {"AGENTBOX_MOVE_RSYNC_TICK": "1"})
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertRegex(run.stdout, r"rsync mirror: before the hang-up for \d+s: 1,048,576 37% 4.50MB/s 0:01:23")
        self.assertIn("--info=progress2", (self.root / "RSYNC").read_text(encoding="utf-8"))
        sent = (self.root / "STUB_ENVOY").read_text(encoding="utf-8")
        self.assertIn("your move goes on.", sent)
        self.assertIn(self.TO, sent)

    def test_a_copy_that_fails_before_the_hang_up_tells_the_lead_and_box_health(self) -> None:
        """A move that stops before the hang-up leaves every session running, so none is DOWN and
        nobody watches the log. Only the sessions were told before (2026-11-03)."""
        self.moving_session()
        self.set_pids(999999901)
        self.set_panes({999999900: ("%81", "agents:4.1")})
        self.target()
        write_stub(self.stubs, "rsync", 'printf "%s\\n" "$*" >>"$STUB_RSYNC"; echo "rsync: connection unexpectedly closed" >&2; exit 12')
        self.assertEqual(self.move_to().returncode, 0)
        [job] = self.jobs()
        self.set_pids()
        run = self.run_bash(f'move_to_run "{job}"', {"AGENTBOX_MOVE_RSYNC_TICK": "0"})
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertIn("refused, nothing was hung up", run.stdout)
        sent = (self.root / "STUB_ENVOY").read_text(encoding="utf-8")
        self.assertIn("notifications.role.lead-box-health", sent)
        self.assertIn("did NOT start and these sessions were not hung up", sent)
        self.assertIn(self.TO, sent)

    def test_a_moved_session_keeps_the_name_of_the_window_it_came_from(self) -> None:
        """A stream's windows are named for the stream and pilot.py counts a stream's sessions by
        that name, which `session:index.pane` does not carry. The job, the move log, the box's
        environment (AGENTBOX_MOVED_FROM_WINDOW) and the target window's @stream option hold it."""
        self.moving_session()
        self.set_pids(999999901)
        self.set_panes({999999900: ("%81", "agents:4.1")})
        self.target()
        # This machine's tmux answers the window query with the stream's name.
        write_stub(self.stubs, "tmux", "\n".join([
            'case " $* " in',
            '  *" list-panes "*) cat "$STUB_TMUX" 2>/dev/null; exit 0 ;;',
            "esac",
            f'printf "%s\\n" "$*" >>"{self.local_tmux}"',
            'case " $* " in',
            '  *"#{session_name}:#{window_name}"*) printf "agents:lane-c\\n" ;;',
            '  *" display-message "*) printf "42507\\n" ;;',
            "esac",
            "exit 0",
        ]))
        self.assertEqual(self.move_to().returncode, 0)
        [job] = self.jobs()
        self.assertEqual(json.loads(job.read_text(encoding="utf-8"))["members"][0]["window"], "agents:lane-c")
        self.set_pids()
        run = self.run_bash(f'move_to_run "{job}"', {"AGENTBOX_MOVE_RSYNC_TICK": "0"})
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        # The target: the box's environment carries it, and the window it runs in keeps it.
        started = (self.remote / "TMUX").read_text(encoding="utf-8")
        self.assertIn("-e AGENTBOX_MOVED_FROM_WINDOW=agents:lane-c", started)
        self.assertIn("set-option -w -t", started)
        self.assertIn("@stream lane-c", started)
        self.assertEqual(self.move_log()[0][7], "agents:lane-c")

    def test_a_read_only_source_tree_arrives_twice_and_keeps_its_modes(self) -> None:
        """LANE-C freezes its verification sets (0555 directories, 0444 files, checked against
        sha256 manifests). rsync gives a directory its final mode BEFORE it fills it, so the
        0555 directory of the first pass stopped the second with "mkdir: Permission denied"
        (rsync 23, 16:19Z and 16:30Z on 2026-11-03). Real rsync here, over the stub ssh, with the
        target's root under the fixture: both passes land and the modes end as the source's."""
        self.target()
        # Real rsync on this side; on the "target" side the stub puts every absolute path under
        # one root, so the copy is observable and nothing outside the fixture is written.
        write_stub(self.stubs, "rsync", 'exec /usr/bin/rsync "$@"')
        far = self.remote / "root"
        far.mkdir()
        write_stub(self.remote / "bin", "rsync", "\n".join([
            'args=("$@")',
            'args[$(( ${#args[@]} - 1 ))]="$STUB_REMOTE/root/"',
            'exec /usr/bin/rsync "${args[@]}"',
        ]))
        frozen = self.root / "frozen"
        (frozen / "repro").mkdir(parents=True)
        (frozen / "repro" / "evidence.txt").write_text("one\n", encoding="utf-8")
        for path, mode in ((frozen / "repro" / "evidence.txt", 0o444), (frozen / "repro", 0o555), (frozen, 0o555)):
            path.chmod(mode)
        # The fixture's tearDown removes the root, so a frozen directory must be writable again
        # whatever the test does.
        def thaw() -> None:
            for p in (frozen, frozen / "repro", frozen / "repro" / "gr17"):
                if p.exists():
                    p.chmod(0o755)
        self.addCleanup(thaw)
        run = self.run_bash(f'MOVE_TO_MIRROR=("{frozen}")\nMOVE_TO_MERGE=()\nmove_to_copy devbox st1',
                            {"AGENTBOX_MOVE_RSYNC_TICK": "0"})
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        there = Path(str(far) + str(frozen))
        self.assertEqual((there / "repro/evidence.txt").read_text(encoding="utf-8"), "one\n")
        self.assertEqual(oct((there / "repro").stat().st_mode)[-3:], "555")
        self.assertEqual(oct((there / "repro/evidence.txt").stat().st_mode)[-3:], "444")
        # A second pass, as every move runs one after the hang-up, with a directory and a file
        # the first lacked: the target must mkdir inside a 0555 directory of the first pass.
        (frozen / "repro").chmod(0o755)
        (frozen / "repro" / "gr17").mkdir()
        (frozen / "repro" / "gr17" / "evidence.txt").write_text("two\n", encoding="utf-8")
        (frozen / "repro" / "gr17" / "evidence.txt").chmod(0o444)
        (frozen / "repro" / "gr17").chmod(0o555)
        (frozen / "repro").chmod(0o555)
        self.addCleanup(lambda: (frozen / "repro" / "gr17").exists() and (frozen / "repro" / "gr17").chmod(0o755))
        again = self.run_bash(f'MOVE_TO_MIRROR=("{frozen}")\nMOVE_TO_MERGE=()\nmove_to_copy devbox st2',
                              {"AGENTBOX_MOVE_RSYNC_TICK": "0"})
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual((there / "repro/gr17/evidence.txt").read_text(encoding="utf-8"), "two\n")
        self.assertEqual(oct((there / "repro/gr17").stat().st_mode)[-3:], "555")
        self.assertEqual(oct((there / "repro").stat().st_mode)[-3:], "555")

    def test_a_path_gone_since_the_job_is_dropped_before_the_hang_up(self) -> None:
        """A move holds for the session's `agentbox move --go`, and a session at work removes a
        tree meanwhile: the librarian removed ~/src/legion-turnfix during the hold, and the native
        push failed AFTER the hang-up with "has no commit to push", rolling the group back
        (17:27:06Z, 2026-11-03). Every path is re-checked right before the hang-up."""
        self.moving_session()
        self.set_pids(999999901)
        self.set_panes({999999900: ("%81", "agents:4.1")})
        self.target()
        self.assertEqual(self.move_to().returncode, 0)
        [job] = self.jobs()
        doc = json.loads(job.read_text(encoding="utf-8"))
        gone = doc["mirror"][0]
        self.set_pids()
        # The session removes it between the job and the hang-up, as a lane does during the hold.
        run = self.run_bash(f'orig_move_to_hold() {{ :; }}\nmove_to_hold() {{ rm -rf "{gone}"; }}\nmove_to_run "{job}"',
                            {"AGENTBOX_MOVE_RSYNC_TICK": "0"})
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn(f"gone since the job: not copied: {gone}", run.stdout)
        self.assertEqual(self.move_log()[0][5], "moved")
        for line in (self.root / "RSYNC").read_text(encoding="utf-8").splitlines()[2:]:
            self.assertNotIn(f" {gone} ", f" {line} ", "a path that is gone is in no later pass")

    def test_only_one_relauncher_runs_for_a_session(self) -> None:
        """Two `move --to-run` for the librarian ran at once at 17:32-17:34Z on 2026-11-03: the
        first had not yet written its claim, so the stop found no pid. The relauncher takes a
        per-session lock before any check and says who holds it."""
        self.moving_session()
        self.set_pids(999999901)
        self.set_panes({999999900: ("%81", "agents:4.1")})
        self.target()
        self.assertEqual(self.move_to().returncode, 0)
        [job] = self.jobs()
        self.set_pids()
        locks = self.root / "cache/omp/agentbox/move-to/locks"
        locks.mkdir(parents=True, exist_ok=True)
        lock = locks / f"{self.TO}.lock"
        lock.write_text("pid=4242424\n", encoding="utf-8")
        with lock.open("a") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            refused = self.run_bash(f'move_to_run "{job}"', {"AGENTBOX_MOVE_RSYNC_TICK": "0"})
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertIn(f"another move of {self.TO} runs (pid 4242424); nothing was hung up", refused.stderr)
        self.assertFalse((self.root / "RSYNC").exists(), "the second relauncher copies nothing")
        # With the lock free the same job runs, and its own claim is the only one it removes.
        ran = self.run_bash(f'move_to_run "{job}"', {"AGENTBOX_MOVE_RSYNC_TICK": "0"})
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_after_the_first_pass_only_what_changed_is_copied(self) -> None:
        """The pass after the hang-up re-walked every tree in full: 14 mirrored and 192 merged
        paths, ~49 GB, 5.8 GB read in 14 min with the librarian down (18:19-18:37Z, 2026-11-03).
        Every pass after the first sends a --files-from list of what changed since the pass
        before it, with what went since in it too, which rsync deletes on the target."""
        self.moving_session()
        self.set_pids(999999901)
        self.set_panes({999999900: ("%81", "agents:4.1")})
        self.target()
        self.assertEqual(self.move_to().returncode, 0)
        [job] = self.jobs()
        doc = json.loads(job.read_text(encoding="utf-8"))
        tree = next(Path(p) for p in doc["mirror"] if Path(p).is_dir())
        gone = tree / "removed-during-the-move.txt"
        gone.write_text("old\n", encoding="utf-8")
        self.set_pids()
        # The session works while the move holds: it writes one file and removes another.
        run = self.run_bash(
            f'move_to_hold() {{ printf x >"{tree}/written-during-the-move.txt"; rm -f "{gone}"; }}\n'
            f'move_to_run "{job}"', {"AGENTBOX_MOVE_RSYNC_TICK": "0"})
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        passes = [line for line in (self.root / "RSYNC").read_text(encoding="utf-8").splitlines()
                  if "--files-from" not in line and "--existing" not in line]
        self.assertEqual(len(passes), 2, f"only the first mirror and merge walk everything: {passes}")
        deltas = [line for line in (self.root / "RSYNC").read_text(encoding="utf-8").splitlines()
                  if "--files-from" in line]
        self.assertTrue(deltas, "every later pass is a delta")
        for line in deltas:
            self.assertNotIn(f"{tree} ", line, "a delta names no whole tree, only its list")
        # The mirror delta deletes on the target what the session removed here.
        self.assertTrue([line for line in deltas if "--delete-missing-args" in line], deltas)
        # Three delta rounds while the session works or stops: before the hold, at the go, after
        # the hang-up.
        for label in ("before the hold", "at the go", "after the hang-up"):
            self.assertIn(label, run.stdout, run.stdout)

    def test_a_moved_session_s_services_are_stopped_on_the_machine_it_left(self) -> None:
        """omp's worker broker is parented to init and outlives the omp, so a moved session's
        services keep running on the source: the chief's six loops ran twice for five hours after
        its move (two sweeps, two ticks, two pilot schedulers, 2026-11-03). After the session is
        confirmed on the target, each service of its own and each of its scopes is stopped here,
        and whatever will not stop is said to the session and to the box-health role."""
        self.moving_session()
        self.set_pids(999999901)
        self.set_panes({999999900: ("%81", "agents:4.1")})
        self.target()
        # The broker's layout: <omp run>/daemons/<broker>/daemons/<name>/meta.json.
        run = self.root / "omp-run"
        broker = run / "daemons" / "22b63d2a564ee63e"
        (broker / "daemons" / "Sweep").mkdir(parents=True)
        (broker / "daemons" / "Ticks").mkdir(parents=True)
        (broker / "daemons" / "Gone").mkdir(parents=True)
        (broker / "scope.json").write_text(json.dumps({"projectDir": str(self.lane)}), encoding="utf-8")
        for name, owner, state in (("Sweep", self.TO, "running"), ("Ticks", self.TO, "ready"),
                                   ("Gone", self.TO, "exited"), ):
            (broker / "daemons" / name / "meta.json").write_text(
                json.dumps({"daemon": {"name": name, "owner": owner, "state": state}}), encoding="utf-8")
        other = broker / "daemons" / "SomeoneElse"
        other.mkdir()
        (other / "meta.json").write_text(
            json.dumps({"daemon": {"name": "SomeoneElse", "owner": self.TO2, "state": "running"}}), encoding="utf-8")
        calls = self.root / "OMP_PS"
        write_stub(self.stubs, "omp", f'printf "%s\\n" "$*" >>"{calls}"; case "$*" in *Ticks*) exit 1 ;; esac; exit 0')
        write_stub(self.stubs, "systemctl", "\n".join([
            f'printf "%s\\n" "$*" >>"{self.root}/SYSTEMCTL"',
            'case " $* " in',
            f'  *" list-units "*) echo "run-c{self.TO}.scope loaded active running" ;;',
            "esac",
            "exit 0",
        ]))
        self.assertEqual(self.move_to().returncode, 0)
        [job] = self.jobs()
        self.set_pids()
        run_out = self.run_bash(f'move_to_run "{job}"',
                                {"AGENTBOX_MOVE_RSYNC_TICK": "0", "OMP_RUN_DIR": str(run)})
        self.assertEqual(run_out.returncode, 0, run_out.stdout + run_out.stderr)
        asked = calls.read_text(encoding="utf-8")
        self.assertIn(f"ps --dir {self.lane} stop Sweep --timeout 10", asked)
        self.assertIn(f"ps --dir {self.lane} stop Ticks --timeout 10", asked)
        self.assertNotIn("Gone", asked, "a service that has exited is not stopped again")
        self.assertNotIn("SomeoneElse", asked, "another session's service is left alone")
        self.assertIn(f"stop run-c{self.TO}.scope", (self.root / "SYSTEMCTL").read_text(encoding="utf-8"))
        self.assertIn(f"{self.TO}: service Sweep ({self.lane}) stopped here", run_out.stdout)
        self.assertIn(f"{self.TO}: service Ticks ({self.lane}) would NOT stop here", run_out.stdout)
        sent = (self.root / "STUB_ENVOY").read_text(encoding="utf-8")
        self.assertIn("these background services stay on oldbox", sent)
        self.assertIn("notifications.role.lead-box-health", sent)

    def test_an_omp_is_the_session_only_when_it_holds_or_resumes_it(self) -> None:
        """A lane's prompt held another session's id, so an argv substring match hung up the wrong
        omp: CAS's lane R17Pin was hung up and CAS was resumed on the target while the real CAS
        kept running (20:57Z, 2026-11-03). An omp is a session's when it holds that session's
        transcript or its command line resumes it."""
        sid = self.TO
        other = self.TO2
        cases = [
            (f"/x/bin/omp --resume /home/sami/.omp/agent/sessions/-w/2026-11-03T00-00-00-000Z_{sid}.jsonl", "", True),
            (f"/x/bin/omp --resume {sid}", "", True),
            (f"/x/bin/omp --session={sid} -p hi", "", True),
            ("/x/bin/omp -p", f"/home/sami/.omp/agent/sessions/-w/2026-11-03T00-00-00-000Z_{sid}.jsonl", True),
            (f"/x/bin/omp -p Review the work of session {sid} and report", "", False),
            (f"/x/bin/omp --resume {other}", f"/w/2026-11-03T00-00-00-000Z_{other}.jsonl", False),
            (f"/x/bin/omp --model gpt --resume {other} --dir /mnt/nvme/{sid}-lane", "", False),
            # A subagent's transcript lives UNDER its parent's directory, so the parent's id is in
            # the path of every child: only the file's own name says whose it is. agentbox halted
            # FRAC's move for a pid that was BET-64's session in a transcript under FRAC's
            # directory (22:57Z, 2026-11-03).
            (f"/x/bin/omp --resume /home/sami/.omp/agent/sessions/-w/2026-10-14T17-14-49-559Z_{sid}/"
             f"2026-10-25T02-17-36-102Z_{other}.jsonl", "", False),
            (f"/x/bin/omp -p", f"/home/sami/.omp/agent/sessions/-w/2026-10-14T17-14-49-559Z_{sid}/"
             f"2026-10-25T02-17-36-102Z_{other}.jsonl", False),
            (f"/x/bin/omp --resume /home/sami/.omp/agent/sessions/-w/2026-10-14T17-14-49-559Z_{other}/"
             f"2026-10-25T02-17-36-102Z_{sid}.jsonl", "", True),
        ]
        for cmd, fds, expected in cases:
            result = self.run_bash(f'move_omp_is_session {sid} {shlex.quote(cmd)} {shlex.quote(fds)} && echo YES || echo NO')
            self.assertEqual(result.stdout.strip(), "YES" if expected else "NO", f"{cmd!r} {fds!r}: {result.stdout}")

    def test_a_commit_github_has_is_fetched_by_the_target_not_pushed_into_its_clone(self) -> None:
        """Pushes into sami-devbox's blob:none partial clone of oss-verify-proofs-internal-src
        (~11k packs) failed for three moves on 2026-11-03: non-fast-forward, exhausted file
        descriptors, promisor fetches. A commit the repository's GitHub origin has already is
        fetched there from origin; only a commit GitHub lacks is pushed."""
        self.target()
        repo = self.checkout("fetchme")
        self.git("worktree", "add", "-q", "-b", "w1", str(self.root / "w1"), cwd=repo)
        log = self.root / "GITLOG"
        # `git branch -r --contains` says the commit is on origin; everything else is the real git.
        write_stub(self.stubs, "git", f'echo "$*" >>"{log}"\n'
                   'case " $* " in\n'
                   '  *" branch -r --contains "*) echo "  origin/main"; exit 0 ;;\n'
                   '  *" push "*) echo "push refused" >&2; exit 1 ;;\n'
                   '  *" fetch "*" origin "*) exit 0 ;;\n'
                   'esac\nexec /usr/bin/git "$@"')
        result = self.run_bash(
            f'MOVE_TO_NATIVE=("{self.root}/w1\t{repo}/.git")\n'
            'move_to_git_native_push devbox')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        asked = log.read_text(encoding="utf-8")
        self.assertIn(f"--git-dir={repo}/.git fetch --quiet --no-write-fetch-head origin", asked)
        self.assertFalse([line for line in asked.splitlines() if " push " in f" {line} "],
                         f"a commit GitHub has is never pushed: {asked}")

    def test_git_storage_the_target_already_has_is_not_copied(self) -> None:
        """INFRA-1018, 2026-11-03: --leave of ~/src/machcsl-riscv sent its 8.96 GB object store to a
        target that had the clone. A merged common dir whose objects the target has is dropped
        from the copy, with the store its objects link to."""
        self.target()
        repo = self.checkout("held")
        store = self.root / "objects-elsewhere"
        shutil.move(str(repo / ".git" / "objects"), str(store))
        os.symlink(store, repo / ".git" / "objects")
        plan = (f'MOVE_TO_MIRROR=()\nMOVE_TO_MERGE=("{repo}/.git" "{store}" "{self.root}/lane")\n'
                'move_to_git_native_plan devbox\nprintf "merge %s\\n" "${MOVE_TO_MERGE[@]}"')
        (self.root / "lane").mkdir()
        held = self.run_bash(plan)
        self.assertEqual(held.stdout.splitlines(), [f"merge {self.root}/lane"], held.stderr)
        # The store merged on its own, without its checkout's .git (PROCS, 06:53Z): it counts as
        # the checkout under $SRC whose .git/objects resolves to it.
        alone = self.run_bash(f'MOVE_TO_MIRROR=()\nMOVE_TO_MERGE=("{store}" "{self.root}/lane")\n'
                              'move_to_git_native_plan devbox\nprintf "merge %s\\n" "${MOVE_TO_MERGE[@]}"')
        self.assertEqual(alone.stdout.splitlines(), [f"merge {self.root}/lane"], alone.stderr)
        (self.remote / "MISSING").write_text(f"{repo}/.git/objects\n", encoding="utf-8")
        lacked = self.run_bash(plan)
        self.assertEqual(sorted(lacked.stdout.splitlines()),
                         sorted([f"merge {repo}/.git", f"merge {store}", f"merge {self.root}/lane"]))

    def test_a_member_resumes_in_its_transcript_header_s_cwd_not_the_shell_s(self) -> None:
        """omp adopts the transcript header's cwd on every resume. LINUX-1248's box worked in its
        worktree while omp went back to the header's ~/src/machcsl-riscv (2026-11-03)."""
        transcript = self.moving_session()
        header_cwd = self.src / "repo"
        transcript.write_text(json.dumps({"type": "session", "id": self.TO, "cwd": str(header_cwd)}) + "\n", encoding="utf-8")
        self.target()
        env = {"AGENTBOX_BOX": "agentbox-0123456789ab", "AGENTBOX_DIRS": str(self.lane),
               "OMP_SESSION_ID": self.TO, "TMUX_PANE": "%81"}
        moved = self.run_bash(f'move_own_omp() {{ echo 999999901; }}\ncd "{self.lane}"\ncmd_move_to --to devbox --accept-orphans', env)
        self.assertEqual(moved.returncode, 0, moved.stdout + moved.stderr)
        [job] = self.jobs()
        self.assertEqual(json.loads(job.read_text(encoding="utf-8"))["members"][0]["cwd"], str(header_cwd))

    def test_cwd_re_roots_the_transcript_on_the_target_with_one_file_for_the_id(self) -> None:
        """--cwd: the transcript moves to that directory's bucket with omp's own pack and unpack,
        which rewrite the header and remove the copy in the old bucket; the subagents' transcripts,
        which pack does not carry, are put back beside the new file. The stub omp does what omp
        does to the file."""
        self.target()
        old = self.home / ".omp/agent/sessions/-old" / f"2026-11-03T00-00-00-000Z_{self.TO}.jsonl"
        old.with_suffix("").mkdir(parents=True)
        old.write_text(json.dumps({"type": "session", "id": self.TO, "cwd": "/old"}) + "\n", encoding="utf-8")
        (old.with_suffix("") / "Sub.jsonl").write_text("{}\n", encoding="utf-8")
        new_cwd = self.root / "newcwd"
        new_cwd.mkdir()
        sessions = self.home / ".omp/agent/sessions"
        write_stub(self.remote / "bin", "omp", "\n".join([
            'case "$2" in',
            '  pack) printf "%s" "$3" >"$4" ;;',
            '  unpack) src="$(cat "$3")"; d="$5"; b="' + str(sessions) + '/-$(echo "$d" | tr / -)"; mkdir -p "$b"; '
            'new="$b/$(basename "$src")"; jq -c --arg c "$d" ".cwd = \\$c" "$src" >"$new"; rm -f "$src"; rm -rf "${src%.jsonl}"; '
            'echo "Replaced the local copy"; echo "$new" ;;',
            'esac']))
        result = self.run_bash(f'move_to_reroot devbox "{old}" "{new_cwd}"')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        new = Path(result.stdout.strip().splitlines()[-1])
        self.assertNotEqual(new, old)
        self.assertEqual(json.loads(new.read_text(encoding="utf-8"))["cwd"], str(new_cwd))
        self.assertTrue((new.with_suffix("") / "Sub.jsonl").exists())
        self.assertFalse(old.exists())
        self.assertEqual(len(list(sessions.glob(f"*/*_{self.TO}.jsonl"))), 1)

    def test_an_archived_transcript_is_found_through_its_open_file(self) -> None:
        """SPEEDCOACH's 01a16c67 (2026-11-03): its transcript is open at its target under
        /mnt/durable/<user>/omp-sessions-moved, a path with no `sessions/` part."""
        archived = self.root / "durable" / "omp-sessions-moved" / "-lane" / f"2026-11-01T09-00-00-000Z_{self.TO}.jsonl"
        archived.parent.mkdir(parents=True)
        archived.write_text("{}\n", encoding="utf-8")
        self.make_proc(999999993, ppid=1, cwd=self.root, writes=(archived,))
        found = self.run_bash(f"move_transcript 999999993 {self.TO}")
        self.assertEqual(found.stdout.strip(), str(archived), found.stderr)

    def test_a_dry_run_prints_progress_each_second_and_gives_up_at_its_bound(self) -> None:
        """Two leads' dry runs printed nothing for 600 and 900 s (2026-11-03). A line a second
        names the step, and past AGENTBOX_MOVE_SCAN_LIMIT the command gives up and says where."""
        self.moving_session()
        self.set_pids(999999901)
        self.set_panes({999999900: ("%81", "agents:4.1")})
        self.target()
        self.env["AGENTBOX_MOVE_SCAN_LIMIT"] = "3"
        slow = self.run_bash('move_own_omp() { echo 999999901; }\n'
                             'eval "orig_$(declare -f move_to_find_group)"\n'
                             'move_to_find_group() { sleep 30; orig_move_to_find_group "$@"; }\n'
                             'cmd_move --to devbox --dry-run --accept-orphans')
        self.assertNotEqual(slow.returncode, 0)
        # The step it names is whichever one the bound caught, which under load may be an
        # earlier one than the group; what matters is a line a second and a named step.
        self.assertRegex(slow.stderr, r"agentbox: \d+s: \S")
        self.assertRegex(slow.stderr, r"move --to gave up after \d+s \(AGENTBOX_MOVE_SCAN_LIMIT\) in: \S")
        self.assertNotIn("no message of its own", slow.stderr)

    def test_branches_are_pushed_once_per_repository(self) -> None:
        """S3, 2026-11-03: 50 worktrees each pushed every branch of the same repository, 40 min
        before its hang-up. Two worktrees of one repository make one push of the branches the
        target lacks."""
        self.target()
        repo = self.checkout("onerepo")
        for name in ("w1", "w2"):
            self.git("worktree", "add", "-q", "-b", name, str(self.root / name), cwd=repo)
        (self.remote / "MISSING").write_text("", encoding="utf-8")
        log = self.root / "GITLOG"
        write_stub(self.stubs, "git", f'echo "$*" >>"{log}"\n'
                   'case " $* " in *" ls-remote "*) exit 0 ;; *" push "*) exit 0 ;; esac\nexec /usr/bin/git "$@"')
        result = self.run_bash(
            f'MOVE_TO_NATIVE=("{self.root}/w1\t{repo}/.git" "{self.root}/w2\t{repo}/.git")\n'
            'move_to_git_native_prepush devbox')
        self.assertEqual(result.returncode, 0, result.stderr)
        pushes = [l for l in log.read_text(encoding="utf-8").splitlines() if " push " in f" {l} "]
        self.assertEqual(len(pushes), 1, pushes)
        self.assertIn("refs/heads/w1:refs/heads/w1", pushes[0])
        self.assertIn("refs/heads/w2:refs/heads/w2", pushes[0])
        # Only the two branches these worktrees have checked out: the repository's other branches
        # belong to other sessions and other machines, and pushing them all took 55+ min of two
        # moves of oss-verify-proofs-internal-src (17:05-18:05Z, 2026-11-03).
        self.assertNotIn("refs/heads/main", pushes[0], pushes[0])
        self.assertIn(f"pushed 2 branch(es) of {repo}/.git before the hang-up", result.stdout)
        # And a second move of the same repository to the same target within the hour makes none.
        again = self.run_bash(
            f'MOVE_TO_NATIVE=("{self.root}/w1\t{repo}/.git")\n'
            'move_to_git_native_prepush devbox')
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("less than 60 min ago (another move); not pushed again", again.stdout)
        self.assertEqual(len([l for l in log.read_text(encoding="utf-8").splitlines()
                              if " push " in f" {l} "]), 1)

    def test_a_move_holds_before_the_hang_up_until_the_session_runs_go(self) -> None:
        """S3 restarted its 14 lanes during a 66-minute first copy (2026-11-03). The relauncher now
        holds after everything before the hang-up, tells the session, and hangs up only after the
        session's own `agentbox move --go`."""
        self.moving_session()
        self.set_pids(999999901)
        self.set_panes({999999900: ("%81", "agents:4.1")})
        self.target()
        self.env["AGENTBOX_MOVE_HOLD_WAIT"] = "120"
        self.env["AGENTBOX_MOVE_POLL"] = "1"
        held = self.move_to()
        self.assertIn("run `agentbox move --go` at your safe point", held.stdout)
        [job] = self.jobs()
        self.set_pids()
        log = self.root / "relauncher.log"
        proc = subprocess.Popen(["bash", "-c", f'source "{AGENTBOX}"\nmove_to_run "{job}"'],
                                env=self.env, stdout=log.open("w"), stderr=subprocess.STDOUT, start_new_session=True)
        self.addCleanup(self.end_relauncher, proc)
        hold = self.root / "cache/omp/agentbox/move-to/hold" / self.TO
        for _ in range(1200):
            if hold.exists():
                break
            time.sleep(0.1)
        self.assertTrue(hold.exists(), log.read_text(encoding="utf-8"))
        time.sleep(2)
        self.assertNotIn("hung up after", log.read_text(encoding="utf-8"))
        self.assertIn("ready to hang you up", (self.root / "STUB_ENVOY").read_text(encoding="utf-8"))
        go = self.run_bash("cmd_move --go", {"OMP_SESSION_ID": self.TO})
        self.assertEqual(go.returncode, 0, go.stderr)
        self.assertIn("hangs this session up within seconds", go.stdout)
        self.assertEqual(proc.wait(timeout=600), 0, log.read_text(encoding="utf-8"))
        text = log.read_text(encoding="utf-8")
        self.assertLess(text.index("ran `agentbox move --go`"), text.index("hung up after"))
        self.assertIn("moved:", text)

    def test_a_hold_that_gets_no_go_refuses_with_nothing_hung_up(self) -> None:
        self.moving_session()
        self.set_pids(999999901)
        self.set_panes({999999900: ("%81", "agents:4.1")})
        self.target()
        self.assertEqual(self.move_to().returncode, 0)
        [job] = self.jobs()
        self.set_pids()
        self.env["AGENTBOX_MOVE_HOLD_WAIT"] = "2"
        self.env["AGENTBOX_MOVE_POLL"] = "1"
        run = self.run_bash(f'move_to_run "{job}"')
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertIn("refused, nothing was hung up: no `agentbox move --go` from", run.stdout)
        self.assertNotIn("hung up after", run.stdout)
        self.assertFalse((self.root / "cache/omp/agentbox/move-to/hold" / self.TO).exists())
        self.assertFalse((self.root / "cache/omp/agentbox/move-to/active" / self.TO).exists())
        # Nothing was moved, so the move log counts no failed attempt toward a halt.
        self.assertFalse(any("refused" in line[5] for line in self.move_log()), self.move_log())
        no_hold = self.run_bash("cmd_move --go", {"OMP_SESSION_ID": self.TO})
        self.assertEqual(no_hold.returncode, 1)
        self.assertIn("is waiting for --go", no_hold.stderr)

    def test_an_inferred_directory_over_the_limit_is_not_copied_unless_named(self) -> None:
        """PROCS, 2026-11-03 07:09Z: its copy held all of /mnt/nvme/sami/wt, 323 GB of every
        session's worktrees, for one file and one worktree of its own there. An inferred directory
        in no git tree over the limit leaves the copy and is listed; a named one stays. The gate
        and the lists are tested as functions, so the test takes the same seconds at any load."""
        big = self.root / "wt"
        big.mkdir()
        (big / "big.bin").write_bytes(b"x" * 4096)
        small = self.root / "lane-small"
        small.mkdir()
        (small / "f").write_text("x", encoding="utf-8")
        tree = self.checkout("kept")
        script = (f'MOVE_TO_MIRROR=("{tree}")\nMOVE_TO_MERGE=("{big}" "{small}")\nMOVE_TO_MKDIR=()\n'
                  'move_to_gate {asked}\n'
                  f'move_to_print_lists devbox "$(printf \'%s\\t%s\\n\' /proc/1/cwd "{big}" /proc/1/cwd "{tree}" | move_to_not_carried)"')
        env = {"AGENTBOX_MOVE_GATE_BYTES": "1000"}
        listed = self.run_bash(script.replace("{asked}", ""), env)
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertIn("WOULD COPY, NOT ASKED FOR", listed.stdout)
        self.assertIn(f"  {big} (", listed.stdout)
        self.assertIn(f"  mirror  {tree}\n", listed.stdout)
        self.assertIn(f"  merge   {small}\n", listed.stdout)
        self.assertNotIn(f"  merge   {big}\n", listed.stdout)
        self.assertIn(f"\n  {big}\n", listed.stdout.split("NOT CARRIED")[1])
        self.assertNotIn(str(tree), listed.stdout.split("NOT CARRIED")[1])
        named = self.run_bash(script.replace("{asked}", f'"{big}"'), env)
        self.assertIn(f"  merge   {big}\n", named.stdout)
        self.assertNotIn("WOULD COPY, NOT ASKED FOR", named.stdout)

    def test_a_relauncher_waits_while_two_other_moves_are_in_flight(self) -> None:
        """~35 sessions move themselves tonight: at most two moves run at once from a host. The
        wait is tested as a function, on claims held by processes the test ends itself once the
        wait is seen, so it takes the same steps at any load."""
        active = self.root / "cache/omp/agentbox/move-to/active"
        active.mkdir(parents=True)
        holders = [subprocess.Popen(["sleep", "600"]) for _ in range(2)]
        self.addCleanup(lambda: [h.kill() for h in holders])
        for k, h in enumerate(holders):
            (active / f"01a1aaaa-0000-7000-8000-00000000000{k}").write_text(
                f"pid={h.pid}\ntarget=devbox\nstart=0\njob=/x/g-other{k}-20261103T070000Z-1.json\n", encoding="utf-8")
        # A claim of the mover's own group never counts.
        (active / "01a1aaaa-0000-7000-8000-000000000009").write_text(
            f"pid={os.getpid()}\ntarget=devbox\nstart=0\njob=/x/g-mine-20261103T070000Z-1.json\n", encoding="utf-8")
        proc = subprocess.Popen(["bash", "-c", f'source "{AGENTBOX}"\nsay() {{ echo "$*"; }}\nmove_to_wait_flight g-mine\necho DONE'],
                                env={**self.env, "AGENTBOX_MOVE_FLIGHT_POLL": "0.2", "XDG_CACHE_HOME": str(self.root / "cache")},
                                stdout=subprocess.PIPE, text=True)
        first = proc.stdout.readline()
        self.assertIn("waiting: 2 moves in flight:", first)
        self.assertIn("g-other0", first)
        self.assertNotIn("g-mine", first)
        holders[0].kill()
        holders[0].wait()
        rest = proc.communicate(timeout=600)[0]
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(rest.strip(), "DONE")

    def test_a_gone_pane_is_not_retried_and_a_session_left_down_is_alerted(self) -> None:
        """R3A, 2026-11-03: its rollback retried a gone pane 40 times over 10 minutes, never took
        the new-window path, logged "rolled back … resume here" while the session was down, and
        alerted nobody. A pane its server does not have is not retried; a member that did not come
        back is logged DOWN and the session, the lead and the box-health role are told."""
        self.moving_session()
        self.set_pids(999999901)
        self.set_panes({999999900: ("%81", "agents:4.1")})
        self.target(resume=False)
        self.env["AGENTBOX_MOVE_LEAD"] = "01a18385-f8c8-74a9-b6ff-694fb5fe87a7"
        # The real budget, to show a gone pane is tried once and not 40 times.
        self.env["AGENTBOX_RESPAWN_TRIES"] = "40"
        self.assertEqual(self.move_to().returncode, 0)
        [job] = self.jobs()
        self.set_pids()
        # Its pane is gone from the server, and no window can be opened either.
        tries = self.root / "RESPAWN_TRIES"
        write_stub(self.stubs, "tmux", "\n".join([
            'case " $* " in',
            '  *" list-panes "*) cat "$STUB_TMUX" 2>/dev/null; exit 0 ;;',
            f'  *" respawn-pane "*) echo "$*" >>"{tries}"; echo "no such pane: %81" >&2; exit 1 ;;',
            '  *" new-window "*) echo "no window" >&2; exit 1 ;;',
            '  *" display-message "*) printf "42507\\n" ;;',
            "esac",
            f'printf "%s\\n" "$*" >>"{self.local_tmux}"',
            "exit 0",
        ]))
        run = self.run_bash(f'move_to_run "{job}"')
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertEqual(len(tries.read_text(encoding="utf-8").splitlines()), 1, tries.read_text(encoding="utf-8"))
        self.assertIn("is gone from its server; not retried", run.stdout)
        self.assertIn(f"{self.TO}: DOWN: pane %81 would not respawn", run.stdout)
        self.assertIn("NOT rolled back, DOWN:", run.stdout)
        self.assertNotIn("rolled back: ", run.stdout)
        [line] = self.move_log()
        self.assertTrue(line[5].startswith("DOWN after failed:"), line)
        sent = (self.root / "STUB_ENVOY").read_text(encoding="utf-8")
        self.assertIn("01a18385-f8c8-74a9-b6ff-694fb5fe87a7", sent)
        self.assertIn("notifications.role.lead-box-health", sent)
        self.assertIn("the session is DOWN on", sent)

    def test_a_rollback_cancels_the_target_s_pending_box_start(self) -> None:
        """PUB and PEER, 2026-11-03: a rollback killed the target's windows, and four `agentbox
        new` launchers waited behind a stuck umount until a person ended them. The rollback now
        kills each launcher's process group, removes a box it made, and refuses to resume the
        session here while one will not die."""
        self.moving_session()
        self.set_pids(999999901)
        self.set_panes({999999900: ("%81", "agents:4.1")})
        self.target(resume=False)
        self.assertEqual(self.move_to().returncode, 0)
        [job] = self.jobs()
        self.set_pids()
        # The launcher the start left behind on the target, and the box it made.
        starting = self.cutover / "starting"
        starting.mkdir(parents=True, exist_ok=True)
        launcher = subprocess.Popen(["sleep", "600"], start_new_session=True)
        self.addCleanup(launcher.kill)
        (starting / self.TO).write_text(f"pid={launcher.pid}\nbox=agentbox-stuck\nwindow=%7\n", encoding="utf-8")
        docker = self.root / "DOCKER"
        write_stub(self.stubs, "docker", f'echo "$*" >>"{docker}"; case "$1" in ps) echo agentbox-stuck ;; inspect) echo "[\"omp\",\"--resume\",\"{self.TO}\"]" ;; esac; exit 0')
        run = self.run_bash(f'move_to_run "{job}"')
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertEqual(launcher.poll(), -15, "the launcher was not ended")
        self.assertIn("rm -f agentbox-stuck", docker.read_text(encoding="utf-8"))
        self.assertFalse((starting / self.TO).exists())
        self.assertIn("rolled back:", run.stdout)

    def test_a_pending_box_start_that_will_not_die_stops_the_rollback(self) -> None:
        self.moving_session()
        self.set_pids(999999901)
        self.set_panes({999999900: ("%81", "agents:4.1")})
        self.target(resume=False)
        self.assertEqual(self.move_to().returncode, 0)
        [job] = self.jobs()
        self.set_pids()
        starting = self.cutover / "starting"
        starting.mkdir(parents=True, exist_ok=True)
        # The target stops answering while the rollback tries to cancel the start.
        (starting / self.TO).write_text("pid=999999999\nbox=\nwindow=%7\n", encoding="utf-8")
        (self.remote / "CANCEL_FAIL").write_text("", encoding="utf-8")
        run = self.run_bash(f'move_to_run "{job}"')
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn("could not be cancelled", run.stdout)
        self.assertIn("is NOT resumed here", run.stdout)
        self.assertIn("DOWN", run.stdout)
        self.assertEqual(self.move_log()[-1][5], "both-machines")

    def test_a_respawn_that_leaves_two_omp_of_one_session_is_refused(self) -> None:
        """`tmux respawn-pane -k` did not always end the old omp at once (the INFRA-681 double
        loop). A respawn waits for the old omp by pid and, when two remain, says so and does not
        report the session resumed."""
        self.moving_session()
        self.set_pids(999999901)
        self.set_panes({999999900: ("%81", "agents:4.1")})
        self.target(resume=False)
        self.assertEqual(self.move_to().returncode, 0)
        [job] = self.jobs()
        # The old omp outlives the respawn: two of them hold the session while the rollback runs.
        self.make_proc(999999902, ppid=1, cwd=self.worktree, session=self.TO)
        self.set_pids(999999901, 999999902)
        run = self.run_bash(f'AGENTBOX_ONE_OMP_WAIT=1\nmove_to_run "{job}"')
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn("TWO omp processes hold this session after the respawn", run.stdout + run.stderr)
        self.assertIn(f"{self.TO}: DOWN: two omp processes hold this session here", run.stdout)
        self.assertNotIn("rolled back:", run.stdout)

    def test_a_resume_that_fails_on_the_target_puts_the_session_back_here(self) -> None:
        transcript = self.moving_session()
        self.set_pids(999999901)
        self.set_panes({999999900: ("%81", "agents:4.1")})
        self.target(resume=False)
        self.assertEqual(self.move_to().returncode, 0)
        [job] = self.jobs()
        self.set_pids()
        run = self.run_bash(f'move_to_run "{job}"')
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn("rolled back", run.stdout)
        self.assertIn("kill-window -t @7", (self.remote / "TMUX").read_text(encoding="utf-8"))
        self.assertFalse((transcript.with_suffix("") / "agentbox-moved").exists())
        self.assertFalse((self.home / ".omp/agentbox-moved" / self.TO).exists())
        self.assertTrue((transcript.with_suffix("") / "Sub.jsonl").exists())
        respawn = [c for c in self.local_tmux.read_text(encoding="utf-8").splitlines() if "respawn-pane" in c]
        self.assertEqual(len(respawn), 1, respawn)
        self.assertIn(f"agentbox new --drop-gone-dirs -e OMP_PROFILE=pbc --dir {self.worktree} --dir {self.lane} -- omp --resume {transcript}", respawn[0])
        [line] = self.move_log()
        self.assertTrue(line[5].startswith(f"failed: not resumed on devbox within 0s: {self.TO}"), line)

    def test_a_working_directory_missing_on_the_target_puts_the_session_back_here(self) -> None:
        """The box on the target cannot start without its first --dir, which --drop-gone-dirs never
        drops, so the move checks it after the copy and rolls back before any window starts."""
        transcript = self.moving_session()
        self.set_pids(999999901)
        self.set_panes({999999900: ("%81", "agents:4.1")})
        self.target()
        self.assertEqual(self.move_to().returncode, 0)
        [job] = self.jobs()
        (self.remote / "MISSING").write_text(f"{self.worktree}\n", encoding="utf-8")
        self.set_pids()
        run = self.run_bash(f'move_to_run "{job}"')
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertNotIn("new-session", (self.remote / "TMUX").read_text(encoding="utf-8"))
        self.assertFalse((transcript.with_suffix("") / "agentbox-moved").exists())
        [line] = self.move_log()
        self.assertEqual(line[5], f"failed: the working directory {self.worktree} of {self.TO} is not on devbox")
        respawn = [c for c in self.local_tmux.read_text(encoding="utf-8").splitlines() if "respawn-pane" in c]
        self.assertIn(f"-- omp --resume {transcript}", respawn[0])

    def test_a_working_directory_the_move_does_not_copy_and_the_target_lacks_is_refused_first(self) -> None:
        """A session working in a root of many sessions' trees (here the fixture's ~/tmp) gets no
        copy of it, so the target must have it already; when it does not, the session is refused
        while it still runs, from its own command and again by the relauncher."""
        root = self.home / "tmp"
        root.mkdir()
        self.make_proc(999999940, comm="bash", ppid=1, cwd=root)
        self.make_proc(999999941, ppid=999999940, cwd=root, session=self.TO)
        self.set_pids(999999941)
        self.set_panes({999999940: ("%85", "agents:8.1")})
        self.target()
        (self.remote / "MISSING").write_text(f"{root}\n", encoding="utf-8")
        refused = self.move_to(own=999999941)
        self.assertEqual(refused.returncode, 1, refused.stdout)
        self.assertIn(f"nothing was hung up: these working directories are not on devbox", refused.stderr)
        self.assertIn(f" {root} ", refused.stderr)
        self.assertEqual(self.jobs(), [])
        (self.remote / "MISSING").unlink()
        self.assertEqual(self.move_to(own=999999941).returncode, 0)
        [job] = self.jobs()
        (self.remote / "MISSING").write_text(f"{root}\n", encoding="utf-8")
        run = self.run_bash(f'move_to_run "{job}"')
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertIn("refused, nothing was hung up: these working directories are not on", run.stdout)
        self.assertFalse((self.root / "RSYNC").exists())

    def test_a_path_no_offbox_refuses_is_never_copied_and_nothing_is_hung_up(self) -> None:
        """LANE-C's embargoed evidence may not leave this machine (2026-11-02): a `.no-offbox`
        marker in a directory above a path the move would copy refuses the move, from the
        session's own command and again by the relauncher, before any hang-up, and the refusal
        names the marker."""
        self.moving_session()
        self.set_pids(999999901)
        self.set_panes({999999900: ("%81", "agents:4.1")})
        self.target()
        marker = self.lane.parent / ".no-offbox"
        marker.write_text("embargoed\n", encoding="utf-8")
        refused = self.move_to()
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertIn("nothing was hung up: no-offbox refuses its copy", refused.stderr)
        self.assertIn(f"marker {marker}", refused.stderr)
        self.assertEqual(self.jobs(), [])
        marker.unlink()
        self.assertEqual(self.move_to().returncode, 0)
        [job] = self.jobs()
        # Marked after the job was written: the relauncher asks again.
        inner = self.lane / ".no-offbox"
        inner.write_text("embargoed\n", encoding="utf-8")
        self.set_pids()
        run = self.run_bash(f'move_to_run "{job}"')
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertIn("refused, nothing was hung up: no-offbox refuses its copy", run.stdout)
        self.assertIn(f"marker {inner}", run.stdout)
        self.assertFalse((self.root / "RSYNC").exists())
        self.assertNotIn("respawn-pane", self.local_tmux.read_text(encoding="utf-8") if self.local_tmux.exists() else "")

    def test_a_dry_run_lists_the_copy_while_the_target_is_not_ready(self) -> None:
        """S3 and LINUX-1248, 2026-11-03: with sami-devbox's target-ready gone, a dry run stopped
        at that refusal and showed nothing of what a move would copy."""
        self.moving_session()
        self.set_pids(999999901)
        self.set_panes({999999900: ("%81", "agents:4.1")})
        self.target()
        (self.cutover / "target-ready").unlink()
        dry = self.move_to("--to devbox --dry-run")
        self.assertEqual(dry.returncode, 1, dry.stdout)
        self.assertIn(f"  mirror  {self.worktree}\n", dry.stdout)
        self.assertIn("the move would REFUSE now, before any hang-up: devbox has no", dry.stderr)
        self.assertNotIn("stopped with exit", dry.stderr)
        self.assertEqual(self.jobs(), [])

    def test_a_copy_destination_the_target_will_not_let_it_write_refuses_before_the_hang_up(self) -> None:
        """LINUX-1248's first move (2026-11-03 02:48Z) hung up, then rsync failed on a directory
        on the target it could not write (an empty one owned by root, left by docker), and the
        group rolled back. The move now asks first. The stub's target is this machine, so an
        unwritable directory here stands in for one there."""
        self.moving_session()
        self.set_pids(999999901)
        self.set_panes({999999900: ("%81", "agents:4.1")})
        self.target()
        self.lane.chmod(0o555)
        try:
            refused = self.move_to()
            dry = self.move_to("--to devbox --dry-run")
        finally:
            self.lane.chmod(0o755)
        self.assertEqual(refused.returncode, 1, refused.stdout)
        self.assertIn(f"nothing was hung up: the copy cannot write on devbox: {self.lane} ({self.lane} is not writable by", refused.stderr)
        self.assertEqual(self.jobs(), [])
        self.assertEqual(dry.returncode, 1)
        self.assertIn("the move would REFUSE now, before any hang-up: the copy cannot write on devbox", dry.stderr)

    def test_a_move_waits_for_a_health_check_in_flight_and_says_so_when_it_gives_up(self) -> None:
        """devbox/sami-devbox-check deletes target-ready when it starts and writes it again only
        after a run with no failure, and a run takes about 6 min, so a missing marker usually means
        a check is in flight (DevboxProvision, 2026-11-03)."""
        self.moving_session()
        self.set_pids(999999901)
        self.set_panes({999999900: ("%81", "agents:4.1")})
        self.target()
        ready = self.cutover / "target-ready"
        ready.unlink()
        # It comes back while the move waits. The writer waits for the move's first check of the
        # marker to reach the target (the ssh stub logs every remote command), not for a fixed
        # time: under load the session scan alone can outlast any fixed sleep.
        ssh_log = self.remote / "SSH"
        writer = subprocess.Popen(["bash", "-c", (
            f'for i in $(seq 1 1200); do grep -q "test.*target-ready" "{ssh_log}" 2>/dev/null && break; sleep 0.1; done; '
            f'sleep 0.5; date -u +%FT%TZ >"{ready}"')])
        self.addCleanup(writer.wait)
        self.env["AGENTBOX_MOVE_READY_WAIT"] = "30"
        self.env["AGENTBOX_MOVE_POLL"] = "1"
        waited = self.move_to()
        self.assertEqual(waited.returncode, 0, waited.stdout + waited.stderr)
        self.assertIn("its health check deletes it while it runs (about 6 min), so this waits up to", waited.stderr)
        self.assertIn(f"{ready} is back on devbox after", waited.stderr)
        self.assertEqual(len(self.jobs()), 1)
        # Still missing when the wait runs out: the refusal says what to do.
        ready.unlink()
        self.env["AGENTBOX_MOVE_READY_WAIT"] = "2"
        gone = self.move_to()
        self.assertEqual(gone.returncode, 1)
        self.assertIn("has written no log there", gone.stderr)
        # With the check's log: when it started, and whether its last run failed.
        (self.cutover / "a2-20261103T082250Z.log").write_text("ok   one\nok   two\n", encoding="utf-8")
        running = self.move_to()
        self.assertIn("started at 2026-11-03 08:22:50Z deletes it while it runs and writes it again when it passes, usually 8-13 min", running.stderr)
        (self.cutover / "a2-20261103T091000Z.log").write_text("ok   one\nFAIL two\n", encoding="utf-8")
        failed = self.move_to()
        self.assertIn("started 2026-11-03 09:10:00Z) FAILED", failed.stderr)
        # A dry run never waits.
        self.env["AGENTBOX_MOVE_READY_WAIT"] = "600"
        start = time.time()
        dry = self.move_to("--to devbox --dry-run")
        self.assertLess(time.time() - start, 120)
        self.assertEqual(dry.returncode, 1)
        self.assertIn("the move would REFUSE now, before any hang-up: devbox has no", dry.stderr)

    def test_nothing_is_hung_up_when_the_target_is_not_ready_or_does_not_answer(self) -> None:
        self.moving_session()
        self.set_pids(999999901)
        self.set_panes({999999900: ("%81", "agents:4.1")})
        self.target()
        (self.cutover / "target-ready").unlink()
        refused = self.move_to()
        self.assertEqual(refused.returncode, 1)
        self.assertIn(f"devbox has no {self.cutover}/target-ready", refused.stderr)
        self.assertIn("nothing was hung up", refused.stderr)
        (self.remote / "DOWN").write_text("", encoding="utf-8")
        refused = self.move_to()
        self.assertIn("devbox does not answer `ssh -o BatchMode=yes -o ConnectTimeout=20 sami@devbox`", refused.stderr)
        self.assertEqual(self.jobs(), [])
        self.assertFalse((self.root / "SETSID").exists())

    def test_a_session_already_running_on_the_target_halts_every_move(self) -> None:
        self.moving_session()
        self.set_pids(999999901)
        self.set_panes({999999900: ("%81", "agents:4.1")})
        self.target()
        proc = self.remote / "proc/4000000099"
        (proc / "fd").mkdir(parents=True)
        (proc / "cmdline").write_text(f"omp\0--resume\0{self.TO}\0", encoding="utf-8")
        (self.remote / "PGREP").write_text("4000000099\n", encoding="utf-8")
        refused = self.move_to()
        self.assertEqual(refused.returncode, 1)
        self.assertIn("one session is running on both machines", refused.stderr)
        [line] = self.move_log()
        self.assertEqual(line[1], self.TO)
        self.assertEqual(line[5], "both-machines")
        (self.remote / "PGREP").write_text("", encoding="utf-8")
        again = self.move_to("--to devbox --dry-run")
        self.assertIn(f"moves to devbox are halted: session {self.TO}", again.stderr)
        self.assertEqual(self.move_to("--to devbox --clear-halt").returncode, 0)
        self.assertEqual(self.move_to("--to devbox --dry-run").returncode, 0)

    def test_two_failed_moves_in_a_row_halt_the_next_until_the_lead_clears_it(self) -> None:
        self.moving_session()
        self.set_pids(999999901)
        self.set_panes({999999900: ("%81", "agents:4.1")})
        self.target()
        (self.cutover / "move-log.tsv").write_text(
            "2026-11-02T10:00:00Z\ts1\tg-1\toldbox\tdevbox\tmoved\t/t1\n"
            "2026-11-02T10:10:00Z\ts2\tg-2\toldbox\tdevbox\tfailed: the copy to devbox\t/t2\n"
            "2026-11-02T10:20:00Z\ts3\tg-3\toldbox\tdevbox\tmoved\t/t3\n"
            "2026-11-02T10:20:00Z\ts4\tg-3\toldbox\tdevbox\tfailed: not resumed on devbox\t/t4\n",
            encoding="utf-8",
        )
        halted = self.move_to("--to devbox --dry-run")
        self.assertEqual(halted.returncode, 1)
        self.assertIn("the last two moves failed (group g-2 at 2026-11-02T10:10:00Z, group g-3 at 2026-11-02T10:20:00Z)", halted.stderr)
        self.assertEqual(self.move_to("--to devbox --clear-halt").returncode, 0)
        self.assertEqual(self.move_log()[-1][5], "halt-cleared")
        allowed = self.move_to("--to devbox --dry-run")
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertIn("--dry-run: nothing was hung up, copied or sent", allowed.stdout)

    def test_a_group_moves_once_every_session_sharing_its_tree_is_at_its_safe_point(self) -> None:
        transcript = self.moving_session()
        # A second session works in the same worktree: one group.
        self.make_proc(999999910, comm="bash", ppid=1, cwd=self.worktree)
        self.make_proc(999999911, ppid=999999910, cwd=self.worktree, session=self.TO2)
        self.set_pids(999999901, 999999911)
        self.set_panes({999999900: ("%81", "agents:4.1"), 999999910: ("%82", "agents:5.1")})
        self.target()
        first = self.move_to()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn(f"the group moves when {self.TO2} run(s) `agentbox move --to devbox` too", first.stdout)
        self.assertEqual(self.jobs(), [])
        sent = (self.root / "STUB_ENVOY").read_text(encoding="utf-8").splitlines()
        self.assertEqual(sent[:2], ["send", self.TO2])
        self.assertIn("run `agentbox move --to devbox` from your bash tool", sent[2])
        # The second session says it is ready (its own `move --to`); the first one's run moves both.
        self.run_bash(f"move_to_ready_mark {self.TO2} devbox")
        both = self.move_to()
        self.assertEqual(both.returncode, 0, both.stderr)
        [job] = self.jobs()
        doc = json.loads(job.read_text(encoding="utf-8"))
        self.assertEqual(sorted(m["sid"] for m in doc["members"]), [self.TO, self.TO2])
        self.assertEqual(doc["group"], "g-01a19700")
        self.assertIn(str(transcript), doc["mirror"])
        self.assertIn(str(self.session_file(self.TO2)), doc["mirror"])

    def test_a_session_working_in_src_itself_shares_no_tree_with_the_others(self) -> None:
        """A lead whose working directory is $SRC (or another root of many sessions' trees) would
        otherwise contain every tree there, and its copy would be all of ~/src."""
        self.moving_session()
        self.make_proc(999999920, comm="bash", ppid=1, cwd=self.src)
        self.make_proc(999999921, ppid=999999920, cwd=self.src, session=self.TO2)
        self.set_pids(999999901, 999999921)
        self.set_panes({999999900: ("%81", "agents:4.1"), 999999920: ("%83", "agents:6.1")})
        self.target()
        moved = self.move_to()
        self.assertEqual(moved.returncode, 0, moved.stderr)
        [job] = self.jobs()
        self.assertEqual([m["sid"] for m in json.loads(job.read_text(encoding="utf-8"))["members"]], [self.TO])
        self.assertNotIn(str(self.src), json.loads(job.read_text(encoding="utf-8"))["mirror"])

    def test_sessions_that_share_only_a_directory_in_no_git_tree_move_apart(self) -> None:
        """Caches, a tool's run directory and a lead root are written by many sessions; on
        2026-11-02 they put 45 of this host's 46 sessions in one group. Such a directory is merged
        into the target, never mirrored, and puts no two sessions in one group."""
        self.moving_session()
        self.make_proc(999999930, comm="bash", ppid=1, cwd=self.lane)
        self.make_proc(999999931, ppid=999999930, cwd=self.lane, session=self.TO2)
        self.set_pids(999999901, 999999931)
        self.set_panes({999999900: ("%81", "agents:4.1"), 999999930: ("%84", "agents:7.1")})
        self.target()
        moved = self.move_to()
        self.assertEqual(moved.returncode, 0, moved.stderr)
        [job] = self.jobs()
        doc = json.loads(job.read_text(encoding="utf-8"))
        self.assertEqual([m["sid"] for m in doc["members"]], [self.TO])
        self.assertIn(str(self.lane), doc["merge"])
        self.assertNotIn(str(self.lane), doc["mirror"])

    def test_a_path_the_session_names_inside_a_pointer_file_s_tree_is_copied(self) -> None:
        """S3, 2026-11-03: the leak-check pointer names a file under a lane tree that is bounded at
        a shared root, and S3's own `--dir <that tree>/s3` collapsed into the pointer's entry,
        which the move then dropped: no copy, no message. The pointer is not read for a move."""
        self.moving_session()
        lanes = self.home / "tmp" / "frac-verify"
        (lanes / "shared/leak-check").mkdir(parents=True)
        (lanes / "s3").mkdir()
        pointer = self.home / ".config/public-leak-check/path"
        pointer.parent.mkdir(parents=True)
        pointer.write_text(f"{lanes}/shared/leak-check/public-leak-check.sh\n", encoding="utf-8")
        self.set_pids(999999901)
        self.set_panes({999999900: ("%81", "agents:4.1")})
        self.target()
        moved = self.move_to(f'--to devbox --dir "{lanes / "s3"}"')
        self.assertEqual(moved.returncode, 0, moved.stderr)
        [job] = self.jobs()
        doc = json.loads(job.read_text(encoding="utf-8"))
        self.assertIn(str(lanes / "s3"), doc["merge"])
        self.assertIn(str(lanes / "s3"), doc["members"][0]["spec"])

    def test_what_a_pointer_file_names_stays_the_machine_s_own(self) -> None:
        """The leak check a pointer file names is configuration of each machine, as the pointer is:
        on sami-rocqperf it is inside a whole lane tree. A move neither copies it nor names it as a
        --dir, since the target's `agentbox new` binds its own."""
        self.moving_session()
        leak = self.root / "lanes/frac-verify/shared/leak-check"
        leak.mkdir(parents=True)
        pointer = self.home / ".config/public-leak-check/path"
        pointer.parent.mkdir(parents=True)
        pointer.write_text(f"{leak}/public-leak-check.sh\n", encoding="utf-8")
        self.set_pids(999999901)
        self.set_panes({999999900: ("%81", "agents:4.1")})
        self.target()
        moved = self.move_to()
        self.assertEqual(moved.returncode, 0, moved.stderr)
        [job] = self.jobs()
        doc = json.loads(job.read_text(encoding="utf-8"))
        for listed in (doc["mirror"], doc["merge"], doc["members"][0]["spec"]):
            self.assertFalse([p for p in listed if p.startswith(str(self.root / "lanes"))], listed)

    def test_a_move_reads_the_paths_of_its_own_group_only(self) -> None:
        """The group is found from the sessions' processes: a session whose processes are in none
        of the group's trees is never inferred, since inferring every session on the host took
        221 s before the hang-up on 2026-11-02."""
        self.moving_session()
        self.make_proc(999999910, comm="bash", ppid=1, cwd=self.worktree)
        self.make_proc(999999911, ppid=999999910, cwd=self.worktree, session=self.TO2)
        apart = self.root / "apart"
        apart.mkdir()
        third = "01a19700-0000-7000-8000-0000000e0003"
        self.make_proc(999999950, comm="bash", ppid=1, cwd=apart)
        self.make_proc(999999951, ppid=999999950, cwd=apart, session=third)
        self.set_pids(999999901, 999999911, 999999951)
        self.set_panes({999999900: ("%81", "agents:4.1"), 999999910: ("%82", "agents:5.1"),
                        999999950: ("%86", "agents:9.1")})
        self.target()
        log = self.root / "PATHS"
        result = self.run_bash(
            'eval "orig_$(declare -f move_to_session_paths)"\n'
            f'move_to_session_paths() {{ echo "$2" >>"{log}"; orig_move_to_session_paths "$@"; }}\n'
            "move_own_omp() { echo 999999901; }\ncmd_move_to --to devbox --dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"group g-01a19700, 2 session(s): {self.TO} {self.TO2}", result.stdout)
        self.assertEqual(sorted(log.read_text(encoding="utf-8").split()), [self.TO, self.TO2])

    def orphan_session(self) -> None:
        """A session at its safe point that still has what its tool calls started: a background
        `sleep` (KNOTCURVE's dashboard was such a child, and died with its move on 2026-11-02),
        omp's own worker broker and python runner, and a server the runner started."""
        lane = self.root / "lane"
        lane.mkdir(exist_ok=True)
        self.make_proc(1100, comm="bash", ppid=1, cwd=lane)
        self.make_proc(1101, ppid=1100, cwd=lane, session="01a19700-0000-7000-8000-0000000f0001")
        self.make_proc(1102, comm="sleep", ppid=1101, cwd=lane, cmdline="sleep 1000")
        self.make_proc(1103, ppid=1101, cwd=lane, cmdline="/x/bin/omp __omp_worker_daemon_broker")
        self.make_proc(1104, comm="python3", ppid=1101, cwd=lane,
                       cmdline="/usr/bin/python3 -u /t/omp-python-runner-1000/runner-ab12.py")
        # A subagent's own tool call: its environment says which session started it, so the lead
        # that must stop it is named, not left to be found by hand (sami-rocqperf, 2026-11-03).
        self.make_proc(1105, comm="python3", ppid=1104, cwd=lane, cmdline="python3 serve.py",
                       environ={"OMP_SESSION_ID": "01a19700-0000-7000-8000-0000000f0009"})
        # A watcher the session started with `setsid`, and what that watcher started: the pane's
        # hang-up reaches neither, so neither is an orphan. Agents make no systemd units
        # (Sami, dispatch://INFRA-26/comment/615d0105-4ad1-4a2e-ae1c-01a1d8894b5c).
        self.make_proc(1106, comm="python3", ppid=1101, cwd=lane, cmdline="python3 watch.py", session_leader=True)
        self.make_proc(1107, comm="python3", ppid=1106, cwd=lane, cmdline="python3 watch-child.py")
        self.set_pids(1101)
        self.set_panes({1100: ("%90", "agents:9.1")})

    def test_a_move_lists_and_refuses_what_dies_with_the_hang_up(self) -> None:
        self.orphan_session()
        env = 'move_own_omp() { echo 1101; }\nmove_image_bytes() { echo 280000000; }\nmove_hand_off() { echo HANDED-OFF; }\n'
        refused = self.run_bash(env + "cmd_move_self")
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertNotIn("HANDED-OFF", refused.stdout)
        self.assertIn("nothing was hung up: these processes of this session die with the hang-up", refused.stderr)
        self.assertIn("sleep 1000 (pid 1102)", refused.stderr)
        self.assertIn("python3 serve.py (pid 1105, of session 01a19700-0000-7000-8000-0000000f0009)",
                      refused.stderr)
        self.assertIn("sleep 1000 (pid 1102)", refused.stderr)  # no environment of its own, no session named
        self.assertNotIn("__omp_worker", refused.stderr)
        self.assertNotIn("runner-ab12", refused.stderr)
        self.assertNotIn("watch.py", refused.stderr)
        self.assertNotIn("watch-child.py", refused.stderr)
        self.assertIn("Start each one detached (`setsid <command> </dev/null >>log 2>&1 &`", refused.stderr)
        self.assertNotIn("systemd-run", refused.stderr)
        dry = self.run_bash(env + "cmd_move_self --dry-run")
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertIn("the move would refuse: these processes", dry.stdout)
        accepted = self.run_bash(env + "cmd_move_self --accept-orphans")
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertIn("HANDED-OFF", accepted.stdout)
        self.assertIn("--accept-orphans: the hang-up of the move ends sleep 1000 (pid 1102)", accepted.stderr)
        # From outside too, and the plan lists them for the session's row.
        now = self.run_bash(env + "cmd_move_notify 01a19700-0000-7000-8000-0000000f0001 --now")
        self.assertNotIn("HANDED-OFF", now.stdout)
        self.assertIn("cannot be moved now, nothing hung up: these processes", now.stderr)
        plan = self.run_bash('move_image_bytes() { echo 280000000; }\nmove_free_bytes() { echo 900000000000; }\ncmd_move_plan --json')
        [row] = json.loads(plan.stdout)["sessions"]
        self.assertIn("DIES WITH THE MOVE", row["note"])
        self.assertIn("sleep 1000 (pid 1102)", row["note"])

    def test_a_move_to_another_machine_refuses_what_dies_with_the_hang_up(self) -> None:
        self.moving_session()
        self.set_pids(999999901)
        self.set_panes({999999900: ("%81", "agents:4.1")})
        self.target()
        refused = self.move_to(accept=False)
        self.assertEqual(refused.returncode, 1, refused.stdout)
        self.assertIn("nothing was hung up: these processes of this session die with the hang-up", refused.stderr)
        self.assertEqual(self.jobs(), [])

    def test_a_member_s_readiness_lapses_after_ten_minutes(self) -> None:
        """A member that ran `move --to` and went back to work is not hung up by a later run: its
        ready file counts for 10 minutes, and after that it is asked again."""
        self.moving_session()
        self.make_proc(999999910, comm="bash", ppid=1, cwd=self.worktree)
        self.make_proc(999999911, ppid=999999910, cwd=self.worktree, session=self.TO2)
        self.set_pids(999999901, 999999911)
        self.set_panes({999999900: ("%81", "agents:4.1"), 999999910: ("%82", "agents:5.1")})
        self.target()
        ready = self.root / "cache/omp/agentbox/move-to/ready"
        ready.mkdir(parents=True)
        (ready / self.TO2).write_text(f"devbox\t{int(time.time()) - 11 * 60}\n", encoding="utf-8")
        stale = self.move_to()
        self.assertEqual(stale.returncode, 0, stale.stderr)
        self.assertIn(f"the group moves when {self.TO2} run(s)", stale.stdout)
        self.assertEqual(self.jobs(), [])
        (ready / self.TO2).write_text(f"devbox\t{int(time.time()) - 9 * 60}\n", encoding="utf-8")
        fresh = self.move_to()
        self.assertEqual(fresh.returncode, 0, fresh.stderr)
        self.assertEqual(len(self.jobs()), 1)

    def test_a_session_resumes_in_its_own_worktree_and_leaves_a_shared_checkout(self) -> None:
        """LINUX-1248's shape (2026-11-03): its omp's working directory is a checkout three other
        sessions work in, and its work is in a worktree of its own. With --cwd and --leave it
        moves alone, resumes in the worktree, and the shared checkout is not mirrored."""
        shared = self.src / "shared" if (self.src / "shared").exists() else self.checkout("shared")
        own = self.root / "wt" / "mine"
        self.git("worktree", "add", "-q", "-b", "mine", str(own), cwd=shared)
        (own / "file").write_text("my uncommitted work\n", encoding="utf-8")
        transcript = self.home / ".omp/agent/sessions/-src-shared" / f"2026-11-03T00-00-00-000Z_{self.TO}.jsonl"
        transcript.parent.mkdir(parents=True)
        transcript.write_text("{}\n", encoding="utf-8")
        self.make_proc(999999960, comm="bash", ppid=1, cwd=shared)
        self.make_proc(999999961, ppid=999999960, cwd=shared, writes=(transcript,))
        self.make_proc(999999962, comm="bash", ppid=999999961, cwd=shared)  # its tool shell
        self.make_proc(999999970, comm="bash", ppid=1, cwd=shared)
        self.make_proc(999999971, ppid=999999970, cwd=shared, session=self.TO2)
        self.set_pids(999999961, 999999971)
        self.set_panes({999999960: ("%91", "agents:9.1"), 999999970: ("%92", "agents:9.2")})
        self.target()
        grouped = self.move_to("--to devbox --dry-run", own=999999961)
        self.assertIn("2 session(s)", grouped.stdout, grouped.stderr)
        alone = self.move_to(f'--to devbox --cwd "{own}" --leave "{shared}"', own=999999961)
        self.assertEqual(alone.returncode, 0, alone.stdout + alone.stderr)
        [job] = self.jobs()
        doc = json.loads(job.read_text(encoding="utf-8"))
        [member] = doc["members"]
        self.assertEqual((member["sid"], member["cwd"]), (self.TO, str(own)))
        self.assertEqual(member["spec"][:2], ["--dir", str(own)])
        self.assertIn(str(own), doc["mirror"])
        self.assertNotIn(str(shared), doc["mirror"])
        # The worktree's git storage still travels (merged), or the tree would have no repository.
        self.assertTrue(any(p.startswith(str(shared / ".git")) for p in doc["merge"]), doc["merge"])
        # A process of its own still writing in the left tree refuses the move.
        (self.root / "cache/omp/agentbox/move-to").mkdir(parents=True, exist_ok=True)
        for j in self.jobs():
            j.unlink()
        self.make_proc(999999963, comm="python3", ppid=999999961, cwd=own, writes=(shared / "out.log",))
        busy = self.move_to(f'--to devbox --cwd "{own}" --leave "{shared}"', own=999999961)
        self.assertEqual(busy.returncode, 1)
        self.assertIn("still works in a tree it would --leave: pid 999999963", busy.stderr)

    def test_leave_with_a_process_writing_elsewhere_moves_and_every_exit_says_why(self) -> None:
        """LINUX-1248's dry run (2026-11-03) ended after its first progress line with no message:
        the --leave check's loop ended on a file outside the left tree, and under pipefail and
        set -e that ended the command. Its process writing elsewhere is no reason to refuse, and
        an exit no refusal explains now says so."""
        shared = self.checkout("shared2")
        own = self.root / "wt" / "mine2"
        self.git("worktree", "add", "-q", "-b", "mine2", str(own), cwd=shared)
        transcript = self.home / ".omp/agent/sessions/-src-shared2" / f"2026-11-03T00-00-00-000Z_{self.TO}.jsonl"
        transcript.parent.mkdir(parents=True)
        transcript.write_text("{}\n", encoding="utf-8")
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        self.make_proc(999999980, comm="bash", ppid=1, cwd=shared)
        self.make_proc(999999981, ppid=999999980, cwd=shared, writes=(transcript,))
        self.make_proc(999999982, comm="python3", ppid=999999981, cwd=own, writes=(elsewhere / "log",))
        self.set_pids(999999981)
        self.set_panes({999999980: ("%93", "agents:9.3")})
        self.target()
        dry = self.move_to(f'--to devbox --cwd "{own}" --leave "{shared}" --dry-run', own=999999981)
        self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)
        self.assertIn("group g-01a19700, 1 session(s)", dry.stdout)
        broken = self.run_bash("move_own_omp() { echo 999999981; }\nmove_to_scan_processes() { false; }\n"
                               "cmd_move --to devbox --dry-run")
        self.assertEqual(broken.returncode, 1)
        self.assertIn("move --to stopped with exit 1 and no message of its own", broken.stderr)

    def test_leave_drops_the_left_tree_s_object_store_wherever_it_lives(self) -> None:
        """INFRA-1018's move (2026-11-03 05:32Z) --leave'd ~/src/machcsl-riscv, whose objects are
        a symlink to /mnt/nvme/<user>/git-objects/machcsl-riscv, and carried that 8.96 GB store.
        A left tree is left out of the group's trees, so its storage is never taken either; this
        holds that. A store a tree the move KEEPS borrows - a worktree of the left checkout -
        still goes, since that tree needs it."""
        shared = self.checkout("shared9")
        store = self.root / "git-objects" / "shared9"
        store.parent.mkdir(exist_ok=True)
        shutil.move(str(shared / ".git/objects"), str(store))
        os.symlink(store, shared / ".git/objects")
        mine = self.checkout("mine9")
        transcript = self.home / ".omp/agent/sessions/-src-shared9" / f"2026-11-03T00-00-00-000Z_{self.TO}.jsonl"
        transcript.parent.mkdir(parents=True)
        transcript.write_text("{}\n", encoding="utf-8")
        self.make_proc(999999960, comm="bash", ppid=1, cwd=shared)
        self.make_proc(999999961, ppid=999999960, cwd=shared, writes=(transcript,))
        self.set_pids(999999961)
        self.set_panes({999999960: ("%97", "agents:9.7")})
        self.target()
        run = self.run_bash(f'cd "{mine}"\nmove_own_omp() {{ echo 999999961; }}\n'
                            f'cmd_move_to --to devbox --cwd "{mine}" --leave "{shared}" --dry-run --accept-orphans')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertFalse([line for line in run.stdout.splitlines() if str(store) in line],
                         f"the left tree's object store is not copied either: {run.stdout}")
        self.assertIn(f"{shared} is not copied: it is in a tree this session --leave's", run.stderr)
        # A worktree of the left checkout that the session keeps needs that store, so it goes.
        own = self.root / "wt" / "mine9b"
        self.git("worktree", "add", "-q", "-b", "mine9b", str(own), cwd=shared)
        self.make_proc(999999962, comm="python3", ppid=999999961, cwd=own, writes=(own / "log",))
        kept = self.run_bash(f'cd "{own}"\nmove_own_omp() {{ echo 999999961; }}\n'
                             f'cmd_move_to --to devbox --cwd "{own}" --leave "{shared}" --dry-run --accept-orphans')
        self.assertEqual(kept.returncode, 0, kept.stdout + kept.stderr)
        self.assertNotIn(f"{store} is not copied", kept.stderr)

    def test_a_pipe_of_the_session_is_not_work_in_a_left_tree(self) -> None:
        """LINUX-1248, 2026-11-03: run from inside the checkout it leaves, its dry run was refused
        for its own pipe: `pipe:[…]` resolved against the working directory landed in the tree."""
        shared = self.checkout("shared3")
        own = self.root / "wt" / "mine3"
        self.git("worktree", "add", "-q", "-b", "mine3", str(own), cwd=shared)
        transcript = self.home / ".omp/agent/sessions/-src-shared3" / f"2026-11-03T00-00-00-000Z_{self.TO}.jsonl"
        transcript.parent.mkdir(parents=True)
        transcript.write_text("{}\n", encoding="utf-8")
        self.make_proc(999999984, comm="bash", ppid=1, cwd=shared)
        self.make_proc(999999985, ppid=999999984, cwd=shared, writes=(transcript,))
        self.make_proc(999999986, comm="bash", ppid=999999985, cwd=shared, writes=(Path("pipe:[1910247]"),))
        self.set_pids(999999985)
        self.set_panes({999999984: ("%94", "agents:9.4")})
        self.target()
        dry = self.run_bash(f'cd "{shared}"\nmove_own_omp() {{ echo 999999985; }}\n'
                            f'cmd_move_to --to devbox --cwd "{own}" --leave "{shared}" --dry-run --accept-orphans')
        self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)
        self.assertIn("group g-01a19700, 1 session(s)", dry.stdout)

    def test_a_worktree_of_the_session_s_own_under_a_root_every_box_has_is_copied(self) -> None:
        """LINUX-1248, 2026-11-03: its worktree is /mnt/nvme/sami/wt/linux-1248-eval, reached as
        ~/src/linux-1248-eval, so every box here has it, and the move refused it as "not copied;
        the target must have it already". The target has no such tree; the move re-creates it."""
        repo = self.checkout("big")
        own = self.root / "wt" / "eval"
        self.git("worktree", "add", "-q", "-b", "eval", str(own), cwd=repo)
        (own / "file").write_text("uncommitted\n", encoding="utf-8")
        os.symlink(own, self.src / "eval")
        transcript = self.home / ".omp/agent/sessions/-src-eval" / f"2026-11-03T00-00-00-000Z_{self.TO}.jsonl"
        transcript.parent.mkdir(parents=True)
        transcript.write_text("{}\n", encoding="utf-8")
        self.make_proc(999999990, comm="bash", ppid=1, cwd=own)
        self.make_proc(999999991, ppid=999999990, cwd=own, writes=(transcript,))
        self.set_pids(999999991)
        self.set_panes({999999990: ("%95", "agents:9.5")})
        self.target()
        moved = self.move_to(own=999999991)
        self.assertEqual(moved.returncode, 0, moved.stdout + moved.stderr)
        self.assertNotIn("is not copied", moved.stderr)
        [job] = self.jobs()
        doc = json.loads(job.read_text(encoding="utf-8"))
        self.assertIn(str(own), doc["mirror"])
        self.assertEqual(doc["members"][0]["spec"][:2], ["--dir", str(own)])
        self.assertTrue(any(p.startswith(str(repo / ".git")) for p in doc["merge"]), doc["merge"])

    def test_a_move_to_another_machine_copies_what_the_session_used_not_its_shared_parent(self) -> None:
        """LINUX-1248, 2026-11-03: its inference became the whole shared /mnt/nvme/sami/tmp/
        verified-linux (other agents' trees, unreadable paths) for a session that worked in
        verified-linux/LINUX-1248 and read one brief in verified-linux/voice-1102. A move to
        another machine copies the deepest directories used and the files read, as themselves."""
        verified = self.home / "tmp" / "verified-linux"
        mine = verified / "LINUX-1248"
        mine.mkdir(parents=True)
        (mine / "notes.md").write_text("mine\n", encoding="utf-8")
        (verified / "other" / "pod").mkdir(parents=True)
        brief = verified / "voice-1102" / "brief-LINUX-1248.md"
        brief.parent.mkdir()
        brief.write_text("brief\n", encoding="utf-8")
        (brief.parent / "someone-elses.md").write_text("x\n", encoding="utf-8")
        # The omp holds its own transcript open, as a live omp does; the record is read from it.
        record = self.transcript(self.TO, [("read", {"path": str(brief)})], slug="-work")
        self.make_proc(999999996, comm="bash", ppid=1, cwd=mine)
        self.make_proc(999999997, ppid=999999996, cwd=mine, writes=(record,))
        self.set_pids(999999997)
        self.set_panes({999999996: ("%96", "agents:9.6")})
        self.target()
        moved = self.move_to(own=999999997)
        self.assertEqual(moved.returncode, 0, moved.stdout + moved.stderr)
        [job] = self.jobs()
        doc = json.loads(job.read_text(encoding="utf-8"))
        copied = doc["mirror"] + doc["merge"]
        self.assertIn(str(mine), copied)
        self.assertIn(str(brief), copied)
        self.assertNotIn(str(verified), copied)
        self.assertNotIn(str(brief.parent), copied)
        spec = doc["members"][0]["spec"]
        self.assertEqual(spec[:2], ["--dir", str(mine)])
        self.assertIn(str(brief.parent), spec)
        self.assertNotIn(str(verified), spec)
        # A box on THIS host is still given the bounded directory.
        here = self.run_bash(f'move_dirs_why "{mine}" 999999997 {self.TO}')
        self.assertIn(f"{verified}\t", here.stdout)

    def test_sibling_dir_paths_are_copied_exactly_never_their_parent(self) -> None:
        """S3, 2026-11-03: --dir …/frac-verify/s3, …/shared/leak-check and …/bet257/s3/p5e2e, with
        its record naming files in s3, became all of /mnt/nvme/sami/frac-verify, whose S4 trees
        it cannot read. Each --dir is copied as itself, and nothing joins them to their parent."""
        fv = self.home / "tmp" / "frac-verify"
        dirs = [fv / "s3", fv / "shared" / "leak-check", fv / "bet257" / "s3" / "p5e2e"]
        for d in dirs:
            d.mkdir(parents=True)
            (d / "f").write_text("x\n", encoding="utf-8")
        closed = fv / "s4" / "tmp" / "pytest-of-root"
        closed.mkdir(parents=True)
        closed.chmod(0)
        try:
            self.check_siblings(fv, dirs)
        finally:
            closed.chmod(0o700)

    def check_siblings(self, fv: Path, dirs: list[Path]) -> None:
        code = self.home / "Code"
        code.mkdir(exist_ok=True)
        self.make_proc(999999998, comm="bash", ppid=1, cwd=code)
        self.make_proc(999999999, ppid=999999998, cwd=code, session=self.TO, writes=(fv / "s3" / "report.md",))
        self.transcript(self.TO, [("read", {"path": str(fv / "s3" / "f")}),
                                  ("read", {"path": str(fv / "bet257" / "s3" / "p5e2e" / "f")})], slug="-work")
        self.set_pids(999999999)
        self.set_panes({999999998: ("%97", "agents:9.7")})
        self.target()
        args = " ".join(f'--dir "{d}"' for d in dirs)
        moved = self.move_to(f"--to devbox {args}", own=999999999)
        self.assertEqual(moved.returncode, 0, moved.stdout + moved.stderr)
        [job] = self.jobs()
        doc = json.loads(job.read_text(encoding="utf-8"))
        copied = doc["mirror"] + doc["merge"]
        for d in dirs:
            self.assertIn(str(d), copied)
        for wide in (fv, fv / "bet257", fv / "shared", fv / "s4"):
            self.assertNotIn(str(wide), copied)
            self.assertNotIn(str(wide), doc["members"][0]["spec"])

    def test_a_dir_naming_a_checkout_others_work_in_groups_nobody(self) -> None:
        """S3, 2026-11-03: its --dir of a checkout many sessions work in grew a group whose listing
        ran past 900 s. A tree the session only names is copied (through git when the target has a
        clone); only trees its own live processes work in group it."""
        shared = self.checkout("taiga")
        self.moving_session()
        self.make_proc(999999970, comm="bash", ppid=1, cwd=shared)
        self.make_proc(999999971, ppid=999999970, cwd=shared, session=self.TO2)
        self.set_pids(999999901, 999999971)
        self.set_panes({999999900: ("%81", "agents:4.1"), 999999970: ("%92", "agents:9.2")})
        self.target()
        moved = self.move_to(f'--to devbox --dir "{shared}"')
        self.assertEqual(moved.returncode, 0, moved.stdout + moved.stderr)
        [job] = self.jobs()
        doc = json.loads(job.read_text(encoding="utf-8"))
        self.assertEqual([m["sid"] for m in doc["members"]], [self.TO])
        self.assertIn(str(shared), doc["mirror"])

    def test_a_working_directory_that_holds_other_trees_is_made_empty_not_copied(self) -> None:
        """S3, 2026-11-03: its working directory ~/Code/personal/theorem holds 881 MiB of other
        sessions' worktrees, and the move would have copied all of it. The target gets it empty;
        only what the session used is copied."""
        theorem = self.home / "Code" / "personal" / "theorem"
        (theorem / "worktrees" / "other" / ".git").mkdir(parents=True)
        (theorem / "big.bin").write_text("x" * 1000, encoding="utf-8")
        mine = self.home / "tmp" / "frac-verify" / "s3"
        mine.mkdir(parents=True)
        self.make_proc(999999966, comm="bash", ppid=1, cwd=theorem)
        self.make_proc(999999967, ppid=999999966, cwd=theorem, session=self.TO)
        self.set_pids(999999967)
        self.set_panes({999999966: ("%98", "agents:9.8")})
        self.target()
        moved = self.move_to(f'--to devbox --dir "{mine}"', own=999999967)
        self.assertEqual(moved.returncode, 0, moved.stdout + moved.stderr)
        self.assertIn(f"{theorem} (session {self.TO}'s working directory) is made empty on the target", moved.stderr)
        [job] = self.jobs()
        doc = json.loads(job.read_text(encoding="utf-8"))
        self.assertEqual(doc["mkdir"], [str(theorem)])
        self.assertNotIn(str(theorem), doc["mirror"] + doc["merge"])
        self.assertIn(str(mine), doc["mirror"] + doc["merge"])
        self.assertEqual(doc["members"][0]["spec"][:2], ["--dir", str(theorem)])
        self.set_pids()
        run = self.run_bash(f'move_to_run "{job}"')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        ssh = (self.remote / "SSH").read_text(encoding="utf-8").splitlines()
        self.assertTrue(any("mkdir" in line and str(theorem) in line for line in ssh), ssh[-3:])
        self.assertTrue(theorem.is_dir())
        self.assertNotIn(str(theorem), (self.root / "RSYNC").read_text(encoding="utf-8"))

    def test_a_box_whose_tmux_cannot_reach_the_host_queues_its_move(self) -> None:
        """A tmux client of another version cannot talk to the host's server (the image's 3.4
        against this host's 3.8), so a boxed move could not start its relauncher (2026-11-03). It
        takes the first client that the server answers, and queues the job when none does; the
        host's watch runs `agentbox move --run-queue` each pass."""
        self.moving_session()
        self.target()
        env = {"AGENTBOX_BOX": "agentbox-0123456789ab", "AGENTBOX_DIRS": str(self.lane),
               "OMP_SESSION_ID": self.TO, "TMUX_PANE": "%81"}
        # Every tmux here refuses the server, as a client of the wrong version does.
        write_stub(self.stubs, "tmux", 'echo "server exited unexpectedly" >&2; exit 1')
        queued = self.run_bash(f'move_own_omp() {{ echo 999999901; }}\ncd "{self.lane}"\ncmd_move_to --to devbox --accept-orphans', env)
        self.assertEqual(queued.returncode, 0, queued.stdout + queued.stderr)
        self.assertIn("the job is queued at", queued.stderr)
        queue = self.root / "cache/omp/agentbox/move-to/queue"
        [link] = list(queue.glob("*.json"))
        [job] = self.jobs()
        self.assertEqual(link.resolve(), job.resolve())
        # The host runs it: the job starts and the queue is emptied.
        write_stub(self.stubs, "setsid", 'printf "%s\\n" "$*" >>"$STUB_SETSID"; exit 0')
        ran = self.run_bash("move_to_run_queue")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn(f"started the queued move of group g-01a19700", ran.stdout)
        self.assertEqual(list(queue.glob("*.json")), [])
        # The queue runner starts each job in the background; wait for the stub's record.
        record = Path(self.env["STUB_SETSID"])
        for _ in range(300):
            if record.exists():
                break
            time.sleep(0.1)
        self.assertIn(f"move --to-run {job}", record.read_text(encoding="utf-8"))

    def test_a_box_takes_the_tmux_client_the_host_server_answers(self) -> None:
        self.moving_session()
        self.target()
        good = self.root / "mise-tmux"
        good.mkdir()
        write_stub(good, "tmux", 'exit 0')
        write_stub(self.stubs, "tmux", 'echo "server exited unexpectedly" >&2; exit 1')
        picked = self.run_bash(f'AGENTBOX_TMUX_CLIENT={good}/tmux\nmove_box_tmux')
        self.assertEqual(picked.stdout.strip(), f"{good}/tmux", picked.stderr)
        none = self.run_bash("set +e; move_box_tmux; echo rc=$?")
        self.assertIn("rc=1", none.stdout, none.stderr)

    def test_move_self_fails_when_its_relauncher_does_not_start(self) -> None:
        """LANE-C, 2026-11-03: `agentbox move --self` returned 0 twice and nothing ever moved. The
        hand-off now waits for the relauncher's own first line and fails when it never comes."""
        self.moving_session()
        self.set_pids(999999901)
        self.set_panes({999999900: ("%81", "agents:4.1")})
        env = {"AGENTBOX_HAND_OFF_WAIT": "2"}
        write_stub(self.stubs, "setsid", 'exit 0')  # a relauncher that never runs
        quiet = self.run_bash(f'move_hand_off 999999901 {self.TO} %81 "{self.worktree}" /t/x.jsonl --dir "{self.lane}"', env)
        self.assertEqual(quiet.returncode, 1, quiet.stdout)
        self.assertIn("wrote nothing to", quiet.stderr)
        self.assertIn("the move did NOT start; nothing was hung up", quiet.stderr)
        self.assertNotIn("handed to a detached relauncher", quiet.stdout)
        log = self.root / "cache/omp/agentbox" / f"move-{self.TO}.log"
        write_stub(self.stubs, "setsid", f'echo "the relauncher is up" >>"{log}"; exit 0')
        started = self.run_bash(f'move_hand_off 999999901 {self.TO} %81 "{self.worktree}" /t/x.jsonl --dir "{self.lane}"', env)
        self.assertEqual(started.returncode, 0, started.stderr)
        self.assertIn("handed to a detached relauncher", started.stdout)

    def test_a_named_dir_narrows_an_inferred_parent_and_kernel_paths_never_travel(self) -> None:
        """A `--dir <tree>/sub` must narrow the inferred <tree>, not widen to it, and /proc, /sys,
        /dev and /run are never copied."""
        tree = self.root / "lanework"
        sub = tree / "mine"
        sub.mkdir(parents=True)
        (sub / "f").write_text("x", encoding="utf-8")
        script = (f'MOVE_TO_MIRROR=()\nMOVE_TO_MERGE=("{tree}" "/proc/1/fd" "/run/user/1000")\nMOVE_TO_MKDIR=()\n'
                  f'MOVE_TO_NAMED=("{sub}")\nmove_to_gate "{sub}"\nprintf "merge %s\\n" ${{MOVE_TO_MERGE[@]+"${{MOVE_TO_MERGE[@]}}"}}\n'
                  'printf "unasked %s\\n" ${MOVE_TO_UNASKED[@]+"${MOVE_TO_UNASKED[@]}"}')
        result = self.run_bash(script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"merge {sub}\n", result.stdout)
        self.assertNotIn(f"merge {tree}\n", result.stdout)
        self.assertNotIn("merge /proc", result.stdout)
        self.assertNotIn("merge /run", result.stdout)
        self.assertIn("unasked /proc/1/fd (a kernel filesystem; never copied)", result.stdout)

    def test_a_target_git_clone_is_never_written_by_a_plain_directory(self) -> None:
        """N6Ready, 2026-11-03: rocqperf's ~/src/oss-verifier has no .git of its own (its GIT_DIR
        is elsewhere), so a move copied it as a plain directory into sami-devbox's clone and left
        272 untracked files there. The target's paths are mapped under `far`, as in the clone
        tests, since the stub's target machine is this one."""
        self.target()
        far = self.root / "target-root"
        plain = self.src / "ops"
        plain.mkdir(parents=True)
        (plain / "f").write_text("x", encoding="utf-8")
        (far / "ops" / ".git").mkdir(parents=True)
        check = ('move_remote() { local s="${2//$STUB_SRC/$STUB_FAR}"; bash -c "$s"; }\n'
                 f'MOVE_TO_MIRROR=("{plain}")\nMOVE_TO_MERGE=()\n'
                 'move_to_target_worktrees devbox || echo "WHY $MOVE_TO_WHY"')
        env = {"STUB_SRC": str(self.src), "STUB_FAR": str(far)}
        bad = self.run_bash(check, env)
        self.assertEqual(bad.returncode, 0, bad.stderr)
        self.assertIn(f"WHY devbox keeps a git clone at {plain}", bad.stdout)
        self.assertIn("would leave untracked files in the clone", bad.stdout)
        # A source that is its own work tree is this repository's, and is fine.
        (plain / ".git").mkdir()
        self.assertEqual(self.run_bash(check, env).stdout.strip(), "")

    def test_in_a_box_the_plan_is_this_session_s_own(self) -> None:
        """GRADE, boxed with `move --self`, got "no-pane (parent 96:bun …), which only a person can
        move" from `move --plan --to` (2026-11-03 13:30Z): inside a box the host census sees only
        the box's own processes and reads the omp's agentbox-run.ts parent as no pane. In a box the
        plan is the one its own move would carry, and it changes nothing."""
        transcript = self.moving_session()
        self.target()
        env = {"AGENTBOX_BOX": "agentbox-0123456789ab", "AGENTBOX_DIRS": str(self.lane),
               "OMP_SESSION_ID": self.TO, "TMUX_PANE": "%81"}
        plan = self.run_bash(f'move_own_omp() {{ echo 999999901; }}\ncd "{self.lane}"\ncmd_move_plan --to devbox', env)
        self.assertEqual(plan.returncode, 0, plan.stdout + plan.stderr)
        self.assertNotIn("only a person can move", plan.stdout + plan.stderr)
        self.assertIn(f"session {self.TO}, in box agentbox-0123456789ab, moves to devbox", plan.stdout)
        self.assertIn("CARRIED to devbox", plan.stdout)
        self.assertIn(str(transcript), plan.stdout)
        self.assertIn("--dry-run: nothing was hung up, copied or sent", plan.stdout)
        self.assertEqual(self.jobs(), [])
        for flag in ("--all", f"--group {self.TO}"):
            refused = self.run_bash(f'cd "{self.lane}"\ncmd_move_plan --to devbox {flag}', env)
            self.assertEqual(refused.returncode, 1, flag)
            self.assertIn("plans this session only", refused.stderr)

    def test_the_plan_of_a_move_is_the_caller_s_own_group_unless_told_otherwise(self) -> None:
        """By default the plan answers the question a session asks before it moves - its own
        group, found as `move --to` finds it - and reads no other session's paths; --group names
        another session, and --all plans every group."""
        self.moving_session()
        self.make_proc(999999910, comm="bash", ppid=1, cwd=self.worktree)
        self.make_proc(999999911, ppid=999999910, cwd=self.worktree, session=self.TO2)
        apart = self.root / "apart"
        apart.mkdir()
        third = "01a19700-0000-7000-8000-0000000e0003"
        self.make_proc(999999950, comm="bash", ppid=1, cwd=apart)
        self.make_proc(999999951, ppid=999999950, cwd=apart, session=third)
        self.set_pids(999999901, 999999911, 999999951)
        self.set_panes({999999900: ("%81", "agents:4.1"), 999999910: ("%82", "agents:5.1"),
                        999999950: ("%86", "agents:9.1")})
        self.target()
        log = self.root / "PATHS"
        wrap = ('eval "orig_$(declare -f move_to_session_paths)"\n'
                f'move_to_session_paths() {{ echo "$2" >>"{log}"; orig_move_to_session_paths "$@"; }}\n')
        own = self.run_bash(wrap + "cmd_move_plan --to devbox", {"OMP_SESSION_ID": self.TO})
        self.assertEqual(own.returncode, 0, own.stderr)
        self.assertIn("group g-01a19700: 2 session(s)", own.stdout)
        self.assertNotIn(third, own.stdout)
        self.assertEqual(sorted(log.read_text(encoding="utf-8").split()), [self.TO, self.TO2])
        other = self.run_bash("cmd_move_plan --to devbox --group 01a19700-0000-7000-8000-0000000e0003")
        self.assertEqual(other.returncode, 0, other.stderr)
        self.assertIn("1 group(s), 1 session(s)", other.stdout)
        self.assertIn(third, other.stdout)
        every = self.run_bash("cmd_move_plan --to devbox --all")
        self.assertIn("2 group(s), 3 session(s)", every.stdout)
        nobody = self.run_bash("cmd_move_plan --to devbox")
        self.assertEqual(nobody.returncode, 1)
        self.assertIn("name one with --group <session>, or plan every group with --all", nobody.stderr)

    def test_an_archived_session_directory_is_copied_once(self) -> None:
        """The archiver leaves one symlink per moved file of a session directory, all into one
        directory of the same name on the cold disk: that directory is one path to copy, not one
        per file (a lead's plan listed 330)."""
        own = self.home / ".omp/agent/sessions/-lane/2026-11-02T10-00-00-000Z_01a19700-0000-7000-8000-0000000e0009"
        own.mkdir(parents=True)
        own.with_suffix(".jsonl").write_text("{}\n", encoding="utf-8")
        cold = self.root / "durable/omp-sessions-moved/-lane" / own.name
        cold.mkdir(parents=True)
        for i in range(3):
            (cold / f"{i}.bash.log").write_text("x\n", encoding="utf-8")
            os.symlink(cold / f"{i}.bash.log", own / f"{i}.bash.log")
        lone = self.root / "elsewhere.jsonl"
        lone.write_text("{}\n", encoding="utf-8")
        os.symlink(lone, own / "Lone.jsonl")
        result = self.run_bash(f'move_to_transcript_paths "{own}.jsonl"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(result.stdout.splitlines()), sorted([
            f"mirror\t{own}.jsonl", f"mirror\t{own}", f"mirror\t{cold}", f"mirror\t{lone}"]))

    def test_the_plan_lists_each_group_s_copy_and_size_and_changes_nothing(self) -> None:
        transcript = self.moving_session()
        self.set_pids(999999901)
        self.set_panes({999999900: ("%81", "agents:4.1")})
        self.target()
        plan = self.run_bash("cmd_move_plan --to devbox --all")
        self.assertEqual(plan.returncode, 0, plan.stderr)
        self.assertIn("nothing is changed, copied or sent", plan.stdout)
        self.assertIn("group g-01a19700: 1 session(s)", plan.stdout)
        self.assertIn(f"resume on devbox: /target/agentbox new -e OMP_PROFILE=pbc --dir {self.worktree} --dir {self.lane} -- omp --resume {transcript}", plan.stdout)
        # The mirrored paths are measured; the merged ones are listed unmeasured, since rsync sends
        # only what is newer than the target's copy.
        for path in (transcript, self.durable, self.worktree, self.other):
            self.assertRegex(plan.stdout, rf"\s\d[\d.]*[KMG]?\s+{path}\n")
        for path in (self.lane, self.src / "repo/.git"):
            self.assertRegex(plan.stdout, rf"\s-\s+{path}\n")
        self.assertIn("1 group(s), 1 session(s), 0 refused", plan.stdout)
        self.assertFalse((self.remote / "SSH").exists())
        self.assertFalse(self.plan_dir.exists())
        # The plan writes no job and leaves no index behind (it makes one under the move cache).
        self.assertEqual(list((self.root / "cache/omp/agentbox/move-to").glob("*.json")), [])
        self.assertEqual(list((self.root / "cache/omp/agentbox/move-to/index").glob("run.*")), [])

    def test_a_session_in_a_box_hands_its_move_to_the_host(self) -> None:
        transcript = self.moving_session()
        self.target()
        env = {"AGENTBOX_BOX": "agentbox-0123456789ab", "AGENTBOX_DIRS": str(self.lane),
               "OMP_SESSION_ID": self.TO, "TMUX_PANE": "%81"}
        moved = self.run_bash(f'move_own_omp() {{ echo 999999901; }}\ncd "{self.worktree}"\ncmd_move_to --to devbox --accept-orphans', env)
        self.assertEqual(moved.returncode, 0, moved.stdout + moved.stderr)
        [job] = self.jobs()
        [member] = json.loads(job.read_text(encoding="utf-8"))["members"]
        self.assertEqual((member["box"], member["pid"], member["resume"]), ("agentbox-0123456789ab", 0, str(transcript)))
        self.assertEqual(member["spec"], ["-e", "OMP_PROFILE=pbc", "--dir", str(self.lane)])
        [call] = [c for c in self.local_tmux.read_text(encoding="utf-8").splitlines() if "run-shell" in c]
        self.assertIn(f"run-shell -b -t %81 setsid {AGENTBOX} move --to-run {job}", call)
        # The host's relauncher hangs the box up with docker and moves it as any other.
        write_stub(self.stubs, "docker", '[[ "$1" == inspect ]] && exit 1; printf "%s\\n" "$*" >>"$STUB_DOCKER"; exit 0')
        run = self.run_bash(f'move_to_run "{job}"')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("kill -s SIGHUP agentbox-0123456789ab", (self.root / "STUB_DOCKER").read_text(encoding="utf-8"))
        self.assertEqual(self.move_log()[0][5], "moved")

    def test_a_transcript_the_session_sweep_took_is_resumed_by_its_recorded_path(self) -> None:
        # dispatch://OSSV-240 deletes a transcript idle for 6 hours, even under a live omp; omp
        # resumes it from Postgres by the path the sweep recorded.
        swept = self.home / ".omp/agent/sessions/-lane" / f"2026-10-25T02-17-36-102Z_{self.METARQ}.jsonl"
        record = self.root / "deleted.tsv"
        record.write_text(f"{swept}\t100\tabc\t\t2026-11-02T08:00:00Z\n", encoding="utf-8")
        self.nested_session(None, cmdline=f"omp --resume {self.METARQ}")
        moved = self.run_bash('move_own_omp() { echo 1101; }\nmove_image_bytes() { echo 280000000; }\n'
                              'cmd_move_self --dry-run', {"AGENTBOX_SWEEP_RECORD": str(record)})
        self.assertEqual(moved.returncode, 0, moved.stderr)
        self.assertIn(f"-- omp --resume {swept}\n", moved.stdout)

    def agent_db(self, home: Path, rows: list[tuple[str, str | None]]) -> None:
        """An omp agent.db under `home` holding credential rows (provider, disabled_cause)."""
        path = home / ".omp/agent/agent.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        script = "create table auth_credentials (id integer primary key, provider text, credential_type text," \
            " data text, disabled_cause text);"
        for provider, cause in rows:
            cause_sql = "null" if cause is None else f"'{cause}'"
            script += f"insert into auth_credentials (provider, credential_type, data, disabled_cause)" \
                f" values ('{provider}', 'oauth', 'secret-never-read', {cause_sql});"
        subprocess.run(["sqlite3", str(path), script], check=True)

    def test_a_move_warns_of_mcp_logins_the_target_lacks(self) -> None:
        """omp keeps MCP OAuth logins per machine. The chief of staff moved to sami-devbox, which had
        no Slack login, and its Slack tools silently never mounted (2026-11-03). The move must say
        which servers the target lacks a login for, and never print a credential's data."""
        slack = "mcp_oauth:profile:default:https://mcp.slack.com/mcp"
        other = "mcp_oauth:profile:default:https://mcp.example.com/mcp"
        self.agent_db(self.home, [(slack, None), (other, None), ("anthropic", None),
                                  ("mcp_oauth:profile:default:https://old.example.com/mcp", "replaced")])
        target_home = self.root / "target-home"
        self.agent_db(target_home, [(other, None), (slack, "replaced by newer credential")])
        remote = f'move_remote() {{ HOME="{target_home}" bash -c "$2"; }}\n'

        warned = self.run_bash(remote + "move_to_warn_mcp_logins sami-devbox")
        self.assertEqual(warned.returncode, 0, warned.stderr)
        self.assertIn("holds no login for the MCP server(s) https://mcp.slack.com/mcp -", warned.stderr)
        self.assertIn("/mcp reauth <server>", warned.stderr)
        self.assertNotIn("mcp.example.com", warned.stderr)
        self.assertNotIn("old.example.com", warned.stderr)
        self.assertNotIn("secret-never-read", warned.stderr + warned.stdout)

        # A target holding every login, and a host holding none, say nothing.
        self.agent_db(target_home / "full", [(slack, None), (other, None)])
        full = f'move_remote() {{ HOME="{target_home / "full"}" bash -c "$2"; }}\n'
        self.assertEqual(self.run_bash(full + "move_to_warn_mcp_logins sami-devbox").stderr, "")
        (self.home / ".omp/agent/agent.db").unlink()
        self.assertEqual(self.run_bash(remote + "move_to_warn_mcp_logins sami-devbox").stderr, "")

        # A target whose agent.db cannot be read lacks every login.
        absent = f'move_remote() {{ HOME="{self.root / "nowhere"}" bash -c "$2"; }}\n'
        self.agent_db(self.home, [(slack, None)])
        self.assertIn("https://mcp.slack.com/mcp", self.run_bash(absent + "move_to_warn_mcp_logins t").stderr)

if __name__ == "__main__":
    unittest.main()
