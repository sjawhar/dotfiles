#!/usr/bin/env python3
"""Regression tests for transcript-lag-watch: when the transcript store stops receiving this
host's sessions, one alert after two checks, one more only on a new fact, one line when it
catches up, and nothing while a deliberate stop is paused.

The gap (notify review G5, 2026-11-03): nothing alerted when the store fell behind. At 02:10Z the
store's newest entry from sami-rocqperf was 132 min old (the importer was stopped at 00:16Z), and
the ossv-agent-* hosts were ~31 h behind.
"""

import calendar
import importlib.machinery
import importlib.util
import os
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "transcript-lag-watch"
LOADER = importlib.machinery.SourceFileLoader("transcript_lag_watch", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
WATCH = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(WATCH)

HOST = "sami-rocqperf"
T0 = float(calendar.timegm(time.strptime("2026-11-03 00:30:00", "%Y-%m-%d %H:%M:%S")))
MIN = 60.0
CHECK = 10 * MIN


class FakeEnvoy:
    publish_ok = True
    send_ok = True

    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []
        self.sent: list[tuple[str, str]] = []

    def publish(self, topic: str, message: str) -> bool:
        self.published.append((topic, message))
        return self.publish_ok

    def send(self, session_id: str, message: str) -> bool:
        self.sent.append((session_id, message))
        return self.send_ok


def units(
    timer_active: str = "active",
    timer_sub: str = "waiting",
    result: str = "success",
    status: str = "0",
    service_active: str = "inactive",
    service_sub: str = "dead",
    load_state: str = "loaded",
) -> dict:
    """What `systemctl --user show` says of the importer's two units, parsed."""
    return {
        WATCH.IMPORT_TIMER: {
            "Id": WATCH.IMPORT_TIMER,
            "LoadState": load_state,
            "ActiveState": timer_active,
            "SubState": timer_sub,
            "UnitFileState": "enabled",
            "LastTriggerUSec": "Tue 2026-11-03 00:10:00 UTC",
        },
        WATCH.IMPORT_SERVICE: {
            "Id": WATCH.IMPORT_SERVICE,
            "LoadState": load_state,
            "ActiveState": service_active,
            "SubState": service_sub,
            "Result": result,
            "ExecMainStatus": status,
            "ExecMainStartTimestamp": f"@{int(T0 - 20 * MIN)}",
            "ExecMainExitTimestamp": f"@{int(T0 - 14 * MIN)}",
        },
    }


class Harness(unittest.TestCase):
    def setUp(self) -> None:
        FakeEnvoy.publish_ok = True
        FakeEnvoy.send_ok = True
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.state_dir = Path(self.temp_dir.name) / "transcript-lag-watch"
        self.units = units()
        self.queries = 0
        env = unittest.mock.patch.dict(os.environ, {"BOX_HEALTH_OWNER": ""})
        env.start()
        self.addCleanup(env.stop)

    def check(
        self, now: float, lag: float | None, *args: str, error: str = ""
    ) -> list[str]:
        """One timer run at `now` with the store's newest entry `lag` seconds old; the texts sent."""

        def store(host: str, timeout: float) -> dict:
            self.queries += 1
            self.assertEqual(host, HOST)
            if error:
                raise WATCH.StoreError(error)
            return {
                "newest": None if lag is None else now - lag,
                "last_load": None if lag is None else now - lag + 2 * MIN,
            }

        options = WATCH.build_parser().parse_args(
            [
                "--state-dir",
                str(self.state_dir),
                "--host",
                HOST,
                "--lag-min",
                "30",
                *args,
            ]
        )
        envoy = FakeEnvoy()
        self.envoy = envoy
        WATCH.run_once(
            options,
            store=store,
            importer=lambda: self.units,
            journal=lambda: (
                "02:16:42 sami-rocqperf transcript-store[1]: loaded batch 1594"
            ),
            envoy=envoy,
            now=now,
        )
        return [text for _, text in envoy.published + envoy.sent]

    def assert_bounded(self, text: str) -> None:
        self.assertLessEqual(len(text), 600, text)
        self.assertIn("Full report: ", text)
        report = Path(text.rsplit("Full report: ", 1)[1].strip())
        self.assertTrue(report.is_file(), report)
        self.assertTrue(report.is_relative_to(self.state_dir / "reports"), report)


class OneAlertAfterTwoChecks(Harness):
    def test_one_check_over_the_bar_says_nothing(self) -> None:
        self.assertEqual(self.check(T0, 31 * MIN), [])

    def test_two_checks_over_the_bar_say_it_once(self) -> None:
        self.check(T0, 31 * MIN)
        sent = self.check(T0 + CHECK, 31 * MIN)
        self.assertEqual(len(sent), 1)
        text = sent[0]
        self.assert_bounded(text)
        self.assertIn(HOST, text)
        self.assertIn("2026-11-03T00:09Z", text)  # newest entry: 00:40Z - 31 min
        self.assertIn("31 min", text)
        self.assertIn(WATCH.IMPORT_TIMER, text)
        self.assertIn("success", text)
        self.assertIn(str(self.state_dir / "paused"), text)

    def test_the_same_lag_on_a_third_check_says_nothing(self) -> None:
        self.check(T0, 31 * MIN)
        self.check(T0 + CHECK, 31 * MIN)
        self.assertEqual(self.check(T0 + 2 * CHECK, 31 * MIN), [])

    def test_a_growing_lag_below_double_says_nothing(self) -> None:
        self.check(T0, 31 * MIN)
        self.check(T0 + CHECK, 41 * MIN)
        self.assertEqual(self.check(T0 + 2 * CHECK, 51 * MIN), [])
        self.assertEqual(self.check(T0 + 3 * CHECK, 61 * MIN), [])

    def test_a_dip_under_the_bar_restarts_the_count(self) -> None:
        self.check(T0, 31 * MIN)
        self.check(T0 + CHECK, 5 * MIN)
        self.assertEqual(self.check(T0 + 2 * CHECK, 31 * MIN), [])

    def test_a_store_with_no_entry_from_this_host_alerts(self) -> None:
        self.check(T0, None)
        sent = self.check(T0 + CHECK, None)
        self.assertEqual(len(sent), 1)
        self.assert_bounded(sent[0])
        self.assertIn("no entry", sent[0])


class OnlyANewFactIsSaidAgain(Harness):
    def alert(self) -> None:
        self.check(T0, 31 * MIN)
        self.assertEqual(len(self.check(T0 + CHECK, 31 * MIN)), 1)

    def test_a_doubled_lag_is_said_once(self) -> None:
        self.alert()
        sent = self.check(T0 + 2 * CHECK, 62 * MIN)
        self.assertEqual(len(sent), 1)
        self.assert_bounded(sent[0])
        self.assertIn("62 min", sent[0])
        self.assertIn("31 min", sent[0])  # the lag at the last message
        self.assertEqual(self.check(T0 + 3 * CHECK, 72 * MIN), [])
        self.assertEqual(len(self.check(T0 + 4 * CHECK, 124 * MIN)), 1)

    def test_a_changed_importer_is_said_once(self) -> None:
        self.alert()
        self.units = units(timer_active="inactive", timer_sub="dead")
        sent = self.check(T0 + 2 * CHECK, 41 * MIN)
        self.assertEqual(len(sent), 1)
        self.assert_bounded(sent[0])
        self.assertIn("inactive", sent[0])
        self.assertEqual(self.check(T0 + 3 * CHECK, 51 * MIN), [])

    def test_a_run_starting_and_ending_is_not_a_new_fact(self) -> None:
        self.alert()
        self.units = units(service_active="activating", service_sub="start")
        self.assertEqual(self.check(T0 + 2 * CHECK, 41 * MIN), [])
        self.units = units()
        self.assertEqual(self.check(T0 + 3 * CHECK, 51 * MIN), [])

    def test_an_importer_state_already_said_is_not_said_again(self) -> None:
        self.alert()
        self.units = units(result="exit-code", status="1")
        self.assertEqual(len(self.check(T0 + 2 * CHECK, 41 * MIN)), 1)
        self.units = units()
        self.assertEqual(self.check(T0 + 3 * CHECK, 51 * MIN), [])
        self.units = units(result="exit-code", status="1")
        self.assertEqual(self.check(T0 + 4 * CHECK, 55 * MIN), [])


class Clearing(Harness):
    def test_back_under_the_bar_is_one_line(self) -> None:
        self.check(T0, 31 * MIN)
        self.check(T0 + CHECK, 41 * MIN)
        sent = self.check(T0 + 2 * CHECK, 4 * MIN)
        self.assertEqual(len(sent), 1)
        self.assert_bounded(sent[0])
        self.assertNotIn("\n", sent[0].strip())
        self.assertEqual(self.check(T0 + 3 * CHECK, 4 * MIN), [])

    def test_no_clear_when_nothing_was_said(self) -> None:
        self.check(T0, 31 * MIN)
        self.assertEqual(self.check(T0 + CHECK, 4 * MIN), [])

    def test_a_new_episode_after_a_clear_alerts_again(self) -> None:
        self.check(T0, 31 * MIN)
        self.check(T0 + CHECK, 41 * MIN)
        self.check(T0 + 2 * CHECK, 4 * MIN)
        self.check(T0 + 3 * CHECK, 35 * MIN)
        self.assertEqual(len(self.check(T0 + 4 * CHECK, 45 * MIN)), 1)


class Pausing(Harness):
    def pause(self, text: str) -> Path:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        path = self.state_dir / "paused"
        path.write_text(text)
        return path

    def test_a_pause_with_an_until_time_says_nothing(self) -> None:
        self.pause("2026-11-03T06:00Z importer stopped for a timed benchmark\n")
        for step in range(6):
            self.assertEqual(self.check(T0 + step * CHECK, (31 + 30 * step) * MIN), [])

    def test_a_pause_with_no_until_time_says_nothing(self) -> None:
        self.pause("store rebuild, INFRA-602\n")
        self.check(T0, 31 * MIN)
        self.assertEqual(self.check(T0 + CHECK, 41 * MIN), [])

    def test_an_expired_pause_alerts_and_says_so(self) -> None:
        self.pause("2026-11-03T00:00Z importer stopped for a timed benchmark\n")
        self.check(T0, 31 * MIN)
        sent = self.check(T0 + CHECK, 41 * MIN)
        self.assertEqual(len(sent), 1)
        self.assert_bounded(sent[0])
        self.assertIn("expired", sent[0])

    def test_the_end_of_a_pause_alerts_on_the_next_check(self) -> None:
        path = self.pause("2026-11-03T06:00Z benchmark\n")
        self.check(T0, 31 * MIN)
        self.check(T0 + CHECK, 41 * MIN)
        path.unlink()
        self.assertEqual(len(self.check(T0 + 2 * CHECK, 51 * MIN)), 1)

    def test_a_pause_after_an_alert_stops_re_sends_but_not_the_clear(self) -> None:
        self.check(T0, 31 * MIN)
        self.check(T0 + CHECK, 31 * MIN)
        self.pause("2026-11-03T06:00Z benchmark\n")
        self.assertEqual(self.check(T0 + 2 * CHECK, 90 * MIN), [])
        self.assertEqual(len(self.check(T0 + 3 * CHECK, 3 * MIN)), 1)

    def test_pause_file_parsing(self) -> None:
        self.assertEqual(
            WATCH.parse_pause("2026-11-03T06:00Z importer stopped\n"),
            (
                float(
                    calendar.timegm(time.strptime("2026-11-03 06:00", "%Y-%m-%d %H:%M"))
                ),
                "importer stopped",
            ),
        )
        self.assertEqual(
            WATCH.parse_pause("  just a reason \n"), (None, "just a reason")
        )
        self.assertEqual(WATCH.parse_pause(""), (None, ""))


class AnUnreachableStore(Harness):
    def test_one_message_per_episode(self) -> None:
        error = "tq: ClickHouse connect to http://10.0.0.20:8123 timed out after 5 s"
        self.assertEqual(self.check(T0, None, error=error), [])
        sent = self.check(T0 + CHECK, None, error=error)
        self.assertEqual(len(sent), 1)
        self.assert_bounded(sent[0])
        self.assertIn("UNREACHABLE", sent[0])
        self.assertIn("10.0.0.20", sent[0])
        self.assertEqual(self.check(T0 + 2 * CHECK, None, error=error), [])
        self.assertEqual(self.check(T0 + 3 * CHECK, None, error="another error"), [])
        back = self.check(T0 + 4 * CHECK, 3 * MIN)
        self.assertEqual(len(back), 1)
        self.assert_bounded(back[0])
        self.assertNotIn("\n", back[0].strip())
        self.check(T0 + 5 * CHECK, None, error=error)
        self.assertEqual(len(self.check(T0 + 6 * CHECK, None, error=error)), 1)

    def test_one_failed_query_says_nothing(self) -> None:
        self.assertEqual(self.check(T0, None, error="timed out"), [])
        self.assertEqual(self.check(T0 + CHECK, 3 * MIN), [])

    def test_a_huge_error_still_fits(self) -> None:
        self.check(T0, None, error="x" * 5000)
        sent = self.check(T0 + CHECK, None, error="x" * 5000)
        self.assertEqual(len(sent), 1)
        self.assert_bounded(sent[0])

    def test_the_query_is_bounded(self) -> None:
        fake = Path(self.temp_dir.name) / "tq"
        fake.write_text("#!/bin/sh\nexec sleep 30\n")
        fake.chmod(0o755)
        started = time.monotonic()
        with self.assertRaises(WATCH.StoreError) as caught:
            WATCH.query_store(HOST, timeout=1.0, tq=str(fake))
        self.assertLess(time.monotonic() - started, 10)
        self.assertIn("timed out", str(caught.exception))

    def test_a_failing_tq_names_its_error(self) -> None:
        fake = Path(self.temp_dir.name) / "tq"
        fake.write_text(
            "#!/bin/sh\necho 'tq: ClickHouse 500: Code: 241. MEMORY_LIMIT_EXCEEDED' >&2\nexit 2\n"
        )
        fake.chmod(0o755)
        with self.assertRaises(WATCH.StoreError) as caught:
            WATCH.query_store(HOST, timeout=5.0, tq=str(fake))
        self.assertIn("MEMORY_LIMIT_EXCEEDED", str(caught.exception))

    def test_tq_rows_are_read(self) -> None:
        fake = Path(self.temp_dir.name) / "tq"
        fake.write_text(
            '#!/bin/sh\necho \'{"newest":1793671002.401,"last_load":1793672377.515}\'\n'
            "echo 'tq: warning: last load from x 3 h ago' >&2\n"
        )
        fake.chmod(0o755)
        self.assertEqual(
            WATCH.query_store(HOST, timeout=5.0, tq=str(fake)),
            {"newest": 1793671002.401, "last_load": 1793672377.515},
        )

    def test_a_host_name_that_is_not_a_host_is_refused(self) -> None:
        with self.assertRaises(WATCH.StoreError):
            WATCH.query_store("x' OR 1=1 --", timeout=5.0, tq="/bin/false")


class EveryMessageFits(Harness):
    def test_an_alert_on_the_real_paths_names_the_importer_and_the_pause_file(
        self,
    ) -> None:
        """The longest host name DNS allows, on this box's state directory: the importer's state
        and where to pause it both fit; the advice is what gets dropped."""
        long_host = "h" * 63
        self.state_dir = Path("/mnt/nvme/sami/tmp/transcript-lag-watch")
        global HOST
        saved, HOST = HOST, long_host
        try:
            self.units = units(timer_active="inactive", timer_sub="dead", status="75")
            run = WATCH.Run(
                WATCH.build_parser().parse_args(
                    ["--state-dir", str(self.state_dir), "--host", long_host]
                ),
                long_host,
                T0,
                self.units,
                {"exists": False, "active": False, "until": None, "reason": ""},
                {
                    "over": 2,
                    "episode": {
                        "since": T0,
                        "told_at": None,
                        "told_lag": None,
                        "said": [],
                    },
                },
            )
            run.judge_lag({"newest": T0 - 400 * MIN, "last_load": T0 - 400 * MIN})
        finally:
            HOST = saved
        self.assertEqual(len(run.notices), 1)
        text = run.notices[0]["text"]
        self.assertLessEqual(len(text), 600)
        self.assertIn("transcript-import.timer inactive (dead)", text)
        self.assertIn("exit 75", text)
        self.assertIn("/mnt/nvme/sami/tmp/transcript-lag-watch/paused", text)
        self.assertNotIn("tq cannot see", text)  # the only part there was no room for
        self.assertTrue(
            text.endswith(
                "/mnt/nvme/sami/tmp/transcript-lag-watch/reports/20261103T003000Z-lag.txt"
            ),
            text,
        )

    def test_a_message_with_no_room_keeps_the_pause_file_over_the_advice(self) -> None:
        head, tail = "H" * 570, " Full report: /x"
        self.assertEqual(
            WATCH.fit(head, [(2, " advice."), (1, " pause file.")], tail),
            head + " pause file." + tail,
        )
        self.assertEqual(
            WATCH.fit("H" * 601, [(1, " pause.")], " T"), "H" * 597 + "…" + " T"
        )

    def test_every_kind_is_bounded_and_names_its_report(self) -> None:
        long_host = "h" * 63
        global HOST
        saved, HOST = HOST, long_host
        try:
            self.units = units(
                result="exit-code",
                status="1",
                timer_active="inactive",
                timer_sub="dead",
            )
            texts = []
            texts += self.check(T0, 400 * MIN)
            texts += self.check(T0 + CHECK, 400 * MIN)  # alert
            self.units = units(result="timeout", status="15")
            texts += self.check(
                T0 + 2 * CHECK, 900 * MIN
            )  # doubled and importer changed
            texts += self.check(T0 + 3 * CHECK, 2 * MIN)  # clear
            texts += self.check(T0 + 4 * CHECK, None, error="e" * 900)
            texts += self.check(T0 + 5 * CHECK, None, error="e" * 900)  # store error
            texts += self.check(T0 + 6 * CHECK, 2 * MIN)  # store back
        finally:
            HOST = saved
        self.assertEqual(len(texts), 5, texts)
        for text in texts:
            self.assert_bounded(text)
            self.assertIn(long_host, text)

    def test_old_reports_are_pruned(self) -> None:
        reports = self.state_dir / "reports"
        reports.mkdir(parents=True)
        old = reports / "20261020T000000Z-lag.txt"
        old.write_text("old")
        os.utime(old, (T0 - 8 * 86400, T0 - 8 * 86400))
        recent = reports / "20261102T000000Z-lag.txt"
        recent.write_text("recent")
        os.utime(recent, (T0 - 86400, T0 - 86400))
        self.check(T0, 3 * MIN)
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())


