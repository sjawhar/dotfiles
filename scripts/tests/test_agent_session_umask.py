#!/usr/bin/env python3
"""No agent session makes group- or other-writable files, whatever umask its launcher had.

A tmux server started under the systemd user manager has the manager's 0002 (pam_umask with
USERGROUPS_ENAB), one started another way 0022. After sami-devbox's reset on 2026-11-25
(dispatch://INFRA-5874) every host session had 0002, and tests that refuse a group-writable file
failed on their own fixtures. scripts/agent-session adds 022 to the caller's mask and keeps a
stricter one.

Run: `PYTHONPATH=. uvx pytest scripts/tests/test_agent_session_umask.py`
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
SCRIPT = DOTFILES / "scripts" / "agent-session"
USER = "agent-session-umask-test-user"
PROBE = 'printf "UMASK=%s\\n" "$(umask)"; : > "$1/made-by-the-agent"'


def executable(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class AgentSessionUmask(unittest.TestCase):
    root: Path
    agent: Path
    base: dict[str, str]

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="agent-session-umask-"))
        self.addCleanup(shutil.rmtree, self.root)
        dotfiles = self.root / "dotfiles"
        (dotfiles / "scripts").mkdir(parents=True)
        (dotfiles / "shims").mkdir()
        executable(dotfiles / "scripts" / "agent-secrets-session", 'exec "$@"')
        if (DOTFILES / "scripts" / "agent-spend-env.sh").exists():
            shutil.copy(DOTFILES / "scripts" / "agent-spend-env.sh", dotfiles / "scripts" / "agent-spend-env.sh")
        nvme = self.root / "nvme" / USER
        nvme.mkdir(parents=True)
        self.agent = self.root / "omp"
        executable(self.agent, PROBE)
        self.base = {
            "PATH": os.environ["PATH"],
            "HOME": str(self.root),
            "USER": USER,
            "DOTFILES_DIR": str(dotfiles),
            "XDG_CONFIG_HOME": str(self.root / "config"),
            "XDG_CACHE_HOME": str(self.root / "cache"),
            "AGENT_SESSION_NVME_HOME": str(nvme),
        }

    def run_under(self, mask: int, argv: list[str]) -> tuple[str, int]:
        out = self.root / "out"
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir()
        result = subprocess.run([*argv, str(out)], env=self.base, cwd=self.root, capture_output=True, text=True,
                                timeout=300, check=False, preexec_fn=lambda: os.umask(mask))
        self.assertEqual(result.returncode, 0, result.stderr)
        mode = stat.S_IMODE((out / "made-by-the-agent").stat().st_mode)
        return result.stdout, mode

    def test_a_group_writable_launcher_mask_becomes_0022(self) -> None:
        direct, direct_mode = self.run_under(0o002, [str(self.agent)])
        self.assertIn("UMASK=0002", direct.splitlines(), "the control did not see 0002, so the check below could not fail")
        self.assertEqual(direct_mode, 0o664)
        out, mode = self.run_under(0o002, [str(SCRIPT), str(self.agent)])
        self.assertIn("UMASK=0022", out.splitlines())
        self.assertEqual(mode, 0o644)

    def test_a_stricter_launcher_mask_is_kept(self) -> None:
        out, mode = self.run_under(0o077, [str(SCRIPT), str(self.agent)])
        self.assertIn("UMASK=0077", out.splitlines())
        self.assertEqual(mode, 0o600)


if __name__ == "__main__":
    unittest.main()
