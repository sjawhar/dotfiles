#!/usr/bin/env python3
"""A box's tmux relay always reaches the host's DEFAULT tmux server.

The relay (`agentbox-tmux-<box>`, ensure_tmux_forwarder) is one unit per box, and it is pointed
at the host's default server whatever server the launch comes from. On 2026-11-02, while it was
still one shared unit, an `agentbox new` from a pane of `tmux -L devbox-drill` re-pointed it at
that server, and from 17:18:45Z to 17:58Z every box's `tmux` reached the drill server instead
of the `agents` session. Only a launch from the default server gives the box its pane
(TMUX_PANE): a pane id of another server names some other session's pane on the default one,
and that pane is what the guard lets the box kill (test_agentbox_tmux_guard.py).

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
        # The workspace directory (~/.tmux/sockets, .bashrc's TMUX_TMPDIR): the default server
        # and a `-L` one; and a stray server named `default` elsewhere, as /tmp/tmux-<uid>/default
        # was on 2026-11-03.
        self.dir = self.root / ".tmux" / "sockets" / f"tmux-{os.getuid()}"
        self.dir.mkdir(parents=True)
        self.sockets = []
        self.stray_dir = self.root / "tmp" / f"tmux-{os.getuid()}"
        self.stray_dir.mkdir(parents=True)
        for path in (self.dir / "default", self.dir / "devbox-drill", self.stray_dir / "default"):
            s = socket.socket(socket.AF_UNIX)
            s.bind(str(path))
            self.sockets.append(s)
        self.default = self.dir / "default"
        self.other = self.dir / "devbox-drill"
        self.stray = self.stray_dir / "default"

    def tearDown(self) -> None:
        for s in self.sockets:
            s.close()
        self.temp_dir.cleanup()

    def run_bash(self, script: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        base = {"HOME": str(self.root), "PATH": "/usr/bin:/bin", "DOTFILES_DIR": str(DOTFILES),
                "AGENTBOX_BOXES": str(self.root / "boxes")}
        return subprocess.run(["bash", "-c", f'source "{AGENTBOX}"\n{script}'], env={**base, **env},
                              capture_output=True, text=True, timeout=60, check=False)

    def relay_unit(self, env: dict[str, str], box: str = "agentbox-testrelay") -> list[str]:
        """What ensure_tmux_forwarder would start, with systemd and the socket wait stubbed."""
        result = self.run_bash(
            f'RUN_BASE="{self.root}/run"\n'
            'start_unit() { printf "%s\\n" "$@"; }\nwait_for() { :; }\n'
            f'ensure_tmux_forwarder {box}', env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.splitlines()

    def relay_target(self, env: dict[str, str]) -> str:
        """The host tmux server the box's guard is pointed at, as `RELAY <socket>`."""
        for line in self.relay_unit(env):
            if line.startswith("--setenv=AGENTBOX_GUARD_SOCK="):
                return "RELAY " + line.split("=", 2)[2]
        return ""

    def test_a_launch_from_another_server_leaves_the_relay_on_the_default_server(self) -> None:
        self.assertEqual(self.relay_target({"TMUX": f"{self.other},4242,0", "TMUX_PANE": "%0"}),
                         f"RELAY {self.default}")

    def test_a_launch_from_the_default_server_relays_to_it(self) -> None:
        self.assertEqual(self.relay_target({"TMUX": f"{self.default},4241,3", "TMUX_PANE": "%47"}),
                         f"RELAY {self.default}")

    def test_a_launch_outside_tmux_or_with_another_tmux_tmpdir_relays_to_the_workspace_server(self) -> None:
        self.assertEqual(self.relay_target({}), f"RELAY {self.default}")
        self.assertEqual(self.relay_target({"TMUX_TMPDIR": str(self.root / "tmp")}), f"RELAY {self.default}")

    def test_a_launch_from_a_stray_server_named_default_never_relays_to_it(self) -> None:
        self.assertEqual(self.relay_target({"TMUX": f"{self.stray},3783974,2", "TMUX_PANE": "%50",
                                            "TMUX_TMPDIR": str(self.root / "tmp")}), f"RELAY {self.default}")
        other = self.run_bash("box_tmux_env", {"TMUX": f"{self.stray},3783974,2", "TMUX_PANE": "%50"})
        self.assertNotIn("TMUX_PANE=%50", other.stdout)

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

    def test_each_box_gets_its_own_socket_its_own_unit_and_the_guard(self) -> None:
        """The relay is per box, so a kill can be attributed, and it runs the guard, not a byte
        relay to the tmux socket: one shared relay could say only that `a box` asked
        (dispatch://INFRA-1192)."""
        unit = self.relay_unit({"TMUX": f"{self.default},4241,3", "TMUX_PANE": "%47"},
                               box="agentbox-aaaabbbbcccc")
        self.assertEqual(unit[0], "agentbox-tmux-agentbox-aaaabbbbcccc")
        sock = f"{self.root}/run/agentbox-aaaabbbbcccc/tmux/tmux-1000/default"
        self.assertIn(f"UNIX-LISTEN:{sock},fork,mode=600,unlink-early", unit)
        self.assertIn(f"EXEC:{DOTFILES / 'scripts' / 'agentbox-tmux-guard'},nofork", unit)
        self.assertIn("--setenv=AGENTBOX_GUARD_BOX=agentbox-aaaabbbbcccc", unit)
        self.assertIn("--setenv=AGENTBOX_GUARD_PANE=%47", unit)
        self.assertIn(f"--setenv=AGENTBOX_GUARD_SOCK={self.default}", unit)
        self.assertTrue(Path(sock).parent.is_dir(), "the socket's directory is made for socat")
        other = self.relay_unit({"TMUX": f"{self.default},4241,3", "TMUX_PANE": "%47"},
                                box="agentbox-ddddeeeeffff")
        self.assertNotEqual(unit[0], other[0])

    def test_a_box_launched_outside_a_pane_owns_no_pane(self) -> None:
        """Then every kill is refused, which is the safe end of the inference, not an outage:
        everything else a lead does still works."""
        unit = self.relay_unit({})
        self.assertIn("--setenv=AGENTBOX_GUARD_PANE=", unit)


    def test_the_tmux_shim_reaches_the_workspace_server_from_a_shell_without_tmux_tmpdir(self) -> None:
        """A tool shell that dropped TMUX_TMPDIR started a second server at /tmp/tmux-<uid>/default
        on 2026-11-03; the shim now sends such a call to ~/.tmux/sockets, and keeps a caller's own."""
        real = self.root / "realbin"
        real.mkdir()
        (real / "tmux").write_text('#!/bin/bash\necho "TMUX_TMPDIR=${TMUX_TMPDIR-<unset>}"\n', encoding="utf-8")
        (real / "tmux").chmod(0o755)
        path = f"{DOTFILES / 'shims'}:{real}:/usr/bin:/bin"
        bare = subprocess.run([str(DOTFILES / "shims" / "tmux"), "new-session", "-d"], capture_output=True, text=True,
                              env={"HOME": str(self.root), "PATH": path}, timeout=30, check=False)
        self.assertEqual(bare.stdout.strip(), f"TMUX_TMPDIR={self.root}/.tmux/sockets", bare.stderr)
        own = subprocess.run([str(DOTFILES / "shims" / "tmux"), "ls"], capture_output=True, text=True,
                             env={"HOME": str(self.root), "PATH": path, "TMUX_TMPDIR": "/x"}, timeout=30, check=False)
        self.assertEqual(own.stdout.strip(), "TMUX_TMPDIR=/x")

if __name__ == "__main__":
    unittest.main()
