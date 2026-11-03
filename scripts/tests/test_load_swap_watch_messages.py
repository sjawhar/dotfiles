#!/usr/bin/env python3
"""Regression tests for how many messages load-swap-watch sends, and what each one holds.

The review of 2026-11-03 (INFRA-672 notify-review.md, "load-swap-watch") measured 28 messages in
48 h, 14 a day. These tests hold the four changes it asked for:

- no CPU clear while the box is stalled: `BOX SHORT OF CPU CLEARED` went out on sami-rocqperf at
  2026-11-02T10:25Z with load 851 on 64 cores and 723 tasks blocked, and at 2026-11-03T00:32Z and
  00:36Z with 329 and 167 blocked. `cpu some` fell because the tasks were waiting on the disk,
  not because they had a CPU;
- one message per run, with one verdict line per condition: 2026-11-01T16:36Z sent CPU and SWAP
  as two 5.6-6 KB messages in the same minute with the same load line and consumer table;
- no CPU alerts on sami-leanperf, the 160-core benchmark box: 7 messages, 0 reactions;
- a STILL message only on a new fact, never because 30 minutes passed;
- every message at most 600 characters and naming its report file.

Nothing here reads a real /proc or a real Envoy.
"""

import argparse
import importlib.machinery
import importlib.util
import os
import re
import tempfile
import time
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "load-swap-watch"
LOADER = importlib.machinery.SourceFileLoader("load_swap_watch_messages", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
WATCH = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(WATCH)

GIB = WATCH.GIB
REPORT_RE = re.compile(r"\nFull report: (\S+)\Z")


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

    def request(self, method: str, path: str, body=None):
        return 200, []


def options(state: Path, **overrides) -> argparse.Namespace:
    values = {
        "cpu_stall": WATCH.CPU_STALL,
        "cpu_rule": True,
        "io_hold": 50.0,
        "queue_per_core": 3.0,
        "swap_used": 0.85,
        "mem_floor": 8 * GIB,
        "mem_floor_fraction": 0.10,
        "mem_floor_cap": 0.25,
        "horizon": 7200.0,
        "rate_window": 3600.0,
        "grace": 0.0,
        "recheck": 1800.0,
        "clear_factor": 0.7,
        "sample_seconds": 0.01,
        "top": 5,
        "state": state,
        "http_timeout": 8.0,
        "dry_run": False,
        "write_state": False,
        "print_alert": False,
        "consumers": False,
        "json": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def rocq(**overrides) -> dict:
    """sami-rocqperf as it reads most of the day: 64 cores carrying load ~300, short of CPU."""
    sample = {
        "at": time.time(),
        "cores": 64,
        "cpu": {"some_avg60": 58.0},
        "io": {"some_avg60": 18.0},
        "memory_pressure": {"full_avg60": 2.5},
        "load1": 349.0,
        "load5": 286.0,
        "load15": 159.0,
        "procs_running": 130,
        "procs_blocked": 12,
        "swap_total": 112 * GIB,
        "swap_free": 80 * GIB,
        "swap_used_fraction": 1 - 80 / 112,
        "mem_available": 58 * GIB,
        "mem_total": 252 * GIB,
        "pswpin": 0,
    }
    sample.update(overrides)
    return sample


def quiet(**overrides) -> dict:
    """The same box at 2026-11-01T12:45Z, when its CPU crossing really did clear."""
    return rocq(
        **{
            "cpu": {"some_avg60": 18.0},
            "io": {"some_avg60": 6.0},
            "memory_pressure": {"full_avg60": 0.4},
            "load1": 79.0,
            "load5": 171.0,
            "load15": 283.0,
            "procs_running": 74,
            "procs_blocked": 11,
            **overrides,
        }
    )


def ranked(cpu: str = "omp", swap: str = "omp", memory: str = "omp") -> dict:
    return {
        "ranked": {
            "cpu": [(cpu, 31.2, 412), ("git", 2.0, 40)],
            "swap": [(swap, 30 * GIB, 120)],
            "memory": [(memory, 108 * GIB, 126)],
        },
        "cpu_rows": [],
        "mem_rows": [],
        "span": 10.0,
    }


class Base(unittest.TestCase):
    HOST = "sami-rocqperf"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state.json"
        self.envoy = FakeEnvoy()
        self.sample = rocq()
        self.top = ranked()
        self.measured = 0

        def measure(options):
            self.measured += 1
            return self.top

        patches = {
            "Envoy": lambda timeout=8.0: self.envoy,
            "sample_box": lambda: dict(self.sample, at=time.time()),
            "measure_consumers": measure,
            "consumers_text": lambda options, envoy=None, measured=None: "CONSUMER TABLE: omp 31.2 cores, pid 4242 ...",
        }
        for name, replacement in patches.items():
            original = getattr(WATCH, name, None)
            setattr(WATCH, name, replacement)
            self.addCleanup(lambda n=name, o=original: setattr(WATCH, n, o) if o else delattr(WATCH, n))
        hostname = WATCH.socket.gethostname
        WATCH.socket.gethostname = lambda: self.HOST
        self.addCleanup(lambda: setattr(WATCH.socket, "gethostname", hostname))

    def run_watch(self, **overrides) -> None:
        WATCH.run_once(options(self.state, **overrides))

    def messages(self) -> list[str]:
        return [text for _, text in self.envoy.published] + [text for _, text in self.envoy.sent]


class CpuClearTest(Base):
    def test_no_cpu_clear_with_723_tasks_blocked_on_64_cores(self) -> None:
        """2026-11-02T10:25Z: load 851/598/449, 97 runnable, 723 blocked, cpu some 19%, io some 94%."""
        self.run_watch()
        self.sample = rocq(
            cpu={"some_avg60": 19.0}, io={"some_avg60": 94.0}, memory_pressure={"full_avg60": 8.5},
            load1=851.0, load5=598.0, load15=449.0, procs_running=97, procs_blocked=723,
        )
        self.run_watch()
        self.assertEqual(1, len(self.messages()))
        self.assertNotIn("CLEARED", self.messages()[0])

    def test_blocked_over_cores_holds_the_clear_even_with_io_low(self) -> None:
        stalled = quiet(procs_blocked=167, io={"some_avg60": 10.0})
        self.assertFalse(WATCH.clears("cpu", stalled, options(self.state)))

    def test_high_io_holds_the_clear(self) -> None:
        """2026-11-03T01:39Z: 11 blocked, cpu some 20%, io some 68%; the crossing re-opened 8 min later."""
        stalled = quiet(load1=240.0, procs_blocked=11, cpu={"some_avg60": 20.0}, io={"some_avg60": 68.0})
        self.assertFalse(WATCH.clears("cpu", stalled, options(self.state)))

    def test_a_quiet_box_still_clears(self) -> None:
        self.run_watch()
        self.sample = quiet()
        self.run_watch()
        cleared = [text for text in self.messages() if "BOX SHORT OF CPU CLEARED" in text]
        self.assertEqual(1, len(cleared))

    def test_a_load_that_fits_on_the_cores_clears_through_any_stall(self) -> None:
        """The hold must not keep a crossing open for ever: this box has work for 8 of 64 cores,
        so it is short of nothing, whatever its disk is doing."""
        idle = quiet(load1=8.0, load5=20.0, procs_running=6, procs_blocked=400, io={"some_avg60": 95.0})
        self.assertTrue(WATCH.clears("cpu", idle, options(self.state)))


class OneMessagePerRunTest(Base):
    def test_cpu_and_memory_opened_in_one_run_are_one_message_with_two_verdicts(self) -> None:
        self.sample = rocq(mem_available=18 * GIB)
        self.run_watch()
        self.assertEqual(1, len(self.messages()))
        lines = self.messages()[0].splitlines()
        cpu = [line for line in lines if line.startswith("BOX SHORT OF CPU")]
        memory = [line for line in lines if line.startswith("BOX SHORT OF MEMORY")]
        self.assertEqual(1, len(cpu))
        self.assertEqual(1, len(memory))
        # Each verdict carries its own evidence, and no line names both conditions.
        self.assertIn("cpu some avg60 58%", cpu[0])
        self.assertIn("MemAvailable 18.0 GiB", memory[0])
        self.assertFalse([line for line in lines if "CPU" in line and "MEMORY" in line])
        self.assertNotIn("CPU+", self.messages()[0])
        # The first line is a verdict naming the box, not a count of them: the decision is in the
        # first ~200 characters, which is all a reader sees in a notification.
        self.assertEqual(cpu[0], lines[0])
        self.assertIn("on sami-rocqperf", lines[0])

    def test_a_clear_and_a_new_verdict_in_one_run_are_one_message_led_by_the_verdict(self) -> None:
        self.run_watch()
        self.sample = quiet(mem_available=18 * GIB)
        self.run_watch()
        self.assertEqual(2, len(self.messages()))
        lines = self.messages()[1].splitlines()
        self.assertTrue(lines[0].startswith("BOX SHORT OF MEMORY on sami-rocqperf:"), lines[0])
        self.assertTrue([line for line in lines if line.startswith("BOX SHORT OF CPU CLEARED")])

    def test_an_unheld_role_sends_the_one_message_to_the_sre(self) -> None:
        self.envoy.publish_ok = False
        self.sample = rocq(mem_available=18 * GIB, swap_free=5 * GIB, swap_used_fraction=1 - 5 / 112)
        self.run_watch()
        self.assertEqual([WATCH.SRE], [session for session, _ in self.envoy.sent])


class LeanperfTest(Base):
    HOST = "sami-leanperf"

    def leanperf(self, **overrides) -> dict:
        """sami-leanperf at 2026-11-01T13:13Z: a benchmark at load 195 on 160 cores, cpu some 80%."""
        values = {
            "cores": 160, "load1": 195.0, "load5": 182.0, "load15": 115.0, "cpu": {"some_avg60": 80.0},
            "procs_running": 201, "procs_blocked": 4, "swap_total": 0, "swap_free": 0,
            "swap_used_fraction": 0.0, "mem_total": 630 * GIB, "mem_available": 500 * GIB,
        }
        values.update(overrides)
        return rocq(**values)

    def parsed(self) -> argparse.Namespace:
        return WATCH.parse_args(["--state", str(self.state)])

    def test_the_cpu_rule_is_off_on_leanperf(self) -> None:
        self.assertFalse(self.parsed().cpu_rule)

    def test_the_cpu_rule_is_on_elsewhere(self) -> None:
        WATCH.socket.gethostname = lambda: "sami-rocqperf"
        self.assertTrue(self.parsed().cpu_rule)

    def test_leanperf_at_full_cpu_sends_nothing(self) -> None:
        self.sample = self.leanperf()
        WATCH.run_once(self.parsed_with_no_grace())
        WATCH.run_once(self.parsed_with_no_grace())
        self.assertEqual([], self.messages())

    def test_leanperf_still_says_memory(self) -> None:
        self.sample = self.leanperf(mem_available=20 * GIB)
        WATCH.run_once(self.parsed_with_no_grace())
        self.assertEqual(1, len(self.messages()))
        self.assertIn("BOX SHORT OF MEMORY", self.messages()[0])
        self.assertNotIn("CPU", self.messages()[0].splitlines()[0])

    def test_an_open_cpu_crossing_on_leanperf_is_dropped_without_a_clear(self) -> None:
        self.sample = self.leanperf()
        WATCH.run_once(options(self.state, cpu_rule=True))
        self.assertEqual(1, len(self.messages()))
        WATCH.run_once(self.parsed_with_no_grace())
        self.assertEqual(1, len(self.messages()))

    def parsed_with_no_grace(self) -> argparse.Namespace:
        return WATCH.parse_args(["--state", str(self.state), "--grace", "0", "--sample-seconds", "0.01"])


class StillTest(Base):
    def test_a_repeat_with_no_new_fact_sends_nothing(self) -> None:
        self.run_watch()
        self.run_watch(recheck=0.0)
        self.run_watch(recheck=0.0)
        self.assertEqual(1, len(self.messages()))

    def test_thirty_minutes_alone_is_not_a_new_fact(self) -> None:
        self.run_watch()
        state = WATCH.json.loads(self.state.read_text())
        for incident in state["incidents"].values():
            for key in ("opened_at", "alerted_at", "checked_at"):
                if incident.get(key):
                    incident[key] -= 3600
            if incident.get("told"):
                incident["told"]["at"] -= 3600
        self.state.write_text(WATCH.json.dumps(state))
        self.run_watch()
        self.assertEqual(1, len(self.messages()))

    def test_load_doubled_is_a_new_fact(self) -> None:
        self.run_watch()
        self.sample = rocq(load1=720.0, load5=500.0, load15=300.0)
        self.run_watch()
        self.assertEqual(2, len(self.messages()))
        first = self.messages()[1].splitlines()[0]
        self.assertTrue(first.startswith("STILL SHORT OF CPU, WORSE"), first)
        self.assertIn("load 720 (was 349)", self.messages()[1])

    def test_blocked_doubled_past_the_cores_is_a_new_fact(self) -> None:
        self.run_watch()
        self.sample = rocq(procs_blocked=160)
        self.run_watch()
        self.assertEqual(2, len(self.messages()))
        self.assertIn("160 blocked (was 12)", self.messages()[1])

    def test_blocked_doubled_under_the_cores_is_not(self) -> None:
        self.run_watch()
        self.sample = rocq(procs_blocked=30)
        self.run_watch()
        self.assertEqual(1, len(self.messages()))

    def test_a_new_fact_is_measured_from_the_last_message_not_the_first(self) -> None:
        self.run_watch()
        self.sample = rocq(load1=720.0)
        self.run_watch()
        self.sample = rocq(load1=1000.0)
        self.run_watch()
        self.assertEqual(2, len(self.messages()))

    def test_a_level_already_said_is_not_news_again(self) -> None:
        """sami-rocqperf, 2026-11-02: 369 blocked was said at 00:20Z; the load doubled at 08:10Z
        while 67 were blocked; 182 blocked at 09:16Z doubles 67 but is half of what was already
        said, so it is a reading, not news. The old code sent it."""
        self.run_watch()
        self.sample = rocq(procs_blocked=369)
        self.run_watch()
        self.sample = rocq(load1=720.0, procs_blocked=67)
        self.run_watch()
        self.assertEqual(3, len(self.messages()))
        self.sample = rocq(load1=720.0, procs_blocked=182)
        self.run_watch()
        self.assertEqual(3, len(self.messages()))

    def test_past_the_worst_already_said_is_news(self) -> None:
        self.run_watch()
        self.sample = rocq(procs_blocked=369)
        self.run_watch()
        self.sample = rocq(procs_blocked=67)
        self.run_watch()
        self.sample = rocq(procs_blocked=800)
        self.run_watch()
        self.assertEqual(3, len(self.messages()))
        self.assertIn("800 blocked (was 369)", self.messages()[2])

    def test_the_ratchet_holds_for_memory_too(self) -> None:
        self.sample = rocq(mem_available=20 * GIB)
        self.run_watch()
        self.sample = rocq(mem_available=8 * GIB)
        self.run_watch()
        self.sample = rocq(mem_available=22 * GIB)
        self.run_watch()
        self.sample = rocq(mem_available=5 * GIB)
        self.run_watch()
        self.assertEqual(2, len(self.messages()))

    def test_a_new_top_consumer_is_a_new_fact(self) -> None:
        self.run_watch()
        self.top = ranked(cpu="lean")
        self.run_watch(recheck=0.0)
        self.assertEqual(2, len(self.messages()))
        self.assertIn("lean", self.messages()[1].splitlines()[0])
        self.run_watch(recheck=0.0)
        self.assertEqual(2, len(self.messages()))

    def test_consumers_are_not_resampled_before_the_recheck_interval(self) -> None:
        self.run_watch()
        before = self.measured
        self.top = ranked(cpu="lean")
        self.run_watch()
        self.assertEqual(before, self.measured)
        self.assertEqual(1, len(self.messages()))

    def test_swap_free_halved_is_a_new_fact(self) -> None:
        self.sample = rocq(swap_free=14 * GIB, swap_used_fraction=1 - 14 / 112)
        self.run_watch()
        self.sample = rocq(swap_free=5 * GIB, swap_used_fraction=1 - 5 / 112)
        self.run_watch()
        self.assertEqual(2, len(self.messages()))
        self.assertTrue([line for line in self.messages()[1].splitlines() if line.startswith("STILL OUT OF SWAP, WORSE")])

    def test_memory_halved_is_a_new_fact(self) -> None:
        self.sample = rocq(mem_available=20 * GIB)
        self.run_watch()
        self.sample = rocq(mem_available=8 * GIB)
        self.run_watch()
        self.assertEqual(2, len(self.messages()))
        self.assertTrue([line for line in self.messages()[1].splitlines() if line.startswith("STILL SHORT OF MEMORY, WORSE")])

    def test_an_alert_from_before_the_upgrade_is_not_re_sent(self) -> None:
        """A state file written by the old code has no baseline: seed it, say nothing."""
        old = {
            "incidents": {
                "cpu": {
                    "opened_at": time.time() - 7200, "alerted_at": time.time() - 6600,
                    "escalated_at": None, "last_seen": time.time() - 120,
                }
            },
            "samples": [],
        }
        self.state.write_text(WATCH.json.dumps(old))
        self.run_watch()
        self.assertEqual([], self.messages())


class MessageSizeTest(Base):
    HOST = "ossv-agent-repo.us-east1-b.c.autoformalization.internal"

    def check(self, text: str) -> None:
        self.assertLessEqual(len(text), 600, text)
        found = REPORT_RE.search(text)
        self.assertIsNotNone(found, text)
        report = Path(found.group(1))
        self.assertTrue(report.is_file(), report)
        self.assertEqual(self.state.parent / "reports", report.parent)

    def test_every_message_of_a_full_cycle_is_short_and_names_its_report(self) -> None:
        # Three verdicts at once, the longest message this watch can write.
        self.sample = rocq(mem_available=18 * GIB, swap_free=5 * GIB, swap_used_fraction=1 - 5 / 112)
        self.run_watch()
        # All three worse, and a new top consumer for each.
        self.top = ranked(cpu="lean", swap="python3", memory="coqc")
        self.sample = rocq(
            load1=900.0, procs_blocked=400, io={"some_avg60": 30.0},
            mem_available=4 * GIB, swap_free=1 * GIB, swap_used_fraction=1 - 1 / 112,
        )
        self.run_watch(recheck=0.0)
        # All three clear.
        self.sample = quiet()
        self.run_watch()
        self.assertEqual(3, len(self.messages()))
        for text in self.messages():
            self.check(text)

    def test_the_report_holds_the_consumer_table(self) -> None:
        self.run_watch()
        report = Path(REPORT_RE.search(self.messages()[0]).group(1))
        body = report.read_text()
        self.assertIn("CONSUMER TABLE", body)
        self.assertIn("cpu pressure some avg60 is 58%", body)
        self.assertNotIn("CONSUMER TABLE", self.messages()[0])

    def test_reports_older_than_seven_days_are_pruned(self) -> None:
        reports = self.state.parent / "reports"
        reports.mkdir(parents=True)
        old = reports / "20261020T000000Z-alert.txt"
        recent = reports / "20261029T000000Z-alert.txt"
        for path, age in ((old, 8 * 86400), (recent, 6 * 86400)):
            path.write_text("x")
            os.utime(path, (time.time() - age, time.time() - age))
        self.run_watch()
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())

    def test_a_report_that_cannot_be_written_never_holds_back_the_message(self) -> None:
        """A full disk is one reason a box is in trouble; the message still has to go."""
        (self.state.parent / "reports").write_text("not a directory")
        self.run_watch()
        self.assertEqual(1, len(self.messages()))
        text = self.messages()[0]
        self.assertLessEqual(len(text), 600, text)
        self.assertTrue(text.endswith("Full report: could not be written (see the journal)"), text)


if __name__ == "__main__":
    unittest.main()
