#!/usr/bin/env python3
"""Regression tests for who hears a per-entry "NEW BIG /tmp ENTRY" notice.

A /tmp entry belongs to one session's work, and that session is the only reader who can remove
it. The box-health owner cannot: on 2026-10-31 it received notices naming other sessions
(01a1809b on ossv-agent-open, 01a18c07 on open5) that it could do nothing with.

So a notice goes to its own live owner. Two exceptions keep it with the box-health owner: no
live session on this box matches the entry, and a disk that is itself short — below
ENTRY_OWNER_SHORT free the entry is the box's problem whoever made it. Box-level crossings
(CPU, memory, swap, disk) are unchanged and always go to the box-health owner.
"""

import argparse
import importlib.machinery
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "disk-fill-watch"
LOADER = importlib.machinery.SourceFileLoader("disk_fill_watch_entry_owner", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
WATCH = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(WATCH)

MOUNT = "/testmount"
LANE = "01a1809b-4a90-7381-9c3a-7e9dd1744d97"
ELSEWHERE = "01a14b36-227b-7141-acdb-b5397251a0b1"
OWNER = "01a14c3d-9d7f-7079-97aa-f810a884c050"


class FakeEnvoy:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.published: list[tuple[str, str]] = []
        self.send_ok = True

    def send(self, session_id: str, message: str) -> bool:
        self.sent.append((session_id, message))
        return self.send_ok

    def publish(self, topic: str, message: str) -> bool:
        self.published.append((topic, message))
        return True

    def request(self, method: str, path: str, body=None):
        return 500, {"error": "no live-session answer in this test"}


class EntryOwnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state.json"
        self.scratch = Path(self.tmp.name) / "tmp"
        self.scratch.mkdir()
        self.envoy = FakeEnvoy()
        self.free = 800 * WATCH.GIB
        self.owner_answer = ""

        previous = os.environ.get("BOX_HEALTH_OWNER")
        os.environ["BOX_HEALTH_OWNER"] = OWNER

        def restore_env() -> None:
            if previous is None:
                os.environ.pop("BOX_HEALTH_OWNER", None)
            else:
                os.environ["BOX_HEALTH_OWNER"] = previous

        self.addCleanup(restore_env)

        for name, replacement in (
            ("Envoy", lambda timeout=8.0: self.envoy),
            ("free_bytes", lambda mount: (self.free, 1000 * WATCH.GIB)),
            ("device_of", lambda path: 1),
            ("writers_now", lambda options, device, mount: "writers: (none in this test)"),
            ("owner_of_path", lambda path: self.owner_answer),
            ("refresh_live_sessions", lambda envoy: self.fake_live()),
        ):
            original = getattr(WATCH, name)
            setattr(WATCH, name, replacement)
            self.addCleanup(lambda n=name, o=original: setattr(WATCH, n, o))
        self.addCleanup(self.reset_live)
        self.reset_live()
        # One young, big entry for the rule to find.
        # Over --tmp-digest-max, so an unowned one is still a message of its own unless a
        # test lowers that. The digest path has tests of its own below.
        (self.scratch / "rev1").write_bytes(b"x" * 1_000_000)
        self.big = 3 * WATCH.GB

    def reset_live(self) -> None:
        WATCH.LIVE_SESSIONS.clear()
        WATCH.LIVE_READ = False

    def fake_live(self, *ids: str) -> None:
        WATCH.LIVE_SESSIONS.clear()
        WATCH.LIVE_SESSIONS.update({session_id: {"session_id": session_id} for session_id in self.live})
        WATCH.LIVE_READ = True

    live: tuple = (LANE,)

    def options(self, **overrides) -> argparse.Namespace:
        values = {
            "mount": [MOUNT], "horizon": 7200.0, "rate_window": 300.0, "min_span": 300.0,
            "low_slack": 2.0, "default_floor": 20.0,
            "max_floor_fraction": WATCH.MAX_FLOOR_FRACTION, "clear_factor": 1.5,
            "grace": 0.0, "escalate": 1800.0, "history": 10800.0, "samples": 400,
            "confirmations": 3, "confirmation_spacing": 300.0, "writer_sample": 15.0,
            "top_writers": 8, "sudo": "never", "writers_json": None,
            "tmp": str(self.scratch), "tmp_mount": MOUNT, "tmp_age": 21600.0, "tmp_big": 0,
            "tmp_walk_budget": 20.0, "tmp_idle_walk_budget": 5.0, "state": self.state,
            "tmp_digest": 3600.0, "tmp_digest_max": WATCH.ENTRY_DIGEST_MAX, "tmp_pending_max": 500,
            "http_timeout": 8.0, "dry_run": False, "write_state": False, "print_alert": False,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def run_once(self, **overrides) -> None:
        self.assertEqual(0, WATCH.run_once(self.options(**overrides)))

    def notices(self) -> list[tuple[str, str]]:
        return [(who, text) for who, text in self.envoy.sent if "NEW BIG /tmp ENTRY" in text]

    def test_a_notice_goes_to_its_own_live_owner(self) -> None:
        self.owner_answer = LANE
        self.run_once()
        self.assertEqual([who for who, _ in self.notices()], [LANE])
        self.assertIn("This entry is yours", self.notices()[0][1])

    def test_an_owner_that_is_not_live_here_falls_back_to_the_box_health_owner(self) -> None:
        """A big unowned entry is still a message of its own."""
        self.owner_answer = ELSEWHERE
        self.run_once(tmp_digest_max=0)
        self.assertEqual([who for who, _ in self.notices()], [OWNER])
        self.assertIn("no live session on this box could be matched", self.notices()[0][1])

    def test_a_pid_only_owner_falls_back_to_the_box_health_owner(self) -> None:
        self.owner_answer = "pid 1234 (python3)"
        self.run_once(tmp_digest_max=0)
        self.assertEqual([who for who, _ in self.notices()], [OWNER])

    def test_a_short_disk_keeps_the_notice_with_the_box_health_owner(self) -> None:
        """A disk with little left is the box's problem, whoever made the entry."""
        self.owner_answer = LANE
        self.free = 10 * WATCH.GIB  # 1% of the 1000 GiB total, and under the 100 GB floor
        self.run_once()
        self.assertEqual([who for who, _ in self.notices()], [OWNER])
        self.assertIn("that is short for a disk this size", self.notices()[0][1])

    def test_a_big_disk_with_room_is_not_short(self) -> None:
        """ossv-agent-open2: 412 GB free of 3.9 TB read as short under the old flat 15%."""
        self.owner_answer = LANE
        self.free = 412 * WATCH.GB
        self.run_once(tmp_digest_max=0)
        self.assertEqual([who for who, _ in self.notices()], [LANE])

    def test_a_small_unowned_entry_waits_for_the_digest(self) -> None:
        import json

        self.owner_answer = ""
        self.run_once()
        self.assertEqual(self.notices(), [])
        kept = json.loads(self.state.read_text())
        self.assertEqual(len(kept["tmp_pending"]), 1)
        self.assertEqual(kept["tmp_pending"][0]["bytes"], 1_000_000)

    def test_the_digest_goes_out_as_one_message_with_a_line_each(self) -> None:
        self.owner_answer = ""
        (self.scratch / "rev2").write_bytes(b"y" * 2_000_000)
        self.run_once(tmp_digest=0.0)
        digests = [text for _who, text in self.envoy.sent if "SMALL UNOWNED /tmp ENTRIES" in text]
        self.assertEqual(len(digests), 1)
        self.assertEqual([who for who, text in self.envoy.sent if "SMALL UNOWNED" in text], [OWNER])
        self.assertIn("rev1", digests[0])
        self.assertIn("rev2", digests[0])
        self.assertIn("2 born in the last", digests[0])

    def test_a_small_entry_with_a_live_owner_is_not_held_back(self) -> None:
        """Only the entries nobody can be sent to wait; an owner hears about its own at once."""
        self.owner_answer = LANE
        self.run_once()
        self.assertEqual([who for who, _ in self.notices()], [LANE])

    def test_a_small_unowned_entry_on_a_short_disk_is_not_held_back(self) -> None:
        self.owner_answer = ""
        self.free = 10 * WATCH.GIB
        self.run_once()
        self.assertEqual([who for who, _ in self.notices()], [OWNER])

    def test_an_owner_that_refuses_the_send_is_not_a_dropped_notice(self) -> None:
        self.owner_answer = LANE
        self.envoy.send_ok = False
        self.run_once()
        self.assertEqual([who for who, _ in self.notices()], [LANE, OWNER])

    def test_a_box_crossing_still_goes_to_the_box_health_owner(self) -> None:
        """The disk rule itself is about the box, so the routing above does not touch it."""
        self.owner_answer = LANE
        self.free = 1 * WATCH.GIB
        self.run_once()
        crossings = [who for who, text in self.envoy.sent if "NEW BIG /tmp ENTRY" not in text]
        self.assertTrue(crossings)
        self.assertEqual(set(crossings), {OWNER})

    def test_each_entry_is_said_once(self) -> None:
        self.owner_answer = LANE
        self.run_once()
        self.run_once()
        self.assertEqual(len(self.notices()), 1)
        kept = json.loads(self.state.read_text())
        self.assertEqual(len(kept.get("named_tmp", [])), 1)


class LiveOwnerIdTest(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(self.reset)
        self.reset()

    def reset(self) -> None:
        WATCH.LIVE_SESSIONS.clear()
        WATCH.LIVE_READ = False

    def test_only_an_id_live_on_this_box_is_an_address(self) -> None:
        WATCH.LIVE_SESSIONS.update({LANE: {}})
        WATCH.LIVE_READ = True
        self.assertEqual(WATCH.live_owner_id(LANE), LANE)
        self.assertEqual(WATCH.live_owner_id(ELSEWHERE), "")

    def test_nothing_is_an_address_before_the_live_list_is_read(self) -> None:
        self.assertEqual(WATCH.live_owner_id(LANE), "")

    def test_a_pid_answer_names_no_session(self) -> None:
        WATCH.LIVE_READ = True
        self.assertEqual(WATCH.live_owner_id("pid 1234 (python3)"), "")
        self.assertEqual(WATCH.live_owner_id(""), "")


if __name__ == "__main__":
    unittest.main()
