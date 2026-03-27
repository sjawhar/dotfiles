---
name: whatsapp
description: Use when reading WhatsApp messages, searching conversations, sending messages, listing chats, or interacting with WhatsApp workspaces
mcp:
  whatsapp:
    url: "${WHATSAPP_MCP_URL}"
    headers:
      Authorization: "Bearer ${WHATSAPP_MCP_SECRET}"
---

# WhatsApp

Interact with WhatsApp via the whatsapp-mcp server. Use `skill_mcp(mcp_name="whatsapp", ...)` to invoke tools.

## Setup

The WhatsApp MCP runs as a persistent HTTP server. It's configured in your environment:
- `WHATSAPP_MCP_URL` — URL of the WhatsApp MCP server (e.g., `http://ghost-wispr.tailb86685.ts.net:3456/mcp`)
- `WHATSAPP_MCP_SECRET` — Bearer token for authentication

For first-time setup of the daemon, see the [deployment docs](~/.dotfiles/whatsapp/).

### Local Development (stdio mode)

For local development or testing, you can run the server directly in stdio mode:
```bash
npx --yes @sjawhar/whatsapp-mcp
```
First run requires QR code pairing — run the command in a terminal, scan with WhatsApp > Linked Devices.

## Tools

| Tool                     | Purpose                                              |
| ------------------------ | ---------------------------------------------------- |
| `list_chats`             | List chats sorted by last activity                   |
| `get_chat`               | Get chat details with recent messages                |
| `list_messages`          | Get messages from a chat (most recent first)         |
| `search_messages`        | Substring search across messages                     |
| `search_contacts`        | Find contacts by name or phone number                |
| `get_message_context`    | Get messages surrounding a specific message          |
| `get_my_profile`         | Get authenticated user's phone number and JID        |
| `update_contact`         | Set display name for a contact                       |
| `sync_contacts`          | Import phone contacts from VCF file or string        |
| `send_message`           | Send text message (requires `confirmed: true`)       |
| `send_file`              | Send file (requires `confirmed: true`, path restricted to uploads dir) |
| `delete_message`         | Delete a message (requires `confirmed: true`)        |
| `delete_chat`            | Delete entire chat (requires `confirmed: true`)      |
| `download_media`         | Download media from a message to local disk          |
| `transcribe_voice_note`  | Transcribe voice note via Whisper API                |
| `resolve_contacts`      | Resolve unknown LID contacts to phone numbers + sync names |

## Confirmation Flow

Destructive tools (`send_message`, `send_file`, `delete_message`, `delete_chat`) require a two-step confirmation:
1. Call with `confirmed: false` (default) → returns preview
2. Call with `confirmed: true` → executes the action

## Usage Examples

```
skill_mcp(mcp_name="whatsapp", tool_name="list_chats", arguments='{"limit": 10}')

skill_mcp(mcp_name="whatsapp", tool_name="search_messages", arguments='{"query": "meeting tomorrow"}')

skill_mcp(mcp_name="whatsapp", tool_name="send_message", arguments='{"jid": "1234567890@s.whatsapp.net", "text": "Hello!", "confirmed": false}')

skill_mcp(mcp_name="whatsapp", tool_name="download_media", arguments='{"jid": "1234567890@s.whatsapp.net", "messageId": "ABC123"}')
```

## JID Format

- Individual: `1234567890@s.whatsapp.net` (phone number without +)
- Group: `120363XXX@g.us`
- Use `search_contacts` to find JIDs by name

## Resolving Unknown Contacts

If contacts show as phone numbers instead of names, sync them from Google Contacts:

1. Load the `google-workspace` skill
2. Export Google Contacts as VCF using `gws contacts export`
3. Pass the VCF content to `sync_contacts` via the `vcf_content` parameter:
   ```
   skill_mcp(mcp_name="whatsapp", tool_name="sync_contacts", arguments='{"vcf_content": "<vcard data>"}')
   ```
4. Review results — the tool reports `exactMatches`, `fuzzyMatches`, and `totalUpdated`

## Security Notes

- `send_file` only allows files within the configured uploads directory
- `download_media` sanitizes filenames to prevent path traversal
- Rate limiting is active on outbound messages (3s minimum interval + jitter)
- Voice transcription requires `WHISPER_API_URL` and `WHISPER_API_KEY` env vars

## ⚠️ Burner Number Warning

This uses the unofficial WhatsApp Web API (Baileys). WhatsApp may ban accounts using unofficial clients. Use a dedicated number you can afford to lose.
