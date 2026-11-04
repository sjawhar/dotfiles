#!/usr/bin/env python3
"""Manifest skill pools must reach native discovery before extensions load."""

import json
import os
import shutil
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "omp-skills-config"


class SkillConfig(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "home"
        self.dots = self.home / "dots"
        self.dots.mkdir(parents=True)
        self.output = self.home / ".cache/omp/dotfiles-skills.yml"

    def generate(self, sources):
        (self.dots / "skills-sources.json").write_text(json.dumps({"sources": sources}))
        return subprocess.run(
            ["python3", str(SCRIPT)],
            env={**os.environ, "HOME": str(self.home), "DOTFILES_DIR": str(self.dots)},
            text=True, capture_output=True, check=False,
        )

    def test_relative_override_disabled_and_missing_pools(self):
        first = self.dots / "vendor/first/skills"
        other = self.home / "src/custom/skills"
        first.mkdir(parents=True)
        other.mkdir(parents=True)
        result = self.generate([
            {"root": "vendor/first"},
            {"root": "unused", "ompSkillsDir": "~/src/custom/skills"},
            {"root": "vendor/first", "omp": False},
            {"root": "missing"},
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(self.output))
        self.assertEqual(json.loads(self.output.read_text()), {
            "skills": {"customDirectories": [str(first), str(other)]},
        })

    def test_new_pool_reaches_next_launch_without_rewriting_unchanged_config(self):
        pool = self.dots / "vendor/first/skills"
        pool.mkdir(parents=True)
        self.assertEqual(self.generate([{"root": "vendor/first"}]).returncode, 0)
        before = self.output.stat().st_mtime_ns
        self.assertEqual(self.generate([{"root": "vendor/first"}]).returncode, 0)
        self.assertEqual(self.output.stat().st_mtime_ns, before)
        second = self.dots / "vendor/second/skills"
        second.mkdir(parents=True)
        self.assertEqual(self.generate([{"root": "vendor/first"}, {"root": "vendor/second"}]).returncode, 0)
        self.assertEqual(json.loads(self.output.read_text())["skills"]["customDirectories"], [str(pool), str(second)])

    def test_launcher_loads_native_overlay_once_and_keeps_caller_precedence(self):
        repository = SCRIPT.parent.parent
        (self.dots / "scripts").mkdir()
        (self.dots / "omp").mkdir()
        (self.dots / "omp/config.yml").write_text("{}\n")
        shutil.copy2(SCRIPT, self.dots / "scripts/omp-skills-config")
        agent = self.dots / "scripts/agent-session"
        agent.write_text('#!/bin/bash\nprintf "%s\\n" "$PI_CONFIG_FILES"\n')
        agent.chmod(0o755)
        binary = self.home / "bin"
        binary.mkdir()
        mise = binary / "mise"
        mise.write_text('#!/bin/bash\nexit 0\n')
        mise.chmod(0o755)
        self.generate([])
        caller = str(self.home / "caller.yml")
        result = subprocess.run(
            ["bash", str(repository / "shims/omp")],
            env={**os.environ, "HOME": str(self.home), "DOTFILES_DIR": str(self.dots),
                 "PATH": f"{binary}:/usr/bin:/bin", "OMP_MEMORY_SCOPE": "test",
                 "OMP_PROFILE": "", "PI_PROFILE": "", "PI_CONFIG_FILES": f"{self.output}:{caller}"},
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        overlays = result.stdout.strip().split(":")
        self.assertEqual(overlays.count(str(self.output)), 1)
        self.assertEqual(overlays[-1], caller)
        self.assertIn(str(self.dots / "omp/config.yml"), overlays)


if __name__ == "__main__":
    unittest.main()
