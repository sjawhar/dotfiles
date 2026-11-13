---
name: chat-hub
description: Use when Sami mentions a chat, text, direct message, or "did X reply". It covers SMS/RCS, WhatsApp, Signal, Discord, Instagram, Messenger, and LinkedIn. Use `chathub` to read chats. Use `hub-send` to send a message or file to a person. Slack uses `slack-messaging` or `slack-search`. Inbox work uses `mailbox`. Other Gmail, Drive, and Docs work uses `google-workspace`.
---

# Chat hub

The chat hub bridges Sami's chat networks into one private Matrix server on oryx. Each conversation is a Matrix room of `@sami:chathub`. The `chathub` MCP reads chats and sends commands to bridge bots. It does not send messages or files to people. Only omp sessions that start in the dotfiles repo load the MCP (`~/.dotfiles/.omp/mcp.json`). The hub listens only on oryx loopback. Its `MATRIX_HUB_*` secrets are in oryx's `secrets.local.env`. Use it only in a session on oryx.

On every network but SMS/RCS, a message goes out **as Sami, on his real accounts**. SMS/RCS has two logins: Sami's phone and the agents' phone. An SMS room sends from the number of its login. A room's login is `content.channel["fi.mau.receiver"]` in its `m.bridge` state event (the bridge's description of the room). "Sending" has the command that reads it.

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

A room name is the contact or group name from the network. The DM with a bridge bot is that network's control room.

## Reading

- New messages: `matrix_get_unread`. All rooms: `matrix_list_rooms`. Network of a room: `matrix_list_room_members`.
- Read with `matrix_read_history` or `matrix_get_unread`. Both tools keep the room unread.
- Call `matrix_mark_read` only when Sami asks. Read receipts tell the sender that Sami saw the message.
- Use `matrix_download_media` to read an attachment. Give it the event's `mxc://` URL.

## Sending

- A room of Sami's login is a room on every network but SMS/RCS, or an SMS room whose login is Sami's. In such a room, to a person Sami has not written to on that network, show Sami the exact text and network. Send only after he approves. The message goes out as him. In a room where Sami has written, send what he asked for without showing it first.
- A room of the agents' phone sends as the agent, under the agent's own instructions.
- Send each message or file to a person only with `secrets MATRIX_HUB_URL MATRIX_HUB_TOKEN -- ~/.dotfiles/scripts/hub-send …`. Use it in an existing room or in a room that `start-chat` creates.
- Write the person's text or caption in a file with the write tool. Do not put the person's text on a shell command. A file prevents shell quoting of the person's text.

```sh
# A text message
secrets MATRIX_HUB_URL MATRIX_HUB_TOKEN -- ~/.dotfiles/scripts/hub-send <room_id> --text-file <file>

# A reply to one event
secrets MATRIX_HUB_URL MATRIX_HUB_TOKEN -- ~/.dotfiles/scripts/hub-send --reply-to <event_id> <room_id> --text-file <file>

# An image or file with a caption
secrets MATRIX_HUB_URL MATRIX_HUB_TOKEN -- ~/.dotfiles/scripts/hub-send <room_id> --file <path> --caption-file <file>
```

- Use `matrix_list_rooms` to read the room ID. Use `matrix_read_history` to read the event ID for a reply.
- Do not use `matrix_send_message`, `matrix_reply`, `matrix_send_media`, or the hub API's `/send` route for a message or file to a person.
- Use `matrix_send_message` only for a command to a bridge bot in its control room. Use it for `start-chat <ID or phone number>`, `list-logins`, `help`, Discord `guilds …`, and `create-portal` commands. `help` lists the bot's commands. The Discord bot's commands are different, so ask it.
- `chathub/login <network> <command>` can send the same commands to a bridge bot.
- No room yet: send `start-chat <ID or phone number>` to the bridge bot of that network. After `start-chat`, send the first message to the person with `hub-send` in the new room.
- For SMS, first send `list-logins` to `@gmessagesbot:chathub`. Then send `start-chat <login ID> <phone number>` with the login of the number to send from. With no login ID, or one that the bridge does not know, the bridge uses Sami's login.
- Before the first send in an SMS room, read the room's login. This applies to the room that `start-chat` names, new or already there, and to a room that you found. Compare the room's login with the login ID that you chose. When they differ, send nothing in that room. `hub-send` refuses a room whose login is down, but it does not compare the login with the one you chose. Read the login with this command: `secrets MATRIX_HUB_URL MATRIX_HUB_TOKEN -- sh -c 'printf "Authorization: Bearer %s\n" "$MATRIX_HUB_TOKEN" | curl -fsS -H @- "$MATRIX_HUB_URL/_matrix/client/v3/rooms/$1/state"' _ '<room ID>' | jq -r '.[] | select(.type == "m.bridge") | .content.channel["fi.mau.receiver"]'`.
- For a Discord server channel, do these steps:
  1. Find the channel ID in `~/.local/share/chathub/discord/discord.db` on oryx. Read `portal` rows with that server's `dc_guild_id`.
  2. Use `matrix_send_message` in the Discord bot control room for `guilds bridging-mode <server id> if-portal-exists`. Then use it for `create-portal <channel id>`. Only that channel joins the hub.
  3. Use `hub-send` in the new room to send to a person.

