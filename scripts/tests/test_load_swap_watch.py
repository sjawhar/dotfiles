#!/usr/bin/env python3
"""Regression tests for load-swap-watch's decisions, attribution and alert routing.

The box-health lead owns box resources (dispatch://INFRA-716), so a crossing goes to that Envoy
role, and to the INFRA lead's session only when the role is held by nobody.
"""

import argparse
import importlib.machinery
import importlib.util
import os
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "load-swap-watch"
LOADER = importlib.machinery.SourceFileLoader("load_swap_watch", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
WATCH = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(WATCH)

GIB = WATCH.GIB

# Neither of these may hear a box alert any more (Sami, 2026-10-31): the chief of staff's
# session, and the INFRA lead's, which owned this detector and handed box health to the SRE.
CHIEF_OF_STAFF = "01a13dca-e979-7264-af32-f654ef57a6f5"
INFRA_LEAD = "01a13c57-d826-7242-80fe-5f0670188b11"


class FakeEnvoy:
    """Records what left, and can answer non-200 for an unheld role or a refused send."""

    publish_ok = True
    send_ok = True

    def __init__(self, timeout: float = 8.0) -> None:
        self.published: list[tuple[str, str]] = []
        self.sent: list[tuple[str, str]] = []

    def publish(self, topic: str, message: str) -> bool:
        self.published.append((topic, message))
        return self.publish_ok

    def send(self, session_id: str, message: str) -> bool:
        self.sent.append((session_id, message))
        return self.send_ok


def options(state: Path, **overrides) -> argparse.Namespace:
    values = {
        "cpu_stall": WATCH.CPU_STALL,
        "queue_per_core": 3.0,
        "swap_used": 0.85,
        "mem_floor": 8 * GIB,
        "mem_floor_fraction": 0.10,
        "mem_floor_cap": 0.25,
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

    def test_the_incident_fires_on_cpu_and_swap_as_two_findings(self) -> None:
        found = WATCH.verdict(starved(), [], options(self.state))
        self.assertEqual(found["kinds"], ["cpu", "swap"])
        self.assertIn("cpu pressure some avg60 is 80%", found["findings"]["cpu"]["reasons"][0])
        self.assertIn("99.7% used", found["findings"]["swap"]["reasons"][0])

    def test_the_false_positive_of_01_32z_is_swap_only(self) -> None:
        """ossv-agent-open, 01:32Z: load 27 on 32 cores at 7% CPU pressure, swap gone."""
        working = box(
            load1=27.0,
            load5=30.0,
            load15=35.0,
            cpu={"some_avg60": 7.0},
            swap_free=0,
            swap_used_fraction=1.0,
        )
        found = WATCH.verdict(working, [], options(self.state))
        self.assertEqual(found["kinds"], ["swap"])

    def test_a_saturated_lane_box_fires_on_cpu(self) -> None:
        """ossv-agent-open2 at the same minute: load 76 on 32 cores at 55%."""
        found = WATCH.verdict(
            box(load1=76.0, cpu={"some_avg60": 55.0}), [], options(self.state)
        )
        self.assertIn("cpu", found["kinds"])

    def test_pressure_without_a_load_over_the_cores_is_not_a_cpu_shortage(self) -> None:
        found = WATCH.verdict(box(load1=20.0, cpu={"some_avg60": 60.0}), [], options(self.state))
        self.assertNotIn("cpu", found["kinds"])

    def test_load_alone_does_not_fire(self) -> None:
        """Load 221 of uninterruptible sleep is a disk problem, and this watch is not it."""
        blocked = box(load1=221.0, procs_running=4, procs_blocked=217, io={"some_avg60": 90.0})
        found = WATCH.verdict(blocked, [], options(self.state))
        self.assertFalse(found["crossing"])

    def test_run_queue_is_the_fallback_without_psi(self) -> None:
        found = WATCH.verdict(box(cpu={}, procs_running=120), [], options(self.state))
        self.assertTrue(found["crossing"])
        self.assertIn("no PSI on this kernel", found["findings"]["cpu"]["reasons"][0])

    def test_psi_present_means_the_run_queue_never_decides(self) -> None:
        found = WATCH.verdict(box(procs_running=120), [], options(self.state))
        self.assertFalse(found["crossing"])

    def test_swap_needs_no_cpu_condition_at_all(self) -> None:
        """Zero free swap is worth saying on an idle box: it is about the next squeeze."""
        idle = box(load1=0.2, cpu={"some_avg60": 0.0}, swap_free=0, swap_used_fraction=1.0)
        found = WATCH.verdict(idle, [], options(self.state))
        self.assertEqual(found["kinds"], ["swap"])

    def test_draining_swap_fires_before_it_is_full(self) -> None:
        now = time.time()
        sample = box(swap_free=20 * GIB, swap_used_fraction=1 - 20 / 192)
        sample["at"] = now
        samples = [[now - 3600, 60 * GIB], [now, 20 * GIB]]
        found = WATCH.verdict(sample, samples, options(self.state))
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

    def test_each_condition_clears_on_its_own_rule(self) -> None:
        """Just under a rule is not clear; 0.7 of it is, and a load back under the cores is."""
        edge = box(load1=40.0, cpu={"some_avg60": 30.0}, swap_free=30 * GIB, swap_used_fraction=1 - 30 / 192)
        self.assertFalse(WATCH.clears("cpu", edge, options(self.state)))
        self.assertFalse(WATCH.clears("swap", edge, options(self.state)))
        self.assertTrue(WATCH.clears("cpu", box(), options(self.state)))
        self.assertTrue(WATCH.clears("swap", box(), options(self.state)))

    def test_cpu_clears_when_the_load_fits_on_the_cores(self) -> None:
        fits = box(load1=20.0, cpu={"some_avg60": 60.0})
        self.assertTrue(WATCH.clears("cpu", fits, options(self.state)))


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
        WATCH.consumers_text = lambda options, envoy=None: "consumers: (none in this test)"

        def restore() -> None:
            WATCH.Envoy = original_envoy
            WATCH.sample_box = original_sample
            WATCH.consumers_text = original_consumers

        self.addCleanup(restore)

    def published(self, needle: str) -> list[str]:
        return [text for _, text in self.envoy.published if needle in text]

    def test_grace_holds_the_first_run_quiet(self) -> None:
        WATCH.run_once(options(self.state))
        self.assertEqual(self.envoy.published, [])
        self.assertEqual(self.envoy.sent, [])

    def test_each_condition_gets_its_own_message(self) -> None:
        WATCH.run_once(options(self.state, grace=0.0))
        self.assertEqual(len(self.envoy.published), 2)
        topics = {topic for topic, _ in self.envoy.published}
        self.assertEqual({WATCH.BOX_HEALTH_ROLE_TOPIC}, topics)
        cpu = self.published("BOX SHORT OF CPU")
        swap = self.published("BOX OUT OF SWAP")
        self.assertEqual(1, len(cpu))
        self.assertEqual(1, len(swap))
        self.assertNotIn("SWAP", cpu[0].splitlines()[0])
        self.assertNotIn("CPU", swap[0].splitlines()[0])
        self.assertIn("Do not restart an agent on this signal", cpu[0])
        self.assertIn("about the NEXT memory squeeze", swap[0])
        self.assertIn("kills nothing", swap[0])

    def test_one_alert_per_condition_per_crossing(self) -> None:
        WATCH.run_once(options(self.state, grace=0.0))
        WATCH.run_once(options(self.state, grace=0.0))
        self.assertEqual(len(self.envoy.published), 2)

    def test_an_unheld_role_falls_back_to_the_sre_session(self) -> None:
        self.envoy.publish_ok = False
        WATCH.run_once(options(self.state, grace=0.0))
        self.assertEqual(
            [WATCH.SRE, WATCH.SRE], [session for session, _ in self.envoy.sent]
        )

    def test_a_recovered_condition_is_never_carried_into_an_escalation(self) -> None:
        """The 01:32Z defect: CPU recovered, swap did not, and the headline said both."""
        WATCH.run_once(options(self.state, grace=0.0))
        self.sample = box(swap_free=0, swap_used_fraction=1.0)
        WATCH.run_once(options(self.state, grace=0.0, escalate=0.0))
        escalations = self.published("STILL OUT OF SWAP")
        self.assertEqual(1, len(escalations))
        self.assertNotIn("CPU", escalations[0].splitlines()[0])
        self.assertEqual([], self.envoy.sent)
        self.assertEqual(1, len(self.published("BOX SHORT OF CPU CLEARED")))

    def test_an_escalation_goes_to_the_box_health_role(self) -> None:
        WATCH.run_once(options(self.state, grace=0.0))
        self.sample = box(swap_free=0, swap_used_fraction=1.0)
        WATCH.run_once(options(self.state, grace=0.0, escalate=0.0))
        topics = {topic for topic, text in self.envoy.published if "STILL OUT OF SWAP" in text}
        self.assertEqual({WATCH.BOX_HEALTH_ROLE_TOPIC}, topics)
        self.assertEqual([], self.envoy.sent)

    def test_an_unheld_role_sends_an_escalation_to_the_sre_session(self) -> None:
        WATCH.run_once(options(self.state, grace=0.0))
        self.envoy.publish_ok = False
        self.sample = box(swap_free=0, swap_used_fraction=1.0)
        WATCH.run_once(options(self.state, grace=0.0, escalate=0.0))
        escalations = [
            session for session, text in self.envoy.sent if "STILL OUT OF SWAP" in text
        ]
        self.assertEqual([WATCH.SRE], escalations)

    def test_no_message_of_a_whole_cycle_reaches_the_chief_or_the_infra_lead(self) -> None:
        """Box health is the SRE session's (Sami, 2026-10-31), and nobody else's."""
        WATCH.run_once(options(self.state, grace=0.0))
        self.sample = box(swap_free=0, swap_used_fraction=1.0)
        WATCH.run_once(options(self.state, grace=0.0, escalate=0.0))
        self.sample = box()
        WATCH.run_once(options(self.state, grace=0.0))
        self.envoy.publish_ok = False
        self.sample = starved()
        WATCH.run_once(options(self.state, grace=0.0))
        WATCH.run_once(options(self.state, grace=0.0, escalate=0.0))
        addressed = [session for session, _ in self.envoy.sent] + [
            topic for topic, _ in self.envoy.published
        ]
        self.assertTrue(addressed)
        self.assertNotIn(CHIEF_OF_STAFF, addressed)
        self.assertNotIn(INFRA_LEAD, addressed)

    def test_recovery_clears_each_condition_and_says_so(self) -> None:
        WATCH.run_once(options(self.state, grace=0.0))
        self.sample = box()
        WATCH.run_once(options(self.state, grace=0.0))
        self.assertEqual(1, len(self.published("BOX SHORT OF CPU CLEARED")))
        self.assertEqual(1, len(self.published("BOX OUT OF SWAP CLEARED")))

    def test_dry_run_sends_nothing(self) -> None:
        WATCH.run_once(options(self.state, grace=0.0, dry_run=True))
        self.assertEqual(self.envoy.published, [])
        self.assertEqual(self.envoy.sent, [])

    def test_a_refused_escalation_is_never_reported_as_told(self) -> None:
        """An escalation that says it was told while every route refused it is a silent failure."""
        WATCH.run_once(options(self.state, grace=0.0))
        self.envoy.publish_ok = False
        self.envoy.send_ok = False
        said = []
        original_log = WATCH.log
        WATCH.log = said.append
        try:
            WATCH.run_once(options(self.state, grace=0.0, escalate=0.0))
        finally:
            WATCH.log = original_log
        self.assertEqual(
            [WATCH.SRE, WATCH.SRE], [session for session, _ in self.envoy.sent]
        )
        self.assertFalse([line for line in said if line.startswith("told ")])
        self.assertTrue([line for line in said if "NOBODY HEARD IT" in line])

    def test_a_delivered_escalation_says_who_heard_it(self) -> None:
        WATCH.run_once(options(self.state, grace=0.0))
        said = []
        original_log = WATCH.log
        WATCH.log = said.append
        try:
            WATCH.run_once(options(self.state, grace=0.0, escalate=0.0))
        finally:
            WATCH.log = original_log
        self.assertIn(f"told {WATCH.BOX_HEALTH_ROLE_TOPIC}", said)


class BoxHealthOwnerTest(unittest.TestCase):
    """A machine whose box health belongs to one session: the lane VMs' fleet monitoring owner."""

    OWNER = "01a14c3d-9d7f-7079-97aa-f810a884c050"

    # The same fakes as RoutingTest, which this class is deliberately not a subclass of: its
    # tests assert on the role publish, and under BOX_HEALTH_OWNER there is none.
    def setUp(self) -> None:
        RoutingTest.setUp(self)
        patch = unittest.mock.patch.dict(os.environ, {"BOX_HEALTH_OWNER": self.OWNER})
        patch.start()
        self.addCleanup(patch.stop)

    def test_alert_escalation_and_clear_all_go_to_the_owner_and_nothing_is_published(self) -> None:
        WATCH.run_once(options(self.state, grace=0.0))
        self.sample = box(swap_free=0, swap_used_fraction=1.0)
        WATCH.run_once(options(self.state, grace=0.0, escalate=0.0))
        self.sample = box()
        WATCH.run_once(options(self.state, grace=0.0))
        self.assertEqual(self.envoy.published, [])
        self.assertEqual({self.OWNER}, {session for session, _ in self.envoy.sent})
        texts = [text for _, text in self.envoy.sent]
        self.assertTrue([text for text in texts if "BOX OUT OF SWAP" in text])
        self.assertTrue([text for text in texts if "STILL" in text.upper()])
        self.assertTrue([text for text in texts if "CLEARED" in text])

    def test_an_unusable_owner_is_refused_at_start(self) -> None:
        with unittest.mock.patch.dict(os.environ, {"BOX_HEALTH_OWNER": "lead-box-health"}):
            with self.assertRaises(SystemExit) as refused:
                WATCH.box_health_owner()
        self.assertIn("BOX_HEALTH_OWNER", str(refused.exception))


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
