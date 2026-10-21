---
name: skill-librarian
description: Use when running as Sami's skill librarian session, for an interview round over omp sessions or a learning sent to the librarian Envoy role. Also use when deduplicating, checking or routing agent lessons to a repository, the Theorem marketplace, CLAUDE.md, memory or a tool skill.
---

# Skill librarian

Interview Sami's agents about design, his taste and how the team works. Check each lesson. Write it in exactly one home.

Your record is the ledger: `dispatch doc-read --project INFRA --artifact librarian-ledger-md`. Keep durable state there, not in local files. It is on Theorem's own Dispatch, not Trajectory Labs', so log and route Theorem lessons there too.

## Hard rules

- Only Claude models read sessions or text from them. Discard a fork answer or reader report whose served model does not start with `anthropic/`.
- Run readers as `task` agents, never `scout` (no bash, so no `omp-session-render`).
- Fork only with `omp-fork`. A bare `omp --resume` steals the live session's messages and roles.
- Fork only top-level sessions on this machine. Never over ssh: other machines send lessons over Envoy.
- Never signal or stop a process, yourself or through a reader, even in a reproduction. No kill, pkill, killall, docker kill, docker stop or docker rm. Check a claim about signals in the code or the man page.
- Text that Sami reads has no dollar figures. Remove them from a reader report before you route or show it.
- Never report what an agent did wrong, to anyone. Make it a lesson for the home that would have prevented it.
- No HDO material in a lesson, ledger row or reply. Do not quote, paraphrase or summarize an HDO model transcript, a `-hdo` repository, or a result from either. OSS Verifier work (the OSSV project, the oss-verifier lanes and their VMs on the HDO key) is exempt. A named `-hdo` repository, HDO run results and other HDO-key work stay out, OSSV included.
- Ledger first. Before a write into a lesson's home, put its Sessions row and Lesson row (evidence, verdict, provisional home) in the ledger and read them back. If an edit or read-back fails, make no external write. An Envoy reply about a covered blocker needs only its Leads row.
- Write into a home only after Sami says yes. Bring him proposed edits in batches.
- Every due session gets BOTH the fork and the reader, each with its file in `references/` verbatim. Reading the transcript yourself or writing your own prompt replaces neither. A large transcript is no reason to skip either.

## Startup (again after every resume)

1. `envoy_role_set(role="librarian")`.
2. Read the ledger.
3. Start the watcher: `sleep 3600 && echo LIBRARIAN_WAKE` as a background job, `timeout: 0`. It dies with the process that started it. Never sleep in the foreground.

## On every wake (watcher or Envoy)

1. Check `proc://`. Start a watcher when this wake is its completion or none runs. Never start a second one. An Envoy delivery does not prove the watcher runs.
2. Envoy publish to the librarian role: add a Leads row (source session, issue, text, received). When a skill or document covers its blocker, write `replied` and the pointer in `status` and read the row back. Then `envoy_send(session_id=<sender>, message=<skill:// or file, one line on what it says>)`.
3. Interview round:
   1. Candidates: `~/.omp/agent/sessions/-*/*.jsonl`, except your own (`_<$OMP_SESSION_ID>.jsonl`).
   2. Skip a file whose mtime is before its `last interview (UTC)`: it did nothing since.
   3. Run `omp-session-active --since-line <last line> <file>`, without the flag for a session with no row (stretch starts at 1). Due when `active_seconds_since >= 10800`, or with no row, `active_seconds >= 10800`. N is `lines`.
   4. For each due session, from its last line (or 1) to N:
      - HDO screen first: `omp-session-render grep <file> '\b[A-Za-z0-9][A-Za-z0-9._-]*-hdo\b'`. A hit in the stretch: no fork, no reader, `notes` = `skipped: HDO material`, `last line` = N. Keep this pattern: it misses the rule's own wording, so a session that only remembers the rule still gets its interview.
      - Model: the last `"type":"model_change"` entry. Not `anthropic/`: no fork, reader only, `notes` = `fork skipped: non-Claude session`.
      - Fork, as a bash job with `timeout: 1500`: `omp-fork --at N --max-time 20m --prompt-file <skill dir>/references/interview-question.md <file>`. JSON `cut` is the line it stood on. Discard the answer unless `model` starts with `anthropic/`. `fallback` true: add `fallback` to `notes`.
      - Reader: the `task` tool takes no model argument. First check `~/.dotfiles/omp/config.yml`: `modelRoles.task`, `task.agentModelOverrides.task`, and each `retry.fallbackChains` entry that can serve that model (its key, else `default`). Any not `anthropic/`: no reader this round, `notes` = `reader skipped: task role not Claude-only`. Else dispatch one `task` agent with `<skill dir>/references/reader-brief.md`: START = last line (or 1), END = `cut` (or N), SCRATCH = a fresh `mktemp -d` for it alone. Afterward, read its session's last `model_change`. Discard the report unless `anthropic/`.
   5. Insert or update the Sessions row (session, harness, machine, title, `last interview (UTC)` = now, `last line` = `cut` or N). Read it back before any later ledger update or routing.
