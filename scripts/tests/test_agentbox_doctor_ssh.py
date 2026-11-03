#!/usr/bin/env python3
"""doctor's box check of ~/.ssh: only the files agentbox/mounts names under .ssh/ (dispatch://INFRA-734).

A session moved to sami-devbox runs scripts on sami-rocqperf over ssh, with the key
.ssh/sami-rocqperf that agentbox/mounts gives every box where the host has it. The check said
"exactly the jj-signing pair", so it failed on every box that had the Compute Engine key or this
one. It now passes for any allow-listed file and still fails for any other, such as a GitHub key.

Technique: source scripts/agentbox (its source guard runs nothing), take DOCTOR_BOX_SCRIPT's
ok/fail helpers and the .ssh block, and run them under bash with a fake HOME.
"""

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
    start = next(i for i, line in enumerate(lines) if line.startswith("# ~/.ssh holds only"))
    end = next(i for i, line in enumerate(lines) if i > start and "outside the allow-list" in line)
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

    def test_allow_listed_files_pass(self) -> None:
        out = self.run_check(["jj-signing", "jj-signing.pub", "google_compute_engine",
                              "google_compute_engine.pub", "sami-rocqperf", "sami-rocqperf.pub",
                              "known_hosts.sami-rocqperf"])
        self.assertIn("ok   box: .ssh holds only allow-listed files", out)
        self.assertNotIn("FAIL", out)

    def test_github_key_fails(self) -> None:
        out = self.run_check(["jj-signing", "jj-signing.pub", "id_ed25519"])
        self.assertIn("FAIL box: .ssh holds files outside the allow-list: id_ed25519", out)

    def test_mounts_name_the_rocqperf_key(self) -> None:
        mounts = (DOTFILES / "agentbox" / "mounts").read_text(encoding="utf-8").splitlines()
        for path in (".ssh/sami-rocqperf", ".ssh/sami-rocqperf.pub", ".ssh/known_hosts.sami-rocqperf"):
            self.assertIn(f"ro {path}", mounts)
        self.assertNotIn("ro .ssh/id_ed25519", mounts)


if __name__ == "__main__":
    unittest.main()
