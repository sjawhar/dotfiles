#!/usr/bin/env python3
"""Who omp-memory-watch tells, how often, and how long each message is (dispatch://INFRA-672).

Measured on sami-rocqperf over 48 h (notify review, 2026-11-03): the box-health owner got 95
messages from this watch. 13 of 14 immediate wall notes were repeated by the 15-minute digest
within 2 minutes, 41 of 52 wall messages had no reaction, and every session NEAR alert and its
CLEARED went to the box-health owner too, though 14 of 20 cleared within 4 minutes. A NEAR body
was about 2 KB.

So: a wall touch is one message, to its owning session; the box-health role hears a wall touch
only when 3 or more touch in 15 minutes on the box, or when the scope has no owning session. A
NEAR goes to its session; the role gets a copy only when the scope is still rising 10 minutes
after the alert, or when the kernel OOM-kills in it; a CLEARED never goes to the role. Every
message is at most 600 characters and ends with `Full report: <path>`.

Every cgroup here is a synthetic directory tree and the clock is the test's, so the tests read
no real cgroup and run anywhere with python3.
"""

import argparse
import importlib.machinery
import importlib.util
import json
import os
import re
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "omp-memory-watch"
LOADER = importlib.machinery.SourceFileLoader("omp_memory_watch_notify", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
WATCH = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(WATCH)

GIB = WATCH.GIB
CEILING = 24 * GIB
ROLE = WATCH.BOX_HEALTH_ROLE_TOPIC
REPORT_RE = re.compile(r"\nFull report: (/\S+)\Z")


def sid(pid: int) -> str:
    return f"0000000{pid}-0000-0000-0000-00000000000{pid}"


class FakeEnvoy:
    """Records what left. GET /v1/sessions fails, so a session id is used as the process names it."""

    instances: list = []
    publish_ok = True

    def __init__(self, timeout: float = 8.0) -> None:
        self.sent: list[tuple[str, str]] = []
        self.published: list[tuple[str, str]] = []
        FakeEnvoy.instances.append(self)

    def request(self, method: str, path: str, body=None):
        return 500, {"error": "no answer in this test"}

    def send(self, session_id: str, message: str) -> bool:
        self.sent.append((session_id, message))
        return True

    def publish(self, topic: str, message: str) -> bool:
        self.published.append((topic, message))
        return FakeEnvoy.publish_ok


def options(state: Path, **overrides) -> argparse.Namespace:
    values = {
        "fraction": 0.70,
        "clear_fraction": 0.62,
        "sample_fraction": 0.45,
        "samples": 24,
        "unlimited_warn": 16.8,
        "grace": 60.0,
        "escalate": 600.0,
        "wall_fraction": 0.98,
        "wall_cooldown": 900.0,
        "wall_quiet_reclaimable": 0.50,
        "wall_burst": 3,
        "wall_burst_window": 900.0,
        "wall_top": 3,
        "wall_pending_max": 400,
        "wall_digest_mode": "send",
        "wall_urgent_anon": 0.50,
        "wall_pending_json": False,
        "state": state,
        "http_timeout": 8.0,
        "dry_run": False,
        "write_state": False,
        "print_alert": False,
        # Synthetic cgroups: no acting pass may touch a real process or pane.
        "oom_bias": False,
        "oom_bias_adj": 500,
        "restart": False,
        "restart_dry_run": False,
        "restart_cooldown": 3600.0,
        "adopt_scopes": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class NotifyCase(unittest.TestCase):
    """Synthetic scopes, one pid each, a session per pid unless a test removes it, and a clock."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        FakeEnvoy.instances = []
        FakeEnvoy.publish_ok = True
        self.sessions: dict[int, str] = {pid: sid(pid) for pid in range(1, 10)}
        self.scopes: dict[str, tuple[int, str]] = {}
        self.now = time.time()
        for name, replacement in (
            ("CGROUP_ROOT", self.root),
            ("Envoy", FakeEnvoy),
            # No worker directory and no session id on the command line: the session is
            # whatever session_of says, so a test can take it away.
            ("cmdline_of", lambda pid: f"omp --lane {pid}"),
            ("rss_of", lambda pid: 3 * GIB),
            ("pid_start", lambda pid: 7),
            ("session_of", lambda pid: self.sessions.get(pid, "")),
            ("self_watch_of", lambda cgroup, pid: ""),
            ("self_watch_anywhere", lambda pid: ""),
            ("omp_processes", lambda: sorted(self.scopes.values())),
            ("log", lambda message: None),
        ):
            original = getattr(WATCH, name)
            setattr(WATCH, name, replacement)
            self.addCleanup(lambda n=name, o=original: setattr(WATCH, n, o))
        clock = unittest.mock.patch.object(WATCH.time, "time", side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.state = self.root / "state" / "state.json"

    def scope(
        self, name: str, pid: int, *, events_max: int = 0, anon: int = GIB // 2, current: int = 2 * GIB,
        peak: int | None = None, oom_kill: int = 0,
    ) -> None:
        cgroup = f"/user.slice/user-1001.slice/user@1001.service/app.slice/{name}"
        directory = self.root / cgroup.strip("/")
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "memory.max").write_text(f"{CEILING}\n")
        (directory / "memory.current").write_text(f"{current}\n")
        (directory / "memory.peak").write_text(f"{peak if peak is not None else current}\n")
        (directory / "memory.swap.max").write_text("0\n")
        (directory / "memory.events").write_text(
            f"low 0\nhigh 0\nmax {events_max}\noom 0\noom_kill {oom_kill}\noom_group_kill 0\n"
        )
        (directory / "memory.stat").write_text(
            f"anon {anon}\nfile {max(current - anon, 0)}\nkernel 0\nsock 0\nshmem 0\n"
        )
        (directory / "cgroup.procs").write_text(f"{pid}\n")
        self.scopes[name] = (pid, cgroup)

    def run_pass(self, after: float = 30.0, **overrides) -> None:
        self.now += after
        WATCH.LIVE_SESSIONS.clear()
        WATCH.LIVE_READ = False
        WATCH.run_once(options(self.state, **overrides))

    def everything(self) -> list[tuple[str, str]]:
        return [row for envoy in FakeEnvoy.instances for row in envoy.sent + envoy.published]

    def role(self) -> list[str]:
        """What the box-health owner heard: the role, or the SRE session when nobody holds it."""
        return [text for who, text in self.everything() if who in (ROLE, WATCH.SRE)]

    def to(self, who: str) -> list[str]:
        return [text for target, text in self.everything() if target == who]

    def assert_short(self, text: str) -> str:
        """At most 600 characters, ending with the report that holds the detail. Returns it."""
        self.assertLessEqual(len(text), 600, text)
        found = REPORT_RE.search(text)
        self.assertIsNotNone(found, f"no `Full report: <path>` at the end of: {text}")
        assert found is not None
        report = Path(found.group(1))
        self.assertTrue(report.is_file(), report)
        self.assertEqual(report.parent, self.state.parent / "reports")
        return report.read_text()


class WallTest(NotifyCase):
    """A wall touch is one message, to its owner; the role hears a burst or an orphan."""

    def seed(self, *names: str) -> None:
        """The first run on an empty state file seeds every counter and reports nothing."""
        for index, name in enumerate(names, start=1):
            self.scope(name, index)
        self.run_pass()
        self.assertEqual(self.everything(), [])

    def touch(self, name: str, count: int = 1, **fields) -> None:
        pid = self.scopes[name][0]
        self.scope(name, pid, events_max=count, **fields)

    def test_one_touch_with_a_live_owner_reaches_only_its_owner(self) -> None:
        self.seed("run-ra.scope")
        self.touch("run-ra.scope")
        self.run_pass()
        self.assertEqual(len(self.to(sid(1))), 1)
        self.assertEqual(self.role(), [])
        report = self.assert_short(self.to(sid(1))[0])
        # The whole report is in the file: the split, the processes, the verdict, the advice.
        self.assertIn("Where that memory is, by memory.stat:", report)
        self.assertIn("write your handover files now", report)

    def test_a_touch_already_sent_is_not_repeated_by_a_digest(self) -> None:
        """13 of 14 immediate notes were repeated by the digest within 2 min."""
        self.seed("run-ra.scope")
        # anon above half the peak: the shape that used to go to the box-health owner at once.
        self.touch("run-ra.scope", anon=int(1.8 * GIB))
        self.run_pass()
        self.run_pass(after=16 * 60)
        self.run_pass(after=60 * 60)
        self.assertEqual(len(self.to(sid(1))), 1)
        self.assertEqual(self.role(), [])

    def test_a_repeat_inside_the_cooldown_sends_nothing(self) -> None:
        self.seed("run-ra.scope")
        self.touch("run-ra.scope", 1)
        self.run_pass()
        self.touch("run-ra.scope", 5)
        self.run_pass()
        self.assertEqual(len(self.to(sid(1))), 1)
        self.assert_short(self.to(sid(1))[0])

    def test_three_touches_in_15_minutes_send_one_role_message(self) -> None:
        self.seed("run-ra.scope", "run-rb.scope", "run-rc.scope")
        for minutes, name in ((1, "run-ra.scope"), (4, "run-rb.scope"), (4, "run-rc.scope")):
            self.touch(name)
            self.run_pass(after=minutes * 60)
        for pid in (1, 2, 3):
            self.assertEqual(len(self.to(sid(pid))), 1)
        role = self.role()
        self.assertEqual(len(role), 1, role)
        self.assertIn("3 scopes", role[0])
        report = self.assert_short(role[0])
        for pid in (1, 2, 3):
            self.assertIn(sid(pid), report)

    def test_two_touches_in_15_minutes_send_the_role_nothing(self) -> None:
        self.seed("run-ra.scope", "run-rb.scope", "run-rc.scope")
        self.touch("run-ra.scope")
        self.run_pass()
        self.touch("run-rb.scope")
        self.run_pass(after=10 * 60)
        # The first touch is now 16 min old: three in all, but never three in 15 min.
        self.touch("run-rc.scope")
        self.run_pass(after=6 * 60)
        self.assertEqual(self.role(), [])

    def test_a_burst_that_goes_on_is_told_again_only_when_it_doubles(self) -> None:
        names = [f"run-r{letter}.scope" for letter in "abcdefg"]
        self.seed(*names)
        for name in names[:3]:
            self.touch(name)
            self.run_pass(after=60)
        self.assertEqual(len(self.role()), 1)
        for name in names[3:5]:
            self.touch(name)
            self.run_pass(after=60)
        self.assertEqual(len(self.role()), 1, "4 and 5 in 15 min are not a new fact after 3")
        self.touch(names[5])
        self.run_pass(after=60)
        role = self.role()
        self.assertEqual(len(role), 2)
        self.assertIn("6 scopes", role[1])

    def test_a_new_burst_after_a_quiet_window_is_told(self) -> None:
        names = [f"run-r{letter}.scope" for letter in "abcdef"]
        self.seed(*names)
        for name in names[:3]:
            self.touch(name)
            self.run_pass(after=60)
        self.run_pass(after=16 * 60)
        for name in names[3:]:
            self.touch(name)
            self.run_pass(after=60)
        role = self.role()
        self.assertEqual(len(role), 2)
        self.assertEqual(["3 scopes" in text for text in role], [True, True])

    def test_a_touch_with_no_owning_session_goes_to_the_role_once(self) -> None:
        self.sessions[1] = ""
        self.seed("run-ra.scope")
        self.touch("run-ra.scope")
        self.run_pass()
        self.run_pass(after=16 * 60)
        role = self.role()
        self.assertEqual(len(role), 1)
        self.assertIn("no live session", role[0])
        self.assert_short(role[0])

    def test_a_burst_of_touches_the_role_already_heard_sends_no_digest(self) -> None:
        for pid in (1, 2, 3):
            self.sessions[pid] = ""
        self.seed("run-ra.scope", "run-rb.scope", "run-rc.scope")
        for name in ("run-ra.scope", "run-rb.scope", "run-rc.scope"):
            self.touch(name)
            self.run_pass(after=60)
        role = self.role()
        self.assertEqual(len(role), 3, "one message per touch, and no digest of the same three")

    def test_an_unheld_role_sends_the_burst_to_the_sre_session(self) -> None:
        FakeEnvoy.publish_ok = False
        self.seed("run-ra.scope", "run-rb.scope", "run-rc.scope")
        for name in ("run-ra.scope", "run-rb.scope", "run-rc.scope"):
            self.touch(name)
            self.run_pass(after=60)
        sre = self.to(WATCH.SRE)
        self.assertEqual(len(sre), 1)
        self.assertIn("3 scopes", sre[0])

    def test_collect_mode_sends_its_urgent_touch_short_with_the_report(self) -> None:
        self.seed("run-ra.scope")
        self.touch("run-ra.scope", anon=int(1.8 * GIB))
        self.run_pass(wall_digest_mode="collect")
        urgent = self.role()
        self.assertEqual(len(urgent), 1)
        self.assertIn("NOT WAITING FOR THE DIGEST", urgent[0])
        self.assertIn("Where that memory is, by memory.stat:", self.assert_short(urgent[0]))

    def test_the_old_digests_waiting_touches_are_dropped_and_not_sent(self) -> None:
        """The live state file holds `wall_pending` from the digest version; each touch in it
        already reached its owner."""
        self.scope("run-ra.scope", 1)
        self.state.parent.mkdir(parents=True)
        waiting = {"at": self.now - 60, "scope": "run-rold.scope", "session": sid(2), "peak": CEILING,
                   "limit": CEILING, "stats": {"anon": GIB, "file": 20 * GIB}}
        self.state.write_text(json.dumps({
            "walls": {"run-ra.scope": {"events_max": 0, "peak": 2 * GIB}},
            "wall_pending": [waiting], "wall_digest_at": 0,
        }))
        self.run_pass()
        self.assertEqual(self.everything(), [])
        kept = json.loads(self.state.read_text())
        self.assertNotIn("wall_pending", kept)
        self.assertNotIn("wall_digest_at", kept)


class NearTest(NotifyCase):
    """A session near its limit hears it; the role hears only what the session did not fix."""

    SCOPE = "run-rnear.scope"

    def level(self, gib: float, oom_kill: int = 0) -> None:
        size = int(gib * GIB)
        self.scope(self.SCOPE, 1, anon=size, current=size, oom_kill=oom_kill)

    def alert(self) -> None:
        """Over 16.8 GiB, held through the 60 s grace: the first alert."""
        self.level(18)
        self.run_pass()
        self.run_pass(after=60)
        self.assertEqual(len(self.to(sid(1))), 1)

    def test_near_goes_to_the_session_only(self) -> None:
        self.alert()
        self.assertEqual(self.role(), [])
        report = self.assert_short(self.to(sid(1))[0])
        self.assertIn("OMP SESSION NEAR ITS MEMORY LIMIT", report)
        self.assertIn("write your handover files", report)

    def test_still_rising_10_minutes_after_the_alert_copies_the_role(self) -> None:
        self.alert()
        self.level(20)
        self.run_pass(after=600)
        role = self.role()
        self.assertEqual(len(role), 1)
        self.assertIn("STILL RISING", role[0])
        self.assert_short(role[0])
        # Once: still rising later is not a new fact.
        self.level(21)
        self.run_pass(after=600)
        self.assertEqual(len(self.role()), 1)

    def test_not_rising_after_10_minutes_sends_nothing_more(self) -> None:
        self.alert()
        self.run_pass(after=600)
        self.run_pass(after=600)
        self.assertEqual(self.role(), [])
        self.assertEqual(len(self.to(sid(1))), 1)

    def test_an_oom_kill_during_the_crossing_copies_the_role_once(self) -> None:
        self.alert()
        self.level(18, oom_kill=1)
        self.run_pass()
        role = self.role()
        self.assertEqual(len(role), 1)
        self.assertIn("OOM KILL", role[0])
        self.assert_short(role[0])
        self.run_pass()
        self.assertEqual(len(self.role()), 1)

    def test_cleared_never_reaches_the_role(self) -> None:
        self.alert()
        self.level(20)
        self.run_pass(after=600)
        self.level(2)
        self.run_pass()
        self.assertFalse([text for text in self.role() if "CLEARED" in text])
        cleared = [text for text in self.to(sid(1)) if "CLEARED" in text]
        self.assertEqual(len(cleared), 1)
        self.assert_short(cleared[0])
        self.assertEqual(len(cleared[0].splitlines()), 2, "a clear is one short line and its report")

    def test_a_session_that_cannot_be_named_is_reported_to_the_role_and_never_cleared_there(self) -> None:
        self.sessions[1] = ""
        self.level(18)
        self.run_pass()
        self.run_pass(after=60)
        role = self.role()
        self.assertEqual(len(role), 1)
        self.assert_short(role[0])
        self.level(2)
        self.run_pass()
        self.assertEqual(len(self.role()), 1)

    def test_a_crossing_the_old_version_copied_to_the_role_clears_to_the_session_only(self) -> None:
        """The live state file holds crossings whose `told` names the role, and no OOM base."""
        self.level(18)
        self.state.parent.mkdir(parents=True)
        self.state.write_text(json.dumps({
            "walls": {self.SCOPE: {"events_max": 0, "peak": 18 * GIB}},
            "scopes": {self.SCOPE: {"pid": 1, "pid_start": 7, "samples": []}},
            "incidents": {self.SCOPE: {
                "opened_at": self.now - 300, "alerted_at": self.now - 240, "alert_usage": 18 * GIB,
                "pid": 1, "scope": self.SCOPE, "session": sid(1), "told": [sid(1), ROLE],
                "limit": CEILING, "warn_at": 0.7 * CEILING, "peak": 18 * GIB, "limited": True,
            }},
        }))
        self.level(18, oom_kill=4)
        self.run_pass()
        # No OOM base was kept, so the counter as first read is the base, not news.
        self.assertEqual(self.everything(), [])
        self.level(2, oom_kill=4)
        self.run_pass()
        self.assertEqual(self.role(), [])
        self.assertEqual(["CLEARED" in text for text in self.to(sid(1))], [True])


class BoxedSessionTest(NotifyCase):
    """A session in an agent box has no scope of its own, but its box has a ceiling."""

    def boxed(self, rss_gib: float, box_gib: int = 109) -> None:
        """One omp in a box: its own cgroup sets no memory.max; the container's, above it, does."""
        box = "/system.slice/docker-0123456789ab.scope"
        leaf = f"{box}/init.scope"
        for path, limit in ((box, f"{box_gib * GIB}"), (leaf, "max")):
            directory = self.root / path.strip("/")
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "memory.max").write_text(f"{limit}\n")
        (self.root / leaf.strip("/") / "cgroup.procs").write_text("1\n")
        self.scopes["box"] = (1, leaf)
        WATCH.rss_of = lambda pid: int(rss_gib * GIB)

    def test_a_boxed_session_is_warned_at_its_box_s_ceiling_not_the_flat_level(self) -> None:
        """XV6 was warned at 19 GiB in a 109 GiB box (01:56Z 2026-11-04): the flat 16.8 GiB level
        is for a session that only the machine's RAM caps."""
        original = WATCH.rss_of
        self.addCleanup(setattr, WATCH, "rss_of", original)
        machine = unittest.mock.patch.object(WATCH, "mem_total", return_value=2200 * GIB)
        machine.start()
        self.addCleanup(machine.stop)
        self.boxed(19)
        for _ in range(4):
            self.run_pass(after=60)
        self.assertEqual(self.to(sid(1)), [], "19 GiB of a 109 GiB box is not near its limit")
        self.boxed(80)
        for _ in range(4):
            self.run_pass(after=60)
        sent = self.to(sid(1))
        self.assertTrue(sent, "80 GiB of a 109 GiB box is past 70% of it")
        self.assertIn("NEAR ITS MEMORY LIMIT", sent[0])

    def test_a_boxed_session_is_told_its_box_s_ceiling_not_that_nothing_caps_it(self) -> None:
        """Session 01a1935b was told at 04:36Z and 04:46Z on 2026-11-04 that it held 16.80 GiB
        "with no scope of its own ... only the box's RAM caps it", in a box whose memory.max is
        24 GiB. The sentence named no ceiling, so the session read it as uncapped; 16.80 GiB is
        70% of that 24 GiB. The cgroup chain is the one that box's omp really had:
        /user.slice/user-1000.slice/user-1000-agentbox.slice/docker-<id>.scope/init.scope."""
        original = WATCH.rss_of
        self.addCleanup(setattr, WATCH, "rss_of", original)
        machine = unittest.mock.patch.object(WATCH, "mem_total", return_value=2200 * GIB)
        machine.start()
        self.addCleanup(machine.stop)
        box = ("/user.slice/user-1000.slice/user-1000-agentbox.slice/"
               "docker-a489244627d27fbb2c5f06d3c08de5adf4c06b1a005c861a78f869025105ecc8.scope")
        leaf = f"{box}/init.scope"
        for path, limit in (("/user.slice", "max"), (box, str(24 * GIB)), (leaf, "max")):
            directory = self.root / path.strip("/")
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "memory.max").write_text(f"{limit}\n")
        (self.root / leaf.strip("/") / "cgroup.procs").write_text("1\n")
        self.scopes["box"] = (1, leaf)
        WATCH.rss_of = lambda pid: int(18 * GIB)
        for _ in range(4):
            self.run_pass(after=60)
        sent = self.to(sid(1))
        self.assertTrue(sent, "18 GiB of a 24 GiB box is past 70% of it")
        said = sent[0] + self.assert_short(sent[0])
        self.assertNotIn("only the box's RAM caps it", said)
        self.assertNotIn("only the machine's RAM caps it", said)
        self.assertIn("24.00 GiB", said, "the box's own ceiling is not named")
        self.assertIn("75%", said, "the share of the box's ceiling is not given")


class ReportFileTest(NotifyCase):
    def test_reports_older_than_seven_days_are_pruned_in_the_run(self) -> None:
        reports = self.state.parent / "reports"
        reports.mkdir(parents=True)
        old = reports / "20261020T000000Z-wall.txt"
        old.write_text("old\n")
        stamp = self.now - 8 * 86400
        os.utime(old, (stamp, stamp))
        recent = reports / "20261102T000000Z-wall.txt"
        recent.write_text("recent\n")
        self.run_pass()
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())


if __name__ == "__main__":
    unittest.main()
