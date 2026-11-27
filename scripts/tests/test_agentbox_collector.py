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
    is not running, whose newest file is over an hour old and in which no process stands, holds a
    file open or runs its program, after
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
import signal
import socket
import stat
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timezone
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
        # default. `rm` logs its arguments to $STUB_DOCKER_RM and forgets the container. `image ls`
        # answers the images in $STUB_IMAGES, none by default. `run` logs its arguments to
        # $STUB_DOCKER_RUN and does what remove_tree's root container does: the real find deletes
        # everything under each `--mount` source, which $STUB_ROOT_OWNED does not stop (root's).
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
if [[ "${1:-}" == image ]]; then
    cat "$STUB_IMAGES" 2>/dev/null
    exit 0
fi
if [[ "${1:-}" == run ]]; then
    echo "$*" >>"$STUB_DOCKER_RUN"
    src=() prev=""
    for a in "$@"; do
        if [[ "$prev" == --mount ]]; then s="${a#*source=}"; src+=("${s%%,*}"); fi
        prev="$a"
    done
    (( ${#src[@]} )) && /usr/bin/find "${src[@]}" -xdev -mindepth 1 -delete
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
        # `systemctl --user list-units` answers the units in $STUB_UNITS, one per line as
        # `--plain --no-legend` prints them; every call is logged.
        write_stub(self.stubs, "systemctl",
                   'printf "%s\\n" "$*" >>"$STUB_SYSTEMCTL"; [[ " $* " == *" list-units "* ]] && cat "$STUB_UNITS" 2>/dev/null; exit 0')
        write_stub(self.stubs, "tmux", 'exit 0')
        write_stub(self.stubs, "envoy", 'exit 0')
        write_stub(self.stubs, "agent-secrets", 'printf "%s\\n" "$*" >>"$STUB_SECRETS"; exit 0')
        write_stub(self.stubs, "mountpoint", 'exit 1')
        # `rm` and `find` are the real ones, except on a path under $STUB_RM_FAIL, which rm leaves
        # in place and fails on (as on a file it may not remove), and under $STUB_FIND_FAIL, which
        # find says it cannot read and fails on. Both work as root, where file modes do not. While
        # the path $STUB_ROOT_OWNED is there, rm fails on it and on each directory that holds it,
        # and removes nothing, as for a file a box's root made: only the `docker run` stub removes it.
        write_stub(self.stubs, "rm", '''for a in "$@"; do
    if [[ -n "${STUB_RM_FAIL:-}" && "$a" == "$STUB_RM_FAIL"* ]]; then
        echo "rm: cannot remove '$a': Operation not permitted" >&2; exit 1
    fi
    if [[ -n "${STUB_ROOT_OWNED:-}" && -e "$STUB_ROOT_OWNED" && "$STUB_ROOT_OWNED/" == "${a%/}/"* ]]; then
        echo "rm: cannot remove '$STUB_ROOT_OWNED': Permission denied" >&2; exit 1
    fi
done
exec /bin/rm "$@"''')
        write_stub(self.stubs, "find", '''for a in "$@"; do
    if [[ -n "${STUB_FIND_FAIL:-}" && "$a" == "$STUB_FIND_FAIL"* ]]; then
        echo "find: '$a': Permission denied" >&2; exit 1
    fi
done
# A /proc entry the kernel gives root's uid, as it does a process that is not dumpable: the owner
# field of a `-printf '%p\\t%U\\t%l'` line under $STUB_FIND_ROOT_OWNED reads 0. A walk that
# descends to $STUB_FIND_DENY says it may not read it, as find does at a directory this user may not
# open (runc's state directory in a box's run-user, root's and mode 1700), and fails.
deny=0
if [[ -n "${STUB_FIND_DENY:-}" ]]; then
    for a in "$@"; do [[ "$STUB_FIND_DENY" == "$a"/* ]] && deny=1; done
fi
if [[ -n "${STUB_FIND_ROOT_OWNED:-}" ]]; then
    /usr/bin/find "$@" | awk -F'\\t' -v p="$STUB_FIND_ROOT_OWNED" 'BEGIN { OFS = "\\t" } index($1, p) == 1 && NF == 3 { $2 = 0 } { print }'
    rc="${PIPESTATUS[0]}"
else
    /usr/bin/find "$@"
    rc=$?
fi
if (( deny )); then echo "find: '$STUB_FIND_DENY': Permission denied" >&2; exit 1; fi
exit "$rc"''')
        # No test may mount or unmount anything for real: sudo only logs.
        write_stub(self.stubs, "sudo", 'printf "%s\\n" "$*" >>"$STUB_SUDO"; exit "${STUB_SUDO_RC:-1}"')
        for name in ("STUB_CONTAINERS", "STUB_SECRETS", "STUB_SUDO", "STUB_CONTAINER_STATE",
                     "STUB_CONTAINER_RC", "STUB_CONTAINER_OOM", "STUB_PS_FAIL", "STUB_DOCKER_RM",
                     "STUB_SYSTEMCTL", "STUB_IMAGES", "STUB_DOCKER_RUN"):
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
                "STUB_CONTAINER_OOM", "STUB_PS_FAIL", "STUB_DOCKER_RM", "STUB_SYSTEMCTL", "STUB_IMAGES",
                "STUB_DOCKER_RUN")},
            "STUB_CONTAINER_FINISHED": str(self.root / "STUB_CONTAINER_FINISHED"),
            "STUB_UNITS": str(self.root / "STUB_UNITS"),
        }
        self.idle_process()
        # Paths unprivileged() gives to another uid than the scratch one (make_foreign).
        self.foreign_paths: list[Path] = []

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
        for path in self.foreign_paths:
            for root, dirs, files in os.walk(path):
                for name in dirs + files:
                    os.chown(Path(root) / name, self.FOREIGN_UID, self.FOREIGN_UID, follow_symlinks=False)
            os.chown(path, self.FOREIGN_UID, self.FOREIGN_UID)
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

    def test_a_filesystem_that_will_not_unmount_is_a_failed_removal_with_its_command(self) -> None:
        """close_box_disk said "its disk image … is left in place" when the unmount failed: a line
        that says something was kept. It is a failed removal, with the command that completes it,
        and status 1; the image and record stay for that command."""
        box = "agentbox-0123456789ab"
        self.leftover(box, image=True, hours=0)
        write_stub(self.stubs, "mountpoint", "exit 0")
        result = self.run_bash(f"close_box_disk {box}")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        image = self.disks / f"{box}.img"
        self.assertTrue(image.exists())
        self.assertIn(f"removing {box}'s disk image {image} failed", result.stderr)
        self.assertIn(f"remove it with: sudo umount {self.boxes / box} && ", result.stderr)
        self.assertIn(f"rm -rf {image}", result.stderr)
        for word in ("left in place", "kept"):
            self.assertNotIn(word, result.stdout + result.stderr)

    def test_the_doctor_box_goes_with_its_volume_and_everything_it_made(self) -> None:
        """`agentbox doctor` starts a box of its own and removes it after: its container with its
        anonymous volume (`docker rm -fv`), its cache, its run dir and its directory."""
        box = "agentbox-d0c70000000a"
        self.leftover(box, hours=0)
        self.set_containers(box)
        result = self.run_bash(f"remove_doctor_box {box}")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"rm -fv {box}", self.stub_log("STUB_DOCKER_RM"))
        self.assertEqual(self.gone(box), [], result.stdout + result.stderr)
        # A cache that will not go is said with its command, and the rest of the box still goes.
        self.leftover(box, hours=0)
        self.set_containers(box)
        result = self.run_bash(f"remove_doctor_box {box}", {"STUB_RM_FAIL": str(self.cache / box)})
        self.assertIn(f"removing {box}'s cache {self.cache / box} failed", result.stderr)
        self.assertEqual(self.gone(box), [str(self.cache / box)], result.stdout + result.stderr)

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
                (self.root / "STUB_DOCKER_RM").write_text("", encoding="utf-8")
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

    def test_a_tmux_relay_unit_whose_box_left_nothing_else_is_stopped(self) -> None:
        """A box's relay unit outlives a launcher that died. When that box left nothing else, no
        container, no file under any root, the collector never named it: on sami-devbox 15 such
        units ran from 5-12 Nov to 2026-11-16, while 8 collector runs looked at every box. It
        finds a box by its unit too, and stops a unit that is all that is left of its box. A unit
        of a box whose container runs, or that has files under the roots, is left to that box."""
        orphan, live, young = "agentbox-0000000000b9", "agentbox-0000000000ba", "agentbox-0000000000bb"
        (self.root / "STUB_UNITS").write_text(
            "".join(f"agentbox-tmux-{box}.service loaded active running agentbox tmux relay for {box}\n"
                    for box in (orphan, live, young)), encoding="utf-8")
        self.set_containers(live)
        self.set_container_state("running")
        self.leftover(young, hours=0)
        result = self.collect()
        said = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, said)
        stops = [line for line in self.stub_log("STUB_SYSTEMCTL").splitlines() if " stop " in f" {line} "]
        self.assertEqual(stops, [f"--user stop agentbox-tmux-{orphan}"], said)
        self.assertIn(f"{orphan} removed", result.stdout)
        self.assertTrue((self.run_base / young).exists())

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
        """Names of another form under its roots are not boxes, a container that runs is not a
        leftover, wherever its files are, and a box-shaped tree outside its roots (here under
        $HOME/boxes, where a machine without /mnt/nvme puts boxes, while this one's roots are
        elsewhere) is not one of its boxes, however old."""
        self.set_containers("agentbox-0000000000f6")
        self.set_container_state("running")
        for odd in ("agentbox-notabox", "agentbox-0000000000a1x"):
            (self.run_base / odd).mkdir()
            age(self.run_base / odd, 2)
        outside = self.home / "boxes" / "agentbox-0000000000a7"
        (outside / "home").mkdir(parents=True)
        (outside / "home" / "notes.md").write_text("a note\n", encoding="utf-8")
        age(outside, 2)
        result = self.collect()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.stub_log("STUB_DOCKER_RM"), "")
        self.assertNotIn(" stop ", self.stub_log("STUB_SYSTEMCTL"))
        for odd in ("agentbox-notabox", "agentbox-0000000000a1x"):
            self.assertTrue((self.run_base / odd).exists())
        self.assertTrue((outside / "home" / "notes.md").exists(), result.stdout)
        self.assertNotIn("agentbox-0000000000a7", result.stdout + result.stderr)

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

    def test_a_file_dated_in_the_future_does_not_keep_a_box(self) -> None:
        """`find -mmin -60` matched a file dated in any future year, so a box that held one was
        younger than an hour in every pass: on sami-devbox at 2026-11-23 a test's `out/late.v` dated
        2242 kept agentbox-469ecd050ad1 (68 GiB) and a `theory.v` dated 2096 kept
        agentbox-c1c776d981f6 (119 GiB). A date more than an hour past the pass's clock is no age; a
        file dated within that hour still keeps the box."""
        cases = {
            "dated 2242": (datetime(2242, 3, 16, tzinfo=timezone.utc).timestamp(), True),
            "dated half an hour ahead": (time.time() + 1800, False),
        }
        for name, (when, removed) in cases.items():
            with self.subTest(name):
                self.forget(OLD)
                self.leftover(OLD)
                late = self.boxes / OLD / "tmp" / "out" / "late.v"
                late.parent.mkdir(parents=True)
                late.write_text("x\n", encoding="utf-8")
                age(self.boxes / OLD, 2)
                os.utime(late, (when, when))
                result = self.collect()
                said = result.stdout + result.stderr
                self.assertEqual(result.returncode, 0, said)
                if removed:
                    self.assertIn(f"{OLD} removed", said)
                    self.assertEqual(self.gone(OLD), [], said)
                else:
                    self.assertIn(f"{OLD} not removed: {late} is younger than 60 minutes", said)
                    self.assertTrue(late.exists(), said)

    def test_files_a_box_s_root_made_are_removed_without_sudo(self) -> None:
        """A box's root writes, as root, into the host paths bound into the box: on sami-devbox
        `run-user/dconf` (root's, mode 700) kept $RUN_BASE/agentbox-ea4e05d6f7bc through its close and
        every collector pass, and `home/.cache` kept agentbox-9131d1634f66's directory. Each line
        named `chmod -R u+w …; rm -rf …`, which cannot remove a file root owns. What this user cannot
        remove goes, at the end of a box and in the collector, in a root container under runc with
        only that path bound in, from an agentbox image already on the host, and never through sudo.
        With no agentbox image on the host the line names `sudo rm -rf`, a command that can."""
        places = {
            "its run dir": (lambda box: self.run_base / box, Path("run-user/dconf/user")),
            "its directory": (lambda box: self.boxes / box, Path("home/.cache/x")),
        }
        for name, (top, inner) in places.items():
            for how, box in (("the collector", OLD), ("the end of a box", "agentbox-0123456789ab")):
                with self.subTest(how, place=name):
                    self.forget(box)
                    for log in ("STUB_DOCKER_RUN", "STUB_SUDO"):
                        (self.root / log).write_text("", encoding="utf-8")
                    (self.root / "STUB_IMAGES").write_text("agentbox:0123456789ab\n", encoding="utf-8")
                    self.leftover(box, hours=0 if how == "the end of a box" else 2)
                    owned = top(box) / inner
                    owned.parent.mkdir(parents=True, exist_ok=True)
                    owned.write_text("root's\n", encoding="utf-8")
                    if how == "the collector":
                        age(top(box), 2)
                        result = self.collect({"STUB_ROOT_OWNED": str(owned)})
                    else:
                        result = self.run_bash(f"close_box {box} {SID} repo", {"STUB_ROOT_OWNED": str(owned)})
                    said = result.stdout + result.stderr
                    self.assertEqual(self.gone(box), [], said)
                    self.assertNotIn("failed", said)
                    if how == "the collector":
                        self.assertIn(f"{box} removed", said)
                    self.assertEqual(
                        self.stub_log("STUB_DOCKER_RUN").splitlines(),
                        [f"run --rm --pull never --runtime runc --network none --user 0:0 --entrypoint find "
                         f"--mount type=bind,source={top(box)},target=/x/0 agentbox:0123456789ab /x/0 -xdev "
                         "-mindepth 1 -delete"],
                        said,
                    )
                    self.assertEqual(self.stub_log("STUB_SUDO"), "", said)
        with self.subTest("no agentbox image on the host"):
            self.forget(OLD)
            for log in ("STUB_DOCKER_RUN", "STUB_IMAGES"):
                (self.root / log).write_text("", encoding="utf-8")
            self.leftover(OLD)
            owned = self.run_base / OLD / "run-user" / "dconf" / "user"
            owned.parent.mkdir(parents=True)
            owned.write_text("root's\n", encoding="utf-8")
            age(self.run_base / OLD, 2)
            result = self.collect({"STUB_ROOT_OWNED": str(owned)})
            said = result.stdout + result.stderr
            self.assertTrue(owned.exists(), said)
            self.assertIn(f"removing {OLD} failed", said)
            self.assertIn(f"remove it with: systemctl --user stop agentbox-tmux-{OLD}; sudo rm -rf {self.run_base / OLD}", said)
            self.assertNotIn("chmod -R u+w", said)
            self.assertEqual(self.stub_log("STUB_DOCKER_RUN"), "", said)

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

    def unreadable_cwd(self, change=None, cgroup: str | None = None) -> None:
        """A process of this user whose working directory the walk cannot read: `cwd` is not a
        link the walk can read. Its `status` is this test's own (the same ids, capabilities and pid
        namespace as the collector), so no reason the kernel gives for hiding a cwd applies;
        CHANGE(lines) edits it into one that does. Its cgroup is an interactive shell's, or CGROUP."""
        d = self.proc / "6161"
        d.mkdir()
        (d / "cwd").write_text("", encoding="utf-8")
        (d / "cgroup").write_text((cgroup or "0::/user.slice/app.slice/bash.scope") + "\n", encoding="utf-8")
        lines = Path("/proc/self/status").read_text(encoding="utf-8").splitlines()
        (d / "status").write_text("\n".join(change(lines) if change else lines) + "\n", encoding="utf-8")

    def test_a_directory_another_user_owns_and_this_user_may_not_read_is_left_out_of_the_age(self) -> None:
        """Every box that ran holds `run-user/runc`, the state directory runc makes at container
        start, root's and mode 1700 (all 21 running boxes on sami-devbox, 2026-11-16), so the age
        walk's find could not open it in any dead box, and c2237788 kept every one as "cannot tell
        how old its files are". The age check is for files this user is still making; nothing under
        a directory another user owns and this user may not open is that. Such a directory is left
        out of the age, and the pass names it. A directory this user owns that it still may not read
        once its own mode lets it in (here find says so of one of mode 755, as for an I/O error), or
        any other find error, keeps the box as before."""
        runc = self.run_base / OLD / "run-user" / "runc"
        cases = {
            "runc's directory, root's": ({"STUB_FIND_DENY": str(runc), "STUB_FIND_ROOT_OWNED": str(runc)}, True),
            "a directory of this user's that it may not read for a reason other than its mode": (
                {"STUB_FIND_DENY": str(runc)}, False),
        }
        for name, (env, goes) in cases.items():
            with self.subTest(name):
                self.forget(OLD)
                self.leftover(OLD)
                runc.mkdir()
                age(self.run_base / OLD, 2)  # the new directory made its parent young
                result = self.collect(env)
                said = result.stdout + result.stderr
                self.assertEqual(result.returncode, 0, said)
                if goes:
                    self.assertEqual(self.gone(OLD), [], said)
                    self.assertIn(f"{OLD} removed", result.stdout)
                    self.assertIn(f"not counted in its age, since another user owns it and this user may not read it: {runc} (uid 0)", said)
                else:
                    self.assertTrue((self.boxes / OLD).exists(), said)
                    self.assertIn(f"{OLD} not removed: cannot tell how old its files are: find: '{runc}': Permission denied", said)
                    self.assertNotIn(f"{OLD} removed", said)

    def shut(self, box: str, mode: int, *, nested: bool = False, young: bool = False) -> Path:
        """What a permission-error test leaves in a box's /tmp: `tmp/.tmpn6wbon/parent/leaf`, a
        directory of mode MODE with a file in it, every path two hours old. NESTED: the file is in a
        directory of mode MODE inside it. YOUNG: the file is new. Returns the file."""
        leaf = self.boxes / box / "tmp" / ".tmpn6wbon" / "parent" / "leaf"
        inner = leaf / "inner" if nested else leaf
        inner.mkdir(parents=True)
        made = inner / "fixture.txt"
        made.write_text("x\n", encoding="utf-8")
        age(self.boxes / box, 2)
        if young:
            os.utime(made, None)
        for d in ([inner, leaf] if nested else [leaf]):
            d.chmod(mode)
        return made

    def test_a_directory_whose_own_mode_shuts_this_user_out_is_opened_and_its_age_read(self) -> None:
        """Tests leave directories that shut their own owner out by mode: a permission-error
        test's `<tmp>/.tmpXXXXXX/parent/leaf`, an ask gate test's
        `zzprobe-*/sessions/ses-1/askgate-verdicts-v*`. agentbox-6524e39d61ae on sami-devbox held 61
        such directories of mode 000 in its /tmp, every one its own user's, and the collector said
        of it "not removed: cannot tell how old its files are: find: '…/tmp/.tmpn6wbon/parent/leaf':
        Permission denied" from its session's death on 2026-11-12 until it was removed by hand: no
        box whose agent ran such a test could ever be collected. Once no process stands in a box
        whose container and launcher are gone, the collector gives this user back its own such
        directories and judges the box's age by what is in them: a young file there keeps the box.
        find names a directory of mode 000, 100 or 111 itself, and the entries of one of mode 400 or
        600, which it may list but not enter."""
        cases = {
            # name: (mode, nested, young, removed, directories opened)
            "mode 000 (a permission-error test's leaf)": (0o000, False, False, True, 1),
            "mode 111": (0o111, False, False, True, 1),
            "mode 100": (0o100, False, False, True, 1),
            "mode 400: it may list it but not enter it": (0o400, False, False, True, 1),
            "mode 600: it may list it but not enter it": (0o600, False, False, True, 1),
            "mode 000 inside mode 000": (0o000, True, False, True, 2),
            "a young file in it keeps the box": (0o000, False, True, False, 1),
        }
        for name, (mode, nested, young, removed, opened) in cases.items():
            with self.subTest(name):
                self.forget(OLD)
                self.leftover(OLD)
                made = self.shut(OLD, mode, nested=nested, young=young)
                # Root reads a directory of mode 000: as root, this runs as a scratch uid that owns the fixture.
                result = self.run_bash("cmd_collect", unprivileged=True)
                said = result.stdout + result.stderr
                self.assertEqual(result.returncode, 0, said)
                self.assertNotIn("cannot tell how old", said)
                self.assertIn(f"{OLD}: opened {opened} of its directories whose own mode shut this user out", said)
                if removed:
                    self.assertEqual(self.gone(OLD), [], said)
                    self.assertIn(f"{OLD} removed", result.stdout)
                else:
                    self.assertTrue(made.exists(), said)
                    self.assertIn(f"{OLD} not removed: {made} is younger than 60 minutes", said)

    def test_a_box_that_something_keeps_keeps_its_shut_directories_shut(self) -> None:
        """The collector opens a box's directories only once nothing else keeps the box: a box
        whose container runs or is being made, whose launcher runs, in which a process stands, or
        for which the walk of processes cannot tell, keeps its directories as its tests left them."""
        start = (Path("/proc") / str(os.getpid()) / "stat").read_text().rsplit(")", 1)[1].split()[19]

        def launcher_runs() -> None:
            d = self.proc / "4242"
            d.mkdir(parents=True, exist_ok=True)
            (d / "stat").write_text(f"4242 (agentbox) S 1 1 1 0 -1 0 0 0 0 0 0 0 0 0 20 0 1 0 {start} 0 0\n",
                                    encoding="utf-8")

        def stands_in(path: Path) -> None:
            d = self.proc / "5151"
            d.mkdir(parents=True, exist_ok=True)
            os.symlink(path, d / "cwd")
            (d / "cgroup").write_text("0::/user.slice/app.slice/bash.scope\n", encoding="utf-8")

        cases = {
            "its container runs": (lambda: (self.set_containers(OLD), self.set_container_state("running")),
                                   "its container is running"),
            "its container is being made": (lambda: (self.set_containers(OLD), self.set_container_state("created")),
                                            "its container is created"),
            "its launcher runs": (launcher_runs, "its launcher, pid 4242, runs"),
            "a process stands in its directory": (lambda: stands_in(self.boxes / OLD / "repo"),
                                                  "process 5151 has its working directory in it"),
            "a process's working directory cannot be read": (lambda: self.unreadable_cwd(),
                                                             "cannot tell whether a process stands in it"),
        }
        for name, (make, why) in cases.items():
            with self.subTest(name):
                self.forget(OLD)
                shutil.rmtree(self.proc)
                self.proc.mkdir()
                self.idle_process()
                self.set_containers()
                self.set_container_state("exited")
                self.leftover(OLD, launcher=f"launcher=4242\nlauncher_start={start}\n")
                made = self.shut(OLD, 0o000)
                make()
                result = self.run_bash("cmd_collect", unprivileged=True)
                said = result.stdout + result.stderr
                self.assertEqual(result.returncode, 0, said)
                self.assertIn(f"{OLD} not removed: {why}", said)
                self.assertEqual(stat.S_IMODE(os.lstat(made.parent).st_mode), 0o000, said)
                self.assertNotIn("opened", said)

    FOREIGN_UID = 65533

    def make_foreign(self, path: Path) -> None:
        """PATH and everything in it belong to a uid other than the collector's from now on. As
        root, unprivileged() gives them to FOREIGN_UID after it gives the rest of the fixture to the
        scratch uid; as any other user, passwordless sudo gives them, and removes them at the end.
        With neither, this user cannot make another user's directory."""
        if os.geteuid() == 0:
            self.foreign_paths.append(path)
            return
        if subprocess.run(["sudo", "-n", "true"], capture_output=True, check=False).returncode != 0:
            self.skipTest("another user's directory needs root or passwordless sudo")
        subprocess.run(["sudo", "-n", "chown", "-R", f"{self.FOREIGN_UID}:{self.FOREIGN_UID}", str(path)],
                       check=True, capture_output=True)
        self.addCleanup(subprocess.run, ["sudo", "-n", "rm", "-rf", str(path)], check=False, capture_output=True)

    def test_a_shut_directory_another_user_owns_is_not_opened(self) -> None:
        """The collector opens only this user's own directories. Another uid's directory of mode 000
        stays as it was: it is left out of the age, as runc's state directory is, and the removal
        cannot delete what is in it, so the box is never said to be removed and the line names what
        is left."""
        self.leftover(OLD)
        made = self.shut(OLD, 0o000)
        self.make_foreign(made.parent)
        result = self.run_bash("cmd_collect", unprivileged=True)
        said = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, said)
        here = os.lstat(made.parent)
        self.assertEqual((stat.S_IMODE(here.st_mode), here.st_uid), (0o000, self.FOREIGN_UID), said)
        self.assertNotIn("opened", said)
        self.assertIn(f"not counted in its age, since another user owns it and this user may not read it: "
                      f"{made.parent} (uid {self.FOREIGN_UID})", said)
        self.assertIn(f"removing {OLD} failed", said)
        self.assertNotIn(f"{OLD} removed", said)
        self.assertTrue((self.boxes / OLD).exists(), said)

    def test_beside_its_own_shut_directory_another_user_s_stays_shut(self) -> None:
        """The walk that opens a box's own shut directories passes over another uid's directory of
        mode 000 in the same box: the box's own directory is opened, the other user's stays as it
        was and is left out of the age, and the removal cannot delete what is in it, so the box is
        never said to be removed."""
        foreign = self.boxes / OLD / "tmp" / "zzprobe-1" / "sessions" / "ses-1" / "askgate-verdicts-v1"
        self.leftover(OLD)
        foreign.mkdir(parents=True)
        (foreign / "verdict.json").write_text("{}\n", encoding="utf-8")
        made = self.shut(OLD, 0o000)  # ages the whole box, the other user's directory included
        foreign.chmod(0o000)
        self.make_foreign(foreign)
        result = self.run_bash("cmd_collect", unprivileged=True)
        said = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, said)
        here = os.lstat(foreign)
        self.assertEqual((stat.S_IMODE(here.st_mode), here.st_uid), (0o000, self.FOREIGN_UID), said)
        self.assertIn(f"{OLD}: opened 1 of its directories whose own mode shut this user out", said)
        self.assertIn(f"not counted in its age, since another user owns it and this user may not read it: "
                      f"{foreign} (uid {self.FOREIGN_UID})", said)
        self.assertNotIn("cannot tell how old", said)
        self.assertFalse(made.exists(), said)
        self.assertIn(f"removing {OLD} failed", said)
        self.assertNotIn(f"{OLD} removed", said)
        self.assertTrue((self.boxes / OLD).exists(), said)

    def test_only_a_container_s_process_or_the_user_manager_is_left_uncounted(self) -> None:
        """The kernel refuses a process's cwd to a reader without CAP_SYS_PTRACE when its ids are
        not all the reader's, when it holds a capability the reader lacks, or when it is in a pid
        namespace below the reader's. Two such processes of this user are always on sami-devbox: its
        systemd manager (CAP_WAKE_ALARM) and a kind cluster's metrics-server (gid 0, a nested pid
        namespace), and counting them kept all 59 leftovers on 2026-11-16. A process in a nested pid
        namespace is a container's, and the manager is known by its own cgroup: those two are not
        counted, and the pass names them. Any other hidden process of this user can stand in a box's
        directory (a `newgrp docker` shell has other gids, a program with file capabilities holds
        capabilities the user lacks), so it keeps every box that pass (review of 2d9f544f,
        dispatch://INFRA-1720/comment/19550300)."""
        def field(name, edit):
            return lambda lines: [edit(line) if line.startswith(name + ":") else line for line in lines]

        def both(*edits):
            def change(lines):
                for edit in edits:
                    lines = edit(lines)
                return lines
            return change

        uid = os.getuid()
        manager = f"0::/user.slice/user-{uid}.slice/user@{uid}.service/init.scope"
        nested = field("NSpid", lambda line: line + "\t1")
        other_gids = field("Gid", lambda _: "Gid:\t0\t0\t0\t0")
        capability = field("CapPrm", lambda _: "CapPrm:\t0000000800000000")
        cases = {
            # name: (status change, cgroup, env, removed, the words the pass says)
            "a nested pid namespace": (nested, None, {}, True, "process 6161 (it is in another pid namespace)"),
            "a nested pid namespace with gid 0 (kind's metrics-server)": (
                both(nested, other_gids), None, {}, True, "process 6161 (it is in another pid namespace)"),
            "the user's systemd manager (CAP_WAKE_ALARM)": (
                capability, manager, {}, True, "process 6161 (it is this user's systemd manager)"),
            "a shell with other gids (newgrp docker)": (
                other_gids, None, {}, False, "cannot tell whether a process stands in it: process 6161"),
            "a program with file capabilities": (
                capability, None, {}, False, "cannot tell whether a process stands in it: process 6161"),
            "other gids in the manager's slice but not its init.scope": (
                other_gids, f"0::/user.slice/user-{uid}.slice/user@{uid}.service/app.slice/x.scope", {}, False,
                "cannot tell whether a process stands in it: process 6161"),
            # A stated limit, not a rule: a process that is not dumpable has a root-owned /proc entry, so
            # the walk takes it for another user's (forward and gpg-agent are such, always running).
            "not dumpable": (None, None, {"STUB_FIND_ROOT_OWNED": str(self.proc / "6161")}, True, None),
        }
        for name, (change, cgroup, env, removed, said_words) in cases.items():
            with self.subTest(name):
                self.forget(OLD)
                shutil.rmtree(self.proc / "6161", ignore_errors=True)
                self.idle_process()
                self.leftover(OLD)
                self.unreadable_cwd(change, cgroup)
                result = self.collect(env)
                said = result.stdout + result.stderr
                self.assertEqual(result.returncode, 0, said)
                if removed:
                    self.assertEqual(self.gone(OLD), [], said)
                    self.assertIn(f"{OLD} removed", result.stdout)
                else:
                    self.assertTrue((self.boxes / OLD).exists(), said)
                    self.assertNotIn(f"{OLD} removed", said)
                if said_words:
                    self.assertIn(said_words, said)

    def test_a_process_with_a_file_or_its_program_in_the_box_keeps_it(self) -> None:
        """A process whose working directory is elsewhere still holds the box when a file it holds
        open, or the program it runs, is under the box's directory or run dir (`tail -f` of a box's
        log, a tool built in the box's clone and run from $HOME): the walk reads `fd` and `exe` too."""
        uid = os.getuid()
        cases = {
            "an open file": ("fd/3", self.boxes / OLD / "repo" / "build.log", "holds a file in it open"),
            "an open file since deleted": ("fd/4", f"{self.boxes / OLD / 'repo' / 'gone.log'} (deleted)",
                                           "holds a file in it open"),
            # The kernel names an open directory that is gone with " (deleted)" after its path. The box's
            # own directory, or its run dir, held open that way still holds the box. A file under the
            # box does not pin the strip (`<box>/x (deleted)` is under `<box>` either way); these do.
            "the box's directory, held open since deleted": ("fd/6", f"{self.boxes / OLD} (deleted)",
                                                             "holds a file in it open"),
            "its run dir, held open since deleted": ("fd/7", f"{self.run_base / OLD} (deleted)",
                                                     "holds a file in it open"),
            "a file of its run dir": ("fd/5", self.run_base / OLD / "run-user" / "agentbox-session",
                                      "holds a file in it open"),
            "its program": ("exe", self.boxes / OLD / "repo" / "target" / "tool", "runs a program from it"),
        }
        for name, (link, target, words) in cases.items():
            with self.subTest(name):
                self.forget(OLD)
                shutil.rmtree(self.proc / "5151", ignore_errors=True)
                self.idle_process()
                self.leftover(OLD)
                d = self.proc / "5151"
                (d / "fd").mkdir(parents=True)
                os.symlink("/", d / "cwd")
                os.symlink(str(target), d / link)
                (d / "cgroup").write_text(f"0::/user.slice/user-{uid}.slice/session-1.scope\n", encoding="utf-8")
                result = self.collect()
                said = result.stdout + result.stderr
                self.assertTrue((self.boxes / OLD).exists(), said)
                self.assertIn(f"{OLD} not removed: process 5151 {words}", said)
        with self.subTest("the control: the same process with nothing in the box"):
            self.forget(OLD)
            shutil.rmtree(self.proc / "5151", ignore_errors=True)
            self.leftover(OLD)
            d = self.proc / "5151"
            (d / "fd").mkdir(parents=True)
            os.symlink("/", d / "cwd")
            os.symlink("/usr/bin/tail", d / "exe")
            os.symlink("/var/log/syslog", d / "fd" / "3")
            result = self.collect()
            self.assertEqual(self.gone(OLD), [], result.stdout + result.stderr)

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

    def test_a_collector_s_detached_waiter_does_not_hold_the_collector_lock(self) -> None:
        """The collector holds its lock on fd 8, and the unenroll waiter it starts for a box's
        session (detached, up to AGENTBOX_UNENROLL_GRACE, 180 s) inherited that fd, so every
        collector started while it waited ended "another collector runs" (Proof1720 at c2237788,
        18:39:21Z and 18:39:49Z, waiter pid 2107221). A second collector during a live waiter runs."""
        self.leftover(OLD)
        self.enrolled(OLD, SID)
        age(self.run_base / OLD, 2)
        first = self.collect({"AGENTBOX_UNENROLL_GRACE": "60"})
        self.addCleanup(self.end_waiters)
        self.assertEqual(self.gone(OLD), [], first.stdout + first.stderr)
        waiters = self.waiters()
        self.assertTrue(waiters, "the first collector started no unenroll waiter")
        second_box = "agentbox-0000000000c2"
        self.leftover(second_box)
        second = self.collect()
        self.assertTrue(self.waiters(), "the waiter must still be waiting for the test to mean anything")
        self.assertNotIn("another collector runs", second.stdout, second.stdout)
        self.assertEqual(self.gone(second_box), [], second.stdout + second.stderr)

    def test_a_detached_learn_child_does_not_hold_the_collector_lock(self) -> None:
        """unenroll_box starts a second kind of detached child besides the session waiter: the one
        that learns and revokes the box's own open enroll attempts (ENROLL_OPEN, kind `own`). Run with
        fd 8 holding the collector's lock, as cmd_collect holds it, and with a learn call that waits,
        that child must not hold the lock once its parent lets go: a collector started while it waits
        runs and removes its leftover."""
        write_stub(self.stubs, "agent-secrets",
                   'printf "%s\\n" "$*" >>"$STUB_SECRETS"; case "$1" in enroll) sleep 60 ;; esac; exit 1')
        lock_dir = self.root / "collect-logs"
        lock_dir.mkdir()
        started = self.run_bash(
            f'exec 8>>"{lock_dir / "collect.lock"}"; flock -n 8 || exit 9\n'
            f'ENROLL_OPEN=("own runtime-{OLD} SHA256:own-thumbprint")\n'
            f'unenroll_box {OLD}')
        self.addCleanup(self.end_waiters)
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        for _ in range(100):
            if "enroll --helper" in self.stub_log("STUB_SECRETS"):
                break
            time.sleep(0.05)
        self.assertIn("enroll --helper", self.stub_log("STUB_SECRETS"), "the learn child never made its call")
        self.assertTrue(self.waiters(), "the learn child must still be waiting for the test to mean anything")
        self.leftover(OLD)
        result = self.collect()
        self.assertTrue(self.waiters(), "the learn child must still be waiting for the test to mean anything")
        self.assertNotIn("another collector runs", result.stdout, result.stdout)
        self.assertEqual(self.gone(OLD), [], result.stdout + result.stderr)

    def waiters(self) -> list[int]:
        """The pids of this test's unenroll waiters: `agentbox-unenroll` processes naming its run base."""
        found = []
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                argv = (entry / "cmdline").read_bytes().split(b"\0")
            except OSError:
                continue
            if b"agentbox-unenroll" in argv and str(self.run_base).encode() in argv:
                found.append(int(entry.name))
        return found

    def end_waiters(self) -> None:
        for pid in self.waiters():
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

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
