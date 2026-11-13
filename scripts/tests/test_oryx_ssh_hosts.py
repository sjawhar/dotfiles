#!/usr/bin/env python3
"""The laptop's Cloudflare SSH hosts and their forward set."""

from __future__ import annotations

import ipaddress
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


DOTFILES = Path(__file__).resolve().parents[2]
SSH = Path("/usr/bin/ssh")
SSH_CONFIG = DOTFILES / "laptop" / "ssh-config"


def normalized_forward(value: str) -> str:
    return re.sub(r"\[([^\]]+)\]", r"\1", value)


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

    def test_oryx_tunnel_has_only_the_required_forwards(self) -> None:
        values = self.ssh_g("oryx-tunnel")
        self.assertEqual(values["hostname"], ["oryx-pcsc.thecybermonk.com"])
        self.assertEqual(values["exitonforwardfailure"], ["yes"])
        self.assertEqual(values["serveraliveinterval"], ["15"])
        self.assertEqual(values["serveralivecountmax"], ["4"])
        self.assertEqual(values["identityagent"], ["none"])
        self.assertNotIn("controlpath", values)
        remote = [normalized_forward(value) for value in values.get("remoteforward", [])]
        local = [normalized_forward(value) for value in values.get("localforward", [])]
        self.assertCountEqual(
            remote,
            [
                "127.0.0.1:12800 127.0.0.1:12800",
                "127.0.0.1:12803 127.0.0.1:12803",
                "127.0.0.1:12804 127.0.0.1:12804",
                "127.0.0.1:12806 127.0.0.1:12806",
                "127.0.0.1:5038 127.0.0.1:5037",
                "127.0.0.1:2223 sami-devbox:22",
                "127.0.0.1:2224 ghost-wispr:22",
            ],
        )
        self.assertCountEqual(
            local,
            [
                "127.0.0.1:12801 127.0.0.1:12801",
                "127.0.0.1:12802 127.0.0.1:12802",
                "127.0.0.1:12805 127.0.0.1:12805",
            ],
        )

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
