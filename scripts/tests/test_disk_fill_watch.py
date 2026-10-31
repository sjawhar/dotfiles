#!/usr/bin/env python3
"""Regression tests for disk-fill-watch's decisions, attribution and alert routing.

The box-health lead owns the box disks (dispatch://INFRA-716), so a crossing goes to that
Envoy role, and to the INFRA lead's session only when the role is held by nobody.
"""

import argparse
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
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

    # Box health is the SRE's work, not the chief of staff's and no longer the INFRA lead's
    # (2026-10-31T02:37:50Z), so neither id may appear where a message is addressed. Both are
    # written out rather than read from the module: a constant the script no longer has cannot
    # prove its holder is off the list.
    CHIEF_OF_STAFF = "01a13dca-e979-7264-af32-f654ef57a6f5"
    INFRA_LEAD = "01a13c57-d826-7242-80fe-5f0670188b11"
    BOX_HEALTH_SRE = "01a18385-f8c8-74a9-b6ff-694fb5fe87a7"

    def test_unheld_role_falls_back_to_the_box_health_sre(self) -> None:
        self.envoy.publish_ok = False

        self.run_once()

        self.assertEqual(
            ["notifications.role.lead-box-health"], [topic for topic, _ in self.envoy.published]
        )
        self.assertEqual([WATCH.BOX_HEALTH_SRE], [target for target, _ in self.envoy.sent])
        self.assertIn("DISK LOW", self.envoy.sent[0][1])
        self.assertEqual([WATCH.BOX_HEALTH_SRE], self.incident()["told"])

    def escalate(self) -> None:
        """Put the crossing half an hour past its alert, with free space lower than it was."""
        self.rewrite_incident(alerted_at=time.time() - 3600, alert_free=self.free)
        self.free = 512 * 1024 * 1024
        self.run_once()

    def test_still_descending_escalates_to_the_box_health_role(self) -> None:
        self.run_once()
        self.envoy.published.clear()
        self.envoy.sent.clear()

        self.escalate()

        self.assertEqual(
            [WATCH.BOX_HEALTH_ROLE_TOPIC], [topic for topic, _ in self.envoy.published]
        )
        self.assertIn("STILL DESCENDING", self.envoy.published[0][1])
        self.assertEqual([], self.envoy.sent)
        self.assertEqual([WATCH.BOX_HEALTH_ROLE_TOPIC], self.incident()["told"])

    def test_an_unheld_role_escalates_to_the_box_health_sre(self) -> None:
        """The publish is driven to fail, which is how an unheld role answers."""
        self.run_once()
        self.envoy.published.clear()
        self.envoy.sent.clear()
        self.envoy.publish_ok = False

        self.escalate()

        self.assertEqual(
            [WATCH.BOX_HEALTH_ROLE_TOPIC], [topic for topic, _ in self.envoy.published]
        )
        self.assertEqual([self.BOX_HEALTH_SRE], [target for target, _ in self.envoy.sent])
        self.assertEqual(WATCH.BOX_HEALTH_SRE, self.BOX_HEALTH_SRE)
        self.assertIn("STILL DESCENDING", self.envoy.sent[0][1])
        self.assertIn(self.BOX_HEALTH_SRE, self.incident()["told"])
        self.assertNotIn(self.INFRA_LEAD, self.incident()["told"])

    def assert_cycle_never_reaches_the_chief_or_the_infra_lead(self, role_held: bool) -> None:
        self.envoy.publish_ok = role_held
        stages: dict[str, list[tuple[str, str]]] = {}

        def take(stage: str) -> None:
            stages[stage] = self.envoy.published + self.envoy.sent
            self.envoy.published.clear()
            self.envoy.sent.clear()

        self.run_once()
        take("first alert")
        self.escalate()
        take("escalation")
        self.free = 90 * WATCH.GIB
        self.run_once()
        take("clear")

        everything = [message for stage in stages.values() for message in stage]
        for stage, messages in stages.items():
            with self.subTest(stage=stage, role_held=role_held):
                # A negative assertion over an empty list proves nothing, so every stage is
                # first shown to have said something of its own.
                self.assertTrue(messages, f"{stage} sent nothing")
                for who in (self.CHIEF_OF_STAFF, self.INFRA_LEAD):
                    self.assertNotIn(who, [target for target, _ in messages])
                    self.assertNotIn(who, "\n".join(text for _, text in messages))
        self.assertIn("DISK LOW", stages["first alert"][0][1])
        self.assertTrue(any("STILL DESCENDING" in text for _, text in stages["escalation"]))
        self.assertTrue(any("CLEARED" in text for _, text in stages["clear"]))
        self.assertNotIn(MOUNT, json.loads(self.state.read_text())["incidents"])
        self.assertEqual(
            [], [t for t, _ in everything if t in (self.CHIEF_OF_STAFF, self.INFRA_LEAD)]
        )

    def test_a_whole_cycle_never_reaches_the_chief_of_staff_or_the_infra_lead(self) -> None:
        self.assert_cycle_never_reaches_the_chief_or_the_infra_lead(role_held=True)

    def test_a_whole_cycle_with_an_unheld_role_reaches_neither_of_them(self) -> None:
        self.assert_cycle_never_reaches_the_chief_or_the_infra_lead(role_held=False)

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

        self.assertEqual([WATCH.BOX_HEALTH_SRE], [target for target, _ in self.envoy.sent])
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


