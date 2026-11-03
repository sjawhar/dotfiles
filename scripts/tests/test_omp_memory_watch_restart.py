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
import calendar
import importlib.machinery
import importlib.util
import json
import os
import socket
import tempfile
import time
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


class RestarterCase(unittest.TestCase):
    """A temporary session root, shim and state file, with tmux and the pane's processes replaced."""

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


class RestartPassTest(RestarterCase):
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
                f"cd {self.cwd} && /home/u/.dotfiles/scripts/agentbox new --drop-gone-dirs --dir /mnt/lane -- omp --resume {SID}"
            ),
            self.respawned[0][2],
        )

    def test_a_box_resumes_without_a_dir_its_spec_names_that_is_gone_and_says_so(self) -> None:
        """01a1798a, pane %23: its 10:42Z box spec named its old omp's TMPDIR, removed when that
        omp exited. At 16:23Z the restarter resumed that spec, `agentbox new` refused the gone
        directory, and the pane sat at a shell until a person restarted it by hand at 16:28Z
        with the same dirs minus that one. The spec below is that one, under the fixture root."""
        root = Path(self.tmp.name)
        kept = [root / "nvme" / name for name in ("git-objects", "frac-verify", "tmp/verified-linux")]
        for path in kept:
            path.mkdir(parents=True)
        gone = root / "nvme" / "session-tmp" / "53458-LivaHYZv"
        spec = (f"--dir {self.cwd} --dir {kept[0]} --dir {kept[1]} --dir {gone} --dir {kept[2]}"
                " --dir /var/tmp/baseline-cache --dir /var/tmp/baseline-farm-state")
        box = {"pid": 9, "start": 1, "sid": SID, "spec": spec, "box": "agentbox-676432b23e44", "cwd": str(self.cwd)}
        self.run_pass(("box", box), 1000.0)
        self.run_pass(("idle", {}), 1030.0)
        self.assertEqual(len(self.respawned), 1, self.lines)
        command = self.respawned[0][2]
        self.assertIn(f"/home/u/.dotfiles/scripts/agentbox new --drop-gone-dirs {spec} -- omp --resume {SID}", command)
        resumed = [line for line in self.lines if "RESUMED" in line]
        self.assertEqual(len(resumed), 1, self.lines)
        self.assertIn(f"without --dir {gone}", resumed[0])
        self.assertNotIn(f"--dir {kept[0]}, ", resumed[0])

    def test_a_box_spec_that_drops_gone_dirs_already_is_not_given_it_twice(self) -> None:
        """A box a move started runs `agentbox new --drop-gone-dirs …`, which is the spec read back."""
        box = {"pid": 9, "start": 1, "sid": SID, "spec": f"--drop-gone-dirs --dir {self.cwd}", "box": "agentbox-0123456789ab", "cwd": str(self.cwd)}
        self.run_pass(("box", box), 1000.0)
        self.run_pass(("idle", {}), 1030.0)
        self.assertTrue(self.respawned[0][2].endswith(
            f"agentbox new --drop-gone-dirs --dir {self.cwd} -- omp --resume {SID}"), self.respawned[0][2])

    def test_a_box_whose_working_directory_is_gone_is_left(self) -> None:
        box = {"pid": 9, "start": 1, "sid": SID, "spec": f"--dir {self.cwd}", "box": "agentbox-0123456789ab", "cwd": str(self.cwd)}
        self.run_pass(("box", box), 1000.0)
        self.cwd.rmdir()
        self.run_pass(("idle", {}), 1030.0)
        self.assertEqual(self.respawned, [])
        self.assertIn(f"left: its working directory {self.cwd} is gone", self.lines[-1])

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


