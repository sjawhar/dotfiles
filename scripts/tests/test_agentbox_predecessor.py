#!/usr/bin/env python3
"""Every box's record, and the predecessor a resume leaves behind (scripts/agentbox).

A session that is resumed gets a NEW box every time, and the old box's image stays on disk
whenever its launcher never got to run close_box: the machine went down under it, its pane was
killed. At 20:05Z on 2026-11-04 this host held 103 such images, 416.2 GiB, each belonging to a
session alive in a new box, and nothing could say whose they were. So two things, both here:

  * `box_record_write` stamps `<boxes>/../disks/<box>.info` at creation with the box, the owning
    session, the creation time and the launch command, and the record dies with the image;
  * `cmd_reap`, started detached by `agentbox new` once the box's omp names its session, waits
    for that session to REGISTER on Envoy (`box_registered`, the test the wake uses) and then
    removes the session's older boxes on this host - keeping any one that still has a container,
    holds work no remote has, or cannot be read, with one line saying which test it was.

And `unenroll_box`'s waiter, which a predecessor's close starts: the enrollment is the SESSION's,
so it must survive while any live box holds that session (20:17Z on 2026-11-04, a duplicate box's
close took the original box's broker identity).

Technique, as test_agentbox_move.py: source `scripts/agentbox` (its source guard runs nothing)
with every host tool stubbed first on PATH, and point AGENTBOX_BOXES, AGENTBOX_DISKS,
AGENTBOX_RUN_BASE and AGENTBOX_PROC at a fixture tree.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
AGENTBOX = DOTFILES / "scripts" / "agentbox"

SID = "01a18385-f8c8-74a9-b6ff-694fb5fe87a7"
OTHER_SID = "01a10000-0000-0000-0000-000000000000"


def write_stub(directory: Path, name: str, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class AgentboxPredecessor(unittest.TestCase):
    TIMEOUT = float(os.environ.get("AGENTBOX_TEST_TIMEOUT", "900"))

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.home = self.root / "home"
        self.boxes = self.root / "boxes"
        self.disks = self.root / "disks"
        self.run_base = self.root / "run"
        self.proc = self.root / "proc"
        for path in (self.home, self.boxes, self.disks, self.run_base, self.proc):
            path.mkdir(parents=True)
        self.stubs = self.root / "bin"
        self.stubs.mkdir()
        # Nothing here reaches the host. docker answers the two forms the code uses: the census's
        # `ps --no-trunc --format` listing and reap_box's `ps -a --filter name=^<box>$`.
        write_stub(self.stubs, "docker", r'''
want=""
for a in "$@"; do case "$a" in name=^*) want="${a#name=^}"; want="${want%$}" ;; esac; done
if [[ -n "$want" ]]; then
    grep -qxF "$want" "$STUB_CONTAINERS" 2>/dev/null && echo "$want"
    exit 0
fi
n=0
while read -r name; do n=$((n + 1)); printf '%012d %s\n' "$n" "$name"; done <"$STUB_CONTAINERS"
exit 0''')
        write_stub(self.stubs, "pgrep", 'cat "$STUB_PGREP" 2>/dev/null; exit 0')
        write_stub(self.stubs, "tmux", 'exit 0')
        write_stub(self.stubs, "envoy", r'''
case "${1:-}" in
    ps) cat "$STUB_ENVOY_PS" 2>/dev/null ;;
    *) printf '%s\n' "$@" >>"$STUB_ENVOY" ;;
esac
exit 0''')
        write_stub(self.stubs, "agent-secrets", 'printf "%s\\n" "$*" >>"$STUB_SECRETS"; exit 0')
        # The one step of a removal that needs root is the loop mount of an image that is not
        # mounted; no test may run it for real, so sudo refuses unless a test says otherwise.
        write_stub(self.stubs, "sudo", 'printf "%s\\n" "$*" >>"$STUB_SUDO"; exit "${STUB_SUDO_RC:-1}"')
        for name in ("STUB_CONTAINERS", "STUB_PGREP", "STUB_ENVOY", "STUB_ENVOY_PS",
                     "STUB_SECRETS", "STUB_SUDO"):
            (self.root / name).write_text("", encoding="utf-8")
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
            "AGENTBOX_RUN_BASE": str(self.run_base),
            "AGENTBOX_PROC": str(self.proc),
            "AGENTBOX_DISK": "0",
            "AGENTBOX_CACHE_ROOT": str(self.root / "box-cache"),
            "AGENTBOX_BOX_HOMES": str(self.root / "box-homes"),
            "AGENTBOX_REAP_LOG_DIR": str(self.root / "reap-logs"),
            "AGENTBOX_REAP_WAIT_STEP": "1",
            "STUB_CONTAINERS": str(self.root / "STUB_CONTAINERS"),
            "STUB_PGREP": str(self.root / "STUB_PGREP"),
            "STUB_ENVOY": str(self.root / "STUB_ENVOY"),
            "STUB_ENVOY_PS": str(self.root / "STUB_ENVOY_PS"),
            "STUB_SECRETS": str(self.root / "STUB_SECRETS"),
            "STUB_SUDO": str(self.root / "STUB_SUDO"),
        }

    # ── fixtures ────────────────────────────────────────────────────────────────────────────
    def run_bash(self, script: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "-c", f'source "{AGENTBOX}"\n{script}'],
            capture_output=True, text=True, check=False, timeout=self.TIMEOUT,
            env={**self.env, **(extra_env or {})}, start_new_session=True,
        )

    def set_containers(self, *names: str) -> None:
        (self.root / "STUB_CONTAINERS").write_text("".join(f"{n}\n" for n in names), encoding="utf-8")

    def register(self, session: str, when_ms: int | None = None) -> None:
        """What `envoy ps --json` answers: this session registered at WHEN (now by default)."""
        updated = int(time.time() * 1000) if when_ms is None else when_ms
        (self.root / "STUB_ENVOY_PS").write_text(
            '{"sessions": [{"session_id": "%s", "updated_at": %d}]}\n' % (session, updated),
            encoding="utf-8")

    def make_omp(self, pid: int, session: str) -> None:
        """An omp of SESSION running on this machine, for move_wake_landed's census."""
        directory = self.proc / str(pid)
        (directory / "fd").mkdir(parents=True)
        (directory / "fdinfo").mkdir(parents=True)
        (directory / "stat").write_text(f"{pid} (omp) S 1 1 0 0 -1 0\n", encoding="utf-8")
        (directory / "comm").write_text("omp\n", encoding="utf-8")
        (directory / "cmdline").write_text("omp\0", encoding="utf-8")
        (directory / "cgroup").write_text("0::/user.slice/omp.scope\n", encoding="utf-8")
        (directory / "environ").write_text("", encoding="utf-8")
        os.symlink(str(self.home), directory / "cwd")
        transcript = self.home / ".omp/agent/sessions/-work" / f"2026-11-04T09:00:00Z_{session}.jsonl"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text("", encoding="utf-8")
        os.symlink(str(transcript), directory / "fd" / "21")
        (directory / "fdinfo" / "21").write_text("pos:\t0\nflags:\t0100002\n", encoding="utf-8")
        (self.root / "STUB_PGREP").write_text(f"{pid}\n", encoding="utf-8")

    def git(self, *args: str, cwd: Path) -> None:
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                       env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"})

    def make_box(self, box: str, session: str, created: str = "2026-11-04T10:00:00Z",
                 image: bool = False) -> Path:
        """A box on disk as a launcher leaves one: its directory, its record, its home."""
        directory = self.boxes / box
        (directory / "home").mkdir(parents=True)
        (directory / "home" / "note.md").write_text("a note the session wrote\n", encoding="utf-8")
        (self.disks / f"{box}.info").write_text(
            f"box={box}\nsession={session}\ncreated={created}\ncommand=omp --resume x\n", encoding="utf-8")
        if image:
            (self.disks / f"{box}.img").write_bytes(b"\0" * 4096)
        return directory

    def clone_with_unpushed_work(self, directory: Path) -> Path:
        """A clone in a box holding a commit no remote has, which keeps the whole box."""
        clone = directory / "repo"
        clone.mkdir()
        self.git("init", "-q", "-b", "main", cwd=clone)
        self.git("config", "user.email", "t@example.com", cwd=clone)
        self.git("config", "user.name", "T", cwd=clone)
        (clone / "file").write_text("x\n", encoding="utf-8")
        self.git("add", "file", cwd=clone)
        self.git("commit", "-qm", "work no remote has", cwd=clone)
        return clone

    # ── the record ──────────────────────────────────────────────────────────────────────────
    def test_a_box_is_stamped_with_its_session_name_creation_time_and_command(self) -> None:
        box = "agentbox-0123456789ab"
        result = self.run_bash(f'box_record_write {box} "" omp --resume "/a path/_{SID}.jsonl"')
        self.assertEqual(result.returncode, 0, result.stderr)
        record = self.disks / f"{box}.info"
        fields = dict(line.split("=", 1) for line in record.read_text(encoding="utf-8").splitlines())
        self.assertEqual(fields["box"], box)
        self.assertEqual(fields["session"], "", "a box that starts a NEW session has no id yet")
        self.assertRegex(fields["created"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertIn("--resume", fields["command"])
        self.assertIn("_%s.jsonl" % SID, fields["command"])
        self.assertEqual(len(record.read_text(encoding="utf-8").splitlines()), 4,
                         "a command with a space or a newline in it stays on one line")
        # The id the box's omp reveals (box_session_id) lands in the record, and only the first:
        # a box runs one session.
        self.assertEqual(self.run_bash(f"box_record_session {box} {SID}").returncode, 0)
        self.assertEqual(self.run_bash(f"box_record_session {box} {OTHER_SID}").returncode, 0)
        got = self.run_bash(f"box_record_get {box} session")
        self.assertEqual(got.stdout.strip(), SID)
        self.assertEqual(self.run_bash(f"box_record_get {box} box").stdout.strip(), box)

    def test_the_record_goes_with_the_image_it_describes(self) -> None:
        box = "agentbox-0123456789ab"
        self.make_box(box, SID, image=True)
        result = self.run_bash(f"close_box_disk {box}")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.disks / f"{box}.img").exists())
        self.assertFalse((self.disks / f"{box}.info").exists(),
                         "a record left on disk must mean an image left on disk")

    def test_a_box_with_no_image_gets_no_record(self) -> None:
        """`--disk 0`: `open_box_disk` deliberately leaves the images directory absent, and such
        a box has no image to orphan, so the record is skipped rather than made — making that
        directory is what `scripts/tests/test_agentbox_limits.py` fails on."""
        shutil.rmtree(self.disks)
        result = self.run_bash('box_record_write agentbox-nodisk000001 "" omp')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.disks.exists(), "a box with no image makes no images directory")

    # ── the predecessor ─────────────────────────────────────────────────────────────────────
    def test_a_predecessor_is_removed_once_the_resumed_session_has_registered(self) -> None:
        old, new = "agentbox-old000000001", "agentbox-new000000002"
        self.make_box(old, SID)
        self.make_box(new, SID, created="2026-11-04T20:00:00Z")
        self.register(SID)
        self.make_omp(999001, SID)
        result = self.run_bash(f"cmd_reap {SID} {new} 0")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.boxes / old).exists(), result.stdout)
        self.assertFalse((self.disks / f"{old}.info").exists())
        self.assertIn(f"{old} removed", result.stdout)
        self.assertTrue((self.boxes / new).exists(), "the box that is running is never touched")
        self.assertTrue((self.disks / f"{new}.info").exists())
        # What the session wrote in the old box's home is kept for it, as a close keeps it.
        carried = list((self.root / "box-homes" / SID).glob("*/note.md"))
        self.assertEqual(len(carried), 1, "the old box's home is carried out before it goes")

    def test_a_box_that_never_registers_removes_nothing(self) -> None:
        old, new = "agentbox-old000000001", "agentbox-new000000002"
        self.make_box(old, SID)
        self.make_box(new, SID, created="2026-11-04T20:00:00Z")
        # Envoy knows nothing of this session: the new box never came up.
        result = self.run_bash(f"cmd_reap {SID} {new} 0", {"AGENTBOX_REAP_WAIT_TOTAL": "1"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("did not register on Envoy", result.stdout)
        self.assertTrue((self.boxes / old).exists())
        self.assertTrue((self.disks / f"{old}.info").exists())
        # Registered somewhere, but no omp of it runs HERE (a rolled-back copy on another
        # machine): still nothing is removed.
        self.register(SID)
        again = self.run_bash(f"cmd_reap {SID} {new} 0", {"AGENTBOX_REAP_WAIT_TOTAL": "1"})
        self.assertEqual(again.returncode, 1)
        self.assertTrue((self.boxes / old).exists())

    def test_a_predecessor_whose_container_still_exists_is_kept(self) -> None:
        old, new = "agentbox-old000000001", "agentbox-new000000002"
        self.make_box(old, SID)
        self.make_box(new, SID, created="2026-11-04T20:00:00Z")
        self.set_containers(old, new)
        self.register(SID)
        self.make_omp(999001, SID)
        result = self.run_bash(f"cmd_reap {SID} {new} 0")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(f"{old} kept: its container still exists", result.stdout)
        self.assertTrue((self.boxes / old).exists())
        self.assertTrue((self.disks / f"{old}.info").exists())

    def test_a_predecessor_holding_work_no_remote_has_is_kept(self) -> None:
        old, new = "agentbox-old000000001", "agentbox-new000000002"
        directory = self.make_box(old, SID)
        self.clone_with_unpushed_work(directory)
        self.make_box(new, SID, created="2026-11-04T20:00:00Z")
        self.register(SID)
        self.make_omp(999001, SID)
        result = self.run_bash(f"cmd_reap {SID} {new} 0")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(f"{old} kept: it holds work no remote has", result.stdout)
        self.assertIn("main", result.stdout)
        self.assertTrue((directory / "repo" / ".git").is_dir())

    def test_a_predecessor_whose_image_cannot_be_read_is_kept(self) -> None:
        """An image that is not mounted is mounted to be read; a mount that fails keeps the box,
        because its clones were never checked."""
        old, new = "agentbox-old000000001", "agentbox-new000000002"
        self.make_box(old, SID, image=True)
        self.make_box(new, SID, created="2026-11-04T20:00:00Z")
        self.register(SID)
        self.make_omp(999001, SID)
        result = self.run_bash(f"cmd_reap {SID} {new} 0")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("could not be mounted", result.stdout)
        self.assertTrue((self.disks / f"{old}.img").exists())
        self.assertIn("mount -o loop,noatime", (self.root / "STUB_SUDO").read_text(encoding="utf-8"))

    def test_only_this_sessions_own_older_boxes_are_touched(self) -> None:
        mine, theirs, new = "agentbox-mine00000001", "agentbox-theirs000002", "agentbox-new000000003"
        nameless = "agentbox-nameless0004"
        self.make_box(mine, SID)
        self.make_box(theirs, OTHER_SID)
        self.make_box(new, SID, created="2026-11-04T20:00:00Z")
        # A box from before records existed: it names no session and nothing may remove it.
        (self.boxes / nameless).mkdir()
        self.register(SID)
        self.make_omp(999001, SID)
        result = self.run_bash(f"cmd_reap {SID} {new} 0")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertFalse((self.boxes / mine).exists())
        self.assertTrue((self.boxes / theirs).exists())
        self.assertTrue((self.disks / f"{theirs}.info").exists())
        self.assertTrue((self.boxes / nameless).exists())

    def test_the_launcher_starts_a_reaper_only_for_a_session_that_has_an_older_box(self) -> None:
        """reap_start detaches the reaper and drops TMUX_PANE (close_box's attention step must
        never run for the live pane from there), and it starts nothing at all for a session
        whose box is its first: a reaper per box would poll for two hours for nothing."""
        calls = self.root / "REAP_CALLS"
        fake = self.root / "fake-dotfiles"
        (fake / "scripts").mkdir(parents=True)
        write_stub(fake / "scripts", "agentbox",
                   'printf "%s pane=[%s]\\n" "$*" "${TMUX_PANE:-}" >>"$REAP_CALLS"; exit 0')
        # After the source, not in the environment: the script reads real files of DOTFILES_DIR
        # while it loads (agent-secrets/broker-url.sh).
        env = {"REAP_CALLS": str(calls), "TMUX_PANE": "%42"}
        start = f'DOTFILES_DIR="{fake}"\nreap_start agentbox-new000000002 {SID} 1762286400000\nwait'
        self.make_box("agentbox-new000000002", SID, created="2026-11-04T20:00:00Z")
        first = self.run_bash(start, env)
        self.assertEqual(first.returncode, 0, first.stderr)
        time.sleep(1)
        self.assertFalse(calls.exists() and calls.read_text(encoding="utf-8").strip(),
                         "a session's first box has nothing to collect")
        self.make_box("agentbox-old000000001", SID)
        result = self.run_bash(start, env)
        self.assertEqual(result.returncode, 0, result.stderr)
        for _ in range(100):
            if calls.exists() and calls.read_text(encoding="utf-8"):
                break
            time.sleep(0.1)
        line = calls.read_text(encoding="utf-8").strip()
        self.assertEqual(line, f"reap-previous {SID} agentbox-new000000002 1762286400000 pane=[]")

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

    def test_a_predecessors_close_leaves_a_live_boxs_enrollment_alone(self) -> None:
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
        self.assertNotIn("unenroll", (self.root / "STUB_SECRETS").read_text(encoding="utf-8"),
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
        self.assertNotIn("unenroll", (self.root / "STUB_SECRETS").read_text(encoding="utf-8"))

    def test_a_session_with_no_box_left_does_end_its_enrollment(self) -> None:
        gone = "agentbox-gone00000001"
        self.enrolled(gone, SID, owner=True)
        self.set_containers()
        result = self.run_bash(f"unenroll_box {gone}\nwait", {"AGENTBOX_UNENROLL_GRACE": "0"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("did not come back", self.unenroll_log())
        self.assertIn(f"unenroll --helper --enrollment enr-{gone}",
                      (self.root / "STUB_SECRETS").read_text(encoding="utf-8"))
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
        # Its own box never counts, and neither does another session's.
        self.set_containers(mine)
        self.assertEqual(self.run_bash(f"session_live_box {SID} {mine}").returncode, 1)
        self.assertEqual(self.run_bash(f"session_live_box {OTHER_SID} {mine}").returncode, 1)


if __name__ == "__main__":
    unittest.main()
