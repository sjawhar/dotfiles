#!/usr/bin/env python3
"""Forward installer output, failure safety, relay environment, and legacy pairs."""

from __future__ import annotations

import shlex
import shutil
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


class ForwardInstaller(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.dotfiles = self.root / "dotfiles"
        self.home = self.root / "home"
        self.config_home = self.home / ".config"
        self.stub_dir = self.root / "bin"
        self.lookups = self.root / "tailscale"
        (self.dotfiles / "bin").mkdir(parents=True)
        shutil.copytree(FORWARD, self.dotfiles / "forward")
        self.home.mkdir()
        self.stub_dir.mkdir()
        self.lookups.mkdir()
        self.systemctl_log = self.root / "systemctl.log"
        self.legacy_units = self.root / "legacy-units"
        self.legacy_units.write_text("", encoding="utf-8")
        for name, address in {
            "self": "100.80.0.1",
            "self-vm": "100.80.0.3",
            "sami": "100.80.0.1",
            "oryx": "100.80.0.2",
            "sami-devbox": "100.80.0.3",
            "sami-rocqperf": "100.80.0.5",
        }.items():
            (self.lookups / name).write_text(address + "\n", encoding="utf-8")
        (self.dotfiles / "agent-secrets").mkdir()
        for name in ("broker-url.sh", "broker.env"):
            shutil.copyfile(DOTFILES / "agent-secrets" / name, self.dotfiles / "agent-secrets" / name)
        write_executable(
            self.dotfiles / "bin" / "mise",
            '''case "$*" in
                "which forward"|"which omp"|"activate --shims bash"|"exec github:sjawhar/oh-my-pi -- omp browser-relay install --dir $HOME/.omp/browser-relay/extension") exit 0 ;;
                *) echo "unexpected mise arguments: $*" >&2; exit 2 ;;
            esac''',
        )
        write_executable(
            self.stub_dir / "systemctl",
            '''printf '%s\n' "$*" >> "$SYSTEMCTL_LOG"
            if [[ -n "${SYSTEMCTL_FAIL:-}" && "$*" == "$SYSTEMCTL_FAIL" ]]; then exit 1; fi
            case "$*" in
                *list-unit-files*--type=service) printf '%s\n' "${PCSC_UNIT_FILES:-}" ;;
                *list-units*pcscd-bridge.service) printf '%s\n' "${PCSC_ACTIVE_UNITS:-}" ;;
                *list-units*|*list-unit-files*) cat "$LEGACY_UNITS" ;;
            esac''',
        )
        write_executable(self.stub_dir / "hostname", 'echo "${STUB_HOSTNAME:-sami-devbox}"')
        write_executable(
            self.stub_dir / "tailscale",
            '''[[ "$#" -ge 2 && "$#" -le 3 && "$1" == ip && "$2" == -4 ]] || exit 2
            cat "$TAILSCALE_FIXTURES/${3:-$TAILSCALE_SELF}"''',
        )
        write_executable(self.stub_dir / "ssh-keygen", "exit 99")
        write_executable(self.stub_dir / "flatpak", "exit 1")
        self.env = {
            "DOTFILES_DIR": str(self.dotfiles),
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.config_home),
            "PATH": f"{self.stub_dir}:/usr/bin:/bin",
            "SYSTEMCTL_LOG": str(self.systemctl_log),
            "LEGACY_UNITS": str(self.legacy_units),
            "TAILSCALE_FIXTURES": str(self.lookups),
        }
        self.config = self.config_home / "forward" / "config.toml"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def install(self, role: str, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(INSTALLER), role],
            capture_output=True,
            text=True,
            env={**self.env, "TAILSCALE_SELF": "self-vm" if role == "serve" else "self", **env},
            timeout=30,
            check=False,
        )

    def installed_config(self) -> dict:
        return tomllib.loads(self.config.read_text(encoding="utf-8"))

    def test_serve_generates_own_tailnet_address_and_literal_laptop_peer(self) -> None:
        install = self.install("serve")
        self.assertEqual(install.returncode, 0, install.stderr)
        config = self.installed_config()
        self.assertEqual(config["listen"], "100.80.0.3")
        self.assertEqual(config["peer"], "100.80.0.1")
        self.assertNotIn("peers", config)
        self.assertEqual(config["forward_ttl_secs"], 300)
        self.assertEqual({key: config.get(key, value) for key, value in DEFAULT_PORTS.items()}, DEFAULT_PORTS)
        self.assertIn("--user enable --now forward-serve", self.systemctl_log.read_text().splitlines())

    def test_serve_does_not_require_a_legacy_pcsc_bridge(self) -> None:
        install = self.install("serve", SYSTEMCTL_FAIL="--user list-unit-files --no-legend --no-pager pcscd-bridge.service")
        self.assertEqual(install.returncode, 0, install.stderr)
        self.assertIn("--user restart forward-serve", self.systemctl_log.read_text().splitlines())

    def test_serve_stops_enabled_or_active_pcsc_bridge_before_start_without_deleting_unit(self) -> None:
        unit = self.home / ".config/systemd/user/pcscd-bridge.service"
        unit.parent.mkdir(parents=True)
        unit.symlink_to(DOTFILES / "devbox/pcscd-bridge.service")
        for state in (
            {"PCSC_UNIT_FILES": "pcscd-bridge.service enabled enabled"},
            {"PCSC_ACTIVE_UNITS": "pcscd-bridge.service loaded active running Bridge"},
        ):
            with self.subTest(state=state):
                if self.systemctl_log.exists():
                    self.systemctl_log.unlink()
                install = self.install("serve", STUB_HOSTNAME="oryx", **state)
                self.assertEqual(install.returncode, 0, install.stderr)
                log = self.systemctl_log.read_text().splitlines()
                self.assertLess(log.index("--user disable --now pcscd-bridge.service"),
                                log.index("--user enable --now forward-serve"))
                self.assertTrue(unit.is_symlink())
                self.assertEqual(unit.resolve(), DOTFILES / "devbox/pcscd-bridge.service")
                self.assertTrue((self.home / ".pcscd").is_dir())

    def test_serve_does_not_start_if_pcsc_ownership_transition_fails(self) -> None:
        for command in (
            "--user list-unit-files --no-legend --no-pager --type=service",
            "--user list-units --all --full --plain --no-legend --no-pager pcscd-bridge.service",
            "--user disable --now pcscd-bridge.service",
        ):
            with self.subTest(command=command):
                if self.systemctl_log.exists():
                    self.systemctl_log.unlink()
                install = self.install("serve", PCSC_UNIT_FILES="pcscd-bridge.service disabled enabled",
                                       SYSTEMCTL_FAIL=command)
                self.assertNotEqual(install.returncode, 0)
                self.assertIn("pcscd-bridge", install.stderr)
                self.assertFalse(any("forward-serve" in line for line in self.systemctl_log.read_text().splitlines()))

    def test_daemon_installs_extension_at_the_flatpak_allowed_path(self) -> None:
        log = self.root / "relay-install-args"
        write_executable(self.dotfiles / "bin/mise", f'''case "$*" in
            *browser-relay*) printf '%s\\n' "$@" > "{log}" ;;
        esac''')
        install = self.install("daemon")
        self.assertEqual(install.returncode, 0, install.stderr)
        self.assertEqual(log.read_text().splitlines()[-2:],
                         ["--dir", str(self.home / ".omp/browser-relay/extension")])

    def test_daemon_generates_one_literal_peer_set_and_installs_one_daemon(self) -> None:
        install = self.install("daemon", STUB_HOSTNAME="sami")
        self.assertEqual(install.returncode, 0, install.stderr)
        config = self.installed_config()
        self.assertEqual(config["listen"], "100.80.0.1")
        self.assertEqual(config["peers"], ["100.80.0.2", "100.80.0.3", "100.80.0.5"])
        self.assertNotIn("peer", config)
        self.assertEqual(config["mode"], "allowlist")
        self.assertEqual(config["opener"], ["/usr/bin/xdg-open"])
        self.assertEqual(config["clipboard"], ["wl-copy"])
        self.assertIn("github.com", config["allow"])
        self.assertNotIn("127.0.0.3:12802", config["allow"])
        self.assertEqual({key: config.get(key, value) for key, value in DEFAULT_PORTS.items()}, DEFAULT_PORTS)
        log = self.systemctl_log.read_text().splitlines()
        self.assertIn("--user enable --now forward-daemon", log)
        self.assertIn("--user enable --now omp-browser-relay", log)
        self.assertFalse(any("enable --now forward-daemon@" in line for line in log))
        unit = self.home / ".config/systemd/user/forward-daemon.service"
        self.assertEqual(unit.resolve(), self.dotfiles / "forward/forward-daemon.service")

    def test_inventory_alone_adds_a_vm_without_pair_files(self) -> None:
        (self.dotfiles / "forward/hosts.toml").write_text(
            'laptop = "sami"\ndev_vms = ["sami-devbox", "new-vm"]\n', encoding="utf-8",
        )
        (self.lookups / "new-vm").write_text("100.80.0.6\n", encoding="utf-8")
        install = self.install("daemon")
        self.assertEqual(install.returncode, 0, install.stderr)
        self.assertEqual(self.installed_config()["peers"], ["100.80.0.3", "100.80.0.6"])

    def test_reinstall_keeps_configuration_identical(self) -> None:
        for role in ("daemon", "serve"):
            with self.subTest(role=role):
                first = self.install(role)
                self.assertEqual(first.returncode, 0, first.stderr)
                contents = self.config.read_bytes()
                self.systemctl_log.unlink()
                second = self.install(role)
                self.assertEqual(second.returncode, 0, second.stderr)
                self.assertEqual(self.config.read_bytes(), contents)
                self.assertFalse(self.config.is_symlink())
                services = ("forward-daemon", "omp-browser-relay") if role == "daemon" else ("forward-serve",)
                log = self.systemctl_log.read_text().splitlines()
                for service in services:
                    self.assertIn(f"--user restart {service}", log)

    def test_retry_restarts_daemon_after_failed_install_already_replaced_config(self) -> None:
        first = self.install("daemon")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("100.80.0.2", self.installed_config()["peers"])
        (self.dotfiles / "forward/hosts.toml").write_text(
            'laptop = "sami"\ndev_vms = ["sami-devbox"]\n', encoding="utf-8",
        )
        failed = self.install("daemon", SYSTEMCTL_FAIL="--user enable --now forward-daemon")
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(self.installed_config()["peers"], ["100.80.0.3"])
        replacement = self.config.read_bytes()
        self.systemctl_log.unlink()
        retry = self.install("daemon")
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertEqual(self.config.read_bytes(), replacement)
        self.assertIn("--user restart forward-daemon", self.systemctl_log.read_text().splitlines())

    def test_noncanonical_config_home_is_rejected_before_writes_or_service_changes(self) -> None:
        for role in ("serve", "daemon"):
            for config_home in (str(self.root / "elsewhere"), "relative"):
                with self.subTest(role=role, config_home=config_home):
                    install = self.install(role, XDG_CONFIG_HOME=config_home)
                    self.assertNotEqual(install.returncode, 0)
                    self.assertIn("XDG_CONFIG_HOME", install.stderr)
                    self.assertFalse(self.config_home.exists())
                    self.assertFalse((self.root / "elsewhere").exists())
                    self.assertFalse(self.systemctl_log.exists())

    def test_unset_config_home_uses_the_systemd_and_box_config_path(self) -> None:
        self.env.pop("XDG_CONFIG_HOME")
        install = self.install("serve")
        self.assertEqual(install.returncode, 0, install.stderr)
        self.assertEqual(self.installed_config()["listen"], "100.80.0.3")

    def test_role_units_pass_canonical_config_despite_user_manager_environment(self) -> None:
        write_executable(self.dotfiles / "bin/mise", 'printf "%s\\n" "$@"')
        for role, wrapper in (("serve", "forward-serve"), ("daemon", "forward-daemon")):
            with self.subTest(role=role):
                unit = (FORWARD / f"{wrapper}.service").read_text(encoding="utf-8")
                command = next(line.removeprefix("ExecStart=") for line in unit.splitlines() if line.startswith("ExecStart="))
                arguments = shlex.split(command.replace("%h", str(self.home)))[1:]
                self.assertEqual(arguments, ["--config", str(self.config)])
                result = subprocess.run(
                    ["bash", str(FORWARD / wrapper), *arguments],
                    env={**self.env, "XDG_CONFIG_HOME": str(self.root / "user-manager-config")},
                    capture_output=True, text=True, timeout=30, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.splitlines(), ["exec", "--", "forward", role, "--config", str(self.config)])

    def test_serve_refuses_an_unknown_vm_before_changing_config_or_units(self) -> None:
        install = self.install("serve", STUB_HOSTNAME="unknown-vm")
        self.assertNotEqual(install.returncode, 0)
        self.assertIn("unknown-vm", install.stderr)
        self.assertIn("hosts.toml", install.stderr)
        self.assertFalse(self.config.exists())
        self.assertFalse(self.systemctl_log.exists())

    def test_failed_or_invalid_lookup_preserves_previous_config_and_services(self) -> None:
        for role, lookup in (("daemon", "sami-devbox"), ("daemon", "self"), ("serve", "sami"), ("serve", "self-vm")):
            for answer in (None, "", "not-an-ip\n", "192.0.2.3\n", "100.80.0.8\n100.80.0.9\n"):
                with self.subTest(role=role, lookup=lookup, answer=answer):
                    self.config.parent.mkdir(parents=True, exist_ok=True)
                    self.config.write_text('listen = "127.0.0.9"\n', encoding="utf-8")
                    prior = self.config.read_bytes()
                    if self.systemctl_log.exists():
                        self.systemctl_log.unlink()
                    path = self.lookups / lookup
                    original = path.read_bytes()
                    if answer is None:
                        path.unlink()
                    else:
                        path.write_text(answer, encoding="utf-8")
                    install = self.install(role)
                    path.write_bytes(original)
                    self.assertNotEqual(install.returncode, 0)
                    self.assertIn("tailscale", install.stderr)
                    self.assertIn("ip -4", install.stderr)
                    self.assertEqual(self.config.read_bytes(), prior)
                    self.assertFalse(self.systemctl_log.exists())

    def test_invalid_inventory_fails_before_writing_config_or_changing_units(self) -> None:
        inventory = self.dotfiles / "forward/hosts.toml"
        for contents in (
            'laptop = "sami"\ndev_vms = []\n',
            'laptop = "sami"\ndev_vms = ["oryx", "oryx"]\n',
            'laptop = "sami"\ndev_vms = ["bad name"]\n',
            'laptop = "sami"\ndev_vms = ["sami"]\n',
        ):
            with self.subTest(contents=contents):
                inventory.write_text(contents, encoding="utf-8")
                install = self.install("daemon")
                self.assertNotEqual(install.returncode, 0)
                self.assertIn("hosts.toml", install.stderr)
                self.assertFalse(self.config.exists())
                self.assertFalse(self.systemctl_log.exists())

    def test_duplicate_peer_address_is_not_silently_accepted(self) -> None:
        (self.lookups / "sami-devbox").write_text("100.80.0.2\n", encoding="utf-8")
        install = self.install("daemon")
        self.assertNotEqual(install.returncode, 0)
        self.assertIn("oryx", install.stderr)
        self.assertIn("sami-devbox", install.stderr)
        self.assertFalse(self.config.exists())
        self.assertFalse(self.systemctl_log.exists())

    def test_migration_disables_legacy_instances_without_deleting_artifacts(self) -> None:
        self.legacy_units.write_text(
            "forward-daemon@oryx.service enabled enabled\n"
            "forward-tunnel@sami-devbox.service loaded active running Tunnel\n"
            "forward-tunnel@retired-vm.service enabled enabled\n"
            "forward-daemon@.service disabled enabled\n",
            encoding="utf-8",
        )
        user_dir = self.home / ".config/systemd/user"
        forward_dir = self.home / ".config/forward"
        ssh_dir = self.home / ".ssh"
        user_dir.mkdir(parents=True)
        forward_dir.mkdir(parents=True)
        ssh_dir.mkdir()
        legacy = {
            path: path.read_bytes()
            for path in (self.dotfiles / "forward").glob("config-*-*.toml")
        }
        for unit in ("forward-daemon@.service", "forward-tunnel@.service"):
            (user_dir / unit).symlink_to(self.dotfiles / "forward" / unit)
        (forward_dir / "config-oryx.toml").symlink_to(self.dotfiles / "forward/config-daemon-oryx.toml")
        for name in ("config.local", "authorized_keys", "forward-tunnel"):
            (ssh_dir / name).write_text("keep me\n", encoding="utf-8")
        self.config.symlink_to(self.dotfiles / "forward/config-daemon-oryx.toml")
        install = self.install("daemon")
        self.assertEqual(install.returncode, 0, install.stderr)
        log = self.systemctl_log.read_text().splitlines()
        for unit in ("forward-daemon@oryx.service", "forward-tunnel@sami-devbox.service", "forward-tunnel@retired-vm.service"):
            self.assertIn(f"--user disable --now {unit}", log)
        self.assertNotIn("--user disable --now forward-daemon@.service", log)
        for path, contents in legacy.items():
            self.assertEqual(path.read_bytes(), contents)
        for unit in ("forward-daemon@.service", "forward-tunnel@.service"):
            self.assertTrue((user_dir / unit).is_symlink())
        self.assertTrue((forward_dir / "config-oryx.toml").is_symlink())
        for name in ("config.local", "authorized_keys", "forward-tunnel"):
            self.assertEqual((ssh_dir / name).read_text(), "keep me\n")
        self.assertFalse(self.config.is_symlink())

    def test_systemd_failure_does_not_report_a_successful_install(self) -> None:
        for command in ("--user enable --now forward-daemon", "--user restart forward-daemon"):
            with self.subTest(command=command):
                install = self.install("daemon", SYSTEMCTL_FAIL=command)
                self.assertNotEqual(install.returncode, 0)
                self.assertIn("forward-daemon", install.stderr)

    def test_roles_remove_only_stale_ambient_relay_configuration(self) -> None:
        for role in ("serve", "daemon"):
            with self.subTest(role=role):
                environment_dir = self.config_home / "environment.d"
                environment_dir.mkdir(parents=True, exist_ok=True)
                (environment_dir / "browser-relay.conf").write_text("BROWSER_RELAY_URL=http://stale.test\n")
                omp_dir = self.config_home / "omp"
                omp_dir.mkdir(exist_ok=True)
                overlay = omp_dir / "browser-relay.yml"
                overlay.symlink_to(self.dotfiles / "gone.yml")
                install = self.install(role)
                self.assertEqual(install.returncode, 0, install.stderr)
                self.assertFalse((environment_dir / "browser-relay.conf").exists())
                self.assertFalse(overlay.is_symlink())

    def test_shell_does_not_gain_an_ambient_relay_endpoint(self) -> None:
        shell = subprocess.run(
            ["bash", "-c", 'source "$1"; printf "%s" "${BROWSER_RELAY_URL-}"', "--", str(BASHRC)],
            capture_output=True, text=True, env=self.env, timeout=30, check=False,
        )
        self.assertEqual(shell.returncode, 0, shell.stderr)
        self.assertEqual(shell.stdout, "")


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
    """A tunnelled pair listens on loopback and travels over forward-tunnel@<pair>."""
    return config["listen"].startswith("127.")


