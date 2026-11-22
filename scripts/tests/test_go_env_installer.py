#!/usr/bin/env python3
"""installers/go-env.sh names the box's Go build and module caches in Go's own env file.

On sami-devbox on 2026-11-21 three `go test` runs under omp's Python eval kernel had no GOCACHE,
since omp gives that kernel an allow-list of variables without it, and Go's default
~/.cache/go-build held 475 GB that nothing capped. A go command reads GOCACHE and GOMODCACHE from
its env file when its environment does not have them; the installer writes them there, keeps every
other line, and a second run changes nothing.

Technique: GO_ENV_NVME_HOME and GO_ENV_SCRATCH_HOME point the installer at a scratch tree, and HOME
at a scratch home. The real Go toolchain, run with only the variables omp's eval kernel passes,
shows which caches such a command uses, before the installer runs and after.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

INSTALLER = Path(__file__).resolve().parents[2] / "installers/go-env.sh"


def real_go() -> str | None:
    """The Go toolchain's own binary, not a mise shim, which needs the real HOME's configuration."""
    if not shutil.which("go"):
        return None
    goroot = subprocess.run(["go", "env", "GOROOT"], capture_output=True, text=True, check=False).stdout.strip()
    binary = Path(goroot) / "bin" / "go"
    return str(binary) if binary.is_file() else None


class GoEnvInstaller(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.nvme = self.root / "nvme"
        self.scratch = self.root / "scratch"
        self.env_file = self.home / ".config" / "go" / "env"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def install(self) -> subprocess.CompletedProcess[str]:
        env = {"HOME": str(self.home), "PATH": os.environ["PATH"], "GO_ENV_NVME_HOME": str(self.nvme),
               "GO_ENV_SCRATCH_HOME": str(self.scratch)}
        r = subprocess.run(["bash", str(INSTALLER)], capture_output=True, text=True, env=env, check=False, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def test_names_the_scratch_build_cache_and_keeps_other_lines(self) -> None:
        self.nvme.mkdir()
        self.scratch.mkdir()
        self.env_file.parent.mkdir(parents=True)
        self.env_file.write_text("GOPRIVATE=github.com/example\nGOCACHE=/old/cache\n", encoding="utf-8")
        r = self.install()
        self.assertEqual(self.env_file.read_text(encoding="utf-8"),
                         f"GOPRIVATE=github.com/example\nGOCACHE={self.scratch}/gocache\n"
                         f"GOMODCACHE={self.nvme}/gomodcache\n")
        self.assertIn(f"GOCACHE /old/cache -> {self.scratch}/gocache", r.stdout)
        self.assertIn(f"GOMODCACHE unset -> {self.nvme}/gomodcache", r.stdout)

    def test_a_second_run_changes_nothing(self) -> None:
        self.nvme.mkdir()
        self.install()
        before = self.env_file.stat()
        r = self.install()
        self.assertIn("already names", r.stdout)
        after = self.env_file.stat()
        self.assertEqual((before.st_ino, before.st_mtime_ns), (after.st_ino, after.st_mtime_ns))

    def test_without_the_scratch_disk_the_build_cache_is_on_the_big_disk(self) -> None:
        self.nvme.mkdir()
        self.install()
        self.assertIn(f"GOCACHE={self.nvme}/gocache\n", self.env_file.read_text(encoding="utf-8"))

    def test_a_machine_without_the_big_disk_is_left_alone(self) -> None:
        r = self.install()
        self.assertIn("left alone", r.stdout)
        self.assertFalse(self.env_file.exists())

    def test_a_go_command_with_the_eval_kernels_environment_uses_the_box_caches(self) -> None:
        go = real_go()
        if go is None:
            self.skipTest("no Go toolchain on PATH")
        self.nvme.mkdir()
        self.scratch.mkdir()
        # What omp's Python eval kernel passes on (runtime.ts's allow-list): no GOCACHE, no GOMODCACHE.
        kernel_env = {"HOME": str(self.home), "PATH": os.environ["PATH"], "TMPDIR": str(self.root),
                      "GOTOOLCHAIN": "local"}

        def caches() -> list[str]:
            r = subprocess.run([go, "env", "GOCACHE", "GOMODCACHE"], capture_output=True, text=True,
                               env=kernel_env, cwd=self.root, check=True, timeout=60)
            return r.stdout.split()

        # Without the file such a command uses Go's defaults, which nothing caps.
        self.assertEqual(caches(), [str(self.home / ".cache" / "go-build"), str(self.home / "go" / "pkg" / "mod")])
        self.install()
        self.assertEqual(caches(), [f"{self.scratch}/gocache", f"{self.nvme}/gomodcache"])


if __name__ == "__main__":
    unittest.main()
