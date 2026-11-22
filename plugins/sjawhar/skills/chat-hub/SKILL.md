---
name: chat-hub
description: Use when Sami mentions a chat, text, direct message or "did X reply" on SMS/RCS, WhatsApp, Signal, Discord, Instagram, Messenger or LinkedIn, or asks to read or send one. Not for Slack (slack-messaging, slack-search), the inbox (mailbox CLI) or other Gmail, Drive or Docs work (google-workspace).
---

# Chat hub

A private Matrix server on oryx bridges Sami's chat networks. Each conversation is a room of `@sami:chathub`. Read the [design](../../../../docs/plans/2026-10-03-chat-hub-design.md) when you need the hub's parts, state or secrets.

- Use the hub only in an omp session on oryx: the hub listens only on oryx loopback, and its `MATRIX_HUB_*` secrets are in oryx's `secrets.local.env`. Only an omp session started in the dotfiles repository loads the `chathub` MCP, from `~/.dotfiles/.omp/mcp.json`.
- Send messages and files to people with `hub-send` (Sending to a person): the `chathub` MCP does not send them. It reads chats and sends commands to bridge bots.

| Network | Logins | A message goes out |
| --- | --- | --- |
| Every network but SMS/RCS | Sami's real accounts | As Sami |
| SMS/RCS | Two: Sami's phone and the agents' phone | From the number of the room's login |

## Networks

`BOTS` in `chathub/login` maps each network name (the one `chathub/login` and `mautrix@<network>` take) to its bridge bot, `@<bot>:chathub`. The DM with a bot is that network's control room. A person's ID starts with its bridge's prefix: `@gmessages_`, `@whatsapp_`, `@signal_`, `@facebook_` (Messenger), `@instagram_`, `@discord_`, `@linkedin_`. A room name is the contact or group name from the network.

## Reading

| For | Tool |
| --- | --- |
| New messages | `matrix_get_unread` |
| All rooms and their IDs | `matrix_list_rooms` |
| A room's network | `matrix_list_room_members` |
| History and event IDs | `matrix_read_history` |
| An attachment | `matrix_download_media` with the event's `mxc://` URL |

- Call `matrix_mark_read` only when Sami asks: read receipts tell the sender that Sami saw the message. Both read tools leave the room unread.

## Sending to a person

1. **Decide whose voice it is.** A room of Sami's login (every network but SMS/RCS, or an SMS room whose login is Sami's) sends as him. A room of the agents' phone sends as the agent, under the agent's own instructions.

   | In a room of Sami's login | Do |
   | --- | --- |
   | To a person Sami has not written to on that network | Show Sami the exact text and network, and send only after he approves |
   | Where Sami has written | Send what he asked for without showing it first |

2. **Write the text or caption to a file** with the write tool, not into a shell command, so shell quoting cannot change it.
3. **Send with `hub-send`**, in an existing room or one `start-chat` created:
   ```sh
   secrets MATRIX_HUB_URL MATRIX_HUB_TOKEN -- ~/.dotfiles/scripts/hub-send <room_id> --text-file <file>
   # a reply: add --reply-to <event_id>
   # an image or file: --file <path> [--caption-file <file>] instead of --text-file
   ```
4. **Read the result.** `hub-send` prints one result line for each command, except a usage error. A usage error prints its reason on stderr and exits 2. The header of `scripts/hub-send` lists the results and exit codes.

   | Result | Meaning |
   | --- | --- |
   | `SENT` | The network took the message, not that the person read it |
   | `FAILED` | The bridge or hub reported a failure |
   | `UNCONFIRMED` | `hub-send` could not learn the result |
   | `REFUSED` | `hub-send` posted nothing, for the reason the line gives |

   | Result | Do |
   | --- | --- |
   | `FAILED` or `UNCONFIRMED` | Tell Sami |
   | `FAILED` or `UNCONFIRMED` | Do not resend or send anything more to that person, on that network or any other, unless Sami asks |
   | `REFUSED`, when its reason is a down login | See Logins |

   A message visible in the room, or no error notice, proves nothing.

- Do not send to a person with `matrix_send_message`, `matrix_reply`, `matrix_send_media` or the hub API's `/send` route.
- Use `--force` only when Sami asks, or when you test `hub-send` itself against a fake hub (a test server, not the real `MATRIX_HUB_URL`), where no message reaches a person. `--force` skips the bridge and login checks, so it can post through a down login. It still needs the room's `m.bridge` state.

## Bridge bot commands and new rooms

- Send a bot command with `matrix_send_message` in the bot's control room, or with `secrets MATRIX_HUB_URL MATRIX_HUB_TOKEN -- ~/.dotfiles/chathub/login <network> <command>`.
  - Commands: `start-chat <ID or phone number>`, `list-logins`, `help`, Discord `guilds …`, `create-portal`. `help` lists the bot's commands. The Discord bot's commands differ.
- **No room yet:**
  1. **Send `start-chat <ID or phone number>`** to that network's bot.
  2. **Send the first message** with `hub-send` in the new room.
- **SMS:**
  1. **Send `list-logins`** to `@gmessagesbot:chathub`.
  2. **Send `start-chat <login ID> <phone number>`** with the login of the number to send from. The bridge uses Sami's login with no login ID, or with one it does not know.
- Read the room's login before the first send in any SMS room (new or existing, from `start-chat` or found), and compare it with the login ID you chose. `hub-send` refuses a room whose login is down. It does not compare logins. The login is `content.channel["fi.mau.receiver"]` of the room's `m.bridge` state event:
  ```sh
  secrets MATRIX_HUB_URL MATRIX_HUB_TOKEN -- sh -c 'printf "Authorization: Bearer %s\n" "$MATRIX_HUB_TOKEN" | curl -fsS -H @- "$MATRIX_HUB_URL/_matrix/client/v3/rooms/$1/state"' _ '<room ID>' | jq -r '.[] | select(.type == "m.bridge") | .content.channel["fi.mau.receiver"]'
  ```
- Send nothing in an SMS room whose login differs from the login ID you chose.
- **A Discord server channel:**
  1. **Find the channel ID** in `~/.local/share/chathub/discord/discord.db` on oryx: the `portal` rows with that server's `dc_guild_id`.
  2. **Send `guilds bridging-mode <server id> if-portal-exists`** in the Discord bot's control room.
  3. **Send `create-portal <channel id>`** in the Discord bot's control room. Only that channel joins the hub.
  4. **Send to people in the new room** with `hub-send`.

## Logins

1. **Tell Sami** which network needs reauthentication when a bot reports that a login expired or ended, or `hub-send` refuses a room for a down login.
2. **Read [references/logins.md](references/logins.md) now** for the repair.

- Do all of it on oryx.
- Install or change nothing on Sami's machine.

## Running the hub (on oryx)

| For | Command |
| --- | --- |
| Status | `systemctl --user status tuwunel 'mautrix@*'` |
| Logs | `journalctl --user -u mautrix@<network>` |

A restart of `tuwunel` restarts all bridges.

- Run `env -u XDG_DATA_HOME ~/.dotfiles/installers/chathub.sh` to restore every bridge configuration: an agent pane's `XDG_DATA_HOME` points away from the hub state, and the installer would create a second state and restart Tuwunel. State is in `~/.local/share/chathub/`. The installer overwrites hand edits. It keeps bridge tokens.