class CommittedLegacyForwardPairs(unittest.TestCase):
    """The retained legacy pairs remain internally consistent until step 4."""

    def pairs(self) -> list[tuple[str, str]]:
        pairs = []
        for serve in sorted(FORWARD.glob("config-serve-*.toml")):
            host = serve.name.removeprefix("config-serve-").removesuffix(".toml")
            pairs.append((serve.name, f"config-daemon-{host}.toml"))
        return pairs

    def test_each_devbox_and_laptop_daemon_trust_each_other(self) -> None:
        daemons = {daemon_name for _, daemon_name in self.pairs()}
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
        """Every pair's daemon has the same URL policy; only its own preview host differs."""
        pairs = self.pairs()
        self.assertGreater(len(pairs), 1, "nothing to compare")
        configs = {daemon_name: load(daemon_name) for _, daemon_name in pairs}
        own = {
            daemon_name: preview_entries(configs[daemon_name]["allow"], load(serve_name)["listen"])
            for serve_name, daemon_name in pairs
        }
        for daemon_name, entries in own.items():
            self.assertTrue(entries, f"{daemon_name} does not allow its own preview host")
        first_name, first = next(iter(configs.items()))
        shared = set(first["allow"]) - own[first_name]
        for daemon_name, pair in configs.items():
            with self.subTest(pair=daemon_name):
                for key in ("mode", "opener", "clipboard"):
                    self.assertEqual(pair[key], first[key], key)
                self.assertEqual(set(pair["allow"]) - own[daemon_name], shared)


if __name__ == "__main__":
    unittest.main()