class DiskFillWatchBoxHealthOwnerTest(unittest.TestCase):
    """A machine whose box health belongs to one session: the lane VMs' fleet monitoring owner."""

    OWNER = "01a14c3d-9d7f-7079-97aa-f810a884c050"

    # The routing harness, without its tests: those assert on the role publish, and under
    # BOX_HEALTH_OWNER there is none.
    options = DiskFillWatchAlertRoutingTest.options
    run_once = DiskFillWatchAlertRoutingTest.run_once
    incident = DiskFillWatchAlertRoutingTest.incident
    rewrite_incident = DiskFillWatchAlertRoutingTest.rewrite_incident
    escalate = DiskFillWatchAlertRoutingTest.escalate

    def setUp(self) -> None:
        DiskFillWatchAlertRoutingTest.setUp(self)
        patch = unittest.mock.patch.dict(os.environ, {"BOX_HEALTH_OWNER": self.OWNER})
        patch.start()
        self.addCleanup(patch.stop)

    def test_alert_escalation_and_clear_all_go_to_the_owner_and_nothing_is_published(self) -> None:
        self.run_once()
        self.escalate()
        self.free = 90 * WATCH.GIB
        self.run_once()

        self.assertEqual([], self.envoy.published)
        self.assertEqual({self.OWNER}, {target for target, _ in self.envoy.sent})
        texts = [text for _, text in self.envoy.sent]
        self.assertTrue([text for text in texts if "DISK LOW" in text])
        self.assertTrue([text for text in texts if "STILL DESCENDING" in text])
        self.assertTrue([text for text in texts if "CLEARED" in text])

    def test_an_unusable_owner_is_refused_at_start(self) -> None:
        with unittest.mock.patch.dict(os.environ, {"BOX_HEALTH_OWNER": "lead-box-health"}):
            with self.assertRaises(SystemExit) as refused:
                WATCH.box_health_owner()
        self.assertIn("BOX_HEALTH_OWNER", str(refused.exception))


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

    def test_tmp_entry_on_another_filesystem_costs_tmp_nothing(self) -> None:
        # A containerd or buildkit overlay mounted under /tmp was named at 3.0 GB on 2026-10-31,
        # but its bytes live on the data disk, not on /. Files on another device must not count.
        entry = Path(self.tmp.name) / "containerd-mount123"
        entry.mkdir()
        (entry / "layer.bin").write_bytes(b"x" * 4096)
        real_lstat = os.lstat
        tmp_dev = os.stat(self.tmp.name).st_dev

        def lstat_on_another_device(path, *args, **kwargs):
            result = real_lstat(path, *args, **kwargs)
            if str(path).startswith(str(entry) + "/"):
                fields = list(result)
                fields[2] = tmp_dev + 1  # st_dev
                return os.stat_result(fields)
            return result

        with unittest.mock.patch.object(WATCH.os, "lstat", side_effect=lstat_on_another_device):
            found = WATCH.tmp_entries(self.tmp.name, age=60.0, big=1, walk_dirs=True, budget=5.0)
        self.assertNotIn(str(entry), [row[1] for row in found])
        found = WATCH.tmp_entries(self.tmp.name, age=60.0, big=1, walk_dirs=True, budget=5.0)
        self.assertIn(str(entry), [row[1] for row in found])


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

    def test_under_the_floor_and_falling_carries_the_deadline_it_computed(self) -> None:
        """The defect of 2026-10-31T02:33Z: the header said 36 minutes, the ending said none."""
        text = self.render(
            self.verdict_like(
                free=int(58.7 * WATCH.GIB),
                total=int(3463.2 * WATCH.GIB),
                floor=int(78.2 * WATCH.GIB),
                below_floor=True,
                df_fill_rate=104.8 * WATCH.GB / 3600,
                df_rate_window=3000.0,
                df_rate_samples=3,
                time_to_full=2165.0,
                rate_candidate=True,
                fast=False,
            )
        )
        self.assertIn("36 min 5 s from full", text)
        self.assertNotIn("There is no deadline here", text)
        self.assertNotIn("is not filling", text)
        self.assertIn("under the 78.2 GiB floor AND still falling", text)
        # The deadline is in the closing paragraph, not only in the header a reader skips.
        self.assertIn("36 min 5 s from full", text.rsplit("\n", 2)[-2])

    def test_a_flat_disk_under_its_floor_keeps_the_standing_low_ending(self) -> None:
        text = self.render(self.verdict_like(below_floor=True, df_rate_samples=9))
        self.assertIn("There is no deadline here", text)
        self.assertIn("flat or rising", text)
        self.assertNotIn("from full", text)

    def test_a_rising_disk_under_its_floor_keeps_the_standing_low_ending(self) -> None:
        text = self.render(
            self.verdict_like(
                below_floor=True, df_fill_rate=-30.0 * WATCH.GB / 3600, df_rate_samples=9
            )
        )
        self.assertIn("rising 30.0 GB/h", text)
        self.assertIn("There is no deadline here", text)

    def test_a_swinging_window_is_reported_as_varying_not_as_a_deadline(self) -> None:
        """Tonight's /mnt/nvme curve: 60, 102, 85.5, 95, 68.8 GiB free inside one window."""
        text = self.render(
            self.verdict_like(
                free=int(57.4 * WATCH.GIB),
                floor=int(78.2 * WATCH.GIB),
                below_floor=True,
                df_fill_rate=4.2 * WATCH.GB / 3600,
                df_rate_window=2340.0,
                df_rate_samples=6,
                df_rate_varying=True,
                df_rate_low=int(57.4 * WATCH.GIB),
                df_rate_high=int(102.0 * WATCH.GIB),
                time_to_full=52_560.0,
            )
        )
        self.assertIn("rose and fell inside that window", text)
        self.assertIn("between 57.4 GiB and 102.0 GiB", text)
        self.assertIn("rather than a trend", text)
        self.assertIn("a rate that is swinging", text)
        self.assertNotIn("There is no deadline here", text)

    def test_the_rate_says_how_many_samples_it_rests_on(self) -> None:
        text = self.render(
            self.verdict_like(
                df_fill_rate=10.0 * WATCH.GB / 3600,
                df_rate_window=1800.0,
                df_rate_samples=2,
                time_to_full=3600.0,
            )
        )
        self.assertIn("from the first and last of 2 samples", text)
        self.assertIn("Two samples stand behind that rate", text)


