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
cmd_new runs with the steps that need a real host (image, network, mounts) replaced by no-ops.
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
# box starts, then lives for a second.
DOCKER_STUB = rf"""
echo "docker $*" >>"$STUB_CALLS"
keydir() {{ echo "$STUB_RUN_BASE/$1/run-user/agent-secrets"; }}
case "$*" in
    inspect*) [[ -z "${{STUB_GONE:-}}" ]] || exit 1; echo "${{STUB_RUNNING:-true}}" ;;
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

# enroll records the marker's mtime as it saw it, then prints the enrollment id and writes it into
# the key dir, as the real client does. renew fails at once, and ends its loop on the third run.
AGENT_SECRETS_STUB = r"""
case "$1" in
    launcher)
        [[ "${STUB_LOGIN_STATUS:-0}" == 0 ]] && { echo issued; exit 0; }
        # As the legion #1589 client: the state on stdout, then a remedy on stderr.
        echo none
        echo "agent-secrets launcher login-status: no machine login has run on this helper; run: agent-secrets launcher login" >&2
        exit 1 ;;
    enroll)
        echo "enroll $* marker-mtime=$(stat -c %Y "$AGENT_SECRETS_KEY_DIR/enrollment.pending" 2>/dev/null || echo none)" >>"$STUB_CALLS"
        case "${STUB_ENROLL:-ok}" in
            ok) echo enr-123 >"$AGENT_SECRETS_KEY_DIR/enrollment"; echo enr-123 ;;
            fail) echo "broker unreachable" >&2; exit 1 ;;
            nofile) echo enr-123 ;;
        esac ;;
    unenroll) echo "unenroll $*" >>"$STUB_CALLS" ;;
    renew)
        echo "renew" >>"$STUB_CALLS"
        echo "agent-secrets renew: 401 PROOF_INVALID"
        (( $(grep -c '^renew$' "$STUB_CALLS") < 3 )) || kill "$PPID"
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
            'echo sleep >>"$STUB_CALLS"\n'
            '[[ -e "$STUB_KEYDIR/enrollment.pending" ]] && touch -h -d "-1000 seconds" "$STUB_KEYDIR/enrollment.pending"\n'
            "exit 0",
        )
        write_stub(self.stub_dir, "docker", DOCKER_STUB)
        write_stub(self.stub_dir, "agent-secrets", AGENT_SECRETS_STUB)
        write_stub(self.stub_dir, "git", "exit 0")
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
            "STUB_CALLS": str(self.calls),
            "STUB_KEYDIR": str(self.keydir),
            "STUB_RUN_BASE": str(self.run_base),
            **stubs,
        }
        return subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, env=env, check=False, timeout=60
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

    def test_a_renew_that_exits_is_started_again(self) -> None:
        # The stub ends the loop by killing it on the third renew, so the run ends by signal.
        self.bash("start_renew box1", STUB_RUN_RENEW="1")
        self.assertEqual(len(self.calls_matching("renew")), 3)
        self.assertEqual((self.keydir / "renew.log").read_text().count("401 PROOF_INVALID"), 3)

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
        renews = [c for c in self.calls_matching("docker exec -d ") if "renew" in c]
        self.assertEqual(len(renews), 1)

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
        'mount_args() { mkdir -p "$(box_keydir "$1")"; }\n'
        "session_env() { :; }; detached() { :; }\n"
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
