#!/usr/bin/env python3
"""Regression tests for unit-health-watch: run counts from systemd's journal records, the four
conditions, one message per run, and alert routing.

The incident: from 2026-11-01 16:00Z to 2026-11-02 13:49Z systemd killed 26-98% of each hour's
runs of envoy-role-holder-watch on sami-rocqperf at its 25 s TimeoutStartSec, none completed from
2026-11-02 10:00Z to 13:00Z, and nothing said so. Its healthy hour, 2026-11-01 13:00Z, was 119
runs started and 119 completed.

The noise (dispatch://INFRA-672, 2026-11-03): RUN NEVER ENDS opened 8 times in 48 h for units
whose runs are long by nature and cleared by itself 10-15 min later; units with their own
OnFailure= handler were told twice; a condition was said again after 2 h with nothing new; a
message ran to 3,625 characters.

UNIT_HEALTH_WATCH_SCRIPT=<copy> runs these against another copy of the script. The copy needs
scripts/envoy_client.py beside it: the script imports the shared Envoy client from its own
directory, so that a copy under test never reaches the live checkout's.
"""

import argparse
import calendar
import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

SCRIPT = Path(
    os.environ.get("UNIT_HEALTH_WATCH_SCRIPT")
    or Path(__file__).resolve().parents[1] / "unit-health-watch"
)
LOADER = importlib.machinery.SourceFileLoader("unit_health_watch", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
WATCH = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(WATCH)

UNIT = "envoy-role-holder-watch.service"
TIMER = "envoy-role-holder-watch.timer"
TEN_Z = float(
    calendar.timegm(time.strptime("2026-11-02 10:00:00", "%Y-%m-%d %H:%M:%S"))
)
ELEVEN_Z = TEN_Z + 3600
DESCRIPTION = "Alert when an Envoy role's holder process is gone, or an agent tmux pane sits at a shell"


class FakeEnvoy:
    publish_ok = True
    send_ok = True

    def __init__(self, timeout: float = 8.0) -> None:
        self.published: list[tuple[str, str]] = []
        self.sent: list[tuple[str, str]] = []
        FakeEnvoy.last = self

    def publish(self, topic: str, message: str) -> bool:
        self.published.append((topic, message))
        return self.publish_ok

    def send(self, session_id: str, message: str) -> bool:
        self.sent.append((session_id, message))
        return self.send_ok


def options(state: Path, **overrides) -> argparse.Namespace:
    with (
        unittest.mock.patch.object(sys, "argv", [str(SCRIPT)]),
        unittest.mock.patch.object(WATCH, "run_once", side_effect=lambda parsed: parsed),
    ):
        parsed = WATCH.main()
    vars(parsed).update(state=state, **overrides)
    return parsed


def record(
    at: float,
    message_id: str,
    message: str,
    invocation: str,
    unit: str = UNIT,
    **fields,
) -> dict:
    """One journal record the way `journalctl -o json` hands it over."""
    entry = {
        "__REALTIME_TIMESTAMP": str(int(at * 1e6)),
        "MESSAGE": message,
        "USER_UNIT": unit,
        "USER_INVOCATION_ID": invocation,
    }
    if message_id:
        entry["MESSAGE_ID"] = message_id
    entry.update(fields)
    return entry


def run_records(
    start: float, outcome: str, invocation: str, unit: str = UNIT, seconds: float = 25.0
) -> list[dict]:
    """The records systemd writes for one run of a oneshot unit, copied from the real journal."""
    name = unit
    out = [
        record(
            start,
            WATCH.UNIT_STARTING,
            f"Starting {name} - {DESCRIPTION}...",
            invocation,
            unit,
            JOB_TYPE="start",
        )
    ]
    end = start + seconds
    if outcome == "done":
        out.append(
            record(
                end,
                WATCH.UNIT_STARTED,
                f"Finished {name} - {DESCRIPTION}.",
                invocation,
                unit,
                JOB_TYPE="start",
                JOB_RESULT="done",
            )
        )
    elif outcome == "timeout":
        out += [
            record(
                end,
                "",
                f"{name}: start operation timed out. Terminating.",
                invocation,
                unit,
            ),
            record(
                end,
                WATCH.UNIT_PROCESS_EXIT,
                f"{name}: Main process exited, code=killed, status=15/TERM",
                invocation,
                unit,
            ),
            record(
                end,
                WATCH.UNIT_RESULT,
                f"{name}: Failed with result 'timeout'.",
                invocation,
                unit,
                UNIT_RESULT="timeout",
            ),
            record(
                end,
                WATCH.UNIT_FAILED,
                f"Failed to start {name} - {DESCRIPTION}.",
                invocation,
                unit,
                JOB_TYPE="start",
                JOB_RESULT="failed",
            ),
        ]
    elif outcome == "exit-code":
        out += [
            record(
                end,
                WATCH.UNIT_PROCESS_EXIT,
                f"{name}: Main process exited, code=exited, status=1/FAILURE",
                invocation,
                unit,
            ),
            record(
                end,
                WATCH.UNIT_RESULT,
                f"{name}: Failed with result 'exit-code'.",
                invocation,
                unit,
                UNIT_RESULT="exit-code",
            ),
            record(
                end,
                WATCH.UNIT_FAILED,
                f"Failed to start {name} - {DESCRIPTION}.",
                invocation,
                unit,
                JOB_TYPE="start",
                JOB_RESULT="failed",
            ),
        ]
    if outcome != "running":
        out.append(
            record(
                end,
                WATCH.UNIT_RESOURCES,
                f"{name}: Consumed 1.811s CPU time.",
                invocation,
                unit,
            )
        )
    return out


def incident_hour() -> list[dict]:
    """The 10:00Z hour of 2026-11-02 as the journal holds it: 118 runs started about 30 s apart, 0
    completed, 116 killed at the 25 s limit and 2 that failed on their exit code."""
    out = []
    for index in range(118):
        outcome = "exit-code" if index in (40, 90) else "timeout"
        out += run_records(TEN_Z + 21 + index * 30.3, outcome, f"inv{index:04d}")
    return out


def healthy_hour(runs: int = 119) -> list[dict]:
    """The same unit's 13:00Z hour of 2026-11-01: 119 runs started, 119 completed."""
    out = []
    for index in range(runs):
        out += run_records(
            TEN_Z + 10 + index * 30.0, "done", f"ok{index:04d}", seconds=2.0
        )
    return out


def timer_unit(**overrides) -> dict:
    """envoy-role-holder-watch's timer and service as `discover()` reads them."""
    unit = {
        "timer": TIMER,
        "service": UNIT,
        "load_state": "loaded",
        "active_state": "active",
        "unit_file_state": "enabled",
        "period": 30.0,
        "trigger": "OnUnitActiveSec=30s",
        "inactive_trigger": False,
        "last_trigger": ELEVEN_Z - 10,
        "next_elapse": None,
        "timer_since": TEN_Z - 86400,
        "slack": 1.0,
        "service_type": "oneshot",
        "service_state": "inactive",
        "service_substate": "dead",
        "timeout_start": 25.0,
        "documentation": ["/home/sami/.dotfiles/scripts/envoy-role-holder-watch.md"],
        "invocation": "",
        "run_started": None,
        "service_inactive_since": ELEVEN_Z - 5,
    }
    unit.update(overrides)
    return unit


class Harness(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "state.json"
        self.expected = Path(self.tmp.name) / "expected-timers.txt"
        self.expected.write_text("", encoding="utf-8")
        self.input_dirs = {
            name: Path(self.tmp.name) / name
            for name in ("stream_jobs", "stream_job_state", "registry", "transcripts")
        }
        for directory in self.input_dirs.values():
            directory.mkdir()
        self.patches = [
            unittest.mock.patch.object(WATCH, "Envoy", FakeEnvoy),
            unittest.mock.patch.object(WATCH, "last_own_line", lambda *a: (None, "")),
            unittest.mock.patch.dict(os.environ, {"BOX_HEALTH_OWNER": ""}),
        ]
        for patch in self.patches:
            patch.start()
        FakeEnvoy.publish_ok = True
        FakeEnvoy.send_ok = True
        self.journal_reads: list[tuple[float, float]] = []

    def tearDown(self) -> None:
        for patch in reversed(self.patches):
            patch.stop()
        self.tmp.cleanup()

    def run_watch(
        self, units: list[dict], records: list[dict], now: float, **overrides
    ) -> FakeEnvoy:
        overrides.setdefault("expected", self.expected)
        for name, directory in self.input_dirs.items():
            overrides.setdefault(name, directory)
        overrides.setdefault("reboot_window", Path(self.tmp.name) / "reboot-window")

        def read(services, since, until):
            self.journal_reads.append((since, until))
            return [
                r
                for r in records
                if since <= int(r["__REALTIME_TIMESTAMP"]) / 1e6 <= until + 1
            ]

        with (
            unittest.mock.patch.object(WATCH, "discover", lambda: units),
            unittest.mock.patch.object(WATCH, "journal_records", read),
            unittest.mock.patch.object(WATCH.time, "time", lambda: now),
        ):
            WATCH.run_once(options(self.state, **overrides))
        return FakeEnvoy.last

    def report_of(self, text: str) -> str:
        """The report file a message names in its `Full report: <path>` ending."""
        self.assertIn("Full report: ", text)
        path = Path(text.rsplit("Full report: ", 1)[1].strip())
        self.assertEqual(path.parent, self.state.parent / "reports")
        return path.read_text()


class TheIncidentHour(Harness):
    def test_counts_match_the_journal(self) -> None:
        runs = WATCH.runs_from_records(incident_hour())[UNIT]
        counts = WATCH.tally(runs, TEN_Z, ELEVEN_Z)
        self.assertEqual(counts["started"], 118)
        self.assertEqual(counts["completed"], 0)
        self.assertEqual(counts["killed"], 116)
        self.assertEqual(counts["failed"], 2)
        self.assertEqual(counts["failures"], {"exit-code": 2})
        self.assertEqual(counts["last_failure"]["outcome"], "timeout")
        self.assertIn(
            f"{UNIT}: start operation timed out. Terminating.",
            counts["last_failure"]["lines"],
        )

    def test_the_twenty_hour_failure_alerts(self) -> None:
        envoy = self.run_watch([timer_unit()], incident_hour(), ELEVEN_Z)
        self.assertEqual(
            len(envoy.published), 1, "one message for the run, not one per condition"
        )
        topic, text = envoy.published[0]
        self.assertEqual(topic, WATCH.BOX_HEALTH_ROLE_TOPIC)
        self.assertLessEqual(len(text), 600)
        self.assertTrue(text.startswith("UNIT RUNS NOT COMPLETING on "))
        self.assertIn("NO RUN COMPLETED - completed 0 of 118 runs", text)
        self.assertIn("RUNS KILLED AT THE TIME LIMIT - 116 of 118 runs", text)
        # The detail is in the report the message names.
        report = self.report_of(text)
        self.assertIn(
            "118 started, 0 completed, 116 killed at its 25s start limit (TimeoutStartSec)",
            report,
        )
        self.assertIn("2 failed otherwise (2 exit-code)", report)
        self.assertIn("Failed with result 'timeout'.", report)
        self.assertIn(f"journalctl --user -u {UNIT} --since 2026-11-02T10:00:00Z", report)
        self.assertIn("envoy-role-holder-watch.md", report)

    def test_the_same_failure_is_said_once(self) -> None:
        records = incident_hour()
        self.run_watch([timer_unit()], records, ELEVEN_Z)
        envoy = self.run_watch([timer_unit()], records, ELEVEN_Z + 60)
        self.assertEqual(envoy.published, [])

    def test_still_failing_hours_later_is_no_new_fact_and_is_not_said_again(self) -> None:
        """It used to be said again once after 2 h (STILL NOT COMPLETING), with nothing new."""
        records = incident_hour()
        late = []
        for index in range(118):
            late += run_records(
                ELEVEN_Z + 7200 + 21 + index * 30.4, "timeout", f"late{index:04d}"
            )
        self.run_watch([timer_unit()], records, ELEVEN_Z)
        for at in (ELEVEN_Z + 10800, ELEVEN_Z + 10860):
            envoy = self.run_watch(
                [timer_unit(last_trigger=at - 10)], records + late, at
            )
            self.assertEqual(envoy.published + envoy.sent, [])

    def test_a_new_unit_failing_while_one_is_open_is_said_alone(self) -> None:
        other = "fixture-second-watch.service"
        records = incident_hour()
        self.run_watch([timer_unit()], records, ELEVEN_Z)
        second = []
        for index in range(118):
            second += run_records(
                TEN_Z + 300 + 21 + index * 30.3, "timeout", f"mem{index:04d}", unit=other
            )
        fresh = ELEVEN_Z + 290
        units = [
            timer_unit(last_trigger=fresh),
            timer_unit(service=other, timer="fixture-second-watch.timer", last_trigger=fresh),
        ]
        envoy = self.run_watch(units, records + second, ELEVEN_Z + 300)
        self.assertEqual(len(envoy.published), 1)
        text = envoy.published[0][1]
        self.assertIn(other, text)
        self.assertNotIn(UNIT, text, "the unit already said is not said again")


class AHealthyHour(Harness):
    def test_119_of_119_is_quiet(self) -> None:
        runs = WATCH.runs_from_records(healthy_hour())[UNIT]
        counts = WATCH.tally(runs, TEN_Z, ELEVEN_Z)
        self.assertEqual(
            (counts["started"], counts["completed"], counts["killed"]), (119, 119, 0)
        )
        envoy = self.run_watch([timer_unit()], healthy_hour(), ELEVEN_Z)
        self.assertEqual(envoy.published + envoy.sent, [])

    def test_recovery_clears(self) -> None:
        self.run_watch([timer_unit()], incident_hour(), ELEVEN_Z)
        recovered: list[dict] = []
        for index in range(119):
            recovered += run_records(
                ELEVEN_Z + 10 + index * 30.0, "done", f"rec{index:04d}", seconds=2.0
            )
        envoy = self.run_watch(
            [timer_unit(last_trigger=ELEVEN_Z + 3590)], recovered, ELEVEN_Z + 3600
        )
        self.assertEqual(len(envoy.published), 1)
        text = envoy.published[0][1]
        self.assertTrue(text.startswith("UNIT HEALTH CLEARED"))
        self.assertIn("NO RUN COMPLETED cleared", text)
        self.assertIn("RUNS KILLED AT THE TIME LIMIT cleared", text)


class TheKillShare(Harness):
    def counts(self, done: int, killed: int) -> dict:
        records = []
        for index in range(done + killed):
            records += run_records(
                TEN_Z + 10 + index * 30.0,
                "timeout" if index < killed else "done",
                f"r{index}",
            )
        return WATCH.tally(WATCH.runs_from_records(records)[UNIT], TEN_Z, ELEVEN_Z)

    def test_over_the_share_alerts(self) -> None:
        found = WATCH.judge(
            timer_unit(),
            self.counts(done=88, killed=31),
            ELEVEN_Z,
            options(Path("/dev/null")),
        )
        self.assertIn("killed", found)
        self.assertNotIn("no-completion", found)

    def test_at_the_share_does_not(self) -> None:
        found = WATCH.judge(
            timer_unit(),
            self.counts(done=90, killed=30),
            ELEVEN_Z,
            options(Path("/dev/null")),
        )
        self.assertEqual(found, {})

    def test_one_killed_run_is_not_a_share(self) -> None:
        found = WATCH.judge(
            timer_unit(),
            self.counts(done=1, killed=1),
            ELEVEN_Z,
            options(Path("/dev/null")),
        )
        self.assertEqual(found, {})

    def test_killed_share_above_threshold_alerts(self) -> None:
        # 83 runs: 47 completed, 36 killed at the time limit.
        found = WATCH.judge(
            timer_unit(),
            self.counts(done=47, killed=36),
            ELEVEN_Z,
            options(Path("/dev/null")),
        )
        self.assertIn("killed", found)


class ADeadTimer(Harness):
    def test_a_timer_that_stopped_firing_alerts_with_no_runs_at_all(self) -> None:
        unit = timer_unit(
            last_trigger=ELEVEN_Z - 3 * 3600, service_inactive_since=ELEVEN_Z - 3 * 3600
        )
        envoy = self.run_watch([unit], [], ELEVEN_Z)
        self.assertEqual(len(envoy.published), 1)
        self.assertIn("TIMER STOPPED FIRING", envoy.published[0][1])
        self.assertIn("last fired 3.0h ago", envoy.published[0][1])

    def test_an_enabled_timer_that_is_not_active_alerts(self) -> None:
        unit = timer_unit(
            active_state="inactive",
            last_trigger=None,
            period=86400.0,
            trigger="OnCalendar=*-*-* 22:00:00 UTC",
        )
        found = WATCH.judge(
            unit, WATCH.tally([], TEN_Z, ELEVEN_Z), ELEVEN_Z, options(Path("/dev/null"))
        )
        self.assertIn("dead-timer", found)
        self.assertIn("enabled but inactive", found["dead-timer"])

    def test_a_disabled_stopped_timer_is_left_alone(self) -> None:
        unit = timer_unit(
            active_state="inactive", unit_file_state="disabled", last_trigger=None
        )
        found = WATCH.judge(
            unit, WATCH.tally([], TEN_Z, ELEVEN_Z), ELEVEN_Z, options(Path("/dev/null"))
        )
        self.assertEqual(found, {})

    def test_a_healthy_unit_whose_timer_is_late_but_inside_two_periods_is_quiet(
        self,
    ) -> None:
        unit = timer_unit(last_trigger=ELEVEN_Z - 60)
        counts = WATCH.tally(
            WATCH.runs_from_records(healthy_hour())[UNIT], TEN_Z, ELEVEN_Z
        )
        self.assertEqual(
            WATCH.judge(unit, counts, ELEVEN_Z, options(Path("/dev/null"))), {}
        )

    def test_a_long_run_that_never_ends_is_stuck(self) -> None:
        unit = timer_unit(
            service_state="activating",
            run_started=ELEVEN_Z - 2 * 3600,
            last_trigger=ELEVEN_Z - 2 * 3600,
        )
        found = WATCH.judge(
            unit, WATCH.tally([], TEN_Z, ELEVEN_Z), ELEVEN_Z, options(Path("/dev/null"))
        )
        self.assertEqual(set(found), {"stuck"})

    def test_an_hourly_run_inside_its_limit_is_not_stuck(self) -> None:
        # An hourly unit with no start limit: the fixture's 25 s TimeoutStartSec would make a
        # 40 min oneshot run one that systemd failed to end, which is stuck.
        unit = timer_unit(
            period=3600.0,
            timeout_start=None,
            service_state="activating",
            run_started=ELEVEN_Z - 40 * 60,
            last_trigger=ELEVEN_Z - 40 * 60,
        )
        found = WATCH.judge(
            unit, WATCH.tally([], TEN_Z, ELEVEN_Z), ELEVEN_Z, options(Path("/dev/null"))
        )
        self.assertEqual(found, {})


class AUnitThatIsRemoved(Harness):
    def test_its_open_conditions_close(self) -> None:
        self.run_watch([timer_unit()], incident_hour(), ELEVEN_Z)
        envoy = self.run_watch([], [], ELEVEN_Z + 300)
        self.assertEqual(len(envoy.published), 1)
        self.assertIn(
            "no loaded timer starts this unit any more", envoy.published[0][1]
        )
        envoy = self.run_watch([], [], ELEVEN_Z + 600)
        self.assertEqual(envoy.published, [])


IMPORT = "transcript-import.service"


def finished_runs(unit: str, durations: list[float], before: float) -> list[dict]:
    """One completed run of each duration, one after another, all done by `before`."""
    out = []
    start = before
    for index, seconds in enumerate(durations):
        start -= seconds + 600.0
        out += run_records(start, "done", f"{unit}-{index:03d}", unit=unit, seconds=seconds)
    return out


class ALongRunAgainstItsOwnHistory(Harness):
    """Replayed over this box's user journal (2026-11-01 10:50Z to 11-03 03:34Z), the old
    two-period floor opened RUN NEVER ENDS 13 times, and every one of those runs ended by itself:
    transcript-import (every 10 min, floor 22 min, TimeoutStartSec 50 min) ran 32-45 min,
    agent-files-archive (every 20 min, floor 43 min, no start limit) 69 and 88 min."""

    IMPORT_UNIT = {
        "service": IMPORT,
        "timer": "transcript-import.timer",
        "period": 600.0,
        "trigger": "OnUnitActiveSec=10min",
        "timeout_start": 3000.0,
        "timeout_stop": 90.0,
        "documentation": [],
    }
    ARCHIVE = {
        "service": "agent-files-archive.service",
        "timer": "agent-files-archive.timer",
        "period": 1200.0,
        "trigger": "OnUnitActiveSec=20min",
        "timeout_start": None,
        "documentation": [],
    }
    # 98 runs of 6 min and 2 of 40 min: p95 6 min, p99 and longest 40 min.
    ARCHIVE_RUNS = [360.0] * 49 + [2400.0] + [360.0] * 49 + [2400.0]

    def run_long(self, age: float, durations=None, **overrides) -> FakeEnvoy:
        values = dict(self.IMPORT_UNIT)
        values.update(
            service_state="activating", run_started=ELEVEN_Z - age, last_trigger=ELEVEN_Z - age
        )
        values.update(overrides)
        records = finished_runs(values["service"], durations or [1080.0] * 20, ELEVEN_Z - age)
        return self.run_watch([timer_unit(**values)], records, ELEVEN_Z)

    def test_a_run_under_twice_its_p99_is_quiet(self) -> None:
        envoy = self.run_long(30 * 60)
        self.assertEqual(envoy.published + envoy.sent, [])

    def test_a_run_over_twice_its_p99_alerts(self) -> None:
        envoy = self.run_long(40 * 60)
        self.assertEqual(len(envoy.published), 1)
        text = envoy.published[0][1]
        self.assertIn(f"{IMPORT} RUN NEVER ENDS", text)
        self.assertIn(
            "over its 36min limit (2 x its p99 run time of 18min over 20 runs, longest 18min)", text
        )

    def test_a_run_that_ends_within_the_units_own_range_is_quiet(self) -> None:
        """agent-files-archive's 69 min run: past its floor and past twice its p95, inside twice
        its p99 and its longest."""
        envoy = self.run_long(69 * 60, durations=self.ARCHIVE_RUNS, **self.ARCHIVE)
        self.assertEqual(envoy.published + envoy.sent, [])

    def test_a_truly_hung_run_still_alerts(self) -> None:
        """The same unit, one run 3 h on: past twice the longest run it ever completed."""
        envoy = self.run_long(3 * 3600, durations=self.ARCHIVE_RUNS, **self.ARCHIVE)
        self.assertEqual(len(envoy.published), 1)
        text = envoy.published[0][1]
        self.assertIn("agent-files-archive.service RUN NEVER ENDS", text)
        self.assertIn("over its 80min limit (2 x its p99 run time of 40min", text)

    def test_the_limit_stops_where_systemd_should_have_ended_the_run(self) -> None:
        # p99 40 min: twice that is past TimeoutStartSec 50 min + TimeoutStopSec 90 s.
        self.assertEqual(self.run_long(50 * 60, durations=[2400.0] * 20).published, [])
        text = self.run_long(53 * 60, durations=[2400.0] * 20).published[0][1]
        self.assertIn("over its 52min limit (its TimeoutStartSec + TimeoutStopSec)", text)

    def test_a_oneshot_past_its_timeout_alerts_even_under_the_floor(self) -> None:
        """omp-session-sql-sweep: hourly, floor 2.1 h, TimeoutStartSec 45 min. A run still going
        at 50 min is one systemd failed to end."""
        sweep = {
            "service": "omp-session-sql-sweep.service",
            "timer": "omp-session-sql-sweep.timer",
            "period": 3600.0,
            "slack": 300.0,
            "timeout_start": 2700.0,
            "on_failure": ["omp-session-sql-sweep-failed.service"],
        }
        self.assertEqual(self.run_long(40 * 60, durations=[600.0] * 20, **sweep).published, [])
        text = self.run_long(50 * 60, durations=[600.0] * 20, **sweep).published[0][1]
        self.assertIn("omp-session-sql-sweep.service RUN NEVER ENDS", text)
        self.assertIn("(its TimeoutStartSec + TimeoutStopSec)", text)

    def test_a_simple_service_is_not_held_to_its_start_limit(self) -> None:
        """infra977-trial-feed: Type=simple, TimeoutStartSec 2 min, runs for hours by design."""
        feed = {
            "service": "infra977-trial-feed.service",
            "timer": "infra977-trial-feed.timer",
            "period": 7200.0,
            "service_type": "simple",
            "timeout_start": 120.0,
        }
        envoy = self.run_long(30 * 60, service_state="active", **feed)
        self.assertEqual(envoy.published + envoy.sent, [])

    def test_the_two_period_floor_holds_when_twice_the_p99_is_shorter(self) -> None:
        # Every 20 min, no start limit, p99 20 min; the floor is 42 min.
        runs = [1200.0] * 20
        self.assertEqual(self.run_long(41 * 60, durations=runs, **self.ARCHIVE).published, [])
        text = self.run_long(43 * 60, durations=runs, **self.ARCHIVE).published[0][1]
        self.assertIn("over its 42min limit (two periods, at least 15min)", text)

    def test_the_history_is_read_once_an_hour_and_only_for_a_long_run(self) -> None:
        def lookbacks() -> int:
            return sum(1 for since, until in self.journal_reads if until - since > 86400)

        self.run_watch([timer_unit()], healthy_hour(), ELEVEN_Z)
        self.assertEqual(lookbacks(), 0, "nothing runs long, so no history is read")
        self.run_long(30 * 60)
        self.assertEqual(lookbacks(), 1)
        state = json.loads(self.state.read_text())
        self.assertEqual(state["run_times"][IMPORT]["runs"], 20)
        self.assertEqual(state["run_times"][IMPORT]["p99"], 1080.0)
        self.run_long(30 * 60)
        self.assertEqual(lookbacks(), 1, "the hour-old cache answers")


class AUnitWithItsOwnOnFailureHandler(Harness):
    """omp-session-sql-sweep and omp-login-watch each have an OnFailure= handler that says a
    failed run; this watch said the same failures again (5 + 3 messages for one unit)."""

    HANDLER = {"on_failure": ["envoy-role-holder-watch-failed.service"]}

    def test_its_failed_runs_are_left_to_its_handler(self) -> None:
        envoy = self.run_watch([timer_unit(**self.HANDLER)], incident_hour(), ELEVEN_Z)
        self.assertEqual(envoy.published + envoy.sent, [])
        self.assertEqual(json.loads(self.state.read_text())["incidents"], {})

    def test_its_timer_stopping_is_still_said(self) -> None:
        unit = timer_unit(
            last_trigger=ELEVEN_Z - 3 * 3600,
            service_inactive_since=ELEVEN_Z - 3 * 3600,
            **self.HANDLER,
        )
        envoy = self.run_watch([unit], [], ELEVEN_Z)
        self.assertEqual(len(envoy.published), 1)
        self.assertIn("TIMER STOPPED FIRING", envoy.published[0][1])

    def test_a_failure_open_when_the_handler_appears_closes_without_a_false_clear(
        self,
    ) -> None:
        self.run_watch([timer_unit()], incident_hour(), ELEVEN_Z)
        envoy = self.run_watch(
            [timer_unit(last_trigger=ELEVEN_Z + 50, **self.HANDLER)],
            incident_hour(),
            ELEVEN_Z + 60,
        )
        self.assertEqual(envoy.published + envoy.sent, [])
        self.assertEqual(json.loads(self.state.read_text())["incidents"], {})


class EveryMessage(Harness):
    """At most 600 characters, ending with the report file that holds the detail."""

    NAMES = [f"lane-{index}-watch" for index in range(6)]

    def stopped(self, name: str) -> dict:
        return timer_unit(
            service=f"{name}.service",
            timer=f"{name}.timer",
            last_trigger=ELEVEN_Z - 3 * 3600,
            service_inactive_since=ELEVEN_Z - 3 * 3600,
        )

    def test_every_message_is_at_most_600_chars_and_names_its_report(self) -> None:
        sent: list[str] = []
        stopped = [self.stopped(name) for name in self.NAMES]
        sent += [text for _, text in self.run_watch(stopped, [], ELEVEN_Z).published]
        self.assertEqual(len(sent), 1, "six units, one message")
        self.assertIn("more in the report", sent[0])
        first = self.report_of(sent[0])
        for name in self.NAMES:
            self.assertIn(f"{name}.service: TIMER STOPPED FIRING", first)
        fine = [
            timer_unit(service=f"{name}.service", timer=f"{name}.timer", last_trigger=ELEVEN_Z + 290)
            for name in self.NAMES[:3]
        ]
        sent += [
            text
            for _, text in self.run_watch(fine + stopped[3:], [], ELEVEN_Z + 300).published
        ]
        # Opened and cleared in one pass: one message.
        sent += [
            text
            for _, text in self.run_watch(
                [timer_unit(last_trigger=ELEVEN_Z + 590)] + stopped[3:4],
                incident_hour(),
                ELEVEN_Z + 600,
            ).published
        ]
        self.assertEqual(len(sent), 3)
        for text in sent:
            self.assertLessEqual(len(text), 600, text)
            self.report_of(text)
        self.assertTrue(sent[1].startswith("UNIT HEALTH CLEARED on "))
        self.assertNotIn("\n", sent[1], "a clear is one line")
        self.assertTrue(sent[2].startswith("UNIT RUNS NOT COMPLETING on "))
        self.assertIn("also lane-4-watch.service TIMER STOPPED FIRING closed", sent[2])

    def test_reports_older_than_a_week_are_pruned(self) -> None:
        reports = self.state.parent / "reports"
        reports.mkdir(parents=True)
        old = reports / "20261025T000000Z-open.txt"
        old.write_text("old")
        os.utime(old, (ELEVEN_Z - 8 * 86400, ELEVEN_Z - 8 * 86400))
        self.run_watch([self.stopped(self.NAMES[0])], [], ELEVEN_Z)
        self.assertFalse(old.exists())
        self.assertEqual(len(list(reports.glob("*-open.txt"))), 1)


class Routing(Harness):
    def test_owner_set_hears_it_alone(self) -> None:
        owner = "01a19999-0000-7000-8000-000000000001"
        with unittest.mock.patch.dict(os.environ, {"BOX_HEALTH_OWNER": owner}):
            envoy = self.run_watch([timer_unit()], incident_hour(), ELEVEN_Z)
        self.assertEqual(envoy.published, [])
        self.assertEqual([target for target, _ in envoy.sent], [owner])

    def test_unheld_role_falls_back_to_the_sre(self) -> None:
        FakeEnvoy.publish_ok = False
        envoy = self.run_watch([timer_unit()], incident_hour(), ELEVEN_Z)
        self.assertEqual([target for target, _ in envoy.sent], [WATCH.SRE])

    def test_dry_run_sends_nothing_and_writes_no_state(self) -> None:
        envoy = self.run_watch([timer_unit()], incident_hour(), ELEVEN_Z, dry_run=True)
        self.assertEqual(envoy.published + envoy.sent, [])
        self.assertEqual(self.state.read_text(), "")
        self.assertFalse((self.state.parent / "reports").exists())


class Parsing(unittest.TestCase):
    def test_timespans(self) -> None:
        self.assertEqual(WATCH.parse_timespan("30s"), 30.0)
        self.assertEqual(WATCH.parse_timespan("1min 30s"), 90.0)
        self.assertEqual(WATCH.parse_timespan("10min"), 600.0)
        self.assertAlmostEqual(WATCH.parse_timespan("1d 15h 37min 3.8s"), 142623.8)
        self.assertIsNone(WATCH.parse_timespan("infinity"))

    def test_period_from_monotonic_triggers_ignores_one_shot_ones(self) -> None:
        props = {
            "TimersMonotonic": [
                "{ OnUnitActiveUSec=30s ; next_elapse=1d 15h 37min 3.802307s }",
                "{ OnActiveUSec=10s ; next_elapse=1min 14.104426s }",
            ]
        }
        self.assertEqual(WATCH.timer_period(props), (30.0, "OnUnitActiveSec=30s"))
        self.assertEqual(
            WATCH.timer_period(
                {"TimersMonotonic": ["{ OnActiveUSec=1s ; next_elapse=1s }"]}
            )[0],
            None,
        )

    def test_period_from_a_calendar(self) -> None:
        analyze = (
            "  Original form: hourly\n"
            "Normalized form: *-*-* *:00:00\n"
            "    Next elapse: Mon 2026-11-02 15:00:00 UTC\n"
            "       From now: 46min left\n"
            "   Iteration #2: Mon 2026-11-02 16:00:00 UTC\n"
            "   Iteration #3: Mon 2026-11-02 17:00:00 UTC\n"
        )
        self.assertEqual(WATCH.calendar_period_from(analyze), 3600.0)
        props = {
            "TimersCalendar": [
                "{ OnCalendar=*-*-* *:00:00 ; next_elapse=Mon 2026-11-02 15:00:00 UTC }"
            ]
        }
        self.assertEqual(
            WATCH.timer_period(props, lambda spec: 3600.0),
            (3600.0, "OnCalendar=*-*-* *:00:00"),
        )

    def test_systemctl_times(self) -> None:
        self.assertEqual(WATCH.parse_when("Mon 2026-11-02 11:00:00 UTC"), ELEVEN_Z)
        self.assertIsNone(WATCH.parse_when(""))


if __name__ == "__main__":
    unittest.main()
