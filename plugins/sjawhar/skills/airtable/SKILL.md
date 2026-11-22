---
name: airtable
description: Use when listing, searching, filtering or updating Airtable records, or given an airtable.com URL, a base ID (app...) or a table ID (tbl...). Not for candidates, applicants or hiring, which Airtable does not hold, and not for rotating the Airtable token.
---

# Airtable

Call the official `@airtable/mcp-cli` with the token from `secrets`; with `AIRTABLE_TOKEN` set it needs no login or `configure`:

```bash
secrets AIRTABLE_TOKEN -- npx -y @airtable/mcp-cli <tool> [flags]
```

- Run `tools`, then `<tool> --help`, before you call a tool: the server defines tool names and flags, and they change. `whoami` checks auth.
- Candidate and applicant data is not in Airtable: the agent-c Platform holds it. Use the `hiring` skill in `trajectory-labs-pbc/core-ops` (`skills/hiring/SKILL.md`) and its `hire` CLI (`hire show <email>`, `hire queue`). Do not read candidate data from, or write it to, the old Applications base (`appvSQts63ngMSoCP`).
- The `hire` CLI is `skills/hiring/scripts/hire.py` in the hiring skill's repository.
- Records tools take `--baseId` and `--tableId`, not `--tableIdOrName`.
- Pass nested inputs (filters, sort, `--tables`) as one JSON object on stdin with `--input -`:
  ```bash
  echo '{"baseId":"appXXX","tableId":"tblXXX","pageSize":20}' \
    | secrets AIRTABLE_TOKEN -- npx -y @airtable/mcp-cli list-records-for-table --input - -q
  ```
- `--filters` takes an operator tree keyed by field ID, not a formula; `list-records-for-table --help` has examples. For select and multi-select fields, get the choice IDs from `get-table-schema` or `list-tables-for-base` before you filter.
- Records come back as `cellValuesByFieldId`; map field IDs to names with `list-tables-for-base`.
- `--output raw` prints the raw server response. `-q` silences status output on stderr.
- Create takes at most 10 records per request: batch larger writes.
- The token's scopes limit the calls: a read-only token cannot write, so report a permission error instead of retrying the call.
