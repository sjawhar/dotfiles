#!/usr/bin/env python3
"""The host finds a box's session by the session's own report (dispatch://INFRA-1720).

The omp a box's runner starts writes one file in the box's runtime directory, `agentbox-session`
(omp/extensions/session-env.ts): its format version, its pid, the id of the session it runs and
that session's file. The host sees it at `$RUN_BASE/<box>/run-user/agentbox-session` and takes
nothing else for the session: no `docker exec` into the box, no launch line in the runner's log,
no directory named after the box. Before this change those three routes found no session for a
session started fresh until its first reply, and none at all under omp's database store: on
2026-11-08 13 of 70 live boxes could not be restarted, and on 2026-11-14 two fresh boxes enrolled
with the secrets broker under the box's name (dispatch://INFRA-3462/comment/7b61823d).

The runner says at its start that it is a runner of this change (`agentbox-runner`), and answers
each restart request in `agentbox-restart`, so no restart that does not happen is silent.

Technique, as test_agentbox_enroll.py: source scripts/agentbox (its source guard runs nothing)
with `docker` and the other host tools stubbed first on PATH, and a scratch $RUN_BASE.
"""

from __future__ import annotations

import os
import shutil
import signal
import stat
import subprocess
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
AGENTBOX = DOTFILES / "scripts" / "agentbox"
GATEWAY = "172.31.0.1"

BOX = "agentbox-0123456789ab"
OTHER_BOX = "agentbox-ba9876543210"
SID = "01a1f00d-0000-7000-8000-00000000a001"
OTHER_SID = "01a1beef-0000-7000-8000-00000000a002"


def write_stub(directory: Path, name: str, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


# Every call is logged. `ps` without -a lists the running boxes ($STUB_RUNNING); `ps -a --filter
# name=^<box>$` names a box that has a container ($STUB_CONTAINERS). `exec <box> sh -c …` is
# box_runner_pid's probe: it answers a pid for a box in $STUB_RUNNER_BOXES and, for the old
# session routes, the `pgrep -x omp` scan answers $STUB_OLD_SID. `exec --user ubuntu <box> CMD…`
# runs CMD here, where the box's paths are the host's. `kill -s USR1 <box>` runs $STUB_ON_USR1, a
# script that stands for the runner's answer. `inspect -f '{{.Id}}'` answers $STUB_CID.
DOCKER_STUB = r"""
echo "docker $*" >>"$STUB_CALLS"
case "$1" in
    ps)
        want=""
        for a in "$@"; do case "$a" in name=^*\$) want="${a#name=^}"; want="${want%\$}" ;; esac; done
        if [[ "$*" == *" -a "* || "$2" == -a ]]; then
            grep -qxF "$want" "$STUB_CONTAINERS" 2>/dev/null && echo "$want"
        else
            cat "$STUB_RUNNING" 2>/dev/null
        fi
        exit 0 ;;
    exec)
        if [[ "$2" == --user ]]; then shift 4; exec "$@"; fi
        case "$*" in
            *"pgrep -x omp"*) echo "${STUB_OLD_SID:-}" ;;
            *agentbox-run*) grep -qxF "$2" "$STUB_RUNNER_BOXES" 2>/dev/null && echo 4242 ;;
        esac
        exit 0 ;;
    kill)
        [[ -x "${STUB_ON_USR1:-}" ]] && "$STUB_ON_USR1" "${@: -1}"
        exit 0 ;;
    inspect) echo "${STUB_CID:-}" ;;
esac
exit 0
"""


class SessionFileFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.home = self.root / "home"
        (self.home / ".cache" / "omp" / "agentbox").mkdir(parents=True)
        self.run_base = self.root / "run"
        self.stubs = self.root / "bin"
        write_stub(self.stubs, "docker", DOCKER_STUB)
        write_stub(self.stubs, "envoy", 'echo "envoy $*" >>"$STUB_CALLS"; cat >>"$STUB_CALLS"; exit 0')
        self.calls = self.root / "calls"
        for name in ("calls", "running", "containers", "runner-boxes"):
            (self.root / name).write_text("", encoding="utf-8")
        self.env = {
            "AGENTBOX_IDENTITY_FILE": str(self.root / "not-a-box"),
            "AGENTBOX_BOX": "",
            "HOME": str(self.home),
            "PATH": f"{self.stubs}:/usr/bin:/bin",
            "DOTFILES_DIR": str(DOTFILES),
            "AGENTBOX_RUN_BASE": str(self.run_base),
            "AGENTBOX_BOXES": str(self.root / "boxes"),
            "AGENTBOX_DISKS": str(self.root / "disks"),
            "AGENTBOX_DISK": "0",
            "STUB_CALLS": str(self.calls),
            "STUB_RUNNING": str(self.root / "running"),
            "STUB_CONTAINERS": str(self.root / "containers"),
            "STUB_RUNNER_BOXES": str(self.root / "runner-boxes"),
        }

    def run_bash(self, body: str, env: dict[str, str] | None = None, timeout: float = 120) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "-c", f'source "{AGENTBOX}"\n{body}'],
            capture_output=True, text=True, check=False, timeout=timeout,
            env={**self.env, **(env or {})},
        )

    def run_user(self, box: str = BOX) -> Path:
        path = self.run_base / box / "run-user"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def transcript(self, sid: str = SID) -> Path:
        path = self.home / ".omp" / "agent" / "sessions" / "-work" / f"2026-11-15T22-00-00-000Z_{sid}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
        return path

    def session_file(self, box: str = BOX, sid: str = SID, pid: int = 77, file: Path | None = None,
                     version: str = "1") -> Path:
        """What session-env.ts writes in the box's runtime dir, as the host sees it."""
        path = self.run_user(box) / "agentbox-session"
        target = file if file is not None else self.transcript(sid)
        path.write_text(f"version={version}\npid={pid}\nsession={sid}\nfile={target}\n", encoding="utf-8")
        return path

    def runner_file(self, box: str = BOX) -> Path:
        """What agentbox-run.ts writes at its start."""
        path = self.run_user(box) / "agentbox-runner"
        path.write_text("version=1\npid=4242\n", encoding="utf-8")
        return path

    def set_lines(self, name: str, *lines: str) -> None:
        (self.root / name).write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")

    def docker_calls(self) -> list[str]:
        return [line for line in self.calls.read_text(encoding="utf-8").splitlines() if line.startswith("docker ")]