## Results

`hub-send` prints one result for each command.

- `SENT` is the only result that counts as sent. It means the network took the message. It does not mean the person got or read it.
- `FAILED` means the bridge or hub reported a failure.
- `UNCONFIRMED` means `hub-send` could not learn the result.
- `REFUSED` means that `hub-send` posted nothing. The line gives the reason. A down login can cause `REFUSED`. See [Logins](#logins).
- After `FAILED`, tell Sami that the message failed. After `UNCONFIRMED`, tell Sami that the message is not confirmed.
- After `FAILED` or `UNCONFIRMED`, send nothing more to that person unless Sami asks. Do not resend. Do not send another message on that network or another network.
- A message in the room proves nothing. No error notice proves nothing.
- Use `--force` only when Sami asks or for a test.

## Logins

When a bot reports that a login expired or ended, tell Sami which network needs reauthentication. Do all work on oryx. Do not install or change anything on Sami's machine.

The Google Messages login of the agents' phone needs no step by Sami. An agent pairs it again by the steps in section "Logins" of `skills/majordomo/references/sites/chat-hub.md` in the private repository `sjawhar/dojo`. Every other login signs in again with Sami, as this section says.

A down login on Google Messages, WhatsApp, Signal, Messenger, Instagram, or LinkedIn signs in again with `relogin` in that bridge's control room. Send each command with `secrets MATRIX_HUB_URL MATRIX_HUB_TOKEN -- ~/.dotfiles/chathub/login <network> <command>`.

1. Send `list-logins`. The reply lists the login IDs.
2. Send `relogin <login ID>`. If the reply asks for a flow, send `relogin <login ID> <flow ID>`.
3. Continue with the row of the network in this table.

| Network | After `relogin` |
|---|---|
| WhatsApp and Signal | Use the flow `qr`. Follow the QR procedure. |
| Google Messages, Messenger, Instagram, and LinkedIn | The bot replies with the step that it needs, for example cookies from a signed-in browser. Show Sami each step. Send a cookie or token from Sami with `chathub/login <network> -`. That form reads it from stdin and never prints it. |
| Discord | Send `login-qr` only when Discord has no login. Follow the QR procedure for it. Send `reconnect` only when its login has no connection. Run `secrets MATRIX_HUB_URL MATRIX_HUB_TOKEN -- ~/.dotfiles/chathub/login discord reconnect` for it. |

Do not use `login` to repair a down Google Messages (SMS) login. A new `login` creates a second login with new rooms. The old rooms then can no longer send. The login of the agents' phone is a second login on purpose, because it has its own number. A new login for the same number is never a repair.

### QR procedure

1. Run `~/.dotfiles/chathub/login-screen`. Leave it running.
2. Start the QR login. For WhatsApp or Signal, run `secrets MATRIX_HUB_URL MATRIX_HUB_TOKEN -- ~/.dotfiles/chathub/login [--keep MINUTES] <network> relogin <login ID> qr`. For Discord, run `secrets MATRIX_HUB_URL MATRIX_HUB_TOKEN -- ~/.dotfiles/chathub/login [--keep MINUTES] discord login-qr`.
3. Send Sami `http://localhost:6080/`. The screen holds the port on his laptop with `forward port`.
4. The page shows each rotated code. `--keep` sends the command again when a code expires.
5. Run one `chathub/login` command at a time. Exit 0 means linked.
6. Stop the screen after Sami finishes.

### Website procedure (only for a network with no login)

mautrix-manager makes a new login. It cannot sign in a login again: version 0.2.1 turns off its relogin control. Use this procedure only for a network that has no login of that account. Never use it for a down login, and never for a down Google Messages login.

1. Run `~/.dotfiles/chathub/login-screen`. Leave it running. The procedure uses no VNC: `forward port` holds mautrix-manager's Chromium debugging port 9347 on Sami's laptop.
2. Send Sami the `Window "mautrix-manager":` DevTools link that it prints.
3. Sami uses the DevTools screencast to control the manager window.
4. The manager signs in to the hub as `sami` with `MATRIX_HUB_PASSWORD`.
5. Sami selects the network and signs in. The script prints the second window's link when a sign-in opens it.
6. Google Messages has no QR login (Google ended it). For Sami's login, he signs in to Google, then taps the emoji on his phone.
7. The bridge drops an unfinished sign-in after 30 minutes. Start the script again.
8. Stop the screen after Sami finishes.

## Running the hub (on oryx)

- Status: `systemctl --user status tuwunel 'mautrix@*'`. Logs: `journalctl --user -u mautrix@<network>` for gmessages, whatsapp, signal, meta, instagram, discord, or linkedin.
- A restart of `tuwunel` restarts all bridges.
- State: `~/.local/share/chathub/`. Re-run `env -u XDG_DATA_HOME ~/.dotfiles/installers/chathub.sh` to restore every bridge configuration. An agent pane's `XDG_DATA_HOME` points away from the hub state. The installer would create a second state and restart Tuwunel. The installer overwrites hand edits and keeps bridge tokens.
- Design: `~/.dotfiles/docs/plans/2026-10-03-chat-hub-design.md`.
