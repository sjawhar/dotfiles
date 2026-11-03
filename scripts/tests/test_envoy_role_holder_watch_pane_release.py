#!/usr/bin/env python3
"""Regression tests for the pane check's false "AGENT PANE AT SHELL" alerts of 2026-11-03.

- %2177 (gt1498-xfam) and %2176 (gt1498-initial) ran one-shot review sessions 01a1a031-90db and
  01a1a031-9157, started 06:56Z, which ended with a normal session_exit five minutes later; both
  alerted three minutes after that.
- %1740 and %1741 alerted at 05:12:57Z and 05:35:37Z while `agentbox move --to sami-devbox` was
  moving their sessions (01a19e3c-8d64, 01a19e3c-8da0) off this machine.
- %40 (session 01a19366, NICKMINE) closed itself on purpose and still alerted every few minutes:
  each time the session ran in a process outside the pane the incident was cleared, and each time
  it stopped the pane opened a new incident and alerted again (06:48 clear, 06:54 alert, 07:04
  clear, 07:09 alert).

Pinned here: a pane whose last omp exited normally, ran a one-shot prompt, or whose session an
`agentbox move` owns or moved away never counts as at a shell; a pane whose session was seen
running elsewhere is not that lane's pane until omp runs in it again; a pane alerts at most once
per session until omp runs in it again. And what it still catches: an omp that died without a
normal exit.

Nothing here reads the real tree, tmux, /proc or Envoy. Set RHW_SCRIPT to run these against
another copy of the script (the before-change run).
"""

import argparse
import importlib.machinery
import importlib.util
import json
import os
import socket
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(os.environ.get("RHW_SCRIPT") or Path(__file__).resolve().parents[1] / "envoy-role-holder-watch")
LOADER = importlib.machinery.SourceFileLoader("envoy_role_holder_watch_pane_release", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
WATCH = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(WATCH)

SESSION = "01a1a031-90db-732b-bec2-7fd7d9f2c5f0"
SUBAGENT = "01a19ed8-665e-720d-abb6-8dd182fb665d"
HERE = os.environ.get("AGENTBOX_HOST") or socket.gethostname().split(".")[0]
EXIT = (
    '{"type":"custom","customType":"session_exit","data":{"reason":"dispose","kind":"normal",'
    '"recordedAt":"2026-11-03T07:01:05.193Z"},"id":"9d6150c1","timestamp":"2026-11-03T07:01:05.193Z"}\n'
)
WORK = '{"type":"message","id":"93897a46","timestamp":"2026-11-03T07:00:59.000Z","message":{}}\n'
OMP_PID, SHELL_PID, ELSEWHERE_PID = 300, 200, 999


class FakeEnvoy:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, session_id: str, message: str) -> bool:
        self.sent.append((session_id, message))
        return True


class FakeState:
    def __init__(self) -> None:
        self.data: dict = {}

    def commit(self, options) -> None:
        pass


