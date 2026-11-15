#!/usr/bin/env python3
"""scripts/agentbox's box enrollment: every way it ends, as the box's key dir shows it.

`agentbox new` puts enrollment.pending in a box's key dir before `docker run`, and from the legion
#1589 release the agent-secrets client in the box waits on that marker while it is fresh.
Enrollment must end it every time: enrolled (key.pem and enrollment, no marker, no error) or not
(enrollment.error, no key, no marker: the box has no broker identity and uses secretsd). The
marker stays fresh while the launcher still works; what the host writes into the box-writable key
dir never follows a link the box planted there, in the key dir or at the key dir's own name in the
box's runtime dir; the id the host revokes is the one it recorded.
An enrolled box whose renew is refused as not live is enrolled again by its launcher with the key
it holds, and a box whose enrollment is live is not. An enroll call whose outcome is not known can
have made an enrollment the host has no id for: the close learns that id from the broker and
revokes it, in a detached process.

Technique: source scripts/agentbox (its source guard runs nothing) with `docker`, `git`,
`agent-secrets` and `sleep` stubbed first on PATH, and call its functions on a scratch key dir;
cmd_new runs with the steps that need a real host (image, network, clones, mounts) replaced by
no-ops.
"""

from __future__ import annotations

import contextlib
import functools
import os
import re
import shutil
import signal
import socket
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from collections.abc import Callable, Iterator
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
AGENTBOX = DOTFILES / "scripts" / "agentbox"
THUMBPRINT = "A" * 43


@functools.cache
def keydir_in_box_dir() -> str:
    """Where scripts/agentbox puts a box's key dir, relative to the box's run dir ($RUN_BASE/<box>):
    box_keydir's own answer, so every fixture here has the layout of the script under test. From
    dispatch://INFRA-3462 on that is `agent-secrets`, beside the box's run-user/ and not in it;
    test_agentbox_dirs.py and test_agentbox_run_user_mode.py hold the layout itself."""
    done = subprocess.run(
        ["bash", "-c", f'source "{AGENTBOX}"\nRUN_BASE=/run-base\nbox_keydir a-box'],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
        env={"HOME": "/nonexistent", "PATH": "/usr/bin:/bin", "DOTFILES_DIR": str(DOTFILES)},
    )
    return str(Path(done.stdout.strip()).relative_to("/run-base/a-box"))


def write_stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def wait_until(test: Callable[[], bool], seconds: float) -> None:
    """Return once `test` answers true, asked every 0.05 s, or after `seconds` whatever it answers."""
    end = time.monotonic() + seconds
    while not test() and time.monotonic() < end:
        time.sleep(0.05)


# A box's key dir is where box_keydir puts it, $STUB_RUN_BASE/<box>/$STUB_KEYDIR_REL. `docker run`
# notes whether the marker is there as the box starts, then lives for a second; with
# STUB_RUN_UNTIL set it lives until the box's renew.log holds that text (60 s at most), then ends
# the box's renew loop, as the box's end does, and says so (`run-ended <box>`). With
# STUB_RUN_END_MARKER set too, the box's end first writes a fresh enrollment.not-live in its key
# dir, which only a launcher pass made after the box ended could act on. `docker exec -d` runs its
# script here when STUB_RUN_RENEW is set: in the foreground, or with `bg` in the background as
# docker leaves it, holding none of the test's pipes, with its pid kept for the box's end and,
# when STUB_RENEW_AFTER is set, only once that line is in the call log. Any other `docker exec
# --user ubuntu <box> sh -c` runs here too, as a process whose key dir is the box's. `inspect`
# answers false until its STUB_RUNNING_AFTER'th call when that is set, as a box does while it is
# still starting. keygen writes a key of its own each time, and a thumbprint of its own after the
# first. `ps -a --filter name=^<box>$` names the box while its name is in $STUB_LIVE_BOXES. With
# STUB_PS_FAIL set, `ps` exits 1 with no output, as when docker cannot be asked.
DOCKER_STUB = rf"""
echo "docker $*" >>"$STUB_CALLS"
keydir() {{ echo "$STUB_RUN_BASE/$1/$STUB_KEYDIR_REL"; }}
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
        if [[ -z "${{STUB_RUN_UNTIL:-}}" ]]; then /bin/sleep 1; exit 0; fi
        for _ in $(seq 600); do
            grep -qF "$STUB_RUN_UNTIL" "$(keydir "$2")/renew.log" 2>/dev/null && break
            /bin/sleep 0.1
        done
        # Only while that pid is still the loop: it may have ended, and the pid gone to another.
        pid="$(cat "$STUB_RUN_BASE/$2/renew-loop.pid" 2>/dev/null)"
        [[ -z "$pid" ]] || ! grep -qsa 'agent-secrets renew' "/proc/$pid/cmdline" || kill "$pid" 2>/dev/null
        [[ -z "${{STUB_RUN_END_MARKER:-}}" ]] || : >"$(keydir "$2")/enrollment.not-live"
        echo "run-ended $2" >>"$STUB_CALLS" ;;
    "exec -d "*)
        case "${{STUB_RUN_RENEW:-}}" in
            "") ;;
            bg)
                (
                    if [[ -n "${{STUB_RENEW_AFTER:-}}" ]]; then
                        for _ in $(seq 300); do
                            grep -qxF "$STUB_RENEW_AFTER" "$STUB_CALLS" && break
                            /bin/sleep 0.1
                        done
                    fi
                    AGENT_SECRETS_KEY_DIR="$(keydir "$5")" exec sh -c "$8"
                ) </dev/null >/dev/null 2>&1 &
                echo "$!" >"$STUB_RUN_BASE/$5/renew-loop.pid" ;;
            *) AGENT_SECRETS_KEY_DIR="$(keydir "$5")" sh -c "$8" ;;
        esac ;;
    "exec --user ubuntu "*" sh -c "*)
        box="$4"
        shift 4
        AGENT_SECRETS_KEY_DIR="$(keydir "$box")" "$@" ;;
    *" keygen "*)
        n=$(grep -c '^docker exec .* agent-secrets keygen ' "$STUB_CALLS")
        case "${{STUB_KEYGEN:-ok}}" in
            ok)
                printf 'BOXKEY %s\n' "$n" >"$(keydir "$4")/key.pem"
                if (( n == 1 )); then echo "{THUMBPRINT}"; else printf 'K%042d\n' "$n"; fi ;;
            fail) echo "keygen: no /run/user dir" >&2; exit 1 ;;
            empty) ;;
        esac ;;
    *"pgrep -x omp"*)
        echo "sid-probe marker-mtime=$(stat -c %Y "$(keydir "$2")/enrollment.pending" 2>/dev/null || echo none)" >>"$STUB_CALLS"
        echo "${{STUB_SID:-}}" ;;
    "ps "*)
        [[ -z "${{STUB_PS_FAIL:-}}" ]] || exit 1
        name="${{4#name=^}}"
        name="${{name%\$}}"
        [[ " ${{STUB_LIVE_BOXES:-}} " != *" $name "* ]] || echo "$name" ;;
esac
"""

