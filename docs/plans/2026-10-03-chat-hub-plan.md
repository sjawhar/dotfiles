# Chat hub Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Matrix hub on oryx that bridges SMS/RCS, WhatsApp, Signal, Discord, Instagram and Messenger, with one MCP that gives agents on any tailnet machine read/send access.

**Architecture:** Tuwunel (server name `chathub`) runs as a user service on oryx. Six mautrix bridges run as instances of one template unit, each rendered from the bridge's own example config plus explicit yq assignments. Agents reach the hub through mindroom-ai's `matrix-mcp`, launched per session with the hub token from agent-tier secrets.

**Tech Stack:** Tuwunel 1.9.3, mautrix bridges v26.09 (bridgev2) and mautrix-discord 0.7.7 (legacy config), matrix-mcp 0.8.1 (PyPI), mise pins, yq 4, systemd user units, bash installer with Python unittest coverage.

**Spec:** `docs/plans/2026-10-03-chat-hub-design.md`

## Global Constraints

- Server name `chathub`; the one Matrix user is `@sami:chathub`.
- Federation off, registration off, encryption off.
- Tuwunel listens on `127.0.0.1:8008` and oryx's tailnet IPv4 `:8008`; bridges listen on loopback only.
- Runtime state lives under `${XDG_DATA_HOME:-$HOME/.local/share}/chathub` (mode 0700) and is never committed.
- No committed absolute paths; units use `%h`, scripts use `$HOME` / `$DOTFILES_DIR`.
- Every tool binary is a `mise.toml` pin.
- Agent-tier secrets: `MATRIX_HUB_URL`, `MATRIX_HUB_TOKEN`, `MATRIX_HUB_PASSWORD`.

## Files

