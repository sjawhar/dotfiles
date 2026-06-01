---
name: airtable
description: Use when reading, searching, or updating Airtable records — applicant/hiring data or any Airtable base. Triggers on airtable.com URLs, base IDs (app...), table IDs (tbl...), or requests to list, query, filter, or update Airtable records.
---

# Airtable

Access Airtable via the official `@airtable/mcp-cli` (a CLI wrapper over Airtable's
MCP server — auto-discovers tools at runtime, so it never loads anything into
context). The PAT lives in `secrets.env` as `AIRTABLE_TOKEN` and is injected per-call.

## Invocation

Every call follows this shape — `secrets` injects the token, `npx` runs the CLI:

```bash
secrets AIRTABLE_TOKEN -- npx -y @airtable/mcp-cli <tool> [--flags]
```

`AIRTABLE_TOKEN` is read from the environment, so no `configure`/login is needed.

## Discover before calling

Tool names and flags come from the server at runtime and may change. **Source of
truth is the live `tools` list** — don't assume.

```bash
# List available tools (human-readable)
secrets AIRTABLE_TOKEN -- npx -y @airtable/mcp-cli tools

# Show flags/schema for one tool
secrets AIRTABLE_TOKEN -- npx -y @airtable/mcp-cli <tool> --help

# Confirm auth
secrets AIRTABLE_TOKEN -- npx -y @airtable/mcp-cli whoami
```

## Common tools

Verified against the live server (run `tools` to confirm current names):

| Tool | Purpose |
| --- | --- |
| `list-bases` | List bases you can access (get the `baseId`) |
| `search-bases` | Find a base by partial name |
| `list-tables-for-base` | Tables + field schemas for a base |
| `get-table-schema` | Detailed field IDs/types/config (needed for select-field choice IDs) |
| `list-records-for-table` | List/query records. Pass `fieldIds` for the fields you want |
| `search-records` | Free-text search within a table |
| `create-records-for-table` | Create records (max 10/request) |
| `update-records-for-table` | Patch records (only specified fields change) |

## Pinned base — hiring applicants

```
baseId:   appvSQts63ngMSoCP
tableId:  tblTAe1moooa5Lsoy
view:     viwyGwAUe0E5k3FXF
```

```bash
# List applicant records (page through with --pageSize / --cursor)
secrets AIRTABLE_TOKEN -- npx -y @airtable/mcp-cli list-records-for-table \
  --baseId appvSQts63ngMSoCP --tableId tblTAe1moooa5Lsoy --pageSize 20

# Discover field IDs + names (run this before filtering/sorting by field)
secrets AIRTABLE_TOKEN -- npx -y @airtable/mcp-cli list-tables-for-base \
  --baseId appvSQts63ngMSoCP

# Detailed schema for specific fields (--tables is a JSON array of {tableId, fieldIds})
secrets AIRTABLE_TOKEN -- npx -y @airtable/mcp-cli get-table-schema \
  --baseId appvSQts63ngMSoCP \
  --tables '[{"tableId":"tblTAe1moooa5Lsoy","fieldIds":["fldFK9Pv35FUE4FrX"]}]'
```

## Tips

- **Flags are per-tool**: records tools use `--baseId` + `--tableId` (NOT `--tableIdOrName`). Always run `<tool> --help` when unsure.
- **JSON args for complex inputs** (filters, sort, --tables): pipe via stdin with `--input -`:
  ```bash
  echo '{"baseId":"appXXX","tableId":"tblXXX","pageSize":20}' \
    | secrets AIRTABLE_TOKEN -- npx -y @airtable/mcp-cli list-records-for-table --input - -q
  ```
- **Filtering**: `--filters` takes a structured operator tree keyed by field ID (not Airtable formula strings). For select/multi-select, call `get-table-schema`/`list-tables-for-base` first to get choice IDs. See `list-records-for-table --help` for filter examples.
- **Records are returned as `cellValuesByFieldId`** — map field IDs to names via `list-tables-for-base`.
- **Output**: defaults to formatted JSON; add `--output raw` for the raw server response, `-q` to silence stderr status.
- **Quotas**: uses Airtable's public API under the hood — subject to standard rate limits; create is capped at 10 records/request.
- **Permissions**: the CLI can only do what the PAT's scopes allow (e.g. read-only token = no writes).
