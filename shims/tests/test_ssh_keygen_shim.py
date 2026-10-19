#!/usr/bin/env python3
"""What shims/ssh-keygen hands the real ssh-keygen: no agent for -Y, everything else unchanged.

Technique: stub ssh-keygen binaries that print which stub ran and the SSH_AUTH_SOCK they received,
in directories placed before and after shims/ on PATH. Every hop is an exec, so a resolution that
leads back into the shim spins until the timeout here kills it.
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
SHIMS = DOTFILES / "shims"
SHIM = SHIMS / "ssh-keygen"
LOOP_TIMEOUT = 10


def write_stub(directory: Path, label: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "ssh-keygen"
    path.write_text(f'#!/bin/bash\necho "{label} sock=${{SSH_AUTH_SOCK-unset}} args=$*"\n', encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class SshKeygenShim(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.before = root / "before"
        self.after = root / "after"
        write_stub(self.before, "before")
        write_stub(self.after, "after")
        # The shim's own tools, without the system's real ssh-keygen beside them.
        self.tools = root / "tools"
        self.tools.mkdir()
        for tool in ("readlink", "dirname"):
            (self.tools / tool).symlink_to(shutil.which(tool))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def run_shim(self, path_dirs: list[Path], *args: str) -> str:
        env = {**os.environ, "PATH": ":".join(str(d) for d in [*path_dirs, self.tools]), "SSH_AUTH_SOCK": "/tmp/agent.sock"}
        result = subprocess.run(
            [str(SHIM), *args], env=env, capture_output=True, text=True, timeout=LOOP_TIMEOUT, check=True
        )
        return result.stdout.strip()

    def test_signing_runs_without_the_agent(self) -> None:
        for op in ("sign", "verify", "find-principals", "check-novalidate"):
            with self.subTest(op=op):
                out = self.run_shim([SHIMS, self.after], "-Y", op, "-n", "git")
                self.assertEqual(out, f"after sock=unset args=-Y {op} -n git")

    def test_other_operations_keep_the_agent(self) -> None:
        out = self.run_shim([SHIMS, self.after], "-l", "-f", "key.pub")
        self.assertEqual(out, "after sock=/tmp/agent.sock args=-l -f key.pub")

    def test_runs_the_first_ssh_keygen_past_its_own_entry(self) -> None:
        out = self.run_shim([self.before, SHIMS, self.after], "-Y", "sign")
        self.assertTrue(out.startswith("after "), out)

    def test_run_by_path_off_path_searches_from_the_top(self) -> None:
        out = self.run_shim([self.before, self.after], "-Y", "sign")
        self.assertEqual(out, "before sock=unset args=-Y sign")

    def test_no_real_ssh_keygen_fails_loudly(self) -> None:
        env = {**os.environ, "PATH": f"{SHIMS}:{self.tools}"}
        result = subprocess.run([str(SHIM), "-Y", "sign"], env=env, capture_output=True, text=True, timeout=LOOP_TIMEOUT)
        self.assertEqual(result.returncode, 127)
        self.assertIn("no real ssh-keygen", result.stderr)


if __name__ == "__main__":
    unittest.main()
