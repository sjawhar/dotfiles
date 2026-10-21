---
name: airtable
description: Use when listing, searching, filtering, or updating Airtable records, including applicant or hiring data. Also use for airtable.com URLs, base IDs (app...), and table IDs (tbl...).
---

# Airtable

Call the official `@airtable/mcp-cli` this way. The token is `AIRTABLE_TOKEN` in `secrets.env`. It needs no login or `configure`.

```bash
secrets AIRTABLE_TOKEN -- npx -y @airtable/mcp-cli <tool> [flags]
```

- Tool names and flags come from the server and change. Run `tools`, then `<tool> --help`, before you call a tool. `whoami` checks auth.
- Candidate pipeline data now lives in Terminal, not Airtable: use the project's `hiring` skill and its `hire` CLI (for example `hire show <email>`, `hire stats`). The old Applications base (`appvSQts63ngMSoCP`) is no longer the system of record. Do not pin this tool to it again.
- Records tools take `--baseId` and `--tableId`, not `--tableIdOrName`.
- Pipe complex inputs (filters, sort, `--tables`) as JSON to `--input -`:
  ```bash
  echo '{"baseId":"appXXX","tableId":"tblXXX","pageSize":20}' \
    | secrets AIRTABLE_TOKEN -- npx -y @airtable/mcp-cli list-records-for-table --input - -q
  ```
- `--filters` takes an operator tree keyed by field ID, not a formula. `list-records-for-table --help` has examples. For select and multi-select fields, get choice IDs from `get-table-schema` or `list-tables-for-base` first.
- Records come back as `cellValuesByFieldId`. `list-tables-for-base` maps field IDs to names.
- `--output raw` gives the raw server response. `-q` stops stderr status output.
- Create takes 10 records per request at most. Standard Airtable API rate limits apply.
- The token's scopes limit the calls. A read-only token cannot write.
