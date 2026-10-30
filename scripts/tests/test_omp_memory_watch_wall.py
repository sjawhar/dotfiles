#!/usr/bin/env python3
"""Regression tests for omp-memory-watch's memory-WALL trigger.

The crossing alert needs the scope's unreclaimable memory above 70% of its ceiling for 60 s.
A worker scope that `ossv-worker` starts (`systemd-run --user --scope -p MemoryMax=24G`, no
MemorySwapMax) can spend its whole budget without ever meeting that: its pressure can be page
cache, or it can reach the ceiling and fall back inside one 30 s period. On ossv-agent-open on
2026-10-30, 31 of 95 worker scopes had touched their 24 GiB wall while every one of them sat
under the 45% sampling level, and 4 were reported.

Every cgroup here is a synthetic directory tree, so the tests read no real cgroup and run
anywhere with python3.
"""

import argparse
import importlib.machinery
import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "omp-memory-watch"
LOADER = importlib.machinery.SourceFileLoader("omp_memory_watch", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
WATCH = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(WATCH)

GIB = WATCH.GIB
CEILING = 24 * GIB


class FakeEnvoy:
    """Records what left. Constructed by run_once exactly as the real one is."""

    instances: list = []

    def __init__(self, timeout: float = 8.0) -> None:
        self.sent: list[tuple[str, str]] = []
        FakeEnvoy.instances.append(self)

    def send(self, session_id: str, message: str) -> bool:
        self.sent.append((session_id, message))
        return True


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


def write_scope(
    root: Path,
    name: str,
    *,
    limit: int = CEILING,
    events_max: int = 0,
    peak: int = 1 * GIB,
    current: int = 1 * GIB,
    anon: int = 512 * 1024 * 1024,
    swap_max: str = "max",
    procs: tuple[int, ...] = (),
    oom: int = 0,
    oom_kill: int = 0,
) -> str:
    """One synthetic cgroup v2 scope directory under a fake /sys/fs/cgroup. Returns its path."""
    cgroup = f"/user.slice/user-1001.slice/user@1001.service/app.slice/{name}"
    directory = root / cgroup.strip("/")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "memory.max").write_text(f"{limit}\n")
    (directory / "memory.current").write_text(f"{current}\n")
    (directory / "memory.peak").write_text(f"{peak}\n")
    (directory / "memory.swap.max").write_text(f"{swap_max}\n")
    (directory / "memory.events").write_text(
        f"low 0\nhigh 0\nmax {events_max}\noom {oom}\noom_kill {oom_kill}\noom_group_kill 0\n"
    )
    (directory / "memory.stat").write_text(f"anon {anon}\nfile {max(current - anon, 0)}\nkernel 0\nsock 0\nshmem 0\n")
    (directory / "cgroup.procs").write_text("".join(f"{pid}\n" for pid in procs))
    return cgroup