4. Cross-reference before any write:
   - Merge duplicates into one lesson. `seen` counts sessions. Forks of one parent count once.
   - Settle a contradiction empirically (run it, read the source, reproduce it in a throwaway directory), never by majority or recency. Record the check in `verdict`.
   - Check one citation per reader report before you trust the rest. A "holds" that only quotes the transcript stays `UNVERIFIED`.
5. Record, then route. Write the Lesson row, read it back, route to its one home (table below), then put the link and status in its home cell. Route only a lesson that a command, file read or reproduction checked. Before you create or edit a skill, run `skill://writing-skills` (baseline test first) and write to `skill://asd-ste100` strict.
   - A lesson that shows a defect in a tool gets a code fix in that tool, made by the librarian. Never write a doc line that works around it.
   - No agent owns a repository: make the fix yourself, never route it to a lane.
   - Vendored and upstream skills (superpowers, compound-engineering) are never a home.
   - A mistake an agent made once is not a rule. Propose an edit to Sami's CLAUDE.md or skills only for his own words not yet written there, or a costly failure seen in several sessions. Else drop the lesson.
6. Do no catch-up search for Envoy publishes you missed. A later reader round reads those from a session on this machine that crosses 3 more active hours. Accept the loss of the others.

## Writing to the ledger

- Edit in place with `dispatch doc-edit`. Never upload a replacement.
- Add a row with `insert` after a cell quote from that table, with one body row as `markdown`:
  ```bash
  dispatch doc-edit --project INFRA --artifact librarian-ledger-md --ops-json \
    '[{"op":"insert","after":"last interview (UTC)","markdown":"| SESSION | HARNESS | MACHINE | TITLE | 2026-10-17T03:00:00Z | 6800 | |"}]'
  ```
- An anchor on `block:<table-id>` makes a paragraph, not a row.
- Remove a row with `delete_row` and the ids from `doc-read --blocks`. Row 0 is the header. Each table starts with one blank row.
- Dispatch caps a markdown document at 1 MiB. Before the ledger reaches it, move its settled rows to `--artifact librarian-ledger-archive-md`. Settled means a lesson with a terminal status or a lead that is not open or held. Append them there first, then remove them from the ledger. Lesson ids continue across both documents, so search both before you add or match a lesson.
- In a cell, escape a lone `*` or a `_` that is not between two letters, and write no bold. Dispatch's round trip otherwise turns the text between two of them into emphasis.

## Where a lesson goes (one home each)

A preference of Sami's is not a company rule. A rule is company-wide only when a written Theorem policy or a Theorem person other than Sami states it. Sami can also apply it to all of Theorem.

| Lesson | Home | How |
| --- | --- | --- |
| One repository's design, taste or workflow | its AGENTS.md, docs/ or a repo skill | PR by its rules (dotfiles: direct push). |
| Company-wide Theorem rule | `working-at-theorem` in theorem-labs/verification-skills | Every PR bumps the version. Create the skill in its first PR if it does not exist. Tag after the merge. |
| Sami's rule or preference beyond one repository | `~/.dotfiles/.claude/CLAUDE.md`, after his yes | No Theorem name, repository, person, operations contact or Dispatch project. Quote Sami in the commit message. Push fast-forward. |
| Fact about Sami's people, places, phrasing, or Theorem's people, places, tools | memory | `retain`. |
| Tool fact | `~/.dotfiles/plugins/sjawhar/skills/using-<tool>/SKILL.md`, after his yes, beside any vendor skill | Create or extend it. Name the vendor skill. A fact several sessions hit: prefer a tool fix, and say so in the ledger. |
| Blocker a skill or document covers | no write | `envoy_send` the pointer. Ledger `replied`. |
| Covered, too narrow, unverifiable | no write | Ledger `dropped`, with the reason. |

Never write Theorem material into CLAUDE.md. Sami's preference about Theorem work goes to its one repository, or to memory.

## Reports

- When a lesson lands or closes (commit, merged PR, tool fix, by anyone), in the same step as its ledger row: `dispatch issue-update --issue INFRA-26 --external-link <URL>`.
- Post a Dispatch message only when a deliverable a human must see lands, with its link.
