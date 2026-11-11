#!/usr/bin/env python3
"""Tests for earlyoom-kill-watch, from the host's real earlyoom journal of 2026-11-02 15:08-15:21Z.

The fixture `earlyoom-2026-11-02.jsonl` holds those lines as `journalctl -o json` gave them
(MESSAGE, __REALTIME_TIMESTAMP, __CURSOR only). Each test builds a fake /proc with the process
tree the watch would have sampled before the kill, removes the killed process, and feeds the
journal lines.
"""

import importlib.machinery
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "earlyoom-kill-watch"
LOADER = importlib.machinery.SourceFileLoader("earlyoom_kill_watch", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
WATCH = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(WATCH)

FIXTURE = [json.loads(line) for line in (HERE / "earlyoom-2026-11-02.jsonl").read_text().splitlines() if line]

KILLED_SESSION = "01a199e0-ca54-76a0-9f96-960779b2cf44"
HOST_SESSION = "01a19111-2222-7333-8444-555555555555"
RUNNER_SESSION = "01a17777-2222-7333-8444-666666666666"
SRE = "01a18385-f8c8-74a9-b6ff-694fb5fe87a7"
BOX_SCOPE = (
    "/user.slice/user-1000.slice/user-1000-agentbox.slice/"
    "docker-0ddb669c048c21d0c544893d29500b13e944b5c180c47e487e0a48f17643e528.scope/init.scope"
)
HOST_SCOPE = "/user.slice/user-1000.slice/user@1000.service/app.slice/run-u4242.scope"
# 2026-11-03: the chief (moved to another machine) and EFF, whose loky python sat in the chief's old scope.
CHIEF = "01a13dca-e979-7264-af32-f654ef57a6f5"
EFF = "01a14126-ae14-7525-98b7-6b1ac6cefd80"
CHIEF_SCOPE = "/user.slice/user-1000.slice/user@1000.service/app.slice/run-c01a13dcae9797264.scope"
APP = "/user.slice/user-1000.slice/user@1000.service/app.slice"
BROKER_SCOPE = f"{APP}/omp-broker-3f2a9c0d1e2b4a59.scope"
LOKY = ("/mnt/nvme/sami/tmp/TwoPartStage1-01a14126/wt/tasks/.venv/bin/python -m loky.backend.popen_loky_posix"
        " --process-name LokyProcess-2 --pipe 20")
PAGE = os.sysconf("SC_PAGE_SIZE")


def at(hms: str) -> float:
    """Epoch seconds of 2026-11-02T<hms>Z."""
    h, m, s = (int(x) for x in hms.split(":"))
    return 1793577600 + h * 3600 + m * 60 + s


def entries(until: str, since: str = "00:00:00") -> list[dict]:
    lo, hi = at(since), at(until)
    return [e for e in FIXTURE if lo <= int(e["__REALTIME_TIMESTAMP"]) / 1e6 < hi + 1]


def kill_entry(t: float, n: int, comm: str = "python3", cmdline: str = "") -> dict:
    """A synthetic journal line of one earlyoom SIGTERM at epoch `t`, in earlyoom's own format."""
    return {
        "MESSAGE": f'sending SIGTERM to process {50000 + n} uid 1000 "{comm}": oom_score 900, VmRSS 900 MiB,'
                   f' cmdline "{cmdline or f"{comm} job{n}.py"}"',
        "__REALTIME_TIMESTAMP": str(int(t * 1e6)),
        "__CURSOR": f"s=synthetic;i={n}",
    }


def report_of(case: unittest.TestCase, text: str) -> str:
    """Every message names its report file last; the file exists and holds the detail."""
    case.assertLessEqual(len(text), 600, text)
    case.assertIn("Full report: ", text)
    path = Path(text.rsplit("Full report: ", 1)[1].strip())
    case.assertTrue(path.is_file(), path)
    return path.read_text()


class FakeProc:
    def __init__(self, root: Path) -> None:
        self.root = root

    def add(self, pid: int, comm: str, ppid: int, argv: list[str], cgroup: str, rss_mib: int = 1,
            start: int = 1000, session_file: str = "", uid_readable: bool = True, env: dict | None = None,
            cwd: str = "") -> None:
        d = self.root / str(pid)
        d.mkdir(parents=True)
        fields = ["S", str(ppid)] + ["0"] * 17 + [str(start), "0", str(rss_mib * 1048576 // PAGE)] + ["0"] * 30
        (d / "stat").write_text(f"{pid} ({comm}) " + " ".join(fields) + "\n")
        (d / "cmdline").write_bytes("\0".join(argv).encode() + b"\0")
        (d / "cgroup").write_text(f"0::{cgroup}\n")
        if uid_readable:
            (d / "fd").mkdir()
            if session_file:
                os.symlink(session_file, d / "fd" / "7")
            if env:
                (d / "environ").write_bytes(b"".join(f"{k}={v}".encode() + b"\0" for k, v in env.items()))
            if cwd:
                os.symlink(cwd, d / "cwd")

    def kill(self, pid: int) -> None:
        d = self.root / str(pid)
        for sub in sorted(d.rglob("*"), reverse=True):
            sub.unlink() if not sub.is_dir() or sub.is_symlink() else sub.rmdir()
        d.rmdir()


class KillWatchTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.proc = FakeProc(Path(tmp.name) / "proc")
        self.proc.root.mkdir()
        self.state = Path(tmp.name) / "state.json"
        self.reports = Path(tmp.name) / "reports"
        self.sent: list[tuple[str, str]] = []
        self.docker_calls: list[str] = []
        self.descriptions: dict[str, str] = {}
        self.live: dict[str, str] = {}

    def watch(self, to: str = "", started: float = 0.0):
        def send(target, text):
            self.sent.append((target, text))
            return True

        def docker_session(container):
            self.docker_calls.append(container)
            return ""

        w = WATCH.Watch(
            proc_root=self.proc.root, state_path=self.state, send=send, to=to,
            host="sami-rocqperf", docker_session=docker_session, started=started,
            describe=lambda unit: self.descriptions.get(unit, ""), live=lambda: self.live,
        )
        w.reports_dir = self.reports
        return w

    def test_scope_with_no_omp_left_is_matched_by_the_scope_description(self) -> None:
        """Step 4: a scope no omp runs in now, described by shims/omp's command (seen 2026-11-02:
        run-r602e682e91424bbf894544c84a240159.scope, 15.7G, no omp), reaches its session while that
        session is live on this host."""
        self.descriptions["run-u4242.scope"] = (
            f"/home/sami/.dotfiles/scripts/agent-session omp --resume {HOST_SESSION}")
        self.live[HOST_SESSION] = "sami-rocqperf"
        self.proc.add(3405892, "python3", 1, ["python3", "enum_dbg.py"], HOST_SCOPE, rss_mib=9000)
        w = self.watch()
        w.sample(at("15:09:05"))
        self.proc.kill(3405892)
        for e in entries("15:09:09", "15:09:09"):
            w.on_entry(e, at("15:09:10"))
        w.flush(at("15:10:10"))
        self.assertEqual([t for t, _ in self.sent], [HOST_SESSION])
        self.assertIn("its systemd scope run-u4242.scope", report_of(self, self.sent[0][1]))

    # -- a scope that outlived its session (2026-11-03) -----------------------------------------

    def left_behind(self, pid: int, cmdline: str, scope: str = CHIEF_SCOPE, **extra) -> None:
        """A process left in a session's scope, reparented to init, as the 20:28Z loky python was."""
        argv = cmdline.split()
        self.proc.add(pid, argv[0].rsplit("/", 1)[-1][:15], 1, argv, scope, rss_mib=1900, **extra)

    def kill_one(self, w, pid: int, cmdline: str, comm: str = "") -> None:
        """earlyoom kills `pid` at 20:28:22Z; the notice goes out a minute later."""
        if (self.proc.root / str(pid)).exists():
            w.sample(at("20:28:20"))
            self.proc.kill(pid)
        name = comm or cmdline.split()[0].rsplit("/", 1)[-1][:15]
        w.on_entry(kill_entry(at("20:28:22"), pid - 50000, name, cmdline), at("20:28:23"))
        w.flush(at("20:29:30"))

    def test_chief_case_path_names_the_live_session_not_the_moved_scope_owner(self) -> None:
        """2026-11-03T20:28:22Z: session 01a14126's loky python, left in the chief's old scope
        run-c01a13dcae9797264.scope after the chief moved to another machine, reached the chief.
        Its command line names /mnt/nvme/sami/tmp/TwoPartStage1-01a14126/, and 01a14126 is live here."""
        self.descriptions["run-c01a13dcae9797264.scope"] = (
            f"omp session {CHIEF} (moved from run-r72ce86c44ff741318560790740180240.scope for OOMPolicy=continue)")
        self.live.update({EFF: "sami-rocqperf", HOST_SESSION: "sami-rocqperf"})
        self.left_behind(50001, LOKY)
        w = self.watch()
        self.kill_one(w, 50001, LOKY)
        self.assertEqual([t for t, _ in self.sent], [EFF])
        self.assertIn("a session's directory in its command line", report_of(self, self.sent[0][1]))

    def test_chief_case_never_sampled_still_reaches_the_session_its_path_names(self) -> None:
        self.live[EFF] = "sami-rocqperf"
        w = self.watch()
        self.kill_one(w, 50001, LOKY, comm="python")
        self.assertEqual([t for t, _ in self.sent], [EFF])

    def test_moved_scope_owner_is_never_told_and_the_box_health_owner_hears_owner_unknown(self) -> None:
        """Step 4 refused: the chief's scope, the chief not live here, nothing else names an owner."""
        self.descriptions["run-c01a13dcae9797264.scope"] = f"omp session {CHIEF} (moved from x for OOMPolicy=continue)"
        self.live[HOST_SESSION] = "sami-rocqperf"
        cmd = "/usr/bin/python3 -m http.server 8080"
        self.left_behind(50002, cmd)
        w = self.watch()
        self.kill_one(w, 50002, cmd, comm="python3")
        self.assertEqual([t for t, _ in self.sent], [WATCH.BOX_HEALTH_ROLE_TOPIC])
        text = self.sent[0][1]
        self.assertIn("OWNER UNKNOWN", text)
        self.assertIn(cmd, text, "the notice names the command line")
        self.assertNotIn(CHIEF, text)
        report = report_of(self, text)
        self.assertIn(f"session {CHIEF} has moved off this host or ended", report)
        self.assertIn(cmd, report)
        self.assertIn("unmatched_this_hour=1", w.counts_line(at("20:29:30")))

    def test_owner_unknown_goes_to_the_sre_session_when_nobody_holds_the_role(self) -> None:
        cmd = "/usr/bin/python3 -m http.server 8080"
        self.left_behind(50002, cmd)
        w = self.watch()
        w.send = lambda target, text: self.sent.append((target, text)) or target != WATCH.BOX_HEALTH_ROLE_TOPIC
        self.kill_one(w, 50002, cmd, comm="python3")
        self.assertEqual([t for t, _ in self.sent], [WATCH.BOX_HEALTH_ROLE_TOPIC, SRE])
        self.assertEqual(w.pending, [])

    def test_run_c_scope_with_no_description_is_its_session_only_while_live(self) -> None:
        """Step 4 from the unit name alone: run-c<first 16 hex of the session id>.scope."""
        cmd = "/usr/bin/python3 train.py"
        self.left_behind(50003, cmd)
        self.live[CHIEF] = "agentbox-0123456789ab"
        w = self.watch()
        self.kill_one(w, 50003, cmd, comm="python3")
        self.assertEqual([t for t, _ in self.sent], [CHIEF], "a session in a box on this host is live here")
        self.assertIn("named for this session", report_of(self, self.sent[0][1]))

    def test_environment_names_the_owner_inside_a_shared_scope(self) -> None:
        """Step 2: omp's shared broker runs every session's tools in the scope of the session that
        started it; each tool's OMP_SESSION_ID still names its own session."""
        self.live.update({EFF: "sami-rocqperf", CHIEF: "sami-rocqperf"})
        self.descriptions["run-c01a13dcae9797264.scope"] = f"omp session {CHIEF}"
        cmd = "python3 qa.py"
        self.left_behind(50004, cmd, env={"OMP_SESSION_ID": EFF, "AGENT_SESSION_SCRATCH": "/mnt/nvme/sami/session-tmp/56969-GXaSuFiC"})
        w = self.watch()
        self.kill_one(w, 50004, cmd)
        self.assertEqual([t for t, _ in self.sent], [EFF], "the environment comes before the scope's original session")
        self.assertIn("OMP_SESSION_ID", report_of(self, self.sent[0][1]))

    def test_environment_scratch_names_the_omp(self) -> None:
        self.proc.add(727012, "omp", 1, ["omp"], HOST_SCOPE,
                      session_file=f"/home/sami/.omp/agent/sessions/-x/2026-11-01T00-00-00-000Z_{RUNNER_SESSION}.jsonl")
        cmd = "python3 job.py"
        self.left_behind(50005, cmd, scope=f"{APP}/run-u777.scope",
                         env={"AGENT_SESSION_SCRATCH": "/mnt/nvme/sami/session-tmp/727012-abcdEFGH"})
        w = self.watch()
        self.kill_one(w, 50005, cmd)
        self.assertEqual([t for t, _ in self.sent], [RUNNER_SESSION])
        self.assertIn("AGENT_SESSION_SCRATCH", report_of(self, self.sent[0][1]))

    def test_environment_naming_a_moved_session_is_owner_unknown(self) -> None:
        cmd = "python3 qa.py"
        self.left_behind(50004, cmd, env={"OMP_SESSION_ID": CHIEF})
        w = self.watch()
        self.kill_one(w, 50004, cmd)
        self.assertEqual([t for t, _ in self.sent], [WATCH.BOX_HEALTH_ROLE_TOPIC])
        self.assertIn(f"its environment names session {CHIEF}, not live on this host", report_of(self, self.sent[0][1]))

    def test_other_processes_of_the_cgroup_name_the_owner(self) -> None:
        """Step 2: the killed process's environment is unreadable; the rest of its cgroup agrees."""
        self.live[EFF] = "sami-rocqperf"
        scope = "/user.slice/user-1000.slice/user@1000.service/app.slice/run-u777.scope"
        self.proc.add(60001, "bash", 1, ["bash", "loop.sh"], scope, env={"OMP_SESSION_ID": EFF})
        cmd = "python3 big.py"
        self.left_behind(50006, cmd, scope=scope, uid_readable=False)
        w = self.watch()
        self.kill_one(w, 50006, cmd)
        self.assertEqual([t for t, _ in self.sent], [EFF])
        self.assertIn("other processes of its cgroup", report_of(self, self.sent[0][1]))

    def test_svc_scope_description_names_the_owner_while_live(self) -> None:
        """Step 3: a run-c<owner>-svc.scope described `omp session <uuid>` names its owner."""
        unit = "run-c01a14126ae147525-svc.scope"
        self.descriptions[unit] = f"omp session {EFF} services"
        cmd = "node server.js"
        self.left_behind(50007, cmd, scope=f"/user.slice/user-1000.slice/user@1000.service/app.slice/{unit}")
        self.live[EFF] = "sami-rocqperf"
        w = self.watch()
        self.kill_one(w, 50007, cmd)
        self.assertEqual([t for t, _ in self.sent], [EFF])
        self.assertIn(f"its systemd scope {unit}, whose Description names the session", report_of(self, self.sent[0][1]))
        self.live.clear()
        self.sent.clear()
        self.left_behind(50008, cmd, scope=f"/user.slice/user-1000.slice/user@1000.service/app.slice/{unit}")
        w = self.watch()
        self.kill_one(w, 50008, cmd)
        self.assertEqual([t for t, _ in self.sent], [WATCH.BOX_HEALTH_ROLE_TOPIC], "an ended -svc owner is not told")

    def test_broker_scope_kill_with_no_owner_goes_to_the_box_health_owner(self) -> None:
        cmd = "python3 orphan.py"
        self.left_behind(50009, cmd, scope=BROKER_SCOPE)
        w = self.watch()
        self.kill_one(w, 50009, cmd)
        self.assertEqual([t for t, _ in self.sent], [WATCH.BOX_HEALTH_ROLE_TOPIC])
        self.assertIn("omp broker scope", report_of(self, self.sent[0][1]))

    def broker(self, started_by: str = CHIEF) -> None:
        """omp's shared broker in its own scope; its later processes go to omp-broker-<hex>-2.scope."""
        self.proc.add(77103, "omp daemon brok", 1, ["/x/bin/omp", "__omp_worker_daemon_broker"], BROKER_SCOPE,
                      env={"OMP_SESSION_ID": started_by, "AGENT_SESSION_SCRATCH": "/mnt/nvme/sami/session-tmp/47314-K7KkdNKX"})

    def test_broker_scope_process_with_the_brokers_own_environment_is_owner_unknown(self) -> None:
        """A headless browser the broker runs for nobody carries the broker's OMP_SESSION_ID, the
        session that started the broker: not an owner, even while that session is live here."""
        self.broker()
        self.live[CHIEF] = "sami-rocqperf"
        cmd = "/home/sami/.omp/puppeteer/chrome/linux-150/chrome --headless"
        self.left_behind(50011, cmd, scope=f"{APP}/omp-broker-3f2a9c0d1e2b4a59-2.scope",
                         env={"OMP_SESSION_ID": CHIEF, "AGENT_SESSION_SCRATCH": "/mnt/nvme/sami/session-tmp/47314-K7KkdNKX"})
        w = self.watch()
        self.kill_one(w, 50011, cmd, comm="chrome")
        self.assertEqual([t for t, _ in self.sent], [WATCH.BOX_HEALTH_ROLE_TOPIC])
        report = report_of(self, self.sent[0][1])
        self.assertIn("omp broker scope", report)
        self.assertIn(f"its environment names {CHIEF}, which is the broker's own", report)

    def test_broker_scope_tool_run_for_another_session_reaches_that_session(self) -> None:
        self.broker()
        self.live.update({CHIEF: "sami-rocqperf", EFF: "sami-rocqperf"})
        cmd = "python3 qa.py"
        self.left_behind(50012, cmd, scope=f"{APP}/omp-broker-3f2a9c0d1e2b4a59-3.scope", env={"OMP_SESSION_ID": EFF})
        w = self.watch()
        self.kill_one(w, 50012, cmd)
        self.assertEqual([t for t, _ in self.sent], [EFF])
        self.assertIn("OMP_SESSION_ID", report_of(self, self.sent[0][1]))

    def test_broker_scope_with_no_broker_left_trusts_no_environment(self) -> None:
        self.live[EFF] = "sami-rocqperf"
        cmd = "python3 qa.py"
        self.left_behind(50012, cmd, scope=BROKER_SCOPE, env={"OMP_SESSION_ID": EFF})
        w = self.watch()
        self.kill_one(w, 50012, cmd)
        self.assertEqual([t for t, _ in self.sent], [WATCH.BOX_HEALTH_ROLE_TOPIC])
        self.assertIn("no broker runs in the scope", report_of(self, self.sent[0][1]))

    def test_broker_scope_path_still_names_the_owner(self) -> None:
        """Step 2's paths come before owner unknown, in a broker scope too."""
        self.broker()
        self.live[EFF] = "sami-rocqperf"
        self.left_behind(50001, LOKY, scope=f"{APP}/omp-broker-3f2a9c0d1e2b4a59-2.scope", env={"OMP_SESSION_ID": CHIEF})
        w = self.watch()
        self.kill_one(w, 50001, LOKY)
        self.assertEqual([t for t, _ in self.sent], [EFF])

    def test_numbered_svc_scope_description_names_the_owner(self) -> None:
        """A numbered run-c<owner>-svc-2.scope still names the session in its Description."""
        unit = "run-c01a14126ae147525-svc-2.scope"
        self.descriptions[unit] = f"omp session {EFF}"
        self.live[EFF] = "sami-rocqperf"
        cmd = "node server.js"
        self.left_behind(50013, cmd, scope=f"{APP}/{unit}")
        w = self.watch()
        self.kill_one(w, 50013, cmd)
        self.assertEqual([t for t, _ in self.sent], [EFF])
        self.assertIn(f"its systemd scope {unit}, whose Description names the session", report_of(self, self.sent[0][1]))

    def test_scope_live_omp_comes_before_paths(self) -> None:
        """Step 1: the one live session omp of the scope wins over a path naming another session."""
        self.proc.add(4000000, "omp", 1, ["omp"], HOST_SCOPE,
                      session_file=f"/home/sami/.omp/agent/sessions/-src/2026-11-02T10-00-00-000Z_{HOST_SESSION}.jsonl")
        self.live.update({EFF: "sami-rocqperf", HOST_SESSION: "sami-rocqperf"})
        self.left_behind(50001, LOKY, scope=HOST_SCOPE)
        w = self.watch()
        self.kill_one(w, 50001, LOKY)
        self.assertEqual([t for t, _ in self.sent], [HOST_SESSION])
        self.assertIn("same systemd scope", report_of(self, self.sent[0][1]))

    def test_nearest_live_ancestor_omp_when_the_nearer_one_ended(self) -> None:
        """Step 2: two omps in one scope (no single scope owner); the inner one ended before the kill."""
        outer_file = f"/home/sami/.omp/agent/sessions/-src/2026-11-02T10-00-00-000Z_{HOST_SESSION}.jsonl"
        inner_file = f"/home/sami/.omp/agent/sessions/-src/2026-11-02T11-00-00-000Z_{RUNNER_SESSION}.jsonl"
        self.proc.add(4000000, "omp", 1, ["omp"], HOST_SCOPE, session_file=outer_file)
        self.proc.add(4000100, "bash", 4000000, ["bash"], HOST_SCOPE)
        self.proc.add(4000200, "omp", 4000100, ["omp"], HOST_SCOPE, session_file=inner_file)
        cmd = "python3 deep.py"
        self.proc.add(50010, "python3", 4000200, cmd.split(), HOST_SCOPE, rss_mib=1900)
        w = self.watch()
        w.sample(at("20:28:20"))
        self.proc.kill(4000200)
        self.proc.kill(50010)
        w.on_entry(kill_entry(at("20:28:22"), 10, "python3", cmd), at("20:28:23"))
        w.flush(at("20:29:30"))
        self.assertEqual([t for t, _ in self.sent], [HOST_SESSION])
        self.assertIn("process tree (omp pid 4000000)", report_of(self, self.sent[0][1]))

    # -- a lane resumed from a subagent's transcript (2026-11-03 23:07Z) -----------------------

    def test_resume_id_is_the_transcript_files_own_name_not_its_parent(self) -> None:
        parent = "01a15237-75c3-7321-befb-e6e1cc57a948"
        child = "01a17dd1-0000-7000-8000-000000000001"
        nested = (f"/home/sami/.omp/agent/sessions/-src/2026-10-19T03-32-08-771Z_{parent}/"
                  f"2026-10-25T02-25-48-934Z_{child}.jsonl")
        self.assertEqual(WATCH.resume_session(["omp", "--resume", nested]), child)
        self.assertEqual(WATCH.resume_session(["omp", f"--resume={nested}"]), child)
        self.assertEqual(WATCH.resume_session(["omp", "--resume", child, "."]), child)
        self.assertEqual(WATCH.resume_session(["omp", "-p", f"see dispatch://X/ask/{parent}"]), "")
        self.assertEqual(WATCH.resume_session(["omp", "--resume", f"/x/2026Z_{parent}/notes.jsonl"]), "")

    def test_lane_resumed_from_a_subagent_transcript_is_its_own_session(self) -> None:
        """The lane's omp holds no transcript the host can read; its --resume path names the
        parent's directory first. The kill goes to the lane, not the parent, both live here."""
        parent = "01a15237-75c3-7321-befb-e6e1cc57a948"
        child = "01a17dd1-0000-7000-8000-000000000001"
        nested = (f"/home/sami/.omp/agent/sessions/-src/2026-10-19T03-32-08-771Z_{parent}/"
                  f"2026-10-25T02-25-48-934Z_{child}.jsonl")
        self.live.update({parent: "sami-rocqperf", child: "sami-rocqperf"})
        scope = f"{APP}/run-r107194aa0000000000000000000000.scope"
        self.proc.add(4100000, "omp", 1, ["/x/bin/omp", "--resume", nested], scope)
        self.proc.add(50020, "python3", 4100000, ["python3", "lane.py"], scope, rss_mib=1900)
        w = self.watch()
        self.kill_one(w, 50020, "python3 lane.py")
        self.assertEqual([t for t, _ in self.sent], [child])

    def test_left_scope_description_with_a_nested_resume_path_names_the_lane(self) -> None:
        parent = "01a15237-75c3-7321-befb-e6e1cc57a948"
        child = "01a17dd1-0000-7000-8000-000000000001"
        unit = "run-r107194aa0000000000000000000000.scope"
        self.descriptions[unit] = (f"/home/sami/.dotfiles/scripts/agent-session omp --resume /home/sami/.omp/agent/"
                                   f"sessions/-src/2026-10-19T03-32-08-771Z_{parent}/2026-10-25T02-25-48-934Z_{child}.jsonl")
        self.live.update({parent: "sami-rocqperf", child: "sami-rocqperf"})
        self.left_behind(50021, "python3 lane.py", scope=f"{APP}/{unit}")
        w = self.watch()
        self.kill_one(w, 50021, "python3 lane.py")
        self.assertEqual([t for t, _ in self.sent], [child])

    def test_a_transcript_directory_in_a_tool_path_names_no_owner(self) -> None:
        parent = "01a15237-75c3-7321-befb-e6e1cc57a948"
        self.live[parent] = "sami-rocqperf"
        cmd = f"python3 scan.py /home/sami/.omp/agent/sessions/-src/2026-10-19T03-32-08-771Z_{parent}/x.jsonl"
        w = self.watch()
        self.kill_one(w, 50022, cmd, comm="python3")
        self.assertEqual(self.sent, [], "a transcript's directory names a parent, not an owner")

    def test_path_prefix_shared_by_two_live_sessions_names_neither(self) -> None:
        twin = "01a14126-0000-7000-8000-000000000000"
        self.live.update({EFF: "sami-rocqperf", twin: "sami-rocqperf"})
        w = self.watch()
        self.kill_one(w, 50001, LOKY, comm="python")
        self.assertEqual(self.sent, [], "never sampled, no scope: unmatched, nobody told")

    def test_a_record_saved_before_this_rule_is_sampled_again(self) -> None:
        """A state file of the old format held one owner per record: the chief's scope description."""
        self.descriptions["run-c01a13dcae9797264.scope"] = f"omp session {CHIEF}"
        self.live[EFF] = "sami-rocqperf"
        self.left_behind(50001, LOKY)
        self.state.write_text(json.dumps({"table": {"50001:1000": {
            "pid": 50001, "start": 1000, "comm": "python", "first_seen": 0, "last_seen": at("20:28:00"),
            "rss_max": 1900, "omp": 0, "omp_start": 0, "container": "", "how": "", "session": "",
            "scope": "run-c01a13dcae9797264.scope"}}}))
        w = self.watch()
        self.assertEqual(w.table, {})
        self.kill_one(w, 50001, LOKY)
        self.assertEqual([t for t, _ in self.sent], [EFF])

    # -- the tree each kill came from ----------------------------------------------------------

    def killed_session_box(self) -> None:
        """The killed session's box: containerd-shim > docker-init > bun (agentbox-run.ts) > omp > bash."""
        self.proc.add(3676141, "containerd-shim", 1, ["containerd-shim-runc-v2"], "/system.slice/containerd.service")
        self.proc.add(3676577, "docker-init", 3676141, ["/sbin/docker-init", "--"], BOX_SCOPE, uid_readable=False)
        self.proc.add(3681848, "bun", 3676577, ["bun", "agentbox-run.ts"], BOX_SCOPE, uid_readable=False)
        self.proc.add(3693878, "omp", 3681848, ["/home/sami/.mise/installs/x/bin/omp", "--resume", KILLED_SESSION],
                      BOX_SCOPE, rss_mib=900, uid_readable=False)
        self.proc.add(3800900, "bash", 3693878, ["/bin/bash", "-c", "simsci baseline"], BOX_SCOPE, uid_readable=False)

    def simsci(self, pid: int, start: int) -> None:
        self.proc.add(pid, "simsci", 3800900, ["python3", "simsci", "baseline"], BOX_SCOPE, rss_mib=5900,
                      start=start, uid_readable=False)

    def host_session(self) -> None:
        """A host session started by shims/omp in its own scope, holding its transcript open."""
        self.proc.add(4000000, "omp", 1, ["/home/sami/.mise/installs/x/bin/omp", "--resume", "01a10000-0000-7000-8000-000000000000"],
                      HOST_SCOPE, rss_mib=2000,
                      session_file=f"/home/sami/.omp/agent/sessions/-src/2026-11-02T10-00-00-000Z_{HOST_SESSION}.jsonl")
        self.proc.add(4000100, "bash", 4000000, ["/bin/bash"], HOST_SCOPE)
        self.proc.add(3405892, "python3", 4000100, ["python3", "enum_dbg.py"], HOST_SCOPE, rss_mib=9000)

    # -- tests ----------------------------------------------------------------------------------

    def test_parse_real_kill_line(self) -> None:
        kill = WATCH.parse_kill(entries("15:09:09", "15:09:09")[-1]["MESSAGE"])
        self.assertEqual(kill["pid"], 3405892)
        self.assertEqual(kill["uid"], 1000)
        self.assertEqual(kill["comm"], "python3")
        self.assertEqual(kill["rss_mib"], 9648)
        self.assertEqual(kill["cmdline"], "python3 enum_dbg.py")
        self.assertIsNone(WATCH.parse_kill("sending SIGTERM when mem avail <= 10.00% and swap free <= 100.00%,"))
        self.assertEqual(WATCH.parse_avail("mem avail: 19197 of 205180 MiB ( 9.36%), swap free: 1 of 2 MiB (5.00%)"),
                         (19197, 205180, 9.36))

    def test_host_kill_reaches_the_session_that_ran_it(self) -> None:
        """15:09:09Z: python3 enum_dbg.py, uid 1000, 9648 MiB."""
        self.host_session()
        w = self.watch()
        w.sample(at("15:09:05"))
        self.proc.kill(3405892)
        for e in entries("15:09:09", "15:09:09"):
            w.on_entry(e, at("15:09:10"))
        w.flush(at("15:09:20"))
        self.assertEqual(self.sent, [], "a batch waits a minute for more kills of the same session")
        w.flush(at("15:10:10"))
        self.assertEqual(len(self.sent), 1)
        target, text = self.sent[0]
        self.assertEqual(target, HOST_SESSION, "the id of the transcript the omp holds, not its --resume id")
        for needle in ("2026-11-02T15:09:09Z", "3405892", '"python3"', "9648 MiB", "143", "running-heavy-jobs"):
            self.assertIn(needle, text)
        report = report_of(self, text)
        for needle in ("2026-11-02T15:09:09Z", "3405892", "9.36%", "19197 of 205180 MiB", "python3 enum_dbg.py",
                       "143", "running-heavy-jobs", "process tree"):
            self.assertIn(needle, report)

    def test_two_box_kills_in_one_minute_are_one_notice_and_the_next_is_another(self) -> None:
        """15:10:59Z and 15:11:50Z: simsci in the killed session's box (uid 166536); 15:20:57Z: one more."""
        self.killed_session_box()
        self.simsci(3458528, 5000)
        w = self.watch()
        w.sample(at("15:10:55"))
        self.proc.kill(3458528)
        for e in entries("15:10:59", "15:10:00"):
            w.on_entry(e, at("15:11:00"))
        self.simsci(3731783, 6000)
        w.sample(at("15:11:45"))
        self.proc.kill(3731783)
        for e in entries("15:11:50", "15:11:01"):
            w.on_entry(e, at("15:11:51"))
        w.flush(at("15:11:58"))
        self.assertEqual(self.sent, [])
        w.flush(at("15:12:00"))
        self.assertEqual(len(self.sent), 1)
        target, text = self.sent[0]
        self.assertEqual(target, KILLED_SESSION)
        self.assertIn("2 of your processes", text)
        for needle in ("2026-11-02T15:10:59Z", "3458528", "5966 MiB",
                       "2026-11-02T15:11:50Z", "3731783", "5936 MiB", '"simsci"'):
            self.assertIn(needle, text)
        report = report_of(self, text)
        for needle in ("9.64%", "9.24%", "simsci baseline"):
            self.assertIn(needle, report)
        self.assertEqual(self.docker_calls, [], "the box omp's --resume argument names the session")

        self.simsci(671743, 7000)
        w.sample(at("15:20:50"))
        self.proc.kill(671743)
        for e in entries("15:20:57", "15:12:00"):
            w.on_entry(e, at("15:20:58"))
        w.flush(at("15:22:00"))
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.sent[1][0], KILLED_SESSION)
        self.assertIn("1 of your processes", self.sent[1][1])
        self.assertIn("5481 MiB", self.sent[1][1])
        self.assertIn("9.91%", report_of(self, self.sent[1][1]))

    def test_a_process_reparented_out_of_the_tree_is_matched_by_its_box(self) -> None:
        self.killed_session_box()
        self.proc.add(3458528, "simsci", 3676577, ["simsci"], BOX_SCOPE, rss_mib=5900, uid_readable=False)
        w = self.watch()
        w.sample(at("15:10:55"))
        self.proc.kill(3458528)
        for e in entries("15:10:59", "15:10:00"):
            w.on_entry(e, at("15:11:00"))
        w.flush(at("15:12:00"))
        self.assertEqual([t for t, _ in self.sent], [KILLED_SESSION])
        self.assertIn("same agent box", report_of(self, self.sent[0][1]))

    def test_session_tmp_path_names_the_omp_when_the_process_was_never_sampled(self) -> None:
        """15:09:07Z: an omp python runner under session-tmp/727012-*, the scratch dir agent-session
        makes as `$$-XXXXXXXX` before it execs omp, so 727012 is the session's omp."""
        self.proc.add(727012, "omp", 1, ["omp"], HOST_SCOPE,
                      session_file=f"/home/sami/.omp/agent/sessions/-x/2026-11-01T00-00-00-000Z_{RUNNER_SESSION}.jsonl")
        w = self.watch()
        for e in entries("15:09:07", "15:09:07"):
            w.on_entry(e, at("15:09:08"))
        w.flush(at("15:10:10"))
        self.assertEqual([t for t, _ in self.sent], [RUNNER_SESSION])
        self.assertIn("session-tmp", report_of(self, self.sent[0][1]))

    def test_unmatched_kill_is_counted_and_sends_nothing(self) -> None:
        """15:09:08Z: an mlflow uvicorn nobody sampled, with no session path in its command line."""
        w = self.watch()
        for e in entries("15:09:08", "15:09:08"):
            w.on_entry(e, at("15:09:09"))
        w.flush(at("15:11:00"))
        self.assertEqual(self.sent, [])
        line = w.counts_line(at("15:11:00"))
        self.assertIn("kills_this_hour=1", line)
        self.assertIn("unmatched_this_hour=1", line)

    def test_hour_count_and_no_repeat_after_restart(self) -> None:
        self.killed_session_box()
        self.host_session()
        w = self.watch()
        w.sample(at("15:09:00"))
        batch = entries("15:12:00", "15:08:00")
        for e in batch:
            w.on_entry(e, at("15:12:00"))
        w.flush(at("15:13:00"))
        w.save(at("15:13:00"))
        self.assertIn("kills_this_hour=5 hour=2026-11-02T15Z", w.counts_line(at("15:13:00")))
        sent = len(self.sent)
        again = self.watch()
        self.assertEqual(again.cursor, batch[-1]["__CURSOR"])
        for e in batch:
            again.on_entry(e, at("15:13:01"))
        again.flush(at("15:15:00"))
        self.assertEqual(len(self.sent), sent, "a line already counted is not counted or sent again")
        self.assertIn("kills_this_hour=5", again.counts_line(at("15:15:00")))
        self.assertIn("kills_this_hour=0 hour=2026-11-02T16Z kills_prev_hour=5",
                      again.counts_line(at("16:00:05")))

    def test_drill_route_sends_every_notice_to_one_session(self) -> None:
        self.killed_session_box()
        self.simsci(3458528, 5000)
        w = self.watch(to=SRE)
        w.sample(at("15:10:55"))
        self.proc.kill(3458528)
        for e in entries("15:10:59", "15:10:00"):
            w.on_entry(e, at("15:11:00"))
        w.flush(at("15:12:00"))
        self.assertEqual([t for t, _ in self.sent], [SRE])
        self.assertIn(KILLED_SESSION, self.sent[0][1], "the drill copy names the session it would have reached")

    def test_first_start_counts_old_kills_but_notifies_none(self) -> None:
        """With no cursor the watch reads from the hour's start for the count; a kill from before
        it started is not news to send."""
        self.host_session()
        w = self.watch(started=at("15:30:00"))
        w.sample(at("15:09:05"))
        self.proc.kill(3405892)
        for e in entries("15:09:09", "15:09:09"):
            w.on_entry(e, at("15:30:01"))
        w.flush(at("15:32:00"))
        self.assertEqual(self.sent, [])
        self.assertIn("kills_this_hour=1", w.counts_line(at("15:32:00")))

    def test_failed_send_is_retried(self) -> None:
        self.host_session()
        w = self.watch()
        ok = [False]

        def send(target, text):
            self.sent.append((target, text))
            return ok[0]

        w.send = send
        w.sample(at("15:09:05"))
        self.proc.kill(3405892)
        for e in entries("15:09:09", "15:09:09"):
            w.on_entry(e, at("15:09:10"))
        w.flush(at("15:10:10"))
        ok[0] = True
        w.flush(at("15:10:40"))
        w.flush(at("15:11:40"))
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.sent[0], self.sent[1], "a retry sends the same notice, naming the same report")

    # -- the box-level rate ---------------------------------------------------------------------

    def feed(self, w, start: float, count: int, step: float, first: int = 0, comm: str = "python3") -> float:
        """`count` kills `step` seconds apart from `start`, each read a second after it; the last kill's time."""
        for i in range(count):
            t = start + i * step
            w.on_entry(kill_entry(t, first + i, comm), t + 1)
        return start + (count - 1) * step

    def box_messages(self) -> list[tuple[str, str]]:
        return [(t, text) for t, text in self.sent if "EARLYOOM KILL RATE" in text]

    def test_box_rate_over_100_an_hour_tells_the_box_health_role_once(self) -> None:
        """846 kills in 48 h on sami-rocqperf, 730 of them in the 00Z hour of 2026-11-03, and no
        box-level message: the SRE heard only of its own processes."""
        w = self.watch()
        last = self.feed(w, at("00:00:00"), 100, 20.0)
        w.flush(last + 2)
        self.assertEqual(self.box_messages(), [], "100 kills in an hour is at the threshold, not over it")
        last = self.feed(w, last + 20, 1, 1.0, first=100)
        w.flush(last + 2)
        box = self.box_messages()
        self.assertEqual([t for t, _ in box], [WATCH.BOX_HEALTH_ROLE_TOPIC])
        self.assertIn("101 kills in the hour", box[0][1])
        report = report_of(self, box[0][1])
        self.assertIn("python3 101", report)
        self.assertIn("101 kills had no session", report)

        # 150 more in the same hour: the same storm, no new fact.
        last = self.feed(w, last + 2, 150, 2.0, first=101)
        w.flush(last + 2)
        self.assertEqual(len(self.box_messages()), 1)

        # 52 more: 202 since the message, twice the 101 it told. One message.
        last = self.feed(w, last + 2, 52, 2.0, first=251)
        w.flush(last + 2)
        box = self.box_messages()
        self.assertEqual(len(box), 2)
        self.assertEqual(box[1][0], WATCH.BOX_HEALTH_ROLE_TOPIC)
        self.assertIn("doubled", box[1][1])
        self.assertIn("202", box[1][1])
        report_of(self, box[1][1])
        self.assertEqual(len([t for t, _ in self.sent if t != WATCH.BOX_HEALTH_ROLE_TOPIC]), 0,
                         "unmatched kills reach no session")

    def test_box_rate_sends_again_only_after_it_fell_to_the_threshold_and_crossed_again(self) -> None:
        w = self.watch()
        last = self.feed(w, at("00:00:00"), 101, 20.0)
        w.flush(last + 2)
        self.assertEqual(len(self.box_messages()), 1)
        # An hour with no kills: the rolling rate falls to 0. Nothing is sent for that.
        w.box_step(last + 3700)
        w.flush(last + 3700)
        self.assertEqual(len(self.box_messages()), 1)
        self.assertIn("box_rate_1h=0", w.counts_line(last + 3700))
        last = self.feed(w, last + 3800, 101, 10.0, first=101)
        w.flush(last + 2)
        box = self.box_messages()
        self.assertEqual(len(box), 2)
        self.assertNotIn("doubled", box[1][1])
        self.assertIn("101 kills in the hour", box[1][1])

    def test_box_message_goes_to_the_sre_session_only_when_nobody_holds_the_role(self) -> None:
        w = self.watch()

        def send(target, text):
            self.sent.append((target, text))
            return target != WATCH.BOX_HEALTH_ROLE_TOPIC

        w.send = send
        last = self.feed(w, at("00:00:00"), 101, 20.0)
        w.flush(last + 2)
        self.assertEqual([t for t, _ in self.box_messages()], [WATCH.BOX_HEALTH_ROLE_TOPIC, SRE])
        w.flush(last + 60)
        self.assertEqual(len(self.box_messages()), 2, "a message the SRE took is not sent again")

    def test_an_undelivered_crossing_is_told_once_with_the_count_to_now(self) -> None:
        w = self.watch()
        ok = [False]

        def send(target, text):
            self.sent.append((target, text))
            return ok[0]

        w.send = send
        last = self.feed(w, at("00:00:00"), 101, 20.0)
        w.flush(last + 2)
        self.assertEqual(len(self.box_messages()), 2, "the role and the SRE both refused")
        last = self.feed(w, last + 3, 202, 1.0, first=101)
        ok[0] = True
        w.flush(last + 60)
        w.flush(last + 120)
        delivered = self.box_messages()[2:]
        self.assertEqual(len(delivered), 1)
        self.assertIn("303 kills in the hour", delivered[0][1])
        self.assertNotIn("doubled", delivered[0][1])
        self.assertIn("606 more", delivered[0][1], "the next doubling is judged against what was told")

    def test_storm_from_before_the_first_start_is_counted_not_sent(self) -> None:
        start = at("00:40:00")
        w = self.watch(started=start)
        last = self.feed(w, at("00:00:00"), 120, 15.0)
        w.flush(start + 1)
        self.assertEqual(self.box_messages(), [])
        self.assertIn("box_rate_1h=120", w.counts_line(last + 1))

    def test_box_state_survives_a_restart(self) -> None:
        w = self.watch()
        last = self.feed(w, at("00:00:00"), 101, 20.0)
        w.flush(last + 2)
        w.save(last + 2)
        again = self.watch()
        self.feed(again, last + 5, 100, 2.0, first=101)
        again.flush(last + 400)
        self.assertEqual(len(self.box_messages()), 1, "a restart does not make the open storm news again")

    def test_every_message_is_at_most_600_chars_and_names_its_report(self) -> None:
        self.proc.add(727012, "omp", 1, ["omp"], HOST_SCOPE,
                      session_file=f"/home/sami/.omp/agent/sessions/-x/2026-11-01T00-00-00-000Z_{RUNNER_SESSION}.jsonl")
        w = self.watch(to=SRE)
        w.host = "h" * 63
        long_cmd = "python3 /tmp/session-tmp/727012-abcdefgh/" + "x" * 400 + ".py --flag " + "y" * 300
        for i in range(30):
            t = at("00:00:00") + i
            w.on_entry(kill_entry(t, i, comm="averyverylongna", cmdline=long_cmd), t + 1)
        for i in range(120):
            t = at("00:01:00") + i
            w.on_entry(kill_entry(t, 100 + i, comm=f"c{i:02d}-longname-xx"), t + 1)
        w.flush(at("00:10:00"))
        self.assertGreaterEqual(len(self.sent), 2)
        for _target, text in self.sent:
            report_of(self, text)
        notice = [text for _, text in self.sent if "EARLYOOM KILL RATE" not in text][0]
        self.assertIn(RUNNER_SESSION, notice, "the drill copy still names the session it was for")
        self.assertIn("30 of your processes", notice)
        self.assertIn("more", notice)
        self.assertEqual(report_of(self, notice).count("averyverylongna"), 30, "the report lists every kill")

    def test_reports_older_than_seven_days_are_pruned(self) -> None:
        self.host_session()
        self.reports.mkdir(parents=True)
        old, recent = self.reports / "20261020T000000Z-box-rate.txt", self.reports / "20261028T000000Z-box-rate.txt"
        for path, days in ((old, 8), (recent, 6)):
            path.write_text("x")
            os.utime(path, (at("15:00:00") - days * 86400,) * 2)
        w = self.watch()
        w.sample(at("15:09:05"))
        self.proc.kill(3405892)
        for e in entries("15:09:09", "15:09:09"):
            w.on_entry(e, at("15:09:10"))
        w.flush(at("15:10:10"))
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())


if __name__ == "__main__":
    unittest.main()
