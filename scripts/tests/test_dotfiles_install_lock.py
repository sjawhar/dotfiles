#!/usr/bin/env python3
"""R1 of dispatch://INFRA-4798: a first install of a new knives pin does not fail a tools step.

The knives shim's install of a missing pin (shims/knives `install_pin`) and the `mise install` of
every other writer of mise's data dir take one lock, `<data dir>/.dotfiles-install.lock`, so the
two installs never run at the same time. A knives call that finds the lock held waits for the pin,
not for the lock; a knives call made under a holder's `mise install` carries
DOTFILES_INSTALL_LOCK_HELD=1 and so does not wait on the lock its ancestor holds.

Technique: every party runs for real (shims/knives, scripts/dotfiles-follow's `install_tools`,
`scripts/dotfiles-sync --tools`, scripts/agent-session, installers/mise.sh and
installers/librarian-harness-host.sh) with one stand-in `mise` (STAND_IN_MISE) first on PATH. The
stand-in's `install` marks itself in progress with `mkdir <state>/installing`; an install that
starts while another runs fails there, as the knives install of 2026-11-20 15:34Z did, and logs
`overlap <role>`. Two installs meet because the test makes them meet: a gate file holds the first
install until the test creates its release file, after the second party has signalled (the shim's
wait line, its exit, or its own install's outcome). Each wait of the test has a bound of 60 s,
which only detects a failure. Every process gets an environment built from nothing, under this
test's temporary directory. Standard library only; no git and no systemd-run anywhere.

Each test is a row of the plan's state table (dispatch://INFRA-4798/artifact/plan-md, "The R1
test"); a row marked "each holder" is one test per entry of HOLDERS.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Callable

REPO = Path(__file__).resolve().parents[2]
SHIM = REPO / "shims" / "knives"
FOLLOW = REPO / "scripts" / "dotfiles-follow"
SYNC = REPO / "scripts" / "dotfiles-sync"
SESSION = REPO / "scripts" / "agent-session"
MISE_INSTALLER = REPO / "installers" / "mise.sh"
LIBRARIAN_INSTALLER = REPO / "installers" / "librarian-harness-host.sh"

PIN = "9.9.9"
# Every wait of the test: a failure detector, never what makes two installs meet.
BOUND = 60.0
# DOTFILES_KNIVES_INSTALL_TIMEOUT for every process, the shim's own bound on a wait for the pin,
# except where a row sets its own (L6, L13, L13b).
SHIM_WAIT = 20
# L13 and L13b: DOTFILES_KNIVES_INSTALL_CAP, and the kill grace of the wrapper's `timeout -k 10` in
# shims/knives (`install_pin`). The shim's mise gets TERM at the cap and KILL at the cap plus the grace.
INSTALL_CAP = 2
KILL_GRACE = 10
# L13 and L13b: DOTFILES_KNIVES_INSTALL_TIMEOUT, far past INSTALL_CAP + KILL_GRACE, so the shim's own
# wait for the pin cannot end before the cap and the KILL do.
CAPPED_SHIM_WAIT = 45
# L13, where mise ignores TERM and ends at the KILL: what the shim adds to INSTALL_CAP + KILL_GRACE.
# Before its install it tries `mise which` twice, with 1 s of sleep between the tries; after the KILL
# it sees the wrapper gone at its next 0.5 s poll.
KILL_SLACK = 5
LOCK_NAME = ".dotfiles-install.lock"
MARKER = "DOTFILES_INSTALL_LOCK_HELD"
WAIT_LINE_PART = "is held by another install; waiting up to"

# The holders of .dotfiles-install.lock: the writers that run `mise install` in a checkout and
# hold the lock around it. `grep -rn 'mise install\|dotfiles-install.lock' scripts shims
# installers` found them: scripts/dotfiles-follow (`install_tools`), scripts/dotfiles-sync
# (`tools_step`) and scripts/agent-session. The same search finds the shim's own install
# (shims/knives `install_pin`) and two installers that ran `mise install` with no lock before
# this change and now take it, installers/mise.sh and installers/librarian-harness-host.sh; those
# two are in INSTALLERS and have rows of their own (L11, L12, L14). A new writer of mise's data dir
# goes into this list, or into a row of its own.
HOLDERS = ("follow", "sync", "session")
INSTALLERS = ("mise_sh", "librarian")

# Follow's tools commands, each run with install_tools' `environment` (scripts/dotfiles-follow
# `install_tools`), which holds the MISE_CACHE_DIR of the checkout's .bashrc: `mise install`,
# `bun install --frozen-lockfile`, the marketplace step's `shims/omp` calls and
# `devbox/sami-devbox-check --pins`. L10 runs each of them; a new tools command goes into this list.
FOLLOW_TOOLS = ("mise", "bun", "marketplace", "pin check")

# The follow holder: scripts/dotfiles-follow loaded by its path, so this file runs unchanged at
# any commit that has `install_tools`, and its `install_tools` called on the scratch checkout.
FOLLOW_CODE = """\
import argparse
import importlib.util
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

loader = SourceFileLoader("dotfiles_follow", sys.argv[1])
spec = importlib.util.spec_from_loader("dotfiles_follow", loader)
follow = importlib.util.module_from_spec(spec)
loader.exec_module(follow)
why = follow.install_tools(Path(sys.argv[2]), argparse.Namespace(mise_timeout=60, check_timeout=60))
if why:
    print(why)
    sys.exit(1)
