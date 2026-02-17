---
name: using-toggl
description: Use when tracking time, managing time entries, listing projects/tags, or interacting with Toggl
mcp:
  toggl:
    command: secrets
    args: ["TOGGL_API_KEY", "--", "node", "/home/sami/toggl/dist/index.js"]
    env: {}
---

# Toggl

Time tracking via the Toggl MCP server. Use `skill_mcp(mcp_name="toggl", ...)` to invoke tools.

## Tools

| Tool | Purpose |
|------|---------|
| `toggl_get_time_entries` | Fetch time entries for a date range |
| `toggl_get_timeline` | Get desktop activity/timeline data (rate limited: 30 req/hr) |
| `toggl_create_time_entry` | Create a new time entry |
| `toggl_list_projects` | List available projects in workspace |
| `toggl_list_tags` | List available tags in workspace |

## Usage Examples

```
skill_mcp(mcp_name="toggl", tool_name="toggl_get_time_entries", arguments='{"start_date": "2026-02-17", "end_date": "2026-02-17"}')

skill_mcp(mcp_name="toggl", tool_name="toggl_list_projects", arguments='{}')

skill_mcp(mcp_name="toggl", tool_name="toggl_list_tags", arguments='{}')

skill_mcp(mcp_name="toggl", tool_name="toggl_create_time_entry", arguments='{"description": "Code review", "start": "2026-02-17T09:00:00Z", "duration": 3600, "project_id": 12345, "tags": ["Code Review"]}')
```

## Notes

- All times in the API are UTC — convert to local timezone for display
- The default workspace (user's primary) is used automatically
- Timeline data is rate-limited to 30 requests per hour
