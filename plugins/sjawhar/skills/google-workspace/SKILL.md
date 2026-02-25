---
name: google-workspace
description: Use when reading, searching, uploading, downloading, sharing, or organizing files on Google Drive. Also for Google Docs, Sheets, Gmail, or Calendar access. Triggers on "google drive", "gdrive", "workspace", Drive/Docs URLs (drive.google.com, docs.google.com), file IDs, or requests to find, list, export, share, or manage cloud documents and spreadsheets.
mcp:
  gws:
    command: gws
    args: ["mcp", "-s", "drive"]
---

# Google Workspace (gws)

Manage Google Drive (and other Workspace APIs) via MCP tools or the `gws` CLI. All output is structured JSON.

## MCP Tools

Use `skill_mcp(mcp_name="gws", ...)` to invoke Drive operations. Discover available tools:

```
skill_mcp(mcp_name="gws")
```

Tool names match CLI structure. Example MCP calls:

```
skill_mcp(mcp_name="gws", tool_name="drive_files_list", arguments='{"pageSize": 10}')

skill_mcp(mcp_name="gws", tool_name="drive_files_list", arguments='{"q": "name contains \"report\""}')
```

If tool names don't match expectations, discover via CLI: `gws drive --help`.

## CLI Quick Start

```bash
# List files
gws drive files list --params '{"pageSize": 10}'

# Search files
gws drive files list --params '{"q": "name contains \"report\"", "pageSize": 10}'

# Download a file
gws drive files get --params '{"fileId": "FILE_ID", "alt": "media"}' -o ./output.pdf

# Upload a file
gws drive files create --json '{"name": "report.pdf"}' --upload ./report.pdf

# Share a file
gws drive permissions create \
  --params '{"fileId": "FILE_ID"}' \
  --json '{"role": "reader", "type": "user", "emailAddress": "user@example.com"}'
```

## CLI Syntax

```
gws <service> <resource> <method> [flags]
```

| Flag | Description |
|------|-------------|
| `--params '{...}'` | URL/query parameters |
| `--json '{...}'` | Request body |
| `--upload <PATH>` | Upload file (multipart) |
| `-o, --output <PATH>` | Save response to file |
| `--page-all` | Auto-paginate (NDJSON) |
| `--dry-run` | Preview without calling API |

## Discovering API Methods

```bash
gws drive --help                    # List all resources
gws schema drive.files.list         # Inspect params/types for a method
```

Use `gws schema` output to build `--params` and `--json` flags.

## Common Drive Queries (q parameter)

```
name contains 'budget'                    # Name search
mimeType = 'application/pdf'              # By type
modifiedTime > '2025-01-01T00:00:00'      # Recently modified
'FOLDER_ID' in parents                    # Files in folder
trashed = false                           # Exclude trash
sharedWithMe = true                       # Shared with me
```

Combine with `and`: `name contains 'report' and mimeType = 'application/pdf'`

## Auth

```bash
# Interactive (needs browser)
gws auth login -s drive

# Headless: export from authenticated machine, then use on server
gws auth export --unmasked > ~/.config/gws/credentials.json
export GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE=~/.config/gws/credentials.json

# Service account
export GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE=/path/to/service-account.json
```

## Expanding to Other Services

Add services to the MCP args (edit this skill's frontmatter) and auth scope:

```bash
gws auth login -s drive,gmail,sheets
gws mcp -s drive,gmail,sheets
```

## Gotchas

- **`fields` parameter required** for some methods (`about.get`, `comments.*`). Check `gws schema` if you get empty responses.
- **Sheets ranges use `!`** which bash interprets as history expansion. Always single-quote: `'Sheet1!A1:C10'`
- **OAuth scope limits**: Unverified apps (testing mode) limited to ~25 scopes. Use `-s drive` not `-s all` for personal use.
- **Google Docs/Sheets aren't directly downloadable** — use `files.export` with a target MIME type (`application/pdf`, `text/csv`), not `files.get`.
- **Pre-v1.0**: `gws` is under active development. Expect occasional breaking changes in flags or MCP tool names.

## Full API Reference

For complete API coverage, see the upstream skills:

- **[gws-drive](https://github.com/googleworkspace/cli/blob/main/skills/gws-drive/SKILL.md)** — All Drive resources and methods
- **[gws-shared](https://github.com/googleworkspace/cli/blob/main/skills/gws-shared/SKILL.md)** — Auth, global flags, security rules
- **[Skills Index](https://github.com/googleworkspace/cli/blob/main/docs/skills.md)** — All services, helpers, and recipes

```bash
# Fetch any upstream skill for detailed reference
curl -sL https://raw.githubusercontent.com/googleworkspace/cli/main/skills/gws-drive/SKILL.md
```
