# Audit brief and tq recipe

Copy the brief and the tq recipe below into the subagent's task. Fill in the values in angle brackets in the brief's header. Change nothing else. The recipe's placeholders are for the subagent to fill.

## The brief

```markdown
# Find dropped work in one session

Session: <full session id> (<title or role>)
Window: <start UTC> to <end UTC>
Report file: <path, outside every repository>
The user's identities: <how the user appears: role='user' rows, and the user's Dispatch actor id (an email)>

You are read-only. Write nothing except the report file. Send no message to any agent or person. Write nothing to Dispatch, GitHub or any repository.

## Tools

- Read skill://using-transcript-store, then use `tq` (recipe below) to find the user's messages, the session's reports and its tool calls. Open the raw transcript only at the FILE:LINE that `tq` gives. The store is about 10 minutes behind the live file: read the raw file after the store's last line for the tail.
- `dispatch read --issue KEY`, `dispatch open-asks`, `dispatch search --query '...'` to check whether an item reached Dispatch. A `dispatch ask` tool call in the transcript is an ask the session opened.
- `gh pr view`, `git log` to check a claim of "merged" or "committed".
- Read only this session's transcript and its own subagents' transcripts. Do not read other audits of this session.

## What to list

A. Each request from the user in the window: typed in chat, a Dispatch message, comment or ask answer, a broadcast, or a relay that quotes the user. One row per distinct thing asked. A question is a request too: list what it asks for ("Why do you need my login?" asks the session to stop needing it), and check that the answer did that, not only explained.
B. Each follow-up that a subagent's result or the session's own report raised: "next step", "later", "follow-up", "I will", "once X lands", an open item, a known defect left in place, a promise.
C. Each sentence in which the session says something waits on a person ("waiting for your answer", "needs the user's click", "tell me and I'll do it", "after we talk"), and each span where the session stopped work for a person.
D. Each Dispatch record (ask, comment, message, spec, artifact) that depends on a file only one machine holds: a path under /tmp, /home, ~, /mnt, a local branch, a box path.

## Outcome of each row

- DONE: delivered. Give the evidence (commit, merged PR, artifact, message, measured number) with its id or link. "Written into a spec", "approval requested", "sent to an agent" and "a todo closed" are not DONE unless the request was for exactly that.
- TRACKED: on a Dispatch issue that someone claims, or an open Dispatch ask. Name it.
- DROPPED: no action after it, work abandoned with no record, the request answered as a different question, or a todo closed without the work.
- WAITING-UNREPORTED: the session waits on a person with no open Dispatch ask for it. A question in chat, a note, a compaction summary, a peer message, a comment on an ask that is already answered, or a review request is not an ask.
- For D rows: LOCAL-ONLY (the reader on another machine cannot open it) or OK.

For each DROPPED and WAITING-UNREPORTED row, give the mechanism and quote the line that shows it: misread the request; asked in chat instead of an ask; held in notes or a compaction summary; told a peer only; comment on an answered ask; review request instead of an ask; "follow-up" never returned to; subagent or relayed result never checked; "done" meant written down or approval requested, not delivered; lost to a restart or compaction; other (say what).

## Report

One table per list (A, B, C, D): time UTC, the words (verbatim, at most 40), outcome, evidence or mechanism with FILE:LINE. Then the totals per outcome and per mechanism. End with every DROPPED, WAITING-UNREPORTED and LOCAL-ONLY row again, one line each, with the action that closes it: deliver it; put it on a claimed Dispatch issue with what done means and a date; or open the ask.
```

## tq recipe

Find the session and its transcript file:

```bash
tq sql "SELECT session_id, title, path, started_at, ended_at FROM sessions_latest
        WHERE session_id LIKE '<prefix>%' AND parent_session IS NULL"
```

The user's own words in the window: typed messages, and Dispatch events whose actor is a person (asks answered, comments, messages, broadcasts):

```bash
tq sql "SELECT ts, raw_line, role, substring(replaceAll(text, '\n', ' '), 1, 400) AS text
        FROM messages
        WHERE session_id = '<id>' AND ts >= '<start>' AND ts < '<end>'
          AND ((kind = 'user' AND role = 'user')
               OR (role = 'custom:envoy-message' AND match(text, 'actor:\\s+kind: user')))
        ORDER BY ts"
```

Relays that may quote the user (a parent's brief, an agent's message):

```bash
tq sql "SELECT ts, raw_line, substring(text, 1, 300) FROM messages
        WHERE session_id = '<id>' AND ts >= '<start>' AND ts < '<end>'
          AND (role = 'user:agent' OR role = 'custom:envoy-message')
          AND positionCaseInsensitive(text, '<user name>') > 0
        ORDER BY ts"
```

The session's own words after a message, and what it did:

```bash
tq next '<a phrase from the message>' -n 5
tq sql "SELECT started_at, tool, raw_line, substring(command, 1, 200) FROM calls
        WHERE session_id = '<id>' AND started_at >= '<start>' AND started_at < '<end>'
          AND (positionCaseInsensitive(command, 'dispatch ask') > 0 OR positionCaseInsensitive(command, 'dispatch issue') > 0)
        ORDER BY started_at"
```

Waits and follow-ups in the session's own text:

```bash
tq sql "SELECT ts, raw_line, substring(text, 1, 300) FROM messages
        WHERE session_id = '<id>' AND ts >= '<start>' AND ts < '<end>' AND kind = 'assistant'
          AND match(lower(text), 'waiting (for|on)|tell me|after we talk|follow-up|once .* lands|i will|i.ll ')
        ORDER BY ts"
```

Subagent transcripts of the session: `SELECT session_id, title FROM sessions_latest WHERE parent_session = '<id>'`.
