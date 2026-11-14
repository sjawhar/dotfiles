#!/usr/bin/env python3
"""devbox uses Cloudflare SSH for oryx and mosh for other hosts."""

from __future__ import annotations

import os
import shlex
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(os.environ.get("DEVBOX_SCRIPT") or Path(__file__).resolve().parents[1] / "devbox")


def write_executable(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/bash\nset -euo pipefail\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class Devbox(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.stubs = self.root / "bin"
        self.stubs.mkdir()
        self.log = self.root / "calls.log"

        record_call = 'printf "%q " "$0" "$@" >>"$STUB_LOG"\nprintf "\\n" >>"$STUB_LOG"'
        write_executable(self.stubs / "ssh", record_call)
        write_executable(self.stubs / "mosh", record_call)
        write_executable(
            self.stubs / "tailscale",
            record_call + "\nprintf '%s\\n' '{\"Peer\": {}}'",
        )
        # The previous script starts a detached supervisor. The stub keeps a
        # before-change test from leaving one behind.
        write_executable(self.stubs / "setsid", "exit 0")
        self.env = {
            **os.environ,
            "HOME": str(self.home),
            "PATH": f"{self.stubs}:/usr/bin:/bin",
            "STUB_LOG": str(self.log),
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def run_devbox(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/bash", str(SCRIPT), *arguments],
            capture_output=True,
            text=True,
            env=self.env,
            check=False,
            timeout=10,
        )

    def calls(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [shlex.split(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def command_arguments(self, command: str) -> list[list[str]]:
        return [call[1:] for call in self.calls() if Path(call[0]).name == command]

    def assert_no_old_tunnel_call(self) -> None:
        arguments = [argument for call in self.calls() for argument in call[1:]]
        self.assertNotIn("oryx-tunnel", arguments)
        self.assertNotIn("oryx-cf", arguments)
        self.assertNotIn("-N", arguments)

    def test_oryx_uses_ssh_with_the_tmux_default(self) -> None:
        result = self.run_devbox("oryx")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.command_arguments("ssh"),
            [["-t", "oryx", "tmux", "attach", "-t", "main", "||", "tmux", "new", "-s", "main"]],
        )
        self.assertEqual(self.command_arguments("mosh"), [])
        self.assert_no_old_tunnel_call()

    def test_oryx_drops_the_command_separator(self) -> None:
        result = self.run_devbox("oryx", "--", "htop")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.command_arguments("ssh"), [["-t", "oryx", "htop"]])
        self.assertEqual(self.command_arguments("mosh"), [])
        self.assert_no_old_tunnel_call()

    def test_other_hosts_keep_mosh_arguments(self) -> None:
        result = self.run_devbox("sami-devbox", "--", "tm")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.command_arguments("ssh"), [])
        self.assertEqual(self.command_arguments("mosh"), [["sami-devbox", "--", "tm"]])

    def test_usage_describes_oryx_and_lists_other_tailnet_hosts(self) -> None:
        result = self.run_devbox()
        self.assertEqual(result.returncode, 2)
        self.assertIn("oryx uses Cloudflare Access with ssh and tmux, not mosh.", result.stderr)
        self.assertIn("Linux machines online on the tailnet (other hosts):", result.stderr)
        self.assertEqual(self.command_arguments("tailscale"), [["status", "--json"]])


if __name__ == "__main__":
    unittest.main()
