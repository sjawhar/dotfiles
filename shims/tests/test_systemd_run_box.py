#!/usr/bin/env python3
"""shims/systemd-run caps a run inside an agent box, where the real one cannot.

A box reaches the host's user manager through the session-bus relay, and that manager cannot
adopt a process from the box's pid namespace, so every `systemd-run --user --scope -p
MemoryMax=…` in a box died with "Failed to start transient scope unit: Connection reset by
peer" and the capped run never happened (2026-11-04, INFRA-602's skills, omp's gate, REARM).

Technique: the shim is driven against a cgroup TREE IN A TEMP DIR (AGENTBOX_CGROUP_ROOT), which
holds the same files a delegated cgroup v2 root holds, so each case reads what the shim wrote
and what it removed on any machine, with no root and no real cgroups. `sudo` is a stub that
runs what it is given, since a writable tree needs none. A stub `systemd-run` after the shims
dir on PATH records the calls that must pass through. The cases at the end, skipped unless sudo
without a password reaches /sys/fs/cgroup (an agent box, or a host that grants it), run the whole
thing in a real cgroup of their own there and check the kernel enforces the cap it wrote.
"""

from __future__ import annotations

import os
import select
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

SHIM = Path(__file__).resolve().parents[1] / "systemd-run"
CGROUPS = Path("/sys/fs/cgroup")
TIMEOUT = 60
# Every file a delegated cgroup v2 directory offers that this shim writes.
CGROUP_FILES = {
    "memory.max": "max",
    "memory.high": "max",
    "memory.min": "0",
    "memory.low": "0",
    "memory.swap.max": "max",
    "memory.oom.group": "0",
    "memory.events": "oom_kill 0",
    "cpu.max": "max 100000",
    "cpu.weight": "100",
    "pids.max": "max",
    "io.weight": "default 100",
    "cgroup.procs": "",
}


