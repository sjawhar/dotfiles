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
YUBIKEY_KEY = "~/.ssh/id_ed25519_sk_oryx_sami"


def host_words() -> list[str]:
    return [
        word
        for line in SSH_CONFIG.read_text(encoding="utf-8").splitlines()
        if line.startswith("Host ")
        for word in line.split()[1:]
        if word != "*"
    ]


class OryxSshHosts(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.home = Path(self.temp_dir.name) / "home"
        self.home.mkdir()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def ssh_g(self, host: str) -> dict[str, list[str]]:
        # OpenSSH expands ~ in `Include ~/.ssh/config.local` from the account's
        # passwd home, not from HOME, so the temporary HOME would not keep a real
        # config.local out. ssh reads a copy without the Include line.
        config = Path(self.temp_dir.name) / "ssh-config"
        lines = SSH_CONFIG.read_text(encoding="utf-8").splitlines(keepends=True)
        config.write_text("".join(line for line in lines if not line.startswith("Include ")), encoding="utf-8")
        result = subprocess.run(
            [str(SSH), "-F", str(config), "-G", host],
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
        self.assertEqual(values["forwardagent"], ["yes"])
        self.assertEqual(values["controlmaster"], ["auto"])
        self.assertEqual(len(values["controlpath"]), 1)
        self.assertTrue(values["controlpath"][0].endswith("/.ssh/sockets/agent@oryx.thecybermonk.com-22"))

    def test_oryx_hosts_offer_1password_then_the_yubikey_key(self) -> None:
        for host in ("oryx", "oryx-ssm"):
            with self.subTest(host=host):
                values = self.ssh_g(host)
                self.assertEqual(len(values["identityagent"]), 1)
                self.assertTrue(values["identityagent"][0].endswith("/.1password/agent.sock"))
                self.assertEqual(values["identitiesonly"], ["no"])
                self.assertEqual(values.get("identityfile"), [YUBIKEY_KEY])

    def test_oryx_host_lines_are_exactly_oryx_and_oryx_ssm(self) -> None:
        oryx_host_lines = [
            line
            for line in SSH_CONFIG.read_text(encoding="utf-8").splitlines()
            if line.startswith("Host ") and "oryx" in line
        ]
        self.assertEqual(oryx_host_lines, ["Host oryx", "Host oryx-ssm"])

    def test_other_hosts_do_not_offer_the_yubikey_key(self) -> None:
        others = [host for host in host_words() if host not in {"oryx", "oryx-ssm"}]
        self.assertTrue(others)
        for host in [*others, "example.invalid"]:
            with self.subTest(host=host):
                values = self.ssh_g(host)
                self.assertNotIn(YUBIKEY_KEY, values.get("identityfile", []))

    def test_no_host_resolves_to_the_oryx_tailnet(self) -> None:
        hosts = host_words()
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