class Routing(Harness):
    def alert(self, *args: str) -> None:
        self.check(T0, 31 * MIN, *args)
        self.check(T0 + CHECK, 31 * MIN, *args)

    def test_the_box_health_role_hears_it(self) -> None:
        self.alert()
        self.assertEqual(
            [topic for topic, _ in self.envoy.published], [WATCH.BOX_HEALTH_ROLE_TOPIC]
        )
        self.assertEqual(self.envoy.sent, [])

    def test_an_unheld_role_falls_back_to_the_sre(self) -> None:
        FakeEnvoy.publish_ok = False
        self.alert()
        self.assertEqual([target for target, _ in self.envoy.sent], [WATCH.SRE])

    def test_box_health_owner_hears_it_alone(self) -> None:
        owner = "01a19999-0000-7000-8000-000000000001"
        with unittest.mock.patch.dict(os.environ, {"BOX_HEALTH_OWNER": owner}):
            self.alert()
        self.assertEqual(self.envoy.published, [])
        self.assertEqual([target for target, _ in self.envoy.sent], [owner])

    def test_dry_run_sends_nothing_and_writes_nothing(self) -> None:
        self.alert("--dry-run")
        self.assertEqual(self.envoy.published + self.envoy.sent, [])
        self.assertFalse((self.state_dir / "state.json").exists())
        self.assertFalse((self.state_dir / "reports").exists())

    def test_dry_run_with_write_state_chains_checks_without_sending(self) -> None:
        with unittest.mock.patch("builtins.print") as printed:
            self.alert("--dry-run", "--write-state")
        self.assertEqual(self.envoy.published + self.envoy.sent, [])
        shown = "\n".join(
            str(call.args[0]) for call in printed.call_args_list if call.args
        )
        self.assertIn("TRANSCRIPT STORE BEHIND", shown)