class BoxSessionId(SessionFileFixture):
    def test_box_session_id_reads_only_the_file(self) -> None:
        """The session file is the one route, and the host asks the box nothing. Every route of
        before is set up here to name another session, so a fault that reads one of them is
        seen: an omp in the box holding a transcript (`docker exec … pgrep -x omp`), the runner's
        launch line with --resume, and a transcript in a directory named after the box."""
        self.env["STUB_OLD_SID"] = OTHER_SID
        (self.home / ".cache/omp/agentbox" / f"{BOX}.log").write_text(
            f"agentbox-run 2026-11-15T22:00:00Z {BOX}: omp started (pid 9): omp --resume {OTHER_SID}\n",
            encoding="utf-8")
        named = self.home / ".omp/agent/sessions" / f"-boxes-{BOX}-repo" / f"2026-11-15T22-00-00-000Z_{OTHER_SID}.jsonl"
        named.parent.mkdir(parents=True)
        named.write_text("{}\n", encoding="utf-8")

        with self.subTest("no session file"):
            got = self.run_bash(f"box_session_id {BOX}")
            self.assertEqual((got.returncode, got.stdout), (0, ""), got.stderr)
            self.assertEqual(self.docker_calls(), [], "box_session_id must not ask the box")

        with self.subTest("the session file"):
            self.session_file()
            got = self.run_bash(f"box_session_id {BOX}")
            self.assertEqual((got.returncode, got.stdout), (0, f"{SID}\n"), got.stderr)
            self.assertEqual(self.docker_calls(), [])

        target = self.run_user() / "agentbox-session"
        elsewhere = self.root / "elsewhere"
        elsewhere.write_text(f"version=1\npid=77\nsession={SID}\nfile=/x.jsonl\n", encoding="utf-8")
        for name, make in (
            ("a symlink to a file", lambda: target.symlink_to(elsewhere)),
            ("a directory", lambda: target.mkdir()),
            ("a FIFO", lambda: os.mkfifo(target)),
        ):
            with self.subTest(name):
                if target.is_dir() and not target.is_symlink():
                    target.rmdir()
                elif os.path.lexists(target):
                    target.unlink()
                make()
                started = time.monotonic()
                got = self.run_bash(f"box_session_id {BOX}")
                self.assertLess(time.monotonic() - started, 30, "a FIFO must not hold the host")
                self.assertEqual((got.returncode, got.stdout), (0, ""), got.stderr)

        for name, text in (
            ("another format version", f"version=2\npid=77\nsession={SID}\nfile=/x.jsonl\n"),
            ("an id that is not a UUID", "version=1\npid=77\nsession=$(touch /tmp/pwned)\nfile=/x.jsonl\n"),
            ("no id", "version=1\npid=77\nfile=/x.jsonl\n"),
        ):
            with self.subTest(name):
                if target.is_dir() and not target.is_symlink():
                    target.rmdir()
                elif os.path.lexists(target):
                    target.unlink()
                target.write_text(text, encoding="utf-8")
                got = self.run_bash(f"box_session_id {BOX}")
                self.assertEqual((got.returncode, got.stdout), (0, ""), got.stderr)

    def test_a_session_id_finds_the_box_whose_session_file_names_it(self) -> None:
        self.set_lines("running", OTHER_BOX, BOX)
        self.session_file(OTHER_BOX, OTHER_SID)
        self.session_file(BOX, SID)
        got = self.run_bash(f"resolve_box {SID}")
        self.assertEqual((got.returncode, got.stdout), (0, f"{BOX}\n"), got.stderr)
        got = self.run_bash(f"resolve_box {SID[:13]}")
        self.assertEqual(got.stdout, f"{BOX}\n", got.stderr)

    def test_a_session_id_no_box_names_says_where_an_old_box_is_found(self) -> None:
        """A box started before this change writes no session file, so its session id finds
        nothing. The bare `no running box matches` told nobody that (2026-11-08T02:03:36Z)."""
        self.set_lines("running", BOX)
        for command in ("sh", "grow", "restart"):
            with self.subTest(command):
                args = f"{OTHER_SID} 64g" if command == "grow" else OTHER_SID
                got = self.run_bash(f"cmd_{command} {args}")
                self.assertEqual(got.returncode, 1, got.stdout + got.stderr)
                self.assertIn("agentbox ls", got.stderr)
                self.assertIn("agentbox move --self", got.stderr)
                self.assertIn("older than", got.stderr)


