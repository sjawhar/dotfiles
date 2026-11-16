#!/usr/bin/env python3
"""The end of a box deletes everything in it, and the collector deletes what a box left
(scripts/agentbox, dispatch://INFRA-1720 R8).

Sami, dispatch://INFRA-1720/ask/16322992: the end of a box deletes everything in it. Before this
change close_box copied the files a session wrote in the box's home to
~/.local/state/agentbox/box-homes/ and put them into the session's next box, kept the whole box
directory when a clone held work no remote had or the copy failed, and removed the container
without its anonymous volume, so the inner Docker store stayed. A box whose launcher was killed
was deleted only when a later box of the same session started.

  * `box_record_write` stamps `<disks>/<box>.info` at creation with the box, the owning session,
    the creation time, the launch command and the launcher's pid, and the record and its `.exit`
    marker die with the image;
  * `close_box` deletes the box's home, clones, image, cache, runtime directory, record, tmux relay
    unit, container and volume, and keeps nothing;
  * `cmd_collect`, which every `agentbox new` starts detached and one at a time, deletes each box
    named `agentbox-<12 hex>` under its own roots whose container is gone or exited, whose launcher
    is not running, whose newest file is over an hour old and in which no process stands, after
    `unenroll_box`, and touches nothing else.

And `unenroll_box`'s waiter, which a close starts: the enrollment is the SESSION's, so it must
survive while any live box holds that session (20:17Z on 2026-11-04, a duplicate box's close took
the original box's broker identity).

Technique, as test_agentbox_move.py: source `scripts/agentbox` (its source guard runs nothing)
with every host tool stubbed first on PATH, and point every root of the script at a fixture tree.
"""

from __future__ import annotations

import fcntl
import os
import shutil
import socket
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
AGENTBOX = DOTFILES / "scripts" / "agentbox"
GATEWAY = "172.31.0.1"

SID = "01a18385-f8c8-74a9-b6ff-694fb5fe87a7"
OTHER_SID = "01a10000-0000-0000-0000-000000000000"
OLD = "agentbox-0000000000a1"
YOUNG = "agentbox-0000000000b2"


