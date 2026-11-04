#!/usr/bin/env python3
"""scripts/agentbox's box enrollment: every way it ends, as the box's key dir shows it.

`agentbox new` puts enrollment.pending in a box's key dir before `docker run`, and from the legion
#1589 release the agent-secrets client in the box waits on that marker while it is fresh.
Enrollment must end it every time: enrolled (key.pem and enrollment, no marker, no error) or not
(enrollment.error, no key, no marker: the box has no broker identity and uses secretsd). The
marker stays fresh while the launcher still works; what the host writes into the box-writable key
dir never follows a link the box planted there; the id the host revokes is the one it recorded.

Technique: source scripts/agentbox (its source guard runs nothing) with `docker`, `git`,
`agent-secrets` and `sleep` stubbed first on PATH, and call its functions on a scratch key dir;
cmd_new runs with the steps that need a real host (image, network, clones, mounts) replaced by
no-ops.
"""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
AGENTBOX = DOTFILES / "scripts" / "agentbox"
THUMBPRINT = "A" * 43


def write_stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


# A box's key dir is $STUB_RUN_BASE/<box>/run-user/agent-secrets. `docker exec -d` runs its
# script here when STUB_RUN_RENEW is set; `docker run` notes whether the marker is there as the
# box starts, then lives for a second. `inspect` answers false until its STUB_RUNNING_AFTER'th
# call when that is set, as a box does while it is still starting.
DOCKER_STUB = rf"""
echo "docker $*" >>"$STUB_CALLS"
keydir() {{ echo "$STUB_RUN_BASE/$1/run-user/agent-secrets"; }}
case "$*" in
    inspect*)
        [[ -z "${{STUB_GONE:-}}" ]] || exit 1
        if [[ -n "${{STUB_RUNNING_AFTER:-}}" ]]; then
            (( $(grep -c '^docker inspect' "$STUB_CALLS") >= STUB_RUNNING_AFTER )) && echo true || echo false
        else
            echo "${{STUB_RUNNING:-true}}"
        fi ;;
    "run "*)
        while [[ "$1" != --name ]]; do shift; done
        if [[ -e "$(keydir "$2")/enrollment.pending" ]]; then m=yes; else m=no; fi
        echo "run $2 marker=$m" >>"$STUB_CALLS"
        /bin/sleep 1 ;;
    "exec -d "*)
        [[ -z "${{STUB_RUN_RENEW:-}}" ]] || AGENT_SECRETS_KEY_DIR="$(keydir "$5")" sh -c "$8" ;;
    *" keygen "*)
        case "${{STUB_KEYGEN:-ok}}" in
            ok) : >"$(keydir "$4")/key.pem"; echo "{THUMBPRINT}" ;;
            fail) echo "keygen: no /run/user dir" >&2; exit 1 ;;
            empty) ;;
        esac ;;
    *"pgrep -x omp"*)
        echo "sid-probe marker-mtime=$(stat -c %Y "$(keydir "$2")/enrollment.pending" 2>/dev/null || echo none)" >>"$STUB_CALLS"
        echo "${{STUB_SID:-}}" ;;
esac
"""

