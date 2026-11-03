#!/usr/bin/env python3
"""shims/gh runs the gh pinned exactly in mise's global config straight from its install dir when the first gh past
the shim on PATH is mise's own shim. mise's shim is mise itself: it reads its configuration and then execs that gh,
one more process on every call (0.15 s p50, 0.51 s p90 over 20 calls at load ~290 on 2026-11-03). With no exact pin,
no install of it, or a first gh that is not in mise's shims directory, the shim runs the gh it found, as before. The
environment is built from nothing, so no agent context applies and the call goes straight to the gh it resolved."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SHIMS = Path(__file__).parents[1]
GH = SHIMS / "gh"
FAKE = """#!/bin/bash
printf '%s %s\\n' NAME "$*" >> "$GH_LOG"
"""


class MisePin(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.data = self.tmp / "mise"
        self.shims = self.data / "shims"
        self.config = self.tmp / "config.toml"
        self.log = self.tmp / "gh.log"
        self.log.touch()
        self.fake(self.shims / "gh", "mise-shim")
        self.path = [str(SHIMS), str(self.shims), "/usr/bin", "/bin"]

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def fake(self, path, name):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(FAKE.replace("NAME", name))
        path.chmod(0o755)

    def run_gh(self):
        env = {
            "PATH": os.pathsep.join(self.path),
            "HOME": str(self.tmp / "home"),
            "MISE_DATA_DIR": str(self.data),
            "MISE_GLOBAL_CONFIG_FILE": str(self.config),
            "KNIVES_GH_BYPASS": "1",
            "GH_LOG": str(self.log),
        }
        result = subprocess.run([str(GH), "api", "x"], env=env, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.log.read_text().split()[0]

    def test_the_pinned_install_runs_in_place_of_mises_shim(self):
        self.config.write_text('[env]\n_.path = ["x"]\n\n[tools]\nnode = "24.1.0"\ngh = "1.2.3"  # was "latest"\n')
        self.fake(self.data / "installs/gh/1.2.3/gh_1.2.3_linux_amd64/bin/gh", "installed")
        self.assertEqual(self.run_gh(), "installed")

    def test_an_install_with_bin_at_its_top_counts_too(self):
        self.config.write_text('[tools]\n"gh" = "1.2.3"\n')
        self.fake(self.data / "installs/gh/1.2.3/bin/gh", "installed")
        self.assertEqual(self.run_gh(), "installed")

    def test_without_an_install_of_the_pin_mises_shim_runs(self):
        self.config.write_text('[tools]\ngh = "9.9.9"\n')
        self.fake(self.data / "installs/gh/1.2.3/bin/gh", "installed")
        self.assertEqual(self.run_gh(), "mise-shim")

    def test_a_pin_that_is_not_exact_leaves_mises_shim(self):
        self.config.write_text('[tools]\ngh = "latest"\n')
        self.fake(self.data / "installs/gh/latest/bin/gh", "installed")
        self.assertEqual(self.run_gh(), "mise-shim")

    def test_a_gh_key_outside_tools_is_not_the_pin(self):
        self.config.write_text('[settings]\ngh = "1.2.3"\n\n[tools]\nnode = "24.1.0"\n')
        self.fake(self.data / "installs/gh/1.2.3/bin/gh", "installed")
        self.assertEqual(self.run_gh(), "mise-shim")

    def test_a_first_gh_outside_mises_shims_runs_as_found(self):
        self.config.write_text('[tools]\ngh = "1.2.3"\n')
        self.fake(self.data / "installs/gh/1.2.3/bin/gh", "installed")
        other = self.tmp / "other"
        self.fake(other / "gh", "other")
        self.path.insert(1, str(other))
        self.assertEqual(self.run_gh(), "other")


if __name__ == "__main__":
    unittest.main()
