#!/usr/bin/env python3
"""omp-memory-watch's pane restarter: resume a tmux pane whose omp died, and only that.

A memcg OOM kill takes a session's omp and leaves its tmux pane at a shell; the lead then
resumed each one by hand with `tmux respawn-pane -k`. The watch records what each omp pane runs
and resumes a pane that went idle, unless the session ended on purpose (a normal `session_exit`,
read by envoy-role-holder-watch's normal_exit_at), still runs somewhere, or was resumed less than
an hour ago.

tmux, the pane's processes and the respawn are replaced here; the transcripts are real files in
a temporary session root, read by the real normal_exit_at.
"""

import argparse
import importlib.machinery
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "omp-memory-watch"
LOADER = importlib.machinery.SourceFileLoader("omp_memory_watch_restart", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
WATCH = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(WATCH)
REAL_RESPAWN = WATCH.respawn_pane

SID = "01a19c00-1111-7222-8333-444455556666"
PANE = {"id": "%42", "pid": 4242, "dead": False, "target": "lanes:3.1"}
NORMAL_EXIT = {
    "type": "custom",
    "customType": "session_exit",
    "data": {"reason": "dispose", "kind": "normal", "recordedAt": "2026-11-02T00:50:00.000Z"},
}


class RestartPassTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.cwd = root / "work"
        self.cwd.mkdir()
        self.sessions = root / "sessions"
        (self.sessions / "-work").mkdir(parents=True)
        self.transcript = self.sessions / "-work" / f"2026-11-02T00-00-00-000Z_{SID}.jsonl"
        self.write_transcript({"type": "message", "message": {"role": "assistant"}})
        shim = root / "omp"
        shim.write_text("#!/bin/sh\n")
        shim.chmod(0o755)
        self.lines: list[str] = []
        self.respawned: list[tuple[str, str, str]] = []
        self.states: list[tuple[str, dict]] = []
        self.panes = [dict(PANE)]
        # What tmux answers for a pane just before a respawn; the listed pid unless a test moves it.
        self.pane_now = {PANE["id"]: {"pid": PANE["pid"], "dead": False}}
        for name, value in (
            ("OMP_SHIM", shim),
            ("AGENTBOX", Path("/home/u/.dotfiles/scripts/agentbox")),
            ("SESSION_ROOT", self.sessions),
            ("log", self.lines.append),
            ("tmux_panes", lambda: self.panes),
            ("pane_state", lambda pane, resurrect, *known: self.states.pop(0)),
            ("process_table", lambda: {"children": {}, "names": {}, "starts": {}}),
            ("pane_processes", lambda pid, resurrect: []),
            ("pane_now", lambda pane_id: self.pane_now.get(pane_id)),
            ("respawn_pane", lambda pane, cwd, command: self.respawned.append((pane, cwd, command)) or (True, "")),
        ):
            original = getattr(WATCH, name)
            setattr(WATCH, name, value)
            self.addCleanup(setattr, WATCH, name, original)
        self.state = WATCH.State(root / "state.json")
        self.addCleanup(self.state.close)

    def write_transcript(self, *entries: dict) -> None:
        lines = [json.dumps({"type": "session", "id": SID})] + [json.dumps(entry) for entry in entries]
        self.transcript.write_text("\n".join(lines) + "\n")

    def options(self, **overrides) -> argparse.Namespace:
        values = dict(dry_run=False, write_state=True, restart_dry_run=False, restart_cooldown=3600.0, state=self.state.path)
        values.update(overrides)
        return argparse.Namespace(**values)

    def run_pass(self, state: tuple[str, dict], now: float, omps=(), **overrides) -> str:
        self.states.append(state)
        return WATCH.restart_pass(self.options(**overrides), self.state, list(omps), now)

    def host(self) -> tuple[str, dict]:
        return "host", {"pid": 777, "start": 1, "sid": SID, "cwd": str(self.cwd)}

    def test_a_pane_whose_omp_died_is_resumed_with_the_absolute_shim(self) -> None:
        self.run_pass(self.host(), 1000.0)
        note = self.run_pass(("idle", {}), 1030.0)
        self.assertEqual(len(self.respawned), 1)
        pane, cwd, command = self.respawned[0]
        self.assertEqual((pane, cwd), ("%42", str(self.cwd)))
        # No prompt after the id: omp would record it as a user message. The waker sends one
        # Envoy notice instead.
        self.assertTrue(command.endswith(f"cd {self.cwd} && {WATCH.OMP_SHIM} --resume {SID}"), command)
        self.assertIn(f"move --wake {SID} ", command)
        self.assertIn("resumed 1", note)
        self.assertTrue(any("RESUMED" in line and "did not end normally" in line for line in self.lines))

    def test_a_resume_keeps_the_ceiling_the_session_ran_at(self) -> None:
        """01a19336 ran at a 40 GiB ceiling and came back at shims/omp's 24G default, under the
        34 GiB it had just used (2026-11-02 09:18:48Z)."""
        kind, record = self.host()
        self.run_pass((kind, dict(record, memory_max=42949672960)), 1000.0)
        self.run_pass(("idle", {}), 1030.0)
        _pane, _cwd, command = self.respawned[0]
        self.assertTrue(command.endswith(f"&& OMP_MEMORY_MAX=42949672960 {WATCH.OMP_SHIM} --resume {SID}"), command)

    def test_the_respawn_is_the_leads_command(self) -> None:
        calls = []

        class Done:
            returncode = 0
            stderr = ""

        original = WATCH.subprocess.run
        WATCH.subprocess.run = lambda argv, **kw: calls.append(argv) or Done()
        self.addCleanup(setattr, WATCH.subprocess, "run", original)
        ok, _ = REAL_RESPAWN("%42", "/w", "cd /w && omp --resume x .")
        self.assertTrue(ok)
        self.assertEqual(
            calls,
            [["tmux", "respawn-pane", "-k", "-t", "%42", "-c", "/w", "bash", "-i", "-c",
              "cd /w && omp --resume x .; exec bash -i"]],
        )

    def test_a_second_death_within_the_hour_is_left(self) -> None:
        self.run_pass(self.host(), 1000.0)
        self.run_pass(("idle", {}), 1030.0)
        self.run_pass(("idle", {}), 1060.0)
        self.assertEqual(len(self.respawned), 1)
        self.assertIn("less than 60 min 0 s ago", self.lines[-1])

    def test_a_session_that_ended_normally_is_left(self) -> None:
        self.run_pass(self.host(), 1000.0)
        self.write_transcript(NORMAL_EXIT)
        self.run_pass(("idle", {}), 1030.0)
        self.assertEqual(self.respawned, [])
        self.assertIn("ended normally at 2026-11-02T00:50:00.000Z", self.lines[-1])

    def test_a_normal_exit_followed_by_its_last_turn_is_left(self) -> None:
        """omp can flush the turn in flight after the exit marker: 01a18c23's last assistant
        message landed 2.3 s after its 07:30:34Z exit, and the session was resumed against its
        owner's Ctrl+D."""
        self.run_pass(self.host(), 1000.0)
        exit_entry = dict(NORMAL_EXIT, timestamp="2026-11-02T00:50:00.000Z")
        self.write_transcript(exit_entry, {"type": "message", "message": {"role": "assistant"}, "timestamp": "2026-11-02T00:50:02.300Z"})
        self.run_pass(("idle", {}), 1030.0)
        self.assertEqual(self.respawned, [])
        self.assertIn("ended normally at 2026-11-02T00:50:00.000Z", self.lines[-1])

    def test_a_session_resumed_after_a_normal_exit_is_resumed_again(self) -> None:
        """Entries minutes after an exit are a resumed session's, so its later death is an incident."""
        self.run_pass(self.host(), 1000.0)
        exit_entry = dict(NORMAL_EXIT, timestamp="2026-11-02T00:50:00.000Z")
        self.write_transcript(exit_entry, {"type": "message", "message": {"role": "assistant"}, "timestamp": "2026-11-02T00:55:00.000Z"})
        self.run_pass(("idle", {}), 1030.0)
        self.assertEqual(len(self.respawned), 1)

    def test_a_pane_still_running_omp_is_never_respawned(self) -> None:
        self.run_pass(self.host(), 1000.0)
        note = self.run_pass(self.host(), 1030.0)
        self.assertEqual(self.respawned, [])
        self.assertIn("1 running omp", note)

    def test_a_session_live_in_another_omp_is_left(self) -> None:
        self.run_pass(self.host(), 1000.0)
        original = WATCH.cmdline_of
        WATCH.cmdline_of = lambda pid: f"omp --resume {SID} ."
        self.addCleanup(setattr, WATCH, "cmdline_of", original)
        self.run_pass(("idle", {}), 1030.0, omps=[(os.getpid(), "/x")])
        self.assertEqual(self.respawned, [])
        self.assertIn(f"live in omp pid {os.getpid()}", self.lines[-1])

    def test_a_session_another_pane_runs_is_left(self) -> None:
        """Resumed by hand in a second pane: a second resume would give one transcript two writers."""
        self.panes = [dict(PANE), dict(PANE, id="%43", pid=4343, target="lanes:3.2")]
        self.states += [self.host(), ("host", {"pid": 888, "start": 1, "sid": SID, "cwd": str(self.cwd)})]
        WATCH.restart_pass(self.options(), self.state, [], 1000.0)
        self.states += [("idle", {}), ("host", {"pid": 888, "start": 1, "sid": SID, "cwd": str(self.cwd)})]
        WATCH.restart_pass(self.options(), self.state, [], 1030.0)
        self.assertEqual(self.respawned, [])
        self.assertIn("live in pane %43", " ".join(self.lines))

    def test_a_pass_past_its_deadline_stops_and_resumes_nothing(self) -> None:
        self.run_pass(self.host(), 1000.0)
        note = WATCH.restart_pass(self.options(), self.state, [], 1030.0, deadline=0.0)
        self.assertEqual(self.respawned, [])
        self.assertIn("1 not read this run", note)

    def test_dry_run_names_the_pane_and_respawns_nothing(self) -> None:
        self.run_pass(self.host(), 1000.0)
        note = self.run_pass(("idle", {}), 1030.0, restart_dry_run=True)
        self.assertEqual(self.respawned, [])
        self.assertIn("would resume 1", note)
        self.assertIn("DRY RUN, would resume", self.lines[-1])
        self.assertTrue(self.lines[-1].endswith(f"--resume {SID}"), self.lines[-1])

    def test_a_box_pane_is_resumed_through_agentbox_with_its_spec(self) -> None:
        box = {"pid": 9, "start": 1, "sid": SID, "spec": "--dir /mnt/lane", "box": "agentbox-0123456789ab", "cwd": str(self.cwd)}
        self.run_pass(("box", box), 1000.0)
        self.run_pass(("idle", {}), 1030.0)
        self.assertTrue(
            self.respawned[0][2].endswith(
                f"cd {self.cwd} && /home/u/.dotfiles/scripts/agentbox new --dir /mnt/lane -- omp --resume {SID}"
            ),
            self.respawned[0][2],
        )

    def test_a_pane_respawned_while_the_run_decided_is_left(self) -> None:
        """`agentbox move` respawns the pane into the box launcher seconds after the hang-up; a run
        that decided in between must not kill that launcher with a respawn of its own."""
        self.run_pass(self.host(), 1000.0)
        self.pane_now["%42"] = {"pid": PANE["pid"] + 1, "dead": False}
        self.run_pass(("idle", {}), 1030.0)
        self.assertEqual(self.respawned, [])
        self.assertIn("left: the pane was respawned while this run decided", self.lines[-1])

    def test_a_pane_that_keeps_running_something_else_is_forgotten(self) -> None:
        self.run_pass(self.host(), 1000.0)
        self.run_pass(("busy", {"comm": "vim"}), 1030.0)
        self.run_pass(("busy", {"comm": "vim"}), 1160.0)
        self.run_pass(("idle", {}), 1190.0)
        self.assertEqual(self.respawned, [])
        self.assertIn("has run vim, not omp, for 2 min 10 s; forgotten", " ".join(self.lines))

    def test_a_pane_busy_for_a_moment_after_its_omp_died_is_still_resumed(self) -> None:
        """The shim's bash finishing and `exec bash -i` reading .bashrc show as a child for seconds."""
        self.run_pass(self.host(), 1000.0)
        self.run_pass(("busy", {"comm": "bash"}), 1030.0)
        self.run_pass(("idle", {}), 1060.0)
        self.assertEqual(len(self.respawned), 1)

    def test_a_death_nobody_saw_for_over_an_hour_is_left(self) -> None:
        self.run_pass(self.host(), 1000.0)
        self.run_pass(("idle", {}), 1000.0 + 3601)
        self.assertEqual(self.respawned, [])
        self.assertIn("more than 60 min 0 s ago", self.lines[-1])

    def test_a_pane_never_seen_running_omp_is_not_touched(self) -> None:
        self.run_pass(("idle", {}), 1000.0)
        self.assertEqual(self.respawned, [])
        self.assertEqual(self.lines, [])


class PaneStateTest(unittest.TestCase):
    """How one pane is read: from one process table, scanning open files only within budget."""

    class Resurrect:
        LAUNCH_SPEC = None

        def __init__(self) -> None:
            self.scans = 0
            self.LAUNCH_SPEC = WATCH.sibling("tmux-resurrect-omp").LAUNCH_SPEC

        def open_session(self, pid: int) -> str:
            self.scans += 1
            return SID

        def cmdline(self, pid: int) -> str:
            return {11: "bash -i", 12: "omp", 21: "bash /h/.dotfiles/scripts/agentbox new --dir /w -- omp --resume x ."}.get(pid, "")

        def named_session(self, command: str):
            return "x" if "--resume x" in command else None

    # pane 10: bash 11 -> omp 12 -> python3 13; pane 20: the agentbox launcher 21 -> docker 22.
    TABLE = {
        "children": {10: [11], 11: [12], 12: [13], 20: [21], 21: [22]},
        "names": {11: "bash", 12: "omp", 13: "python3", 21: "bash", 22: "docker"},
        "starts": {12: 5, 21: 6},
    }
    PANE = {"id": "%1", "pid": 10, "dead": False, "target": "s:1.1"}

    def test_the_walk_stops_at_the_omp_and_never_reads_its_tools(self) -> None:
        self.assertEqual(WATCH.pane_processes(10, None, self.TABLE), [(11, "bash"), (12, "omp")])

    def test_a_recent_read_of_the_same_process_is_reused_without_a_scan(self) -> None:
        resurrect = self.Resurrect()
        known = {"kind": "host", "pid": 12, "start": 5, "sid": SID, "resolved": 990.0, "cwd": "/w"}
        kind, seen = WATCH.pane_state(self.PANE, resurrect, known, 1000.0, self.TABLE, {"scans": 10})
        self.assertEqual((kind, seen["sid"], resurrect.scans), ("host", SID, 0))

    def test_no_scan_is_made_over_the_budget(self) -> None:
        resurrect = self.Resurrect()
        kind, seen = WATCH.pane_state(self.PANE, resurrect, None, 1000.0, self.TABLE, {"scans": 0})
        self.assertEqual((kind, seen["sid"], resurrect.scans), ("host", None, 0))
        kind, seen = WATCH.pane_state(self.PANE, resurrect, None, 1000.0, self.TABLE, {"scans": 1})
        self.assertEqual((seen["sid"], resurrect.scans), (SID, 1))

    def test_an_agentbox_launcher_is_a_box_pane_with_its_spec(self) -> None:
        pane = dict(self.PANE, pid=20)
        kind, seen = WATCH.pane_state(pane, self.Resurrect(), None, 1000.0, self.TABLE, {"scans": 0})
        self.assertEqual((kind, seen["spec"], seen["sid"]), ("box", "--dir /w", "x"))



if __name__ == "__main__":
    unittest.main()
