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

RULING = "01a199e0-ca54-76a0-9f96-960779b2cf44"
HOST_SESSION = "01a19111-2222-7333-8444-555555555555"
RUNNER_SESSION = "01a17777-2222-7333-8444-666666666666"
SRE = "01a18385-f8c8-74a9-b6ff-694fb5fe87a7"
BOX_SCOPE = (
    "/user.slice/user-1000.slice/user-1000-agentbox.slice/"
    "docker-0ddb669c048c21d0c544893d29500b13e944b5c180c47e487e0a48f17643e528.scope/init.scope"
)
HOST_SCOPE = "/user.slice/user-1000.slice/user@1000.service/app.slice/run-u4242.scope"
PAGE = os.sysconf("SC_PAGE_SIZE")


def at(hms: str) -> float:
    """Epoch seconds of 2026-11-02T<hms>Z."""
    h, m, s = (int(x) for x in hms.split(":"))
    return 1793577600 + h * 3600 + m * 60 + s


def entries(until: str, since: str = "00:00:00") -> list[dict]:
    lo, hi = at(since), at(until)
    return [e for e in FIXTURE if lo <= int(e["__REALTIME_TIMESTAMP"]) / 1e6 < hi + 1]


class FakeProc:
    def __init__(self, root: Path) -> None:
        self.root = root

    def add(self, pid: int, comm: str, ppid: int, argv: list[str], cgroup: str, rss_mib: int = 1,
            start: int = 1000, session_file: str = "", uid_readable: bool = True) -> None:
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
        self.sent: list[tuple[str, str]] = []
        self.docker_calls: list[str] = []
        self.descriptions: dict[str, str] = {}

    def watch(self, to: str = "", started: float = 0.0):
        def send(target, text):
            self.sent.append((target, text))
            return True

        def docker_session(container):
            self.docker_calls.append(container)
            return ""

        return WATCH.Watch(
            proc_root=self.proc.root, state_path=self.state, send=send, to=to,
            host="sami-rocqperf", docker_session=docker_session, started=started,
            describe=lambda unit: self.descriptions.get(unit, ""),
        )

    def test_scope_with_no_omp_left_is_matched_by_the_scope_description(self) -> None:
        """A session that moved into a box leaves its host scope behind, described by shims/omp's
        command (seen 2026-11-02: run-r602e682e91424bbf894544c84a240159.scope, 15.7G, no omp)."""
        self.descriptions["run-u4242.scope"] = (
            f"/home/sami/.dotfiles/scripts/agent-session omp --resume {HOST_SESSION}")
        self.proc.add(3405892, "python3", 1, ["python3", "enum_dbg.py"], HOST_SCOPE, rss_mib=9000)
        w = self.watch()
        w.sample(at("15:09:05"))
        self.proc.kill(3405892)
        for e in entries("15:09:09", "15:09:09"):
            w.on_entry(e, at("15:09:10"))
        w.flush(at("15:10:10"))
        self.assertEqual([t for t, _ in self.sent], [HOST_SESSION])
        self.assertIn("its systemd scope run-u4242.scope", self.sent[0][1])

    # -- the tree each kill came from ----------------------------------------------------------

    def ruling_box(self) -> None:
        """RULING's box: containerd-shim > docker-init > bun (agentbox-run.ts) > omp > bash."""
        self.proc.add(3676141, "containerd-shim", 1, ["containerd-shim-runc-v2"], "/system.slice/containerd.service")
        self.proc.add(3676577, "docker-init", 3676141, ["/sbin/docker-init", "--"], BOX_SCOPE, uid_readable=False)
        self.proc.add(3681848, "bun", 3676577, ["bun", "agentbox-run.ts"], BOX_SCOPE, uid_readable=False)
        self.proc.add(3693878, "omp", 3681848, ["/home/sami/.mise/installs/x/bin/omp", "--resume", RULING],
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
        for needle in ("2026-11-02T15:09:09Z", "3405892", '"python3"', "9648 MiB", "9.36%", "19197 of 205180 MiB",
                       "python3 enum_dbg.py", "143", "running-heavy-jobs", "process tree"):
            self.assertIn(needle, text)

    def test_two_box_kills_in_one_minute_are_one_notice_and_the_next_is_another(self) -> None:
        """15:10:59Z and 15:11:50Z: simsci in RULING's box (uid 166536); 15:20:57Z: one more."""
        self.ruling_box()
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
        self.assertEqual(target, RULING)
        self.assertIn("2 of your processes", text)
        for needle in ("2026-11-02T15:10:59Z", "3458528", "5966 MiB", "9.64%",
                       "2026-11-02T15:11:50Z", "3731783", "5936 MiB", "9.24%", '"simsci"'):
            self.assertIn(needle, text)
        self.assertEqual(self.docker_calls, [], "the box omp's --resume argument names the session")

        self.simsci(671743, 7000)
        w.sample(at("15:20:50"))
        self.proc.kill(671743)
        for e in entries("15:20:57", "15:12:00"):
            w.on_entry(e, at("15:20:58"))
        w.flush(at("15:22:00"))
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.sent[1][0], RULING)
        self.assertIn("1 of your processes", self.sent[1][1])
        self.assertIn("5481 MiB", self.sent[1][1])
        self.assertIn("9.91%", self.sent[1][1])

    def test_a_process_reparented_out_of_the_tree_is_matched_by_its_box(self) -> None:
        self.ruling_box()
        self.proc.add(3458528, "simsci", 3676577, ["simsci"], BOX_SCOPE, rss_mib=5900, uid_readable=False)
        w = self.watch()
        w.sample(at("15:10:55"))
        self.proc.kill(3458528)
        for e in entries("15:10:59", "15:10:00"):
            w.on_entry(e, at("15:11:00"))
        w.flush(at("15:12:00"))
        self.assertEqual([t for t, _ in self.sent], [RULING])
        self.assertIn("same agent box", self.sent[0][1])

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
        self.assertIn("session-tmp", self.sent[0][1])

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
        self.ruling_box()
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
        self.ruling_box()
        self.simsci(3458528, 5000)
        w = self.watch(to=SRE)
        w.sample(at("15:10:55"))
        self.proc.kill(3458528)
        for e in entries("15:10:59", "15:10:00"):
            w.on_entry(e, at("15:11:00"))
        w.flush(at("15:12:00"))
        self.assertEqual([t for t, _ in self.sent], [SRE])
        self.assertIn(RULING, self.sent[0][1], "the drill copy names the session it would have reached")

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
        self.assertEqual(self.sent[0], self.sent[1])


if __name__ == "__main__":
    unittest.main()