# keygen writes a key to --out and prints its thumbprint, as the host's session keygen and the
# in-box one both do. enroll records the marker's mtime as it saw it, then prints the enrollment
# id and writes it into the key dir, as the real client does. renew fails at once, and ends its
# loop on the third run.
AGENT_SECRETS_STUB = r"""
case "$1" in
    machine)
        [[ "${STUB_LOGIN_STATUS:-0}" == 0 ]] && { echo issued; exit 0; }
        # As the legion-envoy-v9.0.0 client: the state on stdout, then a remedy on stderr.
        echo none
        echo "agent-secrets machine login-status: no machine login has run on this helper; run: agent-secrets machine login" >&2
        exit 1 ;;
    keygen)
        out=""
        while (( $# )); do [[ "$1" == --out ]] && out="$2"; shift; done
        echo "keygen $out" >>"$STUB_CALLS"
        case "${STUB_KEYGEN:-ok}" in
            ok) (umask 077; printf 'KEY %s\n' "${STUB_KEY:-k1}" >"$out/key.pem"); echo "$STUB_THUMBPRINT" ;;
            fail) echo "keygen: no /run/user dir" >&2; exit 1 ;;
            empty) ;;
        esac ;;
    enroll)
        echo "enroll $* marker-mtime=$(stat -c %Y "$AGENT_SECRETS_KEY_DIR/enrollment.pending" 2>/dev/null || echo none)" >>"$STUB_CALLS"
        case "${STUB_ENROLL:-ok}" in
            ok) echo enr-123 >"$AGENT_SECRETS_KEY_DIR/enrollment"; echo enr-123 ;;
            fail) echo "broker unreachable" >&2; exit 1 ;;
            nofile) echo enr-123 ;;
            idempotent)
                # As the broker: an enrollment is keyed by (runtime id, key), so the same pair
                # gets the same id back and any other pair gets its own.
                rt="" tp=""
                while (( $# )); do
                    [[ "$1" == --runtime-id ]] && rt="$2"
                    [[ "$1" == --thumbprint ]] && tp="$2"
                    shift
                done
                id="enr-$(printf '%s|%s' "$rt" "$tp" | cksum | cut -d' ' -f1)"
                echo "$id" >"$AGENT_SECRETS_KEY_DIR/enrollment"; echo "$id" ;;
            conflict)
                # A live enrollment of this session under a key this host no longer has.
                rt=""
                while (( $# )); do [[ "$1" == --runtime-id ]] && rt="$2"; shift; done
                case "$rt" in
                    session-*) echo "409 ALREADY_ENROLLED this runtime is already enrolled and live" >&2; exit 1 ;;
                    *) echo enr-box >"$AGENT_SECRETS_KEY_DIR/enrollment"; echo enr-box ;;
                esac ;;
        esac ;;
    unenroll) echo "unenroll $*" >>"$STUB_CALLS" ;;
    renew)
        # Fails at once; ends its loop on run $STUB_RENEW_RUNS (3). Run $STUB_LONG_RUN instead
        # moves the fake clock ($STUB_CLOCK, read by the date stub) on by two minutes.
        echo "renew" >>"$STUB_CALLS"
        echo "agent-secrets renew: 401 PROOF_INVALID"
        n=$(grep -c '^renew$' "$STUB_CALLS")
        [[ "$n" != "${STUB_LONG_RUN:-}" ]] || echo $(( $(<"$STUB_CLOCK") + 120 )) >"$STUB_CLOCK"
        (( n < ${STUB_RENEW_RUNS:-3} )) || kill "$PPID"
        exit 1 ;;
    *) echo "unexpected agent-secrets $*" >&2; exit 99 ;;
esac
"""


class EnrollFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.home = self.root / "home"
        (self.home / ".config" / "agent-secrets").mkdir(parents=True)
        self.stub_dir = self.root / "bin"
        self.stub_dir.mkdir()
        self.calls = self.root / "calls"
        # Each sleep ages the marker past the client's trust window, as the real 3 s pauses add up
        # against a slow broker: only a marker renewed before every attempt stays fresh.
        write_stub(
            self.stub_dir,
            "sleep",
            'echo "sleep $*" >>"$STUB_CALLS"\n'
            '[[ -e "$STUB_KEYDIR/enrollment.pending" ]] && touch -h -d "-1000 seconds" "$STUB_KEYDIR/enrollment.pending"\n'
            "exit 0",
        )
        write_stub(self.stub_dir, "docker", DOCKER_STUB)
        write_stub(self.stub_dir, "agent-secrets", AGENT_SECRETS_STUB)
        write_stub(self.stub_dir, "git", "exit 0")
        write_stub(
            self.stub_dir, "date", '[[ -z "${STUB_CLOCK:-}" ]] || exec cat "$STUB_CLOCK"\nexec /bin/date "$@"'
        )
        self.run_base = self.root / "run"
        self.hostdir = self.run_base / "box1"
        self.keydir = self.hostdir / "run-user" / "agent-secrets"
        self.keydir.mkdir(parents=True)
        # A marker as `new` leaves it, already older than the client's 160 s trust window.
        marker = self.keydir / "enrollment.pending"
        marker.write_text("")
        old = time.time() - 1000
        os.utime(marker, (old, old))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def bash(self, body: str, **stubs: str) -> subprocess.CompletedProcess[str]:
        script = f'source "{AGENTBOX}"\nRUN_BASE="{self.run_base}"\n{body}\n'
        env = {
            "HOME": str(self.home),
            "PATH": f"{self.stub_dir}:/usr/bin:/bin",
            "DOTFILES_DIR": str(DOTFILES),
            "AGENTBOX_SRC": str(self.root / "src"),
            # This suite is about enrollment, not the limits: the box tree goes in the temp
            # directory and the box gets no filesystem of its own (scripts/test_agentbox_limits.py
            # covers that one).
            "AGENTBOX_BOXES": str(self.root / "boxes"),
            "AGENTBOX_DISK": "0",
            "STUB_CALLS": str(self.calls),
            "STUB_KEYDIR": str(self.keydir),
            "STUB_RUN_BASE": str(self.run_base),
            "STUB_THUMBPRINT": THUMBPRINT,
            **stubs,
        }
        # 300 s, not 60: the slow-box test below deliberately runs 400 passes of the wait, each
        # forking a stub `docker`, and on this host at load 300-550 that alone takes over a
        # minute (it timed out at 60 s on 2026-11-01, on HEAD as well as on the change under
        # test). Every other case here finishes in seconds, so the larger cap only bites a hang.
        return subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, env=env, check=False, timeout=300
        )

    def enroll(self, **stubs: str) -> subprocess.CompletedProcess[str]:
        return self.bash('if enroll_box box1 sid-1; then echo rc=0; else echo "rc=$?"; fi', **stubs)

    def files(self) -> set[str]:
        return {p.name for p in self.keydir.iterdir()}

    def calls_matching(self, prefix: str) -> list[str]:
        if not self.calls.exists():
            return []
        return [line for line in self.calls.read_text().splitlines() if line.startswith(prefix)]

    def error(self) -> str:
        return (self.keydir / "enrollment.error").read_text()

    def assert_no_identity(self) -> None:
        self.assertEqual(self.files(), {"enrollment.error"})
        self.assertFalse((self.hostdir / "enrollment-id").exists())


