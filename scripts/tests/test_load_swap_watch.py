#!/usr/bin/env python3
"""Regression tests for load-swap-watch's decisions, attribution and alert routing.

The box-health lead owns box resources (dispatch://INFRA-716), so a crossing goes to that Envoy
role, and to the INFRA lead's session only when the role is held by nobody.
"""

import argparse
import importlib.machinery
import importlib.util
import tempfile
import time
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "load-swap-watch"
LOADER = importlib.machinery.SourceFileLoader("load_swap_watch", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
WATCH = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(WATCH)

GIB = WATCH.GIB


class FakeEnvoy:
    """Records what left, and can answer non-200 for an unheld role."""

    publish_ok = True

    def __init__(self, timeout: float = 8.0) -> None:
        self.published: list[tuple[str, str]] = []
        self.sent: list[tuple[str, str]] = []

    def publish(self, topic: str, message: str) -> bool:
        self.published.append((topic, message))
        return self.publish_ok

    def send(self, session_id: str, message: str) -> bool:
        self.sent.append((session_id, message))
        return True


def options(state: Path, **overrides) -> argparse.Namespace:
    values = {
        "cpu_stall": 50.0,
        "queue_per_core": 3.0,
        "swap_used": 0.85,
        "mem_floor": 8 * GIB,
        "horizon": 7200.0,
        "rate_window": 3600.0,
        "grace": 600.0,
        "escalate": 1800.0,
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


def box(**overrides) -> dict:
    """A 32-core box with room to spare, as the incident box reads when it is healthy."""
    sample = {
        "at": time.time(),
        "cores": 32,
        "cpu": {"some_avg10": 6.0, "some_avg60": 6.0, "some_avg300": 8.0},
        "io": {"some_avg60": 11.0},
        "memory_pressure": {"full_avg60": 0.1},
        "load1": 30.0,
        "load5": 43.0,
        "load15": 80.0,
        "procs_running": 18,
        "procs_blocked": 3,
        "swap_total": 192 * GIB,
        "swap_free": 100 * GIB,
        "swap_used_fraction": 1 - 100 / 192,
        "mem_available": 129 * GIB,
        "mem_total": 252 * GIB,
        "pswpin": 0,
    }
    sample.update(overrides)
    return sample


def starved() -> dict:
    """ossv-agent-open as measured at 23:55Z on 2026-10-29."""
    return box(
        cpu={"some_avg10": 80.5, "some_avg60": 80.1, "some_avg300": 74.1},
        io={"some_avg60": 23.5},
        memory_pressure={"full_avg60": 0.23},
        load1=251.0,
        load5=185.0,
        load15=135.0,
        procs_running=84,
        procs_blocked=24,
        swap_free=515 * 1024 * 1024,
        swap_used_fraction=1 - (515 * 1024 * 1024) / (192 * GIB),
        mem_available=127 * GIB,
    )


class VerdictTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state.json"

    def test_healthy_box_is_not_a_crossing(self) -> None:
        found = WATCH.verdict(box(), [], options(self.state))
        self.assertFalse(found["crossing"])
        self.assertEqual(found["reasons"], [])

    def test_the_incident_fires_on_cpu_and_swap(self) -> None:
        found = WATCH.verdict(starved(), [], options(self.state))
        self.assertTrue(found["crossing"])
        self.assertEqual(found["kinds"], ["cpu", "swap"])
        self.assertIn("cpu pressure some avg60 is 80%", found["reasons"][0])
        self.assertIn("99.7% used", found["reasons"][1])

    def test_load_alone_does_not_fire(self) -> None:
        """Load 221 of uninterruptible sleep is a disk problem, and this watch is not it."""
        blocked = box(load1=221.0, procs_running=4, procs_blocked=217, io={"some_avg60": 90.0})
        found = WATCH.verdict(blocked, [], options(self.state))
        self.assertFalse(found["crossing"])

    def test_run_queue_is_the_fallback_without_psi(self) -> None:
        found = WATCH.verdict(box(cpu={}, procs_running=120), [], options(self.state))
        self.assertTrue(found["crossing"])
        self.assertIn("no PSI on this kernel", found["reasons"][0])

    def test_psi_present_means_the_run_queue_never_decides(self) -> None:
        found = WATCH.verdict(box(procs_running=120), [], options(self.state))
        self.assertFalse(found["crossing"])

    def test_draining_swap_fires_before_it_is_full(self) -> None:
        now = time.time()
        sample = box(swap_free=20 * GIB, swap_used_fraction=1 - 20 / 192)
        sample["at"] = now
        samples = [[now - 3600, 60 * GIB], [now, 20 * GIB]]
        found = WATCH.verdict(sample, samples, options(self.state))
        self.assertTrue(found["crossing"])
        self.assertEqual(found["kinds"], ["swap"])
        self.assertLess(found["time_to_exhaustion"], 7200)

    def test_swap_filling_slowly_does_not_fire(self) -> None:
        now = time.time()
        sample = box(swap_free=100 * GIB)
        sample["at"] = now
        samples = [[now - 3600, 101 * GIB], [now, 100 * GIB]]
        found = WATCH.verdict(sample, samples, options(self.state))
        self.assertFalse(found["crossing"])

    def test_memory_floor_fires(self) -> None:
        found = WATCH.verdict(box(mem_available=2 * GIB), [], options(self.state))
        self.assertEqual(found["kinds"], ["memory"])

    def test_clear_needs_hysteresis(self) -> None:
        """Just under each rule is not clear; 0.7 of it is."""
        edge = box(cpu={"some_avg60": 49.0}, swap_free=30 * GIB, swap_used_fraction=1 - 30 / 192)
        self.assertFalse(WATCH.clears(edge, options(self.state)))
        self.assertTrue(WATCH.clears(box(), options(self.state)))


class RoutingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state.json"
        self.envoy = FakeEnvoy()
        self.sample = starved()

        original_envoy = WATCH.Envoy
        original_sample = WATCH.sample_box
        original_consumers = WATCH.consumers_text
        WATCH.Envoy = lambda timeout=8.0: self.envoy
        WATCH.sample_box = lambda: dict(self.sample, at=time.time())
        WATCH.consumers_text = lambda options: "consumers: (none in this test)"

        def restore() -> None:
            WATCH.Envoy = original_envoy
            WATCH.sample_box = original_sample
            WATCH.consumers_text = original_consumers

        self.addCleanup(restore)

    def test_grace_holds_the_first_run_quiet(self) -> None:
        WATCH.run_once(options(self.state))
        self.assertEqual(self.envoy.published, [])
        self.assertEqual(self.envoy.sent, [])

    def test_alert_goes_to_the_box_health_role_once_grace_passes(self) -> None:
        WATCH.run_once(options(self.state, grace=0.0))
        self.assertEqual(len(self.envoy.published), 1)
        topic, text = self.envoy.published[0]
        self.assertEqual(topic, WATCH.BOX_HEALTH_ROLE_TOPIC)
        self.assertIn("BOX OUT OF CPU+SWAP", text)
        self.assertIn("a CPU shortage", text)
        self.assertIn("Do not restart an agent on this signal", text)
        self.assertIn("kills nothing", text)

    def test_one_alert_per_crossing(self) -> None:
        WATCH.run_once(options(self.state, grace=0.0))
        WATCH.run_once(options(self.state, grace=0.0))
        self.assertEqual(len(self.envoy.published), 1)

    def test_an_unheld_role_falls_back_to_the_infra_lead(self) -> None:
        self.envoy.publish_ok = False
        WATCH.run_once(options(self.state, grace=0.0))
        self.assertEqual([session for session, _ in self.envoy.sent], [WATCH.INFRA_LEAD])

    def test_a_crossing_still_open_escalates_to_the_chief_of_staff(self) -> None:
        WATCH.run_once(options(self.state, grace=0.0))
        WATCH.run_once(options(self.state, grace=0.0, escalate=0.0))
        self.assertEqual([session for session, _ in self.envoy.sent], [WATCH.CHIEF_OF_STAFF])
        self.assertIn("STILL OUT OF", self.envoy.sent[0][1])

    def test_recovery_clears_and_says_so(self) -> None:
        WATCH.run_once(options(self.state, grace=0.0))
        self.sample = box()
        WATCH.run_once(options(self.state, grace=0.0))
        self.assertEqual(len(self.envoy.published), 2)
        self.assertIn("CLEARED", self.envoy.published[1][1])

    def test_dry_run_sends_nothing(self) -> None:
        WATCH.run_once(options(self.state, grace=0.0, dry_run=True))
        self.assertEqual(self.envoy.published, [])
        self.assertEqual(self.envoy.sent, [])


class ConsumerAttributionTest(unittest.TestCase):
    def test_by_command_totals_and_counts(self) -> None:
        rows = [
            {"pid": 1, "cores": 2.0, "comm": "omp"},
            {"pid": 2, "cores": 1.0, "comm": "omp"},
            {"pid": 3, "cores": 1.5, "comm": "git"},
        ]
        self.assertEqual(WATCH.by_command(rows, "cores"), [("omp", 3.0, 2), ("git", 1.5, 1)])

    def test_worker_name_comes_from_a_brief_path(self) -> None:
        self.assertEqual(
            WATCH.WORKER_RE.search("omp --config x /home/sami/work/cold/workers/c-libc-str.md").group(1),
            "c-libc-str",
        )

    def test_worker_name_comes_from_a_working_directory(self) -> None:
        self.assertEqual(
            WATCH.WORKER_RE.search("/home/sami/work/workers/g-ph-hreply/").group(1), "g-ph-hreply"
        )

    def test_this_process_is_described_without_a_lane(self) -> None:
        import os

        described = WATCH.describe(os.getpid())
        self.assertIn("up ", described)

    def test_swap_drain_rate_is_bytes_per_second_of_fall(self) -> None:
        now = time.time()
        rate, window = WATCH.swap_drain_rate([[now - 100, 300], [now, 100]], 3600)
        self.assertAlmostEqual(rate, 2.0, places=3)
        self.assertAlmostEqual(window, 100, places=0)

    def test_swap_drain_rate_needs_two_samples(self) -> None:
        self.assertEqual(WATCH.swap_drain_rate([[time.time(), 1]], 3600), (None, 0.0))


if __name__ == "__main__":
    unittest.main()
