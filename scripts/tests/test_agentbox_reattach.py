#!/usr/bin/env python3
"""A box ends when its container stops, not when the launcher's Docker client does
(scripts/agentbox box_client, dispatch://INFRA-4893).

`agentbox new` runs `docker run` attached in the box's pane. When dockerd stops (an upgrade, a
crash), that client ends with status 125 (`error waiting for container: unexpected EOF`), and
with dockerd's live-restore on the container runs on. Until this change the launcher took the
client's end for the box's end: close_box waited 60 s, found the container still running and
removed it with `docker rm -fv`, and its disk with it. Now the launcher asks Docker:

  * the container still runs: it attaches to it again (`docker attach`) in the same pane, and the
    box is not removed;
  * the container stopped: the box is closed as before, once Docker answers, and its exit row
    carries the exit code Docker recorded, not the client's 125;
  * Docker does not answer (or every attach ends at once): the box is left as it is.

Technique, as test_agentbox_enroll.py's CmdNew: source scripts/agentbox (its source guard runs
nothing) with `docker` and the other host tools stubbed first on PATH, and run cmd_new with the
steps that need a real host replaced by no-ops. close_box is the real one. The docker stub plays
one container, whose state is a file, and a Docker that can stop answering.
"""

from __future__ import annotations

import os
import signal
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
AGENTBOX = DOTFILES / "scripts" / "agentbox"


