#!/usr/bin/env python3
"""`agentbox adopt <box>` puts a running box under a launcher of this version and stops nothing in
it (scripts/agentbox cmd_adopt, dispatch://INFRA-4893).

A launcher keeps the copy of scripts/agentbox it started from. One from before box_client takes
its Docker client's end for the box's end, so a dockerd restart removes the box. `adopt` ends that
launcher with SIGKILL (none of its traps run, so the box is not hung up), ends its `docker run`
client with SIGKILL too (`docker run` passes a TERM on to the box), and respawns the launcher's
tmux pane into `agentbox adopt --pane <box>`, which attaches to the box and from then on runs it
as `new` does, enrolling it again when the broker refuses its renew as not live.

Technique: the processes are real (`sleep`), so the signals are; what `adopt` reads of them
(cmdline, stat, environ, cwd) comes from a fixture /proc (AGENTBOX_PROC), as test_agentbox_move.py
does. `docker`, `tmux` and `agent-secrets` are stubs first on PATH.
"""

from __future__ import annotations

import os
import socket
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
AGENTBOX = DOTFILES / "scripts" / "agentbox"
BOX = "agentbox-0123456789ab"
SID = "01a1d3b0-ebf3-757e-8c7e-af00fbf03e95"
TP = "T" * 43


