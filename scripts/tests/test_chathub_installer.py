#!/usr/bin/env python3
"""installers/chathub.sh renders each bridge's own example config for the hub."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

DOTFILES = Path(__file__).resolve().parents[2]
INSTALLER = DOTFILES / "installers" / "chathub.sh"
REAL_YQ = subprocess.run(
    [str(DOTFILES / "bin" / "mise") if (DOTFILES / "bin" / "mise").exists() else "mise", "which", "yq"],
    capture_output=True, text=True, check=True,
).stdout.strip()

BRIDGEV2_EXAMPLE = """\
network: {}
bridge:
    permissions:
        "*": relay
        "example.com": user
        "@admin:example.com": admin
matrix:
    message_status_events: false
    delivery_receipts: false
    message_error_notices: true
database:
    type: postgres
    uri: postgres://user:password@host/database?sslmode=disable
homeserver:
    address: http://example.localhost:8008
    domain: example.com
appservice:
    address: http://localhost:29318
    hostname: 127.0.0.1
    port: 29318
    id: example
    bot:
        username: examplebot
    as_token: "This value is generated when generating the registration"
    hs_token: "This value is generated when generating the registration"
provisioning:
    shared_secret: generate
backfill:
    enabled: false
    max_initial_messages: 50
encryption:
    allow: false
"""

LEGACY_EXAMPLE = """\
homeserver:
    address: https://matrix.example.com
    domain: example.com
appservice:
    address: http://localhost:29334
    hostname: 0.0.0.0
    port: 29334
    database:
        type: postgres
        uri: postgres://user:password@host/database?sslmode=disable
    id: discord
    bot:
        username: discordbot
    as_token: "This value is generated when generating the registration"
    hs_token: "This value is generated when generating the registration"
bridge:
    message_status_events: false
    encryption:
        allow: true
    permissions:
        "*": relay
        "@admin:example.com": admin
"""

# Stands in for `mise`: `which yq` finds the real yq, `exec -- mautrix-X -e -c F`
# writes the bridge's example config, and `-g -c F -r R` writes a registration and
# fresh tokens the way the bridges do, counting each generation. mautrix-discord
# predates `-e` and rejects it, like the real 0.7.7 binary; `current` reports the pin.
# Every call is logged to MISE_LOG.
MISE_STUB = r"""
echo "$*" >> "$MISE_LOG"
case "${1:-}" in
    which) if [ "${2:-}" = yq ]; then echo "$REAL_YQ"; fi; exit 0 ;;
    current) echo "0.7.7"; exit 0 ;;
    install) exit 0 ;;
    exec) shift; [ "${1:-}" = "--" ] && shift ;;
    *) exit 0 ;;
esac
bin="$1"; shift
mode=""; cfg=""; reg=""
while [ $# -gt 0 ]; do
    case "$1" in
        -e) mode=example ;;
        -g) mode=generate ;;
        -c) cfg="$2"; shift ;;
        -r) reg="$2"; shift ;;
    esac
    shift
done
if [ "$mode" = example ]; then
    if [ "$bin" = mautrix-discord ]; then echo "Unknown flag: e" >&2; exit 1; fi
    cp "$BRIDGEV2_EXAMPLE" "$cfg"
elif [ "$mode" = generate ]; then
    echo "$bin" >> "$GENERATE_LOG"
    token="AS-$RANDOM-$RANDOM"
    "$REAL_YQ" -i ".appservice.as_token = \"$token\"" "$cfg"
    printf 'id: %s\nas_token: %s\n' "${bin#mautrix-}" "$token" > "$reg"
fi
"""

# Stands in for `curl -fsSL URL -o FILE`: serves the legacy example config only at
# the pinned tag's URL, and fails like curl -f does for anything else, or for every
# URL when FAIL_CURL=1.
CURL_STUB = r"""
url=""; out=""
while [ $# -gt 0 ]; do
    case "$1" in
        -o) out="$2"; shift ;;
        http*) url="$1" ;;
    esac
    shift
done
pinned="https://raw.githubusercontent.com/mautrix/discord/v0.7.7/example-config.yaml"
if [ "${FAIL_CURL:-}" != 1 ] && [ "$url" = "$pinned" ]; then
    cp "$LEGACY_EXAMPLE" "$out"
else
    echo "curl: (22) The requested URL returned error: 404" >&2; exit 22
