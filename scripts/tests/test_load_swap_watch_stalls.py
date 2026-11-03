#!/usr/bin/env python3
"""Regression tests for load-swap-watch's two kernel-stall conditions (dispatch://INFRA-993).

On 2026-11-02 and 2026-11-03 sami-devbox's cgroup writeback got stuck cleaning up a dead cgroup
that had written millions of inodes. `writeback:inode_switch_wbs` ran at 57-200/s for over an
hour, a `kworker/N:M+inode_switch_wbs` sat at 55-98% CPU, and every teardown `umount` and
`fusermount3 -u` waited in D state for 30 min to 3.4 h. These tests hold:

- WRITEBACK SWITCH STALL: the switch rate at or over the rule for the grace window opens it,
  once; the rate is read only while a switch worker is busy or the crossing is open; the rate
  doubling past the worst said is the only new fact; it clears under 0.7 of the rule and never
  on a rate that could not be read;
- UMOUNT HUNG: an umount or fusermount3 in D state for over 5 minutes opens it at once, by the
  time since it last ran (/proc/<pid>/sched), naming the pid, the mount point and the time; the
  count of hung tasks doubling is the only new fact; it clears once none is left;
- both stalls carry the reset plan, the warning about `systemctl reboot`, and the cause with
  `pull-unit.sh stop`, whole, in a message of at most 600 characters.

The /proc tree and `perf` are fixtures in a temporary directory. Nothing here reads the real
/proc, runs sudo, or talks to a real Envoy.
"""

