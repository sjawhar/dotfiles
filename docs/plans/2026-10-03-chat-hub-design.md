# Chat hub: one Matrix server for every chat network

Agents get first-class read/send access to Sami's personal chat networks through a
single self-hosted Matrix server on oryx, with one mautrix bridge per network and one
MCP for agents.

## Decisions (Sami, 2026-10-03)

- Networks: SMS/RCS (Google Messages, Android + Google Fi, RCS on), WhatsApp, Signal,
  Discord, Instagram, Messenger.
- Agents only. Sami keeps the native apps; no human Matrix client to maintain.
- Self-hosted on oryx.
- Matrix hub over an agent-first aggregator (OpenMessage), because it is the only option
  that covers all six networks without writing protocol code.
- Discord and Meta bridges log in as Sami's personal accounts; Sami accepts the
  terms-of-service and account-flag risk.
- Inbound is pull-only in v1: agents read and check unread through the MCP when asked.
  No push to envoy yet.
- Agent MCP: adopt mindroom-ai/matrix-mcp (MIT, maintained, stdio, token auth) rather
  than write one; write our own only if agents struggle with it on the real hub.

## Components

| Piece | What | Where it listens |
|---|---|---|
| Tuwunel | Matrix homeserver, `server_name` `chathub` (host-independent IDs: `@sami:chathub`), federation off, registration off | `127.0.0.1:8008` for bridges; oryx tailnet address `:8008` for agents on other machines (token auth) |
| mautrix-gmessages | SMS/RCS via Google Messages pairing | loopback appservice port |
| mautrix-whatsapp | WhatsApp linked device | loopback |
| mautrix-signal | Signal linked device | loopback |
| mautrix-discord | Discord user login | loopback |
| mautrix-instagram | Instagram DMs; a separate binary from the same mautrix/meta release since 26.08 | loopback |
| mautrix-meta | Messenger | loopback |
| matrix-mcp | Agent tools: list chats, read, read thread, send text/file, members, user search | stdio, spawned per agent session |

All binaries are pinned in `mise.toml` (Tuwunel and the bridges ship linux amd64 release
binaries; matrix-mcp from PyPI). Each server is its own user systemd unit with
`Restart=on-failure`. Bridges use SQLite. Encryption is off inside the hub: every portal
room is unencrypted, so messages are stored in plaintext on oryx's disk.

Bridges double-puppet `@sami` through one URL-less `doublepuppet` registration: they
join every chat room as him, so no invites wait to be accepted, and what he sends from
his phone appears as sent by him. Sami is the only user, so WhatsApp and Signal name
people from his phone's contacts first. The bridges leave that off by default so that
one user's contacts don't leak to the others on a shared bridge.

## State and secrets

- Committed in dotfiles (`chathub/`): config templates, units, the MCP launcher,
  `installers/chathub.sh` (role installer, run on oryx only).
- Generated on oryx, never committed: appservice registrations and their `as_token` /
  `hs_token`, bridge databases, Tuwunel database, under `~/.local/share/chathub/` (0700).
- Agent-tier secrets in the shared file: `MATRIX_HUB_URL` (oryx tailnet URL) and
  `MATRIX_HUB_TOKEN` (an access token for `@sami:chathub`, device `agents`). The MCP entry
  goes in the shared `omp/mcp.json`, because the hub is reachable from every tailnet
  machine, unlike the loopback-only WhatsApp daemon.

## Behavior rules for agents

- A message to anyone Sami has not written to on that network is drafted for Sami first,
  the same as email. Replies in an existing thread follow Sami's instructions for that
  thread.
- Bridge notices about expired logins go to the bridge's management room; agents report
  them to Sami when they see them.

## Logins (once per network, by Sami)

Each bridge is logged in through its bot. WhatsApp, Signal and Discord show a QR code,
which `chathub/login` opens on Sami's laptop with `forward open`. Google Messages,
Messenger and Instagram need a website sign-in: `chathub/login-screen` runs
mautrix-manager on a virtual display on oryx and shows it in Sami's browser through
noVNC and `forward port`. Google Messages finishes with an emoji tap on his phone.

## Done means

For each of the six networks: a message Sami sends to his own account from another
device shows up through the MCP, and a message an agent sends through the MCP arrives in
the native app. Then the old Baileys WhatsApp MCP daemon is retired.

## Out of scope for v1

Push of inbound messages to envoy, end-to-end encryption inside the hub, a human Matrix
client, Slack, Telegram, LinkedIn.