fi
"""

# Stands in for `systemctl`, logging each call; `restart` fails when FAIL_RESTART=1.
SYSTEMCTL_STUB = r"""
echo "$*" >> "$SYSTEMCTL_LOG"
if [ "${FAIL_RESTART:-}" = 1 ] && [ "${2:-}" = restart ]; then exit 1; fi
"""

# Stands in for `touch` when the restart marker cannot be made, as on a full disk: it fails
# for the marker and passes every other path to the real touch at REAL_TOUCH.
TOUCH_STUB = r"""
for arg in "$@"; do
    case "$arg" in
        *tuwunel-restart-pending) echo "touch: cannot touch '$arg': No space left on device" >&2; exit 1 ;;
    esac
done
exec "$REAL_TOUCH" "$@"
"""

RELAY_URL = "http://127.0.0.1:29342"
TOKEN_RE = r"\A[0-9a-f]{64}\Z"

STATUS_EVENT_PATHS: tuple[tuple[str, str], ...] = (
    ("gmessages", "matrix"),
    ("whatsapp", "matrix"),
    ("signal", "matrix"),
    ("meta", "matrix"),
    ("instagram", "matrix"),
    ("discord", "bridge"),
    ("linkedin", "matrix"),
)

JsonPath = tuple[str | int, ...]
_MISSING = object()


def leaf_values(value: object, path: JsonPath = ()) -> dict[JsonPath, object]:
    if isinstance(value, dict):
        if not value:
            return {path: value}
        leaves: dict[JsonPath, object] = {}
        for key, child in value.items():
            leaves.update(leaf_values(child, path + (key,)))
        return leaves
    if isinstance(value, list):
        if not value:
            return {path: value}
        leaves = {}
        for index, child in enumerate(value):
            leaves.update(leaf_values(child, path + (index,)))
        return leaves
    return {path: value}


def changed_leaves(before: object, after: object) -> dict[JsonPath, tuple[object, object]]:
    before_leaves = leaf_values(before)
    after_leaves = leaf_values(after)
    return {
        path: (before_leaves.get(path, _MISSING), after_leaves.get(path, _MISSING))
        for path in before_leaves.keys() | after_leaves.keys()
        if before_leaves.get(path, _MISSING) != after_leaves.get(path, _MISSING)
    }


def write_executable(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/bash\nset -euo pipefail\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class ChathubInstaller(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.dotfiles = self.root / "dotfiles"
        self.home = self.root / "home"
        self.state = self.root / "data" / "chathub"
        stubs = self.root / "bin"
        for d in (self.dotfiles / "bin", self.dotfiles / "chathub", self.home, stubs):
            d.mkdir(parents=True)
        for name in ("tuwunel.service", "mautrix@.service"):
            (self.dotfiles / "chathub" / name).write_text("test\n", encoding="utf-8")
        (self.root / "bridgev2.yaml").write_text(BRIDGEV2_EXAMPLE, encoding="utf-8")
        (self.root / "legacy.yaml").write_text(LEGACY_EXAMPLE, encoding="utf-8")
        write_executable(self.dotfiles / "bin" / "mise", MISE_STUB)
        write_executable(stubs / "systemctl", SYSTEMCTL_STUB)
        write_executable(stubs / "curl", CURL_STUB)
        self.generate_log = self.root / "generate.log"
        self.mise_log = self.root / "mise.log"
        self.systemctl_log = self.root / "systemctl.log"
        self.relay_reg = self.state / "registrations" / "envoy-relay.yaml"
        self.switch = self.state / "envoy-relay.on"
        self.marker = self.state / "tuwunel-restart-pending"
        self.env = {
            **os.environ,
            "DOTFILES_DIR": str(self.dotfiles),
            "HOME": str(self.home),
            "XDG_DATA_HOME": str(self.root / "data"),
            "PATH": f"{stubs}:{os.environ['PATH']}",
            "SYSTEMCTL_LOG": str(self.systemctl_log),
            "MISE_LOG": str(self.mise_log),
            "GENERATE_LOG": str(self.generate_log),
            "REAL_YQ": REAL_YQ,
            "BRIDGEV2_EXAMPLE": str(self.root / "bridgev2.yaml"),
            "LEGACY_EXAMPLE": str(self.root / "legacy.yaml"),
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def run_installer(self, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(INSTALLER), *args], capture_output=True, text=True, env={**self.env, **env}, check=False
        )

    def install(self, *args: str, **env: str) -> None:
        result = self.run_installer(*args, **env)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def stub_calls(self) -> str:
        """What the mise and systemctl stubs logged; empty when neither ran."""
        return "".join(log.read_text(encoding="utf-8") for log in (self.mise_log, self.systemctl_log) if log.exists())

    def clear_logs(self) -> None:
        for log in (self.mise_log, self.systemctl_log):
            log.write_text("", encoding="utf-8")

    def tuwunel_restarts(self) -> int:
        return self.systemctl_log.read_text(encoding="utf-8").splitlines().count("--user restart tuwunel")

    def marker_fails(self) -> dict[str, str]:
        """The environment of one run whose `touch` cannot make the restart marker."""
        real_touch = shutil.which("touch")
        if real_touch is None:
            self.fail("no touch on PATH")
        failing = self.root / "failing-touch"
        failing.mkdir(exist_ok=True)
        write_executable(failing / "touch", TOUCH_STUB)
        return {"PATH": f"{failing}:{self.env['PATH']}", "REAL_TOUCH": real_touch}

    def relay_tokens(self) -> tuple[str, str]:
        """The relay registration's tokens, each 64 lower-case hex, and not equal."""
        reg = self.yaml(self.relay_reg)
        tokens = (reg["as_token"], reg["hs_token"])
        for token in tokens:
            self.assertRegex(token, TOKEN_RE)
        self.assertNotEqual(tokens[0], tokens[1])
        return tokens

    def config(self, bridge: str) -> dict[str, Any]:
        return self.yaml(self.state / bridge / "config.yaml")

    def yaml(self, path: Path) -> dict[str, Any]:
        out = subprocess.run([REAL_YQ, "-o=json", ".", str(path)], capture_output=True, text=True, check=True).stdout
        return json.loads(out)

    def test_bridgev2_config_points_at_the_hub_and_only_sami_may_use_it(self) -> None:
        self.install()

        c = self.config("whatsapp")
        self.assertEqual(c["homeserver"]["address"], "http://127.0.0.1:8008")
        self.assertEqual(c["homeserver"]["domain"], "chathub")
        self.assertEqual(c["appservice"]["hostname"], "127.0.0.1")
        self.assertEqual(c["appservice"]["port"], 29318)
        self.assertEqual(c["appservice"]["address"], "http://127.0.0.1:29318")
        self.assertEqual(c["database"]["type"], "sqlite3-fk-wal")
        self.assertEqual(c["database"]["uri"], f"file:{self.state}/whatsapp/whatsapp.db?_txlock=immediate")
        self.assertEqual(c["bridge"]["permissions"], {"@sami:chathub": "admin"})
        self.assertFalse(c["encryption"]["allow"])
        # mautrix-manager logs in through the provisioning API with the hub token.
        self.assertEqual(c["provisioning"]["shared_secret"], "generate")
        self.assertTrue(c["provisioning"]["allow_matrix_auth"])
        self.assertTrue((self.state / "registrations" / "whatsapp.yaml").is_file())

    def test_legacy_discord_config_uses_its_own_layout(self) -> None:
        self.install()

        c = self.config("discord")
        self.assertEqual(c["homeserver"]["domain"], "chathub")
        self.assertEqual(c["appservice"]["hostname"], "127.0.0.1")
        self.assertEqual(c["appservice"]["database"]["type"], "sqlite3-fk-wal")
        self.assertEqual(c["appservice"]["database"]["uri"], f"file:{self.state}/discord/discord.db?_txlock=immediate")
        self.assertEqual(c["bridge"]["permissions"], {"@sami:chathub": "admin"})
        self.assertFalse(c["bridge"]["encryption"]["allow"])
        # DMs carry history like the other bridges, and messages sent while the bridge was
        # down are fetched when it starts again.
        self.assertEqual(c["bridge"]["backfill"]["forward_limits"]["initial"]["dm"], 50)
        self.assertEqual(c["bridge"]["backfill"]["forward_limits"]["missed"]["dm"], -1)
        self.assertNotIn("database", c)

    def test_every_bridge_posts_a_send_status(self) -> None:
        """A bridge records each outbound result, so hub-send can report that a message sent."""
        self.install()

        for bridge, section in STATUS_EVENT_PATHS:
            with self.subTest(bridge=bridge):
                matrix_or_bridge = self.config(bridge)[section]
                self.assertTrue(matrix_or_bridge["message_status_events"])
                if section == "matrix":
                    self.assertFalse(matrix_or_bridge["delivery_receipts"])
                    self.assertTrue(matrix_or_bridge["message_error_notices"])

    def test_every_bridge_double_puppets_through_one_hub_registration(self) -> None:
        """Bridges act as @sami through the doublepuppet registration, so they join new
        chat rooms for him instead of leaving invites that nobody accepts."""
        self.install()

        path = self.state / "registrations" / "doublepuppet.yaml"
        reg = self.yaml(path)
        self.assertIsNone(reg["url"])  # Tuwunel sends no events to a registration without a url
        self.assertEqual(reg["namespaces"]["users"], [{"regex": "@.*:chathub", "exclusive": False}])
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        secret = {"chathub": f"as_token:{reg['as_token']}"}
        for bridge in ("gmessages", "whatsapp", "signal", "meta", "instagram", "linkedin"):
            self.assertEqual(self.config(bridge)["double_puppet"]["secrets"], secret, bridge)
        self.assertEqual(self.config("discord")["bridge"]["login_shared_secret_map"], secret)

    def test_people_are_named_as_in_samis_contacts(self) -> None:
        """One user, so the names from Sami's contacts come first; the bridges leave them
        out by default only so they don't leak between users of a shared bridge."""
        self.install()

        whatsapp = self.config("whatsapp")["network"]["displayname_template"]
        self.assertTrue(whatsapp.startswith("{{or .FullName .BusinessName .PushName .Phone"), whatsapp)
        signal = self.config("signal")["network"]["displayname_template"]
        self.assertTrue(signal.startswith("{{or .Nickname .ContactName .ProfileName .PhoneNumber"), signal)

    def test_every_bridge_listens_on_its_own_port(self) -> None:
        self.install()

        bridges = ("gmessages", "whatsapp", "signal", "meta", "instagram", "discord", "linkedin")
        ports = [self.config(b)["appservice"]["port"] for b in bridges]
        self.assertEqual(len(set(ports)), len(bridges), ports)

    def test_rerun_keeps_tokens_and_does_not_regenerate_registrations(self) -> None:
        self.install()
        token = self.config("signal")["appservice"]["as_token"]
        double_puppet = self.yaml(self.state / "registrations" / "doublepuppet.yaml")["as_token"]
        self.clear_logs()

        self.install()

        self.assertEqual(self.config("signal")["appservice"]["as_token"], token)
        self.assertEqual(self.yaml(self.state / "registrations" / "doublepuppet.yaml")["as_token"], double_puppet)
        generated = self.generate_log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(generated.count("mautrix-signal"), 1)
        # Restarting Tuwunel restarts every bridge; a rerun with no new registration must not.
        self.assertEqual(self.tuwunel_restarts(), 0)

    def test_rerun_restores_drift_and_restarts_only_that_bridge(self) -> None:
        """A bridge reads its config at startup, so a corrected config needs a restart;
        bridges whose config did not change must keep running."""
        self.install()
        cfg = self.state / "gmessages" / "config.yaml"
        subprocess.run([REAL_YQ, "-i", '.homeserver.domain = "elsewhere"', str(cfg)], check=True)
        self.clear_logs()

        self.install()

        self.assertEqual(self.config("gmessages")["homeserver"]["domain"], "chathub")
        lines = self.systemctl_log.read_text(encoding="utf-8").splitlines()
        restarts = [l for l in lines if "try-restart mautrix@" in l]
        self.assertEqual(restarts, ["--user try-restart mautrix@gmessages"])

    def test_rerun_turns_on_send_status_and_restarts_each_bridge_once(self) -> None:
        """Configs from before send statuses converge without changing other leaves, and each
        corrected bridge restarts once."""
        self.install()
        for bridge, section in STATUS_EVENT_PATHS:
            cfg = self.state / bridge / "config.yaml"
            subprocess.run([REAL_YQ, "-i", f".{section}.message_status_events = false", str(cfg)], check=True)
        before = {bridge: self.config(bridge) for bridge, _ in STATUS_EVENT_PATHS}
        self.clear_logs()

        self.install()

        for bridge, section in STATUS_EVENT_PATHS:
            with self.subTest(bridge=bridge):
                self.assertEqual(
                    changed_leaves(before[bridge], self.config(bridge)),
                    {(section, "message_status_events"): (False, True)},
                )
        lines = self.systemctl_log.read_text(encoding="utf-8").splitlines()
        restarts = [line for line in lines if "try-restart" in line]
        self.assertEqual(
            restarts,
            [f"--user try-restart mautrix@{bridge}" for bridge, _ in STATUS_EVENT_PATHS],
        )
        self.assertEqual(self.tuwunel_restarts(), 0)

    def test_state_is_private(self) -> None:
        self.install()

        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o700)

    def test_relay_registration_alone_writes_only_the_relay_registration(self) -> None:
        """The registration-only call needs neither mise nor systemctl, and keeps its tokens."""
        self.install("relay-registration")

        reg = self.yaml(self.relay_reg)
        self.assertEqual(
            list(reg), ["id", "url", "as_token", "hs_token", "sender_localpart", "rate_limited", "namespaces"]
        )
        self.assertEqual(reg["id"], "envoy-relay")
        self.assertIsNone(reg["url"])  # Tuwunel sends no events to a registration without a url
        self.assertEqual(reg["sender_localpart"], "envoy-relay")
        self.assertIs(reg["rate_limited"], False)
        # Anchored: Tuwunel matches a namespace unanchored, and this one is Sami alone.
        self.assertEqual(reg["namespaces"], {"users": [{"regex": "^@sami:chathub$", "exclusive": False}]})
        tokens = self.relay_tokens()
        self.assertEqual(stat.S_IMODE(self.relay_reg.stat().st_mode), 0o600)
        self.assertEqual([p.name for p in (self.state / "registrations").iterdir()], ["envoy-relay.yaml"])
        self.assertEqual(list(self.state.glob(".envoy-relay.*")), [])
        self.assertEqual(self.stub_calls(), "")

        self.switch.touch()
        self.install("relay-registration")

        self.assertEqual(self.yaml(self.relay_reg)["url"], RELAY_URL)
        self.assertEqual(self.relay_tokens(), tokens)
        self.assertEqual(self.stub_calls(), "")

    def test_unknown_argument_changes_nothing(self) -> None:
        result = self.run_installer("relay-registraton")

        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        self.assertFalse(self.state.exists())
        self.assertEqual(self.stub_calls(), "")

    def test_switch_change_restarts_tuwunel_once(self) -> None:
        """Tuwunel reads registrations only at startup, so each switch restarts it once, and
        a rerun with the switch unchanged restarts nothing."""
        self.install()
        tokens = self.relay_tokens()
        double_puppet = self.yaml(self.state / "registrations" / "doublepuppet.yaml")
        self.assertTrue(set(tokens).isdisjoint({double_puppet["as_token"], double_puppet["hs_token"]}))

        self.clear_logs()
        self.switch.touch()
        self.install()
        self.assertEqual(self.tuwunel_restarts(), 1)
        self.assertEqual(self.yaml(self.relay_reg)["url"], RELAY_URL)
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.relay_tokens(), tokens)

        self.clear_logs()
        self.install()
        self.assertEqual(self.tuwunel_restarts(), 0)
        self.assertEqual(self.relay_tokens(), tokens)

        self.clear_logs()
        self.switch.unlink()
        self.install()
        self.assertEqual(self.tuwunel_restarts(), 1)
        self.assertIsNone(self.yaml(self.relay_reg)["url"])
        self.assertEqual(self.relay_tokens(), tokens)

    def test_switch_written_alone_is_restarted_by_the_next_full_run(self) -> None:
        self.install()
        self.clear_logs()
        self.switch.touch()

        self.install("relay-registration")
        self.assertEqual(self.stub_calls(), "")

        self.install()
        self.assertEqual(self.tuwunel_restarts(), 1)

    def test_failed_restart_is_retried(self) -> None:
        self.install()
        self.switch.touch()

        self.clear_logs()
        self.install(FAIL_RESTART="1")
        self.assertEqual(self.tuwunel_restarts(), 1)
        self.assertTrue(self.marker.exists())

        self.clear_logs()
        self.install()
        self.assertEqual(self.tuwunel_restarts(), 1)
        self.assertFalse(self.marker.exists())

    def test_first_install_restarts_tuwunel_once(self) -> None:
        """Every registration is new on the first run, and together they need one restart."""
        self.install()

        self.assertEqual(self.tuwunel_restarts(), 1)

    def test_new_bridge_registration_restarts_tuwunel_once(self) -> None:
        """A bridge registration made again on a rerun is new to Tuwunel, so it restarts once."""
        self.install()
        self.clear_logs()
        (self.state / "registrations" / "whatsapp.yaml").unlink()

        self.install()

        self.assertEqual(self.tuwunel_restarts(), 1)
        self.assertFalse(self.marker.exists())

    def test_registration_made_before_a_failed_run_is_restarted_by_the_next_run(self) -> None:
        """The marker is made before the registration, so a run that stops later still leaves
        the restart to the next run. Discord renders after whatsapp and fetches its example
        config with curl only when that config is missing."""
        self.install()
        self.clear_logs()
        (self.state / "registrations" / "whatsapp.yaml").unlink()
        (self.state / "discord" / "config.yaml").unlink()

        result = self.run_installer(FAIL_CURL="1")

        self.assertNotEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertTrue((self.state / "registrations" / "whatsapp.yaml").is_file())
        self.assertTrue(self.marker.exists())
        self.assertEqual(self.tuwunel_restarts(), 0)

        self.install()

        self.assertEqual(self.tuwunel_restarts(), 1)
        self.assertFalse(self.marker.exists())

    def test_switch_change_whose_marker_fails_is_restarted_by_the_next_run(self) -> None:
        """The marker is made before the relay registration is replaced, so a run that cannot
        make it leaves the registration as it was and no temporary file behind, and the next
        run writes it and restarts."""
        self.install()
        self.clear_logs()
        self.switch.touch()

        result = self.run_installer(**self.marker_fails())

        self.assertNotEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIsNone(self.yaml(self.relay_reg)["url"])
        self.assertEqual(self.tuwunel_restarts(), 0)
        self.assertEqual(list(self.state.glob(".envoy-relay.*")), [])

        self.install()

        self.assertEqual(self.tuwunel_restarts(), 1)
        self.assertEqual(self.yaml(self.relay_reg)["url"], RELAY_URL)
        self.assertFalse(self.marker.exists())

        self.clear_logs()
        self.switch.unlink()

        result = self.run_installer("relay-registration", **self.marker_fails())

        self.assertNotEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.yaml(self.relay_reg)["url"], RELAY_URL)
        self.assertEqual(list(self.state.glob(".envoy-relay.*")), [])

        self.install()

        self.assertEqual(self.tuwunel_restarts(), 1)
        self.assertIsNone(self.yaml(self.relay_reg)["url"])
        self.assertFalse(self.marker.exists())

    def test_new_registration_whose_marker_fails_is_restarted_by_the_next_run(self) -> None:
        """The marker is made before a bridge or double-puppet registration is written, so a
        run that cannot make it writes no registration, and the next run writes it and restarts."""
        self.install()
        for name in ("whatsapp.yaml", "doublepuppet.yaml"):
            with self.subTest(registration=name):
                reg = self.state / "registrations" / name
                self.clear_logs()
                reg.unlink()

                result = self.run_installer(**self.marker_fails())

                self.assertNotEqual(result.returncode, 0, result.stderr + result.stdout)
                self.assertFalse(reg.exists())
                self.assertEqual(self.tuwunel_restarts(), 0)

                self.install()

                self.assertEqual(self.tuwunel_restarts(), 1)
                self.assertTrue(reg.is_file())
                self.assertFalse(self.marker.exists())

    def test_unusable_token_is_replaced(self) -> None:
        """Tuwunel stops loading registrations/ at a file it cannot parse, so an empty token
        is made new, never written back."""
        self.relay_reg.parent.mkdir(parents=True)
        self.relay_reg.write_text(f"id: envoy-relay\nurl: null\nas_token:\nhs_token: {'ab' * 32}\n", encoding="utf-8")

        self.install("relay-registration")

        self.relay_tokens()

    def test_unusable_openssl_output_writes_nothing(self) -> None:
        """A token openssl did not make usable stops the run before anything is written, and a
        registration whose tokens are usable is kept without calling openssl."""
        broken = self.root / "broken-openssl"
        broken.mkdir()
        write_executable(broken / "openssl", "echo x")
        path = f"{broken}:{self.env['PATH']}"

        result = self.run_installer("relay-registration", PATH=path)

        self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
        self.assertFalse(self.relay_reg.exists())
        self.assertFalse(self.marker.exists())
        self.assertEqual(list(self.state.glob(".envoy-relay.*")), [])

        self.install("relay-registration")
        before = self.relay_reg.read_bytes()

        self.install("relay-registration", PATH=path)

        self.assertEqual(self.relay_reg.read_bytes(), before)
        self.assertEqual(list(self.state.glob(".envoy-relay.*")), [])


if __name__ == "__main__":
    unittest.main()
