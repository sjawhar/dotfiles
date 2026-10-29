#!/usr/bin/env python3
"""Regression tests for disk-fill-watch's alert routing.

The box-health lead owns the box disks (dispatch://INFRA-716), so a crossing goes to that
Envoy role, and to the INFRA lead's session only when the role is held by nobody.
"""

import argparse
import importlib.machinery
import importlib.util
import json
import tempfile
import time
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "disk-fill-watch"
LOADER = importlib.machinery.SourceFileLoader("disk_fill_watch", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
WATCH = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(WATCH)

MOUNT = "/testmount"


class FakeEnvoy:
    """Records what left, and can answer non-200 for an unheld role."""

    def __init__(self, timeout: float = 8.0) -> None:
        self.published: list[tuple[str, str]] = []
        self.sent: list[tuple[str, str]] = []

    publish_ok = True

    def publish(self, topic: str, message: str) -> bool:
        self.published.append((topic, message))
        return self.publish_ok

    def send(self, session_id: str, message: str) -> bool:
        self.sent.append((session_id, message))
        return True


class DiskFillWatchAlertRoutingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state.json"
        self.envoy = FakeEnvoy()
        self.free = 1 * WATCH.GIB

        original_envoy = WATCH.Envoy
        original_free = WATCH.free_bytes
        original_device = WATCH.device_of
        original_writers = WATCH.writers_now
        WATCH.Envoy = lambda timeout=8.0: self.envoy
        WATCH.free_bytes = lambda mount: (self.free, 100 * WATCH.GIB)
        WATCH.device_of = lambda path: None
        WATCH.writers_now = lambda options, device, mount: "writers: (none in this test)"

        def restore() -> None:
            WATCH.Envoy = original_envoy
            WATCH.free_bytes = original_free
            WATCH.device_of = original_device
            WATCH.writers_now = original_writers

        self.addCleanup(restore)

    def options(self, **overrides) -> argparse.Namespace:
        values = {
            "mount": [MOUNT],
            "horizon": 7200.0,
            "rate_window": 3600.0,
            "min_span": 1500.0,
            "low_slack": 2.0,
            "default_floor": 20.0,
            "clear_factor": 1.5,
            "grace": 0.0,
            "escalate": 1800.0,
            "history": 10800.0,
            "samples": 400,
            "writer_sample": 15.0,
            "top_writers": 8,
            "sudo": "never",
            "writers_json": None,
            "tmp": "/tmp",
            "tmp_mount": "/",
            "tmp_age": 21600.0,
            "tmp_big": 500000000,
            "tmp_walk_budget": 20.0,
            "tmp_idle_walk_budget": 5.0,
            "state": self.state,
            "http_timeout": 8.0,
            "dry_run": False,
            "write_state": False,
            "print_alert": False,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def run_once(self, **overrides) -> None:
        self.assertEqual(0, WATCH.run_once(self.options(**overrides)))

    def incident(self) -> dict:
        return json.loads(self.state.read_text())["incidents"][MOUNT]

    def rewrite_incident(self, **fields) -> None:
        data = json.loads(self.state.read_text())
        data["incidents"][MOUNT].update(fields)
        self.state.write_text(json.dumps(data))

    def test_first_alert_goes_to_the_box_health_role_only(self) -> None:
        self.run_once()

        self.assertEqual(1, len(self.envoy.published))
        topic, text = self.envoy.published[0]
        self.assertEqual("notifications.role.lead-box-health", topic)
        self.assertIn("DISK FILLING", text)
        self.assertEqual([], self.envoy.sent)
        self.assertEqual([WATCH.BOX_HEALTH_ROLE_TOPIC], self.incident()["told"])

    def test_unheld_role_falls_back_to_the_infra_session(self) -> None:
        self.envoy.publish_ok = False

        self.run_once()

        self.assertEqual(
            ["notifications.role.lead-box-health"], [topic for topic, _ in self.envoy.published]
        )
        self.assertEqual([WATCH.INFRA_LEAD], [target for target, _ in self.envoy.sent])
        self.assertIn("DISK FILLING", self.envoy.sent[0][1])
        self.assertEqual([WATCH.INFRA_LEAD], self.incident()["told"])

    def test_still_descending_escalates_to_the_chief_of_staff(self) -> None:
        self.run_once()
        self.rewrite_incident(alerted_at=time.time() - 3600, alert_free=self.free)
        self.free = 512 * 1024 * 1024
        self.envoy.published.clear()
        self.envoy.sent.clear()

        self.run_once()

        self.assertEqual([WATCH.CHIEF_OF_STAFF], [target for target, _ in self.envoy.sent])
        self.assertIn("STILL DESCENDING", self.envoy.sent[0][1])
        self.assertEqual([], self.envoy.published)
        self.assertIn(WATCH.CHIEF_OF_STAFF, self.incident()["told"])
        self.assertIn(WATCH.BOX_HEALTH_ROLE_TOPIC, self.incident()["told"])

    def test_clear_reaches_the_role_that_heard_the_crossing(self) -> None:
        self.run_once()
        self.envoy.published.clear()
        self.free = 90 * WATCH.GIB

        self.run_once()

        self.assertEqual(
            ["notifications.role.lead-box-health"], [topic for topic, _ in self.envoy.published]
        )
        self.assertIn("CLEARED", self.envoy.published[0][1])
        self.assertEqual([], self.envoy.sent)

    def test_clear_reaches_the_fallback_session_that_heard_the_crossing(self) -> None:
        self.envoy.publish_ok = False
        self.run_once()
        self.envoy.published.clear()
        self.envoy.sent.clear()
        self.free = 90 * WATCH.GIB

        self.run_once()

        self.assertEqual([WATCH.INFRA_LEAD], [target for target, _ in self.envoy.sent])
        self.assertIn("CLEARED", self.envoy.sent[0][1])


if __name__ == "__main__":
    unittest.main()