"""

# The stand-in for mise. @NAME@ marks a value the test fills in, shell-quoted.
STAND_IN_MISE = r"""#!/bin/bash
# The stand-in mise of scripts/tests/test_dotfiles_install_lock.py. Its `install` logs each step
# to @LOG@ and takes its orders from the files the test creates in @STATE@.
state=@STATE@
log=@LOG@
shim=@SHIM@
agent=@AGENT@
pin=@PIN@
data="${MISE_DATA_DIR:-$HOME/.local/share/mise}"
knives="$data/installs/github-sjawhar-knives/$pin/bin/knives"

note() { printf '%s\n' "$*" >> "$log"; }
# Waits until the test creates the file. The poll cap (300 s, five times any wait of the test)
# and the check for the state dir only end a stand-in that outlived its test.
wait_for() {
    local polls=0
    while [[ ! -e "$1" ]]; do
        [[ -d "$state" ]] && (( polls++ < 6000 )) || exit 1
        sleep 0.05
    done
}
# place PATH LINE: an executable that prints LINE, written beside PATH, then moved to PATH.
place() {
    mkdir -p "${1%/*}"
    printf '#!/bin/bash\necho %q\n' "$2" > "$1.tmp.$$"
    chmod +x "$1.tmp.$$"
    mv -f "$1.tmp.$$" "$1"
}

case "${1:-}" in
    which)
        for arg in "$@"; do
            case "$arg" in
                knives)
                    if [[ -x "$knives" ]]; then
                        printf '%s\n' "$knives"
                        exit 0
                    fi
                    printf 'mise ERROR github:sjawhar/knives@%s is not installed\n' "$pin" >&2
                    exit 1
                    ;;
                fakeagent)
                    printf '%s\n' "$agent"
                    exit 0
                    ;;
            esac
        done
        printf 'mise ERROR (stand-in) unexpected: mise %s\n' "$*" >&2
        exit 2
        ;;
    activate)
        exit 0
        ;;
    install)
        ;;
    *)
        printf 'mise ERROR (stand-in) unexpected: mise %s\n' "$*" >&2
        exit 2
        ;;
esac

# `install -C / github:sjawhar/knives@<pin>` is the shim's; any other install (bare, from a holder
# or installers/mise.sh; `github:sjawhar/oh-my-pi@<v>`, from the librarian installer) is `tools`.
role=tools
omp=""
for arg in "$@"; do
    case "$arg" in
        github:sjawhar/knives@*) role=shim ;;
        github:sjawhar/oh-my-pi@*) omp="${arg#*@}" ;;
    esac
done
[[ ! -e "$state/ignore-term-$role" ]] || trap '' TERM

# 1.
note "start $role $$ cache=${MISE_CACHE_DIR-unset}"
# 2. Another install runs: fail as the knives install of 2026-11-20 15:34Z did.
if ! mkdir "$state/installing" 2> /dev/null; then
    note "overlap $role"
    printf 'mise ERROR Failed to install github:sjawhar/knives@%s: failed to extract tar: %s: No such file or directory (os error 2)\n' \
        "$pin" "${knives%/bin/knives}" >&2
    exit 1
fi
# 3. Held until the test says.
if [[ -e "$state/gate-$role" ]]; then
    : > "$state/holding-$role"
    wait_for "$state/release-$role-1"
