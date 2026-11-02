---
name: airtable
description: Use when listing, searching, filtering, or updating Airtable records, including applicant or hiring data. Also use for airtable.com URLs, base IDs (app...), and table IDs (tbl...).
---

# Airtable

Call the official `@airtable/mcp-cli` with the token from `secrets`; it needs no login or `configure`:

```bash
secrets AIRTABLE_TOKEN -- npx -y @airtable/mcp-cli <tool> [flags]
```

- Run `tools`, then `<tool> --help`, before you call a tool: the server defines tool names and flags, and they change. `whoami` checks auth.
- Candidate pipeline data is on the agent-c Platform, not Airtable. Use the `hiring` skill in `trajectory-labs-pbc/core-ops` (`skills/hiring/SKILL.md`) and its `hire` CLI (`skills/hiring/scripts/hire.py`, e.g. `hire show <email>`, `hire queue`). The old Applications base (`appvSQts63ngMSoCP`) is no longer the system of record: do not read candidate data from it or write to it.
- Records tools take `--baseId` and `--tableId`, not `--tableIdOrName`.
- Pass nested inputs (filters, sort, `--tables`) as one JSON object on stdin with `--input -`:
  ```bash
  echo '{"baseId":"appXXX","tableId":"tblXXX","pageSize":20}' \
    | secrets AIRTABLE_TOKEN -- npx -y @airtable/mcp-cli list-records-for-table --input - -q
  ```
- `--filters` takes an operator tree keyed by field ID, not a formula; `list-records-for-table --help` has examples. For select and multi-select fields, get choice IDs from `get-table-schema` or `list-tables-for-base` first.
- Records come back as `cellValuesByFieldId`; map field IDs to names with `list-tables-for-base`.
- `--output raw` prints the raw server response. `-q` silences status output on stderr.
- Create takes at most 10 records per request: batch larger writes.
- The token's scopes limit the calls: a read-only token cannot write, so report a permission error instead of retrying the call.