class Pane(unittest.TestCase):
    """One agent pane on a fabricated tmux server, driven period by period."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cwd = "/home/sami/src/bet-grade"
        self.logs: list[str] = []
        self.omp_running = True
        self.children: list[int] = []
        for name, value in (
            ("SESSION_ROOT", self.root / "sessions"),
            ("MOVE_TO_ACTIVE", self.root / "move-to/active"),
            ("MOVED_INDEX", self.root / "agentbox-moved"),
            ("log", self.logs.append),
            ("tmux_panes", lambda: [self.pane()]),
            ("process_table", self.table),
            ("handover_files", lambda session, directory: []),
            ("transcript_by_title", lambda cwd, text, held: None),
            ("resurrect_command", lambda pane, text, limit=60: None),
        ):
            original = getattr(WATCH, name, None)
            setattr(WATCH, name, value)
            self.addCleanup(setattr, WATCH, name, original)
        (self.root / "sessions").mkdir()
        # The walk of the session root is memoised per run; a new root is a new run.
        original_index = WATCH._TRANSCRIPT_INDEX
        WATCH._TRANSCRIPT_INDEX = None
        self.addCleanup(setattr, WATCH, "_TRANSCRIPT_INDEX", original_index)
        WATCH._KNOWN_TRANSCRIPTS.clear()
        self.addCleanup(WATCH._KNOWN_TRANSCRIPTS.clear)
        self.omp_info = {OMP_PID: {"primary": SESSION, "oneshot": False}}
        omp_pids = getattr(WATCH, "_OMP_PIDS", None)
        if isinstance(omp_pids, dict):
            self.addCleanup(omp_pids.clear)
        self.envoy = FakeEnvoy()
        self.state = FakeState()
        self.now = 1793700000.0

    def pane(self) -> dict:
        return {
            "pid": "100", "start_time": "1", "pane_id": "%2177", "session_name": "gt1498-xfam",
            "window_index": "1", "window_name": "bash", "pane_index": "1",
            "pane_current_command": "bash", "pane_pid": str(SHELL_PID),
            "pane_current_path": self.cwd, "pane_title": "\u03c0 PR1498 review", "@agent_oneshot": "",
        }

    def table(self):
        children = {SHELL_PID: ([OMP_PID] if self.omp_running else []) + self.children}
        info = {SHELL_PID: ("bash", OMP_PID if self.omp_running else SHELL_PID)}
        if self.omp_running:
            info[OMP_PID] = ("omp", OMP_PID)
        for child in self.children:
            info[child] = ("sleep", SHELL_PID)
        return children, info

    def options(self) -> argparse.Namespace:
        return argparse.Namespace(
            pane_grace=180.0, move_grace=1800.0, dry_run=False, print_alert=False,
            state=self.root / "state/state.json", write_state=True,
        )

    def period(self, seconds: float, held: dict | None = None) -> None:
        self.now += seconds
        omp_pids = getattr(WATCH, "_OMP_PIDS", None)
        if isinstance(omp_pids, dict):
            omp_pids.clear()
            if self.omp_running:
                omp_pids.update(self.omp_info)
        if held is None:
            held = {SESSION: OMP_PID} if self.omp_running else {}
        WATCH.check_panes(self.options(), self.state, self.envoy, [], held, self.now)

    def transcript(self, *lines: str) -> Path:
        path = WATCH.session_dir_of(self.cwd) / f"2026-11-03T06-56-05-339Z_{SESSION}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"type":"session","id":"' + SESSION + '"}\n' + "".join(lines))
        # What the process scan records while the pane's omp holds this file open.
        WATCH._KNOWN_TRANSCRIPTS[SESSION] = str(path)
        return path

    def omp_stops(self) -> None:
        """omp ran in the pane, then left it at a bare shell for well past the grace."""
        self.period(0)
        self.omp_running = False
        self.period(30)
        self.period(200)
        self.period(400)

    def alerts(self) -> list[str]:
        return [message for _, message in self.envoy.sent if "AGENT PANE AT SHELL" in message
                and "CLEARED" not in message]


class EndedOnPurposeTest(Pane):
    def test_a_session_that_ended_with_a_normal_exit_never_counts_as_at_a_shell(self) -> None:
        """%2177 and %2176: review sessions that ended normally at 07:01Z alerted at 07:04Z."""
        self.transcript(WORK, EXIT)

        self.omp_stops()

        self.assertEqual(self.alerts(), [])
        self.assertTrue(any("normal session_exit" in line for line in self.logs), self.logs)

    def test_a_one_shot_omp_never_counts_as_at_a_shell(self) -> None:
        """`omp -p` processes its prompt and exits by design."""
        self.transcript(WORK)
        self.omp_info = {OMP_PID: {"primary": SESSION, "oneshot": True}}

        self.omp_stops()

        self.assertEqual(self.alerts(), [])
        self.assertTrue(any("one-shot" in line for line in self.logs), self.logs)

    def test_an_omp_that_died_without_a_normal_exit_still_alerts(self) -> None:
        """What the pane check is for: the last entry is work, not an exit."""
        self.transcript(WORK)

        self.omp_stops()

        self.assertEqual(len(self.alerts()), 1, self.envoy.sent)


class MoveTest(Pane):
    def test_a_session_an_agentbox_move_to_owns_never_counts_as_at_a_shell(self) -> None:
        """%1740 and %1741 alerted while `agentbox move --to sami-devbox` moved their sessions."""
        self.transcript(WORK)
        active = self.root / "move-to/active"
        active.mkdir(parents=True)
        (active / SESSION).write_text(f"pid={os.getpid()}\ntarget=sami-devbox\nstart={self.now}\n")

        self.omp_stops()

        self.assertEqual(self.alerts(), [])
        self.assertTrue(any("agentbox move --to sami-devbox" in line for line in self.logs), self.logs)

    def test_a_session_that_moved_away_from_this_machine_never_counts_as_at_a_shell(self) -> None:
        self.transcript(WORK)
        (self.root / "agentbox-moved").mkdir()
        (self.root / "agentbox-moved" / SESSION).write_text(
            f"from={HERE}\nto=sami-devbox\ntime=2026-11-03T05:39:45Z\nsession={SESSION}\n"
        )

        self.omp_stops()

        self.assertEqual(self.alerts(), [])

    def test_a_move_whose_relauncher_died_holds_nothing(self) -> None:
        self.transcript(WORK)
        active = self.root / "move-to/active"
        active.mkdir(parents=True)
        (active / SESSION).write_text("pid=999999999\ntarget=sami-devbox\nstart=0\n")

        self.omp_stops()

        self.assertEqual(len(self.alerts()), 1, self.envoy.sent)

    def test_the_move_is_checked_for_the_panes_top_level_session_not_a_subagent(self) -> None:
        """%1740 was remembered as running 01a19ed8, an advisor subagent of the moved 01a19e3c."""
        self.transcript(WORK)
        active = self.root / "move-to/active"
        active.mkdir(parents=True)
        (active / SESSION).write_text(f"pid={os.getpid()}\ntarget=sami-devbox\nstart={self.now}\n")

        # The subagent last: a pid -> session map built from this keeps the last one it sees.
        self.period(0, held={SESSION: OMP_PID, SUBAGENT: OMP_PID})
        self.omp_running = False
        for seconds in (30, 200, 400):
            self.period(seconds)

        self.assertEqual(self.alerts(), [])


class RepeatTest(Pane):
    def test_a_pane_whose_session_ran_elsewhere_does_not_alert_again(self) -> None:
        """%40: alert, its session runs elsewhere (clear), the session stops, alert again."""
        self.transcript(WORK)
        self.omp_stops()
        self.assertEqual(len(self.alerts()), 1, self.envoy.sent)

        self.period(30, held={SESSION: ELSEWHERE_PID})
        for seconds in (30, 200, 400, 400):
            self.period(seconds, held={})

        self.assertEqual(len(self.alerts()), 1, self.envoy.sent)

    def test_a_session_seen_elsewhere_before_the_first_alert_never_alerts(self) -> None:
        """%40 at 06:48Z-06:54Z: the session ran elsewhere, then the pane alerted anyway."""
        self.transcript(WORK)
        self.period(0)
        self.omp_running = False
        self.period(30, held={SESSION: ELSEWHERE_PID})
        for seconds in (30, 200, 400):
            self.period(seconds, held={})

        self.assertEqual(self.alerts(), [])

    def test_a_pane_that_flips_between_busy_and_idle_alerts_once(self) -> None:
        self.transcript(WORK)
        self.omp_stops()
        self.children = [400]
        self.period(30)
        self.children = []
        for seconds in (30, 200, 400):
            self.period(seconds)

        self.assertEqual(len(self.alerts()), 1, self.envoy.sent)

    def test_omp_running_in_the_pane_again_makes_its_next_stop_news(self) -> None:
        self.transcript(WORK)
        self.omp_stops()
        self.omp_running = True
        self.period(30)
        self.omp_running = False
        for seconds in (30, 200, 400):
            self.period(seconds)

        self.assertEqual(len(self.alerts()), 2, self.envoy.sent)


class TopLevelSessionTest(unittest.TestCase):
    def test_a_top_level_transcript_names_its_session(self) -> None:
        root = WATCH.SESSION_ROOT
        path = root / "-src-machcsl-riscv" / "2026-11-02T21-48-50-976Z_01a19e3c-8da0-7061-a408-0e6343531998.jsonl"
        self.assertEqual(WATCH.top_level_session(str(path)), "01a19e3c-8da0-7061-a408-0e6343531998")

    def test_a_subagent_transcript_names_none(self) -> None:
        root = WATCH.SESSION_ROOT
        path = (root / "-src-machcsl-riscv" / "2026-11-02T21-48-50-916Z_01a19e3c-8d64-7018-88eb-e7313877d45b"
                / "__advisor.drift.jsonl")
        self.assertIsNone(WATCH.top_level_session(str(path)))


NICKMINE = "01a19366-958c-7295-8c2b-171fa219c033"
CONTROL = "01a14ba2-817c-748e-9cb5-d1ad60308a9c"  # the librarian, whose batch it was
ANNOTATOR = "You are a careful annotator. Follow the user's instructions exactly and answer only in the format asked."
# The skill librarian's annotation batch (MethodDesign/llm.py `call`, run by `confirm.py run`),
# exactly as it ran over NICKMINE in pids 1794703, 2244058, 82034 and 1429879, 06:48-07:13Z.
ANNOTATION_ARGV = [
    "omp", "-p", "--model", "anthropic/claude-opus-4-8", "--thinking", "high", "--no-tools",
    "--no-session", "--no-skills", "--no-rules", "--no-context-files", "--no-extensions",
    "--no-title", "--system-prompt", ANNOTATOR, "--mode", "json",
    f"@/mnt/durable/sami/librarian/infra602/MethodDesign/known/runs/opus48/{NICKMINE}-s20/task.md",
]
WRITER, READER = "02102001", "0100000"  # O_WRONLY|O_APPEND|O_CLOEXEC|O_LARGEFILE; O_RDONLY|O_LARGEFILE


class FakeProc:
    """A /proc tree of a few processes, and a session root whose paths read like the real one."""

    def __init__(self, case: unittest.TestCase, root: Path) -> None:
        self.proc = root / "proc"
        self.proc.mkdir(parents=True)
        self.sessions = root / "home/.omp/agent/sessions"
        for name, value in (("PROC_ROOT", self.proc), ("SESSION_ROOT", self.sessions)):
            original = getattr(WATCH, name, None)
            setattr(WATCH, name, value)
            case.addCleanup(setattr, WATCH, name, original)
        # The run's budget and its process table are per run; this is a run of its own.
        deadline = WATCH._RUN.get("deadline") if hasattr(WATCH, "_RUN") else None
        if hasattr(WATCH, "_RUN"):
            WATCH._RUN["deadline"] = float("inf")
            case.addCleanup(WATCH._RUN.__setitem__, "deadline", deadline)
        case.addCleanup(setattr, WATCH, "_PROC_TABLE", None)
        for memo in (WATCH._HEADER_IDS, getattr(WATCH, "_HEADER_CACHE", {}), WATCH._KNOWN_TRANSCRIPTS):
            memo.clear()
            case.addCleanup(memo.clear)

    def transcript(self, session: str) -> Path:
        path = self.sessions / "-src-lane-repo" / f"2026-10-31T19-18-56-140Z_{session}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"type":"title","title":"x"}\n{"type":"session","id":"' + session + '"}\n')
        return path

    def process(self, pid: int, argv: list[str], open_files: tuple = ()) -> None:
        directory = self.proc / str(pid)
        (directory / "fd").mkdir(parents=True)
        (directory / "fdinfo").mkdir()
        (directory / "stat").write_text(f"{pid} ({os.path.basename(argv[0])}) S 1 {pid} {pid} 0 -1 0\n")
        (directory / "cmdline").write_bytes(b"\0".join(word.encode() for word in argv) + b"\0")
        for number, (target, flags) in enumerate(open_files, start=19):
            (directory / "fd" / str(number)).symlink_to(target)
            (directory / "fdinfo" / str(number)).write_text(f"pos:\t0\nflags:\t{flags}\nmnt_id:\t30\n")

    def scan(self, case: unittest.TestCase) -> dict:
        """omp_sessions_by_pid() over this tree, with a control process that must be found: a scan
        that does not read this tree (a script that reads /proc by a literal path) fails here."""
        self.process(4242, ["omp", "--resume", CONTROL])
        held = WATCH.omp_sessions_by_pid()
        case.assertEqual(held.get(CONTROL), 4242, "the scan did not read the fake /proc tree")
        return held


class SessionAttributionTest(unittest.TestCase):
    """A session runs in a process only by that process's resume target, a transcript it writes,
    or that transcript's header."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fake = FakeProc(self, Path(self.tmp.name))

    def test_an_annotation_run_over_a_session_does_not_run_it(self) -> None:
        """NICKMINE was never resumed: four `omp -p --no-session` runs named it in a task path."""
        self.fake.process(1794703, ANNOTATION_ARGV)

        self.assertNotIn(NICKMINE, self.fake.scan(self))

    def test_a_transcript_held_open_only_for_reading_does_not_run_its_session(self) -> None:
        path = self.fake.transcript(NICKMINE)
        self.fake.process(2244058, ["omp"], ((path, READER),))

        self.assertNotIn(NICKMINE, self.fake.scan(self))

    def test_a_written_transcript_and_a_resume_target_run_their_sessions(self) -> None:
        path = self.fake.transcript(NICKMINE)
        self.fake.process(82034, ["omp"], ((path, WRITER),))
        self.fake.process(1429879, ["omp", "--resume", SESSION])
        self.fake.process(1429880, ["omp", "--restore", SUBAGENT])

        held = self.fake.scan(self)

        self.assertEqual(
            (held.get(NICKMINE), held.get(SESSION), held.get(SUBAGENT)), (82034, 1429879, 1429880)
        )


class AnnotationRunPaneTest(Pane):
    def test_an_annotation_run_over_the_panes_session_is_not_the_session_running_elsewhere(self) -> None:
        """%40, 06:48Z and 07:04Z: "its session runs in pid N elsewhere" was an annotation run."""
        self.transcript(WORK)
        self.omp_stops()
        self.assertEqual(len(self.alerts()), 1, self.envoy.sent)
        fake = FakeProc(self, self.root / "scan")
        fake.process(1794703, [*ANNOTATION_ARGV[:-1], ANNOTATION_ARGV[-1].replace(NICKMINE, SESSION)])

        self.period(30, held=fake.scan(self))

        self.assertEqual([m for _, m in self.envoy.sent if "CLEARED" in m], [], self.envoy.sent)
        record = next(iter(self.state.data["panes"].values()))
        self.assertNotIn("left", record)


if __name__ == "__main__":
    unittest.main()
