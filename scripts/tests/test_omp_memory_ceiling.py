#!/usr/bin/env python3
"""shims/omp's default memory ceiling scales with the host (dispatch://INFRA-993).

The default is the larger of 24 GiB and MemTotal/20, in whole GiB; a caller's value is clamped at
four times the default. sami-rocqperf (251 GiB) keeps 24G; sami-devbox (2.1 TiB) gets 109G.
A machine's own `$XDG_CONFIG_HOME/omp/memory-max` (`off`, or a size) replaces the default there:
oryx, Sami's personal machine, sets `off`.
systemd-run is a stub that records its arguments, and /proc/meminfo is a file of the test's.
"""

from __future__ import annotations

import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
SHIM = DOTFILES / "shims" / "omp"

ROCQPERF_KB = 263897780  # sami-rocqperf's MemTotal, 251 GiB
DEVBOX_KB = 2303551644  # sami-devbox's MemTotal, 2.1 TiB
BOUNDARY_KB = 24 * 20 << 20  # MemTotal/20 is exactly 24 GiB


def write_stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class OmpMemoryCeiling(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        home = root / "home"
        agent = home / ".omp" / "agent"
        agent.mkdir(parents=True)
        (home / ".config").mkdir()
        for name in ("config.yml", "models.yml"):
            (agent / name).write_text("{}", encoding="utf-8")
        stubs = root / "bin"
        stubs.mkdir()
        tool = home / ".mise" / "installs" / "github-sjawhar-oh-my-pi" / "1.0.0" / "bin"
        tool.mkdir(parents=True)
        write_stub(tool, "omp", 'echo "AGENT omp $* scope=${OMP_MEMORY_SCOPE:-}"')
        write_stub(stubs, "mise", f"echo 'export PATH=\"{tool}:$PATH\"'")
        self.calls = root / "systemd-run.calls"
        write_stub(stubs, "systemd-run", f'printf "%s\\n" "$*" >> "{self.calls}"')
        self.meminfo = root / "meminfo"
        # Every case here is a HOST: a box takes no scope of its own (its own cgroup is the
        # ceiling), and this suite often runs inside one.
        self.env = {
            "DOTFILES_DIR": str(DOTFILES),
            "HOME": str(home),
            "PATH": f"{stubs}:/usr/bin:/bin",
            "XDG_CONFIG_HOME": str(home / ".config"),
            "OMP_MEMINFO": str(self.meminfo),
            "AGENTBOX_IDENTITY_FILE": str(root / "no-identity-file"),
        }
        self.machine_file = home / ".config" / "omp" / "memory-max"

    def ceiling(self, mem_total_kb: int, **extra: str) -> tuple[str, str]:
        """The MemoryMax the shim asks systemd for on a host of this size, and its stderr."""
        self.meminfo.write_text(f"MemTotal:       {mem_total_kb} kB\nMemFree:        1 kB\n", encoding="utf-8")
        result = subprocess.run([str(SHIM), "--version"], capture_output=True, text=True,
                                env={**self.env, **extra}, check=False, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls.read_text(encoding="utf-8").splitlines()
        self.calls.unlink()
        self.assertTrue(calls, result.stderr)
        values = {word.split("=", 1)[1] for call in calls for word in call.split() if word.startswith("MemoryMax=")}
        self.assertEqual(len(values), 1, calls)
        return values.pop(), result.stderr

    def test_rocqperf_keeps_24g(self) -> None:
        self.assertEqual(self.ceiling(ROCQPERF_KB)[0], "24G")

    def test_devbox_gets_a_twentieth_of_its_ram(self) -> None:
        self.assertEqual(self.ceiling(DEVBOX_KB)[0], "109G")

    def test_boundary(self) -> None:
        self.assertEqual(self.ceiling(BOUNDARY_KB - 1)[0], "24G")
        self.assertEqual(self.ceiling(BOUNDARY_KB)[0], "24G")
        self.assertEqual(self.ceiling(25 * 20 << 20)[0], "25G")

    def test_unreadable_meminfo_keeps_24g(self) -> None:
        self.assertEqual(self.ceiling(ROCQPERF_KB, OMP_MEMINFO="/nonexistent")[0], "24G")

    def test_a_callers_value_is_clamped_at_four_times_the_default(self) -> None:
        self.assertEqual(self.ceiling(ROCQPERF_KB, OMP_MEMORY_MAX="200G")[0], "96G")
        value, stderr = self.ceiling(DEVBOX_KB, OMP_MEMORY_MAX="200G")
        self.assertEqual(value, "200G")
        value, stderr = self.ceiling(DEVBOX_KB, OMP_MEMORY_MAX="1T")
        self.assertEqual(value, "436G")
        self.assertIn("above the 436G ceiling", stderr)

    def test_a_session_in_a_box_asks_for_no_scope_of_its_own(self) -> None:
        """The box's own cgroup (`docker run --memory`) is the ceiling. Since shims/systemd-run
        makes `--user --scope` work inside a box, a probe there would SUCCEED and wrap the
        session in a second cgroup sized from the HOST's RAM, above the box's own."""
        marker = Path(self.temp_dir.name) / "agentbox-identity"
        marker.write_text("box\n", encoding="utf-8")
        self.meminfo.write_text(f"MemTotal:       {DEVBOX_KB} kB\n", encoding="utf-8")
        result = subprocess.run([str(SHIM), "--version"], capture_output=True, text=True,
                                env={**self.env, "AGENTBOX_IDENTITY_FILE": str(marker)},
                                check=False, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.calls.exists(), "a boxed session asked systemd for a scope")
        self.assertIn("the box's own cgroup", result.stderr)
        self.assertIn("AGENT omp", result.stdout, "the session did not start")

    def machine_says(self, text: str) -> None:
        self.machine_file.parent.mkdir(parents=True, exist_ok=True)
        self.machine_file.write_text(text, encoding="utf-8")

    def start_unscoped(self, **extra: str) -> str:
        """Run the shim and return its stdout, asserting it asked systemd for no scope."""
        self.meminfo.write_text(f"MemTotal:       {ROCQPERF_KB} kB\n", encoding="utf-8")
        result = subprocess.run([str(SHIM), "--version"], capture_output=True, text=True,
                                env={**self.env, **extra}, check=False, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.calls.exists(), "the shim asked systemd for a scope")
        self.assertIn("AGENT omp", result.stdout, "the session did not start")
        return result.stdout

    def test_a_machine_that_says_off_starts_every_session_with_no_scope(self) -> None:
        self.machine_says("off\n")
        stdout = self.start_unscoped()
        self.assertIn("scope=off", stdout, "an omp the session starts would take the default")

    def test_a_session_started_without_a_ceiling_passes_that_to_the_omps_it_starts(self) -> None:
        """No machine file here: a nested launch on another machine still takes no default."""
        self.start_unscoped(OMP_MEMORY_SCOPE="off")

    def test_a_machine_size_replaces_the_default_and_is_clamped_like_a_callers(self) -> None:
        self.machine_says("48G")
        self.assertEqual(self.ceiling(ROCQPERF_KB)[0], "48G")
        self.machine_says("200G")
        self.assertEqual(self.ceiling(ROCQPERF_KB)[0], "96G")

    def test_the_callers_value_wins_over_the_machine_file(self) -> None:
        self.machine_says("off")
        self.assertEqual(self.ceiling(ROCQPERF_KB, OMP_MEMORY_MAX="16G")[0], "16G")
        self.machine_says("48G")
        self.start_unscoped(OMP_MEMORY_MAX="off")

    def test_an_empty_machine_file_keeps_the_default(self) -> None:
        self.machine_says("\n")
        self.assertEqual(self.ceiling(ROCQPERF_KB)[0], "24G")


if __name__ == "__main__":
    unittest.main()