class AgentboxEnroll(EnrollFixture):
    """enroll_box, unenroll_box and broker_ready on their own."""

    def test_enrolled_leaves_the_key_and_the_enrollment(self) -> None:
        result = self.enroll()
        self.assertIn("rc=0", result.stdout, result.stderr)
        self.assertEqual(self.files(), {"key.pem", "enrollment"})
        self.assertEqual((self.hostdir / "enrollment-id").read_text(), "enr-123\n")
        (enroll,) = self.calls_matching("enroll ")
        self.assertIn(f"--thumbprint {THUMBPRINT}", enroll)
        self.assertIn("--session-id sid-1", enroll)
        self.assertNotIn("--kind", enroll)
        # The enrollment is the SESSION's, so the broker keys it by the session and not by the
        # box, and the key it holds was made on the host, never inside the box.
        self.assertIn("--runtime-id session-sid-1", enroll)
        self.assertEqual((self.hostdir / "enrollment-session").read_text(), "sid-1\n")
        self.assertEqual([c for c in self.calls_matching("docker ") if " keygen " in c], [])
        session = self.run_base / "session-keys" / "sid-1"
        self.assertEqual((session / "key.pem").read_text(), (self.keydir / "key.pem").read_text())
        self.assertEqual((session / "owner").read_text(), "box1\n")
        self.assertEqual(oct((session / "key.pem").stat().st_mode & 0o777), "0o600")
        self.assertEqual(oct(session.stat().st_mode & 0o777), "0o700")

    def test_gives_up_after_ten_attempts_with_the_marker_fresh_throughout(self) -> None:
        started = time.time()
        result = self.enroll(STUB_ENROLL="fail")
        self.assertIn("rc=1", result.stdout, result.stderr)
        self.assert_no_identity()
        self.assertIn("broker unreachable", self.error())
        attempts = self.calls_matching("enroll ")
        self.assertEqual(len(attempts), 10)
        self.assertEqual(len(self.calls_matching("sleep")), 9)
        for attempt in attempts:
            mtime = int(attempt.rsplit("marker-mtime=", 1)[1])
            self.assertGreaterEqual(mtime, int(started) - 1, attempt)

    def test_keygen_failure(self) -> None:
        result = self.enroll(STUB_KEYGEN="fail")
        self.assertIn("rc=1", result.stdout, result.stderr)
        self.assert_no_identity()
        self.assertIn("no /run/user dir", self.error())
        self.assertEqual(self.calls_matching("enroll "), [])

    def test_keygen_without_thumbprint(self) -> None:
        result = self.enroll(STUB_KEYGEN="empty")
        self.assertIn("rc=1", result.stdout, result.stderr)
        self.assert_no_identity()
        self.assertEqual(self.calls_matching("enroll "), [])

    def test_enroll_that_writes_no_enrollment(self) -> None:
        result = self.enroll(STUB_ENROLL="nofile")
        self.assertIn("rc=1", result.stdout, result.stderr)
        self.assert_no_identity()

    def test_no_launcher_credential_never_touches_the_box(self) -> None:
        result = self.enroll(STUB_LOGIN_STATUS="1")
        self.assertIn("rc=1", result.stdout, result.stderr)
        self.assert_no_identity()
        self.assertIn("login-status: none", self.error())
        self.assertEqual(self.calls_matching("docker "), [])

    def test_a_link_the_box_planted_is_replaced_not_followed(self) -> None:
        outside = self.root / "host-file"
        outside.write_text("host content\n")
        (self.keydir / "enrollment.error").symlink_to(outside)
        (self.keydir / "enrollment.pending").unlink()
        (self.keydir / "enrollment.pending").symlink_to(outside)
        result = self.enroll(STUB_ENROLL="fail")
        self.assertIn("rc=1", result.stdout, result.stderr)
        self.assertEqual(outside.read_text(), "host content\n")
        self.assertFalse((self.keydir / "enrollment.error").is_symlink())
        self.assertIn("broker unreachable", self.error())

    def test_unenroll_revokes_the_recorded_id_never_the_box_copy(self) -> None:
        (self.keydir / "enrollment").write_text("enr-other-box\n")
        self.bash("unenroll_box box1")
        self.assertEqual(self.calls_matching("unenroll "), [])
        (self.hostdir / "enrollment-id").write_text("enr-123\n")
        self.bash("unenroll_box box1")
        (call,) = self.calls_matching("unenroll ")
        self.assertIn("--enrollment enr-123", call)

    def test_close_box_revokes_the_enrollment(self) -> None:
        (self.hostdir / "enrollment-id").write_text("enr-123\n")
        (self.home / "boxes" / "box1").mkdir(parents=True)
        self.bash('close_box box1 ""', STUB_GONE="1")
        (call,) = self.calls_matching("unenroll ")
        self.assertIn("--enrollment enr-123", call)
        self.assertFalse(self.hostdir.exists())

    def test_close_box_drops_the_launchers_pane_from_the_attention_queue(self) -> None:
        # The box's own shutdown drop dies with its container; the launcher runs in the same
        # host pane, so it drops that pane once the container is gone.
        attention = self.root / "attention"
        line = '{"pane":"%7","server":"4242","session":"s","title":"t","cwd":"/w","at":"2026-01-01T00:00:00Z"}'
        env = {"OMP_ATTENTION_DIR": str(attention), "TMUX": "/tmp/tmux-test/default,4242,0", "PATH": "/usr/bin:/bin"}
        subprocess.run(
            [str(DOTFILES / "scripts" / "tmux-attention"), "push", line, "1000"], env=env, check=True, capture_output=True
        )
        (self.home / "boxes" / "box1").mkdir(parents=True)
        self.bash('close_box box1 ""', STUB_GONE="1", TMUX_PANE="%7", TMUX=env["TMUX"], OMP_ATTENTION_DIR=env["OMP_ATTENTION_DIR"])
        self.assertEqual((attention / "queue.jsonl").read_text(), "")

    def test_a_renew_that_exits_is_started_again(self) -> None:
        # The stub ends the loop by killing it on the third renew, so the run ends by signal.
        self.bash("start_renew box1", STUB_RUN_RENEW="1")
        self.assertEqual(len(self.calls_matching("renew")), 3)
        self.assertEqual((self.keydir / "renew.log").read_text().count("401 PROOF_INVALID"), 3)

    def renew_delays(self, **stubs: str) -> list[int]:
        clock = self.root / "clock"
        clock.write_text("1000\n")
        self.bash("start_renew box1", STUB_RUN_RENEW="1", STUB_CLOCK=str(clock), **stubs)
        return [int(c.split()[1]) for c in self.calls_matching("sleep ")]

    def test_a_renew_that_keeps_failing_is_restarted_with_growing_delays(self) -> None:
        self.assertEqual(
            self.renew_delays(STUB_RENEW_RUNS="9"), [10, 20, 40, 80, 160, 320, 600, 600]
        )

    def test_a_renew_that_ran_a_while_restarts_the_delays(self) -> None:
        self.assertEqual(self.renew_delays(STUB_RENEW_RUNS="5", STUB_LONG_RUN="3"), [10, 20, 10, 20])

    def test_broker_ready_names_the_fix(self) -> None:
        cases = {
            "client not installed": ({"PATH": "/usr/bin:/bin"}, "mise install agent-secrets"),
            "no credential": ({"STUB_LOGIN_STATUS": "1"}, "login-status: none"),
        }
        for name, (env, reason) in cases.items():
            with self.subTest(case=name):
                result = self.bash(
                    'if broker_ready; then echo ready; else echo "unready: $BROKER_UNREADY"; fi',
                    **env,
                )
                self.assertIn("unready:", result.stdout, result.stderr)
                self.assertIn(reason, result.stdout)


