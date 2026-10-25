# Writing to the ledger

- The ledger's three tables and their header columns:
  - Sessions: `session | harness | machine | title | last interview (UTC) | last line | notes`
  - Lessons: `id | kind | lesson | seen | evidence | verdict | home | status`
  - Leads: `received | source | issue | text | status`
- Edit in place with `dispatch doc-edit`. Never upload a replacement.
- Add a row with an `insert` op whose `after` is text quoted from a cell of the target table (for example a header cell, as below), and one body row in `markdown`:
  ```bash
  dispatch doc-edit --project INFRA --artifact librarian-ledger-md --ops-json \
    '[{"op":"insert","after":"last interview (UTC)","markdown":"| SESSION | HARNESS | MACHINE | TITLE | 2026-10-17T03:00:00Z | 6800 | |"}]'
  ```
- An anchor on `block:<table-id>` makes a paragraph, not a row.
- Remove a row with `delete_row` and the ids from `doc-read --blocks`. Row 0 is the header and row 1 is the blank row each table starts with, so data rows start at row 2.
- Dispatch caps a markdown document at 1 MiB. Before the ledger reaches it, move its settled rows to `--artifact librarian-ledger-archive-md`. Settled means a lesson with a terminal status or a lead that is not open or held. Append them there first, then remove them from the ledger. Lesson ids continue across both documents, so search both before you add or match a lesson.
- In a cell, escape a lone `*` or a `_` that is not between two letters, and write no bold. Dispatch's round trip otherwise turns the text between two of them into emphasis.