# keygen writes a key to --out and prints its thumbprint, as the host's session keygen and the
# in-box one both do. enroll logs its arguments, its key dir and the marker's mtime as it saw it.
# A box's enroll call n then takes the nth word of $STUB_ENROLL (the last word repeats):
#   ok        prints enr-123 and writes it into the key dir, as the real client does
#   fail      "broker unreachable", exit 1
#   known     the pinned client's ENROLL_FAILED line, exit 1
#   draining  its DRAINING line, exit 1
#   knownsecond  a warning line, then the ENROLL_FAILED line, exit 1
#   conflict  a 409 refusal for a session's runtime id, enr-box for any other
#   broker    the broker's answer (broker_enroll), as the real client prints and writes it
# and nine words for a call whose outcome the client cannot tell, each of which makes the
# enrollment at the broker as `broker` does: nofile (exit 0 with the id, and no enrollment file),
# unknown (the pinned client's ENROLL_OUTCOME_UNKNOWN line), noreply (its line for a helper that
# gave no answer), oldclient (its line for an old helper's empty reply), unwritable (its line for
# an id it could not write), buried (a line for a helper that gave no answer, with the
# ENROLL_FAILED line inside its parentheses), indented (a space, then the ENROLL_FAILED line) and
# warnfirst (a warning line, then the ENROLL_OUTCOME_UNKNOWN line), each exit 1, and signal (a
# SIGHUP to itself: no line, exit 129).
# A learn call (unenroll_learn: no --session-id, and a key dir under $STUB_RUN_BASE/learn.) takes
# the words of $STUB_LEARN instead (broker by default), and with STUB_LEARN_AFTER set it answers
# only once that file exists (120 s at most). With STUB_HUP_PID set, a box's enroll call first
# sends that process a hangup, as a pane kill can during the call. unenroll call n takes the nth
# word of $STUB_UNENROLL (the last word repeats): ok (the default: the broker ends the
# enrollment), known (the pinned client's UNENROLL_FAILED line) or unknown (its
# UNENROLL_OUTCOME_UNKNOWN line), each exit 1 with nothing ended. renew exits as the pinned
# client's does, and ends its loop on the third run.
AGENT_SECRETS_STUB = r"""
# end_enrollment ID: enrollment ID is no longer live at the broker.
end_enrollment() {
    [[ -n "$1" && -e "$STUB_BROKER" ]] || return 0
    sed -i "s/^$1 \(.*\) live\$/$1 \1 ended/" "$STUB_BROKER"
}
# broker_enroll RUNTIME THUMBPRINT: the broker's answer to an enroll call (enroll.go
# recoverConflict), from a table of its enrollments in $STUB_BROKER, one row per enrollment:
# <id> <runtime id> <thumbprint> live|ended. Per runtime id, the same thumbprint gets the same id
# back while that enrollment is live, any other thumbprint is refused 409 (status 1), and once it
# is no longer live (revoked or lapsed) the thumbprint gets a new id. Prints the id.
broker_enroll() {
    local id live_tp
    touch "$STUB_BROKER"
    read -r id _ live_tp _ < <(awk -v rt="$1" '$2 == rt && $4 == "live"' "$STUB_BROKER")
    if [[ -z "$id" ]]; then
        id="enr-$(( $(wc -l <"$STUB_BROKER") + 1 ))"
        echo "$id $1 $2 live" >>"$STUB_BROKER"
    elif [[ "$live_tp" != "$2" ]]; then
        return 1
    fi
    echo "$id"
}
ALREADY_ENROLLED="agent-secrets enroll: ENROLL_FAILED: broker 409 ALREADY_ENROLLED: this runtime is already enrolled and live"
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
        echo "enroll $* keydir=$AGENT_SECRETS_KEY_DIR marker-mtime=$(stat -c %Y "$AGENT_SECRETS_KEY_DIR/enrollment.pending" 2>/dev/null || echo none)" >>"$STUB_CALLS"
        rt="" tp="" sid=""
        while (( $# )); do
            case "$1" in
                --runtime-id) rt="$2" ;;
                --thumbprint) tp="$2" ;;
                --session-id) sid="$2" ;;
            esac
            shift
        done
        learns=$(grep -cF " keydir=$STUB_RUN_BASE/learn." "$STUB_CALLS")
        if [[ -z "$sid" && "$AGENT_SECRETS_KEY_DIR" == "$STUB_RUN_BASE"/learn.* ]]; then
            words="${STUB_LEARN:-broker}" n=$learns
            if [[ -n "${STUB_LEARN_AFTER:-}" ]]; then
                for _ in $(seq 1200); do [[ -e "$STUB_LEARN_AFTER" ]] && break; /bin/sleep 0.1; done
            fi
        else
            words="${STUB_ENROLL:-ok}" n=$(( $(grep -c '^enroll ' "$STUB_CALLS") - learns ))
            [[ -z "${STUB_HUP_PID:-}" ]] || kill -HUP "$STUB_HUP_PID"
        fi
        read -ra modes <<<"$words"
        mode="${modes[n - 1]:-${modes[-1]}}"
        case "$mode" in
            ok) echo enr-123 >"$AGENT_SECRETS_KEY_DIR/enrollment"; echo enr-123 ;;
            fail) echo "broker unreachable" >&2; exit 1 ;;
            known) echo "agent-secrets enroll: ENROLL_FAILED: broker 403 FORBIDDEN: this launcher may not enroll this runtime" >&2; exit 1 ;;
            draining) echo "agent-secrets enroll: DRAINING: the helper is shutting down and takes no new calls" >&2; exit 1 ;;
            knownsecond) printf '%s\n' "agent-secrets: warning: something" "agent-secrets enroll: ENROLL_FAILED: broker 403 FORBIDDEN: this launcher may not enroll this runtime" >&2; exit 1 ;;
            conflict)
                # A live enrollment of this session under a key this host no longer has.
                case "$rt" in
                    session-*) echo "$ALREADY_ENROLLED" >&2; exit 1 ;;
                    *) echo enr-box >"$AGENT_SECRETS_KEY_DIR/enrollment"; echo enr-box ;;
                esac ;;
            broker)
                id="$(broker_enroll "$rt" "$tp")" || { echo "$ALREADY_ENROLLED" >&2; exit 1; }
                echo "$id" >"$AGENT_SECRETS_KEY_DIR/enrollment"; echo "$id" ;;
            nofile|unknown|noreply|oldclient|signal|unwritable|buried|indented|warnfirst)
                id="$(broker_enroll "$rt" "$tp")"
                case "$mode" in
                    nofile) echo "$id" ;;
                    unknown) echo "agent-secrets enroll: ENROLL_OUTCOME_UNKNOWN: the broker's answer did not come back within 20s" >&2; exit 1 ;;
                    noreply) echo "agent-secrets enroll: the helper gave no answer (EOF). So it is not known if the broker made the enrollment. Run the same command again: if the broker made the enrollment, the command gets that enrollment." >&2; exit 1 ;;
                    oldclient) echo "agent-secrets enroll: helper reply is not one JSON object: unexpected end of JSON input" >&2; exit 1 ;;
                    signal) kill -HUP "$$" ;;
                    unwritable) echo "agent-secrets enroll: the broker made enrollment $id, but this command could not write its id to $AGENT_SECRETS_KEY_DIR/enrollment: permission denied" >&2; exit 1 ;;
                    buried) echo "agent-secrets enroll: the helper gave no answer (read unix @->helper.sock: agent-secrets enroll: ENROLL_FAILED: broker 403). So it is not known if the broker made the enrollment." >&2; exit 1 ;;
                    indented) echo " agent-secrets enroll: ENROLL_FAILED: broker 403 FORBIDDEN: this launcher may not enroll this runtime" >&2; exit 1 ;;
                    warnfirst) printf '%s\n' "agent-secrets: warning: something" "agent-secrets enroll: ENROLL_OUTCOME_UNKNOWN: the broker's answer did not come back within 20s" >&2; exit 1 ;;
                esac ;;
            *) echo "unexpected STUB_ENROLL word $mode" >&2; exit 99 ;;
        esac ;;
    unenroll)
        # The call is logged only once its revoke is done: unenroll_calls returns on the logged
        # call, and tearDown then removes the dir the revoke's `sed -i` writes in.
        call="unenroll $*"
        id=""
        while (( $# )); do [[ "$1" == --enrollment ]] && id="$2"; shift; done
        n=$(grep -c '^unenroll ' "$STUB_CALLS" 2>/dev/null)
        read -ra modes <<<"${STUB_UNENROLL:-ok}"
        mode="${modes[${n:-0}]:-${modes[-1]}}"
        case "$mode" in
            ok) end_enrollment "$id"; rc=$? ;;
            known) echo "agent-secrets unenroll: UNENROLL_FAILED: broker 403 FORBIDDEN: this launcher may not revoke this enrollment" >&2; rc=1 ;;
            unknown) echo "agent-secrets unenroll: UNENROLL_OUTCOME_UNKNOWN: the broker's answer did not come back within 20s" >&2; rc=1 ;;
            *) echo "unexpected STUB_UNENROLL word $mode" >&2; rc=99 ;;
        esac
        echo "$call" >>"$STUB_CALLS"
        exit "$rc" ;;
    renew)
        # As the pinned client (legion-envoy-v30.21.1) in a box: renew reads `enrollment` once as
        # it starts, exits 76 only when the broker refuses the enrollment as not live, and retries
        # every timeout and every other refusal itself without exiting. Run n takes the nth word
        # of $STUB_RENEW (the last word repeats): notlive, the enrollment lapsed (its row in the
        # broker table ends); exit=<n>; or invalid, a PROOF_INVALID refusal that does not say not
        # live, then exit 1. Run $STUB_RENEW_RUNS (3) ends the loop, as the box's end does. Run
        # $STUB_LONG_RUN moves the fake clock ($STUB_CLOCK, read by the date stub) on by two
        # minutes.
        id="$(cat "$AGENT_SECRETS_KEY_DIR/enrollment" 2>/dev/null)"
        echo "renew enrollment=$id" >>"$STUB_CALLS"
        n=$(grep -c '^renew ' "$STUB_CALLS")
        read -ra modes <<<"${STUB_RENEW:-exit=2}"
        mode="${modes[n - 1]:-${modes[-1]}}"
        [[ "$n" != "${STUB_LONG_RUN:-}" ]] || echo $(( $(<"$STUB_CLOCK") + 120 )) >"$STUB_CLOCK"
        case "$mode" in
            notlive)
                end_enrollment "$id"
                echo "agent-secrets renew: enrollment $id permanently refused by the broker AGENT_SECRETS_URL names: HTTP 401 ENROLLMENT_NOT_LIVE: enrollment is not live" >&2
                rc=76 ;;
            invalid)
                echo "agent-secrets renew: proof invalid: signature does not verify (PROOF_INVALID)" >&2
                rc=1 ;;
            exit=*)
                rc="${mode#exit=}"
                echo "agent-secrets renew: exits $rc" >&2 ;;
        esac
        (( n < ${STUB_RENEW_RUNS:-3} )) || kill "$PPID"
        exit "$rc" ;;
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
        # against a slow broker: only a marker renewed before every attempt stays fresh. The
        # STUB_NEW_ID_AT'th sleep writes a new enrollment id into the key dir, as the launcher's
        # re-enrollment does while the renew loop waits. STUB_REAL_SLEEP makes it a real sleep.
        # With STUB_GRACE_UNTIL set, the unenroll waiter's grace (a sleep of
        # $AGENTBOX_UNENROLL_GRACE, 180 by default) lasts until that file exists (60 s at most).
        write_stub(
            self.stub_dir,
            "sleep",
            'echo "sleep $*" >>"$STUB_CALLS"\n'
            '[[ -z "${STUB_REAL_SLEEP:-}" ]] || exec /bin/sleep "$@"\n'
            '[[ -z "${STUB_GRACE_UNTIL:-}" || "$*" != "${AGENTBOX_UNENROLL_GRACE:-180}" ]]'
            ' || for _ in $(seq 600); do [[ -e "$STUB_GRACE_UNTIL" ]] && break; /bin/sleep 0.1; done\n'
            '[[ -e "$STUB_KEYDIR/enrollment.pending" ]] && touch -h -d "-1000 seconds" "$STUB_KEYDIR/enrollment.pending"\n'
            '[[ -z "${STUB_NEW_ID_AT:-}" ]] || (( $(grep -c "^sleep " "$STUB_CALLS") != STUB_NEW_ID_AT ))'
            ' || echo enr-new >"$STUB_KEYDIR/enrollment"\n'
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
        self.keydir = self.hostdir / keydir_in_box_dir()
        # The key dir's name in the box's own runtime dir, run-user/ (the box's /run/user/<uid>):
        # the key dir's mount point, and the key dir itself before dispatch://INFRA-3462.
        self.run_user_keydir = self.hostdir / "run-user" / "agent-secrets"
        self.broker = self.root / "broker"
        self.reset_box()

    def reset_box(self) -> None:
        """box1 as `new` leaves it before `docker run`, with no call made and nothing at the
        broker: for setUp, and for each subtest that needs a fresh box. mount_args makes both the
        key dir and its mount point in run-user/ (one directory at the old layout)."""
        shutil.rmtree(self.run_base, ignore_errors=True)
        self.calls.unlink(missing_ok=True)
        self.broker.unlink(missing_ok=True)
        for directory in (self.run_user_keydir, self.keydir):
            directory.mkdir(parents=True, exist_ok=True)
        # A marker as `new` leaves it, already older than the client's 160 s trust window.
        marker = self.keydir / "enrollment.pending"
        marker.write_text("")
        old = time.time() - 1000
        os.utime(marker, (old, old))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def bash_script(self, body: str) -> str:
        """`body`, after the lines that source scripts/agentbox and set its RUN_BASE to the temp
        dir's run dir."""
        return f'source "{AGENTBOX}"\nRUN_BASE="{self.run_base}"\n{body}\n'

    def bash_env(self, **stubs: str) -> dict[str, str]:
        """The environment a bash_script runs in: the stubs first on PATH, the temp dir's paths for
        the script and the stubs, and `stubs` over them."""
        return {
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
            "STUB_KEYDIR_REL": keydir_in_box_dir(),
            "STUB_RUN_BASE": str(self.run_base),
            # The run dir again, for a process that runs scripts/agentbox itself rather than
            # sourcing it: the unenroll waiter's `session-live-box`.
            "AGENTBOX_RUN_BASE": str(self.run_base),
            "STUB_THUMBPRINT": THUMBPRINT,
            "STUB_BROKER": str(self.broker),
            **stubs,
        }

    def bash(self, body: str, timeout: float = 300, **stubs: str) -> subprocess.CompletedProcess[str]:
        # 300 s, not 60: the slow-box test below deliberately runs 400 passes of the wait, each
        # forking a stub `docker`, and on this host at load 300-550 that alone takes over a
        # minute (it timed out at 60 s on 2026-11-01, on HEAD as well as on the change under
        # test). Every other case here finishes in seconds, so the larger cap only bites a hang.
        return subprocess.run(
            ["bash", "-c", self.bash_script(body)],
            capture_output=True,
            text=True,
            env=self.bash_env(**stubs),
            check=False,
            timeout=timeout,
        )

    def enroll(self, **stubs: str) -> subprocess.CompletedProcess[str]:
        return self.bash('if enroll_box box1 sid-1; then echo rc=0; else echo "rc=$?"; fi', **stubs)

    def files(self, keydir: Path | None = None) -> set[str]:
        """The names in a box's key dir (box1's by default)."""
        return {p.name for p in (keydir or self.keydir).iterdir()}

    def marked(self, keydir: Path | None = None) -> bool:
        """Whether a box's key dir (box1's by default) holds enrollment.not-live, of any type."""
        return os.path.lexists((keydir or self.keydir) / "enrollment.not-live")

    def calls_matching(self, prefix: str | tuple[str, ...]) -> list[str]:
        if not self.calls.exists():
            return []
        return [line for line in self.calls.read_text().splitlines() if line.startswith(prefix)]

    def error(self) -> str:
        return (self.keydir / "enrollment.error").read_text()

    def assert_no_identity(self) -> None:
        self.assertEqual(self.files(), {"enrollment.error"})
        self.assertFalse((self.hostdir / "enrollment-id").exists())

    def enrolls(self) -> list[str]:
        """The enroll calls in order, each without the key dir and the marker mtime it recorded."""
        return [call.split(" keydir=")[0] for call in self.calls_matching("enroll ")]

    def broker_rows(self, runtime: str) -> list[list[str]]:
        """The broker's enrollments of one runtime id: [id, runtime id, thumbprint, live|ended]."""
        return [row for row in map(str.split, self.broker.read_text().splitlines()) if row[1] == runtime]

    def assert_enrolled_again_as_recorded(self, sid: str, runtime: str, ended: bool = True) -> None:
        """Two enroll calls, enroll_box's and one more just like it: the same runtime id, the
        same thumbprint, and the session id (none for a box without one). With `ended` the broker
        ended the first enrollment and the second made a new one, live; without, the broker holds
        the first alone, still live."""
        enrolls = self.enrolls()
        self.assertEqual(len(enrolls), 2, enrolls)
        self.assertEqual(enrolls[1], enrolls[0])
        self.assertIn(f"--runtime-id {runtime} --thumbprint {THUMBPRINT}", enrolls[1])
        if sid:
            self.assertTrue(enrolls[1].endswith(f"--session-id {sid}"), enrolls[1])
        else:
            self.assertNotIn("--session-id", enrolls[1])
        if ended:
            rows = [["enr-1", runtime, THUMBPRINT, "ended"], ["enr-2", runtime, THUMBPRINT, "live"]]
        else:
            rows = [["enr-1", runtime, THUMBPRINT, "live"]]
        self.assertEqual(self.broker_rows(runtime), rows)

    # A launcher line in renew.log starts with the UTC time; the renew loop's own lines do not.
    LAUNCHER_LINE = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ (agentbox: .*)")

    def launcher_lines(self, keydir: Path | None = None) -> list[str]:
        """The launcher's lines in a box's renew.log (box1's by default), without the time."""
        log = (keydir or self.keydir) / "renew.log"
        if not log.exists():
            return []
        return [m.group(1) for m in map(self.LAUNCHER_LINE.fullmatch, log.read_text().splitlines()) if m]

    def host_keydir(self, key: bytes | None = None) -> Path:
        """A directory that stands for the host's own /run/user/<uid>/agent-secrets: the helper's
        socket, and with `key` the key.pem a host session's client signs with before it asks the
        socket (legion-envoy-v30.21.1 cmd/agent-secrets identity.go keyDir, main.go selectSigner)."""
        host = self.root / "host-run-user" / "agent-secrets"
        host.mkdir(parents=True)
        with socket.socket(socket.AF_UNIX) as sock:
            sock.bind(str(host / "helper.sock"))
        if key is not None:
            (host / "key.pem").write_bytes(key)
        return host

    def link_run_user_keydir(self, target: Path) -> None:
        """What a process in box1 can do once the box runs: move what is at the key dir's name in
        its runtime dir aside and put a link to `target` there. run-user/ is the box's own mount
        and box_keydir's parent is not, so this is the one link at the key dir's name a box can
        make."""
        self.run_user_keydir.rename(self.run_user_keydir.with_name("agent-secrets.moved"))
        self.run_user_keydir.symlink_to(target)

    @contextlib.contextmanager
    def flip_when_pending(self, target: Path) -> Iterator[threading.Event]:
        """A loop in box1 beside the launcher: once the key dir's name in run-user/ is a link and
        enrollment.pending shows through it, a new link to `target` is renamed over it at once.
        run-user/ is where the box sees its key dir at the old layout only; at the new one the
        marker never shows there, and the loop ends unflipped when the block does (120 s at
        most). The event is set once the loop flipped the link."""
        link = self.run_user_keydir
        flipped, done = threading.Event(), threading.Event()

        def flip() -> None:
            end = time.monotonic() + 120
            while not done.is_set() and time.monotonic() < end:
                if link.is_symlink() and os.path.lexists(link / "enrollment.pending"):
                    new = link.with_name("agent-secrets.flip")
                    new.symlink_to(target)
                    new.replace(link)
                    flipped.set()
                    return
                time.sleep(0.0005)

        thread = threading.Thread(target=flip, daemon=True)
        thread.start()
        try:
            yield flipped
        finally:
            done.set()
            thread.join(timeout=10)


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

    def test_a_link_at_the_key_dirs_name_in_run_user_takes_no_host_write(self) -> None:
        """A process in the box linked its key dir's name in its own runtime dir to the host's
        /run/user/<uid>/agent-secrets before the box's first enrollment. While the key dir was
        run-user/agent-secrets, enroll_box wrote the session's key.pem and the enrollment there,
        where host sessions take key.pem before the helper's socket. The host dir keeps only its
        socket, and the box is enrolled in its own key dir."""
        host = self.host_keydir()
        self.link_run_user_keydir(host)
        result = self.enroll()
        self.assertEqual(self.files(host), {"helper.sock"})
        self.assertIn("rc=0", result.stdout, result.stderr)
        self.assertEqual(self.files(), {"key.pem", "enrollment"})

    def test_a_failed_first_enrollment_through_that_link_deletes_no_host_key(self) -> None:
        """The same link, aimed at a host dir that holds a host session's key.pem, and a first
        enrollment that fails: a box with no session id and the broker unreachable. enroll_failed
        removes key.pem and the marker from the key dir, which through the link were the host's.
        The host's key.pem is still there, byte for byte, and the box ends with no identity."""
        key = b"KEY a host session's key\n"
        host = self.host_keydir(key)
        self.link_run_user_keydir(host)
        result = self.bash('if enroll_box box1 ""; then echo rc=0; else echo "rc=$?"; fi', STUB_ENROLL="fail")
        self.assertEqual(self.files(host), {"helper.sock", "key.pem"})
        self.assertEqual((host / "key.pem").read_bytes(), key)
        self.assertIn("rc=1", result.stdout, result.stderr)
        self.assert_no_identity()

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

    def test_a_record_the_close_cannot_read_does_not_end_the_close(self) -> None:
        """box1's enrollment-id, or its enrollment-session, is there but cannot be read (mode 000).
        Under the script's set -e, `$(<file)` of such a file ended close_box, and another close of
        the box can remove the file between a test and a read. The close goes on and removes
        box1's run dir. It makes no unenroll call: an id it cannot read is not revoked, and an
        enrollment-session it cannot read can name a session, whose enrollment is not box1's to end
        at once (one line on stderr says so)."""
        if os.geteuid() == 0:
            self.skipTest("root reads a file of mode 000")
        for name in ("enrollment-id", "enrollment-session"):
            with self.subTest(unreadable=name):
                self.reset_box()
                (self.hostdir / "enrollment-id").write_text("enr-123\n")
                (self.hostdir / "enrollment-session").write_text("sid-1\n")
                (self.hostdir / name).chmod(0)
                (self.home / "boxes" / "box1").mkdir(parents=True, exist_ok=True)
                result = self.bash('close_box box1 ""\necho closed', STUB_GONE="1")
                self.assertIn("closed", result.stdout, result.stderr)
                self.assertEqual(self.calls_matching("unenroll "), [])
                self.assertFalse(self.hostdir.exists())
                if name == "enrollment-session":
                    self.assertIn(f"cannot read {self.hostdir}/enrollment-session; enrollment enr-123", result.stderr)

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


