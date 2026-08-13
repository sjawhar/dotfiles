---
name: slack-bot
description: Use when reading Slack messages, searching conversations, sending messages, listing channels, or interacting with Slack workspaces
mcp:
  slack:
    command: secrets
    args: ["SLACK_MCP_XOXP_TOKEN", "--", "slack-mcp-server"]
    env:
      SLACK_MCP_ADD_MESSAGE_TOOL: "true"
      SLACK_MCP_ATTACHMENT_TOOL: "true"
---

# Slack Bot

Interact with Slack workspaces via the slack-mcp-server. Use `skill_mcp(mcp_name="slack", ...)` to invoke tools.

## Ground Rules

- **Read threads, not just top-level messages.** A history scan without `conversations_replies` on active threads misses most of the conversation, so don't conclude "no response" from top-level messages alone.

## Tools

| Tool                            | Purpose                                            |
| ------------------------------- | -------------------------------------------------- |
| `channels_list`                 | List channels (public, private, DMs, group DMs)    |
| `conversations_history`         | Read messages from a channel or DM                 |
| `conversations_replies`         | Read thread replies                                |
| `conversations_search_messages` | Search messages with filters (date, user, channel) |
| `conversations_add_message`     | Send a message to a channel or thread              |
| `reactions_add`                 | Add emoji reaction to a message                    |
| `reactions_remove`              | Remove emoji reaction from a message               |
| `users_search`                  | Search users by name, email, or display name       |
| `usergroups_list`               | List user groups in workspace                      |

## Channel Lookup

Channels can be referenced by ID (`C1234567890`) or name (`#general`, `@username_dm`).

## Usage Examples

```
skill_mcp(mcp_name="slack", tool_name="conversations_history", arguments='{"channel_id": "#general", "limit": "1d"}')
```

## History Limits

The `limit` param on history/replies accepts time ranges (`1d`, `1w`, `30d`, `90d`) or message counts (`50`). Leave empty when using `cursor` for pagination.

## Formatting Messages

`conversations_add_message` accepts three rendering paths. **Default to `blocks` for anything with structure** (lists, sections, mixed bold/links). Plain `text` is fine for one-liners; `content_type: "text/markdown"` is a trap for non-trivial messages — see below.

Slack mrkdwn syntax (used inside text and inside `rich_text` element `text` fields): `*bold*` (not `**bold**`), `_italic_`, `~strike~`, `` `code` ``, `<url|text>` for links. No heading syntax.

### Path 1 — Block Kit `blocks` parameter (preferred for structured messages)

Needs **slack-mcp-server v1.3.0+**. The MCP tool accepts a `blocks` parameter (JSON-stringified Block Kit array). When supplied, the server bypasses the markdown converter entirely and posts the blocks as-is; `text` becomes the notification fallback only.

Use **one `rich_text` block** containing a mix of `rich_text_section` (for headers and paragraphs) and `rich_text_list` (for bulleted lists). Each indent level is its OWN `rich_text_list` block placed directly after its parent — Slack does not support nested-list-inside-list.

```
skill_mcp(mcp_name="slack", tool_name="conversations_add_message", arguments='{
  "channel_id": "C...",
  "text": "Standup - Fri May 22",
  "blocks": "[{\"type\":\"rich_text\",\"elements\":[ ... ]}]"
}')
```

Element reference inside `rich_text`:

| Element | Purpose | Key fields |
|---------|---------|-----------|
| `rich_text_section` | Header line, section label, or free-form paragraph | `elements` array of `text` / `link` / `emoji` |
| `rich_text_list` | Bulleted (or ordered) list at a single indent level | `style`: `bullet` or `ordered`, `indent`: 0/1/2, `elements`: array of `rich_text_section` (one per bullet) |
| `text` | Plain text run | `text` string, optional `style: {bold, italic, code, strike}` |
| `link` | Hyperlink | `url`, `text` (display label) |
| `emoji` | Emoji shortcode | `name` (e.g. `"musical_note"`) |

Section transitions (`*Yesterday*` → `*Today*`) are a `rich_text_section` with `\n`, bold label, `\n`. No literal `•` / `◦` characters — Slack renders the bullets from the list structure.

### Path 2 — Plain text + Slack mrkdwn

```
skill_mcp(mcp_name="slack", tool_name="conversations_add_message", arguments='{"channel_id": "#general", "text": "Quick note: *deploy* ready in <https://example.com|staging>", "content_type": "text/plain"}')
```

Fine for short messages with light formatting. For lists, literal `•` / `◦` characters render visually but produce no semantic list structure (breaks copy/paste, accessibility, search) — use `blocks` instead.

### Path 3 — `content_type: "text/markdown"` (AVOID for structured messages)

Do not use content_type text/markdown for structured messages — the converter splits every element into separate blocks (80-char wraps, lost formatting); use blocks instead.

## Inspecting message structure

The MCP `conversations_history` response strips Block Kit structure to a flat text representation. To see the actual block JSON (for replicating a hand-edited message), use Slack's API directly — this is debug-only, not the send path:

```bash
secrets SLACK_MCP_XOXP_TOKEN -- sh -c 'curl -s "https://slack.com/api/conversations.history?channel=$CH&latest=$TS&oldest=$TS&inclusive=true&limit=1" -H "Authorization: Bearer $SLACK_MCP_XOXP_TOKEN"' \
  | jq '.messages[0].blocks'
```

To delete a message:
```bash
secrets SLACK_MCP_XOXP_TOKEN -- sh -c 'curl -s -X POST "https://slack.com/api/chat.delete" \
  -H "Authorization: Bearer $SLACK_MCP_XOXP_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"channel\": \"C1234567890\", \"ts\": \"1234567890.123456\"}"'
```
