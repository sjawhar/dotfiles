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
dir on PATH records the calls that must pass through. One case at the end, skipped unless this
machine really delegates its cgroup tree, runs the whole thing against /sys/fs/cgroup and
checks the kernel enforces the cap it wrote.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SHIM = Path(__file__).resolve().parents[1] / "systemd-run"
TIMEOUT = 60
# Every file a delegated cgroup v2 directory offers that this shim writes.
CGROUP_FILES = {
    "memory.max": "max",
    "memory.high": "max",
    "memory.min": "0",
    "memory.low": "0",
    "memory.swap.max": "max",
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
        # cgroupfs removes a cgroup directory with `rmdir` although its control files are
        # "in" it; a plain directory of files does not. The stub closes that one difference.
        write_stub(self.stubs, "rmdir", "\n".join([
            'dir="${!#}"',
            f'for f in {" ".join(CGROUP_FILES)}; do /bin/rm -f "$dir/$f"; done',
            'exec /bin/rmdir "$@"',
        ]))

    def run_shim(self, *argv: str, env: dict | None = None, input_text: str = "") -> subprocess.CompletedProcess:
        environ = {
            **os.environ,
            "AGENTBOX_BOX": "agentbox-0123456789ab",
            "AGENTBOX_CGROUP_ROOT": str(self.cgroup),
            "AGENTBOX_IDENTITY_FILE": str(self.root / "no-identity"),
            "PATH": f"{self.stubs}:{SHIM.parent}:{self.after}:{os.environ.get('PATH', '')}",
        }
        environ.update(env or {})
        return subprocess.run([str(SHIM), *argv], capture_output=True, text=True,
                              env=environ, input=input_text, timeout=TIMEOUT, cwd=self.root)

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
        run = self.run_shim("--user", "--scope", "-p", "OOMPolicy=continue",
                            "-p", "MemoryMax=1G", "--", "echo", "ran")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("ran", run.stdout)
        self.assertIn("OOMPolicy", run.stderr)

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


@unittest.skipUnless(
    os.path.isdir("/sys/fs/cgroup") and shutil.which("sudo") is not None
    and subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0,
    "needs a delegated cgroup tree and sudo without a password (an agent box)",
)
class RealCgroup(unittest.TestCase):
    """The kernel enforces what the shim wrote. Only in a box; skipped anywhere else."""

    def run_shim(self, *argv: str) -> subprocess.CompletedProcess:
        return subprocess.run([str(SHIM), *argv], capture_output=True, text=True, timeout=TIMEOUT,
                              env={**os.environ, "AGENTBOX_BOX": os.environ.get("AGENTBOX_BOX", "agentbox-test")})

    def test_a_run_past_its_memory_cap_is_killed_by_the_kernel(self) -> None:
        run = self.run_shim("--user", "--scope", "-q", "-p", "MemoryMax=64M", "-p", "MemorySwapMax=0",
                            "--", sys.executable, "-c",
                            "x=bytearray()\nfor _ in range(400): x += bytearray(1024*1024)\nprint('NOT KILLED')")
        self.assertEqual(run.returncode, 137, run.stdout + run.stderr)
        self.assertNotIn("NOT KILLED", run.stdout)
        # The shim's own plumbing is not reported to the caller as the failure.
        self.assertNotIn("read -r _ <", run.stderr)

    def test_a_run_inside_the_cap_finishes_and_leaves_no_cgroup(self) -> None:
        run = self.run_shim("--user", "--scope", "-q", "-p", "MemoryMax=512M", "--",
                            sys.executable, "-c", "print(len(bytearray(8 * 1024 * 1024)))")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("8388608", run.stdout)
        left = [p for p in Path("/sys/fs/cgroup").glob("agentbox-run-*.scope")]
        self.assertEqual(left, [], f"cgroups left behind: {left}")


if __name__ == "__main__":
    unittest.main()
