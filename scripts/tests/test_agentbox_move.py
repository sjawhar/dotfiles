#!/usr/bin/env python3
"""`agentbox move` takes the sessions already running on this host into boxes (dispatch://INFRA-702).

`agentbox new` starts a box for a NEW session. `move` reads every live top-level omp outside a
box, works out the box each one would get, and relaunches its pane into `agentbox new <spec> --
omp --resume <id> .`. The three things that decide whether a window of about a hundred sessions
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
    ) -> None:
        """One process in the fixture /proc: stat, cmdline, cgroup, cwd and its open files."""
        directory = self.proc / str(pid)
        (directory / "fd").mkdir(parents=True)
        (directory / "stat").write_text(f"{pid} ({comm}) S {ppid} {ppid} 0 0 -1 0\n", encoding="utf-8")
        (directory / "cmdline").write_text(cmdline.replace(" ", "\0") + "\0", encoding="utf-8")
        (directory / "cgroup").write_text(cgroup + "\n", encoding="utf-8")
        os.symlink(str(cwd), directory / "cwd")
        if session:
            transcript = self.home / ".omp/agent/sessions/-work" / f"2026-11-01T09:00:00Z_{session}.jsonl"
            transcript.parent.mkdir(parents=True, exist_ok=True)
            transcript.write_text("", encoding="utf-8")
            os.symlink(str(transcript), directory / "fd" / "21")

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
    def test_a_box_that_would_take_the_disk_under_its_floor_is_refused(self) -> None:
        lane = self.root / "lane"
        lane.mkdir()
        self.make_proc(800, comm="bash", ppid=1, cwd=lane)
        self.make_proc(801, cwd=lane, ppid=800, session="01a19700-0000-7000-8000-00000000c001")
        self.set_pids(801)
        self.set_panes({800: ("%5", "agents:4.1")})
        # 74.5 GiB free on /mnt/nvme, whose floor is 84 GB (78.2 GiB): the box is refused, and
        # the refusal comes before anything is touched, so the dry run never prints its launch.
        write_stub(self.stubs, "df", 'if [[ "$*" == *target* ]]; then echo target; echo /mnt/nvme; else echo avail; echo 80000000000; fi')
        refused = self.run_bash(
            'move_own_omp() { echo 801; }\nmove_image_bytes() { echo 280000000; }\ncmd_move_self --dry-run'
        )
        self.assertEqual(refused.returncode, 2)
        self.assertIn("under its 78.2 GiB floor", refused.stderr)
        self.assertEqual(refused.stdout, "")
        # 200 GiB free, and the same box is allowed through to its dry run.
        write_stub(self.stubs, "df", 'if [[ "$*" == *target* ]]; then echo target; echo /mnt/nvme; else echo avail; echo 214748364800; fi')
        allowed = self.run_bash(
            'move_own_omp() { echo 801; }\nmove_image_bytes() { echo 280000000; }\ncmd_move_self --dry-run'
        )
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertIn(
            f"agentbox new --dir {lane} -- omp --resume 01a19700-0000-7000-8000-00000000c001 .",
            allowed.stdout,
        )

    def test_a_session_already_in_a_box_is_told_to_restart_instead_of_moving(self) -> None:
        result = self.run_bash("cmd_move_self", {"AGENTBOX_BOX": "agentbox-aaaaaaaaaaaa"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("already runs in box agentbox-aaaaaaaaaaaa", result.stderr)
        self.assertIn("agentbox restart", result.stderr)

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
        self.assertIn(
            f"agentbox new --dir {lane} -- omp --resume 01a19700-0000-7000-8000-00000000d001 .", notice
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
            plan["summary"]["headroom_bytes"], 214748364800 - 84000000000 - 1073741824
        )
        self.assertEqual(plan["sessions"][0]["session"], "01a19700-0000-7000-8000-000000010001")
        self.assertEqual(plan["sessions"][0]["spec"], f"--dir {lane}")


if __name__ == "__main__":
    unittest.main()