fi
# 4. A knives call under this install, made before the pin is in place.
if [[ -e "$state/nested-$role" ]]; then
    started="${EPOCHREALTIME//[!0-9]/}"
    rc=0
    "$shim" --version < /dev/null > "$state/nested-$role.out" 2>&1 || rc=$?
    us=$(( ${EPOCHREALTIME//[!0-9]/} - started ))
    note "nested rc=$rc secs=$(( us / 1000000 )).$(printf '%03d' $(( us / 1000 % 1000 )))"
fi
# 5.
if [[ ! -e "$state/place-none-$role" ]]; then
    place "$knives" "knives $pin"
    [[ -z "$omp" ]] || place "$data/installs/github-sjawhar-oh-my-pi/$omp/bin/omp" "omp $omp"
    note "placed $role"
fi
# 6. mise goes on after the pin is in place, as it does while it rebuilds every tool's shims.
if [[ -e "$state/linger-$role" ]]; then
    wait_for "$state/release-$role-2"
fi
# 7. A process mise starts that outlives it and keeps every descriptor it inherited.
if [[ -e "$state/spawn-child" ]]; then
    setsid bash -c '
        printf "child %s\n" "$$" >> "$2"
        : > "$1/child-started"
        polls=0
        while [[ ! -e "$1/release-child" ]]; do
            [[ -d "$1" ]] && (( polls++ < 6000 )) || exit 1
            sleep 0.05
        done' stand-in-child "$state" "$log" &
    wait_for "$state/child-started"
fi
# 8.
rmdir "$state/installing"
note "end $role"
# 9. An install that fails with mise's own status 75, the status flock gives for a wait that ran out.
if [[ -e "$state/exit-75-$role" ]]; then
    note "exit 75 $role"
    exit 75
fi
exit 0
"""

# A command a holder runs after its locked `mise install`: follow's pin check
# (devbox/sami-devbox-check), sync's Bun, the agent agent-session execs. With <state>/after-knives
# it makes a knives call and exits with that call's status.
AFTER_LOCK = r"""#!/bin/bash
@FIRST@
if [[ -e @STATE@/after-knives ]]; then
    rc=0
    @SHIM@ --version || rc=$?
    printf 'after rc=%s\n' "$rc" >> @LOG@
    exit "$rc"
fi
exit 0
"""

# L10's stand-in for one of follow's tools commands after `mise install` (@TOOL@, a member of
# FOLLOW_TOOLS): it logs the MISE_CACHE_DIR it got, runs @THEN@ and exits 0.
STAND_IN_TOOL = r"""#!/bin/bash
printf 'ran %s cache=%s\n' @TOOL@ "${MISE_CACHE_DIR-unset}" >> @LOG@
@THEN@
exit 0
"""


def executable(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def alive(pid: int) -> bool:
    """True while the process exists and is not a zombie."""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    except (OSError, IndexError):
        return False
    return bool(fields) and fields[0] != "Z"


class Party:
    """A process the test started. Its output goes to files, not pipes: a detached install that
    inherited a pipe would keep the test's read waiting."""

    def __init__(self, name: str, argv: list[str], env: dict[str, str], cwd: Path, out: Path) -> None:
        self.name = name
        self.stdout_path = out / f"{name}.stdout"
        self.stderr_path = out / f"{name}.stderr"
        with self.stdout_path.open("w") as stdout, self.stderr_path.open("w") as stderr:
            self.popen = subprocess.Popen(
                argv,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
            )
        self.started = time.monotonic()
        self.ended: float | None = None

    def poll(self) -> int | None:
        code = self.popen.poll()
        if code is not None and self.ended is None:
            self.ended = time.monotonic()
        return code

    @property
    def elapsed(self) -> float:
        """Seconds from the start to the first poll that saw the process exit."""
        if self.ended is None:
            raise AssertionError(f"{self.name}: no poll has seen it exit")
        return self.ended - self.started

    @property
    def returncode(self) -> int | None:
        return self.popen.returncode

    def stdout(self) -> str:
        return self.stdout_path.read_text(errors="replace")

    def stderr(self) -> str:
        return self.stderr_path.read_text(errors="replace")


class InstallLock(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="dotfiles-install-lock-"))
        self.parties: list[Party] = []
        self.home = self.root / "home"
        self.config_home = self.root / "config"
        self.cache_home = self.root / "cache"
        self.data = self.root / "data"
        self.lock = self.data / LOCK_NAME
        self.state = self.root / "state"
        self.log = self.root / "events.log"
        self.bin = self.root / "bin"
        self.checkout = self.root / "checkout"
        self.agent = self.root / "agent" / "fakeagent"
        self.tmp = self.root / "tmp"
        self.out = self.root / "out"
        self.mise_config = self.root / "mise-config.toml"
        self.addCleanup(self.clean_up)
        for directory in (self.home, self.config_home, self.cache_home, self.state, self.tmp, self.out):
            directory.mkdir(parents=True)
        self.log.touch()
        pin_config = f'[tools]\n"github:sjawhar/knives" = "{PIN}"\n'
        self.mise_config.write_text(pin_config, encoding="utf-8")

        values = {
            "@STATE@": shlex.quote(str(self.state)),
            "@LOG@": shlex.quote(str(self.log)),
            "@SHIM@": shlex.quote(str(SHIM)),
            "@AGENT@": shlex.quote(str(self.agent)),
            "@PIN@": shlex.quote(PIN),
        }

        def fill(template: str, **more: str) -> str:
            for name, value in {**values, **{f"@{key}@": text for key, text in more.items()}}.items():
                template = template.replace(name, value)
            return template

        mise = executable(self.bin / "mise", fill(STAND_IN_MISE))
        executable(self.bin / "bun", fill(AFTER_LOCK, FIRST="# Bun"))
        executable(self.bin / "uv", "#!/bin/bash\nexit 0\n")
        executable(self.agent, fill(AFTER_LOCK, FIRST="echo 'fakeagent ran'"))

        # The scratch checkout every holder works in: what follow's install_tools runs besides
        # mise (no omp/plugins, so no Bun and no marketplace step), what agent-session sources and
        # execs (as scripts/tests/test_agent_session_install.py sets it up), what installers/mise.sh
        # links, and the .bashrc and bin/mise that dotfiles-sync's tools step reads.
        executable(self.checkout / "scripts" / "omp-link-rules", "#!/bin/bash\nexit 0\n")
        executable(self.checkout / "devbox" / "sami-devbox-check", fill(AFTER_LOCK, FIRST="# the pin check"))
        executable(self.checkout / "scripts" / "agent-secrets-session", '#!/bin/bash\nexec "$@"\n')
        executable(self.checkout / "scripts" / "gh-agent-config", "#!/bin/bash\necho /nonexistent\n")
        (self.checkout / "scripts" / "agent-spend-env.sh").write_text("agent_spend_env() { :; }\n", encoding="utf-8")
        (self.checkout / "shims").mkdir()
        (self.checkout / "mise.toml").write_text(pin_config, encoding="utf-8")
        (self.checkout / "mise").mkdir()
        (self.checkout / "mise" / "mise.conf").write_text(f"MISE_DATA_DIR={self.data}\n", encoding="utf-8")
        (self.checkout / "bin").mkdir()
        (self.checkout / "bin" / "mise").symlink_to(mise)
        self.write_bashrc(f"export MISE_DATA_DIR={shlex.quote(str(self.data))}\n")

    # ------------------------------------------------------------------------- the parties

    def env(self, **changes: str | None) -> dict[str, str]:
        """The environment of every process of the test, built from nothing; None unsets."""
        env = {
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.config_home),
            "XDG_CACHE_HOME": str(self.cache_home),
            "MISE_DATA_DIR": str(self.data),
            "MISE_GLOBAL_CONFIG_FILE": str(self.mise_config),
            "DOTFILES_KNIVES_INSTALL_TIMEOUT": str(SHIM_WAIT),
            "PATH": os.pathsep.join([str(self.bin), str(SHIM.parent), "/usr/bin", "/bin"]),
            "TMPDIR": str(self.tmp),
        }
        for name, value in changes.items():
            if value is None:
                env.pop(name, None)
            else:
                env[name] = value
        return env

    def start(self, name: str, argv: list[str], env: dict[str, str]) -> Party:
        party = Party(f"{len(self.parties)}-{name}", argv, env, self.root, self.out)
        self.parties.append(party)
        return party

    def start_shim(self, env: dict[str, str] | None = None) -> Party:
        return self.start("shim", [str(SHIM), "--version"], self.env() if env is None else env)

    def start_holder(self, holder: str, env: dict[str, str] | None = None) -> Party:
        """A holder of the lock, or one of the two installers, run as its callers run it."""
        env = self.env() if env is None else env
        if holder == "follow":
            argv = [sys.executable, "-c", FOLLOW_CODE, str(FOLLOW), str(self.checkout)]
        elif holder == "sync":
            argv = ["bash", str(SYNC), "--tools", str(self.checkout)]
        elif holder == "session":
            # No fakeagent on PATH, so agent-session installs, then execs what `mise which` names.
            argv = [str(SESSION), "fakeagent"]
            env = {
                **env,
                "DOTFILES_DIR": str(self.checkout),
                "ENVOY_MACHINE_ID": "install-lock-test",
                "AGENT_SESSION_NVME_HOME": str(self.root / "no-nvme"),
            }
        elif holder == "mise_sh":
            # Sourced, as install.sh does; no XDG_RUNTIME_DIR in env, so no systemctl.
            argv = ["bash", "-c", f"source {shlex.quote(str(MISE_INSTALLER))}"]
            env = {**env, "DOTFILES_DIR": str(self.checkout)}
        elif holder == "librarian":
            argv = ["bash", str(LIBRARIAN_INSTALLER)]
        else:
            raise ValueError(holder)
        return self.start(holder, argv, env)

    def write_bashrc(self, text: str) -> None:
        (self.checkout / ".bashrc").write_text(text, encoding="utf-8")

    def flag(self, name: str) -> None:
        (self.state / name).touch()

    # ------------------------------------------------------------------------- what happened

    def events(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []

    def starts(self) -> list[str]:
        return [line.split()[1] for line in self.events() if line.startswith("start ")]

    def start_pid(self, role: str) -> int:
        pids = [int(line.split()[2]) for line in self.events() if line.startswith(f"start {role} ")]
        self.assertEqual(len(pids), 1, f"one `start {role}` line\n{self.describe()}")
        return pids[0]

    def nested(self) -> tuple[int, float]:
        found = [re.fullmatch(r"nested rc=(\d+) secs=([0-9.]+)", line) for line in self.events() if line.startswith("nested ")]
        self.assertEqual(len(found), 1, f"one `nested` line\n{self.describe()}")
        match = found[0]
        self.assertIsNotNone(match, self.describe())
        assert match is not None
        return int(match.group(1)), float(match.group(2))

    def wait_line(self, lock: Path) -> str:
        return f"knives: knives {PIN} is not installed and {lock} is held by another install; waiting up to {SHIM_WAIT}s for it"

    def probe(self, lock: Path) -> int:
        """`flock -n -E 75 <lock> true`: 75 while another process holds the lock, 0 once it is free."""
        try:
            return subprocess.run(
                ["flock", "-n", "-E", "75", str(lock), "true"],
                env=self.env(),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=BOUND,
                check=False,
            ).returncode
        except subprocess.TimeoutExpired:
            self.fail(f"waited {BOUND:g} s for `flock -n -E 75 {lock} true`")

    def describe(self) -> str:
        lines = ["event log:", *(f"  {line}" for line in self.events())]
        for party in self.parties:
            code = party.popen.poll()
            lines.append(f"{party.name}: " + ("still running" if code is None else f"exit {code}"))
            output = (party.stdout() + party.stderr()).splitlines()
            lines += [f"  {line}" for line in output[-15:]]
        return "\n".join(lines)

    # ------------------------------------------------------------------------- waits

    def wait_until(self, check: Callable[[], bool], what: str) -> None:
        deadline = time.monotonic() + BOUND
        while not check():
            if time.monotonic() > deadline:
                self.fail(f"waited {BOUND:g} s for {what}\n{self.describe()}")
            time.sleep(0.05)

    def wait_for_file(self, name: str, what: str) -> None:
        self.wait_until((self.state / name).exists, what)

    def finish(self, party: Party, what: str) -> None:
        try:
            party.popen.wait(timeout=BOUND)
        except subprocess.TimeoutExpired:
            self.fail(f"waited {BOUND:g} s for {what} to exit\n{self.describe()}")
        party.poll()

    def wait_for_the_shim_to_meet(self, shim: Party) -> None:
        """Until the shim, started while a holder's install is gated, has met that install: it
        printed its wait line, or it exited. An install of its own (`start shim`) meets the gated
        one in `mkdir <state>/installing`, logs `overlap shim` and fails, and the shim then exits,
        so the test waits for that exit rather than for `start shim`: a release on `start shim`
        could let the two installs miss each other, and one on `overlap shim` would let the shim
        find the holder's pin before it saw its own install fail."""
        self.wait_until(
            lambda: WAIT_LINE_PART in shim.stderr() or shim.poll() is not None,
            "shims/knives to print its wait line or exit",
        )

    # ------------------------------------------------------------------------- assertions

    def assert_no_overlap(self) -> None:
        overlaps = [line for line in self.events() if line.startswith("overlap ")]
        self.assertEqual(overlaps, [], f"two installs ran at the same time\n{self.describe()}")

    def assert_ran_the_pin(self, shim: Party) -> None:
        self.assertEqual((shim.returncode, shim.stdout()), (0, f"knives {PIN}\n"), self.describe())

    def assert_succeeded(self, party: Party) -> None:
        self.assertEqual(party.returncode, 0, f"{party.name} failed\n{self.describe()}")

    # ------------------------------------------------------------------------- clean-up

    def ours(self) -> set[int]:
        """Live processes of this test: each has the test's HOME in its environment."""
        marker = f"HOME={self.home}".encode()
        found = set()
        for entry in os.listdir("/proc"):
            if not entry.isdigit() or int(entry) == os.getpid():
                continue
            try:
                environ = Path(f"/proc/{entry}/environ").read_bytes()
            except OSError:
                continue
            if marker in environ.split(b"\0") and alive(int(entry)):
                found.add(int(entry))
        return found

    def clean_up(self) -> None:
        # Every release file, so each held stand-in finishes as it would; then every Popen, and any
        # other process of this test that is still alive. Each process the event log names carries
        # the test's HOME, so ours() finds it; a pid of the log alone may already belong to another
        # process of the pod, so it is never killed by its number.
        if self.state.is_dir():
            for role in ("tools", "shim"):
                for step in (1, 2):
                    (self.state / f"release-{role}-{step}").touch()
            (self.state / "release-child").touch()
        deadline = time.monotonic() + BOUND
        for party in self.parties:
            try:
                party.popen.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(party.popen.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                party.popen.kill()
                party.popen.wait()
        settle = time.monotonic() + 10
        while self.ours() and time.monotonic() < settle:
            time.sleep(0.05)
        for pid in self.ours():
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        settle = time.monotonic() + 10
        while self.ours() and time.monotonic() < settle:
            time.sleep(0.05)
        shutil.rmtree(self.root, ignore_errors=True)

    # ------------------------------------------------------------------------- rows

    def row_l1(self, holder: str, env: dict[str, str] | None = None, lock: Path | None = None) -> None:
        """L1: while a holder's install runs and the pin is absent, the shim waits for the pin."""
        lock = self.lock if lock is None else lock
        self.flag("gate-tools")
        party = self.start_holder(holder, env)
        self.wait_for_file("holding-tools", f"{holder}'s mise install to start and hold")
        shim = self.start_shim(env)
        self.wait_for_the_shim_to_meet(shim)
        self.flag("release-tools-1")
        self.finish(shim, "shims/knives")
        self.finish(party, holder)
        self.assert_no_overlap()
        self.assertEqual(self.starts(), ["tools"], self.describe())
        self.assert_ran_the_pin(shim)
        self.assertLess(shim.elapsed, SHIM_WAIT, self.describe())
        self.assertIn(self.wait_line(lock), shim.stderr(), self.describe())
        self.assert_succeeded(party)

    def row_l2(self, holder: str) -> None:
        """L2: a knives call under the holder's install ends at once, installing nothing."""
        self.flag("nested-tools")
        party = self.start_holder(holder)
        self.finish(party, holder)
        self.assert_no_overlap()
        self.assertEqual(self.starts(), ["tools"], self.describe())
        rc, secs = self.nested()
        self.assertEqual(rc, 127, self.describe())
        self.assertLess(secs, 5, self.describe())
        self.assert_succeeded(party)

    def row_l3(self, holder: str) -> None:
        """L3: the shim runs the pin the holder placed while the holder still holds the lock."""
        self.flag("gate-tools")
        self.flag("linger-tools")
        party = self.start_holder(holder)
        self.wait_for_file("holding-tools", f"{holder}'s mise install to start and hold")
        shim = self.start_shim()
        self.wait_for_the_shim_to_meet(shim)
        self.flag("release-tools-1")
        self.finish(shim, "shims/knives")
        self.assert_no_overlap()
        self.assert_ran_the_pin(shim)
        self.assertNotIn("end tools", self.events(), self.describe())
        self.assertEqual(self.probe(self.lock), 75, f"{holder} still installs, so {self.lock} is held\n{self.describe()}")
        self.flag("release-tools-2")
        self.finish(party, holder)
        self.assertEqual(self.starts(), ["tools"], self.describe())
        self.assert_succeeded(party)

    def row_l4(self, holder: str) -> None:
        """L4: the shim's lock lasts as long as its mise, after the pin is in place; then the
        holder installs."""
        self.flag("linger-shim")
        shim = self.start_shim()
        self.finish(shim, "shims/knives")
        self.wait_until(lambda: "placed shim" in self.events(), "the shim's mise install to place the pin")
        self.assert_ran_the_pin(shim)
        self.assertEqual(
            self.probe(self.lock), 75,
            f"the shim's mise install still runs, so {self.lock} must be held\n{self.describe()}",
        )
        self.flag("release-shim-2")
        self.wait_until(lambda: "end shim" in self.events(), "the shim's mise install to end")
        self.wait_until(lambda: self.probe(self.lock) == 0, f"{self.lock} to be free once the shim's mise install ended")
        party = self.start_holder(holder)
        self.finish(party, holder)
        self.assert_no_overlap()
        self.assertEqual(self.starts(), ["shim", "tools"], self.describe())
        self.assert_succeeded(party)

    def row_l9(self, holder: str) -> None:
        """L9: the marker reaches only the holder's `mise install`: the command after its lock
        installs the pin itself."""
        self.flag("place-none-tools")
        self.flag("after-knives")
        if holder == "sync":
            (self.checkout / "omp" / "plugins").mkdir(parents=True)
            (self.checkout / "omp" / "plugins" / "package.json").write_text("{}\n", encoding="utf-8")
        party = self.start_holder(holder)
        self.finish(party, holder)
        events = self.events()
        self.assertIn("after rc=0", events, self.describe())
        self.assertEqual(self.starts(), ["tools", "shim"], self.describe())
        start_shim = next(index for index, line in enumerate(events) if line.startswith("start shim "))
        self.assertLess(events.index("end tools"), start_shim, self.describe())
        self.assert_succeeded(party)

    def row_installer_with_the_lock_held_by_its_caller(self, installer: str) -> None:
        """L11b, L12b: a caller that holds the lock and sets the marker runs the installer, whose
        `mise install` runs at once and ends while the caller still holds the lock."""
        self.data.mkdir(parents=True, exist_ok=True)
        with self.lock.open("a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            party = self.start_holder(installer, self.env(**{MARKER: "1"}))
            self.finish(party, installer)
            self.assertEqual(self.probe(self.lock), 75, f"control: the test holds {self.lock}\n{self.describe()}")
        self.assert_succeeded(party)
        self.assertEqual(self.starts(), ["tools"], self.describe())
        self.assertIn("end tools", self.events(), self.describe())

    def row_l14(self, installer: str) -> None:
        """L14: a `mise install` that itself exits 75 is a failed install, not a wait for the lock
        that ran out: no line names the lock; installers/mise.sh prints its rate-limit line, tries
        again and goes on, and the librarian installer ends non-zero."""
        self.flag("exit-75-tools")
        party = self.start_holder(installer)
        self.finish(party, installer)
        output = party.stdout() + party.stderr()
        self.assertNotIn("another install held", output.lower(), self.describe())
        self.assertNotRegex(output, r"held .* for 600 s", self.describe())
        self.assert_no_overlap()
        tries = 3 if installer == "mise_sh" else 1
        self.assertEqual(self.starts(), ["tools"] * tries, self.describe())
        self.assertEqual([line for line in self.events() if line.startswith("exit ")], ["exit 75 tools"] * tries, self.describe())
        if installer == "mise_sh":
            self.assertIn("Some tools failed to install", output, self.describe())
            self.assert_succeeded(party)
        else:
            self.assertNotEqual(party.returncode, 0, self.describe())

    # ------------------------------------------------------------------------- rows of one party

    def test_l2_a_knives_call_under_the_shims_own_install_ends_at_once(self) -> None:
        """L2, the shim as holder: its own mise's knives call does not wait on the shim's lock."""
        self.flag("nested-shim")
        shim = self.start_shim()
        self.finish(shim, "shims/knives")
        self.assert_no_overlap()
        self.assertEqual(self.starts(), ["shim"], self.describe())
        rc, secs = self.nested()
        self.assertEqual(rc, 127, self.describe())
        self.assertLess(secs, 5, self.describe())
        self.assert_ran_the_pin(shim)

    def test_l5_a_child_the_shims_mise_leaves_running_holds_no_lock(self) -> None:
        """L5: a detached child of the shim's mise that keeps every descriptor does not keep the lock."""
        self.flag("spawn-child")
        shim = self.start_shim()
        self.finish(shim, "shims/knives")
        self.assert_ran_the_pin(shim)
        self.wait_until(lambda: "end shim" in self.events(), "the shim's mise install to end")
        child = int(next(line.split()[1] for line in self.events() if line.startswith("child ")))
        self.wait_until(lambda: self.probe(self.lock) == 0, f"{self.lock} to be free while the child of the shim's mise runs")
        self.assertTrue(alive(child), f"control: the child {child} still runs\n{self.describe()}")

    def test_l6_sync_holding_the_lock_past_the_shims_bound_fails_the_call_loudly(self) -> None:
        """L6: the shim gives up at its bound with the lock's name, and installs nothing."""
        self.flag("gate-tools")
        self.start_holder("sync")
        self.wait_for_file("holding-tools", "sync's mise install to start and hold")
        shim = self.start_shim(self.env(DOTFILES_KNIVES_INSTALL_TIMEOUT="2"))
        self.finish(shim, "shims/knives")
        self.assert_no_overlap()
        self.assertNotIn("shim", self.starts(), self.describe())
        self.assertEqual(shim.returncode, 127, self.describe())
        self.assertGreaterEqual(shim.elapsed, 2, self.describe())
        self.assertIn(f"waited 2s for {self.lock}", shim.stderr(), self.describe())

    def test_l7_a_recent_failed_install_ends_the_call_without_waiting(self) -> None:
        """L7: `<lock>.failed` names the pin: the shim exits at once, without a wait or an install."""
        self.flag("gate-tools")
        self.start_holder("sync")
        self.wait_for_file("holding-tools", "sync's mise install to start and hold")
        Path(f"{self.lock}.failed").write_text(f"{PIN} {int(time.time())}\n", encoding="utf-8")
        shim = self.start_shim()
        self.finish(shim, "shims/knives")
        self.assert_no_overlap()
        self.assertNotIn("shim", self.starts(), self.describe())
        self.assertEqual(shim.returncode, 127, self.describe())
        self.assertLess(shim.elapsed, 10, self.describe())
        self.assertIn("not trying again for 60 s", shim.stderr(), self.describe())
        self.assertNotIn(WAIT_LINE_PART, shim.stderr(), self.describe())

    def test_l8a_follow_and_the_shim_lock_home_mise_when_mise_data_dir_is_unset(self) -> None:
        """L8 (a): no MISE_DATA_DIR, ~/.mise exists: both lock the same absolute path."""
        self.write_bashrc("")
        (self.home / ".mise").mkdir()
        self.row_l1("follow", env=self.env(MISE_DATA_DIR=None), lock=self.home / ".mise" / LOCK_NAME)

    def test_l8b_follow_and_the_shim_lock_one_path_for_a_relative_mise_data_dir(self) -> None:
        """L8 (b): MISE_DATA_DIR=data, both run in the test's directory: one absolute lock path."""
        self.write_bashrc("")
        self.row_l1("follow", env=self.env(MISE_DATA_DIR="data"), lock=self.root / "data" / LOCK_NAME)

    def test_l10_follow_gives_each_tools_command_the_cache_dir_of_the_checkouts_bashrc(self) -> None:
        """L10: each of follow's tools commands (FOLLOW_TOOLS) gets the MISE_CACHE_DIR a shell on the
        box gets; control first."""
        log = shlex.quote(str(self.log))
        plugins = self.checkout / "omp" / "plugins"
        installed = plugins / "installed_plugins.json"

        def record(version: str) -> str:
            return json.dumps({"plugins": {"l10-plugin@l10-market": [{"version": version}]}}) + "\n"

        def tool(path: Path, name: str, then: str = ":") -> None:
            body = STAND_IN_TOOL.replace("@TOOL@", shlex.quote(name)).replace("@LOG@", log)
            executable(path, body.replace("@THEN@", then))

        # Follow runs the marketplace step only for ~/.dotfiles (rule A in scripts/dotfiles-follow's
        # docstring), so here ~/.dotfiles is a symlink to the scratch checkout.
        (self.home / ".dotfiles").symlink_to(self.checkout)
        # package.json makes follow run Bun. A lock entry above the installed record makes the
        # marketplace step run `shims/omp plugin marketplace update` and `shims/omp plugin upgrade`;
        # it runs them because XDG_CONFIG_HOME holds no omp/profile, so the profile is not legion.
        plugins.mkdir(parents=True)
        (plugins / "package.json").write_text("{}\n", encoding="utf-8")
        lock_entry = json.dumps({"plugins": {"l10-plugin": {"version": "2.0.0"}}}) + "\n"
        (plugins / "omp-plugins.lock.json").write_text(lock_entry, encoding="utf-8")
        tool(self.bin / "bun", "bun")
        # The step reads the installed record again after `plugin upgrade`, and a record still below
        # the lock fails it before the pin check; so the upgrade raises it, as the real one does.
        raise_record = f'[[ "${{1-}} ${{2-}}" != "plugin upgrade" ]] || printf %s {shlex.quote(record("2.0.0"))} > {shlex.quote(str(installed))}'
        tool(self.checkout / "shims" / "omp", "marketplace", raise_record)
        tool(self.checkout / "devbox" / "sami-devbox-check", "pin check")
        # After its omp calls follow restores the lock with `git checkout`. This git fails as git
        # does outside a repository, so follow writes the lock back itself and no git runs.
        executable(self.bin / "git", "#!/bin/bash\nexit 128\n")

        def run(what: str) -> dict[str, list[str]]:
            """One install_tools of follow: the MISE_CACHE_DIR each call of each tools command got."""
            installed.write_text(record("1.0.0"), encoding="utf-8")
            before = len(self.events())
            party = self.start_holder("follow")
            self.finish(party, what)
            self.assert_succeeded(party)
            seen: dict[str, list[str]] = {name: [] for name in FOLLOW_TOOLS}
            for line in self.events()[before:]:
                if line.startswith("start tools "):
                    seen["mise"].append(line.split("cache=", 1)[1])
                elif line.startswith("ran "):
                    name, _, cache = line[len("ran "):].partition(" cache=")
                    seen.setdefault(name, []).append(cache)
            return seen

        def assert_each_saw(seen: dict[str, list[str]], cache: str, what: str) -> None:
            silent = [name for name in FOLLOW_TOOLS if not seen[name]]
            self.assertEqual(silent, [], f"{what}: these tools commands made no call\n{self.describe()}")
            self.assertEqual(
                {name: sorted(set(caches)) for name, caches in seen.items()},
                {name: [cache] for name in FOLLOW_TOOLS},
                f"{what}: the MISE_CACHE_DIR each tools command got\n{self.describe()}",
            )

        self.write_bashrc("# exports nothing\n")
        assert_each_saw(run("follow (control)"), "unset", "control")
        cache = self.root / "bashrc-cache"
        self.write_bashrc(f'export MISE_CACHE_DIR="${{MISE_CACHE_DIR:-{cache}}}"\n')
        assert_each_saw(run("follow"), str(cache), "follow")

    def test_l11a_installers_mise_sh_holds_the_lock_and_the_shim_waits(self) -> None:
        """L11 (a): installers/mise.sh's install runs: as L1."""
        self.row_l1("mise_sh")

    def test_l11b_installers_mise_sh_under_a_caller_that_holds_the_lock(self) -> None:
        """L11 (b)."""
        self.row_installer_with_the_lock_held_by_its_caller("mise_sh")

    def test_l12a_the_librarian_installer_holds_the_lock_and_the_shim_waits(self) -> None:
        """L12 (a): installers/librarian-harness-host.sh's install runs: as L1."""
        self.row_l1("librarian")

    def test_l12b_the_librarian_installer_under_a_caller_that_holds_the_lock(self) -> None:
        """L12 (b)."""
        self.row_installer_with_the_lock_held_by_its_caller("librarian")

    def test_l13_the_shims_install_is_capped_and_lets_go_of_the_lock(self) -> None:
        """L13: a hung install that ignores TERM is killed at the cap, and the lock is free after."""
        self.flag("gate-shim")
        self.flag("ignore-term-shim")
        capped = self.env(DOTFILES_KNIVES_INSTALL_CAP=str(INSTALL_CAP), DOTFILES_KNIVES_INSTALL_TIMEOUT=str(CAPPED_SHIM_WAIT))
        shim = self.start_shim(capped)
        self.wait_for_file("holding-shim", "the shim's mise install to start and hold")
        pid = self.start_pid("shim")
        self.finish(shim, "shims/knives")
        self.assertEqual(shim.returncode, 127, self.describe())
        self.assertRegex(shim.stderr(), rf"exited 137 without knives {re.escape(PIN)} in place", self.describe())
        self.assertLess(shim.elapsed, INSTALL_CAP + KILL_GRACE + KILL_SLACK, self.describe())
        failed = Path(f"{self.lock}.failed")
        self.assertTrue(failed.is_file(), self.describe())
        self.assertEqual(failed.read_text(encoding="utf-8").split()[:1], [PIN], self.describe())
        self.wait_until(lambda: not alive(pid), f"the stand-in mise {pid} to be killed")
        self.wait_until(lambda: self.probe(self.lock) == 0, f"{self.lock} to be free once the shim's mise is gone")

    def test_l13b_a_cap_that_stops_the_shims_mise_is_not_called_the_shims_own_timeout(self) -> None:
        """L13 (b): a hung install that stops on TERM ends at the cap; timeout's 124 is the
        install's status, not the shim's wait for the pin running out."""
        self.flag("gate-shim")
        capped = self.env(DOTFILES_KNIVES_INSTALL_CAP=str(INSTALL_CAP), DOTFILES_KNIVES_INSTALL_TIMEOUT=str(CAPPED_SHIM_WAIT))
        shim = self.start_shim(capped)
        self.wait_for_file("holding-shim", "the shim's mise install to start and hold")
        pid = self.start_pid("shim")
        self.finish(shim, "shims/knives")
        self.assertEqual(shim.returncode, 127, self.describe())
        self.assertIn(f"exited 124 without knives {PIN} in place", shim.stderr(), self.describe())
        # mise stops at the TERM, so the unused grace covers the shim's `mise which` tries and poll.
        self.assertLess(shim.elapsed, INSTALL_CAP + KILL_GRACE, self.describe())
        failed = Path(f"{self.lock}.failed")
        self.assertTrue(failed.is_file(), self.describe())
        self.assertEqual(failed.read_text(encoding="utf-8").split()[:1], [PIN], self.describe())
        self.wait_until(lambda: not alive(pid), f"the stand-in mise {pid} to be stopped")
        self.wait_until(lambda: self.probe(self.lock) == 0, f"{self.lock} to be free once the shim's mise is gone")

    def test_l14b_installers_mise_sh_under_a_caller_that_holds_the_lock_reports_mises_75_as_a_failed_install(self) -> None:
        """L14 (b): L14 for installers/mise.sh under a caller that holds the lock and sets the
        marker, as L11 (b) sets it up, so mise.sh runs mise without flock. The librarian installer
        prints no lock line in that branch, so it has no such row."""
        self.flag("exit-75-tools")
        self.data.mkdir(parents=True, exist_ok=True)
        with self.lock.open("a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            party = self.start_holder("mise_sh", self.env(**{MARKER: "1"}))
            self.finish(party, "mise_sh")
            self.assertEqual(self.probe(self.lock), 75, f"control: the test holds {self.lock}\n{self.describe()}")
        output = party.stdout() + party.stderr()
        self.assertEqual([line for line in output.splitlines() if LOCK_NAME in line], [], self.describe())
        self.assertIn("Some tools failed to install", output, self.describe())
        self.assert_no_overlap()
        self.assertEqual(self.starts(), ["tools"] * 3, self.describe())
        self.assertEqual([line for line in self.events() if line.startswith("exit ")], ["exit 75 tools"] * 3, self.describe())
        self.assert_succeeded(party)


def each(row: str, parties: tuple[str, ...], name: str) -> None:
    """One test per party for a row marked "each holder" (or "each installer")."""
    for party in parties:
        def test(self: InstallLock, party: str = party) -> None:
            getattr(self, row)(party)

        test.__name__ = test.__qualname__ = name.format(party=party)
        test.__doc__ = f"{getattr(InstallLock, row).__doc__} ({party})"
        setattr(InstallLock, test.__name__, test)


each("row_l1", HOLDERS, "test_l1_{party}_holds_the_lock_and_the_shim_waits_for_the_pin")
each("row_l2", HOLDERS + INSTALLERS, "test_l2_a_knives_call_under_{party}_ends_at_once")
each("row_l3", HOLDERS, "test_l3_the_shim_runs_the_pin_{party}_placed_while_{party}_holds_the_lock")
each("row_l4", HOLDERS, "test_l4_the_shim_holds_the_lock_until_its_mise_ends_then_{party}_installs")
each("row_l9", HOLDERS, "test_l9_the_command_after_{party}s_lock_installs_the_pin_itself")
each("row_l14", INSTALLERS, "test_l14_a_mise_install_of_{party}_that_exits_75_is_not_reported_as_a_lock_wait")


if __name__ == "__main__":
    unittest.main()
