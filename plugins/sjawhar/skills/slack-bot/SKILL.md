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
      SOPS_AGE_KEY: "${SOPS_AGE_KEY}"
---

# Slack Bot

Interact with Slack workspaces via the slack-mcp-server. Use `skill_mcp(mcp_name="slack", ...)` to invoke tools.

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
skill_mcp(mcp_name="slack", tool_name="channels_list", arguments='{"channel_types": "public_channel,private_channel"}')

skill_mcp(mcp_name="slack", tool_name="conversations_history", arguments='{"channel_id": "#general", "limit": "1d"}')

skill_mcp(mcp_name="slack", tool_name="conversations_search_messages", arguments='{"search_query": "deploy", "filter_in_channel": "#engineering"}')

skill_mcp(mcp_name="slack", tool_name="conversations_add_message", arguments='{"channel_id": "#general", "payload": "Hello from the bot!", "content_type": "text/plain"}')
```

## History Limits

The `limit` param on history/replies accepts time ranges (`1d`, `1w`, `30d`, `90d`) or message counts (`50`). Leave empty when using `cursor` for pagination.

## Formatting Messages

`conversations_add_message` with `content_type: "text/plain"` renders Slack mrkdwn correctly (`*bold*`, `` `code` ``, `_italic_`, `<url|text>` links, `~strike~`). The tool's response text appears stripped, but the actual Slack message renders formatting fine. Use this for all messages.

Slack mrkdwn differs from markdown: `*bold*` (not `**bold**`), `_italic_`, `~strike~`, `<url|text>` for links. No heading syntax.

**Avoid Block Kit `section` blocks** — they cause 80-character word wrapping. If you need Block Kit (headers, dividers, context footers), use `header`, `divider`, and `context` blocks only.

Use `conversations_search_messages` or the Slack API `conversations.list` to find channel IDs when `#channel-name` lookup fails (common with Slack Connect / Enterprise Grid channels).

To delete a message:
```bash
secrets SLACK_MCP_XOXP_TOKEN -- sh -c 'curl -s -X POST "https://slack.com/api/chat.delete" \
  -H "Authorization: Bearer $SLACK_MCP_XOXP_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"channel\": \"C1234567890\", \"ts\": \"1234567890.123456\"}"'
```