def write_stub(directory: Path, name: str, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def age(path: Path, hours: float) -> None:
    """Set the mtime of PATH and of everything under it, links included, HOURS ago."""
    when = time.time() - hours * 3600
    paths = [path]
    if path.is_dir() and not path.is_symlink():
        paths += [Path(root) / name for root, dirs, files in os.walk(path) for name in dirs + files]
    for p in paths:
        os.utime(p, (when, when), follow_symlinks=False)


class AgentboxBoxEnd(unittest.TestCase):
    TIMEOUT = float(os.environ.get("AGENTBOX_TEST_TIMEOUT", "900"))

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.home = self.root / "home"
        self.boxes = self.root / "boxes"
        self.disks = self.root / "disks"
        self.cache = self.root / "box-cache"
        self.run_base = self.root / "run"
        self.proc = self.root / "proc"
        for path in (self.home, self.boxes, self.disks, self.cache, self.run_base, self.proc):
            path.mkdir(parents=True)
        self.stubs = self.root / "bin"
        self.stubs.mkdir()
        # Nothing here reaches the host. `ps -a --filter name=^<box>$` names a container listed in
        # $STUB_CONTAINERS, and for a box named in $STUB_PS_FAIL exits 1 with no output: docker could
        # not be asked. `inspect` answers the state this test gave the containers, running by
        # default. `rm` logs its arguments to $STUB_DOCKER_RM and forgets the container.
        write_stub(self.stubs, "docker", r'''
if [[ "${1:-}" == inspect ]]; then
    fmt=""; name=""
    shift
    while [[ $# -gt 0 ]]; do
        case "$1" in -f|--format) fmt="$2"; shift 2 ;; *) name="$1"; shift ;; esac
    done
    grep -qxF "$name" "$STUB_CONTAINERS" 2>/dev/null || exit 1
    state="$(cat "$STUB_CONTAINER_STATE" 2>/dev/null)"; state="${state:-running}"
    case "$fmt" in
        *State.Status*) echo "$state" ;;
        *State.Running*) [[ "$state" == running ]] && echo true || echo false ;;
        *State.ExitCode*) cat "$STUB_CONTAINER_RC" 2>/dev/null || echo 0 ;;
        *State.OOMKilled*) cat "$STUB_CONTAINER_OOM" 2>/dev/null || echo false ;;
        *State.FinishedAt*) cat "$STUB_CONTAINER_FINISHED" 2>/dev/null || echo "2026-11-07T15:18:00Z" ;;
        *) echo "" ;;
    esac
    exit 0
fi
if [[ "${1:-}" == rm ]]; then
    echo "$*" >>"$STUB_DOCKER_RM"
    shift
    while [[ "${1:-}" == -* ]]; do shift; done
    grep -vxF "${1:-}" "$STUB_CONTAINERS" >"$STUB_CONTAINERS.new" 2>/dev/null || :
    mv -f "$STUB_CONTAINERS.new" "$STUB_CONTAINERS"
    exit 0
fi
# `ps -a --filter status=exited` lists every stub container while their state is `exited`.
if [[ " $* " == *" status=exited "* ]]; then
    state="$(cat "$STUB_CONTAINER_STATE" 2>/dev/null)"
    [[ "$state" == exited ]] && cat "$STUB_CONTAINERS"
    exit 0
fi
want=""
for a in "$@"; do case "$a" in name=^*) want="${a#name=^}"; want="${want%$}" ;; esac; done
if [[ -n "$want" ]]; then
    grep -qxF "$want" "$STUB_PS_FAIL" 2>/dev/null && exit 1
    grep -qxF "$want" "$STUB_CONTAINERS" 2>/dev/null && echo "$want"
    exit 0
fi
cat "$STUB_CONTAINERS"
exit 0''')
        write_stub(self.stubs, "systemctl", 'printf "%s\\n" "$*" >>"$STUB_SYSTEMCTL"; exit 0')
        write_stub(self.stubs, "tmux", 'exit 0')
        write_stub(self.stubs, "envoy", 'exit 0')
        write_stub(self.stubs, "agent-secrets", 'printf "%s\\n" "$*" >>"$STUB_SECRETS"; exit 0')
        write_stub(self.stubs, "mountpoint", 'exit 1')
        # `rm` and `find` are the real ones, except on a path under $STUB_RM_FAIL, which rm leaves
        # in place and fails on (as on a file it may not remove), and under $STUB_FIND_FAIL, which
        # find says it cannot read and fails on. Both work as root, where file modes do not.
        write_stub(self.stubs, "rm", '''for a in "$@"; do
    if [[ -n "${STUB_RM_FAIL:-}" && "$a" == "$STUB_RM_FAIL"* ]]; then
        echo "rm: cannot remove '$a': Operation not permitted" >&2; exit 1
    fi
done
exec /bin/rm "$@"''')
        write_stub(self.stubs, "find", '''for a in "$@"; do
    if [[ -n "${STUB_FIND_FAIL:-}" && "$a" == "$STUB_FIND_FAIL"* ]]; then
        echo "find: '$a': Permission denied" >&2; exit 1
    fi
done
exec /usr/bin/find "$@"''')
        # No test may mount or unmount anything for real: sudo only logs.
        write_stub(self.stubs, "sudo", 'printf "%s\\n" "$*" >>"$STUB_SUDO"; exit "${STUB_SUDO_RC:-1}"')
        for name in ("STUB_CONTAINERS", "STUB_SECRETS", "STUB_SUDO", "STUB_CONTAINER_STATE",
                     "STUB_CONTAINER_RC", "STUB_CONTAINER_OOM", "STUB_PS_FAIL", "STUB_DOCKER_RM",
                     "STUB_SYSTEMCTL"):
            (self.root / name).write_text("", encoding="utf-8")
        # `docker inspect -f {{.State.FinishedAt}}` answers this file; absent, an old end time.
        self.finished = self.root / "STUB_CONTAINER_FINISHED"
        self.env = {
            # These tests run INSIDE a box themselves, and the script takes its in-box paths when
            # it finds the box identity file; pointed at a path that does not exist.
            "AGENTBOX_IDENTITY_FILE": str(self.root / "not-a-box"),
            "AGENTBOX_BOX": "",
            "HOME": str(self.home),
            "PATH": ":".join([str(self.stubs), "/usr/bin", "/bin"]),
            "DOTFILES_DIR": str(DOTFILES),
            "AGENTBOX_BOXES": str(self.boxes),
            "AGENTBOX_DISKS": str(self.disks),
            "AGENTBOX_CACHE_ROOT": str(self.cache),
            "AGENTBOX_RUN_BASE": str(self.run_base),
            "AGENTBOX_PROC": str(self.proc),
            "AGENTBOX_DISK": "0",
            "AGENTBOX_COLLECT_LOG_DIR": str(self.root / "collect-logs"),
            "XDG_STATE_HOME": str(self.root / "state"),
            # collect_start's private copy goes here, never in the host's /tmp.
            "TMPDIR": str(self.root),
            "AGENTBOX_EXITS": str(self.root / "agentbox-exits.tsv"),
            **{name: str(self.root / name) for name in (
                "STUB_CONTAINERS", "STUB_SECRETS", "STUB_SUDO", "STUB_CONTAINER_STATE", "STUB_CONTAINER_RC",
                "STUB_CONTAINER_OOM", "STUB_PS_FAIL", "STUB_DOCKER_RM", "STUB_SYSTEMCTL")},
            "STUB_CONTAINER_FINISHED": str(self.root / "STUB_CONTAINER_FINISHED"),
        }
        self.idle_process()

    # ── fixtures ────────────────────────────────────────────────────────────────────────────
    def run_bash(self, script: str, extra_env: dict[str, str] | None = None, *,
                 unprivileged: bool = False) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*(self.unprivileged() if unprivileged else []), "bash", "-c", f'source "{AGENTBOX}"\n{script}'],
            capture_output=True, text=True, check=False, timeout=self.TIMEOUT,
            env={**self.env, **(extra_env or {})}, start_new_session=True,
        )

    def stub_log(self, name: str) -> str:
        return (self.root / name).read_text(encoding="utf-8")

    def set_containers(self, *names: str) -> None:
        (self.root / "STUB_CONTAINERS").write_text("".join(f"{n}\n" for n in names), encoding="utf-8")

    def set_container_state(self, state: str, rc: str = "0", oom: str = "false") -> None:
        """What `docker inspect` answers for every stub container."""
        (self.root / "STUB_CONTAINER_STATE").write_text(state + "\n", encoding="utf-8")
        (self.root / "STUB_CONTAINER_RC").write_text(rc + "\n", encoding="utf-8")
        (self.root / "STUB_CONTAINER_OOM").write_text(oom + "\n", encoding="utf-8")

    def exits_tally(self) -> str:
        path = self.root / "agentbox-exits.tsv"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def idle_process(self) -> None:
        """A process of this user standing in /, as the collector's own always is in the real
        /proc: a walk that finds no process at all cannot tell who stands where."""
        d = self.proc / "1"
        d.mkdir(parents=True, exist_ok=True)
        if not os.path.lexists(d / "cwd"):
            os.symlink("/", d / "cwd")
        (d / "cgroup").write_text("0::/init.scope\n", encoding="utf-8")

    SCRATCH_UID = 65534

    def unprivileged(self) -> list[str]:
        """Root reads a file of mode 000, so as root the command runs as a scratch uid that owns
        the whole fixture tree (setpriv, util-linux); as any other user it runs as that user."""
        if os.geteuid() != 0:
            return []
        setpriv = shutil.which("setpriv")
        self.assertIsNotNone(setpriv, "as root this test drops to a scratch uid with setpriv (util-linux)")
        for root, dirs, files in os.walk(self.root):
            for name in dirs + files:
                os.chown(Path(root) / name, self.SCRATCH_UID, self.SCRATCH_UID, follow_symlinks=False)
        os.chown(self.root, self.SCRATCH_UID, self.SCRATCH_UID)
        argv = [setpriv, f"--reuid={self.SCRATCH_UID}", f"--regid={self.SCRATCH_UID}", "--clear-groups", "--"]
        reach = subprocess.run([*argv, "test", "-r", str(AGENTBOX), "-a", "-w", str(self.root)],
                               capture_output=True, text=True, check=False)
        self.assertEqual(reach.returncode, 0, f"uid {self.SCRATCH_UID} cannot reach {self.root} or {AGENTBOX}")
        return argv

    def git(self, *args: str, cwd: Path) -> None:
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                       env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"})

    def clone_with_unpushed_work(self, directory: Path) -> Path:
        """A clone in a box holding a commit no remote has, an uncommitted change and a stash:
        each of them kept the whole box before this change."""
        clone = directory / "repo"
        clone.mkdir(parents=True)
        self.git("init", "-q", "-b", "main", cwd=clone)
        self.git("config", "user.email", "t@example.com", cwd=clone)
        self.git("config", "user.name", "T", cwd=clone)
        (clone / "file").write_text("x\n", encoding="utf-8")
        self.git("add", "file", cwd=clone)
        self.git("commit", "-qm", "work no remote has", cwd=clone)
        (clone / "file").write_text("stashed\n", encoding="utf-8")
        self.git("stash", "-q", cwd=clone)
        (clone / "file").write_text("uncommitted\n", encoding="utf-8")
        return clone

    def leftover(self, box: str, *, hours: float = 2, directory: bool = True, record: bool = True,
                 image: bool = False, cache: bool = True, run: bool = True, launcher: str | None = None,
                 session: str = SID) -> None:
        """What a box leaves when its launcher dies: some of its directory, record, image, cache
        and run dir, each file HOURS old. A record without `launcher=` is a box started before this
        change; LAUNCHER is the `launcher=`/`launcher_start=` lines of a record of this change."""
        made = []
        if directory:
            (self.boxes / box / "home").mkdir(parents=True)
            (self.boxes / box / "home" / "notes.md").write_text("a note\n", encoding="utf-8")
            self.clone_with_unpushed_work(self.boxes / box)
            made.append(self.boxes / box)
        if record:
            rec = self.disks / f"{box}.info"
            rec.write_text(f"box={box}\nsession={session}\ncreated=2026-11-15T10:00:00Z\ncommand=omp\n"
                           + (launcher or ""), encoding="utf-8")
            (self.disks / f"{box}.info.exit").write_text("", encoding="utf-8")
            made += [rec, self.disks / f"{box}.info.exit"]
        if image:
            (self.disks / f"{box}.img").write_bytes(b"\0" * 4096)
            made.append(self.disks / f"{box}.img")
        if cache:
            (self.cache / box / "go-mod").mkdir(parents=True)
            (self.cache / box / "go-mod" / "mod.zip").write_text("z\n", encoding="utf-8")
            (self.cache / box / "go-mod").chmod(0o555)
            made.append(self.cache / box)
        if run:
            (self.run_base / box / "run-user").mkdir(parents=True)
            (self.run_base / box / "agent-secrets").mkdir()
            (self.run_base / box / "run-user" / "agentbox-session").write_text(
                f"version=1\npid=7\nsession={session}\nfile=/x.jsonl\n", encoding="utf-8")
            made.append(self.run_base / box)
        for path in made:
            age(path, hours)

    def gone(self, box: str) -> list[str]:
        """Each place the box is still in, which must be none."""
        places = [self.boxes / box, self.disks / f"{box}.img", self.disks / f"{box}.info",
                  self.disks / f"{box}.info.exit", self.cache / box, self.run_base / box]
        return [str(p) for p in places if os.path.lexists(p)]

    def collect(self, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return self.run_bash("cmd_collect", extra_env)

    def tearDown(self) -> None:
        for root, dirs, _ in os.walk(self.root):
            for name in dirs:
                try:
                    os.chmod(Path(root) / name, 0o755)
                except OSError:
                    pass

    # ── the record ──────────────────────────────────────────────────────────────────────────
    def test_a_box_is_stamped_with_its_session_name_creation_time_command_and_launcher(self) -> None:
        box = "agentbox-0123456789ab"
        result = self.run_bash(f'box_record_write {box} "" omp --resume "/a path/_{SID}.jsonl"\necho "pid=$BASHPID"')
        self.assertEqual(result.returncode, 0, result.stderr)
        record = self.disks / f"{box}.info"
        fields = dict(line.split("=", 1) for line in record.read_text(encoding="utf-8").splitlines())
        self.assertEqual(fields["box"], box)
        self.assertEqual(fields["session"], "", "a box that starts a NEW session has no id yet")
        self.assertRegex(fields["created"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertIn("--resume", fields["command"])
        self.assertIn("_%s.jsonl" % SID, fields["command"])
        # The launcher is the process that wrote the record, with its start time, so a pid used
        # again after it is not taken for it.
        self.assertEqual(f"pid={fields['launcher']}", result.stdout.strip())
        self.assertRegex(fields["launcher_start"], r"^[0-9]+$")
        self.assertEqual(len(record.read_text(encoding="utf-8").splitlines()), 6,
                         "a command with a space or a newline in it stays on one line")
        # The id the box's omp reports (box_session_id) lands in the record, and only the first:
        # a box runs one session.
        self.assertEqual(self.run_bash(f"box_record_session {box} {SID}").returncode, 0)
        self.assertEqual(self.run_bash(f"box_record_session {box} {OTHER_SID}").returncode, 0)
        self.assertEqual(self.run_bash(f"box_record_get {box} session").stdout.strip(), SID)

    def test_the_record_and_its_exit_marker_go_with_the_image(self) -> None:
        box = "agentbox-0123456789ab"
        self.leftover(box, image=True, hours=0)
        result = self.run_bash(f"close_box_disk {box}")
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in (f"{box}.img", f"{box}.info", f"{box}.info.exit"):
            self.assertFalse((self.disks / name).exists(), name)

    def test_a_box_with_no_image_gets_no_record(self) -> None:
        """`--disk 0`: `open_box_disk` deliberately leaves the images directory absent, and such
        a box has no image to orphan, so the record is skipped rather than made — making that
        directory is what `scripts/tests/test_agentbox_limits.py` fails on."""
        shutil.rmtree(self.disks)
        result = self.run_bash('box_record_write agentbox-nodisk000001 "" omp')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.disks.exists(), "a box with no image makes no images directory")

    def test_an_exit_is_recorded_once_however_many_times_it_is_tried(self) -> None:
        """The launcher, close_box and the collector each try; the tally gets one row."""
        box = "agentbox-0123456789ab"
        self.leftover(box, hours=0)
        (self.disks / f"{box}.info.exit").unlink()
        self.set_containers(box)
        self.set_container_state("exited", rc="137")
        first = self.run_bash(f'record_box_exit {box} {SID} ""; record_box_exit {box} {SID} ""')
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(self.exits_tally().count(box), 1, self.exits_tally())

    # ── the end of a box ────────────────────────────────────────────────────────────────────
    def test_end_deletes_everything_even_unpushed_clones(self) -> None:
        """A box whose clone holds a commit no remote has, a stash and an uncommitted change, and
        whose home holds a file the session wrote. Its end deletes all of it, keeps nothing for
        anybody and says nothing about keeping; the exit row, the broker's revoke and the tmux
        relay's stop still happen, and the container goes with its volume (`docker rm -v`)."""
        box = "agentbox-0123456789ab"
        self.leftover(box, image=True, hours=0)
        (self.run_base / box / "enrollment-id").write_text("enr-own\n", encoding="utf-8")
        (self.disks / f"{box}.info.exit").unlink()
        self.set_containers(box)
        self.set_container_state("exited", rc="0")
        result = self.run_bash(f"close_box {box} {SID} repo")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.gone(box), [])
        self.assertFalse((self.root / "state" / "agentbox" / "box-homes").exists(), "nothing is copied out")
        for word in ("kept", "left in place", "holds work"):
            self.assertNotIn(word, result.stdout + result.stderr)
        self.assertIn(f"rm -v {box}", self.stub_log("STUB_DOCKER_RM"))
        self.assertIn(box, self.exits_tally())
        self.assertIn("unenroll --helper --enrollment enr-own", self.stub_log("STUB_SECRETS"))
        self.assertIn(f"--user stop agentbox-tmux-{box}", self.stub_log("STUB_SYSTEMCTL"))
        # A container that is still running after the wait is removed by force, with its volume.
        self.leftover(box, hours=0)
        self.set_containers(box)
        self.set_container_state("running")
        result = self.run_bash(f"seq() {{ echo 1; }}\nsleep() {{ :; }}\nclose_box {box} {SID} repo")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"rm -fv {box}", self.stub_log("STUB_DOCKER_RM"))

    def launch(self, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        """`agentbox new repo1 -- omp --resume SID`, every host step stubbed. `$DOTFILES_DIR/
        scripts/agentbox` is a stub that logs each call and its TMUX_PANE to $COLLECT_CALLS: the
        collector a launch starts runs from a copy of that file (collect_start)."""
        (self.home / ".config").mkdir(exist_ok=True)
        (self.home / ".omp" / "agent" / "sessions").mkdir(parents=True, exist_ok=True)
        (self.root / "src" / "repo1").mkdir(parents=True, exist_ok=True)
        run_args = self.root / "run-args"
        write_stub(self.stubs, "docker", r'[[ "$1" == run ]] && { shift; printf "%s\n" "$@" >"$STUB_RUN_ARGS"; }; exit 0')
        for name in ("git", "sleep"):
            write_stub(self.stubs, name, "exit 0")
        write_stub(self.stubs, "curl", "exit 7")
        write_stub(self.stubs, "agent-secrets", "echo none; exit 1")
        write_stub(self.stubs, "df", "printf 'Avail\\n%s\\n' 1099511627776")
        dot = self.root / "dotfiles"
        write_stub(dot / "scripts", "envoy-nats-seed", "exit 1")
        write_stub(dot / "scripts", "agentbox",
                   'printf "%s pane=[%s]\\n" "$*" "${TMUX_PANE:-}" >>"$COLLECT_CALLS"; exit 0')
        # The real snapshot, which writes the box's private Docker config and its (empty) bind
        # list into the fixture's box tree, and a fixture secretsd socket for mount_args, which
        # refuses a box with none: this test reads mount_args' real work, and neither the host's
        # ~/.docker nor its secretsd may decide it (a Legion pod has neither).
        (dot / "scripts" / "agentbox-docker-snapshot").symlink_to(DOTFILES / "scripts" / "agentbox-docker-snapshot")
        write_stub(dot / "scripts", "on-google-vm", "exit 1")
        write_stub(dot / "scripts", "omp-session-live", "exit 1")
        (dot / "agent-secrets").symlink_to(DOTFILES / "agent-secrets")
        (dot / "agentbox").symlink_to(DOTFILES / "agentbox")
        secretsd = self.root / "secretsd.sock"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(secretsd))
        self.addCleanup(listener.close)
        no_host = (
            "image_tag() { echo agentbox:test; }; build_image() { :; }; ensure_forwarders() { :; }\n"
            f"ensure_network() {{ NETWORK_GATEWAY={GATEWAY} NETWORK_SUBNET=172.31.0.0/16; }}\n"
            "clone_checkout() { :; }; close_box() { :; }; box_cache_mounts() { :; }\n"
            "forward_channels() { FORWARD_CHANNELS=(); }\n"
            f"SECRETSD_SOCK={secretsd}\n"
        )
        return self.run_bash(f"{no_host}cmd_new repo1 -- omp --resume {SID}", {
            "DOTFILES_DIR": str(dot), "AGENTBOX_SRC": str(self.root / "src"), "STUB_RUN_ARGS": str(run_args),
            "AGENT_SECRETS_URL": "https://broker.example", "AGENTBOX_DURABLE": str(self.root / "durable"),
            "COLLECT_CALLS": str(self.root / "COLLECT_CALLS"), **(extra_env or {})})

    def test_a_new_box_gets_nothing_of_an_earlier_box(self) -> None:
        """The seed of a new box's home from what a close carried out goes too: a launch with
        `--resume` of a session that has such a copy starts with an empty home."""
        copy = self.root / "state" / "agentbox" / "box-homes" / SID / "agentbox-000000000000-20261115T000000Z"
        copy.mkdir(parents=True)
        (copy / "notes.md").write_text("from the last box\n", encoding="utf-8")
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])
        (box,) = [p for p in self.boxes.iterdir()]
        self.assertEqual(sorted(p.name for p in (box / "home").iterdir()), [], "the new box's home starts empty")
        self.assertNotIn("last box left", result.stderr)

    # ── the collector ───────────────────────────────────────────────────────────────────────
    def test_collector_deletes_a_leftover_in_every_place_it_left(self) -> None:
        """A box whose launcher a SIGKILL ended and whose container exited: its directory with
        an unpushed clone, its image, record and `.exit` marker, its cache (read-only inside, as Go
        leaves one), its run dir, its exited container with its volume and its tmux relay unit.
        A record without a launcher pid, as every box started before this change has, counts as a
        launcher that is not running. A box that left only its cache, or only its run dir, goes
        too."""
        self.leftover(OLD, image=True)
        (self.disks / f"{OLD}.info.exit").unlink()
        only_cache, only_run = "agentbox-0000000000c3", "agentbox-0000000000d4"
        self.leftover(only_cache, directory=False, record=False, run=False)
        self.leftover(only_run, directory=False, record=False, cache=False)
        self.set_containers(OLD)
        self.set_container_state("exited", rc="137")
        result = self.collect()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for box in (OLD, only_cache, only_run):
            self.assertEqual(self.gone(box), [], result.stdout + result.stderr)
        self.assertIn(f"rm -v {OLD}", self.stub_log("STUB_DOCKER_RM"))
        self.assertIn(f"--user stop agentbox-tmux-{OLD}", self.stub_log("STUB_SYSTEMCTL"))
        self.assertIn(OLD, self.exits_tally())
        self.assertIn("SIGKILL", self.exits_tally())
        self.assertIn(f"{OLD} removed", result.stdout)
        self.assertNotIn("kept", result.stdout + result.stderr)

    def test_collector_unenrolls_before_it_deletes_the_run_dir(self) -> None:
        """The run dir holds the enrollment the box recorded: the collector runs unenroll_box
        while it is still there. With the box's own id, that is one revoke at once."""
        self.leftover(OLD)
        (self.run_base / OLD / "enrollment-id").write_text("enr-own\n", encoding="utf-8")
        age(self.run_base / OLD, 2)
        order = self.root / "ORDER"
        seen = self.run_bash(
            f'unenroll_box() {{ [[ -d "$RUN_BASE/$1" ]] && echo "unenroll $1 with its run dir" >>"{order}"; }}\ncmd_collect')
        self.assertEqual(seen.returncode, 0, seen.stdout + seen.stderr)
        self.assertEqual(order.read_text(encoding="utf-8"), f"unenroll {OLD} with its run dir\n")
        self.leftover(OLD)
        (self.run_base / OLD / "enrollment-id").write_text("enr-own\n", encoding="utf-8")
        age(self.run_base / OLD, 2)
        result = self.collect()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("unenroll --helper --enrollment enr-own", self.stub_log("STUB_SECRETS"))
        self.assertEqual(self.gone(OLD), [])

    def test_collector_leaves_an_unreadable_enrollment_session_to_its_lease(self) -> None:
        """`enrollment-session` is there but cannot be read: the recorded id can be a session's,
        so nothing is revoked and the lease ends it, as #66's unenroll_box does; the run dir still
        goes."""
        self.leftover(OLD)
        (self.run_base / OLD / "enrollment-id").write_text("enr-own\n", encoding="utf-8")
        (self.run_base / OLD / "enrollment-session").write_text(f"{SID}\n", encoding="utf-8")
        (self.run_base / OLD / "enrollment-session").chmod(0)
        age(self.run_base / OLD, 2)
        # Root reads a file of mode 000: as root, this runs as a scratch uid that owns the fixture.
        result = self.run_bash("cmd_collect", unprivileged=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("unenroll", self.stub_log("STUB_SECRETS"))
        self.assertIn("left to end with its lease", result.stdout + result.stderr)
        self.assertEqual(self.gone(OLD), [])

    def test_collector_leaves_bus_session_keys_and_learn_dirs(self) -> None:
        """Entries of the run base that are not boxes: the session bus relay, the session keys with
        the detached unenroll processes' log, and the key dirs of #66's learn calls. All are old,
        and none is a box, so none is touched; a leftover box beside them goes."""
        (self.run_base / "bus").mkdir()
        (self.run_base / "bus" / "bus").write_text("", encoding="utf-8")
        (self.run_base / "session-keys" / SID).mkdir(parents=True)
        (self.run_base / "session-keys" / "unenroll.log").write_text("a line\n", encoding="utf-8")
        (self.run_base / "learn.Ab12Cd").mkdir()
        (self.run_base / "learn.Ab12Cd" / "error").write_text("", encoding="utf-8")
        for name in ("bus", "session-keys", "learn.Ab12Cd"):
            age(self.run_base / name, 2)
        self.leftover(OLD)
        result = self.collect()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.gone(OLD), [], "the control: a leftover box beside them goes")
        for path in ("bus/bus", f"session-keys/{SID}", "session-keys/unenroll.log", "learn.Ab12Cd/error"):
            self.assertTrue((self.run_base / path).exists(), path)

    def test_collector_keeps_what_is_not_a_leftover(self) -> None:
        start = (Path("/proc") / str(os.getpid()) / "stat").read_text().rsplit(")", 1)[1].split()[19]

        def launcher_runs() -> None:
            d = self.proc / "4242"
            d.mkdir(parents=True, exist_ok=True)
            (d / "stat").write_text(f"4242 (agentbox) S 1 1 1 0 -1 0 0 0 0 0 0 0 0 0 20 0 1 0 {start} 0 0\n",
                                    encoding="utf-8")

        def stands_in(path: Path, cgroup: str = "0::/user.slice/app.slice/bash.scope") -> None:
            d = self.proc / "5151"
            d.mkdir(parents=True, exist_ok=True)
            os.symlink(path, d / "cwd")
            (d / "cgroup").write_text(cgroup + "\n", encoding="utf-8")

        cases = {
            "its container runs": lambda: (self.set_containers(OLD), self.set_container_state("running")),
            "its container is being made": lambda: (self.set_containers(OLD), self.set_container_state("created")),
            "docker cannot be asked": lambda: (self.root / "STUB_PS_FAIL").write_text(f"{OLD}\n", encoding="utf-8"),
            "its launcher runs": launcher_runs,
            "a process stands in its directory": lambda: stands_in(self.boxes / OLD / "repo"),
            "a process stands in its run dir": lambda: stands_in(self.run_base / OLD / "run-user"),
        }
        for name, make in cases.items():
            with self.subTest(name):
                self.forget(OLD)
                shutil.rmtree(self.proc)
                self.proc.mkdir()
                self.idle_process()
                self.set_containers()
                self.set_container_state("exited")
                (self.root / "STUB_PS_FAIL").write_text("", encoding="utf-8")
                self.leftover(OLD, launcher=f"launcher=4242\nlauncher_start={start}\n")
                make()
                result = self.collect()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertTrue((self.boxes / OLD).exists(), result.stdout)
                self.assertTrue((self.run_base / OLD).exists(), result.stdout)
                self.assertTrue((self.disks / f"{OLD}.info").exists(), result.stdout)
                self.assertIn(f"{OLD} not removed:", result.stdout)
                self.assertIn("remove it with:", result.stdout)
                self.assertIn(str(self.boxes / OLD), result.stdout.split("remove it with:", 1)[1])
                self.assertNotIn("kept", result.stdout + result.stderr)
                self.assertEqual(self.stub_log("STUB_DOCKER_RM"), "")
        with self.subTest("it is younger than one hour"):
            self.leftover(YOUNG, hours=0.5)
            result = self.collect()
            self.assertTrue((self.boxes / YOUNG).exists(), result.stdout)
            self.assertTrue((self.run_base / YOUNG).exists(), result.stdout)
        with self.subTest("one young file under any root keeps it"):
            self.leftover("agentbox-0000000000e5")
            (self.run_base / "agentbox-0000000000e5" / "run-user" / "agentbox-session").touch()
            result = self.collect()
            self.assertTrue((self.boxes / "agentbox-0000000000e5").exists(), result.stdout)

    def test_the_box_s_own_tmux_relay_does_not_keep_it(self) -> None:
        """The relay unit agentbox-tmux-<box> outlives a launcher that died, and its process stands
        in the box's run dir; it is the box's own, and goes with it."""
        self.leftover(OLD)
        d = self.proc / "5151"
        d.mkdir()
        os.symlink(self.run_base / OLD / "run-user", d / "cwd")
        (d / "cgroup").write_text(f"0::/user.slice/user-1000.slice/user@1000.service/app.slice/agentbox-tmux-{OLD}.service\n",
                                  encoding="utf-8")
        result = self.collect()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.gone(OLD), [], result.stdout)
        self.assertIn(f"--user stop agentbox-tmux-{OLD}", self.stub_log("STUB_SYSTEMCTL"))

    def test_the_collector_touches_nothing_that_is_not_a_box(self) -> None:
        """Names of another form under its roots are not boxes, and a container that runs is not
        a leftover, wherever its files are."""
        self.set_containers("agentbox-0000000000f6")
        self.set_container_state("running")
        for odd in ("agentbox-notabox", "agentbox-0000000000a1x"):
            (self.run_base / odd).mkdir()
            age(self.run_base / odd, 2)
        result = self.collect()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.stub_log("STUB_DOCKER_RM"), "")
        self.assertEqual(self.stub_log("STUB_SYSTEMCTL"), "")
        for odd in ("agentbox-notabox", "agentbox-0000000000a1x"):
            self.assertTrue((self.run_base / odd).exists())

    def test_the_collector_removes_an_exited_container_whose_files_are_gone(self) -> None:
        """An exited box container with nothing of it left under the collector's roots is what a
        box left too, with its inner Docker store in its anonymous volume: a box whose files went
        while its container stayed (any box closed between 72f17697, 2026-11-07, and this change
        whose `docker rm` failed), or one whose files a person removed. The end of a box deletes
        everything in it (Sami, dispatch://INFRA-1720/ask/16322992), so the collector removes such
        a container, with its volume and after its exit row, once it ended more than an hour ago;
        it judges the hour by the container's end, since it has no file to judge it by."""
        box = "agentbox-0000000000f6"
        self.set_containers(box)
        self.set_container_state("exited", rc="137")
        with self.subTest("it ended ten minutes ago"):
            self.finished.write_text(
                time.strftime("%Y-%m-%dT%H:%M:%S.000000000Z", time.gmtime(time.time() - 600)) + "\n",
                encoding="utf-8")
            result = self.collect()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(self.stub_log("STUB_DOCKER_RM"), "")
            self.assertIn(f"{box} not removed: its container ended", result.stdout)
            self.assertIn(f"remove it with: docker rm -fv {box}", result.stdout)
        with self.subTest("it ended two hours ago"):
            self.finished.write_text(
                time.strftime("%Y-%m-%dT%H:%M:%S.000000000Z", time.gmtime(time.time() - 7200)) + "\n",
                encoding="utf-8")
            result = self.collect()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(f"rm -v {box}", self.stub_log("STUB_DOCKER_RM"))
            self.assertIn(box, self.exits_tally())
            self.assertIn(f"--user stop agentbox-tmux-{box}", self.stub_log("STUB_SYSTEMCTL"))
            self.assertIn(f"{box} removed", result.stdout)
            self.assertEqual(self.gone(box), [])
        with self.subTest("an end time docker does not give keeps it"):
            self.set_containers(box)
            (self.root / "STUB_DOCKER_RM").write_text("", encoding="utf-8")
            self.finished.write_text("0001-01-01T00:00:00Z-not-a-time\n", encoding="utf-8")
            result = self.collect()
            self.assertEqual(self.stub_log("STUB_DOCKER_RM"), "")
            self.assertIn(f"{box} not removed", result.stdout)

    def test_a_removal_that_fails_is_said_with_its_command_never_as_removed(self) -> None:
        """A place of the box that cannot be removed is a failure line naming the command that
        removes what is left; the collector then does not say the box was removed, and the end of a
        box says it too. rm fails here on the path the test names (STUB_RM_FAIL), as root too."""
        places = {
            "its cache": lambda box: self.cache / box,
            "its run dir": lambda box: self.run_base / box,
            "its image": lambda box: self.disks / f"{box}.img",
        }
        for name, place in places.items():
            with self.subTest("the collector", place=name):
                self.forget(OLD)
                self.leftover(OLD, image=True)
                result = self.collect({"STUB_RM_FAIL": str(place(OLD))})
                said = result.stdout + result.stderr
                self.assertTrue(os.path.lexists(place(OLD)), said)
                self.assertNotIn(f"{OLD} removed", said)
                self.assertIn(f"removing {OLD} failed", said)
                self.assertRegex(said, rf"failed[^\n]*{place(OLD)}[^\n]*with: [^\n]*rm -[rf]+ [^\n]*{place(OLD)}")
                self.assertNotIn("kept", said)
                self.assertNotIn("left in place", said)
            with self.subTest("the end of a box", place=name):
                box = "agentbox-0123456789ab"
                self.forget(box)
                self.leftover(box, image=True, hours=0)
                result = self.run_bash(f"close_box {box} {SID} repo", {"STUB_RM_FAIL": str(place(box))})
                said = result.stdout + result.stderr
                self.assertTrue(os.path.lexists(place(box)), said)
                self.assertRegex(said, rf"removing [^\n]*{place(box)}[^\n]*failed[^\n]*with: [^\n]*rm -[rf]+ [^\n]*{place(box)}")
                self.assertNotIn("left in place", said)

    def test_a_walk_that_cannot_read_keeps_the_box_and_says_why(self) -> None:
        """What the collector could not read is no answer: a process of this user whose working
        directory it cannot read could stand in the box, a walk of the processes that found none
        saw nothing, and an age walk that failed did not show the files are old. Each keeps the box,
        with a line that says what could not be read and the command that removes it."""
        cases = {
            "a process's working directory": (lambda: self.unreadable_cwd(), {},
                                              "cannot tell whether a process stands in it: process 6161"),
            "a walk that found no process": (lambda: shutil.rmtree(self.proc / "1"), {},
                                             "cannot tell whether a process stands in it: the walk of"),
            "the age of its files": (lambda: None, {"STUB_FIND_FAIL": str(self.boxes / OLD)},
                                     "cannot tell how old its files are: find: "),
        }
        for name, (make, env, why) in cases.items():
            with self.subTest(name):
                self.forget(OLD)
                shutil.rmtree(self.proc / "6161", ignore_errors=True)
                self.idle_process()
                self.leftover(OLD)
                make()
                result = self.collect(env)
                said = result.stdout + result.stderr
                self.assertEqual(result.returncode, 0, said)
                self.assertTrue((self.boxes / OLD).exists(), said)
                self.assertTrue((self.run_base / OLD).exists(), said)
                self.assertIn(f"{OLD} not removed: {why}", said)
                self.assertIn("remove it with:", said)
                self.assertNotIn(f"{OLD} removed", said)
        with self.subTest("the control: with every walk readable the box goes"):
            self.forget(OLD)
            shutil.rmtree(self.proc / "6161", ignore_errors=True)
            self.idle_process()
            self.leftover(OLD)
            result = self.collect()
            self.assertEqual(self.gone(OLD), [], result.stdout + result.stderr)
            self.assertIn(f"{OLD} removed", result.stdout)

    def unreadable_cwd(self) -> None:
        """A process of this user whose working directory the walk cannot read: `cwd` is not a
        link the walk can read (as a process's is when it may not be inspected)."""
        d = self.proc / "6161"
        d.mkdir()
        (d / "cwd").write_text("", encoding="utf-8")
        (d / "cgroup").write_text("0::/user.slice/app.slice/bash.scope\n", encoding="utf-8")

    def forget(self, box: str) -> None:
        """Remove whatever an earlier subtest left of BOX, read-only directories included."""
        for p in (self.boxes / box, self.cache / box, self.run_base / box):
            if not p.exists():
                continue
            for root, dirs, _ in os.walk(p):
                for d in dirs:
                    if not (Path(root) / d).is_symlink():
                        os.chmod(Path(root) / d, 0o755)
            shutil.rmtree(p)
        for p in self.disks.glob(f"{box}.*"):
            p.unlink()

    def test_collectors_run_one_at_a_time(self) -> None:
        self.leftover(OLD)
        lock_dir = self.root / "collect-logs"
        lock_dir.mkdir()
        with (lock_dir / "collect.lock").open("w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            result = self.collect()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("another collector runs", result.stdout)
        self.assertTrue((self.boxes / OLD).exists())
        result = self.collect()
        self.assertEqual(self.gone(OLD), [], result.stdout)

    def test_agentbox_new_starts_the_collector_detached(self) -> None:
        """`agentbox new` starts the collector, every box, a session's first box too, detached from
        the launcher's pane and without TMUX_PANE (close_box's attention step must never run for the
        live pane from there). Driven through cmd_new, so a launch that no longer calls
        collect_start fails it."""
        calls = self.root / "COLLECT_CALLS"
        result = self.launch({"TMUX_PANE": "%42"})
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])
        for _ in range(100):
            if calls.exists() and calls.read_text(encoding="utf-8"):
                break
            time.sleep(0.1)
        self.assertTrue(calls.exists(), "the launch started no collector")
        self.assertEqual(calls.read_text(encoding="utf-8").strip(), "collect pane=[]")

    # ── the session's broker enrollment ─────────────────────────────────────────────────────
    def enrolled(self, box: str, session: str, *, owner: bool = False) -> None:
        """A box as enroll_box leaves it: its enrollment id and the session it enrolled for, and
        the session key dir whose `owner` names the last box that enrolled."""
        (self.run_base / box).mkdir(parents=True, exist_ok=True)
        (self.run_base / box / "enrollment-id").write_text(f"enr-{box}\n", encoding="utf-8")
        (self.run_base / box / "enrollment-session").write_text(f"{session}\n", encoding="utf-8")
        keys = self.run_base / "session-keys" / session
        keys.mkdir(parents=True, exist_ok=True)
        (keys / "key.pem").write_text("key\n", encoding="utf-8")
        if owner:
            (keys / "owner").write_text(f"{box}\n", encoding="utf-8")

    def unenroll_log(self) -> str:
        path = self.run_base / "session-keys" / "unenroll.log"
        for _ in range(200):
            if path.exists() and path.read_text(encoding="utf-8").strip():
                break
            time.sleep(0.1)
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def test_a_close_leaves_a_live_boxs_enrollment_alone(self) -> None:
        """The enrollment is the SESSION's (runtime id session-<id>), so a box that ends while
        another box of that session runs must not end it. At 20:17Z on 2026-11-04 a duplicate
        box's close took the ORIGINAL box's broker identity: `owner` still named the duplicate,
        so the waiter read "nobody came back"."""
        live, duplicate = "agentbox-live00000001", "agentbox-dup000000002"
        self.enrolled(live, SID)
        self.enrolled(duplicate, SID, owner=True)  # the duplicate enrolled last: owner is its own
        self.set_containers(live)                  # and the ORIGINAL is the one still running
        result = self.run_bash(f"unenroll_box {duplicate}\nwait", {"AGENTBOX_UNENROLL_GRACE": "0"})
        self.assertEqual(result.returncode, 0, result.stderr)
        log = self.unenroll_log()
        self.assertIn(f"session {SID} is live in box {live}", log)
        self.assertNotIn("unenroll", self.stub_log("STUB_SECRETS"),
                         "the live box's broker identity must survive its duplicate's close")
        self.assertTrue((self.run_base / "session-keys" / SID / "key.pem").exists(),
                        "the session's key is the live box's too")

    def test_a_session_that_came_back_in_a_later_box_keeps_its_enrollment(self) -> None:
        old, new = "agentbox-old000000001", "agentbox-new000000002"
        self.enrolled(old, SID)
        self.enrolled(new, SID, owner=True)
        self.set_containers(new)
        result = self.run_bash(f"unenroll_box {old}\nwait", {"AGENTBOX_UNENROLL_GRACE": "0"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"session {SID} came back in {new}", self.unenroll_log())
        self.assertNotIn("unenroll", self.stub_log("STUB_SECRETS"))

    def test_a_session_with_no_box_left_does_end_its_enrollment(self) -> None:
        gone = "agentbox-gone00000001"
        self.enrolled(gone, SID, owner=True)
        self.set_containers()
        result = self.run_bash(f"unenroll_box {gone}\nwait", {"AGENTBOX_UNENROLL_GRACE": "0"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("did not come back", self.unenroll_log())
        self.assertIn(f"unenroll --helper --enrollment enr-{gone}", self.stub_log("STUB_SECRETS"))
        self.assertFalse((self.run_base / "session-keys" / SID).exists())

    def test_session_live_box_names_the_box_the_session_is_in(self) -> None:
        live, dead, mine = "agentbox-live00000001", "agentbox-dead00000002", "agentbox-mine00000003"
        self.enrolled(live, SID)
        self.enrolled(dead, SID)
        self.enrolled(mine, SID)
        self.set_containers(live)
        found = self.run_bash(f"session_live_box {SID} {mine}")
        self.assertEqual(found.returncode, 0, found.stderr)
        self.assertEqual(found.stdout.strip(), live)
        # Its own box never counts, and neither does another session's: the answer is `none`.
        self.set_containers(mine)
        for sid in (SID, OTHER_SID):
            found = self.run_bash(f"session_live_box {sid} {mine}")
            self.assertEqual((found.returncode, found.stdout), (1, "none\n"), found.stderr)
        # With no session to look for, there is no answer.
        found = self.run_bash(f'session_live_box "" {mine}')
        self.assertEqual((found.returncode, found.stdout), (2, ""), found.stderr)

    def test_session_live_box_answers_2_when_docker_cannot_be_asked(self) -> None:
        """A `docker ps` that fails for a box of the session is not "no container": status 2 and
        no output, which the waiter does not read as "no other box is live". A box found live
        after such a failure still gives status 0 and its name. The box that docker cannot be
        asked about sorts first, so session_live_box asks about it first."""
        unknown, live, mine = "agentbox-afail0000001", "agentbox-live00000002", "agentbox-mine00000003"
        self.enrolled(unknown, SID)
        self.enrolled(mine, SID)
        self.set_containers(unknown, mine)
        (self.root / "STUB_PS_FAIL").write_text(f"{unknown}\n", encoding="utf-8")
        found = self.run_bash(f"session_live_box {SID} {mine}")
        self.assertEqual((found.returncode, found.stdout), (2, ""), found.stderr)
        self.enrolled(live, SID)
        self.set_containers(unknown, live, mine)
        found = self.run_bash(f"session_live_box {SID} {mine}")
        self.assertEqual((found.returncode, found.stdout), (0, f"{live}\n"), found.stderr)

    def test_session_live_box_reads_past_a_record_it_cannot_read(self) -> None:
        """Two boxes whose `enrollment-session` is there but cannot be read, both sorted before
        the others: a directory (`-s` is true for it) and a file of mode 000. Under the script's
        set -e, `$(<file)` of the mode-000 file ended the whole command with status 1 and no
        output, before it looked at the boxes after it. session_live_box reads past both: with no
        live box after them it answers 2 (they can be boxes of the session), and with one it names
        that box."""
        folder, locked = "agentbox-adir00000001", "agentbox-alock0000002"
        live, mine = "agentbox-live00000003", "agentbox-mine00000004"
        for box in (folder, locked, mine):
            self.enrolled(box, SID)
        record = self.run_base / folder / "enrollment-session"
        record.unlink()
        record.mkdir()
        (record / "entry").write_text("", encoding="utf-8")  # a directory with an entry has a size
        (self.run_base / locked / "enrollment-session").chmod(0)
        self.set_containers(folder, locked, mine)
        # Root reads a file of mode 000: as root, this runs as a scratch uid that owns the fixture.
        found = self.run_bash(f"session_live_box {SID} {mine}", unprivileged=True)
        self.assertEqual((found.returncode, found.stdout), (2, ""), found.stderr)
        self.enrolled(live, SID)
        self.set_containers(folder, locked, live, mine)
        found = self.run_bash(f"session_live_box {SID} {mine}", unprivileged=True)
        self.assertEqual((found.returncode, found.stdout), (0, f"{live}\n"), found.stderr)


if __name__ == "__main__":
    unittest.main()
