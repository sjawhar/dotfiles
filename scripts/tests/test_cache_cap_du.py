#!/usr/bin/env python3
"""devbox/nvme-cache-cap and devbox/gocache-cap size a cache that is written while du walks it.

On 2026-11-02 at 20:39Z nvme-cache-cap's hourly run failed: uv removed two `.tmp*` entries under
builds-v0 while `du -sb` walked the cache, du printed its total but exited 1, and `set -euo
pipefail` ended the run, so no cache was checked against its cap until the next hour.

A vanished entry must not fail a run; any other du error (an unreadable directory, say) still must.

Technique: a `du` stub first on PATH prints the given total for the directory it is asked about,
writes the given error lines to stderr, and exits with the given status, as GNU du does.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

DEVBOX = Path(__file__).resolve().parents[2] / "devbox"

DU_STUB = r"""#!/bin/bash
dir="${!#}"
printf '%s' "${STUB_DU_ERR:-}" >&2
printf '%s\t%s\n' "${STUB_DU_SIZE:-1000}" "$dir"
exit "${STUB_DU_RC:-0}"
"""

VANISHED = "du: cannot access '{d}/builds-v0/.tmp1Yu7b1': No such file or directory\n"
UNREADABLE = "du: cannot read directory '{d}/builds-v0/locked': Permission denied\n"


class CacheCapDu(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        stubs = self.root / "bin"
        stubs.mkdir()
        du = stubs / "du"
        du.write_text(DU_STUB, encoding="utf-8")
        du.chmod(0o755)
        self.nvme = self.root / "nvme"
        (self.nvme / "cache" / "uv").mkdir(parents=True)
        self.gocache = self.root / "gocache"
        self.gocache.mkdir()
        self.env = {"HOME": str(self.root), "PATH": f"{stubs}:{os.environ['PATH']}", "TMPDIR": str(self.root)}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_unit(self, name: str, err: str, rc: int) -> subprocess.CompletedProcess:
        env = dict(self.env, STUB_DU_ERR=err, STUB_DU_RC=str(rc), STUB_DU_SIZE="1000",
                   NVME_CACHE_ROOT=str(self.nvme), SCRATCH_CACHE_ROOT=str(self.root / "none"),
                   GOCACHE_CAP_DIR=str(self.gocache))
        return subprocess.run([str(DEVBOX / name), "--dry-run"] if name == "nvme-cache-cap" else [str(DEVBOX / name)],
                              capture_output=True, text=True, env=env, check=False, timeout=60)

    def test_nvme_cache_cap_takes_the_total_when_an_entry_vanishes(self) -> None:
        r = self.run_unit("nvme-cache-cap", VANISHED.format(d=self.nvme / "cache" / "uv"), 1)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"nvme-cache-cap: uv {self.nvme}/cache/uv is 1000 bytes", r.stdout)

    def test_nvme_cache_cap_still_fails_on_another_du_error(self) -> None:
        r = self.run_unit("nvme-cache-cap", UNREADABLE.format(d=self.nvme / "cache" / "uv"), 1)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Permission denied", r.stderr)

    def test_gocache_cap_takes_the_total_when_an_entry_vanishes(self) -> None:
        r = self.run_unit("gocache-cap", VANISHED.format(d=self.gocache), 1)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"gocache-cap: {self.gocache} is 1000 bytes, at or under the trim size", r.stdout)

    def test_gocache_cap_still_fails_on_another_du_error(self) -> None:
        r = self.run_unit("gocache-cap", UNREADABLE.format(d=self.gocache), 1)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Permission denied", r.stderr)


if __name__ == "__main__":
    unittest.main()
