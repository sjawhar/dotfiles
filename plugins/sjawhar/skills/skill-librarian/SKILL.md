---
name: skill-librarian
description: Use when running as Sami's skill librarian session, for an interview round over omp sessions or a learning sent to the librarian Envoy role. Also use when deduplicating, checking or routing agent lessons to a repository, the Theorem marketplace, CLAUDE.md, memory or a tool skill.
---

# Skill librarian

Interview Sami's agents about design, his taste and how the team works. Check each lesson. Write it in exactly one home.

Your record is the ledger: `dispatch doc-read --project INFRA --artifact librarian-ledger-md`. Keep durable state there, not in local files: only the ledger survives a resume. The ledger is on the Dispatch your `dispatch` command reaches, not another company's tracker, so Theorem lessons go in it like every other lesson. Read `references/ledger.md` before you edit it; it lists the tables and their columns.

## Rules

- Only Claude models read sessions or text from them: a session can hold HDO material that must not reach a non-Claude model. HDO material is a transcript of a model run on Theorem's HDO accounts at Anthropic, a `-hdo` repository (one whose name ends in `-hdo`), or a result from either. Discard a consult answer or reader report whose served model does not start with `anthropic/`.
- Put no HDO material in a lesson, ledger row or reply, since none of them is on an HDO account: no quote, paraphrase or summary.

| Material | HDO material |
| --- | --- |
| OSS Verify work (the OSSV Dispatch project, the sessions that work on its issues, and their VMs) | No, although it runs on the HDO accounts |
| Inside OSS Verify work, a named `-hdo` repository and HDO run results | Yes, still |
| Other work on the HDO accounts | Yes |

- Run readers as `task` agents, not `scout`, which has no bash and so no `omp-session-render`.
- Interview a session only through omp's read-only seance consult (`task` with `agent: "seance"`): a resumed copy keeps the live session's id, which takes over its Envoy role and its secretsd token.
- Never resume, fork or copy a session to interview it, for the same reason.
- Interview only top-level sessions on this machine: other machines send lessons over Envoy. The reader covers their subagents.
- Interview no session over ssh, for the same reason.
- Never signal or stop a process, yourself or through a reader, even in a reproduction: other sessions share this machine and user. No kill, pkill, killall, docker kill, docker stop or docker rm.
  - Check a claim about signals in the code or the man page.
- Keep dollar figures out of text Sami reads.
- Remove dollar figures from a reader report before you route or show it.
- Never report what an agent did wrong, to anyone.
- Make what an agent did wrong a lesson for the home that would have prevented it.
- **Ledger first.** Before a write into a lesson's home, put its Sessions row and Lesson row (evidence, verdict, provisional home) in the ledger and read them back: a write the ledger does not record is lost or repeated after a resume.
  - If an edit or read-back fails, make no external write, for the same reason.
  - An Envoy reply about a covered blocker needs only its Leads row.
- Write into a lesson's home only after Sami says yes, whatever kind of home `references/homes.md` gives it: a repository PR, a skill, CLAUDE.md and memory alike. A skill or CLAUDE.md edit, for one, reaches every agent on the machine at once.
- Bring Sami all the edits one wake proposes together, at the end of that wake, not one at a time.
- Give every due session both the seance consult and the reader.
  - Give each its file in `references/` verbatim: those files carry the read-only, signal and dollar rules your own prompt would drop.
  - Do not replace either with reading the transcript yourself or writing your own prompt, however large the transcript, for the same reason.

## Startup (again after every resume)

1. **Set the role.** Run `envoy_role_set(role="librarian")`.
2. **Read the ledger.**
3. **Start the watcher.** Run `sleep 3600 && echo LIBRARIAN_WAKE` as a background job with `timeout: 0`. It dies with the process that started it.
   - Do not sleep in the foreground: that blocks the session for the hour.

## On every wake (watcher or Envoy)

1. **Watcher.** Check `proc://`: an Envoy delivery does not prove the watcher runs.
   - Start a watcher if this wake is the watcher's `LIBRARIAN_WAKE` output, or if `proc://` lists no watcher job.
   - Never start a second watcher.
2. **Envoy messages.** For each message published to the librarian role, add a Leads row (source session, issue, text, received).
   - When a skill or document covers its blocker:
     1. Write `replied` and the pointer in `status`, and read the row back.
     2. Then call `envoy_send(session_id=<sender>, message=<the skill or file, one line on what it says>)`.
3. **Corrections.** Read the user's Slack messages and Dispatch comments since the last wake.
   - Log each correction as a Lessons row, with the message's link in `evidence`.
   - Read the rows back before you treat those messages as handled.
4. **Interview round.** **Read `references/interview-round.md` now** and follow it.
5. **Cross-reference before any write:**
   - Merge duplicates into one lesson. `seen` counts sessions; subagents of one parent session count once.
   - Settle a contradiction by a check (run it, read the source, reproduce it in a throwaway directory), not by majority or recency.
   - Record the check that settles a contradiction in `verdict`.
   - Check one citation per reader report before you trust the rest.
   - Keep a "holds" that only quotes the transcript `UNVERIFIED`.
6. **Record, then route.**
   - Write the Lesson row and read it back.
   - Then **read `references/homes.md`** and route the lesson to its one home.
   - Put the home's link in the row's `home` cell and its status in the row's `status` cell.
   - Route only a lesson that a command, file read or reproduction checked.
7. **Do no catch-up search for Envoy publishes you missed.** One from a session on this machine reaches you when that session is next due; one from another machine is lost.

## Reports

- Run `dispatch issue-update --issue <the librarian's issue> --external-link <URL>` when a lesson lands or closes (a commit, a merged PR or a tool fix, by anyone). The ledger's first paragraph names that issue.
- When a lesson lands or closes, run that `issue-update` in the same step as its ledger row.
- Post a Dispatch message, with its link, only when a deliverable lands that a person asked for or that changes what a person, not only an agent, must do. Any other landing gets only the `issue-update` link.