class RenewLoop(EnrollFixture):
    """The box's renew loop, RENEW_LOOP, as start_renew runs it in the box.

    At the pinned client renew exits 76 only when the broker refuses the enrollment as not live,
    and retries every timeout and every other refusal itself. That 76, from a renew started with
    an id in `enrollment`, is the one exit that writes enrollment.not-live for the box's launcher;
    every other exit is restarted after the loop's growing delay. The sleep stub returns at once
    and records each call, so the tests count the loop's waits and polls rather than time them.
    """

    REFUSED = (
        "agent-secrets renew: enrollment enr-old permanently refused by the broker AGENT_SECRETS_URL names:"
        " HTTP 401 ENROLLMENT_NOT_LIVE: enrollment is not live"
    )
    NOT_LIVE = "agentbox: agent-secrets renew exited 76: enrollment enr-old is not live; the host enrols this box again"

    def run_loop(self, modes: str, runs: int, enrollment: str | None = "enr-old", **stubs: str) -> list[int]:
        """Run the loop with `enrollment` in the key dir (None: no file) until renew's run `runs`
        ends it; return the delays the loop slept, in order."""
        if enrollment is not None:
            (self.keydir / "enrollment").write_text(f"{enrollment}\n" if enrollment else "")
        clock = self.root / "clock"
        clock.write_text("1000\n")
        # The stub kills the loop, so start_renew fails; the line after it shows the run went on.
        result = self.bash(
            "start_renew box1 || true\necho loop-ended",
            STUB_RUN_RENEW="1",
            STUB_CLOCK=str(clock),
            STUB_RENEW=modes,
            STUB_RENEW_RUNS=str(runs),
            **stubs,
        )
        self.assertIn("loop-ended", result.stdout, result.stderr)
        return [int(c.split()[1]) for c in self.calls_matching("sleep ")]

    def renew_log(self) -> list[str]:
        return (self.keydir / "renew.log").read_text().splitlines()

    def loop_lines(self) -> list[str]:
        """The loop's own lines in renew.log, without renew's."""
        return [line for line in self.renew_log() if line.startswith("agentbox: ")]

    def test_a_renew_that_exits_is_started_again(self) -> None:
        # A box with no `enrollment`: renew has no identity and exits 2. The stub ends the loop by
        # killing it on the third renew.
        self.run_loop("exit=2", 3, enrollment=None)
        self.assertEqual(len(self.calls_matching("renew ")), 3)
        self.assertEqual(self.renew_log().count("agent-secrets renew: exits 2"), 3)

    def test_a_renew_that_keeps_failing_is_restarted_with_growing_delays(self) -> None:
        self.assertEqual(self.run_loop("exit=2", 9, enrollment=None), [10, 20, 40, 80, 160, 320, 600, 600])

    def test_a_renew_that_ran_a_while_restarts_the_delays(self) -> None:
        # renew exits 0 on SIGTERM, here after its third run went on for two minutes.
        self.assertEqual(self.run_loop("exit=0", 5, STUB_LONG_RUN="3"), [10, 20, 10, 20])

    def test_a_renew_refused_as_not_live_marks_the_box_for_its_launcher(self) -> None:
        self.run_loop("notlive exit=2", 2, STUB_NEW_ID_AT="1")
        self.assertTrue(self.marked())
        self.assertEqual(self.renew_log(), [self.REFUSED, self.NOT_LIVE, "agent-secrets renew: exits 2"])

    def test_a_new_enrollment_starts_renew_again_at_once_with_the_first_delay(self) -> None:
        """The launcher writes the new id during the second poll: the next renew starts after
        that one `sleep 2`, reads the new id, and is restarted after 10 s, not the 80 s before."""
        self.run_loop("exit=2 exit=2 exit=2 notlive exit=2", 6, STUB_NEW_ID_AT="5")
        calls = self.calls_matching(("renew ", "sleep "))
        self.assertEqual(
            calls,
            [
                "renew enrollment=enr-old", "sleep 10",
                "renew enrollment=enr-old", "sleep 20",
                "renew enrollment=enr-old", "sleep 40",
                "renew enrollment=enr-old", "sleep 2", "sleep 2",
                "renew enrollment=enr-new", "sleep 10",
                "renew enrollment=enr-new",
            ],
        )
        restarted = "agentbox: agent-secrets renew exited 2; restarting in {}s".format
        self.assertEqual(
            self.loop_lines(), [restarted(10), restarted(20), restarted(40), self.NOT_LIVE, restarted(10)]
        )

    def test_an_enrollment_still_not_live_is_renewed_again_after_90_s(self) -> None:
        """The wait is the longer of the delay (10 s here) and 90 s, counted in 2 s polls; after
        it the delay doubles as after any other wait."""
        self.assertEqual(self.run_loop("notlive exit=2", 3), [2] * 45 + [20])
        self.assertTrue(self.marked())
        self.assertEqual(
            self.loop_lines(),
            [
                self.NOT_LIVE,
                "agentbox: enrollment enr-old is still not live after 90s; renewing again",
                "agentbox: agent-secrets renew exited 2; restarting in 20s",
            ],
        )
        self.assertEqual(len(self.calls_matching("renew enrollment=enr-old")), 3)

    def test_a_longer_delay_makes_a_longer_wait_for_the_new_enrollment(self) -> None:
        self.assertEqual(
            self.run_loop("exit=2 exit=2 exit=2 exit=2 notlive exit=2", 7),
            [10, 20, 40, 80] + [2] * 80 + [320],
        )
        self.assertIn("agentbox: enrollment enr-old is still not live after 160s; renewing again", self.loop_lines())

    def test_no_other_exit_marks_the_box(self) -> None:
        """Only a 76 from a renew that read an id is the signal; every other exit keeps the
        `restarting in <d>s` line and the delays."""
        delays = [10, 20, 40, 80, 160, 320, 600, 600]
        cases = {
            "exit 0, renew ended by SIGTERM": ("exit=0", "enr-old", 0),
            "exit 1": ("exit=1", "enr-old", 1),
            "exit 2, no identity": ("exit=2", "enr-old", 2),
            "exit 143, renew killed": ("exit=143", "enr-old", 143),
            "a PROOF_INVALID that does not say not live, exit 1": ("invalid", "enr-old", 1),
            "76 with an empty enrollment": ("notlive", "", 76),
            "76 with no enrollment": ("notlive", None, 76),
        }
        for case, (mode, enrollment, rc) in cases.items():
            with self.subTest(case=case):
                self.calls.unlink(missing_ok=True)
                for name in ("enrollment", "enrollment.not-live", "renew.log"):
                    (self.keydir / name).unlink(missing_ok=True)
                self.assertEqual(self.run_loop(mode, 9, enrollment=enrollment), delays)
                self.assertFalse(self.marked())
                self.assertEqual(
                    self.loop_lines(), [f"agentbox: agent-secrets renew exited {rc}; restarting in {d}s" for d in delays]
                )


