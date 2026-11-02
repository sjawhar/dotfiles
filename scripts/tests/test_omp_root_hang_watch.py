#!/usr/bin/env python3
"""Tests for omp-root-hang-watch: when it alerts, and that it alerts once per stall."""

import importlib.machinery
import importlib.util
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "omp-root-hang-watch"
LOADER = importlib.machinery.SourceFileLoader("omp_root_hang_watch", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
WATCH = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(WATCH)

SESSION = "00000000-1111-2222-3333-444444444444"
TO = "99999999-8888-7777-6666-555555555555"


def assistant(call_ids, tool="bash"):
    content = [{"type": "toolCall", "id": c, "name": tool, "arguments": {}} for c in call_ids]
    return {"type": "message", "timestamp": "2026-11-02T00:08:09.945Z",
            "message": {"role": "assistant", "content": content}}


def start(call_id, tool="bash"):
    return {"type": "custom", "customType": "tool_execution_start", "timestamp": "2026-11-02T00:08:09.946Z",
            "data": {"toolCallId": call_id, "toolName": tool, "startedAt": "2026-11-02T00:08:09.946Z",
                     "intent": "Queueing #2361 new head"}}


def result(call_id):
    return {"type": "message", "timestamp": "2026-11-02T00:08:10Z",
            "message": {"role": "toolResult", "toolCallId": call_id, "content": []}}


class HangWatchTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "sessions"
        self.transcript = self.root / "-src" / f"2026-10-23T21-15-45-456Z_{SESSION}.jsonl"
        self.transcript.parent.mkdir(parents=True)
        self.subdir = self.transcript.with_suffix("")
        self.subdir.mkdir()
        self.state = Path(tmp.name) / "state.json"
        self.sent: list[tuple[str, str]] = []
        self.send_ok = True

        def fake_send(target, text):
            self.sent.append((target, text))
            return self.send_ok

        for name, value in (("send", fake_send), ("root_pids", lambda session: [4242])):
            original = getattr(WATCH, name)
            setattr(WATCH, name, value)
            self.addCleanup(setattr, WATCH, name, original)

    def write(self, *entries, age_min=45.0):
        with self.transcript.open("a") as handle:
            for entry in entries:
                handle.write(json.dumps(entry) + "\n")
        self.age(self.transcript, age_min)

    @staticmethod
    def age(path, minutes):
        then = time.time() - minutes * 60
        os.utime(path, (then, then))

    def run_watch(self, *extra):
        return WATCH.main(["--session", SESSION, "--to", TO, "--sessions-root", str(self.root),
                           "--state", str(self.state), *extra])

    def test_stale_transcript_with_open_call_alerts_once_per_stall(self):
        """The 2026-11-02 incident: a bash call with no result and a still transcript."""
        self.write(assistant(["a"]), start("a"))
        self.assertEqual(self.run_watch(), 0)
        self.assertEqual(len(self.sent), 1)
        target, text = self.sent[0]
        self.assertEqual(target, TO)
        self.assertIn("ROOT SESSION HANG", text)
        self.assertIn("bash started 2026-11-02T00:08:09.946Z (Queueing #2361 new head)", text)
        self.assertIn("omp process 4242 is alive", text)
        self.assertIn("did not stop or restart anything", text)

        self.assertEqual(self.run_watch(), 0)
        self.assertEqual(len(self.sent), 1, "a second run on the same stall must stay quiet")

        # The transcript moves, then stalls again: that is a new stall, so one new alert.
        self.write({"type": "custom", "customType": "envoy-replay-state", "data": {}}, age_min=40)
        self.assertEqual(self.run_watch(), 0)
        self.assertEqual(len(self.sent), 2)

    def test_fresh_transcript_with_open_call_is_quiet(self):
        self.write(assistant(["a"]), start("a"), age_min=5)
        self.assertEqual(self.run_watch(), 0)
        self.assertEqual(self.sent, [])

    def test_stale_idle_session_is_quiet(self):
        """No live child job: every call has its result, so the session is idle at its prompt."""
        self.write(assistant(["a", "b"]), start("a"), start("b"), result("a"), result("b"))
        self.assertEqual(self.run_watch(), 0)
        self.assertEqual(self.sent, [])

    def test_call_left_open_before_a_restart_does_not_count(self):
        self.write(assistant(["old"]), start("old"), assistant([]))
        self.assertEqual(self.run_watch(), 0)
        self.assertEqual(self.sent, [])

    def test_wait_on_jobs_that_still_write_is_quiet_and_alerts_when_they_stop(self):
        self.write(assistant(["w"], tool="wait"), start("w", tool="wait"))
        child = self.subdir / "Worker.jsonl"
        child.write_text("{}\n")
        self.assertEqual(self.run_watch(), 0)
        self.assertEqual(self.sent, [], "a wait on a subagent that still writes is normal")

        self.age(child, 40)
        self.assertEqual(self.run_watch(), 0)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("wait started", self.sent[0][1])

    def test_dry_run_sends_nothing_and_writes_no_state(self):
        self.write(assistant(["a"]), start("a"))
        self.assertEqual(self.run_watch("--dry-run"), 0)
        self.assertEqual(self.sent, [])
        self.assertFalse(self.state.exists())

    def test_failed_send_is_retried_next_run(self):
        self.write(assistant(["a"]), start("a"))
        self.send_ok = False
        self.assertEqual(self.run_watch(), 1)
        self.assertFalse(self.state.exists())
        self.send_ok = True
        self.assertEqual(self.run_watch(), 0)
        self.assertEqual(len(self.sent), 2)

    def test_missing_transcript_fails(self):
        self.assertEqual(WATCH.main(["--session", "nope", "--sessions-root", str(self.root),
                                     "--state", str(self.state)]), 1)


if __name__ == "__main__":
    unittest.main()
