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
    """Records what left, and answers the live-sessions lookup the watch makes before a report.

    `live` is what GET /v1/sessions returns; None makes it fail, which must leave attribution
    unverified rather than calling every session dead.
    """

    instances: list = []
    live: list | None = None

    def __init__(self, timeout: float = 8.0) -> None:
        self.sent: list[tuple[str, str]] = []
        self.published: list[tuple[str, str]] = []
        FakeEnvoy.instances.append(self)

    def request(self, method: str, path: str, body=None):
        if (method, path) == ("GET", "/v1/sessions") and FakeEnvoy.live is not None:
            return 200, FakeEnvoy.live
        return 500, {"error": "no answer in this test"}

    def send(self, session_id: str, message: str) -> bool:
        self.sent.append((session_id, message))
        return True

    def publish(self, topic: str, message: str) -> bool:
        self.published.append((topic, message))
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
        # These runs read synthetic cgroups: neither acting pass may touch a real process or pane.
        "oom_bias": False,
        "oom_bias_adj": 500,
        "restart": False,
        "restart_dry_run": False,
        "restart_cooldown": 3600.0,
        "adopt_scopes": False,
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

    # ── a capped job working as designed ────────────────────────────────────────────────────
    def jail_target(self, cgroup: str, name: str, limit: int) -> dict:
        """As run_once builds it, memory.stat included: the split decides whether it is quiet."""
        usage, current, fields = WATCH.scope_pressure(cgroup)
        peak = WATCH.read_int(WATCH.CGROUP_ROOT / cgroup.strip("/") / "memory.peak")
        return dict(
            key=name, scope=name, cgroup=cgroup, pid=4242, limit=limit,
            limit_from=f"memory.max of {name}", limited=True, usage=usage, current=current,
            events=WATCH.memory_events(cgroup), peak_bytes=peak, swap_max=WATCH.swap_setting(cgroup),
            stats=fields, measured="test",
        )

    def test_a_wall_of_page_cache_with_no_oom_is_not_told(self) -> None:
        """Jailed `omp -p` readers on an 8 GiB run-r*.scope sat on their wall with 76-94% of it
        page cache, oom 0, and were told to the box-health owner every few minutes (pid
        1047986 at 06:29Z on 2026-11-04). That is the cap doing its job."""
        jail = 8 * GIB
        name = "run-rjail.scope"
        # Seeded below the wall, so the first call is a baseline and not itself a touch.
        cgroup = write_scope(self.root, name, limit=jail, events_max=0, peak=1 * GIB, current=1 * GIB,
                             anon=int(GIB * 0.12))
        walls: dict = {}
        self.assertIsNone(
            WATCH.wall_touch(self.jail_target(cgroup, name, jail), walls, self.options, 1000.0, False))
        for moment, rises in ((1030.0, 40), (1060.0, 400), (1090.0, 4000)):
            write_scope(self.root, name, limit=jail, events_max=rises, peak=jail, current=jail,
                        anon=int(jail * 0.12))
            self.assertIsNone(
                WATCH.wall_touch(self.jail_target(cgroup, name, jail), walls, self.options, moment, False),
                f"a cache-full wall with oom 0 was told at {moment}")
        self.assertEqual(walls[name]["quiet"], 3, "the quiet touches were not counted")
        self.assertNotIn("reported_at", walls[name], "a quiet touch started the cooldown")

    def test_the_first_dangerous_touch_after_quiet_ones_is_told_at_once_and_counts_them(self) -> None:
        """Quiet touches start no cooldown: a scope whose reclaim then fails is told THAT run."""
        jail = 8 * GIB
        name = "run-rturn.scope"
        cgroup = write_scope(self.root, name, limit=jail, peak=1 * GIB, current=1 * GIB, anon=int(GIB * 0.12))
        walls: dict = {}
        self.assertIsNone(
            WATCH.wall_touch(self.jail_target(cgroup, name, jail), walls, self.options, 1000.0, False))
        write_scope(self.root, name, limit=jail, events_max=50, peak=jail, current=jail, anon=int(jail * 0.12))
        self.assertIsNone(WATCH.wall_touch(self.jail_target(cgroup, name, jail), walls, self.options, 1030.0, False))
        # Reclaim failed once: oom rises. Not quiet any more, whatever the cache says.
        write_scope(self.root, name, limit=jail, events_max=90, peak=jail, current=jail,
                    anon=int(jail * 0.12), oom=1)
        touch = WATCH.wall_touch(self.jail_target(cgroup, name, jail), walls, self.options, 1060.0, False)
        self.assertIsNotNone(touch, "a wall where reclaim failed was kept quiet")
        assert touch is not None
        self.assertEqual(touch["quiet"], 1)
        self.assertEqual(touch["events_oom"], 1)

    def test_each_condition_alone_makes_the_touch_told(self) -> None:
        """Quiet needs ALL three: oom 0, oom_kill 0, and at least half the ceiling reclaimable."""
        jail = 8 * GIB
        for label, kwargs in (
            ("oom", dict(anon=int(jail * 0.12), oom=2)),
            ("oom_kill", dict(anon=int(jail * 0.12), oom_kill=1)),
            ("mostly anon", dict(anon=int(jail * 0.60))),  # only 40% reclaimable
        ):
            with self.subTest(label):
                name = f"run-r{label.replace(' ', '')}.scope"
                cgroup = write_scope(self.root, name, limit=jail, peak=1 * GIB, current=1 * GIB,
                                     anon=int(GIB * 0.12))
                walls: dict = {}
                self.assertIsNone(
                    WATCH.wall_touch(self.jail_target(cgroup, name, jail), walls, self.options, 1000.0, False))
                write_scope(self.root, name, limit=jail, events_max=7, peak=jail, current=jail, **kwargs)
                self.assertIsNotNone(
                    WATCH.wall_touch(self.jail_target(cgroup, name, jail), walls, self.options, 1030.0, False),
                    f"{label}: a wall that is not just page cache was kept quiet")

    def test_a_told_scope_is_told_at_most_once_an_hour(self) -> None:
        """The default --wall-cooldown is an hour (it was 15 min: four notices an hour for one
        job that sat on its wall)."""
        source = Path(WATCH.__file__).read_text(encoding="utf-8")
        self.assertRegex(source, r'"--wall-cooldown", type=float, default=3600\.0',
                         "the default is no longer one report per scope per hour")
        self.assertRegex(source, r'"--wall-quiet-reclaimable", type=float, default=0\.50')
        name = "run-rhour.scope"
        opts = options(self.root / "state.json", wall_cooldown=3600.0)
        cgroup = write_scope(self.root, name, events_max=1, anon=int(CEILING * 0.9), current=CEILING, peak=CEILING)
        walls: dict = {}
        self.assertIsNotNone(WATCH.wall_touch(self.jail_target(cgroup, name, CEILING), walls, opts, 1000.0, False))
        for index, moment in enumerate((1900.0, 2800.0, 4500.0), start=2):
            write_scope(self.root, name, events_max=index, anon=int(CEILING * 0.9), current=CEILING, peak=CEILING)
            self.assertIsNone(WATCH.wall_touch(self.jail_target(cgroup, name, CEILING), walls, opts, moment, False),
                              f"told again {moment - 1000:.0f}s after the last report")
        write_scope(self.root, name, events_max=9, anon=int(CEILING * 0.9), current=CEILING, peak=CEILING)
        touch = WATCH.wall_touch(self.jail_target(cgroup, name, CEILING), walls, opts, 1000.0 + 3601, False)
        assert touch is not None
        self.assertEqual(touch["suppressed"], 3)


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

    def touch(self, pid: int = 11) -> dict:
        name = "run-rreport.scope"
        cgroup = write_scope(
            self.root, name, events_max=5, peak=CEILING, current=16 * GIB, procs=tuple(sorted(self.commands))
        )
        walls: dict = {name: {"events_max": 0, "peak": 1 * GIB}}
        target = dict(
            key=name, scope=name, cgroup=cgroup, pid=pid, limit=CEILING,
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

    def test_a_scope_led_by_the_worker_broker_is_reported_to_its_session(self) -> None:
        """At 09:41:57Z on 2026-11-02 the librarian's scope was led by omp's worker broker, which
        names no session, and the report went to nobody; the session omp was in the same scope."""
        (self.worker_dir / "session_unconfirmed").write_text("")
        self.commands[15] = "/home/sami/.mise/installs/omp/bin/omp __omp_worker_daemon_broker"
        self.rss[15] = 12 * GIB
        sid = "01a14ba2-817c-748e-9cb5-d1ad60308a9c"
        original = WATCH.session_of
        WATCH.session_of = lambda pid: sid if pid == 11 else ""
        self.addCleanup(lambda: setattr(WATCH, "session_of", original))
        self.assertEqual(self.touch(pid=15)["session"], sid)

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
        self.assertIn("DO, first and whatever the cause:", WATCH.wall_text(touch))
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

    def test_the_lever_follows_the_dominant_field(self) -> None:
        """A scope whose memory is anonymous must not be told to cut its file traffic."""
        page_cache = WATCH.wall_advice(self.touch())
        self.assertIn("this lane's file traffic", page_cache)
        anonymous = WATCH.wall_advice(
            self.touch(stats={"anon": 18 * GIB, "file": GIB, "shmem": 0, "kernel": 0, "sock": 0})
        )
        self.assertIn("already ended", anonymous)
        self.assertNotIn("file traffic", anonymous)
        shared = WATCH.wall_advice(
            self.touch(stats={"anon": GIB, "file": GIB, "shmem": 18 * GIB, "kernel": 0, "sock": 0})
        )
        self.assertIn("/dev/shm", shared)
        self.assertNotIn("file traffic", shared)

    def test_a_fallen_back_scope_says_the_split_describes_it_now(self) -> None:
        advice = WATCH.wall_advice(self.touch(current=2 * GIB))
        self.assertIn("not as it was at the ceiling", advice)

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

    def test_the_digest_row_is_box_worker_peak_and_the_file_anon_split(self) -> None:
        row = WATCH.digest_row(self.touch())
        self.assertIn("c-asan", row)
        self.assertIn("peak 24.00 GiB of 24.00 GiB", row)
        self.assertIn("file 17.00 GiB (71%)", row)
        self.assertIn("anon 2.00 GiB (8%)", row)
        # The detail stays in the owner's own report.
        self.assertNotIn("processes holding", row)
        self.assertNotIn("NOT IN ANY PROCESS", row)

    def test_an_oom_kill_shows_on_the_row(self) -> None:
        self.assertIn("oom_kill 2", WATCH.digest_row(self.touch(events_oom_kill=2)))

    def test_a_touch_is_urgent_on_anon_or_an_oom_kill(self) -> None:
        """The two shapes that end in a kill do not wait for the hourly digest."""
        import argparse

        options = argparse.Namespace(wall_urgent_anon=0.50)
        self.assertEqual(WATCH.urgent_reason(self.touch(), options), "")
        anon = self.touch(stats={"anon": 20 * GIB, "file": GIB, "shmem": 0, "kernel": 0, "sock": 0})
        self.assertIn("cannot reclaim", WATCH.urgent_reason(anon, options))
        self.assertIn("83%", WATCH.urgent_reason(anon, options))
        killed = self.touch(events_oom_kill=1)
        self.assertIn("already killed", WATCH.urgent_reason(killed, options))
        self.assertIn("NOT WAITING FOR THE DIGEST", WATCH.urgent_text(killed, "x"))


class WallVerdictTest(unittest.TestCase):
    """The report's verdict follows the counters, not the fact of being at the wall.

    What a wrong verdict costs: on 2026-11-01 a session read three "one allocation away from a
    memcg OOM kill" reports on a scope whose memory.events said `max` +697, `oom` 0, `oom_kill`
    0 and whose 24.00 GiB held 10.14 GiB of page cache, and nearly cut a shared fleet gate from
    2 slots to 1 -- doubling the wait for ~27 lanes -- to avert a kill the counters said was not
    coming. cgroup-v2.rst: `max` counts the times usage "was about to go over the max boundary"
    and reclaim brought it down; `oom` is the counter that rises when reclaim fails.
    """

    def touch(self, **overrides) -> dict:
        base = dict(
            at=1000.0, scope="run-r1.scope", key="run-r1.scope", session="s", worker="c-asan",
            limit=CEILING, limit_from="memory.max", peak=CEILING, current=CEILING,
            swap_max="0", events_max=697, events_delta=697, events_oom=0, events_oom_kill=0,
            peak_touch=True, task_count=12, rss_sum=4 * GIB,
            top=[(11, GIB, "omp --resume x")], suppressed=0, cooldown=900.0,
            warn_fraction=0.70, urgent_anon=0.50,
        )
        base.update(overrides)
        stats = base["stats"]
        base.setdefault(
            "usage",
            stats.get("anon", 0) + stats.get("shmem", 0) + stats.get("kernel", 0) + stats.get("sock", 0),
        )
        return base

    def cache_dominated(self, **overrides) -> dict:
        """Tonight's third real event: 24.00 GiB at the ceiling, 10.14 GiB of it page cache."""
        return self.touch(
            stats={"anon": 11545 * GIB // 1024, "file": 10383 * GIB // 1024,
                   "kernel": 2652 * GIB // 1024, "shmem": 0, "sock": 0},
            **overrides,
        )

    def unreclaimable(self, **overrides) -> dict:
        return self.touch(
            stats={"anon": 21 * GIB, "file": GIB, "kernel": 2 * GIB, "shmem": 0, "sock": 0},
            **overrides,
        )

    def test_the_headline_says_how_much_of_the_ceiling_is_reclaimable_cache(self) -> None:
        """The number that decides whether the alert means anything goes in the first sentence."""
        headline = WATCH.wall_text(self.cache_dominated()).splitlines()[0]
        self.assertIn("10.14 GiB (42% of the ceiling) is reclaimable page cache", headline)
        tight = WATCH.wall_text(self.unreclaimable()).splitlines()[0]
        self.assertIn("1.00 GiB (4% of the ceiling) is reclaimable page cache", tight)

    def test_a_cache_filled_scope_with_no_oom_is_told_it_is_not_about_to_die(self) -> None:
        state, verdict = WATCH.wall_state(self.cache_dominated())
        self.assertEqual(state, "normal")
        self.assertIn("NOT one allocation from a kill", verdict)
        text = WATCH.wall_text(self.cache_dominated())
        self.assertNotIn("one allocation away", text)
        self.assertIn("Do NOT kill your builds, halve a shared gate", text)

    def test_an_unreclaimable_scope_at_its_ceiling_keeps_the_urgency(self) -> None:
        state, verdict = WATCH.wall_state(self.unreclaimable())
        self.assertEqual(state, "urgent")
        self.assertIn("URGENT", verdict)
        self.assertIn("the memcg OOM killer runs", verdict)
        self.assertNotIn("NOT URGENT", verdict)

    def test_an_oom_kill_is_urgent_however_much_cache_the_scope_holds(self) -> None:
        state, verdict = WATCH.wall_state(self.cache_dominated(events_oom_kill=1))
        self.assertEqual(state, "urgent")
        self.assertIn("ALREADY killed", verdict)

    def test_a_failed_reclaim_is_urgent_before_anything_has_died(self) -> None:
        """`oom` rises when reclaim could not bring usage under memory.max; nothing died yet."""
        state, verdict = WATCH.wall_state(self.cache_dominated(events_oom=3))
        self.assertEqual(state, "urgent")
        self.assertIn("memory.events oom is 3", verdict)
        self.assertIn("the next failure is the kill", verdict)

    def test_an_unreadable_memory_stat_reads_as_urgent(self) -> None:
        """No split means no proof that any of the charge is reclaimable: assume none is."""
        state, verdict = WATCH.wall_state(self.touch(stats={}, usage=CEILING))
        self.assertEqual(state, "urgent")
        self.assertIn("conservatively", verdict)
        self.assertIn("none can be shown reclaimable", WATCH.wall_text(self.touch(stats={}, usage=CEILING)))

    def test_the_verdict_is_urgent_wherever_the_box_health_owner_is_told_at_once(self) -> None:
        """The owner's report and the immediate escalation can never contradict each other."""
        import argparse

        options = argparse.Namespace(wall_urgent_anon=0.50)
        anon_heavy = self.touch(
            stats={"anon": 13 * GIB, "file": 10 * GIB, "kernel": 0, "shmem": 0, "sock": 0}
        )
        # Under the 70% unreclaimable level, so only the anon share makes it urgent.
        self.assertLess(anon_heavy["usage"], 0.70 * CEILING)
        self.assertNotEqual(WATCH.urgent_reason(anon_heavy, options), "")
        self.assertEqual(WATCH.wall_state(anon_heavy)[0], "urgent")

    def test_the_handover_instruction_survives_every_state(self) -> None:
        for name, touch in (
            ("cache", self.cache_dominated()),
            ("unreclaimable", self.unreclaimable()),
            ("killed", self.cache_dominated(events_oom_kill=2)),
        ):
            with self.subTest(name):
                text = WATCH.wall_text(touch)
                self.assertIn("write your handover files now", text)
                # And everything else the report owes its reader.
                self.assertIn("Where that memory is, by memory.stat:", text)
                self.assertIn("Why you are hearing this and did not before:", text)
                self.assertIn("One report per scope per 15 min 0 s", text)
                self.assertIn("This watch only detects; it kills nothing.", text)


class RunOnceTest(unittest.TestCase):
    """One whole pass over synthetic scopes: who is sent what, and what the state file keeps."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        FakeEnvoy.instances = []
        FakeEnvoy.live = None
        self.addCleanup(self.reset_live)
        self.reset_live()
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

    def reset_live(self) -> None:
        """The live-session lookup is per RUN, so each pass must look it up again."""
        WATCH.LIVE_SESSIONS.clear()
        WATCH.LIVE_READ = False

    def scopes(self, events: dict[str, int], **fields) -> None:
        self.cgroups = {}
        for name, counter in events.items():
            self.cgroups[name] = write_scope(
                self.root, name, events_max=counter, peak=2 * GIB, current=2 * GIB,
                procs=(int(name[-7:-6]),), **fields
            )

    def processes(self) -> list[tuple[int, str]]:
        return [(int(name[-7:-6]), cgroup) for name, cgroup in self.cgroups.items()]

    def run_pass(self, **overrides) -> FakeEnvoy:
        WATCH.omp_processes = lambda: self.processes()
        self.reset_live()
        WATCH.run_once(options(self.state, **overrides))
        return FakeEnvoy.instances[-1]

    def report_of(self, text: str) -> str:
        """The report a message names; the message itself is at most 600 characters."""
        self.assertLessEqual(len(text), 600, text)
        head, _, path = text.rpartition("\nFull report: ")
        self.assertTrue(head and path, text)
        return Path(path).read_text()

    def fleet_digest(self) -> str:
        """What oss-verifier's wall-digest-collect.sh renders from this box's drained touches."""
        import contextlib
        import io
        import json

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            WATCH.drain_pending(options(self.state, wall_pending_json=True))
        return WATCH.wall_digest_text(json.loads(out.getvalue())["touches"], 3600.0, "1 lane VM(s)")

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
            ],
        )
        # Two touches are each their own owner's: the box-health owner hears 3 or more in 15 min.
        self.assertEqual(envoy.published, [])

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
            ["00000001-0000-0000-0000-000000000001"],
        )
        self.assertEqual(envoy.published, [])

    def test_the_fleet_digest_is_one_line_a_scope_and_not_the_owners_report(self) -> None:
        """A per-box digest every 15 min was eight messages an hour saying the same thing.

        The reader wants the shape of the fleet; each scope's own owner keeps the detail.
        """
        WATCH.rss_of = lambda pid: {1: 9 * GIB}.get(pid, 2 * GIB)
        WATCH.cmdline_of = lambda pid: f"omp @/home/sami/work/.ossv-workers/w{pid}/message.md --lane {pid}"
        self.scopes({"run-ra1.scope": 1})
        self.run_pass(wall_digest_mode="collect")
        self.scopes({"run-ra1.scope": 6})
        envoy = self.run_pass(wall_digest_mode="collect")
        digest = self.fleet_digest()
        rows = [line for line in digest.splitlines() if line.startswith("  2026")]
        self.assertEqual(len(rows), 1)
        self.assertIn("w1", rows[0])
        self.assertIn("peak 2.00 GiB of 24.00 GiB", rows[0])
        self.assertIn("file ", rows[0])
        self.assertIn("anon ", rows[0])
        self.assertNotIn("pid 1", digest)
        self.assertNotIn(".ossv-workers/w1/message.md", digest)
        # The owner's own report still has everything.
        report = self.report_of([text for session, text in envoy.sent if session.startswith("00000001")][0])
        self.assertIn("9.00 GiB  pid 1", report)

    def test_a_touch_that_waits_on_disk_keeps_the_split_the_row_needs(self) -> None:
        """In collect mode a touch waits in the state file, through JSON, for the collector."""
        import json

        self.scopes({"run-ra1.scope": 1})
        self.run_pass(wall_digest_mode="collect")
        self.scopes({"run-ra1.scope": 1, "run-rb2.scope": 5})
        self.run_pass(wall_digest_mode="collect")
        kept = json.loads(self.state.read_text())
        waiting = kept["wall_pending"][0]
        self.assertTrue(waiting["stats"]["file"])
        self.assertEqual(waiting["worker"], "w2")
        digest = self.fleet_digest()
        self.assertIn("w2", digest)
        self.assertIn("file 1.50 GiB", digest)

    def test_a_stale_session_id_is_not_sent_to_and_the_live_owner_gets_it(self) -> None:
        """A session id from an `omp --resume` path is the id the directory was CREATED with.

        On ossv-agent-repo that stale id was VERIFY, which runs on sami-rocqperf; sending there
        reaches that session, not a silent owner.
        """
        live = "01a14b84-6cd3-71af-918c-db534cd8d762"
        FakeEnvoy.live = [
            {"session_id": live, "host": WATCH.socket.gethostname()},
            # Not this box, whatever box the tests run on.
            {"session_id": "00000001-0000-0000-0000-000000000001", "host": "some-other-box.example"},
        ]
        self.scopes({"run-ra1.scope": 1})
        self.run_pass()
        self.scopes({"run-ra1.scope": 4})
        envoy = self.run_pass()
        owners = [session for session, _ in envoy.sent]
        self.assertEqual(owners, [live])
        report = self.report_of([text for session, text in envoy.sent if session == live][0])
        self.assertIn("is not live on this box", report)
        self.assertIn(f"so this went to {live}", report)

    def test_a_stale_id_with_no_live_owner_here_goes_to_the_box_health_owner(self) -> None:
        FakeEnvoy.live = [
            {"session_id": "01a14b84-6cd3-71af-918c-db534cd8d762", "host": WATCH.socket.gethostname()},
            {"session_id": "01a18c6a-ffcd-719e-92a0-31ace0ee6b81", "host": WATCH.socket.gethostname()},
        ]
        self.scopes({"run-ra1.scope": 1})
        self.run_pass()
        self.scopes({"run-ra1.scope": 4})
        envoy = self.run_pass()
        self.assertEqual([session for session, _ in envoy.sent], [])
        told = [text for topic, text in envoy.published if topic == WATCH.BOX_HEALTH_ROLE_TOPIC]
        self.assertEqual(len(told), 1)
        self.assertIn("no live session on this box owns it", told[0])
        self.assertIn("no live session here could be matched to it", self.report_of(told[0]))

    def test_a_live_id_is_sent_to_unchanged_and_says_nothing_extra(self) -> None:
        claim = "00000001-0000-0000-0000-000000000001"
        FakeEnvoy.live = [{"session_id": claim, "host": WATCH.socket.gethostname()}]
        self.scopes({"run-ra1.scope": 1})
        self.run_pass()
        self.scopes({"run-ra1.scope": 4})
        envoy = self.run_pass()
        report = self.report_of([text for session, text in envoy.sent if session == claim][0])
        self.assertNotIn("not live on this box", report)

    def test_an_envoy_that_cannot_list_sessions_still_tells_the_owner(self) -> None:
        """Unverified must never become 'nobody is live': the report would reach no one."""
        FakeEnvoy.live = None
        self.scopes({"run-ra1.scope": 1})
        self.run_pass()
        self.scopes({"run-ra1.scope": 4})
        envoy = self.run_pass()
        self.assertIn("00000001-0000-0000-0000-000000000001", [session for session, _ in envoy.sent])

    def test_collect_mode_sends_no_digest_and_keeps_the_touches(self) -> None:
        """Eight boxes each sending a digest every 15 minutes was 32 messages an hour."""
        import json

        self.scopes({"run-ra1.scope": 1})
        self.run_pass(wall_digest_mode="collect")
        self.scopes({"run-ra1.scope": 4})
        envoy = self.run_pass(wall_digest_mode="collect")
        # The scope's own owner still hears; nobody sends a digest.
        self.assertEqual([session for session, _ in envoy.sent], ["00000001-0000-0000-0000-000000000001"])
        kept = json.loads(self.state.read_text())
        self.assertEqual(len(kept["wall_pending"]), 1)

    def test_the_drain_prints_the_touches_with_the_box_and_clears_them(self) -> None:
        import json

        self.scopes({"run-ra1.scope": 1})
        self.run_pass(wall_digest_mode="collect")
        self.scopes({"run-ra1.scope": 4})
        self.run_pass(wall_digest_mode="collect")
        import io
        import contextlib

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            WATCH.drain_pending(options(self.state, wall_pending_json=True))
        answer = json.loads(out.getvalue())
        self.assertEqual(len(answer["touches"]), 1)
        self.assertEqual(answer["touches"][0]["box"], answer["box"])
        self.assertTrue(answer["touches"][0]["stats"]["file"])
        self.assertFalse(answer["locked_out"])
        # Drained once: a second drain has nothing, so no touch is ever counted twice.
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            WATCH.drain_pending(options(self.state, wall_pending_json=True))
        self.assertEqual(json.loads(out.getvalue())["touches"], [])

    def test_an_anon_dominated_touch_is_sent_at_once_even_in_collect_mode(self) -> None:
        self.scopes({"run-ra1.scope": 1})
        self.run_pass(wall_digest_mode="collect")
        # anon above half the peak: the kernel has nothing left to reclaim.
        self.scopes({"run-ra1.scope": 4}, anon=int(1.8 * GIB))
        envoy = self.run_pass(wall_digest_mode="collect")
        urgent = [text for _topic, text in envoy.published if "NOT WAITING FOR THE DIGEST" in text]
        self.assertEqual(len(urgent), 1)
        self.assertIn("cannot reclaim", urgent[0])

    def test_an_oom_kill_is_sent_at_once_even_in_collect_mode(self) -> None:
        self.scopes({"run-ra1.scope": 1})
        self.run_pass(wall_digest_mode="collect")
        self.scopes({"run-ra1.scope": 4}, oom_kill=3)
        envoy = self.run_pass(wall_digest_mode="collect")
        urgent = [text for _topic, text in envoy.published if "NOT WAITING FOR THE DIGEST" in text]
        self.assertEqual(len(urgent), 1)
        self.assertIn("already killed", urgent[0])

    def test_a_reclaimable_touch_is_not_urgent(self) -> None:
        self.scopes({"run-ra1.scope": 1})
        self.run_pass(wall_digest_mode="collect")
        self.scopes({"run-ra1.scope": 4})
        envoy = self.run_pass(wall_digest_mode="collect")
        self.assertEqual([text for _t, text in envoy.published if "NOT WAITING" in text], [])


if __name__ == "__main__":
    unittest.main()
