#!/usr/bin/env python3
"""envoy-role-holder-watch ends its period inside its own TimeoutStartSec when a read stalls.

On 2026-11-03 three runs (started 07:14:50Z, 07:19:21Z and 07:20:56Z, at io some 87% with 305
tasks blocked) were killed at the unit's 90 s having logged nothing at all, while the runs
between them finished in 2-12 s (dispatch://INFRA-672). Its budget is a clock it checks between
two units of work, and a read the box has stalled never returns to look at it. Every disk and
/proc read now runs in a forked child under a hard limit (`bounded_step`); one that does not
answer in time is cut, the pane or role it was for is not judged, and the run goes on.

A stalled read is played by a FIFO nobody writes: opening it for reading blocks, exactly as a
read the box has stalled blocks, and no clock checked inside the step can end it. The SIGALRM
safety net (`Stalled`) only exists so that the script before this change -- which blocks in the
run itself -- fails here on its timing instead of hanging the suite.

Set RHW_SCRIPT to run these against another copy of the script (the before-change run).
"""

import argparse
import importlib.machinery
import importlib.util
import json
import os
import signal
import socket
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(os.environ.get("RHW_SCRIPT") or Path(__file__).resolve().parents[1] / "envoy-role-holder-watch")
LOADER = importlib.machinery.SourceFileLoader("envoy_role_holder_watch_stalls", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
WATCH = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(WATCH)

HOST = socket.gethostname()
SEEN = "01a19366-958c-7295-8c2b-171fa219c033"
UNSEEN = "01a18f00-e348-7617-8e7a-a1d0b774b455"
STEP = 2.0  # each bounded read's own limit in these runs
SLACK = 6.0  # what a loaded box adds to a period: forks, process starts, the state commits
STALL = 30  # when the safety net gives up on a run that is still blocked


class Stalled(Exception):
    """The run was still blocked in a stalled read when the safety net fired."""


def stalled(signum, frame):
    raise Stalled(f"the run was still blocked in a stalled read after {STALL} s")


class RunEnvoy:
    """Envoy with a fixed session list and a 404 for every registry read; records sends."""

    url = "http://127.0.0.1:9020"
    sessions_list: list = []
    sent: list = []

    def __init__(self, timeout: float = 8.0) -> None:
        pass

    def sessions(self) -> list:
        return list(RunEnvoy.sessions_list)

    def registry(self, session_id: str):
        return 404, {"error": "not found"}

    def send(self, session_id: str, message: str) -> bool:
        RunEnvoy.sent.append((session_id, message))
        return True

    def publish(self, topic: str, message: str) -> bool:
        return True


class StalledRead(unittest.TestCase):
    """A /proc tree, a session root, a sweep record and a state file in a temp directory."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.proc = self.root / "proc"
        self.proc.mkdir()
        self.sessions = self.root / "home/.omp/agent/sessions"
        self.sessions.mkdir(parents=True)
        self.sweep = self.root / "omp-session-sql"
        self.sweep.mkdir()
        self.state = self.root / "state.json"
        self.logs: list[str] = []
        self.panes: list[dict] = []
        RunEnvoy.sessions_list, RunEnvoy.sent = [], []
        for name, value in (
            ("PROC_ROOT", self.proc), ("SESSION_ROOT", self.sessions), ("SWEEP_STATE", self.sweep),
            ("MOVE_TO_ACTIVE", self.root / "move-to"), ("MOVED_INDEX", self.root / "moved"),
            ("RESURRECT_DIR", self.root / "resurrect"), ("Envoy", RunEnvoy), ("log", self.logs.append),
            ("tmux_panes", lambda: list(self.panes)), ("process_started", time.time),
            ("STEP_SECONDS", STEP), ("_TRANSCRIPT_INDEX", None), ("_SWEEP_INDEX", None),
            ("_PROC_TABLE", None), ("_OPEN_JSONL", None),
        ):
            original = getattr(WATCH, name, None)
            setattr(WATCH, name, value)
            self.addCleanup(setattr, WATCH, name, original)
        for memo in (WATCH._KNOWN_TRANSCRIPTS, WATCH._SWEPT, WATCH._HEADER_IDS, WATCH._HEADER_CACHE, WATCH._HEADER_USED):
            memo.clear()
            self.addCleanup(memo.clear)
        previous = signal.signal(signal.SIGALRM, stalled)
        self.addCleanup(signal.signal, signal.SIGALRM, previous)
        self.addCleanup(signal.alarm, 0)

    def process(self, pid: int, comm: str, argv: list[str] | None, tpgid: int = -1) -> Path:
        """One process of the /proc tree; argv None leaves its cmdline to the caller."""
        directory = self.proc / str(pid)
        (directory / "fd").mkdir(parents=True)
        (directory / "fdinfo").mkdir()
        (directory / "stat").write_text(f"{pid} ({comm}) S 1 {pid} {pid} 34816 {tpgid} 0\n")
        if argv is not None:
            (directory / "cmdline").write_bytes(b"\0".join(word.encode() for word in argv) + b"\0")
        return directory / "cmdline"

    def role(self, name: str, holder: str, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        RunEnvoy.sessions_list.append(
            {"session_id": holder, "host": HOST, "roles": [name], "dir": str(directory), "last_seen": 0}
        )

    def period(self, budget: float = 30.0) -> float:
        """One run_once; its wall clock. A run still blocked after STALL seconds fails the test."""
        options = argparse.Namespace(
            state=self.state, dry_run=True, write_state=True, print_alert=False, http_timeout=1.0,
            grace=60.0, write_grace=120.0, dir_grace=300.0, escalate=600.0, pane_grace=180.0,
            move_grace=1800.0, budget=budget,
        )
        self.logs.clear()
        began = time.monotonic()
        signal.alarm(STALL)
        try:
            self.assertEqual(WATCH.run_once(options), 0)
        except Stalled as error:
            self.fail(f"{error}; it logged: {self.logs}")
        finally:
            signal.alarm(0)
        return time.monotonic() - began

    def data(self) -> dict:
        return json.loads(self.state.read_text())

    def summary(self) -> str:
        return next(line for line in reversed(self.logs) if line.startswith("checked "))


class StalledProcessScanTest(StalledRead):
    def test_a_stalled_proc_read_stops_the_run_inside_its_budget_with_nothing_decided(self) -> None:
        """An omp's /proc/<pid>/cmdline that never answers: the run stops at its budget, says the
        process scan was cut, and judges no holder on a half-read process table."""
        self.process(4242, "omp", ["omp", "--resume", SEEN])
        cmdline = self.process(4343, "omp", None)
        os.mkfifo(cmdline)
        self.role("lane-owner-seen", SEEN, self.root / "src/seen")

        took = self.period(budget=4.0)

        self.assertLess(took, 4.0 + SLACK, self.logs)
        self.assertIn("STOPPED at its 4 s budget in the process scan", self.summary())
        self.assertIn("read(s) cut at their limit, nothing decided on them: the process scan", self.summary())
        self.assertEqual(self.data().get("incidents") or {}, {})
        self.assertFalse([line for line in self.logs if line.startswith("lane-owner-seen")], self.logs)

        # The read answers again: the next period reads it, and the holder is seen.
        cmdline.unlink()
        cmdline.write_bytes(b"omp\0--version\0")
        self.period(budget=30.0)
        self.assertIn(
            f"lane-owner-seen: ok (holder {SEEN}, an omp process holds this session: pid 4242)", self.logs
        )
        self.assertNotIn("cut at their limit", self.summary())


class StalledRoleEvidenceTest(StalledRead):
    def test_a_stalled_read_for_one_holder_skips_that_role_and_judges_the_next(self) -> None:
        """The session sweep's record of an unseen holder never answers: that role is not judged
        -- no incident, no alert -- and the role after it is."""
        self.process(4242, "omp", ["omp", "--resume", SEEN])
        os.mkfifo(self.sweep / "deleted.tsv")
        self.role("a-lane-owner", UNSEEN, self.root / "src/unseen")
        self.role("b-lane-owner", SEEN, self.root / "src/seen")

        took = self.period()

        self.assertLess(took, STEP + SLACK, self.logs)
        self.assertTrue(
            any(line.startswith(f"a-lane-owner: holder {UNSEEN} not judged this run: the session sweep's"
                                " record, cut after 2 s") for line in self.logs),
            self.logs,
        )
        self.assertIn(
            f"b-lane-owner: ok (holder {SEEN}, an omp process holds this session: pid 4242)", self.logs
        )
        self.assertEqual(self.data().get("incidents") or {}, {})
        self.assertIn("1 read(s) cut at their limit", self.summary())


class StalledPaneReadTest(StalledRead):
    def test_a_stalled_transcript_in_a_pane_directory_leaves_the_pane_unjudged(self) -> None:
        """An agent pane at a shell whose session directory holds a transcript that never
        answers: the pane is not judged -- no incident opened -- and the run ends on time."""
        cwd = self.root / "src/pane-lane"
        cwd.mkdir(parents=True)
        directory = WATCH.session_dir_of(str(cwd))
        directory.mkdir(parents=True)
        os.mkfifo(directory / f"2026-11-03T00-00-00-000Z_{UNSEEN}.jsonl")
        self.process(5151, "bash", ["bash"], tpgid=5151)
        self.panes = [{
            "pid": "900", "start_time": "1", "pane_id": "%7", "session_name": "lanes",
            "window_index": "0", "window_name": "lane", "pane_index": "0",
            "pane_current_command": "bash", "pane_pid": "5151", "pane_current_path": str(cwd),
            "pane_title": "π Fix the parser", "@agent_oneshot": "",
        }]

        took = self.period()

        self.assertLess(took, STEP + SLACK, self.logs)
        self.assertTrue(
            any(line.startswith("pane %7 lanes:0.0: not judged this run: the pane directory's"
                                " transcript titles, cut after 2 s") for line in self.logs),
            self.logs,
        )
        self.assertEqual(self.data().get("pane_incidents") or {}, {})


if __name__ == "__main__":
    unittest.main()
