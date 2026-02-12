---
name: linear
description: Manage Linear issues. Use when working with tasks, tickets, bugs, or Linear.
mcp:
  linear:
    command: node
    args: ["/home/sami/.dotfiles/vendor/streamlinear/mcp/dist/index.js"]
    env:
      LINEAR_API_TOKEN: ${LINEAR_API_TOKEN}
---

# Linear (Stream Linear)

Single-tool MCP with action dispatch. All operations go through the `linear` tool via `skill_mcp(mcp_name="linear", tool_name="linear", ...)`.

## Actions

### Search Issues

```
skill_mcp(mcp_name="linear", tool_name="linear", arguments='{"action": "search"}')                          # Your active issues
skill_mcp(mcp_name="linear", tool_name="linear", arguments='{"action": "search", "query": "auth bug"}')    # Text search
skill_mcp(mcp_name="linear", tool_name="linear", arguments='{"action": "search", "query": {"state": "In Progress"}}')  # Filter
skill_mcp(mcp_name="linear", tool_name="linear", arguments='{"action": "search", "query": {"team": "ENG", "assignee": "me"}}')
```

### Get Issue Details

```
skill_mcp(mcp_name="linear", tool_name="linear", arguments='{"action": "get", "id": "ABC-123"}')               # By short ID
skill_mcp(mcp_name="linear", tool_name="linear", arguments='{"action": "get", "id": "https://linear.app/..."}')  # By URL
```

Returns: title, description, status, labels, comments, attachments.

### Update Issue

```
skill_mcp(mcp_name="linear", tool_name="linear", arguments='{"action": "update", "id": "ABC-123", "state": "Done"}')
skill_mcp(mcp_name="linear", tool_name="linear", arguments='{"action": "update", "id": "ABC-123", "priority": 1}')
skill_mcp(mcp_name="linear", tool_name="linear", arguments='{"action": "update", "id": "ABC-123", "assignee": "me"}')
skill_mcp(mcp_name="linear", tool_name="linear", arguments='{"action": "update", "id": "ABC-123", "labels": ["worker-done", "existing-label"]}')
```

**Labels array replaces all labels.** Fetch current labels first, then append.

### Comment on Issue

```
skill_mcp(mcp_name="linear", tool_name="linear", arguments='{"action": "comment", "id": "ABC-123", "body": "Fixed in commit abc123"}')
```

### Create Issue

```
skill_mcp(mcp_name="linear", tool_name="linear", arguments='{"action": "create", "title": "Bug: Login fails", "team": "ENG"}')
skill_mcp(mcp_name="linear", tool_name="linear", arguments='{"action": "create", "title": "Bug", "team": "ENG", "body": "Details", "priority": 2}')
```

### Raw GraphQL

```
skill_mcp(mcp_name="linear", tool_name="linear", arguments='{"action": "graphql", "graphql": "query { viewer { name } }"}')
```

### Help

```
skill_mcp(mcp_name="linear", tool_name="linear", arguments='{"action": "help"}')
```

## Reference

- Priority: 0=None, 1=Urgent, 2=High, 3=Medium, 4=Low
- State matching is fuzzy: "done" → "Done", "in prog" → "In Progress"
- IDs accept: `ABC-123`, Linear URLs, or UUIDs
