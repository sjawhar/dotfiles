#!/usr/bin/env python3
"""Only configured, listening loopback channels need agentbox TCP relays."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
AGENTBOX = DOTFILES / "scripts/agentbox"


class ForwardChannels(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.config_home = self.root / ".config"
        self.config = self.config_home / "forward/config.toml"
        self.config.parent.mkdir(parents=True)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        ss = self.bin / "ss"
        ss.write_text('#!/bin/bash\n[[ "$*" == -Hltn ]] || exit 2\ncat "$SS_FIXTURE"\n', encoding="utf-8")
        ss.chmod(0o755)
        self.listeners = self.root / "listeners"
        self.env = {
            "HOME": str(self.root),
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "DOTFILES_DIR": str(DOTFILES),
            "AGENTBOX_BOXES": str(self.root / "boxes"),
            "XDG_CONFIG_HOME": str(self.config_home),
            "SS_FIXTURE": str(self.listeners),
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def discover(self, config: str | None, listeners: list[str]) -> subprocess.CompletedProcess[str]:
        if config is not None:
            self.config.write_text(config, encoding="utf-8")
        self.listeners.write_text(
            "".join(f"LISTEN 0 128 {endpoint} 0.0.0.0:*\n" for endpoint in listeners),
            encoding="utf-8",
        )
        return subprocess.run(
            ["bash", "-c", 'source "$1"; forward_channels; for channel in "${FORWARD_CHANNELS[@]}"; do printf "%s\\n" "$channel"; done',
             "--", str(AGENTBOX)],
            env=self.env, capture_output=True, text=True, timeout=30, check=False,
        )

    def test_custom_channel_outside_128xx_is_relayed_from_active_config(self) -> None:
        result = self.discover(
            'listen = "127.0.0.2"\npeer = "127.0.0.1"\nbridge_port = 24567\ngrant_port = 24568\n',
            ["127.0.0.2:24567", "127.0.0.2:24568", "127.0.0.1:12800", "127.0.0.2:12802"],
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(set(result.stdout.splitlines()), {
            "127.0.0.2:24567", "127.0.0.2:24568", "127.0.0.1:12800", "127.0.0.2:12802",
        })

    def test_unconfigured_addresses_disabled_channels_and_abandoned_ports_are_excluded(self) -> None:
        result = self.discover(
            'listen = "127.0.0.2"\npeer = "127.0.0.1"\nbridge_port = 24567\nrelay_port = 0\n',
            ["127.0.0.2:24567", "127.0.0.2:12801", "127.0.0.1:12803", "127.0.0.9:24567", "127.0.0.1:12899"],
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["127.0.0.2:24567"])

    def test_channels_match_role_addresses_not_the_address_port_cross_product(self) -> None:
        result = self.discover(
            'listen = "127.0.0.2"\npeer = "127.0.0.1"\n',
            [f"{address}:{port}" for address in ("127.0.0.1", "127.0.0.2") for port in range(12800, 12807)],
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(set(result.stdout.splitlines()), {
            "127.0.0.2:12801", "127.0.0.2:12802", "127.0.0.2:12805",
            "127.0.0.1:12800", "127.0.0.1:12803", "127.0.0.1:12804", "127.0.0.1:12806",
        })

    def test_custom_peer_channel_ports_use_peer_address_only(self) -> None:
        result = self.discover(
            'listen = "127.0.0.2"\npeer = "127.0.0.1"\nrelay_port = 24570\npcsc_port = 24571\npulse_port = 24572\n',
            [f"{address}:{port}" for address in ("127.0.0.1", "127.0.0.2") for port in (24570, 24571, 24572)],
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(set(result.stdout.splitlines()), {
            "127.0.0.1:24570", "127.0.0.1:24571", "127.0.0.1:24572",
        })

    def test_wrong_role_addresses_do_not_relay_even_when_they_have_live_listeners(self) -> None:
        result = self.discover(
            'listen = "127.0.0.2"\npeer = "127.0.0.1"\n',
            ["127.0.0.1:12801", "127.0.0.1:12802", "127.0.0.1:12805",
             "127.0.0.2:12800", "127.0.0.2:12803", "127.0.0.2:12804", "127.0.0.2:12806"],
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_same_port_on_distinct_live_endpoints_is_refused_before_forwarders_start(self) -> None:
        self.config.write_text('listen = "127.0.0.2"\npeer = "127.0.0.1"\nbridge_port = 12803\n', encoding="utf-8")
        self.listeners.write_text(
            "LISTEN 0 128 127.0.0.2:12803 0.0.0.0:*\nLISTEN 0 128 127.0.0.1:12803 0.0.0.0:*\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            ["bash", "-c", 'source "$1"; ensure_tcp_forwarder() { echo "forwarder started"; }; ensure_forward_channels',
             "--", str(AGENTBOX)],
            env=self.env, capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("127.0.0.1:12803", result.stderr)
        self.assertIn("127.0.0.2:12803", result.stderr)
        self.assertIn("port-only", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_discovery_failure_propagates_when_forwarder_setup_is_a_condition(self) -> None:
        self.env["XDG_CONFIG_HOME"] = str(self.root / "elsewhere")
        self.env["DOTFILES_DIR"] = str(self.root / "dotfiles")
        scripts = self.root / "dotfiles/scripts"
        scripts.mkdir(parents=True)
        broker = self.root / "dotfiles/agent-secrets"
        broker.mkdir()
        for name in ("broker-url.sh", "broker.env"):
            shutil.copyfile(DOTFILES / "agent-secrets" / name, broker / name)
        on_google_vm = scripts / "on-google-vm"
        on_google_vm.write_text("#!/bin/bash\nexit 1\n", encoding="utf-8")
        on_google_vm.chmod(0o755)
        result = subprocess.run(
            ["bash", "-c", '''source "$1"
             ensure_tcp_forwarder() { :; }
             ensure_unix_forwarder() { :; }
             ensure_tmux_forwarder() { :; }
             ensure_loopback_services() { echo "continued after failed discovery"; }
             if ensure_forwarders; then exit 0; else exit 1; fi''', "--", str(AGENTBOX)],
            env=self.env, capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("XDG_CONFIG_HOME", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_forward_channel_cannot_reuse_envoy_or_metadata_gateway_port(self) -> None:
        for port, owner in ((9020, "127.0.0.1:9020"), (8169, "metadata service")):
            with self.subTest(port=port):
                result = self.discover(
                    f'listen = "127.0.0.2"\npeer = "127.0.0.1"\nbridge_port = {port}\n',
                    [f"127.0.0.2:{port}"],
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(owner, result.stderr)
                self.assertIn("gateway port", result.stderr)
                self.assertEqual(result.stdout, "")

    def test_forward_channel_cannot_reuse_another_loopback_service_route(self) -> None:
        result = self.discover(
            'listen = "127.0.0.2"\npeer = "127.0.0.1"\nbridge_port = 5005\n',
            ["127.0.0.2:5005", "127.0.0.1:5005"],
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("gateway port 5005", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_identical_envoy_and_loopback_routes_are_not_relayed_twice(self) -> None:
        for port in (9020, 5005):
            with self.subTest(port=port):
                result = self.discover(
                    f'listen = "127.0.0.1"\npeer = "127.0.0.1"\nbridge_port = {port}\n',
                    [f"127.0.0.1:{port}"],
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "")

    def test_shared_port_on_one_address_is_one_unambiguous_endpoint(self) -> None:
        result = self.discover(
            'listen = "127.0.0.1"\npeer = "127.0.0.1"\nbridge_port = 12803\n',
            ["127.0.0.1:12803"],
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["127.0.0.1:12803"])

    def test_same_port_with_only_one_live_endpoint_is_unambiguous(self) -> None:
        result = self.discover(
            'listen = "127.0.0.2"\npeer = "127.0.0.1"\nbridge_port = 12803\n',
            ["127.0.0.2:12803"],
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["127.0.0.2:12803"])

    def test_noncanonical_config_home_is_not_silently_used_instead_of_mounted_config(self) -> None:
        self.env["XDG_CONFIG_HOME"] = str(self.root / "elsewhere")
        result = self.discover('listen = "127.0.0.2"\npeer = "127.0.0.1"\n', ["127.0.0.2:12802"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("XDG_CONFIG_HOME", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_tailnet_channels_are_routed_directly_not_bound_inside_the_box(self) -> None:
        result = self.discover(
            'listen = "100.80.0.3"\npeer = "100.80.0.1"\n',
            ["100.80.0.3:12801", "100.80.0.3:12802", "100.80.0.3:12805", "127.0.0.1:12800"],
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_missing_serve_config_does_not_relay_unrelated_services(self) -> None:
        result = self.discover(None, ["127.0.0.1:12800", "127.0.0.1:12802"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_legacy_config_symlink_is_followed(self) -> None:
        legacy = self.root / "legacy.toml"
        legacy.write_text('listen = "127.0.0.3"\npeer = "127.0.0.1"\ngrant_port = 24568\n', encoding="utf-8")
        self.config.symlink_to(legacy)
        result = self.discover(None, ["127.0.0.3:24568", "127.0.0.2:12805"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["127.0.0.3:24568"])

    def test_invalid_serve_config_is_actionable_instead_of_silently_skipping_channels(self) -> None:
        result = self.discover('listen = "127.0.0.2"\nbridge_port = "bad"\n', ["127.0.0.2:12801"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(str(self.config), result.stderr)
        self.assertIn("bridge_port", result.stderr)


if __name__ == "__main__":
    unittest.main()
