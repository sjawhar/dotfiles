---
name: airtable
description: Use when listing, searching, filtering, or updating Airtable records. Also use for airtable.com URLs, base IDs (app...), and table IDs (tbl...). Not for candidate or applicant data, which is no longer in Airtable.
---

# Airtable

Call the official `@airtable/mcp-cli` with the token from `secrets`:

```bash
secrets AIRTABLE_TOKEN -- npx -y @airtable/mcp-cli <tool> [flags]
```

The CLI needs no login or `configure` with `AIRTABLE_TOKEN` set.

## Candidate and applicant data

- Candidate and applicant data is not in Airtable: the agent-c Platform holds it. Use the `hiring` skill in `trajectory-labs-pbc/core-ops` (`skills/hiring/SKILL.md`) and its `hire` CLI (`hire show <email>`, `hire queue`). Do not read candidate data from, or write it to, the old Applications base (`appvSQts63ngMSoCP`).
  - The `hire` CLI is `skills/hiring/scripts/hire.py` in the hiring skill's repository.

## Tools and records

- Run `tools`, then `<tool> --help`, before you call a tool: the server defines tool names and flags, and they change.
- Pass nested inputs (filters, sort, `--tables`) as one JSON object on stdin with `--input -`:
  ```bash
  echo '{"baseId":"appXXX","tableId":"tblXXX","pageSize":20}' \
    | secrets AIRTABLE_TOKEN -- npx -y @airtable/mcp-cli list-records-for-table --input - -q
  ```
- Get the choice IDs of select and multi-select fields from `get-table-schema` or `list-tables-for-base` before you filter.
- Map field IDs to names with `list-tables-for-base`: records come back as `cellValuesByFieldId`.
- Batch larger writes: Create takes at most 10 records per request.
- Report a permission error instead of retrying the call: the token's scopes limit the calls. A read-only token cannot write.

| Tool or flag | Does |
| --- | --- |
| `whoami` | Checks auth |
| Records tools | Take `--baseId` and `--tableId`, not `--tableIdOrName` |
| `--filters` | Takes an operator tree keyed by field ID, not a formula |
| `list-records-for-table --help` | Has `--filters` examples |
| `--output raw` | Prints the raw server response |
| `-q` | Silences status output on stderr |
