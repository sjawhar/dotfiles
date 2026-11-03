#!/usr/bin/env python3
"""scripts/agent-session gives each agent session a GOTMPDIR of its own.

On 2026-11-03 at 16:20:04Z one session's cleanup, `rm -rf /mnt/nvme/sami/gotmp/go-build*`, deleted
the live build tree of another session's `go test` in the box-wide GOTMPDIR. Each session now builds
in <its AGENT_SESSION_SCRATCH>/gotmp, which session-scratch-reap removes with the scratch directory.
The box-wide value every shell inherits counts as unset; any other value a caller set wins; GOCACHE
stays shared.

Technique: AGENT_SESSION_NVME_HOME points the script at a scratch tree, USER is a name with no
/mnt/scratch/<user>, agent-secrets-session is a stub that execs its argv, and the agent prints its
environment.
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
USER = "agent-session-gotmp-test-user"


def executable(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class AgentSessionGotmpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="agent-session-gotmp-"))
        self.addCleanup(shutil.rmtree, self.root)
        dotfiles = self.root / "dotfiles"
        (dotfiles / "scripts").mkdir(parents=True)
        (dotfiles / "shims").mkdir()
        (dotfiles / "agent-spend").mkdir()
        executable(dotfiles / "scripts" / "agent-secrets-session", 'exec "$@"')
        if (DOTFILES / "scripts" / "agent-spend-env.sh").exists():
            shutil.copy(DOTFILES / "scripts" / "agent-spend-env.sh", dotfiles / "scripts" / "agent-spend-env.sh")
        self.nvme = self.root / "nvme" / USER
        self.nvme.mkdir(parents=True)
        self.agent = self.root / "omp"
        executable(
            self.agent,
            'printf "%s\\n" "GOTMPDIR=${GOTMPDIR-}" "GOCACHE=${GOCACHE-}" '
            '"AGENT_SESSION_SCRATCH=${AGENT_SESSION_SCRATCH-}" "TMPDIR=${TMPDIR-}"',
        )
        self.base = {
            "PATH": os.environ["PATH"],
            "HOME": str(self.root),
            "USER": USER,
            "DOTFILES_DIR": str(dotfiles),
            "XDG_CONFIG_HOME": str(self.root / "config"),
            "XDG_CACHE_HOME": str(self.root / "cache"),
            "AGENT_SESSION_NVME_HOME": str(self.nvme),
        }

    def run_agent(self, env: dict[str, str] | None = None) -> dict[str, str]:
        result = subprocess.run(
            [str(SCRIPT), str(self.agent)],
            env={**self.base, **(env or {})},
            cwd=self.root,
            capture_output=True,
            text=True,
            check=True,
        )
        return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)

    def test_two_sessions_get_different_gotmpdirs_under_their_own_scratch(self) -> None:
        shared = str(self.nvme / "gotmp")
        first = self.run_agent({"GOTMPDIR": shared})
        second = self.run_agent({"GOTMPDIR": shared})
        self.assertNotEqual(first["GOTMPDIR"], second["GOTMPDIR"])
        for out in (first, second):
            self.assertEqual(out["GOTMPDIR"], out["AGENT_SESSION_SCRATCH"] + "/gotmp")
            self.assertTrue(out["AGENT_SESSION_SCRATCH"].startswith(str(self.nvme / "session-tmp") + "/"))
            self.assertTrue(Path(out["GOTMPDIR"]).is_dir())

    def test_an_unset_gotmpdir_also_gets_the_sessions_own(self) -> None:
        out = self.run_agent()
        self.assertEqual(out["GOTMPDIR"], out["AGENT_SESSION_SCRATCH"] + "/gotmp")

    def test_a_value_the_caller_set_wins(self) -> None:
        own = self.root / "my-gotmp"
        own.mkdir()
        out = self.run_agent({"GOTMPDIR": str(own)})
        self.assertEqual(out["GOTMPDIR"], str(own))

    def test_a_nested_launch_keeps_its_parent_sessions_gotmpdir(self) -> None:
        parent = self.run_agent()
        child = self.run_agent(
            {"AGENT_SESSION_SCRATCH": parent["AGENT_SESSION_SCRATCH"], "GOTMPDIR": parent["GOTMPDIR"]}
        )
        self.assertEqual(child["GOTMPDIR"], parent["GOTMPDIR"])
        self.assertEqual(child["AGENT_SESSION_SCRATCH"], parent["AGENT_SESSION_SCRATCH"])

    def test_gocache_stays_shared(self) -> None:
        first, second = self.run_agent(), self.run_agent()
        self.assertEqual(first["GOCACHE"], second["GOCACHE"])
        self.assertEqual(first["GOCACHE"], str(self.nvme / "gocache"))


if __name__ == "__main__":
    unittest.main()
