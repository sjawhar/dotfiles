# The stream record

One Dispatch document per stream, `stream.md`, on the stream issue. It holds what a standing lead used to hold in
its context, so that any session can read it and none has to stay alive to remember it.

- **The top part is rewritten in place.** Keep it current. A fact that changed is replaced, not appended.
- **The decisions part only grows.** A new decision is a new entry. An entry that changes an old one names it in
  `Replaces`; the old entry stays, so the reason for the change can be read.
- **The summary job parses four things**, so keep their shape exactly: the scoreboard link under `## Numbers`, each
  row of `## Order of work`, each line of `## Open commitments`, and each row of `## Envoy roles and claims`. It
  reads `due YYYY-MM-DDTHH:MMZ` (UTC), "waits on <KEY>", and the first issue key and session id on a line; the rest
  of the line is free text.
- **Create it once** with `dispatch artifact --issue <STREAM-ISSUE> --name stream.md --path stream.md --summary
  'stream record'`. **Change it only with `dispatch doc-edit`** against the document token of a fresh read, and read
  again on a token conflict: a second upload is a new version that erases every edit made since its copy was read.
- A person reads this document: write `https://` links, never `dispatch://`.

## Template

Copy everything below the line, replace each `<...>`, and delete any example line you do not use.

---

```markdown
# <Stream name>: stream record

Stream issue: <https link to the stream issue>. Record owner: <session id or person who keeps the top current>.
Summary job: 06:30 and 14:30 Pacific, and at each due time below. Last summary: <link, written by the job>.
Local copy when Dispatch is down: <the summary job's state directory>/<STREAM-ISSUE>/stream.md
Scheduler heartbeat (under 15 minutes old while it runs): <the summary job's state directory>/heartbeat

## Numbers

Scoreboard: <https link to the stream issue's scoreboard.json>
- Main: <name>: <value> on <date> (target <value> by <date>). Guard: <name>: <value> on <date>.
- Fast: <name>: <value> on <date>.
- Bottleneck now: <one line, with its measured share and source>.

## Order of work

Highest first. One row per issue. Each issue is one outcome with one owner session.

| # | Issue | Outcome, and what makes it done | Owner session | State |
| --- | --- | --- | --- | --- |
| 1 | <https link to KEY-N> | <outcome>. Done when <merged PR / measured result / launched with controls>. | <session id, or none> | <working / waits on KEY-M, starts when it lands / ready> |

## Sami's direction

One line per direction, as narrow as he gave it, with the date and the link to his own words. Never widen it.
- <his direction, in one line> (Sami, <YYYY-MM-DD>, <https link to his words>)

## Open commitments

One line each. Remove a line when its link shows it done, or when the person it was made to agrees to drop it.
- [ ] <what> · owner <session id> · due <YYYY-MM-DDTHH:MMZ> · <https link to the issue or the promise>

## Standing roles and scheduled jobs

A standing role is a session only when its issue states the condition that ends it. Periodic work is a scheduled
job, never a session.

| Role or job | Issue | Ends when / runs at | Holder or job name |
| --- | --- | --- | --- |
| <merge train runner> | <https link> | <the train is retired> | <session id> |
| <3-hourly report> | <https link> | <every 3 h, from the scheduler's jobs file> | <job name> |

## Envoy roles and claims

| Envoy role | Holder now | Goes to when the holder ends |
| --- | --- | --- |
| lead-<stream> | <session id or process> | <the record owner> |

---

## Decisions

A decision settles later cases with the same deciding facts. It is not a rule, and it binds nobody: only Sami's
own words bind. Newest last. Number entries D1, D2, ...

### D<n>: <the question, one line>

- Answer: <the choice>
- Reason: <why, with the facts that decided it>
- Applied: <Sami's words or the earlier decision it follows, with a link; or "none: a new case">
- Asked by: <session id>, <YYYY-MM-DD>
- Replaces: <D<m>, or none>
```
