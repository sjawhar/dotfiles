#!/usr/bin/env python3
"""Regression tests for disk-fill-watch's decisions, attribution and alert routing.

The box-health lead owns the box disks (dispatch://INFRA-716), so a crossing goes to that
Envoy role, and to the INFRA lead's session only when the role is held by nobody.
"""

import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
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

    def request(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        """A listener that answers nothing, so no session is ever verified as live here."""
        return 0, {}

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
        WATCH.writers_now = lambda options, device, mount, deadline: ("writers: (none in this test)", [])

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
            "urgent_horizon": WATCH.URGENT_HORIZON,
            "run_budget": 100.0,
            "history": 10800.0,
            "samples": 400,
            "confirmations": 3,
            "confirmation_spacing": 300.0,
            "writer_sample": 15.0,
            "top_writers": 8,
            "sudo": "never",
            "writers_json": None,
            "tmp": "/tmp",
            "src": "/nonexistent-src",
            "tmp_age": 21600.0,
            "tmp_big": 500000000,
            "root_big": 2000000000,
            "tmp_walk_budget": 20.0,
            "tmp_idle_walk_budget": 5.0,
            "tmp_digest": 86400.0,
            "tmp_pending_max": 500,
            "state": self.state,
            "iops_caps": {},
            "http_timeout": 8.0,
            "dry_run": False,
            "write_state": False,
            "print_alert": False,
            "report": False,
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

    def fall_to(self, free_gib: float) -> None:
        self.free = int(free_gib * WATCH.GIB)
        self.run_once()

    def escalations(self) -> list[str]:
        return [
            text
            for _, text in self.envoy.published + self.envoy.sent
            if "STILL DESCENDING" in text
        ]

    def big_filesystem(self, free_gib: float) -> None:
        """A 1 TB filesystem, so the 20 GiB default floor is not held to a tenth of it."""
        WATCH.free_bytes = lambda mount: (self.free, 1000 * WATCH.GIB)
        self.free = int(free_gib * WATCH.GIB)

    # The `/` crossing of 2026-10-31 opened at 11:04Z, alerted at 11:09Z, escalated once at
    # 11:39Z and then said nothing while free space fell from 22.6 GiB to 3.9 GiB. A fall of
    # a quarter below the level already reported is a new fact and is said again
    # (RESEND_FALL_FRACTION; it was a fixed 5 GiB step until 2026-11-03).
    def test_a_crossing_that_keeps_falling_is_said_again_on_each_quarter_lost(self) -> None:
        self.big_filesystem(19)
        self.run_once()
        self.envoy.published.clear()
        self.envoy.sent.clear()

        for free_gib in (17, 15, 13, 11, 9, 7):  # a 12 GiB fall, in 2 GiB samples
            self.fall_to(free_gib)

        # The samples are one instant apart here, so the first of them is already under an
        # hour from full and says so; after that, each quarter lost below the level last
        # reported: 17 -> 11 is 35%, 11 -> 7 is 36%, and the levels between are not said.
        said = self.escalations()
        self.assertEqual(3, len(said))
        self.assertIn("under 1 h to full", said[0])
        self.assertIn("free fell 35% below the 17.0 GiB last reported", said[1])
        self.assertIn("free fell 36% below the 11.0 GiB last reported", said[2])
        self.assertEqual(3, self.incident()["escalations"])
        self.assertEqual(7 * WATCH.GIB, self.incident()["reported_free"])

    def test_a_flat_crossing_is_never_said_again(self) -> None:
        self.big_filesystem(19)
        self.run_once()
        self.envoy.published.clear()
        self.envoy.sent.clear()

        for _ in range(6):
            self.fall_to(19)

        self.assertEqual([], self.escalations())
        self.assertEqual(19 * WATCH.GIB, self.incident()["reported_free"])

    def repeats(self) -> list[str]:
        """Every message that is not the first alert, whichever fact sent it."""
        return [
            text
            for _, text in self.envoy.published + self.envoy.sent
            if "Said again:" in text
        ]

    def age_last_message(self, seconds: float) -> None:
        """Put the crossing's last message -- alert or repeat -- that far in the past."""
        incident = self.incident()
        field = "escalated_at" if incident.get("escalated_at") else "alerted_at"
        shift = time.time() - seconds - incident[field]
        fields = {field: incident[field] + shift}
        if field == "escalated_at":
            fields["alerted_at"] = incident["alerted_at"] + shift
        self.rewrite_incident(**fields)

    # The clock is gone: 52 of the 150 messages in the 48 h to 2026-11-03 were a 30-minute
    # timer restating one open crossing, and the SRE acted on none of them
    # (dispatch://INFRA-672). Time alone is not a fact about the disk.
    def test_a_crossing_under_its_floor_is_not_repeated_by_the_clock(self) -> None:
        self.big_filesystem(19)
        self.run_once()
        self.envoy.published.clear()
        self.envoy.sent.clear()

        for _ in range(6):  # three hours of five-minute runs, free space unchanged
            self.age_last_message(1800)
            self.run_once()

        self.assertEqual([], self.repeats())
        self.assertEqual(0, int(self.incident().get("escalations") or 0))

    def test_a_quarter_lost_says_which_fact_sent_it(self) -> None:
        self.big_filesystem(19)
        self.run_once()
        self.envoy.published.clear()
        self.envoy.sent.clear()

        self.fall_to(13)  # a 6 GiB fall, 32% of what was left

        self.assertEqual(1, len(self.repeats()))
        self.assertIn("Said again: free fell 32% below the 19.0 GiB last reported.", self.repeats()[0])

    def rate_only_crossing(self) -> None:
        """A crossing open on the fill rate alone, with free space well above the floor."""
        WATCH.free_bytes = lambda mount: (self.free, 1000 * WATCH.GIB)
        self.free = 30 * WATCH.GIB
        now = time.time()
        self.state.write_text(
            json.dumps(
                {
                    "mounts": {
                        MOUNT: {
                            "samples": [[now - 3000, 50 * WATCH.GIB], [now - 1, self.free]],
                            "horizon_confirmations": [
                                [now - 900, 3600.0],
                                [now - 600, 3600.0],
                                [now - 300, 3600.0],
                            ],
                        }
                    },
                    "incidents": {},
                }
            )
        )

    # Above the floor the crossing rests on the fill rate, which the first alert already
    # carries. Two small falls say nothing; the hour running out is a new fact and says so.
    def test_a_rate_only_crossing_speaks_again_when_the_hour_runs_out(self) -> None:
        self.rate_only_crossing()
        self.run_once()
        self.assertIn("DISK FILLING", self.envoy.published[0][1])
        self.assertTrue(self.incident()["free"] > self.incident_floor())
        self.envoy.published.clear()
        self.envoy.sent.clear()

        self.age_last_message(1800)
        self.fall_to(29)  # 69 min to full: nothing new
        self.age_last_message(1800)
        self.fall_to(28)  # 64 min to full: still nothing new
        self.assertEqual([], self.repeats())

        self.fall_to(23)  # 43 min to full
        self.assertEqual(1, len(self.repeats()))
        self.assertIn("Said again: under 1 h to full", self.repeats()[0])

        self.fall_to(22)  # still under the hour, and only 4% further down: said once
        self.assertEqual(1, len(self.repeats()))

    def incident_floor(self) -> float:
        return min(
            WATCH.FLOORS.get(MOUNT, self.options().default_floor * WATCH.GIB),
            1000 * WATCH.GIB * WATCH.MAX_FLOOR_FRACTION,
        )


class DiskFillWatchWritersJsonChildTest(unittest.TestCase):
    """`--writers-json` is parsed by its parent, so its stdout must be JSON and only JSON.

    On 2026-10-31 every privileged sample of the day was lost to `JSONDecodeError: Extra data:
    line 1 column 5 (char 4)` -- the four digits of the year in a log line log() had printed on
    that stdout before the document. The child is run here exactly as `writers_now` runs it.
    """

    def test_the_child_prints_one_json_document_and_logs_to_stderr(self) -> None:
        payload = json.dumps({"seconds": 0.2, "device": os.stat("/").st_dev, "top": 2})
        environment = dict(os.environ)
        # A listener that answers nothing, so the child certainly logs its live-session line.
        environment["ENVOY_URL"] = "http://127.0.0.1:1"
        done = subprocess.run(
            [sys.executable, str(SCRIPT), "--writers-json", payload],
            capture_output=True,
            text=True,
            timeout=120,
            env=environment,
        )

        self.assertEqual(0, done.returncode, done.stderr)
        answer = json.loads(done.stdout)
        self.assertIsInstance(answer["writers"], list)
        self.assertIsInstance(answer["denied"], int)
        self.assertIn("live sessions unavailable", done.stderr)


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
        WATCH.writers_now = lambda options, device, mount, deadline: ("writers: (none in this test)", [])
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
            urgent_horizon=WATCH.URGENT_HORIZON,
            run_budget=100.0,
            history=10800.0,
            samples=400,
            confirmations=3,
            confirmation_spacing=300.0,
            writer_sample=15.0,
            top_writers=8,
            sudo="never",
            writers_json=None,
            tmp=self.tmp.name,
            src="/nonexistent-src",
            tmp_age=21600.0,
            tmp_big=500000000,
            root_big=2000000000,
            tmp_walk_budget=20.0,
            tmp_idle_walk_budget=5.0,
            tmp_digest=86400.0,
            tmp_pending_max=500,
            state=self.state,
            iops_caps={},
            http_timeout=8.0,
            dry_run=False,
            write_state=False,
            print_alert=False,
            report=False,
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
            "Three samples in a row reach full inside the 2 h horizon",
            self.envoy.published[0][1],
        )
        self.assertIn(
            "three scheduled 5 min samples in a row, each under the 2 h horizon",
            self.report(self.envoy.published[0][1]),
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

    # 2026-11-01: /mnt/nvme crossed its floor at 00:25:51Z with 59.5 GiB free, alerted at
    # 00:31:11Z and was at 7.9 GiB by 00:35:51Z. The grace window took half the time the box
    # had. An hour of gentle fall hides the last five minutes: here the hour reads about
    # 100 GB/h (38 min to full, outside the urgent mark) while the last two samples read about
    # 360 GB/h (11 min), and it is the second number the grace decision must use.
    def falling_hour(self) -> list[list[float]]:
        start = int(160 * WATCH.GIB)
        step = int(6 * WATCH.GIB)
        return [[300.0 * index, start - step * index] for index in range(13)]

    def test_a_crossing_inside_the_urgent_mark_alerts_on_its_first_sample(self) -> None:
        self.state.write_text(json.dumps({"mounts": {MOUNT: {"samples": self.falling_hour()}}}))
        options = self.options()
        options.rate_window = 3600.0
        options.min_span = 1500.0
        options.grace = 240.0
        options.default_floor = 78.2
        self.now = 3900.0
        self.free = int(60 * WATCH.GIB)

        self.run_once(options)

        self.assertEqual(1, len(self.envoy.published))
        text = self.envoy.published[0][1]
        self.assertIn("Sent on the first sample of this crossing", text)
        self.assertIn("inside the 30 min urgent mark", text)
        # The sentence a reader acts on is in the message; the whole account is in the report.
        self.assertIn(
            "Alerted on the first sample of this crossing, with no grace window", self.report(text)
        )
        incident = json.loads(self.state.read_text())["incidents"][MOUNT]
        self.assertEqual("urgent", incident["first_rule"])
        self.assertEqual(3900.0, incident["opened_at"])
        self.assertEqual(3900.0, incident["alerted_at"])

    def report(self, message: str) -> str:
        """The report a message names, which holds what the 600 characters left out."""
        path = Path(message.rsplit("Full report: ", 1)[1].strip())
        self.assertTrue(path.is_file(), path)
        return path.read_text()

    def test_the_hour_rate_alone_would_have_held_that_first_sample(self) -> None:
        """The same sample, asked of each rate: the hour says 38 min, the last two say 11."""
        options = self.options()
        options.rate_window = 3600.0
        options.default_floor = 78.2
        samples = self.falling_hour() + [[3900.0, int(60 * WATCH.GIB)]]
        found = WATCH.verdict(MOUNT, int(60 * WATCH.GIB), 1000 * WATCH.GIB, samples, options)

        self.assertGreater(found["time_to_full"], options.urgent_horizon)
        self.assertLess(found["urgent_time_to_full"], options.urgent_horizon)
        self.assertEqual(300.0, found["latest_rate_window"])
        self.assertTrue(found["urgent"])

    def test_a_crossing_two_hours_from_full_still_waits_for_the_grace_window(self) -> None:
        """A slow fall under the floor keeps the old path: nothing goes out on its first
        sample, and the alert comes on the next scheduled one."""
        step = int(10 * WATCH.GB / 3600 * 300)
        self.free = int(70 * WATCH.GIB)
        self.state.write_text(
            json.dumps({"mounts": {MOUNT: {"samples": [[0.0, self.free + step]]}}})
        )
        options = self.options()
        options.rate_window = 3600.0
        options.grace = 240.0
        options.default_floor = 78.2

        self.run_once(options)
        self.assertEqual([], self.envoy.published)
        found = WATCH.verdict(MOUNT, self.free, 1000 * WATCH.GIB, [[0.0, self.free + step], [300.0, self.free]], options)
        self.assertGreater(found["urgent_time_to_full"], 2 * 3600)

        self.now = 600.0
        self.free -= step
        self.run_once(options)
        self.assertEqual(1, len(self.envoy.published))
        text = self.envoy.published[0][1]
        self.assertIn("is under the 78.2 GiB floor.", text)
        self.assertNotIn("no grace window", text)
        self.assertIn("Alerted after the crossing held the grace window", self.report(text))
        self.assertEqual("grace", json.loads(self.state.read_text())["incidents"][MOUNT]["first_rule"])

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
                    "on_mount": True,
                    "example": f"cwd {self.tmp.name}",
                    "fd_error": "",
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
            found = WATCH.young_entries(
                self.tmp.name, age=60.0, big=0, walk_dirs=True, budget=5.0
            )
            bytes_there, path, newest_mtime = next(row for row in found if row[1] == str(entry))
            # One byte of content, one 4 KiB block of disk. young_entries reports what the
            # filesystem gave away, not what the file claims, so that a sparse file cannot read
            # as the largest thing on the machine.
            self.assertEqual(4096, bytes_there)
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
            found = WATCH.young_entries(self.tmp.name, age=60.0, big=1, walk_dirs=True, budget=5.0)
        self.assertNotIn(str(entry), [row[1] for row in found])
        found = WATCH.young_entries(self.tmp.name, age=60.0, big=1, walk_dirs=True, budget=5.0)
        self.assertIn(str(entry), [row[1] for row in found])


class DiskFillWatchBirthTimeTest(unittest.TestCase):
    """An entry is young by its birth time, never by its ctime or its mtime.

    On 2026-10-31T21:51:59Z the watch named `/home/sami/src/example-task-repo` — the
    shared canonical checkout, created 2026-10-14T01:48:07Z — as "born in the last 6 h",
    because a session had restored its HEAD at 18:01:03Z and a Linux ctime moves with the
    directory's mtime. The birth comes from statx(2); `os.stat` has no `st_birthtime` on Linux
    before Python 3.13 and this box runs 3.12.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_birth_time_is_the_number_stat_reports(self) -> None:
        entry = Path(self.tmp.name) / "tree"
        entry.mkdir()
        reported = subprocess.run(
            ["stat", "-c", "%W", str(entry)], capture_output=True, text=True, check=True
        ).stdout.strip()

        self.assertEqual(float(reported), WATCH.birth_time(str(entry)))
        self.assertEqual(float(reported), WATCH.statx_birth(str(entry)))
        self.assertLess(abs(time.time() - WATCH.birth_time(str(entry))), 120)

    def test_an_old_directory_touched_today_is_not_young(self) -> None:
        entry = Path(self.tmp.name) / "example-task-repo"
        entry.mkdir()
        (entry / "pack.bin").write_bytes(b"x" * 4096)
        os.utime(entry, None)  # the HEAD restore: fresh mtime, and a ctime that moved with it
        born_three_weeks_ago = time.time() - 21 * 86400

        with unittest.mock.patch.object(
            WATCH, "birth_time", lambda path, stat_result=None: born_three_weeks_ago
        ):
            found = WATCH.young_entries(self.tmp.name, age=21600.0, big=1, walk_dirs=True, budget=5.0)
        self.assertEqual([], [row[1] for row in found])

        # The same tree, born now: the rule still names what is genuinely new.
        found = WATCH.young_entries(self.tmp.name, age=21600.0, big=1, walk_dirs=True, budget=5.0)
        self.assertEqual([str(entry)], [row[1] for row in found])

    def test_an_unknown_birth_is_not_young(self) -> None:
        entry = Path(self.tmp.name) / "tree"
        entry.mkdir()
        (entry / "pack.bin").write_bytes(b"x" * 4096)

        with unittest.mock.patch.object(WATCH, "birth_time", lambda path, stat_result=None: 0.0):
            found = WATCH.young_entries(self.tmp.name, age=21600.0, big=1, walk_dirs=True, budget=5.0)

        self.assertEqual([], [row[1] for row in found])


class DiskFillWatchSrcEntryTest(unittest.TestCase):
    """A new, big top-level checkout under ~/src is named like a new, big /tmp entry.

    Where ~/src is on / (sami-rocqperf), a checkout cut there fills the disk every session shares, and the tree
    belongs on /mnt/nvme. A tree already moved leaves a SYMLINK at its old path, which is
    measured only as itself and must never alert: that is what the LPSREV trees became on
    2026-10-31.
    """

    # A tree with a live owner on this box: since 2026-11-03 a NEW BIG notice goes to that
    # session and to nobody else, so the tests that read the notice give it one.
    LANE = "01a19336-7537-7291-98db-3b4c6de1d7de"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state.json"
        self.src = Path(self.tmp.name) / "src"
        self.src.mkdir()
        self.envoy = FakeEnvoy()

        original_envoy = WATCH.Envoy
        original_free = WATCH.free_bytes
        original_device = WATCH.device_of
        original_writers = WATCH.writers_now
        WATCH.Envoy = lambda timeout=8.0: self.envoy
        WATCH.free_bytes = lambda mount: (500 * WATCH.GIB, 1000 * WATCH.GIB)
        WATCH.device_of = lambda path: 1  # src, /tmp and the watched mount on one filesystem
        WATCH.writers_now = lambda options, device, mount, deadline: ("writers: (none in this test)", [])

        def restore() -> None:
            WATCH.Envoy = original_envoy
            WATCH.free_bytes = original_free
            WATCH.device_of = original_device
            WATCH.writers_now = original_writers
            WATCH.LIVE_SESSIONS.clear()
            WATCH.LIVE_READ = False

        self.addCleanup(restore)
        WATCH.LIVE_SESSIONS.clear()
        WATCH.LIVE_READ = False

    def owned_by_a_live_session(self) -> None:
        """Envoy lists one session live here, and the entry is reached through it."""
        original_owner = WATCH.owner_of_path
        WATCH.owner_of_path = lambda path: self.LANE
        self.addCleanup(setattr, WATCH, "owner_of_path", original_owner)
        WATCH.LIVE_SESSIONS[self.LANE] = {"session_id": self.LANE}
        WATCH.LIVE_READ = True

    def options(self) -> argparse.Namespace:
        values = dict(
            DiskFillWatchAlertRoutingTest.options(self).__dict__,
            state=self.state,
            tmp=str(Path(self.tmp.name) / "no-such-tmp"),
            src=str(self.src),
            tmp_big=1,
            root_big=1,
            tmp_pending_max=200,
            tmp_digest=86400.0,
        )
        return argparse.Namespace(**values)

    def run_once(self) -> None:
        self.assertEqual(0, WATCH.run_once(self.options()))

    def messages(self) -> list[str]:
        return [text for _, text in self.envoy.published + self.envoy.sent if "NEW BIG" in text]

    def report(self, message: str) -> str:
        return Path(message.rsplit("Full report: ", 1)[1].strip()).read_text()

    def test_a_new_big_checkout_under_src_is_named_once(self) -> None:
        self.owned_by_a_live_session()
        checkout = self.src / "agent-infra-task9"
        checkout.mkdir()
        (checkout / "pack.bin").write_bytes(b"x" * 4096)

        self.run_once()

        self.assertEqual(1, len(self.messages()))
        message = self.messages()[0]
        self.assertIn("NEW BIG ~/src ENTRY", message)
        self.assertIn(str(checkout), message)
        self.assertIn("agent-workspace", message)
        self.assertIn("/mnt/nvme", message)
        # Its owner session, and nobody else: not the role, not the box-health SRE.
        self.assertEqual([self.LANE], [who for who, _ in self.envoy.sent])
        self.assertEqual([], self.envoy.published)

        self.envoy.sent.clear()
        self.run_once()
        self.assertEqual([], self.messages())

    def test_a_checkout_nobody_on_this_box_owns_waits_for_the_digest(self) -> None:
        """Since 2026-11-03 an orphan is a line in one digest a day, not a message of its own:
        the box-health owner acted on none of the 62 it received in 48 h."""
        checkout = self.src / "machcsl-cut"
        checkout.mkdir()
        (checkout / "pack.bin").write_bytes(b"x" * 4096)

        self.run_once()

        self.assertEqual([], self.envoy.published + self.envoy.sent)
        pending = json.loads(self.state.read_text())["tmp_pending"]
        self.assertEqual([str(checkout)], [entry["path"] for entry in pending])

    def test_a_symlink_left_by_a_moved_tree_never_alerts(self) -> None:
        moved = Path(self.tmp.name) / "moved-to-nvme"
        moved.mkdir()
        (moved / "pack.bin").write_bytes(b"x" * 8192)
        (self.src / "lpsrev-9.2").symlink_to(moved)

        self.run_once()

        self.assertEqual([], self.messages())

    def big_checkout(self) -> Path:
        checkout = self.src / "oss-verify-proofs-internal"
        checkout.mkdir()
        (checkout / "pack.bin").write_bytes(b"x" * 4096)
        return checkout

    def on_devices(self, src: int, mount: int, root: int, free: float = 500 * WATCH.GIB) -> None:
        """Stat ~/src, the watched mount and / to the given devices."""
        devices = {str(self.src): src, MOUNT: mount, "/": root}
        WATCH.device_of = lambda path: devices.get(path, 99)
        WATCH.free_bytes = lambda mount_point: (free, 1000 * WATCH.GIB)

    def test_src_on_the_root_device_gets_the_root_disk_advice(self) -> None:
        """sami-rocqperf: ~/src really is on /, and a checkout there belongs on /mnt/nvme."""
        self.owned_by_a_live_session()
        self.on_devices(src=1, mount=1, root=1)
        checkout = self.big_checkout()

        self.run_once()

        self.assertEqual(1, len(self.messages()))
        message = self.messages()[0]
        self.assertIn(str(checkout), message)
        self.assertIn("A checkout belongs on /mnt/nvme", message)
        self.assertIn(f"~/src is on {MOUNT}", self.report(message))

    def test_src_on_another_device_with_room_says_nothing(self) -> None:
        """sami-devbox, 2026-11-02T19:42:14Z: /home is a bind of /mnt/nvme/home, so ~/src is on
        the 16 TiB state disk, not on /, and a 4.8 GB checkout there is where it belongs."""
        self.on_devices(src=2, mount=2, root=1)
        self.big_checkout()

        self.run_once()

        self.assertEqual([], self.messages())
        self.assertEqual([], self.envoy.published + self.envoy.sent)

    def test_src_on_another_short_device_is_named_without_root_disk_advice(self) -> None:
        self.owned_by_a_live_session()
        self.on_devices(src=2, mount=2, root=1, free=10 * WATCH.GIB)
        checkout = self.big_checkout()

        self.run_once()

        found = [text for text in self.messages() if str(checkout) in text]
        self.assertEqual(1, len(found))
        self.assertNotIn("A checkout belongs on /mnt/nvme", found[0])
        self.assertNotIn("agent-workspace", found[0])
        self.assertIn("That disk is short", found[0])
        self.assertIn(f"~/src is on {MOUNT}", self.report(found[0]))


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
            "urgent_horizon": WATCH.URGENT_HORIZON,
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

    def test_the_durable_disk_is_watched_for_free_space_not_only_for_iops(self) -> None:
        """It had an IOPS cap and no floor, so every run measured it and none watched it.

        A device this watch reads operations from and never reads free space from is the gap
        that made this test: on 2026-11-08 /mnt/durable sat at 160 GiB of 3.9 TiB, falling,
        with nothing able to alert on it.
        """
        original = WATCH.os.path.ismount
        WATCH.os.path.ismount = lambda mount: mount in {"/mnt/durable", "/mnt/scratch"}
        try:
            mounts = WATCH.default_mounts()
        finally:
            WATCH.os.path.ismount = original
        self.assertIn("/mnt/durable", mounts)
        self.assertIn("/mnt/durable", WATCH.FLOORS)
        # Every mount whose operations are measured must also have its free space measured.
        self.assertEqual([], [mount for mount in WATCH.DEFAULT_IOPS_CAPS if mount not in mounts])

    def test_a_machine_without_those_disks_watches_neither(self) -> None:
        original = WATCH.os.path.ismount
        WATCH.os.path.ismount = lambda mount: False
        try:
            mounts = WATCH.default_mounts()
        finally:
            WATCH.os.path.ismount = original
        self.assertEqual(["/", "/mnt/nvme"], mounts)


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


class WriterFilesystemEvidenceTest(unittest.TestCase):
    """Which filesystem a writer's bytes land on, decided by device id and not by a path.

    On 2026-11-01T05:18Z the / alert listed pytest workers whose argv named a /home path that
    is a symlink onto another disk, and the SRE paused them; the real / consumer was a `git
    worktree add` into ~/src. These tests run real processes, because the evidence is their
    /proc entries: a path in the argv proves nothing, the st_dev of the cwd and of each open
    file proves it.
    """

    HOLD = (
        "import sys, time\n"
        "handle = open(sys.argv[1], 'wb')\n"
        "handle.write(b'x' * 4096)\n"
        "handle.flush()\n"
        "print('ready', flush=True)\n"
        "time.sleep(120)\n"
    )

    def setUp(self) -> None:
        self.here = tempfile.TemporaryDirectory()
        self.addCleanup(self.here.cleanup)
        # /dev/shm is a tmpfs, so it is a different device on every Linux box, which is what
        # this needs: /mnt/nvme exists only here.
        if not os.path.isdir("/dev/shm") or os.stat("/dev/shm").st_dev == os.stat(self.here.name).st_dev:
            self.skipTest("no second filesystem to put the other writer on")
        self.other = tempfile.TemporaryDirectory(dir="/dev/shm")
        self.addCleanup(self.other.cleanup)
        self.device = os.stat(self.here.name).st_dev
        # The shape that misled the SRE: a path under the alerting mount that is a symlink
        # onto the other disk, so the argv says /here and every byte goes to the other device.
        self.through_symlink = Path(self.here.name) / "home-link"
        self.through_symlink.symlink_to(self.other.name)
        WATCH.PANES.clear()
        WATCH.PANES_READ = True
        WATCH.LIVE_SESSIONS.clear()
        WATCH.LIVE_READ = True

        def restore() -> None:
            WATCH.PANES.clear()
            WATCH.PANES_READ = False
            WATCH.LIVE_SESSIONS.clear()
            WATCH.LIVE_READ = False

        self.addCleanup(restore)

    def writer(self, cwd: str, target: str) -> int:
        """A live process with `cwd` as its working directory and `target` open for writing."""
        process = subprocess.Popen(
            [sys.executable, "-c", self.HOLD, target],
            cwd=cwd,
            # pytest's capture gives this process a temporary file as stderr, which the child
            # would inherit as an open file on whichever disk TMPDIR is on. The test is about
            # the file the writer itself opened, so the child gets no inherited one.
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self.addCleanup(process.wait)
        self.addCleanup(process.kill)
        self.assertEqual("ready\n", process.stdout.readline())
        return process.pid

    def test_an_argv_path_on_the_mount_is_not_evidence_the_bytes_land_there(self) -> None:
        pid = self.writer(self.other.name, str(self.through_symlink / "output.log"))
        found = WATCH.mount_evidence(pid, self.device)
        self.assertFalse(found["on_mount"])
        self.assertEqual("", found["fd_error"])
        self.assertEqual(self.other.name, found["cwd"])

    def test_an_open_file_on_the_mount_is_evidence_even_from_another_cwd(self) -> None:
        target = Path(self.here.name) / "output.log"
        pid = self.writer(self.other.name, str(target))
        found = WATCH.mount_evidence(pid, self.device)
        self.assertTrue(found["on_mount"])
        self.assertIn(str(target), found["example"])

    def test_a_cwd_on_the_mount_is_evidence_with_no_open_file_there(self) -> None:
        pid = self.writer(self.here.name, str(Path(self.other.name) / "output.log"))
        found = WATCH.mount_evidence(pid, self.device)
        self.assertTrue(found["on_mount"])
        self.assertIn(self.here.name, found["example"])

    def test_a_writer_whose_fds_cannot_be_read_says_so(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("root reads every /proc/<pid>/fd")
        found = WATCH.mount_evidence(1, self.device)
        self.assertFalse(found["on_mount"])
        self.assertIn("open files could not be read", found["fd_error"])

    def test_a_writer_that_exited_during_the_sample_is_not_called_unreadable(self) -> None:
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait()
        found = WATCH.mount_evidence(gone.pid, self.device)
        self.assertFalse(found["on_mount"])
        self.assertIn("exited during the sample", found["fd_error"])

    def test_a_file_held_read_only_does_not_put_a_writer_on_the_mount(self) -> None:
        """The 05:18Z shape in its second form: every pytest worker holds its interpreter,
        which is on /, open read-only while all of its output goes to the other disk."""
        interpreter = Path(self.here.name) / "python-copy"
        interpreter.write_bytes(b"y" * 8192)
        hold = (
            "import sys, time\n"
            "read = open(sys.argv[2], 'rb')\n"
            "handle = open(sys.argv[1], 'wb')\n"
            "handle.write(b'x' * 4096)\n"
            "handle.flush()\n"
            "print('ready', flush=True)\n"
            "time.sleep(120)\n"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", hold, str(Path(self.other.name) / "output.log"), str(interpreter)],
            cwd=self.other.name,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self.addCleanup(process.wait)
        self.addCleanup(process.kill)
        self.assertEqual("ready\n", process.stdout.readline())

        found = WATCH.mount_evidence(process.pid, self.device)
        self.assertFalse(found["on_mount"])
        self.assertIn(str(interpreter), found["read_only"])
        text = WATCH.writers_text(
            WATCH.describe_writers([{"pid": process.pid, "written": WATCH.GIB}], self.device, 15.0, 8),
            denied=0,
            seconds=15.0,
            mount=self.here.name,
            privileged=True,
        )
        self.assertIn(f"nothing open for writing on {self.here.name}", text)

    def test_the_alert_lists_the_mounts_writers_first_and_the_others_under_a_heading(self) -> None:
        elsewhere = self.writer(self.other.name, str(self.through_symlink / "output.log"))
        target = Path(self.here.name) / "output.log"
        on_mount = self.writer(self.other.name, str(target))
        described = WATCH.describe_writers(
            # The misleading order of the 05:18Z alert: the writer on another disk wrote more.
            [{"pid": elsewhere, "written": 9 * WATCH.GIB}, {"pid": on_mount, "written": 1 * WATCH.GIB}],
            self.device,
            15.0,
            8,
        )
        text = WATCH.writers_text(described, denied=0, seconds=15.0, mount=self.here.name, privileged=True)

        heading = text.index(f"Writing elsewhere, not on {self.here.name}")
        self.assertLess(text.index(f"pid {on_mount}"), heading)
        self.assertGreater(text.index(f"pid {elsewhere}"), heading)
        self.assertIn(f"on {self.here.name}: {target}", text)
        self.assertIn(f"no cwd or open file on {self.here.name}", text)

    def test_the_ranking_inside_each_group_is_the_write_traffic_order(self) -> None:
        small = self.writer(self.here.name, str(Path(self.here.name) / "small.log"))
        large = self.writer(self.here.name, str(Path(self.here.name) / "large.log"))
        described = WATCH.describe_writers(
            [{"pid": large, "written": 8 * WATCH.GIB}, {"pid": small, "written": 2 * WATCH.GIB}],
            self.device,
            15.0,
            8,
        )
        text = WATCH.writers_text(described, denied=0, seconds=15.0, mount=self.here.name, privileged=True)

        self.assertNotIn("Writing elsewhere", text)
        self.assertLess(text.index(f"pid {large}"), text.index(f"pid {small}"))


class DiskFillWatchIopsCapTest(unittest.TestCase):
    """The IOPS of a capped mount's device, sampled every run against the previous run.

    /mnt/durable is an Azure P50 data disk, capped at 7,500 completed operations a second. On
    2026-10-31T21:58Z sami-rocqperf went down with it held at that cap for hours
    (dispatch://INFRA-586) and nothing sampled the device, so "minutes at the cap" could not be
    counted afterwards.
    """

    MOUNT = "/mnt/durable"
    CAP = 7500.0
    # Two readings of /proc/diskstats, 300 s apart. Fields: major, minor, name, reads
    # completed, ... , writes completed (field 8). The data disk is 259:1 and the array
    # 9:127, which carries its own line and is never summed from its members.
    FIRST = "\n".join(
        [
            " 259       0 nvme0n1 100 0 0 0 200 0 0 0 0 0 0",
            " 259       1 nvme0n2 1000 10 0 0 2000 20 0 0 0 0 0",
            "   9     127 md127 50 0 0 0 60 0 0 0 0 0 0",
        ]
    )
    SECOND = "\n".join(
        [
            " 259       0 nvme0n1 140 0 0 0 260 0 0 0 0 0 0",
            " 259       1 nvme0n2 751000 10 0 0 1502000 20 0 0 0 0 0",
            "   9     127 md127 90 0 0 0 110 0 0 0 0 0 0",
        ]
    )

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state.json"
        self.envoy = FakeEnvoy()
        self.now = 1_000_000.0
        self.ops = 5_000_000

        original_envoy = WATCH.Envoy
        original_ops = WATCH.device_ops
        original_writers = WATCH.writers_now
        original_time = WATCH.time.time
        WATCH.Envoy = lambda timeout=8.0: self.envoy
        # device_ops reports reads and writes apart, so the caller can say which side a device
        # at its cap is spending itself on. These cases are write-bound: all of self.ops are
        # writes, which keeps their totals what they were.
        WATCH.device_ops = lambda mount: ("nvme0n2", 0, self.ops)
        WATCH.writers_now = lambda options, device, mount, deadline, measure="bytes": (
            "Top writers now: (none in this test)",
            [],
        )
        WATCH.time.time = lambda: self.now

        def restore() -> None:
            WATCH.Envoy = original_envoy
            WATCH.device_ops = original_ops
            WATCH.writers_now = original_writers
            WATCH.time.time = original_time

        self.addCleanup(restore)

    def options(self, **overrides) -> argparse.Namespace:
        values = dict(
            DiskFillWatchAlertRoutingTest.options(self).__dict__,
            mount=[],  # the IOPS sample is on its own mounts, and no disk is filling here
            tmp="/nonexistent-tmp",
            src="/nonexistent-src",
            iops_caps={self.MOUNT: self.CAP},
        )
        values.update(overrides)
        return argparse.Namespace(**values)

    def step(self, iops: float, seconds: float = 300.0, **overrides) -> str:
        """One scheduled run, `seconds` after the last, with the device at `iops`."""
        self.now += seconds
        self.ops += int(iops * seconds)
        printed = io.StringIO()
        # log() writes to the module's own LOG_STREAM, which is bound at import.
        with unittest.mock.patch.object(WATCH, "LOG_STREAM", printed):
            with contextlib.redirect_stdout(printed):
                self.assertEqual(0, WATCH.run_once(self.options(**overrides)))
        return printed.getvalue()

    def messages(self) -> list[str]:
        return [text for _, text in self.envoy.published + self.envoy.sent]

    def test_the_iops_figure_comes_from_two_diskstats_readings(self) -> None:
        before = WATCH.diskstats_rows(self.FIRST)[(259, 1)]
        after = WATCH.diskstats_rows(self.SECOND)[(259, 1)]
        # Reads and writes come back apart. The cap is on their sum, and the rate below still
        # uses the sum; keeping them separate is what lets the alert say which side a device at
        # its cap is spending itself on, which decides what a person does about it.
        self.assertEqual(("nvme0n2", 1000, 2000), before)
        self.assertEqual(("nvme0n2", 751_000, 1_502_000), after)
        self.assertEqual(3000, before[1] + before[2])
        self.assertEqual(2_253_000, after[1] + after[2])

        rate, span = WATCH.iops_rate(
            {"at": 1000.0, "ops": before[1] + before[2]}, after[1] + after[2], 1300.0
        )

        self.assertEqual(300.0, span)
        self.assertAlmostEqual(7500.0, rate)

    def test_an_array_is_read_from_its_own_line_not_from_its_members(self) -> None:
        self.assertEqual(("md127", 50, 60), WATCH.diskstats_rows(self.FIRST)[(9, 127)])

    def test_a_counter_that_went_backwards_produces_no_figure(self) -> None:
        """A reboot, or another device under the mount: the counters restart at zero."""
        rate, _ = WATCH.iops_rate({"at": 1000.0, "ops": 2_253_000}, 3000, 1300.0)
        self.assertIsNone(rate)

    def test_the_first_run_records_the_counters_and_the_figure_comes_next_run(self) -> None:
        self.assertIn("counters recorded", self.step(self.CAP))
        self.assertEqual([], self.messages())
        self.assertEqual(5_000_000 + 2_250_000, json.loads(self.state.read_text())["iops"][self.MOUNT]["ops"])

    def test_the_run_logs_the_iops_figure_whether_or_not_anything_is_wrong(self) -> None:
        self.step(100.0)
        line = self.step(100.0)
        # The log carries the read/write split beside the total, so a device at its cap can be
        # told apart from one merely busy on the side this watch cannot attribute.
        self.assertIn(f"{self.MOUNT}: 100 IOPS over the last 300 s, 0 read + 100 write, cap 7500", line)
        self.assertEqual([], self.messages())

    def test_three_consecutive_runs_at_the_cap_alert_once(self) -> None:
        self.step(self.CAP)  # the first run has nothing to subtract from
        self.step(self.CAP)  # one figure at the cap
        self.step(self.CAP)  # two
        self.assertEqual([], self.messages())

        self.step(self.CAP)  # three in a row: said once

        self.assertEqual(1, len(self.envoy.published))
        self.assertEqual(WATCH.BOX_HEALTH_ROLE_TOPIC, self.envoy.published[0][0])
        text = self.envoy.published[0][1]
        self.assertIn("DISK AT ITS IOPS CAP", text)
        self.assertIn("100% of its 7500 cap", text)
        self.assertIn("for three runs since", text)
        report = Path(text.rsplit("Full report: ", 1)[1].strip()).read_text()
        self.assertIn("7500 IOPS this device is provisioned for", report)
        self.assertIn("Three consecutive runs", report)
        self.assertIn("Top writers now", report)

        self.step(self.CAP)  # and not again while the crossing stays open
        self.assertEqual(1, len(self.envoy.published))

    def test_a_run_at_ninety_percent_of_the_cap_counts_as_at_the_cap(self) -> None:
        for _ in range(4):
            self.step(0.90 * self.CAP)
        self.assertEqual(1, len(self.envoy.published))

    def test_a_run_between_the_two_fractions_neither_alerts_nor_clears(self) -> None:
        self.step(self.CAP)
        self.step(self.CAP)
        self.step(self.CAP)
        self.step(0.85 * self.CAP)  # breaks the run of three
        self.step(self.CAP)
        self.assertEqual([], self.messages())

        self.step(self.CAP)
        self.step(self.CAP)
        self.assertEqual(1, len(self.envoy.published))

        self.step(0.85 * self.CAP)  # and does not close the crossing either
        self.assertEqual(1, len(self.envoy.published))

    def test_a_run_under_eighty_percent_clears_the_crossing(self) -> None:
        for _ in range(4):
            self.step(self.CAP)
        self.assertEqual(1, len(self.envoy.published))

        self.step(0.5 * self.CAP)

        self.assertEqual(2, len(self.envoy.published))
        clear = self.envoy.published[1][1]
        self.assertIn("DISK IOPS CLEARED", clear)
        self.assertIn("3750 IOPS", clear)
        self.assertIn("highest figure sampled was 7500 IOPS", clear)
        self.assertNotIn("alerted_at", json.dumps(json.loads(self.state.read_text())["iops"]))

        for _ in range(4):  # a second crossing is a new alert
            self.step(self.CAP)
        self.assertEqual(3, len(self.envoy.published))

    def test_a_dry_run_sends_nothing(self) -> None:
        for _ in range(4):
            self.step(self.CAP, dry_run=True, write_state=True)
        self.assertEqual([], self.messages())

    def test_a_mount_with_no_block_device_is_skipped(self) -> None:
        WATCH.device_ops = lambda mount: None
        self.assertIn("no block device of its own", self.step(self.CAP))
        self.assertEqual([], self.messages())


if __name__ == "__main__":
    unittest.main()