def write_stub(directory: Path, name: str, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


# One container, whose state is $STUB_STATE: `running`, `exited N` or `gone`. Every call is logged
# to $STUB_LOG. Docker does not answer a read (`ps`, `inspect`: exit 1, no output) while STUB_DOWN
# is set, and for the first $STUB_DOWN_READS reads.
#   run      notes the box's name; with STUB_RUN_UNTIL set it waits (60 s at most) until that file
#            exists; then ends as STUB_RUN_END says: `eof` (the client loses dockerd: status 125,
#            the container runs on), `eof-stopped N` (status 125, and the container is `exited N`,
#            as when dockerd stops it), or `exit N` (the box's command ended with N).
#   attach   logs whether the box's directory and runtime directory exist, then ends as
#            STUB_ATTACH_END says: `exit N` (the box ends with N while attached) or `eof` (status 1,
#            the container runs on).
#   ps       `--format {{.State}}` prints the container's state word, `{{.Names}}` its name;
#            nothing for a container that is gone.
#   rm       forgets the container. kill, with STUB_KILL_RELEASES set, makes STUB_RUN_UNTIL.
# With STUB_LAST_READ_GATE set, the first `ps` after box_client said it gives up (the launcher's own
# last read) logs `last read waits` and waits (60 s at most) until that file exists.
DOCKER_STUB = r'''
echo "docker $*" >>"$STUB_LOG"
state="$(cat "$STUB_STATE")"
box="$(cat "$STUB_BOX" 2>/dev/null)"
down() {
    [[ -n "${STUB_DOWN:-}" ]] && return 0
    (( $(grep -cE '^docker (ps|inspect) ' "$STUB_LOG") <= ${STUB_DOWN_READS:-0} ))
}
ending() {
    case "$1" in
        eof) echo "docker: error waiting for container: unexpected EOF" >&2; exit 125 ;;
        eof-stopped\ *) echo "exited ${1#* }" >"$STUB_STATE"; echo "docker: error waiting for container: unexpected EOF" >&2; exit 125 ;;
        exit\ *) echo "exited ${1#* }" >"$STUB_STATE"; exit "${1#* }" ;;
    esac
    echo "stub: no such ending '$1'" >&2; exit 99
}
case "$1" in
    run)
        while [[ "$1" != --name ]]; do shift; done
        echo "$2" >"$STUB_BOX"
        echo running >"$STUB_STATE"
        if [[ -n "${STUB_RUN_UNTIL:-}" ]]; then
            for _ in $(seq 600); do [[ -e "$STUB_RUN_UNTIL" ]] && break; /bin/sleep 0.1; done
        fi
        /bin/sleep 0.3
        ending "$STUB_RUN_END" ;;
    attach)
        dir=no; [[ -d "$STUB_BOXES/$2" ]] && dir=yes
        run=no; [[ -d "$STUB_RUN_BASE/$2" ]] && run=yes
        echo "attached $2 state=$state dir=$dir run=$run" >>"$STUB_LOG"
        /bin/sleep 0.1
        [[ "$STUB_ATTACH_END" == eof ]] && { echo "error: unexpected EOF" >&2; exit 1; }
        ending "$STUB_ATTACH_END" ;;
    ps)
        if [[ -n "${STUB_LAST_READ_GATE:-}" ]] && grep -qs "did not say within" "$HOME/.cache/omp/agentbox/$box.log" \
            && ! grep -qx "last read waits" "$STUB_LOG"; then
            echo "last read waits" >>"$STUB_LOG"
            for _ in $(seq 600); do [[ -e "$STUB_LAST_READ_GATE" ]] && break; /bin/sleep 0.1; done
        fi
        down && exit 1
        [[ "$state" == gone ]] && exit 0
        case "$*" in
            *"{{.State}}"*) echo "${state%% *}" ;;
            *) echo "$box" ;;
        esac ;;
    inspect)
        down && exit 1
        [[ "$state" == gone ]] && { echo "Error: No such object: ${*: -1}" >&2; exit 1; }
        code=0; [[ "$state" == exited\ * ]] && code="${state#* }"
        case "$*" in
            *State.Running*) [[ "$state" == running ]] && echo true || echo false ;;
            *"State.Status}} {{.State.ExitCode"*) echo "${state%% *} $code" ;;
            *State.Status*) echo "${state%% *}" ;;
            *State.ExitCode*) echo "$code" ;;
            *State.Pid*) echo 0 ;;
            *State.OOMKilled*) echo false ;;
            *State.FinishedAt*) echo 2026-11-20T00:00:00Z ;;
            *) echo "[]" ;;
        esac ;;
    rm) echo gone >"$STUB_STATE" ;;
    kill) [[ -z "${STUB_KILL_RELEASES:-}" ]] || : >"$STUB_RUN_UNTIL" ;;
esac
exit 0
'''

# The steps of `new` that need a real host are no-ops; mount_args makes the key dir and the box's
# runtime dir; the host has no broker. close_box is the real one.
NO_HOST = (
    "image_tag() { echo agentbox:test; }; build_image() { :; }; ensure_network() { :; }\n"
    "ensure_forwarders() { :; }; check_box_disk_space() { :; }\n"
    'mount_args() { mkdir -p "$(box_keydir "$1")" "$RUN_BASE/$1/run-user"; }\n'
    'session_env() { :; }; clone_checkout() { mkdir -p "$2"; }\n'
    "collect_start() { :; }\n"
    'broker_ready() { BROKER_UNREADY="no broker in this test"; return 1; }\n'
)


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.home = self.root / "home"
        self.boxes = self.root / "boxes"
        self.run_base = self.root / "run"
        self.log = self.root / "log"
        self.state = self.root / "state"
        self.exits = self.root / "agentbox-exits.tsv"
        for path in (self.home / ".config", self.boxes, self.run_base, self.root / "disks", self.root / "src" / "repo1"):
            path.mkdir(parents=True)
        self.log.write_text("", encoding="utf-8")
        self.state.write_text("running\n", encoding="utf-8")
        stubs = self.root / "bin"
        write_stub(stubs, "docker", DOCKER_STUB)
        # A real pause, short: the launcher's own polls sleep while the client lives. With
        # STUB_PAUSE_GATE set, box_client's pause before an attach (`sleep 5`) logs `pause waits`
        # and lasts until that file exists (60 s at most).
        write_stub(stubs, "sleep", 'echo "sleep $*" >>"$STUB_LOG"\n'
                   'if [[ -n "${STUB_PAUSE_GATE:-}" && "$*" == 5 ]]; then\n'
                   '    echo "pause waits" >>"$STUB_LOG"\n'
                   '    for _ in $(seq 600); do [[ -e "$STUB_PAUSE_GATE" ]] && break; /bin/sleep 0.1; done\n'
                   'fi\n'
                   'exec /bin/sleep 0.02')
        # With STUB_DATE_GATE set, the first `date -u +%FT%TZ` after box_client's first state read
        # (the time of its first log line) logs `date waits` and waits (60 s at most) for that file.
        write_stub(stubs, "date", 'if [[ -n "${STUB_DATE_GATE:-}" && "$*" == "-u +%FT%TZ" ]] \\\n'
                   '    && grep -q "^docker ps " "$STUB_LOG" && ! grep -qx "date waits" "$STUB_LOG"; then\n'
                   '    echo "date waits" >>"$STUB_LOG"\n'
                   '    for _ in $(seq 600); do [[ -e "$STUB_DATE_GATE" ]] && break; /bin/sleep 0.1; done\n'
                   'fi\n'
                   'exec /bin/date "$@"')
        write_stub(stubs, "systemctl", 'echo "systemctl $*" >>"$STUB_LOG"; exit 0')
        write_stub(stubs, "mountpoint", "exit 1")
        write_stub(stubs, "sudo", 'echo "sudo $*" >>"$STUB_LOG"; exit 1')
        write_stub(stubs, "git", "exit 0")
        write_stub(stubs, "agent-secrets", "exit 1")
        self.env = {
            # These tests can run INSIDE a box, and the script takes its in-box paths when it
            # finds the box identity file; pointed at a path that does not exist.
            "AGENTBOX_IDENTITY_FILE": str(self.root / "not-a-box"),
            "AGENTBOX_BOX": "",
            "HOME": str(self.home),
            "PATH": f"{stubs}:/usr/bin:/bin",
            "DOTFILES_DIR": str(DOTFILES),
            "AGENTBOX_SRC": str(self.root / "src"),
            "AGENTBOX_BOXES": str(self.boxes),
            "AGENTBOX_DISKS": str(self.root / "disks"),
            "AGENTBOX_CACHE_ROOT": str(self.root / "box-cache"),
            "AGENTBOX_RUN_BASE": str(self.run_base),
            "AGENTBOX_DISK": "0",
            "AGENTBOX_EXITS": str(self.exits),
            "AGENTBOX_COLLECT_LOG_DIR": str(self.root / "collect-logs"),
            "TMPDIR": str(self.root),
            "STUB_LOG": str(self.log),
            "STUB_STATE": str(self.state),
            "STUB_BOX": str(self.root / "box-name"),
            "STUB_BOXES": str(self.boxes),
            "STUB_RUN_BASE": str(self.run_base),
        }

    def script(self) -> str:
        return f'source "{AGENTBOX}"\n{NO_HOST}cmd_new repo1 -- bash -c true\n'

    def launch(self, **stubs: str) -> subprocess.CompletedProcess[str]:
        """`agentbox new repo1 -- bash -c true`, the docker stub set by `stubs`."""
        return subprocess.run(
            ["bash", "-c", self.script()],
            capture_output=True,
            text=True,
            env={**self.env, **stubs},
            check=False,
            timeout=120,
            start_new_session=True,
        )

    def box(self) -> str:
        return (self.root / "box-name").read_text(encoding="utf-8").strip()

    def calls(self, prefix: str) -> list[str]:
        return [line for line in self.log.read_text(encoding="utf-8").splitlines() if line.startswith(prefix)]

    def box_log(self) -> str:
        path = self.home / ".cache" / "omp" / "agentbox" / f"{self.box()}.log"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def exit_rows(self) -> list[list[str]]:
        if not self.exits.exists():
            return []
        return [line.split("\t") for line in self.exits.read_text(encoding="utf-8").splitlines()]

    def assert_closed(self, result: subprocess.CompletedProcess[str], rc: int) -> None:
        """Closed as before: the container removed with its volume and not by force, its
        directory and runtime directory gone, one exit row with `rc`, and the launcher's status
        `rc`."""
        said = result.stdout + result.stderr
        box = self.box()
        self.assertEqual(self.calls("docker rm"), [f"docker rm -v {box}"], said)
        self.assertFalse((self.boxes / box).exists(), said)
        self.assertFalse((self.run_base / box).exists(), said)
        rows = self.exit_rows()
        self.assertEqual([(row[1], row[3]) for row in rows], [(box, str(rc))], said)
        self.assertEqual(result.returncode, rc, said)

    def assert_left(self, result: subprocess.CompletedProcess[str], rc: int) -> None:
        """Left as it is: no removal of any kind, the box's directory and runtime directory still
        there, no exit row, the launcher's status `rc` (its last client's), and the launcher says
        so in the pane and in the box's log."""
        said = result.stdout + result.stderr
        box = self.box()
        self.assertEqual(self.calls("docker rm"), [], said)
        self.assertEqual(self.calls("docker kill"), [], said)
        self.assertTrue((self.boxes / box).is_dir(), said)
        self.assertTrue((self.run_base / box).is_dir(), said)
        self.assertEqual(self.exit_rows(), [], said)
        self.assertEqual(result.returncode, rc, said)
        self.assertIn(f"{box}: the box is left as it is, and nothing of it was removed", result.stderr)
        self.assertIn("the box is left as it is", self.box_log())


class Reattach(Fixture):
    def test_a_client_that_ends_while_the_box_runs_is_replaced_by_an_attach(self) -> None:
        """dockerd stops: `docker run` ends with 125 and the container runs on. The launcher
        attaches to the same container again while its directory and runtime directory are still
        there, and removes nothing. The box then ends (its command exits 0 while attached) and is
        closed as before."""
        result = self.launch(STUB_RUN_END="eof", STUB_ATTACH_END="exit 0")
        box = self.box()
        self.assertNotIn(f"docker rm -fv {box}", self.calls("docker rm"), result.stdout + result.stderr)
        self.assertEqual(self.calls("attached"), [f"attached {box} state=running dir=yes run=yes"],
                         result.stdout + result.stderr)
        self.assertEqual(self.calls("docker attach"), [f"docker attach {box}"])
        self.assertIn(
            f"{box}: its Docker client ended (status 125), and the box still runs; attaching to it again",
            self.box_log(),
        )
        self.assert_closed(result, 0)

    def test_a_box_that_stopped_while_docker_was_away_is_closed_once_docker_answers(self) -> None:
        """dockerd stops and takes the container with it (live-restore off): `docker run` ends
        with 125, and Docker answers none of the first five reads. The launcher asks again until
        Docker answers, then closes the box as before: `docker rm -v`, never an attach, and the
        exit row carries the exit code Docker recorded (137), not the client's 125. Before this
        change the close ran at once, while Docker did not answer: the container was never
        removed, and the row said 125."""
        result = self.launch(STUB_RUN_END="eof-stopped 137", STUB_DOWN_READS="5")
        self.assert_closed(result, 137)
        self.assertEqual(self.calls("docker attach"), [])
        self.assertIn("Docker does not say whether the box still runs", self.box_log())

    def test_a_box_docker_does_not_answer_about_is_left_as_it_is(self) -> None:
        """dockerd stops and does not come back within the window (2 s here, 300 s by default):
        every read fails. A read that fails is not an answer, so the box is left as it is:
        nothing removed, no attach, and the launcher ends with the client's status. Before this
        change close_box ran at once and deleted the box's directory and runtime directory under a
        container that could still be running."""
        result = self.launch(STUB_RUN_END="eof", STUB_DOWN="1", AGENTBOX_REATTACH_WAIT="2")
        self.assert_left(result, 125)
        self.assertEqual(self.calls("docker attach"), [])
        self.assertIn("Docker did not say within 2s whether the box still runs", self.box_log())

    def test_an_attach_that_keeps_ending_is_given_up_and_the_box_left(self) -> None:
        """Docker answers that the box runs, but each attach ends at once. The launcher tries
        again, a pause apart, until the window (2 s here) ends, then leaves the box as it is. It
        does not loop without end, and it does not remove a running box."""
        result = self.launch(STUB_RUN_END="eof", STUB_ATTACH_END="eof", AGENTBOX_REATTACH_WAIT="2")
        self.assert_left(result, 1)
        self.assertGreaterEqual(len(self.calls("docker attach")), 2, result.stdout + result.stderr)
        self.assertIn("the box still runs, but every attach to it ended within 2s; no more attaches", self.box_log())

    def test_a_box_whose_command_ends_is_closed_as_before(self) -> None:
        """The box's command exits 3: the container is `exited 3` when `docker run` returns 3.
        No attach; the box is closed as before."""
        result = self.launch(STUB_RUN_END="exit 3")
        self.assertEqual(self.calls("docker attach"), [])
        self.assertEqual(self.box_log(), "")
        self.assert_closed(result, 3)

    def term_when(self, mark: str, gate: Path | None = None, **stubs: str) -> tuple[str, str]:
        """cmd_new as `launch` does, with `stubs`; once the call log holds a line starting with
        `mark`, TERM to the launcher, as a move or a person sends it; then, 0.5 s later, `gate` is
        made, when there is one. Returns the launcher's stdout and stderr."""
        proc = subprocess.Popen(
            ["bash", "-c", self.script()],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**self.env, **stubs},
            start_new_session=True,
        )
        end = time.monotonic() + 60
        while not self.calls(mark) and time.monotonic() < end:
            time.sleep(0.05)
        self.assertTrue(self.calls(mark), f"no '{mark}' in the call log")
        os.kill(proc.pid, signal.SIGTERM)
        if gate is not None:
            time.sleep(0.5)
            gate.touch()
        return proc.communicate(timeout=120)

    def test_a_launcher_told_to_end_attaches_no_more(self) -> None:
        """TERM to the launcher: its trap hangs the box up, and here the client ends with 125
        while the container still runs. The box is being ended, so the launcher attaches no more
        and closes it as before (60 s of waiting, then `docker rm -fv`). An attach made anyway
        would end at once with the box (STUB_ATTACH_END), so a launcher that attaches fails this
        test at once and does not hang it."""
        release = self.root / "release"
        stdout, stderr = self.term_when(
            "docker run", STUB_RUN_END="eof", STUB_RUN_UNTIL=str(release), STUB_KILL_RELEASES="1",
            STUB_ATTACH_END="exit 0",
        )
        box = self.box()
        self.assertTrue(release.exists(), stdout + stderr)
        self.assertEqual(self.calls("docker attach"), [], stdout + stderr)
        self.assertEqual(self.calls("docker rm"), [f"docker rm -fv {box}"], stdout + stderr)
        self.assertFalse((self.boxes / box).exists(), stdout + stderr)

    def test_a_launcher_told_to_end_during_the_pause_before_an_attach_attaches_no_more(self) -> None:
        """The client ends with 125 while the box runs, and the TERM comes during box_client's
        pause before its attach. The USR1 the trap sends runs its handler only once the pause
        ends; the launcher still attaches no more, and closes the box as before."""
        gate = self.root / "pause-gate"
        stdout, stderr = self.term_when(
            "pause waits", gate, STUB_RUN_END="eof", STUB_PAUSE_GATE=str(gate), STUB_ATTACH_END="exit 0",
        )
        box = self.box()
        self.assertEqual(self.calls("docker attach"), [], stdout + stderr)
        self.assertEqual(self.calls("docker rm"), [f"docker rm -fv {box}"], stdout + stderr)
        self.assertFalse((self.boxes / box).exists(), stdout + stderr)

    def test_a_launcher_told_to_end_while_box_client_logs_attaches_no_more(self) -> None:
        """The client ends with 125 while the box runs, and the TERM comes while box_client writes
        its "attaching to it again" line (its `date`). The USR1 runs its handler once that command
        ends; the launcher still attaches no more, and closes the box as before."""
        gate = self.root / "date-gate"
        stdout, stderr = self.term_when(
            "date waits", gate, STUB_RUN_END="eof", STUB_DATE_GATE=str(gate), STUB_ATTACH_END="exit 0",
        )
        box = self.box()
        self.assertEqual(self.calls("docker attach"), [], stdout + stderr)
        self.assertEqual(self.calls("docker rm"), [f"docker rm -fv {box}"], stdout + stderr)
        self.assertFalse((self.boxes / box).exists(), stdout + stderr)

    def test_a_signal_during_the_launchers_last_read_closes_the_box(self) -> None:
        """Docker does not answer, box_client gives up, and the TERM comes while the launcher's
        own last read runs. The box is being ended, so it is closed as before (with Docker away
        that removes no container), and not left as it is."""
        gate = self.root / "read-gate"
        stdout, stderr = self.term_when(
            "last read waits", gate, STUB_RUN_END="eof", STUB_DOWN="1", AGENTBOX_REATTACH_WAIT="1",
            STUB_LAST_READ_GATE=str(gate),
        )
        box = self.box()
        self.assertNotIn("left as it is", stderr, stdout + stderr)
        self.assertFalse((self.boxes / box).exists(), stdout + stderr)
        self.assertFalse((self.run_base / box).exists(), stdout + stderr)

    def test_a_launcher_told_to_end_records_the_exit_code_docker_recorded(self) -> None:
        """TERM to the launcher, and the client ends with 125 as dockerd stops the container
        (exited 137). The exit row carries Docker's 137, not the client's 125."""
        release = self.root / "release"
        stdout, stderr = self.term_when(
            "docker run", STUB_RUN_END="eof-stopped 137", STUB_RUN_UNTIL=str(release), STUB_KILL_RELEASES="1",
        )
        box = self.box()
        self.assertEqual(self.calls("docker attach"), [], stdout + stderr)
        self.assertEqual(self.calls("docker rm"), [f"docker rm -v {box}"], stdout + stderr)
        self.assertEqual([(row[1], row[3]) for row in self.exit_rows()], [(box, "137")], stdout + stderr)


