---
name: finding-dropped-work
description: Use when about to report an issue or a request done or end a session, after a restart or compaction, for a lead's daily or the chief of staff's periodic check of agent sessions, when an eval's result sits unread, or when the user says requests were dropped or blockers were not in Dispatch.
---

# Finding dropped work

Audit a session's transcript for dropped work, and close each row the audit finds. A session's own memory of its work is the thing that fails, so a fresh agent reads the transcript instead.

**Done when:** you took the action in Close each row for each row the report's closing list names (step 3), and reported done with the audit's counts (step 4).

## When

- **Any session:** before it tells a person that an issue or a request is done, before it moves an issue to `done`, and
  before it ends. Audit its own transcript since its last audit or its start.
- **A lead, once a day:** each agent of its stream, and its own.
  - A stream is a line of work with its own Dispatch issue labelled `stream`.
  - The stream's lead is the session that the issue's `scoreboard.json` artifact names as `lead`, else the session
    that holds the claim on that issue.
  - The stream's agents are the sessions that hold the claim on that issue or on one of its child issues.
  - If the stream has its own daily audit program (a script that audits the stream's sessions, such as the Rocq speed
    stream's `audit.py`), run it with its roots set to the stream's sessions.
  - If the stream has no such program, follow How below, one subagent per agent.
- **The chief of staff, every 4 hours:** every session with a message since the last audit, each read from that audit
  to now, one subagent per session.

## How

1. **Start the subagents.** Start one fresh read-only subagent per transcript. On Oh My Pi, use `task` with the default
   agent: the subagent needs a shell for `tq` and `dispatch read`. Read `assets/audit-brief.md` when you write its task.
   Give it that brief as the file says, with the session id, the window and a report path filled in.
2. **Check the report.** Read the report yourself. Open the FILE:LINE behind each DROPPED, MISSED-GOAL, UNSURFACED,
   WAITING-UNREPORTED and LOCAL-ONLY row before you act on it.
3. **Close each row.** Within one hour of receiving the report, take the action below for each row its closing list
   names. Work that takes longer starts in that hour and goes on until it is delivered.
4. **Report the counts.** Report done with the audit's counts: rows found, and rows closed by each action.

## Close each row

| The row | Action |
| --- | --- |
| You can do it now | Deliver it. Done means deployed, merged or measured, as the request needs, with the evidence linked. Written into a spec, approval requested or sent to an agent is not done. |
| It takes longer | Do it now, in this session or in a subagent it starts and waits for, until it is delivered as the row above says. An issue for later, a follow-up kept in a todo list or notes, or a hand-off to another agent is deferral: deferred work does not come back. |
| It waits on the user | Open a Dispatch ask on its issue (`skill://dispatch`). A chat question, a note, a peer message, a comment on an answered ask or a review request is not an ask. |
| A Dispatch record names a local file | Upload the file as an artifact on that issue and link it. |
| The result sits in a document, artifact or chat line with no ask | Open an ask that links the result and asks the user's next step. If the result is in a Dispatch document, end that document with an ask block (a `:::ask` block, `skill://dispatch`). For any other result, open a `dispatch ask`. An artifact notifies nobody. |
| The work was delivered smaller or different than its goal | Count it open. Finish the missing part now, or open an ask on its issue naming each unmet requirement and asking whether it is dropped. A new issue for the missing part does not close the row. |
| An eval's result does not answer the question it was asked | Count it open. Say in one line what it does answer and what is missing. Then run the missing arm or cells now, or open an ask with both. |
| An idle span (the report's list E) | Close the item it left open with that item's own action above. |
| A long project run without its workflow skill (list F) | Run the rest of the project under that skill. |
| Dropped on purpose | Say so to the requester, with the reason, on the issue. |
