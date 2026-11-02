#!/usr/bin/env python3
"""Regression tests for unit-health-watch: run counts from systemd's journal records, the four
conditions, one message per run, and alert routing.

The incident: from 2026-11-01 16:00Z to 2026-11-02 13:49Z systemd killed 26-98% of each hour's
runs of envoy-role-holder-watch on sami-rocqperf at its 25 s TimeoutStartSec, none completed from
2026-11-02 10:00Z to 13:00Z, and nothing said so. Its healthy hour, 2026-11-01 13:00Z, was 119
runs started and 119 completed.
"""

import argparse
import calendar
import importlib.machinery
import importlib.util
import os
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "unit-health-watch"
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
    values = {
        "window": 3600.0,
        "killed_share": 0.25,
        "min_killed": 2,
        "clear_factor": 0.5,
        "stuck_after": 900.0,
        "escalate": 7200.0,
        "state": state,
        "http_timeout": 8.0,
        "dry_run": False,
        "write_state": False,
        "print_alert": False,
        "report": False,
        "json": False,
        "at": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


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
        self.patches = [
            unittest.mock.patch.object(WATCH, "Envoy", FakeEnvoy),
            unittest.mock.patch.object(WATCH, "last_own_line", lambda *a: (None, "")),
            unittest.mock.patch.dict(os.environ, {"BOX_HEALTH_OWNER": ""}),
        ]
        for patch in self.patches:
            patch.start()
        FakeEnvoy.publish_ok = True
        FakeEnvoy.send_ok = True

    def tearDown(self) -> None:
        for patch in reversed(self.patches):
            patch.stop()
        self.tmp.cleanup()

    def run_watch(
        self, units: list[dict], records: list[dict], now: float, **overrides
    ) -> FakeEnvoy:
        with (
            unittest.mock.patch.object(WATCH, "discover", lambda: units),
            unittest.mock.patch.object(
                WATCH,
                "journal_records",
                lambda services, since, until: [
                    r
                    for r in records
                    if since <= int(r["__REALTIME_TIMESTAMP"]) / 1e6 <= until + 1
                ],
            ),
            unittest.mock.patch.object(WATCH.time, "time", lambda: now),
        ):
            WATCH.run_once(options(self.state, **overrides))
        return FakeEnvoy.last


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
        self.assertIn("NO RUN COMPLETED - completed 0 of 118 runs", text)
        self.assertIn("RUNS KILLED AT THE TIME LIMIT - 116 of 118 runs", text)
        self.assertIn(
            "118 started, 0 completed, 116 killed at its 25s start limit (TimeoutStartSec)",
            text,
        )
        self.assertIn("2 failed otherwise (2 exit-code)", text)
        self.assertIn("Failed with result 'timeout'.", text)
        self.assertIn(f"journalctl --user -u {UNIT} --since 2026-11-02T10:00:00Z", text)
        self.assertIn("envoy-role-holder-watch.md", text)

    def test_the_same_failure_is_said_once(self) -> None:
        records = incident_hour()
        self.run_watch([timer_unit()], records, ELEVEN_Z)
        envoy = self.run_watch([timer_unit()], records, ELEVEN_Z + 60)
        self.assertEqual(envoy.published, [])

    def test_still_failing_after_escalate_is_said_again_once(self) -> None:
        records = incident_hour()
        late = []
        for index in range(118):
            late += run_records(
                ELEVEN_Z + 7200 + 21 + index * 30.4, "timeout", f"late{index:04d}"
            )
        self.run_watch([timer_unit()], records, ELEVEN_Z)
        envoy = self.run_watch(
            [timer_unit(last_trigger=ELEVEN_Z + 10790)],
            records + late,
            ELEVEN_Z + 10800,
        )
        self.assertEqual(len(envoy.published), 1)
        self.assertTrue(envoy.published[0][1].startswith("STILL NOT COMPLETING"))
        envoy = self.run_watch(
            [timer_unit(last_trigger=ELEVEN_Z + 10850)],
            records + late,
            ELEVEN_Z + 10860,
        )
        self.assertEqual(envoy.published, [])


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
        self.assertTrue(text.startswith("UNIT RUNS CLEARED"))
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

    def test_omp_memory_watch_on_its_worst_hour_alerts(self) -> None:
        # 2026-11-01T10Z: 83 runs, 47 completed, 36 killed at its limit.
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
        unit = timer_unit(
            period=3600.0,
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