import argparse
import importlib.machinery
import importlib.util
import os
import re
import stat
import tempfile
import time
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "load-swap-watch"
LOADER = importlib.machinery.SourceFileLoader("load_swap_watch_stalls", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
WATCH = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(WATCH)

GIB = WATCH.GIB
REPORT_RE = re.compile(r"\nFull report: (\S+)\Z")
UPTIME = 50000.0
SYSBOXFS = "/var/lib/sysboxfs/5bfc5de108c48363495cafd4f32fa0799a350ec31543cf1a3317ad9aea30ec81"
BOX = "/mnt/nvme/sami/agentbox/boxes/agentbox-dc9594e43c18"
# `sudo -n perf stat -x, -I 10000 -e writeback:inode_switch_wbs -a`, sami-rocqperf,
# 2026-11-03 13:36:21-13:51:38Z, per second for each 10 s interval.
ROCQPERF_BASELINE = (
    10.7, 6.4, 4, 250.3, 166.7, 18.3, 1.7, 267.4, 1.4, 34, 6.6, 3.8, 187.2, 2.6, 2.6, 83, 108.2,
    1.6, 1.1, 778.4, 6.9, 62.5, 7.2, 10.9, 8.7, 40.8, 32.1, 2, 199.3, 124.8, 3.3, 48, 4.6, 1.5,
    36.3, 3.9, 1.5, 18.3, 2.9, 7.6, 6, 1.7, 109.4, 6.2, 25.4, 0.7, 4.7, 4.7, 10.8, 10.9, 3.5,
    57.4, 13.2, 17.2, 2.3, 13.3, 755.1, 12.3, 84.4, 1.2, 17.3, 2.6, 6, 124.2, 1.7, 1.1, 10, 1.6,
    119.2, 2.1, 3.8, 2.1, 2.1, 100.7, 2.7, 3.1, 18.3, 2.4, 83, 1.5, 0.9,
)

# The fixture `perf`: prints what the test put in perf-stderr to stderr, exits with perf-exit,
# and records its arguments, the way `sudo -n perf stat -x, ...` answers.
FAKE_PERF = """#!/bin/sh
here=$(dirname "$0")
echo "$@" >> "$here/perf-calls"
cat "$here/perf-stderr" >&2
exit "$(cat "$here/perf-exit")"
"""


class FakeEnvoy:
    publish_ok = True

    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []
        self.sent: list[tuple[str, str]] = []

    def publish(self, topic: str, message: str) -> bool:
        self.published.append((topic, message))
        return self.publish_ok

    def send(self, session_id: str, message: str) -> bool:
        self.sent.append((session_id, message))
        return True

    def request(self, method: str, path: str, body=None):
        return 200, []


def devbox(**overrides) -> dict:
    """sami-devbox at 2026-11-03T13:20Z, with its CPU, swap and memory all fine."""
    sample = {
        "cores": 288,
        "cpu": {"some_avg60": 3.0},
        "io": {"some_avg60": 40.0},
        "memory_pressure": {"full_avg60": 0.0},
        "load1": 66.0,
        "load5": 53.0,
        "load15": 42.0,
        "procs_running": 57,
        "procs_blocked": 9,
        "swap_total": 64 * GIB,
        "swap_free": 60 * GIB,
        "swap_used_fraction": 1 - 60 / 64,
        "mem_available": 200 * GIB,
        "mem_total": 251 * GIB,
        "pswpin": 0,
    }
    sample.update(overrides)
    return sample


class Base(unittest.TestCase):
    HOST = "sami-devbox"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.state = root / "state" / "state.json"
        self.proc = root / "proc"
        self.proc.mkdir()
        (self.proc / "uptime").write_text(f"{UPTIME:.2f} 1000000.00\n")
        self.perf_dir = root / "perf"
        self.perf_dir.mkdir()
        perf = self.perf_dir / "perf"
        perf.write_text(FAKE_PERF)
        perf.chmod(perf.stat().st_mode | stat.S_IEXEC)
        self.perf_count(0)
        self.envoy = FakeEnvoy()
        # The fixture /proc is read by the watch's own reader; a version without one reads
        # nothing from it, so these tests then fail on what that version sends, not on setUp.
        patches = {
            "Envoy": lambda timeout=8.0: self.envoy,
            "sample_box": lambda: dict(devbox(), at=time.time(), **getattr(WATCH, "kernel_stalls", dict)()),
            "measure_consumers": lambda options: {"ranked": {}, "cpu_rows": [], "mem_rows": [], "span": 0.0},
            "consumers_text": lambda options, envoy=None, measured=None: "CONSUMER TABLE",
            "PROC": self.proc,
            "PERF": (str(perf),),
        }
        missing = object()
        for name, replacement in patches.items():
            original = getattr(WATCH, name, missing)
            setattr(WATCH, name, replacement)
            self.addCleanup(
                lambda n=name, o=original: delattr(WATCH, n) if o is missing else setattr(WATCH, n, o)
            )
        hostname = WATCH.socket.gethostname
        WATCH.socket.gethostname = lambda: self.HOST
        self.addCleanup(lambda: setattr(WATCH.socket, "gethostname", hostname))

    # -- fixtures

    def task(self, pid: int, comm: str, state: str = "S", argv: tuple = (), ran_at: float = UPTIME, ticks: int = 0) -> None:
        """One /proc/<pid>: comm, stat (state, utime), sched (se.exec_start in ms) and cmdline."""
        base = self.proc / str(pid)
        base.mkdir(exist_ok=True)
        (base / "comm").write_text(comm + "\n")
        rest = [state, "2", "0", "0", "0", "-1", "69238880", "0", "0", "0", "0", str(ticks), "0"] + ["0"] * 39
        (base / "stat").write_text(f"{pid} ({comm[:15]}) {' '.join(rest)}\n")
        (base / "sched").write_text(
            f"{comm} ({pid}, #threads: 1)\n"
            "-------------------------------------------------------------------\n"
            f"se.exec_start                                :     {ran_at * 1000:.6f}\n"
            "se.vruntime                                  :      12345.678901\n"
        )
        (base / "cmdline").write_bytes(b"\0".join(arg.encode() for arg in argv) + (b"\0" if argv else b""))

    def gone(self, pid: int) -> None:
        base = self.proc / str(pid)
        for child in base.iterdir():
            child.unlink()
        base.rmdir()

    def busy_worker(self, pid: int = 1168597) -> None:
        self.task(pid, "kworker/108:0+inode_switch_wbs", state="R", ticks=500000)

    def idle_worker(self, pid: int = 1168597) -> None:
        self.task(pid, "kworker/108:0-inode_switch_wbs", state="I", ticks=500000)

    def hung(self, pid: int, blocked: float, comm: str = "umount", mount: str = BOX, state: str = "D") -> None:
        argv = ("umount", mount) if comm == "umount" else ("fusermount3", "-u", mount)
        self.task(pid, comm, state=state, argv=argv, ran_at=UPTIME - blocked)

    def perf_count(self, switched: int, queued: int = 22, exit_code: int = 0, stderr: str | None = None) -> None:
        """What `perf stat -x,` prints for one interval (format read on sami-devbox, 13:16Z)."""
        text = stderr if stderr is not None else (
            f"{switched},,writeback:inode_switch_wbs,3093050605958,100.00,,\n"
            f"{queued},,writeback:inode_switch_wbs_queue,3093050126655,100.00,,\n"
        )
        (self.perf_dir / "perf-stderr").write_text(text)
        (self.perf_dir / "perf-exit").write_text(str(exit_code))

    def perf_calls(self) -> list[str]:
        calls = self.perf_dir / "perf-calls"
        return calls.read_text().splitlines() if calls.exists() else []

    # -- running it

    def options(self, *extra: str) -> argparse.Namespace:
        return WATCH.parse_args(["--state", str(self.state), "--sample-seconds", "0.01", *extra])

    def run_watch(self, *extra: str) -> None:
        WATCH.run_once(self.options(*extra))

    def age(self, kind: str, seconds: float) -> None:
        """Move an open crossing back in time, as if the timer had run for `seconds`."""
        data = WATCH.json.loads(self.state.read_text())
        incident = data["incidents"][kind]
        for key in ("opened_at", "alerted_at", "checked_at", "last_seen"):
            if incident.get(key):
                incident[key] -= seconds
        if incident.get("told"):
            incident["told"]["at"] -= seconds
        self.state.write_text(WATCH.json.dumps(data))

    def incidents(self) -> dict:
        return WATCH.json.loads(self.state.read_text()).get("incidents", {})

    def messages(self) -> list[str]:
        return [text for _, text in self.envoy.published] + [text for _, text in self.envoy.sent]

    def check_action(self, text: str) -> None:
        """The box-health lead's action, whole, in a message of at most 600 characters."""
        self.assertLessEqual(len(text), 600, text)
        self.assertIn(WATCH.STALL_ACTION, text)
        self.assertIn("reset-plan-20261103.md", text)
        self.assertIn("never systemctl reboot", text)
        self.assertIn("dead cgroup that wrote millions of inodes", text)
        self.assertIn("devbox/bulk-copy/pull-unit.sh stop", text)
        self.assertIsNotNone(REPORT_RE.search(text), text)


class WritebackStallTest(Base):
    def open_and_alert(self, switched: int = 1005) -> None:
        self.busy_worker()
        self.perf_count(switched)
        self.run_watch()
        self.age("writeback", 600)
        self.run_watch()

    def test_a_quiet_box_runs_no_perf(self) -> None:
        """No busy switch worker and no crossing: the rate costs nothing to not read."""
        self.idle_worker()
        self.perf_count(1005)
        self.run_watch()
        self.assertEqual([], self.perf_calls())
        self.assertEqual({}, self.incidents())
        self.assertEqual([], self.messages())

    def test_a_busy_worker_reads_the_rate_with_perf_stat(self) -> None:
        self.busy_worker()
        self.perf_count(5)
        self.run_watch()
        self.assertEqual(1, len(self.perf_calls()))
        self.assertIn("stat -x, -e writeback:inode_switch_wbs,writeback:inode_switch_wbs_queue -a -- sleep 5", self.perf_calls()[0])

    def test_the_stall_rate_opens_and_waits_for_ten_minutes(self) -> None:
        """201/s, the top of the 2026-11-03 incident: opened on the first run, said after 600 s."""
        self.busy_worker()
        self.perf_count(1005)
        self.run_watch()
        self.assertIn("writeback", self.incidents())
        self.assertEqual([], self.messages())
        self.age("writeback", 300)
        self.run_watch()
        self.assertEqual([], self.messages())
        self.age("writeback", 300)
        self.run_watch()
        self.assertEqual(1, len(self.messages()))
        text = self.messages()[0]
        first = text.splitlines()[0]
        self.assertTrue(first.startswith("WRITEBACK SWITCH STALL on sami-devbox: 201 inodes/s switched over 5 s (rule"), first)
        self.assertIn("; kworker/108:0+inode_switch_wbs at ", first)
        report = Path(REPORT_RE.search(text).group(1)).read_text()
        self.assertIn("kworker/108:0+inode_switch_wbs (pid 1168597)", report)
        self.assertEqual(WATCH.BOX_HEALTH_ROLE_TOPIC, self.envoy.published[0][0])
        self.check_action(text)

    def test_the_lowest_incident_rate_opens(self) -> None:
        """57/s, the lowest rate of the incident hour."""
        self.open_and_alert(switched=285)
        self.assertEqual(1, len(self.messages()))
        self.assertIn("57 inodes/s", self.messages()[0])

    def test_a_healthy_busy_box_does_not_open(self) -> None:
        """sami-rocqperf's worst 10 s of the healthy baseline (see load-swap-watch.md)."""
        self.busy_worker()
        self.perf_count(int(WATCH.SWITCH_RATE * 5) - 1)
        self.run_watch()
        self.assertEqual({}, self.incidents())

    def test_said_once_while_it_holds(self) -> None:
        self.open_and_alert()
        for _ in range(3):
            self.age("writeback", 120)
            self.run_watch()
        self.assertEqual(1, len(self.messages()))

    def test_the_rate_doubled_is_the_new_fact(self) -> None:
        self.open_and_alert(switched=300)  # 60/s
        self.perf_count(650)  # 130/s
        self.run_watch()
        self.assertEqual(2, len(self.messages()))
        first = self.messages()[1].splitlines()[0]
        self.assertTrue(first.startswith("STILL A WRITEBACK SWITCH STALL, WORSE on sami-devbox"), first)
        self.assertIn("130 inodes/s switched (was 60)", first)
        self.check_action(self.messages()[1])
        # 200/s does not double the 130 already said.
        self.perf_count(1000)
        self.run_watch()
        self.assertEqual(2, len(self.messages()))

    def test_a_crossing_keeps_reading_after_the_worker_goes_idle_and_clears(self) -> None:
        self.open_and_alert()
        self.idle_worker()
        self.perf_count(2)
        before = len(self.perf_calls())
        self.run_watch()
        self.assertEqual(before + 1, len(self.perf_calls()))
        self.assertEqual(2, len(self.messages()))
        self.assertTrue(self.messages()[1].startswith("WRITEBACK SWITCH STALL CLEARED on sami-devbox after"), self.messages()[1])
        self.assertNotIn("writeback", self.incidents())
        # Cleared: a quiet box reads no rate again.
        self.run_watch()
        self.assertEqual(before + 1, len(self.perf_calls()))

    def test_under_the_rule_but_not_clear_of_it_stays_open(self) -> None:
        self.open_and_alert()
        self.perf_count(int(WATCH.SWITCH_RATE * 5 * 0.85))
        self.run_watch()
        self.assertEqual(1, len(self.messages()))
        self.assertIn("writeback", self.incidents())

    def test_a_rate_that_could_not_be_read_never_clears(self) -> None:
        self.open_and_alert()
        self.perf_count(0, exit_code=1, stderr="sudo: a password is required\n")
        self.run_watch()
        self.assertEqual(1, len(self.messages()))
        self.assertIn("writeback", self.incidents())

    def test_a_burst_whose_worker_is_idle_by_the_next_run_clears_unsaid(self) -> None:
        """A healthy burst: 778/s with the worker busy, the worker idle two minutes later."""
        self.busy_worker()
        self.perf_count(3890)
        self.run_watch()
        self.assertIn("writeback", self.incidents())
        self.idle_worker()
        self.age("writeback", 120)
        self.run_watch()
        self.assertEqual({}, self.incidents())
        self.assertEqual([], self.messages())

    def test_the_rocqperf_baseline_replayed_sends_nothing(self) -> None:
        """sami-rocqperf 2026-11-03 13:36-13:51Z, its 81 ten-second counts as successive runs
        two minutes apart, with the switch worker busy in one run of eight (7 of 60 seconds)."""
        for index, rate in enumerate(ROCQPERF_BASELINE):
            (self.busy_worker if index % 8 == 0 else self.idle_worker)()
            self.perf_count(round(rate * 5))
            self.run_watch()
            if "writeback" in self.incidents():
                self.age("writeback", 120)
        self.assertEqual([], self.messages())

    def test_the_stall_replayed_alerts_once(self) -> None:
        """sami-devbox 12:45-13:03Z: the worker busy every run, 57-200/s."""
        for rate in (57, 120, 200, 88, 160, 63, 140, 75):
            self.busy_worker()
            self.perf_count(rate * 5)
            self.run_watch()
            self.age("writeback", 120)
        self.assertEqual(1, len(self.messages()))
        self.assertTrue(self.messages()[0].startswith("WRITEBACK SWITCH STALL on sami-devbox"), self.messages()[0])

    def test_a_failed_perf_opens_nothing(self) -> None:
        self.busy_worker()
        self.perf_count(0, exit_code=1, stderr="sudo: a password is required\n")
        self.run_watch()
        self.assertEqual({}, self.incidents())


class UmountHungTest(Base):
    def test_blocked_over_five_minutes_is_said_at_once(self) -> None:
        """No grace on top: the five minutes in D state are the hold."""
        self.hung(2964188, 4616)
        self.run_watch()
        self.assertEqual(1, len(self.messages()))
        text = self.messages()[0]
        first = text.splitlines()[0]
        self.assertTrue(first.startswith("UMOUNT HUNG on sami-devbox: 1 in D state over 5 min"), first)
        self.assertIn(f"pid 2964188 umount {BOX} blocked 77 min", first)
        self.check_action(text)

    def test_the_block_is_timed_from_when_it_last_ran_not_from_its_start(self) -> None:
        self.hung(171269, 338)
        found = WATCH.kernel_stalls()["umounts"]
        self.assertEqual(1, len(found))
        self.assertAlmostEqual(338, found[0]["blocked"], places=3)
        self.assertEqual(BOX, found[0]["mount"])

    def test_the_fusermount3_mount_point_is_its_last_argument(self) -> None:
        self.hung(2705095, 4646, comm="fusermount3", mount=SYSBOXFS)
        self.run_watch()
        report = Path(REPORT_RE.search(self.messages()[0]).group(1)).read_text()
        self.assertIn(f"pid 2705095 fusermount3 {SYSBOXFS} blocked 77 min", report)
        self.assertIn("pid 2705095 fusermount3 /var/lib/sysboxfs/", self.messages()[0])

    def test_under_five_minutes_is_not_hung(self) -> None:
        self.hung(171269, 200)
        self.run_watch()
        self.assertEqual([], self.messages())
        self.assertEqual({}, self.incidents())

    def test_not_in_d_state_is_not_hung(self) -> None:
        self.hung(171269, 4000, state="S")
        self.run_watch()
        self.assertEqual([], self.messages())

    def test_another_command_in_d_state_is_not_this_condition(self) -> None:
        self.task(4242, "rsync", state="D", argv=("rsync", "-a", "x", "y"), ran_at=UPTIME - 4000)
        self.run_watch()
        self.assertEqual([], self.messages())

    def test_said_once_while_it_holds_however_long(self) -> None:
        self.hung(2964188, 400)
        self.run_watch()
        self.hung(2964188, 4000)  # blocked longer: time passing, not a fact
        self.run_watch()
        self.run_watch()
        self.assertEqual(1, len(self.messages()))

    def test_the_count_doubled_is_the_new_fact(self) -> None:
        self.hung(2964188, 4616)
        self.run_watch()
        self.hung(2705095, 4646, comm="fusermount3", mount=SYSBOXFS)
        self.run_watch()
        self.assertEqual(2, len(self.messages()))
        first = self.messages()[1].splitlines()[0]
        self.assertTrue(first.startswith("STILL UMOUNT HUNG, WORSE on sami-devbox"), first)
        self.assertIn("2 umount/fusermount3 hung (was 1)", first)
        self.hung(2237501, 4004)  # 3 does not double 2
        self.run_watch()
        self.assertEqual(2, len(self.messages()))

    def test_it_clears_when_none_is_left(self) -> None:
        self.hung(2964188, 4616)
        self.run_watch()
        self.gone(2964188)
        self.run_watch()
        self.assertEqual(2, len(self.messages()))
        self.assertTrue(self.messages()[1].startswith("UMOUNT HUNG CLEARED on sami-devbox after"), self.messages()[1])
        self.assertNotIn("umount", self.incidents())

    def test_a_new_umount_still_young_holds_the_clear(self) -> None:
        """Clear at 0.7 of the rule: an umount 4 minutes into its wait keeps the crossing open."""
        self.hung(2964188, 4616)
        self.run_watch()
        self.gone(2964188)
        self.hung(171269, 240)
        self.run_watch()
        self.assertEqual(1, len(self.messages()))
        self.assertIn("umount", self.incidents())


class BothStallsTest(Base):
    def test_sami_devbox_at_1320z_is_one_message_with_both_lines_and_the_action_once(self) -> None:
        """The box as read on 2026-11-03 at 13:20Z: 9 tasks hung 6 min to 77 min, 201/s switched."""
        self.busy_worker()
        self.perf_count(1005)
        for pid, blocked, comm, mount in (
            (171269, 338, "umount", "/mnt/nvme/sami/agentbox/boxes/agentbox-2fa0219ec7fd"),
            (2237501, 4004, "umount", "/mnt/nvme/sami/agentbox/boxes/agentbox-db9ef1329fe8"),
            (2260358, 3915, "umount", "/mnt/nvme/sami/agentbox/boxes/agentbox-61f4922f09ff"),
            (2378029, 3441, "umount", "/mnt/nvme/sami/agentbox/boxes/agentbox-f385124772fe"),
            (2395590, 3359, "umount", "/mnt/nvme/sami/agentbox/boxes/agentbox-d80276a32452"),
            (2476615, 2941, "umount", "/mnt/nvme/sami/agentbox/boxes/agentbox-892ab0765f64"),
            (2705095, 4646, "fusermount3", SYSBOXFS),
            (2914013, 1006, "umount", "/mnt/nvme/sami/agentbox/boxes/agentbox-f1e1f283d732"),
            (2964188, 4616, "umount", BOX),
        ):
            self.hung(pid, blocked, comm=comm, mount=mount)
        self.run_watch()
        self.assertEqual(1, len(self.messages()))  # the umounts at once; the rate waits
        self.age("writeback", 600)
        self.run_watch()
        self.assertEqual(2, len(self.messages()))
        text = self.messages()[1]
        self.assertTrue(text.startswith("WRITEBACK SWITCH STALL on sami-devbox:"), text)
        self.check_action(text)
        self.assertEqual(1, text.count("DO (box-health owner)"))
        first = self.messages()[0]
        self.assertIn("UMOUNT HUNG on sami-devbox: 9 in D state over 5 min", first)
        self.assertIn("pid 2705095 fusermount3", first)
        self.assertIn("8 more in the report", first)
        self.check_action(first)
        report = Path(REPORT_RE.search(first).group(1)).read_text()
        for pid in (171269, 2237501, 2260358, 2378029, 2395590, 2476615, 2705095, 2914013, 2964188):
            self.assertIn(f"pid {pid} ", report)
        self.assertIn("systemctl reboot", report)

    def test_a_cpu_verdict_beside_a_stall_never_crowds_out_the_action(self) -> None:
        self.hung(2705095, 4646, comm="fusermount3", mount=SYSBOXFS)
        self.hung(2964188, 4616)
        original = WATCH.sample_box
        WATCH.sample_box = lambda: dict(
            original(), cpu={"some_avg60": 80.0}, load1=900.0, procs_blocked=700,
            mem_available=2 * GIB, swap_free=0, swap_used_fraction=1.0,
        )
        self.run_watch("--grace", "0")
        self.assertEqual(1, len(self.messages()))
        self.check_action(self.messages()[0])


if __name__ == "__main__":
    unittest.main()
