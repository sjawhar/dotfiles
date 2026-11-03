#!/usr/bin/env python3
"""Which session omp-memory-watch names, and tells, for a lane resumed from a subagent's transcript.

2026-11-03 23:07Z: a near-limit notice named session 01a15237 on sami-devbox. 01a15237 runs on
sami-rocqperf at 1.2 of 24 GiB; the scope (run-r107194...) held a lane resumed on devbox from
`.../<stamp>Z_01a15237-.../<stamp>Z_<own id>.jsonl`, a transcript that was 01a15237's subagent.
Every id in the command line was read and the smallest taken: the parent's. The session id is the
id in the last path component of --resume (the transcript's own name). When that session has no
live Envoy registration, the session whose directory holds the transcript is told, with the lane's
pid, tmux window and cwd, so it knows the notice is about a lane and not itself.
"""

import importlib.machinery
import importlib.util
import socket
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
LOADER = importlib.machinery.SourceFileLoader("omp_memory_watch_notify_tests", str(HERE / "test_omp_memory_watch_notify.py"))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
NOTIFY = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(NOTIFY)
WATCH = NOTIFY.WATCH

PARENT = "01a15237-75c3-7321-befb-e6e1cc57a948"
LANE = "01a17dd1-0000-7000-8000-000000000001"
NESTED = (f"/home/sami/.omp/agent/sessions/-src/2026-10-19T03-32-08-771Z_{PARENT}/"
          f"2026-10-25T02-25-48-934Z_{LANE}.jsonl")


class ResumeIdTest(unittest.TestCase):
    def test_the_id_is_the_transcript_files_own_name(self) -> None:
        self.assertEqual(WATCH.resume_session(["omp", "--resume", NESTED]), LANE)
        self.assertEqual(WATCH.resume_session(["omp", f"--resume={NESTED}"]), LANE)
        self.assertEqual(WATCH.resume_session(["omp", "--resume", LANE, "."]), LANE)
        self.assertEqual(WATCH.resume_session(["omp", "--resume", f"/x/2026Z_{PARENT}/ArityLane.jsonl"]), "")

    def test_session_of_never_takes_another_id_on_the_command_line(self) -> None:
        original = WATCH.cmdline_of
        self.addCleanup(setattr, WATCH, "cmdline_of", original)
        gone = 2 ** 22 + 12345  # no such pid: no descriptors to read
        WATCH.cmdline_of = lambda pid: f"/x/bin/omp --resume {NESTED}"
        self.assertEqual(WATCH.session_of(gone), LANE)
        WATCH.cmdline_of = lambda pid: f"/x/bin/omp -p review dispatch://X/ask/{PARENT} now"
        self.assertEqual(WATCH.session_of(gone), "", "an id inside a prompt names no session")

    def test_identity_names_the_transcripts_directory_owner(self) -> None:
        original = WATCH.cmdline_of
        self.addCleanup(setattr, WATCH, "cmdline_of", original)
        WATCH.cmdline_of = lambda pid: f"/x/bin/omp --resume {NESTED}"
        found = WATCH.omp_identity(2 ** 22 + 12345)
        self.assertEqual(found["transcript_parent"], PARENT)
        self.assertEqual(found["omp_pid"], 2 ** 22 + 12345)


class LaneEnvoy(NOTIFY.FakeEnvoy):
    """Envoy lists the parent as live on this box, and not the lane."""

    live = (PARENT,)

    def request(self, method: str, path: str, body=None):
        return 200, [{"session_id": s, "host": socket.gethostname()} for s in self.live]


class LaneRoutingTest(NOTIFY.NotifyCase):
    SCOPE = "run-r107194aa.scope"

    def setUp(self) -> None:
        super().setUp()
        self.sessions[1] = LANE
        for name, replacement in (
            ("Envoy", LaneEnvoy),
            ("omp_identity", lambda pid: {"omp_pid": pid, "cwd": "/home/sami/src/lane-wt",
                                          "window": "main:3 (lane)", "transcript_parent": PARENT}),
        ):
            original = getattr(WATCH, name)
            setattr(WATCH, name, replacement)
            self.addCleanup(lambda n=name, o=original: setattr(WATCH, n, o))
        LaneEnvoy.live = (PARENT,)

    def run_pass(self, after: float = 30.0, **overrides) -> None:
        self.now += after
        WATCH.LIVE_SESSIONS.clear()
        WATCH.LIVE_READ = False
        WATCH.run_once(NOTIFY.options(self.state, **overrides))

    def near(self) -> None:
        size = 18 * NOTIFY.GIB
        self.scope(self.SCOPE, 1, anon=size, current=size)
        self.run_pass()
        self.run_pass(after=60)

    def test_an_unregistered_lanes_near_alert_goes_to_its_parent_marked_as_a_lane(self) -> None:
        self.near()
        self.assertEqual(self.to(LANE), [])
        told = self.to(PARENT)
        self.assertEqual(len(told), 1)
        text = told[0]
        self.assertTrue(text.startswith("A LANE OF YOURS, NOT YOU"), text)
        for needle in ("omp pid 1", "tmux main:3 (lane)", "cwd /home/sami/src/lane-wt", f"session {LANE}"):
            self.assertIn(needle, text)
        report = self.assert_short(text)
        self.assertIn(f"transcript is in session {PARENT}'s directory", report)
        self.assertNotIn("restart yourself with", text)

    def test_a_registered_lane_hears_its_own_alert_with_its_pid_and_cwd(self) -> None:
        LaneEnvoy.live = (PARENT, LANE)
        self.near()
        self.assertEqual(self.to(PARENT), [])
        told = self.to(LANE)
        self.assertEqual(len(told), 1)
        self.assertIn("omp pid 1, tmux main:3 (lane), cwd /home/sami/src/lane-wt", told[0])
        self.assertNotIn("A LANE OF YOURS", told[0])

    def test_the_clear_goes_to_the_parent_that_heard_the_alert(self) -> None:
        self.near()
        self.scope(self.SCOPE, 1, anon=2 * NOTIFY.GIB, current=2 * NOTIFY.GIB)
        self.run_pass()
        self.assertEqual(len([t for t in self.to(PARENT) if "CLEARED" in t]), 1)

    def test_a_lanes_wall_touch_goes_to_its_parent(self) -> None:
        self.scope(self.SCOPE, 1)
        self.run_pass()
        self.scope(self.SCOPE, 1, events_max=1)
        self.run_pass()
        told = self.to(PARENT)
        self.assertEqual(len(told), 1, self.everything())
        self.assertTrue(told[0].startswith("A LANE OF YOURS, NOT YOU"), told[0])
        self.assertIn("cwd /home/sami/src/lane-wt", told[0])
        self.assertEqual(self.to(LANE), [])


if __name__ == "__main__":
    unittest.main()
