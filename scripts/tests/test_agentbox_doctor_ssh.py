#!/usr/bin/env python3
"""doctor accepts the host's shared SSH state without an invented key allow-list."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
AGENTBOX = DOTFILES / "scripts" / "agentbox"


def ssh_block() -> str:
    script = subprocess.run(
        ["bash", "-c", f'source "{AGENTBOX}" && printf "%s" "$DOCTOR_BOX_SCRIPT"'],
        check=True, capture_output=True, text=True,
    ).stdout
    lines = script.splitlines()
    helpers = [line for line in lines if line.startswith(("ok() ", "fail() "))]
    start = next(i for i, line in enumerate(lines) if line.startswith("# SSH state is shared"))
    end = start + 1
    return "\n".join(["fails=0", *helpers, *lines[start:end + 1]]) + "\n"


class DoctorSshTest(unittest.TestCase):
    def run_check(self, files: list[str]) -> str:
        with tempfile.TemporaryDirectory() as home:
            ssh = Path(home) / ".ssh"
            ssh.mkdir()
            for name in files:
                (ssh / name).touch()
            env = dict(os.environ, HOME=home, DOTFILES_DIR=str(DOTFILES))
            return subprocess.run(["bash"], input=ssh_block(), env=env, check=True,
                                  capture_output=True, text=True).stdout

    def test_host_key_files_pass(self) -> None:
        out = self.run_check(["jj-signing", "jj-signing.pub", "google_compute_engine",
                              "google_compute_engine.pub", "sami-rocqperf", "sami-rocqperf.pub",
                              "known_hosts.sami-rocqperf"])
        self.assertIn("ok   box: .ssh is shared host state", out)
        self.assertNotIn("FAIL", out)

    def test_other_host_keys_are_not_rejected(self) -> None:
        out = self.run_check(["jj-signing", "jj-signing.pub", "id_ed25519"])
        self.assertNotIn("FAIL", out)

    def test_mounts_share_the_whole_ssh_directory_writable(self) -> None:
        mounts = (DOTFILES / "agentbox" / "mounts").read_text(encoding="utf-8").splitlines()
        self.assertIn("rw .ssh", mounts)
        self.assertFalse(any(line.startswith("ro .ssh/") for line in mounts))


if __name__ == "__main__":
    unittest.main()
