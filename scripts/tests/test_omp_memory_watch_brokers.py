#!/usr/bin/env python3
"""omp-memory-watch's broker pass: omp's worker broker, and each lane's services, leave the
session scope they inherited.

omp starts one worker broker per project directory as a plain child of the first session that
needs one, so on 2026-11-03 the broker of one project checkout and the services of 14
sessions ran in the chief's run-c01a13dcae9797264.scope. Each pass moves the broker into
omp-broker-<hash>.scope, and each service with an owner, with its tree, into
run-c<16 hex of the owner>-svc.scope described `omp session <owner>`.

Every /proc, cgroup and broker file here is synthetic, and every systemd call is recorded.
"""

import argparse
import importlib.machinery
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "omp-memory-watch"
LOADER = importlib.machinery.SourceFileLoader("omp_memory_watch_brokers", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
WATCH = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(WATCH)

UID = os.getuid()
USER = f"/user.slice/user-{UID}.slice"
APP = f"{USER}/user@{UID}.service/app.slice"
CHIEF = f"{APP}/run-c01a13dcae9797264.scope"
TOOL = f"{APP}/run-u77.scope"
BOX = "/system.slice/docker-053db9df5925.scope/init.scope"
CHIEF_SID = "01a13dca-e979-7264-af32-f654ef57a6f5"
LANE = "01a16553-3e04-7776-a285-c3590293cf71"
TICKS = os.sysconf("SC_CLK_TCK")


class Tree:
    def __init__(self, root: Path) -> None:
        self.proc = root / "proc"
        self.cgroup = root / "cgroup"
        self.runtime = root / "run" / "daemons" / "df6678a5a7e8e37a"
        self.proc.mkdir()
        self.cgroup.mkdir()
        (self.runtime / "daemons").mkdir(parents=True)
        (self.proc / "uptime").write_text("100000.00 1.00\n")

    def process(self, pid: int, comm: str, parent: int, cgroup: str, argv: list[str] | None = None,
                env: dict | None = None, age: float = 3600.0) -> None:
        directory = self.proc / str(pid)
        directory.mkdir()
        start = int((100000.0 - age) * TICKS)
        fields = ["S", str(parent)] + ["0"] * 17 + [str(start), "0"]
        (directory / "comm").write_text(f"{comm}\n")
        (directory / "stat").write_text(f"{pid} ({comm}) " + " ".join(fields) + "\n")
        (directory / "cgroup").write_text(f"0::{cgroup}\n")
        (directory / "cmdline").write_bytes("\0".join(argv or [comm]).encode() + b"\0")
        (directory / "environ").write_bytes(b"".join(f"{k}={v}\0".encode() for k, v in (env or {}).items()))
        group = self.cgroup / cgroup.strip("/")
        group.mkdir(parents=True, exist_ok=True)

    def move(self, pid: int, cgroup: str) -> None:
        (self.proc / str(pid) / "cgroup").write_text(f"0::{cgroup}\n")
        (self.cgroup / cgroup.strip("/")).mkdir(parents=True, exist_ok=True)

    def daemon(self, name: str, pid: int, owner: str | None, state: str = "ready") -> None:
        directory = self.runtime / "daemons" / name
        directory.mkdir()
        (directory / "meta.json").write_text(json.dumps({"daemon": {"name": name, "pid": pid, "owner": owner, "state": state}}))


class FakeState:
    def __init__(self) -> None:
        self.data: dict = {}

    def commit(self, options) -> None:
        pass


class BrokerPassTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tree = Tree(Path(self.tmp.name))
        self.calls: list[list[str]] = []
        for name, value in (("PROC_ROOT", self.tree.proc), ("CGROUP_ROOT", self.tree.cgroup),
                            ("OMP_HOME", str(self.tree.runtime.parents[1]) + "/"),
                            ("systemd_user", self.systemd), ("log", lambda line: None),
                            ("mem_total", lambda: 263897780 << 10)):  # sami-rocqperf: 24 GiB default
            original = getattr(WATCH, name)
            setattr(WATCH, name, value)
            self.addCleanup(setattr, WATCH, name, original)
        self.state = FakeState()
        self.broker_unit = WATCH.broker_unit(str(self.tree.runtime))
        self.delegated: dict[str, bool] = {}

    def systemd(self, argv: list[str], timeout: float = 10.0) -> tuple[bool, str]:
        """Record the call, and do what systemd would: a new scope takes its first pid; an attach
        moves the pid only into a delegated scope (systemd 255 refuses the rest with "Process
        migration not available on non-delegated units", sami-rocqperf 2026-11-03)."""
        self.calls.append(argv)
        if "StartTransientUnit" in argv:
            unit = argv[argv.index("StartTransientUnit") + 2]
            self.delegated[unit] = "Delegate b true" in " ".join(argv)
            self.tree.move(int(argv[-2]), f"{APP}/{unit}")
        elif "AttachProcessesToUnit" in argv:
            unit = argv[argv.index("AttachProcessesToUnit") + 2]
            if not self.delegated.get(unit):
                return False, "Call failed: Process migration not available on non-delegated units."
            self.tree.move(int(argv[-1]), f"{APP}/{unit}")
        elif argv[:3] == ["systemctl", "--user", "show"] and "Delegate" in argv:
            return True, "yes" if self.delegated.get(argv[-1]) else "no"
        return True, ""

    def old_scope(self, unit: str) -> None:
        """A scope an earlier version of the pass made without Delegate."""
        self.delegated[unit] = False
        (self.tree.cgroup / APP.strip("/") / unit).mkdir(parents=True)

    def run_pass(self, dry_run: bool = False) -> str:
        return WATCH.broker_scope_pass(self.state, argparse.Namespace(dry_run=dry_run))

    def cgroup(self, pid: int) -> str:
        return (self.tree.proc / str(pid) / "cgroup").read_text().strip()[3:]

    def started(self) -> dict[str, str]:
        found = {}
        for call in self.calls:
            if "StartTransientUnit" in call:
                found[call[call.index("StartTransientUnit") + 2]] = call[call.index("Description") + 2]
        return found

    def chief_shape(self) -> None:
        """77103's shape: the broker in the chief's scope, carrying the chief's OMP_SESSION_ID,
        with a chief service, a lane's service with a child, a lane service known only by its
        environment, the shared headless browser (no owner, the broker's id inherited), and a
        lane tool in its own scope."""
        self.tree.process(77103, WatchArgs.COMM, 1, CHIEF, ["/x/bin/omp", WatchArgs.BROKER],
                          {"OMP_DAEMON_RUNTIME_DIR": str(self.tree.runtime),
                           "OMP_DAEMON_PROJECT_DIR": "/home/sami/src/project-a",
                           "OMP_MEMORY_SCOPE": "24G", "OMP_SESSION_ID": CHIEF_SID})
        self.tree.process(82674, "bash", 77103, CHIEF, env={"OMP_SESSION_ID": CHIEF_SID})
        self.tree.process(4220, "bash", 77103, CHIEF)
        self.tree.process(4221, "sleep", 4220, CHIEF)
        self.tree.process(4222, "python3", 4220, TOOL)
        self.tree.process(4300, "bash", 77103, CHIEF, env={"OMP_SESSION_ID": LANE})
        self.tree.process(229151, "chrome", 77103, CHIEF, env={"OMP_SESSION_ID": CHIEF_SID})
        self.tree.process(900, "bash", 1, CHIEF)  # not the broker's: stays
        self.tree.daemon("chief-due", 82674, CHIEF_SID)
        self.tree.daemon("mach-reconcile", 4220, LANE, state="running")
        self.tree.daemon("omp.browser.headless", 229151, None)
        self.tree.daemon("old-loop", 4220, LANE, state="failed")

    def test_the_broker_and_each_lanes_service_get_their_own_scopes(self) -> None:
        self.chief_shape()

        note = self.run_pass()

        broker_scope = f"{APP}/{self.broker_unit}"
        self.assertEqual(self.cgroup(77103), broker_scope)
        self.assertEqual(self.cgroup(229151), broker_scope)  # serves no session: the broker's
        self.assertEqual(self.cgroup(82674), f"{APP}/run-c01a13dcae9797264-svc.scope")
        self.assertEqual(self.cgroup(4220), f"{APP}/run-c01a165533e047776-svc.scope")
        self.assertEqual(self.cgroup(4221), f"{APP}/run-c01a165533e047776-svc.scope")
        self.assertEqual(self.cgroup(4300), f"{APP}/run-c01a165533e047776-svc.scope")  # no record read
        self.assertEqual(self.cgroup(4222), TOOL)  # a scope of its own is left
        self.assertEqual(self.cgroup(900), CHIEF)  # never a process outside the broker's tree
        self.assertEqual(self.started(), {
            self.broker_unit: "omp worker broker for /home/sami/src/project-a",
            "run-c01a13dcae9797264-svc.scope": f"omp session {CHIEF_SID}",
            "run-c01a165533e047776-svc.scope": f"omp session {LANE}",
        })
        start = next(c for c in self.calls if "StartTransientUnit" in c)
        self.assertEqual(start[-2], "77103")  # the broker's scope is made with the broker
        for want in ("Delegate b true", "OOMPolicy s continue", f"MemoryMax t {24 << 30}", "MemorySwapMax t 0"):
            self.assertIn(want, " ".join(start))
        self.assertIn("moved 6 process(es)", note)

    def test_a_second_run_moves_only_a_new_service(self) -> None:
        self.chief_shape()
        self.run_pass()
        self.calls.clear()
        self.tree.process(5000, "bash", 77103, f"{APP}/{self.broker_unit}")
        self.tree.daemon("new-loop", 5000, LANE)

        self.run_pass()

        self.assertEqual(self.started(), {})  # the owner's scope exists: attached, not made again
        self.assertEqual([c[-1] for c in self.calls if "AttachProcessesToUnit" in c], ["5000"])
        self.assertEqual(self.cgroup(5000), f"{APP}/run-c01a165533e047776-svc.scope")

    def test_a_subagents_service_goes_to_its_top_level_session(self) -> None:
        """The broker's record names the subagent that asked (01a19db5, a task subagent of
        01a14126, 2026-11-03); the service's environment names the session a notice reaches."""
        self.chief_shape()
        subagent = "01a19db5-3eab-73fa-a236-4153a387989d"
        self.tree.process(4400, "bash", 77103, CHIEF, env={"OMP_SESSION_ID": CHIEF_SID})
        self.tree.daemon("chief-subagent-loop", 4400, subagent)
        self.tree.process(4401, "bash", 77103, CHIEF, env={"OMP_SESSION_ID": LANE})
        self.tree.daemon("lane-subagent-loop", 4401, subagent)

        self.run_pass()

        self.assertEqual(self.cgroup(4400), f"{APP}/run-c01a13dcae9797264-svc.scope")
        self.assertEqual(self.cgroup(4401), f"{APP}/run-c01a165533e047776-svc.scope")

    def test_a_scope_made_without_delegate_keeps_its_processes_and_new_ones_go_to_a_successor(self) -> None:
        """The first live version of this pass (2026-11-03 22:16-22:21Z) made its scopes without
        Delegate, so systemd refused every later attach to them, 75-125 a run."""
        self.chief_shape()
        lane = "run-c01a165533e047776-svc.scope"
        self.old_scope(lane)
        self.old_scope(self.broker_unit)
        self.tree.move(77103, f"{APP}/{self.broker_unit}")
        self.tree.move(4220, f"{APP}/{lane}")
        self.tree.move(229151, f"{APP}/{self.broker_unit}")
        # What that version's runs left in the state file: where the broker came from.
        self.state.data["brokers"] = {"77103": {"start": WATCH.pid_start(77103), "from": [CHIEF], "daemons": {}}}

        note = self.run_pass()

        self.assertEqual(self.cgroup(77103), f"{APP}/{self.broker_unit}")  # not moved again
        self.assertEqual(self.cgroup(229151), f"{APP}/{self.broker_unit}")
        self.assertEqual(self.cgroup(4220), f"{APP}/{lane}")
        self.assertEqual(self.cgroup(4221), f"{APP}/run-c01a165533e047776-svc-2.scope")
        self.assertEqual(self.cgroup(4300), f"{APP}/run-c01a165533e047776-svc-2.scope")
        self.assertEqual(self.started()["run-c01a165533e047776-svc-2.scope"], f"omp session {LANE}")
        self.assertNotIn("failed", note)

        self.calls.clear()
        self.assertIn("moved 0 process(es)", self.run_pass())  # settled: nothing to do, no retries

    def test_a_young_child_with_no_record_waits_and_an_old_one_stays_with_the_broker(self) -> None:
        self.chief_shape()
        self.tree.process(6000, "bash", 77103, CHIEF, age=5.0)
        self.tree.process(6001, "lsp-mux", 77103, CHIEF)

        self.run_pass()

        self.assertEqual(self.cgroup(6000), CHIEF)  # its record may still be coming
        self.assertEqual(self.cgroup(6001), f"{APP}/{self.broker_unit}")
        self.tree.daemon("late", 6000, LANE)
        self.run_pass()
        self.assertEqual(self.cgroup(6000), f"{APP}/run-c01a165533e047776-svc.scope")

    def test_a_dry_run_moves_nothing(self) -> None:
        self.chief_shape()

        note = self.run_pass(dry_run=True)

        self.assertEqual(self.calls, [])
        self.assertEqual(self.cgroup(77103), CHIEF)
        self.assertIn("would move 6 process(es)", note)

    def test_a_broker_in_an_agent_box_and_a_session_omp_are_left(self) -> None:
        self.tree.process(700, WatchArgs.COMM, 1, BOX, ["/x/bin/omp", WatchArgs.BROKER],
                          {"OMP_DAEMON_RUNTIME_DIR": str(self.tree.runtime)})
        self.tree.process(701, "omp", 1, CHIEF, ["/x/bin/omp", "--resume", CHIEF_SID])

        note = self.run_pass()

        self.assertEqual(self.calls, [])
        self.assertIn("brokers: 0", note)

    def test_a_sandboxs_own_broker_stays_in_the_sandboxs_scope(self) -> None:
        """A grading jail runs omp with its own HOME, so its broker's runtime dir is outside
        ~/.omp, and its scope is the jail's limit (run-u109009.scope, 2026-11-03)."""
        jail = f"{APP}/run-u109009.scope"
        self.tree.process(710, WatchArgs.COMM, 1, jail, ["/x/bin/omp", WatchArgs.BROKER],
                          {"OMP_DAEMON_RUNTIME_DIR": "/mnt/nvme/sami/tmp/ws/h-1jT5Jm/.omp/run/daemons/4fca"})

        note = self.run_pass()

        self.assertEqual(self.calls, [])
        self.assertEqual(self.cgroup(710), jail)
        self.assertIn("brokers: 0", note)

    def test_the_adoption_pass_never_takes_a_broker_for_a_session(self) -> None:
        login = f"{USER}/session-c5.scope"
        self.tree.process(800, "omp", 1, login, ["/x/bin/omp", WatchArgs.BROKER],
                          {"OMP_DAEMON_RUNTIME_DIR": str(self.tree.runtime)})

        WATCH.scope_adopt_pass([(800, login)], self.state, argparse.Namespace(dry_run=False), 0.0)

        self.assertEqual([c for c in self.calls if "StartTransientUnit" in c], [])


class WatchArgs:
    BROKER = "__omp_worker_daemon_broker"
    COMM = "omp daemon brok"  # the broker renames itself; /proc/77103/comm on 2026-11-03


if __name__ == "__main__":
    unittest.main()
