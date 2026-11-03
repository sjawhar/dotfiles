#!/usr/bin/env python3
"""Regression tests for envoy-role-holder-watch's run cost, journal volume and message size.

Measured 2026-11-01..03 on sami-rocqperf (dispatch://INFRA-672, review §3 "Box watchers"):

- 2,078 of ~3,976 runs were killed at the then 25 s TimeoutStartSec; at 90 s, 35 of 545 runs in
  six hours still were. The dominant steps: the header reads of the ~500 transcripts the omp
  processes hold open (38 s of a 39 s process scan at load 268, up to 5 s for ONE read), and the
  3 s session-root walk plus the 1 s directory walk, paid every period for one holder that had
  exited and whose transcript the session sweep had taken -- 80 s on 2026-11-03T00:18Z.
- 150,188 journal lines in 40 h: an "ok" line per role and a "holding" line per pane, every 30 s.
- A pane alert of 2,404 characters.

Pinned here: a run stops at its budget and saves what it did; the next run starts with what the
last one did not reach; header reads are kept across runs; an exited holder is not searched
for again; the journal says only what changed, plus one summary line a run; every message is at
most 600 characters and names a report file that holds the whole text.

Nothing here touches the real tree, the real Envoy or the real state file. Set RHW_SCRIPT to run
these against another copy of the script (the before-change run).
"""

import argparse
import importlib.machinery
import importlib.util
import json
import os
import re
import socket
import tempfile
import time
import unittest
from pathlib import Path