class WallTriggerTest(unittest.TestCase):
    """The decision alone: wall_touch, with no Envoy and no state file."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.original_root = WATCH.CGROUP_ROOT
        WATCH.CGROUP_ROOT = self.root
        self.addCleanup(lambda: setattr(WATCH, "CGROUP_ROOT", self.original_root))
        self.options = options(self.root / "state.json")

    def target(self, cgroup: str, name: str) -> dict:
        usage, current, _fields = WATCH.scope_pressure(cgroup)
        peak = WATCH.read_int(WATCH.CGROUP_ROOT / cgroup.strip("/") / "memory.peak")
        return dict(
            key=name, scope=name, cgroup=cgroup, pid=4242, limit=CEILING,
            limit_from=f"memory.max of {name}", limited=True, usage=usage, current=current,
            events=WATCH.memory_events(cgroup), peak_bytes=peak, swap_max=WATCH.swap_setting(cgroup),
            measured="test",
        )

    def test_events_counter_rise_reports_without_any_hold(self) -> None:
        """The whole point: a scope under the 45% sampling level, never held, still reports."""
        name = "run-rwall.scope"
        cgroup = write_scope(self.root, name, events_max=0, peak=2 * GIB, current=2 * GIB)
        walls: dict = {}
        self.assertIsNone(WATCH.wall_touch(self.target(cgroup, name), walls, self.options, 1000.0, False))

        write_scope(self.root, name, events_max=17, peak=2 * GIB, current=2 * GIB)
        touch = WATCH.wall_touch(self.target(cgroup, name), walls, self.options, 1030.0, False)
        self.assertIsNotNone(touch)
        assert touch is not None
        self.assertEqual(touch["events_delta"], 17)
        self.assertEqual(touch["events_max"], 17)
        # Nothing about this scope is near the crossing alert's levels.
        self.assertLess(touch["usage"], CEILING * self.options.sample_fraction)

    def test_peak_crossing_reports_once_and_not_again(self) -> None:
        """memory.peak is a high-water mark: once at the ceiling it stays there for ever."""
        name = "run-rpeak.scope"
        cgroup = write_scope(self.root, name, events_max=0, peak=1 * GIB)
        walls: dict = {}
        WATCH.wall_touch(self.target(cgroup, name), walls, self.options, 1000.0, False)

        write_scope(self.root, name, events_max=0, peak=CEILING)
        touch = WATCH.wall_touch(self.target(cgroup, name), walls, self.options, 1030.0, False)
        assert touch is not None
        self.assertTrue(touch["peak_touch"])
        self.assertEqual(touch["events_delta"], 0)

        # Same peak a cooldown later: no second report, because the peak did not rise again.
        self.assertIsNone(WATCH.wall_touch(self.target(cgroup, name), walls, self.options, 9000.0, False))

    def test_peak_below_the_fraction_is_not_a_touch(self) -> None:
        name = "run-rnear.scope"
        cgroup = write_scope(self.root, name, peak=int(CEILING * 0.90))
        walls: dict = {}
        WATCH.wall_touch(self.target(cgroup, name), walls, self.options, 1000.0, False)
        write_scope(self.root, name, peak=int(CEILING * 0.97))
        self.assertIsNone(WATCH.wall_touch(self.target(cgroup, name), walls, self.options, 1030.0, False))

    def test_cooldown_suppresses_and_the_next_report_counts_what_it_covered(self) -> None:
        name = "run-rcool.scope"
        cgroup = write_scope(self.root, name, events_max=1)
        walls: dict = {}
        self.assertIsNotNone(WATCH.wall_touch(self.target(cgroup, name), walls, self.options, 1000.0, False))
        for index, moment in enumerate((1030.0, 1060.0, 1090.0), start=2):
            write_scope(self.root, name, events_max=index)
            self.assertIsNone(WATCH.wall_touch(self.target(cgroup, name), walls, self.options, moment, False))
        write_scope(self.root, name, events_max=9)
        touch = WATCH.wall_touch(self.target(cgroup, name), walls, self.options, 1000.0 + 901, False)
        assert touch is not None
        self.assertEqual(touch["suppressed"], 3)
        self.assertEqual(touch["events_delta"], 5)

    def test_cold_start_seeds_the_backlog_and_reports_nothing(self) -> None:
        """A counter already above zero dates from before the state file existed."""
        name = "run-rseed.scope"
        cgroup = write_scope(self.root, name, events_max=42422, peak=CEILING)
        walls: dict = {}
        self.assertIsNone(WATCH.wall_touch(self.target(cgroup, name), walls, self.options, 1000.0, True))
        self.assertEqual(walls[name]["events_max"], 42422)
        write_scope(self.root, name, events_max=42423, peak=CEILING)
        touch = WATCH.wall_touch(self.target(cgroup, name), walls, self.options, 1030.0, True)
        assert touch is not None
        self.assertEqual(touch["events_delta"], 1)

    def test_a_scope_born_after_the_first_run_reports_its_first_touch(self) -> None:
        """Not a cold start: an unknown scope's baseline is zero, so its backlog IS its news."""
        name = "run-rnew.scope"
        cgroup = write_scope(self.root, name, events_max=3)
        walls: dict = {}
        touch = WATCH.wall_touch(self.target(cgroup, name), walls, self.options, 1000.0, False)
        assert touch is not None
        self.assertEqual(touch["events_delta"], 3)


