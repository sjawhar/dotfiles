#!/usr/bin/env python3
"""shims/omp's default memory ceiling scales with the host (dispatch://INFRA-993).

The default is the larger of 24 GiB and MemTotal/20, in whole GiB; a caller's value is clamped at
four times the default. sami-rocqperf (251 GiB) keeps 24G; sami-devbox (2.1 TiB) gets 109G.
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
        write_stub(tool, "omp", 'echo "AGENT omp $*"')
        write_stub(stubs, "mise", f"echo 'export PATH=\"{tool}:$PATH\"'")
        self.calls = root / "systemd-run.calls"
        write_stub(stubs, "systemd-run", f'printf "%s\\n" "$*" >> "{self.calls}"')
        self.meminfo = root / "meminfo"
        self.env = {
            "DOTFILES_DIR": str(DOTFILES),
            "HOME": str(home),
            "PATH": f"{stubs}:/usr/bin:/bin",
            "XDG_CONFIG_HOME": str(home / ".config"),
            "OMP_MEMINFO": str(self.meminfo),
        }

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


if __name__ == "__main__":
    unittest.main()
