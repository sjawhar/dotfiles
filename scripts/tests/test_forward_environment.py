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

    def test_daemon_role_tunnels_only_loopback_pairs(self) -> None:
        """A pair listening on loopback is off the tailnet and needs its SSH tunnel; a tailnet pair must not get one."""
        forward = self.dotfiles / "forward"
        (forward / "config-daemon-oryx.toml").write_text('listen = "fd7a:115c:a1e0::e03b:5c61"\n', encoding="utf-8")
        (forward / "config-daemon-vm.toml").write_text('listen = "127.0.0.1"\npeer = "127.0.0.1"\n', encoding="utf-8")
        for unit in ("forward-daemon@.service", "forward-tunnel@.service"):
            (forward / unit).write_text("test\n", encoding="utf-8")

        install = self.install("daemon")

        self.assertEqual(install.returncode, 0, install.stderr)
        enabled = self.systemctl_log.read_text(encoding="utf-8").splitlines()
        self.assertIn("--user enable --now forward-tunnel@vm", enabled)
        self.assertNotIn("--user enable --now forward-tunnel@oryx", enabled)
        tunnel_unit = self.home / ".config" / "systemd" / "user" / "forward-tunnel@.service"
        self.assertEqual(tunnel_unit.resolve(), (forward / "forward-tunnel@.service").resolve())


def preview_host(address: str) -> str:
    """How a preview URL's host reaches the allowlist: IPv6 keeps its brackets."""
    return f"[{address}]" if ":" in address else address


def preview_entries(allow: list[str], devbox_listen: str) -> set[str]:
    """Allow entries for a pair's preview host, the devbox's listen address: bare, or pinned to the preview port 12802."""
    host = preview_host(devbox_listen)
    return set(allow) & {host, f"{host}:12802"}


def load(name: str) -> dict:
    return tomllib.loads((FORWARD / name).read_text(encoding="utf-8"))


# forward's defaults for the ports a config may move. The URL channel (12800) and
# file preview (12802) are constants, not config keys.
DEFAULT_PORTS = {"bridge_port": 12801, "relay_port": 12803, "pcsc_port": 12804, "grant_port": 12805, "pulse_port": 12806}


def port(config: dict, key: str) -> int:
    return config.get(key, DEFAULT_PORTS[key])


def is_tunnel(config: dict) -> bool:
    """A pair off the tailnet listens on loopback and travels over forward-tunnel@<pair>."""
    return config["listen"].startswith("127.")


def host_forwards(ssh_config: str, host: str) -> set[str]:
    forwards, inside = set(), False
    for line in ssh_config.splitlines():
        words = line.split()
        if words[:1] == ["Host"]:
            inside = words[1:] == [host]
        elif inside and words[:1] in (["RemoteForward"], ["LocalForward"]):
            forwards.add(" ".join(words))
    return forwards


class CommittedForwardPairs(unittest.TestCase):
    """Each devbox config and its laptop daemon config must name each other."""

    def pairs(self) -> list[tuple[str, str]]:
        pairs = [("config-serve.toml", "config.toml")]
        for serve in sorted(FORWARD.glob("config-serve-*.toml")):
            host = serve.name.removeprefix("config-serve-").removesuffix(".toml")
            pairs.append((serve.name, f"config-daemon-{host}.toml"))
        return pairs

    def test_each_devbox_and_laptop_daemon_trust_each_other(self) -> None:
        daemons = {daemon_name for _, daemon_name in self.pairs()[1:]}
        self.assertEqual({path.name for path in FORWARD.glob("config-daemon-*.toml")}, daemons, "a laptop daemon has no devbox side")
        for serve_name, daemon_name in self.pairs():
            with self.subTest(pair=serve_name):
                self.assertTrue((FORWARD / daemon_name).is_file(), f"{serve_name} has no laptop side {daemon_name}")
                serve, daemon = load(serve_name), load(daemon_name)
                if is_tunnel(daemon):
                    # sshd and ssh open the tunnelled connections from 127.0.0.1 at both ends.
                    self.assertEqual((serve["peer"], daemon["peer"]), ("127.0.0.1", "127.0.0.1"))
                    self.assertEqual(serve["listen"], daemon["listen"])
                else:
                    self.assertEqual(serve["peer"], daemon["listen"])
                    self.assertEqual(serve["listen"], daemon["peer"])
                for key in DEFAULT_PORTS:
                    self.assertEqual(port(serve, key), port(daemon, key), key)

    def test_laptop_daemons_share_one_url_policy(self) -> None:
        """Extra pairs copy config.toml's policy; only their own preview host differs."""
        default = load("config.toml")
        shared = set(default["allow"]) - preview_entries(default["allow"], load("config-serve.toml")["listen"])
        for serve_name, daemon_name in self.pairs()[1:]:
            with self.subTest(pair=daemon_name):
                pair = load(daemon_name)
                for key in ("mode", "opener", "clipboard"):
                    self.assertEqual(pair[key], default[key], key)
                own = preview_entries(pair["allow"], load(serve_name)["listen"])
                self.assertTrue(own, f"{daemon_name} does not allow its own preview host")
                self.assertEqual(set(pair["allow"]) - own, shared)

    def test_tunnels_carry_their_pairs_ports_without_colliding_on_the_laptop(self) -> None:
        """Each tunnel's Host block forwards exactly its pair's channels, and no two
        tunnel pairs claim one laptop address and port."""
        ssh_config = (DOTFILES / "laptop" / "ssh-config").read_text(encoding="utf-8")
        claimed: dict[str, str] = {}
        for serve_name, daemon_name in self.pairs():
            serve, daemon = load(serve_name), load(daemon_name)
            if not is_tunnel(daemon):
                continue
            pair = daemon_name.removeprefix("config-daemon-").removesuffix(".toml")
            with self.subTest(pair=pair):
                # The laptop daemon listens on these; the devbox dials them at its peer.
                listened = [p for p in (12800, port(daemon, "relay_port"), port(daemon, "pcsc_port"), port(daemon, "pulse_port")) if p]
                # The laptop dials these at its peer; the devbox listens on them.
                dialled = [p for p in (port(daemon, "bridge_port"), port(daemon, "grant_port")) if p]
                remote = {f"RemoteForward {serve['peer']}:{p} {daemon['listen']}:{p}" for p in listened}
                local = {f"LocalForward {daemon['peer']}:{p} {serve['listen']}:{p}" for p in dialled}
                local.add(f"LocalForward {serve['listen']}:12802 {serve['listen']}:12802")
                self.assertEqual(host_forwards(ssh_config, f"{pair}-tunnel"), remote | local)
                laptop_ends = [f"{daemon['listen']}:{p}" for p in listened] + [line.split()[1] for line in local]
                for end in laptop_ends:
                    self.assertNotIn(end, claimed, f"{pair} and {claimed.get(end)} both claim {end} on the laptop")
                    claimed[end] = pair


if __name__ == "__main__":
    unittest.main()
