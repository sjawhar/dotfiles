#!/usr/bin/env python3
"""Regression tests for envoy-role-holder-watch's mover panes and lane-worker owners.

Between 20:14Z and 20:35Z on 2026-10-30 this watch raised three alerts on ossv-agent-open for
panes in tmux sessions named `mv-*`. Those are `ossv-agent move` relocating a worker: the mover
stops the worker here, so its pane shows `[Stopped by SIGTERM]` and the mover's recovery lines,
pushes the tree, and resumes the SAME session id on another box. Telling a reader to restart omp
in that pane would put a second process on a session the mover is about to resume elsewhere.

The second half is the owner. A lane-worker pane has no `lead-*` role holder in its own
directory, so its alert used to fall through to the INFRA lead and the box operator. ossv-worker
records the real owner in `<home>/work/.ossv-workers/<lane>/lead`, which is a dated claim and is
checked against Envoy's live sessions before it is used.

Nothing here reads the real tree: `LANE_WORK_ROOT` and the files under it are redirected into a
temporary directory.
"""

import importlib.machinery
import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "envoy-role-holder-watch"
LOADER = importlib.machinery.SourceFileLoader("envoy_role_holder_watch_moves", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
WATCH = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(WATCH)

LANE_LEAD = "01a182c4-1dc3-77c9-9be8-b2283d6ce54f"
OTHER = "01a17b94-d9dc-708a-8838-cf844e070ff4"


class FakeTree(unittest.TestCase):
    """ossv-worker's records under a temporary <home>/work."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "work"
        self.addCleanup(self.tmp.cleanup)
        for name, value in (
            ("LANE_WORK_ROOT", self.root),
            ("OSSV_WORKER_STATE", self.root / ".ossv-workers"),
            ("COLD_WORKERS_TSV", self.root / "cold/workers.tsv"),
        ):
            original = getattr(WATCH, name)
            setattr(WATCH, name, value)
            self.addCleanup(lambda n=name, o=original: setattr(WATCH, n, o))

    def worker_dir(self, lane: str) -> Path:
        directory = self.root / ".ossv-workers" / lane
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def moved_note(self, worker: str, text: str) -> None:
        directory = self.root / "workers" / worker
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "MOVED.md").write_text(text + "\n")

    def workers_tsv(self, *rows: str) -> None:
        path = self.root / "cold/workers.tsv"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("name\tsession\tmodel\tvm\trole\tlive\n" + "".join(row + "\n" for row in rows))


class MoverPaneTest(FakeTree):
    def test_a_mv_pane_is_recognised_as_a_move_of_its_own_worker(self) -> None:
        move = WATCH.move_in_progress({"session_name": "mv-h-cmd-a1"})
        assert move is not None
        self.assertEqual(move["worker"], "h-cmd-a1")
        self.assertEqual(move["mover"], "mv-h-cmd-a1")

    def test_an_ordinary_pane_is_not_a_move(self) -> None:
        self.assertIsNone(WATCH.move_in_progress({"session_name": "w-h-cmd-a1"}))
        self.assertIsNone(WATCH.move_in_progress({"session_name": "agents3"}))
        self.assertIsNone(WATCH.move_in_progress({"session_name": ""}))

    def test_where_the_worker_went_comes_from_moved_md_first(self) -> None:
        note = (
            "MOVED: worker h-cmd-a7 left ossv-agent-open at 20:11Z; it runs on ossv-agent-open5"
            f" now, same session id {OTHER}, same directory. Do not start it here again."
        )
        self.moved_note("h-cmd-a7", note)
        self.workers_tsv("h-cmd-a7\tx\tclaude\tossv-agent-open\trole\tyes")
        self.assertEqual(WATCH.moved_to("h-cmd-a7"), note)

    def test_the_workers_table_answers_when_there_is_no_moved_md(self) -> None:
        self.workers_tsv("h-cmd-a1\tx\tclaude\tossv-agent-open5\trole\tyes")
        self.assertIn("ossv-agent-open5", WATCH.moved_to("h-cmd-a1"))

    def test_an_unrecorded_worker_claims_nothing(self) -> None:
        self.assertEqual(WATCH.moved_to("h-cmd-a1"), "")

    def test_the_move_alert_never_says_how_to_restart_anything(self) -> None:
        self.moved_note("h-cmd-a1", "MOVED: worker h-cmd-a1 runs on ossv-agent-open5 now.")
        text = WATCH.move_alert_text(
            {
                "pane_id": "%41",
                "where": "mv-h-cmd-a1:0.0",
                "window_name": "move",
                "shell": "bash",
                "opened_at": 0,
                "move": WATCH.move_in_progress({"session_name": "mv-h-cmd-a1"}),
            },
            2400,
        )
        self.assertIn("WORKER MOVE NOT FINISHED", text)
        self.assertIn("mover session mv-h-cmd-a1", text.replace("the mover session ", "mover session "))
        self.assertIn("ossv-agent-open5", text)
        self.assertNotIn("send-keys", text)
        self.assertNotIn("--resume", text)
        self.assertIn("NOT a dead lane", text)

    def test_a_move_with_no_record_says_so_rather_than_guessing(self) -> None:
        text = WATCH.move_alert_text(
            {
                "pane_id": "%41",
                "where": "mv-h-cmd-a1:0.0",
                "window_name": "move",
                "shell": "bash",
                "opened_at": 0,
                "move": WATCH.move_in_progress({"session_name": "mv-h-cmd-a1"}),
            },
            2400,
        )
        self.assertIn("is not recorded yet", text)


class LaneOwnerTest(FakeTree):
    def sessions(self, *ids: str) -> list[dict]:
        return [{"session_id": session_id, "dir": "/elsewhere", "roles": []} for session_id in ids]

    def test_the_lane_lead_from_ossv_workers_is_told_when_it_is_live(self) -> None:
        (self.worker_dir("h-cmd-a1") / "lead").write_text(LANE_LEAD + "\n")
        targets, notes = WATCH.pane_recipients(
            "/home/sami/work/workers/h-cmd-a1", None, self.sessions(LANE_LEAD, WATCH.LANE_BOX_OPERATOR)
        )
        told = [target for target, _ in targets]
        self.assertIn(LANE_LEAD, told)
        self.assertIn(WATCH.LANE_BOX_OPERATOR, told)
        self.assertTrue(any("ossv-worker's record" in note for note in notes))

    def test_a_recorded_lead_that_is_not_live_is_not_told_and_is_named(self) -> None:
        (self.worker_dir("h-cmd-a1") / "lead").write_text(LANE_LEAD + "\n")
        targets, notes = WATCH.pane_recipients(
            "/home/sami/work/workers/h-cmd-a1", None, self.sessions(WATCH.LANE_BOX_OPERATOR)
        )
        self.assertNotIn(LANE_LEAD, [target for target, _ in targets])
        self.assertTrue(any("not live" in note and LANE_LEAD in note for note in notes))

    def test_a_role_holder_in_the_panes_own_directory_still_wins(self) -> None:
        (self.worker_dir("h-cmd-a1") / "lead").write_text(LANE_LEAD + "\n")
        sessions = [
            {"session_id": OTHER, "dir": "/home/sami/work/workers/h-cmd-a1", "roles": ["stream-lead-open"]}
        ]
        targets, notes = WATCH.pane_recipients("/home/sami/work/workers/h-cmd-a1", None, sessions)
        self.assertEqual([target for target, _ in targets], [WATCH.INFRA_LEAD, OTHER])
        self.assertEqual(notes, [])

    def test_a_lane_with_no_record_falls_back_as_before(self) -> None:
        targets, notes = WATCH.pane_recipients(
            "/home/sami/work/workers/h-cmd-a1", None, self.sessions(WATCH.LANE_BOX_OPERATOR)
        )
        self.assertEqual([target for target, _ in targets], [WATCH.INFRA_LEAD, WATCH.LANE_BOX_OPERATOR])

    def test_a_malformed_lead_record_is_ignored(self) -> None:
        (self.worker_dir("h-cmd-a1") / "lead").write_text("not-a-uuid\n")
        targets, _ = WATCH.pane_recipients(
            "/home/sami/work/workers/h-cmd-a1", None, self.sessions(WATCH.LANE_BOX_OPERATOR)
        )
        self.assertEqual([target for target, _ in targets], [WATCH.INFRA_LEAD, WATCH.LANE_BOX_OPERATOR])

    def test_the_lane_name_comes_from_the_panes_directory(self) -> None:
        self.assertEqual(WATCH.lane_of("/home/sami/work/workers/h-cmd-a1/frame/x"), "h-cmd-a1")
        self.assertEqual(WATCH.lane_of("/home/sami/work/cold/workers/c-asan"), "c-asan")
        self.assertEqual(WATCH.lane_of("/home/sami/src/oss-verifier"), "")


class CheckPanesTest(FakeTree):
    """One whole pass of the pane check over a fabricated tmux server."""

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

    def pane(self, session_name: str) -> dict:
        return {
            "pid": "100", "start_time": "1", "pane_id": "%41", "session_name": session_name,
            "window_index": "0", "window_name": "omp", "pane_index": "0",
            "pane_current_command": "bash", "pane_pid": "200",
            "pane_current_path": "/home/sami/work/workers/h-cmd-a1",
            "pane_title": "\u03c0 h-cmd-a1", "@agent_oneshot": "",
        }

    def options(self, **overrides):
        import argparse

        values = {"pane_grace": 180.0, "move_grace": 1800.0, "dry_run": False, "print_alert": False}
        values.update(overrides)
        return argparse.Namespace(**values)

    def one_pass(self, session_name: str, open_for: float, **overrides):
        """Two passes: the first opens the incident, the second is `open_for` seconds later."""
        for name, replacement in (
            ("tmux_panes", lambda: [self.pane(session_name)]),
            ("process_table", lambda: ({200: []}, {200: ("bash", 200)})),
            ("handover_files", lambda session, directory: []),
            ("transcript_by_title", lambda cwd, text, held: None),
            ("resurrect_command", lambda pane, text, limit=60: None),
        ):
            original = getattr(WATCH, name)
            setattr(WATCH, name, replacement)
            self.addCleanup(lambda n=name, o=original: setattr(WATCH, n, o))
        envoy = self.FakeEnvoy()
        state = self.FakeState()
        now = 100000.0
        WATCH.check_panes(self.options(**overrides), state, envoy, [], {}, now - open_for)
        WATCH.check_panes(self.options(**overrides), state, envoy, [], {}, now)
        return envoy

    def test_a_mover_pane_sends_nothing_inside_the_move_grace(self) -> None:
        """The 20:14-20:35Z false alarms: three of these, each telling a reader to resume."""
        self.assertEqual(self.one_pass("mv-h-cmd-a1", 600).sent, [])

    def test_the_same_pane_without_the_mv_prefix_does_alert(self) -> None:
        sent = self.one_pass("w-h-cmd-a1", 600).sent
        self.assertTrue(sent)
        self.assertIn("AGENT PANE AT SHELL", sent[0][1])

    def test_a_move_that_outlives_its_grace_alerts_without_a_restart(self) -> None:
        self.moved_note("h-cmd-a1", "MOVED: worker h-cmd-a1 runs on ossv-agent-open5 now.")
        sent = self.one_pass("mv-h-cmd-a1", 3600).sent
        self.assertTrue(sent)
        text = sent[0][1]
        self.assertIn("WORKER MOVE NOT FINISHED", text)
        self.assertIn("ossv-agent-open5", text)
        self.assertNotIn("send-keys", text)


if __name__ == "__main__":
    unittest.main()
