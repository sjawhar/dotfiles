#!/usr/bin/env python3
"""omp-memory-watch's scope adoption: a session with no run-*.scope of its own gets one.

shims/omp starts a session with no memory ceiling when both of its scope probes time out
(01a19679 at 11:57Z on 2026-11-02, in the login scope session-c5.scope). Sessions from before
shims/omp c31519b run in scopes with OOMPolicy=stop. Each pass moves such a session -- its own
process tree, never the whole login scope -- into a new delegated run-c<session>.scope with
OOMPolicy=continue.

Every /proc and cgroup file here is synthetic, and every systemd call is recorded, not made.
"""

import argparse
import importlib.machinery
import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "omp-memory-watch"
LOADER = importlib.machinery.SourceFileLoader("omp_memory_watch_adopt", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
WATCH = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(WATCH)

import os  # noqa: E402

UID = os.getuid()
USER = f"/user.slice/user-{UID}.slice"
LOGIN = f"{USER}/session-c5.scope"
APP = f"{USER}/user@{UID}.service/app.slice"
OLD = f"{APP}/run-u4242.scope"
BOX = "/system.slice/docker-053db9df5925.scope/init.scope"
SID = "01a19679-1111-7000-8000-000000000001"
TICKS = os.sysconf("SC_CLK_TCK")


class Tree:
    def __init__(self, root: Path) -> None:
        self.proc = root / "proc"
        self.cgroup = root / "cgroup"
        self.proc.mkdir()
        self.cgroup.mkdir()
        (self.proc / "uptime").write_text("100000.00 1.00\n")

    def process(self, pid: int, comm: str, parent: int, cgroup: str, argv: list[str] | None = None,
                env: dict | None = None, age: float = 3600.0, rss_pages: int = 1000) -> None:
        directory = self.proc / str(pid)
        directory.mkdir()
        start = int((100000.0 - age) * TICKS)
        fields = ["S", str(parent)] + ["0"] * 17 + [str(start), "0"]
        (directory / "comm").write_text(f"{comm}\n")
        (directory / "stat").write_text(f"{pid} ({comm}) " + " ".join(fields) + "\n")
        (directory / "cgroup").write_text(f"0::{cgroup}\n")
        (directory / "cmdline").write_bytes("\0".join(argv or [comm]).encode() + b"\0")
        (directory / "environ").write_bytes(
            b"".join(f"{k}={v}\0".encode() for k, v in (env or {}).items()))
        (directory / "statm").write_text(f"{rss_pages * 2} {rss_pages} 0 0 0 0 0\n")
        group = self.cgroup / cgroup.strip("/")
        group.mkdir(parents=True, exist_ok=True)
        procs = group / "cgroup.procs"
        procs.write_text((procs.read_text() if procs.exists() else "") + f"{pid}\n")


class FakeState:
    def __init__(self) -> None:
        self.data: dict = {}
        self.commits = 0

    def commit(self, options) -> None:
        self.commits += 1


class AdoptPassTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tree = Tree(Path(self.tmp.name))
        self.calls: list[list[str]] = []
        self.policy = {"run-u4242.scope": "stop"}
        for name, value in (("PROC_ROOT", self.tree.proc), ("CGROUP_ROOT", self.tree.cgroup),
                            ("systemd_user", self.systemd), ("log", lambda line: None)):
            original = getattr(WATCH, name)
            setattr(WATCH, name, value)
            self.addCleanup(setattr, WATCH, name, original)
        self.state = FakeState()

    def systemd(self, argv: list[str], timeout: float = 10.0) -> tuple[bool, str]:
        self.calls.append(argv)
        if argv[:3] == ["systemctl", "--user", "show"] and "OOMPolicy" in argv:
            units = [a for a in argv[3:] if a.endswith(".scope")]
            return True, "\n\n".join(f"Id={u}\nOOMPolicy={self.policy.get(u, 'continue')}" for u in units)
        if argv[:3] == ["systemctl", "--user", "show"]:
            return True, "MemoryMax=25769803776\nMemorySwapMax=0\nTasksMax=infinity"
        return True, ""

    def run_pass(self, dry_run: bool = False, omps=None) -> str:
        if omps is None:
            omps = [(int(p.name), (p / "cgroup").read_text().strip()[3:])
                    for p in self.tree.proc.iterdir() if p.name.isdigit()
                    and (p / "comm").read_text().strip() == "omp"]
        return WATCH.scope_adopt_pass(omps, self.state, argparse.Namespace(dry_run=dry_run), 0.0)

    def bus(self, method: str) -> list[list[str]]:
        return [call for call in self.calls if method in call]

    def unscoped_session(self) -> None:
        """01a19679's shape: its omp, a subagent omp and a compiler in the login scope, beside a
        process of another login that must stay; and a tool child with a scope of its own."""
        self.tree.process(300, "bash", 1, LOGIN)
        self.tree.process(500, "omp", 300, LOGIN, ["/x/bin/omp", "--resume", SID], {"OMP_MEMORY_SCOPE": "8G"})
        self.tree.process(501, "omp", 500, LOGIN)
        self.tree.process(502, "tsgo", 500, LOGIN)
        self.tree.process(503, "sleep", 500, f"{APP}/run-u9.scope")
        self.tree.process(600, "vim", 300, LOGIN)

    def test_an_unscoped_session_moves_with_its_own_tree_only(self) -> None:
        self.unscoped_session()

        note = self.run_pass()

        start = self.bus("StartTransientUnit")
        self.assertEqual(len(start), 1)
        argv = start[0]
        self.assertEqual(argv[argv.index("StartTransientUnit") + 2], "run-c01a1967911117000.scope")
        joined = " ".join(argv)
        for want in ("OOMPolicy s continue", "Delegate b true", "Slice s app.slice",
                     f"MemoryMax t {8 << 30}", "MemorySwapMax t 0", "PIDs au 1 500 0"):
            self.assertIn(want, joined)
        attached = sorted(call[-1] for call in self.bus("AttachProcessesToUnit"))
        self.assertEqual(attached, ["501", "502"])  # never 600 (another login's), never 503
        self.assertTrue(all(call[-2] == "1" for call in self.bus("AttachProcessesToUnit")))
        self.assertIn("moved 1 session", note)
        self.assertEqual(self.state.data["adopted"]["run-c01a1967911117000.scope"]["pid"], 500)

    def test_no_ceiling_asked_takes_the_shims_default(self) -> None:
        self.tree.process(500, "omp", 1, LOGIN, ["/x/bin/omp"], {"OMP_SESSION_ID": SID})

        self.run_pass()

        self.assertIn(f"MemoryMax t {24 << 30}", " ".join(self.bus("StartTransientUnit")[0]))

    def test_a_dry_run_moves_nothing(self) -> None:
        self.unscoped_session()

        note = self.run_pass(dry_run=True)

        self.assertEqual(self.bus("StartTransientUnit") + self.bus("AttachProcessesToUnit"), [])
        self.assertIn("would move 1 session", note)

    def test_an_oom_policy_stop_scope_moves_with_its_limits(self) -> None:
        self.tree.process(500, "omp", 1, OLD, ["/x/bin/omp", "--resume", SID])
        self.tree.process(501, "tsgo", 500, OLD)

        self.run_pass()

        joined = " ".join(self.bus("StartTransientUnit")[0])
        self.assertIn("MemoryMax t 25769803776", joined)
        self.assertNotIn("TasksMax", joined)
        self.assertEqual([call[-1] for call in self.bus("AttachProcessesToUnit")], ["501"])

    def test_a_continue_scope_a_box_a_subagent_and_a_new_omp_are_left(self) -> None:
        self.policy = {}
        self.tree.process(500, "omp", 1, OLD, ["/x/bin/omp"])  # OOMPolicy=continue already
        self.tree.process(700, "omp", 1, BOX, ["/x/bin/omp", "--resume", SID])  # an agent box
        self.tree.process(800, "omp", 1, LOGIN, ["/x/bin/omp", "--version"], age=5)  # a moment's omp

        self.run_pass()

        self.assertEqual(self.bus("StartTransientUnit"), [])

    def test_the_policy_of_a_scope_is_read_once(self) -> None:
        self.policy = {}
        self.tree.process(500, "omp", 1, OLD, ["/x/bin/omp"])

        self.run_pass()
        self.run_pass()

        self.assertEqual(len([c for c in self.calls if "OOMPolicy" in c]), 1)

    def test_what_was_left_behind_is_attached_on_the_next_pass(self) -> None:
        self.unscoped_session()
        self.state.data["adopted"] = {"run-c01a1967911117000.scope": {
            "pid": 500, "start": WATCH.pid_start(500), "from": LOGIN, "kind": "unscoped"}}
        (self.tree.proc / "500" / "cgroup").write_text(f"0::{APP}/run-c01a1967911117000.scope\n")

        self.run_pass()

        self.assertEqual(self.bus("StartTransientUnit"), [])
        self.assertEqual(sorted(call[-1] for call in self.bus("AttachProcessesToUnit")), ["501", "502"])

    def test_a_tree_already_over_its_ceiling_is_left_and_logged(self) -> None:
        self.tree.process(500, "omp", 1, LOGIN, ["/x/bin/omp"], {"OMP_MEMORY_SCOPE": "1M"}, rss_pages=1000)

        self.run_pass()

        self.assertEqual(self.bus("StartTransientUnit"), [])


if __name__ == "__main__":
    unittest.main()
