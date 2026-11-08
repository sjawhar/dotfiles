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
        # `mise env -s bash` puts the pinned release's install dir on PATH.
        tool = self.home / ".mise" / "installs" / "github-sjawhar-oh-my-pi" / "1.0.0" / "bin"
        tool.mkdir(parents=True)
        # The stub omp prints the profile variables it was started with.
        write_stub(tool, "omp", 'echo "AGENT omp $*"\necho "PROFILE ${OMP_PROFILE-unset} ${OMP_IN_DEFAULT_PROFILE-unset}"')
        write_stub(stubs, "mise", f"echo 'export PATH=\"{tool}:$PATH\"'")
        self.env = {
            "DOTFILES_DIR": str(DOTFILES),
            "HOME": str(self.home),
            "PATH": f"{stubs}:/usr/bin:/bin",
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            # No memory scope: with no user manager here each launch would wait out the shim's
            # 10 s scope retry, and the memory ceiling has its own test.
            "OMP_MEMORY_SCOPE": "off",
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

    def launched_env(self, *args: str, **extra: str) -> tuple[str, str]:
        """OMP_PROFILE and OMP_IN_DEFAULT_PROFILE as omp saw them ("unset" when absent)."""
        result = self.omp_run(*args, **extra)
        self.assert_launched(result)
        [line] = [line for line in result.stdout.splitlines() if line.startswith("PROFILE ")]
        omp_profile, in_default = line.split(" ")[1:]
        return omp_profile, in_default

    def assert_mirrors_canonical(self, profile: Path) -> None:
        self.assertEqual(os.readlink(profile / "plugins"), str(self.targets / "plugins"))
        for name in ("agents", "hooks", "prompts"):
            self.assertEqual(os.readlink(profile / "agent" / name), str(self.targets / name))
        # A shared config file is a copy, so a write to the profile's file cannot reach the shared one.
        self.assertFalse(os.path.islink(profile / "agent" / "mcp.json"))
        self.assertEqual((profile / "agent" / "mcp.json").read_bytes(), (self.targets / "mcp.json").read_bytes())
        # Extension code stays linked: a copy would resolve its imports from the profile.
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

    def test_a_write_to_the_profiles_mcp_json_does_not_reach_the_shared_file(self) -> None:
        """2026-11-01: a probe harness wrote `{ "mcpServers": {} }` to its profile's agent/mcp.json,
        a link to the shared file, and every session on the box started with no MCP servers."""
        (self.targets / "mcp.json").write_text('{"mcpServers": {"slack": {}}}', encoding="utf-8")
        agent = self.omp / "profiles" / "probe" / "agent"
        self.assert_launched(self.omp_run("--profile", "probe"))
        (agent / "mcp.json").write_text('{ "mcpServers": {} }\n', encoding="utf-8")
        self.assertEqual((self.targets / "mcp.json").read_text(encoding="utf-8"), '{"mcpServers": {"slack": {}}}')
        self.assert_launched(self.omp_run("--profile", "probe"))
        self.assertEqual((agent / "mcp.json").read_text(encoding="utf-8"), '{ "mcpServers": {} }\n')
        self.assertEqual((self.targets / "mcp.json").read_text(encoding="utf-8"), '{"mcpServers": {"slack": {}}}')

    def test_the_copy_follows_the_shared_file_until_the_profile_changes_it(self) -> None:
        agent = self.omp / "profiles" / "theorem" / "agent"
        (self.targets / "mcp.json").write_text("v1", encoding="utf-8")
        self.assert_launched(self.omp_run("--profile", "theorem"))
        (self.targets / "mcp.json").write_text("v2", encoding="utf-8")
        self.assert_launched(self.omp_run("--profile", "theorem"))
        self.assertEqual((agent / "mcp.json").read_text(encoding="utf-8"), "v2")
        (agent / "mcp.json").write_text("the profile's own", encoding="utf-8")
        (self.targets / "mcp.json").write_text("v3", encoding="utf-8")
        self.assert_launched(self.omp_run("--profile", "theorem"))
        self.assertEqual((agent / "mcp.json").read_text(encoding="utf-8"), "the profile's own")

    def test_an_old_link_to_the_shared_file_becomes_a_copy(self) -> None:
        agent = self.omp / "profiles" / "theorem" / "agent"
        agent.mkdir(parents=True)
        (self.targets / "mcp.json").write_text("shared", encoding="utf-8")
        (agent / "mcp.json").symlink_to(self.targets / "mcp.json")
        self.assert_launched(self.omp_run("--profile", "theorem"))
        self.assertFalse(os.path.islink(agent / "mcp.json"))
        self.assertEqual((agent / "mcp.json").read_text(encoding="utf-8"), "shared")
        (agent / "mcp.json").write_text("written", encoding="utf-8")
        self.assertEqual((self.targets / "mcp.json").read_text(encoding="utf-8"), "shared")

    def test_a_copy_whose_shared_file_is_gone_is_removed_unless_changed(self) -> None:
        agent = self.omp / "profiles" / "theorem" / "agent"
        self.assert_launched(self.omp_run("--profile", "theorem"))
        self.assertTrue((agent / "mcp.json").is_file())
        (self.omp / "agent" / "mcp.json").unlink()
        self.assert_launched(self.omp_run("--profile", "theorem"))
        self.assertFalse(os.path.lexists(agent / "mcp.json"))

    def test_a_profiles_envoy_json_is_its_own(self) -> None:
        """A named profile reads Dispatch and Envoy from its own agent dir's envoy.json, and has
        none without one: the shim neither prunes a profile's envoy.json link (one to the neutral
        file is how a profile gets them) nor gives a profile the default agent dir's."""
        neutral = self.home / ".config" / "legion" / "envoy.json"
        neutral.parent.mkdir(parents=True)
        neutral.write_text("{}", encoding="utf-8")
        own = self.omp / "profiles" / "legion" / "agent" / "envoy.json"
        own.parent.mkdir(parents=True)
        own.symlink_to(neutral)
        self.assert_launched(self.omp_run("--profile", "legion"))
        self.assertEqual(os.readlink(own), str(neutral))
        (self.omp / "agent" / "envoy.json").symlink_to(neutral)
        self.assert_launched(self.omp_run("--profile", "theorem"))
        self.assertFalse(os.path.lexists(self.omp / "profiles" / "theorem" / "agent" / "envoy.json"))

    def test_default_profile_creates_nothing(self) -> None:
        for args, extra in (((), {}), (("--profile", "default"), {}), ((), {"OMP_PROFILE": ""})):
            with self.subTest(args=args, env=extra):
                self.assert_launched(self.omp_run(*args, **extra))
        self.assertFalse((self.omp / "profiles").exists())

    def test_a_machine_file_names_the_profile_of_a_bare_launch(self) -> None:
        """oryx, Sami's personal machine, holds `personal` in $XDG_CONFIG_HOME/omp/profile."""
        (self.home / ".config" / "omp").mkdir()
        (self.home / ".config" / "omp" / "profile").write_text("theorem\n", encoding="utf-8")
        self.assertEqual(self.launched_env(), ("theorem", "unset"))
        self.assert_mirrors_canonical(self.omp / "profiles" / "theorem")
        # A flag or a defined variable still wins over the file.
        self.assertEqual(self.launched_env("--profile", "other"), ("unset", "unset"))
        self.assertEqual(self.launched_env(OMP_PROFILE="other"), ("other", "unset"))
        self.assertEqual(self.launched_env(PI_PROFILE="other"), ("unset", "unset"))
        self.assertTrue((self.omp / "profiles" / "other").is_dir())

    def test_the_default_profile_stays_reachable_beside_a_machine_file(self) -> None:
        (self.home / ".config" / "omp").mkdir()
        (self.home / ".config" / "omp" / "profile").write_text("theorem\n", encoding="utf-8")
        # omp drops OMP_PROFILE in a default-profile session, so the marker is what keeps an omp
        # that session starts with no profile named in the default.
        for args, extra in ((("--profile", "default"), {}), (("--profile=default",), {}), ((), {"OMP_PROFILE": ""}),
                            ((), {"OMP_PROFILE": "default"}), ((), {"OMP_IN_DEFAULT_PROFILE": "1"})):
            with self.subTest(args=args, env=extra):
                omp_profile, in_default = self.launched_env(*args, **extra)
                self.assertEqual(in_default, "1")
                self.assertNotEqual(omp_profile, "theorem")
        self.assertFalse((self.omp / "profiles").exists())
        # Naming a profile clears the marker for that session's own children.
        self.assertEqual(self.launched_env("--profile", "theorem", OMP_IN_DEFAULT_PROFILE="1"), ("unset", "unset"))

    def test_a_machine_without_the_file_exports_nothing_new(self) -> None:
        for args in ((), ("--profile", "default")):
            with self.subTest(args=args):
                self.assertEqual(self.launched_env(*args), ("unset", "unset"))
        self.assertFalse((self.omp / "profiles").exists())

    def test_a_bad_name_in_the_machine_file_refuses_before_omp_runs(self) -> None:
        (self.home / ".config" / "omp").mkdir()
        (self.home / ".config" / "omp" / "profile").write_text("../x\n", encoding="utf-8")
        result = self.omp_run()
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("AGENT", result.stdout)

    def test_bad_profile_name_refuses_before_omp_runs(self) -> None:
        for name in ("..", ".hidden", "a/b"):
            with self.subTest(name=name):
                result = self.omp_run("--profile", name)
                self.assertEqual(result.returncode, 2)
                self.assertNotIn("AGENT", result.stdout)
        self.assertFalse((self.omp / "profiles").exists())


if __name__ == "__main__":
    unittest.main()
