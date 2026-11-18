#!/usr/bin/env python3
"""scripts/agent-session names omp/config.probe.yml only for an omp that holds the probe's fix.

omp/config.yml keeps task.completionProbe false: a running session watches every file its
PI_CONFIG_FILES names and reads the key at each subagent start, so true there turns the unfixed
probe on in each session of an older release (dispatch://INFRA-4027). omp/config.probe.yml sets it
true, and agent-session names it, right after omp/config.yml and before the machine's
config.local.yml, only when the omp it is about to run is a release stamped 20261117-053702 or
later, read from the version directory of the binary's real path. No stamp, or one it cannot read,
gets no file. A list without omp/config.yml, or a checkout without the file, gets none either: omp
refuses to start on an overlay that does not exist ("Config overlay not found").

Technique, as test_agent_session_moved.py: a scratch DOTFILES_DIR whose agent-secrets-session execs
its argv, and an agent named `omp` at a path shaped as mise installs it
(<installs>/<tool>/<version>/bin/omp) that prints its PI_CONFIG_FILES. AGENT_SESSION_SCRIPT names
another copy of the script, as test_agent_session_gotmp.py allows.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
SCRIPT = Path(os.environ.get("AGENT_SESSION_SCRIPT") or DOTFILES / "scripts" / "agent-session")
FIXED = "18.18.6-sami.20261117-053702"


def executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class AgentSessionProbeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="agent-session-probe-"))
        self.addCleanup(shutil.rmtree, self.root)
        dotfiles = self.root / "dotfiles"
        (dotfiles / "shims").mkdir(parents=True)
        (dotfiles / "agent-spend").mkdir()
        executable(dotfiles / "scripts" / "agent-secrets-session", 'exec "$@"')
        executable(dotfiles / "scripts" / "gh-agent-config", "echo /nonexistent")
        (dotfiles / "scripts" / "agent-spend-env.sh").write_text("agent_spend_env() { :; }\n", encoding="utf-8")
        omp = dotfiles / "omp"
        omp.mkdir()
        self.config, self.probe, self.local = omp / "config.yml", omp / "config.probe.yml", omp / "config.local.yml"
        for path in (self.config, self.probe, self.local):
            path.write_text("task: {}\n", encoding="utf-8")
        self.caller = self.root / "caller.yml"
        # What shims/omp hands agent-session: config.yml, the machine's file, a caller's own overlay.
        self.built = [str(self.config), str(self.local), str(self.caller)]
        self.installs = self.root / "installs" / "github-sjawhar-oh-my-pi"
        self.env = {
            "PATH": os.environ["PATH"],
            "HOME": str(self.root),
            "DOTFILES_DIR": str(dotfiles),
            "XDG_CONFIG_HOME": str(self.root / "config"),
        }

    def agent(self, version: str, name: str = "omp") -> Path:
        """An agent at <installs>/<tool>/VERSION/bin/NAME that prints the list it was started with."""
        path = self.installs / version / "bin" / name
        executable(path, 'printf "PI_CONFIG_FILES=%s\\n" "${PI_CONFIG_FILES-unset}"')
        return path

    def launch(self, agent: Path, files: list[str] | None = None) -> list[str] | None:
        """The PI_CONFIG_FILES the agent saw, as a list (None when unset), started with FILES."""
        env = dict(self.env)
        if files is not None:
            env["PI_CONFIG_FILES"] = ":".join(files)
        result = subprocess.run([str(SCRIPT), str(agent)], env=env, cwd=self.root,
                                capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        [line] = [line for line in result.stdout.splitlines() if line.startswith("PI_CONFIG_FILES=")]
        value = line.removeprefix("PI_CONFIG_FILES=")
        return None if value == "unset" else value.split(":") if value else []

    def test_a_release_with_the_fix_gets_the_file_after_config_yml_and_before_the_local_file(self) -> None:
        for version in (FIXED, "18.18.6-sami.20261117-053703", "18.19.0-sami.20261201-000000"):
            with self.subTest(version=version):
                self.assertEqual(self.launch(self.agent(version), self.built),
                                 [str(self.config), str(self.probe), str(self.local), str(self.caller)])

    def test_an_older_release_or_a_version_it_cannot_read_gets_no_file(self) -> None:
        for version in ("18.18.6-sami.20261110-032522", "18.18.6-sami.20261117-053701", "18.18.6",
                        "18.18.6-sami.latest", "18.18.6-sami.2026111705370", "18.18.6-sami.20261117-05370",
                        "v18.18.6-sami.20261117-053702", "18.18.6-sami.20261117-053702-dirty", "oh-my-pi"):
            with self.subTest(version=version):
                self.assertEqual(self.launch(self.agent(version), self.built), self.built)

    def test_an_inherited_entry_is_dropped_for_an_older_release_and_named_once_for_a_fixed_one(self) -> None:
        # A nested launch inherits its parent's list, which names the file when the parent holds the fix.
        inherited = [str(self.config), str(self.probe), str(self.local)]
        self.assertEqual(self.launch(self.agent("18.18.6-sami.20261110-032522"), inherited),
                         [str(self.config), str(self.local)])
        self.assertEqual(self.launch(self.agent(FIXED), inherited), inherited)

    def test_a_link_counts_by_the_release_it_points_at(self) -> None:
        fixed, old = self.agent(FIXED).parent.parent, self.agent("18.18.6-sami.20261110-032522").parent.parent
        # mise's own aliases (18.18, latest) are links to the version directory.
        (self.installs / "18.18").symlink_to(fixed.name)
        (self.installs / "18.18.6-sami.20991231-000000").symlink_to(old.name)
        self.assertEqual(self.launch(self.installs / "18.18" / "bin" / "omp", self.built),
                         [str(self.config), str(self.probe), str(self.local), str(self.caller)])
        self.assertEqual(self.launch(self.installs / "18.18.6-sami.20991231-000000" / "bin" / "omp", self.built),
                         self.built)

    def test_no_file_without_config_yml_in_the_list_or_in_the_checkout(self) -> None:
        agent = self.agent(FIXED)
        self.assertEqual(self.launch(agent, [str(self.caller)]), [str(self.caller)])
        self.assertIsNone(self.launch(agent))
        self.probe.unlink()
        self.assertEqual(self.launch(agent, self.built), self.built)

    def test_another_agent_keeps_its_list(self) -> None:
        inherited = [str(self.config), str(self.probe), str(self.local)]
        self.assertEqual(self.launch(self.agent(FIXED, name="claude"), self.built), self.built)
        self.assertEqual(self.launch(self.agent("18.18.6-sami.20261110-032522", name="claude"), inherited), inherited)


if __name__ == "__main__":
    unittest.main()
