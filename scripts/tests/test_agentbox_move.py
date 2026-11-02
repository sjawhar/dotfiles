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

import json
import os
import stat
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
    ) -> None:
        """One process in the fixture /proc: stat, comm, cmdline, cgroup, cwd and its open files.

        `writes` and `reads` are files the process holds open; each gets an fd symlink and the
        `flags:` line of /proc/<pid>/fdinfo/<fd> that says which, since only a file open for
        WRITING names a directory the session's box must be given."""
        directory = self.proc / str(pid)
        (directory / "fd").mkdir(parents=True)
        (directory / "fdinfo").mkdir(parents=True)
        (directory / "stat").write_text(f"{pid} ({comm}) S {ppid} {ppid} 0 0 -1 0\n", encoding="utf-8")
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

    def run_bash(self, script: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "-c", f'source "{AGENTBOX}"\n{script}'],
            capture_output=True,
            text=True,
            env={**self.env, **(extra_env or {})},
            check=False,
            timeout=300,
        )

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
        mirror, merge = (self.root / "RSYNC").read_text(encoding="utf-8").splitlines()
        self.assertIn(f"--delete --backup --backup-dir={self.cutover}/replaced/", mirror)
        self.assertIn("--exclude=agentbox-moved", mirror)
        self.assertTrue(mirror.endswith(" sami@devbox:/"), mirror)
        self.assertIn(f" {transcript} ", mirror)
        # The target has a clone of both repositories (the stub's target is this machine), so
        # their storage is not copied: the trees go through git and their .git stays out of the
        # mirror.
        self.assertIn(f"--exclude={self.src / 'other/.git'}/ ", mirror)
        self.assertIn(f"--exclude={self.worktree}/.git ", mirror)
        self.assertIn(f"--update --exclude=agentbox-moved -e ssh -o BatchMode=yes -o ConnectTimeout=20 {self.lane} sami@devbox:/", merge)
        self.assertIn("through git:", run.stdout)
        for marker in (transcript.with_suffix("") / "agentbox-moved", self.home / ".omp/agentbox-moved" / self.TO):
            body = marker.read_text(encoding="utf-8")
            self.assertIn("from=oldbox\nto=devbox\n", body)
            self.assertIn(f"transcript={transcript}\n", body)
        [started] = [c for c in (self.remote / "TMUX").read_text(encoding="utf-8").splitlines() if c.startswith("new-")]
        self.assertIn("new-session -d -P -F #{window_id} -s moved -n 01a19700", started)
        # A --dir the target lacks is left out at launch there, as on a resume here.
        self.assertIn(f"/target/agentbox new --drop-gone-dirs -e OMP_PROFILE=pbc --dir {self.worktree} --dir {self.lane} -- omp --resume {transcript}", started)
        [line] = self.move_log()
        self.assertEqual(line[1:], [self.TO, "g-01a19700", "oldbox", "devbox", "moved", str(transcript)])
        respawn = [c for c in self.local_tmux.read_text(encoding="utf-8").splitlines() if "respawn-pane" in c]
        self.assertEqual(len(respawn), 1, respawn)
        self.assertIn("-t %81", respawn[0])
        self.assertIn(f"session {self.TO} moved to devbox", respawn[0])

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
        _mirror, merge = (self.root / "RSYNC").read_text(encoding="utf-8").splitlines()
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
        self.assertIn(f"nothing was hung up: its working directory {root} is not on devbox", refused.stderr)
        self.assertEqual(self.jobs(), [])
        (self.remote / "MISSING").unlink()
        self.assertEqual(self.move_to(own=999999941).returncode, 0)
        [job] = self.jobs()
        (self.remote / "MISSING").write_text(f"{root}\n", encoding="utf-8")
        run = self.run_bash(f'move_to_run "{job}"')
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertIn("refused, nothing was hung up: its working directory", run.stdout)
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
        self.make_proc(1105, comm="python3", ppid=1104, cwd=lane, cmdline="python3 serve.py")
        self.set_pids(1101)
        self.set_panes({1100: ("%90", "agents:9.1")})

    def test_a_move_lists_and_refuses_what_dies_with_the_hang_up(self) -> None:
        self.orphan_session()
        env = 'move_own_omp() { echo 1101; }\nmove_image_bytes() { echo 280000000; }\nmove_hand_off() { echo HANDED-OFF; }\n'
        refused = self.run_bash(env + "cmd_move_self")
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertNotIn("HANDED-OFF", refused.stdout)
        self.assertIn("nothing was hung up: these processes of this session are not systemd units", refused.stderr)
        self.assertIn("sleep 1000 (pid 1102)", refused.stderr)
        self.assertIn("python3 serve.py (pid 1105)", refused.stderr)
        self.assertNotIn("__omp_worker", refused.stderr)
        self.assertNotIn("runner-ab12", refused.stderr)
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
        self.assertIn("nothing was hung up: these processes of this session are not systemd units", refused.stderr)
        self.assertEqual(self.jobs(), [])

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
        self.assertFalse((self.root / "cache/omp/agentbox/move-to").exists())

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

if __name__ == "__main__":
    unittest.main()