def write_stub(directory: Path, name: str, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.home = self.root / "home"
        self.proc = self.root / "proc"
        self.run_base = self.root / "run"
        self.disks = self.root / "disks"
        self.log = self.root / "log"
        for path in (self.home, self.proc, self.run_base, self.disks, self.root / "boxes"):
            path.mkdir(parents=True)
        self.log.write_text("", encoding="utf-8")
        self.stubs = self.root / "bin"
        # `ps` says the box runs; every call is logged, so a hang-up or removal would show.
        write_stub(self.stubs, "docker", 'echo "docker $*" >>"$STUB_LOG"\n'
                   'case "$1" in ps) echo "${STUB_STATE:-running}" ;; esac\nexit 0')
        write_stub(self.stubs, "tmux", '{ printf "tmux"; printf " [%s]" "$@"; echo; } >>"$STUB_LOG"; exit 0')
        self.sock = self.root / "tmux.sock"
        listener = socket.socket(socket.AF_UNIX)
        listener.bind(str(self.sock))
        self.addCleanup(listener.close)
        self.procs: list[subprocess.Popen[bytes]] = []
        self.env = {
            "AGENTBOX_IDENTITY_FILE": str(self.root / "not-a-box"),
            "AGENTBOX_BOX": "",
            "HOME": str(self.home),
            "PATH": f"{self.stubs}:/usr/bin:/bin",
            "DOTFILES_DIR": str(DOTFILES),
            "AGENTBOX_PROC": str(self.proc),
            "AGENTBOX_RUN_BASE": str(self.run_base),
            "AGENTBOX_DISKS": str(self.disks),
            "AGENTBOX_BOXES": str(self.root / "boxes"),
            "TMPDIR": str(self.root),
            "STUB_LOG": str(self.log),
        }

    def tearDown(self) -> None:
        for p in self.procs:
            if p.poll() is None:
                p.kill()
            p.wait()

    def real(self) -> subprocess.Popen[bytes]:
        """A real process for a signal to reach, reaped at once when it ends (a reaper thread),
        as its real parent would reap it."""
        p = subprocess.Popen(["sleep", "300"])
        self.procs.append(p)
        threading.Thread(target=p.wait, daemon=True).start()
        return p

    def fake(self, pid: int, ppid: int, argv: list[str], start: int, environ: dict[str, str] | None = None,
             session: int = 0) -> None:
        """What the fixture /proc says of PID: its argv, its parent, its session (field 6 of
        stat), its start time (field 22), and, for a launcher, its environment and working
        directory."""
        d = self.proc / str(pid)
        d.mkdir()
        (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
        fields = ["S", str(ppid), "0", str(session)] + ["0"] * 15 + [str(start)] + ["0"] * 30
        (d / "stat").write_text(f"{pid} ({os.path.basename(argv[0])}) {' '.join(fields)}\n", encoding="utf-8")
        if environ is not None:
            (d / "environ").write_bytes(b"".join(f"{k}={v}".encode() + b"\0" for k, v in environ.items()))
            os.symlink(self.root, d / "cwd")

    def box_launched(self, upper_argv: list[str] | None = None, leader: bool = False) -> tuple[subprocess.Popen[bytes], ...]:
        """A box launched as on sami-devbox before box_client: the pane's `bash -i -c` (upper),
        its launcher `bash <copy> new …`, and the launcher's `docker run … --name BOX` (client).
        With `leader`, the launcher is the pane's own command, its session's leader."""
        upper, launcher, client = self.real(), self.real(), self.real()
        (self.run_base / BOX).mkdir(exist_ok=True)
        launcher_argv = ["bash", "/tmp/agentbox-self-AbCdEf", "new", "--dir", "/x", "--", "omp"]
        self.fake(upper.pid, 1, upper_argv or ["bash", "-i", "-c", "agentbox new …; exec bash -i"], 100, session=upper.pid)
        self.fake(launcher.pid, upper.pid, launcher_argv, 200, {
            "TMUX_PANE": "%7", "TMUX": f"{self.sock},4242,0", "AGENTBOX_BOXES": "/boxes/of/that/launcher",
            "AGENTBOX_SELF_COPY": "/tmp/agentbox-self-AbCdEf", "PATH": "/bin",
            "AGENTBOX_RUN_BASE": str(self.run_base), "AGENTBOX_DISKS": str(self.disks),
        }, session=launcher.pid if leader else upper.pid)
        self.fake(client.pid, launcher.pid, ["docker", "run", "--init", "--name", BOX, "-it", "img", "omp"], 300,
                  session=launcher.pid if leader else upper.pid)
        return upper, launcher, client

    def adopt(self, prelude: str = "", **env: str) -> subprocess.CompletedProcess[str]:
        """`agentbox adopt BOX` from a shell whose AGENTBOX_RUN_BASE and AGENTBOX_DISKS are not the
        launcher's: the box's runtime directory and record are where its launcher put them."""
        operator = {"AGENTBOX_RUN_BASE": str(self.root / "operator-run"), "AGENTBOX_DISKS": str(self.root / "operator-disks")}
        return subprocess.run(
            ["bash", "-c", f'source "{AGENTBOX}"\n{prelude}cmd_adopt {BOX}'],
            capture_output=True, text=True, env={**self.env, **operator, **env}, check=False, timeout=120,
        )

    def calls(self, prefix: str) -> list[str]:
        return [line for line in self.log.read_text(encoding="utf-8").splitlines() if line.startswith(prefix)]

    def wait_end(self, p: subprocess.Popen[bytes]) -> int | None:
        end = time.monotonic() + 15
        while p.poll() is None and time.monotonic() < end:
            time.sleep(0.05)
        return p.returncode


class Adopt(Fixture):
    def test_the_launcher_and_then_the_client_get_sigkill_back_to_back(self) -> None:
        """SIGKILL to the launcher, then at once SIGKILL to the client, and no SIGSTOP: a stopped
        job makes the pane's interactive bash go on to `exec bash -i`, which sends SIGTERM to its
        stopped jobs, and that TERM reached the box through the client on the test VM (exit 143).
        A dead launcher cannot take the client's end for the box's end. A `kill` function logs
        each signal (not the `kill -0` probes) before the builtin sends it."""
        upper, launcher, client = self.box_launched()
        prelude = 'kill() { [[ "$1" == -0 ]] || echo "kill $*" >>"$STUB_LOG"; builtin kill "$@"; }\n'
        result = self.adopt(prelude)
        said = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, said)
        self.assertEqual(self.calls("kill "), [f"kill -KILL {launcher.pid}", f"kill -KILL {client.pid}"], said)

    def test_a_launcher_that_is_its_panes_own_command_is_refused(self) -> None:
        """The launcher leads its pane's session: ending it would hang up the pane's terminal,
        which closes the pane before it can be respawned and sends the client a SIGHUP that
        docker passes on to the box. Nothing is signalled."""
        upper, launcher, client = self.box_launched(leader=True)
        result = self.adopt()
        said = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, said)
        self.assertIn("is its pane's own command", said)
        time.sleep(0.5)
        self.assertEqual([p.poll() for p in (upper, launcher, client)], [None, None, None], said)
        self.assertEqual(self.calls("tmux"), [], said)

    def test_adopt_ends_the_launcher_and_client_and_respawns_the_pane_and_leaves_the_box_running(self) -> None:
        """The launcher and the client end by SIGKILL (a TERM would run the launcher's trap, and
        `docker run` passes a TERM on to the box: exit 143 on the test VM, 2026-11-21), the box
        is never hung up, stopped or removed (no `docker kill`, `stop`, `rm`), and the launcher's
        pane is respawned, on its server, in its directory, into `agentbox adopt --pane BOX` with
        the launcher's AGENTBOX_* settings and not its private copy's name."""
        upper, launcher, client = self.box_launched()
        (self.disks / f"{BOX}.info").write_text(f"box={BOX}\nlauncher={launcher.pid}\nlauncher_start=200\n", encoding="utf-8")
        result = self.adopt()
        said = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, said)
        self.assertEqual(self.wait_end(launcher), -9, said)
        self.assertEqual(self.wait_end(client), -9, said)
        self.assertIsNone(upper.poll(), said)
        for verb in ("kill", "stop", "rm", "attach"):
            self.assertEqual(self.calls(f"docker {verb}"), [], said)
        (respawn,) = self.calls("tmux")
        args = respawn.removeprefix("tmux [").removesuffix("]").split("] [")
        self.assertEqual(args[:9], ["-S", str(self.sock), "respawn-pane", "-k", "-t", "%7", "-c", str(self.root), "bash"], said)
        self.assertEqual(args[9:10], ["-i"], said)
        inner = args[11]
        # DOTFILES_DIR too: without it the new launcher reads ~/.dotfiles' files beside a checkout
        # elsewhere, which ended it on the test VM (2026-11-21, no broker-url.sh there).
        self.assertIn(f"env DOTFILES_DIR={DOTFILES} AGENTBOX_BOXES=/boxes/of/that/launcher AGENTBOX_RUN_BASE={self.run_base}"
                      f" AGENTBOX_DISKS={self.disks} {DOTFILES}/scripts/agentbox adopt --pane {BOX};", inner)
        self.assertNotIn("AGENTBOX_SELF_COPY", inner)
        self.assertIn("exec bash -i", inner)

    def test_a_box_already_under_a_launcher_that_attaches_again_is_left_alone(self) -> None:
        """The client's parent is box_client, a subshell of a launcher of this version: its own
        parent runs the same command. Nothing is signalled and no pane respawned."""
        launcher_argv = ["bash", "/tmp/agentbox-self-AbCdEf", "new", "--dir", "/x", "--", "omp"]
        upper, launcher, client = self.box_launched(upper_argv=launcher_argv)
        result = self.adopt()
        said = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, said)
        self.assertIn(f"{BOX} already runs under a launcher that attaches to it again", said)
        time.sleep(0.5)
        self.assertEqual([p.poll() for p in (upper, launcher, client)], [None, None, None], said)
        self.assertEqual(self.calls("tmux"), [], said)

    def test_a_launcher_whose_start_time_is_not_the_records_is_refused(self) -> None:
        """The box's record names its launcher by pid and start time; the parent of the client has
        that pid but another start time (the pid was used again). Nothing is signalled."""
        upper, launcher, client = self.box_launched()
        (self.disks / f"{BOX}.info").write_text(f"box={BOX}\nlauncher={launcher.pid}\nlauncher_start=199\n", encoding="utf-8")
        result = self.adopt()
        said = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, said)
        self.assertIn(f"record names launcher pid {launcher.pid} (start 199), not pid {launcher.pid} (start 200)", said)
        time.sleep(0.5)
        self.assertEqual([p.poll() for p in (upper, launcher, client)], [None, None, None], said)
        self.assertEqual(self.calls("tmux"), [], said)

    def test_a_parent_that_is_not_an_agentbox_launcher_is_refused(self) -> None:
        """A `docker run --name BOX` started by hand from a shell: its parent is no launcher, so
        nothing is signalled (a SIGKILL would end the person's shell)."""
        shell, client = self.real(), self.real()
        (self.run_base / BOX).mkdir()
        self.fake(shell.pid, 1, ["bash"], 100, {"TMUX_PANE": "%7", "TMUX": f"{self.sock},4242,0"})
        self.fake(client.pid, shell.pid, ["docker", "run", "--name", BOX, "img"], 300)
        result = self.adopt()
        said = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, said)
        self.assertIn("is not an agentbox launcher", said)
        time.sleep(0.5)
        self.assertEqual([shell.poll(), client.poll()], [None, None], said)

    def test_a_second_adopt_of_the_same_box_is_refused_while_one_runs(self) -> None:
        """Two adopts of one box at once could both find the old launcher and both respawn its
        pane; the second respawn's hangup reaches the first one's new launcher, whose HUP trap
        hangs the box up. While one adopt holds the box's adopt lock, another signals nothing."""
        import fcntl
        upper, launcher, client = self.box_launched()
        with open(self.run_base / BOX / "adopt.lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.adopt()
        said = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, said)
        self.assertIn(f"another agentbox adopt of {BOX} runs; nothing was done", said)
        time.sleep(0.5)
        self.assertEqual([p.poll() for p in (upper, launcher, client)], [None, None, None], said)
        self.assertEqual(self.calls("tmux"), [], said)

    def test_a_box_docker_does_not_say_runs_is_refused(self) -> None:
        upper, launcher, client = self.box_launched()
        result = self.adopt(STUB_STATE="exited")
        said = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, said)
        self.assertIn("not running; nothing was done", said)
        self.assertEqual([p.poll() for p in (upper, launcher, client)], [None, None, None], said)