class DiskFillWatchRateShapeTest(unittest.TestCase):
    """What the two-point rate rests on, measured off the same samples the rate used."""

    def shape(self, values: list[float], step: float = 300.0) -> dict:
        samples = [[step * index, value] for index, value in enumerate(values)]
        return WATCH.rate_shape(samples, 3600.0)

    def test_tonights_sawtooth_is_varying(self) -> None:
        found = self.shape([60.0, 102.0, 85.5, 95.0, 68.8])
        self.assertTrue(found["varying"])
        self.assertEqual(5, found["count"])
        self.assertEqual(60.0, found["low"])
        self.assertEqual(102.0, found["high"])

    def test_a_steady_fall_with_small_jitter_is_not_varying(self) -> None:
        found = self.shape([100.0, 90.0, 90.2, 80.0, 70.0, 60.0])
        self.assertFalse(found["varying"])
        self.assertEqual(6, found["count"])

    def test_a_window_outside_the_rate_window_is_not_counted(self) -> None:
        found = self.shape([100.0, 90.0, 80.0, 70.0], step=1800.0)
        self.assertEqual(3, found["count"])

    def test_one_sample_has_no_shape(self) -> None:
        self.assertEqual(
            {"count": 1, "varying": False, "low": None, "high": None}, self.shape([100.0])
        )


if __name__ == "__main__":
    unittest.main()
