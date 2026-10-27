---
name: chat-hub
description: Use when Sami mentions texting someone, a text, a DM or "did X reply", even without naming the chat hub. Also for SMS/RCS, WhatsApp, Signal, Discord, Instagram or Messenger - reading, sending, unread, finding a conversation, re-linking an expired login. Not for Slack (slack-messaging, slack-search) or email. Inbox work (read, search, archive, labels, threaded replies, drafts, attachments) uses `mailbox`. Other Gmail (a new gws send), Drive and Docs use google-workspace.
---

# Chat hub

The chat hub bridges Sami's chat networks into one private Matrix server on oryx. Each conversation is a Matrix room of `@sami:chathub`. The `chathub` MCP (mindroom-ai's matrix-mcp) reads and sends **as Sami, on his real accounts and real phone number**. It loads only in omp sessions started in the dotfiles repo (`~/.dotfiles/.omp/mcp.json`).

## Networks

| Network | People | Bridge bot |
|---|---|---|
| SMS/RCS (Google Messages) | `@gmessages_…:chathub` | `@gmessagesbot:chathub` |
| WhatsApp | `@whatsapp_…:chathub` | `@whatsappbot:chathub` |
| Signal | `@signal_…:chathub` | `@signalbot:chathub` |
| Messenger | `@facebook_…:chathub` | `@metabot:chathub` |
| Instagram | `@instagram_…:chathub` | `@instagrambot:chathub` |
| Discord | `@discord_…:chathub` | `@discordbot:chathub` |

A room name is the contact or group name from the network. The DM with a bridge bot is the control room of that network.

## Reading

- New messages: `matrix_get_unread`. All rooms: `matrix_list_rooms`. Network of a room: `matrix_list_room_members`.
- Read with `matrix_read_history` or `matrix_get_unread`; both keep the room unread.
- Call `matrix_mark_read` only when Sami asks: read receipts go back to the network and tell the sender that Sami saw it.
- Attachments: `matrix_download_media` with the `mxc://` URL of the event.

## Sending

- To a person Sami has not written to on that network, show Sami the exact text and the network, and send only after he approves, because the message goes out as him. In a room where Sami has written, send what he asked for without showing it first.
- New message: `matrix_send_message` (room, body). Answer to one message: `matrix_reply`.
- No room yet: send `start-chat <phone number or ID>` to the bridge bot of that network. `help` in the bot DM lists its commands; the Discord bot's differ, so ask it.
- Discord server channel (servers are not bridged by default):
  1. Find the channel id in `~/.local/share/chathub/discord/discord.db` on oryx (`portal` table, rows with the `dc_guild_id` of that server).
  2. Send the Discord bot `guilds bridging-mode <server id> if-portal-exists`, then `create-portal <channel id>`. Only that channel joins the hub.
  3. Send with `matrix_send_message` in the new room.

## Logins

When a bot posts that a login expired or ended, tell Sami which network and re-link it. Do all of it on oryx; install or change nothing on Sami's machine.

| Network | Re-link |
|---|---|
| WhatsApp, Signal, Discord (QR code) | `secrets MATRIX_HUB_URL MATRIX_HUB_TOKEN -- ~/.dotfiles/chathub/login <net> <cmd>`: `whatsapp login qr`, `signal login qr`, `discord login-qr`. It opens the QR in Sami's browser through `forward open`. A rotated code overwrites the same file: refresh the tab. Exit 0 means linked. |
| Google Messages, Messenger, Instagram (website sign-in) | Run `~/.dotfiles/chathub/login-screen` (it runs `forward port 6080`) and keep it running. Send Sami `http://localhost:6080/vnc.html?autoconnect=1&resize=scale` (mautrix-manager, signed in to the hub). He picks the network and signs in. Google Messages ends with an emoji tap on his phone. Stop the script when he finishes. |

## Running the hub (on oryx)

- Status: `systemctl --user status tuwunel 'mautrix@*'`. Logs: `journalctl --user -u mautrix@<network>` (gmessages, whatsapp, signal, meta for Messenger, instagram, discord).
- A restart of `tuwunel` restarts all bridges.
- State: `~/.local/share/chathub/`. Re-run `~/.dotfiles/installers/chathub.sh` to restore every bridge config to what the hub needs; it overwrites hand edits and keeps the bridge tokens.
- Design: `~/.dotfiles/docs/plans/2026-10-03-chat-hub-design.md`.