| Path | Responsibility |
|---|---|
| `mise.toml` | Pins: Tuwunel, six bridges (Instagram through a `[tool_alias]`, since it shares `mautrix/meta` with Messenger), `pipx:matrix-mcp` |
| `chathub/tuwunel.toml` | Committed Tuwunel config without machine paths |
| `chathub/tuwunel-serve` | Launcher: adds state paths and the tailnet bind address with `-O` |
| `chathub/tuwunel.service` | User unit for Tuwunel |
| `chathub/bridge-serve` | Launcher for one bridge instance: `mautrix-<name> -c <state>/<name>/config.yaml -n` |
| `chathub/mautrix@.service` | Template unit, one instance per bridge |
| `installers/chathub.sh` | Role installer (oryx only): pins, state dirs, render bridge configs, registrations, units |
| `chathub/bootstrap-user` | One-time: create `@sami:chathub`, log in a device `AGENTS`, store the three secrets |
| `scripts/chathub-mcp` | Per-session MCP launcher: private config dir, `matrix-mcp auth token`, `exec matrix-mcp serve` |
| `omp/mcp.json` | Shared `chathub` MCP entry |
| `scripts/tests/test_chathub_installer.py` | Rendering and idempotency tests |
| `skills/chat-hub/SKILL.md` (agents' skill dir) | How agents use the hub and its sending rules |

## Bridge table (installer constant)

| Instance | Binary | Config layout | Appservice port | Bot |
|---|---|---|---|---|
| gmessages | mautrix-gmessages | bridgev2 | 29336 | gmessagesbot |
| whatsapp | mautrix-whatsapp | bridgev2 | 29318 | whatsappbot |
| signal | mautrix-signal | bridgev2 | 29328 | signalbot |
| meta | mautrix-meta | bridgev2 | 29319 | metabot |
| instagram | mautrix-instagram | bridgev2 | 29320 | instagrambot |
| discord | mautrix-discord | legacy | 29334 | discordbot |

The template unit runs `mautrix-%i`, so each instance name must equal its binary's
suffix (`mautrix-meta` is the Messenger binary).

---

### Task 1: Pins and the Instagram correction to the design

**Files:** Modify `mise.toml`, `docs/plans/2026-10-03-chat-hub-design.md`

- [ ] Add pins:

```toml
[tool_alias]
mautrix-instagram = "github:mautrix/meta"

[tools]
"github:matrix-construct/tuwunel" = { version = "1.9.3", asset_pattern = "*-x86_64-v2-linux-gnu-tuwunel.zst", bin = "tuwunel" }
"github:mautrix/gmessages" = { version = "0.2609.0", asset_pattern = "mautrix-gmessages-amd64", bin = "mautrix-gmessages" }
"github:mautrix/whatsapp" = { version = "0.2609.0", asset_pattern = "mautrix-whatsapp-amd64", bin = "mautrix-whatsapp" }
"github:mautrix/signal" = { version = "0.2609.0", asset_pattern = "mautrix-signal-amd64", bin = "mautrix-signal" }
"github:mautrix/discord" = { version = "0.7.7", asset_pattern = "mautrix-discord-amd64", bin = "mautrix-discord" }
"github:mautrix/meta" = { version = "0.2609.0", asset_pattern = "mautrix-meta-amd64", bin = "mautrix-meta" }
mautrix-instagram = { version = "0.2609.0", asset_pattern = "mautrix-instagram-amd64", bin = "mautrix-instagram" }
"pipx:matrix-mcp" = "0.8.1"
```

(`[tool_alias]` merges into the existing table if `mise.toml` already has one.)

- [ ] In the design doc, replace the two `mautrix-meta (…)` rows with `mautrix-meta`
  (Messenger) and `mautrix-instagram` (Instagram): as of 26.08 they are separate
  binaries from the same release, not modes of one.
- [ ] Verify: `mise install` succeeds, and `mise exec -- tuwunel --version`,
  `mautrix-meta --version`, `mautrix-instagram --version`, `matrix-mcp --help` all run.
- [ ] Commit `chathub: pin Tuwunel, mautrix bridges, matrix-mcp`.

### Task 2: Tuwunel up, `@sami:chathub` and the agent token

**Files:** Create `chathub/tuwunel.toml`, `chathub/tuwunel-serve`, `chathub/tuwunel.service`, `chathub/bootstrap-user`

- [ ] `chathub/tuwunel.toml`:

```toml
[global]
server_name = "chathub"
port = 8008
allow_registration = false
allow_federation = false
allow_encryption = false
trusted_servers = []
new_user_displayname_suffix = ""
```

- [ ] `chathub/tuwunel-serve` (state paths and bind address are per machine, so they
  arrive as `-O` overrides rather than committed values):

```bash
#!/bin/bash
set -euo pipefail
MISE="${DOTFILES_DIR:-$HOME/.dotfiles}/bin/mise"
export MISE_AUTO_INSTALL=0
export MISE_DATA_DIR="${MISE_DATA_DIR:-$HOME/.mise}"
STATE="${XDG_DATA_HOME:-$HOME/.local/share}/chathub"
TAILNET_V4="$(tailscale ip -4)"
exec "$MISE" exec -- tuwunel -c "${DOTFILES_DIR:-$HOME/.dotfiles}/chathub/tuwunel.toml" \
    -O "database_path=\"$STATE/tuwunel\"" \
    -O "appservice_dir=\"$STATE/registrations\"" \
    -O "address=[\"127.0.0.1\", \"$TAILNET_V4\"]" "$@"
```

- [ ] `chathub/tuwunel.service`: `ExecStart=%h/.dotfiles/chathub/tuwunel-serve`,
  `After=network-online.target tailscaled.service`, `Restart=on-failure`,
  `RestartSec=5`, `WantedBy=default.target`.
- [ ] `chathub/bootstrap-user` (run once, then never again): generate a password with
  `openssl rand -hex 24`. Stop the unit, then run `tuwunel-serve -O
  'admin_execute=["users create-user sami <password>", "server shutdown"]'`, then start
  the unit. Log in with `POST /_matrix/client/v3/login` (type `m.login.password`, user
  `sami`, `device_id` `AGENTS`). Append `MATRIX_HUB_URL=http://<tailnet-v4>:8008`,
  `MATRIX_HUB_TOKEN=<access_token>` and `MATRIX_HUB_PASSWORD=<password>` to the shared
  agent-tier file through `secrets edit`, with `EDITOR` set to a script that appends the
  three lines. Never echo the values.
- [ ] Verify: `curl -s http://127.0.0.1:8008/_matrix/client/versions` returns JSON.
  Remote check from sami-agents:
  `secrets MATRIX_HUB_URL MATRIX_HUB_TOKEN -- sh -c 'curl -s -H "Authorization: Bearer $MATRIX_HUB_TOKEN" "$MATRIX_HUB_URL/_matrix/client/v3/account/whoami"'`
  returns `@sami:chathub`. Federation is off:
  `curl -s http://127.0.0.1:8008/_matrix/federation/v1/version` is refused or returns
  M_FORBIDDEN/404.
- [ ] Commit `chathub: Tuwunel server and first-user bootstrap`.

### Task 3: Bridge rendering in the installer (TDD)

**Files:** Create `installers/chathub.sh`, `scripts/tests/test_chathub_installer.py`

Behavior to test, using the repo's existing harness pattern (temp HOME, stubbed
`systemctl`, `tailscale` and `mise` on `PATH`; the `mise` stub's `exec -- mautrix-X -e
-c F` writes a fixture example config, and `-g -c F -r R` writes a registration and
sets tokens in F; real `yq` resolved with `mise which yq`):

- [ ] Test 1, fresh render (bridgev2): after `bash installers/chathub.sh`,
  `$STATE/whatsapp/config.yaml` has `homeserver.address == "http://127.0.0.1:8008"`,
  `homeserver.domain == "chathub"`, `appservice.port == 29318`,
  `appservice.hostname == "127.0.0.1"`, `database.type == "sqlite3-fk-wal"`,
  `database.uri == "file:$STATE/whatsapp/whatsapp.db?_txlock=immediate"`,
  `bridge.permissions == {"@sami:chathub": "admin"}` (exactly; the example's `"*"`
  relay entry must be gone), `encryption.allow == false`, and
  `$STATE/registrations/whatsapp.yaml` exists.
- [ ] Test 2, legacy layout: the same assertions for discord at the legacy paths
  (`appservice.database.*`, `bridge.encryption.allow`, `bridge.permissions`).
- [ ] Test 3, idempotency: a second run leaves `appservice.as_token` unchanged and
  doesn't call `-g` again (the stub counts `-g` calls).
- [ ] Test 4, convergence: change a value in the rendered config (for example
  `homeserver.domain`), rerun, and assert it is back to `chathub`.
- [ ] Run, watch them fail, then implement the installer's render function:

```bash
render_bridge() {  # name port bot layout
    local name=$1 port=$2 bot=$3 layout=$4 dir="$STATE/$1" cfg="$STATE/$1/config.yaml"
    mkdir -p "$dir"
    [ -f "$cfg" ] || "$MISE" exec -- "mautrix-$name" -e -c "$cfg" >/dev/null
    local db="file:$dir/$name.db?_txlock=immediate"
    local common=".homeserver.address = \"http://127.0.0.1:8008\" | .homeserver.domain = \"chathub\" |
        .appservice.address = \"http://127.0.0.1:$port\" | .appservice.hostname = \"127.0.0.1\" |
        .appservice.port = $port | .appservice.id = \"$name\" | .appservice.bot.username = \"$bot\" |
        .bridge.permissions = {\"@sami:chathub\": \"admin\"}"
    if [ "$layout" = bridgev2 ]; then
        "$YQ" -i "$common | .database.type = \"sqlite3-fk-wal\" | .database.uri = \"$db\" |
            .encryption.allow = false | .provisioning.shared_secret = \"disable\" |
            .backfill.enabled = true | .backfill.max_initial_messages = 50" "$cfg"
    else
        "$YQ" -i "$common | .appservice.database.type = \"sqlite3-fk-wal\" |
            .appservice.database.uri = \"$db\" | .bridge.encryption.allow = false" "$cfg"
    fi
    [ -f "$STATE/registrations/$name.yaml" ] \
        || "$MISE" exec -- "mautrix-$name" -g -c "$cfg" -r "$STATE/registrations/$name.yaml" >/dev/null
}
```

- [ ] Run the tests until they pass, plus `shellcheck installers/chathub.sh`.
- [ ] Commit `chathub: installer renders bridge configs and registrations`.

### Task 4: Units, start everything, register bots

**Files:** Create `chathub/bridge-serve`, `chathub/mautrix@.service`; modify `installers/chathub.sh`

- [ ] `chathub/bridge-serve <name>`: the same mise preamble as `tuwunel-serve`, then
  `exec "$MISE" exec -- "mautrix-$1" -c "$STATE/$1/config.yaml" -n`.
- [ ] `chathub/mautrix@.service`: `ExecStart=%h/.dotfiles/chathub/bridge-serve %i`,
  `After=tuwunel.service`, `Requires=tuwunel.service`, `Restart=on-failure`, `RestartSec=10`.
- [ ] Installer tail: link `tuwunel.service` and `mautrix@.service` into
  `~/.config/systemd/user`, `daemon-reload`, enable and start `tuwunel`, then
  `mautrix@<name>` for the six instances. Restart `tuwunel` after any new registration
  (`appservice_dir` is read at startup).
- [ ] Verify: all seven units `active`. A DM from `@sami:chathub` to each bot
  (`@gmessagesbot:chathub` … `@discordbot:chathub`) saying `help` gets a reply. Script it
  with curl against the client API using `MATRIX_HUB_TOKEN`.
- [ ] Commit `chathub: units for Tuwunel and the six bridges`.

### Task 5: Agent MCP

**Files:** Create `scripts/chathub-mcp`; modify `omp/mcp.json`

- [ ] `scripts/chathub-mcp`:

```bash
#!/bin/bash
# omp spawns this per session under `secrets MATRIX_HUB_URL MATRIX_HUB_TOKEN --`.
set -euo pipefail
cfg="$(mktemp -d)"; trap 'rm -rf "$cfg"' EXIT
export XDG_CONFIG_HOME="$cfg"
matrix-mcp auth token "$MATRIX_HUB_URL" "@sami:chathub" "$MATRIX_HUB_TOKEN" --device-id AGENTS >/dev/null
matrix-mcp serve
```

(Not `exec`: the trap has to remove the token copy when the server exits.)

- [ ] `omp/mcp.json`: `"chathub": {"command": "secrets", "args": ["MATRIX_HUB_URL", "MATRIX_HUB_TOKEN", "--", "chathub-mcp"]}`.
- [ ] Verify on oryx **and** on sami-agents: in a fresh omp session the chathub tools
  appear, and `matrix_whoami` returns `@sami:chathub` with device `AGENTS`.
- [ ] Commit `chathub: matrix-mcp entry for agents`.

### Task 6: Agent skill

**Files:** Create the `chat-hub` skill in the dotfiles agent skills directory (same place as `slack-bot`)

- [ ] Contents: what the hub is; how to find a person's chat (`matrix_list_rooms` and room
  names, the network shown by the bridge bot or the member IDs `@whatsapp_…`,
  `@signal_…`); the sending rule (anyone without an existing thread gets a draft for
  Sami first; replies follow Sami's instructions for that thread); login expiry
  (bridge notices in the bot DM; tell Sami, and re-login through the bot with the QR
  shown on his laptop using `forward open`).
- [ ] Commit `skills: chat-hub`.

### Task 7: Logins and per-network acceptance (with Sami)

- [ ] For each bridge, DM its bot `login`. Google Messages, WhatsApp, Signal and Discord
  reply with a QR image: download it from the media API, `forward open` it on Sami's
  laptop, and he scans it in the network's app. Meta and Instagram take cookies Sami
  copies from a private window.
- [ ] Acceptance for each network: Sami sends a message from his own device, and
  `matrix_read_room_recent` through the MCP shows it. An agent sends a reply through
  `matrix_send_message`, and Sami confirms it arrived in the native app.

### Task 8: Retire the laptop WhatsApp MCP

**Files:** Delete `installers/whatsapp.sh`, `whatsapp/`; modify `laptop/install.sh`, `installers/AGENTS.md`

- [ ] Remove the WhatsApp MCP installer call and files. On the laptop: `systemctl --user
  disable --now whatsapp-mcp`, remove the `whatsapp` entry from `~/.omp/agent/.mcp.json`,
  and unlink the old WhatsApp linked device from Sami's phone (Sami does this).
- [ ] Commit `whatsapp: retire the laptop MCP; WhatsApp now goes through chathub`.
