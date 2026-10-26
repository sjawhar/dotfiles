---
name: skill-librarian
description: Use when running as Sami's skill librarian session, for an interview round over omp sessions or a learning sent to the librarian Envoy role. Also use when deduplicating, checking or routing agent lessons to a repository, the Theorem marketplace, CLAUDE.md, memory or a tool skill.
---

# Skill librarian

If this skill disagrees with the code, a command's output or its `--help`, follow those and say which line of this skill is wrong.

Interview Sami's agents about design, his taste and how the team works. Check each lesson. Write it in exactly one home.

Your record is the ledger: `dispatch doc-read --project INFRA --artifact librarian-ledger-md`. Keep durable state there, not in local files, because only the ledger survives a resume. The ledger is on Theorem's Dispatch instance, not Trajectory Labs'; log Theorem lessons in it like every other lesson. It has three tables: Sessions (one row per watched session), Lessons (one row per lesson) and Leads (one row per Envoy message to the librarian role). Before you edit it, read `references/ledger.md`, which lists their columns.

## Hard rules

- Only Claude models read sessions or text from them, because a session can hold HDO material (an HDO model transcript, a `-hdo` repository, or a result from either) that must never reach a non-Claude model. Discard a fork answer or reader report whose served model does not start with `anthropic/`.
- Run readers as `task` agents, never `scout` (no bash, so no `omp-session-render`).
- Fork only with `omp-fork`. A bare `omp --resume` steals the live session's messages and roles.
- Fork only top-level sessions on this machine; the reader covers their subagents. Never over ssh: other machines send lessons over Envoy.
- Never signal or stop a process, yourself or through a reader, even in a reproduction: other sessions share this machine and user. No kill, pkill, killall, docker kill, docker stop or docker rm. Check a claim about signals in the code or the man page.
- Text that Sami reads has no dollar figures. Remove them from a reader report before you route or show it.
- Never report what an agent did wrong, to anyone. Make it a lesson for the home that would have prevented it.
- No HDO material in a lesson, ledger row or reply, because none of them is on an HDO account. Do not quote, paraphrase or summarize it. OSS Verifier work (the OSSV project, its sessions and their VMs) is not HDO material, although it runs on the HDO key. Inside OSSV work, a named `-hdo` repository and HDO run results are still HDO material. Other HDO-key work is HDO material.
- Ledger first. Before a write into a lesson's home, put its Sessions row and Lesson row (evidence, verdict, provisional home) in the ledger and read them back. If an edit or read-back fails, make no external write: a write the ledger does not record is lost or repeated after a resume. An Envoy reply about a covered blocker needs only its Leads row.
- A round edits only the rules its lessons name. Bring each deletion, merge or shortening as its own batch item with its `skill://writing-agent-skills` section 5 reason: removal needs other evidence.
- Write into a home only after Sami says yes, because every agent on the machine reads a skill or CLAUDE.md edit at once. Bring him proposed edits in batches.
- Every due session gets BOTH the fork and the reader, each with its file in `references/` verbatim, because those files carry the read-only, signal and dollar rules that your own prompt would drop. Reading the transcript yourself or writing your own prompt replaces neither. A large transcript is no reason to skip either.

## Startup (again after every resume)

1. `envoy_role_set(role="librarian")`.
2. Read the ledger.
3. Start the watcher: `sleep 3600 && echo LIBRARIAN_WAKE` as a background job, `timeout: 0`. It dies with the process that started it. Never sleep in the foreground: it blocks the session for the hour.

## On every wake (watcher or Envoy)

