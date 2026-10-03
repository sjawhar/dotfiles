#!/usr/bin/env python3
"""installers/chathub.sh renders each bridge's own example config for the hub."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

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
MISE_STUB = r"""
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
# the pinned tag's URL, and fails like curl -f does for anything else.
CURL_STUB = r"""
url=""; out=""
while [ $# -gt 0 ]; do
    case "$1" in
        -o) out="$2"; shift ;;
        http*) url="$1" ;;
    esac
    shift
done
if [ "$url" = "https://raw.githubusercontent.com/mautrix/discord/v0.7.7/example-config.yaml" ]; then
    cp "$LEGACY_EXAMPLE" "$out"
else
    echo "curl: (22) The requested URL returned error: 404" >&2; exit 22
fi
"""


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
        write_executable(stubs / "systemctl", 'echo "$*" >> "$SYSTEMCTL_LOG"')
        write_executable(stubs / "curl", CURL_STUB)
        write_executable(stubs / "dpkg", "exit 0")  # login-screen packages count as installed
        self.generate_log = self.root / "generate.log"
        self.env = {
            **os.environ,
            "DOTFILES_DIR": str(self.dotfiles),
            "HOME": str(self.home),
            "XDG_DATA_HOME": str(self.root / "data"),
            "PATH": f"{stubs}:{os.environ['PATH']}",
            "SYSTEMCTL_LOG": str(self.root / "systemctl.log"),
            "GENERATE_LOG": str(self.generate_log),
            "REAL_YQ": REAL_YQ,
            "BRIDGEV2_EXAMPLE": str(self.root / "bridgev2.yaml"),
            "LEGACY_EXAMPLE": str(self.root / "legacy.yaml"),
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def install(self) -> None:
        result = subprocess.run(["bash", str(INSTALLER)], capture_output=True, text=True, env=self.env, check=False)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def config(self, bridge: str) -> dict:
        out = subprocess.run(
            [REAL_YQ, "-o=json", ".", str(self.state / bridge / "config.yaml")],
            capture_output=True, text=True, check=True,
        ).stdout
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
        self.assertNotIn("database", c)

    def test_every_bridge_listens_on_its_own_port(self) -> None:
        self.install()

        bridges = ("gmessages", "whatsapp", "signal", "meta", "instagram", "discord")
        ports = [self.config(b)["appservice"]["port"] for b in bridges]
        self.assertEqual(len(set(ports)), len(bridges), ports)

    def test_rerun_keeps_tokens_and_does_not_regenerate_registrations(self) -> None:
        self.install()
        token = self.config("signal")["appservice"]["as_token"]

        self.install()

        self.assertEqual(self.config("signal")["appservice"]["as_token"], token)
        generated = self.generate_log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(generated.count("mautrix-signal"), 1)

    def test_rerun_restores_drift_and_restarts_only_that_bridge(self) -> None:
        """A bridge reads its config at startup, so a corrected config needs a restart;
        bridges whose config did not change must keep running."""
        self.install()
        cfg = self.state / "gmessages" / "config.yaml"
        subprocess.run([REAL_YQ, "-i", '.homeserver.domain = "elsewhere"', str(cfg)], check=True)
        log = self.root / "systemctl.log"
        log.write_text("", encoding="utf-8")

        self.install()

        self.assertEqual(self.config("gmessages")["homeserver"]["domain"], "chathub")
        restarts = [l for l in log.read_text(encoding="utf-8").splitlines() if "try-restart mautrix@" in l]
        self.assertEqual(restarts, ["--user try-restart mautrix@gmessages"])

    def test_state_is_private(self) -> None:
        self.install()

        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o700)


if __name__ == "__main__":
    unittest.main()