SCRIPT = Path(os.environ.get("RHW_SCRIPT") or Path(__file__).resolve().parents[1] / "envoy-role-holder-watch")
LOADER = importlib.machinery.SourceFileLoader("envoy_role_holder_watch_tuning", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
WATCH = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(WATCH)

HOST = socket.gethostname()
HOLDER = "01a19366-958c-7295-8c2b-171fa219c033"
OTHER = "01a18f00-e348-7617-8e7a-a1d0b774b455"
THIRD = "01a170d9-5e66-7491-b0d2-fa6a2e55c14e"
LIMIT = 600
EXIT_LINE = (
    '{"type":"custom","customType":"session_exit","data":{"reason":"dispose","kind":"normal",'
    '"recordedAt":"2026-11-02T05:41:32.174Z"},"id":"da4a6644",'
    '"timestamp":"2026-11-02T05:41:32.174Z"}\n'
)


class RunEnvoy:
    """Envoy with a configurable session list, 404 for every registry read; records sends."""

    url = "http://127.0.0.1:9020"
    sessions_list: list = []
    sent: list = []
    published: list = []

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
        RunEnvoy.published.append((topic, message))
        return True


class Box(unittest.TestCase):
    """A temporary session root, sweep record and state file; Envoy, /proc and tmux faked."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.dir = self.root / "src/lane"
        self.dir.mkdir(parents=True)
        (self.root / "agent/sessions").mkdir(parents=True)
        self.sweep = self.root / "omp-session-sql"
        self.sweep.mkdir()
        self.logs: list[str] = []
        self.held: dict = {}
        self.panes: list = []
        self.table: tuple = ({}, {})
        RunEnvoy.sessions_list, RunEnvoy.sent, RunEnvoy.published = [], [], []
        for name, value in (
            ("SESSION_ROOT", self.root / "agent/sessions"), ("SWEEP_STATE", self.sweep),
            ("_TRANSCRIPT_INDEX", None), ("_SWEEP_INDEX", None),
            ("Envoy", RunEnvoy), ("log", self.logs.append),
            ("omp_sessions_by_pid", lambda: dict(self.held)),
            ("transcript_holders", lambda path: []),
            ("handover_files", lambda session, directory: []),
            ("tmux_panes", lambda: list(self.panes)),
            ("process_table", lambda: self.table),
            ("transcript_by_title", lambda cwd, text, held: None),
            ("resurrect_command", lambda pane, text, limit=60: None),
        ):
            original = getattr(WATCH, name, None)
            setattr(WATCH, name, value)
            self.addCleanup(setattr, WATCH, name, original)
        for memo in (
            WATCH._KNOWN_TRANSCRIPTS, getattr(WATCH, "_SWEPT", {}), WATCH._HEADER_IDS,
            getattr(WATCH, "_HEADER_CACHE", {}), getattr(WATCH, "_HEADER_USED", {}),
        ):
            # The header memos are keyed by (device, inode) and by path: one temp file can reuse
            # the inode of another test's, so a run of the whole file must start them empty.
            memo.clear()
            self.addCleanup(memo.clear)
        self.state = self.root / "state/state.json"
        self.state.parent.mkdir()
        self.now = time.time()

    def options(self, **overrides) -> argparse.Namespace:
        values = dict(
            state=self.state, dry_run=False, write_state=True, print_alert=False,
            http_timeout=1.0, grace=60.0, write_grace=120.0, dir_grace=300.0, escalate=600.0,
            pane_grace=180.0, move_grace=1800.0, budget=60.0,
        )
        values.update(overrides)
        return argparse.Namespace(**values)

    def write_state(self, roles: dict, incidents: dict | None = None, **extra) -> None:
        self.state.write_text(json.dumps({"roles": roles, "incidents": incidents or {}, **extra}))

    def role(self, holder: str = HOLDER, **extra) -> dict:
        return {"holder": holder, "host": HOST, "dir": str(self.dir), "title": "a lane", **extra}

    def live_session(self, holder: str = HOLDER, *roles: str) -> dict:
        """The session as Envoy lists it: on this host, heartbeating now."""
        return {
            "session_id": holder, "host": HOST, "dir": str(self.dir), "roles": list(roles),
            "topics": [], "last_seen": self.now * 1000,
        }

    def period(self, **overrides) -> list[str]:
        self.logs.clear()
        WATCH._TRANSCRIPT_INDEX = None
        self.assertEqual(WATCH.run_once(self.options(**overrides)), 0)
        return list(self.logs)

    def data(self) -> dict:
        return json.loads(self.state.read_text())

    def assert_short(self, message: str) -> Path:
        """At most 600 characters, ending in `Full report: <path>`, and the file holds more."""
        self.assertLessEqual(len(message), LIMIT, message)
        found = re.search(r"Full report: (\S+)$", message)
        self.assertIsNotNone(found, message)
        report = Path(found.group(1))
        self.assertEqual(report.parent, self.state.parent / "reports")
        self.assertRegex(report.name, r"^\d{8}T\d{6}Z-[A-Za-z0-9._-]+\.txt$")
        self.assertTrue(report.is_file(), report)
        return report

    def pane(self, title: str = "π fix the long lane", **extra) -> dict:
        pane = {
            "pid": "100", "start_time": "1", "pane_id": "%41", "session_name": "agents",
            "window_index": "4", "window_name": "lane", "pane_index": "2",
            "pane_current_command": "bash", "pane_pid": "200",
            "pane_current_path": "/home/sami/src/a-rather-long-working-directory/for/a/lane/" + "x" * 80,
            "pane_title": title, "@agent_oneshot": "",
        }
        pane.update(extra)
        return pane


class QuietJournalTest(Box):
    """150,188 lines in 40 h: an ok line per role and a holding line per pane every 30 s."""

    def healthy(self, pid: int = 4242) -> None:
        """One role whose holder runs here and is live on Envoy: the box's ordinary state."""
        self.write_state({"lead-lane": self.role()})
        RunEnvoy.sessions_list = [self.live_session(HOLDER, "lead-lane")]
        self.held = {HOLDER: pid}

    def test_an_unchanged_role_logs_nothing_on_the_next_period(self) -> None:
        self.healthy()

        first = self.period()
        second = self.period()

        self.assertEqual(len([line for line in first if line.startswith("lead-lane")]), 1, first)
        self.assertEqual([line for line in second if "lead-lane" in line], [], second)

    def test_a_role_whose_holder_moves_to_a_new_pid_logs_once(self) -> None:
        self.healthy()
        self.period()
        self.held = {HOLDER: 4343}

        lines = [line for line in self.period() if line.startswith("lead-lane")]

        self.assertEqual(len(lines), 1, lines)
        self.assertIn("4343", lines[0])

    def test_an_unchanged_pane_logs_nothing_on_the_next_period(self) -> None:
        self.write_state({})
        self.panes = [self.pane()]
        # bash, and something else holds the terminal: an agent box's pane.
        self.table = ({200: [201]}, {200: ("bash", 201), 201: ("docker", 201)})

        first = self.period()
        second = self.period()

        self.assertEqual(len([line for line in first if line.startswith("pane %41")]), 1, first)
        self.assertEqual([line for line in second if "%41" in line], [], second)

    def test_each_period_writes_one_summary_line_that_carries_the_suppression(self) -> None:
        self.healthy()
        self.period()

        second = self.period()

        self.assertEqual(len(second), 1, second)
        self.assertIn("checked 1 local role(s)", second[0])
        self.assertIn("SUPPRESSION", second[0])


class MessageTest(Box):
    """Every message at most 600 characters and naming a report file with the whole text."""

    def unseen(self, **incident) -> None:
        self.write_state(
            {"lead-lane": self.role(topics=["notifications.team.lane.>"])},
            {f"lead-lane|{HOLDER}": {"opened_at": self.now - 700, "dir": str(self.dir), **incident}},
        )
        RunEnvoy.sessions_list = [
            {"session_id": OTHER, "dir": str(self.dir), "roles": ["stream-lead-lane"], "host": HOST,
             "topics": ["notifications.team.lane.>"]},
        ]

    def test_a_first_alert_is_short_and_its_report_holds_the_whole_alert(self) -> None:
        self.unseen()

        self.period()

        self.assertTrue(RunEnvoy.sent)
        for _, message in RunEnvoy.sent:
            report = self.assert_short(message)
            self.assertIn("lead-lane", message[:200])
            self.assertIn(HOLDER, message[:200])
            self.assertIn("ESTABLISHED: I cannot see this session", report.read_text())
            self.assertIn("THE HOLDER MAY NOT BE AN omp SESSION AT ALL", report.read_text())
        self.assertTrue(RunEnvoy.published)
        for _, message in RunEnvoy.published:
            self.assert_short(message)

    def test_a_repeat_with_no_new_fact_sends_nothing(self) -> None:
        self.unseen(alerted_at=self.now - 100, told=[OTHER])

        self.period()
        self.period()

        self.assertEqual(RunEnvoy.sent, [])

    def test_the_one_repeat_is_short(self) -> None:
        self.unseen(alerted_at=self.now - 650, told=[OTHER])

        self.period()

        self.assertEqual(len(RunEnvoy.sent), 1)
        report = self.assert_short(RunEnvoy.sent[0][1])
        self.assertIn("REPEAT", RunEnvoy.sent[0][1][:200])
        self.assertIn("THIS IS A REPEAT, AND THE ONLY ONE", report.read_text())

    def test_a_clear_is_short(self) -> None:
        self.unseen(alerted_at=self.now - 100, told=[OTHER])
        self.held = {HOLDER: 4242}

        self.period()

        self.assertEqual([target for target, _ in RunEnvoy.sent], [OTHER])
        self.assert_short(RunEnvoy.sent[0][1])
        self.assertIn("CLEARED", RunEnvoy.sent[0][1][:200])

    def test_a_quieting_clear_is_short(self) -> None:
        self.unseen(alerted_at=self.now - 100, told=[OTHER])
        (self.dir / "ps.log").write_text("x\n")

        self.period()

        self.assertEqual(len(RunEnvoy.sent), 1)
        report = self.assert_short(RunEnvoy.sent[0][1])
        self.assertIn("NOT ALERTING AGAIN", RunEnvoy.sent[0][1][:200])
        self.assertIn("it does not say the lane is working", report.read_text())

    def test_an_exit_clear_is_short(self) -> None:
        self.unseen(alerted_at=self.now - 100, told=[OTHER])
        path = WATCH.session_dir_of(str(self.dir)) / f"2026-10-31T19-18-56-140Z_{HOLDER}.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text('{"type":"session","id":"' + HOLDER + '"}\n' + EXIT_LINE)
        os.utime(path, (self.now - 900, self.now - 900))

        self.period()

        self.assertEqual(len(RunEnvoy.sent), 1)
        self.assert_short(RunEnvoy.sent[0][1])
        self.assertIn("EXITED NORMALLY", RunEnvoy.sent[0][1][:200])

    def test_a_suppression_clear_is_short(self) -> None:
        role = "node-envprobe-d10302249-2"
        rule = WATCH.SUPPRESSIONS[0]
        if time.time() >= WATCH.epoch_of(rule["until"]):
            self.skipTest("the suppression has expired")
        self.write_state(
            {role: self.role()},
            {f"{role}|{HOLDER}": {"opened_at": self.now - 700, "alerted_at": self.now - 100, "told": [OTHER]}},
        )

        self.period()

        self.assertEqual(len(RunEnvoy.sent), 1)
        self.assert_short(RunEnvoy.sent[0][1])

    def pane_pass(self, **pane) -> None:
        self.write_state({})
        self.panes = [self.pane(**pane)]
        self.table = ({200: []}, {200: ("bash", 200)})
        self.period()
        data = self.data()
        for incident in data["pane_incidents"].values():
            incident["opened_at"] -= 4000
        self.state.write_text(json.dumps(data))
        self.period()

    def test_a_pane_alert_is_short_and_its_report_holds_the_whole_alert(self) -> None:
        self.pane_pass(pane_title="π " + "a very long pane title " * 8)

        self.assertTrue(RunEnvoy.sent)
        for _, message in RunEnvoy.sent:
            report = self.assert_short(message)
            self.assertIn("AGENT PANE AT SHELL", message[:200])
            self.assertIn("%41", message[:200])
            self.assertIn("ESTABLISHED: this pane is at a shell", report.read_text())
            self.assertIn("NOT THE MOMENT YOU READ IT", report.read_text())

    def test_a_pane_clear_is_short(self) -> None:
        self.pane_pass()
        RunEnvoy.sent = []
        self.panes = []
        self.table = ({}, {})
        self.panes = [self.pane(pane_pid="300")]  # same pane id, a new process tree with omp
        self.table = ({300: [301]}, {300: ("bash", 301), 301: ("omp", 301)})

        self.period()

        self.assertTrue(RunEnvoy.sent)
        for _, message in RunEnvoy.sent:
            self.assert_short(message)
            self.assertIn("CLEARED", message[:200])

    def test_a_move_alert_is_short(self) -> None:
        self.pane_pass(session_name="mv-h-cmd-a1")

        self.assertTrue(RunEnvoy.sent)
        for _, message in RunEnvoy.sent:
            self.assert_short(message)
            self.assertIn("WORKER MOVE NOT FINISHED", message[:200])
            self.assertNotIn("send-keys", message)

    def test_reports_older_than_seven_days_are_pruned(self) -> None:
        reports = self.state.parent / "reports"
        reports.mkdir()
        old = reports / "20261020T000000Z-role-alert-old.txt"
        old.write_text("old\n")
        os.utime(old, (self.now - 8 * 86400, self.now - 8 * 86400))
        self.unseen()

        self.period()

        self.assertFalse(old.exists())


class BudgetTest(Box):
    """A run that cannot finish stops cleanly at its budget, with what it did saved."""

    def setUp(self) -> None:
        super().setUp()
        self.order: list[str] = []
        self.slow_first = 1.2
        original = WATCH.holder_evidence

        def evidence(envoy, holder, *args, **kwargs):
            self.order.append(holder)
            if len(self.order) == 1 and self.slow_first:
                time.sleep(self.slow_first)
            return original(envoy, holder, *args, **kwargs)

        WATCH.holder_evidence = evidence
        self.addCleanup(setattr, WATCH, "holder_evidence", original)
        self.write_state({
            "role-a": self.role(HOLDER), "role-b": self.role(OTHER), "role-c": self.role(THIRD),
        })
        RunEnvoy.sessions_list = [
            self.live_session(HOLDER, "role-a"), self.live_session(OTHER, "role-b"),
            self.live_session(THIRD, "role-c"),
        ]
        self.held = {HOLDER: 1, OTHER: 2, THIRD: 3}

    def test_a_slow_step_stops_the_run_inside_its_budget_and_saves_state(self) -> None:
        started = time.time()
        lines = self.period(budget=0.5)
        took = time.time() - started

        self.assertLess(took, 0.5 + self.slow_first + 1.0)
        self.assertEqual(len(self.order), 1, self.order)
        self.assertEqual([line for line in lines if line.startswith("role-")],
                         [f"role-a: ok (holder {HOLDER}, an omp process holds this session: pid 1)"])
        self.assertIn("STOPPED at its 0.5 s budget", lines[-1])
        data = self.data()
        self.assertEqual(sorted(data["roles"]), ["role-a", "role-b", "role-c"])
        self.assertIn("checked_at", data["roles"]["role-a"])

    def test_the_next_run_starts_with_the_roles_the_last_one_did_not_reach(self) -> None:
        self.period(budget=0.5)
        self.order.clear()

        self.period(budget=0.5)

        self.assertEqual(self.order[:1], [OTHER])


class HeaderCacheTest(Box):
    """38 s of a 39 s process scan at load 268 was re-reading the same ~500 transcript headers."""

    def transcript(self, name: str, session: str, header: bool = True) -> Path:
        path = self.root / "agent/sessions/-src-lane" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        title = '{"type":"title","title":"x"}\n'
        path.write_text(title + ('{"type":"session","id":"' + session + '"}\n' if header else ""))
        return path

    def fresh_run(self) -> None:
        WATCH._HEADER_IDS.clear()

    def test_a_header_read_once_is_not_read_again_by_the_next_run(self) -> None:
        cache = self.state.parent / "headers.json"
        path = self.transcript("Lane.jsonl", HOLDER)
        WATCH.load_header_cache(cache)
        self.assertEqual(WATCH.session_id_in_header(path), HOLDER)
        WATCH.save_header_cache(cache)
        # Same inode, different bytes: only a re-read could see the change.
        with open(path, "r+") as handle:
            text = handle.read()
            handle.seek(0)
            handle.write(text.replace(HOLDER, OTHER))
        self.fresh_run()
        WATCH.load_header_cache(cache)

        self.assertEqual(WATCH.session_id_in_header(path), HOLDER)

    def test_a_replaced_file_is_read_again(self) -> None:
        cache = self.state.parent / "headers.json"
        path = self.transcript("Lane.jsonl", HOLDER)
        WATCH.load_header_cache(cache)
        WATCH.session_id_in_header(path)
        WATCH.save_header_cache(cache)
        path.unlink()
        keep = self.transcript("Keep.jsonl", THIRD)  # holds the old inode number away
        self.transcript("Lane.jsonl", OTHER)
        self.fresh_run()
        WATCH.load_header_cache(cache)

        self.assertEqual(WATCH.session_id_in_header(path), OTHER)
        self.assertTrue(keep.exists())

    def test_a_file_whose_header_is_not_written_yet_is_not_cached_as_having_none(self) -> None:
        cache = self.state.parent / "headers.json"
        path = self.transcript("Lane.jsonl", HOLDER, header=False)
        WATCH.load_header_cache(cache)
        self.assertIsNone(WATCH.session_id_in_header(path))
        WATCH.save_header_cache(cache)
        with open(path, "a") as handle:
            handle.write('{"type":"session","id":"' + HOLDER + '"}\n')
        self.fresh_run()
        WATCH.load_header_cache(cache)

        self.assertEqual(WATCH.session_id_in_header(path), HOLDER)

    def test_an_unseen_holder_is_held_while_headers_were_left_unread(self) -> None:
        """A holder known only by an unread header must not be called unseen."""
        self.write_state(
            {"lead-lane": self.role()},
            {f"lead-lane|{HOLDER}": {"opened_at": self.now - 700, "dir": str(self.dir)}},
        )
        unread = {"n": 3}

        def scan() -> dict:
            WATCH._SCAN["unread_headers"] = unread["n"]
            return {}

        WATCH.omp_sessions_by_pid = scan
        lines = self.period()
        self.assertEqual(RunEnvoy.sent, [])
        self.assertTrue(any("unread" in line for line in lines), lines)

        unread["n"] = 0
        self.period()
        self.assertTrue(RunEnvoy.sent)


class ExitedHolderCostTest(Box):
    """2026-11-03T00:18:13Z-00:19:33Z: 80 s on one holder that had exited on 11-02 and whose
    transcript the sweep had taken -- the 3 s walk and the 1 s directory walk, every period."""

    def setUp(self) -> None:
        super().setUp()
        self.walks = 0
        self.dir_walks = 0
        index, newest = WATCH.transcript_index, WATCH.newest_write

        def counted_index():
            self.walks += 1
            return index()

        def counted_newest(*args, **kwargs):
            self.dir_walks += 1
            return newest(*args, **kwargs)

        WATCH.transcript_index, WATCH.newest_write = counted_index, counted_newest
        self.addCleanup(setattr, WATCH, "transcript_index", index)
        self.addCleanup(setattr, WATCH, "newest_write", newest)

    def test_a_recorded_exit_is_not_searched_for_again(self) -> None:
        path = str(WATCH.session_dir_of(str(self.dir)) / f"2026-10-31T19-18-56-140Z_{HOLDER}.jsonl")
        (self.sweep / "deleted.tsv").write_text(f"{path}\t1\tx\t/mnt/x\t2026-11-02T14:10:26Z\n")
        self.write_state(
            {"bet-owner-nickmine": self.role(transcript_missed_at=self.now - 7200)},
            {f"bet-owner-nickmine|{HOLDER}": {
                "opened_at": self.now - 3600, "exited_at": "2026-11-02T05:41:32.174Z",
                "exited_reported_at": self.now - 3600, "alerted_at": self.now - 3000,
                "cleared_at": self.now - 2900, "told": [OTHER],
            }},
            swept={HOLDER: {"path": path, "swept_at": "2026-11-02T14:10:26Z", "mtime": self.now - 30000,
                            "exited_at": "2026-11-02T05:41:32.174Z"}},
        )

        self.period()
        self.period()

        self.assertEqual((self.walks, self.dir_walks), (0, 0))
        self.assertEqual(RunEnvoy.sent, [])


if __name__ == "__main__":
    unittest.main()
