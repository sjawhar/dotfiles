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

## Hiring applicants base is retired

The old Applications base (`appvSQts63ngMSoCP`) is no longer the hiring system of record.
Candidate pipeline data now lives in Terminal; see the project's `hiring` skill and its
`hire` CLI (`hire show <email>`, `hire stats`, and so on). Do not re-pin this tool to that
base.

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
