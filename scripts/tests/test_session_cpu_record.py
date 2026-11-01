#!/usr/bin/env python3
"""Tests for scripts/session-cpu-record: its delta math, its attribution and its retention.

Nothing here reads a real /proc or a real cgroup. The process table, the cgroup counters and
the environments are fabricated, and the attribution test drives the REAL `session_claim` of
scripts/load-swap-watch over that fabricated tree, so a change there that breaks this recorder
fails here.
"""

import datetime as dt
import importlib.machinery
import importlib.util
import json
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "session-cpu-record"
LOADER = importlib.machinery.SourceFileLoader("session_cpu_record", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
TOOL = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(TOOL)

ALPHA = "01a13bcd-3e9f-7770-b0f4-88fa364eb58e"
BETA = "01a16c67-c564-7581-82fb-53fbc135de32"


class FakeBox(unittest.TestCase):
    """One fabricated box: agent pids, their cgroups, and the counters of both sources."""

    def setUp(self) -> None:
        self.agents: dict[int, str] = {}
        self.cgroup: dict[int, str] = {}
        self.session: dict[int, str] = {}
        self.parent: dict[int, int] = {}
        self.started: dict[int, float] = {}
        self.cgroup_cpu: dict[str, float] = {}
        self.proc_cpu: dict[int, float] = {}

        self.patch(TOOL, "agent_processes", lambda: dict(self.agents))
        self.patch(TOOL, "cgroup_of", lambda pid: self.cgroup.get(pid, ""))
        self.patch(TOOL, "cgroup_cpu_seconds", lambda path, root="/sys/fs/cgroup": self.cgroup_cpu.get(path))
        self.patch(TOOL, "cgroup_memory", lambda path, root="/sys/fs/cgroup": 0)
        self.patch(TOOL, "cpu_seconds_of", lambda pid: self.proc_cpu.get(pid))
        self.patch(TOOL, "start_time_of", lambda pid: self.started.get(pid, 0.0))
        self.patch(TOOL, "session_titles", dict)
        self.patch(TOOL.WATCH, "session_claim", lambda pid, depth=12: (self.session.get(pid, ""), "test"))
        self.patch(TOOL.WATCH, "ppid_of", lambda pid: self.parent.get(pid, 1))
        self.patch(TOOL.WATCH, "rss_of", lambda pid: 0)
        self.patch(TOOL.WATCH, "_cwd_of", lambda pid: "/home/sami/src")

    def patch(self, where, name: str, value) -> None:
        before = getattr(where, name)
        setattr(where, name, value)
        self.addCleanup(setattr, where, name, before)

    def add_agent(self, pid: int, session: str, cgroup: str, started: float, parent: int = 1) -> None:
        self.agents[pid] = "omp"
        self.session[pid] = session
        self.cgroup[pid] = cgroup
        self.started[pid] = started
        self.parent[pid] = parent

    def rows(self, previous: dict, now: float) -> tuple[dict[str, dict], dict]:
        found, counters = TOOL.collect(previous, now)
        return {row["session"]: row for row in found}, counters


class DeltaMath(FakeBox):
    def test_counts_the_interval_not_the_lifetime(self) -> None:
        """A session running since before the window contributes 0 on the first sighting."""
        self.add_agent(100, ALPHA, "/alpha.scope", started=1000.0)
        self.cgroup_cpu["/alpha.scope"] = 7200.0  # two hours of CPU, all of it older
        first, counters = self.rows({"__at__": 5000.0}, 5300.0)
        self.assertEqual(first[ALPHA]["cpu_seconds"], 0.0)
        self.assertEqual(first[ALPHA]["source"], "cgroup")

        self.cgroup_cpu["/alpha.scope"] = 7500.0  # 300 more over the next interval
        second, _ = self.rows(counters, 5600.0)
        self.assertEqual(second[ALPHA]["cpu_seconds"], 300.0)

    def test_a_session_born_inside_the_window_brings_its_whole_total(self) -> None:
        self.add_agent(101, BETA, "/beta.scope", started=5100.0)
        self.cgroup_cpu["/beta.scope"] = 42.0
        found, _ = self.rows({"__at__": 5000.0}, 5300.0)
        self.assertEqual(found[BETA]["cpu_seconds"], 42.0)

    def test_a_counter_that_went_backwards_never_goes_negative(self) -> None:
        """A reused scope path, or a pid whose counter restarted, must not subtract CPU."""
        self.add_agent(102, ALPHA, "/alpha.scope", started=1000.0)
        self.cgroup_cpu["/alpha.scope"] = 10.0
        found, _ = self.rows({"__at__": 5000.0, "cg:/alpha.scope": 900.0}, 5300.0)
        self.assertEqual(found[ALPHA]["cpu_seconds"], 0.0)

    def test_a_shared_cgroup_falls_back_to_each_process(self) -> None:
        """The ssh login scope measured on sami-rocqperf: two sessions, one cgroup.

        `session-c5.scope` held a `-p` headless run in /home/sami/src/bet-grade beside a
        `--resume` session in /home/sami/src/oss-verifier. Charging that scope's cpu.stat to
        either session would be wrong, so both are counted from their own /proc/<pid>/stat.
        """
        self.add_agent(200, ALPHA, "/session-c5.scope", started=1000.0)
        self.add_agent(201, BETA, "/session-c5.scope", started=1000.0)
        self.cgroup_cpu["/session-c5.scope"] = 999_999.0  # must never be used
        self.proc_cpu[200] = 100.0
        self.proc_cpu[201] = 50.0
        _, counters = self.rows({"__at__": 5000.0}, 5300.0)
        self.proc_cpu[200] = 130.0
        self.proc_cpu[201] = 55.0
        found, _ = self.rows(counters, 5600.0)
        self.assertEqual(found[ALPHA]["cpu_seconds"], 30.0)
        self.assertEqual(found[BETA]["cpu_seconds"], 5.0)
        self.assertEqual(found[ALPHA]["source"], "proc")

    def test_a_child_agent_of_the_same_session_is_not_counted_twice(self) -> None:
        """In a shared cgroup, an `omp __omp_worker_…` child's time is in its parent's cutime."""
        self.add_agent(300, ALPHA, "/session-c5.scope", started=1000.0)
        self.add_agent(301, ALPHA, "/session-c5.scope", started=1000.0, parent=300)
        self.add_agent(302, BETA, "/session-c5.scope", started=1000.0)
        self.proc_cpu[300] = self.proc_cpu[301] = self.proc_cpu[302] = 10.0
        found, counters = self.rows({"__at__": 5000.0}, 5300.0)
        self.assertEqual(found[ALPHA]["procs"], 1)
        self.assertNotIn("pid:301:1000", counters)

    def test_a_process_with_no_session_is_recorded_as_unknown(self) -> None:
        self.add_agent(400, "", "", started=1000.0)
        self.proc_cpu[400] = 5.0
        found, _ = self.rows({"__at__": 5000.0}, 5300.0)
        self.assertIn("unknown", found)


class AttributionIsLoadSwapWatch(unittest.TestCase):
    """The recorder must not carry a second copy of process-to-session attribution.

    This drives load-swap-watch's own `session_claim` over a fabricated tree in the shape that
    broke it on 2026-10-30: an inherited OMP_SESSION_ID naming another box's session, and the
    real owner only in the `--resume` path of the session's own omp.
    """

    def setUp(self) -> None:
        self.parent = {900: 800, 800: 1}
        self.comm = {900: "python3", 800: "omp"}
        resumed = f"/home/sami/.omp/agent/sessions/-src/2026-10-17T18-53-21-403Z_{ALPHA}/session.jsonl"
        self.cmdline = {900: "python3 build.py", 800: f"omp --resume {resumed}"}
        self.environ = {pid: {"OMP_SESSION_ID": BETA} for pid in self.parent}
        for name, value in (
            ("ppid_of", lambda pid: self.parent.get(pid, 1)),
            ("comm_of", lambda pid: self.comm.get(pid, "")),
            ("cmdline_of", lambda pid: self.cmdline.get(pid, "")),
            ("environ_of", lambda pid: self.environ.get(pid, {})),
            ("session_from_fds", lambda pid, limit=128: ""),
            ("worker_dir_of", lambda pid, depth=8: ""),
            ("_cwd_of", lambda pid: "/home/sami/src"),
        ):
            before = getattr(TOOL.WATCH, name)
            setattr(TOOL.WATCH, name, value)
            self.addCleanup(setattr, TOOL.WATCH, name, before)

    def test_the_tool_uses_the_watch_module(self) -> None:
        self.assertEqual(TOOL.WATCH.__file__, str(SCRIPT.parent / "load-swap-watch"))
        self.assertTrue(hasattr(TOOL.WATCH, "session_claim"))

    def test_the_resumed_session_wins_over_the_inherited_variable(self) -> None:
        for name, value in (
            ("agent_processes", lambda: {900: "python3", 800: "omp"}),
            ("cgroup_of", lambda pid: "/alpha.scope"),
            ("cgroup_cpu_seconds", lambda path, root="/sys/fs/cgroup": 10.0),
            ("cgroup_memory", lambda path, root="/sys/fs/cgroup": 0),
            ("start_time_of", lambda pid: 5100.0),
            ("session_titles", dict),
        ):
            before = getattr(TOOL, name)
            setattr(TOOL, name, value)
            self.addCleanup(setattr, TOOL, name, before)
        rows, _ = TOOL.collect({"__at__": 5000.0}, 5300.0)
        self.assertEqual([row["session"] for row in rows], [ALPHA])


class Retention(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.now = dt.datetime(2026, 11, 1, 12, 0, tzinfo=dt.timezone.utc).timestamp()

    def day(self, ago: int) -> Path:
        when = dt.datetime.fromtimestamp(self.now, dt.timezone.utc).date() - dt.timedelta(days=ago)
        path = self.dir / f"{when.isoformat()}.jsonl"
        path.write_text('{"epoch": 1}\n')
        return path

    def test_seven_days_stay_and_the_eighth_goes(self) -> None:
        kept = [self.day(ago) for ago in range(7)]
        gone = [self.day(ago) for ago in (7, 8, 40)]
        removed = TOOL.prune(self.dir, self.now)
        self.assertEqual(sorted(removed), sorted(path.name for path in gone))
        for path in kept:
            self.assertTrue(path.exists(), path.name)
        for path in gone:
            self.assertFalse(path.exists(), path.name)

    def test_nothing_else_in_the_directory_is_touched(self) -> None:
        other = self.dir / "state.json"
        other.write_text("{}")
        odd = self.dir / "notes.jsonl"
        odd.write_text("")
        self.day(30)
        TOOL.prune(self.dir, self.now)
        self.assertTrue(other.exists())
        self.assertTrue(odd.exists())

    def test_a_sample_run_writes_a_day_file_and_prunes(self) -> None:
        stale = self.day(30)
        options = TOOL.argparse.Namespace(records=str(self.dir), keep_days=7, quiet=True)
        TOOL.sample(options)
        self.assertFalse(stale.exists())
        today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
        written = json.loads((self.dir / f"{today}.jsonl").read_text().splitlines()[-1])
        self.assertIn("sessions", written)
        self.assertIn("counters", written)


class ReadingItBack(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.base = time.time() - 3600
        lines = []
        for index in range(3):
            lines.append(
                json.dumps(
                    {
                        "at": TOOL.iso(self.base + index * 300),
                        "epoch": self.base + index * 300,
                        "interval_s": 300.0 if index else 0.0,
                        "host": "sami-rocqperf",
                        "cores": 64,
                        "sessions": [
                            {"session": ALPHA, "cpu_seconds": 600.0, "rss_bytes": 2 << 30, "title": "PIPE", "cwd": "/a"},
                            {"session": BETA, "cpu_seconds": 60.0, "rss_bytes": 1 << 30, "title": "R3A", "cwd": "/b"},
                        ],
                        "counters": {},
                    }
                )
            )
        day = dt.datetime.fromtimestamp(self.base, dt.timezone.utc).strftime("%Y-%m-%d")
        (self.dir / f"{day}.jsonl").write_text("\n".join(lines) + "\n")

    def test_rank_sums_cpu_and_the_window_it_covers(self) -> None:
        records = TOOL.samples_in(self.dir, self.base - 1, time.time())
        self.assertEqual(len(records), 3)
        ordered, covered = TOOL.rank(records)
        self.assertEqual([row["session"] for row in ordered], [ALPHA, BETA])
        self.assertEqual(ordered[0]["cpu_seconds"], 1800.0)
        self.assertEqual(covered, 600.0)
        self.assertEqual(ordered[0]["cpu_seconds"] / covered, 3.0)

    def test_a_window_outside_the_samples_finds_nothing(self) -> None:
        self.assertEqual(TOOL.samples_in(self.dir, self.base - 7200, self.base - 3600), [])

    def test_times_are_read_as_instants_or_ages(self) -> None:
        now = 1_762_000_000.0
        self.assertEqual(TOOL.parse_when("15m", now), now - 900)
        self.assertEqual(TOOL.parse_when("2h", now), now - 7200)
        self.assertEqual(TOOL.parse_when("2026-11-01T12:00Z", now), 1793534400.0)
        self.assertEqual(TOOL.parse_when("2026-11-01T12:00:00+00:00", now), 1793534400.0)
        with self.assertRaises(SystemExit):
            TOOL.parse_when("last tuesday", now)


if __name__ == "__main__":
    unittest.main()