class SessionIdentity(EnrollFixture):
    """A session's broker identity follows the session, not the box it is running in.

    A pending credential request is bound to the enrollment id alone, and ending an enrollment
    cancels every request still pending under it and revokes its grants (legion
    packages/envoy/internal/broker/enroll/enroll.go endEnrollment). So a key made inside the box
    and an enrollment keyed by the box's name meant `agentbox move` orphaned every card Sami had
    not answered and threw away every grant he had given (dispatch://INFRA-1167).
    """

    def second_box(self, name: str = "box2") -> Path:
        """A box dir as mount_args makes one, for the box the session moves into."""
        keydir = self.run_base / name / "run-user" / "agent-secrets"
        keydir.mkdir(parents=True)
        (keydir / "enrollment.pending").write_text("")
        return keydir

    def test_the_same_session_in_a_new_box_gets_the_same_enrollment(self) -> None:
        two = self.second_box()
        # STUB_KEY and STUB_THUMBPRINT change between the boxes: a key made for the second box
        # would be a different one, and the test would see it.
        first = self.bash("enroll_box box1 sid-1", STUB_ENROLL="idempotent")
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self.bash(
            "enroll_box box2 sid-1", STUB_ENROLL="idempotent", STUB_KEY="k2", STUB_THUMBPRINT="B" * 43
        )
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(
            (self.run_base / "box2" / "enrollment-id").read_text(),
            (self.run_base / "box1" / "enrollment-id").read_text(),
        )
        self.assertEqual(two.joinpath("key.pem").read_text(), (self.keydir / "key.pem").read_text())
        for call in self.calls_matching("enroll "):
            self.assertIn("--runtime-id session-sid-1", call)
            self.assertIn(f"--thumbprint {THUMBPRINT}", call)
        # One keygen for the session, at its first box; the second box reuses the host's key.
        self.assertEqual(len(self.calls_matching("keygen ")), 1)
        self.assertEqual((self.run_base / "session-keys" / "sid-1" / "owner").read_text(), "box2\n")

    def test_a_different_session_gets_an_enrollment_of_its_own(self) -> None:
        self.second_box()
        self.bash("enroll_box box1 sid-1", STUB_ENROLL="idempotent")
        self.bash("enroll_box box2 sid-2", STUB_ENROLL="idempotent", STUB_KEY="k2", STUB_THUMBPRINT="B" * 43)
        self.assertNotEqual(
            (self.run_base / "box2" / "enrollment-id").read_text(),
            (self.run_base / "box1" / "enrollment-id").read_text(),
        )

    def test_a_box_with_no_session_still_makes_its_key_inside_the_box(self) -> None:
        """A `--` command that is not omp, `doctor`, an `omp --no-session` run."""
        result = self.bash('enroll_box box1 ""')
        self.assertEqual(result.returncode, 0, result.stderr)
        (enroll,) = self.calls_matching("enroll ")
        self.assertIn("--runtime-id box1", enroll)
        self.assertNotIn("--session-id", enroll)
        self.assertEqual(len([c for c in self.calls_matching("docker ") if " keygen " in c]), 1)
        self.assertFalse((self.run_base / "session-keys").exists())
        self.assertFalse((self.hostdir / "enrollment-session").exists())

    def test_a_live_enrollment_under_a_key_this_host_lost_falls_back_to_the_box(self) -> None:
        """After a reboot the host's key dir is gone while the old lease runs on. The box takes
        an identity of its own rather than waiting out the 409 for fifteen minutes."""
        result = self.bash("enroll_box box1 sid-1", STUB_ENROLL="conflict")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("enrolls under its own name", result.stderr)
        runtimes = [c.split("--runtime-id ")[1].split()[0] for c in self.calls_matching("enroll ")]
        self.assertEqual(runtimes, ["session-sid-1", "box1"])
        self.assertEqual((self.hostdir / "enrollment-id").read_text(), "enr-box\n")
        self.assertFalse((self.hostdir / "enrollment-session").exists())

    def unenroll_calls(self, deadline: float = 20.0) -> list[str]:
        """The waiter unenroll_box leaves behind is detached; wait for it to have run."""
        log = self.run_base / "session-keys" / "unenroll.log"
        end = time.time() + deadline
        while time.time() < end:
            if log.exists() and log.read_text().strip():
                break
            time.sleep(0.2)
        return self.calls_matching("unenroll ")

    def test_a_session_that_came_back_in_another_box_keeps_its_enrollment(self) -> None:
        self.second_box()
        self.bash("enroll_box box1 sid-1", STUB_ENROLL="idempotent")
        self.bash("enroll_box box2 sid-1", STUB_ENROLL="idempotent")
        # box1 closing after the move must not cancel what the session has pending.
        self.bash("unenroll_box box1", AGENTBOX_UNENROLL_GRACE="0")
        self.assertEqual(self.unenroll_calls(), [])
        self.assertIn("left alone", (self.run_base / "session-keys" / "unenroll.log").read_text())
        self.assertTrue((self.run_base / "session-keys" / "sid-1" / "key.pem").exists())

    def test_a_session_that_did_not_come_back_ends_its_enrollment_and_its_key(self) -> None:
        self.bash("enroll_box box1 sid-1", STUB_ENROLL="idempotent")
        self.bash("unenroll_box box1", AGENTBOX_UNENROLL_GRACE="0")
        (call,) = self.unenroll_calls()
        self.assertIn("--enrollment ", call)
        self.assertFalse((self.run_base / "session-keys" / "sid-1").exists())


