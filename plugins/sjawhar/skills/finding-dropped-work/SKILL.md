---
name: finding-dropped-work
description: Use when about to report a body of work done or end a session, after a restart or compaction, for a lead's daily or the chief of staff's periodic check of agent sessions, when an eval's result sits unread, or when the user says requests were dropped or blockers were not in Dispatch.
---

# Finding dropped work

A session's own memory of its work is the thing that fails, so a fresh agent reads the transcript instead.

## When

- Any session: before it reports a body of work done, and before it ends. Audit its own transcript since its last
  audit or its start.
- A lead, once a day: each agent of the stream, and its own. Use the stream's own daily audit program if it has one.
- The chief of staff, every few hours: every session since the last audit.

## How

1. Start one fresh read-only subagent per transcript (on Oh My Pi: `task` with the default agent; it needs a shell
   for `tq` and `dispatch read`). Read `references/audit-brief.md` when you write its task, and give it that brief
   as the file says, with the session id, the window and a report path filled in.
2. Read the report yourself. Open the FILE:LINE behind each DROPPED, MISSED-GOAL, UNSURFACED, WAITING-UNREPORTED and
   LOCAL-ONLY row before you act on it.
3. Close each row that is not DONE in the same hour, by the rules below.
4. Report done with the audit's counts: rows found, and rows closed by each action.

## Close each row

- You can do it now: deliver it. Done means deployed, merged or measured, as the request needs, with the evidence
  linked.
- It takes longer: do it now, in this session or in a subagent it starts and waits for. An issue for later, a
  follow-up or a hand-off to another agent is deferral, and deferred work does not come back.
- It waits on the user: open a Dispatch ask on its issue (`skill://dispatch`). A chat question, a note, a peer
  message, a comment on an answered ask or a review request is not an ask.
- A Dispatch record names a local file: upload the file as an artifact on that issue and link it.
- The result sits in a document, artifact or chat line with no ask: open an ask that links the result and asks the
  user's next step, as an ask block at the end of that document or a `dispatch ask`. An artifact notifies nobody.
- The work was delivered smaller or different than its goal: count it open. Finish the missing part now, or open an
  ask on its issue naming each unmet requirement and asking whether it is dropped. A new issue for the missing part
  does not close the row.
- An eval's result does not answer the question it was asked: count it open. Say in one line what it does answer and
  what is missing, then run the missing arm or cells now, or open an ask with both.
- Dropped on purpose: say so to the requester, with the reason, on the issue.