class ContainerState(Fixture):
    def test_only_dockers_own_answer_is_stopped(self) -> None:
        """box_container_state maps `docker ps`'s answer: running; exited, dead, created, removing
        or no container at all are stopped; a failed or timed-out read, or any other state, is
        unknown."""
        cases = [
            ("echo running", "running"),
            ("echo exited", "stopped"),
            ("echo dead", "stopped"),
            ("echo created", "stopped"),
            ("echo removing", "stopped"),
            ("exit 0", "stopped"),
            ("echo paused", "unknown"),
            ("echo restarting", "unknown"),
            ("exit 1", "unknown"),
            ("/bin/sleep 5", "unknown"),
        ]
        stubs = self.root / "bin"
        for body, want in cases:
            with self.subTest(body=body):
                write_stub(stubs, "docker", body)
                done = subprocess.run(
                    ["bash", "-c", f'source "{AGENTBOX}"\nbox_container_state agentbox-0123456789ab'],
                    capture_output=True,
                    text=True,
                    env={**self.env, "AGENTBOX_DOCKER_READ_TIMEOUT": "1"},
                    check=False,
                    timeout=60,
                )
                self.assertEqual((done.returncode, done.stdout), (0, f"{want}\n"), done.stderr)


if __name__ == "__main__":
    unittest.main()
