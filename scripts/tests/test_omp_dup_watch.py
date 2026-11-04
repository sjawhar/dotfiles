#!/usr/bin/env python3
"""Regression tests for omp-dup-watch: one session id run by more than one live omp process.

The incident: on sami-rocqperf, 2026-11-03 00:29:44-01:08:51Z, a second tmux server
(/tmp/tmux-1000/default) restored the saved layout and resumed every saved session, so 36
sessions got a second omp in that server's panes and 14 got one in a new agent box each. No
watch said so; people found it. These tests build /proc trees with the files the watch reads
(stat, cmdline, cgroup, environ, cwd, fd) for host omps, a box's omp (visible on the host, its
descriptors not readable) and omp's own helper processes.
"""

import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "omp-dup-watch"
LOADER = importlib.machinery.SourceFileLoader("omp_dup_watch", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
WATCH = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(WATCH)

HOST = "sami-rocqperf"
BTIME = 1793486100
X = "01a1aaaa-0000-7000-8000-00000000000a"
Y = "01a1bbbb-0000-7000-8000-00000000000b"
Z = "01a1cccc-0000-7000-8000-00000000000c"
BOX_ID = "8a41b727ca1b9d88591bce0cd308e14e6caebae9bb4638e879ebf44a278cf17c"
BOX = "agentbox-8a41b727ca1b"
MAIN_SOCKET = "/home/sami/.tmux/sockets/tmux-1000/default"
STRAY_SOCKET = "/tmp/tmux-1000/default"
HOST_CGROUP = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/run-c01a1523775c37321.scope\n"
BOX_CGROUP = (
    f"0::/user.slice/user-1000.slice/user-1000-agentbox.slice/docker-{BOX_ID}.scope/init.scope\n"
)
OMP = "/home/sami/.mise/installs/github-sjawhar-oh-my-pi/18.13.3/bin/omp"


def transcript(sid: str, bucket: str = "-src-repo") -> str:
    return f"/home/sami/.omp/agent/sessions/{bucket}/2026-11-03T00-29-44-000Z_{sid}.jsonl"


class FakeProc:
    """A /proc tree holding only what omp-dup-watch reads."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True)
        (root / "stat").write_text(f"cpu  1 2 3 4\nbtime {BTIME}\n")

    def add(
        self,
        pid: int,
        comm: str,
        ppid: int = 1,
        start: int = 1000,
        argv: tuple[str, ...] = (),
        cwd: str | None = None,
        holds: tuple[str, ...] = (),
        reads: tuple[str, ...] = (),
        cgroup: str = HOST_CGROUP,
        environ: dict[str, str] | None = None,
        box: bool = False,
    ) -> None:
        d = self.root / str(pid)
        d.mkdir()
        zeros = " ".join(["0"] * 12)
        (d / "stat").write_text(f"{pid} ({comm}) S {ppid} {pid} {pid} 0 -1 4194560 {zeros} {start} 0 0\n")
        (d / "cmdline").write_bytes(b"".join(a.encode() + b"\0" for a in argv))
        (d / "cgroup").write_text(cgroup)
        if box:
            # A box's omp runs under the box's uid: its fd, cwd and environ are not readable.
            return
        os.symlink(cwd or "/home/sami", d / "cwd")
        (d / "fd").mkdir()
        (d / "fdinfo").mkdir()
        # The flags the kernel shows in /proc/<pid>/fdinfo/<fd>, in octal: omp's transcript
        # writer is O_WRONLY|O_APPEND|O_CLOEXEC|O_LARGEFILE (02102001, every writer on
        # sami-rocqperf on 2026-11-03); a reader is O_RDONLY|O_CLOEXEC|O_LARGEFILE.
        fds = [("/dev/null", "0100002")] + [(t, "02102001") for t in holds] + [(t, "02100000") for t in reads]
        for n, (target, flags) in enumerate(fds, start=19):
            name = "0" if n == 19 else str(n)
            os.symlink(target, d / "fd" / name)
            (d / "fdinfo" / name).write_text(f"pos:\t0\nflags:\t{flags}\nmnt_id:\t30\nino:\t1\n")
        if environ:
            (d / "environ").write_bytes(b"".join(f"{k}={v}".encode() + b"\0" for k, v in environ.items()))

    def drop_fds(self, pid: int) -> None:
        for sub in ("fd", "fdinfo"):
            for link in (self.root / str(pid) / sub).iterdir():
                if link.name != "0":
                    link.unlink()

    def remove(self, pid: int) -> None:
        d = self.root / str(pid)
        for sub in ("fd", "fdinfo"):
            if (d / sub).is_dir():
                for link in (d / sub).iterdir():
                    link.unlink()
                (d / sub).rmdir()
        for f in d.iterdir():
            f.unlink()
        d.rmdir()

    def tmux_server(self, pid: int, socket: str) -> None:
        """A tmux server, with its listening socket in /proc/net/unix as the kernel shows it."""
        self.add(pid, "tmux: server", argv=("tmux", "new-session", "-d"), environ={"TMUX_TMPDIR": "x"})
        self.socket_of = getattr(self, "socket_of", {})
        self.socket_of[pid] = socket
        inode = 130000 + pid
        os.symlink(f"socket:[{inode}]", self.root / str(pid) / "fd" / "6")
        net = self.root / "net"
        net.mkdir(exist_ok=True)
        header = "Num       RefCount Protocol Flags    Type St Inode Path\n"
        line = f"0000000000000000: 00000002 00000000 00010000 0001 01 {inode} {socket}\n"
        (net / "unix").write_text(
            ((net / "unix").read_text() if (net / "unix").exists() else header) + line
        )

    def host_omp(
        self, pid: int, server: int, argv: tuple[str, ...] = (OMP,), holds: tuple[str, ...] = (),
        start: int = 1000, cwd: str = "/home/sami/src/repo", pane: str = "%5",
        reads: tuple[str, ...] = (),
    ) -> None:
        """An omp in a tmux pane: server -> bash -> omp, with the pane's TMUX in its environment.
        `holds` are transcripts it writes; `reads` are files it holds open read-only."""
        shell = pid - 1
        self.add(shell, "bash", ppid=server, start=start, argv=("-bash",))
        socket = self.socket_of[server]
        self.add(
            pid, "omp", ppid=shell, start=start, argv=argv, cwd=cwd, holds=holds, reads=reads,
            environ={"TMUX": f"{socket},{server},0", "TMUX_PANE": pane},
        )

    def box_omp(self, pid: int, sid: str, start: int = 2000) -> None:
        self.add(pid, "omp", start=start, argv=(OMP, "--resume", transcript(sid)), cgroup=BOX_CGROUP, box=True)


class FakeEnvoy:
    """GET /v1/sessions answers `rows` (None: Envoy does not answer); publish/send are recorded."""

    publish_ok = True

    def __init__(self) -> None:
        self.rows: list[dict] | None = []
        self.published: list[tuple[str, str]] = []
        self.sent: list[tuple[str, str]] = []

    def request(self, method: str, path: str, body: dict | None = None):
        assert (method, path) == ("GET", "/v1/sessions")
        if self.rows is None:
            return 0, {"error": "connection refused"}
        return 200, self.rows

    def publish(self, topic: str, message: str) -> bool:
        self.published.append((topic, message))
        return self.publish_ok

    def send(self, session_id: str, message: str) -> bool:
        self.sent.append((session_id, message))
        return True

    def messages(self) -> list[str]:
        return [text for _, text in self.published] + [text for _, text in self.sent]


def row(sid: str, host: str = HOST) -> dict:
    return {"session_id": sid, "host": host, "dir": "/home/sami/src/repo", "last_seen": int(time.time() * 1000)}


class OmpDupWatchTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.state = self.base / "state" / "state.json"
        self.reports = self.state.parent / "reports"
        self.proc = FakeProc(self.base / "proc")
        self.proc.tmux_server(500, MAIN_SOCKET)
        self.proc.tmux_server(900, STRAY_SOCKET)
        self.envoy = FakeEnvoy()
        for patch in (
            unittest.mock.patch.object(WATCH, "Envoy", lambda timeout=8.0: self.envoy),
            unittest.mock.patch.object(WATCH, "container_names", return_value={BOX_ID[:12]: BOX}),
            unittest.mock.patch.object(WATCH.socket, "gethostname", return_value=HOST),
            unittest.mock.patch.dict(os.environ, {"BOX_HEALTH_OWNER": ""}),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def options(self, **overrides) -> argparse.Namespace:
        values = {
            "state": self.state,
            "proc": self.proc.root,
            "budget": 45.0,
            "http_timeout": 8.0,
            "dry_run": False,
            "print_alert": False,
            "report": False,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def run_once(self, **overrides) -> list[str]:
        """One run; the messages it sent."""
        before = len(self.envoy.messages())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(WATCH.run_once(self.options(**overrides)), 0)
        return self.envoy.messages()[before:]

    def report_of(self, message: str) -> str:
        self.assertIn("Full report: ", message)
        path = Path(message.rsplit("Full report: ", 1)[1].strip())
        self.assertEqual(path.parent, self.reports)
        self.assertTrue(path.is_file(), path)
        return path.read_text()

    def duplicate_x(self) -> None:
        """X in its own pane on the main server, and again in a box, as on 2026-11-03 00:31Z."""
        self.proc.host_omp(1001, 500, holds=(transcript(X),))
        self.proc.box_omp(2001, X)

    # -- the four rows the brief names ------------------------------------------------------

    def test_two_omps_for_one_id_send_one_alert_with_a_report_of_both(self) -> None:
        self.duplicate_x()
        sent = self.run_once()
        self.assertEqual(len(sent), 1, sent)
        self.assertEqual(self.envoy.published[0][0], "notifications.role.lead-box-health")
        message = sent[0]
        self.assertTrue(message.startswith("DUPLICATE OMP SESSIONS on sami-rocqperf"), message)
        self.assertIn(X, message)
        self.assertLessEqual(len(message), 600)
        report = self.report_of(message)
        for fact in ("1001", "2001", MAIN_SOCKET, BOX, "/home/sami/src/repo", "2026-10-31T22:35:10Z"):
            self.assertIn(fact, report)

    def test_the_same_duplicate_on_the_next_run_sends_nothing(self) -> None:
        self.duplicate_x()
        self.assertEqual(len(self.run_once()), 1)
        self.assertEqual(self.run_once(), [])
        self.assertEqual(self.run_once(), [])

    def test_a_new_duplicated_id_sends_one_alert_naming_only_it(self) -> None:
        self.duplicate_x()
        self.run_once()
        self.proc.host_omp(1101, 500, holds=(transcript(Y),))
        self.proc.host_omp(1201, 900, argv=(OMP, "--resume", Y), start=5000, pane="%1")
        sent = self.run_once()
        self.assertEqual(len(sent), 1, sent)
        self.assertIn(Y, sent[0])
        self.assertNotIn(X, sent[0])
        self.assertLessEqual(len(sent[0]), 600)
        report = self.report_of(sent[0])
        self.assertIn(STRAY_SOCKET, report)
        self.assertIn(X, report)  # the report holds every id still duplicated

    def test_an_id_back_to_one_omp_sends_a_one_line_clear(self) -> None:
        self.duplicate_x()
        self.run_once()
        self.proc.remove(2001)
        sent = self.run_once()
        self.assertEqual(len(sent), 1, sent)
        clear = sent[0]
        self.assertNotIn("\n", clear)
        self.assertIn("CLEARED", clear)
        self.assertIn(X, clear)
        self.assertLessEqual(len(clear), 600)
        self.report_of(clear)
        self.assertEqual(self.run_once(), [])

    def test_every_message_is_at_most_600_chars_and_names_its_report(self) -> None:
        """The incident's size: 50 ids at once, each in a pane of a second server or a box."""
        sids = [f"01a1{n:04x}-0000-7000-8000-{n:012x}" for n in range(50)]
        for n, sid in enumerate(sids):
            pid = 10000 + 10 * n
            self.proc.host_omp(pid + 1, 500, holds=(transcript(sid, bucket="--mnt-nvme-sami-a-very-long-worktree-name"),))
            if n % 3:
                self.proc.host_omp(pid + 5, 900, argv=(OMP, "--resume", sid), start=9000)
            else:
                self.proc.box_omp(pid + 7, sid)
        sent = self.run_once()
        self.assertEqual(len(sent), 1)
        self.assertLessEqual(len(sent[0]), 600, sent[0])
        self.assertIn("50", sent[0])
        report = self.report_of(sent[0])
        for sid in sids:
            self.assertIn(sid, report)
        for n in range(50):
            pid = 10000 + 10 * n
            self.proc.remove(pid + (5 if n % 3 else 7))
        sent = self.run_once()
        self.assertEqual(len(sent), 1)
        self.assertLessEqual(len(sent[0]), 600, sent[0])
        self.assertNotIn("\n", sent[0])
        self.report_of(sent[0])

    # -- what counts as a session's omp --------------------------------------------------------

    def test_omps_own_helpers_holding_the_transcript_are_not_a_second_copy(self) -> None:
        self.proc.host_omp(1001, 500, holds=(transcript(X),))
        self.proc.add(1002, "omp", ppid=1001, argv=(OMP, "__omp_worker_lsp"), holds=(transcript(X),))
        self.assertEqual(self.run_once(), [])

    def test_a_session_whose_transcript_sits_under_another_sessions_directory_is_identified(self) -> None:
        """Three live omps on 2026-11-03 resume `…/<bucket>/<stamp>_<other id>/<stamp>_<id>.jsonl`.
        The file name ends in the session's own id at any depth, which is what names the session;
        a second copy of one of them must not pass as an id nobody can read."""
        nested = (
            f"/home/sami/.omp/agent/sessions/-Code-personal-theorem/2026-10-14T17-14-49-559Z_{Z}"
            f"/2026-10-25T02-24-36-861Z_{X}.jsonl"
        )
        self.proc.host_omp(1001, 500, holds=(nested,))
        self.proc.host_omp(1101, 900, argv=(OMP, "--resume", nested))
        sent = self.run_once()
        self.assertEqual(len(sent), 1, sent)
        self.assertIn(X, sent[0])
        self.assertNotIn(Z, sent[0])

    def test_a_pid_reused_between_the_two_reads_of_the_census_is_dropped(self) -> None:
        """A pid that was an omp when its stat was read and another program by the time its
        command line is: 2026-11-03 02:59Z, pid 1728661 was `omp` in the pass and a python
        runner a second later. Counting its command line would attribute a session to it."""
        self.proc.host_omp(1001, 500, holds=(transcript(X),))
        self.proc.add(1301, "python", start=7000, argv=("python", "-u", "--resume", transcript(X)))
        table, _ = WATCH.process_table(self.proc.root, time.monotonic() + 30)
        name, state, parent, start = table[1301]
        with unittest.mock.patch.object(
            WATCH, "process_table", return_value=({**table, 1301: ("omp", state, parent, start)}, True)
        ):
            self.assertEqual(self.run_once(), [])

    def test_an_omp_reading_another_sessions_transcript_is_not_a_second_copy(self) -> None:
        """2026-11-03 05:59:13Z, the live watch's false positive: omp 2559955 runs session
        01a170df and writes its transcript; for a moment it also held 01a170e0's transcript open
        read-only, and 01a170e0's file was the newer one, so the watch took it as 2559955's
        session and reported 01a170e0 as run twice. An omp runs a transcript only when it holds it
        open for writing (O_WRONLY or O_RDWR in fdinfo's flags)."""
        sessions = self.base / "home/.omp/agent/sessions/-Code-personal-theorem"
        sessions.mkdir(parents=True)
        own = sessions / f"2026-10-25T02-24-36-861Z_{X}.jsonl"
        other = sessions / f"2026-10-25T02-25-48-934Z_{Y}.jsonl"
        own.write_text("{}\n")
        other.write_text("{}\n")
        os.utime(own, (time.time() - 600, time.time() - 600))  # the other session's file is newer
        self.proc.host_omp(1101, 500, holds=(str(other),), argv=(OMP, "--resume", str(other)))
        self.proc.host_omp(1001, 500, holds=(str(own),), reads=(str(other),), argv=(OMP, "--resume", str(own)))
        self.assertEqual(self.run_once(), [])

    def test_an_idle_omp_reading_another_sessions_transcript_keeps_its_own_id(self) -> None:
        """The same reader with its own transcript closed (idle) and no id on its command line
        (a session resumed in-app): a read-only descriptor names no session, so it is not
        counted as a copy of the session it reads."""
        self.proc.host_omp(1101, 500, holds=(transcript(Y),))
        self.proc.host_omp(1001, 500, reads=(transcript(Y),))
        self.assertEqual(self.run_once(), [])

    def test_a_process_resumed_from_a_subagent_transcript_names_no_session(self) -> None:
        """2026-11-01: two omps resumed from `…/<stamp>_<parent>/<stamp>_<id>/ArityLane.jsonl`."""
        self.proc.host_omp(1001, 500, holds=(transcript(X),))
        sub = f"/home/sami/.omp/agent/sessions/-src-repo/2026-11-01T00-00-00-000Z_{X}/ArityLane.jsonl"
        self.proc.host_omp(1101, 500, argv=(OMP, "--resume", sub))
        self.assertEqual(self.run_once(), [])

    def test_the_server_socket_is_read_from_the_kernel_when_the_omp_has_no_tmux(self) -> None:
        """An omp a tool call started carries no TMUX (4 of 73 here at 02:59Z), and the server's
        own command line has no -S; the path the reader needs is the server's bound socket."""
        self.proc.host_omp(1001, 500, holds=(transcript(X),))
        (self.proc.root / "1001" / "environ").unlink()
        self.proc.host_omp(1101, 900, argv=(OMP, "--resume", X))
        (self.proc.root / "1101" / "environ").unlink()
        sent = self.run_once()
        report = self.report_of(sent[0])
        self.assertIn(f"socket {MAIN_SOCKET}", report)
        self.assertIn(f"socket {STRAY_SOCKET}", report)

    def test_an_id_prefix_on_the_command_line_counts_for_the_full_id(self) -> None:
        self.proc.host_omp(1001, 500, holds=(transcript(X),))
        self.proc.host_omp(1101, 900, argv=(OMP, "--resume", X[:8]))
        sent = self.run_once()
        self.assertEqual(len(sent), 1)
        self.assertIn(X, sent[0])

    def test_two_reviewers_started_in_the_same_second_are_two_sessions(self) -> None:
        """2026-11-03 23:15:16Z: two `omp -p` reviewers of one PR, started in the same second in one
        directory, were reported as session "f1006fe8" run twice: `-r f1006fe8` inside their one
        prompt argument read as a resume. Their UUIDv7 ids share the first 8 hex; each holds its own
        transcript, and they are compared in full."""
        a = "01a1a5d0-1111-7000-8000-000000000001"
        b = "01a1a5d0-2222-7000-8000-000000000002"
        prompt = "You are reviewing #103. Check the evidence (`git ls-tree -r f1006fe8 theories/`)."
        for pid, sid, model in ((1101, a, "sol"), (1201, b, "opus")):
            self.proc.host_omp(pid, 500, argv=(OMP, "-p", "--mode", "json", "--model", model, prompt),
                               holds=(transcript(sid),), cwd="/mnt/nvme/sami/gate-runs")
        self.assertEqual(self.run_once(), [])
        for pid in (1101, 1201):
            self.proc.drop_fds(pid)
        self.assertEqual(self.run_once(), [], "a flag inside the prompt names no session")

    def test_a_prefix_two_full_ids_share_names_no_session(self) -> None:
        a = "01a1a5d0-1111-7000-8000-000000000001"
        b = "01a1a5d0-2222-7000-8000-000000000002"
        self.proc.host_omp(1001, 500, holds=(transcript(a),))
        self.proc.host_omp(1101, 500, holds=(transcript(b),))
        self.proc.host_omp(1201, 900, argv=(OMP, "--resume", a[:8]))
        self.assertEqual(self.run_once(), [])

    def test_resume_argument_is_read_from_its_last_path_component(self) -> None:
        parent, child = X, "01a17dd1-0000-7000-8000-000000000001"
        nested = f"/home/sami/.omp/agent/sessions/-src/2026-10-19T03-32-08-771Z_{parent}/2026-10-25T02-25-48-934Z_{child}.jsonl"
        self.assertEqual(WATCH.named_session([OMP, "--resume", nested]), child)
        self.assertEqual(WATCH.named_session([OMP, f"--resume={nested}"]), child)
        self.assertEqual(WATCH.named_session([OMP, "-r", parent[:8]]), parent[:8])
        self.assertIsNone(WATCH.named_session([OMP, "-p", f"run git log -r {parent[:8]}"]))
        self.assertIsNone(WATCH.named_session([OMP, "--resume", f"/x/2026Z_{parent}/ArityLane.jsonl"]))
        # A fork consult (scripts/omp-fork) resumes a COPY under a store of its own: it writes
        # nothing to the original and registers no second Envoy row, so it is not that session.
        # Six such consults from one box were reported as six duplicates (03:57:17Z, 2026-11-04).
        fork = f"/tmp/omp-fork-ab12cd/2026-11-04T03-50-00-000Z_{parent}.jsonl"
        self.assertIsNone(WATCH.named_session([OMP, "-p", "--mode", "json", "--session-dir",
                                               "/tmp/omp-fork-ab12cd", "--resume", fork]))
        self.assertIsNone(WATCH.named_session([OMP, "--session-dir=/tmp/omp-fork-ab12cd",
                                               f"--resume={fork}"]))
        # Its own --session-dir does not excuse a resume of the SHARED store's transcript.
        self.assertEqual(WATCH.named_session([OMP, "--session-dir", "/tmp/omp-fork-ab12cd",
                                              "--resume", nested]), child)
        self.proc.host_omp(1001, 500, holds=(transcript(parent),))
        self.proc.host_omp(1101, 900, argv=(OMP, "--resume", nested))
        self.assertEqual(self.run_once(), [], "the lane is its own session, not its parent's copy")
        # And a live session with six fork consults of itself running is not duplicated.
        for pid in range(1201, 1261, 10):
            self.proc.host_omp(pid, 900, argv=(OMP, "-p", "--mode", "json", "--session-dir",
                                                     f"/tmp/omp-fork-{pid}", "--resume",
                                                     f"/tmp/omp-fork-{pid}/2026Z_{parent}.jsonl"))
        self.assertEqual(self.run_once(), [], "a fork consult is a copy, not a second omp of the session")

    def test_a_process_that_closed_its_transcript_keeps_the_id_it_was_seen_holding(self) -> None:
        """A fresh session has no id on its command line; once seen holding its file, the same
        process (pid and start time) stays that session, so a closed descriptor is no clear."""
        self.duplicate_x()
        self.assertEqual(len(self.run_once()), 1)
        self.proc.drop_fds(1001)
        self.assertEqual(self.run_once(), [])

    # -- Envoy ---------------------------------------------------------------------------------

    def test_the_report_names_the_copy_envoy_delivers_to(self) -> None:
        """Envoy keys its registry by session id, so one id has one row; that row says which
        copy now receives the session's messages, which is what the reader needs to decide."""
        self.duplicate_x()
        self.envoy.rows = [row(X, host=BOX)]
        sent = self.run_once()
        report = self.report_of(sent[0])
        self.assertIn(f"Envoy delivers this session to agent box {BOX} (pid 2001)", report)

    def test_an_id_envoy_lists_twice_from_this_box_is_a_duplicate(self) -> None:
        self.proc.host_omp(1001, 500, holds=(transcript(X),))
        self.envoy.rows = [row(X), row(X, host=BOX)]
        sent = self.run_once()
        self.assertEqual(len(sent), 1)
        self.assertIn(X, sent[0])
        self.assertIn("Envoy", self.report_of(sent[0]))

    def test_rows_of_other_machines_are_not_counted(self) -> None:
        self.envoy.rows = [row(X, host="sami-leanperf"), row(X, host="sami-leanperf")]
        self.assertEqual(self.run_once(), [])

    def test_an_envoy_that_does_not_answer_still_leaves_process_duplicates_said(self) -> None:
        self.envoy.rows = None
        self.duplicate_x()
        self.assertEqual(len(self.run_once()), 1)

    # -- bounds, routing, side effects -------------------------------------------------------

    def test_a_census_cut_short_by_its_budget_clears_nothing(self) -> None:
        self.duplicate_x()
        self.run_once()
        self.proc.remove(2001)
        self.assertEqual(self.run_once(budget=0.0), [])
        self.assertIn(X, json.loads(self.state.read_text())["incidents"])
        sent = self.run_once()
        self.assertEqual(len(sent), 1)
        self.assertIn("CLEARED", sent[0])

    def test_the_sre_hears_it_when_nobody_holds_the_role(self) -> None:
        self.envoy.publish_ok = False
        self.duplicate_x()
        self.run_once()
        self.assertEqual([sid for sid, _ in self.envoy.sent], [WATCH.SRE])

    def test_an_alert_nobody_heard_is_tried_again_next_run(self) -> None:
        self.envoy.publish_ok = False
        self.duplicate_x()
        with unittest.mock.patch.object(self.envoy, "send", return_value=False):
            self.run_once()
        self.envoy.publish_ok = True
        sent = self.run_once()
        self.assertEqual(len(sent), 1)
        self.assertIn(X, sent[0])

    def test_dry_run_prints_and_sends_nothing_writes_nothing(self) -> None:
        self.duplicate_x()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            WATCH.run_once(self.options(dry_run=True))
        self.assertEqual(self.envoy.messages(), [])
        self.assertFalse(self.state.exists())
        self.assertFalse(self.reports.exists() and any(self.reports.iterdir()))
        self.assertIn("DUPLICATE OMP SESSIONS", out.getvalue())
        self.assertIn("2001", out.getvalue())

    def test_reports_older_than_seven_days_are_pruned(self) -> None:
        self.reports.mkdir(parents=True)
        old = self.reports / "20261020T000000Z-duplicate.txt"
        recent = self.reports / "20261102T000000Z-duplicate.txt"
        for path, age in ((old, 8 * 86400), (recent, 86400)):
            path.write_text("x")
            os.utime(path, (time.time() - age, time.time() - age))
        self.run_once()
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())


if __name__ == "__main__":
    unittest.main()
