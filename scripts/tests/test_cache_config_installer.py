#!/usr/bin/env python3
"""installers/cache-config.sh names the box's uv, pip and bun caches in each tool's config file.

A tool that does not inherit UV_CACHE_DIR, PIP_CACHE_DIR or BUN_INSTALL_CACHE_DIR from .bashrc or
scripts/agent-session (one run from omp's Python eval kernel, whose environment is an allow-list
without them, or from a systemd user unit) used its home default, which nothing caps. The installer
writes uv.toml, pip.conf and .bunfig.toml, rewrites only a file it wrote, and a second run changes
nothing.

Technique: CACHE_CONFIG_NVME_HOME and CACHE_CONFIG_SCRATCH_HOME point the installer at a scratch
tree, and HOME at a scratch home. The real uv, pip and bun, run with only the variables omp's eval
kernel passes, show which cache each uses before the installer runs and after.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.tests.test_dotfiles_sync import real_binary

INSTALLER = Path(__file__).resolve().parents[2] / "installers/cache-config.sh"
MARKER = "# Written by installers/cache-config.sh in the dotfiles. Delete this line to keep your own edits."


def installed(name: str) -> str | None:
    """The tool's own binary (not a mise shim, which breaks under a scratch HOME), or None."""
    try:
        return real_binary(name)
    except unittest.SkipTest:
        return None


class CacheConfigInstaller(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.nvme = self.root / "nvme"
        self.scratch = self.root / "scratch"
        self.uv = self.home / ".config" / "uv" / "uv.toml"
        self.pip = self.home / ".config" / "pip" / "pip.conf"
        self.bun = self.home / ".bunfig.toml"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def install(self) -> subprocess.CompletedProcess[str]:
        env = {"HOME": str(self.home), "PATH": os.environ["PATH"], "CACHE_CONFIG_NVME_HOME": str(self.nvme),
               "CACHE_CONFIG_SCRATCH_HOME": str(self.scratch)}
        r = subprocess.run(["bash", str(INSTALLER)], capture_output=True, text=True, env=env, check=False, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def test_names_the_caches_where_the_shells_do(self) -> None:
        self.nvme.mkdir()
        self.scratch.mkdir()
        self.install()
        self.assertEqual(self.uv.read_text(),
                         f'{MARKER}\ncache-dir = "{self.scratch}/cache/uv"\nlink-mode = "copy"\n')
        self.assertEqual(self.pip.read_text(), f"{MARKER}\n[global]\ncache-dir = {self.scratch}/cache/pip\n")
        self.assertEqual(self.bun.read_text(), f'{MARKER}\n[install.cache]\ndir = "{self.nvme}/cache/bun"\n')

    def test_without_the_scratch_disk_everything_is_on_the_big_disk_and_uv_may_hardlink(self) -> None:
        self.nvme.mkdir()
        self.install()
        self.assertEqual(self.uv.read_text(), f'{MARKER}\ncache-dir = "{self.nvme}/cache/uv"\n')
        self.assertIn(f"cache-dir = {self.nvme}/cache/pip\n", self.pip.read_text())

    def test_a_second_run_changes_nothing(self) -> None:
        self.nvme.mkdir()
        self.install()
        before = [(p.stat().st_ino, p.stat().st_mtime_ns) for p in (self.uv, self.pip, self.bun)]
        r = self.install()
        self.assertEqual(r.stdout.count("already up to date"), 3, r.stdout)
        self.assertEqual(before, [(p.stat().st_ino, p.stat().st_mtime_ns) for p in (self.uv, self.pip, self.bun)])

    def test_rewrites_a_file_it_wrote_and_leaves_someone_elses_alone(self) -> None:
        self.nvme.mkdir()
        self.uv.parent.mkdir(parents=True)
        self.uv.write_text(f'{MARKER}\ncache-dir = "/old/uv"\n')
        self.pip.parent.mkdir(parents=True)
        self.pip.write_text("[global]\nindex-url = https://example.invalid/simple\n")
        r = self.install()
        self.assertEqual(self.uv.read_text(), f'{MARKER}\ncache-dir = "{self.nvme}/cache/uv"\n')
        self.assertEqual(self.pip.read_text(), "[global]\nindex-url = https://example.invalid/simple\n")
        self.assertIn(f"{self.pip} was not written here", r.stderr)
        self.assertIn(f"cache-dir = {self.nvme}/cache/pip", r.stderr)

    def test_a_machine_without_the_big_disk_is_left_alone(self) -> None:
        r = self.install()
        self.assertIn("left alone", r.stdout)
        self.assertEqual([p for p in (self.uv, self.pip, self.bun) if p.exists()], [])

    def test_tools_with_the_eval_kernels_environment_use_the_box_caches(self) -> None:
        self.nvme.mkdir()
        self.scratch.mkdir()
        work = self.root / "work"
        work.mkdir()
        # What omp's Python eval kernel passes on (runtime.ts's allow-list): none of the cache variables.
        kernel_env = {"HOME": str(self.home), "PATH": "/usr/bin:/bin", "TMPDIR": str(self.root)}
        probes: list[tuple[str, list[str], Path, Path]] = []
        uv, bun = installed("uv"), installed("bun")
        if uv:
            probes.append(("uv", [uv, "cache", "dir"], self.home / ".cache" / "uv", self.scratch / "cache" / "uv"))
        if subprocess.run(["/usr/bin/python3", "-m", "pip", "--version"], capture_output=True, check=False).returncode == 0:
            probes.append(("pip", ["/usr/bin/python3", "-m", "pip", "cache", "dir"], self.home / ".cache" / "pip",
                           self.scratch / "cache" / "pip"))
        if bun:
            probes.append(("bun", [bun, "pm", "cache"], self.home / ".bun" / "install" / "cache",
                           self.nvme / "cache" / "bun"))
        if not probes:
            self.skipTest("none of uv, pip and bun is installed")

        def cache_dirs() -> dict[str, str]:
            return {name: subprocess.run(cmd, capture_output=True, text=True, env=kernel_env, cwd=work, check=True,
                                         timeout=60).stdout.strip() for name, cmd, _, _ in probes}

        # Without the files each tool uses its home default, which nothing caps.
        self.assertEqual(cache_dirs(), {name: str(default) for name, _, default, _ in probes})
        self.install()
        self.assertEqual(cache_dirs(), {name: str(box) for name, _, _, box in probes})


if __name__ == "__main__":
    unittest.main()
