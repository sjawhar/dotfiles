---
name: finding-dropped-work
description: Use when about to report an issue or a request done or end a session, after a restart or compaction, for a lead's daily or the chief of staff's periodic check of agent sessions, when an eval's result sits unread, or when the user says requests were dropped or blockers were not in Dispatch.
---

# Finding dropped work

An agent under load forgets what it was asked, closes a todo before the work ships, and waits on the user in chat where nobody sees it. Its own memory of the session is the thing that failed. A fresh agent that reads the transcript finds what the session forgot.

## When

| Who | When | Which transcripts | How |
|---|---|---|---|
| Any session | Before it tells a person that an issue or a request is done, before it moves an issue to `done`, and before it ends | Its own, since its last audit or its start | The brief below |
| A lead | Once a day | Each agent of its stream, and its own | The stream's daily audit program if it has one (a script that audits the stream's sessions, such as the Rocq speed stream's `audit.py` of dispatch://INFRA-296), with its roots set to the stream's sessions; else the brief below, one subagent per agent |
| The chief of staff | Every 4 hours | Every session with a message since the last audit, each read from that audit to now | The brief below, one subagent per session |

A stream is a line of work with its own Dispatch issue labelled `stream`. Its lead is the session that the issue's `scoreboard.json` artifact names as `lead`, else the session that holds the claim on that issue. Its agents are the sessions that hold the claim on that issue or on one of its child issues.

## How

1. Start one fresh read-only subagent per transcript (on Oh My Pi: `task` with the default agent; it needs a shell for `tq` and `dispatch read`). Give it the brief in [audit-brief.md](assets/audit-brief.md) word for word, with the session id, the window and a report path filled in. The brief carries the `tq` recipe that finds the transcript and the user's messages.
2. Read the report yourself. Open the FILE:LINE behind each DROPPED, MISSED-GOAL, UNSURFACED, WAITING-UNREPORTED and LOCAL-ONLY row before you act on it.

The report lists:

- (a) each request from the user, and whether it is done;
- (b) each follow-up a subagent or a report raised, and whether it is done, tracked, asked, or dropped with a stated reason;
- (c) each sentence that says something waits on a person, and the open ask or owner behind it;
- (d) each Dispatch record that depends on a file only one machine holds;
- (e) each idle span of more than an hour while an item was open;
- (f) each long project run without its workflow skill.

## Close each row

Within one hour of receiving the report, each row its closing list names leaves the audit with one of these. Work that takes longer starts in that hour and goes on until it is delivered.

| The row | Action |
|---|---|
| You can do it now | Deliver it. Done means deployed, merged or measured, as the request needs, with the evidence linked. Written into a spec, approval requested or sent to an agent is not done. |
| It takes longer | Do it now, in this session or in a subagent it starts and waits for, until it is delivered as above. An issue for later, a follow-up kept in a todo list or notes, or a hand-off to another agent is deferral, and deferred work does not come back. |
| It waits on the user | Open a Dispatch ask on its issue (`skill://dispatch`). A chat question, a note, a peer message, a comment on an answered ask or a review request is not an ask. |
| A Dispatch record names a local file | Upload the file as an artifact on that issue and link it. |
| The result sits in a document, artifact or chat line with no ask | Open an ask that links the result and asks the user's next step. If the result is in a Dispatch document, end that document with an ask block (a `:::ask` block, `skill://dispatch`); for any other result, open a `dispatch ask`. An artifact notifies nobody. |
| The work was delivered smaller or different than its goal | Count it open, not done. Finish the missing part now, or open an ask on its issue naming each unmet requirement and asking whether it is dropped. A new issue for the missing part does not close this row. |
| An eval's result exists and does not answer the question it was asked | Count it open. Say in one line what the result does answer and what is missing, then either run the missing arm or cells now, or open an ask with both. A figure delivered with no answer is not done. |
| An idle span (the report's list E) | Close the item it left open with that item's own action above. |
| A long project run without its workflow skill (list F) | Run the rest of the project under that skill. |
| Dropped on purpose | Say so to the requester, with the reason, on the issue. |

Then report done, with the audit's counts: rows found, rows closed by each action.

## Common mistakes

| Mistake | Instead |
|---|---|
| Audit from memory or the compaction summary | A fresh agent reads the transcript. |
| "Done" = written in the spec, approval requested, or sent to an agent | Done is delivered and checked. |
| A follow-up kept in a todo list, notes or a new issue | The work done now, as "It takes longer" says. |
| A wait reported in chat because the user is online | An ask. A chat question is gone after the turn; an ask stays open where the user and the stream see it. |