class EnrollWhenUp(EnrollFixture):
    """`agentbox new`'s enrollment of a box that is starting, against a real child process."""

    def when_up(
        self, cmd: str, child: str = "/bin/sleep 30 &", **stubs: str
    ) -> subprocess.CompletedProcess[str]:
        return self.bash(
            f"{child}\nchild=$!\n"
            f'if enroll_when_up box1 "$child" {cmd}; then echo rc=0; else echo "rc=$?"; fi\n'
            'kill "$child" 2>/dev/null || true',
            **stubs,
        )

    def test_any_other_command_enrolls_at_once_and_renews(self) -> None:
        result = self.when_up("bash")
        self.assertIn("rc=0", result.stdout, result.stderr)
        self.assertEqual(self.files(), {"key.pem", "enrollment"})
        (enroll,) = self.calls_matching("enroll ")
        self.assertNotIn("--session-id", enroll)
        # One detached exec, the renew loop (its multi-line script follows on the logged line).
        self.assertEqual(len(self.calls_matching("docker exec -d ")), 1)

    def test_omp_enrolls_with_its_session_id(self) -> None:
        result = self.when_up("omp", STUB_SID="0192f3a4-5b6c-7d8e-9f01-23456789abcd")
        self.assertIn("rc=0", result.stdout, result.stderr)
        (enroll,) = self.calls_matching("enroll ")
        self.assertIn("--session-id 0192f3a4-5b6c-7d8e-9f01-23456789abcd", enroll)

    def test_a_box_that_never_runs_ends_without_identity(self) -> None:
        result = self.when_up("omp", child="/bin/true &\nwait", STUB_RUNNING="false")
        self.assertIn("rc=1", result.stdout, result.stderr)
        self.assert_no_identity()
        self.assertEqual(self.calls_matching("enroll "), [])

    def test_a_box_that_ends_first_ends_without_identity(self) -> None:
        result = self.when_up("omp", child="/bin/sleep 1 &")
        self.assertIn("rc=1", result.stdout, result.stderr)
        self.assert_no_identity()
        self.assertEqual(self.calls_matching("enroll "), [])

    def test_a_box_slow_to_reach_running_still_enrolls_with_its_session_id(self) -> None:
        """A box still starting after hundreds of checks is waited for while its launcher lives.

        The launcher stands in for one that lives longer than the wait: 400 passes of the loop
        fork a stub `docker` each, which on a loaded host takes longer than 30 s, and a child
        that ends first makes the launcher give up instead (the test below that one)."""
        result = self.when_up(
            "omp",
            child="/bin/sleep 300 &",
            STUB_RUNNING_AFTER="400",
            STUB_SID="0192f3a4-5b6c-7d8e-9f01-23456789abcd",
        )
        self.assertIn("rc=0", result.stdout, result.stderr)
        self.assertEqual(self.files(), {"key.pem", "enrollment"})
        self.assertGreaterEqual(len(self.calls_matching("docker inspect")), 400)
        (enroll,) = self.calls_matching("enroll ")
        self.assertIn("--session-id 0192f3a4-5b6c-7d8e-9f01-23456789abcd", enroll)

    def test_the_marker_stays_fresh_while_it_waits(self) -> None:
        """Each pass of the wait renews the marker; each sleep ages it past 160 s."""
        started = time.time()
        self.when_up("omp", child="/bin/sleep 1 &")
        probes = self.calls_matching("sid-probe ")
        self.assertGreater(len(probes), 1)
        for probe in probes:
            self.assertGreaterEqual(int(probe.rsplit("=", 1)[1]), int(started) - 1, probe)