class SessionWaiterTest2(SessionFileFixture):
    def test_test_2_sees_a_box_of_the_session_that_is_still_enrolling(self) -> None:
        """dispatch://INFRA-1720/comment/4fab10d7, gap 1. A box of the session that is still
        enrolling has no `enrollment-session` yet, so the waiter's test 2 (session_live_box)
        could not see it, and with a broker slower than the 180-second grace the waiter ended the
        session's enrollment while that box enrolled. The session file is written before the
        enrollment, and test 2 reads it."""
        closing, enrolling = OTHER_BOX, BOX
        (self.run_base / closing).mkdir(parents=True)
        (self.run_base / closing / "enrollment-session").write_text(f"{SID}\n", encoding="utf-8")
        self.session_file(enrolling, SID)
        self.set_lines("containers", enrolling)
        got = self.run_bash(f"session_live_box {SID} {closing}")
        self.assertEqual((got.returncode, got.stdout), (0, f"{enrolling}\n"), got.stderr)
        # A session file of another session, or of a box with no container, is not it.
        self.session_file(enrolling, OTHER_SID)
        got = self.run_bash(f"session_live_box {SID} {closing}")
        self.assertEqual((got.returncode, got.stdout), (1, "none\n"), got.stderr)
        self.session_file(enrolling, SID)
        self.set_lines("containers")
        got = self.run_bash(f"session_live_box {SID} {closing}")
        self.assertEqual((got.returncode, got.stdout), (1, "none\n"), got.stderr)


# The runner's answer to every restart request waiting in RUN_USER/agentbox-restart.d, as
# agentbox-run.ts appends it: the line ends ` [request <id>]` and the request file goes. A shell
# snippet for a stand-in runner; `other` first appends an answer under another caller's id, as when
# two callers' requests reach the runner together.
def reply(run_user: str, word: str, text: str, other: str = "") -> str:
    first = (f'printf "2026-11-15T22:00:00.000Z {other} [request another-caller]\\n" >>"{run_user}/agentbox-restart"\n'
             if other else "")
    return first + (
        f'for f in "{run_user}"/agentbox-restart.d/*; do [ -e "$f" ] || continue; '
        f'printf "2026-11-15T22:00:00.000Z {word} {text} [request %s]\\n" "${{f##*/}}" >>"{run_user}/agentbox-restart"; '
        'rm -f "$f"; done\n'
    )


def answer(word: str, text: str) -> str:
    """An answer line under no request: one the caller must never take for its own."""
    return f"2026-11-15T22:00:00.000Z {word} {text}\n"


