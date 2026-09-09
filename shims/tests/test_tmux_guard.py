#!/usr/bin/env python3
"""tmux shim: kill-server is refused on the default server and nowhere else."""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
SHIM = DOTFILES / "shims" / "tmux"


class TmuxGuard(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.stub_dir = root / "bin"
        self.stub_dir.mkdir()
        self.argv_log = root / "argv"
        stub = self.stub_dir / "tmux"
        stub.write_text(
            f'#!/bin/bash\nprintf "%s\\n" "$@" > {self.argv_log}\n', encoding="utf-8"
        )
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def run_shim(self, *args: str, tmux: str | None = None, allow: bool = False):
        env = {
            "PATH": f"{SHIM.parent}:{self.stub_dir}:/usr/bin:/bin",
            "HOME": self.temp_dir.name,
            "DOTFILES_DIR": str(DOTFILES),
        }
        if tmux is not None:
            env["TMUX"] = tmux
        if allow:
            env["TMUX_ALLOW_KILL_SERVER"] = "1"
        return subprocess.run([str(SHIM), *args], env=env, capture_output=True, text=True)

    def assert_forwarded(self, result, *argv: str) -> None:
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.argv_log.read_text().split("\n")[:-1], list(argv))

    def assert_refused(self, result) -> None:
        self.assertEqual(result.returncode, 1)
        self.assertIn("refusing `kill-server`", result.stderr)
        self.assertFalse(self.argv_log.exists(), "real tmux must not run")

    def test_ordinary_commands_pass_through_verbatim(self) -> None:
        self.assert_forwarded(self.run_shim("list-panes", "-a", "-F", "#{pane_id}"),
                              "list-panes", "-a", "-F", "#{pane_id}")

    def test_kill_server_outside_tmux_targets_default_and_is_refused(self) -> None:
        self.assert_refused(self.run_shim("kill-server"))

    def test_kill_server_inside_default_server_is_refused(self) -> None:
        self.assert_refused(self.run_shim("kill-server", tmux="/tmp/tmux-1000/default,123,0"))

    def test_command_prefix_and_chained_form_are_refused(self) -> None:
        self.assert_refused(self.run_shim("kill-serv"))
        self.assert_refused(self.run_shim("new-session", "-d", ";", "kill-server"))

    def test_explicit_socket_is_allowed(self) -> None:
        self.assert_forwarded(self.run_shim("-L", "scratch", "kill-server"), "-L", "scratch", "kill-server")
        self.assert_forwarded(self.run_shim("-S/tmp/x.sock", "kill-server"), "-S/tmp/x.sock", "kill-server")

    def test_inside_a_non_default_server_is_allowed(self) -> None:
        self.assert_forwarded(self.run_shim("kill-server", tmux="/tmp/tmux-1000/rtest,123,0"), "kill-server")

    def test_override_is_allowed(self) -> None:
        self.assert_forwarded(self.run_shim("kill-server", allow=True), "kill-server")

    def test_kill_session_and_lookalike_words_are_allowed(self) -> None:
        self.assert_forwarded(self.run_shim("kill-session", "-t", "legion-smoke1"),
                              "kill-session", "-t", "legion-smoke1")
        self.assert_forwarded(self.run_shim("send-keys", "tmux kill-server", "Enter"),
                              "send-keys", "tmux kill-server", "Enter")

    def test_capture_pane_S_is_not_a_socket_option(self) -> None:
        # -S after the command word is capture-pane's start line, not a server socket.
        self.assert_refused(self.run_shim("capture-pane", "-p", "-S", "-200", ";", "kill-server"))


if __name__ == "__main__":
    unittest.main()