class WallReportTest(unittest.TestCase):
    """What the owner is told: scope, session, worker, peak, top 3 processes with command lines."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.original_root = WATCH.CGROUP_ROOT
        WATCH.CGROUP_ROOT = self.root
        self.addCleanup(lambda: setattr(WATCH, "CGROUP_ROOT", self.original_root))
        self.commands = {
            11: "omp --config /home/sami/.ossv-agent/omp-vm.yml @/home/sami/work/.ossv-workers/c-asan/message-resume-0.md",
            12: "/usr/bin/python3 -u /tmp/omp-python-runner-1001/runner-nket50qsf4p8.py",
            13: "/bin/bash ./score_rpwin.sh cand/alreloc.py e1-rpwin",
            14: "tee e1-rpwin/rpwin.txt",
        }
        self.rss = {11: 9 * GIB, 12: 4 * GIB, 13: 2 * GIB, 14: 1024}
        for name, replacement in (
            ("cmdline_of", lambda pid: self.commands.get(pid, "")),
            ("rss_of", lambda pid: self.rss.get(pid, 0)),
            ("session_of", lambda pid: ""),
        ):
            original = getattr(WATCH, name)
            setattr(WATCH, name, replacement)
            self.addCleanup(lambda n=name, o=original: setattr(WATCH, n, o))
        # The worker directory `ossv-worker` keeps, named by the omp command line above.
        worker = self.root / "home/sami/work/.ossv-workers/c-asan"
        worker.mkdir(parents=True)
        (worker / "session").write_text("01a16551-03d5-76e6-8056-64317a9f893f\n")
        self.worker_dir = worker
        self.commands[11] = self.commands[11].replace("/home/sami/work", str(self.root / "home/sami/work"))
        self.options = options(self.root / "state.json")

    def touch(self) -> dict:
        name = "run-rreport.scope"
        cgroup = write_scope(
            self.root, name, events_max=5, peak=CEILING, current=16 * GIB, procs=(11, 12, 13, 14)
        )
        walls: dict = {name: {"events_max": 0, "peak": 1 * GIB}}
        target = dict(
            key=name, scope=name, cgroup=cgroup, pid=11, limit=CEILING,
            limit_from=f"memory.max of {name}", limited=True,
            usage=WATCH.scope_pressure(cgroup)[0], current=16 * GIB,
            events=WATCH.memory_events(cgroup), peak_bytes=CEILING,
            swap_max=WATCH.swap_setting(cgroup), measured="test",
        )
        found = WATCH.wall_touch(target, walls, self.options, 2000.0, False)
        assert found is not None
        return found

    def test_report_names_scope_session_worker_peak_and_top_three(self) -> None:
        touch = self.touch()
        self.assertEqual(touch["worker"], "c-asan")
        self.assertEqual(touch["session"], "01a16551-03d5-76e6-8056-64317a9f893f")
        self.assertEqual([pid for pid, _rss, _cmd in touch["top"]], [11, 12, 13])
        text = WATCH.wall_text(touch)
        self.assertIn("run-rreport.scope", text)
        self.assertIn("c-asan", text)
        self.assertIn("01a16551-03d5-76e6-8056-64317a9f893f", text)
        self.assertIn("24.00 GiB", text)
        self.assertIn("score_rpwin.sh", text)
        self.assertIn("runner-nket50qsf4p8.py", text)
        self.assertIn("9.00 GiB", text)

    def test_an_unconfirmed_worker_session_is_not_claimed(self) -> None:
        """`ossv-worker` writes session_unconfirmed while $d/session still names the old one."""
        (self.worker_dir / "session_unconfirmed").write_text("")
        touch = self.touch()
        self.assertEqual(touch["session"], "")
        self.assertIn("session UNKNOWN", WATCH.wall_text(touch))

    def test_swap_sentence_follows_memory_swap_max(self) -> None:
        """The old text claimed MemorySwapMax=0 for every scope; a worker scope has swap."""
        touch = self.touch()
        self.assertEqual(touch["swap_max"], "max")
        self.assertIn("memory.swap.max is `max`", WATCH.wall_text(touch))
        self.assertNotIn("MemorySwapMax=0", WATCH.wall_text(touch))
        self.assertIn("memory.swap.max is 0", WATCH.swap_sentence("0"))
        self.assertIn("could not be read", WATCH.swap_sentence(""))

    def test_crossing_alert_no_longer_claims_memoryswapmax_zero(self) -> None:
        incident = dict(
            limit=CEILING, warn_at=CEILING * 0.70, peak=18 * GIB, current=20 * GIB, limited=True,
            session="01a16551-03d5-76e6-8056-64317a9f893f", pid=11, scope="run-r1.scope",
            limit_from="memory.max of run-r1.scope", opened_at=1000.0, self_watch="none",
            task_count=4, omp_rss=9 * GIB, measured="test", swap_max="max",
        )
        text = WATCH.alert_text("run-r1.scope", incident, 1200.0, "first alert")
        self.assertNotIn("MemorySwapMax=0", text)
        self.assertIn("memory.swap.max is `max`", text)
        incident["swap_max"] = "0"
        self.assertIn("memory.swap.max is 0", WATCH.alert_text("run-r1.scope", incident, 1200.0, "first alert"))


class WallAdviceTest(unittest.TestCase):
    """The owner's DO line chooses by where the memory actually is.

    In the open4 and open6 digests of 2026-10-30 the three largest processes held 10-29% of a
    scope sitting at its ceiling, so "find the process" was the wrong instruction for them.
    """

    def touch(self, **overrides) -> dict:
        base = dict(
            at=1000.0, scope="run-r1.scope", key="run-r1.scope", session="s", worker="c-asan",
            limit=CEILING, limit_from="memory.max", peak=CEILING, current=20 * GIB,
            usage=2 * GIB, swap_max="max", events_max=5, events_delta=5, events_oom=0,
            events_oom_kill=0, peak_touch=True, task_count=12, rss_sum=4 * GIB,
            stats={"anon": 2 * GIB, "file": 17 * GIB, "shmem": GIB, "kernel": 0, "sock": 0, "slab": 0},
            top=[(11, GIB, "omp --resume x"), (12, GIB, "python3 a"), (13, GIB, "python3 b")],
            suppressed=0, cooldown=900.0,
        )
        base.update(overrides)
        return base

    def test_one_runaway_when_the_named_processes_hold_most_of_the_peak(self) -> None:
        touch = self.touch(
            rss_sum=16 * GIB,
            top=[(11, 15 * GIB, "omp --resume x"), (12, GIB, "python3 a")],
        )
        advice = WATCH.wall_advice(touch)
        self.assertIn("ONE RUNAWAY", advice)
        self.assertIn("67% of the peak", advice)
        self.assertIn("DO:", WATCH.wall_text(touch))
        self.assertIn("ONE RUNAWAY", WATCH.wall_text(touch))

    def test_many_processes_when_the_whole_scope_holds_the_peak_but_no_one_process_does(self) -> None:
        touch = self.touch(
            task_count=40,
            rss_sum=19 * GIB,
            top=[(pid, GIB, f"python3 lane-{pid}") for pid in (11, 12, 13)],
        )
        advice = WATCH.wall_advice(touch)
        self.assertIn("MANY PROCESSES", advice)
        self.assertIn("all 40 together", advice)
        self.assertIn("reduce the fan-out", advice.lower())

    def test_no_process_holds_it_names_the_dominant_memory_stat_field(self) -> None:
        advice = WATCH.wall_advice(self.touch())
        self.assertIn("NOT IN ANY PROCESS", advice)
        self.assertIn("file", advice)
        self.assertIn("page cache", advice)
        self.assertNotIn("ONE RUNAWAY", advice)

    def test_shmem_is_named_when_it_is_the_dominant_field(self) -> None:
        advice = WATCH.wall_advice(
            self.touch(stats={"anon": GIB, "file": 2 * GIB, "shmem": 18 * GIB, "kernel": 0, "sock": 0})
        )
        self.assertIn("shmem", advice)
        self.assertIn("tmpfs", advice)

    def test_a_scope_that_fell_back_says_the_peak_was_a_burst(self) -> None:
        advice = WATCH.wall_advice(self.touch(current=2 * GIB))
        self.assertIn("fallen back", advice)

    def test_unreadable_memory_stat_claims_nothing(self) -> None:
        advice = WATCH.wall_advice(self.touch(stats={}))
        self.assertIn("could not be read", advice)
        self.assertEqual(WATCH.stat_split({}), "memory.stat unreadable")

    def test_the_report_carries_the_count_the_rss_sum_and_the_split(self) -> None:
        text = WATCH.wall_text(self.touch())
        self.assertIn("12 processes holding 4.00 GiB of RSS between them, 17% of the peak", text)
        self.assertIn("file 17.00 GiB", text)
        self.assertIn("anon 2.00 GiB", text)
        # Largest first, and `slab` is left out: it is already inside `kernel`.
        self.assertLess(text.index("file 17.00 GiB"), text.index("anon 2.00 GiB"))
        self.assertNotIn("slab", text)

    def test_the_digest_line_carries_the_same_three_numbers(self) -> None:
        digest = WATCH.wall_digest_text([self.touch()], 900.0)
        self.assertIn("12 processes holding 4.00 GiB of RSS (17% of peak)", digest)
        self.assertIn("memory.stat: file 17.00 GiB", digest)
        self.assertIn("NOT IN ANY PROCESS", digest)


class RunOnceTest(unittest.TestCase):
    """One whole pass over synthetic scopes: who is sent what, and what the state file keeps."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        FakeEnvoy.instances = []
        for name, replacement in (
            ("CGROUP_ROOT", self.root),
            ("Envoy", FakeEnvoy),
            ("cmdline_of", lambda pid: f"omp @/home/sami/work/.ossv-workers/w{pid}/message.md"),
            ("rss_of", lambda pid: 3 * GIB),
            ("pid_start", lambda pid: 7),
            ("session_of", lambda pid: f"0000000{pid}-0000-0000-0000-00000000000{pid}"),
        ):
            original = getattr(WATCH, name)
            setattr(WATCH, name, replacement)
            self.addCleanup(lambda n=name, o=original: setattr(WATCH, n, o))
        self.state = self.root / "state.json"

    def scopes(self, events: dict[str, int]) -> None:
        self.cgroups = {}
        for name, counter in events.items():
            self.cgroups[name] = write_scope(
                self.root, name, events_max=counter, peak=2 * GIB, current=2 * GIB, procs=(int(name[-7:-6]),)
            )

    def processes(self) -> list[tuple[int, str]]:
        return [(int(name[-7:-6]), cgroup) for name, cgroup in self.cgroups.items()]

    def run_pass(self, **overrides) -> FakeEnvoy:
        WATCH.omp_processes = lambda: self.processes()
        WATCH.run_once(options(self.state, **overrides))
        return FakeEnvoy.instances[-1]

    def test_cold_start_is_silent_then_every_new_touch_reaches_its_owner(self) -> None:
        self.scopes({"run-ra1.scope": 4, "run-rb2.scope": 0})
        envoy = self.run_pass()
        self.assertEqual(envoy.sent, [])

        self.scopes({"run-ra1.scope": 9, "run-rb2.scope": 3})
        envoy = self.run_pass()
        owners = sorted(session for session, _text in envoy.sent)
        self.assertEqual(
            owners,
            [
                "00000001-0000-0000-0000-000000000001",
                "00000002-0000-0000-0000-000000000002",
                WATCH.INFRA_LEAD,
            ],
        )
        digest = [text for session, text in envoy.sent if session == WATCH.INFRA_LEAD][0]
        self.assertIn("2 touch(es)", digest)
        self.assertIn("run-ra1.scope", digest)
        self.assertIn("run-rb2.scope", digest)

    def test_the_lead_digest_waits_but_loses_no_touch(self) -> None:
        self.scopes({"run-ra1.scope": 1})
        self.run_pass()
        self.scopes({"run-ra1.scope": 2})
        self.run_pass()
        # The digest just went out; a second scope touching now waits for the next window.
        self.scopes({"run-ra1.scope": 2, "run-rb2.scope": 5})
        envoy = self.run_pass()
        self.assertEqual([session for session, _ in envoy.sent], ["00000002-0000-0000-0000-000000000002"])
        import json

        kept = json.loads(self.state.read_text())
        self.assertEqual([entry["scope"] for entry in kept["wall_pending"]], ["run-rb2.scope"])

        self.scopes({"run-ra1.scope": 2, "run-rb2.scope": 5})
        envoy = self.run_pass(wall_digest=0.0)
        digest = [text for session, text in envoy.sent if session == WATCH.INFRA_LEAD][0]
        self.assertIn("run-rb2.scope", digest)

    def test_dry_run_sends_nothing(self) -> None:
        self.scopes({"run-ra1.scope": 1})
        self.run_pass()
        self.scopes({"run-ra1.scope": 4})
        envoy = self.run_pass(dry_run=True, write_state=True)
        self.assertEqual(envoy.sent, [])

    def test_a_dead_scope_is_forgotten(self) -> None:
        self.scopes({"run-ra1.scope": 1, "run-rb2.scope": 1})
        self.run_pass()
        self.scopes({"run-ra1.scope": 1})
        self.run_pass()
        import json

        kept = json.loads(self.state.read_text())
        self.assertEqual(sorted(kept["walls"]), ["run-ra1.scope"])

    def test_a_state_file_from_the_version_before_this_trigger_still_seeds(self) -> None:
        """The upgrade path: `scopes` is full, `walls` is absent, and the backlog is history."""
        import json

        self.state.parent.mkdir(parents=True, exist_ok=True)
        self.state.write_text(
            json.dumps({"scopes": {"run-ra1.scope": {"pid": 1, "pid_start": 7, "samples": []}}, "incidents": {}})
        )
        self.scopes({"run-ra1.scope": 42422})
        envoy = self.run_pass()
        self.assertEqual(envoy.sent, [])
        kept = json.loads(self.state.read_text())
        self.assertEqual(kept["walls"]["run-ra1.scope"]["events_max"], 42422)

        self.scopes({"run-ra1.scope": 42423})
        envoy = self.run_pass()
        self.assertEqual(
            sorted(session for session, _ in envoy.sent),
            ["00000001-0000-0000-0000-000000000001", WATCH.INFRA_LEAD],
        )

    def test_the_digest_carries_each_scopes_top_three_with_command_lines(self) -> None:
        """The INFRA lead reads the digest without the owner's report beside it."""
        WATCH.rss_of = lambda pid: {1: 9 * GIB}.get(pid, 2 * GIB)
        WATCH.cmdline_of = lambda pid: f"omp @/home/sami/work/.ossv-workers/w{pid}/message.md --lane {pid}"
        self.scopes({"run-ra1.scope": 1})
        self.run_pass()
        self.scopes({"run-ra1.scope": 6})
        envoy = self.run_pass()
        digest = [text for session, text in envoy.sent if session == WATCH.INFRA_LEAD][0]
        self.assertIn("run-ra1.scope", digest)
        self.assertIn("9.00 GiB  pid 1", digest)
        self.assertIn(".ossv-workers/w1/message.md --lane 1", digest)

    def test_a_digest_restored_from_the_state_file_still_has_the_command_lines(self) -> None:
        """A touch waits on disk through JSON, where its tuples come back as lists."""
        import json

        self.scopes({"run-ra1.scope": 1})
        self.run_pass()
        self.scopes({"run-ra1.scope": 2})
        self.run_pass()
        self.scopes({"run-ra1.scope": 2, "run-rb2.scope": 5})
        self.run_pass()
        kept = json.loads(self.state.read_text())
        self.assertTrue(kept["wall_pending"][0]["top"])
        self.scopes({"run-ra1.scope": 2, "run-rb2.scope": 5})
        envoy = self.run_pass(wall_digest=0.0)
        digest = [text for session, text in envoy.sent if session == WATCH.INFRA_LEAD][0]
        self.assertIn("pid 2", digest)
        self.assertIn(".ossv-workers/w2/message.md", digest)


if __name__ == "__main__":
    unittest.main()