class RestartReasons(SessionFileFixture):
    """Every restart that does not happen prints why and exits non-zero (R3)."""

    def on_usr1(self, script: str) -> None:
        """What happens in the box when the host sends SIGUSR1: $1 is the box."""
        path = self.root / "on-usr1"
        path.write_text("#!/bin/bash\n" + textwrap.dedent(script), encoding="utf-8")
        path.chmod(0o755)
        self.env["STUB_ON_USR1"] = str(path)

    def answers(self, box: str = BOX) -> Path:
        return self.run_user(box) / "agentbox-restart"

    def usr1_sent(self) -> bool:
        return any(line.startswith("docker kill") for line in self.docker_calls())

    def test_the_waits_are_the_spec_s_figures(self) -> None:
        """With no environment that says otherwise, `--now` waits 120 s for the relaunched omp to
        report its session and an in-box restart 15 s for the runner's answer (spec R3): every
        other test here shortens both, so a changed default passed them all."""
        got = self.run_bash('echo "$RESTART_REPORT_WAIT $RESTART_ANSWER_WAIT"',
                            {"AGENTBOX_RESTART_REPORT_WAIT": "", "AGENTBOX_RESTART_ANSWER_WAIT": ""})
        self.assertEqual(got.stdout.split(), ["120", "15"], got.stderr)

    def test_the_host_writes_no_request_through_a_link_the_box_put(self) -> None:
        """The box owns its runtime directory, so it can put a link at `agentbox-restart.d`, or at a
        request's name, pointing at a host directory or file. The host's request goes through
        neither: it refuses, sends no signal, and writes nothing where the link points."""
        waits = {"AGENTBOX_RESTART_ANSWER_WAIT": "2", "AGENTBOX_RESTART_REPORT_WAIT": "2"}
        self.set_lines("running", BOX)
        self.set_lines("runner-boxes", BOX)
        self.runner_file()
        target = self.root / "host-dir"
        target.mkdir()
        requests = self.run_user() / "agentbox-restart.d"
        requests.symlink_to(target)
        got = self.run_bash(f"cmd_restart {BOX} --now", waits)
        self.assertEqual(got.returncode, 1, got.stdout + got.stderr)
        self.assertIn("could not write a restart request", got.stderr)
        self.assertFalse(self.usr1_sent(), "no signal without a request")
        self.assertEqual(list(target.iterdir()), [], "nothing written through the box's link")

    def test_restart_says_why_for_each_reason(self) -> None:
        waits = {"AGENTBOX_RESTART_ANSWER_WAIT": "2", "AGENTBOX_RESTART_REPORT_WAIT": "2"}
        self.set_lines("running", BOX)

        with self.subTest("a box that does not run omp under the runner"):
            got = self.run_bash(f"cmd_restart {BOX} --now", waits)
            self.assertEqual(got.returncode, 1, got.stdout + got.stderr)
            self.assertIn("does not run omp under the runner", got.stderr)
            self.assertFalse(self.usr1_sent())

        self.set_lines("runner-boxes", BOX)
        for now in ("--now", ""):
            with self.subTest("a box whose runner is older than this change", now=now):
                self.calls.write_text("", encoding="utf-8")
                self.session_file()
                got = self.run_bash(f"cmd_restart {BOX} {now}", waits)
                self.assertEqual(got.returncode, 1, got.stdout + got.stderr)
                self.assertIn("older than this change", got.stderr)
                self.assertIn("agentbox move --self", got.stderr)
                self.assertFalse(self.usr1_sent(), "no signal to a runner that cannot answer")
                self.assertNotIn("envoy", self.calls.read_text(encoding="utf-8"))

        self.runner_file()
        for word, text in (("refused", "the session did not report its id: no session file"),
                           ("busy", "a restart is already in progress; this signal is ignored")):
            with self.subTest("the runner says no", word=word):
                self.answers().unlink(missing_ok=True)
                self.on_usr1(reply(str(self.run_user()), word, text))
                got = self.run_bash(f"cmd_restart {BOX} --now", waits)
                self.assertEqual(got.returncode, 1, got.stdout + got.stderr)
                self.assertIn(text, got.stdout + got.stderr)
                self.assertTrue(self.usr1_sent())

        with self.subTest("a second caller gets its own busy, not the first caller's started"):
            self.answers().unlink(missing_ok=True)
            self.on_usr1(reply(str(self.run_user()), "busy", "another restart request came first; this one is ignored",
                               other=f"started session {SID}: hanging up omp (pid 77) to resume x"))
            got = self.run_bash(f"cmd_restart {BOX} --now", waits)
            self.assertEqual(got.returncode, 1, got.stdout + got.stderr)
            self.assertIn("came first", got.stderr)
            self.assertNotIn("hanging up omp", got.stdout + got.stderr, "the first caller's answer is not this one")
            self.assertEqual(list((self.run_user() / "agentbox-restart.d").iterdir()), [])
        with self.subTest("no answer, with an earlier answer already in the file"):
            self.answers().write_text(answer("started", f"session {SID}: hanging up omp (pid 77) to resume x"),
                                      encoding="utf-8")
            self.on_usr1(":\n")
            got = self.run_bash(f"cmd_restart {BOX} --now", waits)
            self.assertEqual(got.returncode, 1, got.stdout + got.stderr)
            self.assertIn("no answer", got.stderr)
            self.assertNotIn("hanging up omp", got.stdout + got.stderr, "an earlier answer is not this one")

        log = self.home / ".cache/omp/agentbox" / f"{BOX}.log"
        log.write_text("agentbox-run 2026-11-15T22:00:01Z x: omp exited after 1s; the box ends\n", encoding="utf-8")
        with self.subTest("a relaunch that does not report its session"):
            self.answers().unlink()
            self.on_usr1(reply(str(self.run_user()), "started", f"session {SID}: hanging up omp (pid 77) to resume x"))
            got = self.run_bash(f"cmd_restart {BOX} --now", waits)
            self.assertEqual(got.returncode, 1, got.stdout + got.stderr)
            self.assertIn("did not report its session", got.stderr)
            self.assertIn("omp exited after 1s; the box ends", got.stderr)

        with self.subTest("a relaunch that reports its session"):
            self.answers().unlink()
            new_file = self.session_file(pid=78).read_text(encoding="utf-8")
            self.session_file(pid=77)
            self.on_usr1(
                reply(str(self.run_user()), "started", f"session {SID}: hanging up omp (pid 77) to resume x")
                + f"( sleep 0.5; printf '%s' '{new_file}' >'{self.run_user()}/agentbox-session' ) &\n")
            got = self.run_bash(f"cmd_restart {SID} --now", waits)
            self.assertEqual(got.returncode, 0, got.stdout + got.stderr)
            self.assertIn("hanging up omp (pid 77)", got.stdout)
            self.assertIn("78", got.stdout)

        with self.subTest("the notice, with no session known"):
            (self.run_user() / "agentbox-session").unlink()
            got = self.run_bash(f"cmd_restart {BOX}", waits)
            self.assertEqual(got.returncode, 1, got.stdout + got.stderr)
            self.assertIn("session is not known", got.stderr)

        with self.subTest("the notice, with a transcript path that is not a file"):
            self.session_file(file=self.root / "gone.jsonl")
            got = self.run_bash(f"cmd_restart {BOX}", waits)
            self.assertEqual(got.returncode, 1, got.stdout + got.stderr)
            self.assertIn("not a regular file", got.stderr)
            self.assertNotRegex(self.calls.read_text(encoding="utf-8"), "envoy send")

        with self.subTest("the notice"):
            self.session_file()
            got = self.run_bash(f"cmd_restart {BOX}", waits)
            self.assertEqual(got.returncode, 0, got.stdout + got.stderr)
            self.assertRegex(self.calls.read_text(encoding="utf-8"), rf"envoy send .*{SID}")
            self.assertIn(SID, got.stdout)

        with self.subTest("a session id that no box's session file names"):
            got = self.run_bash(f"cmd_restart {OTHER_SID} --now", waits)
            self.assertEqual(got.returncode, 1, got.stdout + got.stderr)
            self.assertIn("agentbox ls", got.stderr)
            self.assertIn("agentbox move --self", got.stderr)

        with self.subTest("one box that does not restart among two makes the status non-zero"):
            self.set_lines("running", BOX, OTHER_BOX)
            got = self.run_bash(f"cmd_restart {BOX} {OTHER_BOX}", waits)
            self.assertEqual(got.returncode, 1, got.stdout + got.stderr)
            self.assertRegex(self.calls.read_text(encoding="utf-8"), rf"envoy send .*{SID}")
            self.assertIn(OTHER_BOX, got.stderr)

    def in_box(self, runner_script: str, env: dict[str, str] | None = None,
               version: bool = True) -> tuple[subprocess.CompletedProcess[str], Path]:
        """`agentbox restart` in a box: a stand-in runner (a process that traps SIGUSR1) whose
        pid is AGENTBOX_RUNNER_PID, and the box's runtime dir as XDG_RUNTIME_DIR.

        The runner waits in `wait`, which a trap interrupts at once (a `sleep` in the foreground
        held each trap until it ended), and before it is killed it gets a SIGUSR2 fence: a SIGUSR1
        the command sent before it ended is pending by then, and bash runs the lower signal's trap
        first, so `signals` holds every signal the command sent once `fence` exists. Without the
        fence a runner killed the moment the command returned recorded nothing, and a command that
        signalled a runner it must not signal passed."""
        runtime = self.root / "box-run-user"
        runtime.mkdir(exist_ok=True)
        for name in ("agentbox-runner", "agentbox-restart", "signals", "fence"):
            (runtime / name).unlink(missing_ok=True)
        shutil.rmtree(runtime / "agentbox-restart.d", ignore_errors=True)
        if version:
            (runtime / "agentbox-runner").write_text("version=1\npid=1\n", encoding="utf-8")
        runner = subprocess.Popen(
            ["bash", "-c", f'trap \'echo usr1 >>"$R/signals"; {runner_script}\' USR1\n'
                           'trap \'echo fence >"$R/fence"\' USR2\n'
                           'echo ready >"$R/ready"\nwhile :; do sleep 0.1 & wait $!; done'],
            env={**os.environ, "R": str(runtime)},
        )
        self.addCleanup(runner.kill)
        for _ in range(100):
            if (runtime / "ready").exists():
                break
            time.sleep(0.05)
        transcript = self.transcript()
        got = self.run_bash("cmd_restart", {
            "AGENTBOX_RUNNER_PID": str(runner.pid), "XDG_RUNTIME_DIR": str(runtime),
            "OMP_SESSION_ID": SID, "PI_SESSION_FILE": str(transcript),
            "AGENTBOX_RESTART_ANSWER_WAIT": "2", **(env or {}),
        })
        runner.send_signal(signal.SIGUSR2)
        for _ in range(200):
            if (runtime / "fence").exists():
                break
            time.sleep(0.05)
        self.assertTrue((runtime / "fence").exists(), "the stand-in runner never took its fence")
        runner.kill()
        runner.wait()
        return got, runtime

    def test_the_in_box_restart_says_why_for_each_reason(self) -> None:
        def runner(word: str, text: str, other: str = "") -> str:
            # The trap's script, where $R is the box's runtime dir.
            return reply("$R", word, text, other).replace("\n", "; ")

        started = runner("started", f"session {SID}: hanging up omp (pid 77) to resume x")
        with self.subTest("a subagent"):
            got, runtime = self.in_box(started, {"PI_SESSION_FILE": str(self.root / "x" / "Explore.jsonl")})
            self.assertEqual(got.returncode, 1, got.stdout + got.stderr)
            self.assertIn("subagent", got.stderr)
            self.assertFalse((runtime / "signals").exists())

        with self.subTest("a runner older than this change"):
            got, runtime = self.in_box(started, version=False)
            self.assertEqual(got.returncode, 1, got.stdout + got.stderr)
            self.assertIn("older than this change", got.stderr)
            self.assertIn("agentbox move --self", got.stderr)
            self.assertFalse((runtime / "signals").exists())

        for name, script, rc, text in (
            ("a refusal", runner("refused", "the session did not report its id: no session file"), 1, "did not report its id"),
            ("busy", runner("busy", "a restart is already in progress; this signal is ignored"), 1, "already in progress"),
            ("no answer", ":", 1, "no answer"),
            ("an answer under no request", f'printf "%s" "{answer("started", "x: hanging up omp")}" >>"$R/agentbox-restart"', 1, "no answer"),
            ("a second caller", runner("busy", "another restart request came first; this one is ignored",
                                       other=f"started session {SID}: hanging up omp (pid 77) to resume x"), 1, "came first"),
            ("started", started, 0, "hanging up omp"),
        ):
            with self.subTest(name):
                got, runtime = self.in_box(script)
                self.assertEqual(got.returncode, rc, got.stdout + got.stderr)
                self.assertIn(text, got.stdout + got.stderr)
                if rc:
                    self.assertNotIn("hanging up omp", got.stdout, "another caller's answer is not this one")
                self.assertTrue((runtime / "signals").exists(), "the runner got its signal")
                self.assertEqual(list((runtime / "agentbox-restart.d").glob("*")), [], "the request is gone")


if __name__ == "__main__":
    unittest.main()
