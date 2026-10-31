#!/usr/bin/env python3
"""Who hears a crossing of omp-memory-watch: the session itself and the box-health owner.

Box health on these boxes belongs to the SRE session, not to the chief of staff (Sami,
2026-10-31: "There's an SRE. They can handle it."), so the box-health owner is addressed by the
`lead-box-health` Envoy role, and the SRE session hears it when nobody holds the role. No
message of a crossing may reach the chief of staff's session.

Every cgroup here is a synthetic directory tree, so the tests read no real cgroup.
"""

import argparse
import importlib.machinery
import importlib.util
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "omp-memory-watch"
LOADER = importlib.machinery.SourceFileLoader("omp_memory_watch_recipients", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
WATCH = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(WATCH)

GIB = WATCH.GIB
CEILING = 24 * GIB
SESSION = "00000001-0000-0000-0000-000000000001"
# Neither of these may hear a box alert any more (Sami, 2026-10-31): the chief of staff's
# session, and the INFRA lead's, which owned this detector and handed box health to the SRE.
CHIEF_OF_STAFF = "01a13dca-e979-7264-af32-f654ef57a6f5"
INFRA_LEAD = "01a13c57-d826-7242-80fe-5f0670188b11"


class FakeEnvoy:
    """Records what left, and can answer non-200 for an unheld role or a refused send."""

    instances: list = []
    publish_ok = True
    send_ok = True

    def __init__(self, timeout: float = 8.0) -> None:
        self.published: list[tuple[str, str]] = []
        self.sent: list[tuple[str, str]] = []
        FakeEnvoy.instances.append(self)

    def request(self, method: str, path: str, body=None):
        return 500, {"error": "no answer in this test"}

    def publish(self, topic: str, message: str) -> bool:
        self.published.append((topic, message))
        return FakeEnvoy.publish_ok

    def send(self, session_id: str, message: str) -> bool:
        self.sent.append((session_id, message))
        return FakeEnvoy.send_ok


def options(state: Path, **overrides) -> argparse.Namespace:
    values = {
        "fraction": 0.70,
        "clear_fraction": 0.62,
        "sample_fraction": 0.45,
        "samples": 24,
        "unlimited_warn": 16.8,
        "grace": 0.0,
        "escalate": 0.0,
        "wall_fraction": 0.98,
        "wall_cooldown": 900.0,
        "wall_digest": 900.0,
        "wall_top": 3,
        "wall_pending_max": 400,
        "state": state,
        "http_timeout": 8.0,
        "dry_run": False,
        "write_state": False,
        "print_alert": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class CrossingRecipientsTest(unittest.TestCase):
    """One scope that crosses, escalates and clears, over several runs."""

    SCOPE = "run-rmem1.scope"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        FakeEnvoy.instances = []
        FakeEnvoy.publish_ok = True
        FakeEnvoy.send_ok = True
        for name, replacement in (
            ("CGROUP_ROOT", self.root),
            ("Envoy", FakeEnvoy),
            ("cmdline_of", lambda pid: "omp --resume x"),
            ("rss_of", lambda pid: 3 * GIB),
            ("pid_start", lambda pid: 7),
            ("session_of", lambda pid: SESSION),
            ("self_watch_of", lambda cgroup, pid: ""),
            ("self_watch_anywhere", lambda pid: ""),
            ("omp_processes", lambda: [(11, self.cgroup)]),
        ):
            original = getattr(WATCH, name)
            setattr(WATCH, name, replacement)
            self.addCleanup(lambda n=name, o=original: setattr(WATCH, n, o))
        self.state = self.root / "state.json"
        self.cgroup = self.write_scope(20 * GIB)

    def write_scope(self, anon: int) -> str:
        """A synthetic cgroup v2 scope whose unreclaimable memory is `anon`."""
        cgroup = f"/user.slice/user-1001.slice/user@1001.service/app.slice/{self.SCOPE}"
        directory = self.root / cgroup.strip("/")
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "memory.max").write_text(f"{CEILING}\n")
        (directory / "memory.current").write_text(f"{anon}\n")
        (directory / "memory.peak").write_text(f"{anon}\n")
        (directory / "memory.swap.max").write_text("max\n")
        (directory / "memory.events").write_text("low 0\nhigh 0\nmax 0\noom 0\noom_kill 0\noom_group_kill 0\n")
        (directory / "memory.stat").write_text(f"anon {anon}\nfile 0\nkernel 0\nsock 0\nshmem 0\n")
        (directory / "cgroup.procs").write_text("11\n")
        return cgroup

    def run_pass(self, anon: int, **overrides) -> None:
        self.write_scope(anon)
        WATCH.run_once(options(self.state, **overrides))

    def everything(self) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        sent = [row for envoy in FakeEnvoy.instances for row in envoy.sent]
        published = [row for envoy in FakeEnvoy.instances for row in envoy.published]
        return sent, published

    def test_the_first_alert_goes_to_the_session_and_the_box_health_role(self) -> None:
        self.run_pass(20 * GIB)
        sent, published = self.everything()
        self.assertEqual([SESSION], [who for who, _ in sent])
        self.assertEqual([WATCH.BOX_HEALTH_ROLE_TOPIC], [topic for topic, _ in published])

    def test_the_escalation_goes_to_the_box_health_role(self) -> None:
        self.run_pass(20 * GIB)
        self.run_pass(21 * GIB)
        sent, published = self.everything()
        self.assertEqual(
            [False, True], [("STILL RISING" in text) for _, text in published]
        )
        self.assertEqual({WATCH.BOX_HEALTH_ROLE_TOPIC}, {topic for topic, _ in published})
        self.assertEqual([SESSION], [who for who, _ in sent])

    def test_an_unheld_role_sends_the_escalation_to_the_sre_session(self) -> None:
        self.run_pass(20 * GIB)
        FakeEnvoy.publish_ok = False
        self.run_pass(21 * GIB)
        sent, _published = self.everything()
        self.assertEqual(
            [WATCH.SRE], [who for who, text in sent if "STILL RISING" in text]
        )

    def test_the_clear_reaches_each_recipient_once(self) -> None:
        self.run_pass(20 * GIB)
        self.run_pass(21 * GIB)
        self.run_pass(2 * GIB)
        sent, published = self.everything()
        clears_sent = [who for who, text in sent if "CLEARED" in text]
        clears_published = [topic for topic, text in published if "CLEARED" in text]
        self.assertEqual([SESSION], clears_sent)
        self.assertEqual([WATCH.BOX_HEALTH_ROLE_TOPIC], clears_published)

    def test_no_message_of_a_whole_cycle_reaches_the_chief_or_the_infra_lead(self) -> None:
        """Alert, escalation and clear, with the role held and then unheld."""
        self.run_pass(20 * GIB)
        self.run_pass(21 * GIB)
        self.run_pass(2 * GIB)
        FakeEnvoy.publish_ok = False
        self.run_pass(20 * GIB)
        self.run_pass(21 * GIB)
        self.run_pass(2 * GIB)
        sent, published = self.everything()
        addressed = [who for who, _ in sent] + [topic for topic, _ in published]
        self.assertTrue(addressed)
        self.assertNotIn(INFRA_LEAD, addressed)
        self.assertNotIn(CHIEF_OF_STAFF, addressed)


class BoxHealthOwnerTest(unittest.TestCase):
    """A machine whose box health belongs to one session: the lane VMs' fleet monitoring owner."""

    OWNER = "01a14c3d-9d7f-7079-97aa-f810a884c050"
    SCOPE = CrossingRecipientsTest.SCOPE

    # The crossing harness, without its tests: those assert on the role publish, and under
    # BOX_HEALTH_OWNER there is none.
    write_scope = CrossingRecipientsTest.write_scope
    run_pass = CrossingRecipientsTest.run_pass
    everything = CrossingRecipientsTest.everything

    def setUp(self) -> None:
        CrossingRecipientsTest.setUp(self)
        patch = unittest.mock.patch.dict(os.environ, {"BOX_HEALTH_OWNER": self.OWNER})
        patch.start()
        self.addCleanup(patch.stop)

    def test_alert_escalation_and_clear_all_go_to_the_owner_and_nothing_is_published(self) -> None:
        self.run_pass(20 * GIB)
        self.run_pass(21 * GIB)
        self.run_pass(2 * GIB)
        sent, published = self.everything()
        self.assertEqual([], published)
        self.assertEqual({SESSION, self.OWNER}, {who for who, _ in sent})
        owned = [text for who, text in sent if who == self.OWNER]
        self.assertTrue([text for text in owned if "STILL RISING" not in text and "CLEARED" not in text])
        self.assertTrue([text for text in owned if "STILL RISING" in text])
        self.assertTrue([text for text in owned if "CLEARED" in text])

    def test_an_unusable_owner_is_refused_at_start(self) -> None:
        with unittest.mock.patch.dict(os.environ, {"BOX_HEALTH_OWNER": "lead-box-health"}):
            with self.assertRaises(SystemExit) as refused:
                WATCH.box_health_owner()
        self.assertIn("BOX_HEALTH_OWNER", str(refused.exception))


if __name__ == "__main__":
    unittest.main()