# Two real move logs from ~/.cache/omp/agentbox on sami-rocqperf, verbatim. A healthy move:
# 01a14db1 hung up at 12:09:23Z and respawned into its box at 12:09:29Z on 2026-11-02.
HEALTHY_SID = "01a14db1-5568-7640-b39d-4b09c768ce73"
HEALTHY_LOG = """2026-11-02T12:09:23Z move 01a14db1-5568-7640-b39d-4b09c768ce73: pid 48107, pane %3, cwd /home/sami/src, spec --dir /home/sami/src --dir /mnt/nvme/sami/frac-verify --dir /mnt/nvme/sami/session-tmp/48107-STceSGNZ --dir /mnt/nvme/sami/trend
2026-11-02T12:09:23Z move 01a14db1-5568-7640-b39d-4b09c768ce73: tmux server /home/sami/.tmux/sockets/tmux-1000/default, client /home/sami/.mise/installs/github-sjawhar-tmux-builds/3.8-fix5776/tmux
2026-11-02T12:09:23Z move 01a14db1-5568-7640-b39d-4b09c768ce73: omp 48107 hung up; waiting for its teardown
2026-11-02T12:09:29Z move 01a14db1-5568-7640-b39d-4b09c768ce73: omp 48107 exited after 6s
2026-11-02T12:09:29Z move 01a14db1-5568-7640-b39d-4b09c768ce73: pane %3 respawned into agentbox new --dir /home/sami/src --dir /mnt/nvme/sami/frac-verify --dir /mnt/nvme/sami/session-tmp/48107-STceSGNZ --dir /mnt/nvme/sami/trend
2026-11-02T12:10:34Z move 01a14db1-5568-7640-b39d-4b09c768ce73: the resumed session registered on Envoy; wake notice sent
"""
HEALTHY_CWD = "/home/sami/src"
# R3A (01a16c67): the first move failed to fork on 2026-11-01; the second hung it up at 10:20:35Z
# on 2026-11-02, its pane %74 was already gone, and at 10:32:00Z, after 40 tries, it gave up.
DOWN_SID = "01a16c67-c564-7581-82fb-53fbc135de32"
DOWN_LOG = """2026-11-01T17:47:30Z move 01a16c67-c564-7581-82fb-53fbc135de32: pid 51406, pane %74, cwd /home/sami/src/speed-r3-a, spec --dir /home/sami/src/speed-r3-a
2026-11-01T17:47:30Z move 01a16c67-c564-7581-82fb-53fbc135de32: omp 51406 hung up; waiting for its teardown
2026-11-01T17:47:36Z move 01a16c67-c564-7581-82fb-53fbc135de32: omp 51406 exited after 6s
respawn pane failed: fork failed: No such device
2026-11-02T10:20:34Z move 01a16c67-c564-7581-82fb-53fbc135de32: pid 908873, pane %74, cwd /home/sami/src/speed-r3-a, spec --dir /home/sami/src/speed-r3-a --dir /mnt/nvme/sami/frac-verify --dir /mnt/nvme/sami/speed --dir /mnt/nvme/sami/tmp/r3a --dir /mnt/nvme/sami/gate-runs --dir /mnt/nvme/sami/gate-briefs --dir /mnt/nvme/sami/git-objects
2026-11-02T10:20:35Z move 01a16c67-c564-7581-82fb-53fbc135de32: omp 908873 hung up; waiting for its teardown
2026-11-02T10:20:47Z move 01a16c67-c564-7581-82fb-53fbc135de32: omp 908873 exited after 12s
2026-11-02T10:20:48Z move 01a16c67-c564-7581-82fb-53fbc135de32: respawn of pane %74 failed (try 1 of 40): can't find pane: %74
2026-11-02T10:21:03Z move 01a16c67-c564-7581-82fb-53fbc135de32: respawn of pane %74 failed (try 2 of 40): can't find pane: %74
2026-11-02T10:21:18Z move 01a16c67-c564-7581-82fb-53fbc135de32: respawn of pane %74 failed (try 3 of 40): can't find pane: %74
2026-11-02T10:21:33Z move 01a16c67-c564-7581-82fb-53fbc135de32: respawn of pane %74 failed (try 4 of 40): can't find pane: %74
2026-11-02T10:21:48Z move 01a16c67-c564-7581-82fb-53fbc135de32: respawn of pane %74 failed (try 5 of 40): can't find pane: %74
2026-11-02T10:22:04Z move 01a16c67-c564-7581-82fb-53fbc135de32: respawn of pane %74 failed (try 6 of 40): can't find pane: %74
2026-11-02T10:22:28Z move 01a16c67-c564-7581-82fb-53fbc135de32: respawn of pane %74 failed (try 7 of 40): can't find pane: %74
2026-11-02T10:22:45Z move 01a16c67-c564-7581-82fb-53fbc135de32: respawn of pane %74 failed (try 8 of 40): can't find pane: %74
2026-11-02T10:23:00Z move 01a16c67-c564-7581-82fb-53fbc135de32: respawn of pane %74 failed (try 9 of 40): can't find pane: %74
2026-11-02T10:23:18Z move 01a16c67-c564-7581-82fb-53fbc135de32: respawn of pane %74 failed (try 10 of 40): can't find pane: %74
2026-11-02T10:23:34Z move 01a16c67-c564-7581-82fb-53fbc135de32: respawn of pane %74 failed (try 11 of 40): can't find pane: %74
2026-11-02T10:23:55Z move 01a16c67-c564-7581-82fb-53fbc135de32: respawn of pane %74 failed (try 12 of 40): can't find pane: %74
2026-11-02T10:24:10Z move 01a16c67-c564-7581-82fb-53fbc135de32: respawn of pane %74 failed (try 13 of 40): can't find pane: %74
2026-11-02T10:24:31Z move 01a16c67-c564-7581-82fb-53fbc135de32: respawn of pane %74 failed (try 14 of 40): can't find pane: %74
2026-11-02T10:24:46Z move 01a16c67-c564-7581-82fb-53fbc135de32: respawn of pane %74 failed (try 15 of 40): can't find pane: %74
2026-11-02T10:25:02Z move 01a16c67-c564-7581-82fb-53fbc135de32: respawn of pane %74 failed (try 16 of 40): can't find pane: %74
2026-11-02T10:25:17Z move 01a16c67-c564-7581-82fb-53fbc135de32: respawn of pane %74 failed (try 17 of 40): can't find pane: %74
2026-11-02T10:25:32Z move 01a16c67-c564-7581-82fb-53fbc135de32: respawn of pane %74 failed (try 18 of 40): can't find pane: %74
2026-11-02T10:25:55Z move 01a16c67-c564-7581-82fb-53fbc135de32: respawn of pane %74 failed (try 19 of 40): can't find pane: %74
2026-11-02T10:26:11Z move 01a16c67-c564-7581-82fb-53fbc135de32: respawn of pane %74 failed (try 20 of 40): can't find pane: %74
2026-11-02T10:26:26Z move 01a16c67-c564-7581-82fb-53fbc135de32: respawn of pane %74 failed (try 21 of 40): can't find pane: %74
2026-11-02T10:26:45Z move 01a16c67-c564-7581-82fb-53fbc135de32: respawn of pane %74 failed (try 22 of 40): can't find pane: %74
2026-11-02T10:27:03Z move 01a16c67-c564-7581-82fb-53fbc135de32: respawn of pane %74 failed (try 23 of 40): can't find pane: %74
2026-11-02T10:27:18Z move 01a16c67-c564-7581-82fb-53fbc135de32: respawn of pane %74 failed (try 24 of 40): can't find pane: %74
2026-11-02T10:27:33Z move 01a16c67-c564-7581-82fb-53fbc135de32: respawn of pane %74 failed (try 25 of 40): can't find pane: %74
2026-11-02T10:27:54Z move 01a16c67-c564-7581-82fb-53fbc135de32: respawn of pane %74 failed (try 26 of 40): can't find pane: %74
2026-11-02T10:28:09Z move 01a16c67-c564-7581-82fb-53fbc135de32: respawn of pane %74 failed (try 27 of 40): can't find pane: %74
2026-11-02T10:28:24Z move 01a16c67-c564-7581-82fb-53fbc135de32: respawn of pane %74 failed (try 28 of 40): can't find pane: %74
2026-11-02T10:28:42Z move 01a16c67-c564-7581-82fb-53fbc135de32: respawn of pane %74 failed (try 29 of 40): can't find pane: %74
2026-11-02T10:28:58Z move 01a16c67-c564-7581-82fb-53fbc135de32: respawn of pane %74 failed (try 30 of 40): can't find pane: %74
2026-11-02T10:29:15Z move 01a16c67-c564-7581-82fb-53fbc135de32: respawn of pane %74 failed (try 31 of 40): can't find pane: %74
2026-11-02T10:29:33Z move 01a16c67-c564-7581-82fb-53fbc135de32: respawn of pane %74 failed (try 32 of 40): can't find pane: %74
2026-11-02T10:29:48Z move 01a16c67-c564-7581-82fb-53fbc135de32: respawn of pane %74 failed (try 33 of 40): can't find pane: %74
2026-11-02T10:30:06Z move 01a16c67-c564-7581-82fb-53fbc135de32: respawn of pane %74 failed (try 34 of 40): can't find pane: %74
2026-11-02T10:30:21Z move 01a16c67-c564-7581-82fb-53fbc135de32: respawn of pane %74 failed (try 35 of 40): can't find pane: %74
2026-11-02T10:30:36Z move 01a16c67-c564-7581-82fb-53fbc135de32: respawn of pane %74 failed (try 36 of 40): can't find pane: %74
2026-11-02T10:30:54Z move 01a16c67-c564-7581-82fb-53fbc135de32: respawn of pane %74 failed (try 37 of 40): can't find pane: %74
2026-11-02T10:31:18Z move 01a16c67-c564-7581-82fb-53fbc135de32: respawn of pane %74 failed (try 38 of 40): can't find pane: %74
2026-11-02T10:31:33Z move 01a16c67-c564-7581-82fb-53fbc135de32: respawn of pane %74 failed (try 39 of 40): can't find pane: %74
2026-11-02T10:32:00Z move 01a16c67-c564-7581-82fb-53fbc135de32: respawn of pane %74 failed (try 40 of 40): can't find pane: %74
2026-11-02T10:32:00Z move 01a16c67-c564-7581-82fb-53fbc135de32: pane %74 holds no process and would not respawn; the session is DOWN. Bring it back from a host session: tmux respawn-pane -k -t %74 -c /home/sami/src/speed-r3-a bash -i -c '( setsid /home/sami/.dotfiles/scripts/agentbox move --wake 01a16c67-c564-7581-82fb-53fbc135de32 </dev/null >>/home/sami/.cache/omp/agentbox/move-01a16c67-c564-7581-82fb-53fbc135de32.log 2>&1 & ); agentbox new --dir /home/sami/src/speed-r3-a --dir /mnt/nvme/sami/frac-verify --dir /mnt/nvme/sami/speed --dir /mnt/nvme/sami/tmp/r3a --dir /mnt/nvme/sami/gate-runs --dir /mnt/nvme/sami/gate-briefs --dir /mnt/nvme/sami/git-objects -- omp --resume 01a16c67-c564-7581-82fb-53fbc135de32 ; exec bash -i'
"""
DOWN_CWD = "/home/sami/src/speed-r3-a"


