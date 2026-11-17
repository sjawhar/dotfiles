#!/usr/bin/env python3
"""The metadata relay every box shares runs from the installed dotfiles, whatever checkout launches.

On a Google VM `ensure_forwarders` runs one user unit, agentbox-imds, for every box on the machine.
A launch from another checkout (a scratch clone, a worktree) stopped it and started it from that
checkout: INFRA-1720's real-box run did so from a clone that was then deleted, and from 19:20Z on
2026-11-16 to 00:57:43Z every box's metadata relay ran a deleted script (Proof1720). Here the
launch comes from a second checkout while the installed one is $HOME/.dotfiles; systemctl and
systemd-run are stubs that keep the unit's ExecStart in a file and log each call.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
AGENTBOX = DOTFILES / "scripts" / "agentbox"
RELAY = "scripts/agentbox-gce-metadata-relay"

SYSTEMCTL = r'''#!/bin/bash
printf 'systemctl %s\n' "$*" >>"$STUB_LOG"
case "$*" in
    "--user is-active --quiet agentbox-imds") [[ -s "$STUB_STATE" ]] ;;
    "--user show -p ExecStart --value agentbox-imds") cat "$STUB_STATE" 2>/dev/null; exit 0 ;;
    "--user stop agentbox-imds") : >"$STUB_STATE" ;;
    *) exit 0 ;;
esac
'''
SYSTEMD_RUN = r'''#!/bin/bash
printf 'systemd-run %s\n' "$*" >>"$STUB_LOG"
while [[ "$1" == --* ]]; do [[ "$1" == --unit ]] && shift; shift; done
printf '{ path=%s ; argv[]=%s }\n' "$1" "$*" >"$STUB_STATE"
'''


class ImdsRelay(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "home"
        self.installed = self.home / ".dotfiles"
        self.second = self.root / "scratch-clone"
        for checkout in (self.installed, self.second):
            (checkout / "scripts").mkdir(parents=True)
            (checkout / RELAY).write_text("# the relay\n", encoding="utf-8")
            on_vm = checkout / "scripts" / "on-google-vm"
            on_vm.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
            on_vm.chmod(0o755)
            (checkout / "agent-secrets").mkdir()
            for name in ("broker-url.sh", "broker.env"):
                shutil.copyfile(DOTFILES / "agent-secrets" / name, checkout / "agent-secrets" / name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for name, body in (("systemctl", SYSTEMCTL), ("systemd-run", SYSTEMD_RUN)):
            (self.bin / name).write_text(body, encoding="utf-8")
            (self.bin / name).chmod(0o755)
        self.log = self.root / "log"
        self.state = self.root / "execstart"
        self.env = {
            "HOME": str(self.home),
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "DOTFILES_DIR": str(self.second),
            "AGENTBOX_BOXES": str(self.root / "boxes"),
            "STUB_LOG": str(self.log),
            "STUB_STATE": str(self.state),
        }

    def running(self, relay: Path | str) -> None:
        self.state.write_text(f"{{ path=python3 ; argv[]=python3 {relay} 172.31.0.1 8169 172.31.0.0/16 }}\n",
                              encoding="utf-8")

    def launch(self) -> subprocess.CompletedProcess[str]:
        self.log.write_text("", encoding="utf-8")
        return subprocess.run(
            ["bash", "-c", '''source "$1"
             NETWORK_GATEWAY=172.31.0.1 NETWORK_SUBNET=172.31.0.0/16
             ensure_tcp_forwarder() { :; }; ensure_unix_forwarder() { :; }; ensure_tmux_forwarder() { :; }
             ensure_forward_channels() { :; }; ensure_loopback_services() { :; }; ensure_tailnet_serve() { :; }
             wait_for() { :; }
             ensure_forwarders agentbox-0123456789ab''', "--", str(AGENTBOX)],
            env=self.env, capture_output=True, text=True, timeout=60, check=False,
        )

    def calls(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").splitlines()

    def test_a_launch_from_another_checkout_leaves_the_running_relay_as_it_is(self) -> None:
        self.running(self.installed / RELAY)
        before = self.state.read_text(encoding="utf-8")
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.state.read_text(encoding="utf-8"), before, "the unit's ExecStart changed")
        self.assertNotIn("systemctl --user stop agentbox-imds", self.calls())
        self.assertFalse([c for c in self.calls() if c.startswith("systemd-run")], self.calls())
        self.assertIn("runs " + str(self.installed / RELAY), result.stderr)

    def test_a_relay_started_from_another_checkout_goes_back_onto_the_installed_one(self) -> None:
        """What Proof1720 found: the unit ran a clone's relay, deleted since. Any launch puts it back
        onto the installed relay; none puts it onto its own checkout."""
        self.running(self.root / "deleted-clone" / RELAY)
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("systemctl --user stop agentbox-imds", self.calls())
        self.assertIn(str(self.installed / RELAY), self.state.read_text(encoding="utf-8"))
        self.assertNotIn(str(self.second), self.state.read_text(encoding="utf-8"))

    def test_with_no_relay_running_a_launch_starts_the_installed_one(self) -> None:
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(self.installed / RELAY), self.state.read_text(encoding="utf-8"))

    def test_a_machine_with_no_installed_relay_keeps_a_running_relay_and_else_starts_its_own(self) -> None:
        (self.installed / RELAY).unlink()
        self.running(self.root / "other-checkout" / RELAY)
        before = self.state.read_text(encoding="utf-8")
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.state.read_text(encoding="utf-8"), before, "a running relay was replaced")
        self.assertNotIn("systemctl --user stop agentbox-imds", self.calls())
        self.state.write_text("", encoding="utf-8")
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(self.second / RELAY), self.state.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
