#!/usr/bin/env python3
"""The laptop's Cloudflare SSH hosts."""

from __future__ import annotations

import ipaddress
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


DOTFILES = Path(__file__).resolve().parents[2]
SSH = Path("/usr/bin/ssh")
SSH_CONFIG = DOTFILES / "laptop" / "ssh-config"


class OryxSshHosts(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.home = Path(self.temp_dir.name) / "home"
        self.home.mkdir()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def ssh_g(self, host: str) -> dict[str, list[str]]:
        result = subprocess.run(
            [str(SSH), "-F", str(SSH_CONFIG), "-G", host],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env={**os.environ, "HOME": str(self.home)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        values: dict[str, list[str]] = {}
        for line in result.stdout.splitlines():
            key, value = line.split(maxsplit=1)
            values.setdefault(key, []).append(value)
        return values

    def test_oryx_uses_cloudflare_access(self) -> None:
        values = self.ssh_g("oryx")
        self.assertEqual(values["hostname"], ["oryx.thecybermonk.com"])
        self.assertEqual(values["user"], ["agent"])
        self.assertEqual(values["proxycommand"], ["cloudflared access ssh --hostname %h"])
        self.assertEqual(values["hostkeyalias"], ["oryx.tail12ec1c.ts.net"])
        self.assertTrue(values["identityagent"][0].endswith("/.1password/agent.sock"))
        self.assertEqual(values["identitiesonly"], ["no"])
        self.assertFalse(any(value.endswith("/.ssh/oryx-tunnel") for value in values.get("identityfile", [])))
        self.assertEqual(values["forwardagent"], ["yes"])
        self.assertEqual(values["controlmaster"], ["auto"])
        self.assertEqual(len(values["controlpath"]), 1)
        self.assertTrue(values["controlpath"][0].endswith("/.ssh/sockets/agent@oryx.thecybermonk.com-22"))

    def test_oryx_host_lines_are_exactly_oryx_and_oryx_ssm(self) -> None:
        oryx_host_lines = [
            line
            for line in SSH_CONFIG.read_text(encoding="utf-8").splitlines()
            if line.startswith("Host ") and "oryx" in line
        ]
        self.assertEqual(oryx_host_lines, ["Host oryx", "Host oryx-ssm"])

    def test_no_host_resolves_to_the_oryx_tailnet(self) -> None:
        hosts = [
            word
            for line in SSH_CONFIG.read_text(encoding="utf-8").splitlines()
            if line.startswith("Host ")
            for word in line.split()[1:]
            if word != "*"
        ]
        self.assertTrue(hosts)
        tailnet_v4 = ipaddress.IPv4Network("100.64.0.0/10")
        tailnet_v6 = ipaddress.IPv6Network("fd7a:115c:a1e0::/48")
        for host in hosts:
            with self.subTest(host=host):
                hostname = self.ssh_g(host)["hostname"][0]
                self.assertFalse(hostname.endswith(".ts.net"))
                try:
                    address = ipaddress.ip_address(hostname)
                except ValueError:
                    continue
                self.assertNotIn(address, tailnet_v4)
                self.assertNotIn(address, tailnet_v6)


if __name__ == "__main__":
    unittest.main()
