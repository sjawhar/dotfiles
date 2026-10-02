#!/usr/bin/env python3
"""Regression coverage for forward roles: relay environment, devbox pairing."""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
INSTALLER = DOTFILES / "installers" / "forward.sh"
BASHRC = DOTFILES / ".bashrc"
FORWARD = DOTFILES / "forward"


def write_executable(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/bash\nset -euo pipefail\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class ForwardServeEnvironment(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.dotfiles = self.root / "dotfiles"
        self.home = self.root / "home"
        self.config_home = self.root / "config"
        self.stub_dir = self.root / "bin"
        (self.dotfiles / "bin").mkdir(parents=True)
        (self.dotfiles / "forward").mkdir()
        self.home.mkdir()
        self.stub_dir.mkdir()

        write_executable(
            self.dotfiles / "bin" / "mise",
            'if [ "${1:-}" = "which" ] || [ "${1:-}" = "exec" ]; then exit 0; fi',
        )
        write_executable(self.stub_dir / "systemctl", 'echo "$*" >> "$SYSTEMCTL_LOG"')
        write_executable(self.stub_dir / "hostname", 'echo "${STUB_HOSTNAME:-somebox}"')
        self.systemctl_log = self.root / "systemctl.log"
        for name in (
            "config.toml",
            "config-serve.toml",
            "forward-daemon.service",
            "forward-serve.service",
            "omp-browser-relay.service",
        ):
            (self.dotfiles / "forward" / name).write_text("test\n", encoding="utf-8")
        self.env = {
            **os.environ,
            "DOTFILES_DIR": str(self.dotfiles),
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.config_home),
            "PATH": f"{self.stub_dir}:{os.environ['PATH']}",
            "SYSTEMCTL_LOG": str(self.systemctl_log),
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def install(self, role: str, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(INSTALLER), role],
            capture_output=True,
            text=True,
            env={**self.env, **env},
            check=False,
        )

    def shell_relay_url(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "-c", 'source "$1"; printf "%s" "${BROWSER_RELAY_URL-}"', "--", str(BASHRC)],
            capture_output=True,
            text=True,
            env=self.env,
            check=False,
        )

    def test_serve_role_installs_no_relay_environment_or_overlay(self) -> None:
        """The ambient relay endpoint is gone; grants supply per-session endpoints."""
        install = self.install("serve")

        self.assertEqual(install.returncode, 0, install.stderr)
        self.assertFalse(
            (self.config_home / "environment.d" / "browser-relay.conf").exists()
        )
        self.assertFalse((self.config_home / "omp" / "browser-relay.yml").exists())
        shell = self.shell_relay_url()
        self.assertEqual(shell.returncode, 0, shell.stderr)
        self.assertEqual(shell.stdout, "")

    def test_serve_role_removes_a_stale_relay_environment_and_overlay(self) -> None:
        """Reinstalling cleans up what bypass-era installs wrote."""
        environment_dir = self.config_home / "environment.d"
        environment_dir.mkdir(parents=True)
        (environment_dir / "browser-relay.conf").write_text(
            "BROWSER_RELAY_URL=http://stale.test\n", encoding="utf-8"
        )
        omp_dir = self.config_home / "omp"
        omp_dir.mkdir(parents=True)
        (omp_dir / "browser-relay.yml").symlink_to(self.dotfiles / "gone.yml")

        install = self.install("serve")

        self.assertEqual(install.returncode, 0, install.stderr)
        self.assertFalse((environment_dir / "browser-relay.conf").exists())
        self.assertFalse((omp_dir / "browser-relay.yml").is_symlink())

    def test_daemon_role_installs_no_relay_environment(self) -> None:
        """The laptop role never had the ambient endpoint and must not gain one."""
        install = self.install("daemon")

        self.assertEqual(install.returncode, 0, install.stderr)
        self.assertFalse(
            (self.config_home / "environment.d" / "browser-relay.conf").exists()
        )
        self.assertFalse((self.config_home / "omp" / "browser-relay.yml").exists())

    def test_serve_role_uses_the_hosts_own_pairing_when_it_has_one(self) -> None:
        """A devbox with config-serve-<host>.toml must not run the default pair's
        addresses: that is how oryx ended up minting sami-agents' preview URLs."""
        (self.dotfiles / "forward" / "config-serve-oryx.toml").write_text("oryx\n", encoding="utf-8")
        link = self.home / ".config" / "forward" / "config.toml"
        for host, expected in (("oryx", "config-serve-oryx.toml"), ("sami-agents", "config-serve.toml")):
            with self.subTest(host=host):
                install = self.install("serve", STUB_HOSTNAME=host)
                self.assertEqual(install.returncode, 0, install.stderr)
                self.assertEqual(link.resolve(), (self.dotfiles / "forward" / expected).resolve())

    def test_daemon_role_runs_one_daemon_per_extra_pair(self) -> None:
        (self.dotfiles / "forward" / "config-daemon-oryx.toml").write_text("oryx\n", encoding="utf-8")
        (self.dotfiles / "forward" / "forward-daemon@.service").write_text("test\n", encoding="utf-8")

        install = self.install("daemon")

        self.assertEqual(install.returncode, 0, install.stderr)
        pair_config = self.home / ".config" / "forward" / "config-oryx.toml"
        self.assertEqual(pair_config.resolve(), (self.dotfiles / "forward" / "config-daemon-oryx.toml").resolve())
        enabled = self.systemctl_log.read_text(encoding="utf-8").splitlines()
        self.assertIn("--user enable --now forward-daemon", enabled)
        self.assertIn("--user enable --now forward-daemon@oryx", enabled)


def preview_host(address: str) -> str:
    """How a preview URL's host reaches the allowlist: IPv6 keeps its brackets."""
    return f"[{address}]" if ":" in address else address


def load(name: str) -> dict:
    return tomllib.loads((FORWARD / name).read_text(encoding="utf-8"))


class CommittedForwardPairs(unittest.TestCase):
    """Each devbox config and its laptop daemon config must name each other."""

    def pairs(self) -> list[tuple[str, str]]:
        pairs = [("config-serve.toml", "config.toml")]
        for serve in sorted(FORWARD.glob("config-serve-*.toml")):
            host = serve.name.removeprefix("config-serve-").removesuffix(".toml")
            pairs.append((serve.name, f"config-daemon-{host}.toml"))
        return pairs

    def test_each_devbox_and_laptop_daemon_trust_each_other(self) -> None:
        for serve_name, daemon_name in self.pairs():
            with self.subTest(pair=serve_name):
                self.assertTrue((FORWARD / daemon_name).is_file(), f"{serve_name} has no laptop side {daemon_name}")
                serve, daemon = load(serve_name), load(daemon_name)
                self.assertEqual(serve["peer"], daemon["listen"])
                self.assertEqual(serve["listen"], daemon["peer"])
                self.assertEqual(serve.get("pcsc_port"), daemon.get("pcsc_port"))

    def test_laptop_daemons_share_one_url_policy(self) -> None:
        """Extra pairs copy config.toml's policy; only their own preview host differs."""
        default = load("config.toml")
        shared = set(default["allow"]) - {preview_host(default["peer"])}
        for path in sorted(FORWARD.glob("config-daemon-*.toml")):
            with self.subTest(pair=path.name):
                pair = load(path.name)
                for key in ("mode", "opener", "clipboard"):
                    self.assertEqual(pair[key], default[key], key)
                self.assertIn(preview_host(pair["peer"]), pair["allow"])
                self.assertEqual(set(pair["allow"]) - {preview_host(pair["peer"])}, shared)


if __name__ == "__main__":
    unittest.main()
