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
        self.assertIn(f"--dir {extra} -- omp --resume 01a170c2-69f0-7542-a611-195ce0c4a111", moved.stdout)
        refused = self.run_bash(env + f'cmd_move_notify --all --now --dry-run --dir "{extra}"')
        self.assertEqual(refused.returncode, 1)
        self.assertIn("--dir names paths of ONE session", refused.stderr)

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
            f'move_relaunch 999999999 01a170c2-69f0-7542-a611-195ce0c4a111 %70 "{lane}" --dir "{lane}"',
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
            f"agentbox new --dir {lane} -- omp --resume 01a19700-0000-7000-8000-00000000c001\n",
            allowed.stdout,
        )

    def test_a_session_inside_a_box_can_move_again_with_one_more_dir(self) -> None:
        """A box must not be a one-way door. Every box defect found so far is fixed by moving
        again with one more --dir, and a boxed session could do neither: the path is the HOST's,
        so checking it in the box's own view answered `--dir '<path>' is not an existing
        directory`, and --self then refused outright."""
        host_only = "/mnt/nvme/sami/tmp/a-path-this-box-does-not-have"
        self.assertFalse(Path(host_only).exists(), "the fixture needs a path this host lacks")
        env = {
            "AGENTBOX_BOX": "agentbox-aaaaaaaaaaaa",
            "AGENTBOX_DIRS": f"{self.home}/lane:{self.home}/evidence",
            "OMP_SESSION_ID": "01a19700-0000-7000-8000-000000000d09",
            "TMUX_PANE": "%7",
        }
        result = self.run_bash(f'cmd_move_self --dry-run --dir "{host_only}"', env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already in box agentbox-aaaaaaaaaaaa", result.stdout)
        # This box's own paths, then the new one, and the session resumed by its own id.
        self.assertIn(
            f"--dir {self.home}/lane --dir {self.home}/evidence --dir {host_only} "
            "-- omp --resume 01a19700-0000-7000-8000-000000000d09\n",
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
            f"agentbox new --dir {lane} -- omp --resume 01a19700-0000-7000-8000-00000000d001`", notice
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
        result = self.run_bash(f"move_pane_command {sid} --dir /lane")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"move --wake {sid}", result.stdout)
        self.assertTrue(result.stdout.rstrip().endswith(f"agentbox new --dir /lane -- omp --resume {sid}"), result.stdout)

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


if __name__ == "__main__":
    unittest.main()
