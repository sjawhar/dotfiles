#!/usr/bin/env python3
"""Regression tests for disk-fill-watch's decisions, attribution and alert routing.

The box-health lead owns the box disks (dispatch://INFRA-716), so a crossing goes to that
Envoy role, and to the INFRA lead's session only when the role is held by nobody.
"""

import argparse
import importlib.machinery
import importlib.util
import json
import subprocess
import sys
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
            "max_floor_fraction": WATCH.MAX_FLOOR_FRACTION,
            "clear_factor": 1.5,
            "grace": 0.0,
            "escalate": 1800.0,
            "history": 10800.0,
            "samples": 400,
            "confirmations": 3,
            "confirmation_spacing": 300.0,
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
        self.assertIn("DISK LOW", text)
        self.assertEqual([], self.envoy.sent)
        self.assertEqual([WATCH.BOX_HEALTH_ROLE_TOPIC], self.incident()["told"])

    def test_unheld_role_falls_back_to_the_infra_session(self) -> None:
        self.envoy.publish_ok = False

        self.run_once()

        self.assertEqual(
            ["notifications.role.lead-box-health"], [topic for topic, _ in self.envoy.published]
        )
        self.assertEqual([WATCH.INFRA_LEAD], [target for target, _ in self.envoy.sent])
        self.assertIn("DISK LOW", self.envoy.sent[0][1])
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

    def test_a_mount_the_run_stops_watching_is_withdrawn_not_dropped(self) -> None:
        """The deduplication path: whoever heard the alert is told why it ends."""
        self.run_once()
        self.envoy.published.clear()

        self.run_once(mount=["/other"])

        withdrawn = [text for _, text in self.envoy.published if "no longer watched separately" in text]
        self.assertEqual(1, len(withdrawn))
        self.assertIn("CLEARED", withdrawn[0])
        self.assertIn("withdrawn, not resolved", withdrawn[0])
        self.assertNotIn(MOUNT, json.loads(self.state.read_text())["incidents"])


class DiskFillWatchRateAndOwnershipTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state.json"
        self.envoy = FakeEnvoy()
        self.free = 200 * WATCH.GIB
        self.now = 300.0

        original_envoy = WATCH.Envoy
        original_free = WATCH.free_bytes
        original_device = WATCH.device_of
        original_writers = WATCH.writers_now
        original_time = WATCH.time.time
        WATCH.Envoy = lambda timeout=8.0: self.envoy
        WATCH.free_bytes = lambda mount: (self.free, 1000 * WATCH.GIB)
        WATCH.device_of = lambda path: None
        WATCH.writers_now = lambda options, device, mount: "writers: (none in this test)"
        WATCH.time.time = lambda: self.now

        def restore() -> None:
            WATCH.Envoy = original_envoy
            WATCH.free_bytes = original_free
            WATCH.device_of = original_device
            WATCH.writers_now = original_writers
            WATCH.time.time = original_time

        self.addCleanup(restore)

    def options(self) -> argparse.Namespace:
        return argparse.Namespace(
            mount=[MOUNT],
            horizon=7200.0,
            rate_window=300.0,
            min_span=300.0,
            low_slack=2.0,
            default_floor=20.0,
            max_floor_fraction=WATCH.MAX_FLOOR_FRACTION,
            clear_factor=1.5,
            grace=0.0,
            escalate=1800.0,
            history=10800.0,
            samples=400,
            confirmations=3,
            confirmation_spacing=300.0,
            writer_sample=15.0,
            top_writers=8,
            sudo="never",
            writers_json=None,
            tmp=self.tmp.name,
            tmp_mount=MOUNT,
            tmp_age=21600.0,
            tmp_big=500000000,
            tmp_walk_budget=20.0,
            tmp_idle_walk_budget=5.0,
            state=self.state,
            http_timeout=8.0,
            dry_run=False,
            write_state=False,
            print_alert=False,
        )

    def run_once(self, options: argparse.Namespace | None = None) -> None:
        self.assertEqual(0, WATCH.run_once(options or self.options()))

    def test_time_to_full_requires_three_spaced_df_samples(self) -> None:
        df_rate = 139.5 * WATCH.GB / 3600
        step = int(df_rate * 300)
        self.free = int(163.7 * WATCH.GIB) + 2 * step
        self.state.write_text(
            json.dumps({"mounts": {MOUNT: {"samples": [[0.0, self.free + step]]}}})
        )
        options = self.options()
        options.default_floor = 78.2

        self.run_once(options)
        first = json.loads(self.state.read_text())["mounts"][MOUNT]["horizon_confirmations"]
        self.assertEqual(1, len(first))
        self.assertEqual([], self.envoy.published)

        self.now = 600.0
        self.free -= step
        self.run_once(options)
        self.assertEqual([], self.envoy.published)

        self.now = 900.0
        self.free -= step
        self.run_once(options)
        self.assertEqual(1, len(self.envoy.published))
        self.assertIn(
            "three scheduled 5 min samples in a row, each under the 2 h horizon",
            self.envoy.published[0][1],
        )

    def test_today_one_minute_swings_never_reach_three_confirmations(self) -> None:
        options = self.options()
        options.rate_window = 60.0
        options.min_span = 60.0
        free_before = int(144.8 * WATCH.GIB)
        gain = int(130 * WATCH.GB / 3600 * 60)
        spike = int(508 * WATCH.GB / 3600 * 60)
        recovery = int(43.6 * WATCH.GB / 3600 * 60)
        record = {}

        gaining = WATCH.verdict(
            MOUNT,
            free_before,
            1000 * WATCH.GIB,
            [[-60.0, free_before - gain], [0.0, free_before]],
            options,
        )
        self.assertFalse(gaining["rate_candidate"])
        self.assertEqual(0, WATCH.update_horizon_confirmations(record, gaining, 0.0, options))

        alarming = WATCH.verdict(
            MOUNT,
            free_before - spike,
            1000 * WATCH.GIB,
            [[0.0, free_before], [60.0, free_before - spike]],
            options,
        )
        self.assertTrue(alarming["rate_candidate"])
        self.assertEqual(1, WATCH.update_horizon_confirmations(record, alarming, 60.0, options))

        recovered = WATCH.verdict(
            MOUNT,
            free_before - spike + recovery,
            1000 * WATCH.GIB,
            [[0.0, free_before], [60.0, free_before - spike], [120.0, free_before - spike + recovery]],
            options,
        )
        self.assertFalse(recovered["rate_candidate"])
        self.assertEqual(0, WATCH.update_horizon_confirmations(record, recovered, 120.0, options))

    def test_alert_labels_df_growth_and_single_window_write_traffic(self) -> None:
        now = {
            "mount": MOUNT,
            "free": 163_700_000_000,
            "total": 1_000_000_000_000,
            "floor": 78_200_000_000,
            "df_fill_rate": 14_000_000_000 / 3600,
            "df_rate_window": 3480.0,
            "time_to_full": 163_700_000_000 / (14_000_000_000 / 3600),
            "fast": True,
            "floor_held": False,
            "configured_floor": 78_200_000_000,
            "reasons": ["three scheduled 5 min samples in a row, each under the 2 h horizon"],
        }
        writers = WATCH.writers_text(
            [
                {
                    "pid": 7,
                    "written": 698_333_333,
                    "write_traffic_rate": 698_333_333 / 15,
                    "command": "scratch-writer",
                    "session": "",
                    "scope": "",
                    "cwd": self.tmp.name,
                    "holds": "",
                    "age": 1.0,
                }
            ],
            denied=0,
            seconds=15.0,
            mount=MOUNT,
            privileged=True,
        )
        text = WATCH.alert_text(
            now,
            {"opened_at": 0.0, "opened_free": now["free"]},
            writers,
            [],
            "first alert",
            3600.0,
        )

        self.assertIn("df free-space fill rate: falling 14.0 GB/h over the last 58 min", text)
        self.assertIn("write traffic: 698.3 MB in one 15 s sample", text)
        self.assertIn("not a disk-growth estimate", text)

    def test_closed_between_writes_reports_the_mtime_not_a_ended_session(self) -> None:
        entry = Path(self.tmp.name) / "writer-output"
        entry.mkdir()
        payload = entry / "log.txt"
        writer = subprocess.Popen(
            [
                sys.executable,
                "-c",
                (
                    "from pathlib import Path\n"
                    "import sys\n"
                    "import time\n"
                    "path = Path(sys.argv[1])\n"
                    "with path.open('ab') as handle:\n"
                    "    handle.write(b'x')\n"
                    "    handle.flush()\n"
                    "print('closed', flush=True)\n"
                    "time.sleep(10)\n"
                ),
                str(payload),
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            assert writer.stdout is not None
            self.assertEqual("closed\n", writer.stdout.readline())
            self.assertEqual("", WATCH.owner_of_path(str(entry)))
            found = WATCH.tmp_entries(
                self.tmp.name, age=60.0, big=0, walk_dirs=True, budget=5.0
            )
            bytes_there, path, newest_mtime = next(row for row in found if row[1] == str(entry))
            self.assertEqual(1, bytes_there)
            owner = WATCH.tmp_owner_text("", newest_mtime, time.time())
            self.assertIn("no open descriptor or cwd was observed at the sample", owner)
            self.assertIn("newest observed file or directory mtime", owner)
            self.assertNotIn("session has ended", owner)
        finally:
            writer.terminate()
            writer.wait()


class DiskFillWatchFloorAgainstDeviceTest(unittest.TestCase):
    """A floor is a number about a device, so it is checked against the device it names.

    The first sami-leanperf alert, 2026-10-30T00:30Z, said "DISK FILLING ... falling 0.0 GB/h
    over the last 5 min 20 s. At that rate it is 3558 h 42 min from full" for a disk at 82% used
    and not moving, because /mnt/nvme there is the same 247 GB /dev/root as / and the farm's
    84 GB array floor is 31.7% of it.
    """

    def options(self, **overrides) -> argparse.Namespace:
        values = {
            "horizon": 7200.0,
            "rate_window": 3600.0,
            "min_span": 1500.0,
            "low_slack": 2.0,
            "default_floor": 20.0,
            "max_floor_fraction": WATCH.MAX_FLOOR_FRACTION,
            "confirmations": 3,
            "confirmation_spacing": 300.0,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_the_leanperf_floor_is_held_to_its_device_and_does_not_cross(self) -> None:
        found = WATCH.verdict(
            "/mnt/nvme", int(45.7 * WATCH.GIB), int(246.9 * WATCH.GIB), [], self.options()
        )
        self.assertTrue(found["floor_held"])
        self.assertEqual(84 * WATCH.GB, found["configured_floor"])
        self.assertAlmostEqual(0.10, found["floor"] / found["total"], places=3)
        self.assertFalse(found["below_floor"])
        self.assertFalse(found["crossing"])

    def test_the_rocqperf_floors_are_untouched(self) -> None:
        """Measured 2026-10-30: / is 531 GB and /mnt/nvme is 3.72 TB there."""
        root = WATCH.verdict("/", 40 * WATCH.GIB, 531_415_142_400, [], self.options())
        array = WATCH.verdict("/mnt/nvme", 170 * WATCH.GIB, 3_718_546_333_696, [], self.options())
        self.assertFalse(root["floor_held"])
        self.assertEqual(18 * WATCH.GIB, root["floor"])
        self.assertFalse(array["floor_held"])
        self.assertEqual(84 * WATCH.GB, array["floor"])

    def test_a_held_floor_is_said_once_and_again_only_when_it_changes(self) -> None:
        notes: dict = {}
        found = WATCH.verdict(
            "/mnt/nvme", int(45.7 * WATCH.GIB), int(246.9 * WATCH.GIB), [], self.options()
        )
        said = []
        original_log = WATCH.log
        WATCH.log = said.append
        try:
            WATCH.note_held_floor(found, notes)
            WATCH.note_held_floor(found, notes)
        finally:
            WATCH.log = original_log
        self.assertEqual(1, len(said))
        self.assertIn("32% of this", said[0])
        self.assertIn("This is a note, not an alert", said[0])

    def test_two_mounts_on_one_filesystem_are_watched_once(self) -> None:
        devices = {"/": 2049, "/mnt/nvme": 2049, "/other": 77}
        original = WATCH.device_of
        WATCH.device_of = devices.get
        try:
            kept = WATCH.distinct_devices(["/", "/mnt/nvme", "/other"], {})
        finally:
            WATCH.device_of = original
        self.assertEqual(["/", "/other"], kept)

    def test_two_devices_are_both_watched(self) -> None:
        devices = {"/": 2049, "/mnt/nvme": 9000}
        original = WATCH.device_of
        WATCH.device_of = devices.get
        try:
            kept = WATCH.distinct_devices(["/", "/mnt/nvme"], {})
        finally:
            WATCH.device_of = original
        self.assertEqual(["/", "/mnt/nvme"], kept)


class DiskFillWatchHeadlineTest(unittest.TestCase):
    """Under a floor and falling toward full are different states and get different words."""

    def verdict_like(self, **overrides) -> dict:
        now = {
            "mount": "/mnt/nvme",
            "free": int(45.7 * WATCH.GIB),
            "total": int(246.9 * WATCH.GIB),
            "floor": int(24.6 * WATCH.GIB),
            "configured_floor": 84 * WATCH.GB,
            "floor_held": True,
            "df_fill_rate": 0.0,
            "df_rate_window": 320.0,
            "time_to_full": None,
            "fast": False,
            "reasons": ["free 45.7 GiB is under the 24.6 GiB floor"],
        }
        now.update(overrides)
        return now

    def render(self, now: dict) -> str:
        return WATCH.alert_text(now, {"opened_at": 0.0, "opened_free": now["free"]}, "writers: none", [], "first alert", 600.0)

    def test_a_standing_low_is_not_called_filling(self) -> None:
        text = self.render(self.verdict_like())
        self.assertIn("DISK LOW", text)
        self.assertNotIn("DISK FILLING", text)
        self.assertIn("free space is flat", text)
        self.assertNotIn("from full", text)
        self.assertIn("There is no deadline here", text)

    def test_a_zero_rate_never_prints_a_time_to_full(self) -> None:
        text = self.render(self.verdict_like(time_to_full=12_811_000.0))
        self.assertNotIn("3558", text)
        self.assertIn("It is not falling measurably", text)

    def test_a_real_fall_keeps_the_filling_headline_and_its_deadline(self) -> None:
        """sami-rocqperf tonight: 41.7 GB/h with 41 minutes to full. Nothing here quietens it."""
        text = self.render(
            self.verdict_like(
                mount="/",
                floor=18 * WATCH.GIB,
                configured_floor=18 * WATCH.GIB,
                floor_held=False,
                df_fill_rate=41.7 * WATCH.GB / 3600,
                df_rate_window=3600.0,
                time_to_full=2460.0,
                fast=True,
                reasons=["three scheduled 5 min samples in a row, each under the 2 h horizon"],
            )
        )
        self.assertIn("DISK FILLING", text)
        self.assertIn("falling 41.7 GB/h", text)
        self.assertIn("41 min 0 s from full", text)
        self.assertIn("DO, and there is a deadline", text)

    def test_a_held_floor_drops_the_farm_kind_list(self) -> None:
        self.assertNotIn("grade farm's own floors", self.render(self.verdict_like()))

    def test_a_slow_fall_on_a_nearly_full_disk_keeps_its_deadline(self) -> None:
        """Under the flat rate, but reaching zero inside the horizon: not flat, and not quiet."""
        text = self.render(
            self.verdict_like(
                free=1 * WATCH.GIB,
                df_fill_rate=0.5 * WATCH.GB / 3600,
                time_to_full=7000.0,
                rate_candidate=True,
                fast=True,
            )
        )
        self.assertIn("DISK FILLING", text)
        self.assertNotIn("free space is flat", text)
        self.assertIn("from full", text)


if __name__ == "__main__":
    unittest.main()
