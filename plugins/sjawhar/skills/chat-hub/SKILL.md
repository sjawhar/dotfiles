---
name: chat-hub
description: Use for Sami's personal chat networks through the chat hub - reading or sending SMS/RCS texts, WhatsApp, Signal, Discord, Instagram or Messenger messages, checking what's unread, finding someone's conversation, or re-linking a network whose login expired. Use it whenever Sami mentions texting someone, a text, WhatsApp, Signal, a DM, "did X reply", or messages on any of those networks, even if he doesn't name the hub. Not for Slack (slack-bot) or email (google-workspace).
---

# Chat hub

Sami's chat networks are bridged into one private Matrix server on oryx. Every
conversation is a Matrix room the user `@sami:chathub` is in, and the `chathub` MCP
(mindroom-ai's matrix-mcp) reads and sends in those rooms **as Sami, on his real
accounts and his real phone number**. The MCP loads only in omp sessions started in the
dotfiles repo (`~/.dotfiles/.omp/mcp.json`); a session started anywhere else does not
have these tools.

## Which network a room is on

Each bridged person is a user whose ID names the network:

| Network | People | Bridge bot |
|---|---|---|
| SMS/RCS (Google Messages) | `@gmessages_…:chathub` | `@gmessagesbot:chathub` |
| WhatsApp | `@whatsapp_…:chathub` | `@whatsappbot:chathub` |
| Signal | `@signal_…:chathub` | `@signalbot:chathub` |
| Messenger | `@facebook_…:chathub` | `@metabot:chathub` |
| Instagram | `@instagram_…:chathub` | `@instagrambot:chathub` |
| Discord | `@discord_…:chathub` | `@discordbot:chathub` |

Room names are the contact or group name the network reported. The DM with each
bridge bot is that network's control room.

## Reading

- `matrix_get_unread` for what's new, `matrix_list_rooms` for everything,
  `matrix_list_room_members` to see which network a room is on.
- Read with `matrix_read_history` or `matrix_get_unread`, which leave the room
  unread. Read receipts travel back to the network, so marking something read tells the
  sender Sami saw it. Use `matrix_mark_read` only when Sami asks.
- Attachments: `matrix_download_media` with the event's `mxc://` URL.

## Sending

- Anyone Sami hasn't written to on that network gets a draft first: show Sami the exact
  text and the network, and send only after he says so. That is the same rule as email.
- In an existing conversation, follow what Sami asked for that conversation.
- `matrix_send_message` (room, body) for a new message, `matrix_reply` to answer a
  specific one.
- To start a conversation that has no room yet, send `start-chat <phone number or ID>`
  to that network's bridge bot. `help` in the bot's DM lists every command; Discord's
  bot has its own set.
- Discord servers are not bridged by default, so a server channel has no room. To post
  in one, find the channel id in the bridge database on oryx (`portal` table in
  `~/.local/share/chathub/discord/discord.db`, rows with that server's `dc_guild_id`), then
  send the Discord bot `guilds bridging-mode <server id> if-portal-exists` and
  `create-portal <channel id>`. That bridges only that channel; the rest of the server
  stays out of the hub. Send with `matrix_send_message` in the new room.

## Logins

When a bot posts that a login expired or was logged out, tell Sami which network and
re-link it. Everything runs on oryx; nothing is installed or changed on Sami's machine.

- **WhatsApp, Signal, Discord** link by QR code:

      secrets MATRIX_HUB_URL MATRIX_HUB_TOKEN -- ~/.dotfiles/chathub/login whatsapp login qr

  (signal: `login qr`; discord: `login-qr` as the whole command). It opens the QR image
  in Sami's browser through `forward open`; a rotated code overwrites the same file, so
  refreshing that tab shows the newest. Exit 0 means linked.
- **Google Messages, Messenger, Instagram** link by signing in to the website. Run
  `~/.dotfiles/chathub/login-screen` (keep it running) and send Sami
  `http://localhost:6080/vnc.html?autoconnect=1&resize=scale`: it shows mautrix-manager,
  already signed in to the hub, where he picks the network and signs in. Google
  Messages finishes with an emoji tap on his phone. Stop the script when he's done.

## Running the hub (on oryx)

- Services: `systemctl --user status tuwunel 'mautrix@*'`; logs with
  `journalctl --user -u mautrix@<network>`, where `<network>` is gmessages, whatsapp,
  signal, meta (Messenger), instagram or discord. Restarting `tuwunel` restarts every
  bridge with it.
- State lives in `~/.local/share/chathub/`. Re-running `installers/chathub.sh`
  restores every bridge's config to what the hub needs and keeps the bridges' tokens.
- Design: `docs/plans/2026-10-03-chat-hub-design.md` in the dotfiles repo.
