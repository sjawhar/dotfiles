#!/usr/bin/env python3
"""What disk-fill-watch says, how long it is, and to whom.

dispatch://INFRA-672/ask/b448049d-c312-4e37-9ccb-d5bb1ffe7256 asked for fewer, more useful
box-watcher messages. In 48 h this watch sent 150 messages, 66% of all watcher text: 52 were
"STILL" re-sends of ~6 KB sent by a 30-minute clock with no new fact, 8 of them headed
"DESCENDING" while their own rate said rising or flat, and 62 were NEW BIG notices that named
nobody who could act. So:

- an open crossing is said again only on a new fact: free fell a further 25% below the lowest
  level already reported, time to full fell under an hour, or free fell under the floor;
- a headline says DESCENDING only while the disk is measurably falling;
- every message is at most 600 characters and ends with `Full report: <path>`;
- a NEW BIG entry goes to its owner session only, and one with no owner waits for one digest a
  day; on the root disk an entry must reach 2 GB;
- the scratch scan stops at its budget and resumes where it stopped, and the alert path keeps
  inside the run's budget, so a run under load finishes inside the unit's limit.
"""

import argparse
import calendar
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path


SCRIPT = Path(
    os.environ.get("DISK_FILL_WATCH_SCRIPT") or Path(__file__).resolve().parents[1] / "disk-fill-watch"
)
LOADER = importlib.machinery.SourceFileLoader("disk_fill_watch_messages", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
WATCH = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(WATCH)

GIB = WATCH.GIB
GB = WATCH.GB
NVME = "/mnt/nvme"
ROOT = "/"
# sami-rocqperf, measured 2026-11-02.
NVME_TOTAL = int(3463.2 * GIB)
ROOT_TOTAL = int(494.9 * GIB)
LANE = "01a19336-7537-7291-98db-3b4c6de1d7de"
OTHER_LANE = "01a17abe-cb74-726b-ac1f-5a5711aec7b5"
THIRD_LANE = "01a1809b-4a90-7381-9c3a-7e9dd1744d97"
REPORT_RE = re.compile(r"Full report: (\S+)\Z")


def at(stamp: str) -> float:
    return float(calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")))


def writer(pid: int, gigabytes: float, session: str, command: str, on_mount: bool = True) -> dict:
    """One described writer, as describe_writers() returns it."""
    return {
        "pid": pid,
        "written": int(gigabytes * GB),
        "write_traffic_rate": gigabytes * GB / 15,
        "command": command,
        "session": f"session {session}" if session else "no session id; scope none",
        "session_id": session,
        "scope": "omp-run-r0c84d28.scope",
        "pane": "lane-7:work.0",
        "cwd": "/mnt/nvme/sami/src/oss-verify-proofs-internal" if on_mount else "/home/sami",
        "on_mount": on_mount,
        "example": "/mnt/nvme/sami/tmp/pytest-of-sami/run-1234/output.bin (2.1 GB)" if on_mount else "",
        "read_only": "",
        "fd_error": "",
        "age": 3600.0,
    }


# Long commands, real session ids: what a rocqperf alert carries.
WRITERS = [
    writer(
        4100001, 3.1, LANE,
        "/home/sami/.local/share/uv/python/cpython-3.14.0-linux-x86_64-gnu/bin/python3.14 -m pytest"
        " -n 32 tests/integration/test_grade_farm_end_to_end.py"
        " --basetemp=/mnt/nvme/sami/tmp/pytest-of-sami/run-1234",
    ),
    writer(
        4100002, 2.4, OTHER_LANE,
        "/usr/bin/rsync -a --info=progress2 /mnt/nvme/sami/grade-farm/cache/build-cache/"
        " /mnt/nvme/sami/grade-farm/staging/registration-2026-11-02/",
    ),
    writer(
        4100003, 1.2, THIRD_LANE,
        "dune build @install --root /mnt/nvme/sami/src/oss-verify-proofs-internal/_build/default -j 64",
    ),
    writer(4100004, 0.9, "", "tar -xf /mnt/nvme/sami/tmp/frozen-drop-2026-11-02.tar"),
    writer(4100005, 5.0, LANE, "/usr/bin/cp -a /home/sami/scratch/a /mnt/durable/sami/b", on_mount=False),
]


class FakeEnvoy:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []
        self.sent: list[tuple[str, str]] = []
        self.publish_ok = True
        self.send_ok = True

    def request(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        return 0, {}

    def publish(self, topic: str, message: str) -> bool:
        self.published.append((topic, message))
        return self.publish_ok

    def send(self, session_id: str, message: str) -> bool:
        self.sent.append((session_id, message))
        return self.send_ok


class Harness(unittest.TestCase):
    """A crossing on sami-rocqperf's /mnt/nvme, with the clock and the disk in the test's hands."""

    mount = NVME
    total = NVME_TOTAL

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state" / "state.json"
        self.state.parent.mkdir()
        self.envoy = FakeEnvoy()
        self.now = at("2026-11-02T03:13:10Z")
        self.free = 0
        self.sampled: list[float] = []

        environment = unittest.mock.patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("BOX_HEALTH_OWNER", None)

        replacements = {
            "Envoy": lambda timeout=8.0: self.envoy,
            "free_bytes": lambda mount: (self.free, self.total),
            "device_of": lambda path: None,
            "sample_writers": self.fake_sample,
            "describe_writers": lambda rows, device, seconds, top: [dict(row) for row in WRITERS[:top]],
        }
        for name, replacement in replacements.items():
            original = getattr(WATCH, name)
            setattr(WATCH, name, replacement)
            self.addCleanup(setattr, WATCH, name, original)
        original_time = WATCH.time.time
        WATCH.time.time = lambda: self.now
        self.addCleanup(setattr, WATCH.time, "time", original_time)

    def fake_sample(self, seconds: float) -> tuple[list[dict], int]:
        self.sampled.append(seconds)
        return [{"pid": row["pid"], "written": row["written"]} for row in WRITERS], 0

    def options(self, **overrides) -> argparse.Namespace:
        values = {
            "mount": [self.mount],
            "horizon": 7200.0,
            "rate_window": 3600.0,
            "min_span": 1500.0,
            "low_slack": 2.0,
            "default_floor": 20.0,
            "max_floor_fraction": WATCH.MAX_FLOOR_FRACTION,
            "clear_factor": 1.5,
            "grace": 240.0,
            "urgent_horizon": WATCH.URGENT_HORIZON,
            "history": 10800.0,
            "samples": 400,
            "confirmations": 3,
            "confirmation_spacing": 300.0,
            "writer_sample": 15.0,
            "top_writers": 8,
            "sudo": "never",
            "writers_json": None,
            "tmp": "/nonexistent-tmp",
            "src": "/nonexistent-src",
            "tmp_age": 21600.0,
            "tmp_big": 500_000_000,
            "root_big": 2_000_000_000,
            "tmp_walk_budget": 20.0,
            "tmp_idle_walk_budget": 5.0,
            "tmp_digest": 86400.0,
            "tmp_pending_max": 500,
            "run_budget": 100.0,
            "state": self.state,
            "iops_caps": {},
            "http_timeout": 8.0,
            "dry_run": False,
            "write_state": False,
            "print_alert": False,
            "report": None,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def run_watch(self, **overrides) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            with unittest.mock.patch.object(WATCH, "LOG_STREAM", io.StringIO()):
                self.assertEqual(0, WATCH.run_once(self.options(**overrides)))

    def series(self, points: list[tuple[float, float]]) -> list[list[float]]:
        """[[stamp, free bytes]] from (seconds before now, GiB free) pairs."""
        return [[self.now - before, int(free * GIB)] for before, free in points]

    def incident(self, alerted_ago: float, free_gib: float, **fields) -> dict:
        """A crossing already alerted `alerted_ago` seconds ago, its last message at `free_gib`."""
        alerted = self.now - alerted_ago
        free = int(free_gib * GIB)
        record = {
            "opened_at": alerted - 300,
            "opened_free": free,
            "alerted_at": alerted,
            "alert_free": free,
            "reported_free": free,
            "low_free": free,
            "free": free,
            "told": [WATCH.BOX_HEALTH_ROLE_TOPIC],
            "first_rule": "grace",
        }
        record.update(fields)
        return record

    def seed(self, samples: list[list[float]], incident: dict | None = None) -> None:
        self.state.write_text(
            json.dumps(
                {
                    "mounts": {self.mount: {"samples": samples}},
                    "incidents": {self.mount: incident} if incident else {},
                }
            )
        )

    def messages(self) -> list[str]:
        return [text for _, text in self.envoy.published + self.envoy.sent]

    def assert_short(self, text: str) -> str:
        """At most 600 characters, ending with the report it names; returns that report."""
        self.assertLessEqual(len(text), 600, text)
        found = REPORT_RE.search(text)
        self.assertIsNotNone(found, text)
        path = Path(found.group(1))
        self.assertTrue(path.is_file(), path)
        self.assertEqual(self.state.parent / "reports", path.parent)
        self.assertRegex(path.name, r"^\d{8}T\d{6}Z-[a-z0-9-]+\.txt$")
        return path.read_text()


class CrossingResendTest(Harness):
    def test_a_repeat_with_no_new_fact_sends_nothing(self) -> None:
        """/mnt/nvme hovered under its floor for 7 h on 2026-11-01 and was said 20 times."""
        start = self.now
        self.seed(self.series([(3600 - 300 * i, 30.0) for i in range(12)]), self.incident(3600, 30.0))

        for step, free in enumerate([31.0, 29.0, 32.0, 30.0, 28.5, 31.5] * 6):  # three hours
            self.now = start + 300 * (step + 1)
            self.free = int(free * GIB)
            self.run_watch()

        self.assertEqual([], self.messages())

    def test_a_fall_of_a_quarter_below_the_last_message_is_said(self) -> None:
        self.mount, self.total = ROOT, ROOT_TOTAL
        self.seed(
            self.series([(3300 - 300 * i, 12.5 - 0.3 * i) for i in range(11)]),
            self.incident(600, 12.0),
        )
        self.free = int(9.1 * GIB)  # 24% under the 12.0 GiB last said: not yet

        self.run_watch()
        self.assertEqual([], self.messages())

        self.now += 300
        self.free = int(8.9 * GIB)  # 26% under it
        self.run_watch()

        self.assertEqual(1, len(self.messages()))
        text = self.messages()[0]
        self.assert_short(text)
        self.assertIn("fell 26%", text)
        self.assertIn("12.0 GiB", text)

    def falling_to_46(self) -> None:
        """An hour at 60 GB/h ending at 46 GiB: 49 minutes to full, 8% under the last message."""
        anchor = 46.0 + 55 * GB / GIB
        self.seed(
            self.series([(3300 - 300 * i, anchor - (anchor - 46.0) * (300 * i) / 3300) for i in range(11)]),
            self.incident(600, 50.0),
        )
        self.free = int(46.0 * GIB)

    def test_under_an_hour_to_full_is_said_once(self) -> None:
        self.falling_to_46()

        self.run_watch()

        self.assertEqual(1, len(self.messages()))
        text = self.messages()[0]
        self.assert_short(text)
        self.assertIn("under 1 h to full", text)
        self.assertIn("49 min", text)

        self.now += 300
        self.free = int(42.0 * GIB)  # still under an hour, and only 9% further down
        self.run_watch()
        self.assertEqual(1, len(self.messages()))

    def test_a_rising_disk_is_never_called_descending(self) -> None:
        """2026-11-01T19:54Z said STILL DESCENDING with `rising 20.2 GB/h` in its own body."""
        self.seed(self.series([(3300, 20.0), (300, 24.0)]), self.incident(1200, 40.0))
        self.free = int(28.0 * GIB)  # 30% under the 40.0 GiB last said, rising over the hour

        self.run_watch()

        self.assertEqual(1, len(self.messages()))
        text = self.messages()[0]
        self.assertIn("STILL UNDER THE FLOOR", text)
        self.assertNotIn("DESCENDING", text)
        self.assertIn("rising", text)
        self.assert_short(text)

    def test_falling_under_the_floor_is_said_once(self) -> None:
        """A crossing opened on the fill rate at 81 GiB, above the 78.2 GiB floor."""
        self.seed(self.series([(3300, 85.0), (300, 79.0)]), self.incident(600, 81.0))
        self.free = int(77.5 * GIB)

        self.run_watch()

        self.assertEqual(1, len(self.messages()))
        text = self.messages()[0]
        self.assertIn("Said again: free 77.5 GiB is under the 78.2 GiB floor.", text)
        self.assert_short(text)

        self.now += 300
        self.free = int(77.0 * GIB)
        self.run_watch()
        self.assertEqual(1, len(self.messages()))

    # The journal of 2026-11-02: /mnt/nvme's free space, the crossing's first alert at 00:51:43Z
    # and its last message at 02:53:20Z with 15.9 GiB free and 28 minutes to full.
    JOURNAL_0313 = [
        ("2026-11-02T02:11:15Z", 57.6),
        ("2026-11-02T02:16:15Z", 70.1),
        ("2026-11-02T02:21:15Z", 59.9),
        ("2026-11-02T02:26:23Z", 49.1),
        ("2026-11-02T02:32:22Z", 50.0),
        ("2026-11-02T02:37:22Z", 46.1),
        ("2026-11-02T02:42:27Z", 37.6),
        ("2026-11-02T02:47:27Z", 30.8),
        ("2026-11-02T02:52:35Z", 15.9),
        ("2026-11-02T02:57:47Z", 25.7),
        ("2026-11-02T03:03:05Z", 24.4),
        ("2026-11-02T03:08:05Z", 17.6),
    ]

    def test_the_2026_11_02_0313Z_fall_still_fires(self) -> None:
        """9.2 GiB free, falling 68.9 GB/h, 8 minutes from full: the repeat that carried a fact."""
        self.seed(
            [[at(stamp), int(free * GIB)] for stamp, free in self.JOURNAL_0313],
            {
                "opened_at": at("2026-11-02T00:51:43Z"),
                "opened_free": int(61.2 * GIB),
                "alerted_at": at("2026-11-02T00:51:43Z"),
                "alert_free": int(61.2 * GIB),
                "reported_free": int(15.9 * GIB),
                "escalated_at": at("2026-11-02T02:53:20Z"),
                "escalations": 9,
                "said_under_hour": True,
                "first_rule": "urgent",
                "low_free": int(15.9 * GIB),
                "free": int(17.6 * GIB),
                "told": [WATCH.BOX_HEALTH_ROLE_TOPIC],
            },
        )
        self.free = int(9.2 * GIB)

        self.run_watch()

        self.assertEqual(1, len(self.messages()))
        self.assertEqual(WATCH.BOX_HEALTH_ROLE_TOPIC, self.envoy.published[0][0])
        text = self.messages()[0]
        report = self.assert_short(text)
        for fact in ("/mnt/nvme", "9.2 GiB free", "falling 68.9 GB/h", "8 min", "to full", "STILL DESCENDING", "fell 42%"):
            self.assertIn(fact, text)
        # The top three writers on the disk, with their sessions; the rest is in the report.
        for row in WRITERS[:3]:
            self.assertIn(f"pid {row['pid']}", text)
            self.assertIn(row["session_id"], text)
        self.assertIn("Top writers now", report)
        for row in WRITERS:
            self.assertIn(str(row["pid"]), report)


class MessageShapeTest(Harness):
    def urgent_first_alert(self) -> None:
        """/mnt/nvme falling 6 GiB a period, then 28 GiB in the last one: urgent on sample one."""
        self.seed(self.series([(3900 - 300 * i, 160.0 - 6 * i) for i in range(13)]))
        self.free = int(60 * GIB)

    def test_a_first_alert_is_short_and_its_report_has_the_detail(self) -> None:
        self.urgent_first_alert()

        self.run_watch()

        self.assertEqual(1, len(self.messages()))
        text = self.messages()[0]
        self.assertIn("first alert", text)
        report = self.assert_short(text)
        self.assertIn("Why this alerts", report)
        self.assertIn("Top writers now", report)
        for row in WRITERS:
            self.assertIn(str(row["pid"]), report)

    def test_a_clear_is_one_line_naming_its_report(self) -> None:
        self.seed(self.series([(600, 150.0), (300, 150.0)]), self.incident(3600, 30.0))
        self.free = int(150 * GIB)

        self.run_watch()

        self.assertEqual(1, len(self.messages()))
        text = self.messages()[0]
        self.assertIn("CLEARED", text)
        self.assertNotIn("\n", text)
        self.assert_short(text)

    def test_a_withdrawn_crossing_is_one_line_naming_its_report(self) -> None:
        self.seed(self.series([(300, 30.0)]), self.incident(3600, 30.0))
        self.free = int(500 * GIB)

        self.run_watch(mount=["/other"])

        self.assertEqual(1, len(self.messages()))
        text = self.messages()[0]
        self.assertIn("withdrawn, not resolved", text)
        self.assertNotIn("\n", text)
        self.assert_short(text)

    def test_the_iops_alert_and_clear_are_short_and_name_their_reports(self) -> None:
        ops = [5_000_000]
        original = WATCH.device_ops
        WATCH.device_ops = lambda mount: ("nvme0n2", ops[0])
        self.addCleanup(setattr, WATCH, "device_ops", original)

        def step(iops: float) -> None:
            self.now += 300
            ops[0] += int(iops * 300)
            self.run_watch(mount=[], iops_caps={"/mnt/durable": 7500.0})

        for _ in range(4):
            step(7500.0)
        self.assertEqual(1, len(self.messages()))
        alert = self.messages()[0]
        self.assertIn("DISK AT ITS IOPS CAP", alert)
        report = self.assert_short(alert)
        self.assertIn("Why this alerts", report)
        self.assertIn("Top writers now", report)

        step(3750.0)
        self.assertEqual(2, len(self.messages()))
        clear = self.messages()[1]
        self.assertIn("DISK IOPS CLEARED", clear)
        self.assertNotIn("\n", clear)
        self.assert_short(clear)

    def test_the_alert_goes_out_without_writers_when_the_run_budget_is_spent(self) -> None:
        """A run under load must still send: 7 runs in 6 h were killed at the 120 s limit."""
        self.urgent_first_alert()

        self.run_watch(run_budget=10.0)

        self.assertEqual([], self.sampled)
        self.assertEqual(1, len(self.messages()))
        text = self.messages()[0]
        report = self.assert_short(text)
        self.assertIn("not sampled", text + report)

    def test_reports_older_than_a_week_are_pruned(self) -> None:
        reports = self.state.parent / "reports"
        reports.mkdir()
        old = reports / "20261025T000000Z-crossing-mnt-nvme.txt"
        recent = reports / "20261101T000000Z-crossing-mnt-nvme.txt"
        for path, age in ((old, 8 * 86400), (recent, 86400)):
            path.write_text("a report\n")
            os.utime(path, (self.now - age, self.now - age))
        self.seed(self.series([(300, 500.0)]))
        self.free = int(500 * GIB)

        self.run_watch()

        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())


class ScratchEntryTest(unittest.TestCase):
    """NEW BIG notices: to the owner session only, and the unowned in one digest a day."""

    MOUNT = "/testmount"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state" / "state.json"
        self.state.parent.mkdir()
        self.scratch = Path(self.tmp.name) / "tmp"
        self.scratch.mkdir()
        self.envoy = FakeEnvoy()
        self.now = time.time()
        self.free = 800 * GIB
        self.owner_answer = ""
        self.devices = {str(self.scratch): 1, self.MOUNT: 1, "/": 1}

        environment = unittest.mock.patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("BOX_HEALTH_OWNER", None)

        def live(envoy) -> None:
            WATCH.LIVE_SESSIONS.clear()
            WATCH.LIVE_SESSIONS[LANE] = {"session_id": LANE}
            WATCH.LIVE_READ = True

        replacements = {
            "Envoy": lambda timeout=8.0: self.envoy,
            "free_bytes": lambda mount: (self.free, 1000 * GIB),
            "device_of": lambda path: self.devices.get(path, 99),
            "owner_of_path": lambda path: self.owner_answer,
            "refresh_live_sessions": live,
            "sample_writers": lambda seconds: ([], 0),
            "describe_writers": lambda rows, device, seconds, top: [],
        }
        for name, replacement in replacements.items():
            original = getattr(WATCH, name)
            setattr(WATCH, name, replacement)
            self.addCleanup(setattr, WATCH, name, original)
        original_time = WATCH.time.time
        WATCH.time.time = lambda: self.now
        self.addCleanup(setattr, WATCH.time, "time", original_time)

        def forget_live() -> None:
            WATCH.LIVE_SESSIONS.clear()
            WATCH.LIVE_READ = False

        forget_live()
        self.addCleanup(forget_live)

    def options(self, **overrides) -> argparse.Namespace:
        values = dict(
            Harness.options(self).__dict__,
            mount=[self.MOUNT],
            tmp=str(self.scratch),
            tmp_age=10 * 86400.0,  # the clock moves days here; every test entry stays young
            tmp_big=500,  # bytes: the test's entries are small
            root_big=2000,
        )
        values.update(overrides)
        return argparse.Namespace(**values)

    mount = MOUNT

    def run_watch(self, **overrides) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            with unittest.mock.patch.object(WATCH, "LOG_STREAM", io.StringIO()):
                self.assertEqual(0, WATCH.run_once(self.options(**overrides)))

    assert_short = Harness.assert_short

    def messages(self) -> list[str]:
        return [text for _, text in self.envoy.published + self.envoy.sent]

    def kept(self) -> dict:
        return json.loads(self.state.read_text())

    def entry(self, name: str, size: int) -> Path:
        path = self.scratch / name
        with open(path, "wb") as handle:
            handle.truncate(size)  # sparse: the size the rule reads, and no disk
        return path

    def test_an_owned_entry_goes_to_its_owner_only(self) -> None:
        self.owner_answer = LANE
        self.entry("rev1", 3000)

        self.run_watch()

        self.assertEqual([], self.envoy.published)
        self.assertEqual([LANE], [who for who, _ in self.envoy.sent])
        text = self.envoy.sent[0][1]
        self.assertIn("NEW BIG /tmp ENTRY", text)
        self.assertIn("rev1", text)
        self.assert_short(text)

    def test_a_short_disk_no_longer_takes_an_owned_entry_from_its_owner(self) -> None:
        self.owner_answer = LANE
        self.free = 50 * GIB  # short (under 100 GB), and above the 20 GiB floor
        self.entry("rev1", 3000)

        self.run_watch()

        self.assertEqual([], self.envoy.published)
        self.assertEqual([LANE], [who for who, _ in self.envoy.sent])

    def test_an_unowned_entry_waits_for_one_digest_a_day(self) -> None:
        self.entry("rev1", 3000)
        start = self.now

        self.run_watch()
        self.assertEqual([], self.messages())
        self.assertEqual(1, len(self.kept()["tmp_pending"]))

        self.now = start + 3600  # the old hourly digest would go now
        self.run_watch()
        self.assertEqual([], self.messages())

        self.now = start + 86400
        self.run_watch()
        self.assertEqual(1, len(self.messages()))
        self.assertEqual(WATCH.BOX_HEALTH_ROLE_TOPIC, self.envoy.published[0][0])
        digest = self.envoy.published[0][1]
        self.assertIn("rev1", digest + self.assert_short(digest))

        self.entry("rev2", 3000)
        self.now = start + 86400 + 300
        self.run_watch()
        self.now = start + 86400 + 3600
        self.run_watch()
        self.assertEqual(1, len(self.messages()))
        self.now = start + 2 * 86400
        self.run_watch()
        self.assertEqual(2, len(self.messages()))
        self.assertIn("rev2", self.envoy.published[1][1] + self.assert_short(self.envoy.published[1][1]))

    def test_a_big_unowned_entry_waits_for_the_digest_too(self) -> None:
        """/tmp/argv-src.tar.gz, 1.7 GB, 2026-11-02T03:23Z: nobody was named to act on it."""
        self.entry("argv-src.tar.gz", 2_100_000_000)

        self.run_watch(root_big=2_000_000_000, tmp_big=500_000_000)

        self.assertEqual([], self.messages())
        self.assertEqual(1, len(self.kept()["tmp_pending"]))

    def test_an_owner_that_refuses_the_send_puts_the_entry_in_the_digest(self) -> None:
        self.owner_answer = LANE
        self.envoy.send_ok = False
        self.entry("rev1", 3000)

        self.run_watch()

        self.assertEqual([LANE], [who for who, _ in self.envoy.sent])
        self.assertEqual([], self.envoy.published)
        self.assertEqual(1, len(self.kept()["tmp_pending"]))

    def test_an_entry_on_the_root_disk_must_reach_two_gigabytes(self) -> None:
        self.owner_answer = LANE
        self.entry("rev1", 1000)  # over --tmp-big (500), under --root-big (2000)

        self.run_watch()

        self.assertEqual([], self.messages())
        self.assertEqual([], self.kept().get("named_tmp", []))

        # The same entry on a short disk that is not the root keeps the --tmp-big floor.
        self.devices = {str(self.scratch): 2, self.MOUNT: 2, "/": 1}
        self.free = 50 * GIB
        self.run_watch()
        self.assertEqual([LANE], [who for who, _ in self.envoy.sent])

    def test_the_defaults_are_two_gigabytes_on_root_and_one_digest_a_day(self) -> None:
        defaults = WATCH.build_parser().parse_args([])
        self.assertEqual(2_000_000_000, defaults.root_big)
        self.assertEqual(500_000_000, defaults.tmp_big)
        self.assertEqual(86400.0, defaults.tmp_digest)

    def test_the_digest_is_short_and_lists_every_entry_in_its_report(self) -> None:
        names = [f"lockprobe-{index:02d}-" + "x" * 90 for index in range(30)]
        for name in names:
            self.entry(name, 3000)

        self.run_watch(tmp_digest=0.0, top_writers=40)

        self.assertEqual(1, len(self.messages()))
        digest = self.messages()[0]
        report = self.assert_short(digest)
        self.assertIn("30", digest)
        for name in names:
            self.assertIn(name, report)


class ScanBoundTest(unittest.TestCase):
    """The scratch scan is bounded by its budget and resumes where it stopped."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.names = [f"entry-{index:02d}" for index in range(30)]
        for name in self.names:
            (self.root / name).write_bytes(b"x" * 10)
        self.clock = [1_000_000.0]
        self.seen: list[str] = []

        def tick() -> float:
            self.clock[0] += 1.0  # every look at the clock costs a second
            return self.clock[0]

        def birth(path: str, stat_result=None) -> float:
            self.seen.append(os.path.basename(path))
            return self.clock[0]

        original_time = WATCH.time.time
        WATCH.time.time = tick
        self.addCleanup(setattr, WATCH.time, "time", original_time)
        original_birth = WATCH.birth_time
        WATCH.birth_time = birth
        self.addCleanup(setattr, WATCH, "birth_time", original_birth)

    def test_a_scan_stops_at_its_budget_and_the_next_resumes_where_it_stopped(self) -> None:
        cursor: dict = {}
        WATCH.young_entries(str(self.root), 3600.0, 1, True, 5.0, cursor=cursor)
        first = list(self.seen)
        self.assertGreater(len(first), 0)
        self.assertLess(len(first), len(self.names))

        for _ in range(len(self.names)):
            if set(self.seen) == set(self.names):
                break
            WATCH.young_entries(str(self.root), 3600.0, 1, True, 5.0, cursor=cursor)

        self.assertEqual(set(self.names), set(self.seen))
        # Every entry is reached once before any is reached twice.
        lap = self.seen[: len(self.names)]
        self.assertEqual(len(lap), len(set(lap)))


class ReportCommandTest(unittest.TestCase):
    def test_report_prints_the_newest_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            reports = Path(tmp) / "reports"
            reports.mkdir()
            (reports / "20261101T000000Z-crossing-mnt-nvme.txt").write_text("the older report\n")
            (reports / "20261102T031310Z-crossing-mnt-nvme.txt").write_text("the newest report\n")
            environment = {key: value for key, value in os.environ.items() if key != "BOX_HEALTH_OWNER"}
            environment["ENVOY_URL"] = "http://127.0.0.1:1"

            done = subprocess.run(
                [sys.executable, str(SCRIPT), "--state", str(state), "--report"],
                capture_output=True,
                text=True,
                timeout=120,
                env=environment,
            )

        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn("the newest report", done.stdout)
        self.assertNotIn("the older report", done.stdout)


class FloorWordingTest(Harness):
    """Free space under the floor is never printed as the same number as the floor.

    DdRootFloor's proof run, 2026-11-03: at 19,327,348,736 bytes free on / the crossing said
    "free 18.0 GiB is under the 18.0 GiB floor", because both round to 18.0 at one decimal.
    When they round equal, the message says by how much free is under it instead.
    """

    mount = ROOT
    total = ROOT_TOTAL
    FLOOR = 18 * GIB

    def first_alert_under_the_floor_by(self, short_by: int) -> tuple[str, str]:
        self.seed(self.series([(600, 18.0), (300, 18.0)]))
        self.free = self.FLOOR - short_by
        self.run_watch(grace=0.0)
        self.assertEqual(1, len(self.messages()))
        text = self.messages()[0]
        return text, self.assert_short(text)

    def assert_never_equal(self, text: str, report: str, shortfall: str) -> None:
        for rendered in (text, report):
            self.assertNotIn("18.0 GiB is under the 18.0 GiB floor", rendered)
            self.assertNotIn("Free is under its 18.0 GiB floor", rendered)
        self.assertIn(f"{shortfall} under the 18.0 GiB floor", text)
        self.assertIn(f"{shortfall} under the 18.0 GiB floor", report)

    def test_one_byte_under_the_floor_says_one_byte(self) -> None:
        text, report = self.first_alert_under_the_floor_by(1)
        self.assert_never_equal(text, report, "1 B")

    def test_one_megabyte_under_the_floor_says_one_megabyte(self) -> None:
        text, report = self.first_alert_under_the_floor_by(1_000_000)
        self.assert_never_equal(text, report, "1.0 MB")

    def test_a_clear_shortfall_keeps_both_numbers(self) -> None:
        """17.6 and 18.0 already differ, so the ordinary sentence stands."""
        text, report = self.first_alert_under_the_floor_by(int(0.4 * GIB))
        self.assertIn("free 17.6 GiB is under the 18.0 GiB floor", report)
        self.assertIn("17.6 GiB is under the 18.0 GiB floor", text)


class GuardWatchFaultTest(Harness):
    """The growth watch's self-test faults reach Envoy through this watch and nowhere else.

    `guard-disk-writers` runs as root, writes only to the journal, and cannot send over Envoy.
    One event has one source, and this watch is the disk alerter on these boxes, so it
    forwards the three WATCH lines once each (Sami through sami-proxy, option B, relayed
    2026-11-03; agent-infra branch guard-disk-writers-root-selftest).
    """

    UNPROVEN = (
        "disk-writers: WATCH UNPROVEN on /mnt/nvme: the probe wrote 4.0 MB and the reporter"
        " charged it to no session. Growth on /mnt/nvme may go unreported; its floor is still"
        " checked. The self-test runs again every 30 minutes."
    )
    BACK = (
        "disk-writers: WATCH BACK on /mnt/nvme: its self-test passed, so the probe sees writes"
        " there again."
    )
    STOPPED = (
        "disk-writers: WATCH STOPPED: the self-test failed: /proc/self/io is not readable."
        " systemd does not start the watch again; a person must look at this kernel."
    )
    PASSED = (
        "disk-writers: the self-test passed: the probe charged this reporter's writes to its"
        " session on every watched mount"
    )

    def setUp(self) -> None:
        super().setUp()
        self.journal: list[tuple[list[str], str, str]] = []
        self.reads: list[str] = []
        self.timeouts: list[float | None] = []
        self.free = int(500 * GIB)  # nothing is crossing: these runs say only this

        def read(cursor: str, deadline: float, timeout: float | None = None) -> tuple[list[str], str, str]:
            self.reads.append(cursor)
            self.timeouts.append(timeout)
            return self.journal.pop(0) if self.journal else ([], cursor, "")

        original = WATCH.guard_journal
        WATCH.guard_journal = read
        self.addCleanup(setattr, WATCH, "guard_journal", original)

        # The unit's own directory, so these tests do not depend on what this host installed.
        self.units = Path(self.tmp.name) / "systemd"
        self.units.mkdir()
        self.unit = self.units / WATCH.GUARD_UNIT
        self.unit.write_text("[Unit]\n")
        original_dirs = WATCH.GUARD_UNIT_DIRS
        WATCH.GUARD_UNIT_DIRS = (str(self.units),)
        self.addCleanup(setattr, WATCH, "GUARD_UNIT_DIRS", original_dirs)

    def run_guard(self, **overrides) -> None:
        self.run_watch(sudo="auto", **overrides)

    def state_record(self) -> dict:
        return json.loads(self.state.read_text()).get("guard_watch", {})

    def test_an_unproven_mount_is_forwarded_once(self) -> None:
        self.journal.append(([self.UNPROVEN], "c1", ""))

        self.run_guard()

        self.assertEqual(1, len(self.messages()))
        self.assertEqual(WATCH.BOX_HEALTH_ROLE_TOPIC, self.envoy.published[0][0])
        text = self.messages()[0]
        self.assertIn("WATCH UNPROVEN on /mnt/nvme", text)
        self.assertIn(self.UNPROVEN, self.assert_short(text))
        self.assertEqual("c1", self.state_record()["cursor"])
        self.assertEqual(["/mnt/nvme"], self.state_record()["open_mounts"])

        # The next read starts after that cursor, so the line is never sent twice.
        self.now += 300
        self.run_guard()
        self.assertEqual(1, len(self.messages()))
        self.assertEqual(["", "c1"], self.reads)

    def test_a_stopped_watch_is_forwarded(self) -> None:
        self.journal.append(([self.STOPPED], "c1", ""))

        self.run_guard()

        text = self.messages()[0]
        self.assertIn("WATCH STOPPED", text)
        self.assert_short(text)

    def test_the_reporters_own_self_test_closes_every_open_mount(self) -> None:
        """After its restart the reporter writes one self-test line, not one BACK per mount."""
        self.journal.append(
            ([self.UNPROVEN, self.UNPROVEN.replace("/mnt/nvme", "/mnt/durable")], "c1", "")
        )
        self.run_guard()
        self.assertEqual(
            ["/mnt/nvme", "/mnt/durable"], self.state_record()["open_mounts"]
        )
        self.envoy.published.clear()

        self.now += 300
        self.journal.append(([self.PASSED], "c2", ""))
        self.run_guard()

        said = self.messages()
        self.assertEqual(2, len(said))
        for mount, text in zip(("/mnt/nvme", "/mnt/durable"), said):
            self.assertIn(f"WATCH BACK on {socket.gethostname()}: {mount} is watched again", text)
            self.assert_short(text)
        self.assertEqual([], self.state_record()["open_mounts"])

    def test_a_per_mount_back_line_closes_its_own_mount(self) -> None:
        self.journal.append(([self.UNPROVEN], "c1", ""))
        self.run_guard()
        self.envoy.published.clear()

        self.now += 300
        self.journal.append(([self.BACK], "c2", ""))
        self.run_guard()

        self.assertEqual(1, len(self.messages()))
        self.assertIn("WATCH BACK on /mnt/nvme", self.messages()[0])
        self.assertEqual([], self.state_record()["open_mounts"])

    def test_a_journal_read_that_fails_is_said_once(self) -> None:
        self.journal.append(([], "", "journalctl exited 1: no permission"))

        self.run_guard()

        self.assertEqual(1, len(self.messages()))
        text = self.messages()[0]
        self.assertIn("DISK GROWTH WATCH UNREADABLE", text)
        self.assertIn("journalctl exited 1", text)
        self.assert_short(text)

        self.now += 300
        self.journal.append(([], "", "journalctl exited 1: no permission"))
        self.run_guard()
        self.assertEqual(1, len(self.messages()))  # same reason: said once

        self.now += 300
        self.journal.append(([], "c1", ""))
        self.run_guard()
        self.assertNotIn("read_error", self.state_record())

    def test_an_unprivileged_run_forwards_nothing(self) -> None:
        self.journal.append(([self.UNPROVEN], "c1", ""))

        self.run_watch(sudo="never")

        self.assertEqual([], self.messages())
        self.assertEqual([], self.reads)

    def test_a_box_without_the_growth_watch_reads_nothing_and_says_nothing(self) -> None:
        """sami-leanperf, sami-devbox and the lane VMs run this script without that unit."""
        self.unit.unlink()
        self.journal.append(([self.UNPROVEN], "c1", ""))

        self.run_guard()

        self.assertEqual([], self.reads)
        self.assertEqual([], self.messages())
        self.assertEqual({}, self.state_record())

    # 2026-11-03T04:59:08Z, load ~320: one read hit its 15 s limit and the box-health lead was
    # told the growth watch was unreadable. A timeout on a loaded box is not a broken journal.
    TIMED_OUT = "journalctl timed out"

    def test_a_timeout_the_retry_reads_past_says_nothing_about_it(self) -> None:
        self.journal.append(([], "", self.TIMED_OUT))
        self.journal.append(([self.UNPROVEN], "c1", ""))

        self.run_guard()

        self.assertEqual(2, len(self.reads))
        self.assertEqual(45.0, self.timeouts[1])
        said = self.messages()
        self.assertEqual(1, len(said))
        self.assertIn("WATCH UNPROVEN on /mnt/nvme", said[0])
        self.assertNotIn("UNREADABLE", said[0])
        self.assertEqual("c1", self.state_record()["cursor"])

    def test_timeouts_are_said_once_after_two_consecutive_runs(self) -> None:
        for _ in range(2):  # run 1: both attempts time out, nothing said
            self.journal.append(([], "", self.TIMED_OUT))
        self.run_guard()
        self.assertEqual([], self.messages())
        self.assertEqual(1, self.state_record()["timeouts"])

        self.now += 300
        for _ in range(2):  # run 2: the second run in a row, said
            self.journal.append(([], "", self.TIMED_OUT))
        self.run_guard()
        self.assertEqual(1, len(self.messages()))
        text = self.messages()[0]
        self.assertIn("DISK GROWTH WATCH UNREADABLE", text)
        self.assertIn("2 consecutive runs", text)
        self.assert_short(text)

        self.now += 300
        for _ in range(2):  # run 3: still timing out, already said
            self.journal.append(([], "", self.TIMED_OUT))
        self.run_guard()
        self.assertEqual(1, len(self.messages()))

        self.now += 300
        self.journal.append(([], "c1", ""))  # run 4: it reads again
        self.run_guard()
        self.assertNotIn("timeouts", self.state_record())
        self.assertNotIn("read_error", self.state_record())

    def test_any_other_failure_is_said_at_once_even_after_a_timeout(self) -> None:
        for _ in range(2):
            self.journal.append(([], "", self.TIMED_OUT))
        self.run_guard()

        self.now += 300
        self.journal.append(([], "", "journalctl exited 1: sudo: a password is required"))
        self.run_guard()

        self.assertEqual(1, len(self.messages()))
        self.assertIn("journalctl exited 1", self.messages()[0])
        self.assertEqual(3, len(self.reads))  # two in run 1, one in run 2: no retry for this


if __name__ == "__main__":
    unittest.main()