def write_stub(directory: Path, name: str, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


# A hook on the first run's PATH prints this line and then waits for one line on its stdin.
HOOK_HOLDS = ["echo made", "read -r _"]


def race_a_sweep(case: unittest.TestCase, env: dict, second, scopes) -> None:
    """Two capped runs at once, the first held by its hook just after its cgroup exists and before
    its command is moved in, so that cgroup is empty; `second` runs whole meanwhile, its sweep
    included. Then the first goes on, and must finish: its cgroup was not swept from under it."""
    first = subprocess.Popen(
        [str(SHIM), "--user", "--scope", "-q", "-p", "MemoryMax=64M", "--", "echo", "first ran"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    case.addCleanup(first.kill)
    ready, _, _ = select.select([first.stdout], [], [], TIMEOUT)
    case.assertTrue(ready, "the first run never made its cgroup")
    case.assertEqual(first.stdout.readline(), "made\n")
    held = scopes()
    case.assertEqual(len(held), 1, held)
    ran = second()
    case.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
    kept = held[0].is_dir()
    out, err = first.communicate("go\n", timeout=TIMEOUT)
    case.assertEqual(first.returncode, 0, f"the first run did not finish: {out}{err}")
    case.assertIn("first ran", out)
    case.assertTrue(kept, "the second run swept the cgroup of a run still going")
    case.assertEqual(scopes(), [], "the first run's cgroup was left behind")


class FakeTree(unittest.TestCase):
    """The shim against a cgroup tree this test owns."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cgroup = self.root / "cgroup"
        self.cgroup.mkdir()
        (self.cgroup / "cgroup.subtree_control").write_text("", encoding="utf-8")
        (self.cgroup / "cgroup.procs").write_text("", encoding="utf-8")
        self.stubs = self.root / "stubs"
        self.stubs.mkdir()
        # A mkdir in this tree makes the files a real delegated cgroup directory would have, so
        # the shim's writes land where they would on a box.
        write_stub(self.stubs, "sudo", "\n".join([
            '[ "${1-}" = -n ] && shift',
            'exec "$@"',
        ]))
        self.mkdir_hook = write_stub(self.stubs, "mkdir", "\n".join([
            'dir="${!#}"',
            '/bin/mkdir "$@" || exit $?',
            f'for f in {" ".join(CGROUP_FILES)}; do',
            '    [ -e "$dir/$f" ] || printf "%s" "$(default_of "$f")" > "$dir/$f"',
            "done",
        ]).replace('"$(default_of "$f")"', '"${DEFAULTS[$f]-}"'))
        # bash associative array of the defaults, written into the stub itself.
        body = self.mkdir_hook.read_text(encoding="utf-8").splitlines()
        body.insert(1, "declare -A DEFAULTS=(" + " ".join(
            f'[{name}]="{value}"' for name, value in CGROUP_FILES.items()) + ")")
        self.mkdir_hook.write_text("\n".join(body) + "\n", encoding="utf-8")
        self.real_calls = self.root / "REAL_SYSTEMD_RUN"
        self.after = self.root / "after"
        write_stub(self.after, "systemd-run", f'printf "%s\\n" "$*" >>"{self.real_calls}"')
        # Two differences between a directory of files and a cgroup: cgroupfs removes a cgroup
        # with `rmdir` although its control files are "in" it, and it REFUSES (EBUSY) while a
        # process is in it. The stub closes both, reading cgroup.procs as the kernel does: a pid
        # that no longer runs is in no cgroup.
        write_stub(self.stubs, "rmdir", "\n".join([
            'dir="${!#}"',
            'while read -r pid; do',
            '    [ -n "$pid" ] || continue',
            '    if kill -0 "$pid" 2>/dev/null; then echo "rmdir: $dir: Device or resource busy" >&2; exit 1; fi',
            'done < "$dir/cgroup.procs" 2>/dev/null',
            f'for f in {" ".join(CGROUP_FILES)}; do /bin/rm -f "$dir/$f"; done',
            'exec /bin/rmdir "$@"',
        ]))

    def environ(self, env: dict | None = None) -> dict:
        environ = {
            **os.environ,
            "AGENTBOX_BOX": "agentbox-0123456789ab",
            "AGENTBOX_CGROUP_ROOT": str(self.cgroup),
            "AGENTBOX_IDENTITY_FILE": str(self.root / "no-identity"),
            "PATH": f"{self.stubs}:{SHIM.parent}:{self.after}:{os.environ.get('PATH', '')}",
        }
        environ.update(env or {})
        return environ

    def run_shim(self, *argv: str, env: dict | None = None, input_text: str = "") -> subprocess.CompletedProcess:
        return subprocess.run([str(SHIM), *argv], capture_output=True, text=True,
                              env=self.environ(env), input=input_text, timeout=TIMEOUT, cwd=self.root)

    def scopes(self) -> list[Path]:
        return [p for p in self.cgroup.iterdir() if p.is_dir()]

    # ── the capped run ──────────────────────────────────────────────────────────────────────
    def test_a_capped_run_happens_and_its_limits_are_written_into_a_cgroup(self) -> None:
        seen = self.root / "SEEN"
        run = self.run_shim(
            "--user", "--scope", "--unit=lane-build",
            "-p", "MemoryMax=24G", "-p", "MemorySwapMax=0", "-p", "CPUQuota=800%",
            "-p", "TasksMax=4096",
            "--", "bash", "-c", f'cat /proc/self/cgroup >"{seen}"; echo ran')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("ran", run.stdout)
        self.assertTrue(seen.is_file(), "the command did not run")
        # The limits, as the kernel takes them: the written values are read back from the
        # directory the run was given, which the shim removes at the end. A copy is kept by the
        # command itself below; here the proof is that the directory existed with them in it.
        self.assertEqual(self.scopes(), [], "the cgroup was left behind")
        written = run.stderr
        self.assertIn("lane-build.scope", written, written)

    def test_the_limits_land_in_the_cgroup_files_the_kernel_reads(self) -> None:
        """Read from INSIDE the run, since the shim removes the directory when it ends."""
        out = self.root / "LIMITS"
        reader = (
            f'd="{self.cgroup}"/lane.scope; '
            f'{{ echo "memory.max=$(cat $d/memory.max)"; echo "swap=$(cat $d/memory.swap.max)"; '
            f'echo "cpu=$(cat $d/cpu.max)"; echo "pids=$(cat $d/pids.max)"; '
            f'echo "high=$(cat $d/memory.high)"; echo "procs=$(cat $d/cgroup.procs)"; }} >"{out}"'
        )
        run = self.run_shim("--user", "--scope", "--unit", "lane",
                            "-p", "MemoryMax=24G", "-p", "MemorySwapMax=0", "-p", "CPUQuota=800%",
                            "-p", "TasksMax=4096", "-p", "MemoryHigh=512M",
                            "--", "bash", "-c", reader)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        values = dict(line.split("=", 1) for line in out.read_text(encoding="utf-8").splitlines())
        self.assertEqual(values["memory.max"], str(24 * 1024 ** 3))
        self.assertEqual(values["swap"], "0")
        self.assertEqual(values["cpu"], "800000 100000", "CPUQuota=800% is eight cores of one period")
        self.assertEqual(values["pids"], "4096")
        self.assertEqual(values["high"], str(512 * 1024 ** 2))
        self.assertNotEqual(values["procs"].strip(), "", "no process was moved into the cgroup")

    def test_sizes_and_quotas_are_read_as_systemd_reads_them(self) -> None:
        out = self.root / "UNITS"
        total = int(subprocess.run(["awk", "/^MemTotal:/ {print $2 * 1024}", "/proc/meminfo"],
                                   capture_output=True, text=True).stdout.strip())
        cases = [
            ("MemoryMax=512M", "memory.max", str(512 * 1024 ** 2)),
            ("MemoryMax=1536", "memory.max", "1536"),
            ("MemoryMax=2GB", "memory.max", str(2 * 1000 ** 3)),
            ("MemoryMax=infinity", "memory.max", "max"),
            ("MemoryMax=10%", "memory.max", str(total // 10)),
            ("CPUQuota=50%", "cpu.max", "50000 100000"),
            ("CPUQuota=infinity", "cpu.max", "max 100000"),
            ("TasksMax=infinity", "pids.max", "max"),
        ]
        for prop, file, want in cases:
            with self.subTest(prop=prop):
                run = self.run_shim("--user", "--scope", "--unit", "u", "-p", prop, "--",
                                    "bash", "-c", f'cat "{self.cgroup}/u.scope/{file}" >"{out}"')
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                got = out.read_text(encoding="utf-8").strip()
                # A percentage of this machine's RAM is rounded by the kernel to a page.
                if prop.endswith("%") and file == "memory.max":
                    self.assertLess(abs(int(got) - int(want)), 1 << 20, f"{prop}: {got}")
                else:
                    self.assertEqual(got, want, prop)

    def test_the_cgroup_is_removed_when_the_run_ends_however_it_ends(self) -> None:
        for name, command, status in (
            ("ok", ["true"], 0),
            ("failed", ["bash", "-c", "exit 3"], 3),
        ):
            with self.subTest(run=name):
                run = self.run_shim("--user", "--scope", "-q", "-p", "MemoryMax=1G", "--", *command)
                self.assertEqual(run.returncode, status, run.stdout + run.stderr)
                self.assertEqual(self.scopes(), [], f"{name}: the cgroup was left behind")

    def test_a_cgroup_a_detached_process_held_is_named_and_the_next_run_removes_it(self) -> None:
        """A command that leaves a process behind keeps its cgroup, as a scope does on the host.
        No manager removes it in a box, so the next run here sweeps the empty ones whose run has
        ended: the pid in each name is that of the run that made it, and this one no longer runs."""
        ended = subprocess.Popen(["true"])
        ended.wait()
        held = self.cgroup / f"agentbox-run-{ended.pid}-1.scope"
        held.mkdir()
        for name, value in CGROUP_FILES.items():
            (held / name).write_text(value, encoding="utf-8")
        (held / "cgroup.procs").write_text("4242\n", encoding="utf-8")  # still running
        empty = self.cgroup / f"agentbox-run-{ended.pid}-2.scope"
        empty.mkdir()
        for name, value in CGROUP_FILES.items():
            (empty / name).write_text(value, encoding="utf-8")
        named = self.cgroup / "someone-elses.scope"  # not this shim's to remove
        named.mkdir()
        run = self.run_shim("--user", "--scope", "-q", "-p", "MemoryMax=1G", "--", "true")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertTrue(held.is_dir(), "a cgroup still holding a process was removed")
        self.assertFalse(empty.exists(), "an empty cgroup an earlier run left was not swept")
        self.assertTrue(named.is_dir(), "a cgroup this shim did not name was removed")

    def test_a_run_still_going_keeps_its_empty_cgroup_when_another_run_sweeps(self) -> None:
        """A run's cgroup is empty from its mkdir until its command is moved in. A second run that
        starts in that window sweeps the empty cgroups of runs that have ended, and the first
        run's is not one of them: it is held at its mkdir here, alive, while the second runs."""
        gated = write_stub(self.root / "gated", "mkdir",
                           "\n".join([f'"{self.stubs / "mkdir"}" "$@" || exit $?', *HOOK_HOLDS]))
        race_a_sweep(self, self.environ({"PATH": f"{gated.parent}:{self.environ()['PATH']}"}),
                     lambda: self.run_shim("--user", "--scope", "-q", "-p", "MemoryMax=1G", "--", "true"),
                     self.scopes)

    def test_the_command_s_exit_status_and_streams_are_the_caller_s(self) -> None:
        run = self.run_shim("--user", "--scope", "-q", "-p", "MemoryMax=1G", "--",
                            "bash", "-c", 'echo out; echo err >&2; cat; exit 7',
                            input_text="stdin reached it\n")
        self.assertEqual(run.returncode, 7)
        self.assertIn("out", run.stdout)
        self.assertIn("stdin reached it", run.stdout)
        self.assertIn("err", run.stderr)

    def test_quiet_says_nothing_and_the_default_names_the_cgroup(self) -> None:
        quiet = self.run_shim("--user", "--scope", "-q", "-p", "MemoryMax=1G", "--", "true")
        self.assertEqual(quiet.stderr, "", quiet.stderr)
        loud = self.run_shim("--user", "--scope", "-p", "MemoryMax=1G", "--", "true")
        self.assertIn("agent box", loud.stderr)

    # ── what it refuses, and what it hands on ───────────────────────────────────────────────
    def test_a_resource_limit_it_cannot_apply_is_refused_before_anything_runs(self) -> None:
        """A capped run that silently loses its cap is the failure this exists to prevent."""
        marker = self.root / "RAN"
        run = self.run_shim("--user", "--scope", "-p", "MemoryZSwapMax=1G", "--",
                            "touch", str(marker))
        self.assertEqual(run.returncode, 1)
        self.assertIn("cannot apply", run.stderr)
        self.assertIn("Nothing ran", run.stderr)
        self.assertFalse(marker.exists(), "the command ran without the cap it asked for")
        self.assertEqual(self.scopes(), [])

    def test_a_unit_property_that_is_not_a_limit_is_named_and_the_run_goes_on(self) -> None:
        run = self.run_shim("--user", "--scope", "-p", "KillMode=mixed",
                            "-p", "MemoryMax=1G", "--", "echo", "ran")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("ran", run.stdout)
        self.assertIn("KillMode", run.stderr)

    def test_oom_policy_kill_or_stop_makes_the_cgroup_one_oom_group(self) -> None:
        """systemd's OOMPolicy=kill takes the whole unit when the kernel OOM-kills a process of it; here the kernel
        does that itself for a cgroup whose memory.oom.group is 1. continue, a scope's default, leaves it 0. Read from
        inside the run, since the shim removes the directory when it ends."""
        out = self.root / "GROUP"
        for policy, want in (("kill", "1"), ("stop", "1"), ("continue", "0")):
            with self.subTest(policy=policy):
                run = self.run_shim("--user", "--scope", "-q", "--unit", "g", "-p", "MemoryMax=1G",
                                    "-p", f"OOMPolicy={policy}", "--",
                                    "bash", "-c", f'cat "{self.cgroup}/g.scope/memory.oom.group" >"{out}"')
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                self.assertEqual(out.read_text(encoding="utf-8"), want)
                self.assertNotIn("OOMPolicy", run.stderr)
        marker = self.root / "RAN"
        run = self.run_shim("--user", "--scope", "-p", "OOMPolicy=sometimes", "--", "touch", str(marker))
        self.assertEqual(run.returncode, 1)
        self.assertIn("OOMPolicy=sometimes", run.stderr)
        self.assertFalse(marker.exists())
        self.assertEqual(self.scopes(), [])

    def test_a_status_passes_through_and_only_a_proven_oom_kill_ends_the_shim_by_sigkill(self) -> None:
        """On the host the command is exec'd in its scope, so its caller sees the signal that ended it. Here the shim
        ends by SIGKILL only when its command ended with 137 and its cgroup recorded an OOM kill, the one death it can
        prove; every other status passes through as it is. A death by another signal N reads as 128 + N (a SIGTERM
        death as 143, a SIGKILL with no OOM kill as 137), the same as a command that exits 128 + N."""
        events = f"{self.cgroup}/r.scope/memory.events"
        for name, script, status in (("oom-killed", f'printf "oom 1\\noom_kill 1\\n" >"{events}"; kill -KILL $$',
                                      -signal.SIGKILL),
                                     ("oom-recorded-exit-0", f'printf "oom 1\\noom_kill 1\\n" >"{events}"', 0),
                                     ("kill-without-oom", "kill -KILL $$", 137),
                                     ("term-death", "kill -TERM $$", 143),
                                     ("exit-143", "exit 143", 143),
                                     ("exit-137", "exit 137", 137),
                                     ("exit-129", "exit 129", 129),
                                     ("exit-3", "exit 3", 3)):
            with self.subTest(command=name):
                run = self.run_shim("--user", "--scope", "-q", "--unit", "r", "-p", "MemoryMax=1G", "--",
                                    "bash", "-c", script)
                self.assertEqual(run.returncode, status, run.stdout + run.stderr)
                self.assertEqual(self.scopes(), [], f"{name}: the cgroup was left behind")
                said = "shims/systemd-run: oom_kill=1 in " in run.stderr
                self.assertEqual(said, name.startswith("oom-"), run.stderr)

    def test_an_oom_kill_in_the_cgroup_is_said_under_quiet_too(self) -> None:
        """The host records an OOM kill as the scope's Result=oom-kill; a box has no unit to carry it, so the shim
        says it, read from the cgroup's memory.events before it removes the cgroup."""
        run = self.run_shim("--user", "--scope", "-q", "--unit", "o", "-p", "MemoryMax=1G", "--",
                            "bash", "-c", f'printf "oom 2\\noom_kill 2\\n" >"{self.cgroup}/o.scope/memory.events"')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("shims/systemd-run: oom_kill=2 in ", run.stderr)
        quiet = self.run_shim("--user", "--scope", "-q", "-p", "MemoryMax=1G", "--", "true")
        self.assertEqual(quiet.stderr, "", "a run with no OOM kill said something under --quiet")

    def test_an_option_this_box_cannot_honour_is_refused_rather_than_dropped(self) -> None:
        marker = self.root / "RAN"
        run = self.run_shim("--user", "--scope", "--remain-after-exit", "--", "touch", str(marker))
        self.assertEqual(run.returncode, 1)
        self.assertIn("--remain-after-exit", run.stderr)
        self.assertFalse(marker.exists())

    def test_every_call_that_is_not_a_user_scope_goes_to_the_real_systemd_run(self) -> None:
        for argv in (
            ["--user", "--on-active=5s", "echo", "hi"],   # a timer, not a scope
            ["--scope", "echo", "hi"],                    # the system manager's
            ["--version"],
            ["--help"],
        ):
            with self.subTest(argv=argv):
                self.real_calls.write_text("", encoding="utf-8")
                run = self.run_shim(*argv)
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                self.assertIn(argv[0], self.real_calls.read_text(encoding="utf-8"))

    def test_on_a_host_every_call_is_the_real_one_s(self) -> None:
        """No box: the host's user manager works, and nothing here should come between."""
        self.real_calls.write_text("", encoding="utf-8")
        run = self.run_shim("--user", "--scope", "-p", "MemoryMax=24G", "--", "echo", "hi",
                            env={"AGENTBOX_BOX": "", "AGENTBOX_IDENTITY_FILE": str(self.root / "none")})
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("--user --scope", self.real_calls.read_text(encoding="utf-8"))
        self.assertEqual(self.scopes(), [], "a host call made a cgroup")

    def test_a_name_another_run_holds_is_refused(self) -> None:
        (self.cgroup / "taken.scope").mkdir()
        run = self.run_shim("--user", "--scope", "--unit=taken", "-p", "MemoryMax=1G", "--", "true")
        self.assertEqual(run.returncode, 1)
        self.assertIn("another run holds that name", run.stderr)
        self.assertTrue((self.cgroup / "taken.scope").is_dir(), "it removed another run's cgroup")


def sudo(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(["sudo", "-n", *argv], capture_output=True, text=True, timeout=TIMEOUT)


def wait_until_empty(cgroup: Path, timeout: float = TIMEOUT) -> bool:
    """True once the kernel says no process runs in `cgroup` or below it, False at the timeout.
    cgroup.events is read again each time the kernel signals it changed (POLLPRI), as
    cgroup-v2.rst documents: no polling interval to guess."""
    deadline = time.monotonic() + timeout
    with open(cgroup / "cgroup.events", "rb", buffering=0) as events:
        poller = select.poll()
        poller.register(events, select.POLLPRI)
        while True:
            events.seek(0)
            if b"populated 0" in events.read():
                return True
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            poller.poll(left * 1000)


def remove_cgroup(cgroup: Path) -> bool:
    """Ends every process in `cgroup` and below it, then removes the cgroups in it and itself."""
    try:
        sudo("sh", "-c", f"printf 1 > '{cgroup}/cgroup.kill'")
        if not wait_until_empty(cgroup):
            return False
        for child in [p for p in cgroup.iterdir() if p.is_dir()]:
            sudo("rmdir", str(child))
    except FileNotFoundError:  # gone already: another case's setup swept it
        pass
    sudo("rmdir", str(cgroup))
    return not cgroup.exists()


def maker_runs(cgroup: Path, prefix: str) -> bool:
    """Whether the process whose pid the cgroup's name carries after `prefix` still runs."""
    maker = cgroup.name[len(prefix):].split("-", 1)[0]
    return maker.isdigit() and Path("/proc", maker).exists()


def holds_a_process(cgroup: Path) -> bool:
    """Read, not sized: cgroupfs gives every control file a size of 0, whatever it holds."""
    return (cgroup / "cgroup.procs").read_text(encoding="utf-8").strip() != ""


def sweep_ended_cases() -> None:
    """Removes the cgroup a case killed before its cleanup left (SIGKILL, a timeout), and the run
    cgroups in it, by the rule of the shim's own sweep: only when none of them holds a process and
    the pid in each name, the case's and every run's, runs no more. A reused pid only keeps a cgroup
    until that process ends. The pids are read in this process's own pid namespace, so a case or a
    run in another pid namespace that sees this same cgroup tree is not told apart from a dead one."""
    for case in CGROUPS.glob("agentbox-test-*"):
        try:
            if not case.is_dir() or maker_runs(case, "agentbox-test-") or holds_a_process(case):
                continue
            runs = [p for p in case.iterdir() if p.is_dir()]
            if any(maker_runs(run, "agentbox-run-") or holds_a_process(run) for run in runs):
                continue
        except FileNotFoundError:  # another case's setup, under `pytest -n`, removed it meanwhile
            continue
        for run in runs:
            sudo("rmdir", str(run))
        sudo("rmdir", str(case))


@unittest.skipUnless(
    os.path.isdir("/sys/fs/cgroup") and shutil.which("sudo") is not None
    and subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0,
    "needs a cgroup v2 tree at /sys/fs/cgroup and sudo without a password (an agent box, or a host that grants it)",
)
class RealCgroup(unittest.TestCase):
    """The kernel enforces what the shim wrote, in a real cgroup tree.

    /sys/fs/cgroup is the machine's, and every other run of the shim on it (another case of this
    class under `pytest -n`, a session's capped build in a box) makes and leaves agentbox-run-*
    cgroups there: a case that read the whole tree failed on theirs (dispatch://INFRA-4383). So
    each case gives the shim a cgroup of its own as its root (AGENTBOX_CGROUP_ROOT) and reads only
    that. Root makes it, as root owns a box's own root, so the shim takes the sudo path it takes
    in a box. Its name is no agentbox-run-* one, which no run's sweep would therefore touch; it
    carries this process's pid instead, which the next case's setup reads (sweep_ended_cases)."""

    def setUp(self) -> None:
        sweep_ended_cases()
        self.root = CGROUPS / f"agentbox-test-{os.getpid()}-{os.urandom(4).hex()}"
        made = sudo("mkdir", str(self.root))
        self.assertEqual(made.returncode, 0, made.stderr)
        self.addCleanup(self.remove_root)

    def remove_root(self) -> None:
        """Ends whatever a case left running in its cgroup, then removes the cgroup."""
        self.assertTrue(remove_cgroup(self.root), f"{self.root} is still there")

    def scopes(self) -> list[Path]:
        return [p for p in self.root.iterdir() if p.is_dir()]

    # ── the cgroups of cases that died ──────────────────────────────────────────────────────
    def process(self) -> subprocess.Popen:
        """A process of this case's own, which ends at end(). It stands in for a case or a run
        while its cgroup is built, so no other case's setup sweeps that cgroup half built."""
        proc = subprocess.Popen(["cat"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
        self.addCleanup(self.end, proc)
        return proc

    @staticmethod
    def end(proc: subprocess.Popen) -> None:
        if proc.stdin is not None and not proc.stdin.closed:
            proc.stdin.close()  # cat ends at EOF: no signal
        proc.wait(timeout=TIMEOUT)

    def plant(self, maker: int, *runs: int) -> Path:
        """A case's cgroup as a case of process `maker` leaves it, with a run cgroup in it for
        each pid of `runs`, all empty. Removed at the end, if a setup has not removed it."""
        planted = CGROUPS / f"agentbox-test-{maker}-{os.urandom(4).hex()}"
        made = sudo("mkdir", str(planted))
        self.assertEqual(made.returncode, 0, made.stderr)
        self.addCleanup(remove_cgroup, planted)
        for n, run in enumerate(runs):
            made = sudo("mkdir", str(planted / f"agentbox-run-{run}-{n}.scope"))
            self.assertEqual(made.returncode, 0, made.stderr)
        return planted

    def next_case(self) -> list[tuple]:
        """Runs another case's setup, as the next case of this class runs it, and returns the
        sudo calls it made."""
        other = RealCgroup(self._testMethodName)
        with mock.patch.object(sys.modules[__name__], "sudo", wraps=sudo) as calls:
            other.setUp()
        self.addCleanup(other.doCleanups)
        return [call.args for call in calls.call_args_list]

    def test_the_empty_cgroup_a_killed_case_left_is_removed_by_the_next_case(self) -> None:
        """A case killed before its cleanup ran (SIGKILL, a timeout) leaves its cgroup and the
        cgroup of the run it was making in it; nothing else ever removes them."""
        killed = self.process()
        planted = self.plant(killed.pid, killed.pid)
        self.end(killed)
        self.next_case()
        self.assertFalse(planted.exists(), "the cgroup of a case that died was left behind")

    def test_the_cgroup_of_a_case_still_running_is_kept(self) -> None:
        planted = self.plant(os.getpid())
        calls = self.next_case()
        self.assertTrue(planted.is_dir(), "a setup removed the cgroup of a case still running")
        self.assertNotIn(("rmdir", str(planted)), calls, "a setup tried to remove a running case's cgroup")

    def test_a_dead_case_s_cgroup_that_is_not_empty_is_kept(self) -> None:
        """A process in it, or a run in it whose shim still runs (a run's cgroup is empty until
        its command is moved in): either keeps it, and nothing in it is touched."""
        killed = self.process()
        held = self.plant(killed.pid)
        holder = self.process()  # ended before its cgroup is removed: cleanups run last first
        moved = sudo("sh", "-c", f"printf %s {holder.pid} > '{held}/cgroup.procs'")
        self.assertEqual(moved.returncode, 0, moved.stderr)
        going = self.plant(killed.pid, os.getpid())
        planted = [held, going, *[p for p in going.iterdir() if p.is_dir()]]
        self.end(killed)
        calls = self.next_case()
        for cgroup in planted:
            self.assertTrue(cgroup.is_dir(), f"a setup removed {cgroup}")
            self.assertNotIn(("rmdir", str(cgroup)), calls, f"a setup tried to remove {cgroup}")

    def environ(self, env: dict | None = None) -> dict:
        return {**os.environ, "AGENTBOX_BOX": os.environ.get("AGENTBOX_BOX", "agentbox-test"),
                "AGENTBOX_CGROUP_ROOT": str(self.root), **(env or {})}

    def run_shim(self, *argv: str, env: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run([str(SHIM), *argv], capture_output=True, text=True, timeout=TIMEOUT,
                              env=self.environ(env))

    def test_a_run_whose_command_is_not_in_yet_keeps_its_cgroup_while_another_run_sweeps(self) -> None:
        """The race on the kernel's own cgroups: the first run is held just after root made its
        cgroup, the second sweeps meanwhile. cgroupfs sizes cgroup.procs at 0 bytes whatever it
        holds, and the empty cgroup of a run whose command is not in yet looks exactly like one an
        ended run left, so only the maker's pid in the name tells them apart."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        gated = write_stub(Path(tmp.name), "sudo", "\n".join([
            f'"{shutil.which("sudo")}" "$@" || exit $?',
            '[ "${2-}" = mkdir ] || exit 0',  # the shim runs `sudo -n mkdir <its cgroup>`
            *HOOK_HOLDS,
        ]))
        race_a_sweep(self, self.environ({"PATH": f"{gated.parent}:{os.environ.get('PATH', '')}"}),
                     lambda: self.run_shim("--user", "--scope", "-q", "-p", "MemoryMax=64M", "--", "true"),
                     self.scopes)

    def test_a_run_past_its_memory_cap_is_killed_by_the_kernel(self) -> None:
        run = self.run_shim("--user", "--scope", "-q", "-p", "MemoryMax=64M", "-p", "MemorySwapMax=0",
                            "--", sys.executable, "-c",
                            "x=bytearray()\nfor _ in range(400): x += bytearray(1024*1024)\nprint('NOT KILLED')")
        # Ended by the kernel's SIGKILL, which the shim then ends by too, as the exec'd command of a host scope does.
        self.assertEqual(run.returncode, -signal.SIGKILL, run.stdout + run.stderr)
        self.assertNotIn("NOT KILLED", run.stdout)
        self.assertIn("shims/systemd-run: oom_kill=", run.stderr)
        # The shim's own plumbing is not reported to the caller as the failure.
        self.assertNotIn("read -r _ <", run.stderr)

    def test_an_oom_kill_takes_the_whole_command_under_oom_policy_kill(self) -> None:
        """A child past the cap: with OOMPolicy=kill the kernel kills every process of the cgroup with it, so the
        shell that would go on is gone too, as systemd's policy takes a host scope whole. With continue, a scope's
        default, the child dies alone and the shell goes on."""
        eater = "x=bytearray()\nfor _ in range(400): x += bytearray(1024*1024)\n"
        for policy, status, went_on in (("kill", -signal.SIGKILL, False), ("continue", 0, True)):
            with self.subTest(policy=policy):
                run = self.run_shim("--user", "--scope", "-q", "-p", "MemoryMax=64M", "-p", "MemorySwapMax=0",
                                    "-p", f"OOMPolicy={policy}", "--", "sh", "-c",
                                    f'"{sys.executable}" -c "$1"; echo WENT ON', "sh", eater)
                self.assertEqual(run.returncode, status, run.stdout + run.stderr)
                self.assertEqual("WENT ON" in run.stdout, went_on, run.stdout + run.stderr)
                self.assertIn("shims/systemd-run: oom_kill=", run.stderr)
                self.assertEqual(self.scopes(), [], "the run's cgroup was left behind")

    def test_a_process_the_command_leaves_behind_keeps_its_cgroup_and_the_next_run_sweeps_it(self) -> None:
        """The kernel refuses to remove a cgroup that still holds a process, as it does a
        scope's on the host; there no manager removes it afterwards, so the next run does once the
        process has ended. cgroupfs gives cgroup.procs a size of 0 whatever it holds, so the sweep
        reads it: a run while the process lives does not even try to remove that cgroup."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        release = Path(tmp.name) / "release"
        os.mkfifo(release)
        # Its stdio is not the run's pipes, so the run's output ends when the shim does.
        run = self.run_shim("--user", "--scope", "-p", "MemoryMax=256M", "--",
                            "bash", "-c", f'(read -r _ < "{release}" &) >/dev/null 2>&1; echo parent done')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("parent done", run.stdout)
        self.assertIn("still run in it", run.stderr)
        left = self.scopes()
        self.assertEqual(len(left), 1, f"the detached process's cgroup is gone: {left}")
        calls = Path(tmp.name) / "SUDO_CALLS"
        logging = write_stub(Path(tmp.name) / "stubs", "sudo",
                             f'printf "%s\\n" "$*" >>"{calls}"\nexec "{shutil.which("sudo")}" "$@"')
        during = self.run_shim("--user", "--scope", "-q", "-p", "MemoryMax=64M", "--", "true",
                               env={"PATH": f"{logging.parent}:{os.environ.get('PATH', '')}"})
        self.assertEqual(during.returncode, 0, during.stdout + during.stderr)
        self.assertTrue(left[0].is_dir(), "a cgroup still holding a process was removed")
        said = calls.read_text(encoding="utf-8")
        self.assertIn(f"mkdir {self.root}/agentbox-run-", said, "the run's sudo calls were not seen")
        self.assertNotIn(f"rmdir {left[0]}", said, "the sweep tried to remove a cgroup still holding a process")
        # The detached process ends when it reads a line; its cgroup is then empty and sweepable.
        writer = os.open(release, os.O_WRONLY | os.O_NONBLOCK)  # ENXIO: the process is gone already
        os.write(writer, b"go\n")
        os.close(writer)
        self.assertTrue(wait_until_empty(left[0]), "the detached process never ended")
        after = self.run_shim("--user", "--scope", "-q", "-p", "MemoryMax=64M", "--", "true")
        self.assertEqual(after.returncode, 0, after.stdout + after.stderr)
        self.assertFalse(left[0].exists(), "the next run did not sweep the empty cgroup")

    def test_a_run_inside_the_cap_finishes_and_leaves_no_cgroup(self) -> None:
        run = self.run_shim("--user", "--scope", "-q", "-p", "MemoryMax=512M", "--",
                            sys.executable, "-c",
                            "print(len(bytearray(8 * 1024 * 1024))); print(open('/proc/self/cgroup').read())")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("8388608", run.stdout)
        self.assertIn(f"/{self.root.name}/agentbox-run-", run.stdout, "the run was not in this case's cgroup")
        self.assertEqual(self.scopes(), [], "the run's cgroup was left behind")


if __name__ == "__main__":
    unittest.main()
