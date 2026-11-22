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
- Dispatch caps a markdown document at 1 MiB (1,048,576 bytes). On each wake, before your first edit, read the ledger once into a private file and measure that file: `d=$(mktemp -d) && dispatch doc-read --project INFRA --artifact librarian-ledger-md > "$d/ledger.md" && wc -c < "$d/ledger.md"`. A failed read or a count of 0 is unknown, not small: make no edit until a read succeeds. When it prints more than 900,000, move its settled rows to `--artifact librarian-ledger-archive-md`, so the next wake's rows still fit: append them there first, then remove them from the ledger. Lesson ids continue across both documents, so search both before you add or match a lesson; until you write a Lessons row, search that file for the ledger's rows instead of reading it again.
- A settled row is a Lessons row whose `status` starts with `dropped`, `written`, `fixed`, `landed`, `merged`, `recorded`, `withdrawn`, `closed`, `rejected`, `reverted`, `done`, `replied`, `acted on`, `nothing added` or `live`, or a Leads row whose `status` starts with neither `open` nor `held`. A new Leads row's `status` is `open`.
- In a cell, escape a lone `*` or a `_` that is not between two letters, and write no bold. Dispatch's round trip otherwise turns the text between two of them into emphasis.
