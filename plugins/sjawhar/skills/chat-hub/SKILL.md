---
name: chat-hub
description: Use when Sami mentions texting someone, a text, a DM or "did X reply", even without naming the chat hub. Also for SMS/RCS, WhatsApp, Signal, Discord, Instagram or Messenger - reading, sending, unread, finding a conversation, re-linking an expired login. Not for Slack (slack-messaging, slack-search) or email. Inbox work (read, search, archive, labels, threaded replies, drafts, attachments) uses `mailbox`. Other Gmail (a new gws send), Drive and Docs use google-workspace.
---

# Chat hub

The chat hub bridges Sami's chat networks into one private Matrix server on oryx. Each conversation is a Matrix room of `@sami:chathub`. The `chathub` MCP (mindroom-ai's matrix-mcp) reads and sends in these rooms. It loads only in omp sessions started in the dotfiles repo (`~/.dotfiles/.omp/mcp.json`). On every network but SMS/RCS a send goes out **as Sami, on his real accounts**. SMS/RCS has two logins, Sami's phone and the agents' phone, and an SMS room sends from the number of its login. A room's login is `content.channel["fi.mau.receiver"]` in its `m.bridge` state event (the bridge's description of the room); "Sending" has the command that reads it. The hub listens on oryx's loopback only and its `MATRIX_HUB_*` secrets are in oryx's `secrets.local.env`, so it works only in a session on oryx.

## Networks

| Network | People | Bridge bot |
|---|---|---|
| SMS/RCS (Google Messages) | `@gmessages_…:chathub` | `@gmessagesbot:chathub` |
| WhatsApp | `@whatsapp_…:chathub` | `@whatsappbot:chathub` |
| Signal | `@signal_…:chathub` | `@signalbot:chathub` |
| Messenger | `@facebook_…:chathub` | `@metabot:chathub` |
| Instagram | `@instagram_…:chathub` | `@instagrambot:chathub` |
| Discord | `@discord_…:chathub` | `@discordbot:chathub` |
| LinkedIn (messages only) | `@linkedin_…:chathub` | `@linkedinbot:chathub` |

A room name is the contact or group name from the network. The DM with a bridge bot is the control room of that network.

## Reading

- New messages: `matrix_get_unread`. All rooms: `matrix_list_rooms`. Network of a room: `matrix_list_room_members`.
- Read with `matrix_read_history` or `matrix_get_unread`; both keep the room unread.
- Call `matrix_mark_read` only when Sami asks: read receipts go back to the network and tell the sender that Sami saw it.
- Attachments: `matrix_download_media` with the `mxc://` URL of the event.

## Sending

- In a room of Sami's login (every network but SMS/RCS, and an SMS room whose login is Sami's): to a person Sami has not written to on that network, show Sami the exact text and the network, and send only after he approves, because the message goes out as him. In a room where Sami has written, send what he asked for without showing it first. A room of the agents' phone sends as the agent, under the agent's own instructions.
- New message: `matrix_send_message` (room, body). Answer to one message: `matrix_reply`.
- No room yet: send `start-chat <ID or phone number>` to the bridge bot of that network. For SMS, first send `list-logins` to `@gmessagesbot:chathub`, then `start-chat <login ID> <phone number>` with the login of the number to send from. With no login ID, or one the bridge does not know, the bridge uses Sami's login. Before the first send in an SMS room (the room `start-chat` names, new or already there, or one you found), read the room's login and compare it with the login ID you chose; when they differ, send nothing in that room: `secrets MATRIX_HUB_URL MATRIX_HUB_TOKEN -- sh -c 'printf "Authorization: Bearer %s\n" "$MATRIX_HUB_TOKEN" | curl -fsS -H @- "$MATRIX_HUB_URL/_matrix/client/v3/rooms/$1/state"' _ '<room ID>' | jq -r '.[] | select(.type == "m.bridge") | .content.channel["fi.mau.receiver"]'`. `help` in the bot DM lists its commands; the Discord bot's differ, so ask it.
- Discord server channel (servers are not bridged by default):
  1. Find the channel id in `~/.local/share/chathub/discord/discord.db` on oryx (`portal` table, rows with the `dc_guild_id` of that server).
  2. Send the Discord bot `guilds bridging-mode <server id> if-portal-exists`, then `create-portal <channel id>`. Only that channel joins the hub.
  3. Send with `matrix_send_message` in the new room.

## Logins

When a bot posts that a login expired or ended, tell Sami which network. Re-link it as the table says: the Google Messages login of the agents' phone without him, every other login with him. Do all of it on oryx; install or change nothing on Sami's machine.

| Network | Re-link |
|---|---|
| WhatsApp, Signal, Discord (QR code) | Run `~/.dotfiles/chathub/login-screen` and keep it running, then `secrets MATRIX_HUB_URL MATRIX_HUB_TOKEN -- ~/.dotfiles/chathub/login [--keep MINUTES] <net> <cmd>`: `whatsapp login qr`, `signal login qr`, `discord login-qr`. Send Sami `http://localhost:6080/` (the screen holds the port on his laptop with `forward port`). The page follows each rotated code; `--keep` starts the login again when the bot reports a code ran out. One `login` run at a time shows its code. Exit 0 means linked. Stop the screen when he finishes. |
| Google Messages, Messenger, Instagram, LinkedIn (website sign-in) | Run `~/.dotfiles/chathub/login-screen` and keep it running (no VNC: mautrix-manager's Chromium debugging port 9347 is held on his laptop with `forward port`). Send Sami the `Window "mautrix-manager":` DevTools link it prints; he drives the window in the DevTools screencast. The manager is signed in to the hub as `sami` (`MATRIX_HUB_PASSWORD`). He picks the network and signs in; a sign-in opens a second window, whose link the script prints next. Google Messages has no QR login any more (Google ended it): for Sami's login he signs in to Google, then taps the emoji on his phone. The Google Messages login of the agents' phone needs no step by Sami: an agent pairs it again by the steps in section "Logins" of `skills/majordomo/references/sites/chat-hub.md` in the private repository `sjawhar/dojo`. A sign-in nobody finished in 30 minutes is dropped by the bridge: restart the script. Stop it when he finishes. |

## Running the hub (on oryx)

- Status: `systemctl --user status tuwunel 'mautrix@*'`. Logs: `journalctl --user -u mautrix@<network>` (gmessages, whatsapp, signal, meta for Messenger, instagram, discord, linkedin).
- A restart of `tuwunel` restarts all bridges.
- State: `~/.local/share/chathub/`. Re-run `~/.dotfiles/installers/chathub.sh` to restore every bridge config to what the hub needs; it overwrites hand edits and keeps the bridge tokens.
- Design: `~/.dotfiles/docs/plans/2026-10-03-chat-hub-design.md`.