class CmdNew(EnrollFixture):
    """cmd_new gives a box a marker and an enrollment only when the host can enroll it."""

    NO_HOST = (
        "build_image() { :; }; ensure_network() { :; }; ensure_forwarders() { :; }\n"
        "check_box_disk_space() { :; };\n"
        'mount_args() { mkdir -p "$(box_keydir "$1")"; }\n'
        "session_env() { :; }; clone_checkout() { :; }\n"
        'close_box() { echo "close_box $1" >>"$STUB_CALLS"; }\n'
        "cmd_new repo1 -- bash -c true"
    )

    def new_box(self, **stubs: str) -> Path:
        (self.root / "src" / "repo1" / ".jj").mkdir(parents=True)
        result = self.bash(self.NO_HOST, **stubs)
        self.assertEqual(result.returncode, 0, result.stderr)
        (box,) = self.run_base.glob("agentbox-*")
        return box / "run-user" / "agent-secrets"

    def test_a_host_that_can_enroll_marks_and_enrolls_the_box(self) -> None:
        keydir = self.new_box(STUB_LOGIN_STATUS="0")
        (started,) = self.calls_matching("run ")
        self.assertTrue(started.endswith("marker=yes"), started)
        self.assertEqual({p.name for p in keydir.iterdir()}, {"key.pem", "enrollment"})
        self.assertEqual(len(self.calls_matching("enroll ")), 1)

    def test_a_host_that_cannot_enroll_leaves_the_box_alone(self) -> None:
        keydir = self.new_box(STUB_LOGIN_STATUS="1")
        (started,) = self.calls_matching("run ")
        self.assertTrue(started.endswith("marker=no"), started)
        self.assertEqual(list(keydir.iterdir()), [])
        self.assertEqual(self.calls_matching("enroll "), [])
        self.assertEqual([c for c in self.calls_matching("docker ") if " keygen " in c], [])


if __name__ == "__main__":
    unittest.main()