class NotAnImportingHost(Harness):
    def test_no_importer_and_no_store_config_is_quiet_and_queries_nothing(self) -> None:
        self.units = units(load_state="not-found")
        with unittest.mock.patch.object(WATCH, "store_host", return_value=None):
            options = WATCH.build_parser().parse_args(
                ["--state-dir", str(self.state_dir), "--lag-min", "30"]
            )
            envoy = FakeEnvoy()
            for step in range(3):
                WATCH.run_once(
                    options,
                    store=lambda host, timeout: self.fail("queried the store"),
                    importer=lambda: self.units,
                    journal=lambda: "",
                    envoy=envoy,
                    now=T0 + step * CHECK,
                )
        self.assertEqual(envoy.published + envoy.sent, [])


class Parsing(unittest.TestCase):
    def test_the_state_dir_is_the_big_disk_where_there_is_one(self) -> None:
        with tempfile.TemporaryDirectory() as big:
            self.assertEqual(
                WATCH.default_state_dir(Path(big)), Path(big) / "transcript-lag-watch"
            )
        self.assertEqual(
            WATCH.default_state_dir(Path("/nonexistent-disk/sami/tmp")),
            Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
            / "transcript-lag-watch",
        )

    def test_importer_summary_names_a_busy_lock(self) -> None:
        line, key = WATCH.importer_summary(units(status="75"), T0)
        self.assertIn("exit 75 (lock busy", line)
        self.assertEqual(key, WATCH.importer_summary(units(), T0)[1])

    def test_importer_summary_of_a_stopped_timer_and_a_killed_run(self) -> None:
        line, _ = WATCH.importer_summary(
            units(
                timer_active="inactive", timer_sub="dead", result="signal", status="15"
            ),
            T0,
        )
        self.assertEqual(
            line,
            "transcript-import.timer inactive (dead); last run signal, exit 15, ended 2026-11-03T00:16Z",
        )

    def test_systemctl_show_of_two_units(self) -> None:
        text = (
            "Result=success\nExecMainStartTimestamp=@1793673523\nExecMainExitTimestamp=\n"
            "ExecMainStatus=0\nId=transcript-import.service\nLoadState=loaded\n"
            "ActiveState=activating\nSubState=start\nUnitFileState=static\n\n"
            "NextElapseUSecRealtime=\nLastTriggerUSec=Tue 2026-11-03 02:38:43 UTC\nResult=success\n"
            "Id=transcript-import.timer\nLoadState=loaded\nActiveState=active\nSubState=running\n"
            "UnitFileState=enabled\n"
        )
        parsed = WATCH.parse_show(text)
        self.assertEqual(parsed[WATCH.IMPORT_SERVICE]["ActiveState"], "activating")
        self.assertEqual(
            parsed[WATCH.IMPORT_SERVICE]["ExecMainStartTimestamp"], "@1793673523"
        )
        self.assertEqual(parsed[WATCH.IMPORT_TIMER]["SubState"], "running")
        self.assertEqual(WATCH.parse_when("@1793673523"), 1793673523.0)
        self.assertEqual(WATCH.parse_when("Tue 2026-11-03 02:38:43 UTC"), 1793673523.0)
        self.assertIsNone(WATCH.parse_when(""))


if __name__ == "__main__":
    unittest.main()