def at(stamp: str) -> float:
    return float(calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")))


class MoveLogTest(RestarterCase):
    """`agentbox move` respawns a session itself, 1 to 17 s after its SIGHUP, and the transcript's
    `signal` exit cannot tell that from a machine loss: the move's own log decides
    (dispatch://INFRA-898). When the move gives up, the session is brought back in a new pane."""

    def setUp(self) -> None:
        super().setUp()
        self.moves = Path(self.tmp.name) / "agentbox"
        self.moves.mkdir()
        self.splits: list[tuple[str, str, str, str, str]] = []
        self.split_answer = (True, "%900", "window @7")
        self.told: list[str] = []
        self.logs: dict[Path, list[str]] = {}
        for name, value in (
            ("MOVE_LOG_DIR", self.moves),
            ("open_pane", lambda window, session, name, cwd, command: self.splits.append((window, session, name, cwd, command)) or self.split_answer),
            ("tell_box_health", lambda envoy, text: self.told.append(text) or "01a18385-f8c8-74a9-b6ff-694fb5fe87a7"),
        ):
            original = getattr(WATCH, name)
            setattr(WATCH, name, value)
            self.addCleanup(setattr, WATCH, name, original)

    def session(self, sid: str, cwd: str, log: str, upto: int | None = None) -> Path:
        """The session's transcript and its move log, with the log's real working directory
        replaced by one that exists here (the first `upto` lines only, when given). Each pass
        sees the log as it stood at that pass's time."""
        work = Path(self.tmp.name) / sid[:8]
        work.mkdir(exist_ok=True)
        lines = log.replace(cwd, str(work)).splitlines(keepends=True)
        self.logs[self.moves / f"move-{sid}.log"] = lines[:upto] if upto else lines
        (self.sessions / "-work" / f"2026-11-01T00-00-00-000Z_{sid}.jsonl").write_text(
            json.dumps({"type": "session", "id": sid}) + "\n"
        )
        return work

    def pass_at(self, stamp: str, *states: tuple[str, dict], **overrides) -> str:
        now = at(stamp)
        for path, lines in self.logs.items():
            written, last = [], 0.0
            for line in lines:
                if line[:20].endswith("Z") and line[4] == "-":
                    last = at(line[:20])
                if last <= now:
                    written.append(line)
            path.write_text("".join(written))
        self.states += list(states)
        return WATCH.restart_pass(self.options(**overrides), self.state, [], now, envoy=object())

    def report_of(self, message: str) -> str:
        """A message to the box-health owner is at most 600 characters and ends with the report
        that holds the detail; returns that report."""
        self.assertLessEqual(len(message), 600, message)
        head, _, path = message.rpartition("\nFull report: ")
        self.assertTrue(head and path, message)
        self.assertEqual(Path(path).parent, self.state.path.parent / "reports")
        return Path(path).read_text()

    def test_a_pane_idle_during_a_healthy_move_is_left_to_the_move(self) -> None:
        work = self.session(HEALTHY_SID, HEALTHY_CWD, HEALTHY_LOG)
        self.pass_at("2026-11-02T12:09:10Z", ("host", {"pid": 777, "start": 1, "sid": HEALTHY_SID, "cwd": str(work)}))
        # Between the hang-up (12:09:23Z) and the respawn (12:09:29Z).
        note = self.pass_at("2026-11-02T12:09:26Z", ("idle", {}))
        self.assertEqual(self.respawned, [])
        self.assertIn("wait: agentbox move owns its respawn (2026-11-02T12:09:23Z: omp 48107 hung up", self.lines[-1])
        self.assertIn("1 held by agentbox move", note)
        # The record stays: the next run reads the pane again, and finds the box launcher.
        self.assertIn(PANE["id"], self.state.data["panes"])

    def test_a_box_that_dies_after_the_move_respawned_it_is_resumed(self) -> None:
        """The move's lines before this watch saw the box running are history, so its death a
        minute later is an ordinary one."""
        work = self.session(HEALTHY_SID, HEALTHY_CWD, HEALTHY_LOG)
        box = {"pid": 9, "start": 1, "sid": HEALTHY_SID, "spec": f"--dir {work}", "box": "agentbox-0123456789ab", "cwd": str(work)}
        self.pass_at("2026-11-02T12:09:40Z", ("box", box))
        self.pass_at("2026-11-02T12:10:10Z", ("idle", {}))
        self.assertEqual(len(self.respawned), 1)
        self.assertIn(f"agentbox new --drop-gone-dirs --dir {work} -- omp --resume {HEALTHY_SID}", self.respawned[0][2])

    def test_a_move_silent_for_ten_minutes_no_longer_holds_the_pane(self) -> None:
        work = self.session(HEALTHY_SID, HEALTHY_CWD, HEALTHY_LOG, upto=3)  # killed after the hang-up
        self.pass_at("2026-11-02T12:09:10Z", ("host", {"pid": 777, "start": 1, "sid": HEALTHY_SID, "cwd": str(work)}))
        self.pass_at("2026-11-02T12:14:00Z", ("idle", {}))
        self.assertEqual(self.respawned, [])
        self.pass_at("2026-11-02T12:19:24Z", ("idle", {}))
        self.assertEqual(len(self.respawned), 1)

    def r3a_record(self, work: Path) -> dict:
        return {"pid": 908873, "start": 1, "sid": DOWN_SID, "cwd": str(work)}

    def test_a_pane_gone_during_the_move_is_kept_then_resumed_in_its_window_when_the_move_gives_up(self) -> None:
        work = self.session(DOWN_SID, DOWN_CWD, DOWN_LOG)
        self.panes = [dict(PANE, id="%74", target="agents3:7.4", window="@7")]
        self.pass_at("2026-11-02T10:20:30Z", ("host", self.r3a_record(work)))
        self.panes = []
        self.pass_at("2026-11-02T10:20:48Z")
        self.pass_at("2026-11-02T10:26:00Z")
        self.assertEqual(self.splits, [])
        kept = [line for line in self.lines if "kept: the pane is gone" in line]
        self.assertEqual(len(kept), 1, self.lines)
        note = self.pass_at("2026-11-02T10:32:20Z")
        self.assertEqual(len(self.splits), 1)
        window, tmux_session, title, cwd, command = self.splits[0]
        self.assertEqual((window, tmux_session, title, cwd), ("@7", "agents3", "omp-01a16c67", str(work)))
        # Back as the box the move was making, with its spec, not as the host session it was.
        self.assertTrue(command.endswith(
            f"cd {work} && /home/u/.dotfiles/scripts/agentbox new --drop-gone-dirs --dir {work} --dir /mnt/nvme/sami/frac-verify"
            " --dir /mnt/nvme/sami/speed --dir /mnt/nvme/sami/tmp/r3a --dir /mnt/nvme/sami/gate-runs"
            f" --dir /mnt/nvme/sami/gate-briefs --dir /mnt/nvme/sami/git-objects -- omp --resume {DOWN_SID}"
        ), command)
        self.assertIn("resumed 1", note)
        self.assertIn("RESUMED in new pane %900 (window @7)", self.lines[-1])
        self.assertEqual(len(self.told), 1)
        self.assertIn(f"agentbox move could not bring session {DOWN_SID} back (2026-11-02T10:32:00Z", self.told[0])
        self.assertIn(command, self.report_of(self.told[0]))
        self.assertNotIn("%74", self.state.data["panes"])
        # Once: the next run finds it resumed less than an hour ago.
        self.pass_at("2026-11-02T10:32:50Z")
        self.assertEqual(len(self.splits), 1)

    def test_no_new_pane_tells_the_box_health_owner_the_command(self) -> None:
        work = self.session(DOWN_SID, DOWN_CWD, DOWN_LOG)
        self.panes = [dict(PANE, id="%74", target="agents3:7.4", window="@7")]
        self.pass_at("2026-11-02T10:20:30Z", ("host", self.r3a_record(work)))
        self.panes = []
        self.split_answer = (False, "split-window -t @7: can't find window: @7; new-window -t agents3: can't find session: agents3", "")
        self.pass_at("2026-11-02T10:32:20Z")
        self.assertEqual(len(self.told), 1)
        self.assertIn(f"session {DOWN_SID} is down and was NOT resumed", self.told[0])
        report = self.report_of(self.told[0])
        self.assertIn("no new pane could be opened: split-window -t @7: can't find window: @7; new-window", report)
        self.assertIn(f"Resume it from a host session: tmux new-window -n omp-01a16c67 -c {work} bash -i -c ", report)
        self.assertIn(f"omp --resume {DOWN_SID}; exec bash -i", report)

    def test_dry_run_names_the_new_pane_and_opens_none(self) -> None:
        work = self.session(DOWN_SID, DOWN_CWD, DOWN_LOG)
        self.panes = [dict(PANE, id="%74", target="agents3:7.4", window="@7")]
        self.pass_at("2026-11-02T10:20:30Z", ("host", self.r3a_record(work)))
        self.panes = []
        self.pass_at("2026-11-02T10:32:20Z", restart_dry_run=True)
        self.assertEqual((self.splits, self.told), ([], []))
        self.assertIn("DRY RUN, would resume in a new pane of window @7 (else a new window omp-01a16c67 of tmux session agents3)", self.lines[-1])

    def test_a_pane_still_there_after_the_move_gave_up_is_respawned_into_the_box(self) -> None:
        """2026-11-01T17:47Z: `fork failed: No such device` left the pane with no process."""
        work = self.session(DOWN_SID, DOWN_CWD, DOWN_LOG)
        self.pass_at("2026-11-01T17:47:00Z", ("host", self.r3a_record(work)))
        self.pass_at("2026-11-01T17:48:00Z", ("idle", {}))
        self.assertEqual(len(self.respawned), 1)
        self.assertIn(f"agentbox new --drop-gone-dirs --dir {work} -- omp --resume {DOWN_SID}", self.respawned[0][2])
        self.assertIn("agentbox move gave up (2026-11-01T17:47:36Z: respawn pane failed", self.lines[-1])
        self.assertEqual(len(self.told), 1)
        self.assertIn(self.respawned[0][2], self.report_of(self.told[0]))

    def test_a_session_resumed_by_hand_after_the_move_gave_up_is_its_own_again(self) -> None:
        """R3A came back by hand on the host in pane %1641: its next death is not the move's."""
        work = self.session(DOWN_SID, DOWN_CWD, DOWN_LOG)
        self.pass_at("2026-11-02T14:50:00Z", ("host", self.r3a_record(work)))
        self.pass_at("2026-11-02T14:50:30Z", ("idle", {}))
        self.assertEqual(len(self.respawned), 1)
        self.assertTrue(self.respawned[0][2].endswith(f"--resume {DOWN_SID}"), self.respawned[0][2])
        self.assertNotIn("agentbox new", self.respawned[0][2].split(";", 1)[1])
        self.assertEqual(self.told, [])

    def test_a_pane_closed_with_no_move_is_forgotten(self) -> None:
        self.pass_at("2026-11-02T12:55:00Z", self.host())
        self.panes = []
        self.pass_at("2026-11-02T12:55:30Z")
        self.assertEqual((self.splits, self.told), ([], []))
        self.assertIn("forgotten, the pane is gone", self.lines[-1])
        self.assertEqual(self.state.data["panes"], {})


class MoveToTest(RestarterCase):
    """`agentbox move --to <machine>` moves a session to ANOTHER machine. On 2026-11-03 at
    04:10:57Z this watch resumed 01a19e3c on sami-rocqperf 6 s after its move to sami-devbox hung
    it up: only the same-machine move log was read, so the move in flight looked like a crash."""

    DEAD_PID = 2147483646  # above any pid_max: never a live process

    def setUp(self) -> None:
        super().setUp()
        root = Path(self.tmp.name)
        self.active = root / "agentbox" / "move-to" / "active"
        self.active.mkdir(parents=True)
        self.index = root / "agentbox-moved"
        for name, value in (("MOVE_LOG_DIR", root / "agentbox"), ("MOVED_INDEX", self.index)):
            original = getattr(WATCH, name, None)
            setattr(WATCH, name, value)
            self.addCleanup(setattr, WATCH, name, original)

    def moving(self, pid: int) -> None:
        (self.active / SID).write_text(f"pid={pid}\ntarget=sami-devbox\nstart=1000\n")

    def marker(self, path: Path, source: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"from={source}\nto=sami-devbox\ntime=2026-11-03T04:11:30Z\nsession={SID}\n")

    def here(self) -> str:
        return socket.gethostname().split(".")[0]

    def test_a_move_to_another_machine_in_flight_owns_the_pane(self) -> None:
        self.run_pass(self.host(), 1000.0)
        self.moving(os.getpid())
        note = self.run_pass(("idle", {}), 1006.0)
        self.assertEqual(self.respawned, [])
        self.assertIn("wait: agentbox move --to sami-devbox owns it", self.lines[-1])
        self.assertIn("1 held by agentbox move", note)
        self.assertIn(PANE["id"], self.state.data["panes"])

    def test_a_pane_gone_during_a_move_to_another_machine_is_kept(self) -> None:
        self.run_pass(self.host(), 1000.0)
        self.moving(os.getpid())
        self.panes = []
        self.run_pass(("idle", {}), 1006.0)
        self.assertIn("kept: the pane is gone, and agentbox move --to sami-devbox owns it", self.lines[-1])
        self.assertIn(PANE["id"], self.state.data["panes"])

    def test_a_move_whose_relauncher_is_dead_owns_nothing(self) -> None:
        self.run_pass(self.host(), 1000.0)
        self.moving(self.DEAD_PID)
        self.run_pass(("idle", {}), 1030.0)
        self.assertEqual(len(self.respawned), 1)

    def test_a_session_moved_away_by_its_index_marker_is_left(self) -> None:
        self.run_pass(self.host(), 1000.0)
        self.marker(self.index / SID, self.here())
        self.run_pass(("idle", {}), 1030.0)
        self.assertEqual(self.respawned, [])
        self.assertIn("left: it moved to sami-devbox at 2026-11-03T04:11:30Z", self.lines[-1])
        self.assertNotIn(PANE["id"], self.state.data["panes"])

    def test_a_session_moved_away_by_its_transcript_marker_is_left(self) -> None:
        self.run_pass(self.host(), 1000.0)
        self.marker(self.transcript.with_suffix("") / "agentbox-moved", self.here())
        self.run_pass(("idle", {}), 1030.0)
        self.assertEqual(self.respawned, [])
        self.assertIn("left: it moved to sami-devbox", self.lines[-1])

    def test_a_marker_from_another_machine_is_inert_here(self) -> None:
        """agent-session's rule: a marker counts only on the machine it names as `from=`, so the
        target can still resume the session it now runs."""
        self.run_pass(self.host(), 1000.0)
        self.marker(self.index / SID, "some-other-machine")
        self.marker(self.transcript.with_suffix("") / "agentbox-moved", "some-other-machine")
        self.run_pass(("idle", {}), 1030.0)
        self.assertEqual(len(self.respawned), 1)


class OpenPaneTest(unittest.TestCase):
    """Where a session whose pane is gone gets a new one: its window, else a new window."""

    def run_tmux(self, answers: dict[str, tuple[int, str]]) -> tuple[tuple[bool, str, str], list[list[str]]]:
        calls: list[list[str]] = []

        class Done:
            def __init__(self, code: int, text: str) -> None:
                self.returncode, self.stdout, self.stderr = code, text if code == 0 else "", text if code else ""

        def fake(argv, **kw):
            calls.append(argv)
            return Done(*answers.get(argv[1], (0, "")))

        original = WATCH.subprocess.run
        WATCH.subprocess.run = fake
        self.addCleanup(setattr, WATCH.subprocess, "run", original)
        return WATCH.open_pane("@1570", "mvdrill", "omp-01a19cd3", "/w", "cd /w && omp --resume x"), calls

    def test_a_window_with_room_gets_the_pane(self) -> None:
        result, calls = self.run_tmux({"split-window": (0, "%900\n")})
        self.assertEqual(result, (True, "%900", "window @1570"))
        self.assertEqual(calls, [["tmux", "split-window", "-d", "-P", "-F", "#{pane_id}", "-t", "@1570", "-c", "/w",
                                  "bash", "-i", "-c", "cd /w && omp --resume x; exec bash -i"]])

    def test_a_full_window_is_tiled_then_a_new_window_is_opened(self) -> None:
        """tmux 3.8 says `no space for a new pane`: the drill window refused at 15:29:49Z on 2026-11-02."""
        result, calls = self.run_tmux({"split-window": (1, "no space for a new pane"), "new-window": (0, "%901\n")})
        self.assertEqual(result, (True, "%901", "new window omp-01a19cd3 of tmux session mvdrill"))
        self.assertEqual([c[1] for c in calls], ["split-window", "select-layout", "split-window", "new-window"])
        self.assertEqual(calls[1], ["tmux", "select-layout", "-t", "@1570", "tiled"])
        self.assertEqual(calls[3][:10], ["tmux", "new-window", "-d", "-P", "-F", "#{pane_id}", "-t", "mvdrill:", "-n", "omp-01a19cd3"])

    def test_no_window_and_no_session_says_both_errors(self) -> None:
        result, _ = self.run_tmux({"split-window": (1, "can't find window: @1570"), "new-window": (1, "can't find session: mvdrill")})
        self.assertEqual(result, (False, "split-window -t @1570: can't find window: @1570; new-window -t mvdrill: can't find session: mvdrill", ""))


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
