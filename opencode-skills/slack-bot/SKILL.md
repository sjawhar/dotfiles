---
name: slack-bot
description: Use when reading Slack messages, searching conversations, sending messages, listing channels, or interacting with Slack workspaces
mcp:
  slack:
    command: slack-mcp-server
    args: []
    env:
      SLACK_MCP_XOXP_TOKEN: "${SLACK_MCP_XOXP_TOKEN}"
      SLACK_MCP_ADD_MESSAGE_TOOL: "true"
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

skill_mcp(mcp_name="slack", tool_name="conversations_add_message", arguments='{"channel_id": "#general", "payload": "Hello from the bot!", "content_type": "text/markdown"}')
```

## History Limits

The `limit` param on history/replies accepts time ranges (`1d`, `1w`, `30d`, `90d`) or message counts (`50`). Leave empty when using `cursor` for pagination.