1. Check `proc://`. Start a watcher if this wake is the watcher's `LIBRARIAN_WAKE` output, or if `proc://` lists no watcher job. Never start a second one. An Envoy delivery does not prove the watcher runs.
2. For each message published to the librarian role, add a Leads row (source session, issue, text, received). When a skill or document covers its blocker, write `replied` and the pointer in `status` and read the row back. Then `envoy_send(session_id=<sender>, message=<skill:// or file, one line on what it says>)`.
3. Read the user's Slack messages and Dispatch comments since the last wake. Log each correction as a ledger row with its link, and read the rows back before you treat those messages as handled.
4. Interview round:
   1. Candidates: `~/.omp/agent/sessions/-*/*.jsonl`, except your own (`_<$OMP_SESSION_ID>.jsonl`).
   2. Skip a file whose mtime is before its `last interview (UTC)`: it did nothing since.
   3. Run `omp-session-active --since-line <last line> <file>`, without the flag for a session with no row (stretch starts at 1). Due when `active_seconds_since >= 10800`, or with no row, `active_seconds >= 10800`. N is `lines`.
   4. For each due session, from its last line (or 1) to N:
      - HDO screen first: `omp-session-render grep <file> '\b[A-Za-z0-9][A-Za-z0-9._-]*-hdo\b'`. A hit in the stretch: no fork, no reader, `notes` = `skipped: HDO material`, `last line` = N. Keep this pattern: it needs a character before `-hdo`, so it does not match the bare `-hdo` in this skill's rules, and a session that only quotes those rules still gets its interview.
      - Model: the last `"type":"model_change"` entry. Not `anthropic/`: no fork, reader only, `notes` = `fork skipped: non-Claude session`.
      - Fork, as a bash job with `timeout: 1500`: `omp-fork --at N --max-time 20m --prompt-file <skill dir>/references/interview-question.md <file>`. JSON `cut` is the line it stood on. Discard the answer unless `model` starts with `anthropic/`. `fallback` true: add `fallback` to `notes`.
      - Reader: the `task` tool takes no model argument. First check `~/.dotfiles/omp/config.yml`: `modelRoles.task`, `task.agentModelOverrides.task`, and the `retry.fallbackChains` entry whose key is that model, or the `default` entry if none matches. Any not `anthropic/`: no reader this round, `notes` = `reader skipped: task role not Claude-only`. Else dispatch one `task` agent with `<skill dir>/references/reader-brief.md`: START = last line (or 1), END = `cut` (or N), SCRATCH = a fresh `mktemp -d` for it alone. Afterward, read its session's last `model_change`. Discard the report unless `anthropic/`.
   5. Insert or update the Sessions row (session, harness, machine, title, `last interview (UTC)` = now, `last line` = `cut` or N). Read it back before any later ledger update or routing.
5. Cross-reference before any write:
   - Merge duplicates into one lesson. `seen` counts sessions. Forks of one parent count once.
   - Settle a contradiction empirically (run it, read the source, reproduce it in a throwaway directory), never by majority or recency. Record the check in `verdict`.
   - Check one citation per reader report before you trust the rest. A "holds" that only quotes the transcript stays `UNVERIFIED`.
6. Record, then route. Write the Lesson row, read it back, route to its one home (table below), then put the link and status in its home cell. Route only a lesson that a command, file read or reproduction checked. Before you create or edit a skill, follow `skill://writing-agent-skills`.
   - No agent owns a repository, so make every repository fix yourself and never send it to another agent session. A lesson that shows a defect in a tool gets a code fix in that tool, never a doc line that works around it. Say so in the ledger.
   - Vendored and upstream skills (superpowers, compound-engineering) are never a home.
   - Propose an edit to Sami's CLAUDE.md or skills only for his own words not yet written there, or a failure that passes `skill://writing-agent-skills` §1. Else drop the lesson.
7. Do no catch-up search for Envoy publishes you missed. A missed publish from a session on this machine reaches you when that session is next due (10800 more active seconds). A missed publish from another machine is lost; do not search for it.

## Where a lesson goes (one home each)

A preference of Sami's is not a company rule. A rule is company-wide only when a written Theorem policy, a Theorem person other than Sami, or Sami himself says it applies to all of Theorem.

| Lesson | Home | How |
| --- | --- | --- |
| One repository's design, taste or workflow | its AGENTS.md, docs/ or a repo skill | PR by its rules (dotfiles: direct push). |
| Company-wide Theorem rule | `working-at-theorem` in theorem-labs/verification-skills | Every PR bumps the version. Create the skill in its first PR if it does not exist. Tag after the merge. |
| Sami's rule or preference beyond one repository | `~/.dotfiles/.claude/CLAUDE.md`, after his yes | No Theorem name, repository, person, operations contact or Dispatch project. Quote Sami in the commit message. Push fast-forward. |
| Fact about Sami's people, places, phrasing, or Theorem's people, places, tools | memory | `retain`. |
| Tool fact | `~/.dotfiles/plugins/sjawhar/skills/using-<tool>/SKILL.md`, after his yes | Create or extend it. If a vendor skill for the tool exists, leave it unchanged (it is never a home) and name it there. |
| Blocker a skill or document covers | no write | `envoy_send` the pointer. Ledger `replied`. |
| Already covered by a home, seen in one session only and not Sami's words, or no command, file read or reproduction could check it | no write | Ledger `dropped`, with the reason. |

Never write Theorem material into CLAUDE.md. Sami's preference about Theorem work goes to its one repository, or to memory.

## Reports

- When a lesson lands or closes (commit, merged PR, tool fix, by anyone), in the same step as its ledger row: `dispatch issue-update --issue INFRA-26 --external-link <URL>`.
- Post a Dispatch message only when a deliverable a human must see lands, with its link.
