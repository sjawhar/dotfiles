---
name: whatsapp
description: Use when reading WhatsApp messages, searching conversations, sending messages, listing chats, or interacting with WhatsApp workspaces
mcp:
  whatsapp:
    command: npx
    args: ["--yes", "@sjawhar/whatsapp-mcp"]
    env:
      WHISPER_API_URL: "${WHISPER_API_URL}"
      WHISPER_API_KEY: "${WHISPER_API_KEY}"
---

# WhatsApp

Interact with WhatsApp via the whatsapp-mcp server. Use `skill_mcp(mcp_name="whatsapp", ...)` to invoke tools.

## Setup

```bash
npm install -g @sjawhar/whatsapp-mcp
```

First run requires QR code pairing — run `whatsapp-mcp` in a terminal, scan with WhatsApp > Linked Devices.

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
| `sync_contacts`          | Import phone contacts from VCF file                  |
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

## Security Notes

- `send_file` only allows files within the configured uploads directory
- `download_media` sanitizes filenames to prevent path traversal
- Rate limiting is active on outbound messages (3s minimum interval + jitter)
- Voice transcription requires `WHISPER_API_URL` and `WHISPER_API_KEY` env vars

## ⚠️ Burner Number Warning

This uses the unofficial WhatsApp Web API (Baileys). WhatsApp may ban accounts using unofficial clients. Use a dedicated number you can afford to lose.