class AdoptPane(Fixture):
    """`agentbox adopt --pane BOX`, the launcher the pane is respawned into."""

    def test_the_new_launcher_enrolls_the_box_again_when_its_renew_is_refused_as_not_live(self) -> None:
        """The box is enrolled as its session (enrollment-session, enrollment-id, the session
        key's thumbprint on record) and its renew loop has marked the enrollment not live. The new
        launcher enrolls the same key again, as the session, with the recorded thumbprint, then
        closes the box when it ends."""
        boxrun = self.run_base / BOX
        keydir = boxrun / "agent-secrets"
        keydir.mkdir(parents=True)
        (boxrun / "run-user").mkdir()
        (boxrun / "enrollment-session").write_text(SID + "\n", encoding="utf-8")
        (boxrun / "enrollment-id").write_text("enr-1\n", encoding="utf-8")
        (keydir / "key.pem").write_text("key\n", encoding="utf-8")
        (keydir / "enrollment.not-live").write_text("", encoding="utf-8")
        skd = self.run_base / "session-keys" / SID
        skd.mkdir(parents=True)
        (skd / "thumbprint").write_text(TP + "\n", encoding="utf-8")
        (skd / "key.pem").write_text("key\n", encoding="utf-8")
        (self.root / "boxes" / BOX).mkdir()
        # The box ends 1 s after the attach; `ps` then says exited. Enroll writes the new id.
        write_stub(self.stubs, "docker", 'echo "docker $*" >>"$STUB_LOG"\n'
                   'case "$1" in\n'
                   '    attach) /bin/sleep 1; echo exited >"$STUB_STATE_FILE"; exit 0 ;;\n'
                   '    ps) cat "$STUB_STATE_FILE" 2>/dev/null || echo running ;;\n'
                   '    inspect) case "$*" in *Status*ExitCode*) echo "exited 0" ;; *ExitCode*) echo 0 ;; *Running*) echo false ;; *) echo "" ;; esac ;;\n'
                   'esac\nexit 0')
        write_stub(self.stubs, "agent-secrets", 'echo "agent-secrets $*" >>"$STUB_LOG"\n'
                   'case "$1" in\n'
                   '    machine) exit 0 ;;\n'
                   '    enroll) echo enr-2 >"$AGENT_SECRETS_KEY_DIR/enrollment"; echo enr-2 ;;\n'
                   'esac\nexit 0')
        for name in ("systemctl", "mountpoint", "sudo"):
            write_stub(self.stubs, name, "exit 1" if name != "systemctl" else "exit 0")
        write_stub(self.stubs, "sleep", 'exec /bin/sleep 0.02')
        result = subprocess.run(
            ["bash", "-c", f'source "{AGENTBOX}"\ncmd_adopt_pane {BOX}'],
            capture_output=True, text=True, check=False, timeout=120,
            env={**self.env, "STUB_STATE_FILE": str(self.root / "state"), "AGENTBOX_UNENROLL_GRACE": "0",
                 "AGENTBOX_EXITS": str(self.root / "exits.tsv")},
        )
        said = result.stdout + result.stderr
        self.assertEqual(self.calls("docker attach"), [f"docker attach {BOX}"], said)
        enrolls = self.calls("agent-secrets enroll")
        self.assertEqual(enrolls, [f"agent-secrets enroll --helper --runtime-id session-{SID} --thumbprint {TP} --session-id {SID}"], said)
        self.assertEqual(result.returncode, 0, said)
        self.assertFalse((self.root / "boxes" / BOX).exists(), said)
        # The close hands the session's enrollment to a detached waiter (unenroll_box), which
        # writes in this tree; it ends once it has revoked the enrollment.
        end = time.monotonic() + 60
        while not self.calls("agent-secrets unenroll") and time.monotonic() < end:
            time.sleep(0.1)
        time.sleep(1)
        self.assertEqual(self.calls("agent-secrets unenroll"), ["agent-secrets unenroll --helper --enrollment enr-2"], said)


if __name__ == "__main__":
    unittest.main()
