#!/usr/bin/env python3
"""A box's tmux relay always reaches the host's DEFAULT tmux server.

The relay (`agentbox-tmux`, ensure_tmux_forwarder) is one unit shared by every box on the host.
On 2026-11-02 one `agentbox new` from a pane of `tmux -L devbox-drill` re-pointed it at that
server, and from 17:18:45Z to 17:58Z every box's `tmux` reached the drill server instead of the
`agents` session. So the relay's target is the default server whatever server the launch comes
from, and only a launch from the default server gives the box its pane (TMUX_PANE): a pane id
of another server names some other session's pane on the default one.

Technique, as test_agentbox_dirs.py: source `scripts/agentbox` (its source guard runs nothing)
with real unix sockets standing in for the tmux servers.
"""

from __future__ import annotations

import os
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
AGENTBOX = DOTFILES / "scripts" / "agentbox"


class TmuxRelay(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        # A TMUX_TMPDIR like this host's (~/.tmux/sockets): the default server and a `-L` one.
        self.dir = self.root / "sockets" / f"tmux-{os.getuid()}"
        self.dir.mkdir(parents=True)
        self.sockets = []
        for name in ("default", "devbox-drill"):
            s = socket.socket(socket.AF_UNIX)
            s.bind(str(self.dir / name))
            self.sockets.append(s)
        self.default = self.dir / "default"
        self.other = self.dir / "devbox-drill"

    def tearDown(self) -> None:
        for s in self.sockets:
            s.close()
        self.temp_dir.cleanup()

    def run_bash(self, script: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        base = {"HOME": str(self.root), "PATH": "/usr/bin:/bin", "DOTFILES_DIR": str(DOTFILES),
                "AGENTBOX_BOXES": str(self.root / "boxes")}
        return subprocess.run(["bash", "-c", f'source "{AGENTBOX}"\n{script}'], env={**base, **env},
                              capture_output=True, text=True, timeout=60, check=False)

    def relay_target(self, env: dict[str, str]) -> str:
        """The socket ensure_tmux_forwarder hands the shared unit, with the unit itself stubbed."""
        result = self.run_bash(
            'ensure_unix_forwarder() { echo "RELAY $3"; }\nensure_tmux_forwarder', env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_a_launch_from_another_server_leaves_the_relay_on_the_default_server(self) -> None:
        self.assertEqual(self.relay_target({"TMUX": f"{self.other},4242,0", "TMUX_PANE": "%0"}),
                         f"RELAY {self.default}")

    def test_a_launch_from_the_default_server_relays_to_it(self) -> None:
        self.assertEqual(self.relay_target({"TMUX": f"{self.default},4241,3", "TMUX_PANE": "%47"}),
                         f"RELAY {self.default}")

    def test_a_launch_outside_tmux_finds_the_default_server_under_tmux_tmpdir(self) -> None:
        self.assertEqual(self.relay_target({"TMUX_TMPDIR": str(self.root / "sockets")}), f"RELAY {self.default}")

    def test_a_host_with_no_default_server_gets_no_relay(self) -> None:
        self.default.unlink()
        self.assertEqual(self.relay_target({"TMUX": f"{self.other},4242,0"}), "")

    def test_only_a_launch_from_the_default_server_gives_the_box_its_pane(self) -> None:
        own = self.run_bash("box_tmux_env", {"TMUX": f"{self.default},4241,3", "TMUX_PANE": "%47"})
        self.assertEqual(own.returncode, 0, own.stderr)
        lines = own.stdout.splitlines()
        self.assertIn(f"TMUX=/run/user/{os.getuid()}/tmux/tmux-1000/default,4241,3", lines)
        self.assertIn("TMUX_PANE=%47", lines)
        other = self.run_bash("box_tmux_env", {"TMUX": f"{self.other},4242,0", "TMUX_PANE": "%0"})
        self.assertEqual(other.returncode, 0, other.stderr)
        self.assertEqual(other.stdout.splitlines(), ["-e", f"TMUX={self.other},4242,0"])
        self.assertIn("this box gets no TMUX_PANE", other.stderr)


if __name__ == "__main__":
    unittest.main()
