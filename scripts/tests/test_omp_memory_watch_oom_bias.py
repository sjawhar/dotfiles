#!/usr/bin/env python3
"""omp-memory-watch's oom bias: the kernel must pick a runaway child, not the session's omp.

When a capped omp scope reaches its MemoryMax the memcg OOM killer kills its member with the
largest badness. With every oom_score_adj at 0 that was the session's omp (14.9 of 24 GiB at
the 2026-10-27 kill, dispatch://INFRA-602), and about 40 subagents died with it. The watch now
raises every member except the session's own omp -- the omp whose parent is outside the scope --
to +500. A nested omp (a subagent) is a child like any other.

Every /proc and cgroup file here is a synthetic tree; no real process is read or written.
"""

import argparse
import importlib.machinery
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "omp-memory-watch"
LOADER = importlib.machinery.SourceFileLoader("omp_memory_watch_oom_bias", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
WATCH = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(WATCH)

GIB = WATCH.GIB
SCOPE = "/user.slice/user-1001.slice/user@1001.service/app.slice/run-u1.scope"
LOGIN = "/user.slice/user-1001.slice/session-3.scope"


class Tree:
    """A fake /proc and /sys/fs/cgroup the watch is pointed at."""

    def __init__(self, root: Path) -> None:
        self.proc = root / "proc"
        self.cgroup = root / "cgroup"
        self.proc.mkdir()
        self.cgroup.mkdir()

    def scope(self, cgroup: str, pids: list[int], limit: int | None = 24 * GIB) -> None:
        directory = self.cgroup / cgroup.strip("/")
        directory.mkdir(parents=True, exist_ok=True)
        if limit is not None:
            (directory / "memory.max").write_text(f"{limit}\n")
        (directory / "cgroup.procs").write_text("".join(f"{pid}\n" for pid in pids))

    def process(self, pid: int, comm: str, parent: int, adj: int = 0) -> Path:
        directory = self.proc / str(pid)
        directory.mkdir()
        (directory / "comm").write_text(f"{comm}\n")
        (directory / "stat").write_text(f"{pid} ({comm}) S {parent} {pid} {pid} 0 -1 4194304 0 0\n")
        (directory / "oom_score_adj").write_text(f"{adj}\n")
        return directory / "oom_score_adj"

    def adj(self, pid: int) -> int:
        return int((self.proc / str(pid) / "oom_score_adj").read_text())


class OomBiasPassTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tree = Tree(Path(self.tmp.name))
        for name, value in (("PROC_ROOT", self.tree.proc), ("CGROUP_ROOT", self.tree.cgroup)):
            original = getattr(WATCH, name)
            setattr(WATCH, name, value)
            self.addCleanup(setattr, WATCH, name, original)

    def session_scope(self) -> None:
        """The run-u20238.scope shape of 2026-11-02 01:09Z: the session's omp, a nested
        subagent omp, two tsgo compilers and a git."""
        self.tree.scope(SCOPE, [100, 101, 102, 103, 104])
        self.tree.process(100, "omp", parent=1)  # started by shims/omp: its parent is outside
        self.tree.process(101, "omp", parent=100)  # a nested subagent omp
        self.tree.process(102, "tsgo", parent=100)
        self.tree.process(103, "tsgo", parent=100)
        self.tree.process(104, "git", parent=101)

    def test_every_child_is_raised_and_only_the_sessions_own_omp_is_kept(self) -> None:
        self.session_scope()
        counts = WATCH.oom_bias_pass([(100, SCOPE), (101, SCOPE)], 500, dry_run=False)
        self.assertEqual(self.tree.adj(100), 0, "the session's omp must stay the least likely victim")
        for pid in (101, 102, 103, 104):
            self.assertEqual(self.tree.adj(pid), 500, f"pid {pid} must be preferred over the session")
        self.assertEqual((counts["scopes"], counts["raised"], counts["refused"]), (1, 4, 0))

    def test_a_higher_adj_is_never_lowered(self) -> None:
        self.session_scope()
        (self.tree.proc / "102" / "oom_score_adj").write_text("900\n")
        counts = WATCH.oom_bias_pass([(100, SCOPE)], 500, dry_run=False)
        self.assertEqual(self.tree.adj(102), 900)
        self.assertEqual(counts["already"], 1)

    @unittest.skipIf(os.geteuid() == 0, "root may write a read-only file")
    def test_a_process_it_may_not_write_is_counted_as_refused(self) -> None:
        self.session_scope()
        (self.tree.proc / "103" / "oom_score_adj").chmod(0o444)
        counts = WATCH.oom_bias_pass([(100, SCOPE)], 500, dry_run=False)
        self.assertEqual(self.tree.adj(103), 0)
        self.assertEqual(counts["refused"], 1)
        self.assertEqual(counts["raised"], 3)

    def test_a_scope_without_a_limit_of_its_own_is_not_touched(self) -> None:
        self.tree.scope(LOGIN, [200, 201], limit=None)
        self.tree.process(200, "omp", parent=1)
        self.tree.process(201, "python3", parent=200)
        counts = WATCH.oom_bias_pass([(200, LOGIN)], 500, dry_run=False)
        self.assertEqual(self.tree.adj(201), 0)
        self.assertEqual(counts["scopes"], 0)

    def test_a_pass_past_its_deadline_leaves_the_scope_for_the_next_run(self) -> None:
        self.session_scope()
        counts = WATCH.oom_bias_pass([(100, SCOPE)], 500, dry_run=False, deadline=0.0)
        self.assertEqual(counts["unfinished"], 1)
        self.assertEqual(self.tree.adj(102), 0)

    def test_dry_run_counts_and_writes_nothing(self) -> None:
        self.session_scope()
        counts = WATCH.oom_bias_pass([(100, SCOPE)], 500, dry_run=True)
        self.assertEqual(counts["raised"], 4)
        self.assertEqual([self.tree.adj(pid) for pid in (101, 102, 103, 104)], [0, 0, 0, 0])

    def test_with_no_omp_parented_outside_every_omp_is_left_alone(self) -> None:
        self.tree.scope(SCOPE, [300, 301, 302])
        self.tree.process(300, "bash", parent=1)
        self.tree.process(301, "omp", parent=300)
        self.tree.process(302, "python3", parent=301)
        counts = WATCH.oom_bias_pass([(301, SCOPE)], 500, dry_run=False)
        self.assertEqual(self.tree.adj(301), 0)
        self.assertEqual(self.tree.adj(302), 500)
        self.assertEqual(counts["no_leader"], 1)


class OomBiasInTheRunTest(unittest.TestCase):
    """The pass runs inside run_once, is reported in its log line, and can never fail it."""

    class QuietEnvoy:
        def __init__(self, timeout: float = 8.0) -> None:
            pass

        def request(self, method, path, body=None):
            return 500, {}

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.lines: list[str] = []
        for name, value in (
            ("Envoy", self.QuietEnvoy),
            ("omp_processes", lambda: []),
            ("log", self.lines.append),
        ):
            original = getattr(WATCH, name)
            setattr(WATCH, name, value)
            self.addCleanup(setattr, WATCH, name, original)

    def options(self, **overrides) -> argparse.Namespace:
        values = dict(
            fraction=0.70, clear_fraction=0.62, sample_fraction=0.45, samples=24, unlimited_warn=16.8,
            grace=60.0, escalate=600.0, wall_fraction=0.98, wall_cooldown=900.0, wall_quiet_reclaimable=0.50, wall_burst=3,
            wall_burst_window=900.0, wall_top=3, wall_pending_max=400, wall_digest_mode="send", wall_urgent_anon=0.50,
            wall_pending_json=False, state=Path(self.tmp.name) / "state.json", http_timeout=8.0,
            dry_run=True, write_state=False, print_alert=False, oom_bias=True, oom_bias_adj=500,
            restart=False, restart_dry_run=False, restart_cooldown=3600.0, adopt_scopes=False,
        )
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_the_counts_are_in_the_runs_log_line(self) -> None:
        self.assertEqual(WATCH.run_once(self.options()), 0)
        self.assertIn("oom bias +500: 0 capped scope(s), would raise 0", self.lines[-1])

    def test_a_failing_pass_is_logged_and_the_run_still_succeeds(self) -> None:
        def broken(*_args):
            raise RuntimeError("procfs went away")

        original = WATCH.oom_bias_pass
        WATCH.oom_bias_pass = broken
        self.addCleanup(setattr, WATCH, "oom_bias_pass", original)
        self.assertEqual(WATCH.run_once(self.options()), 0)
        self.assertIn("oom bias failed: RuntimeError: procfs went away", self.lines[-1])

    def test_no_oom_bias_turns_it_off(self) -> None:
        def must_not_run(*_args):
            raise AssertionError("--no-oom-bias must not run the pass")

        original = WATCH.oom_bias_pass
        WATCH.oom_bias_pass = must_not_run
        self.addCleanup(setattr, WATCH, "oom_bias_pass", original)
        self.assertEqual(WATCH.run_once(self.options(oom_bias=False)), 0)
        self.assertIn("oom bias: off", self.lines[-1])


if __name__ == "__main__":
    unittest.main()