class SessionIdentity(EnrollFixture):
    """A session's broker identity follows the session, not the box it is running in.

    A pending credential request is bound to the enrollment id alone, and ending an enrollment
    then cancelled every request still pending under it and revoked its grants (legion
    packages/envoy/internal/broker/enroll/enroll.go endEnrollment, before the broker's carry). So
    a key made inside the box and an enrollment keyed by the box's name meant `agentbox move`
    orphaned every card Sami had not answered and threw away every grant he had given
    (dispatch://INFRA-1167).
    """

    def second_box(self, name: str = "box2") -> Path:
        """A box dir as mount_args makes one, for the box the session moves into: the key dir and
        its mount point in run-user/."""
        keydir = self.run_base / name / keydir_in_box_dir()
        for directory in (self.run_base / name / "run-user" / "agent-secrets", keydir):
            directory.mkdir(parents=True, exist_ok=True)
        (keydir / "enrollment.pending").write_text("")
        return keydir

    def test_the_same_session_in_a_new_box_gets_the_same_enrollment(self) -> None:
        two = self.second_box()
        # STUB_KEY and STUB_THUMBPRINT change between the boxes: a key made for the second box
        # would be a different one, and the test would see it.
        first = self.bash("enroll_box box1 sid-1", STUB_ENROLL="broker")
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self.bash(
            "enroll_box box2 sid-1", STUB_ENROLL="broker", STUB_KEY="k2", STUB_THUMBPRINT="B" * 43
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
        self.bash("enroll_box box1 sid-1", STUB_ENROLL="broker")
        self.bash("enroll_box box2 sid-2", STUB_ENROLL="broker", STUB_KEY="k2", STUB_THUMBPRINT="B" * 43)
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
        an identity of its own rather than waiting out the 409 for fifteen minutes, and the
        fallback call follows the 409 at once, with no 3 s pause between."""
        result = self.bash("enroll_box box1 sid-1", STUB_ENROLL="conflict")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("enrolls under its own name", result.stderr)
        runtimes = [c.split("--runtime-id ")[1].split()[0] for c in self.calls_matching("enroll ")]
        self.assertEqual(runtimes, ["session-sid-1", "box1"])
        self.assertEqual(self.calls_matching("sleep "), [])
        self.assertEqual((self.hostdir / "enrollment-id").read_text(), "enr-box\n")
        self.assertFalse((self.hostdir / "enrollment-session").exists())

    def unenroll_calls(self, deadline: float = 20.0) -> list[str]:
        """The waiter unenroll_box leaves behind is detached; wait for it to reach one of its
        ends: its unenroll call in the call log, or a line in unenroll.log that says it left the
        enrollment alone. Any other line there is not enough: the waiter logs 'ending enrollment'
        before it calls unenroll."""
        log = self.run_base / "session-keys" / "unenroll.log"
        end = time.time() + deadline
        while time.time() < end:
            if self.calls_matching("unenroll ") or (log.exists() and "left alone" in log.read_text()):
                break
            time.sleep(0.2)
        return self.calls_matching("unenroll ")

    def test_a_session_that_came_back_in_another_box_keeps_its_enrollment(self) -> None:
        self.second_box()
        self.bash("enroll_box box1 sid-1", STUB_ENROLL="broker")
        self.bash("enroll_box box2 sid-1", STUB_ENROLL="broker")
        # box1 closing after the move must not cancel what the session has pending.
        self.bash("unenroll_box box1", AGENTBOX_UNENROLL_GRACE="0")
        self.assertEqual(self.unenroll_calls(), [])
        self.assertIn("left alone", (self.run_base / "session-keys" / "unenroll.log").read_text())
        self.assertTrue((self.run_base / "session-keys" / "sid-1" / "key.pem").exists())

    def test_a_session_that_did_not_come_back_ends_its_enrollment_and_its_key(self) -> None:
        self.bash("enroll_box box1 sid-1", STUB_ENROLL="broker")
        self.bash("unenroll_box box1", AGENTBOX_UNENROLL_GRACE="0")
        (call,) = self.unenroll_calls()
        self.assertIn("--enrollment ", call)
        self.assertFalse((self.run_base / "session-keys" / "sid-1").exists())

    def test_unenroll_calls_returns_only_once_the_waiters_revoke_reached_the_broker(self) -> None:
        """dispatch://INFRA-3462, testing after review round 2. unenroll_calls must not return
        while the detached waiter still works in this test's temp dir, because tearDown removes
        that dir. The stub's revoke (end_enrollment, a `sed -i` on the broker table) makes a temp
        file there, so the stub logs its unenroll call only after the revoke, and unenroll_calls
        returns on the logged call. At d10a9465 the stub logged the call first, and
        test_a_session_that_did_not_come_back_ends_its_enrollment_and_its_key failed in 1 of 12
        runs under load, with OSError: Directory not empty. Here the revoke is held 2 s, so a stub
        that logs its call before the revoke fails this test every time."""
        write_stub(
            self.stub_dir,
            "sed",
            '[[ "$1" == -i && "${@: -1}" == "$STUB_BROKER" ]] && /bin/sleep 2\nexec /bin/sed "$@"',
        )
        self.bash("enroll_box box1 sid-1", STUB_ENROLL="broker")
        eid = (self.hostdir / "enrollment-id").read_text().strip()
        self.bash("unenroll_box box1", AGENTBOX_UNENROLL_GRACE="0")
        (call,) = self.unenroll_calls()
        self.assertIn(f"--enrollment {eid}", call)
        rows = [line.split() for line in self.broker.read_text().splitlines()]
        self.assertIn("ended", [row[-1] for row in rows if row and row[0] == eid], rows)


class OpenAttempts(EnrollFixture):
    """The close of a box whose enroll calls can have made an enrollment the host has no id for:
    each call whose outcome is not known (ENROLL_OPEN). close_box learns that enrollment's id from
    the broker, with an enroll call of the attempt's runtime id and thumbprint and no session id,
    and revokes it, in a detached process.

    ENROLL_OPEN is the launcher's memory, so each case runs the box's enrollment and its close in
    one bash process (`close`). The body writes a `close` line into the call log before the close,
    so the calls after that line are the close's, and ends with `wait`: the close's detached
    processes are children of the body's shell, so it returns once they have ended, before
    tearDown removes the dir they write in. They log to session-keys/unenroll.log. The case that
    kills the body's process group waits for the detached process's line in that log instead.
    """

    OWN_OPEN = f"own box1 {THUMBPRINT}"
    SESSION_OPEN = f"session session-sid-1 {THUMBPRINT}"
    # Each way an enroll call can end with its outcome not known, as the stub's words.
    OPEN_WORDS = ("unknown", "noreply", "oldclient", "signal", "unwritable", "nofile")
    TIMED = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ (.*)")

    def close(
        self, steps: str, close: str = 'close_box box1 ""', after: str = "", timeout: float = 300, **stubs: str
    ) -> None:
        """`steps`, the `close` line, `close`, `after`, then `wait`, in one bash process, with
        box1's container gone (STUB_GONE). A line after the wait shows that every step ran under
        the script's set -euo pipefail."""
        result = self.bash(
            f'{steps}\necho close >>"$STUB_CALLS"\n{close}\n{after}\nwait\necho closed',
            timeout=timeout,
            **{"STUB_GONE": "1", **stubs},
        )
        self.assertIn("closed", result.stdout, result.stderr)

    def calls_around_close(self, prefix: str) -> tuple[list[str], list[str]]:
        """The calls that start with `prefix`: those before the `close` line, and those after it."""
        lines = self.calls.read_text().splitlines()
        at = lines.index("close")
        return (
            [line for line in lines[:at] if line.startswith(prefix)],
            [line for line in lines[at + 1 :] if line.startswith(prefix)],
        )

    def learn_calls(self) -> list[tuple[str, Path]]:
        """The enroll calls the close made, each as the client's arguments (`enroll …`) and its key
        dir."""
        calls = []
        for call in self.calls_around_close("enroll ")[1]:
            args, rest = call.removeprefix("enroll ").split(" keydir=", 1)
            calls.append((args, Path(rest.split(" marker-mtime=")[0])))
        return calls

    def assert_one_learn_call(self, runtime: str) -> None:
        """The close made one enroll call, `enroll --helper --runtime-id RUNTIME --thumbprint
        THUMBPRINT` and no more arguments (so no --session-id), with a key dir outside box1's run
        dir."""
        learns = self.learn_calls()
        self.assertEqual(
            [args for args, _ in learns], [f"enroll --helper --runtime-id {runtime} --thumbprint {THUMBPRINT}"]
        )
        self.assertFalse(learns[0][1].is_relative_to(self.hostdir), learns[0][1])

    def revokes_after_close(self) -> list[str]:
        """The unenroll calls the close made, each as the client's arguments (`unenroll …`)."""
        return [call.removeprefix("unenroll ") for call in self.calls_around_close("unenroll ")[1]]

    def unenroll_log(self) -> list[str]:
        """The lines of session-keys/unenroll.log, each without the UTC time it starts with (a line
        without one comes back whole)."""
        log = self.run_base / "session-keys" / "unenroll.log"
        if not log.exists():
            return []
        return [m.group(1) if (m := self.TIMED.fullmatch(line)) else line for line in log.read_text().splitlines()]

    def test_the_close_learns_and_revokes_the_enrollment_of_an_open_attempt(self) -> None:
        """A box with no session id whose enroll calls each end with the outcome not known while
        the broker made the enrollment: the pinned client's ENROLL_OUTCOME_UNKNOWN, no answer from
        the helper, an old helper's empty reply, a signal, an id it could not write, or an exit 0
        that left no enrollment file. Then close_box. The close makes one learn call, for box1 and
        the key's thumbprint, and one unenroll call, for the id the learn call got (enr-1); the
        broker's row of enr-1 has ended; and unenroll.log holds one line, which names enr-1 and
        the runtime id."""
        for word in self.OPEN_WORDS:
            with self.subTest(enroll=word):
                self.reset_box()
                self.close('enroll_box box1 "" || true', STUB_ENROLL=word)
                self.assert_one_learn_call("box1")
                self.assertEqual(self.revokes_after_close(), ["unenroll --helper --enrollment enr-1"])
                self.assertEqual(self.broker_rows("box1"), [["enr-1", "box1", THUMBPRINT, "ended"]])
                self.assertEqual(self.unenroll_log(), ["box1: enrollment enr-1 (runtime id box1) revoked"])

    def test_a_known_failure_leaves_nothing_to_learn(self) -> None:
        """Each of enroll_box's ten calls is refused ENROLL_FAILED, or DRAINING: the helper says
        that no enrollment was made. The close makes no enroll call."""
        for word in ("known", "draining"):
            with self.subTest(enroll=word):
                self.reset_box()
                self.close('enroll_box box1 "" || true', STUB_ENROLL=word)
                before, after = self.calls_around_close("enroll ")
                self.assertEqual(len(before), 10, before)
                self.assertEqual(after, [])

    def test_a_known_code_where_it_does_not_count_leaves_the_call_open(self) -> None:
        """Each of enroll_box's calls exits 1 while the broker made the enrollment, and no line of
        its stderr starts with a known code: the ENROLL_FAILED line inside the text of a line for a
        helper that gave no answer (buried), the ENROLL_FAILED line after a space (indented), or a
        warning line and then the ENROLL_OUTCOME_UNKNOWN line (warnfirst). Then close_box. The close
        makes one learn call, for box1 and the key's thumbprint, and one unenroll call, of enr-1;
        the broker's row of enr-1 has ended."""
        for word in ("buried", "indented", "warnfirst"):
            with self.subTest(enroll=word):
                self.reset_box()
                self.close('enroll_box box1 "" || true', STUB_ENROLL=word)
                self.assert_one_learn_call("box1")
                self.assertEqual(self.revokes_after_close(), ["unenroll --helper --enrollment enr-1"])
                self.assertEqual(self.broker_rows("box1"), [["enr-1", "box1", THUMBPRINT, "ended"]])

    def test_a_known_code_at_the_start_of_a_later_line_leaves_nothing_to_learn(self) -> None:
        """Each of enroll_box's ten calls is refused with a warning line and then the ENROLL_FAILED
        line (knownsecond): the code starts the second line of the stderr, not the first. The
        close makes no enroll call."""
        self.close('enroll_box box1 "" || true', STUB_ENROLL="knownsecond")
        before, after = self.calls_around_close("enroll ")
        self.assertEqual(len(before), 10, before)
        self.assertEqual(after, [])

    def test_a_recorded_success_settles_an_earlier_open_attempt(self) -> None:
        """The first call ends with its outcome not known while the broker made enr-1; the retry,
        with the same runtime id and thumbprint, gets enr-1 back and records it. The close revokes
        enr-1 and makes no enroll call."""
        self.close('enroll_box box1 ""', STUB_ENROLL="unknown broker")
        before, after = self.calls_around_close("enroll ")
        self.assertEqual(len(before), 2, before)
        self.assertEqual(after, [])
        self.assertEqual(self.revokes_after_close(), ["unenroll --helper --enrollment enr-1"])

    def test_the_learn_call_names_the_thumbprint_of_the_open_attempt(self) -> None:
        """enroll_box's calls, with the key made first in the box (THUMBPRINT), end with the
        outcome not known. reenroll_box then finds no key.pem, makes a new key in the box, and its
        one call, with that key's thumbprint, is refused ENROLL_FAILED. The close's one learn call
        names THUMBPRINT."""
        self.close(
            'enroll_box box1 "" || true\nexport STUB_ENROLL=known\nreenroll_box box1 || true', STUB_ENROLL="unknown"
        )
        before, _ = self.calls_around_close("enroll ")
        self.assertEqual(len(before), 11, before)
        self.assertIn(f"--runtime-id box1 --thumbprint K{2:042d} keydir=", before[-1])
        self.assert_one_learn_call("box1")

    def test_a_session_whose_first_enrollment_ends_open_is_learned_after_its_grace(self) -> None:
        """Session sid-1's `owner` names box0, an earlier box of the session that is gone. Each
        enroll call of box1 for the session ends with its outcome not known, so box1 records no id,
        and `owner` still names box0 at the close. After the grace (0 s) the waiter learns the
        enrollment with runtime id session-sid-1 and no --session-id, revokes the id it got (the
        broker's row has ended), removes the session's key dir, and logs a line for each step."""
        skd = self.run_base / "session-keys" / "sid-1"
        skd.mkdir(parents=True)
        (skd / "owner").write_text("box0\n")
        self.close(
            'enroll_box box1 sid-1 || true\ncp "$RUN_BASE/session-keys/sid-1/owner" "$RUN_BASE/owner-at-close"',
            STUB_ENROLL="unknown",
            AGENTBOX_UNENROLL_GRACE="0",
        )
        self.assertEqual((self.run_base / "owner-at-close").read_text(), "box0\n")
        self.assert_one_learn_call("session-sid-1")
        self.assertEqual(self.revokes_after_close(), ["unenroll --helper --enrollment enr-1"])
        self.assertEqual(self.broker_rows("session-sid-1"), [["enr-1", "session-sid-1", THUMBPRINT, "ended"]])
        self.assertFalse(skd.exists())
        self.assertEqual(
            self.unenroll_log(),
            [
                "box1: session sid-1 did not come back; ending enrollment none,"
                f" and the enrollment of each open attempt: {self.SESSION_OPEN}",
                "box1: enrollment enr-1 (runtime id session-sid-1) revoked",
            ],
        )

    # box1 enrolls for session sid-1 (enr-1, recorded). The broker revokes enr-1, and the launcher's
    # call to enroll the box again ends with its outcome not known while the broker makes enr-2.
    # Run with STUB_ENROLL="broker unknown".
    REENROLLED_OPEN = "\n".join(
        [
            "enroll_box box1 sid-1",
            'agent-secrets unenroll --helper --enrollment "$(<"$RUN_BASE/box1/enrollment-id")"',
            ': >"$(box_keydir box1)/enrollment.not-live"',
            "reenroll_if_not_live box1",
        ]
    )

    def test_a_session_whose_reenrollment_ends_open_has_both_enrollments_ended(self) -> None:
        """box1 enrolled for session sid-1 (enr-1, recorded); the broker revoked enr-1, and the
        launcher's call to enroll the box again ended with its outcome not known while the broker
        made enr-2. After the grace (0 s) the waiter revokes the recorded enr-1, then learns enr-2
        with runtime id session-sid-1 and no --session-id, and revokes it: two unenroll calls after
        the close, both rows ended, and a log line for each step."""
        self.close(self.REENROLLED_OPEN, STUB_ENROLL="broker unknown", AGENTBOX_UNENROLL_GRACE="0")
        self.assert_one_learn_call("session-sid-1")
        self.assertEqual(
            self.revokes_after_close(),
            ["unenroll --helper --enrollment enr-1", "unenroll --helper --enrollment enr-2"],
        )
        self.assertEqual(
            self.broker_rows("session-sid-1"),
            [["enr-1", "session-sid-1", THUMBPRINT, "ended"], ["enr-2", "session-sid-1", THUMBPRINT, "ended"]],
        )
        self.assertEqual(
            self.unenroll_log(),
            [
                "box1: session sid-1 did not come back; ending enrollment enr-1,"
                f" and the enrollment of each open attempt: {self.SESSION_OPEN}",
                "box1: enrollment enr-1 (recorded) revoked",
                "box1: enrollment enr-2 (runtime id session-sid-1) revoked",
            ],
        )

    def test_a_session_live_in_another_box_keeps_the_open_enrollment(self) -> None:
        """Each enroll call of box1 for session sid-1 ends with its outcome not known, and box2,
        another box of the session, still runs. After the grace (0 s) the waiter's test 2 finds
        box2: no learn call, and one line that leaves the open attempt's enrollment alone."""
        box2 = self.run_base / "box2"
        box2.mkdir()
        (box2 / "enrollment-session").write_text("sid-1\n")
        self.close(
            'enroll_box box1 sid-1 || true', STUB_ENROLL="unknown", STUB_LIVE_BOXES="box2", AGENTBOX_UNENROLL_GRACE="0"
        )
        self.assertEqual(self.learn_calls(), [])
        self.assertEqual(
            self.unenroll_log(),
            [
                "box1: session sid-1 is live in box box2; enrollment none left alone,"
                f" and the enrollment of each open attempt: {self.SESSION_OPEN}"
            ],
        )

    def close_while_test_2_cannot_be_read(self, steps: str) -> None:
        """The steps of REENROLLED_OPEN, so box1 has a recorded enrollment (enr-1) and an open
        session entry (enr-2). box2's run dir records sid-1. Then `steps`, the close and the grace
        (0 s)."""
        box2 = self.run_base / "box2"
        box2.mkdir()
        (box2 / "enrollment-session").write_text("sid-1\n")
        self.close(f"{self.REENROLLED_OPEN}\n{steps}", STUB_ENROLL="broker unknown", AGENTBOX_UNENROLL_GRACE="0")

    def assert_test_2_not_read(self, status: int) -> None:
        """The waiter made no learn call and no unenroll call after the close, the session's key
        dir is still there, and unenroll.log holds one line: test 2 could not be read, with the
        status of session-live-box and its empty output, and enr-1 and the open entry left
        alone."""
        self.assertEqual(self.learn_calls(), [])
        self.assertEqual(self.revokes_after_close(), [])
        self.assertTrue((self.run_base / "session-keys" / "sid-1" / "key.pem").exists())
        self.assertEqual(
            self.unenroll_log(),
            [
                f"box1: session sid-1: test 2 could not be read (session-live-box exited {status} and"
                " printed ''), so an enrollment that no box renews ends with its lease (15 min);"
                f" enrollment enr-1 left alone, and the enrollment of each open attempt: {self.SESSION_OPEN}"
            ],
        )

    def test_a_waiter_that_cannot_ask_docker_leaves_the_enrollments_alone(self) -> None:
        """`docker ps` fails from the close on, so at the waiter's wake session-live-box cannot
        tell whether box2 has a container. It exits 2 with no output, and the waiter does not
        read that as "no other box is live"."""
        self.close_while_test_2_cannot_be_read("export STUB_PS_FAIL=1")
        self.assert_test_2_not_read(2)

    def self_that_only(self, body: str) -> str:
        """The step that makes the waiter's SELF a script that only runs `body`: DOTFILES_DIR names
        a dir whose scripts/agentbox is that script, and unenroll_box gives that file to the waiter
        as SELF."""
        scripts = self.root / "dotfiles-stub" / "scripts"
        scripts.mkdir(parents=True)
        write_stub(scripts, "agentbox", body)
        return f'DOTFILES_DIR="{scripts.parent}"'

    def test_a_session_live_box_with_no_answer_leaves_the_enrollments_alone(self) -> None:
        """The waiter's session-live-box exits 0 with no output, as this script can when a publish
        replaces it during the read."""
        self.close_while_test_2_cannot_be_read(self.self_that_only("exit 0"))
        self.assert_test_2_not_read(0)

    def test_a_session_live_box_that_ends_before_it_looks_leaves_the_enrollments_alone(self) -> None:
        """The waiter's session-live-box exits 1 with no output, as this script does when a
        command ends it under set -e before it looks. Status 1 alone is not the answer `none`."""
        self.close_while_test_2_cannot_be_read(self.self_that_only("exit 1"))
        self.assert_test_2_not_read(1)

    def test_a_session_that_came_back_keeps_the_open_enrollment(self) -> None:
        """Each enroll call of box1 for session sid-1 ends with its outcome not known, and after
        the close `owner` names box2, as when box2 enrolls for the session during the grace. The
        grace lasts until the body has written that (STUB_GRACE_UNTIL), and the waiter's test 1
        sees the change: no learn call, and one line that says the session came back."""
        grace_over = self.run_base / "grace-over"
        self.close(
            'enroll_box box1 sid-1 || true',
            after=f'echo box2 >"$RUN_BASE/session-keys/sid-1/owner"\n: >"{grace_over}"',
            STUB_ENROLL="unknown",
            AGENTBOX_UNENROLL_GRACE="180",
            STUB_GRACE_UNTIL=str(grace_over),
        )
        self.assertEqual(self.learn_calls(), [])
        self.assertEqual(
            self.unenroll_log(),
            [
                "box1: session sid-1 came back in box2; enrollment none left alone,"
                f" and the enrollment of each open attempt: {self.SESSION_OPEN}"
            ],
        )

    def test_a_learn_call_that_fails_leaves_the_enrollment_to_its_lease(self) -> None:
        """The learn call is refused ENROLL_FAILED, or ends with its outcome not known. unenroll.log
        holds one line, which names the open attempt and the client's answer and says that the
        enrollment ends with its lease; the close makes no unenroll call."""
        for word, code in (("known", "ENROLL_FAILED"), ("unknown", "ENROLL_OUTCOME_UNKNOWN")):
            with self.subTest(learn=word):
                self.reset_box()
                self.close('enroll_box box1 "" || true', STUB_ENROLL="unknown", STUB_LEARN=word)
                log = self.unenroll_log()
                self.assertEqual(len(log), 1, log)
                self.assertRegex(
                    log[0],
                    rf"^box1: open attempt '{self.OWN_OPEN}' not learned: agent-secrets enroll exited 1 and"
                    rf" printed '': agent-secrets enroll: {code}: .*; an enrollment, if the attempt made"
                    r" one, ends with its lease \(15 min\)$",
                )
                self.assertEqual(self.revokes_after_close(), [])

    # The two ways to a revoke that the close's detached process makes: the id a learn call got
    # (box1 with no session id, its enroll calls open), and a session's recorded id (the waiter,
    # after its grace). Each: the body's steps, the runtime id, the stubs.
    REVOKE_PATHS = {
        "a learned id": ('enroll_box box1 "" || true', "box1", {"STUB_ENROLL": "unknown"}),
        "a session's recorded id": (
            "enroll_box box1 sid-1",
            "session-sid-1",
            {"STUB_ENROLL": "broker", "AGENTBOX_UNENROLL_GRACE": "0"},
        ),
    }

    def test_a_revoke_whose_outcome_is_not_known_is_made_once_more(self) -> None:
        """The first unenroll call ends UNENROLL_OUTCOME_UNKNOWN and the second succeeds: two
        unenroll calls of enr-1 after the close, and the broker's row of enr-1 has ended."""
        for path, (steps, runtime, stubs) in self.REVOKE_PATHS.items():
            with self.subTest(path=path):
                self.reset_box()
                self.close(steps, STUB_UNENROLL="unknown ok", **stubs)
                self.assertEqual(self.revokes_after_close(), ["unenroll --helper --enrollment enr-1"] * 2)
                self.assertEqual(self.broker_rows(runtime), [["enr-1", runtime, THUMBPRINT, "ended"]])

    def test_a_revoke_refused_unenroll_failed_is_not_made_again(self) -> None:
        """The unenroll call is refused UNENROLL_FAILED, a known outcome: one unenroll call of
        enr-1 after the close, and the broker's row of enr-1 is still live."""
        for path, (steps, runtime, stubs) in self.REVOKE_PATHS.items():
            with self.subTest(path=path):
                self.reset_box()
                self.close(steps, STUB_UNENROLL="known", **stubs)
                self.assertEqual(self.revokes_after_close(), ["unenroll --helper --enrollment enr-1"])
                self.assertEqual(self.broker_rows(runtime), [["enr-1", runtime, THUMBPRINT, "live"]])

    def test_the_close_does_not_wait_for_the_learn_call(self) -> None:
        """The stub's learn call answers only once a file exists (STUB_LEARN_AFTER), and the body
        writes that file only after unenroll_box has returned. The learn call then answers, and
        the id it got is revoked. A close that waited for the learn call would wait for that file
        for 120 s, past the run's 60 s timeout."""
        returned = self.run_base / "unenroll-box-returned"
        self.close(
            'enroll_box box1 "" || true',
            close="unenroll_box box1",
            after=f': >"{returned}"',
            timeout=60,
            STUB_ENROLL="unknown",
            STUB_LEARN_AFTER=str(returned),
        )
        self.assert_one_learn_call("box1")
        self.assertEqual(self.revokes_after_close(), ["unenroll --helper --enrollment enr-1"])

    def test_a_kill_of_the_launchers_process_group_does_not_stop_the_learn_call(self) -> None:
        """The body runs in a process group of its own, as a launcher does, and sleeps once
        close_box has returned. Each enroll call of box1 (no session id) ends with its outcome not
        known, and the learn call answers only once a file exists (STUB_LEARN_AFTER). Once
        close_box has returned (within 120 s), the body's process group gets SIGKILL, as a
        cancelled `agentbox move` sends it 30 s after TERM (move_to_cancel_start), and then the
        file is written. Within 60 s unenroll.log holds one line, the revoke of enr-1 (runtime id
        box1), and the broker's row of enr-1 has ended."""
        gate = self.root / "learn-gate"
        returned = self.root / "close-box-returned"
        revoked = "box1: enrollment enr-1 (runtime id box1) revoked"
        # /bin/sleep, not the stub, which returns at once: the body is still in its process group
        # when the kill comes.
        body = (
            'enroll_box box1 "" || true\necho close >>"$STUB_CALLS"\nclose_box box1 ""\n'
            f': >"{returned}"\n/bin/sleep 60'
        )
        proc = subprocess.Popen(
            ["bash", "-c", self.bash_script(body)],
            env=self.bash_env(STUB_GONE="1", STUB_ENROLL="unknown", STUB_LEARN_AFTER=str(gate)),
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            wait_until(returned.exists, 120)
        finally:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=10)
            gate.write_text("")
        # The revoke line is the detached process's last write, so waiting for it also keeps that
        # process from outliving the temp dir.
        wait_until(lambda: revoked in self.unenroll_log(), 60)
        self.assertTrue(returned.exists(), "close_box did not return")
        self.assertEqual(self.unenroll_log(), [revoked])
        self.assertEqual(self.broker_rows("box1"), [["enr-1", "box1", THUMBPRINT, "ended"]])

    def test_a_hangup_stops_the_attempts_and_the_close_learns_the_one_made(self) -> None:
        """A HUP comes during enroll_box's first call (STUB_HUP_PID), and a HUP trap sets
        `interrupted`, as cmd_new's does. Every call ends with its outcome not known. enroll_box
        makes one call, not ten, and the close learns that call's enrollment and revokes it."""
        self.close(
            "trap 'interrupted=1' HUP\nexport STUB_HUP_PID=$$\nenroll_box box1 \"\" || true", STUB_ENROLL="unknown"
        )
        before, _ = self.calls_around_close("enroll ")
        self.assertEqual(len(before), 1, before)
        self.assert_one_learn_call("box1")
        self.assertEqual(self.revokes_after_close(), ["unenroll --helper --enrollment enr-1"])


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


class Reenroll(EnrollFixture):
    """The launcher enrolls its box again when the box's renew loop marks the enrollment not live.

    reenroll_if_not_live acts on enrollment.not-live with the key the box holds and the runtime
    id, thumbprint and session id enroll_box left in the launcher's globals, so each case runs
    enroll_box and the launcher's passes in one bash process. The enroll stub answers as the broker
    does (STUB_ENROLL=broker), and a revoke at the broker is `agent-secrets unenroll --helper`.
    Each case runs for a box with a session id (the host's session key, runtime id session-<sid>)
    and for one without (a key made in the box, runtime id box1).
    """

    BOXES = {"with a session id": ("sid-1", "session-sid-1"), "without one": ("", "box1")}
    MARKER = '"$(box_keydir box1)/enrollment.not-live"'
    REVOKE = 'agent-secrets unenroll --helper --enrollment "$(<"$RUN_BASE/box1/enrollment-id")"'
    PASS = "reenroll_if_not_live box1"
    ENROLLED_AGAIN = ["agentbox: box1 enrolled again: enrollment enr-1 -> enr-2"]

    def run_steps(self, sid: str, steps: str, timeout: float = 300, **stubs: str) -> subprocess.CompletedProcess[str]:
        """enroll_box, a copy of the key it left (key-before) and that file's inode, then `steps`,
        then a line that shows every step ran under the script's set -euo pipefail."""
        result = self.bash(
            f'enroll_box box1 "{sid}"\n'
            'cp "$(box_keydir box1)/key.pem" "$RUN_BASE/key-before"\n'
            'stat -c %i "$(box_keydir box1)/key.pem" >"$RUN_BASE/key-before.inode"\n'
            f"{steps}\n"
            "echo steps-done",
            timeout=timeout,
            STUB_ENROLL="broker",
            **stubs,
        )
        self.assertIn("steps-done", result.stdout, result.stderr)
        return result

    def assert_key_kept(self, keydir: Path | None = None) -> None:
        self.assertEqual(((keydir or self.keydir) / "key.pem").read_bytes(), (self.run_base / "key-before").read_bytes())

    def assert_one_enroll_call_as_recorded(self, sid: str, runtime: str, ended: bool = True) -> None:
        """One enroll call after enroll_box's, just like it (assert_enrolled_again_as_recorded),
        and no key made or copied in since: the same key.pem, the same file."""
        self.assert_enrolled_again_as_recorded(sid, runtime, ended)
        in_box = [c for c in self.calls_matching("docker ") if " keygen " in c]
        self.assertEqual(len(self.calls_matching("keygen ")) + len(in_box), 1)
        self.assert_key_kept()
        inode = (self.run_base / "key-before.inode").read_text().strip()
        self.assertEqual(str((self.keydir / "key.pem").stat().st_ino), inode)

    def test_no_other_renew_exit_makes_an_enroll_call(self) -> None:
        """Case 2 of the plan (dispatch://INFRA-3462): renew exits 0, 1, 2 or 143, or prints a
        PROOF_INVALID that does not say not live and exits 1. The launcher's pass after the loop,
        in the same process after enroll_box, makes no enroll call beyond enroll_box's own. The
        line after the pass shows it ran, and the count comes before the marker, so a loop that
        marks every non-zero exit fails on the count."""
        for shape, (sid, _runtime) in self.BOXES.items():
            for mode in ("exit=0", "exit=1", "exit=2", "exit=143", "invalid"):
                with self.subTest(box=shape, renew=mode):
                    self.reset_box()
                    # The stub ends the loop by killing it, so start_renew fails.
                    self.run_steps(sid, f"start_renew box1 || true\n{self.PASS}", STUB_RUN_RENEW="1", STUB_RENEW=mode)
                    self.assertEqual(len(self.calls_matching("enroll ")), 1)
                    self.assertFalse(self.marked())

    def test_an_enrollment_not_live_is_made_again_with_the_key_the_box_holds(self) -> None:
        """Case 3 of the plan (dispatch://INFRA-3462): the broker revoked the enrollment and the
        loop marked it. One enroll call, as recorded, with no key made and key.pem's bytes
        unchanged; the broker ends with the old row ended and a new one live; the host's record
        and renew.log name the new id; the marker is gone."""
        for shape, (sid, runtime) in self.BOXES.items():
            with self.subTest(box=shape):
                self.reset_box()
                self.run_steps(sid, f"{self.REVOKE}\n: >{self.MARKER}\n{self.PASS}")
                self.assert_one_enroll_call_as_recorded(sid, runtime)
                self.assertEqual((self.hostdir / "enrollment-id").read_text(), "enr-2\n")
                self.assertEqual((self.keydir / "enrollment").read_text(), "enr-2\n")
                self.assertEqual(self.launcher_lines(), self.ENROLLED_AGAIN)
                # The marker, enrollment.pending and enrollment.error are gone.
                self.assertEqual(self.files(), {"key.pem", "enrollment", "renew.log"})
                if sid:
                    session = self.run_base / "session-keys" / sid
                    self.assertEqual((self.hostdir / "enrollment-session").read_text(), f"{sid}\n")
                    self.assertEqual((session / "enrollment-id").read_text(), "enr-2\n")
                    self.assertEqual((session / "owner").read_text(), "box1\n")
                else:
                    self.assertFalse((self.hostdir / "enrollment-session").exists())

    def test_a_stray_marker_on_a_live_enrollment_makes_no_second_enrollment(self) -> None:
        """Case 4 of the plan (dispatch://INFRA-3462): a marker while the enrollment is live
        costs one enroll call, which gets the same id back; the broker still holds one row, the
        log says the enrollment is live, and the marker is gone."""
        for shape, (sid, runtime) in self.BOXES.items():
            with self.subTest(box=shape):
                self.reset_box()
                self.run_steps(sid, f": >{self.MARKER}\n{self.PASS}")
                self.assert_one_enroll_call_as_recorded(sid, runtime, ended=False)
                self.assertEqual((self.hostdir / "enrollment-id").read_text(), "enr-1\n")
                self.assertEqual(self.launcher_lines(), ["agentbox: enrollment enr-1 is live; box1 not enrolled again"])
                self.assertEqual(self.files(), {"key.pem", "enrollment", "renew.log"})

    def test_a_box_that_no_longer_holds_its_key_is_given_one_and_enrolled_again(self) -> None:
        """The broker revoked the enrollment, and the box removed key.pem before the launcher
        found the marker, so reenroll_box runs enroll_box's key step again. A box with a session
        id gets the host's session key back, the same bytes, and enrolls it as recorded; a box
        with none makes a key in the box and enrolls that key's thumbprint under its own name.
        Either way one enroll call makes a new id, and nothing reaches the launcher's stderr."""
        for shape, (sid, runtime) in self.BOXES.items():
            with self.subTest(box=shape):
                self.reset_box()
                result = self.run_steps(
                    sid, f'{self.REVOKE}\nrm "$(box_keydir box1)/key.pem"\n: >{self.MARKER}\n{self.PASS}'
                )
                self.assertEqual(result.stderr, "")
                self.assertEqual(self.launcher_lines(), self.ENROLLED_AGAIN)
                self.assertEqual((self.hostdir / "enrollment-id").read_text(), "enr-2\n")
                self.assertEqual(self.files(), {"key.pem", "enrollment", "renew.log"})
                in_box = [c for c in self.calls_matching("docker ") if " keygen " in c]
                if sid:
                    self.assert_enrolled_again_as_recorded(sid, runtime)
                    session_key = self.run_base / "session-keys" / sid / "key.pem"
                    self.assertEqual((self.keydir / "key.pem").read_bytes(), session_key.read_bytes())
                    self.assertEqual((len(self.calls_matching("keygen ")), len(in_box)), (1, 0))
                else:
                    # The docker stub's second keygen in the box prints this thumbprint.
                    new_tp = f"K{2:042d}"
                    enrolls = self.enrolls()
                    self.assertEqual(len(enrolls), 2, enrolls)
                    self.assertTrue(enrolls[1].endswith(f"--runtime-id box1 --thumbprint {new_tp}"), enrolls[1])
                    self.assertEqual(
                        self.broker_rows(runtime),
                        [["enr-1", runtime, THUMBPRINT, "ended"], ["enr-2", runtime, new_tp, "live"]],
                    )
                    # enroll_box's keygen in the box, and this one.
                    self.assertEqual(len(in_box), 2)
                    self.assertNotEqual((self.keydir / "key.pem").read_bytes(), (self.run_base / "key-before").read_bytes())

    def test_a_box_whose_session_key_is_gone_too_enrolls_under_its_own_name(self) -> None:
        """The box removed key.pem, the host's key dir for the session is gone (prune_session_keys
        removes one untouched for 7 days), and the session's enrollment is still live under the
        old key. The host makes the session a new key, which the broker refuses with 409 under
        the live runtime id, and the same attempt falls back to the box's own runtime id, as
        enroll_box does: one pass enrolls the box under its own name, and the host's record that
        the box's enrollment is the session's (enrollment-session) goes, so close_box revokes the
        new one at once. enroll_attempts prints a note on stderr for the fallback, and the
        launcher's stderr holds nothing."""
        new_tp = "B" * 43
        steps = "\n".join(
            [
                'rm "$(box_keydir box1)/key.pem"',
                'rm -r "$RUN_BASE/session-keys/sid-1"',
                f"export STUB_KEY=k2 STUB_THUMBPRINT={new_tp}",
                f": >{self.MARKER}",
                self.PASS,
            ]
        )
        result = self.run_steps("sid-1", steps)
        self.assertEqual(result.stderr, "")
        enrolls = self.enrolls()
        self.assertEqual([c.split("--runtime-id ")[1].split()[0] for c in enrolls], ["session-sid-1", "session-sid-1", "box1"])
        self.assertTrue(enrolls[1].endswith(f"--thumbprint {new_tp} --session-id sid-1"), enrolls[1])
        self.assertTrue(enrolls[2].endswith(f"--thumbprint {new_tp} --session-id sid-1"), enrolls[2])
        self.assertEqual(self.broker_rows("session-sid-1"), [["enr-1", "session-sid-1", THUMBPRINT, "live"]])
        self.assertEqual(self.broker_rows("box1"), [["enr-2", "box1", new_tp, "live"]])
        self.assertEqual((self.hostdir / "enrollment-id").read_text(), "enr-2\n")
        self.assertFalse((self.hostdir / "enrollment-session").exists())
        self.assertEqual(self.launcher_lines(), self.ENROLLED_AGAIN)
        self.assertEqual(self.files(), {"key.pem", "enrollment", "renew.log"})

    # snap NAME: the key dir as it is, and the count of enroll calls so far, beside the box.
    SNAP = (
        'snap() { cp -a "$(box_keydir box1)" "$RUN_BASE/snap-$1";'
        ' grep -c "^enroll " "$STUB_CALLS" >"$RUN_BASE/snap-$1.enrolls" || true; }'
    )

    def test_a_failed_attempt_keeps_the_key_and_tries_again_after_the_back_off(self) -> None:
        """Case 5 of the plan (dispatch://INFRA-3462): the broker unreachable, then the helper
        with no launcher credential, then success. Each failure keeps key.pem and the marker,
        puts its reason in enrollment.error, leaves no enrollment.pending, and is not tried again
        before its back-off (30 s, then 60 s, each plus up to 9 s); the test moves SECONDS on, as
        no pass sleeps."""
        steps = "\n".join(
            [
                self.SNAP,
                self.REVOKE,
                f": >{self.MARKER}",
                "export STUB_ENROLL=fail",
                f"{self.PASS}; snap broker-down",
                f"{self.PASS}; snap broker-down-again-at-once",
                "SECONDS=$((SECONDS + 1000))",
                "export STUB_ENROLL=broker STUB_LOGIN_STATUS=1",
                f"{self.PASS}; snap logged-out",
                f"{self.PASS}; snap logged-out-again-at-once",
                "SECONDS=$((SECONDS + 1000))",
                "export STUB_LOGIN_STATUS=0",
                f"{self.PASS}; snap enrolled",
            ]
        )
        unreachable = "agent-secrets enroll failed: broker unreachable"
        unready = "the helper holds no live launcher credential (login-status: none; run: agent-secrets-login <email>)"
        failed = {"key.pem", "enrollment", "enrollment.error", "enrollment.not-live", "renew.log"}
        for shape, (sid, _runtime) in self.BOXES.items():
            with self.subTest(box=shape):
                self.reset_box()
                self.run_steps(sid, steps)
                for snap, reason in (
                    ("broker-down", unreachable),
                    ("broker-down-again-at-once", unreachable),
                    ("logged-out", unready),
                    ("logged-out-again-at-once", unready),
                ):
                    keydir = self.run_base / f"snap-{snap}"
                    self.assertEqual((self.run_base / f"snap-{snap}.enrolls").read_text(), "2\n", snap)
                    self.assertEqual(self.files(keydir), failed, snap)
                    self.assertEqual((keydir / "enrollment.error").read_text(), f"{reason}\n", snap)
                    self.assert_key_kept(keydir)
                enrolled = self.run_base / "snap-enrolled"
                self.assertEqual((self.run_base / "snap-enrolled.enrolls").read_text(), "3\n")
                self.assertEqual(self.files(enrolled), {"key.pem", "enrollment", "renew.log"})
                self.assertEqual((enrolled / "enrollment").read_text(), "enr-2\n")
                self.assert_key_kept(enrolled)
                # enrollment.pending was there for each attempt that reached the broker.
                for call in self.calls_matching("enroll ")[1:]:
                    self.assertNotIn("marker-mtime=none", call)
                lines = self.launcher_lines()
                self.assertEqual(len(lines), 3, lines)
                for line, reason, backoff in ((lines[0], unreachable, 30), (lines[1], unready, 60)):
                    m = re.fullmatch(rf"agentbox: enrolling box1 again failed: {re.escape(reason)}; next try in (\d+)s", line)
                    self.assertIsNotNone(m, line)
                    self.assertIn(int(m.group(1)), range(backoff, backoff + 10), line)
                self.assertEqual(lines[2], "agentbox: box1 enrolled again: enrollment enr-1 -> enr-2")

    def test_a_marker_that_is_a_link_or_a_fifo_is_neither_read_nor_followed(self) -> None:
        """Case 7 of the plan (dispatch://INFRA-3462): the box can put anything at
        enrollment.not-live. The launcher only tests that the name is there and removes it: a
        FIFO (which blocks whoever opens it), a link to one, a link to a host file and a link to
        nothing each count as the marker, the launcher does not hang (the run's timeout is 60 s),
        and what a link names is left as it was."""
        plants = {
            "a FIFO": 'mkfifo "$m"',
            "a link to a FIFO": 'mkfifo "$RUN_BASE/outside" && ln -s "$RUN_BASE/outside" "$m"',
            "a link to a host file": 'echo "host content" >"$RUN_BASE/outside" && ln -s "$RUN_BASE/outside" "$m"',
            "a link to nothing": 'ln -s "$RUN_BASE/outside" "$m"',
        }
        outside = self.run_base / "outside"
        for shape, (sid, _runtime) in self.BOXES.items():
            for plant, how in plants.items():
                with self.subTest(box=shape, marker=plant):
                    self.reset_box()
                    self.run_steps(sid, f"{self.REVOKE}\nm={self.MARKER}\n{how}\n{self.PASS}", timeout=60)
                    self.assertEqual(self.launcher_lines(), self.ENROLLED_AGAIN)
                    self.assertFalse(self.marked())
                    if plant == "a link to a FIFO":
                        self.assertTrue(stat.S_ISFIFO(outside.lstat().st_mode))
                    elif plant == "a link to a host file":
                        self.assertEqual(outside.read_text(), "host content\n")
                    elif plant == "a link to nothing":
                        self.assertFalse(os.path.lexists(outside))

    def test_no_attempt_once_the_launcher_is_interrupted(self) -> None:
        """cmd_new's HUP, TERM or INT trap has set `interrupted`: the box is ending, so a pass
        that finds the marker makes no enroll call, writes no line, and leaves the marker."""
        for shape, (sid, _runtime) in self.BOXES.items():
            with self.subTest(box=shape):
                self.reset_box()
                self.run_steps(sid, f"{self.REVOKE}\n: >{self.MARKER}\ninterrupted=1\n{self.PASS}")
                self.assertEqual(len(self.enrolls()), 1)
                self.assertTrue(self.marked())
                self.assertEqual(self.launcher_lines(), [])

    def test_a_box_enroll_box_did_not_enroll_is_never_enrolled_by_a_marker(self) -> None:
        """Only the box that enroll_box enrolled (ENROLLED_BOX) is enrolled again. A box with no
        broker identity, or whose first enrollment failed, has no enrollment for its renew loop to
        find not live, so a marker there is the box's own doing. The broker is ready for the pass,
        so only that test stops it: no enroll call, no keygen, no line, and the marker left."""
        cases = {"a box enroll_box never enrolled": ""}
        for shape, (sid, _runtime) in self.BOXES.items():
            cases[f"a box {shape} whose enroll_box failed"] = f'enroll_box box1 "{sid}" || true'
        for case, first in cases.items():
            with self.subTest(case=case):
                self.reset_box()
                result = self.bash(
                    f"{first}\n"
                    'touch "$STUB_CALLS"\nbefore=$(wc -l <"$STUB_CALLS")\n'
                    f": >{self.MARKER}\n{self.PASS}\n"
                    'tail -n "+$((before + 1))" "$STUB_CALLS" >"$RUN_BASE/pass-calls"\n'
                    "echo steps-done",
                    STUB_ENROLL="fail",
                )
                self.assertIn("steps-done", result.stdout, result.stderr)
                calls = (self.run_base / "pass-calls").read_text().splitlines()
                self.assertEqual([c for c in calls if c.startswith("enroll ") or "keygen " in c], [], calls)
                self.assertEqual(self.launcher_lines(), [])
                self.assertTrue(self.marked())

    def test_a_link_flipped_at_the_key_dirs_name_takes_no_host_write(self) -> None:
        """The review's case (sjawhar/dotfiles#57), for a box with a session id. The broker ended
        the enrollment; the box removes key.pem and puts the marker, so the re-enrollment copies
        the session's key in again. The box also moves what is at its key dir's name in run-user/
        aside and links that name to the moved copy, and a loop beside the launcher
        (flip_when_pending) points the link at the host's /run/user/<uid>/agent-secrets once
        enrollment.pending shows through it. While the key dir was run-user/agent-secrets, the
        launcher's next writes, the session's key.pem and the enrollment, went into the host dir.
        The host dir keeps only its socket, the marker never shows through run-user/, and the
        box's key dir holds the session's key and the new enrollment."""
        host = self.host_keydir()
        moved = self.run_user_keydir.with_name("agent-secrets.moved")
        steps = "\n".join(
            [
                self.REVOKE,
                'rm "$(box_keydir box1)/key.pem"',
                f": >{self.MARKER}",
                f'mv "{self.run_user_keydir}" "{moved}"',
                f'ln -s "{moved}" "{self.run_user_keydir}"',
                self.PASS,
            ]
        )
        with self.flip_when_pending(host) as flipped:
            self.run_steps("sid-1", steps)
        self.assertEqual(self.files(host), {"helper.sock"})
        self.assertFalse(flipped.is_set(), "enrollment.pending showed through the box's run-user/")
        session_key = self.run_base / "session-keys" / "sid-1" / "key.pem"
        self.assertEqual((self.keydir / "key.pem").read_bytes(), session_key.read_bytes())
        self.assertEqual((self.keydir / "enrollment").read_text(), "enr-2\n")
        self.assertEqual(self.launcher_lines(), self.ENROLLED_AGAIN)

    def test_a_hangup_during_an_attempt_leaves_stderr_where_the_trap_put_it(self) -> None:
        """cmd_new's HUP trap points stdout and stderr at /dev/null, as nothing reads a hung-up
        terminal. A hangup during the attempt's enroll call (STUB_HUP_PID) runs that trap inside
        the pass's own redirect of stderr, whose end puts the terminal (here, the run's stderr)
        back; the pass makes the trap's redirect again, so a line written after it goes nowhere.
        stdout is /dev/null from the trap on, so the steps' end is a file, not a line."""
        for shape, (sid, _runtime) in self.BOXES.items():
            with self.subTest(box=shape):
                self.reset_box()
                result = self.bash(
                    f'enroll_box box1 "{sid}"\n'
                    "trap 'interrupted=1; hung_up=1; exec >/dev/null 2>&1' HUP\n"
                    f"{self.REVOKE}\n: >{self.MARKER}\n"
                    "export STUB_HUP_PID=$$\n"
                    f"{self.PASS}\n"
                    'echo "a line after the hangup" >&2\n'
                    ': >"$RUN_BASE/steps-done"',
                    STUB_ENROLL="broker",
                )
                self.assertTrue((self.run_base / "steps-done").exists(), result.stderr)
                self.assertEqual(result.stderr, "")
                # The hangup did not stop the attempt it came in.
                self.assertEqual(self.launcher_lines(), self.ENROLLED_AGAIN)


class BoxWait(EnrollFixture):
    """box_wait, the launcher's wait for an enrolled box, against a real child and a real
    `sleep 10` (STUB_REAL_SLEEP): it returns as soon as the child ends, leaving the child's status
    to `wait`, and a trapped HUP, TERM or INT acts at once, as in the blocking `wait` before it. A
    box_wait that slept its 10 s through would take that long."""

    def test_returns_when_the_child_ends_and_leaves_it_its_status(self) -> None:
        started = time.monotonic()
        result = self.bash(
            "( /bin/sleep 1; exit 7 ) </dev/null >/dev/null 2>&1 &\nchild=$!\n"
            'box_wait box1 "$child"\n'
            'if wait "$child"; then echo rc=0; else echo "rc=$?"; fi',
            STUB_REAL_SLEEP="1",
        )
        self.assertIn("rc=7", result.stdout, result.stderr)
        self.assertLess(time.monotonic() - started, 8)

    def test_a_trapped_signal_acts_at_once(self) -> None:
        for signal in ("HUP", "TERM", "INT"):
            with self.subTest(signal=signal):
                started = time.monotonic()
                result = self.bash(
                    "/bin/sleep 30 </dev/null >/dev/null 2>&1 &\nchild=$!\n"
                    f"trap 'echo trapped; kill \"$child\"' {signal}\n"
                    f"( /bin/sleep 1; kill -{signal} $$ ) </dev/null >/dev/null 2>&1 &\n"
                    'box_wait box1 "$child"\n'
                    "echo box-wait-returned",
                    STUB_REAL_SLEEP="1",
                )
                self.assertEqual(result.stdout.split(), ["trapped", "box-wait-returned"], result.stderr)
                self.assertLess(time.monotonic() - started, 8)

    def test_a_child_that_ends_during_the_pass_is_not_waited_for(self) -> None:
        """The child ends while the pass's reenroll_if_not_live runs, as during an enroll call, and
        bash has reaped it before the pass would wait: box_wait returns in well under the 10 s of
        that wait, and its stderr holds no `no such job`. With bash 5.2.15 (Debian bookworm), a
        `wait -n` with the reaped child's pid writes `no such job` and waits for all of the sleep;
        with bash 5.2.37 it does not."""
        started = time.monotonic()
        result = self.bash(
            "/bin/sleep 30 </dev/null >/dev/null 2>&1 &\nchild=$!\n"
            # kill -0 fails only once bash has reaped the child: a zombie still takes signal 0.
            "reenroll_if_not_live() {\n"
            '    kill "$child"\n'
            '    while kill -0 "$child" 2>/dev/null; do /bin/sleep 0.1; done\n'
            "    /bin/sleep 0.5\n"
            "}\n"
            'box_wait box1 "$child"\n'
            "echo box-wait-returned",
            STUB_REAL_SLEEP="1",
        )
        self.assertEqual(result.stdout.split(), ["box-wait-returned"], result.stderr)
        self.assertNotIn("no such job", result.stderr)
        self.assertLess(time.monotonic() - started, 5)


class CmdNew(EnrollFixture):
    """cmd_new gives a box a marker and an enrollment only when the host can enroll it, and enrolls
    it again while it runs when its renew is refused as not live."""

    # The steps that need a real host are no-ops; mount_args makes the key dir and its mount point
    # in run-user/, and close_box, the launcher's last step, records the enrollment id its revoke
    # would name.
    NO_HOST = (
        "build_image() { :; }; ensure_network() { :; }; ensure_forwarders() { :; }\n"
        "check_box_disk_space() { :; };\n"
        'mount_args() { mkdir -p "$(box_keydir "$1")" "$RUN_BASE/$1/run-user/agent-secrets"; }\n'
        "session_env() { :; }; clone_checkout() { :; }\n"
        'close_box() { echo "close_box $1 revokes=$(cat "$RUN_BASE/$1/enrollment-id" 2>/dev/null)" >>"$STUB_CALLS"; }\n'
    )
    SID = "0192f3a4-5b6c-7d8e-9f01-23456789abcd"

    def new_box(self, cmd: str = "bash -c true", **stubs: str) -> Path:
        (self.root / "src" / "repo1" / ".jj").mkdir(parents=True)
        result = self.bash(f"{self.NO_HOST}cmd_new repo1 -- {cmd}", **stubs)
        self.assertEqual(result.returncode, 0, result.stderr)
        (box,) = self.run_base.glob("agentbox-*")
        return box / keydir_in_box_dir()

    def test_a_host_that_can_enroll_marks_and_enrolls_the_box(self) -> None:
        keydir = self.new_box(STUB_LOGIN_STATUS="0")
        (started,) = self.calls_matching("run ")
        self.assertTrue(started.endswith("marker=yes"), started)
        self.assertEqual(self.files(keydir), {"key.pem", "enrollment"})
        self.assertEqual(len(self.calls_matching("enroll ")), 1)

    def test_a_host_that_cannot_enroll_leaves_the_box_alone(self) -> None:
        keydir = self.new_box(STUB_LOGIN_STATUS="1")
        (started,) = self.calls_matching("run ")
        self.assertTrue(started.endswith("marker=no"), started)
        self.assertEqual(list(keydir.iterdir()), [])
        self.assertEqual(self.calls_matching("enroll "), [])
        self.assertEqual([c for c in self.calls_matching("docker ") if " keygen " in c], [])

    def renew_refused_while_it_runs(self, cmd: str, **stubs: str) -> tuple[str, Path, list[str]]:
        """cmd_new for a box whose first renew is refused as not live (the broker ended its
        enrollment) and whose second exits 0 and ends the loop. The box lives until its launcher
        has logged the re-enrollment, then ends its renew loop and says `run-ended <box>`. Returns
        the box, its key dir and the call log."""
        keydir = self.new_box(
            cmd,
            STUB_LOGIN_STATUS="0",
            STUB_ENROLL="broker",
            STUB_RUN_RENEW="bg",
            STUB_RENEW="notlive exit=0",
            STUB_RENEW_RUNS="2",
            STUB_RUN_UNTIL="enrolled again",
            **stubs,
        )
        return keydir.relative_to(self.run_base).parts[0], keydir, self.calls.read_text().splitlines()

    def assert_enrolled_again_while_it_ran(self, box: str, keydir: Path, calls: list[str], sid: str, runtime: str) -> None:
        """One re-enrollment, as recorded, before the box ended; close_box's revoke, after the box
        ended, names the new id."""
        self.assert_enrolled_again_as_recorded(sid, runtime)
        self.assertIn(
            "agentbox: agent-secrets renew exited 76: enrollment enr-1 is not live; the host enrols this box again",
            (keydir / "renew.log").read_text(),
        )
        self.assertEqual(self.launcher_lines(keydir), [f"agentbox: {box} enrolled again: enrollment enr-1 -> enr-2"])
        self.assertIn(f"run-ended {box}", calls)
        ended = calls.index(f"run-ended {box}")
        self.assertIn(f"close_box {box} revokes=enr-2", calls[ended:])

    def test_an_omp_box_refused_as_not_live_is_enrolled_again_by_its_wait(self) -> None:
        """Case 6 of the plan (dispatch://INFRA-3462), a box with a session id. Its session-id
        poll ends once the id is known, so box_wait enrolls it again: its renew loop starts only
        once box_wait's first `sleep 10` is in the call log. No pass after the box ended starts an
        attempt: the box's end writes a fresh marker and its `docker run` exits at once, while
        box_wait is inside a real 10 s wait (STUB_REAL_SLEEP) that the child's exit ends, so only
        a pass after that exit could act on the marker. The marker is still there when cmd_new
        returns, and there are still two enroll calls."""
        box, keydir, calls = self.renew_refused_while_it_runs(
            "omp", STUB_SID=self.SID, STUB_RENEW_AFTER="sleep 10", STUB_RUN_END_MARKER="1", STUB_REAL_SLEEP="1"
        )
        self.assert_enrolled_again_while_it_ran(box, keydir, calls, self.SID, f"session-{self.SID}")
        self.assertTrue(self.marked(keydir))

    def test_a_box_without_a_session_refused_as_not_live_is_enrolled_again_by_its_poll(self) -> None:
        """Case 6 of the plan (dispatch://INFRA-3462), a box with no session id: its session-id
        poll lasts its whole life and enrolls it again."""
        box, keydir, calls = self.renew_refused_while_it_runs("bash -c true")
        self.assert_enrolled_again_while_it_ran(box, keydir, calls, "", box)


if __name__ == "__main__":
    unittest.main()
