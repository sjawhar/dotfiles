---
name: finding-dropped-work
description: Use before you report a body of work done or end a session, when a lead does its daily check of each agent, when the chief of staff checks sessions every few hours, after a restart or compaction, or when the user says requests were dropped or blockers were not in Dispatch.
---

# Finding dropped work

An agent under load forgets what it was asked, closes a todo before the work ships, and waits on the user in chat where nobody sees it. Its own memory of the session is the thing that failed. A fresh agent that reads the transcript finds what the session forgot.

## When

| Who | When | Which transcripts | How |
|---|---|---|---|
| Any session | Before it reports a body of work done, and before it ends | Its own, since its last audit or its start | The brief below |
| A lead | Once a day | Each agent of the stream, and its own | The stream's daily audit program if it has one (the Rocq speed stream: `audit.py` of dispatch://INFRA-296, with its roots set to the stream's sessions); else the brief below, one subagent per agent |
| The chief of staff | Every few hours | Every session, since the last audit | The brief below, one subagent per session |

## How

1. Start one fresh read-only subagent per transcript (on Oh My Pi: `task` with the default agent; it needs a shell for `tq` and `dispatch read`). Give it the brief in [audit-brief.md](audit-brief.md) word for word, with the session id, the window and a report path filled in. The brief carries the `tq` recipe that finds the transcript and the user's messages.
2. Read the report yourself. Open the FILE:LINE behind each DROPPED, WAITING-UNREPORTED and LOCAL-ONLY row before you act on it.

The report lists:

- (a) each request from the user, and whether it is done;
- (b) each follow-up a subagent or a report raised, and whether it is done, tracked, asked, or dropped with a stated reason;
- (c) each sentence that says something waits on a person, and the open ask or owner behind it;
- (d) each Dispatch record that depends on a file only one machine holds;
- (e) each idle span of more than an hour while an item was open;
- (f) each long project run without its workflow skill.

## Close each row

Each row that is not DONE leaves the audit with one of these, the same hour:

| The row | Action |
|---|---|
| You can do it now | Deliver it. Done means deployed, merged or measured, as the request needs, with the evidence linked. |
| It takes longer, or someone else does it | Put it on a Dispatch issue that a session claims. The spec says what done means (deployed, merged, measured) and a date. |
| It waits on the user | Open a Dispatch ask on its issue (`skill://dispatch`). A chat question, a note, a peer message, a comment on an answered ask or a review request is not an ask. |
| A Dispatch record names a local file | Upload the file as an artifact on that issue and link it. |
| Dropped on purpose | Say so to the requester, with the reason, on the issue. |

Then report done, with the audit's counts: rows found, rows closed by each action.

## Common mistakes

| Mistake | Instead |
|---|---|
| Audit from memory or the compaction summary | A fresh agent reads the transcript. |
| "Done" = written in the spec, approval requested, or sent to an agent | Done is delivered and checked. |
| A follow-up kept in a todo list or notes | A claimed issue with done and a date. |
| A wait reported in chat because the user is online | An ask. A chat question is gone after the turn; an ask stays open where the user and the stream see it. |
