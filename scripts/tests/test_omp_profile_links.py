#!/usr/bin/env python3
"""shims/omp under a named profile: the profile's symlinks track the installer's in ~/.omp."""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
SHIM = DOTFILES / "shims" / "omp"


def write_stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class OmpProfileLinks(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.home = root / "home"
        (self.home / ".config").mkdir(parents=True)
        self.targets = root / "dotfiles-omp"
        for name in ("plugins", "agents", "hooks", "prompts"):
            (self.targets / name).mkdir(parents=True)
        for name in ("mcp.json", "viewport.ts"):
            (self.targets / name).write_text("", encoding="utf-8")
        self.omp = self.home / ".omp"
        agent = self.omp / "agent"
        (agent / "extensions").mkdir(parents=True)
        (self.omp / "plugins").symlink_to(self.targets / "plugins")
        for name in ("agents", "hooks", "prompts", "mcp.json"):
            (agent / name).symlink_to(self.targets / name)
        (agent / "extensions" / "viewport.ts").symlink_to(self.targets / "viewport.ts")
        for name in ("config.yml", "models.yml", "agent.db"):
            (agent / name).write_text("{}", encoding="utf-8")
        stubs = root / "bin"
        stubs.mkdir()
        # `mise x github:sjawhar/oh-my-pi -- …omp` runs the command with the release's install
        # dir on PATH; the default profile's command is scripts/omp-no-provider-keys, which takes
        # the bare `omp` from that dir.
        tool = self.home / ".mise" / "installs" / "github-sjawhar-oh-my-pi" / "1.0.0" / "bin"
        tool.mkdir(parents=True)
        write_stub(tool, "omp", 'echo "AGENT omp $*"')
        write_stub(stubs, "mise", f'shift 3; PATH="{tool}:$PATH" exec "$@"')
        write_stub(stubs, "secrets", "exit 0")
        self.env = {
            "DOTFILES_DIR": str(DOTFILES),
            "HOME": str(self.home),
            "PATH": f"{stubs}:/usr/bin:/bin",
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "NATS_NKEY_SEED_FILE": "/dev/null",
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def omp_run(self, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(SHIM), *args], capture_output=True, text=True, env={**self.env, **extra}, check=False, timeout=60
        )

    def assert_launched(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("AGENT omp", result.stdout)

    def assert_mirrors_canonical(self, profile: Path) -> None:
        self.assertEqual(os.readlink(profile / "plugins"), str(self.targets / "plugins"))
        for name in ("agents", "hooks", "prompts", "mcp.json"):
            self.assertEqual(os.readlink(profile / "agent" / name), str(self.targets / name))
        self.assertEqual(os.readlink(profile / "agent/extensions/viewport.ts"), str(self.targets / "viewport.ts"))

    def test_named_profile_gets_every_installer_link(self) -> None:
        for args, extra in ((("--profile", "theorem"), {}), ((), {"OMP_PROFILE": "theorem"})):
            with self.subTest(args=args, env=extra):
                self.assert_launched(self.omp_run(*args, **extra))
                profile = self.omp / "profiles" / "theorem"
                self.assert_mirrors_canonical(profile)
                for name in ("config.yml", "models.yml", "agent.db"):
                    self.assertFalse(os.path.lexists(profile / "agent" / name), name)

    def test_profile_follows_the_installer_and_keeps_its_own_files(self) -> None:
        agent = self.omp / "profiles" / "theorem" / "agent"
        (agent / "extensions").mkdir(parents=True)
        (agent / "agents").symlink_to(self.targets / "old-agents")
        (agent / "extensions" / "retired.ts").symlink_to(self.targets / "retired.ts")
        (agent / "models.yml").symlink_to(self.omp / "agent" / "models.yml")
        (agent / "mcp.json").write_text("the profile's own", encoding="utf-8")
        (agent / "agent.db").write_text("state", encoding="utf-8")
        self.assert_launched(self.omp_run("--profile", "theorem"))
        self.assertEqual(os.readlink(agent / "agents"), str(self.targets / "agents"))
        self.assertFalse(os.path.lexists(agent / "extensions" / "retired.ts"))
        self.assertFalse(os.path.lexists(agent / "models.yml"))
        self.assertEqual((agent / "mcp.json").read_text(encoding="utf-8"), "the profile's own")
        self.assertEqual((agent / "agent.db").read_text(encoding="utf-8"), "state")

    def test_default_profile_creates_nothing(self) -> None:
        for args, extra in (((), {}), (("--profile", "default"), {}), ((), {"OMP_PROFILE": ""})):
            with self.subTest(args=args, env=extra):
                self.assert_launched(self.omp_run(*args, **extra))
        self.assertFalse((self.omp / "profiles").exists())

    def test_bad_profile_name_refuses_before_omp_runs(self) -> None:
        for name in ("..", ".hidden", "a/b"):
            with self.subTest(name=name):
                result = self.omp_run("--profile", name)
                self.assertEqual(result.returncode, 2)
                self.assertNotIn("AGENT", result.stdout)
        self.assertFalse((self.omp / "profiles").exists())


if __name__ == "__main__":
    unittest.main()
