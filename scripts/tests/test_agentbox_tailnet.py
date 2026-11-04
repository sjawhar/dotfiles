#!/usr/bin/env python3
"""A box reaches this machine's own `tailscale serve` services.

Every serve listener is bound to the tailnet address ON the tailscale0 device
(`100.112.105.63%tailscale0:8448`), so a packet that arrives over a box bridge is not accepted
although it reaches the host: on 2026-11-04 curl to :8448 answered 000 in every box on
sami-devbox and 200 on the host, and sessions read that as their dashboard being down. Each
serve port gets a relay on the box network's gateway, and the box resolves this machine's
tailnet names to that gateway, so the URL works unchanged and TLS still verifies against the
real listener's certificate.

Technique: stub `tailscale` answering the two JSON shapes the launcher reads, and a stub
`systemd-run`/`ss` pair recording what a relay would start, so no case needs a tailnet, root or
docker. The whole chain was also run against the real thing from a fresh box on sami-devbox
(2026-11-04): both 8448 and 8446 answered 200 inside the box.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
AGENTBOX = DOTFILES / "scripts/agentbox"
ADDRESS = "100.112.105.63"
GATEWAY = "172.25.0.1"
STATUS = {
    "Self": {
        "DNSName": "sami-devbox.tail12ec1c.ts.net.",
        "TailscaleIPs": [ADDRESS, "fd7a:115c:a1e0::a136:6940"],
    }
}
SERVE = {"TCP": {"8446": {"HTTPS": True}, "8448": {"HTTPS": True}, "8450": {"HTTPS": True}}}


class TailnetServe(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.status = self.root / "status.json"
        self.serve = self.root / "serve.json"
        self.status.write_text(json.dumps(STATUS), encoding="utf-8")
        self.serve.write_text(json.dumps(SERVE), encoding="utf-8")
        self.started = self.root / "STARTED"
        self.stub("tailscale", "\n".join([
            'case "$*" in',
            '  "status --json") [ -s "$STATUS_FIXTURE" ] || exit 1; cat "$STATUS_FIXTURE" ;;',
            '  "serve status --json") [ -s "$SERVE_FIXTURE" ] || exit 1; cat "$SERVE_FIXTURE" ;;',
            '  *) exit 2 ;;',
            "esac",
        ]))
        # What a relay start and its readiness check would do, recorded instead of done.
        self.stub("systemd-run", f'printf "%s\\n" "$*" >>"{self.started}"')
        self.stub("systemctl", "exit 3")  # no unit is active
        self.stub("ss", f'while read -r line; do printf "%s\\n" "$line"; done <"{self.root}/listeners"')
        (self.root / "listeners").write_text("", encoding="utf-8")
        self.env = {
            "HOME": str(self.root),
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "DOTFILES_DIR": str(DOTFILES),
            "AGENTBOX_BOXES": str(self.root / "boxes"),
            "STATUS_FIXTURE": str(self.status),
            "SERVE_FIXTURE": str(self.serve),
        }

    def stub(self, name: str, body: str) -> None:
        path = self.bin / name
        path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
        path.chmod(0o755)

    def run_bash(self, script: str, env: dict | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "-c", f'source "$1" >/dev/null 2>&1 || true\n{script}', "--", str(AGENTBOX)],
            env={**self.env, **(env or {})}, capture_output=True, text=True, timeout=60, check=False,
        )

    def read(self) -> subprocess.CompletedProcess[str]:
        return self.run_bash(
            'tailnet_serve\n'
            'printf "address=%s\\n" "$TAILNET_ADDRESS"\n'
            'printf "name=%s\\n" ${TAILNET_NAMES[@]+"${TAILNET_NAMES[@]}"}\n'
            'printf "port=%s\\n" ${TAILNET_SERVE_PORTS[@]+"${TAILNET_SERVE_PORTS[@]}"}\n'
        )

    def test_this_machine_s_address_names_and_serve_ports_are_read(self) -> None:
        run = self.read()
        self.assertEqual(run.returncode, 0, run.stderr)
        lines = run.stdout.splitlines()
        self.assertIn(f"address={ADDRESS}", lines, "the IPv6 address must not win")
        self.assertIn("name=sami-devbox.tail12ec1c.ts.net", lines, "the trailing dot must be gone")
        self.assertIn("name=sami-devbox", lines, "the short name is what a session often types")
        self.assertEqual([line for line in lines if line.startswith("port=")],
                         ["port=8446", "port=8448", "port=8450"])

    def test_an_ephemeral_listener_on_the_same_device_is_not_relayed(self) -> None:
        """tailscale's own peerapi listens on tailscale0 too and is no one's service, so the
        ports come from `tailscale serve`, never from the listener list."""
        (self.root / "listeners").write_text(
            f"LISTEN 0 4096 {ADDRESS}%tailscale0:52369 0.0.0.0:*\n"
            f"LISTEN 0 4096 {ADDRESS}%tailscale0:8448 0.0.0.0:*\n", encoding="utf-8")
        run = self.read()
        self.assertNotIn("port=52369", run.stdout.splitlines())

    def test_a_relay_is_started_for_each_serve_port_on_the_box_gateway(self) -> None:
        run = self.run_bash(
            f'NETWORK_GATEWAY={GATEWAY}\nNETWORK_SUBNET=172.25.0.0/24\nensure_tailnet_serve\n')
        self.assertEqual(run.returncode, 0, run.stderr)
        started = self.started.read_text(encoding="utf-8") if self.started.exists() else ""
        for port in ("8446", "8448", "8450"):
            self.assertIn(f"--unit agentbox-tailnet-{port}", started, started)
            self.assertIn(f"TCP4-LISTEN:{port},bind={GATEWAY}", started, started)
            self.assertIn(f"TCP4:{ADDRESS}:{port}", started, started)
        self.assertNotIn("8449", started, "a port this host does not serve was relayed")

    def test_the_box_resolves_this_machine_s_tailnet_names_to_that_gateway(self) -> None:
        run = self.run_bash(
            f'NETWORK_GATEWAY={GATEWAY}\ntailnet_serve\nbox_tailnet_hosts\n')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout.split(), [
            "--add-host", f"sami-devbox.tail12ec1c.ts.net:{GATEWAY}",
            "--add-host", f"sami-devbox:{GATEWAY}",
        ])

    def test_a_machine_with_no_tailnet_relays_nothing_and_adds_no_host(self) -> None:
        """A launch must not die of it, and a box on such a machine is unchanged."""
        self.status.write_text("", encoding="utf-8")
        run = self.run_bash(
            f'NETWORK_GATEWAY={GATEWAY}\nNETWORK_SUBNET=172.25.0.0/24\n'
            'ensure_tailnet_serve\nprintf "rc=%s\\n" "$?"\nbox_tailnet_hosts\n')
        self.assertIn("rc=0", run.stdout, run.stderr)
        self.assertNotIn("--add-host", run.stdout)
        self.assertFalse(self.started.exists(), "a relay was started with no tailnet")

    def test_a_machine_with_the_tailnet_but_no_served_port_adds_the_names_only(self) -> None:
        """The names still point at the gateway, which is the host: every service bound to
        0.0.0.0 (sshd) answers there, so the name keeps working for all of them."""
        self.serve.write_text(json.dumps({}), encoding="utf-8")
        run = self.run_bash(
            f'NETWORK_GATEWAY={GATEWAY}\nNETWORK_SUBNET=172.25.0.0/24\n'
            'ensure_tailnet_serve\ntailnet_serve\nbox_tailnet_hosts\n')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn(f"--add-host sami-devbox:{GATEWAY}", " ".join(run.stdout.split()))
        self.assertFalse(self.started.exists(), "a relay was started for a port nothing serves")

    def test_no_tailscale_on_the_machine_is_not_an_error(self) -> None:
        (self.bin / "tailscale").unlink()
        run = self.run_bash(
            f'NETWORK_GATEWAY={GATEWAY}\nensure_tailnet_serve\nprintf "rc=%s\\n" "$?"\nbox_tailnet_hosts\n')
        self.assertIn("rc=0", run.stdout, run.stderr)
        self.assertNotIn("--add-host", run.stdout)


if __name__ == "__main__":
    unittest.main()
