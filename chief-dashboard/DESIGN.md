# Streams dashboard: page design

One page that reads: a link to the Dispatch Inbox, streams at a glance, what changed. On a laptop the streams sit right of what changed; on a phone they stack. Each element serves one of the five decisions (D1-D5). Explainer videos have no section or page of their own: on the front page, each stream's videos play from a dropdown beside its note box to the lead.

Leads own their rows: a stream is any open Dispatch issue labelled `stream`; its `scoreboard.json` gives name, lead and numbers. `streams.json` only overrides rank and attention. `chief-dashboard check` shows a lead what the page will show.

## Header
When the data was read, any source that failed, and count chips that jump to the first match.

## 1. Decisions (D1): the Dispatch Inbox
Sami's decisions live, and are answered, in his Dispatch Inbox; the page does not copy them and holds no list of
things waiting on him. One line at the top links there. Each stream row counts the open Dispatch asks on that
stream's issues (its issue and `scope_issues`) that wait on him (issues assigned to him, or unassigned in his
projects), linking to the Inbox. The page reads no `needs_sami` field: a decision for him is an ask or nothing.

## 2. Streams at a glance (D2, D3, D5)
One row per stream: attention first, then behind or not measured, then rank.
- The lead's `headline` (160 characters).
- The lead's first main number: value, change since the previous reading, a bar from its first value to its target; a status chip for every main number, so a worse one still shows.
- The bottleneck in one line; the loop's first two flags.
- Lead, last Dispatch activity, decisions waiting, "note to lead".
Tapping a row opens its detail in place (`#s-<slug>` links open it too): every main number with its guards and trend, every loop flag, experiments, every value, and the note box (Envoy to the scoreboard's lead; delivery shown).

## 3. What changed (D4)
Since Sami's previous visit (or 24 h / 7 d): new decisions, numbers that moved (old → new), experiments ended with verdict, then the rest. Data: every scoreboard version, diffed; each ask's `created_at`.

Refresh: the server re-reads every 2 minutes and pushes a new page version over Server-Sent Events when what the page
shows changed; the page then morphs itself into the new fragment in place, keeping scroll, typed text, open rows and a
playing video, and reconnects when the stream drops.
